"""引擎的词汇表：一次 agent run 里流动的所有数据都长这样。

这个文件**刻意不含任何行为**。它只回答一个问题："数据长什么样。"
loop、LLM provider、工具三方只通过这些类型交流，谁都不认识谁。

面试时这一段是入口：整条链路就是下面几个类型的搬运过程。
    Message ──chat──> LLMResponse（含 ToolCall）
        └──invoke──> ToolResult ──> 变成 role="tool" 的 Message ──> 回到 chat
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# 模型说"这一轮我结束了"
# 注意：这是模型层的词汇，只有两种值 —— 模型要么说完，要么要工具。
LLMStopReason = Literal["end_turn", "tool_use"]

# 一次 run 停下来的原因（loop 层的词汇）。
# "tool_use" 不在里面：模型要工具不是终点，是继续跑的理由。
RunStopReason = Literal["end_turn", "max_steps", "error"]

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    """模型的一次工具调用请求。

    id 是**模型给的**（不是我们编的）。它的唯一职责是把结果对回去：
    一次要 3 个工具时，靠它才知道哪条 ToolResult 属于哪次调用。
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """一次工具执行的结果。

    关键设计：**工具失败不抛异常，失败也是一种结果**（is_error=True）。
    模型能读到"你的工具报错了"，从而自己决定换个参数或改方案；
    循环不会被一个坏工具带崩。
    """

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Message:
    """一条消息。这是引擎里唯一的持久状态 —— 没有隐藏状态机。

    - role="user"      : 用户输入
    - role="assistant" : 模型输出；可能带 tool_calls（那一轮 content 通常为空）
    - role="tool"      : 工具结果；必须带 tool_call_id 指回上一条 assistant 的某次调用
    - role="system"    : 暂时不用（M1 不需要，加了反而多一个变量）
    """

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """给模型看的"工具说明书"。

    注意这不是工具本身：ToolSpec 是**写给模型看的**，
    Tool（见 tools.py）是**我们真正执行的**。两者故意分开。
    """

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    """模型一次调用的返回。

    message 一定是 assistant 消息；stop_reason 由模型给，缺省时由 provider 兜底成
    "end_turn"（这一条是从 KamaClaude S1 学来的已知简化，后面要讲清它的风险）。
    """

    message: Message
    stop_reason: LLMStopReason


@dataclass(frozen=True)
class RunResult:
    """一次 run 的结局。**循环结束后唯一被交出来的东西。**

    messages 是完整现场：出了问题先看它，不用加日志。
    """

    status: Literal["ok", "failed"]
    stop_reason: RunStopReason
    final_text: str
    messages: tuple[Message, ...]
    steps: int  # 调用了几次模型（不是"跑了几圈工具"）
    error: str | None = None
