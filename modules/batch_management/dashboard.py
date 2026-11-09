"""批次工作台与团队/类别聚合（v1.25.5）。

只读查询管理数据库：批次、数据组、格式检查、翻译预审与导入记录。
不写任何表，也不访问或修改源图片/TXT/CSV。
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from modules.common.timeutil import today_str
from modules.database.connection import Database

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
        return {
            row["batch_id"]: {
                "task_count": int(row["task_count"] or 0),
                "image_count": int(row["image_count"] or 0),
                "txt_count": int(row["txt_count"] or 0),
                "user_names": str(row["user_names"] or ""),
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
                   COALESCE(SUM(failed_tasks), 0) AS format_failed,
                   COALESCE(SUM(warning_tasks), 0) AS format_warnings,
                   MAX(started_at) AS last_format
            FROM format_check_runs
            WHERE batch_id IS NOT NULL
            GROUP BY batch_id
            """
        ).fetchall()
        latest = self.db.connect().execute(
            """
            SELECT r.* FROM format_check_runs r
            JOIN (
                SELECT batch_id, MAX(COALESCE(finished_at, started_at)) AS ts
                FROM format_check_runs WHERE batch_id IS NOT NULL GROUP BY batch_id
            ) m ON m.batch_id = r.batch_id
               AND COALESCE(r.finished_at, r.started_at) = m.ts
            WHERE r.batch_id IS NOT NULL
            """
        ).fetchall()
        latest_map: dict[str, dict[str, Any]] = {}
        for row in latest:
            item = dict(row)
            latest_map.setdefault(item["batch_id"], item)
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            item["latest_run"] = latest_map.get(row["batch_id"], {})
            result[row["batch_id"]] = item
        return result

    def _translation_stats(self) -> dict[str, dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT batch_id, COUNT(*) AS translation_jobs,
                   MAX(started_at) AS last_translation
            FROM translation_jobs
            WHERE batch_id IS NOT NULL
            GROUP BY batch_id
            """
        ).fetchall()
        items = self.db.connect().execute(
            """
            SELECT j.batch_id, COUNT(i.id) AS translated_items
            FROM translation_jobs j
            LEFT JOIN translation_items i ON i.job_id = j.id
            WHERE j.batch_id IS NOT NULL
            GROUP BY j.batch_id
            """
        ).fetchall()
        item_map = {row["batch_id"]: int(row["translated_items"] or 0) for row in items}
        latest = self.db.connect().execute(
            """
            SELECT j.* FROM translation_jobs j
            JOIN (
                SELECT batch_id, MAX(COALESCE(finished_at, started_at, created_at)) AS ts
                FROM translation_jobs WHERE batch_id IS NOT NULL GROUP BY batch_id
            ) m ON m.batch_id = j.batch_id
               AND COALESCE(j.finished_at, j.started_at, j.created_at) = m.ts
            WHERE j.batch_id IS NOT NULL
            """
        ).fetchall()
        latest_map: dict[str, dict[str, Any]] = {}
        for row in latest:
            item = dict(row)
            latest_map.setdefault(item["batch_id"], item)
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            item["translated_items"] = item_map.get(row["batch_id"], 0)
            item["latest_job"] = latest_map.get(row["batch_id"], {})
            result[row["batch_id"]] = item
        return result

    def batches(self) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT * FROM batches ORDER BY COALESCE(submit_date, created_at) DESC, created_at DESC"
        ).fetchall()
        groups = self._group_stats()
        imports = self._import_stats()
        formats = self._format_stats()
        translations = self._translation_stats()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            batch_id = item["id"]
            identity = _json_loads(item.get("identity_json"))
            group = groups.get(batch_id, {})
            imported = imports.get(batch_id, {})
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
            project = _first_text(identity.get("project"), item.get("project_id"))
            last_activity = max(
                filter(
                    None,
                    (
                        _timestamp(item),
                        _timestamp(imported),
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
                    "acceptance_status": item.get("acceptance_status") or "not_started",
                    "acceptance_label": acceptance_label(item.get("acceptance_status")),
                    "format_status": item.get("format_status") or "not_checked",
                    "format_label": format_label(item.get("format_status")),
                    "translation_status": item.get("translation_status") or "not_started",
                    "translation_label": translation_label(item.get("translation_status")),
                    "task_count": int(group.get("task_count") or item.get("data_count") or 0),
                    "image_count": int(group.get("image_count") or 0),
                    "txt_count": int(group.get("txt_count") or 0),
                    "qualified_count": int(item.get("qualified_count") or 0),
                    "rejected_count": int(item.get("rejected_count") or 0),
                    "remaining_count": int(item.get("remaining_count") or 0),
                    "import_count": int(imported.get("import_count") or 0),
                    "imported_records": int(imported.get("imported_records") or 0),
                    "import_errors": int(imported.get("import_errors") or 0),
                    "format_runs": int(format_info.get("format_runs") or 0),
                    "format_failed": int(format_info.get("format_failed") or 0),
                    "format_warnings": int(format_info.get("format_warnings") or 0),
                    "translation_jobs": int(translation_info.get("translation_jobs") or 0),
                    "translated_items": int(translation_info.get("translated_items") or 0),
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
        return {
            "batch": batch,
            "groups": groups,
            "sub_batches": sub_batches,
            "status_logs": status_logs,
            "imports": imports,
            "format_runs": format_runs,
            "translation_jobs": translation_jobs,
        }
