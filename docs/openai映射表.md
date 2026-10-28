# OpenAI 兼容协议的映射表

> 这个文件只回答一个问题：**我们的 `types.py` 和 OpenAI Chat Completions 的 JSON 怎么互转，转的时候丢了什么。**
> 转换代码住 `src/kama_engine/openai_compat.py`（纯函数，不碰网络）；`llm_openai.py` 只管发 HTTP。
> 验收判据：每一行都要能回答「如果这一步转换失败，run 会怎样」。

## 一、一眼看全

| # | 我们的（`types.py`） | 协议（Chat Completions） | 责任 |
|---|---|---|---|
| 1 | `Message(role="user", content: str)` | `{"role":"user","content":str}` | 转换层 |
| 2 | `Message(role="assistant", content, tool_calls)` | `{"role":"assistant","content":str\|null,"tool_calls":[...]}` | 转换层 |
| 3 | `Message(role="tool", tool_call_id, content, name)` | `{"role":"tool","tool_call_id":…,"content":…}` | 转换层（序列化）/ loop（组装） |
| 4 | `ToolCall(id, name, arguments: dict)` | `{"id","type":"function","function":{"name","arguments":str}}` | 转换层 |
| 5 | `ToolSpec(name, description, parameters: dict)` | `{"type":"function","function":{…}}` | 转换层 |
| 6 | `LLMResponse.stop_reason: LLMStopReason` | `choices[0].finish_reason` | 转换层 |

判据一句话：**谁碰 wire 格式，谁负责。** loop 从头到尾不该看见 `finish_reason`、`function.arguments` 这些词。责任一划错，后面写实现就会把 JSON 处理漏进 loop，然后 loop 的测试要开始 mock HTTP。

## 二、逐行细说

### 1. user 消息 —— 只有一处能力缺失

`role` / `content` 直接对应，无损。转成 user 消息时不带任何工具字段。

**丢的：多模态。** 协议的 `content` 可以是「文本片段 + 图片 URL」的数组，我们只有 `str`。但方向是单向的：

- 我们 → 协议：**无损**（文本是协议的子集）
- 协议 → 我们：**有损**（图片、音频表达不了）

所以 `Message.content: str` 是协议类型的一个**子集嵌入**。现在不处理——引擎是纯文本的，这是能力边界，不是 bug。真要上多模态，动点是把 `content: str` 改成 `content: str | list[Part]`。

### 2. assistant 消息 —— 三处差异，一处是假差异

- **假差异（别记）：元组 → 数组。** JSON 没有 tuple。`tuple` 是我们在 Python 侧选不可变容器，到了 wire 上就是 `[...]`，和 `list` 一个字节不差。这是**载体差异**，不是表示差异，别为它写测试。
- **真差异 A：多一层包装。** 协议要 `{"type":"function","function":{"name","arguments"}}`，我们是扁的 `ToolCall(id, name, arguments)`。出站补包装，入境拆包装。
- **真差异 B：`arguments` 是 JSON 字符串。** 我们是 `dict`，协议是字符串。出站 dumps，入境 loads——而 loads 可能失败，见第 4 行。
- **真差异 C（回程硬要求）：`content` 可能是 `null`。** 带 `tool_calls` 的 assistant 消息，协议里 `content` 就是 `null`。我们 `content: str` 不收 None，**回程必须归一成 `""`**，否则第一句话就 `TypeError`。出站发 `""` 大部分兼容层能收，算软问题。

**责任：转换层。** loop 只负责把 provider 交回来的 `message` 追加进 `messages`，它不知道 wire 长什么样。

### 3. tool 消息 —— `name` 不发给模型

`content` 和 `tool_call_id` 直接对应。**我们的 `name` 在协议里没有对应字段**——那是旧版 function-calling 的遗留。内部留着调试好看，**序列化时必须省略**（`docs/M2-待办.md` 第 2 条）。

责任分两层，别混：转换层负责「序列化成什么形状」，loop 负责「什么时候组装这条消息、`tool_call_id` 指向谁」。

### 4. `arguments` —— 这里会真出事

`id` 直接对应，`name` / `arguments` 塞进 `function` 对象，补 `type: "function"`。

**关键不在「能转过去」，在转不过去时怎么办。** `function.arguments` 是**模型生成的字符串**，不是我们造的。以下都是真会发生的：

- 流式输出被截断 → `{"path": "a.p`
- 模型吐中文引号 → `{“a”: 1}`
- 吐空串、吐 `NaN`、吐两段拼在一起的 JSON

**定的策略：不崩。** 解析失败不让 run 失败，把错误包成 `ToolResult(is_error=True)` 还给模型，让它自己修参数重试。理由一条：**模型自己能修的错误，不该变成 Python 异常。**

⚠️ 这个策略有个结构性后果，见第四节。

### 5. ToolSpec —— 不 dumps

`name` / `description` / `parameters` 原样放进 `function` 对象，补 `type: "function"`。

`parameters` 已经是 JSON Schema **对象**（dict），**不要单独 `json.dumps`**——整个 payload 最后才序列化一次。单独转成字符串，某些网关会把它当普通字符串收，语义就错了。

### 6. `finish_reason` —— 内部类型比协议穷

映射：`stop → end_turn`，`tool_calls → tool_use`。

协议给的值比我们多，而且不同厂商还不一样（DeepSeek 还有 `insufficient_system_resource`）：

| finish_reason | 兜成 `end_turn` 会丢什么 |
|---|---|
| `stop` | — |
| `tool_calls` | — |
| `length` | **被截断了**，但没人知道 |
| `content_filter` | 内容被拦了，看起来像正常结束 |
| `insufficient_system_resource` | 服务端资源不足，也会被当成答完 |
| `function_call`（旧版） | 兼容策略待定 |

**定的策略：B —— `LLMStopReason` 加一个 `"unknown"`。**

理由一句话：**内部类型不该比协议更穷。** 比协议穷，投影时必有损，而损失发生在最安静的地方——日志里看不出来，表现和正常结束一模一样。

代价：loop 得处理 `unknown`。现在 `if stop_reason == "end_turn" / elif == "tool_use"` 那套分支要加显式兜底，**别让 `unknown` 掉进 else 变成空转**。

**责任：转换层产出这个值；loop 只读懂，不做二次判断。**

## 三、这次要分清的一类东西

**载体差异 ≠ 表示差异。**

- **表示差异**：wire 上真的少了或多了一个 bit（`null` vs `""`、2 值 vs 6 值、`is_error` 无处安放）→ 要处理，要写测试
- **载体差异**：同一个抽象值在两种语言里的不同容器或编码（tuple vs list）→ 转换时顺手就对，不值得写测试

分不清的代价不是写错代码，是**写出一批永远绿、永远没用的测试**，然后把它当覆盖率。

## 四、待决议（明天第一件事）

**第 4 行的策略（不崩、包成 `ToolResult(is_error=True)`）没法在纯转换层里实现。**

原因很硬：转换层的输出是 `LLMResponse`，而 `ToolResult` 是 loop 拿着 `ToolCall` 去**执行工具**之后才产生的。转换层手里根本没有 `ToolResult` 这个类型可用——它造不出错误该去的那个容器。

三个方向：

- **A（最小改动）**：`ToolCall` 加字段 `raw_arguments: str | None = None`。解析失败时 `arguments={}`、`raw_arguments=<原始串>`；执行工具前若 `raw_arguments` 非空，直接产出 `is_error=True` 的 ToolResult，把原始串回给模型让它自己改。
- **B**：`arguments: dict` 改成 `dict | None`，`None` 表示解析失败。原始串丢了，模型看到报错也不知道错在哪——不好。
- **C**：`LLMResponse` 上挂 `parse_errors: tuple[...]`。信息最全，但类型变复杂，且错误与调用要额外配对。

倾向 **A**：只加一个字段，失败路径只有一条，原始串还留着。

## 五、接入参数（非机密）

| 项 | 值 |
|---|---|
| base_url（OpenAI 兼容） | `https://api.deepseek.com` |
| 端点 | `{base_url}/chat/completions`——注意这家 base_url **不带** `/v1` |
| model（默认） | `deepseek-flash` |
| model（备选） | `deepseek-v4-pro` |
| Anthropic 兼容入口 | `https://api.deepseek.com/anthropic`（本项目不用） |

`api_key` 只进 `.env`，变量名见仓库根的 `.env.example`。
