"""豆包 Ark（OpenAI 兼容 chat/completions）。"""
from __future__ import annotations

from .openai_compat import OpenAICompatProvider


class ArkProvider(OpenAICompatProvider):
    """Ark 使用 OpenAI 兼容协议；/api/v3 由配置的 Base URL 决定。"""

    name = "ark"


__all__ = ["ArkProvider"]
