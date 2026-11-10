"""骨架自检：包能导入、版本能读到。真正的 loop 测试在 M1 里写。"""

from pop_engine import __version__


def test_package_imports() -> None:
    assert __version__ == "0.1.0"
