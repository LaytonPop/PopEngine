# TODO —— 唯一清单

> **这是唯一入口。** 其他 `docs/*.md` 讲的是"为什么这么做、检查点是什么"，不是待办。
> 两边说法冲突时，以本文件为准；做完一件事就回来打勾。
> 更新时间：2026-11-10

## 进度总览

| 里程碑                            | 状态           |
| --------------------------------- | -------------- |
| M0 骨架                           | ✅             |
| M1 假数据 loop（4 场景 + 工具 + 冒烟） | ✅ 12 passed（loop 7 / tools 4 / smoke 1） |
| M2 真模型 · 转换层               | ✅ 13 passed（含双 spec 顺序断言） |
| M2 真模型 · 接线 + 真工具        | ✅ a/b 轮验收完成，回归 3/3 |
| M2 事件（events.jsonl）          | ✅ c 轮完成（2026-11-10），29 passed |
| M3 graph 引擎跑同一任务 + 对比    | ⬜ 下一站    |
| M4 README + 面试讲稿              | ⬜             |

> 测试总数：**29 = loop 11 + openai_compat 13 + tools 4 + smoke 1**。
> 沿革：18（M2 转换层）→ 23（a 轮后）→ 25（b 轮）→ **29**（c 轮补 4 条事件测试）。

---

## 现在做：M2

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

### M3：graph 引擎跑同一任务 + 对比

> **2026-11-11 定调**（依据 [`design/引擎对比.md`](design/引擎对比.md)）：
>
> - **不追求"完整实现"。** "完整"没有边界：可以是一周的黑板+波次，也可以是一个月的
>   任意 router / 任意护栏 / 任意重入。后者对目标是负收益。**按最小可用做。**
> - **graph 不是"另一个 loop"，是"另一个 `finishTurn`"。** 两者共用同一个停止契约，对比才公平。
> - **对比指标只留三个**：`model_calls` / `total_ms` / `steps`（c 轮事件系统全拿得出）。
>   `model_calls` 最重要 —— 实测模型调用 ~1049ms、工具调用 ~2ms，时间几乎全花在等模型。
> - **graph 的判据是"更可预测"，不是"更快"。**
> - **不做并行 wave**：M3 要回答的是结构差异，不是并发能力。并行会把统计口径搞脏。

- [ ] **前置：把 `max_steps` 抽成 `should_stop` 契约**（对比有效性的前提，两边共用）
- [ ] 读 c 轮的 `docs/evidence/m2-run-ok.jsonl`，确认三个指标能从事件里算出来
- [ ] graph 引擎最小实现（黑板 dict + wave 串行执行 + router 是普通函数 + `max_visits` 护栏）
- [ ] 同一任务、同一模型跑两遍，把三个数字并排放，产出 `docs/loop-vs-graph.md`

### M4：README + 面试讲稿

- [ ] README 补齐进度
- [ ] 讲稿：loop vs graph、怎么调 agent（用那份失败记录）、为什么工具参数用 JSON Schema

---

## 冻结（想动先回一页纸读"不做什么"）

多 agent · RAG / 向量库 · MCP · TUI · 会话持久化 · 重试 · 上下文压缩 · 流式 · 权限审批 · 插件系统

---

## 文件地图

> 2026-11-11 重组：原来按 M1/M2/M3 分（**时间轴**），同一个概念每个阶段都要重讲一遍，
> 所以不正交。现在按**用途**分四类：`docs/` 根目录放"现在要用的"，
> `design/` 放"还活着的为什么"，`history/` 放"完结的阶段性记录"，其余是资产目录。

### 根目录：现在就要用的（三个）

| 文件 | 角色 |
| --- | --- |
| `docs/TODO.md` | **本文件**：唯一清单。开工先看这里 |
| [协作约定.md](协作约定.md) | **怎么提交**：分支（`alma/*` / `Pop/*`）、commit 格式、红线、检查单 |
| [验收手册.md](验收手册.md) | **怎么验**：跑什么、怎么算过、怎么故意弄坏 |

### `design/`：还活着的"为什么"（四个）

| 文件 | 角色 |
| --- | --- |
| [分层与边界.md](design/分层与边界.md) | **结构**：原子 / 分子 / 五层 / 框架到底封装了什么 |
| [openai映射表.md](design/openai映射表.md) | **协议**：内部类型 ⇄ OpenAI 的逐行对照 + 真实响应实测 |
| [事件设计.md](design/事件设计.md) | **可观测**：`StepEvent` 为什么这么设计、边界划在哪 |
| [引擎对比.md](design/引擎对比.md) | **外部参照**：和 pi / Codex / Claude Code 的逐行对比（M3 依据） |

### `history/`：完结了，留作记录（不再更新）

| 文件 | 角色 |
| --- | --- |
| [M1-任务书.md](history/M1-任务书.md) | M1 的作业书，已完成 |
| [M2-任务书.md](history/M2-任务书.md) | M2 怎么切、检查点、会踩的坑 |
| [M2-真实任务.md](history/M2-真实任务.md) | M2 的靶子规格（`run_task.py` 按它写） |
| [M2-待办.md](history/M2-待办.md) | M1 review 留下的 4 条，已并入本文件 |
| `history/archive/` | 作废的计划，**不要照做** |

### 资产目录

| 目录 | 内容 |
| --- | --- |
| [diagrams/README.md](diagrams/README.md) | 三张图 + 四组真数据对照 |
| [evidence/README.md](evidence/README.md) | 两次真跑的 jsonl 留档（一次成功、一次 401） |

---

## 概念归属（一个概念只在一个文档里定义）

> 解决"同一概念被投影到多个文档"：**owner 文档负责定义，其余文档只引用、不重讲。**
> 2026-11-11 实测的重复度：`stop_reason` 出现在 11 个文档、`max_steps` 10 个、`unknown` 8 个。

| 概念 | Owner | 别处怎么处理 |
| --- | --- | --- |
| 原子 / 分子 / 五层 | `design/分层与边界.md` | 只引一句"见分层与边界" |
| `finish_reason` → `stop_reason` 的映射 | `design/openai映射表.md` §二·6 | 引用，不重列映射表 |
| `stop_reason` ⟺ `tool_calls` 等价 | `design/openai映射表.md` §七 | 引用 §七，不重讲理由 |
| `tool_call_id` 配对 | `design/openai映射表.md` §二·3 | 引用 |
| `StepEvent` 字段与边界 | `design/事件设计.md` | 引用 |
| `max_steps` → `should_stop` 插槽 | `design/引擎对比.md` §三 | 引用，不重讲 |
| `length` 截断怎么处理 | `design/引擎对比.md` §二 | 引用 |
| 提交规范 / 分支前缀 | `协作约定.md` | 引用 |
| a–e 轮怎么验 | `验收手册.md` | 引用 |

**`history/` 里的文档不受这条约束** —— 它们是冻结的时间切片，里面的说法不改。
发现 history 与 design 冲突时，**以 design 为准**（history 反映的是当时的认识）。
