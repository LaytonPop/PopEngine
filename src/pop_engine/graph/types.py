"""graph 的契约类型：节点和 router 的形状。"""

from collections.abc import Callable
from typing import Any

NodeFn = Callable[[dict[str, Any]], dict[str, Any]]
RouterFn = Callable[[dict[str, Any]], str | None]

# 节点 → 引擎的运输通道：节点把子 run 的 RunResult 放在 patch 的这个键下，
# 引擎摘走它（取 events / messages），**不写进白板、不登记 owners**。
# 定义在这里而不是两处各写一遍，是为了让 agent_node 和 engine 用同一个常量 ——
# 字符串常量分头硬编码，改一处忘一处就会静默失效。
SUB_RUN_KEY = "_subrun"
