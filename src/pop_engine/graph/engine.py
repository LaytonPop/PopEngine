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

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Literal

from pop_engine.graph.types import SUB_RUN_KEY, NodeFn, RouterFn
from pop_engine.types import Message, RunResult, RunStopReason, StepEvent

# NodeFn / RouterFn 只有一份定义，在 graph/types.py —— 这里不再重定义一份。
# 同一个契约写两遍就是两个来源，迟早分叉（见 design/引擎对比.md §七补记）。


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

        ── 子 run 的事件怎么并进来（任务书 §5 坑 1 的答案）──

        节点可以整轮跑一个 AgentLoop（见 agent_node.py）。子 run 的 `step`
        从 1 开始，父 run 也有自己的轮次 —— 但这里**不给子事件加编号偏移**，
        而是用 `StepEvent.node` 标出它属于哪个节点，然后按发生顺序拼进 `events`。

        理由：加偏移会让 `step` 同时表示两种东西（节点内第几次 / 全局第几次），
        以后任何人读这个字段都要先问"这是哪种编号"；加字段则 `step` 在两边
        都还是一个意思，而"哪个节点产生的"是另一个问题，用另一个字段回答。

        ── 三个数字从哪来 ──

        `events` 是唯一的账本，`model_calls` / `total_ms` 全部由它算。
        `steps` 沿用 `RunResult.steps` 原本的含义（调用了几次模型），
        所以 graph 这边它也等于 `model_calls` —— 与 `metrics.summarize` 一致。
        **"结构做了几次决策"是另一个量，要报就得另起名字（node_visits），
        不要借用 steps。**（任务书 §7 那三个选项，这里选的是"保持一个含义"。）
        """

        # 子 run 的现场：事件按发生顺序攒；messages 只留最后一次。
        # 为什么 messages 只留最后一次：出问题时人先看最后一步，中途那些
        # 留给 events（任务书 §5 坑 3）。没有 agent_node 的图里它保持初始值。
        events: list[StepEvent] = []
        last_messages: tuple[Message, ...] = (Message(role="user", content=prompt),)

        def finish(
            *,
            status: Literal["ok", "failed"],
            stop_reason: RunStopReason,
            final_text: str = "",
            error: str | None = None,
        ) -> RunResult:
            """所有出口共用：保证 events / messages / steps 三样一定被填上。

            以前五个出口各自 new 一个 RunResult，谁漏了 events 都看不出来 ——
            实测过一次：graph 跑完 `events=0`，三个指标全零，而 49 个测试全绿。
            收成一个出口工厂，就不会再有"某一个分支忘了填"。
            """
            return RunResult(
                status=status,
                stop_reason=stop_reason,
                final_text=final_text,
                messages=last_messages,
                steps=sum(1 for event in events if event.kind == "model"),
                error=error,
                events=tuple(events),
            )

        def failed(error: str, stop_reason: RunStopReason = "error") -> RunResult:
            return finish(status="failed", stop_reason=stop_reason, error=error)

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

            # 运输通道不是业务数据：先摘出来，不写进白板、也不登记 owners。
            # 否则每个 agent_node 都会写同一个 `_subrun`，第二个就被判成撞键 ——
            # 跟当初每个节点都写 `last_status` 是同一个病。
            subrun = patch.pop(SUB_RUN_KEY, None)
            if subrun is not None:
                if not isinstance(subrun, RunResult):
                    raise TypeError(
                        f"节点 {current!r} 在 {SUB_RUN_KEY!r} 里放的不是 RunResult，"
                        f"而是 {type(subrun).__name__} —— 这个键只给 agent_node 用"
                    )
                # 子事件带上节点名再拼进父 run：step 保持子 run 的原编号，
                # 归属靠 node 字段，两套编号不用合并。
                events.extend(replace(event, node=current) for event in subrun.events)
                if subrun.messages:
                    last_messages = subrun.messages

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
                return finish(
                    status="ok",
                    stop_reason="end_turn",
                    # 图的最终回答约定写在 state["final_text"]；没这个键就是空串。
                    final_text=str(state.get("final_text", "")),
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
