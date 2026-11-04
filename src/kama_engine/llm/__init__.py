"""模型层的边界：引擎只依赖这一个函数签名。

为什么是 Protocol 而不是基类：
    Provider 不需要继承我，只要能"看起来像"就够 —— 官方 SDK、本地模型、
    测试里的假 provider，三者零成本互换。这就是 M1 不用 API key 的原因。

这一层**不负责**的事（都留给 loop，别塞进来）：
    - 不重试、不降级、不做超时策略（M1 明确不做）
    - 不拼 prompt、不管上下文裁剪
    - 不管"该不该停下" —— 停不停是 loop 的判断

AI 改（2026-11-04）：本文件从 llm.py 挪成了包 `llm/` 的 __init__.py。
    原因：同一目录下 `llm.py` 和 `llm/` 不能共存（实测 package 赢，llm.py 被静默忽略）。
    规则一句话：**一个模块一旦要有实现文件住在它下面，它就变成包**。
    好处：`from kama_engine.llm import LLMProvider` 这条老路径一个字都没变，
    而实现（HTTP 那层）住在同目录的 provider.py —— 边界和实现终于在同一层。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from kama_engine.types import LLMResponse, Message, ToolSpec


class LLMProvider(Protocol):
    """一次同步的模型调用。

    同步而非 async 的理由：v1 工具是串行执行的，一次 run 里没有任何并发收益，
    async 只会把 4 个测试全部套进 event loop。等真需要并行工具时再谈。
    """

    def chat(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> LLMResponse:
        """把完整消息列表 + 工具清单交给模型，拿回它的下一步。

        失败就直接抛异常，不要自己吞 —— 异常处理是 loop 的职责。
        """
        ...
