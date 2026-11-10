"""第 1 段验收脚本：一句 user 输入 -> 真模型 -> 打印 RunResult。

不接工具：先证明"HTTP 接线 + 转换层 + 循环"这一整条路能通。
工具往返是第 2 段的事，这里多接一个变量只会让第一次排错更难。

跑法（在项目根目录）：
    uv run python scripts/hello_llm.py
    uv run python scripts/hello_llm.py "用一句话解释什么是 agent"

注意：os.environ 只认进程环境变量，不自动读 .env。
    如果报"环境变量未设置"，要么先 source/设置环境，要么用下面 load_dotenv 那段。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """.env -> os.environ 的最小读取器。

    只用标准库：不为了"读一行 KEY=VALUE"引入 python-dotenv。
    规则故意写得很窄：空行和 # 注释跳过，只认第一个 =，已有环境变量不覆盖。
    """
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        # 允许值被引号包着写，去掉最外层一对引号。
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        # 已存在的环境变量优先：命令行里临时 export 的值不该被 .env 覆盖。
        os.environ.setdefault(key, value)


def main() -> int:
    # 脚本在 scripts/ 下，项目根目录是它的上一级。
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")

    # 延迟到读完 .env 之后再导入 provider：它在构造时就会读环境变量。
    from pop_engine.llm.provider import OpenAIProvider
    from pop_engine.loop import AgentLoop
    from pop_engine.tools import ToolRegistry

    prompt = sys.argv[1] if len(sys.argv) > 1 else "你好，用一句话介绍你自己。"

    provider = OpenAIProvider()
    # 空注册表：第 1 段只走一问一答，模型没有工具可调。
    loop = AgentLoop(provider, ToolRegistry([]), max_steps=3)

    result = loop.run(prompt)

    print("=" * 60)
    print(f"status      : {result.status}")
    print(f"stop_reason : {result.stop_reason}")
    print(f"steps       : {result.steps}")
    print(f"error       : {result.error}")
    print("-" * 60)
    print("final_text:")
    print(result.final_text)
    print("=" * 60)

    # 验收判据是 stop_reason == "end_turn"；失败时返回非零退出码，
    # 方便以后放进 CI 或 shell 判断，而不是靠人眼看输出。
    if result.status != "ok" or result.stop_reason != "end_turn":
        print("验收未通过：期望 stop_reason == 'end_turn'", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
