# PopEngine

一个我自己写得出来、每行都能解释的 agent 引擎。**loop 为主，graph 做对比。**

> 设计目标：面试时能站着讲 20 分钟，每个设计决定都能回答「为什么」。
> 参照：waku-agent 的形状 + KamaClaude S1 的源码。笔记在 `D:\workSpace\Pop\github\kamacode`。

## 这不是什么

- 不是框架：没有插件系统、没有 YAML 配置
- 不是多 agent、不是分布式
- 不是产品：没有 TUI、Web、权限审批、会话持久化
- 不是 RAG：v1 不碰向量库

## 当前进度

- [x] M0 阶段 0：一页纸 + 仓库骨架
- [x] M1 假数据跑通 loop（2026-10-27：11 个测试全绿）
- [ ] M2 接真模型（OpenAI 兼容 / DeepSeek）+ 真实任务 ← **当前**
  - [x] 转换层 `openai_compat.py`（2026-10-29：18 个测试全绿）
  - [ ] 接线真跑 · 真工具 · events.jsonl
- [ ] M3 graph 引擎跑同一任务
- [ ] M4 《loop vs graph》对比 + 面试讲稿

**待办清单（唯一入口）：[`docs/TODO.md`](docs/TODO.md)** ← 每次开工先看这个

阶段 0 一页纸：`D:\workSpace\Pop\github\kamacode\面试项目\02-阶段0-一页纸.md`

## 开发

```bash
uv sync
uv run pytest
```

## 目录

```text
src/pop_engine/
  types.py            词汇表：Message / ToolCall / ToolResult / LLMResponse / RunResult
  llm/                模型层（包）
    __init__.py       边界：一个 chat() 签名（Protocol），可换假模型
    provider.py       真实现：OpenAI 兼容 HTTP（M2 作业）
  tools/              工具层（包）
    __init__.py       边界：ToolRegistry.invoke() 永不抛异常
    builtin.py        真工具：list_dir / read_file（M2）
  loop.py             循环体（M1 主战场）
  openai_compat.py    转换层：内部类型 ⇄ OpenAI wire 格式（纯函数，不碰网络）
tests/
  fakes.py            假 LLM + 假工具，不需要 API key
  test_loop.py        M1 四场景
  test_openai_compat.py
docs/
  TODO.md             ★ 唯一待办清单
  M2-任务书.md         M2 怎么切、检查点、会踩的坑
  openai映射表.md      协议逐行对照 + 真实响应实测记录
  diagrams/           两张图 + 四组真数据对照
  archive/            作废的计划，不要照做
```
