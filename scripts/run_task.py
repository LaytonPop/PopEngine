"""第 2 段验收脚本：跑 M2 真实任务，并把它自己的输出验一遍。

任务规格在 `docs/M2-真实任务.md`，这里只做三件事：
    1. 把仓库路径交给 loop，让模型自己用 list_dir / read_file 去看
    2. 从最终回答里抠出那份 JSON
    3. 按文档里的三条断言做程序化检查，打印 PASS / FAIL

为什么"检查"也要写进脚本，而不是靠人眼看：
    验收判据如果是"看着像"，那它就不是判据。
    这份脚本退出码非零就代表没过，将来可以原样塞进 CI。

跑法（项目根目录）：
    uv run python scripts/run_task.py <仓库路径>
    uv run python scripts/run_task.py <仓库路径> --trace              # 打印工具轨迹到终端
    uv run python scripts/run_task.py <仓库路径> --trace traces\\a.jsonl  # 落盘成 jsonl

`--trace` 后面跟路径就写文件，不跟就只打印 —— 两个功能不冲突：
一个是给人当场看的，一个是给程序读的。

断言 3（换个仓库再跑同样合法）没法在一个进程里自动完成，
所以这条靠"换个路径再跑一次本脚本"，脚本本身对任何路径一视同仁。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# 任务说明。刻意写死在代码里而不是从文档读出来：
# 这段 prompt 本身就是要进面试稿的东西，改它等于改任务定义。
TASK_PROMPT = """你的任务：看一个本地仓库，然后判断它是什么项目。

你只有两个工具，都可以用：
  - list_dir(path)：列出某个目录下一层的条目，不递归
  - read_file(path)：读一个文本文件（过长会截断）

所有路径都相对于仓库根目录，根目录用 "." 表示。

只输出一个 JSON 对象，不要任何解释，不要 markdown 代码围栏：
{"name": "项目名",
 "one_liner": "一句话说清这个项目是干什么的",
 "language": "主要编程语言",
 "top_dirs": ["最值得看的顶层目录"],
 "entrypoints": ["最像程序入口的文件路径"]}

没有把握的字段就给空字符串或空数组，不要编造。
"""

# 五个字段的类型要求，和 docs/M2-真实任务.md 保持一字不差。
EXPECTED: dict[str, type] = {
    "name": str,
    "one_liner": str,
    "language": str,
    "top_dirs": list,
    "entrypoints": list,
}


def load_env() -> None:
    """复用 hello_llm.py 里那个极简 .env 读取器（不为了读 KEY=VALUE 引依赖）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    from hello_llm import load_dotenv  # noqa: PLC0415 —— 必须先把 scripts 加进路径

    load_dotenv(ROOT / ".env")


def extract_json(text: str) -> dict[str, Any]:
    """从模型回答里抠出 JSON 对象。

    模型经常会自作主张套 ```json 围栏，或者前面加一句"好的"。
    这里用"第一个 { 到最后一个 }"的粗办法，够用且不要求模型完全听话；
    万一抠出来的是坏 JSON，会在 json.loads 立刻炸出来，不会静默通过。
    """
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)

    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"回答里找不到 JSON 对象，原文前 200 字：{text[:200]!r}")

    return json.loads(cleaned[start : end + 1])


def check_output(data: dict[str, Any]) -> list[str]:
    """断言 1：五个键齐全、类型对；list 型字段里面必须全是字符串。"""
    problems: list[str] = []

    for key, expected_type in EXPECTED.items():
        if key not in data:
            problems.append(f"缺少字段：{key}")
            continue

        value = data[key]
        if not isinstance(value, expected_type):
            problems.append(
                f"字段 {key} 类型不对：期望 {expected_type.__name__}，实际 {type(value).__name__}"
            )
            continue

        # 这里必须再 isinstance 一次，不能直接用 expected_type 判断：
        # mypy 只认 isinstance(value, 具体类型)，用变量做判断时它把 value 收窄成 object。
        if isinstance(value, list):
            bad = [item for item in value if not isinstance(item, str)]
            if bad:
                problems.append(f"字段 {key} 里混进了非字符串：{bad[:3]}")

    return problems


def check_trace(messages: tuple[Any, ...]) -> tuple[list[str], list[str], list[str]]:
    """断言 2 + 一条很便宜的轨迹自检。

    返回 (problems, 工具调用顺序, 警告)。

    "由模型决定"怎么证明？ —— 看调用出现在 **assistant 的 tool_calls** 里。
    loop 里没有任何写死的工具顺序，所以只要 trace 里有 list_dir，
    它就只可能是模型自己要求的。这一行注释本身就是面试时的答案。
    """
    problems: list[str] = []
    warnings: list[str] = []
    order: list[str] = []

    # 每个 tool 消息都要能对上一条前面发出去的 ToolCall.id，否则就是"结果没回家"。
    pending: set[str] = set()
    unmatched: list[str] = []

    for message in messages:
        if message.role == "assistant":
            for call in message.tool_calls:
                order.append(call.name)
                pending.add(call.id)
        elif message.role == "tool":
            if message.tool_call_id in pending:
                pending.discard(message.tool_call_id)
            else:
                unmatched.append(str(message.tool_call_id))

    if not order:
        problems.append("trace 里没有任何工具调用：这次是凭参数背答案，不是看出来的")
    elif "list_dir" not in order:
        problems.append("trace 里没有 list_dir：模型没看目录就下了结论")

    if unmatched:
        problems.append(f"有 {len(unmatched)} 条工具结果对不上 tool_call_id：{unmatched[:3]}")
    if pending:
        problems.append(f"有 {len(pending)} 次工具调用没有结果：{sorted(pending)[:3]}")

    if "read_file" not in order:
        warnings.append("没有 read_file：只看了目录名就判断，结论可能偏浅")

    return problems, order, warnings


def parse_args(argv: list[str]) -> tuple[Path, Path | None]:
    """拆出「仓库路径」和「可选的 --trace 输出路径」。

    这里不用 argparse：只有两个参数，而 argparse 会顺手接管 --help 的文案，
    反而看不清我们自己想说什么。手写一个够用，也更好讲。

    这个函数是为了修一个静默 bug：旧版是
        args = [item for item in argv if not item.startswith("--")]
        show_trace = "--trace" in argv
    于是 `--trace traces\\a.jsonl` 里那个路径不以 "--" 开头，会被当成**仓库路径**，
    而 --trace 仍然只是个"打印到终端"的开关 —— 跑完 PASS、退出码 0、文件根本没创建。
    静默失败是最难发现的一种，所以解析要显式。
    """
    repo: str | None = None
    trace: str | None = None

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--trace":
            i += 1
            if i >= len(argv):
                raise SystemExit("--trace 后面要跟一个输出路径，或者干脆不写这个选项")
            trace = argv[i]
        elif arg.startswith("--"):
            raise SystemExit(f"不认识的选项：{arg}")
        elif repo is None:
            repo = arg
        else:
            raise SystemExit(f"多余的位置参数：{arg}")
        i += 1

    if repo is None:
        raise SystemExit("用法: python scripts/run_task.py <仓库路径> [--trace <输出路径>]")

    return Path(repo), (Path(trace) if trace else None)


def write_events(path: Path, events: tuple[Any, ...]) -> None:
    """把事件一行一条写成 JSONL。父目录不存在就建。

    ensure_ascii=False：中文原样落盘，不写成 \\uXXXX —— 这份文件是给人看的。
    内容是 StepEvent 的**白名单字段**（asdict 只取 dataclass 上声明的那些），
    所以 key、header、request body 这些永远进不来。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


def main(argv: list[str]) -> int:
    repo_arg, trace_path = parse_args(argv)
    show_trace = "--trace" in argv  # 不管落不落盘，都打到终端一份

    repo = repo_arg.expanduser().resolve()
    if not repo.is_dir():
        print(f"不是目录：{repo}", file=sys.stderr)
        return 2

    load_env()

    from pop_engine.llm.provider import OpenAIProvider
    from pop_engine.loop import AgentLoop
    from pop_engine.tools import ToolRegistry
    from pop_engine.tools.builtin import ListDirTool, ReadFileTool

    # 工具锁定在被观察的仓库里：模型给的 path 只能在这个根下面活动。
    registry = ToolRegistry([ListDirTool(repo), ReadFileTool(repo)])
    result = AgentLoop(OpenAIProvider(), registry, max_steps=8).run(TASK_PROMPT)

    problems, order, warnings = check_trace(result.messages)

    print("=" * 64)
    print(f"仓库     : {repo}")
    print(f"status   : {result.status}")
    print(f"stop     : {result.stop_reason}    steps: {result.steps}")
    print("-" * 64)

    if show_trace:
        for index, message in enumerate(result.messages):
            for call in message.tool_calls:
                print(f"  [{index}] 模型要工具 {call.name}({call.arguments})")
            if message.role == "tool":
                head = message.content.replace("\n", " | ")[:100]
                print(f"  [{index}] 工具返回   {head}")

    print(f"工具轨迹 : {order if order else '（无）'}")

    if trace_path is not None:
        write_events(trace_path, result.events)
        print(f"events   : {trace_path}（{len(result.events)} 行）")

    data: dict[str, Any] | None = None
    if result.status != "ok":
        problems.append(f"loop 没跑完：{result.error}")
    else:
        try:
            data = extract_json(result.final_text)
        except (ValueError, json.JSONDecodeError) as exc:
            problems.append(f"输出不是合法 JSON：{exc}")

    if data is not None:
        problems.extend(check_output(data))

    print("-" * 64)
    if data is not None:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(result.final_text or "（无输出）")
    print("-" * 64)

    for warning in warnings:
        print(f"警告: {warning}")

    if problems:
        print("FAIL —— 没过：")
        for problem in problems:
            print(f"  · {problem}")
        return 1

    print("PASS —— 断言 1、2 通过；断言 3 请换个仓库路径再跑一次。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
