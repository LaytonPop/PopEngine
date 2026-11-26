"""`agent_node` 的契约测试：把一次 AgentLoop.run 包成 graph 节点。

分三批：
  · 键的形状 / prompt 两种形态 / 结局映射 —— 锁已经做对的设计。
  · 事件通道 —— **M3 的命门**。这三条曾经红着（引擎把 `_subrun` 当普通数据
    写进白板就丢了，父 run 的 events 恒为空、三个指标全零），现在全绿。
    它们用 events 算 model_calls，等于把"测量通道通不通"变成了测试。
  · 边界 —— 不接 agent_node 的图必须是 0 而不是 None；`_subrun` 里放错类型要当场报。

为什么这批测试全都用 ScriptedProvider（假模型），不用真模型：
    agent_node 要保证的是**接线正确**（prompt 进去了、结果写回了、失败没外抛），
    跟模型的智能程度无关。真模型没法稳定制造"子 run 失败"这种剧本。
"""

from __future__ import annotations

from typing import Any

import pytest
from fakes import ScriptedProvider, assistant_text, assistant_tool_calls

from pop_engine.graph import GraphEngine, agent_node
from pop_engine.graph.types import SUB_RUN_KEY
from pop_engine.loop import AgentLoop
from pop_engine.metrics import summarize
from pop_engine.tools import ToolRegistry
from pop_engine.types import ToolCall


def _loop(*script: Any) -> AgentLoop:
    """造一个只跑脚本的 loop。不传剧本时默认一轮就说完了。"""
    return AgentLoop(ScriptedProvider(list(script) or [assistant_text("done")]), ToolRegistry([]))


# ------------------------------------------------------------------ 键的形状
def test_node_writes_output_key_plus_three_side_keys() -> None:
    """节点必须交出 4 个业务键 + 1 个保留键 `_subrun`。

    业务键带 output_key 前缀，是为了让同一个图里的多个 agent_node **互不相交** ——
    这正是 GraphEngine 的 owners 检查要求的。
    """
    node = agent_node(_loop(), "固定 prompt", "scan_out")

    patch = node({"task": "读仓库"})

    assert set(patch) == {
        "scan_out",
        "scan_out_status",
        "scan_out_stop_reason",
        "scan_out_error",
        "_subrun",
    }


def test_output_key_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        agent_node(_loop(), "p", "")


@pytest.mark.parametrize("reserved", ["_subrun", "current_node", "prompt"])
def test_reserved_output_key_is_rejected(reserved: str) -> None:
    """这三个键归引擎/运输通道所有，节点不能抢。

    `current_node` 是引擎的脚印；`prompt` 是用户输入；`_subrun` 是事件通道。
    抢了前两个会让 owners 检查或 router 读错；抢了第三个会和别的 agent_node 撞。
    """
    with pytest.raises(ValueError, match="保留"):
        agent_node(_loop(), "p", reserved)


# ------------------------------------------------------------------ prompt 两种形态
def test_string_prompt_is_sent_verbatim() -> None:
    """固定 prompt：state 里有什么都不影响它。"""
    loop = _loop()
    node = agent_node(loop, "只读 README", "out")

    node({"task": "别的东西"})

    sent = loop.provider.calls[0][0]  # type: ignore[attr-defined]
    assert [m.content for m in sent] == ["只读 README"]


def test_function_prompt_reads_state_at_call_time() -> None:
    """函数 prompt：每次运行前读一遍黑板 —— 这是重试能带上上一轮结果的前提。

    断言的是一段具体的拼装，而不是"调用了函数"：
    重试场景要的是「上一轮的 draft 和 problems 真的进了 prompt」。
    """
    loop = _loop()
    node = agent_node(
        loop,
        lambda s: f"改这份草案：{s['draft']}\n问题：{s['problems']}",
        "out",
    )

    node({"draft": "v1", "problems": "缺 entrypoints"})

    sent = loop.provider.calls[0][0]  # type: ignore[attr-defined]
    assert sent[0].content == "改这份草案：v1\n问题：缺 entrypoints"


# ------------------------------------------------------------------ 结局映射
def test_successful_subrun_maps_to_output_and_status() -> None:
    node = agent_node(_loop(assistant_text("五个键都齐了")), "p", "out")

    patch = node({})

    assert patch["out"] == "五个键都齐了"
    assert patch["out_status"] == "ok"
    assert patch["out_stop_reason"] == "end_turn"
    assert patch["out_error"] is None


def test_failed_subrun_is_returned_as_patch_not_raised() -> None:
    """子 run 炸了，节点**不往外抛**，而是把失败写进 patch。

    这条跟 loop 里「工具失败不抛异常，失败也是一种结果」是同一条规矩：
    一个阶段失败，图还能走别的分支（或者被 router 看出来）。
    节点一旦抛，GraphEngine 只能整轮作废。
    """
    node = agent_node(_loop(RuntimeError("provider 挂了")), "p", "out")

    patch = node({})  # 关键：这里不 pytest.raises

    assert patch["out_status"] == "failed"
    assert patch["out_stop_reason"] == "error"
    assert "provider 挂了" in (patch["out_error"] or "")
    assert patch["out"] == ""


# ------------------------------------------------------------------ 事件通道（M3 的命门）
def test_subrun_events_reach_the_parent_run() -> None:
    """M3 的三个指标全靠 `RunResult.events`。子 run 的事件进不来，graph 那行填不出来。

    这条曾经是红的（engine 把 `_subrun` 当普通数据写进白板就丢了，
    父 run 的 events 恒为空、三个指标全零）。现在它是回归测试。
    """
    node = agent_node(_loop(), "p", "out")

    result = GraphEngine({"scan": node}, lambda state: None, entry="scan").run("任务")

    assert summarize(result.events)["model_calls"] == 1.0
    assert summarize(result.events)["total_ms"] >= 0.0
    assert result.steps == 1


def _two_stage() -> GraphEngine:
    """scan → read → 收工。多阶段图是 M3 对比的基本形态。"""
    scan = agent_node(_loop(assistant_text("列完了")), "扫描", "scan_out")
    read = agent_node(_loop(assistant_text("读完了")), "读文件", "read_out")

    def router(state: dict[str, Any]) -> str | None:
        return "read" if state["current_node"] == "scan" else None

    return GraphEngine({"scan": scan, "read": read}, router, entry="scan")


def test_two_agent_nodes_can_share_one_graph() -> None:
    """两个 agent_node 能放进同一个图。

    这条曾经是红的：`_subrun` 是全局共享键，第二个 agent_node 就被 owners 判成撞车。
    跟当初每个节点都写 `last_status` 是同一个病，只是换了个名字。
    """
    result = _two_stage().run("任务")

    assert result.status == "ok"
    assert result.steps == 2
    assert len([e for e in result.events if e.kind == "model"]) == 2


def test_subrun_events_are_tagged_with_the_node_name() -> None:
    """归属靠 `StepEvent.node`，不靠给 step 加偏移。

    断言的正是"不加偏移"这个决定：两段子 run 的 step 都从 1 开始，
    在父 run 里可以出现同号的 step，只要 node 不同就能分开。
    如果哪天有人给它加全局偏移，这条会红 —— 那正是需要重新讨论的时刻。
    """
    result = _two_stage().run("任务")

    model_events = [e for e in result.events if e.kind == "model"]
    assert [(e.node, e.step) for e in model_events] == [("scan", 1), ("read", 1)]


def test_subrun_key_never_lands_on_the_blackboard() -> None:
    """`_subrun` 是运输通道：引擎摘走它，下一个节点读到的白板里不该有它。"""
    seen: list[dict[str, Any]] = []

    def probe(state: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(state))
        return {}

    node = agent_node(_loop(), "p", "out")

    def router(state: dict[str, Any]) -> str | None:
        return "probe" if state["current_node"] == "scan" else None

    GraphEngine({"scan": node, "probe": probe}, router, entry="scan").run("任务")

    assert SUB_RUN_KEY not in seen[0]
    assert "out" in seen[0]


def test_final_messages_come_from_the_last_subrun() -> None:
    """`messages` 放最后一次子 run 的现场 —— 出问题时人先看最后一步。"""
    result = _two_stage().run("任务")

    assert result.messages[-1].content == "读完了"


def test_plain_node_graph_reports_zero_model_calls() -> None:
    """不接 agent_node 的图里一次模型都没调 —— 必须是 0，不是 None。

    这条是给对比表打底的：`0` 和"没填"要能分得开。
    """
    result = GraphEngine({"a": lambda state: {}}, lambda state: None, entry="a").run("任务")

    assert result.status == "ok"
    assert result.events == ()
    assert summarize(result.events) == {"model_calls": 0.0, "total_ms": 0.0, "steps": 0.0}


def test_sub_run_key_must_hold_a_run_result() -> None:
    """`_subrun` 里放别的东西 → 当场报明确的类型错，不是三层之外的 AttributeError。"""

    def bad(state: dict[str, Any]) -> dict[str, Any]:
        return {SUB_RUN_KEY: "不是 RunResult"}

    with pytest.raises(TypeError, match="RunResult"):
        GraphEngine({"a": bad}, lambda state: None, entry="a").run("任务")


# ------------------------------------------------------------------ 假绿：子 run 失败不许报 ok
def test_failed_subrun_makes_the_whole_graph_fail() -> None:
    """子 run 失败 → 整图 failed，而不是路由走完报 ok。

    原症状（三次真跑全部命中）：report 节点撞 max_steps，`final_text` 是空串，
    而 `GraphEngine.run()` 返回 `status="ok"`。**对比表上会显示"跑通了"，只是内容全空。**
    这种假绿比报错危险 —— 报错会让人去看，假绿会让人继续往下走。
    """
    # max_steps=1 + 第 1 次就要工具 → 子 run failed/max_steps
    loop = AgentLoop(
        ScriptedProvider(
            [assistant_tool_calls(ToolCall(id="c1", name="不存在的工具", arguments={}))]
        ),
        ToolRegistry([]),
        max_steps=1,
    )
    node = agent_node(loop, "p", "out")

    result = GraphEngine({"scan": node}, lambda state: None, entry="scan").run("任务")

    assert result.status == "failed"
    assert result.stop_reason == "error"
    assert "scan" in (result.error or "")
    assert "max_steps" in (result.error or "")


def test_successful_graph_still_reports_ok() -> None:
    """对照组：子 run 都成功时，图必须还是 ok。

    没有这条，上一条可以通过"永远报 failed"来作弊。
    """
    node = agent_node(_loop(), "p", "out")

    result = GraphEngine({"scan": node}, lambda state: None, entry="scan").run("任务")

    assert result.status == "ok"
    assert result.error is None
