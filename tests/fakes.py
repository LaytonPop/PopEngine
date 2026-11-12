"""测试替身：假 LLM + 假工具。生产代码里不出现，只用来驱动 loop。

为什么假 LLM 算"真测试"（面试一定会问）：
    它验证的是**循环的控制流**——什么时候继续、什么时候停、消息怎么排。
    这部分逻辑跟模型的智能程度无关，所以用剧本比用真模型更严格：
    真模型不会稳定地帮你制造"连续 10 次要工具"或"抛异常"。
    它**没证明**的是：提示词写得对不对、真实工具的输出能不能被模型用起来 ——
    那两件事只能靠 M2 真跑。

剧本写法：一个 LLMResponse 列表，按顺序吐。
    需要模拟模型层失败时，往剧本里塞一个 Exception 实例。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pop_engine.types import LLMResponse, Message, ToolCall, ToolSpec


def assistant_text(text: str) -> LLMResponse:
    """模型说完了一句话，本轮结束。"""
    return LLMResponse(
        message=Message(role="assistant", content=text),
        stop_reason="end_turn",
    )


def assistant_tool_calls(*calls: ToolCall) -> LLMResponse:
    """模型要求调工具（可以一次要多个，B 场景就是一次要两个）。"""
    return LLMResponse(
        message=Message(role="assistant", tool_calls=tuple(calls)),
        stop_reason="tool_use",
    )


class ScriptedProvider:
    """照剧本吐响应的假 LLM。

    `calls` 记录每次模型调用看到的**消息快照**，
    用来断言"第二轮到底看没看见工具结果"。
    """

    def __init__(self, script: Sequence[LLMResponse | Exception]) -> None:
        self._script = list(script)
        self.calls: list[tuple[list[Message], list[ToolSpec]]] = []

    def chat(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> LLMResponse:
        self.calls.append((list(messages), list(tools)))
        if not self._script:
            raise AssertionError(
                f"剧本用完了：模型已被调用 {len(self.calls)} 次，比预期多。"
                "（要么 loop 多跑了一圈，要么该写 end_turn 的地方还在要工具）"
            )
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class AddTool:
    """一个最小的真逻辑工具：加起来。结果可预期，方便断言。"""

    spec = ToolSpec(
        name="add",
        description="把两个整数相加",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )

    def run(self, **kwargs: Any) -> str:
        return str(int(kwargs["a"]) + int(kwargs["b"]))


class BoomTool:
    """专门用来验证"工具炸了，循环不炸"。留给 4 个场景全绿之后的第 5 个测试。"""

    spec = ToolSpec(
        name="boom",
        description="总是抛异常",
        parameters={"type": "object", "properties": {}},
    )

    def run(self, **kwargs: Any) -> str:
        raise RuntimeError("工具内部炸了")


class CountingTool:
    """有副作用、且把副作用记下来的工具。

    用来回答一个 mock 回答不了的问题：**这个工具到底被执行了几次**。
    存在理由（2026-11-12）：`max_steps` 用完的那一步，loop 会照常执行
    工具、把结果 append 进 messages，然后返回 failed —— 模型永远读不到
    那个结果，但副作用已经发生了。这是个真实存在的行为，得钉住它，
    免得将来有人改控制流时不知道自己动了什么。
    """

    spec = ToolSpec(
        name="count",
        description="有副作用的工具：被调用一次就记一次",
        parameters={"type": "object", "properties": {}},
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(dict(kwargs))
        return f"第 {len(self.calls)} 次副作用已发生"
