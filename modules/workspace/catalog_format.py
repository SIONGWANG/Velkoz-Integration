"""批次格式检查（v1.25.11 P4）。

按类目绑定的图片数量规则检查批次内的编号文件夹：
- 三级类规则优先，其次小类，最后大类；都未配置时本批次标记为“未判定”。
- 只读源目录，不修改图片/TXT/CSV。
- 结果写入 catalog_format_* 表；页面只显示总数/成功/失败，明细导出 Excel。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable

from modules.common.path_guard import normalize_path
from modules.common.timeutil import now_iso
from modules.database.connection import Database
from modules.format_checker.report import guard_report_file
from modules.workspace.catalog_service import (
    FORMAT_CHECKED,
    FORMAT_FAILED,
    FORMAT_NOT_CHECKED,
    CatalogService,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
RULE_SOURCE_NONE = "未配置"


@dataclass
class FormatRule:
    expected: int = 0
    source: str = RULE_SOURCE_NONE
    category: str = ""

    @property
    def configured(self) -> bool:
        return self.expected > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected": self.expected,
            "source": self.source,
            "category": self.category,
            "configured": self.configured,
        }


@dataclass
class GroupCheck:
    number: str
    group_path: str
    expected: int = 0
    actual: int = 0
    txt_count: int = 0
    status: str = "skipped"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "group_path": self.group_path,
            "expected": self.expected,
            "actual": self.actual,
            "txt_count": self.txt_count,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class BatchCheckResult:
    batch_id: str
    batch_name: str = ""
    batch_path: str = ""
    category_l1: str = ""
    category_l2: str = ""
    category_l3: str = ""
    rule: FormatRule = field(default_factory=FormatRule)
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duplicates: int = 0
    status: str = FORMAT_CHECKED
    message: str = ""
    run_id: str = ""
    details: list[GroupCheck] = field(default_factory=list)
    duplicate_hits: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "batch_name": self.batch_name,
            "batch_path": self.batch_path,
            "category_l1": self.category_l1,
            "category_l2": self.category_l2,
            "category_l3": self.category_l3,
            "rule": self.rule.to_dict(),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "duplicates": self.duplicates,
            "status": self.status,
            "message": self.message,
            "run_id": self.run_id,
            "details": [item.to_dict() for item in self.details],
            "duplicate_hits": list(self.duplicate_hits),
        }


@dataclass
class FormatSummary:
    batches: int = 0
    checked: int = 0
    failed: int = 0
    skipped: int = 0
    total: int = 0
    passed: int = 0
    failed_groups: int = 0
    duplicates: int = 0
    results: list[BatchCheckResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batches": self.batches,
            "checked": self.checked,
            "failed": self.failed,
            "skipped": self.skipped,
            "total": self.total,
            "passed": self.passed,
            "failed_groups": self.failed_groups,
            "duplicates": self.duplicates,
            "results": [item.to_dict() for item in self.results],
        }


def _new_id(prefix: str) -> str:
    return f"{prefix}_{os.urandom(12).hex()}"


def category_rules(service: CatalogService) -> dict[tuple[int, str], int]:
    rules: dict[tuple[int, str], int] = {}
    for row in service.categories():
        if not row.get("enabled"):
            continue
        value = int(row.get("image_count") or 0)
        if value > 0:
            rules[(int(row.get("level") or 0), str(row.get("name") or ""))] = value
    return rules


def resolve_rule(
    batch: dict[str, Any],
    rules: dict[tuple[int, str], int],
) -> FormatRule:
    for level, field_name, label in (
        (3, "category_l3", "三级类"),
        (2, "category_l2", "小类"),
        (1, "category_l1", "大类"),
    ):
        name = str(batch.get(field_name) or "")
        if not name:
            continue
        expected = rules.get((level, name), 0)
        if expected > 0:
            return FormatRule(expected=expected, source=label, category=name)
    return FormatRule()


def _count_images(path: str) -> tuple[int, int]:
    images = txt = 0
    try:
        entries = os.scandir(path)
    except OSError:
        return 0, 0
    with entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            ext = os.path.splitext(entry.name)[1].casefold()
            if ext in IMAGE_EXTS:
                images += 1
            elif ext == ".txt":
                txt += 1
    return images, txt


def _library_hits(
    db: Database,
    batch_id: str,
    numbers: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """查库中合格编号。final_acceptance 视为合格库，其他来源要求 group 已合格。"""
    wanted = {str(item) for item in numbers if str(item)}
    hits: dict[str, dict[str, Any]] = {}
    if not wanted:
        return hits
    rows = db.connect().execute(
        """
        SELECT g.task_id, g.batch_id, g.status,
               b.name AS batch_name, b.batch_path, b.reviewer_name, b.source_type
        FROM catalog_groups g
        JOIN catalog_batches b ON b.id = g.batch_id
        WHERE g.batch_id <> ?
          AND (g.status = 'qualified' OR b.source_type = 'final_acceptance')
        ORDER BY b.updated_at DESC
        """,
        (batch_id,),
    ).fetchall()
    for row in rows:
        number = str(row["task_id"] or "")
        if number in wanted and number not in hits:
            hits[number] = {
                "number": number,
                "other_batch_id": row["batch_id"],
                "other_batch_name": row["batch_name"] or "",
                "other_batch_path": row["batch_path"] or "",
                "other_reviewer": row["reviewer_name"] or "",
                "other_source_type": row["source_type"] or "",
            }
    return hits


def check_batch(
    db: Database,
    batch_id: str,
    *,
    operator: str = "",
    apply: bool = True,
) -> BatchCheckResult:
    service = CatalogService(db)
    batch = service.batch(batch_id)
    if not batch:
        raise ValueError("批次不存在")
    rules = category_rules(service)
    rule = resolve_rule(batch, rules)
    result = BatchCheckResult(
        batch_id=batch_id,
        batch_name=str(batch.get("name") or ""),
        batch_path=str(batch.get("batch_path") or ""),
        category_l1=str(batch.get("category_l1") or ""),
        category_l2=str(batch.get("category_l2") or ""),
        category_l3=str(batch.get("category_l3") or ""),
        rule=rule,
        status=FORMAT_CHECKED,
    )
    groups = service.groups(batch_id)
    result.total = len(groups)
    if not groups:
        result.status = FORMAT_CHECKED
        result.skipped = 0
        result.message = "批次没有编号文件夹，未判定"
        return result
    if not rule.configured:
        result.status = FORMAT_NOT_CHECKED
        result.message = (
            "未配置图片数量规则：请在设置页给这个批次的大类/小类/三级类填写图片数"
        )
    for group in groups:
        number = str(group.get("task_id") or "")
        path = str(group.get("path") or "")
        images, txt = _count_images(path)
        check = GroupCheck(
            number=number,
            group_path=path,
            expected=rule.expected,
            actual=images,
            txt_count=txt,
        )
        if not os.path.isdir(path):
            check.status = "failed"
            check.reason = "编号文件夹不存在"
        elif not rule.configured:
            check.status = "skipped"
            check.reason = "未配置图片数量规则"
        elif images == rule.expected:
            check.status = "passed"
        else:
            check.status = "failed"
            check.reason = f"图片数量不符：期望 {rule.expected}，实际 {images}"
        result.details.append(check)
        if check.status == "passed":
            result.passed += 1
        elif check.status == "failed":
            result.failed += 1
        else:
            result.skipped += 1
    for group in groups:
        images, txt = _count_images(str(group.get("path") or ""))
        if apply:
            db.connect().execute(
                "UPDATE catalog_groups SET image_count = ?, txt_count = ?, updated_at = ? WHERE id = ?",
                (images, txt, now_iso(), group["id"]),
            )

    hits = _library_hits(db, batch_id, [item.number for item in result.details])
    result.duplicate_hits = list(hits.values())
    result.duplicates = len(result.duplicate_hits)
    if result.failed:
        result.status = FORMAT_FAILED
        result.message = f"检查失败：{result.failed} 个编号文件夹不符合图片数量规则"
    elif not rule.configured:
        result.status = FORMAT_NOT_CHECKED
        result.message = "未配置图片数量规则，已记录图片数量但未判定"
    else:
        result.status = FORMAT_CHECKED
        result.message = f"检查通过：{result.passed} 个编号文件夹全部符合规则"
    if not apply:
        return result
    _save_result(db, result, batch, operator=operator)
    return result


def _save_result(
    db: Database,
    result: BatchCheckResult,
    batch: dict[str, Any],
    *,
    operator: str = "",
) -> None:
    conn = db.connect()
    now = now_iso()
    run_id = _new_id("cfmt")
    old_status = str(batch.get("format_status") or "not_checked")
    conn.execute(
        """
        INSERT INTO catalog_format_runs(
            id, batch_id, rule_source, expected_images, total, passed, failed,
            skipped, duplicates, status, report_path, operator, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
        """,
        (
            run_id,
            result.batch_id,
            f"{result.rule.source}:{result.rule.category}" if result.rule.configured else RULE_SOURCE_NONE,
            result.rule.expected,
            result.total,
            result.passed,
            result.failed,
            result.skipped,
            result.duplicates,
            result.status,
            operator,
            now,
            now,
        ),
    )
    for item in result.details:
        conn.execute(
            """
            INSERT INTO catalog_format_results(
                id, run_id, batch_id, number, group_path, expected_images,
                actual_images, txt_count, status, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("cfres"),
                run_id,
                result.batch_id,
                item.number,
                item.group_path,
                item.expected,
                item.actual,
                item.txt_count,
                item.status,
                item.reason,
                now,
            ),
        )
    for hit in result.duplicate_hits:
        conn.execute(
            """
            INSERT INTO catalog_duplicate_hits(
                id, run_id, batch_id, number, other_batch_id, other_batch_name,
                other_batch_path, other_reviewer, other_source_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("cdup"),
                run_id,
                result.batch_id,
                hit["number"],
                hit["other_batch_id"],
                hit["other_batch_name"],
                hit["other_batch_path"],
                hit["other_reviewer"],
                hit["other_source_type"],
                now,
            ),
        )
    if result.status != FORMAT_NOT_CHECKED:
        conn.execute(
            "UPDATE catalog_batches SET format_status = ?, updated_at = ? WHERE id = ?",
            (result.status, now, result.batch_id),
        )
        conn.execute(
            """
            INSERT INTO catalog_status_log(
                id, batch_id, kind, from_status, to_status, operator, reason, created_at
            ) VALUES (?, ?, 'format', ?, ?, ?, ?, ?)
            """,
            (
                _new_id("clog"),
                result.batch_id,
                old_status,
                result.status,
                operator,
                result.message,
                now,
            ),
        )
    conn.commit()
    result.run_id = run_id


def check_batches(
    db: Database,
    batch_ids: Iterable[str],
    *,
    operator: str = "",
) -> FormatSummary:
    summary = FormatSummary()
    service = CatalogService(db)
    for batch_id in batch_ids:
        batch_id = str(batch_id)
        if not batch_id:
            continue
        summary.batches += 1
        try:
            result = check_batch(db, batch_id, operator=operator, apply=True)
        except Exception as exc:
            summary.failed += 1
            failed = BatchCheckResult(
                batch_id=batch_id,
                status=FORMAT_FAILED,
                message=f"检查失败：{exc}",
            )
            summary.results.append(failed)
            continue
        summary.results.append(result)
        summary.total += result.total
        summary.passed += result.passed
        summary.failed_groups += result.failed
        summary.duplicates += result.duplicates
        if result.status == FORMAT_FAILED:
            summary.failed += 1
        elif result.skipped:
            summary.skipped += 1
        else:
            summary.checked += 1
    return summary


def latest_run(db: Database, batch_id: str) -> dict[str, Any] | None:
    row = db.connect().execute(
        "SELECT * FROM catalog_format_runs WHERE batch_id = ? ORDER BY finished_at DESC LIMIT 1",
        (batch_id,),
    ).fetchone()
    return dict(row) if row else None


def run_details(db: Database, run_id: str) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT * FROM catalog_format_results WHERE run_id = ? ORDER BY number",
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def run_duplicates(db: Database, run_id: str) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT * FROM catalog_duplicate_hits WHERE run_id = ? ORDER BY number",
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def export_excel(
    db: Database,
    summary: FormatSummary,
    out_path: str,
    *,
    source_root: str = "",
) -> str:
    """导出批量检查明细；返回实际文件路径。"""
    import xlsxwriter

    target = normalize_path(out_path)
    if source_root:
        target = guard_report_file(target, source_root)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)

    workbook = xlsxwriter.Workbook(target, {"strings_to_formulas": False, "strings_to_urls": False})
    title_fmt = workbook.add_format({"bold": True, "font_size": 14})
    head_fmt = workbook.add_format({"bold": True, "bg_color": "#DDEBF7", "border": 1, "align": "center"})
    wrap_fmt = workbook.add_format({"text_wrap": True, "valign": "top"})
    key_fmt = workbook.add_format({"bold": True, "valign": "top"})
    bad_fmt = workbook.add_format({"font_color": "#9C0006"})
    good_fmt = workbook.add_format({"font_color": "#006100"})

    sheet = workbook.add_worksheet("总览")
    sheet.set_column(0, 0, 20)
    sheet.set_column(1, 1, 100)
    sheet.write(0, 0, "批次格式检查报告", title_fmt)
    rows = [
        ("批次数", summary.batches),
        ("全部通过批次", summary.checked),
        ("检查失败批次", summary.failed),
        ("未配置规则批次", summary.skipped),
        ("编号总数", summary.total),
        ("通过编号", summary.passed),
        ("失败编号", summary.failed_groups),
        ("重复编号", summary.duplicates),
        ("说明", "本报告只读生成；检查过程不修改源图片/TXT/CSV。"),
    ]
    for index, (key, value) in enumerate(rows, start=2):
        sheet.write(index, 0, key, key_fmt)
        sheet.write(index, 1, value, wrap_fmt)

    sheet = workbook.add_worksheet("批次汇总")
    headers = ["批次", "大类", "小类", "三级类", "规则", "期望图片", "编号数", "通过", "失败", "未判定", "重复", "状态", "说明"]
    for column, header in enumerate(headers):
        sheet.write(0, column, header, head_fmt)
    for index, item in enumerate(summary.results, start=1):
        values = [
            item.batch_name,
            item.category_l1,
            item.category_l2,
            item.category_l3,
            f"{item.rule.source}:{item.rule.category}" if item.rule.configured else RULE_SOURCE_NONE,
            item.rule.expected,
            item.total,
            item.passed,
            item.failed,
            item.skipped,
            item.duplicates,
            "检查失败" if item.status == FORMAT_FAILED else "已检查",
            item.message,
        ]
        for column, value in enumerate(values):
            sheet.write(index, column, value, bad_fmt if column == 11 and item.status == FORMAT_FAILED else None)
    sheet.freeze_panes(1, 0)
    sheet.set_column(0, 0, 40)
    sheet.set_column(1, 3, 18)
    sheet.set_column(12, 12, 60)

    sheet = workbook.add_worksheet("检查明细")
    headers = ["批次", "编号", "编号文件夹", "期望图片", "实际图片", "TXT", "结果", "说明"]
    for column, header in enumerate(headers):
        sheet.write(0, column, header, head_fmt)
    row_index = 1
    for item in summary.results:
        for detail in item.details:
            values = [
                item.batch_name,
                detail.number,
                detail.group_path,
                detail.expected,
                detail.actual,
                detail.txt_count,
                {"passed": "通过", "failed": "失败", "skipped": "未判定"}.get(detail.status, detail.status),
                detail.reason,
            ]
            for column, value in enumerate(values):
                fmt = good_fmt if column == 6 and detail.status == "passed" else (
                    bad_fmt if column == 6 and detail.status == "failed" else None
                )
                sheet.write(row_index, column, value, fmt)
            row_index += 1
    sheet.freeze_panes(1, 0)
    sheet.set_column(0, 0, 36)
    sheet.set_column(2, 2, 90)
    sheet.set_column(7, 7, 50)

    sheet = workbook.add_worksheet("重复编号")
    headers = ["批次", "编号", "库中批次", "库中路径", "库中验收员", "来源类型"]
    for column, header in enumerate(headers):
        sheet.write(0, column, header, head_fmt)
    row_index = 1
    for item in summary.results:
        for hit in item.duplicate_hits:
            values = [
                item.batch_name,
                hit.get("number"),
                hit.get("other_batch_name"),
                hit.get("other_batch_path"),
                hit.get("other_reviewer"),
                hit.get("other_source_type"),
            ]
            for column, value in enumerate(values):
                sheet.write(row_index, column, value)
            row_index += 1
    sheet.freeze_panes(1, 0)
    sheet.set_column(0, 0, 36)
    sheet.set_column(1, 1, 16)
    sheet.set_column(2, 2, 36)
    sheet.set_column(3, 3, 90)

    workbook.close()
    return target

