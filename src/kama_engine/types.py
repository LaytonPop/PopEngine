"""引擎的词汇表：一次 agent run 里流动的所有数据都长这样。

这个文件**刻意不含任何行为**。它只回答一个问题："数据长什么样。"
loop、LLM provider、工具三方只通过这些类型交流，谁都不认识谁。

面试时这一段是入口：整条链路就是下面几个类型的搬运过程。
    Message ──chat──> LLMResponse（含 ToolCall）
        └──invoke──> ToolResult ──> 变成 role="tool" 的 Message ──> 回到 chat
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# 这一轮模型为什么停下 —— 模型层的词汇。
# 绝大多数情况只有两种值：说完了，或者要工具。
# 但协议给的 finish_reason 比这多得多：length（被截断）、content_filter（被拦了）、
# 各家私有的 insufficient_system_resource……这些值不能装作是 end_turn ——
# 被截断的答案和答完的答案，在日志里长得一模一样，这是最容易静默出错的地方。
# 所以留一个 unknown：内部类型不比协议更穷，丢信息要显式暴露，不能靠兜底遮住。
LLMStopReason = Literal["end_turn", "tool_use", "unknown"]

# 一次 run 停下来的原因（loop 层的词汇）。
# "tool_use" 不在里面：模型要工具不是终点，是继续跑的理由。
RunStopReason = Literal["end_turn", "max_steps", "error"]

Role = Literal["system", "user", "assistant", "tool"]

# 事件的种类。一个类 + 一个判别式，而不是三个类 ——
# 字段还只有八个，拆成 Union 是过度设计。真涨到互斥得厉害再拆。
EventKind = Literal["model", "tool_start", "tool_end"]


@dataclass(frozen=True)
class ToolCall:
    """模型的一次工具调用请求。

    id 是**模型给的**（不是我们编的）。它的唯一职责是把结果对回去：
    一次要 3 个工具时，靠它才知道哪条 ToolResult 属于哪次调用。
    """

    id: str
    name: str
    arguments: dict[str, Any]
    # arguments 是"模型生成的 JSON 文本"解析出来的结果 —— 解析可能失败。
    # 失败时 arguments 退化成 {}，原始串留在这里不丢。
    # 为什么不在这里就报错：转换层手里没有 ToolResult（那是执行工具之后才有的东西），
    # 它造不出错误该去的容器。所以它只负责把"解析不了"原样带下去，
    # 由 loop 变成 ToolResult(is_error=True) 还给模型，让它自己修参数。
    raw_arguments: str | None = None


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

    旧版本(已经改变)
    message 一定是 assistant 消息；stop_reason 由模型给，缺省时由 provider 兜底成
    "end_turn"（这一条是从 KamaClaude S1 学来的已知简化，后面要讲清它的风险）。
    新版本:
    message 一定是 assistant 消息；stop_reason 由模型给，认不出的 finish_reason
    一律落到 "unknown"，刻意不兜成 "end_turn"（映射表 §二 · 6 策略 B）。
    """

    message: Message
    stop_reason: LLMStopReason


@dataclass(frozen=True)
class StepEvent:
    """一次 run 里发生的单件事。**给人看的，模型永远看不到它。**

    和 Message 的分工（这条区分比字段本身重要）：
      - Message   是**模型视角**的对话上下文，下一轮 chat 要用它
      - StepEvent 是**人的视角**的观测记录，只用来调试、以及 M3 拿来做对比

    两者都放在 RunResult 里，但消费方不同。所以不是"过程记录一律不进结果"，
    而是"进结果的过程记录，要分清谁在消费"。

    字段按 kind 分工：
      kind == "model"        → stop_reason / duration_ms / error
      kind == "tool_start"   → tool_name / tool_call_id
      kind == "tool_end"     → tool_name / tool_call_id / is_error / duration_ms
    用不到的字段留 None，不做联合类型 —— 八个字段还没多到值得拆。
    """

    kind: EventKind
    step: int  # 第几次模型调用（1-based）

    stop_reason: LLMStopReason | None = None
    duration_ms: float | None = None

    tool_name: str | None = None
    tool_call_id: str | None = None
    is_error: bool | None = None

    error: str | None = None


@dataclass(frozen=True)
class RunResult:
    """一次 run 的结局。**循环结束后唯一被交出来的东西。**

    messages 是完整现场：出了问题先看它，不用加日志。
    events 是给人看的时间线：哪一步、花了多久、哪个工具错了。
    """

    status: Literal["ok", "failed"]
    stop_reason: RunStopReason
    final_text: str
    messages: tuple[Message, ...]
    steps: int  # 调用了几次模型（不是"跑了几圈工具"）
    error: str | None = None
    # 默认空 tuple：老代码不传 events 也能构造，是纯增量改动。
    # 用 default_factory 不用 = ()，是为了养成习惯 —— 哪天换成 list/dict，
    # = [] 就是共享可变默认值那个经典坑。
    events: tuple[StepEvent, ...] = field(default_factory=tuple)
