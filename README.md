# kama-engine

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
- [ ] M1 假数据跑通 loop
- [ ] M2 接真模型（OpenAI 兼容 / DeepSeek）+ 真实任务
- [ ] M3 graph 引擎跑同一任务
- [ ] M4 《loop vs graph》对比 + 面试讲稿

阶段 0 一页纸：`D:\workSpace\Pop\github\kamacode\面试项目\02-阶段0-一页纸.md`

## 开发

```bash
uv sync
uv run pytest
```

## 目录

```text
src/kama_engine/   引擎源码（M1 起开始填）
tests/             测试（假 LLM + 假工具，不需要 API key）
```
