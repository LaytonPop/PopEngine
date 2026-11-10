"""工具的边界：注册表 + **永不抛异常**的调用。

为什么值得单独一层（这是本仓库少数"看起来多余"的设计，面试会被问）：
    工具是我们自己写的代码，一定会炸。如果 loop 里到处 try/except，
    循环的正确性就被工具的健壮性绑架了。
    把"炸掉"统一翻译成 ToolResult(is_error=True) 之后：
      - loop 里没有任何 try/except 处理工具
      - 模型能看见失败原因（"未知工具：foo" / "TimeoutError: ..."）
    这是"边界处收口"的典型用法。

AI 改（2026-11-04）：本文件从 tools.py 挪成了包 `tools/` 的 __init__.py。
    和 llm 同一个原因：`tools.py` 与 `tools/` 不能共存，而真工具要住在
    tools/builtin.py。`from pop_engine.tools import ToolRegistry` 的路径没变。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from pop_engine.types import ToolCall, ToolResult, ToolSpec


class Tool(Protocol):
    """一个工具：一张说明书 + 一个能跑的纯函数。

    run 只负责"把参数变成一段文本"：
      - 成功了就返回文本（错也是结果的一部分，由 registry 兜）
      - 失败了就抛异常 —— 抛不抛不归你管，边界层会接住
    """

    spec: ToolSpec

    def run(self, **kwargs: Any) -> str: ...


class ToolRegistry:
    """名字 → 工具 的查表；同时是"调用"这件事的唯一入口。"""

    def __init__(self, tools: Iterable[Tool]) -> None:
        self._by_name: dict[str, Tool] = {tool.spec.name: tool for tool in tools}

    def specs(self) -> Sequence[ToolSpec]:
        """交给模型看的说明书清单（每次 chat 都要带上）。"""
        return [tool.spec for tool in self._by_name.values()]

    def invoke(self, call: ToolCall) -> ToolResult:
        """执行一次工具调用。**任何情况下都返回 ToolResult，不抛异常。**"""
        # 参数解析失败时 from_wire 会把原文留在 raw_arguments 里。
        # 这时 arguments 是 {}，直接执行只会得到"缺少参数"这种误导性错误；
        # 把原文还给模型，它才知道要修的是 JSON 本身。
        if call.raw_arguments is not None:
            return ToolResult(
                tool_call_id=call.id,
                content=f"参数不是合法的 JSON 对象：{call.raw_arguments}",
                is_error=True,
            )
        tool = self._by_name.get(call.name)
        if tool is None:
            return ToolResult(
                tool_call_id=call.id,
                content=f"未知工具：{call.name}",
                is_error=True,
            )
        try:
            return ToolResult(tool_call_id=call.id, content=tool.run(**call.arguments))
        except Exception as exc:  # 故意接住一切：这就是本层存在的理由
            return ToolResult(
                tool_call_id=call.id,
                content=f"{type(exc).__name__}: {exc}",
                is_error=True,
            )
