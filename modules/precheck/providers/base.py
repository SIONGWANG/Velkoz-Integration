"""模型提供方抽象。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..errors import PrecheckError


@dataclass
class ChatResponse:
    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    endpoint: str = ""
    latency_ms: int = 0
    raw_usage: dict[str, Any] = field(default_factory=dict)


class ChatProvider:
    """统一 chat 接口；实现方负责错误码映射与密钥脱敏。"""

    name = "base"

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        raise NotImplementedError

    def test_connection(self) -> ChatResponse:
        return self.chat(
            [{"role": "user", "content": "连接测试，请只回复 OK"}],
            temperature=0.0,
            max_tokens=8,
        )

    def list_models(self) -> list[str]:
        return []


def timed(callable_obj) -> tuple[Any, int]:
    started = time.perf_counter()
    value = callable_obj()
    return value, int((time.perf_counter() - started) * 1000)


def ensure_response_text(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        raise PrecheckError("PC-109", "模型返回内容为空")
    return cleaned
