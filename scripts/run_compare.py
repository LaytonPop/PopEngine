"""M3 对比脚本：同一个任务、同一个模型，loop 和 graph 各跑 N 次，把数字并排放。

任务规格沿用 M2（`docs/history/M2-真实任务.md`），三个指标的算法沿用
`pop_engine.metrics.summarize`（`model_calls` / `total_ms` / `steps`）。

跑法（项目根目录）：
    uv run python scripts/run_compare.py .                     # 两个引擎各 3 次
    uv run python scripts/run_compare.py . --runs 5            # 各 5 次
    uv run python scripts/run_compare.py . --engine graph      # 只跑一个
    uv run python scripts/run_compare.py . --out traces/cmp    # 指定落盘目录

── 这个脚本必须自己说清的三件事（不说清，数字就会被质疑）──

1. **graph 那侧是固定三节点管线**：scan → read → report → 收工。
   router 只看「现在在哪个节点」，不看模型写了什么。

   ⇒ 这里有一个**我预判错了、被数据推翻**的点：我原以为固定 router 会让
   `model_calls` 变成常数。实测是 7 / 5 / 6（极差 2），比 loop 的 5 / 6 / 5 还大一点。
   原因是 router 只管「走哪几个节点」，管不到「每个节点里模型问几次」。
   **固定结构夹住的是区间，不是数字** —— 而区间宽度是你配的预算，不是测出来的。

   真正的"更可预测"要等 router 依赖模型输出（`enough: yes/no`）才测得到。

2. **两边的 prompt 不可能一样。** loop 收到一个完整任务让模型自己决定看什么；
   graph 的每个节点收到的是"只管这一步"的指令。这不是脚本偷懒，
   是两种控制流的结构差异本身。⇒ 这不是纯粹的引擎对比，是**两种配置的对比**。

3. **`total_ms` 用事件和（口径 A），不是墙上时钟。** 墙钟另记一列仅供参考。
   同一个 run 里只用一种口径，表下面写明白。

⚠️ 同一个 prompt 连跑会撞 provider 的 prompt cache，后几次明显更快。
   `total_ms` 因此天然有下降趋势 —— 这不是引擎差异。看 `model_calls` 和
   五键通过率，那两个不受缓存影响。
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# 复用 run_task.py 的任务定义与检查函数 —— 不复制一份。
# 任务 prompt 是「任务定义」，复制一份就是两个定义，迟早分叉。
sys.path.insert(0, str(ROOT / "scripts"))
from run_task import (  # noqa: E402
    EXPECTED,
    TASK_PROMPT,
    check_output,
    check_trace,
    extract_json,
    load_env,
    write_events,
)

# ------------------------------------------------------------------ graph 侧的三段 prompt
#
# 为什么不像 loop 那样给一段完整任务：
#   固定管线的每一段只管一件事，把完整任务丢给 scan 节点，它就会自己去读文件、
#   自己下结论 —— 那 graph 的结构就白搭了。这不是我偷懒，是结构决定的。
#
# 代价必须承认：这三段 prompt 是我写的，loop 那段是 M2 早就定死的。
#   ⇒ 两边的"输入"不同，比出来的是"两种配置"，不是"两个引擎"。

SCAN_PROMPT = """用 list_dir 工具列出仓库根目录 "." 下有哪些条目。

只如实报告你看到的名字，不要判断这是什么项目，不要输出 JSON。"""

READ_PROMPT = """仓库根目录下的条目是：

{scan}

现在用 read_file 读 README.md。如果它不存在，就试 pyproject.toml，再不行试 package.json。

读完之后用几句话说明：这个项目叫什么、在做什么、用什么语言写、有哪些主要目录、
程序入口在哪。不要输出 JSON。"""

# JSON 规格直接引用 EXPECTED 的键名，避免"规格改了 prompt 没改"——
# 那种不一致只有跑到模型那里才会暴露，而且表现为"模型不听话"，很难查。
_JSON_SHAPE = """{{"name": "项目名",
 "one_liner": "一句话说清这个项目是干什么的",
 "language": "主要编程语言",
 "top_dirs": ["最值得看的顶层目录"],
 "entrypoints": ["最像程序入口的文件路径"]}}"""

REPORT_PROMPT = f"""你要判断一个本地仓库是什么项目。下面是你同事已经查到的材料。

目录条目：
{{scan}}

文件内容：
{{read}}

**你手上没有任何工具，也不要请求调用工具** —— 需要的材料都在上面了，
再查也查不到新东西。直接根据现有材料作答。

只输出一个 JSON 对象，不要任何解释，不要 markdown 代码围栏：
{_JSON_SHAPE}

没有把握的字段就给空字符串或空数组，不要编造。
（需要的字段一共五个：{", ".join(EXPECTED)}）"""


def _scan_prompt(state: dict[str, Any]) -> str:
    return SCAN_PROMPT


def _read_prompt(state: dict[str, Any]) -> str:
    return READ_PROMPT.format(scan=state.get("scan_out", "（上一步没有输出）"))


def _report_prompt(state: dict[str, Any]) -> str:
    return REPORT_PROMPT.format(
        scan=state.get("scan_out", "（无）"),
        read=state.get("read_out", "（无）"),
    )


# ------------------------------------------------------------------ 两个引擎
def build_loop(repo: Path, provider: Any) -> Any:
    from pop_engine.loop import AgentLoop
    from pop_engine.tools import ToolRegistry
    from pop_engine.tools.builtin import ListDirTool, ReadFileTool

    registry = ToolRegistry([ListDirTool(repo), ReadFileTool(repo)])
    return AgentLoop(provider, registry, max_steps=8)


def build_graph(repo: Path, provider: Any) -> Any:
    from pop_engine.graph import GraphEngine, agent_node
    from pop_engine.loop import AgentLoop
    from pop_engine.tools import ToolRegistry
    from pop_engine.tools.builtin import ListDirTool, ReadFileTool

    # 三个节点共用同一个 registry：工具体一样，锁的根也一样。
    # AgentLoop 是无状态的，所以同一个 provider / registry 可以被多个 loop 复用。
    registry = ToolRegistry([ListDirTool(repo), ReadFileTool(repo)])

    # 每个节点的 max_steps 单独给：一段只干一件事。
    # ⚠️ 预算给紧了会静默失血，实测踩过两次：
    #   read 给 4 时，模型第 4 次还想再读一个文件 → failed/max_steps →
    #   read_out 是空串 → report 只能凭 scan_out 编 → 五键全空但 status=ok。
    #   跟 loop 里那个孤儿副作用是同一个形状：**预算掐掉的那一步，结果没人看得到。**
    # 所以这里给足：scan 只列目录（4 够）、read 要反复读（6）。
    scan = agent_node(AgentLoop(provider, registry, max_steps=4), _scan_prompt, "scan_out")
    read = agent_node(AgentLoop(provider, registry, max_steps=6), _read_prompt, "read_out")
    # ⚠️ report **必须给空 registry**，不给工具。
    #
    # 这不是省事，是这个阶段该有的约束：report 的职责是"把手上的材料整合成 JSON"，
    # 不是"再去补查"。给了工具它就会绕开结构自己去查 —— 那 graph 的结构就白搭了，
    # 而且会写出结构没打算给它的材料。
    #
    # 实测踩过：最早它拿到整套工具 + max_steps=1，第一步就要 list_dir，
    # 被预算掐掉 → final_text 是空串，而整张图三次全部 status=ok。
    # 给 2 次而不是 1 次，是因为模型偶尔会先请求一个不存在的工具（拿到 is_error 结果），
    # 第二次才乖乖输出 JSON —— 给 1 次等于把这条正常路径掐死。
    report = agent_node(
        AgentLoop(provider, ToolRegistry([]), max_steps=2), _report_prompt, "final_text"
    )

    def router(state: dict[str, Any]) -> str | None:
        """固定管线：只看位置，不看内容。

        ⚠️ 我曾经以为这样 `model_calls` 就一定是常数（= 3）。**实测否掉了**：
            测出来是 7 / 5 / 6，极差 2，比 loop 的 5/6/5 还大一点。

        为什么：router 固定的是**走哪几个节点**，不是**每个节点问几次模型**。
        节点内部还是一个完整的 loop，模型要 1 次还是 3 次工具由它自己定。
        ⇒ 固定 router 把调用次数夹进【节点数, Σ节点预算】这个区间，
          而那个区间的宽度是**我配的**，不是测出来的。

        要真的把"谁决定停止"分开，得让 router 依赖模型写的 key
        （比如让 scan 节点写 enough: yes/no，router 读它）——
        那时候代码规则和模型的"我觉得够了"才是同一个位置上的两种答案。
        """
        current = state["current_node"]
        if current == "scan":
            return "read"
        if current == "read":
            return "report"
        return None

    return GraphEngine(
        {"scan": scan, "read": read, "report": report},
        router,
        entry="scan",
        max_steps=12,  # 3 节点 + 余量；固定管线理论上走 3 步就到头
        max_visits=1,  # 每个节点只许走一次，回退立刻暴露
    )


# ------------------------------------------------------------------ 一次跑 + 验收
def _tools_from_events(events: tuple[Any, ...]) -> list[str]:
    """从事件里数工具调用。

    为什么 graph 侧必须用这个方法：`RunResult.messages` 只留最后一次子 run 的现场
    （report 节点没有工具），用 `check_trace(messages)` 会误判成"没调工具"。
    **事件才是跨节点唯一的全量账本** —— 这正好证明了 M3 把事件打通是对的。
    """
    return [e.tool_name for e in events if e.kind == "tool_end" and e.tool_name]


def run_once(engine: str, repo: Path, provider: Any) -> dict[str, Any]:
    from pop_engine.metrics import summarize

    target = build_loop(repo, provider) if engine == "loop" else build_graph(repo, provider)
    prompt = TASK_PROMPT if engine == "loop" else "判断这个仓库是什么项目"

    wall_started = time.perf_counter()
    result = target.run(prompt)
    wall_ms = round((time.perf_counter() - wall_started) * 1000, 2)

    metrics = summarize(result.events)
    # 三个指标必须从事件算 —— 如果 events 空着，这里会诚实地给出 0，
    # 而不是让引擎自己报一个数。这条是 M3 一开始就定下的口径。
    tools = _tools_from_events(result.events)

    problems: list[str] = []
    data: dict[str, Any] | None = None

    if result.status != "ok":
        problems.append(f"没跑完：{result.stop_reason} / {result.error}")
    else:
        try:
            data = extract_json(result.final_text)
        except (ValueError, json.JSONDecodeError) as exc:
            problems.append(f"输出不是合法 JSON：{exc}")

        if data is not None:
            problems.extend(check_output(data))

    if not tools:
        problems.append("一次工具都没调：这是凭参数背答案")
    elif "list_dir" not in tools:
        problems.append("没调 list_dir：模型没看目录就下结论")

    # loop 侧额外跑 M2 那套 trace 自检（messages 是完整现场，能用）。
    # graph 侧不做 —— 它只留最后一段的 messages，按 M2 的标准会误判。
    if engine == "loop":
        trace_problems, _, _ = check_trace(result.messages)
        problems.extend(trace_problems)

    return {
        "engine": engine,
        "status": result.status,
        "stop_reason": result.stop_reason,
        "model_calls": metrics["model_calls"],
        "total_ms": metrics["total_ms"],
        "steps": metrics["steps"],
        "wall_ms": wall_ms,
        "tools": tools,
        "n_tools": len(tools),
        "ok": not problems,
        "problems": problems,
        "output": data,
        "events": len(result.events),
        "_events": result.events,
    }


# ------------------------------------------------------------------ 报表
def _dist(values: list[float]) -> str:
    return ", ".join(f"{v:g}" for v in values)


def print_report(records: list[dict[str, Any]], runs: int, out_dir: Path) -> None:
    print()
    print("=" * 88)
    print("M3 对比结果")
    print("=" * 88)
    print()
    print("| 引擎  | run | model_calls | total_ms | steps | 工具数 | 五键 | 备注 |")
    print("| ----- | --- | ----------- | -------- | ----- | ------ | ---- | ---- |")
    for index, record in enumerate(records, start=1):
        note = "PASS" if record["ok"] else "FAIL: " + "; ".join(record["problems"])[:40]
        print(
            f"| {record['engine']:<5} | {index:<3} | {record['model_calls']:>11g} "
            f"| {record['total_ms']:>8.0f} | {record['steps']:>5g} "
            f"| {record['n_tools']:>6} | {'✓' if record['ok'] else '✗':<4} | {note} |"
        )

    print()
    print("`total_ms` 口径：各事件 `duration_ms` 之和（不含 Python 开销），**不是墙钟**。")
    print(f"墙钟参考：{_dist([r['wall_ms'] for r in records])} ms（含全部开销，仅供对照）。")
    print()

    print(f"── 分布（每个引擎 {runs} 次）──")
    for engine in ("loop", "graph"):
        calls = [r["model_calls"] for r in records if r["engine"] == engine]
        if not calls:
            continue
        passes = sum(1 for r in records if r["engine"] == engine and r["ok"])
        spread = max(calls) - min(calls)
        print(
            f"{engine:<6} model_calls: {_dist(calls):<14} "
            f"中位 {statistics.median(calls):g}，极差 {spread:g}，"
            f"五键 {passes}/{len(calls)}"
        )
    print()
    print("⚠️ 关于「固定 router ⇒ model_calls 恒定」：**实测不成立。**")
    print("   router 决定的是「走哪几个节点」，但每个节点内部是模型在跑，")
    print("   它要 1 次还是 3 次工具是它自己定的。所以方差没被消灭，只被缩小了范围。")
    print()
    print("   真正的因果链是：router 固定 ⇒ **节点数**固定 ⇒ 调用次数被夹在")
    print("   【节点数, Σ节点预算】这个区间里。区间宽度取决于各节点预算给多大，")
    print("   **那个宽度是你配的，不是测出来的。**")
    print()
    print(f"逐次事件落盘：{out_dir}/（每 run 一份 jsonl）")


def main(argv: list[str]) -> int:
    # Windows 控制台默认 GBK，报表里的 ✓ / ✗ / ⚠ 不在 GBK 里，
    # 会在 print 时抛 UnicodeEncodeError —— 报错发生在"打印阶段"，
    # 数字其实已经跑出来了，只是没显示。强制 utf-8 + replace 兜底。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    repo_arg: str | None = None
    runs = 3
    only: str | None = None
    out_dir = Path("traces/compare")

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--runs":
            i += 1
            runs = int(argv[i])
        elif arg == "--engine":
            i += 1
            only = argv[i]
            if only not in ("loop", "graph"):
                raise SystemExit("--engine 只能是 loop 或 graph")
        elif arg == "--out":
            i += 1
            out_dir = Path(argv[i])
        elif arg.startswith("--"):
            raise SystemExit(f"不认识的选项：{arg}")
        elif repo_arg is None:
            repo_arg = arg
        else:
            raise SystemExit(f"多余的位置参数：{arg}")
        i += 1

    if repo_arg is None:
        raise SystemExit(
            "用法: python scripts/run_compare.py <仓库路径> "
            "[--runs N] [--engine loop|graph] [--out <目录>]"
        )
    if runs < 1:
        raise SystemExit("--runs 至少 1")

    repo = Path(repo_arg).expanduser().resolve()
    if not repo.is_dir():
        print(f"不是目录：{repo}", file=sys.stderr)
        return 2

    load_env()

    from pop_engine.llm.provider import OpenAIProvider

    provider = OpenAIProvider()
    engines = [only] if only else ["loop", "graph"]
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    # 交替执行（第 1 轮 loop、graph；第 2 轮 loop、graph …），不按引擎分批。
    # 理由：同一个 prompt 连跑会命中 provider 的 prefix cache，越跑越快。
    # 分批跑会让"后跑的那个引擎"整批都在热缓存上；交替则两边轮次新鲜度一致。
    for index in range(1, runs + 1):
        for engine in engines:
            print(f"[{engine} {index}/{runs}] 跑 ...", flush=True)
            try:
                record = run_once(engine, repo, provider)
            except Exception as exc:  # 一次崩了不该带走整批实验
                print(f"  崩了：{type(exc).__name__}: {exc}")
                records.append(
                    {
                        "engine": engine,
                        "round": index,
                        "status": "crashed",
                        "stop_reason": "exception",
                        "model_calls": 0,
                        "total_ms": 0,
                        "steps": 0,
                        "wall_ms": 0,
                        "tools": [],
                        "n_tools": 0,
                        "ok": False,
                        "problems": [f"{type(exc).__name__}: {exc}"],
                        "output": None,
                        "events": 0,
                        "_events": (),
                    }
                )
                continue
            record["round"] = index
            records.append(record)
            flag = "PASS" if record["ok"] else "FAIL"
            print(
                f"  {flag}  model_calls={record['model_calls']:g} "
                f"total_ms={record['total_ms']:.0f} 工具={record['n_tools']} "
                f"events={record['events']}"
            )
            if not record["ok"]:
                for problem in record["problems"]:
                    print(f"    · {problem}")

    # 落盘：一次一份 json（汇总）+ 一份 events.jsonl（原始账本）。
    # 两份都要 —— 汇总让人一眼看懂，jsonl 让人能自己复核那三个数字是怎么算出来的。
    # 只留汇总等于要求读者相信本脚本；留了账本，`summarize` 的结果可被任何人重算。
    for record in records:
        stem = f"{record['engine']}-r{record.get('round', 0):02d}"
        payload = {key: value for key, value in record.items() if key not in ("events", "_events")}
        (out_dir / f"{stem}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        events = record.get("_events") or ()
        if events:
            write_events(out_dir / f"{stem}.events.jsonl", events)

    print_report(records, runs, out_dir)

    # 退出码：任何一次没通过就非零 —— 跟 run_task.py 一个规矩，
    # 判据是程序化的，不是"看着像"。
    return 0 if all(record["ok"] for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
