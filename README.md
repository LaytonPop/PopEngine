# PopEngine

一个我自己写得出来、每行都能解释的 agent 引擎。**loop 为主，graph 做对比。**

> 设计目标：面试时能站着讲 20 分钟，每个设计决定都能回答「为什么」。
> 参照：waku-agent 的形状 + KamaClaude S1 的源码。设计文档都在本仓库 `docs/`。

## 这不是什么

- 不是框架：没有插件系统、没有 YAML 配置
- 不是多 agent、不是分布式
- 不是壳层前端：没有 TUI、没有 Web
- 不是 RAG：不内置向量库

## 当前进度

- [x] M0 阶段 0：一页纸 + 仓库骨架
- [x] M1 假数据跑通 loop（2026-10-27：12 个测试全绿）
- [x] M2 接真模型（OpenAI 兼容 / DeepSeek）+ 真实任务
  - [x] 转换层 `openai_compat.py`（2026-10-29：18 个测试全绿）
  - [x] 接线真跑 · 真工具 · events.jsonl（2026-11-10：29 passed，`docs/evidence/` 留档可回放）
- [x] M3 graph 引擎 + 同任务对比（2026-11-26：67 passed）
  - [x] `graph/` 包：引擎 + 契约 + `agent_node`，`max_visits` + `max_steps` 双护栏
  - [x] `agent_node` 把一次 `AgentLoop.run()` 包成节点 —— 图包裹循环，不替代它
  - [x] 同任务同模型各跑 3 次，产出对比报告 [`docs/loop-vs-graph.md`](docs/loop-vs-graph.md)
- [ ] M4 L2 机制层四关（对标 `KamaClaude@stage/s6` + `waku-agent` 真源码） ← **当前**
  - [ ] 第 1 关：循环薄参数（`before_model` / `should_stop`）+ 撞线禁工具
  - [ ] 第 2 关：4 层静态权限评估 + `PermissionGate` 装饰器 + `write_file` / `run_command`
  - [ ] 第 3 关：`SessionStore` + `on_message` 实时落盘 + 孤儿 `tool_use` 补齐恢复
  - [ ] 第 4 关：两级上下文压缩（一级免费 `shrink_seen` + 二级六段交接摘要）
- [ ] M5 README 收口 + 面试讲稿

**测试现状**：`uv run pytest` → 67 passed（无需 API Key，假 Provider + 假工具）。

**待办清单（唯一入口）：[`docs/TODO.md`](docs/TODO.md)** ← 每次开工先看这个

阶段 0 一页纸：属个人笔记，未随仓库发布。冻结清单见 [`docs/TODO.md`](docs/TODO.md)。

## 开发

```bash
uv sync
uv run pytest
```

## 目录

```text
src/pop_engine/
  types.py            词汇表：Message / ToolCall / ToolResult / LLMResponse / StepEvent / RunResult
  loop.py             L1 分子：循环体
  openai_compat.py    转换层：内部类型 ⇄ OpenAI wire 格式（纯函数，不碰网络）
  metrics.py          观测纯函数：从 StepEvent 算 model_calls / total_ms
  llm/                模型层（包）
    __init__.py       边界：一个 chat() 签名（Protocol），可换假模型
    provider.py       真实现：OpenAI 兼容 HTTP（零 SDK 依赖）
  tools/              工具层（包）
    __init__.py       边界：ToolRegistry.invoke() 永不抛异常
    builtin.py        真工具：list_dir / read_file
  graph/              L3 编排层（包）
    types.py          State / NodeFn / RouterFn 契约
    engine.py         最小图执行引擎（max_visits + max_steps 双护栏）
    agent_node.py     把 AgentLoop.run() 包成图节点，合并带 node 标签的 StepEvent
tests/
  fakes.py            假 LLM + 假工具，不需要 API key
  test_loop.py        loop 场景与预算边界（14 条）
  test_openai_compat.py
  test_tools.py
  test_metrics.py
  test_graph.py
  test_agent_node.py
docs/
  TODO.md             ★ 唯一待办清单（从这里进）
  M4-任务书.md         当前阶段：L2 机制层四关切口、真源码行号与反向验收
  loop-vs-graph.md    M3 产出：loop / graph 同任务实测对比报告
  协作约定.md          怎么提交：分支 alma/M<n> Pop/M<n>、commit 格式、红线
  验收手册.md          a–d 轮怎么验：跑什么、怎么算过、怎么故意弄坏
  design/             设计文档，还活着、还被引用的「为什么」
    分层与边界.md       原子 / 分子 / 五层 / 框架到底封装了什么
    三层与两轴.md       agent = loop + graph + harness 的两轴结构
    openai映射表.md     协议逐行对照 + 真实响应实测记录
    事件设计.md         StepEvent 为什么这么设计
    引擎对比.md         和 pi / Codex / Claude Code 的逐行对比（M3 依据）
    s3对照-task与trace.md 两种可观测与任务约束哲学
  history/            阶段性完结，留作记录，不再更新
    M1-任务书.md / M2-任务书.md / M2-真实任务.md / M2-待办.md / M3-任务书.md
    archive/          作废的计划，不要照做
  diagrams/           六张图（01–03 协议与状态机 / 04–06 graph 与三层结构）+ 四组真数据对照
  evidence/           两次真跑的 jsonl 留档（一次成功、一次 401）
```
