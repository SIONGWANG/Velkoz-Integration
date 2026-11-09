"""翻译与预审页面兼容入口。

实际页面位于 `ui/pages/precheck.py`；此模块只做转发，便于按设计文档
`modules/precheck/ui.py` 的路径引用。
"""
from __future__ import annotations

from ui.pages.precheck import render

__all__ = ["render"]
