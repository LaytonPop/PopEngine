"""M4 第 1 关 · T0：run 收一份现场，而不是一句话。

判据不是"能传历史了"，是**故意把现场弄残也能救回来**。
五条，每条对应 `M4/M4第一关.md` 第四节 4.1 的一个不变量：

    1  顺序：history 在前，新消息在后       ← 顺序错是静默的
    2  零侵入：单条消息时行为与改动前一致   ← 17 处替换没改语义
    3  不修改入参                           ← `list(...)` 那一步的用途
    4  救回没人读的现场                     ← 这一关存在的理由
    5  现场累积、计数只算本轮               ← 两个概念不能混

跑法：`uv run pytest tests/test_run_timeline.py -v`
"""

import pytest
from fakes import ScriptedProvider, assistant_text, assistant_tool_calls, user_message

from pop_engine.loop import AgentLoop
from pop_engine.tools import ToolRegistry
from pop_engine.types import Message, ToolCall


def _loop(provider: ScriptedProvider) -> AgentLoop:
    return AgentLoop(provider, ToolRegistry([]), max_steps=5)


# ---------------------------------------------------------------- 1 顺序
def test_history_goes_before_the_new_message() -> None:
    """模型第一次调用看到的必须是 `history[0], history[1], 新消息`。

    顺序反了不会报错 —— 模型只会把"以前说过的"当成"刚说的"，
    然后开始圆一个不存在的约定。这是静默错里最难查的一种。
    """
    provider = ScriptedProvider([assistant_text("好")])
    history = [user_message("第一句"), user_message("第二句")]

    result = _loop(provider).run([*history, user_message("第三句")])

    assert result.status == "ok"
    sent, _ = provider.calls[0]
    assert [m.content for m in sent] == ["第一句", "第二句", "第三句"]


# ---------------------------------------------------------------- 2 零侵入
def test_single_message_behaves_like_before() -> None:
    """只有一条 user 时，行为与改动前完全一致（零侵入）。

    没有这条，17 处调用点的批量替换就没有对照物：
    改坏了也会全绿，因为断言都是关于"历史"的。
    """
    provider = ScriptedProvider([assistant_text("你好")])

    result = _loop(provider).run([user_message("你好")])

    assert result.status == "ok"
    assert result.stop_reason == "end_turn"
    assert result.steps == 1
    assert [m.role for m in result.messages] == ["user", "assistant"]


# ---------------------------------------------------------------- 3 不修改入参
def test_caller_list_is_not_mutated() -> None:
    """跑完之后，调用方传进来的那个 list 长度和内容都不变。

    钉的是 `messages = list(initial_messages)` 那一步 —— 少了它，
    loop 会一边跑一边往调用方的列表里 append，第二次 run 同一份历史时
    现场已经是脏的（而且脏得静默）。
    """
    provider = ScriptedProvider([assistant_text("好")])
    passed_in = [user_message("原始一句")]
    before = (len(passed_in), [m.content for m in passed_in])

    _loop(provider).run(passed_in)

    assert (len(passed_in), [m.content for m in passed_in]) == before
    assert len(passed_in) == 1


# ---------------------------------------------------------------- 4 救回现场
def test_resumes_a_scene_whose_tool_result_was_never_read() -> None:
    """末尾是一条没人读的 tool 结果 → 续跑，模型直接收工，工具 0 次执行。

    这是 T0 存在的全部理由：上一轮跑完（或被 kill 前）留下的工具结果
    已经躺在账本里，模型却没读过。把这份现场原样喂回 `run`，
    模型能接着用它 —— 而不是从头再问一遍、把副作用再执行一遍。

    ⚠️ 这里的现场是**手工构造**的，不取 `test_l` 的产出：
    `test_l` 的语义会随第 3 关（撞线禁工具）改掉，依赖它这条会跟着失效。
    手工构造的现场与撞线语义无关，跨关依然有效。
    """
    provider = ScriptedProvider([assistant_text("读到了，是 42")])
    tool_call = ToolCall(id="c1", name="count", arguments={})
    history = (
        user_message("数一下"),
        Message(role="assistant", tool_calls=(tool_call,)),
        Message(role="tool", content="第 1 次副作用已发生", tool_call_id="c1", name="count"),
    )

    # 传 tuple 不是笔误：`RunResult.messages` 就是 tuple，
    # 「把上一轮的现场原样送回」正是这条 API 的正经用法，必须被走过一次。
    result = _loop(provider).run(history)

    assert result.status == "ok"
    assert result.final_text == "读到了，是 42"
    assert result.steps == 1  # 一次调用就收工，没有重新跑一轮工具

    sent, tools = provider.calls[0]
    assert [m.role for m in sent] == ["user", "assistant", "tool"]
    assert sent[-1].content == "第 1 次副作用已发生"
    assert tools == []  # ToolRegistry([])：本测试不关心说明书


# ---------------------------------------------------------------- 5 两个概念
def test_scene_accumulates_but_steps_only_count_this_run() -> None:
    """现场是累积的（历史 + 本轮），`steps` 只数本轮。

    两句话是一对，缺一条就会被误用：有人会拿 `len(result.messages)` 当进度，
    也有人会以为 `steps` 是全会话累计。都不对。
    """
    provider = ScriptedProvider(
        [
            assistant_tool_calls(ToolCall(id="c1", name="count", arguments={})),
            assistant_text("好了"),
        ]
    )
    history = [user_message("旧的 1"), user_message("旧的 2")]

    result = _loop(provider).run([*history, user_message("新的")])

    assert result.status == "ok"
    assert result.steps == 2  # 只数本轮：两次模型调用
    # 现场：3 条历史/新消息 + 本轮 assistant(tool_calls) + tool + assistant = 6
    assert len(result.messages) == 6
    assert [m.content for m in result.messages[:3]] == ["旧的 1", "旧的 2", "新的"]


# ---------------------------------------------------------------- 守卫
def test_empty_input_raises_instead_of_sending_nothing() -> None:
    """空入参当场报错，而不是让 `to_wire([])` 静默返回 `[]` 发出去。

    `loop.py` 对入参零语义检查（历史合不合法归 session 层），
    但"你什么都没给"必须挡在这里 —— 空值一路无声到 API 才 400，最难查。
    """
    provider = ScriptedProvider([assistant_text("不该被调用")])

    with pytest.raises(ValueError, match="不能为空"):
        _loop(provider).run([])

    assert provider.calls == []  # 一次模型调用都没发生
