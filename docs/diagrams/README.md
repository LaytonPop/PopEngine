# 三层世界速查：同一份数据，两种长相

这里放三样东西，解决同一个问题——**我们的类型、线上协议、和中间那层转换，到底各长什么样**。

| 看什么 | 看哪 | 回答什么问题 |
| --- | --- | --- |
| 数据怎么流过去、什么时候变形 | [01-run-sequence.html](./01-run-sequence.html) | 一张图看完一次 run，变形只发生在两条竖线上 |
| 进来的时候哪几步会出错 | [02-from-wire.html](./02-from-wire.html) | 五道关卡，每一关都可能拿到垃圾，每一关都不许抛 |
| 同一个东西在两边分别长什么样 | 下面这张表 | 左边是 Python，右边是真 JSON（从 `tests/fixtures/` 抄的，不是编的） |

---

## 1. 模型要工具那一轮（入境）

**线上真响应**（`tests/fixtures/deepseek_tool_calls.json` 原文节选）：

```json
{
  "role": "assistant",
  "content": "我先读一下这个文件。",
  "reasoning_content": "",
  "tool_calls": [
    {
      "index": 0,
      "id": "call_00_ET_9ywpIUwsKe7aCs23AdiM4605",
      "type": "function",
      "function": { "name": "read_file", "arguments": "{\"path\": \"README.md\"}" }
    }
  ]
}
```

**我们这边**：

```python
Message(
    role="assistant",
    content="我先读一下这个文件。",
    tool_calls=(
        ToolCall(
            id="call_00_ET_9ywpIUwsKe7aCs23AdiM4605",
            name="read_file",
            arguments={"path": "README.md"},
            raw_arguments=None,
        ),
    ),
)
```

三个变形：

1. `tool_calls[]` 外面那层 `{index, type, function:{...}}` 包装被拆掉了 —— 我们不需要 `type`（永远 `"function"`）也不需要 `index`（顺序就是 tuple 顺序）。
2. `arguments` 从 **JSON 字符串**变成 **dict**。这一步会失败，失败时 `arguments={}`、`raw_arguments` 留原文。
3. `reasoning_content`、`index`、`logprobs`、`usage` **整块丢掉**。丢法是只按白名单取键，不写 `Message(**msg)`。

> 反常识的一条：带 `tool_calls` 的时候 `content` **也可能是文本**（这次就是「我先读一下这个文件。」）。所以不能「有 tool_calls 就把 content 丢空」。

---

## 2. 工具结果回给模型（出境）

**我们这边**：

```python
Message(
    role="tool",
    content="README.md 共 42 行 ……",
    tool_call_id="call_00_ET_9ywpIUwsKe7aCs23AdiM4605",
    name="read_file",
)
```

**线上要发出去的**：

```json
{
  "role": "tool",
  "tool_call_id": "call_00_ET_9ywpIUwsKe7aCs23AdiM4605",
  "content": "README.md 共 42 行 ……"
}
```

唯一的变形：**`name` 不写出去**。`name` 是 OpenAI 早期工具协议的遗留字段，现在靠 `tool_call_id` 回溯就够；留着不发比发出去更稳（有的网关会因为多余字段直接 400）。

`tool_call_id` 一个字节都不能动 —— 它是把结果对回某次调用的唯一凭据（模型一次要 3 个工具时就靠它）。

---

## 3. 工具说明书（出境）

**我们这边**：

```python
ToolSpec(
    name="read_file",
    description="读一个文本文件，返回全文。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "相对仓库根目录的路径"}},
        "required": ["path"],
    },
)
```

**线上**：

```json
{
  "type": "function",
  "function": {
    "name": "read_file",
    "description": "读一个文本文件，返回全文。",
    "parameters": {
      "type": "object",
      "properties": { "path": { "type": "string", "description": "相对仓库根目录的路径" } },
      "required": ["path"]
    }
  }
}
```

变形：**外面补一层 `{type, function}` 包装**。

注意 `parameters` **不要 `json.dumps`**。它本来就是 dict，跟着最外层 payload 一起被序列化一次就够；手动 dumps 一遍会得到「字符串里包着 JSON」，模型收到的是文本不是 schema。

---

## 4. 为什么停下

| 我们 | 线上 |
| --- | --- |
| `LLMStopReason = Literal["end_turn", "tool_use", "unknown"]` | `finish_reason ∈ {stop, tool_calls, length, content_filter, insufficient_system_resource, ...}` |

映射：

- `stop` → `end_turn`
- `tool_calls` → `tool_use`
- **其它全部**（含以后新加的）→ `unknown`

为什么留 `unknown` 而不是兜底成 `end_turn`：被截断的答案（`length`）和答完的答案（`stop`）如果都变成 `end_turn`，日志里长得一模一样 —— 这是最容易静默出错的地方。内部类型不该比协议更穷。

---

## 一句话判据

**谁碰 wire 格式，谁负责。** `loop.py` 里不该出现 `finish_reason` 这个词；`openai_compat.py` 里不该出现 `while` 循环。
