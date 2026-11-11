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

    def run(self, prompt: str) -> RunResult:
        """跑完一轮，返回结局。

        必须满足的四件事（就是那 4 个测试）：
          A. 模型一次就结束 → status="ok"，final_text=模型的话，steps=1
          B. 模型要工具 → 工具结果按 tool_call_id 回填成 role="tool" 消息，
             再喂回模型；顺序不能错
          C. 模型一直要工具 → 到 max_steps 停，status="failed"，
             stop_reason="max_steps"，模型调用次数绝不超过 max_steps
          D. 模型抛异常 → 不往上冒，status="failed"，stop_reason="error"，
             error 里带上异常信息，已积累的 messages 保留

        Q&A&sum:
            一个完整的数据模型包括：模型返回结果，用户传入的信息，工具返回结果

        """
        messages = [Message(role="user", content=prompt)]

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
