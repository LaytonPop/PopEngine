# 证据留档

> `traces/` 是「跑一次生一次」的产物，不入库（见 `.gitignore`）。
> 这里放的是**值得留的那两次**：一次成功、一次故意失败。

## m2-run-ok.jsonl

2026-11-10 跑本仓库的结果。26 行：

```
model 6 行（== RunResult.steps）
tool_start 10 行
tool_end   10 行
总行数 26 == 6 + 2×10
```

**为什么值得留**：它是"事件账目能对上"的实物样本。
只看代码的话，"model 行数 == steps" 是个断言；
看一眼这个文件，能直接数出来。

**能讲什么**：
- 每一步之间有 `duration_ms`，能看出哪一步慢（模型调用毫秒级上千，工具调用多在个位数）
- `tool_start` / `tool_end` 靠 `tool_call_id` 配对，顺序就是发生的顺序
- 字段是白名单（8 个），没有 request body、没有 header

## m2-provider-401.jsonl

2026-11-10 故意用错 key（`sk-wrong-key-on-purpose`）跑出来的。

```json
{"kind": "model", "step": 1, "stop_reason": null, "duration_ms": 397.99,
 "error": "RuntimeError:HTTP请求失败，状态码：401，响应内容：{...Authentication Fails...}"}
```

**为什么值得留**：c 轮的最后一条要求是「故意失败一次，把那份 events 留下」。
这里是它落成的样子 —— **一行、`stop_reason` 是 null、`error` 里带状态码和服务端原文**。

**能讲什么**（这是面试里「你怎么调一个 agent」的答案素材）：
1. 失败不是"没有事件"，而是"有一条 `stop_reason=null` 的事件"。查日志的人一眼能看出：
   模型调用发生了（`duration_ms` 有值），但没有拿到响应（`stop_reason` 是 null）。
2. `error` 字段装的是**服务端原文**。这比"请求失败"四个字有用得多——
   401 那次它直接说了 `Authentication Fails`，不用猜是 key 错还是地址错。
3. `steps: 1` —— 一次就止损，没有重试、没有绕圈。

## ⚠️ 一个必须知道的风险

`error` 字段装的是**服务端返回的原文**，我们没法控制里面写什么。

这次 DeepSeek 自己把 key 脱敏了（回显成 `****pose`），所以文件里没有完整 key。
**但这不是我们能保证的**——换个服务商，如果它在错误信息里回显完整 key，
这个字段就会把 key 写进日志。

所以验收手册里那条红线（`Select-String -Pattern "sk-","Authorization"`）**每次跑完都要执行**，
不能因为"上次是干净的"就跳过。真要更稳，可以在 `provider.py` 抛错前把 key 从
`detail` 里替换掉——那是 provider 层的责任（它才知道 key 是什么），不是 loop 的。

这个坑记在这儿，是因为它属于"看不见的耦合"：我们的日志安全性，
依赖了别人的错误信息写法。
