import json
from pathlib import Path

from kama_engine.openai_compat import from_wire, to_wire
from kama_engine.types import Message, ToolCall


def load_response() -> dict:
    """每次重新读取并解析 fixture，避免测试之间共享可变字典。"""
    path = Path(__file__).parent / "fixtures" / "deepseek_tool_calls.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_from_wire_real_tool_call_response() -> None:
    # Arrange：读取未经修改的真实 DeepSeek 工具调用响应。
    data = load_response()
    # Act：把外部协议字典转换为引擎内部响应类型。
    response = from_wire(data)
    # Assert：正文、调用身份和参数都应完整保留并完成类型转换。
    assert response.stop_reason == "tool_use"
    assert response.message.role == "assistant"
    assert response.message.content == "我先读一下这个文件。"
    assert len(response.message.tool_calls) == 1
    call = response.message.tool_calls[0]
    assert call.id == "call_00_ET_9ywpIUwsKe7aCs23AdiM4605"
    assert call.name == "read_file"
    assert call.arguments == {"path": "README.md"}
    assert call.raw_arguments is None


def test_from_wire_null_content_becomes_empty_str() -> None:
    # Arrange：只把真实响应的正文改成协议允许的 null。
    data = load_response()
    data["choices"][0]["message"]["content"] = None
    # Act：转换这份仍然包含正常工具调用的响应。
    response = from_wire(data)
    # Assert：正文归一化为空字符串，同时不能误伤调用和结束原因。
    assert response.message.content == ""
    assert response.stop_reason == "tool_use"
    assert response.message.tool_calls[0].arguments == {"path": "README.md"}


def test_from_wire_bad_arguments_does_not_raise() -> None:
    # Arrange：仅破坏 function.arguments 内部的 JSON 文本。
    data = load_response()
    bad = '{"path": "a.p'
    wire_call = data["choices"][0]["message"]["tool_calls"][0]
    wire_call["function"]["arguments"] = bad
    # Act：坏参数应被转换层接住，而不是向 pytest 抛出解析异常。
    response = from_wire(data)
    # Assert：内部保持字典形状，并保存原文供诊断，其他字段不受影响。
    call = response.message.tool_calls[0]
    assert call.arguments == {}
    assert call.raw_arguments == bad
    assert call.name == "read_file"
    assert response.message.content == "我先读一下这个文件。"


def test_from_wire_unknown_finish_reason_maps_to_unknown() -> None:
    # Arrange / Act / Assert：每轮重新读样本，只替换一个结束原因后立即验证。
    for reason in ("length", "insufficient_system_resource"):
        data = load_response()
        data["choices"][0]["finish_reason"] = reason
        response = from_wire(data)
        assert response.stop_reason == "unknown", reason


def test_to_wire_tool_message_omits_name() -> None:
    # Arrange：故意设置 name，确认转换器不会把内部可选字段错误透传。
    message = Message(
        role="tool",
        content="README 的内容",
        tool_call_id="call_1",
        name="read_file",
    )
    # Act：将一条内部工具结果转换为外部 messages 元素。
    wire = to_wire([message])
    # Assert：工具结果只携带协议要求的关联 ID 和正文，不携带 name。
    assert len(wire) == 1
    assert "name" not in wire[0]
    assert wire[0] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "README 的内容",
    }


def test_from_wire_parallel_tool_calls_keeps_every_call() -> None:
    """模型一次要两个工具时，两个都得留下。

    这条不是补覆盖率：真实响应里只有一个调用，所以"只转换最后一个"这种
    差一层的写法在 fixture 上永远是绿的。必须造出第二个调用才看得见。
    """
    # Arrange：在真实调用的基础上手工补一个并列调用。
    data = load_response()
    first = data["choices"][0]["message"]["tool_calls"][0]
    second = json.loads(json.dumps(first))
    second["id"] = "call_second"
    second["function"] = {"name": "list_dir", "arguments": '{"path": "."}'}
    data["choices"][0]["message"]["tool_calls"] = [first, second]
    # Act
    response = from_wire(data)
    # Assert：顺序和内容都要保住，少一个就是静默丢数据。
    ids = [call.id for call in response.message.tool_calls]
    assert ids == ["call_00_ET_9ywpIUwsKe7aCs23AdiM4605", "call_second"]
    assert response.message.tool_calls[1].name == "list_dir"
    assert response.message.tool_calls[1].arguments == {"path": "."}


def test_to_wire_preserves_raw_arguments() -> None:
    """坏参数在回程不能被洗成 {}。

    from_wire 把解析失败原文留在 raw_arguments 里，是为了下一轮还能把
    模型自己写的那串东西原样发回去。如果 to_wire 只看 arguments（此时是
    {}），模型会看到一次跟自己写的不一样的调用 —— 信息在回程路上丢了。
    """
    # Arrange：模拟"模型给了坏参数"那一轮留在历史里的 assistant 消息。
    bad = '{"path": "a.p'
    message = Message(
        role="assistant",
        content="",
        tool_calls=(ToolCall(id="call_1", name="read_file", arguments={}, raw_arguments=bad),),
    )
    # Act
    wire = to_wire([message])
    # Assert：发回去的还是原来那串，不是 "{}"。
    assert wire[0]["tool_calls"][0]["function"]["arguments"] == bad
