# TODO —— 唯一清单

> **这是唯一入口。** 其他 `docs/*.md` 讲的是"为什么这么做、检查点是什么"，不是待办。
> 两边说法冲突时，以本文件为准；做完一件事就回来打勾。
> 更新时间：2026-10-31

## 进度总览

| 里程碑                            | 状态         |
| --------------------------------- | ------------ |
| M0 骨架                           | ✅           |
| M1 假数据 loop（4 场景）          | ✅           |
| M2 真模型 · 转换层               | ✅ 18 passed |
| M2 真模型 · 接线 + 真工具 + 事件 | 🚧 第 1 段已通 |
| M3 graph 引擎跑同一任务 + 对比    | ⬜           |
| M4 README + 面试讲稿              | ⬜           |

---

## 现在做：M2

### 0. 开工前置（5 分钟，必须先做）

- [X] **定真实任务** → 已落盘 `docs/M2-真实任务.md`（2026-11-04，三条验收断言可进测试）

### 1. 接线 + 场景 A 真跑（约 60 分钟）

- [X] `src/kama_engine/llm/provider.py`：实现 `OpenAIProvider.chat()`（urllib，不引 SDK）
- [X] `scripts/hello_llm.py`：一句输入 → 打印 `RunResult`
- [X] 真跑一次 → 验收：真实输出 + `stop_reason: end_turn`（2026-11-04 通过：`steps=1`，真模型输出；负向也验了：错 key → 401 带完整响应体）
- [ ] （可选，真跑前补最划算）`to_wire_tools` 的测试 —— 三条出境路径里唯一裸奔的

### 2. 真工具 + 场景 B 真跑（约 60 分钟）

- [ ] `src/kama_engine/tools/builtin.py`：`list_dir` / `read_file`
  （锁根目录、越界返 `is_error=True`、截断要明说、参数写 JSON Schema）
- [ ] 真跑 → 验收：trace 里两轮工具往返，且是模型自己决定先 list 再 read
- [ ] `stop_reason` 与 `tool_calls` 一致性校验（`M2-待办.md` 第 1 条，**接工具后第一件**）

### 3. 事件 + 失败记录（约 45 分钟）

- [ ] 每步发事件 → 落 `events.jsonl`（step / stop_reason / 工具名 / 耗时 / is_error）
- [ ] `max_steps` 终局事件写人话（`M2-待办.md` 第 3 条）
- [ ] 故意失败一次（错 key 或错模型名），把那份 events 留下

### 4. M2 收尾

- [ ] `self.tools.specs()` 提到循环外（`M2-待办.md` 第 3 条；`loop.py` 的小优化，跑通后再动）
- [ ] `uv run pytest -q` + ruff + mypy 三件套
- [ ] 提交 + 回来更新本文件的勾

---

## 之后

### M3：graph 引擎跑同一任务 + 对比

- [ ] 先读第 3 段产出的 `events.jsonl`，确定"对比"要比哪三个数字
- [ ] graph 引擎（黑板 dict + wave 执行 + router 是普通函数 + `max_visits`/`max_steps` 双护栏）
- [ ] 同一任务、同一模型跑两遍，把数字并排放

### M4：README + 面试讲稿

- [ ] README 补齐进度
- [ ] 讲稿：loop vs graph、怎么调 agent（用那份失败记录）、为什么工具参数用 JSON Schema

---

## 冻结（想动先回一页纸读"不做什么"）

多 agent · RAG / 向量库 · MCP · TUI · 会话持久化 · 重试 · 上下文压缩 · 流式 · 权限审批 · 插件系统

---

## 文件地图（哪个文件是干什么的）

| 文件                                    | 角色                                                 |
| --------------------------------------- | ---------------------------------------------------- |
| `docs/TODO.md`                        | **本文件**：唯一清单                           |
| [M2-任务书.md](M2-任务书.md)             | M2 的说明：三段怎么切、每段检查点、会踩的坑          |
| [M2-待办.md](M2-待办.md)                 | M1 review 留下的 4 条，已并入本文件，留原文看上下文  |
| [openai映射表.md](openai映射表.md)       | 内部类型 ⇄ OpenAI 协议的逐行对照 + 真实响应实测记录 |
| [diagrams/README.md](diagrams/README.md) | 四组真数据对照 + 两张图怎么读                        |
| [M1-任务书.md](M1-任务书.md)             | M1 的作业书，已完成，留作记录                        |
| `docs/archive/`                       | 作废的计划，**不要照做**                       |
