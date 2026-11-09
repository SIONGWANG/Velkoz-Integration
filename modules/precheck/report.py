"""翻译与预审报告：Excel（xlsxwriter）与 CSV。

报告只写入管理数据目录或用户指定且不在源目录内的目录。
"""
from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime
from typing import Any, Iterable

from modules.common.path_guard import is_relative_to, normalize_path

from .cost import currency_label

_INVALID_CELL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MAX_CELL = 32000

ITEM_STATUS_LABELS = {
    "translated": "成功",
    "failed": "失败",
    "skipped": "跳过",
    "pending": "待处理",
    "no_zh": "缺少中文",
    "missing_en": "缺少英文",
    "empty_en": "英文为空",
    "empty_zh": "中文为空",
    "invalid": "读取失败",
}

TRANSLATION_HEADERS = [
    "所在文件夹",
    "任务ID",
    "状态",
    "中文指令",
    "英文指令",
    "Token消耗",
    "预估费用(元)",
    "翻译提示词",
]

COST_HEADERS = [
    "日期",
    "时间",
    "API类型",
    "endpoint",
    "总任务数",
    "成功数",
    "失败数",
    "Token消耗(输入)",
    "Token消耗(输出)",
    "Token消耗(总计)",
    "预估费用(元)",
    "备注",
]

CHECK_HEADERS = [
    "所在文件夹",
    "任务ID",
    "检查类型",
    "结果",
    "问题",
    "建议",
    "Token消耗",
    "预估费用(元)",
]


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return value
    text = _INVALID_CELL.sub("", str(value))
    if len(text) > _MAX_CELL:
        text = text[:_MAX_CELL] + "…（已截断）"
    return text


def _stamp(started_at: str | None) -> str:
    try:
        return datetime.fromisoformat(started_at or "").strftime("%Y%m%d_%H%M%S")
    except (TypeError, ValueError):
        return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_name(value: str, fallback: str = "precheck") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "").strip(" .")
    return cleaned[:60] or fallback


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    index = 2
    while True:
        candidate = f"{stem}_{index}{ext}"
        if not os.path.exists(candidate):
            return candidate
        index += 1


def guard_report_dir(report_dir: str, source_root: str) -> str:
    if not report_dir:
        raise ValueError("未指定报告输出目录")
    target = normalize_path(report_dir)
    root = normalize_path(source_root) if source_root else ""
    if root and (
        os.path.normcase(target) == os.path.normcase(root)
        or is_relative_to(target, root)
    ):
        raise ValueError(f"报告目录不能位于检查源目录内：{target}")
    return target


def guard_report_file(path: str, source_root: str) -> str:
    target = normalize_path(path)
    root = normalize_path(source_root) if source_root else ""
    if root and (
        os.path.normcase(target) == os.path.normcase(root)
        or is_relative_to(target, root)
    ):
        raise ValueError(f"报告文件不能写入检查源目录：{target}")
    return target


def status_label(value: str) -> str:
    text = str(value or "")
    if text.startswith("unknown:"):
        return f"未知（{text.split(':', 1)[1]}）"
    return ITEM_STATUS_LABELS.get(text, text)


def write_status_label(item: dict[str, Any]) -> str:
    if item.get("written_at"):
        return "已写入"
    if item.get("status") == "translated":
        return "已翻译未写入"
    return ""


def _translation_rows(
    job: dict[str, Any], items: Iterable[dict[str, Any]]
) -> list[list[Any]]:
    prompt = job.get("prompt_version") or ""
    rows: list[list[Any]] = []
    for item in items:
        rows.append(
            [
                item.get("folder_path", ""),
                item.get("task_id", ""),
                status_label(item.get("status", "")),
                item.get("zh_text", ""),
                item.get("en_text") or item.get("english", ""),
                int(item.get("token_input") or 0) + int(item.get("token_output") or 0),
                round(float(item.get("cost") or 0), 6),
                prompt,
            ]
        )
    return rows


def _cost_rows(usage_rows: Iterable[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for row in usage_rows:
        rows.append(
            [
                row.get("usage_date", ""),
                row.get("usage_time", ""),
                row.get("api_type", ""),
                row.get("endpoint", ""),
                int(row.get("total") or 0),
                int(row.get("success") or 0),
                int(row.get("failed") or 0),
                int(row.get("token_in") or 0),
                int(row.get("token_out") or 0),
                int(row.get("token_total") or 0),
                round(float(row.get("cost") or 0), 6),
                row.get("note", ""),
            ]
        )
    return rows


def _check_rows(items: Iterable[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in items:
        if item.get("grammar_checked"):
            rows.append(
                [
                    item.get("folder_path", ""),
                    item.get("task_id", ""),
                    "语法",
                    "通过" if item.get("grammar_passed") else "未通过",
                    "",
                    item.get("suggestion", ""),
                    int(item.get("token_input") or 0) + int(item.get("token_output") or 0),
                    round(float(item.get("cost") or 0), 6),
                ]
            )
        if item.get("consistency_checked"):
            rows.append(
                [
                    item.get("folder_path", ""),
                    item.get("task_id", ""),
                    "一致性",
                    "通过" if item.get("consistency_passed") else "未通过",
                    "",
                    item.get("suggestion", ""),
                    int(item.get("token_input") or 0) + int(item.get("token_output") or 0),
                    round(float(item.get("cost") or 0), 6),
                ]
            )
    return rows


def write_csv_files(
    job: dict[str, Any],
    items: list[dict[str, Any]],
    out_dir: str,
    *,
    source_root: str = "",
    usage_rows: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    directory = guard_report_dir(out_dir, source_root)
    os.makedirs(directory, exist_ok=True)
    stamp = _stamp(job.get("started_at"))
    paths: dict[str, str] = {}

    translation_path = unique_path(
        os.path.join(directory, f"translation_report_{stamp}.csv")
    )
    _write_csv(translation_path, TRANSLATION_HEADERS, _translation_rows(job, items))
    paths["translation_csv"] = translation_path

    checks = _check_rows(items)
    if checks:
        checks_path = unique_path(
            os.path.join(directory, f"translation_checks_{stamp}.csv")
        )
        _write_csv(checks_path, CHECK_HEADERS, checks)
        paths["checks_csv"] = checks_path

    if usage_rows:
        cost_path = unique_path(os.path.join(directory, f"cost_report_{stamp}.csv"))
        _write_csv(cost_path, COST_HEADERS, _cost_rows(usage_rows))
        paths["cost_csv"] = cost_path

    return paths


def _write_csv(path: str, headers: list[str], rows: list[list[Any]]) -> str:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([_cell(value) for value in row])
    return path


def write_excel(
    job: dict[str, Any],
    items: list[dict[str, Any]],
    out_path: str,
    *,
    source_root: str = "",
    usage_rows: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
) -> str:
    import xlsxwriter

    path = guard_report_file(out_path, source_root)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    workbook = xlsxwriter.Workbook(
        path, {"strings_to_formulas": False, "strings_to_urls": False}
    )
    title_fmt = workbook.add_format({"bold": True, "font_size": 14})
    head_fmt = workbook.add_format(
        {"bold": True, "bg_color": "#DDEBF7", "border": 1, "align": "center"}
    )
    wrap_fmt = workbook.add_format({"text_wrap": True, "valign": "top"})
    key_fmt = workbook.add_format({"bold": True, "valign": "top"})
    warn_fmt = workbook.add_format({"font_color": "#9C0006", "bold": True})

    summary = summary or {}
    usage_rows = usage_rows or []

    sheet = workbook.add_worksheet("总览")
    sheet.set_column(0, 0, 22)
    sheet.set_column(1, 1, 110)
    sheet.write(0, 0, "翻译与预审报告", title_fmt)
    overview = [
        ("任务ID", job.get("id", "")),
        ("根目录", job.get("root_path", "")),
        ("提供方", job.get("provider", "")),
        ("模型", job.get("model", "")),
        ("提示词版本", job.get("prompt_version", "")),
        ("状态", job.get("status", "")),
        ("操作员", job.get("operator", "")),
        ("开始时间", job.get("started_at", "")),
        ("结束时间", job.get("finished_at", "")),
        ("条目数", summary.get("total", len(items))),
        ("翻译成功", summary.get("translated", 0)),
        ("失败", summary.get("failed", 0)),
        ("跳过", summary.get("skipped", 0)),
        ("Token 输入", summary.get("token_input", 0)),
        ("Token 输出", summary.get("token_output", 0)),
        ("预估费用", summary.get("cost", 0)),
        ("外部报告", job.get("source_report") or ""),
        ("备注", job.get("note") or ""),
        (
            "说明",
            "翻译结果只有在用户确认后才写入英文 TXT；写入前自动备份，"
            "失败不覆盖；语法/一致性检查只出建议。",
        ),
    ]
    row = 2
    for key, value in overview:
        sheet.write(row, 0, key, key_fmt)
        sheet.write(row, 1, _cell(value), wrap_fmt)
        row += 1

    sheet = workbook.add_worksheet("翻译明细")
    headers = [
        "所在文件夹",
        "任务ID",
        "状态",
        "写入状态",
        "中文长度",
        "英文长度",
        "Token输入",
        "Token输出",
        "费用",
        "错误码",
        "错误信息",
        "写入时间",
        "备份路径",
        "中文指令",
        "英文指令",
    ]
    for column, header in enumerate(headers):
        sheet.write(0, column, header, head_fmt)
    for index, item in enumerate(items, start=1):
        values = [
            item.get("folder_path", ""),
            item.get("task_id", ""),
            status_label(item.get("status", "")),
            write_status_label(item),
            int(item.get("zh_length") or 0),
            int(item.get("en_length") or 0),
            int(item.get("token_input") or 0),
            int(item.get("token_output") or 0),
            round(float(item.get("cost") or 0), 6),
            item.get("error_code", ""),
            item.get("error_message", ""),
            item.get("written_at", ""),
            item.get("backup_path", ""),
            item.get("zh_text", ""),
            item.get("en_text") or item.get("english", ""),
        ]
        for column, value in enumerate(values):
            sheet.write(index, column, _cell(value), wrap_fmt)
    sheet.set_column(0, 0, 60)
    sheet.set_column(1, 1, 16)
    sheet.set_column(2, 10, 12)
    sheet.set_column(13, 14, 60)
    sheet.freeze_panes(1, 0)

    sheet = workbook.add_worksheet("检查结果")
    for column, header in enumerate(CHECK_HEADERS):
        sheet.write(0, column, header, head_fmt)
    for index, values in enumerate(_check_rows(items), start=1):
        for column, value in enumerate(values):
            sheet.write(index, column, _cell(value), wrap_fmt)
    sheet.set_column(0, 0, 60)
    sheet.set_column(1, 1, 16)
    sheet.set_column(5, 5, 80)
    sheet.freeze_panes(1, 0)

    sheet = workbook.add_worksheet("费用")
    for column, header in enumerate(COST_HEADERS):
        sheet.write(0, column, header, head_fmt)
    for index, values in enumerate(_cost_rows(usage_rows), start=1):
        for column, value in enumerate(values):
            sheet.write(index, column, _cell(value))
    sheet.set_column(0, 3, 18)
    sheet.set_column(11, 11, 40)
    sheet.freeze_panes(1, 0)

    sheet = workbook.add_worksheet("说明")
    sheet.set_column(0, 0, 22)
    sheet.set_column(1, 1, 110)
    sheet.write(0, 0, "规则与安全说明", title_fmt)
    notes = [
        ("来源", "原生翻译/预审模块，仿照外部工具能力，不保证 Prompt 与质量完全一致。"),
        ("费用", f"按配置单价估算，货币：{currency_label(job.get('currency') or 'CNY')}；以模型服务商账单为准。"),
        ("写入", "只有用户确认后才写入英文 TXT；写前备份到管理数据目录；失败不覆盖。"),
        ("隐私", "报告不含 API Key；源 TXT 内容仅在本平台管理数据目录的报告中出现。"),
        ("检查", "语法与一致性检查只输出建议，不自动修改任何文件。"),
        ("外部报告", "历史报告只读导入，不修改外部工具目录与文件。"),
    ]
    row = 2
    for key, value in notes:
        sheet.write(row, 0, key, key_fmt)
        sheet.write(row, 1, _cell(value), wrap_fmt)
        row += 1

    workbook.close()
    return path


def export_reports(
    job: dict[str, Any],
    items: list[dict[str, Any]],
    out_dir: str,
    *,
    source_root: str = "",
    usage_rows: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
    formats: tuple[str, ...] = ("xlsx", "csv"),
    batch_name: str = "",
) -> dict[str, str]:
    directory = guard_report_dir(out_dir or "", source_root)
    stamp = _stamp(job.get("started_at"))
    name = _safe_name(batch_name or os.path.basename(job.get("root_path") or ""))
    paths: dict[str, str] = {}
    if "xlsx" in formats:
        target = unique_path(
            os.path.join(directory, f"翻译预审_{name}_{stamp}.xlsx")
        )
        paths["xlsx"] = write_excel(
            job,
            items,
            target,
            source_root=source_root,
            usage_rows=usage_rows,
            summary=summary,
        )
    if "csv" in formats:
        paths.update(
            write_csv_files(
                job,
                items,
                directory,
                source_root=source_root,
                usage_rows=usage_rows,
            )
        )
    return paths
