"""M1 四个场景：把循环的每个判断都用假数据验证一遍。

这四个测试就是 M1 的验收标准，也是 `loop.py` 的任务书。
不许为了让测试变绿而改这里的断言 —— 断言写错了就说出来，我们一起改。

跑法：`uv run pytest tests/test_loop.py -v`
"""

from fakes import AddTool, ScriptedProvider, assistant_text, assistant_tool_calls

from kama_engine.loop import AgentLoop
from kama_engine.tools import ToolRegistry
from kama_engine.types import ToolCall


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
