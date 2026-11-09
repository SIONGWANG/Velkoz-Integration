"""CSV/Excel 只读解析与表结构识别。

不依赖 openpyxl：.xlsx 通过 zipfile + XML 读取首个工作表；.xls 旧格式
明确标记为不支持，不尝试修改或转换源文件。
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from modules.common.path_guard import normalize_path

from .errors import RecordImportError
from .models import (
    MAX_FILE_BYTES,
    TYPE_BACKUP,
    TYPE_EXPORT_CSV,
    TYPE_LEGACY_CSV,
    TYPE_OTHER_STATS,
    TYPE_UNCLASSIFIED,
    ParsedRecordRow,
)

TEXT_ENCODINGS = ("utf-8", "gb18030", "utf-16")
_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "task_id": ("图片ID", "任务ID", "任务id", "图片id", "ID", "task_id", "taskid", "image_id"),
    "operator": ("姓名", "验收员", "操作员", "质检员", "operator", "name", "user_name"),
    "result": ("结果", "状态", "最终结果", "result", "status"),
    "l1": ("一级", "L1", "大类", "一级分类"),
    "l2": ("二级", "L2", "小类", "二级分类"),
    "note": ("备注", "说明", "备注信息", "note", "comment"),
    "tags": ("标签", "错误标签", "tags", "tag"),
    "evidence": ("错误截图", "截图", "证据", "evidence", "screenshot"),
    "record_time": ("质检时间", "验收时间", "时间", "record_time", "time", "date"),
    "path": ("路径", "图片路径", "任务路径", "path", "folder"),
}

STATS_TOKENS = ("统计", "汇总", "合计", "数量", "总数", "合格数", "不合格数", "通过率")
BACKUP_TOKENS = ("备份", "backup", ".bak")
EXPORT_TOKENS = ("导出", "export")


def _normalize_header(text: str) -> str:
    value = (text or "").lstrip("\ufeff").strip().casefold()
    value = value.replace("\u3000", "")
    value = "".join(value.split())
    value = value.replace("（", "(").replace("）", ")").replace("*", "")
    return value


def _alias_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, names in FIELD_ALIASES.items():
        for name in names:
            mapping[_normalize_header(name)] = key
    return mapping


def map_headers(headers: Iterable[str]) -> tuple[dict[int, str], list[str]]:
    mapping = _alias_map()
    positions: dict[int, str] = {}
    unknown: list[str] = []
    for index, header in enumerate(headers):
        key = mapping.get(_normalize_header(header))
        if key:
            positions[index] = key
        elif (header or "").strip():
            unknown.append(header)
    return positions, unknown


def _cell(row: list[str], positions: dict[int, str], key: str) -> str:
    for index, mapped in positions.items():
        if mapped == key:
            return (row[index] if index < len(row) else "").strip()
    return ""


def _decode(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    raise RecordImportError("RI-003")


def _read_csv(path: str) -> tuple[list[str], list[list[str]], str, str]:
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        raise RecordImportError("RI-011", f"文件 {os.path.basename(path)} 为 {size} 字节")
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise RecordImportError("RI-002", f"读取失败：{exc}") from exc
    text, encoding = _decode(raw)
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


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    try:
        root = ET.fromstring(archive.read(name))
    except ET.ParseError as exc:
        raise RecordImportError("RI-006", "sharedStrings 解析失败") from exc
    values: list[str] = []
    for item in root.findall(f"{_XLSX_NS}si"):
        parts = [node.text or "" for node in item.iter(f"{_XLSX_NS}t")]
        values.append("".join(parts))
    return values


def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Za-z]+", ref or "")
    if not letters:
        return 0
    index = 0
    for char in letters.group(0).upper():
        index = index * 26 + (ord(char) - 64)
    return max(0, index - 1)


def _first_sheet_name(archive: zipfile.ZipFile) -> str:
    names = sorted(
        name
        for name in archive.namelist()
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    )
    return names[0] if names else ""


def _read_xlsx(path: str) -> tuple[list[str], list[list[str]], str, str]:
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        raise RecordImportError("RI-011", f"文件 {os.path.basename(path)} 为 {size} 字节")
    try:
        with zipfile.ZipFile(path) as archive:
            shared = _shared_strings(archive)
            sheet_name = _first_sheet_name(archive)
            if not sheet_name:
                raise RecordImportError("RI-006", "找不到工作表")
            try:
                root = ET.fromstring(archive.read(sheet_name))
            except ET.ParseError as exc:
                raise RecordImportError("RI-006", "工作表解析失败") from exc
            rows: list[list[str]] = []
            for row_node in root.iter(f"{_XLSX_NS}row"):
                cells: dict[int, str] = {}
                max_col = -1
                for cell in row_node.findall(f"{_XLSX_NS}c"):
                    column = _column_index(cell.get("r") or "")
                    max_col = max(max_col, column)
                    kind = cell.get("t")
                    if kind == "inlineStr":
                        value = "".join(
                            node.text or "" for node in cell.iter(f"{_XLSX_NS}t")
                        )
                    else:
                        node = cell.find(f"{_XLSX_NS}v")
                        text = node.text if node is not None else ""
                        if kind == "s" and text not in (None, ""):
                            try:
                                value = shared[int(text)]
                            except (ValueError, IndexError):
                                value = ""
                        else:
                            value = text or ""
                    cells[column] = str(value).strip()
                if max_col >= 0:
                    rows.append([cells.get(i, "") for i in range(max_col + 1)])
            if not rows:
                return [], [], "xlsx", ""
            headers = [(cell or "").strip() for cell in rows[0]]
            data = [row for row in rows[1:] if any((cell or "").strip() for cell in row)]
            return headers, data, "xlsx", ""
    except zipfile.BadZipFile as exc:
        raise RecordImportError("RI-006", "文件不是有效的 .xlsx") from exc
    except OSError as exc:
        raise RecordImportError("RI-002", f"读取失败：{exc}") from exc


def read_table(path: str) -> tuple[list[str], list[list[str]], str, str]:
    target = normalize_path(path)
    ext = os.path.splitext(target)[1].lower()
    if ext == ".xls":
        raise RecordImportError("RI-005", "旧版 .xls 暂不支持，请另存为 .xlsx")
    if ext == ".xlsx":
        return _read_xlsx(target)
    if ext in (".csv", ".txt"):
        return _read_csv(target)
    raise RecordImportError("RI-004", f"不支持的扩展名：{ext}")


def classify_table(path: str, headers: list[str]) -> str:
    """按文件名与表头识别类型；不确定时返回 unclassified。"""
    name = os.path.basename(path).casefold()
    normalized_headers = [_normalize_header(item) for item in headers]
    positions, _ = map_headers(headers)
    keys = set(positions.values())

    is_backup = any(token in name for token in BACKUP_TOKENS)
    is_export = any(token in name for token in EXPORT_TOKENS)
    has_record = "task_id" in keys or "note" in keys or "tags" in keys
    strict_record = "task_id" in keys and bool(
        keys & {"result", "tags", "note", "operator"}
    )
    has_stats = any(
        token in header
        for header in normalized_headers
        for token in STATS_TOKENS
    )

    if strict_record:
        if is_backup:
            return TYPE_BACKUP
        if is_export:
            return TYPE_EXPORT_CSV
        return TYPE_LEGACY_CSV
    if "task_id" in keys:
        return TYPE_BACKUP if is_backup else TYPE_LEGACY_CSV
    if has_stats:
        # 备份优先于统计：两者都不导入，但备份表更贴近真实来源。
        return TYPE_BACKUP if is_backup else TYPE_OTHER_STATS
    if has_record:
        return TYPE_BACKUP if is_backup else TYPE_LEGACY_CSV
    return TYPE_UNCLASSIFIED


def inspect_table(path: str) -> dict[str, Any]:
    """只读检查一个文件；返回类型、表头、编码与行数。"""
    headers, rows, encoding, delimiter = read_table(path)
    source_type = classify_table(path, headers)
    positions, unknown = map_headers(headers)
    operator = ""
    for row in rows:
        value = _cell(row, positions, "operator")
        if value:
            operator = value
            break
    return {
        "source_type": source_type,
        "headers": headers,
        "rows": rows,
        "encoding": encoding,
        "delimiter": delimiter,
        "unknown_columns": unknown,
        "operator": operator,
        "rows_total": len(rows),
    }


def _normalize_time(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    if 20000 <= number <= 80000:
        try:
            moment = _dt.datetime(1899, 12, 30) + _dt.timedelta(days=number)
            return moment.strftime("%Y-%m-%d %H:%M:%S")
        except (OverflowError, ValueError):
            return text
    return text


def row_fingerprint(source_path: str, values: list[str]) -> str:
    payload = json.dumps(
        [os.path.normcase(normalize_path(source_path)), *values],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_rows(
    source_path: str, headers: list[str], rows: list[list[str]]
) -> tuple[list[ParsedRecordRow], list[str]]:
    """解析记录行；保留标签/备注/路径原文，不要求本机标签存在。"""
    positions, unknown = map_headers(headers)
    keys = set(positions.values())
    if not keys & {"task_id", "note", "tags"}:
        raise RecordImportError("RI-004", "缺少图片ID、备注或标签列")
    parsed: list[ParsedRecordRow] = []
    errors: list[str] = []
    for index, row in enumerate(rows, start=2):
        try:
            values = [
                _cell(row, positions, "task_id"),
                _cell(row, positions, "operator"),
                _cell(row, positions, "result"),
                _cell(row, positions, "l1"),
                _cell(row, positions, "l2"),
                _cell(row, positions, "note"),
                _cell(row, positions, "tags"),
                _cell(row, positions, "evidence"),
                _cell(row, positions, "record_time"),
                _cell(row, positions, "path"),
            ]
            if not any(values):
                continue
            record = ParsedRecordRow(
                source_row=index,
                task_id=values[0],
                operator=values[1],
                result=values[2],
                l1=values[3],
                l2=values[4],
                note=values[5],
                tags_raw=values[6],
                evidence_raw=values[7],
                record_time=_normalize_time(values[8]),
                path_raw=values[9],
                fingerprint=row_fingerprint(source_path, values),
            )
            parsed.append(record)
        except Exception as exc:  # 单行失败不影响其他行
            errors.append(f"第 {index} 行：{type(exc).__name__}")
    return parsed, errors


def file_sha256(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise RecordImportError("RI-002", f"读取失败：{exc}") from exc
    return digest.hexdigest()
