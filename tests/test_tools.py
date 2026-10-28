"""工具层自己的契约：invoke() 永远返回 ToolResult，永远不抛异常。

这一层的测试和 loop 无关，所以单独一个文件：
就算以后 loop 全重写，这几条也该继续绿。
"""

from fakes import AddTool, BoomTool

from kama_engine.tools import ToolRegistry
from kama_engine.types import ToolCall


def test_invoke_runs_the_tool() -> None:
    registry = ToolRegistry([AddTool()])

    result = registry.invoke(ToolCall(id="call_1", name="add", arguments={"a": 2, "b": 3}))

    assert result.tool_call_id == "call_1"
    assert result.content == "5"
    assert result.is_error is False


def test_unknown_tool_is_an_error_result() -> None:
    """模型编了一个不存在的工具名 —— 这是常态，不能崩。"""
    registry = ToolRegistry([AddTool()])

    result = registry.invoke(ToolCall(id="call_9", name="nope", arguments={}))

    assert result.tool_call_id == "call_9"
    assert result.is_error is True
    assert "nope" in result.content


def test_raising_tool_is_an_error_result() -> None:
    """BoomTool 存在的唯一目的：证明异常不会从这一层漏出去。"""
    registry = ToolRegistry([BoomTool()])

    result = registry.invoke(ToolCall(id="call_1", name="boom", arguments={}))

    assert result.is_error is True
    assert "RuntimeError" in result.content
    assert "工具内部炸了" in result.content


def test_specs_are_what_the_model_sees() -> None:
    registry = ToolRegistry([AddTool(), BoomTool()])

    assert sorted(spec.name for spec in registry.specs()) == ["add", "boom"]
