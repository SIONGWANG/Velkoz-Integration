"""时间工具：统一 ISO 8601（带本地时区偏移）。"""
from __future__ import annotations

import datetime as _dt
import re

_DATE_RE = re.compile(r"^(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])$")


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today_str() -> str:
    return _dt.date.today().isoformat()


def parse_date(value: str) -> str | None:
    """解析 2026-11-07 / 2026.11.07 / 20261107，返回 ISO 日期或 None。"""
    if not value:
        return None
    m = _DATE_RE.fullmatch(value.strip())
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def duration_ms(started: _dt.datetime, finished: _dt.datetime | None = None) -> int:
    end = finished or _dt.datetime.now()
    return max(0, int((end - started).total_seconds() * 1000))


def format_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms} 毫秒"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    minutes = int(seconds // 60)
    return f"{minutes} 分 {int(seconds % 60)} 秒"
