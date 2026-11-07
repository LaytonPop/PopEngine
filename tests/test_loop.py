"""M1 四个场景：把循环的每个判断都用假数据验证一遍。

这四个测试就是 M1 的验收标准，也是 `loop.py` 的任务书。
不许为了让测试变绿而改这里的断言 —— 断言写错了就说出来，我们一起改。

跑法：`uv run pytest tests/test_loop.py -v`
"""

import pytest
from fakes import AddTool, BoomTool, ScriptedProvider, assistant_text, assistant_tool_calls

from kama_engine.loop import AgentLoop
from kama_engine.tools import ToolRegistry
from kama_engine.types import LLMResponse, Message, ToolCall


def _loop(provider: ScriptedProvider, max_steps: int = 5) -> AgentLoop:
    return AgentLoop(provider, ToolRegistry([AddTool()]), max_steps=max_steps)


# ---------------------------------------------------------------- 场景 A
def test_a_end_turn_immediately() -> None:
    """模型一次就结束：循环跑一圈就退出，不再多调一次模型。"""
    provider = ScriptedProvider([assistant_text("你好，我是模型。")])

    result = _loop(provider).run("你好")

    assert result.status == "ok"
    assert result.stop_reason == "end_turn"
    assert result.final_text == "你好，我是模型。"
    assert result.steps == 1
    assert result.error is None

    # 现场：只有一问一答，没有多余消息
    assert [m.role for m in result.messages] == ["user", "assistant"]
    assert result.messages[0].content == "你好"

    # 模型只被调用一次，且第一次就拿到了工具说明书
    assert len(provider.calls) == 1
    first_messages, first_specs = provider.calls[0]
    assert [m.role for m in first_messages] == ["user"]
    assert [spec.name for spec in first_specs] == ["add"]


# ---------------------------------------------------------------- 场景 B
def test_b_two_tool_calls_then_end_turn() -> None:
    """模型一次要 2 个工具，拿到结果后再结束。

    这是 M1 最核心的一条：结果必须按 tool_use_id 回到正确位置，
    且消息顺序必须是 user → assistant(带 tool_calls) → tool → tool → assistant。
    """
    provider = ScriptedProvider(
        [
            assistant_tool_calls(
                ToolCall(id="call_1", name="add", arguments={"a": 1, "b": 2}),
                ToolCall(id="call_2", name="add", arguments={"a": 10, "b": 20}),
            ),
            assistant_text("第一个是 3，第二个是 30。"),
        ]
    )

    result = _loop(provider).run("帮我算 1+2 和 10+20")

    assert result.status == "ok"
    assert result.stop_reason == "end_turn"
    assert result.steps == 2
    assert result.final_text == "第一个是 3，第二个是 30。"

    # 完整现场
    assert [m.role for m in result.messages] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]
    # assistant 那条必须保留 tool_calls（否则 tool 消息就没有父节点了）
    assert [c.id for c in result.messages[1].tool_calls] == ["call_1", "call_2"]
    # 两条工具结果按调用顺序回填，各自身份正确
    tool_messages = [m for m in result.messages if m.role == "tool"]
    assert [m.tool_call_id for m in tool_messages] == ["call_1", "call_2"]
    assert [m.content for m in tool_messages] == ["3", "30"]

    # 关键：第二轮模型调用必须能看见上一条 assistant 和两条工具结果
    second_call_messages = provider.calls[1][0]
    assert [m.role for m in second_call_messages] == ["user", "assistant", "tool", "tool"]


# ---------------------------------------------------------------- 场景 C
def test_c_stop_at_max_steps() -> None:
    """模型永动机：到 max_steps 必须停，且模型调用次数绝不超过它。

    （至于停下时最后那次 tool_use 有没有配到结果，是实现自由度，这里不钉。）
    """
    forever = [
        assistant_tool_calls(ToolCall(id=f"call_{i}", name="add", arguments={"a": 1, "b": 1}))
        for i in range(10)
    ]
    provider = ScriptedProvider(forever)

    result = _loop(provider, max_steps=3).run("永动机")

    assert result.status == "failed"
    assert result.stop_reason == "max_steps"
    assert result.steps == 3
    assert len(provider.calls) == 3  # 护栏生效：绝不多要第 4 次
    assert result.messages[0].role == "user"


# ---------------------------------------------------------------- 场景 D
def test_d_provider_exception_is_captured() -> None:
    """模型层抛异常：循环不死，失败可读，现场保留。"""
    provider = ScriptedProvider([RuntimeError("connection reset by peer")])

    result = _loop(provider).run("你好")

    assert result.status == "failed"
    assert result.stop_reason == "error"
    assert result.error is not None
    assert "connection reset by peer" in result.error
    # 已经产生的消息不能丢 —— 出问题时 messages 就是唯一的现场
    assert [m.role for m in result.messages] == ["user"]
    assert len(provider.calls) == 1


# ---------------------------------------------------------------- 场景 E
def test_e_tool_failure_is_fed_back_and_loop_survives() -> None:
    """工具炸了：循环不能死，而且模型必须能看见这件事。

    这条是 tools.py 存在的理由 —— 失败被翻译成一条 tool 消息，
    而不是一个向上冒的异常。
    """
    provider = ScriptedProvider(
        [
            assistant_tool_calls(ToolCall(id="call_1", name="boom", arguments={})),
            assistant_text("工具挂了，我换个办法。"),
        ]
    )
    loop = AgentLoop(provider, ToolRegistry([AddTool(), BoomTool()]), max_steps=5)

    result = loop.run("试试那个会炸的工具")

    assert result.status == "ok"  # 循环活着走完了
    assert result.steps == 2
    failure_message = result.messages[2]
    assert failure_message.role == "tool"
    assert failure_message.tool_call_id == "call_1"
    assert "工具内部炸了" in failure_message.content


# ---------------------------------------------------------------- 构造器护栏
def test_max_steps_must_be_positive() -> None:
    """自己立的不变量也要有测试，不然就是没验证过的分支。"""
    provider = ScriptedProvider([])

    with pytest.raises(ValueError):
        AgentLoop(provider, ToolRegistry([AddTool()]), max_steps=0)


# ---------------------------------------------------------------- 场景 F
def test_f_unknown_stop_reason_stops_immediately() -> None:
    """unknown 必须当场收场，不许掉进工具分支空转到 max_steps。

    unknown 是「模型合法地以我们没建模的原因停下」（length 被截断 /
    content_filter 被拦）。这种响应的 tool_calls 一定是空的，所以它会
    悄悄穿过工具分支，一圈一圈把预算烧完，error 还是 None —— 2026-11-06
    复核实测：steps=8、模型调用 8 次、error=None。

    steps == 1 是这条测试的灵魂：ScriptedProvider 剧本里只有一条，loop
    若还想跑第二圈就会抛 AssertionError，被 loop 的 except 接住，steps 变 2。
    """
    provider = ScriptedProvider(
        [
            LLMResponse(
                message=Message(role="assistant", content=""),
                stop_reason="unknown",
            )
        ]
    )
    loop = AgentLoop(provider, ToolRegistry([AddTool()]), max_steps=8)

    result = loop.run("随便问点什么")

    assert result.status == "failed"
    assert result.stop_reason == "error"
    assert result.steps == 1
    assert "unknown" in (result.error or "")
