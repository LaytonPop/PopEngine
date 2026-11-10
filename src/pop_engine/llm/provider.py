"""
- 新建 `src/pop_engine/llm/provider.py`，实现 `OpenAIProvider.chat(messages, tools) -> LLMResponse`
  内部顺序固定死：
  ``to_wire(messages) → to_wire_tools(tools) → POST /chat/completions``
  ``→ 检查 status_code → from_wire(resp.json())``
- [ ] 用标准库 `urllib.request`，**不引 openai SDK**
  （理由：多一个依赖 = 多一层看不见的转换；面试时"HTTP 是我自己发的"更好讲）
- [ ] key 只从环境变量读，**任何情况下不打印**
- [ ] 写 `scripts/hello_llm.py`：一句 user 输入 → 打印 `RunResult`
- [ ] 真跑一次

"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pop_engine.openai_compat import from_wire, to_wire, to_wire_tools
from pop_engine.types import LLMResponse, Message, ToolSpec


class OpenAIProvider:
    def __init__(self, timeout: float = 60.0) -> None:
        # 从环境变量中读取配置，不直接把api写入到代码里面
        self._base_url = self._required_env("KAMA_LLM_BASE_URL").rstrip("/")
        self._api_key = self._required_env("KAMA_LLM_API_KEY")
        self._model = self._required_env("KAMA_LLM_MODEL")

        if timeout <= 0:
            raise ValueError(f"timeout必须大于0,当前实际传入为{timeout}")
        self._timeout = timeout

    @staticmethod
    def _required_env(name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise RuntimeError(f"环境变量{name}未设置或为空")
        return value

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> LLMResponse:
        """
        1、把内部消息对象转换成外部请求需要的字典列表
        2、把内部工具对象转换成外部请求需要的字典列表
        3、发起HTTP请求，获取响应
        4、检查响应状态码，如果不是200，抛出异常
        5、解析响应JSON，转换成LLMResponse对象返回
        """
        # 1、把内部消息对象转换成外部请求需要的字典列表
        wire_messages = to_wire(messages)
        # 2、把内部工具对象转换成外部请求需要的字典列表
        wire_tools = to_wire_tools(tools)

        payload = {
            "model": self._model,
            "messages": wire_messages,
        }

        # 空工具清单直接省略，避免部分兼容服务拒绝
        if wire_tools:
            payload["tools"] = wire_tools

        # 3.python字典->json文本->HTTP请求需要的字节。

        body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            url=f"{self._base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        # 4.发起HTTP请求，设置单纯超时，不重试
        try:
            with urlopen(request, timeout=self._timeout) as response:
                status = response.status
                response_body = response.read()

                # AI 改: 这段其实是"走不到"的 —— urllib 遇到 4xx/5xx 已经抛 HTTPError 了。
                # 保留只为挡住"中间代理改了状态码但仍是 2xx 形态"这种边角情况。
                # 顺手把 read() 提到判断之前，body 只读一次。
                if not 200 <= status < 300:
                    detail = response_body.decode("utf-8", errors="replace")
                    raise RuntimeError(f"HTTP请求失败，状态码：{status}，响应内容：{detail}")

        except HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            e.close()
            raise RuntimeError(f"HTTP请求失败，状态码：{e.code}，响应内容：{detail}") from None

        except TimeoutError:
            raise RuntimeError("模型请求超时") from None
        except URLError:
            raise RuntimeError("无法连接模型服务，请检查地址、网络和超时配置") from None

        # 5.http 响应字节 ->json字典
        try:
            data = json.loads(response_body.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"响应不是有效的JSON：{response_body.decode('utf-8')}") from e

        if not isinstance(data, dict):
            raise RuntimeError("模型服务返回的 JSON 顶层必须是对象")

        # 6. 外部响应 -> 内部 LLMResponse。
        return from_wire(data=data)
