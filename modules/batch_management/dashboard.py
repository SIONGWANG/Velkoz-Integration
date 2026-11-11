"""批次工作台与团队/类别聚合（v1.25.5）。

只读查询管理数据库：批次、数据组、格式检查、翻译预审与导入记录。
不写任何表，也不访问或修改源图片/TXT/CSV。
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable

from modules.common.timeutil import today_str
from modules.common.path_guard import normalize_path
from modules.database.connection import Database
from modules.workspace.models import (
    ACCEPTANCE_STATE_COMPLETED,
    ACCEPTANCE_STATE_IN_PROGRESS,
    ACCEPTANCE_STATE_LABELS,
    ACCEPTANCE_STATE_NO_RECORDS,
    ACCEPTANCE_STATE_PAUSED,
    ACCEPTANCE_STATE_PENDING,
    FINAL_STATE_COMPLETED,
    FINAL_STATE_FOUND,
    FINAL_STATE_IN_PROGRESS,
    FINAL_STATE_LABELS,
    FORMAT_STATE_FAILED,
    FORMAT_STATE_LABELS,
    FORMAT_STATE_NOT_CHECKED,
    FORMAT_STATE_PARTIAL,
    FORMAT_STATE_PASSED,
    FORMAT_STATE_RUNNING,
    FORMAT_STATE_WARNING,
    SOURCE_FINAL,
    SOURCE_LABELS,
    SOURCE_PENDING,
    TRANSLATION_STATE_COMPLETED,
    TRANSLATION_STATE_FAILED,
    TRANSLATION_STATE_LABELS,
    TRANSLATION_STATE_MISSING_ZH,
    TRANSLATION_STATE_NOT_SCANNED,
    TRANSLATION_STATE_PARTIAL,
    TRANSLATION_STATE_PENDING,
    TRANSLATION_STATE_RUNNING,
)

STATUS_LABELS = {
    "confirmed": "已确认",
    "paused": "已暂停",
    "completed": "已完成",
    "archived": "已归档",
    "pending": "待处理",
    "in_progress": "进行中",
    "not_started": "未开始",
    "not_checked": "未检查",
    "passed": "通过",
    "failed": "失败",
    "partial": "部分完成",
    "warning": "有警告",
    "skipped": "已跳过",
    "running": "进行中",
    "cancelled": "已取消",
    "draft": "草稿",
}

ACCEPTANCE_LABELS = {
    "not_started": "未开始",
    "in_progress": "进行中",
    "completed": "已完成",
    "paused": "已暂停",
}

FORMAT_LABELS = {
    "not_checked": "未检查",
    "passed": "通过",
    "failed": "失败",
    "partial": "部分通过",
}

TRANSLATION_LABELS = {
    "not_started": "未开始",
    "in_progress": "进行中",
    "completed": "已完成",
    "partial": "部分完成",
    "failed": "失败",
}


def status_label(value: str | None) -> str:
    return STATUS_LABELS.get(value or "", value or "")


def acceptance_label(value: str | None) -> str:
    return ACCEPTANCE_LABELS.get(value or "", value or "未开始")


def format_label(value: str | None) -> str:
    return FORMAT_LABELS.get(value or "", value or "未检查")


def translation_label(value: str | None) -> str:
    return TRANSLATION_LABELS.get(value or "", value or "未开始")


def _json_loads(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _timestamp(item: dict[str, Any]) -> str:
    return _first_text(
        item.get("imported_at"),
        item.get("finished_at"),
        item.get("started_at"),
        item.get("updated_at"),
        item.get("created_at"),
    )


class BatchDashboard:
    """批次级只读聚合。"""

    def __init__(self, db: Database):
        self.db = db

    # ── 原始读取 ─────────────────────────────────────────
    def _group_stats(self) -> dict[str, dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT batch_id, COUNT(*) AS task_count,
                   COALESCE(SUM(image_count), 0) AS image_count,
                   COALESCE(SUM(txt_count), 0) AS txt_count,
                   GROUP_CONCAT(DISTINCT NULLIF(user_name, '')) AS user_names
            FROM data_groups
            GROUP BY batch_id
            """
        ).fetchall()
        file_rows = self.db.connect().execute(
            """
            SELECT g.batch_id,
                   COALESCE(SUM(CASE WHEN f.role = 'zh' THEN 1 ELSE 0 END), 0) AS zh_files,
                   COALESCE(SUM(CASE WHEN f.role = 'en' THEN 1 ELSE 0 END), 0) AS en_files
            FROM data_groups g
            LEFT JOIN data_group_files f ON f.data_group_id = g.id
            GROUP BY g.batch_id
            """
        ).fetchall()
        file_map = {
            row["batch_id"]: {
                "zh_files": int(row["zh_files"] or 0),
                "en_files": int(row["en_files"] or 0),
            }
            for row in file_rows
        }
        return {
            row["batch_id"]: {
                "task_count": int(row["task_count"] or 0),
                "image_count": int(row["image_count"] or 0),
                "txt_count": int(row["txt_count"] or 0),
                "user_names": str(row["user_names"] or ""),
                **file_map.get(row["batch_id"], {"zh_files": 0, "en_files": 0}),
            }
            for row in rows
        }

    def _import_stats(self) -> dict[str, dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT batch_id, COUNT(*) AS import_count,
                   COALESCE(SUM(rows_new), 0) AS imported_records,
                   COALESCE(SUM(rows_error), 0) AS import_errors,
                   MAX(imported_at) AS last_import
            FROM record_imports
            WHERE batch_id IS NOT NULL
            GROUP BY batch_id
            """
        ).fetchall()
        return {row["batch_id"]: dict(row) for row in rows}

    def _format_stats(self) -> dict[str, dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT batch_id, COUNT(*) AS format_runs,
                   MAX(COALESCE(finished_at, started_at)) AS last_format
            FROM format_check_runs
            WHERE batch_id IS NOT NULL
            GROUP BY batch_id
            """
        ).fetchall()
        latest = self.db.connect().execute(
            """
            SELECT * FROM (
                SELECT r.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.batch_id
                           ORDER BY COALESCE(r.finished_at, r.started_at) DESC
                       ) AS rn
                FROM format_check_runs r
                WHERE r.batch_id IS NOT NULL
            ) WHERE rn = 1
            """
        ).fetchall()
        latest_map = {row["batch_id"]: dict(row) for row in latest}
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            latest_run = latest_map.get(row["batch_id"], {})
            item["latest_run"] = latest_run
            item["format_failed"] = int(latest_run.get("failed_tasks") or 0)
            item["format_warnings"] = int(latest_run.get("warning_tasks") or 0)
            result[row["batch_id"]] = item
        return result

    def _translation_stats(self) -> dict[str, dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT batch_id, COUNT(*) AS translation_jobs,
                   MAX(COALESCE(finished_at, started_at, created_at)) AS last_translation
            FROM translation_jobs
            WHERE batch_id IS NOT NULL
            GROUP BY batch_id
            """
        ).fetchall()
        latest = self.db.connect().execute(
            """
            SELECT * FROM (
                SELECT j.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY j.batch_id
                           ORDER BY COALESCE(j.finished_at, j.started_at, j.created_at) DESC
                       ) AS rn
                FROM translation_jobs j
                WHERE j.batch_id IS NOT NULL
            ) WHERE rn = 1
            """
        ).fetchall()
        latest_map = {row["batch_id"]: dict(row) for row in latest}
        item_map: dict[str, dict[str, Any]] = {}
        job_ids = [item["id"] for item in latest_map.values() if item.get("id")]
        if job_ids:
            placeholders = ",".join("?" for _ in job_ids)
            item_rows = self.db.connect().execute(
                f"""
                SELECT job_id, COUNT(*) AS total,
                       COALESCE(SUM(CASE WHEN status = 'translated' THEN 1 ELSE 0 END), 0) AS translated,
                       COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,
                       COALESCE(SUM(CASE WHEN status IN ('missing_en', 'empty_en') THEN 1 ELSE 0 END), 0) AS pending,
                       COALESCE(SUM(CASE WHEN status IN ('no_zh', 'empty_zh') THEN 1 ELSE 0 END), 0) AS missing_zh,
                       COALESCE(SUM(token_input), 0) AS token_input,
                       COALESCE(SUM(token_output), 0) AS token_output,
                       COALESCE(SUM(cost), 0) AS cost
                FROM translation_items WHERE job_id IN ({placeholders}) GROUP BY job_id
                """,
                job_ids,
            ).fetchall()
            item_map = {row["job_id"]: dict(row) for row in item_rows}
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            latest_job = latest_map.get(row["batch_id"], {})
            counts = item_map.get(latest_job.get("id") or "", {})
            item["latest_job"] = latest_job
            item["translated_items"] = int(counts.get("translated") or 0)
            item["failed_items"] = int(counts.get("failed") or 0)
            item["pending_items"] = int(counts.get("pending") or 0)
            item["missing_zh_items"] = int(counts.get("missing_zh") or 0)
            item["item_total"] = int(counts.get("total") or 0)
            item["token_input"] = int(counts.get("token_input") or 0)
            item["token_output"] = int(counts.get("token_output") or 0)
            item["cost"] = float(counts.get("cost") or 0.0)
            result[row["batch_id"]] = item
        return result

    def _acceptance_stats(self) -> dict[str, dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT COALESCE(ri.batch_id, g.batch_id) AS batch_id,
                   COUNT(*) AS accepted_records,
                   COALESCE(SUM(CASE WHEN ir.result IN ('合格', '修改后合格') THEN 1 ELSE 0 END), 0) AS accepted_qualified,
                   COALESCE(SUM(CASE WHEN ir.result IN ('不合格', '作废') THEN 1 ELSE 0 END), 0) AS accepted_rejected,
                   COALESCE(SUM(CASE WHEN ir.result IS NULL OR ir.result = ''
                                      OR ir.result NOT IN ('合格', '修改后合格', '不合格', '作废')
                                THEN 1 ELSE 0 END), 0) AS accepted_other,
                   MAX(COALESCE(ir.record_time, ir.created_at)) AS last_accepted
            FROM imported_records ir
            LEFT JOIN record_imports ri ON ri.id = ir.import_id
            LEFT JOIN data_groups g ON g.id = ir.data_group_id
            WHERE COALESCE(ri.batch_id, g.batch_id) IS NOT NULL
            GROUP BY COALESCE(ri.batch_id, g.batch_id)
            """
        ).fetchall()
        return {row["batch_id"]: dict(row) for row in rows}

    def _archive_stats(self) -> dict[str, dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT j.batch_id,
                   COALESCE(SUM(CASE WHEN i.status IN ('verified', 'skipped') THEN 1 ELSE 0 END), 0) AS archived_groups,
                   COALESCE(SUM(CASE WHEN i.status = 'conflict' THEN 1 ELSE 0 END), 0) AS archive_conflicts,
                   COALESCE(SUM(CASE WHEN i.status = 'failed' THEN 1 ELSE 0 END), 0) AS archive_failed,
                   MAX(COALESCE(j.finished_at, j.updated_at, j.created_at)) AS last_archive
            FROM archive_jobs j
            LEFT JOIN archive_items i ON i.job_id = j.id
            WHERE j.batch_id IS NOT NULL
            GROUP BY j.batch_id
            """
        ).fetchall()
        return {row["batch_id"]: dict(row) for row in rows}

    @staticmethod
    def _format_state(latest: dict[str, Any]) -> tuple[str, str]:
        if not latest:
            return FORMAT_STATE_NOT_CHECKED, FORMAT_STATE_LABELS[FORMAT_STATE_NOT_CHECKED]
        status = str(latest.get("status") or "")
        if status in {"running", "started"}:
            return FORMAT_STATE_RUNNING, FORMAT_STATE_LABELS[FORMAT_STATE_RUNNING]
        if status in {"failed", "error"}:
            return FORMAT_STATE_FAILED, FORMAT_STATE_LABELS[FORMAT_STATE_FAILED]
        if status in {"cancelled", "partial"}:
            return FORMAT_STATE_PARTIAL, FORMAT_STATE_LABELS[FORMAT_STATE_PARTIAL]
        if int(latest.get("failed_tasks") or 0) > 0:
            return FORMAT_STATE_FAILED, FORMAT_STATE_LABELS[FORMAT_STATE_FAILED]
        if int(latest.get("warning_tasks") or 0) > 0:
            return FORMAT_STATE_WARNING, FORMAT_STATE_LABELS[FORMAT_STATE_WARNING]
        if int(latest.get("scanned_tasks") or 0) > 0:
            return FORMAT_STATE_PASSED, FORMAT_STATE_LABELS[FORMAT_STATE_PASSED]
        return FORMAT_STATE_PARTIAL, FORMAT_STATE_LABELS[FORMAT_STATE_PARTIAL]

    @staticmethod
    def _translation_state(
        latest: dict[str, Any],
        counts: dict[str, Any],
        *,
        task_count: int,
        zh_files: int,
    ) -> tuple[str, str]:
        if not latest:
            if task_count <= 0:
                return TRANSLATION_STATE_NOT_SCANNED, TRANSLATION_STATE_LABELS[TRANSLATION_STATE_NOT_SCANNED]
            if zh_files <= 0:
                return TRANSLATION_STATE_MISSING_ZH, TRANSLATION_STATE_LABELS[TRANSLATION_STATE_MISSING_ZH]
            return TRANSLATION_STATE_PENDING, TRANSLATION_STATE_LABELS[TRANSLATION_STATE_PENDING]
        status = str(latest.get("status") or "")
        if status in {"running", "started", "pending"}:
            return TRANSLATION_STATE_RUNNING, TRANSLATION_STATE_LABELS[TRANSLATION_STATE_RUNNING]
        if int(counts.get("failed_items") or 0) > 0 or status in {"failed", "fail"}:
            return TRANSLATION_STATE_FAILED, TRANSLATION_STATE_LABELS[TRANSLATION_STATE_FAILED]
        if status in {"cancelled", "partial"}:
            return TRANSLATION_STATE_PARTIAL, TRANSLATION_STATE_LABELS[TRANSLATION_STATE_PARTIAL]
        if int(counts.get("missing_zh_items") or 0) > 0 and int(counts.get("translated_items") or 0) <= 0:
            return TRANSLATION_STATE_MISSING_ZH, TRANSLATION_STATE_LABELS[TRANSLATION_STATE_MISSING_ZH]
        if int(counts.get("translated_items") or 0) > 0:
            if int(counts.get("pending_items") or 0) == 0 and int(counts.get("failed_items") or 0) == 0:
                return TRANSLATION_STATE_COMPLETED, TRANSLATION_STATE_LABELS[TRANSLATION_STATE_COMPLETED]
            return TRANSLATION_STATE_PARTIAL, TRANSLATION_STATE_LABELS[TRANSLATION_STATE_PARTIAL]
        return TRANSLATION_STATE_PENDING, TRANSLATION_STATE_LABELS[TRANSLATION_STATE_PENDING]

    @staticmethod
    def _acceptance_state(
        *,
        batch_status: str,
        records: int,
        qualified: int,
        rejected: int,
        remaining: int,
    ) -> tuple[str, str]:
        if batch_status == "paused":
            return ACCEPTANCE_STATE_PAUSED, ACCEPTANCE_STATE_LABELS[ACCEPTANCE_STATE_PAUSED]
        if records <= 0:
            if batch_status == "completed":
                return ACCEPTANCE_STATE_COMPLETED, ACCEPTANCE_STATE_LABELS[ACCEPTANCE_STATE_COMPLETED]
            return ACCEPTANCE_STATE_NO_RECORDS, ACCEPTANCE_STATE_LABELS[ACCEPTANCE_STATE_NO_RECORDS]
        if batch_status == "completed" or (remaining <= 0 and (qualified + rejected) > 0):
            return ACCEPTANCE_STATE_COMPLETED, ACCEPTANCE_STATE_LABELS[ACCEPTANCE_STATE_COMPLETED]
        if qualified + rejected > 0:
            return ACCEPTANCE_STATE_IN_PROGRESS, ACCEPTANCE_STATE_LABELS[ACCEPTANCE_STATE_IN_PROGRESS]
        return ACCEPTANCE_STATE_PENDING, ACCEPTANCE_STATE_LABELS[ACCEPTANCE_STATE_PENDING]

    @staticmethod
    def _final_state(
        *,
        batch_status: str,
        records: int,
        qualified: int,
        rejected: int,
        remaining: int,
    ) -> tuple[str, str]:
        if batch_status == "completed" or (records > 0 and remaining <= 0 and (qualified + rejected) > 0):
            return FINAL_STATE_COMPLETED, FINAL_STATE_LABELS[FINAL_STATE_COMPLETED]
        if records > 0 or qualified + rejected > 0:
            return FINAL_STATE_IN_PROGRESS, FINAL_STATE_LABELS[FINAL_STATE_IN_PROGRESS]
        return FINAL_STATE_FOUND, FINAL_STATE_LABELS[FINAL_STATE_FOUND]

    def batches(self) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT * FROM batches ORDER BY COALESCE(submit_date, created_at) DESC, created_at DESC"
        ).fetchall()
        groups = self._group_stats()
        imports = self._import_stats()
        acceptance = self._acceptance_stats()
        archives = self._archive_stats()
        formats = self._format_stats()
        translations = self._translation_stats()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            batch_id = item["id"]
            identity = _json_loads(item.get("identity_json"))
            group = groups.get(batch_id, {})
            imported = imports.get(batch_id, {})
            accepted = acceptance.get(batch_id, {})
            archive = archives.get(batch_id, {})
            format_info = formats.get(batch_id, {})
            translation_info = translations.get(batch_id, {})
            latest_format = format_info.get("latest_run") or {}
            latest_translation = translation_info.get("latest_job") or {}
            team = _first_text(
                identity.get("team"),
                item.get("team_id"),
                (group.get("user_names") or "").split(",")[0],
            )
            supplier = _first_text(identity.get("supplier"), item.get("supplier_id"))
            project = _first_text(identity.get("project"), item.get("project_id"), item.get("project_name"))
            task_count = int(group.get("task_count") or item.get("data_count") or 0)
            record_count = int(accepted.get("accepted_records") or 0)
            accepted_qualified = int(accepted.get("accepted_qualified") or 0)
            accepted_rejected = int(accepted.get("accepted_rejected") or 0)
            acceptance_state, acceptance_state_label = self._acceptance_state(
                batch_status=str(item.get("status") or ""),
                records=record_count,
                qualified=accepted_qualified,
                rejected=accepted_rejected,
                remaining=int(item.get("remaining_count") or 0),
            )
            format_state, format_state_label = self._format_state(latest_format)
            translation_state, translation_state_label = self._translation_state(
                latest_translation,
                translation_info,
                task_count=task_count,
                zh_files=int(group.get("zh_files") or group.get("txt_count") or 0),
            )
            final_state = final_state_label = ""
            if (item.get("source_type") or "") == SOURCE_FINAL:
                from modules.workspace.models import FINAL_STATE_LABELS

                final_state, _ = self._final_state(
                    batch_status=str(item.get("status") or ""),
                    records=record_count,
                    qualified=accepted_qualified,
                    rejected=accepted_rejected,
                    remaining=int(item.get("remaining_count") or 0),
                )
                final_state_label = FINAL_STATE_LABELS.get(final_state, final_state)
            last_activity = max(
                filter(
                    None,
                    (
                        _timestamp(item),
                        _timestamp(imported),
                        _timestamp(accepted),
                        _timestamp(archive),
                        _timestamp(latest_format),
                        _timestamp(latest_translation),
                    ),
                ),
                default="",
            )
            result.append(
                {
                    "id": batch_id,
                    "display_id": item.get("display_id") or "",
                    "name": item.get("name") or "",
                    "team": team,
                    "supplier": supplier,
                    "project": project,
                    "category_l1": item.get("category_l1") or identity.get("category_l1") or "",
                    "category_l2": item.get("category_l2") or identity.get("category_l2") or "",
                    "submit_date": item.get("submit_date") or "",
                    "status": item.get("status") or "",
                    "status_label": status_label(item.get("status")),
                    "source_type": item.get("source_type") or "legacy",
                    "source_label": SOURCE_LABELS.get(item.get("source_type") or "legacy", "历史批次"),
                    "source_root_id": item.get("source_root_id") or "",
                    "source_root_path": item.get("source_root_path") or "",
                    "relative_path": item.get("relative_path") or "",
                    "project_name": item.get("project_name") or "",
                    "parent_batch_id": item.get("parent_batch_id") or "",
                    "acceptance_status": item.get("acceptance_status") or "not_started",
                    "acceptance_label": acceptance_label(item.get("acceptance_status")),
                    "acceptance_state": acceptance_state,
                    "acceptance_state_label": acceptance_state_label,
                    "format_status": item.get("format_status") or "not_checked",
                    "format_label": format_label(item.get("format_status")),
                    "format_state": format_state,
                    "format_state_label": format_state_label,
                    "translation_status": item.get("translation_status") or "not_started",
                    "translation_label": translation_label(item.get("translation_status")),
                    "translation_state": translation_state,
                    "translation_state_label": translation_state_label,
                    "final_state": final_state,
                    "final_state_label": final_state_label,
                    "task_count": task_count,
                    "image_count": int(group.get("image_count") or 0),
                    "txt_count": int(group.get("txt_count") or 0),
                    "zh_files": int(group.get("zh_files") or 0),
                    "en_files": int(group.get("en_files") or 0),
                    "qualified_count": int(item.get("qualified_count") or 0),
                    "rejected_count": int(item.get("rejected_count") or 0),
                    "remaining_count": int(item.get("remaining_count") or 0),
                    "accepted_records": record_count,
                    "accepted_qualified": accepted_qualified,
                    "accepted_rejected": accepted_rejected,
                    "accepted_other": int(accepted.get("accepted_other") or 0),
                    "last_accepted": accepted.get("last_accepted") or "",
                    "import_count": int(imported.get("import_count") or 0),
                    "imported_records": int(imported.get("imported_records") or 0),
                    "import_errors": int(imported.get("import_errors") or 0),
                    "format_runs": int(format_info.get("format_runs") or 0),
                    "format_failed": int(format_info.get("format_failed") or 0),
                    "format_warnings": int(format_info.get("format_warnings") or 0),
                    "format_checked_tasks": int(latest_format.get("scanned_tasks") or 0),
                    "format_passed_tasks": int(latest_format.get("passed_tasks") or 0),
                    "format_last_run_id": latest_format.get("id") or "",
                    "format_last_time": latest_format.get("finished_at") or latest_format.get("started_at") or "",
                    "format_template": latest_format.get("template_name") or "",
                    "translation_jobs": int(translation_info.get("translation_jobs") or 0),
                    "translated_items": int(translation_info.get("translated_items") or 0),
                    "translation_failed_items": int(translation_info.get("failed_items") or 0),
                    "translation_pending_items": int(translation_info.get("pending_items") or 0),
                    "translation_missing_zh": int(translation_info.get("missing_zh_items") or 0),
                    "translation_item_total": int(translation_info.get("item_total") or 0),
                    "translation_tokens": int(translation_info.get("token_input") or 0)
                    + int(translation_info.get("token_output") or 0),
                    "translation_cost": float(translation_info.get("cost") or 0.0),
                    "translation_model": latest_translation.get("model") or "",
                    "translation_last_job_id": latest_translation.get("id") or "",
                    "translation_last_time": latest_translation.get("finished_at")
                    or latest_translation.get("started_at")
                    or latest_translation.get("created_at")
                    or "",
                    "archived_groups": int(archive.get("archived_groups") or 0),
                    "archive_conflicts": int(archive.get("archive_conflicts") or 0),
                    "archive_failed": int(archive.get("archive_failed") or 0),
                    "last_archive": archive.get("last_archive") or "",
                    "last_activity": last_activity,
                    "data_root": item.get("data_root") or "",
                    "batch_path": item.get("batch_path") or "",
                    "identity": identity,
                }
            )
        return result

    # ── 筛选 ─────────────────────────────────────────────
    @staticmethod
    def filter_options(rows: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
        rows = list(rows)
        def values(key: str) -> list[str]:
            return sorted({str(row.get(key) or "") for row in rows if row.get(key)})
        return {
            "teams": values("team"),
            "suppliers": values("supplier"),
            "categories_l1": values("category_l1"),
            "categories_l2": values("category_l2"),
            "dates": sorted({str(row.get("submit_date") or "") for row in rows if row.get("submit_date")}, reverse=True),
            "statuses": values("status"),
        }

    @staticmethod
    def filter_rows(
        rows: Iterable[dict[str, Any]],
        *,
        team: str = "",
        supplier: str = "",
        category_l1: str = "",
        category_l2: str = "",
        date: str = "",
        status: str = "",
        keyword: str = "",
    ) -> list[dict[str, Any]]:
        needle = (keyword or "").strip().casefold()
        result: list[dict[str, Any]] = []
        for row in rows:
            if team and row.get("team") != team:
                continue
            if supplier and row.get("supplier") != supplier:
                continue
            if category_l1 and row.get("category_l1") != category_l1:
                continue
            if category_l2 and row.get("category_l2") != category_l2:
                continue
            if date and row.get("submit_date") != date:
                continue
            if status and row.get("status") != status:
                continue
            if needle:
                haystack = " ".join(
                    str(row.get(key) or "")
                    for key in ("name", "display_id", "team", "supplier", "category_l1", "category_l2", "batch_path")
                ).casefold()
                if needle not in haystack:
                    continue
            result.append(row)
        return result

    # ── 聚合 ─────────────────────────────────────────────
    @staticmethod
    def _sum(rows: Iterable[dict[str, Any]], key: str) -> int:
        return sum(int(row.get(key) or 0) for row in rows)

    def team_summary(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            buckets.setdefault(row.get("team") or "未指定团队", []).append(row)
        result = []
        for team, items in buckets.items():
            result.append(
                {
                    "team": team,
                    "batches": len(items),
                    "task_count": self._sum(items, "task_count"),
                    "qualified_count": self._sum(items, "qualified_count"),
                    "rejected_count": self._sum(items, "rejected_count"),
                    "remaining_count": self._sum(items, "remaining_count"),
                    "imported_records": self._sum(items, "imported_records"),
                    "format_failed": self._sum(items, "format_failed"),
                    "translation_jobs": self._sum(items, "translation_jobs"),
                    "categories": sorted({row.get("category_l1") or "" for row in items if row.get("category_l1")}),
                    "last_activity": max((row.get("last_activity") or "" for row in items), default=""),
                }
            )
        return sorted(result, key=lambda item: (-item["task_count"], item["team"]))

    def category_summary(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            key = (row.get("category_l1") or "未分类", row.get("category_l2") or "未分类")
            buckets.setdefault(key, []).append(row)
        result = []
        for (l1, l2), items in buckets.items():
            result.append(
                {
                    "category_l1": l1,
                    "category_l2": l2,
                    "batches": len(items),
                    "teams": sorted({row.get("team") or "" for row in items if row.get("team")}),
                    "task_count": self._sum(items, "task_count"),
                    "qualified_count": self._sum(items, "qualified_count"),
                    "rejected_count": self._sum(items, "rejected_count"),
                    "remaining_count": self._sum(items, "remaining_count"),
                    "imported_records": self._sum(items, "imported_records"),
                    "format_failed": self._sum(items, "format_failed"),
                }
            )
        return sorted(result, key=lambda item: (-item["task_count"], item["category_l1"], item["category_l2"]))

    def workbench(self, rows: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
        rows = list(rows) if rows is not None else self.batches()
        today = today_str()
        today_batches = [
            row for row in rows
            if (row.get("submit_date") or "") == today
            or (row.get("last_activity") or "").startswith(today)
        ]
        pending = [
            row for row in rows
            if row.get("acceptance_status") in {"not_started", "in_progress"}
            or int(row.get("remaining_count") or 0) > 0
        ]
        anomalies = [
            row for row in rows
            if int(row.get("format_failed") or 0) > 0 or int(row.get("import_errors") or 0) > 0
        ]
        recent_imports = self.db.connect().execute(
            """
            SELECT * FROM record_imports
            ORDER BY COALESCE(imported_at, created_at) DESC LIMIT 10
            """
        ).fetchall()
        recent_formats = self.db.connect().execute(
            """
            SELECT * FROM format_check_runs
            ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 10
            """
        ).fetchall()
        return {
            "today": today,
            "today_batches": today_batches,
            "pending": pending,
            "anomalies": anomalies,
            "recent_imports": [dict(row) for row in recent_imports],
            "recent_formats": [dict(row) for row in recent_formats],
            "totals": {
                "batches": len(rows),
                "task_count": self._sum(rows, "task_count"),
                "qualified_count": self._sum(rows, "qualified_count"),
                "rejected_count": self._sum(rows, "rejected_count"),
                "remaining_count": self._sum(rows, "remaining_count"),
                "imported_records": self._sum(rows, "imported_records"),
                "pending_batches": len(pending),
                "anomaly_batches": len(anomalies),
            },
        }

    # ── 批次详情 ─────────────────────────────────────────
    # ── 工作台：待确认候选与业务统计 ─────────────────────
    def pending_candidates(self, project_id: str = "") -> list[dict[str, Any]]:
        params: list[Any] = []
        where = [
            "c.status IN ('pending', 'partial', 'duplicate', 'rescan')",
            "c.confirmed_batch_id IS NULL",
            "r.source_type IN ('pending_acceptance', 'final_acceptance')",
        ]
        if project_id:
            where.append("r.project_root_id = ?")
            params.append(project_id)
        rows = self.db.connect().execute(
            f"""
            SELECT c.*, r.source_type, r.project_root_id, r.project_name, r.root_path,
                   r.started_at AS run_started_at, r.finished_at AS run_finished_at
            FROM scan_candidates c
            JOIN scan_runs r ON r.id = c.run_id
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(r.finished_at, r.started_at) DESC
            """,
            params,
        ).fetchall()
        existing = {
            (row["source_type"], os.path.normcase(normalize_path(row["batch_path"] or "")))
            for row in self.db.connect().execute(
                "SELECT source_type, batch_path FROM batches WHERE batch_path IS NOT NULL"
            ).fetchall()
        }
        seen: set[tuple[str, str]] = set()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            key = (
                item.get("source_type") or "",
                os.path.normcase(normalize_path(item.get("batch_path") or "")),
            )
            if key in seen or key in existing:
                continue
            seen.add(key)
            identity = _json_loads(item.get("identity_json"))
            reason = _json_loads(item.get("reason_json"))
            result.append(
                {
                    "candidate_id": item["id"],
                    "batch_path": item.get("batch_path") or "",
                    "batch_name": item.get("batch_name") or os.path.basename(item.get("batch_path") or ""),
                    "source_type": item.get("source_type") or "",
                    "source_label": SOURCE_LABELS.get(item.get("source_type") or "", ""),
                    "project_root_id": item.get("project_root_id") or "",
                    "project_name": item.get("project_name") or "",
                    "root_path": item.get("root_path") or "",
                    "identity": identity,
                    "category_l1": identity.get("category_l1") or "",
                    "category_l2": identity.get("category_l2") or "",
                    "team": identity.get("team") or "",
                    "date": identity.get("date") or "",
                    "group_count": int(item.get("group_count") or 0),
                    "file_count": int(item.get("file_count") or 0),
                    "confidence": float(item.get("confidence") or 0.0),
                    "status": item.get("status") or "",
                    "reason": reason.get("explain") or "；".join(reason.get("reasons") or []),
                    "discovered_at": item.get("run_finished_at") or item.get("run_started_at") or "",
                }
            )
        return result

    @staticmethod
    def workspace_totals(
        rows: Iterable[dict[str, Any]], candidates: Iterable[dict[str, Any]] = ()
    ) -> dict[str, Any]:
        rows = list(rows)
        candidates = list(candidates)
        pending_source = sum(1 for row in rows if row.get("source_type") == SOURCE_PENDING)
        final_source = sum(1 for row in rows if row.get("source_type") == SOURCE_FINAL)
        format_pending = sum(
            1 for row in rows if row.get("format_state") == FORMAT_STATE_NOT_CHECKED
        )
        translation_pending = sum(
            1
            for row in rows
            if row.get("translation_state")
            in {TRANSLATION_STATE_PENDING, TRANSLATION_STATE_PARTIAL, TRANSLATION_STATE_FAILED}
        )
        acceptance_pending = sum(
            1
            for row in rows
            if row.get("acceptance_state")
            in {ACCEPTANCE_STATE_PENDING, ACCEPTANCE_STATE_IN_PROGRESS, ACCEPTANCE_STATE_NO_RECORDS}
        )
        anomalies = sum(
            1
            for row in rows
            if row.get("format_state") in {FORMAT_STATE_FAILED, FORMAT_STATE_WARNING}
            or row.get("translation_state") == TRANSLATION_STATE_FAILED
            or int(row.get("import_errors") or 0) > 0
            or int(row.get("archive_conflicts") or 0) > 0
        )
        return {
            "pending_batches": pending_source,
            "final_batches": final_source,
            "format_pending": format_pending,
            "translation_pending": translation_pending,
            "acceptance_pending": acceptance_pending,
            "anomaly_batches": anomalies,
            "unconfirmed": len(candidates),
            "batches": len(rows),
            "task_count": sum(int(row.get("task_count") or 0) for row in rows),
            "image_count": sum(int(row.get("image_count") or 0) for row in rows),
        }

    def parent_suggestions(self, batch_id: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT b.id, b.display_id, b.name, b.batch_path, b.submit_date,
                   b.source_type, b.qualified_count, b.rejected_count,
                   COUNT(DISTINCT g.task_id) AS overlap
            FROM data_groups g
            JOIN batches b ON b.id = g.batch_id
            WHERE b.id <> ?
              AND b.source_type IN ('pending_acceptance', 'legacy')
              AND g.task_id IN (
                  SELECT task_id FROM data_groups WHERE batch_id = ? AND task_id <> ''
              )
            GROUP BY b.id
            ORDER BY overlap DESC, COALESCE(b.submit_date, b.created_at) DESC
            LIMIT ?
            """,
            (batch_id, batch_id, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        return next((row for row in self.batches() if row["id"] == batch_id), None)

    def batch_detail(self, batch_id: str) -> dict[str, Any] | None:
        batch = self.get_batch(batch_id)
        if batch is None:
            return None
        conn = self.db.connect()
        groups = [dict(row) for row in conn.execute(
            "SELECT * FROM data_groups WHERE batch_id = ? ORDER BY normalized_path COLLATE NOCASE",
            (batch_id,),
        ).fetchall()]
        sub_batches = [dict(row) for row in conn.execute(
            "SELECT * FROM sub_batches WHERE batch_id = ? ORDER BY name COLLATE NOCASE",
            (batch_id,),
        ).fetchall()]
        status_logs = [dict(row) for row in conn.execute(
            "SELECT * FROM batch_status_log WHERE batch_id = ? ORDER BY created_at DESC",
            (batch_id,),
        ).fetchall()]
        imports = [dict(row) for row in conn.execute(
            "SELECT * FROM record_imports WHERE batch_id = ? ORDER BY COALESCE(imported_at, created_at) DESC",
            (batch_id,),
        ).fetchall()]
        format_runs = [dict(row) for row in conn.execute(
            "SELECT * FROM format_check_runs WHERE batch_id = ? ORDER BY COALESCE(finished_at, started_at) DESC",
            (batch_id,),
        ).fetchall()]
        translation_jobs = [dict(row) for row in conn.execute(
            "SELECT * FROM translation_jobs WHERE batch_id = ? ORDER BY COALESCE(finished_at, started_at, created_at) DESC",
            (batch_id,),
        ).fetchall()]
        latest_format = format_runs[0] if format_runs else {}
        format_issues: list[dict[str, Any]] = []
        if latest_format.get("id"):
            format_issues = [dict(row) for row in conn.execute(
                "SELECT * FROM format_check_issues WHERE run_id = ? ORDER BY code, task_id",
                (latest_format["id"],),
            ).fetchall()]
        latest_translation = translation_jobs[0] if translation_jobs else {}
        translation_items: list[dict[str, Any]] = []
        translation_counts: dict[str, int] = {}
        if latest_translation.get("id"):
            translation_items = [dict(row) for row in conn.execute(
                "SELECT * FROM translation_items WHERE job_id = ? ORDER BY task_id",
                (latest_translation["id"],),
            ).fetchall()]
            for item in translation_items:
                status = str(item.get("status") or "")
                translation_counts[status] = translation_counts.get(status, 0) + 1
        acceptance_records = [dict(row) for row in conn.execute(
            """
            SELECT ir.*, ri.source_path AS import_source, ri.imported_at AS import_time
            FROM imported_records ir
            LEFT JOIN record_imports ri ON ri.id = ir.import_id
            LEFT JOIN data_groups g ON g.id = ir.data_group_id
            WHERE COALESCE(ri.batch_id, g.batch_id) = ?
            ORDER BY COALESCE(ir.record_time, ir.created_at) DESC
            LIMIT 500
            """,
            (batch_id,),
        ).fetchall()]
        archive_items = [dict(row) for row in conn.execute(
            """
            SELECT i.*, j.status AS job_status, j.output_root, j.finished_at AS job_finished_at
            FROM archive_items i JOIN archive_jobs j ON j.id = i.job_id
            WHERE j.batch_id = ? ORDER BY i.updated_at DESC LIMIT 500
            """,
            (batch_id,),
        ).fetchall()]
        parent_batch = None
        if batch.get("parent_batch_id"):
            row = conn.execute(
                "SELECT id, display_id, name, batch_path, submit_date, source_type FROM batches WHERE id = ?",
                (batch["parent_batch_id"],),
            ).fetchone()
            parent_batch = dict(row) if row else None
        children = [dict(row) for row in conn.execute(
            "SELECT id, display_id, name, batch_path, submit_date FROM batches WHERE parent_batch_id = ? ORDER BY created_at",
            (batch_id,),
        ).fetchall()]
        return {
            "batch": batch,
            "groups": groups,
            "sub_batches": sub_batches,
            "status_logs": status_logs,
            "imports": imports,
            "format_runs": format_runs,
            "translation_jobs": translation_jobs,
            "latest_format": latest_format,
            "format_issues": format_issues,
            "latest_translation": latest_translation,
            "translation_items": translation_items,
            "translation_counts": translation_counts,
            "acceptance_records": acceptance_records,
            "archive_items": archive_items,
            "parent_batch": parent_batch,
            "child_batches": children,
            "parent_suggestions": self.parent_suggestions(batch_id),
        }
