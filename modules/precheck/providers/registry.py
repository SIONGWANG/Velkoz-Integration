"""模型提供方预设与构建。"""
from __future__ import annotations

from ..models import ProviderProfile
from .ark import ArkProvider
from .base import ChatProvider, ChatResponse
from .dashscope import DashScopeProvider
from .openai_compat import OpenAICompatProvider, masked_endpoint

PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "ark": {
        "label": "豆包 Ark（OpenAI 兼容）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model_hint": "填写 Ark 推理接入点 ID 或模型 ID",
    },
    "dashscope": {
        "label": "阿里云百炼 / 千问（兼容模式）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_hint": "例如 qwen-plus",
    },
    "openai_compat": {
        "label": "OpenAI 兼容（自定义 Base URL）",
        "base_url": "https://api.openai.com/v1",
        "model_hint": "例如 gpt-4o-mini，或自建服务模型名",
    },
}

PROVIDER_LABELS = {key: value["label"] for key, value in PROVIDER_PRESETS.items()}


def default_profiles() -> list[ProviderProfile]:
    return [
        ProviderProfile(
            id=key,
            name=value["label"],
            provider=key,
            base_url=value["base_url"],
            model="",
            builtin=True,
            note=value["model_hint"],
        )
        for key, value in PROVIDER_PRESETS.items()
    ]


def validate_profile(profile: ProviderProfile) -> list[str]:
    errors: list[str] = []
    if not profile.name.strip():
        errors.append("配置名称不能为空")
    if profile.provider not in PROVIDER_PRESETS:
        errors.append(f"未知提供方：{profile.provider}")
    base = (profile.base_url or "").strip()
    if not base:
        errors.append("Base URL 不能为空")
    elif not base.lower().startswith(("http://", "https://")):
        errors.append("Base URL 必须以 http:// 或 https:// 开头")
    if not (profile.model or "").strip():
        errors.append("模型名不能为空")
    if profile.timeout <= 0:
        errors.append("超时时间必须大于 0")
    if not 0 <= profile.max_retries <= 10:
        errors.append("重试次数应在 0–10 之间")
    if not 1 <= profile.concurrency <= 32:
        errors.append("并发数应在 1–32 之间")
    if profile.temperature < 0 or profile.temperature > 2:
        errors.append("temperature 应在 0–2 之间")
    if profile.input_price < 0 or profile.output_price < 0:
        errors.append("单价不能为负数")
    if profile.unit_tokens <= 0:
        errors.append("计价单位 Token 数必须大于 0")
    return errors


def build_provider(
    profile: ProviderProfile, api_key: str
) -> ChatProvider:
    """三类提供方都使用 OpenAI 兼容协议，仅 Base URL / 模型不同。"""
    if profile.provider == "ark":
        return ArkProvider(profile, api_key)
    if profile.provider == "dashscope":
        return DashScopeProvider(profile, api_key)
    if profile.provider == "openai_compat":
        return OpenAICompatProvider(profile, api_key)
    raise ValueError(f"未知提供方：{profile.provider}")


__all__ = [
    "PROVIDER_PRESETS",
    "PROVIDER_LABELS",
    "ChatProvider",
    "ChatResponse",
    "OpenAICompatProvider",
    "masked_endpoint",
    "default_profiles",
    "validate_profile",
    "build_provider",
]
