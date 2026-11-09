"""OpenAI 兼容 chat/completions 客户端（Ark / 百炼千问 / OpenAI 兼容）。"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import requests

from ..errors import PrecheckError
from ..models import ProviderProfile
from ..secrets import redact
from .base import ChatProvider, ChatResponse, ensure_response_text


def masked_endpoint(url: str) -> str:
    """只保留 scheme + host + path，去掉查询串与凭据。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "invalid-url"
    if not parts.scheme or not parts.netloc:
        return "invalid-url"
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class OpenAICompatProvider(ChatProvider):
    name = "openai_compat"

    def __init__(
        self,
        profile: ProviderProfile,
        api_key: str,
        *,
        session: requests.Session | None = None,
    ):
        self.profile = profile
        self.api_key = api_key or ""
        self.session = session
        self._timeout = max(1.0, float(profile.timeout or 60.0))

    # ── URL / 请求 ───────────────────────────────────────
    def chat_url(self) -> str:
        base = (self.profile.base_url or "").strip().rstrip("/")
        if not base:
            raise PrecheckError("PC-102", "未填写 Base URL")
        if not base.lower().startswith(("http://", "https://")):
            raise PrecheckError("PC-102", "Base URL 必须以 http:// 或 https:// 开头")
        if base.lower().endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def models_url(self) -> str:
        base = (self.profile.base_url or "").strip().rstrip("/")
        if base.lower().endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
        return f"{base}/models"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.profile.extra_headers or {})
        return headers

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        requester = self.session or requests
        try:
            response = requester.request(
                method,
                url,
                headers=self._headers(),
                timeout=(min(10.0, self._timeout), self._timeout),
                **kwargs,
            )
        except requests.Timeout as exc:
            raise PrecheckError(
                "PC-105",
                redact(f"请求超时：{exc}", self.api_key),
                retryable=True,
            ) from exc
        except requests.RequestException as exc:
            raise PrecheckError(
                "PC-106",
                redact(f"网络连接失败：{exc}", self.api_key),
                retryable=True,
            ) from exc
        self._raise_for_status(response)
        return response

    def _raise_for_status(self, response: requests.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        retry_after: float | None = None
        raw = response.headers.get("Retry-After") if response.headers else None
        if raw:
            try:
                retry_after = max(0.0, float(raw))
            except ValueError:
                retry_after = None
        if status in (401, 403):
            raise PrecheckError(
                "PC-103",
                "认证失败：API Key 无效、过期或无权限",
                status_code=status,
            )
        if status == 429:
            raise PrecheckError(
                "PC-104",
                "触发限流，请降低并发或稍后重试",
                retryable=True,
                status_code=status,
                retry_after=retry_after,
            )
        if status in (408, 409):
            raise PrecheckError(
                "PC-105",
                f"服务端请求超时/冲突（HTTP {status}）",
                retryable=True,
                status_code=status,
            )
        if 500 <= status < 600:
            raise PrecheckError(
                "PC-107",
                f"模型服务端错误（HTTP {status}）",
                retryable=True,
                status_code=status,
                retry_after=retry_after,
            )
        text = ""
        try:
            text = (response.text or "")[:300]
        except Exception:  # pragma: no cover - 极端响应对象
            text = ""
        raise PrecheckError(
            "PC-118",
            redact(f"请求被拒绝（HTTP {status}）：{text}", self.api_key),
            status_code=status,
        )

    # ── 接口 ─────────────────────────────────────────────
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        if not self.api_key:
            raise PrecheckError("PC-101", "未配置 API Key")
        target_model = (model or self.profile.model or "").strip()
        if not target_model:
            raise PrecheckError("PC-102", "未填写模型名")
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": (
                float(self.profile.temperature)
                if temperature is None
                else float(temperature)
            ),
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)

        url = self.chat_url()
        started = time.perf_counter()
        response = self._request("POST", url, json=payload)
        latency = int((time.perf_counter() - started) * 1000)
        try:
            data = response.json()
        except ValueError as exc:
            raise PrecheckError("PC-108", "响应不是合法 JSON") from exc
        return self._parse_chat(data, target_model, url, latency)

    def _parse_chat(
        self, data: dict[str, Any], model: str, url: str, latency: int
    ) -> ChatResponse:
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise PrecheckError("PC-108", "响应缺少 choices")
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in (None, "text"):
                    parts.append(str(part.get("text") or ""))
                elif isinstance(part, str):
                    parts.append(part)
            content = "".join(parts)
        text = ensure_response_text(str(content or ""))
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(
            usage.get("total_tokens") or prompt_tokens + completion_tokens
        )
        return ChatResponse(
            text=text,
            model=str(data.get("model") or model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            endpoint=masked_endpoint(url),
            latency_ms=latency,
            raw_usage=dict(usage) if isinstance(usage, dict) else {},
        )

    def list_models(self) -> list[str]:
        if not self.api_key:
            raise PrecheckError("PC-101", "未配置 API Key")
        response = self._request("GET", self.models_url())
        try:
            data = response.json()
        except ValueError as exc:
            raise PrecheckError("PC-108", "模型列表响应不是合法 JSON") from exc
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return sorted(
            str(item.get("id"))
            for item in items
            if isinstance(item, dict) and item.get("id")
        )
