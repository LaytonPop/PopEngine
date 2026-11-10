import json
from collections.abc import Sequence
from typing import Any

from pop_engine.types import LLMResponse, LLMStopReason, Message, ToolCall, ToolSpec


def to_wire(messages: Sequence[Message]) -> list[dict]:
    """
    why:loop 不应该知道openai的字段包装，而是由转换层统一翻译历史消息

    what:输入的是内部的消息序列，输出是请求的messages字段需要的字典列表，简单来说就是把内部消息对象转换成外部请求需要的字典列表

    diff:role=tool,role=assistant,role=user,role=system
    """

    # how 1、创建空结果列表，然后按输入顺序遍历每条Message
    result: list[dict[str, Any]] = []
    for message in messages:
        # how 2、为每个消息建立最小字典
        item: dict[str, Any] = {"role": message.role, "content": message.content}

        # how 3、如果roleshi tool,那么花如意tool_call_id,不加入name
        if message.role == "tool":
            # 可能出现的问题
            # error:
            # 无法将“str | None”类型的参数分配给函数“__setitem__”中类型为“str”的参数“value”
            # 类型“str | None”不可分配给类型“str”“None”不可分配给“str”
            # 解决就算应用from typing import Any，
            # item["tool_call_id"]=message.tool_call_id
            tool_call_id = message.tool_call_id
            if tool_call_id is None:
                raise ValueError("工具使用必须要有有效的tool_call_id")
            item["tool_call_id"] = tool_call_id
        # how 4、如果role是assistant且tool_calls非空，逐个转换ToolCall
        if message.role == "assistant" and message.tool_calls:
            # 可能出现的问题
            # error:
            # 无法将“list[Any]”类型的参数分配给函数“__setitem__”中类型为“str”的参数“value”
            # “list[Any]”不可分配给“str”
            # item["tool_calls"]=[]

            tool_calls: list[dict[str, Any]] = []
            for call in message.tool_calls:
                # AI 改: 原来这一行是 json.dumps(call.arguments, ...)。
                # 问题是 arguments 不一定可信：from_wire 解析失败时把 arguments
                # 退化成 {}、原文留在 raw_arguments 里（types.py 里就是为这留的字段）。
                # 只 dumps arguments 会把那串坏 JSON 洗成 "{}" 发回去 —— 模型看到的
                # 调用和它自己写的不一样，信息在回程路上丢了。
                # 修法: 有原串就用原串，没有才序列化。
                arguments_text = (
                    call.raw_arguments
                    if call.raw_arguments is not None
                    else json.dumps(call.arguments, ensure_ascii=False)
                )
                call_dict = {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": arguments_text,
                    },
                }
                tool_calls.append(call_dict)
            item["tool_calls"] = tool_calls
        # how 5、user/system在当前文本子集中保持role/content即可
        # 当前任务不支持把 name 等其他可选字段无差别发给所有角色。
        # 如果未来要支持 user/assistant 的可选 name，应明确加入角色分支和测试。
        # 无论 assistant 有没有工具调用，都不要把原有 content 强行改成空串。

        # how 6、将当前item加入结果列表，循环结束后返回列表
        result.append(item)

    return result


def to_wire_tools(specs: Sequence[ToolSpec]) -> list[dict]:
    """
    WHY：内部工具说明保持供应商无关，外部协议包装由转换层补齐。

    WHAT：输入工具说明序列，输出请求 tools 字段需要的列表。
    不执行工具，也不从 ToolSpec 中推导实际调用参数.


    """
    # how1、按顺序遍历每一个spec
    result: list[dict[str, Any]] = []
    for spec in specs:
        # how2、为他建立字典，然后放入结果列表
        item: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        result.append(item)
    return result


def from_wire(data: dict) -> LLMResponse:
    """
    why:外部响应的结构和取值不能直接流进Loop

    what:
    1.入外部已经解码成python字典的响应，提取第一个候选结果。
    2.输出内部的LLMResponse对象，里面包含assistant Message和stop_reason，供Loop使用。
    3.如果外部响应中有tool_calls，转换成内部的ToolCall对象，供Loop使用。
    4.本函数不处理流式分块拼接，fixtures是完整响应流程

    """
    # how1、提取第一个候选结果
    choice = data["choices"][0]
    wire_message = choice["message"]
    if wire_message["role"] != "assistant":
        raise ValueError(f"外部响应中第一个候选结果不是assistant，而是{wire_message['role']}")

    # 提取正文，明确处理None。
    content = wire_message.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ValueError(f"当前转换层只支持字符串正文或者None，不能是{type(content)}")

    # how3、先准备外部调用调用列表，再逐个翻译成内部的ToolCall。
    # get的默认值只处理缺失键，因此None需要单独处理

    wire_calls = wire_message.get("tool_calls")
    if wire_calls is None:
        wire_calls = []
    if not isinstance(wire_calls, list):
        raise ValueError("tool_calls必须为列表或者none")

    calls: list[ToolCall] = []
    for wire_call in wire_calls:
        if wire_call.get("type") != "function":
            raise ValueError("当前转换层只支持function工具调用")

        # AI 改: 下面这一整段（到 calls.append 为止）原来比循环少 4 个空格，
        # 也就是「只在循环里做检查，搬运全在循环外」。两个后果：
        #   1) 一条响应里有 N 个工具调用时，只转换最后一个，前面全丢 —— 静默丢数据；
        #   2) wire_calls 为空时循环一次都不跑，wire_call 从未赋值，下一行直接
        #      UnboundLocalError。而"这一轮没有工具调用"恰恰是最常见的收尾。
        # 修法: 整段右移一层，和上面的 type 检查待在同一层缩进里。
        function = wire_call["function"]
        call_id = wire_call["id"]
        name = function["name"]
        raw = function["arguments"]
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("工具调用id必须为非空字符串")
        if not isinstance(name, str) or not name:
            raise ValueError("工具名称必须为非空字符串")
        if not isinstance(raw, str):
            raise ValueError("function.arguments 必须为 JSON 字符串")

        # how4、先解析json，再检查结果事实不是内部要求的字典
        # try只包住解析动作，不会吞掉整个函数的其他错误
        arguments: dict[str, Any]
        raw_arguments: str | None = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            arguments = {}
            raw_arguments = raw
        else:
            # else 只在 try 没有抛异常时执行。
            # '[]' 虽然是合法 JSON，解析结果却是列表，不是参数字典。
            if isinstance(parsed, dict):
                arguments = parsed
            else:
                arguments = {}
                raw_arguments = raw

        # HOW 5：按白名单显式组装，不直接展开外部字典。
        # 这样 type、index 等外部字段不会变成构造函数的意外参数。
        call = ToolCall(
            id=call_id,
            name=name,
            arguments=arguments,
            raw_arguments=raw_arguments,
        )
        calls.append(call)

    # HOW 6：只映射已明确支持的结束原因，其他值统一为 unknown。
    # 分支中给出明确的 Literal 值，便于类型检查器验证。
    finish_reason = choice.get("finish_reason")
    stop_reason: LLMStopReason
    if finish_reason == "stop":
        stop_reason = "end_turn"
    elif finish_reason == "tool_calls":
        stop_reason = "tool_use"
    else:
        stop_reason = "unknown"

    # HOW 8：stop_reason 和 tool_calls 来自同一个响应，对不上就是协议自相矛盾。
    # 现实里两种坏法：
    #   a) finish_reason="tool_calls" 但 tool_calls 为空 —— loop 以为还有工具要调，
    #      空转到 max_steps，error=None，一条人话都没有（M2-待办.md 第 1 条）。
    #   b) 带了 tool_calls 但 finish_reason 是 stop —— loop 当它答完了直接收工，
    #      调用被静默丢掉，模型永远等不到结果。
    # 为什么在这里抛、不往 loop 里塞 if：这两样都产自同一个 response，
    # 对不上说明是适配层或网关写错了，属于"转换层不该产出非法对象"。
    # 抛出去正好落进 loop 既有的失败路径（有 error、有 events），预算不会被烧完。
    if stop_reason == "tool_use" and not calls:
        raise ValueError(
            "finish_reason=tool_calls 但 tool_calls 为空：stop_reason 与调用列表自相矛盾"
        )
    if calls and stop_reason != "tool_use":
        raise ValueError(
            f"带了 {len(calls)} 个 tool_calls，finish_reason 却是 {finish_reason!r}："
            "这种响应会让 loop 以为已经答完，把调用静默丢掉"
        )

    # HOW 7：先用转换好的零件组装 Message，再组装 LLMResponse。
    # tuple 只改变容器；calls 中的元素已经在前面转换成 ToolCall。
    message = Message(
        role="assistant",
        content=content,
        tool_calls=tuple(calls),
    )
    return LLMResponse(message=message, stop_reason=stop_reason)
