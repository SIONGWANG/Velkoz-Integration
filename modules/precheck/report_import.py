"""外部历史报告只读导入（translation_report_*.csv / cost_report.csv）。

- 只读取用户选择的 CSV，不修改外部工具目录与文件；
- 表头按别名映射，未知列忽略，未知状态保留；
- 行哈希去重，重复导入幂等。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .cost import parse_float, parse_int
from .errors import PrecheckError
from .safety import decode_bytes, text_length, word_count

_MAX_BYTES = 20 * 1024 * 1024

TRANSLATION_ALIASES: dict[str, tuple[str, ...]] = {
    "folder": ("所在文件夹", "任务文件夹", "文件夹", "路径", "folder", "path"),
    "task_id": ("任务ID", "任务id", "id", "taskid", "task_id"),
    "status": ("状态", "status"),
    "zh_text": ("中文指令", "中文", "zh", "chinese"),
    "en_text": ("英文指令", "英文", "en", "english"),
    "tokens": ("Token消耗", "Token消耗(总计)", "token消耗", "tokens", "token"),
    "cost": ("预估费用(元)", "预估费用", "费用", "cost"),
    "prompt": ("翻译提示词", "提示词", "prompt"),
    "error": ("错误提示", "错误信息", "error", "message"),
}

COST_ALIASES: dict[str, tuple[str, ...]] = {
    "usage_date": ("日期", "date"),
    "usage_time": ("时间", "time"),
    "api_type": ("API类型", "api类型", "api_type", "provider", "api"),
    "endpoint": ("endpoint", "端点"),
    "total": ("总任务数", "总数", "total"),
    "success": ("成功数", "成功", "success"),
    "failed": ("失败数", "失败", "failed"),
    "token_in": ("Token消耗(输入)", "输入Token", "token_in"),
    "token_out": ("Token消耗(输出)", "输出Token", "token_out"),
    "token_total": ("Token消耗(总计)", "Token消耗", "token_total"),
    "cost": ("预估费用(元)", "预估费用", "cost"),
    "note": ("备注", "note"),
}

STATUS_MAP = {
    "成功": "translated",
    "已完成": "translated",
    "已翻译": "translated",
    "完成": "translated",
    "ok": "translated",
    "success": "translated",
    "translated": "translated",
    "失败": "failed",
    "错误": "failed",
    "error": "failed",
    "failed": "failed",
    "跳过": "skipped",
    "已跳过": "skipped",
    "skipped": "skipped",
    "待处理": "pending",
    "待翻译": "pending",
    "pending": "pending",
}


@dataclass
class ImportResult:
    kind: str
    path: str
    encoding: str = ""
    delimiter: str = ","
    headers: list[str] = field(default_factory=list)
    rows_total: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)
    usages: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    usage_row: dict[str, Any] | None = None

    @property
    def rows_ok(self) -> int:
        return self.rows_total - len(self.errors)

    def summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "rows_total": self.rows_total,
            "rows_ok": self.rows_ok,
            "rows_error": len(self.errors),
            "items": len(self.items),
            "usages": len(self.usages),
            "encoding": self.encoding,
            "unknown_columns": list(self.unknown_columns),
            "errors": list(self.errors[:50]),
        }


def _normalize_header(text: str) -> str:
    value = (text or "").lstrip("\ufeff").strip().casefold()
    value = value.replace("\u3000", "")
    value = "".join(value.split())
    value = value.replace("（", "(").replace("）", ")").replace("*", "")
    return value


def _alias_map(aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, names in aliases.items():
        for name in names:
            mapping[_normalize_header(name)] = key
    return mapping


def map_headers(
    headers: list[str], aliases: dict[str, tuple[str, ...]]
) -> tuple[dict[int, str], list[str]]:
    mapping = _alias_map(aliases)
    positions: dict[int, str] = {}
    unknown: list[str] = []
    for index, header in enumerate(headers):
        key = mapping.get(_normalize_header(header))
        if key:
            positions[index] = key
        elif header.strip():
            unknown.append(header)
    return positions, unknown


def read_table(path: str) -> tuple[list[str], list[list[str]], str, str]:
    target = Path(path)
    if not target.is_file():
        raise PrecheckError("PC-401", f"文件不存在：{target}")
    size = target.stat().st_size
    if size > _MAX_BYTES:
        raise PrecheckError("PC-401", f"文件过大（{size} 字节），拒绝导入")
    raw = target.read_bytes()
    text, encoding = decode_bytes(raw)
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return [], [], encoding, delimiter
    headers = [(cell or "").strip() for cell in rows[0]]
    data = [row for row in rows[1:] if any((cell or "").strip() for cell in row)]
    return headers, data, encoding, delimiter


def detect_kind(path: str, headers: list[str]) -> str:
    name = os.path.basename(path).casefold()
    if "cost" in name:
        return "cost"
    if "translation" in name or "翻译" in name:
        return "translation"
    translation_positions, _ = map_headers(headers, TRANSLATION_ALIASES)
    cost_positions, _ = map_headers(headers, COST_ALIASES)
    keys = set(translation_positions.values())
    if "task_id" in keys and keys & {"folder", "zh_text", "en_text"}:
        return "translation"
    cost_keys = set(cost_positions.values())
    if cost_keys & {"total", "token_in", "token_total"} and cost_keys & {
        "usage_date",
        "api_type",
    }:
        return "cost"
    return "unknown"


def normalize_status(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "unknown"
    return STATUS_MAP.get(text.casefold(), STATUS_MAP.get(text, f"unknown:{text[:40]}"))


def _row_hash(kind: str, values: list[str]) -> str:
    payload = json.dumps([kind, *values], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_hash(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _cell(row: list[str], positions: dict[int, str], key: str) -> str:
    for index, mapped in positions.items():
        if mapped == key:
            return (row[index] if index < len(row) else "").strip()
    return ""


def build_import(path: str, kind: str | None = None) -> ImportResult:
    headers, rows, encoding, delimiter = read_table(path)
    if not headers:
        raise PrecheckError("PC-401", "CSV 为空或缺少表头")
    resolved = kind or detect_kind(path, headers)
    if resolved == "unknown":
        raise PrecheckError(
            "PC-401", "无法识别报告类型；请确认是 translation_report 或 cost_report"
        )
    if resolved == "translation":
        return _build_translation(path, headers, rows, encoding, delimiter)
    return _build_cost(path, headers, rows, encoding, delimiter)


def _build_translation(
    path: str,
    headers: list[str],
    rows: list[list[str]],
    encoding: str,
    delimiter: str,
) -> ImportResult:
    positions, unknown = map_headers(headers, TRANSLATION_ALIASES)
    needed = {"task_id", "folder"}
    if not needed & set(positions.values()):
        raise PrecheckError("PC-401", "缺少“任务ID / 所在文件夹”列")
    result = ImportResult(
        kind="translation",
        path=path,
        encoding=encoding,
        delimiter=delimiter,
        headers=headers,
        rows_total=len(rows),
        unknown_columns=unknown,
    )
    token_sum = 0
    cost_sum = 0.0
    translated = 0
    failed = 0
    fallback_date = datetime.now().strftime("%Y-%m-%d")
    fallback_time = datetime.now().strftime("%H:%M:%S")
    for number, row in enumerate(rows, start=2):
        try:
            folder = _cell(row, positions, "folder")
            task_id = _cell(row, positions, "task_id") or (
                os.path.basename(folder.rstrip("\\/")) if folder else ""
            )
            zh = _cell(row, positions, "zh_text")
            en = _cell(row, positions, "en_text")
            raw_status = _cell(row, positions, "status")
            tokens = parse_int(_cell(row, positions, "tokens"), 0)
            cost = parse_float(_cell(row, positions, "cost"), 0.0)
            prompt = _cell(row, positions, "prompt")
            error = _cell(row, positions, "error")
            if cost is None:
                raise ValueError(f"费用无法解析：{_cell(row, positions, 'cost')}")
            status = normalize_status(raw_status)
            if status == "translated":
                translated += 1
            elif status == "failed":
                failed += 1
            token_sum += tokens
            cost_sum += cost
            row_hash = _row_hash(
                "translation",
                [folder, task_id, raw_status, zh, en, str(tokens), str(cost), prompt, error],
            )
            result.items.append(
                {
                    "task_id": task_id or f"row{number}",
                    "folder_path": folder,
                    "zh_txt_path": "",
                    "en_txt_path": "",
                    "status": status,
                    "zh_length": text_length(zh),
                    "en_length": word_count(en),
                    "model": "",
                    "translated_at": "",
                    "grammar_checked": 0,
                    "grammar_passed": 0,
                    "consistency_checked": 0,
                    "consistency_passed": 0,
                    "suggestion": prompt,
                    "manual_fixed": 0,
                    "error_code": "PC-402" if error else "",
                    "error_message": error,
                    # 外部报告只有总 Token，暂记在 token_input，报告中求和仍正确
                    "token_input": tokens,
                    "token_output": 0,
                    "cost": cost,
                    "report_row_hash": row_hash,
                    "written_at": "",
                    "backup_path": "",
                    "zh_text": zh,
                    "en_text": en,
                }
            )
        except Exception as exc:
            result.errors.append(f"第 {number} 行：{exc}")

    result.usage_row = {
        "job_id": None,
        "usage_date": fallback_date,
        "usage_time": fallback_time,
        "api_type": "external_report",
        "endpoint": "",
        "total": len(result.items),
        "success": translated,
        "failed": failed,
        "token_in": token_sum,
        "token_out": 0,
        "token_total": token_sum,
        "cost": round(cost_sum, 8),
        "currency": "CNY",
        "note": f"导入外部翻译报告：{os.path.basename(path)}",
        "source_report": path,
        "row_hash": _row_hash("usage-translation", [_file_hash(path)]),
    }
    return result


def _build_cost(
    path: str,
    headers: list[str],
    rows: list[list[str]],
    encoding: str,
    delimiter: str,
) -> ImportResult:
    positions, unknown = map_headers(headers, COST_ALIASES)
    if not {"total", "token_total", "cost"} & set(positions.values()):
        raise PrecheckError("PC-401", "缺少“总任务数 / Token消耗 / 费用”列")
    result = ImportResult(
        kind="cost",
        path=path,
        encoding=encoding,
        delimiter=delimiter,
        headers=headers,
        rows_total=len(rows),
        unknown_columns=unknown,
    )
    for number, row in enumerate(rows, start=2):
        try:
            values = [str(_cell(row, positions, key)) for key in COST_ALIASES]
            token_in = parse_int(_cell(row, positions, "token_in"), 0)
            token_out = parse_int(_cell(row, positions, "token_out"), 0)
            token_total = parse_int(
                _cell(row, positions, "token_total"), token_in + token_out
            )
            cost = parse_float(_cell(row, positions, "cost"), 0.0)
            if cost is None:
                raise ValueError(f"费用无法解析：{_cell(row, positions, 'cost')}")
            result.usages.append(
                {
                    "job_id": None,
                    "usage_date": _cell(row, positions, "usage_date"),
                    "usage_time": _cell(row, positions, "usage_time"),
                    "api_type": _cell(row, positions, "api_type") or "external_report",
                    "endpoint": _cell(row, positions, "endpoint"),
                    "total": parse_int(_cell(row, positions, "total"), 0),
                    "success": parse_int(_cell(row, positions, "success"), 0),
                    "failed": parse_int(_cell(row, positions, "failed"), 0),
                    "token_in": token_in,
                    "token_out": token_out,
                    "token_total": token_total,
                    "cost": cost,
                    "currency": "CNY",
                    "note": _cell(row, positions, "note"),
                    "source_report": path,
                    "row_hash": _row_hash("cost", values),
                }
            )
        except Exception as exc:
            result.errors.append(f"第 {number} 行：{exc}")
    return result
