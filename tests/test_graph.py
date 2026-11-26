"""graph 执行器的离线测试：不接模型，节点和 router 都是普通函数。

第一个测试就是任务书第五节的检查点 —— **反向验收**：
max_visits=1 + 必然回退的 A→B→A，报错必须说清「哪个节点被访问了几次」。
说不清，后面的对比数字都不用看。
"""

from typing import Any

import pytest

from pop_engine.graph.engine import GraphEngine


def _a(state: dict[str, Any]) -> dict[str, Any]:
    return {"from_a": True}


def _b(state: dict[str, Any]) -> dict[str, Any]:
    return {"from_b": True}


def _ping_pong(state: dict[str, Any]) -> str:
    """a ↔ b 永远互相回退，这个图永远不会自然收工。"""
    return "b" if state["current_node"] == "a" else "a"


# ---------------------------------------------------------------- 反向验收
def test_max_visits_names_the_node_and_the_count() -> None:
    """A→B→A，max_visits=1：第二次踏进 a 必须被拦下。

    断言的是报错内容，不是"失败了"：
      · 节点名 'a'
      · 被访问 2 次
      · 上限 1
    三样缺一，人就得靠猜；写句"超过限制"等于没写。
    """
    engine = GraphEngine(
        {"a": _a, "b": _b},
        _ping_pong,
        entry="a",
        max_steps=8,
        max_visits=1,
    )

    result = engine.run("随便")

    assert result.status == "failed"
    assert result.stop_reason == "error"
    assert "a" in (result.error or "")
    assert "2 次" in (result.error or "")
    assert "上限 1" in (result.error or "")


def test_max_visits_failure_is_not_reported_as_max_steps() -> None:
    """两道护栏各记各的账，不能互相掩盖。

    A→B→A 在第 3 步就撞 max_visits 了，离 max_steps=8 还很远；
    报错里出现 max_steps 就说明它把原因指错了地方。
    """
    engine = GraphEngine(
        {"a": _a, "b": _b},
        _ping_pong,
        entry="a",
        max_steps=8,
        max_visits=1,
    )

    result = engine.run("随便")

    assert "max_steps" not in (result.error or "")


# ---------------------------------------------------------------- 正常收工
def test_router_none_finishes_with_ok() -> None:
    """router 返回 None = 结构说"收工"，和 loop 的 end_turn 对应。"""

    def node(state: dict[str, Any]) -> dict[str, Any]:
        return {"final_text": "图跑完了"}

    engine = GraphEngine({"only": node}, lambda state: None, entry="only")

    result = engine.run("问点什么")

    assert result.status == "ok"
    assert result.stop_reason == "end_turn"
    assert result.final_text == "图跑完了"
    assert result.error is None


def test_state_patch_is_merged_into_the_blackboard() -> None:
    """节点返回的是"要合并的键"，不是一份新 state。

    第二个节点必须能读到第一个节点写进去的东西 —— 这是黑板的意义。
    """
    seen: list[Any] = []

    def first(state: dict[str, Any]) -> dict[str, Any]:
        return {"n": 1}

    def second(state: dict[str, Any]) -> dict[str, Any]:
        seen.append(state.get("n"))
        return {"n_plus_one": state["n"] + 1}

    def router(state: dict[str, Any]) -> str | None:
        return "second" if state["current_node"] == "first" else None

    engine = GraphEngine({"first": first, "second": second}, router, entry="first")

    result = engine.run("黑板")

    assert result.status == "ok"
    assert seen == [1]


# ---------------------------------------------------------------- 两道护栏
def test_max_steps_names_itself_and_the_last_node() -> None:
    """max_visits 放宽后，永动图由 max_steps 收场，且报错指向 max_steps。"""
    engine = GraphEngine(
        {"a": _a, "b": _b},
        _ping_pong,
        entry="a",
        max_steps=4,
        max_visits=99,
    )

    result = engine.run("永远来回")

    assert result.status == "failed"
    assert result.stop_reason == "max_steps"
    assert "max_steps=4" in (result.error or "")
    assert "max_visits" not in (result.error or "")


def test_max_visits_counts_per_node_not_globally() -> None:
    """同一个节点被访问多次才算超限；四个不同节点各走一次不算。

    这是"按节点计数"和"全局计数"的分水岭：前者是护栏，
    后者会把正常的多节点路径误杀。
    """
    order = ["n1", "n2", "n3", "n4"]

    def router(state: dict[str, Any]) -> str | None:
        i = order.index(state["current_node"])
        return order[i + 1] if i + 1 < len(order) else None

    nodes = {name: (lambda state, name=name: {f"seen_{name}": True}) for name in order}

    engine = GraphEngine(nodes, router, entry="n1", max_visits=1, max_steps=10)

    result = engine.run("走一遍")

    assert result.status == "ok"


# ---------------------------------------------------------------- 形状护栏
def test_two_nodes_writing_the_same_key_raise() -> None:
    """节点返回的键必须不相交 —— 串行时撞不上，这条是给并行留的。

    报错要说清是哪个键、被哪两个节点碰过，不然并行一开就是静默覆盖。
    """

    def first(state: dict[str, Any]) -> dict[str, Any]:
        return {"x": 1}

    def second(state: dict[str, Any]) -> dict[str, Any]:
        return {"x": 2}

    def router(state: dict[str, Any]) -> str | None:
        return "second" if state["current_node"] == "first" else None

    engine = GraphEngine({"first": first, "second": second}, router, entry="first")

    with pytest.raises(ValueError, match="x"):
        engine.run("撞键")


def test_unknown_node_from_router_is_a_clear_failure() -> None:
    """router 指到不存在的节点：当场失败，报错列出已有节点。"""
    engine = GraphEngine({"a": _a}, lambda state: "does_not_exist", entry="a")

    result = engine.run("指错路")

    assert result.status == "failed"
    assert "does_not_exist" in (result.error or "")


def test_entry_must_exist() -> None:
    with pytest.raises(ValueError):
        GraphEngine({"a": _a}, lambda state: None, entry="missing")


def test_max_visits_must_be_positive() -> None:
    with pytest.raises(ValueError):
        GraphEngine({"a": _a}, lambda state: None, entry="a", max_visits=0)


def test_nodes_cannot_write_current_node() -> None:
    """current_node 是引擎自己的键，节点写它就是抢引擎的账本。"""

    def greedy(state: dict[str, Any]) -> dict[str, Any]:
        return {"current_node": "b"}

    engine = GraphEngine({"a": greedy, "b": _b}, lambda state: None, entry="a")

    with pytest.raises(ValueError, match="current_node"):
        engine.run("抢键")
