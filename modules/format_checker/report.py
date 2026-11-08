"""格式检查报告：Excel（xlsxwriter）与 CSV。

报告只写入调用方指定的输出目录；如果输出目录位于检查源目录内，直接拒绝。
"""
from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime
from typing import Any

from modules.common.path_guard import is_relative_to, normalize_path

from .errors import code_title, ERROR_CATALOG
from .models import FormatCheckResult, Template

_INVALID_CELL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_CELL = 32000


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    text = _INVALID_CELL.sub("", text)
    if len(text) > _MAX_CELL:
        text = text[:_MAX_CELL] + "…（已截断）"
    return text


def guard_report_dir(report_dir: str, source_root: str) -> str:
    """拒绝把报告写进检查源目录。"""
    if not report_dir:
        raise ValueError("未指定报告输出目录")
    target = normalize_path(report_dir)
    root = normalize_path(source_root)
    if os.path.normcase(target) == os.path.normcase(root) or is_relative_to(target, root):
        raise ValueError(
            f"报告输出目录不能位于检查源目录内：{target}（源目录：{root}）"
        )
    return target


def guard_report_file(path: str, source_root: str) -> str:
    target = normalize_path(path)
    root = normalize_path(source_root)
    if os.path.normcase(target) == os.path.normcase(root) or is_relative_to(target, root):
        raise ValueError(f"报告文件不能写入检查源目录：{target}")
    return target


def _stamp(started_at: str) -> str:
    try:
        return datetime.fromisoformat(started_at).strftime("%Y%m%d_%H%M%S")
    except (TypeError, ValueError):
        return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_name(value: str, fallback: str = "root") -> str:
    text = _INVALID_NAME.sub("_", (value or "").strip()).strip(" .")
    return text[:60] or fallback


def _severity_label(value: str) -> str:
    return {"error": "错误", "warn": "警告", "info": "提示"}.get(value, value)


def _result_label(value: str) -> str:
    return {
        "pass": "合格",
        "fail": "异常",
        "warn": "警告",
        "skipped": "跳过",
    }.get(value, value)


def _mode_label(value: str | None) -> str:
    return {"single": "单图", "multi": "多图"}.get(value or "", value or "")


def _issue_rows(result: FormatCheckResult) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for issue in result.run_issues:
        rows.append(
            [
                issue.task_id,
                issue.task_path,
                issue.code,
                _severity_label(issue.severity),
                issue.file_path or "",
                issue.expected or "",
                issue.actual or "",
                issue.message,
                issue.suggestion or "",
            ]
        )
    for task in result.tasks:
        for issue in task.issues:
            rows.append(
                [
                    issue.task_id or task.task_id,
                    issue.task_path or task.task_path,
                    issue.code,
                    _severity_label(issue.severity),
                    issue.file_path or "",
                    issue.expected or "",
                    issue.actual or "",
                    issue.message,
                    issue.suggestion or "",
                ]
            )
    return rows


def _task_rows(result: FormatCheckResult) -> list[list[Any]]:
    return [
        [
            task.task_id,
            task.task_path,
            _mode_label(task.detected_mode),
            _result_label(task.result),
            task.image_count,
            task.txt_count,
            task.file_count,
            task.issue_count,
            task.fingerprint,
        ]
        for task in result.tasks
    ]


TASK_HEADERS = [
    "任务ID",
    "路径",
    "模式",
    "结果",
    "图片数",
    "TXT数",
    "文件数",
    "问题数",
    "指纹",
]

ISSUE_HEADERS = [
    "任务ID",
    "任务路径",
    "错误码",
    "级别",
    "文件",
    "期望",
    "实际",
    "说明",
    "建议",
]


def write_excel(
    result: FormatCheckResult,
    out_path: str,
    template: Template,
    *,
    diff: dict[str, Any] | None = None,
    source_root: str | None = None,
) -> str:
    """生成 Excel 报告，返回实际路径。"""
    import xlsxwriter

    path = guard_report_file(out_path, source_root or result.summary.root_path)
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
    ok_fmt = workbook.add_format({"font_color": "#006100", "bold": True})

    summary = result.summary

    # ── 总览 ──────────────────────────────────────────────
    sheet = workbook.add_worksheet("总览")
    sheet.set_column(0, 0, 22)
    sheet.set_column(1, 1, 110)
    sheet.write(0, 0, "格式检查报告", title_fmt)
    rows: list[tuple[str, Any]] = [
        ("运行ID", summary.run_id),
        ("根目录", summary.root_path),
        ("模板", f"{summary.template_name}（{summary.template_id} v{summary.template_version}）"),
        ("批次ID", summary.batch_id),
        ("操作员", summary.operator),
        ("开始时间", summary.started_at),
        ("结束时间", summary.finished_at),
        ("状态", {"completed": "完成", "cancelled": "已取消", "failed": "失败"}.get(summary.status, summary.status)),
        ("耗时", f"{summary.duration_ms} 毫秒"),
        ("任务总数", summary.scanned_tasks),
        ("合格", summary.passed_tasks),
        ("异常", summary.failed_tasks),
        ("警告", summary.warning_tasks),
        ("跳过", summary.skipped_tasks),
        ("扫描是否截断", summary.truncated),
        ("完整性校验", "正常" if summary.integrity_status == "ok" else "检查期间发生变化"),
        ("上次运行", summary.previous_run_id),
        ("报告路径", summary.report_path),
        ("说明", "本报告只读生成；检查过程不修改源图片/TXT/CSV。"),
    ]
    row = 2
    for key, value in rows:
        sheet.write(row, 0, key, key_fmt)
        sheet.write(row, 1, _cell(value), wrap_fmt)
        row += 1

    if result.integrity_changes:
        row += 1
        sheet.write(row, 0, "完整性变化", warn_fmt)
        row += 1
        for item in result.integrity_changes:
            sheet.write(row, 1, _cell(f"{item.get('task_path')}：{item.get('before')} → {item.get('after')}"), wrap_fmt)
            row += 1

    row += 1
    sheet.write(row, 0, "错误码", head_fmt)
    sheet.write(row, 1, "数量", head_fmt)
    sheet.write(row, 2, "说明", head_fmt)
    row += 1
    if summary.issues_by_code:
        for code, count in summary.issues_by_code.items():
            sheet.write(row, 0, code)
            sheet.write(row, 1, count)
            sheet.write(row, 2, _cell(code_title(code)))
            row += 1
    else:
        sheet.write(row, 0, "无")
        sheet.write(row, 1, 0)
        row += 1

    # ── 任务结果 ──────────────────────────────────────────
    sheet = workbook.add_worksheet("任务结果")
    for column, header in enumerate(TASK_HEADERS):
        sheet.write(0, column, header, head_fmt)
    for index, row_values in enumerate(_task_rows(result), start=1):
        for column, value in enumerate(row_values):
            sheet.write(index, column, _cell(value))
    sheet.set_column(0, 0, 16)
    sheet.set_column(1, 1, 90)
    sheet.set_column(2, 8, 12)
    sheet.freeze_panes(1, 0)

    # ── 异常明细 ──────────────────────────────────────────
    sheet = workbook.add_worksheet("异常明细")
    for column, header in enumerate(ISSUE_HEADERS):
        sheet.write(0, column, header, head_fmt)
    for index, row_values in enumerate(_issue_rows(result), start=1):
        for column, value in enumerate(row_values):
            sheet.write(index, column, _cell(value))
    sheet.set_column(0, 0, 14)
    sheet.set_column(1, 1, 70)
    sheet.set_column(2, 2, 10)
    sheet.set_column(3, 3, 8)
    sheet.set_column(4, 4, 50)
    sheet.set_column(5, 8, 28)
    sheet.freeze_panes(1, 0)

    # ── 规则说明 ──────────────────────────────────────────
    sheet = workbook.add_worksheet("规则说明")
    sheet.set_column(0, 0, 22)
    sheet.set_column(1, 1, 110)
    sheet.write(0, 0, "规则与只读说明", title_fmt)
    rule_rows: list[tuple[str, Any]] = [
        ("模板ID", template.id),
        ("模板名称", template.name),
        ("模板版本", template.version),
        ("作用域", template.scope),
        ("ID规则", _describe_id_rule(template)),
        ("检查模式", "、".join(_mode_label(item) for item in template.modes)),
        ("额外文件策略", template.extra_files),
        ("警告是否算失败", template.warn_as_fail),
        ("递归扫描", template.scan.recursive),
        ("忽略名称", "、".join(template.scan.ignore_names)),
        ("只读声明", "检查只读取目录项、文件大小和修改时间；不写入、不重命名、不删除任何源文件。"),
    ]
    row = 2
    for key, value in rule_rows:
        sheet.write(row, 0, key, key_fmt)
        sheet.write(row, 1, _cell(value), wrap_fmt)
        row += 1
    row += 1
    sheet.write(row, 0, "模板快照", key_fmt)
    row += 1
    for line in json.dumps(template.to_dict(), ensure_ascii=False, indent=2).splitlines():
        sheet.write(row, 1, _cell(line))
        row += 1

    if diff is not None:
        sheet = workbook.add_worksheet("差异")
        headers = ["类型", "任务ID", "路径", "说明"]
        for column, header in enumerate(headers):
            sheet.write(0, column, header, head_fmt)
        rows = _diff_rows(diff)
        for index, row_values in enumerate(rows, start=1):
            for column, value in enumerate(row_values):
                sheet.write(index, column, _cell(value))
        sheet.set_column(1, 1, 16)
        sheet.set_column(2, 2, 90)
        sheet.set_column(3, 3, 40)
        sheet.freeze_panes(1, 0)

    workbook.close()
    return path


def _describe_id_rule(template: Template) -> str:
    rule = template.id_rule
    if rule.mode == "digits":
        return f"{rule.length} 位纯数字"
    if rule.mode == "prefix_digits":
        return f"{rule.prefix} + {rule.length} 位数字"
    return f"正则：{rule.regex}"


def _diff_rows(diff: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in diff.get("added", []):
        rows.append(["新增任务", item.get("task_id", ""), item.get("task_path", ""), item.get("result", "")])
    for item in diff.get("removed", []):
        rows.append(["消失任务", item.get("task_id", ""), item.get("task_path", ""), item.get("result", "")])
    for item in diff.get("pass_to_fail", []):
        rows.append(["通过→失败", "", item.get("task_path", ""), f"{item.get('before')} → {item.get('after')}"])
    for item in diff.get("fail_to_pass", []):
        rows.append(["失败→通过", "", item.get("task_path", ""), f"{item.get('before')} → {item.get('after')}"])
    for item in diff.get("code_changed", []):
        rows.append(
            [
                "错误码变化",
                "",
                item.get("task_path", ""),
                f"新增 {item.get('added')}；消失 {item.get('removed')}",
            ]
        )
    for item in diff.get("file_count_changed", []):
        rows.append(
            [
                "文件数变化",
                "",
                item.get("task_path", ""),
                f"{item.get('before')} → {item.get('after')}",
            ]
        )
    return rows


def _unique_path(path: str) -> str:
    """避免同一秒内的多次检查互相覆盖报告。"""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    index = 2
    while True:
        candidate = f"{stem}_{index}{ext}"
        if not os.path.exists(candidate):
            return candidate
        index += 1


def _write_csv(path: str, headers: list[str], rows: list[list[Any]]) -> str:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([_cell(value) for value in row])
    return path


def write_csv(
    result: FormatCheckResult,
    out_dir: str,
    *,
    source_root: str | None = None,
) -> dict[str, str]:
    """生成任务 CSV 与问题 CSV（UTF-8 BOM），返回路径。"""
    directory = guard_report_dir(out_dir, source_root or result.summary.root_path)
    os.makedirs(directory, exist_ok=True)
    stamp = _stamp(result.summary.started_at)
    tasks_path = _unique_path(os.path.join(directory, f"format_check_tasks_{stamp}.csv"))
    issues_path = _unique_path(os.path.join(directory, f"format_check_issues_{stamp}.csv"))
    _write_csv(tasks_path, TASK_HEADERS, _task_rows(result))
    _write_csv(issues_path, ISSUE_HEADERS, _issue_rows(result))
    return {"tasks_csv": tasks_path, "issues_csv": issues_path}


def export_reports(
    result: FormatCheckResult,
    out_dir: str,
    template: Template,
    *,
    formats: tuple[str, ...] = ("xlsx", "csv"),
    source_root: str | None = None,
    diff: dict[str, Any] | None = None,
    batch_name: str = "",
) -> dict[str, str]:
    """按指定格式生成报告，返回格式 → 路径。"""
    root = source_root or result.summary.root_path
    directory = guard_report_dir(out_dir, root)
    stamp = _stamp(result.summary.started_at)
    name = _safe_name(batch_name or os.path.basename(result.summary.root_path) or "root")
    paths: dict[str, str] = {}
    if "xlsx" in formats:
        target = _unique_path(
            os.path.join(directory, f"格式检查_{name}_{stamp}.xlsx")
        )
        result.summary.report_path = target
        paths["xlsx"] = write_excel(
            result, target, template, diff=diff, source_root=root
        )
    if "csv" in formats:
        paths.update(write_csv(result, directory, source_root=root))
    return paths


def issue_catalog_rows() -> list[list[Any]]:
    """完整错误码目录，供模板/帮助页展示。"""
    return [
        [code, severity, title, suggestion]
        for code, (severity, title, suggestion) in sorted(ERROR_CATALOG.items())
    ]
