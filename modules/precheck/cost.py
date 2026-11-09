"""Token / 费用计算与用量汇总。"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .models import ProviderProfile, TranslationResult

_NUMBER_CLEAN = re.compile(r"[,\s¥￥$€%]")


def parse_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = _NUMBER_CLEAN.sub("", str(value)).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: Any, default: int = 0) -> int:
    number = parse_float(value, default=float(default))
    return int(number or 0)


def compute_cost(
    profile: ProviderProfile, token_input: int, token_output: int
) -> float:
    unit = max(1, int(profile.unit_tokens or 1_000_000))
    total = (
        max(0, int(token_input)) / unit * float(profile.input_price or 0)
        + max(0, int(token_output)) / unit * float(profile.output_price or 0)
    )
    return round(total, 8)


def currency_label(currency: str) -> str:
    return {"CNY": "元", "USD": "美元", "EUR": "欧元"}.get(
        (currency or "").upper(), currency or ""
    )


def format_cost(value: float, currency: str = "CNY") -> str:
    return f"{float(value or 0):.4f} {currency_label(currency)}"


def summarize_results(
    results: Iterable[TranslationResult],
) -> dict[str, Any]:
    rows = list(results)
    token_in = sum(item.token_input for item in rows)
    token_out = sum(item.token_output for item in rows)
    return {
        "total": len(rows),
        "translated": sum(1 for item in rows if item.status == "translated"),
        "failed": sum(1 for item in rows if item.status == "failed"),
        "skipped": sum(1 for item in rows if item.status == "skipped"),
        "token_input": token_in,
        "token_output": token_out,
        "token_total": token_in + token_out,
        "cost": round(sum(item.cost for item in rows), 8),
    }


def usage_row(
    job_id: str,
    *,
    profile: ProviderProfile,
    results: Iterable[TranslationResult],
    endpoint: str = "",
    source_report: str = "",
    note: str = "",
    row_hash: str = "",
    usage_date: str = "",
    usage_time: str = "",
) -> dict[str, Any]:
    summary = summarize_results(results)
    return {
        "job_id": job_id,
        "usage_date": usage_date,
        "usage_time": usage_time,
        "api_type": profile.provider,
        "endpoint": endpoint,
        "total": summary["total"],
        "success": summary["translated"],
        "failed": summary["failed"],
        "token_in": summary["token_input"],
        "token_out": summary["token_output"],
        "token_total": summary["token_total"],
        "cost": summary["cost"],
        "currency": profile.currency or "CNY",
        "note": note,
        "source_report": source_report,
        "row_hash": row_hash or None,
    }
