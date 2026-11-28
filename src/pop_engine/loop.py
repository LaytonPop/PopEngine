"""循环体：一次 agent run 的全部控制流。

一处判断，一条回边，三个出口 —— 整台引擎就这么大：
    看 stop_reason 分岔（end_turn 收工 / unknown 止损 / 其余去调工具）
    调完工具带着结果绕回 provider.chat
    出口只有 ok、failed(error)、failed(max_steps) 三种

M1 时的约束是「不加日志、不加事件」；M2 的 c 轮解冻了这一条：
现在每一步都会产出 `StepEvent`（见 types.py），攒在 `events` 里随 RunResult 交出去。
**loop 只负责"发生了什么"，序列化成 jsonl 是脚本的事** —— 它不认识文件，也不认识 JSON。

契约边界：types.py / llm/ / tools/ 是对外契约，改它们要说明理由。
`docs/history/M1-任务书.md` 是当时的作业书，已完成，留作记录。
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from pop_engine.llm import LLMProvider
from pop_engine.tools import ToolRegistry
from pop_engine.types import Message, RunResult, StepEvent


def _elapsed_ms(started: float) -> float:
    """从 started 到现在过了多少毫秒，保留两位小数。

    用 perf_counter 不用 time()：前者是单调时钟，不受系统改时间/对时影响；
    后者可能往回跳，算出来的耗时会是负数。计量一律用单调时钟。
    """
    return round((time.perf_counter() - started) * 1000, 2)


class AgentLoop:
    """一次 agent run。

    刻意是"无状态对象"：`max_steps` 这类配置放构造器，**运行状态只活在 run() 里**。
    好处是同一个 loop 可以被连续 run 很多次而不会串味 —— M3 的 graph 引擎
    会反复调用它，这一点是前提。
    """

    def __init__(self, provider: LLMProvider, tools: ToolRegistry, *, max_steps: int = 8) -> None:
        if max_steps < 1:
            raise ValueError("max_steps 必须至少为 1")
        self.provider = provider
        self.tools = tools
        self.max_steps = max_steps

    def run(self, initial_messages: Sequence[Message]) -> RunResult:
        """跑完一轮，返回结局。

        必须满足的四件事（就是那 4 个测试）：
          A. 模型一次就结束 → status="ok"，final_text=模型的话，steps=1
          B. 模型要工具 → 工具结果按 tool_call_id 回填成 role="tool" 消息，
             再喂回模型；顺序不能错
          C. 模型一直要工具 → 到 max_steps 停，status="failed"，
             stop_reason="max_steps"，模型调用次数绝不超过 max_steps
          D. 模型抛异常 → 不往上冒，status="failed"，stop_reason="error"，
             error 里带上异常信息，已积累的 messages 保留

        ── 入参形状（2026-11-28，M4 第 1 关 T0）──

        参数是**完整现场**，不是"一句话"：`run` 不构造任何消息，连 user 都不造。
        调用方负责把该说的话放进去 ——

            首轮    run([Message(role="user", content="查这个仓库")])
            续聊    run([*上次.messages, Message(role="user", content="第二个文件呢")])
            续跑    run(上次.messages)      ← 原样送回，末尾是还没人读的 tool

        **末尾必须是模型还没消费过的东西**：一条 `role="user"`，或一批没读的
        `role="tool"`。旧版本靠 `messages[0]` 是不是 user 来判"起点对不对"，
        现在那个保证没有了 —— 换成了这条更弱也更准的。

        `run` **不校验**入参合法性（历史合不合法归 session 层，见 M4 第 3 关），
        只挡一种情况：空入参。空的时候 `to_wire([])` 会静默返回 `[]`，
        一路无声地发出去，直到 API 那边 400 —— 静默的空比当场报错难查得多。

        `messages = list(initial_messages)` 那一步也不是顺手写的：它是
        「不修改入参」这条不变量的**唯一实现**（调用方传进来的 list 跑完不变）。

        ── 预算语义（2026-11-12 钉死，M3 对比有效性的前提）──

        `max_steps` 限的是**模型调用次数**，不是"成功次数"。第 N 次调用是
        **合法调用**，它跟前面 N-1 次没有任何区别。所以：

          · 第 N 次调用返回 end_turn  → **成功**。
            不是"end_turn 打赢了 max_steps"——根本没有两条规则在抢，
            是这次调用正当且答完了，没有理由判它失败。预算耗尽限制的是
            第 N+1 次，而它压根没发生。
          · 第 N 次调用返回 tool_use   → 失败（failed/max_steps）。
            注意：这批工具**已经执行了**，副作用不可逆，但结果模型读不到
            （见 test_l 钉住的孤儿副作用）。M3 不修，M5 的"重跑会不会
            执行两遍"那问要从这里起算。
          · 第 N 次调用抛异常 / 返回 unknown → 失败（error），不是"预算用完"。

        **不要把这个结论泛化成"正常结束永远优先于所有护栏"。** 权限拒绝、
        用户中断、成本上限的检查时机和语义都不一样，各有各的规则，不能
        往同一张排序表里塞。这里只说了 end_turn 和步数预算这一对。

        ── 交出去的那份现场（2026-11-28）──

        `RunResult.messages` 是**到这次 run 结束为止的完整现场**，不是"本次新增"：
        传进来的那部分原样在里面，后面接本次产生的。恢复要用的就是它，整份存整份喂。
        所以别拿 `len(result.messages)` 当工作量看 —— 那不是进度条。

        `steps` 则只算**本次**的模型调用次数，不跨 run 累计。两句话是一对：
        现场是累积的，计数是本轮的。

        Q&A&sum:
            一个完整的数据模型包括：模型返回结果，用户传入的信息，工具返回结果

        """
        if not initial_messages:
            raise ValueError("initial_messages 不能为空：run 不再替你造 user 消息")
        messages = list(initial_messages)

        # 工具在一次 run 里不变，说明书只算一次。
        # 提到循环外不只是省几个列表推导 —— 它同时钉死了一件事：
        # 同一轮 run 里，模型每次看到的说明书必须一模一样。
        tool_specs = self.tools.specs()

        # 事件只在这里攒，序列化不归 loop 管 —— 它不认识 JSON，也不认识文件。
        # 换 stdout / SQLite / 远端：改的永远是消费方，这个文件不用动。
        events: list[StepEvent] = []

        for step in range(1, self.max_steps + 1):
            started = time.perf_counter()
            try:
                response = self.provider.chat(list(messages), tool_specs)
            except Exception as exc:
                # 失败也要记：不然"这次 run 花了多久才炸"就无从谈起。
                events.append(
                    StepEvent(
                        kind="model",
                        step=step,
                        duration_ms=_elapsed_ms(started),
                        error=f"{type(exc).__name__}:{exc}",
                    )
                )
                return RunResult(
                    status="failed",
                    stop_reason="error",
                    final_text="",  # 没拿到回复，自然没有最终文本
                    messages=tuple(messages),
                    steps=step,
                    error=f"{type(exc).__name__}:{exc}",
                    events=tuple(events),
                )

            events.append(
                StepEvent(
                    kind="model",
                    step=step,
                    stop_reason=response.stop_reason,
                    duration_ms=_elapsed_ms(started),
                )
            )

            # 先记录模型的请求，再记录工具的结果，保证结果有对应得到请求，先请求后结果的原因是因为
            messages.append(response.message)

            if response.stop_reason == "end_turn":
                return RunResult(
                    status="ok",
                    stop_reason="end_turn",
                    final_text=response.message.content,
                    messages=tuple(messages),
                    steps=step,
                    error=None,
                    events=tuple(events),
                )

            # unknown 也是终点：它是「模型合法地以我们没建模的原因停下」
            # （length 被截断 / content_filter 被拦）。这种响应的 tool_calls
            # 一定是空的，若放它往下走，for 一次不跑、同一响应再绕一圈，
            # 会把整个预算烧完而 error 还是 None。
            if response.stop_reason == "unknown":
                return RunResult(
                    status="failed",
                    stop_reason="error",
                    final_text="",
                    messages=tuple(messages),
                    steps=step,
                    error=f"模型以未建模的原因停下（stop_reason=unknown），第 {step} 步中止",
                    events=tuple(events),
                )

            # 工具失败也会返回 ToolResult，这里不需要额外的 try/except
            for call in response.message.tool_calls:
                # start / end 两条：中途没有可观测点（无流式、无异步），
                # 但"开始了没有结束"这件事能在日志里显形（比如进程被 kill）。
                events.append(
                    StepEvent(
                        kind="tool_start",
                        step=step,
                        tool_name=call.name,
                        tool_call_id=call.id,
                    )
                )
                started_tool = time.perf_counter()
                result = self.tools.invoke(call)
                events.append(
                    StepEvent(
                        kind="tool_end",
                        step=step,
                        tool_name=call.name,
                        tool_call_id=result.tool_call_id,
                        # is_error 造出来之后一直没人读过，这里是第一个消费方
                        is_error=result.is_error,
                        duration_ms=_elapsed_ms(started_tool),
                    )
                )
                messages.append(
                    Message(
                        role="tool",
                        content=result.content,
                        tool_call_id=result.tool_call_id,
                        name=call.name,
                    )
                )
        # 最后一轮的工具仍会执行，但不会再发起超出预算的模型调用
        return RunResult(
            status="failed",
            stop_reason="max_steps",
            final_text="",
            messages=tuple(messages),
            steps=self.max_steps,
            error=f"超过 max_steps={self.max_steps} 步仍未结束",
            events=tuple(events),
        )
