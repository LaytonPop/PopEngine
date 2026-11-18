"""图执行引擎：M3 的最小参考实现。

算法视角：一次 run() 就是一次**带预算的有界图遍历** —— 状态是黑板，
转移由节点和 router 完成，终止由两道预算保证。完整论证见 run() 的 docstring。

和 loop 的分工（任务书第一节的判据）：
    loop  —— 什么时候继续，由模型说了算（stop_reason）
    graph —— 什么时候继续，由**结构**说了算：节点干活，router 读 state 选下一步

这一版只做"最小可用"：黑板 dict + 串行节点 + 普通函数 router + 两道护栏。
不接模型、不并行、不重试 —— M3 要回答的是结构差异，不是并发能力。

四条不变量（任务书第四节）：
    1. state 是普通 dict，不做 State 类 —— 黑板的好处正是"能存什么"没有上限
    2. 节点返回的键不相交：同一个键不许被两个节点写过
    3. router 是普通函数，**不许在 router 里问模型** —— 一问，graph 就退化成
       "带名字的 loop"，对比也就没意义了
    4. max_visits 按节点计数，max_steps 全局计数 —— 两道护栏分开记账，
       谁先触发就在 error 里写谁，不写"超过限制"

一个任务书没写死、这里替你定下的契约：
    **router 靠 `state["current_node"]` 知道刚跑完哪个节点。**
    任务书给的签名是 RouterFn(state)，只有一个入参；而 A→B→A 这种图里，
    router 必须知道"我现在在 a 还是 b"才能决定往哪走。这个键由引擎写，
    节点不许写（写了会报错）。这是 ①"假图验签名"要暴露的问题之一。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pop_engine.types import Message, RunResult, RunStopReason

# 下面两个是**不变的契约**：节点和 router 的形状。
# M3 期间它们不动；真正随 run 变化的是 run() 里的五样局部状态（见那里）。
NodeFn = Callable[[dict[str, Any]], dict[str, Any]]
RouterFn = Callable[[dict[str, Any]], str | None]


class GraphEngine:
    """一次 graph run。

    和 AgentLoop 同一条规矩：构造器只放配置，**运行状态只活在 run() 里**。
    这样同一个 engine 可以被连续 run 很多次而不会串味。
    """

    def __init__(
        self,
        nodes: Mapping[str, NodeFn],
        router: RouterFn,
        *,
        entry: str,
        max_steps: int = 8,
        max_visits: int = 3,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps 必须至少为 1")
        if max_visits < 1:
            raise ValueError("max_visits 必须至少为 1")
        if entry not in nodes:
            raise ValueError(f"entry={entry!r} 不在 nodes 里")
        self.nodes = dict(nodes)
        self.router = router
        self.entry = entry
        self.max_steps = max_steps
        self.max_visits = max_visits

    def run(self, prompt: str) -> RunResult:
        """执行一次有界图遍历 —— M3 里 graph 引擎的算法本体。

        
        """

        def failed(error: str, stop_reason: RunStopReason = "error") -> RunResult:
            return RunResult(
                status="failed",
                stop_reason=stop_reason,
                final_text="",
                messages=(Message(role="user", content=prompt),),
                steps=0,
                error=error,
            )

       
        state: dict[str, Any] = {"prompt": prompt}
        visits: dict[str, int] = {}
        # current_node 是引擎自己的键，先占上，节点写它会撞车。
        owners: dict[str, str] = {"current_node": "<engine>"}

        current = self.entry  # 初始状态：状态机停在入口节点
        step = 0  # 已执行的轮数；终止性只依赖它

        while step < self.max_steps:
            
            step += 1
            state["current_node"] = current
            visits[current] = visits.get(current, 0) + 1

            
            if visits[current] > self.max_visits:
                
                return failed(
                    f"节点 {current!r} 被访问 {visits[current]} 次，上限 {self.max_visits}"
                )

            
            patch = self.nodes[current](state)

           
            stolen = [key for key in patch if key in owners and owners[key] != current]
            if stolen:
                key = sorted(stolen)[0]
                raise ValueError(
                    f"键 {key!r} 已经被节点 {owners[key]!r} 写过，"
                    f"节点 {current!r} 不能再写 —— 节点返回的键必须不相交"
                )
            owners.update({key: current for key in patch})
            state.update(patch)

            
            nxt = self.router(state)
            if nxt is None:
                # 接受条件：结构说收工。对应 loop 的 end_turn，
                # 但决定权在 router（代码）手里，不在模型手里。
                return RunResult(
                    status="ok",
                    stop_reason="end_turn",
                    # 图的最终回答约定写在 state["final_text"]；没这个键就是空串。
                    final_text=str(state.get("final_text", "")),
                    messages=(Message(role="user", content=prompt),),
                    steps=0,
                )
            if nxt not in self.nodes:
                # 拒绝条件：router 指到未知节点。当场失败 ——
                # 否则它会悄悄绕到 max_steps 才暴露，报错指向错误的护栏。
                return failed(f"router 返回了未知节点 {nxt!r}，已有节点：{sorted(self.nodes)}")
            current = nxt  # 下一圈从它开始

        # 掉出 while：终止性保证的唯一出口（step 溢出）。
        # 报错指向 max_steps（而不是 max_visits），并带上最后停在哪个节点 ——
        # 两个护栏谁触发的，一眼能分清。
        return failed(
            f"超过 max_steps={self.max_steps} 步仍未结束（最后停在节点 {current!r}）",
            stop_reason="max_steps",
        )
