"""M1 四个场景：把循环的每个判断都用假数据验证一遍。

这四个测试就是 M1 的验收标准，也是 `loop.py` 的任务书。
不许为了让测试变绿而改这里的断言 —— 断言写错了就说出来，我们一起改。

跑法：`uv run pytest tests/test_loop.py -v`
"""

import pytest
from fakes import (
    AddTool,
    BoomTool,
    CountingTool,
    ScriptedProvider,
    assistant_text,
    assistant_tool_calls,
)

from pop_engine.loop import AgentLoop
from pop_engine.tools import ToolRegistry
from pop_engine.types import LLMResponse, Message, ToolCall


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


# ---------------------------------------------------------------- 事件（c 轮）
def test_g_end_turn_records_one_model_event() -> None:
    """一问一答：只有一条 model 事件，且把它该说的都说全了。

    事件是"给人看的时间线"，所以除了条数，还要验它带上了 step 和 stop_reason ——
    只断言 len == 1 的话，一个空的 StepEvent 也能过。
    """
    provider = ScriptedProvider([assistant_text("你好。")])

    result = _loop(provider).run("打个招呼")

    assert len(result.events) == 1
    event = result.events[0]
    assert event.kind == "model"
    assert event.step == 1
    assert event.stop_reason == "end_turn"
    # 计时必须真的做了：0 是合法耗时，None 说明压根没埋点。
    assert event.duration_ms is not None
    assert event.duration_ms >= 0


def test_h_tool_call_records_start_and_end_in_order() -> None:
    """一次工具调用 = start + end 两条，且顺序不能反。

    断言的是完整的 kind 序列，不是"有没有两条 tool 事件" ——
    后者对「先 end 后 start」这种错误照样是绿的。
    而顺序正是这份日志的意义：它要能还原"先要工具、再拿到结果"这件事。
    """
    provider = ScriptedProvider(
        [
            assistant_tool_calls(ToolCall(id="call_1", name="add", arguments={"a": 1, "b": 2})),
            assistant_text("算完了，是 3。"),
        ]
    )

    result = _loop(provider).run("1 加 2 等于几")

    assert [event.kind for event in result.events] == [
        "model",
        "tool_start",
        "tool_end",
        "model",
    ]
    start, end = result.events[1], result.events[2]
    # 两条必须对得上同一个调用，否则日志没法把"开始"和"结束"配成一对。
    assert start.tool_call_id == end.tool_call_id == "call_1"
    assert start.tool_name == end.tool_name == "add"
    assert end.is_error is False
    assert end.duration_ms is not None


def test_i_tool_failure_is_visible_as_is_error() -> None:
    """工具炸了也要留痕：is_error 是给日志用的，模型看到的只是那段文字。

    这条同时在验一件历史遗留：ToolResult.is_error 造出来之后一直没人读过，
    loop 只用 content 和 tool_call_id。这里是它第一个消费方。
    """
    provider = ScriptedProvider(
        [
            assistant_tool_calls(ToolCall(id="call_1", name="boom", arguments={})),
            assistant_text("工具挂了，我换个办法。"),
        ]
    )
    loop = AgentLoop(provider, ToolRegistry([AddTool(), BoomTool()]))

    result = loop.run("试试那个会炸的工具")

    end = result.events[2]
    assert end.kind == "tool_end"
    assert end.is_error is True


def test_j_provider_exception_still_records_an_event() -> None:
    """模型层抛异常，也要留下一条事件。

    不然"这次 run 花了多久才炸"就无从谈起 —— 失败路径的事件
    恰恰是调 agent 时最想看的那一条。
    """
    provider = ScriptedProvider([RuntimeError("connection reset by peer")])

    result = _loop(provider).run("随便问问")

    assert len(result.events) == 1
    event = result.events[0]
    assert event.kind == "model"
    assert event.stop_reason is None  # 没拿到响应，自然没有结束原因
    assert event.error is not None
    assert "connection reset by peer" in event.error
    assert event.duration_ms is not None


# ------------------------------------------------- 预算语义的边界（M3 前置）
def test_k_last_step_end_turn_still_succeeds() -> None:
    """恰好用满预算的那一步答完，算成功。

    这是**特征测试**（characterization test），不是修 bug —— 现在就是这个行为，
    写下来是为了把它从"靠 return 摆的位置凑出来的事实"变成"一条明说的契约"。

    为什么要钉：任何一次把控制流从 for 改成 while、或给末尾加兜底的人，
    都可能顺手把它改成"用满预算 = 失败"。那是个静悄悄的行为变更，
    没有测试就没人会发现。
    """
    calls = [
        assistant_tool_calls(ToolCall(id="c1", name="add", arguments={"a": 1, "b": 1})),
        assistant_tool_calls(ToolCall(id="c2", name="add", arguments={"a": 2, "b": 2})),
        assistant_text("好了，答案是 2。"),  # ← 第 3 次 = 预算的最后一次
    ]
    provider = ScriptedProvider(calls)

    result = _loop(provider, max_steps=3).run("数到三")

    assert result.status == "ok"
    assert result.stop_reason == "end_turn"
    assert result.final_text == "好了，答案是 2。"
    assert result.steps == 3
    assert result.error is None
    # 预算限的是调用次数：正好 3 次，没有第 4 次
    assert len(provider.calls) == 3


def test_l_last_step_tool_use_leaves_an_orphan_side_effect() -> None:
    """用满预算那一步还在要工具：工具照常执行，结果却喂不回模型。

    钉住一个真实存在、且**不好**的行为（2026-11-12 读代码发现）：

        第 N 次调用返回 tool_calls
        → loop 照常执行这批工具（副作用已发生，不可逆）
        → 结果 append 进 messages
        → 掉出 for，返回 failed/max_steps
        → **那份工具结果，模型永远读不到**

    为什么留着不修：修它要动控制流（最后一步不给工具，或允许一次只读收尾），
    那是 M5 的事，M3 要保持两个引擎的控制流形状一致才比得公平。
    但"留着"和"不知道"是两件事 —— 这条测试就是那句"我知道"。

    M5 的"重跑一次，副作用会不会执行两遍"那个问题，起点就在这。
    """
    counter = CountingTool()

    def _count_call(i: int) -> LLMResponse:
        return assistant_tool_calls(ToolCall(id=f"c{i}", name="count", arguments={}))

    provider = ScriptedProvider([_count_call(i) for i in range(10)])
    loop = AgentLoop(provider, ToolRegistry([counter]), max_steps=3)

    result = loop.run("一直数")

    assert result.status == "failed"
    assert result.stop_reason == "max_steps"
    assert result.steps == 3

    # 三次都执行了 —— 包括最后一次，它的产出没有任何人消费
    assert len(counter.calls) == 3

    # 现场里确实躺着那三条工具结果：数据在，只是没人读
    tool_messages = [m for m in result.messages if m.role == "tool"]
    assert len(tool_messages) == 3
    assert all("副作用已发生" in (m.content or "") for m in tool_messages)

    # 事件层也看得见：最后一次 tool_end 和 max_steps 结局同时存在
    tool_ends = [e for e in result.events if e.kind == "tool_end"]
    assert len(tool_ends) == 3
    assert tool_ends[-1].step == 3


def test_l2_orphan_side_effect_is_only_at_the_boundary() -> None:
    """对照：没撞上预算时，工具结果一定被模型读到 —— 孤儿只发生在边界上。

    没有这条对照，上面那个测试也可以被解读成"工具结果本来就不回填"。
    两条一起看才说得清：孤儿副作用是**边界现象**，不是常态。
    """
    counter = CountingTool()

    provider = ScriptedProvider(
        [
            assistant_tool_calls(ToolCall(id="c1", name="count", arguments={})),
            assistant_text("数完了"),
        ]
    )
    loop = AgentLoop(provider, ToolRegistry([counter]), max_steps=5)

    result = loop.run("数一次")

    assert result.status == "ok"
    assert len(counter.calls) == 1
    # 第 2 次调用时，模型确实看见了上一次的工具结果
    second_messages, _ = provider.calls[1]
    assert any(m.role == "tool" for m in second_messages)
