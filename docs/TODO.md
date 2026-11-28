# TODO —— 唯一清单

> **这是唯一入口。** 其他 `docs/*.md` 讲的是"为什么这么做、检查点是什么"，不是待办。
> 两边说法冲突时，以本文件为准；做完一件事就回来打勾。
> 更新时间：2026-11-26

## 进度总览

| 里程碑                            | 状态           |
| --------------------------------- | -------------- |
| M0 骨架                           | ✅             |
| M1 假数据 loop（4 场景 + 工具 + 冒烟） | ✅ 12 passed（loop 7 / tools 4 / smoke 1） |
| M2 真模型 · 转换层               | ✅ 13 passed（含双 spec 顺序断言） |
| M2 真模型 · 接线 + 真工具        | ✅ a/b 轮验收完成，回归 3/3 |
| M2 事件（events.jsonl）          | ✅ c 轮完成（2026-11-10），29 passed |
| M3 graph 引擎跑同一任务 + 对比    | ✅ 2026-11-26（67 passed） |
| M4 L2 机制层四关（完整化）        | 🚧 下一站（插槽 → 写工具+权限 → 会话恢复 → 压缩） |
| M5 README + 面试讲稿              | ⬜ 四关过完后收口 |

> 测试总数：**67 = loop 14 + openai_compat 13 + tools 4 + smoke 1 + metrics 6
> + graph 11 + agent_node 18**。
> 沿革：18（M2 转换层）→ 23（a 轮后）→ 25（b 轮）→ 29（c 轮补 4 条事件测试）
> → 32（M3 前置补 3 条预算边界测试）→ 38（`metrics`）→ 49（`graph`）
> → 65（`agent_node` + 事件通道）→ **67**（补「子 run 失败不许报 ok」+ 对照组）。
>
> M4 目标 **~104**，但注意：**第 1 关会作废 2 条旧测试的语义**（`test_c` / `test_l`），
> 这不是「顺手改断言」，是改契约 —— 单独一次提交，正文写清为什么旧的孤儿副作用是 bug。

---

## 已完结：M2

> M1–M3 全部完成。**下一步是 M4（README + 面试讲稿）**，见下面「之后」一节。
> 保留这一段是为了留痕：每一条都带日期和当时的实测数字。

### 0. 开工前置（5 分钟，必须先做）

- [X] **定真实任务** → 已落盘 `docs/M2-真实任务.md`（2026-11-04，三条验收断言可进测试）

### 1. 接线 + 场景 A 真跑（约 60 分钟）

- [X] `src/pop_engine/llm/provider.py`：实现 `OpenAIProvider.chat()`（urllib，不引 SDK）
- [X] `scripts/hello_llm.py`：一句输入 → 打印 `RunResult`
- [X] 真跑一次 → 验收：真实输出 + `stop_reason: end_turn`（2026-11-04 通过：`steps=1`，真模型输出；负向也验了：错 key → 401 带完整响应体）
- [X] `to_wire_tools` 的测试（2026-11-04 三条；2026-11-07 补第四条**双 spec 顺序断言** —— 前三条都只传一个 spec，对"顺序错乱/只取一个"永远是绿的）

### 2. 真工具 + 场景 B 真跑（约 60 分钟）

- [X] `src/pop_engine/tools/builtin.py`：`list_dir` / `read_file`
  （锁根目录、越界返 `is_error=True`、截断要明说、参数写 JSON Schema）
- [X] `scripts/run_task.py`：第 2 段验收脚本（抠 JSON → 验五键类型 → 验 trace 有 `list_dir` → 顺手做 `tool_call_id` 配对自检）
- [X] 真跑 → 验收：trace 里两轮工具往返，且是模型自己决定先 list 再 read（2026-11-04：本项目 6 步 11 次调用、ChatBox 7 步 17 次调用，两个仓库都 PASS）
- [X] `stop_reason` 与 `tool_calls` 一致性校验（2026-11-04：`from_wire` 末尾两条 `raise`，规则 = 二者必须等价；映射表 §七 记了为什么放转换层不放 loop）
- [X] `unknown` 的显式兜底（2026-11-07：`loop.py` 加 `if stop_reason == "unknown"` 当场收场 + 场景 F 测试 `test_f_unknown_stop_reason_stops_immediately`。
      修前：`steps=8 error=None 模型调用=8` 空转烧钱；修后：`steps=1 stop=error 模型调用=1`。
      **破了一次「不许改 loop.py」的铁律**，理由：映射表 §二·6 自己把责任派给 loop，边界划漏了一格）

### 3. 事件 + 失败记录（约 45 分钟）✅ 2026-11-10 完成

- [X] 每步发事件 → 落 `events.jsonl`（step / stop_reason / 工具名 / 耗时 / is_error）
      设计见 `docs/M2-事件设计.md`；`StepEvent` 在 `types.py`，埋点全在 `loop.py`，
      序列化只在 `run_task.py`（loop 不认识 JSON、不认识文件）
- [X] `max_steps` 终局事件写人话（`error=f"超过 max_steps={n} 步仍未结束"`）
- [X] 故意失败一次（错 key → 401），events 留档 `docs/evidence/m2-provider-401.jsonl`
- 实测：`model 6 行 == steps 6`、`10 对 start/end` 且 `tool_call_id` 全配上、总行数 `26 == 6+2×10`
- 红线已验证：`sk-` / `Authorization` / `Bearer` 零命中；字段白名单 8 个键

### 4. M2 收尾

- [X] `self.tools.specs()` 提到循环外（2026-11-07：实测 3 步的 run 里 `specs()` 从 3 次降到 **1 次**，三轮拿到的是同一个 `ToolSpec` 元素。
      附带钉死一件事：同一轮 run 里模型每次看到的说明书必须一模一样）
- [X] 三件套（2026-11-07：`25 passed` / `All checks passed!` / `15 files already formatted` / `Success: no issues found in 15 source files`）
- [X] 提交 + 回来更新本文件的勾（2026-11-07：`a8576d0` b 轮补四个洞 + specs()；`9884a40` 03-run-lifecycle 状态机图）

> **a / b 轮验收（2026-11-07 完成）**：真跑回归 3/3 全过 ——
> `hello_llm`（`steps=1`）、`run_task` 本项目（`steps=5`，PASS）、`run_task` ChatBox（`steps=7`，PASS）。
> 详细验收步骤见 `docs/验收手册.md` 的 a、b 两轮。

---

## 之后

### M3：graph 引擎跑同一任务 + 对比 ✅

> **2026-11-11 定调**（依据 [`design/引擎对比.md`](design/引擎对比.md)）：
>
> - **不追求"完整实现"。** "完整"没有边界：可以是一周的黑板+波次，也可以是一个月的
>   任意 router / 任意护栏 / 任意重入。后者对目标是负收益。**按最小可用做。**
> - **graph 不是"另一个 loop"，是"另一个 `finishTurn`"。** 两者共用同一个停止契约，对比才公平。
> - **对比指标只留三个**：`model_calls` / `total_ms` / `steps`（c 轮事件系统全拿得出）。
>   `model_calls` 最重要 —— 实测模型调用 ~1049ms、工具调用 ~2ms，时间几乎全花在等模型。
> - **graph 的判据是"更可预测"，不是"更快"。**
> - **不做并行 wave**：M3 要回答的是结构差异，不是并发能力。并行会把统计口径搞脏。

**前置：预算语义钉死（2026-11-12 ✅，`alma/M3` 第一个提交）**

> 原计划写的是"把 `max_steps` 抽成 `should_stop` 契约"。**这个说法被降级了。**
> 理由见 [`design/引擎对比.md`](design/引擎对比.md) §三补记：共用契约首先是**两边遵守
> 同一套明确、可测试的行为**，不一定要先共用一个函数。先写行为，跑起来再决定要不要抽。

- [X] **写明预算语义**（`loop.py` 的 `run()` docstring）：`max_steps` 限的是模型调用次数，
      第 N 次是合法调用 —— 第 N 次 `end_turn` 算成功；第 N 次要工具算失败；
      第 N 次异常/`unknown` 算 error。**不泛化**成"正常结束优先于所有护栏"。
- [X] 边界测试 `test_k`：恰好用满预算那步 `end_turn` → `ok`。**特征测试**，锁住现有行为。
- [X] 孤儿副作用 `test_l` + 对照 `test_l2`：用满预算那步 `tool_use` → 工具**已执行**、
      结果留在 messages 里、但模型永远读不到。**留着不修**（修要动控制流，M3 要保持形状一致），
      但"知道"和"不知道"是两件事。
- [X] 读 c 轮的 `docs/evidence/m2-run-ok.jsonl`，确认三个指标能从事件里算出来
      （2026-11-13：`model_calls 6` / `total_ms 25644.21`，只碰 `events`，不碰 `messages`。
      顺手量出一个之前没人拆过的数：6 次调用里第 4 次独占 19218ms（75%），
      「模型调用慢」其实是一次长尾，不是均匀的慢。教训：**平均耗时这个口径会藏住长尾。**）
      → `metrics.py`，纯函数，配一整份离线测试
- [X] graph 引擎最小实现（黑板 dict + 串行执行 + router 是普通函数 + `max_visits` 护栏）
      （2026-11-13~14：`graph/engine.py`。**没有 wave/并行** —— M3 要的是结构差异，
      并行会把耗时口径搞脏。waku 那套 wave 是它的需求逼出来的，不是普适正确。）
- [X] `agent_node`：一次 `AgentLoop.run()` 包成节点，子 run 的 `StepEvent` 带 `node`
      标签并进父 run（2026-11-26）。**编号不加偏移**，靠字段归属——
      加偏移会让 `step` 同时表示"节点内第几次"和"全局第几次"
- [X] 同一任务、同一模型各跑 3 次，三个数字并排放 → `docs/loop-vs-graph.md`（2026-11-26）
- [X] 反向验收：`max_visits=1` + A→B→A 必回退 → 报错说清"节点 'a' 被访问 2 次，上限 1"

> **M3 的两条结论（比数字重要）**
>
> ① **我预判错了，被数据推翻。** 原以为「router 固定 ⇒ `model_calls` 恒定」，
> 实测 graph 是 7/5/6（极差 2），比 loop 的 5/6/5 还大。因为 router 固定的是
> **走哪几个节点**，管不到**每个节点里模型问几次**。
> ⇒ **固定结构夹住的是区间，不是数字；区间宽度是你配的预算，不是测出来的。**
> 所以「graph 更可预测」这句话在本轮**不成立**，报告里写的是"不能说"。
>
> ② **graph 用 1/3 的工具调用拿到同样结果**（3.3 次 vs 9 次，五键都 3/3）。
> loop 的模型自己觉得"看够了"，但之前反复扫了一堆用不上的目录；
> graph 里 `scan` 的职责只有"列一次根目录"，它没有动机扫第二遍。
> ⇒ **约束省下的不是时间，是动作。**
>
> **还没测到的**（M3 之后想做，不阻塞 M4）：让 router 依赖模型写的 key
> （`scan` 写 `enough: yes/no`）—— 那才是"代码规则 vs 模型自觉"在同一个位置上竞争。

### M4：L2 机制层四关（让引擎长出防线） 🚧

> **2026-11-27 定版（照真源码，不再是推断）**：M1–M3 已把 L0（原子）、L1（loop）、L3（graph）焊牢（`67 passed`）。
> 四关的具体切口全部改成对 `KamaClaude@stage/s6`、`waku-agent`、`opencode` 源码的对照 ——
> 11-26 那版的 `hooks.py` 中间件框架、滑动窗口折叠、丢弃孤儿调用三处**已被真源码推翻**，理由见任务书 §〇。
>
> 详细切口、签名、不变量与反向验收见 **[`M4-任务书.md`](M4/M4-任务书.md)**。
> 总判据：**不按「有这个功能」验收，按「故意制造故障时它怎么拦」验收。**

#### 第 1 关：循环三个薄参数 + 预算收尾
- [ ] `loop.py` 加 `before_model: (step, messages) -> Sequence[Message]`（照 `waku/loop/agent.py:125`，只在 `step > 1` 调；**返回新序列不改入参**，原文留在 `RunResult`）
- [ ] `loop.py` 加 `should_stop: (step, response, messages) -> str | None`，调用点在「append assistant 之后、执行工具之前」（护栏必须在动作之前）；`RunStopReason` 新增 `"stopped"`
- [ ] **撞线禁工具**（改编 `waku/loop/agent.py:165-209`）：`step == max_steps` 时 `tool_specs` 传空，模型就地作答 → 不变量 `steps ≤ max_steps` 不动，孤儿副作用从成因上消失
- [ ] 改 `loop.py` docstring 的「预算语义」一段 + 改写 `test_c` / `test_l`（旧语义作废，单独一次提交并说明理由）
- [ ] `tests/test_hooks_loop.py`：零侵入回归、`before_model` 调用次数与「p 收到短的 / result 留长的」、`should_stop` 中止且工具零执行、撞线那次 `tool_specs == []`、不听话的模型硬要工具不执行

#### 第 2 关：权限网关 + 两个真能干活的工具
- [ ] `tools/__init__.py`：`Tool` 协议加 `capability: "readonly" | "write" | "exec"` —— **不加进 `ToolSpec`**（说明书是给模型看的，能力是我们自己的元数据）
- [ ] `src/pop_engine/permission.py`（新，单文件）：照 `KamaClaude/core/permissions/policy.py` 的 4 层 `evaluate`（deny_patterns → `OUTSIDE_CWD_HEURISTICS` 强制 ASK → allow_patterns → 工具默认），顺序不许调
- [ ] `PermissionGate`（与 `ToolRegistry` 同形状的装饰器：`specs()` / `invoke()`）+ `approver` 回调处理 ASK；**不抄** `storage.py` 持久化与 pending-Future 双进程审批
- [ ] `tools/builtin.py`：`write_file`（1MB 上限、`..` 拒绝、自动建父目录）+ `run_command`（64KB 截断、timeout 1–120 默认 60、stderr 合并、非零退出带 `[exit N]`）；**改同步 `subprocess.run`**，别抄 KamaClaude 的 asyncio
- [ ] `tests/test_permission.py`：表驱动 4 层、拒绝时被拒工具执行次数 **0**、拒绝后模型自愈收尾、`..` 越界零字节、超时不挂死、`approver` 放行/拒绝

#### 第 3 关：会话持久化 + 真·断点恢复
- [ ] `session.py`（新，单文件）：`message_to_dict` / `message_from_dict` **无损往返**（保住 `tool_calls` 元组与 `raw_arguments`）+ `SessionStore` 追加写 `thread.jsonl` / `meta.json`
- [ ] `loop.py` 加两个入口：构造器 `on_message: Callable[[Message], None]`（在 user/assistant/tool **三处 append 之后立刻调**）+ `run(prompt, *, history=...)`
- [ ] **孤儿补齐优先、回滚兜底**（与 `KamaClaude` 的 `_trim_orphan_tool_use` 刻意不同）：给「有请求无回执」的 `tool_call_id` 补合成回执（「结果未知，副作用可能已发生」），只在末尾 assistant 本身残缺时才回滚
- [ ] `tests/test_session.py`：无损往返、`on_message` 次数/顺序、跨实例续聊、残缺历史愈合后 `to_wire` 合法、**第 3 步被 kill 续跑后前 3 步工具不重跑**、坏行跳过不崩

#### 第 4 关：两级压缩（一级免费，二级才花钱）
- [ ] `compact.py`（新，单文件）一级 `shrink_seen`：照 `waku/loop/trim.py`（`LONG_CHARS=4000` / `KEEP_CHARS=2000`），只截 `messages[:-1]`、**只截一次**、不动条数与配对
- [ ] 二级六段摘要：照 `KamaClaude/core/compact/compactor.py` 的 `_COMPACT_PROMPT`（Original Goal / Completed Steps / Key Constraints / File State / TODOs / Critical Data），压成 `[user(summary), assistant(ack)]` 一对；`Completed Steps` 必须写明已执行的副作用
- [ ] 挂进 `before_model`；**最后一步（禁工具步）不压**；`EventKind` 新增 `"compact"`；摘要失败不中断任务
- [ ] `tests/test_compact.py`：未超线返回同一对象、一级保配对且幂等、二级触发、切在「1 拖 3 工具调用」中间也不破配对、摘要抛异常仍继续、端到端 6 个大文件跑通

---

### M5：README + 面试讲稿（四关收口后）

> **口径（2026-11-26 更新）**：四关过完后，测试预计从 `67` 涨到 `100+ passed`，
> L0–L3 全齐。讲稿里的每一句「生产级防线」，都能直接指到 M1–M4 的代码与故障测试。

- [ ] README 补齐完整架构图、五层对照表与 100+ 测试验证命令
- [ ] 讲稿：loop vs graph 实测反直觉结论、四道故障防线（权限拦截 / 孤儿恢复 / 保配对压缩 / 死循环熔断）、为什么工具参数用 JSON Schema

---

## 已解冻 vs 仍冻结

- **M4 已解冻**：循环插槽（`should_stop`）· 写工具与权限审批 · 会话持久化与断点恢复 · 上下文压缩
- **仍冻结（不进 M4）**：多 agent · RAG / 向量库（归毕设） · MCP · TUI · 流式 · 插件系统

---

## 文件地图

> 2026-11-11 重组：原来按 M1/M2/M3 分（**时间轴**），同一个概念每个阶段都要重讲一遍，
> 所以不正交。现在按**用途**分四类：`docs/` 根目录放"现在要用的"，
> `design/` 放"还活着的为什么"，`history/` 放"完结的阶段性记录"，其余是资产目录。

### 根目录：现在就要用的（五个）

| 文件 | 角色 |
| --- | --- |
| `docs/TODO.md` | **本文件**：唯一清单。开工先看这里 |
| [M4-任务书.md](M4/M4-任务书.md) | **M4 怎么做**：L2 四关（插槽 / 写工具+权限 / 会话恢复 / 压缩）的切口、真源码行号与反向验收 |
| [loop-vs-graph.md](design/loop-vs-graph.md) | **M3 的产出**：loop / graph 同任务对比 + 为什么不能说"更可预测" |
| [协作约定.md](协作约定.md) | **怎么提交**：分支（`alma/M<n>` / `Pop/M<n>`）、commit 格式、红线、检查单 |
| [验收手册.md](验收手册.md) | **怎么验**：跑什么、怎么算过、怎么故意弄坏 |

### `design/`：还活着的"为什么"（六个）

| 文件 | 角色 |
| --- | --- |
| [分层与边界.md](design/分层与边界.md) | **结构**：原子 / 分子 / 五层 / 框架到底封装了什么 |
| [三层与两轴.md](design/三层与两轴.md) | **坐标**：`agent = loop + graph + harness` 怎么讲才站得住（嵌套 + 横切面，不是并列加法） |
| [openai映射表.md](design/openai映射表.md) | **协议**：内部类型 ⇄ OpenAI 的逐行对照 + 真实响应实测 |
| [事件设计.md](design/事件设计.md) | **可观测**：`StepEvent` 为什么这么设计、边界划在哪 |
| [引擎对比.md](design/引擎对比.md) | **外部参照**：和 pi / Codex / Claude Code 的逐行对比（M3 依据） |
| [s3对照-task与trace.md](design/s3对照-task与trace.md) | **可观测的另一种做法**：trace 当仪表 vs 当数据；task 是软约束不是调度器 |

### `history/`：完结了，留作记录（不再更新）

| 文件 | 角色 |
| --- | --- |
| [M1-任务书.md](history/M1/M1-任务书.md) | M1 的作业书，已完成 |
| [M2-任务书.md](history/M2/M2-任务书.md) | M2 怎么切、检查点、会踩的坑 |
| [M2-真实任务.md](history/M2/M2-真实任务.md) | M2 的靶子规格（`run_task.py` 按它写） |
| [M2-待办.md](history/M2/M2-待办.md) | M1 review 留下的 4 条，已并入本文件 |
| [M3-任务书.md](history/M3/M3-任务书.md) | M3 怎么做的（已完结）：怎么切、检查点、会踩的坑。留作 M5 讲稿素材 |
| `history/archive/` | 作废的计划，**不要照做** |

### 资产目录

| 目录 | 内容 |
| --- | --- |
| [diagrams/README.md](diagrams/README.md) | 六张图（01–03 协议与状态机 / 04–06 graph 与三层结构）+ 四组真数据对照 |
| [evidence/README.md](evidence/README.md) | 两次真跑的 jsonl 留档（一次成功、一次 401） |

---

## 概念归属（一个概念只在一个文档里定义）

> 解决"同一概念被投影到多个文档"：**owner 文档负责定义，其余文档只引用、不重讲。**
> 2026-11-11 实测的重复度：`stop_reason` 出现在 11 个文档、`max_steps` 10 个、`unknown` 8 个。

| 概念 | Owner | 别处怎么处理 |
| --- | --- | --- |
| 原子 / 分子 / 五层 | `design/分层与边界.md` | 只引一句"见分层与边界" |
| `agent = loop + graph + harness` 两轴关系 | `design/三层与两轴.md` | 引用，不重讲为什么不是加法 |
| `finish_reason` → `stop_reason` 的映射 | `design/openai映射表.md` §二·6 | 引用，不重列映射表 |
| `stop_reason` ⟺ `tool_calls` 等价 | `design/openai映射表.md` §七 | 引用 §七，不重讲理由 |
| `tool_call_id` 配对 | `design/openai映射表.md` §二·3 | 引用 |
| `StepEvent` 字段与边界 | `design/事件设计.md` | 引用 |
| `max_steps` → `should_stop` 插槽 | `design/引擎对比.md` §三 | 引用，不重讲 |
| `length` 截断怎么处理 | `design/引擎对比.md` §二 | 引用 |
| 提交规范 / 分支前缀 / scope 白名单 | `协作约定.md` | 引用 |
| 三个对比指标的定义 | `design/引擎对比.md` §七 | ⚠️ 曾有第三处写错（验收手册 d 轮写过"token 数"），2026-11-12 已改成引用 |
| M3 怎么切 / 检查点 / 坑 | `history/M3-任务书.md` | 已归档，只引用 |
| M4 四关切口 / 真源码行号 / 反向验收 | `M4-任务书.md` | 引用，不重讲为什么 |

**`history/` 里的文档不受这条约束** —— 它们是冻结的时间切片，里面的说法不改。
发现 history 与 design 冲突时，**以 design 为准**（history 反映的是当时的认识）。
