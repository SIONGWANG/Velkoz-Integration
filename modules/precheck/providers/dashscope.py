"""阿里云百炼 / 千问（OpenAI 兼容模式）。"""
from __future__ import annotations

from .openai_compat import OpenAICompatProvider


class DashScopeProvider(OpenAICompatProvider):
    """百炼兼容模式同样使用 /chat/completions。"""

    name = "dashscope"


__all__ = ["DashScopeProvider"]
