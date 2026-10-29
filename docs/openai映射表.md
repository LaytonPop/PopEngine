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
| 4 | `ToolCall(id, name, arguments: dict, raw_arguments)` | `{"id","index","type":"function","function":{"name","arguments":str}}` | 转换层 |
| 5 | `ToolSpec(name, description, parameters: dict)` | `{"type":"function","function":{…}}` | 转换层 |
| 6 | `LLMResponse.stop_reason: LLMStopReason` | `choices[0].finish_reason` | 转换层 |
| 7 | （我们没有） | `message.reasoning_content: str` | 转换层，入站丢弃 |

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
- **真差异 C（回程硬要求）：`content` 可以是 `null`，也可以有文本。** 协议两种都允许。我们 `content: str` 不收 None，**null 必须归一成 `""`**，否则第一句话就 `TypeError`。

  ⚠️ **这里我被真响应打过脸。** 原本以为「带 `tool_calls` 的 assistant 消息，`content` 就是 `null`」。2026-10-29 实测（`tests/fixtures/deepseek_tool_calls.json`）：模型回的是 `content: "我先读一下这个文件。"` **加**一个 `tool_calls`。所以不能写成「有 tool_calls 就把 content 丢掉」——那等于把模型说过的话抹掉，多轮回传时它会看到自己没说话。规则是**两种都收，有文本就留着**。

  出站发 `""` 兼容层一般能收，算软问题。

**责任：转换层。** loop 只负责把 provider 交回来的 `message` 追加进 `messages`，它不知道 wire 长什么样。

### 3. tool 消息 —— `name` 不发给模型

`content` 和 `tool_call_id` 直接对应。**我们的 `name` 在协议里没有对应字段**——那是旧版 function-calling 的遗留。内部留着调试好看，**序列化时必须省略**（`docs/M2-待办.md` 第 2 条）。

责任分两层，别混：转换层负责「序列化成什么形状」，loop 负责「什么时候组装这条消息、`tool_call_id` 指向谁」。

### 4. `arguments` —— 这里会真出事

`id` 直接对应，`name` / `arguments` 塞进 `function` 对象，补 `type: "function"`。

**关键不在「能转过去」，在转不过去时怎么办。** `function.arguments` 是**模型生成的字符串**，不是我们造的（实测：`'{"path": "README.md"}'`，就是 str）。以下都是真会发生的：

- 流式输出被截断 → `{"path": "a.p`
- 模型吐中文引号 → `{“a”: 1}`
- 吐空串、吐 `NaN`、吐两段拼在一起的 JSON

**定的策略：不崩。** 解析失败不让 run 失败，把错误包成 `ToolResult(is_error=True)` 还给模型，让它自己修参数重试。理由一条：**模型自己能修的错误，不该变成 Python 异常。**

落地方式（第四节已决议 A）：解析失败时 `arguments={}`、`raw_arguments=<原始串>`，由 loop 变成 `is_error=True` 的 ToolResult。

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

**定的策略：B —— `LLMStopReason` 加一个 `"unknown"`**（已落进 `types.py`）。

理由一句话：**内部类型不该比协议更穷。** 比协议穷，投影时必有损，而损失发生在最安静的地方——日志里看不出来，表现和正常结束一模一样。

代价：loop 得处理 `unknown`。现在 `if stop_reason == "end_turn" / elif == "tool_use"` 那套分支要加显式兜底，**别让 `unknown` 掉进 else 变成空转**。

**责任：转换层产出这个值；loop 只读懂，不做二次判断。**

### 7. `reasoning_content` —— 我们不认识的字段，以后还会有

实测的 `message` 里除了 `role` / `content` / `tool_calls`，还有 `reasoning_content`（思考链）。**这个键一直存在**：普通回答里是一段文本，工具调用里是空串。

我们不入链：它是模型的草稿，不是对话内容。不参与多轮上下文，回传没有收益、只花 token。（这是我们的取舍，不是厂商的规矩。）

真正要学的不是这一个字段，是：**协议会长出新字段。** 所以回程解析必须**宽松**——只白名单取你要的键，不认识的一律忽略，绝不 `Message(**msg)` 或 strict 校验。少一个字段要能跑，多十个字段也要能跑。

## 三、这次要分清的三类东西

**载体差异 ≠ 表示差异。** 再加一类：协议比你大。

- **表示差异**：wire 上真的少了或多了一个 bit（`null` vs `""`、2 值 vs 6 值、`is_error` 无处安放）→ 要处理，要写测试
- **载体差异**：同一个抽象值在两种语言里的不同容器或编码（tuple vs list）→ 转换时顺手就对，不值得写测试
- **协议比你大**：多了你不需要的字段（`reasoning_content`、`tool_calls[].index`、`choices[0].logprobs`、`usage` 的一大串）→ 忽略，但**解析不能因此崩**

前两类分不清的代价不是写错代码，是**写出一批永远绿、永远没用的测试**，然后把它当覆盖率。第三类分不清的代价是**上线当天崩**——因为对方加了个字段。

## 四、已决议：`arguments` 解析失败怎么落地

**问题**：第 4 行的策略（不崩、包成 `ToolResult(is_error=True)`）没法在纯转换层里实现。

原因很硬：转换层的输出是 `LLMResponse`，而 `ToolResult` 是 loop 拿着 `ToolCall` 去**执行工具**之后才产生的。转换层手里根本没有 `ToolResult` 这个类型可用——它造不出错误该去的那个容器。

**决定：A。** `ToolCall` 加字段 `raw_arguments: str | None = None`。解析失败时 `arguments={}`、`raw_arguments=<原始串>`；执行工具前若 `raw_arguments` 非空，直接产出 `is_error=True` 的 ToolResult，把原始串回给模型让它自己改。

否掉的两个：B（`arguments: dict | None`，原始串丢了，模型看到报错也不知道错在哪）；C（`LLMResponse` 挂 `parse_errors`，信息最全但错误与调用要额外配对，v1 太贵）。

## 五、接入参数（非机密）

| 项 | 值 |
|---|---|
| base_url（OpenAI 兼容） | `https://api.deepseek.com` |
| 端点 | `{base_url}/chat/completions`——注意这家 base_url **不带** `/v1` |
| model（默认） | `deepseek-flash` |
| model（备选） | `deepseek-v4-pro` |
| Anthropic 兼容入口 | `https://api.deepseek.com/anthropic`（本项目不用） |

`api_key` 只进 `.env`，变量名见仓库根的 `.env.example`。

## 六、真实响应实测记录（2026-10-29）

第一次真调用的原始响应存在 `tests/fixtures/`：

- `deepseek_tool_calls.json` —— 带工具问「读 README.md」，回 `finish_reason: "tool_calls"`，1 个调用
- `deepseek_plain.json` —— 不带工具问「只回三个字」，回 `finish_reason: "stop"`

实测确认 / 打脸的几件事：

1. `function.arguments` 确实是 **str**：`'{"path": "README.md"}'`
2. 带 `tool_calls` 的 assistant 消息，`content` **不一定是 null**，实测是文本 → 见第 2 行真差异 C
3. `message` 里有 `reasoning_content`；`tool_calls[]` 里有 `index`；`choices[0]` 里有 `index` / `logprobs`；顶层有 `object` / `created` / `system_fingerprint` / `usage`（含 `prompt_cache_hit_tokens`、`reasoning_tokens`）→ 全部是我们不要的字段，全部必须能忽略
4. `usage` 会花真钱：那次带工具的调用 `prompt_tokens: 317` / `completion_tokens: 44`（工具定义占了 317 里的大头）

**一句话教训**：我能写出来的「OpenAI 响应长什么样」只是记忆，真响应才是证据。**fixture 比文档可靠**。
