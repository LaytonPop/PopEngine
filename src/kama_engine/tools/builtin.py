from __future__ import annotations

from pathlib import Path
from typing import Any

from kama_engine.types import ToolSpec

# 读文件最多返回这么多行；超过就截断并在末尾注明。
# 选 200 而不是 1000：上下文不是无限的，一个超大文件塞进去会挤掉关键信息。
MAX_READ_LINES = 200


class _BuiltinTool:
    """两个工具共用的"安全路径"逻辑，避免复制一遍。

    子类只需要提供 spec 和 run；路径校验在这里统一做。
    """

    spec: ToolSpec

    def __init__(self, root: str | Path) -> None:
        # root 存成 resolve 之后的绝对路径：外部传相对路径也能用，
        # 而且在构造时就把它固定下来，之后的比较都基于同一个基准。
        self._root = Path(root).resolve()

    def _safe_path(self, path: str) -> Path:
        """把模型给的相对路径解析成绝对路径，并检查没跑出 root。

        越界或非法输入会抛 ValueError —— 由子类的 run() 决定怎么处理。
        """
        if not isinstance(path, str):
            raise ValueError("path 必须是字符串")

        candidate = Path(path)

        # 绝对路径直接拒绝：模型不该给我们任何本机绝对路径。
        # （用 resolve 后 is_relative_to 也能挡住，但提前拒绝错误信息更清楚。）
        if candidate.is_absolute():
            raise ValueError(f"不允许绝对路径：{path}")

        resolved = (self._root / candidate).resolve()

        # is_relative_to：resolved 是否落在 root 之下。
        # 注意要比较 self._root 本身也已经是 resolved 的。
        if not resolved.is_relative_to(self._root):
            raise ValueError(f"路径越界：{path}")

        return resolved


class ListDirTool(_BuiltinTool):
    """列出目录下一层的条目（不递归）。"""

    spec = ToolSpec(
        name="list_dir",
        description=(
            "列出指定目录下一层的文件和子目录名，不递归。"
            'path 是相对于仓库根目录的路径；仓库根目录用 "." 表示。'
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": '相对于仓库根目录的目录路径，例如 "." 或 "src"',
                }
            },
            "required": ["path"],
        },
    )

    def run(self, **kwargs: Any) -> str:
        path = kwargs.get("path", ".")

        try:
            target = self._safe_path(path)
        except ValueError as exc:
            # 越界是预期内的用户错误：抛出去会被 registry 翻译成 is_error=True。
            # 错误文案里带上原因，模型才知道怎么改。
            raise ValueError(f"list_dir 失败：{exc}") from None

        if not target.exists():
            raise ValueError(f"list_dir 失败：路径不存在：{path}")
        if not target.is_dir():
            raise ValueError(f"list_dir 失败：不是目录：{path}")

        try:
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except OSError as exc:
            # 权限等问题也归为"工具执行失败"，交给 registry 收口。
            raise OSError(f"list_dir 失败：{exc}") from None

        if not entries:
            return "（空目录）"

        # 子目录加 "/" 后缀，让模型一眼分清目录和文件。
        lines = [entry.name + "/" if entry.is_dir() else entry.name for entry in entries]
        return "\n".join(lines)


class ReadFileTool(_BuiltinTool):
    """读一个文本文件；超过 MAX_READ_LINES 行就截断并注明。"""

    spec = ToolSpec(
        name="read_file",
        description=(
            f"读取指定文本文件的内容；超过 {MAX_READ_LINES} 行会被截断并在末尾注明。"
            "path 是相对于仓库根目录的文件路径。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": '相对于仓库根目录的文件路径，例如 "README.md"',
                }
            },
            "required": ["path"],
        },
    )

    def run(self, **kwargs: Any) -> str:
        path = kwargs.get("path")
        if path is None:
            raise ValueError("read_file 失败：缺少参数 path")

        try:
            target = self._safe_path(path)
        except ValueError as exc:
            raise ValueError(f"read_file 失败：{exc}") from None

        if not target.exists():
            raise ValueError(f"read_file 失败：文件不存在：{path}")
        if not target.is_file():
            raise ValueError(f"read_file 失败：不是文件：{path}")

        try:
            # errors="replace"：遇到非 UTF-8 字节不崩，替换成占位符，
            # 这比"读不了"更有用 —— 模型至少能看到内容。
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            raise OSError(f"read_file 失败：{exc}") from None

        total = len(lines)

        if total <= MAX_READ_LINES:
            return "\n".join(lines)

        # 截断必须明说：模型若以为这是完整内容，会基于残片下结论。
        kept = lines[:MAX_READ_LINES]
        return "\n".join(kept) + f"\n\n[已截断，共 {total} 行，仅显示前 {MAX_READ_LINES} 行]"
