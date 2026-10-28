"""M1 的循环体。**这个文件由你自己写。**

靶子已经立好了：`tests/test_loop.py` 里 4 个场景现在全是红的，
它们钉死了 `AgentLoop` 的对外形状和行为。你的任务只有一件：

    把 `run()` 写出来，让 4 个测试变绿，并且**不许改测试来迁就实现**。

规则：
  - 只改这个文件。types.py / llm.py / tools.py 是契约，动了就要说明理由。
  - 不用加日志、不用加事件、不用加重试 —— M1 只验证控制流。
  - 全绿之后再跑 ruff 和 mypy。

卡住看 `docs/M1-任务书.md`（里面有分步提示，但故意不给代码）。
"""

from __future__ import annotations

from kama_engine.llm import LLMProvider
from kama_engine.tools import ToolRegistry
from kama_engine.types import Message, RunResult


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

        for step in range(1, self.max_steps + 1):
            try:
                response = self.provider.chat(list(messages), self.tools.specs())
            except Exception as exc:
                return RunResult(
                    status="failed",
                    stop_reason="error",
                    final_text="",  # 没拿到回复，自然没有最终文本
                    messages=tuple(messages),
                    steps=step,
                    error=f"{type(exc).__name__}:{exc}",
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
                )
            # 工具失败也会返回 ToolResult，这里不需要额外的 try/except
            for call in response.message.tool_calls:
                result = self.tools.invoke(call)
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
            error=None,
        )
