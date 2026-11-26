# s3 对照：任务系统与可观测层

> 写于 2026-11-15。读的是 `KamaClaude-repo` 的 `stage/s3` @ `16bbe3b`（2026-05-19）。
> 来源标注沿用 `引擎对比.md` 的规矩：`【读源码】` 是真读到的，`【推断】` 是没读到、按形状猜的。
> **不标来源的结论不许写进这份文件。**

---

## 〇、先摆读了什么

```limits
s3 相对 s2 的改动 | 42 文件 +2194/−143 | 2 个提交
其中 core/task/   | 173 行 | model 42 + manager 127
其中 core/trace/  | 166 行 | record 22 + writer 44 + provider 95
其中 TUI app.py   | +279 | 主要受益方
loop.py 的变化    | +2 行 | 见第四节，不是任务相关
```

`core/task/` 和 `core/trace/` 全部读完了（339 行）。`tui/app.py` **没读**，
所以下面只要涉及 TUI 怎么显示，都是没说的部分。

---

## 一、trace：两套可观测，两种哲学

这是 s3 里最值得对照的一块。同一个目的（把一次 run 发生过什么留下来），
两边走了完全相反的路。

### 1.1 形状

| | PopEngine | KamaClaude s3 |
| --- | --- | --- |
| 事件从哪来 | **loop 里手写 `events.append`**（4 处）【读源码】 | **provider 被装饰**，`TracingProvider.chat()` 前后 emit【读源码】 |
| 事件存哪 | `RunResult.events` 这个**返回值**里【读源码】 | `TraceWriter` 的 asyncio 队列，直接落 jsonl【读源码】 |
| 谁负责落盘 | **消费者**：`scripts/run_task.py:198 write_events()`【读源码】 | **写入方自己**：writer 的 `_drain()` 后台 task【读源码】 |
| loop 认识文件/JSON 吗 | 不认识（这条是刻意的）【读源码】 | 也不认识（因为它根本不知道 trace 存在）【读源码】 |
| 能纯函数测吗 | **能**。`metrics.summarize(Sequence[StepEvent])` 不碰 IO【读源码】 | 不能。得跑 asyncio + 真文件【读源码】 |

**一句话**：PopEngine 把 trace 当**数据**，KamaClaude 把 trace 当**仪表**。

数据的好处是可测 —— 这是 `metrics.py` 能配上一整份纯函数测试的前提，
也是 M3 那三个对比指标（model_calls / total_ms / steps）能直接吃事件的原因。
仪表的唯一好处是**可以在不改引擎的前提下加**：loop 一行不动，换一个 provider 就够了。

### 1.2 抄什么

**（a）`include_llm_payload` 这个开关，值得抄。**

`s3 core/config.py:44` 是 `include_llm_payload: bool = True`，`False` 时
`TracingProvider` 只记 `message_count` / `tool_count`，不记全量 messages 和 tool_calls。【读源码】

两个理由都对 PopEngine 成立：

1. **体积**。每次调用都带全部历史消息，一条 run 的 trace 是 O(n²) 增长。
   PopEngine 现在 `--trace` 落盘的 jsonl 是全量 `events`，但事件里本来就没有 messages，
   所以这条暂时不痛 —— **一旦 M5 加上上下文压缩、要对比"压掉了多少"，
   就必须记 messages 长度，那时这条会立刻变痛**。
2. **红线**。11-13 那次检查是 `grep -E "sk-|Authorization" traces/*.jsonl` 干净。
   现在是干净的，因为事件里压根没放请求体。哪天为了调试把 messages 记进去，
   这个 grep 就会红。**开关应该是默认关，调试时手动开** —— s3 的默认是 `True`，
   这一点我认为它选错了。【推断】它这么做可能是因为 trace 本来就是调试工具。

**（b）把 latency 记在"层"上，而不是只记在 loop 里。**

s3 的 `api_response` 记录里带 `latency_ms = int((monotonic - t0) * 1000)`，量的是
**provider.chat 这一层**。【读源码】
PopEngine 的 `StepEvent.duration_ms` 是 loop 在外面裹的 `perf_counter`，
量的是**"从 loop 决定调用到 provider 返回"**，语义上包含了 loop 自己那点开销。

差别很小，但它决定了以后能不能回答"慢在网络还是慢在我这层"。
现在 PopEngine 的 `kind=model` 事件是唯一的模型观测点，M3 对比 `total_ms` 够用；
要做 M5 的性能剖析就得拆。**先记着，不急改。**

### 1.3 不抄什么

**`direction` 字段是双进程的产物，单进程别抄。**

`TraceRecord.direction` 是 `"CLIENT→CORE" | "CORE→CLIENT" | "CORE" | "CORE→LLM" | "LLM→CORE"`。【读源码】
这四个箭头是 s2 引入的 daemon/TCP 架构逼出来的：trace 要同时记录 IPC 两个方向和
引擎内部。PopEngine 是单进程单线程，没有"从哪到哪"这回事 ——
`kind` 一个字段就够表达了。**这是 s2 带出来的字段，不是 s3 的功劳**，
也正说明"读 s2 价值低"只对 loop 成立，对 trace 不成立。

---

## 二、task：127 行实现的任务系统

### 2.1 它是什么

`Task` 七个字段：`id / subject / description / status / blocked_by / created_at / updated_at`。
`status` 三态：`pending` / `in_progress` / `completed`。【读源码】

没有 parent、没有 tree、没有 assignee、没有 priority。**依赖关系只有 `blocked_by: list[int]` 一个方向** ——
只记"我依赖谁"，不记"谁依赖我"。【读源码】

`blocked_by` 的反向维护用**写时清理**：任务被标 `completed` 时，`_clear_dependency()`
把这条 id 从**所有其他任务**的 blocked_by 里摘掉（全目录读一遍、改、写回）。【读源码】

代价是 O(全部任务) 的写放大；好处是 `format_list()` 读一条任务就能直接显示
`(blocked by: [2, 3])`，不需要反查。**一次写换很多次读，这个取舍对"给模型看的白板"是对的。**

### 2.2 最容易被名字骗掉的一点

`TaskManager` 是在 `AgentRunner.run_and_capture()` 里创建的，
路径是 `run_path / ".tasks"`。【读源码】

也就是说：**任务的生命周期绑在一次 run 上，不是跨 run 的。**

这个和"s2 之后有会话、s4 才有持久化"的顺序是一致的，但对名字的预期是反的。
你以为"任务系统"是 agent 的待办清单（像 TodoWrite 那样跨会话存在），
实际是"这次 run 内部的拆解"。**跨 run 的任务表要等会话持久化那一层**，
在 s3 里没有。

### 2.3 它是软约束，不是调度器

四个工具 `task_create / task_update / task_list / task_get` 全部只是
**读写文件 + 返回字符串**。没有任何地方**阻止**模型去做一个 blocked 的任务 ——
没有 `task_run`，没有引擎侧的 DAG 调度，引擎压根不知道任务的存在。【读源码】

所以这一层的能力上限是：**给模型一块看得见的白板，靠它自己遵守**。
想让它变成硬约束，得在引擎里加"当前可执行任务集"的概念 ——
那才是 L3 编排（和 graph 同一层），s3 没做。

### 2.4 关键证据：加这四个工具，loop 几乎没动

`s2 → s3` 的 `loop.py` 只改了 2 行，而且**跟任务无关**：`end_turn` 时补一句
`context.result = response.text or ""`。【读源码】
（这是 bug fix —— 不加的话 `RunOutcome.result` 永远是空串，TUI 拿不到答案。）

+2194 行里，引擎本体动了 1 行。这正好验证 `分层与边界.md` 那条：
**分子只有一个（那个循环），其余都是给循环加配件。**
加一个任务系统 = 4 个 ToolSpec + 一个共享的 manager 实例注入 registry，不动控制流。

---

## 三、一条能带走的通用区分：控制流状态给谁看

这是读完这两块之后我最想固定的一个概念，因为它把 graph 和 task 摆到了同一张表的两端：

| | 状态存在哪 | 谁写 | 模型看得见吗 | 引擎看得见吗 |
| --- | --- | --- | --- | --- |
| **graph 的 state** | 引擎局部变量 | 节点 + router | **不直接看**（节点里可以塞进 prompt） | 是，全部 |
| **task 的 Task 列表** | 文件 | 模型通过工具 | **是，就是给它看的** | **不看**（只当普通 ToolResult） |
| **loop 的 messages** | `RunResult.messages` | 引擎 | 是（下一轮喂回去） | 引擎用它，不解析它 |

三者的分工是**正交的**，不是三个竞争方案：

- graph 的 state 是**给引擎决定下一步用的**（结构视角）
- task 是**给模型自己排计划用的**（模型视角）
- messages 是**给模型记住发生过什么用的**（上下文视角）

⇒ 别问"用 graph 还是用 task"，要问"这个状态是给谁决策的"。
**同一个 run 里三者可以同时存在**：graph 节点里跑一个 AgentLoop，loop 里调 task 工具。

---

## 四、顺手发现的两处差异（PopEngine 有一处更好）

### 4.1 预算由语法保证 vs 由状态机保证

```text
PopEngine   for step in range(1, max_steps + 1):     ← 语法保证最多 N 圈
s3          while not context.is_done():             ← 靠 mark_failed 记得改 status
```

s3 的 `max_steps` 检查写在循环体**末尾**：`elif context.step >= context.max_steps:
context.mark_failed(...)`。【读源码】漏写这一行、或者漏了某个分支，就是死循环。
PopEngine 的 `for range` 是**结构性**的 —— 循环体里发生什么都逃不出 N 圈。

这是 PopEngine 更硬的地方，值得记下来：**能交给语法保证的，别交给纪律。**

### 4.2 s3 缺一个 `unknown` 分支，PopEngine 有

s3 的循环体只有三条出口：`end_turn` → success、`tool_use` → 跑工具、
`step >= max_steps` → failed。【读源码】

如果 provider 返回 `stop_reason="max_tokens"` 或别的不认识的值：不跑工具、
不标成功、不标失败 → 绕回去、`step += 1`、**空转把预算烧完**，
最后 error 是 `exceeded_max_steps`，把真正的死因藏了。

PopEngine `loop.py` 里那个 `unknown` 分支（注释写着"unknown 也是终点"）
干的就是这个。**这是我在 s3 里没找到、而 PopEngine 已经防住的一格。**
不是想证明我比它强，是想说明：这个坑不写测试基本发现不了，
它属于"只有真跑起来才会撞"的那类。

### 4.3 孤儿副作用在 s3 里长得一模一样

s3 的 `max_steps` 检查在**跑完工具之后**。所以第 N 步若返回 `tool_use`：
工具已经执行 → 结果已进 messages → 然后判 failed。【读源码】

和 PopEngine `test_l` 钉住的是同一个现象。**两个独立实现撞到同一个形状，
说明这不是谁不小心，是控制流的结构必然。**这条对 M5 的"重跑会不会执行两遍"是好消息：
它是结构问题，就有结构解法。

---

## 五、对 PopEngine 的动作项

| 项 | 判断 | 理由 |
| --- | --- | --- |
| 抄 `include_llm_payload` 开关思路 | ✅ 记下，M5 做上下文压缩时才动手 | 那时必须记 messages 长度 |
| 抄 latency 分层 | ⬜ 不急 | M3 的 `total_ms` 够用 |
| 抄 `direction` | ❌ 不抄 | 双进程产物，单进程无此维度 |
| 抄 task 系统 | ⬜ 等 M5 | 现在没有"模型自己排计划"的需求，M3 要的是结构对比 |
| 保持 `for range` 预算 | ✅ 不动 | 语法级保证优于纪律 |
| 保持 `unknown` 分支 | ✅ 不动 | s3 缺这格，别学它 |

---

## 六、留着没答案的

- **s3 的 trace 和 PopEngine 的 `--trace`
  谁更能回答"这次 run 为什么慢"，我没真跑过两边的长任务，没有数据。**
  按代码形状我倾向 PopEngine（事件带 step 和 kind，能按步拆），但这是【推断】。
- `TaskManager._next_id` 靠扫目录取 max+1，多个进程同时写会撞 id。
  单进程无所谓，**s2 建了双进程、s3 的任务却仍然假设单写者** ——
  这是它对 s2 的一个欠账，还是有意为之，我没找到证据。
