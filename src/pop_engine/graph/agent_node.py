"""把 AgentLoop 包成一个 graph 节点：图包裹循环，不替代循环。

节点只做三件事：
    1. 根据 state 生成这次子 run 的 prompt（固定字符串或函数）；
    2. 调 AgentLoop.run(prompt)，拿到一个完整的 RunResult；
    3. 把结果写进 patch：output_key 放 final_text，旁边记 status/error，
       再把整个 RunResult 塞进保留键 `_subrun`，交给 GraphEngine 合并事件。

为什么不是"节点自己调 provider/工具"：
    那等于在 graph 里重写一个 loop。M3 要比较的是外层结构，
    内层必须还是同一个 AgentLoop，否则两个引擎做的不是同一件事。

为什么 `_subrun` 不直接写进黑板：
    RunResult 里有 messages 和 events，它们是子 run 的"现场"，
    不是图节点之间共享的业务数据。引擎把它摘掉，只取 events 和
    messages 拼进父 run；黑板保持干净，owners 也不会把它当成某个
    节点的私有键。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pop_engine.graph.types import SUB_RUN_KEY, NodeFn
from pop_engine.loop import AgentLoop
from pop_engine.types import Message

# prompt 可以是一段固定文本，也可以是一个读黑板的函数。
# 后者用来做重试：第二次 inspect 要把上一轮的问题和输出带进 prompt。
PromptBuilder = str | Callable[[dict[str, Any]], str]

# 这些键由引擎/运输通道占用，agent_node 不能拿它们当 output_key。
_RESERVED_OUTPUT_KEYS = {SUB_RUN_KEY, "current_node", "prompt"}


def agent_node(loop: AgentLoop, prompt: PromptBuilder, output_key: str) -> NodeFn:
    """把一次 AgentLoop.run() 包成 graph 节点。

    output_key 是这个节点在黑板上的主键，必须和别的节点不相交；
    节点还会写 output_key_status / output_key_stop_reason / output_key_error。
    同一个节点被重试时，这些键归它自己，engine 的 owners 检查允许重写。
    """
    if not output_key:
        raise ValueError("output_key 不能为空")
    if output_key in _RESERVED_OUTPUT_KEYS:
        raise ValueError(f"output_key={output_key!r} 是引擎保留键，换一个")

    def node(state: dict[str, Any]) -> dict[str, Any]:
        # 固定 prompt 直接用；函数 prompt 每次运行前读一遍黑板，
        # 这样重试时可以带上上一轮的 problems/draft。
        actual_prompt = prompt if isinstance(prompt, str) else prompt(state)
        result = loop.run([Message(role="user", content=actual_prompt)])
        return {
            output_key: result.final_text,
            f"{output_key}_status": result.status,
            f"{output_key}_stop_reason": result.stop_reason,
            f"{output_key}_error": result.error,
            # 保留键：GraphEngine 摘掉它，合并 events / messages，不写进黑板。
            SUB_RUN_KEY: result,
        }

    return node
