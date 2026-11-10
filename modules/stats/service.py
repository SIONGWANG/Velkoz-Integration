"""统计与报表服务（v1.25.7）。

只读查询管理数据库；唯一写入是可按源表重建的 `stats_daily` 缓存，
以及用户主动导出的 Excel 报表（写入管理数据目录 reports/stats）。
不访问、不修改任何源图片/TXT/CSV。
"""
from __future__ import annotations

import datetime as _dt
import itertools
import json
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator

from modules.common.path_guard import normalize_path
from modules.common.timeutil import now_iso, today_str
from modules.config import app_config
from modules.database.connection import Database, open_database
from modules.format_checker.errors import ERROR_CATALOG

from .models import (
    BATCH_STATS_COLUMNS,
    DAILY_STATS_COLUMNS,
    ERROR_STATS_COLUMNS,
    METRIC_SOURCES,
    OPERATOR_STATS_COLUMNS,
    RESULT_QUALIFIED,
    RESULT_REJECTED,
    SEVERITY_LABELS,
    TAG_STATS_COLUMNS,
    TRACE_ISSUE_COLUMNS,
    TRACE_RECORD_COLUMNS,
    WORKLOAD_IMAGES,
    WORKLOAD_MIXED,
    WORKLOAD_RECORDS,
    StatsFilters,
)

_RECORD_BASE = """
WITH base AS (
    SELECT ir.id AS record_id,
           COALESCE(NULLIF(ir.operator, ''), '未指定') AS operator,
           COALESCE(NULLIF(ir.record_time, ''), ir.created_at, '') AS record_ts,
           COALESCE(ri.batch_id, g.batch_id) AS batch_id,
           b.name AS batch_name,
           ir.data_group_id,
           ir.task_id,
           ir.result,
           ir.tags_raw,
           ir.note,
           ir.source_row,
           ri.source_path
    FROM imported_records ir
    LEFT JOIN record_imports ri ON ri.id = ir.import_id
    LEFT JOIN data_groups g ON g.id = ir.data_group_id
    LEFT JOIN batches b ON b.id = COALESCE(ri.batch_id, g.batch_id)
)
"""


def _identity(text: str | None) -> dict[str, str]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): str(val) for key, val in value.items() if val is not None}


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _split_tags(raw: str) -> list[str]:
    """拆分历史标签原文；保留原文，未知标签也统计。"""
    text = str(raw or "")
    for separator in ("；", "，", "、", "|", "/", "\n", "\r", "\t"):
        text = text.replace(separator, ";")
    tags: list[str] = []
    for part in text.split(";"):
        for piece in part.split(","):
            tag = piece.strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def _scope_filters(filters: StatsFilters) -> StatsFilters:
    """日报/批次内指标不使用日期范围（日期由调用方显式指定）。"""
    return StatsFilters(
        date_from="",
        date_to="",
        batch_id=filters.batch_id,
        team=filters.team,
        operator=filters.operator,
    )


def _excel_text(value: Any) -> str:
    """Excel 单元格上限 32767 字符，超长时截断，避免导出失败。"""
    text = "" if value is None else str(value)
    return text[:32000]


class StatsService:
    def __init__(
        self,
        db_path: str | Path | None = None,
        reports_dir: str | Path | None = None,
    ):
        self.db_path = Path(db_path) if db_path else app_config.get_db_path()
        self.db: Database = open_database(self.db_path)
        if reports_dir:
            self.reports_dir = Path(normalize_path(reports_dir))
        elif db_path:
            self.reports_dir = self.db_path.resolve().parent.parent / "reports" / "stats"
        else:
            self.reports_dir = app_config.get_reports_dir() / "stats"

    def close(self) -> None:
        self.db.close()

    # ── 筛选与选项 ────────────────────────────────────────
    @staticmethod
    def _batch_where(alias: str, filters: StatsFilters, params: list[Any]) -> str:
        clauses: list[str] = []
        if filters.batch_id:
            clauses.append(f"{alias}.id = ?")
            params.append(filters.batch_id)
        if filters.date_from:
            clauses.append(
                f"substr(COALESCE({alias}.submit_date, {alias}.created_at, ''), 1, 10) >= ?"
            )
            params.append(filters.date_from)
        if filters.date_to:
            clauses.append(
                f"substr(COALESCE({alias}.submit_date, {alias}.created_at, ''), 1, 10) <= ?"
            )
            params.append(filters.date_to)
        if filters.team:
            clauses.append(
                f"EXISTS (SELECT 1 FROM data_groups tg WHERE tg.batch_id = {alias}.id"
                " AND tg.user_name = ?)"
            )
            params.append(filters.team)
        if filters.operator:
            clauses.append(
                f"EXISTS (SELECT 1 FROM imported_records ir"
                " LEFT JOIN record_imports ri ON ri.id = ir.import_id"
                " LEFT JOIN data_groups g2 ON g2.id = ir.data_group_id"
                f" WHERE ir.operator = ? AND COALESCE(ri.batch_id, g2.batch_id) = {alias}.id)"
            )
            params.append(filters.operator)
        return " AND ".join(clauses) if clauses else "1=1"

    @staticmethod
    def _record_where(filters: StatsFilters, params: list[Any], alias: str = "base") -> str:
        clauses: list[str] = []
        if filters.date_from:
            clauses.append(f"substr({alias}.record_ts, 1, 10) >= ?")
            params.append(filters.date_from)
        if filters.date_to:
            clauses.append(f"substr({alias}.record_ts, 1, 10) <= ?")
            params.append(filters.date_to)
        if filters.batch_id:
            clauses.append(f"{alias}.batch_id = ?")
            params.append(filters.batch_id)
        if filters.operator:
            clauses.append(f"{alias}.operator = ?")
            params.append(filters.operator)
        if filters.team:
            clauses.append(
                f"{alias}.batch_id IN (SELECT batch_id FROM data_groups WHERE user_name = ?)"
            )
            params.append(filters.team)
        return " AND ".join(clauses) if clauses else "1=1"

    def list_batches(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT id, display_id, name, submit_date, category_l1, category_l2, status"
            " FROM batches ORDER BY COALESCE(submit_date, created_at) DESC, created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_teams(self) -> list[str]:
        teams = {
            str(row["user_name"]).strip()
            for row in self.db.connect().execute(
                "SELECT DISTINCT user_name FROM data_groups WHERE user_name IS NOT NULL AND user_name <> ''"
            ).fetchall()
            if str(row["user_name"]).strip()
        }
        for row in self.db.connect().execute("SELECT name, identity_json FROM batches").fetchall():
            identity = _identity(row["identity_json"])
            team = _first_text(identity.get("team"))
            if team:
                teams.add(team)
        return sorted(teams)

    def list_operators(self) -> list[str]:
        rows = self.db.connect().execute(
            "SELECT DISTINCT operator FROM imported_records"
            " WHERE operator IS NOT NULL AND operator <> '' ORDER BY operator"
        ).fetchall()
        return [str(row["operator"]).strip() for row in rows if str(row["operator"]).strip()]

    # ── 批次统计 ─────────────────────────────────────────
    def _group_stats(self, filters: StatsFilters) -> dict[str, dict[str, Any]]:
        params: list[Any] = []
        where = self._batch_where("b", filters, params)
        rows = self.db.connect().execute(
            f"""
            SELECT g.batch_id,
                   COUNT(*) AS task_count,
                   COALESCE(SUM(g.image_count), 0) AS image_count,
                   COALESCE(SUM(g.file_count), 0) AS file_count,
                   GROUP_CONCAT(DISTINCT NULLIF(g.user_name, '')) AS user_names
            FROM data_groups g
            JOIN batches b ON b.id = g.batch_id
            WHERE {where}
            GROUP BY g.batch_id
            """,
            params,
        ).fetchall()
        return {
            row["batch_id"]: {
                "task_count": _as_int(row["task_count"]),
                "image_count": _as_int(row["image_count"]),
                "file_count": _as_int(row["file_count"]),
                "user_names": str(row["user_names"] or ""),
            }
            for row in rows
        }

    def _latest_format(self) -> dict[str, dict[str, Any]]:
        rows = self.db.connect().execute(
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
        return {row["batch_id"]: dict(row) for row in rows}

    def _latest_translation(self) -> dict[str, dict[str, Any]]:
        rows = self.db.connect().execute(
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
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            detail = dict(row)
            items = self.db.connect().execute(
                """
                SELECT COALESCE(SUM(status = 'translated'), 0) AS translated,
                       COALESCE(SUM(status = 'failed'), 0) AS failed,
                       COUNT(*) AS total
                FROM translation_items WHERE job_id = ?
                """,
                (row["id"],),
            ).fetchone()
            detail["translated_items"] = _as_int(items["translated"])
            detail["failed_items"] = _as_int(items["failed"])
            detail["item_count"] = _as_int(items["total"])
            result[row["batch_id"]] = detail
        return result

    def _import_stats(self, filters: StatsFilters) -> dict[str, dict[str, Any]]:
        params: list[Any] = [*RESULT_QUALIFIED, *RESULT_REJECTED]
        where = self._record_where(_scope_filters(filters), params)
        rows = self.db.connect().execute(
            f"""{_RECORD_BASE}
            SELECT batch_id,
                   COUNT(*) AS records,
                   SUM(CASE WHEN result IN (?, ?) THEN 1 ELSE 0 END) AS qualified,
                   SUM(CASE WHEN result IN (?, ?) THEN 1 ELSE 0 END) AS rejected,
                   MAX(record_ts) AS last_record
            FROM base
            WHERE {where}
            GROUP BY batch_id
            """,
            params,
        ).fetchall()
        return {row["batch_id"]: dict(row) for row in rows if row["batch_id"]}

    def _archive_stats(self) -> dict[str, dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT j.batch_id,
                   COALESCE(SUM(i.status IN ('verified', 'skipped')), 0) AS archived
            FROM archive_items i
            JOIN archive_jobs j ON j.id = i.job_id
            WHERE j.batch_id IS NOT NULL
            GROUP BY j.batch_id
            """
        ).fetchall()
        return {row["batch_id"]: dict(row) for row in rows}

    def batch_stats(self, filters: StatsFilters | None = None) -> list[dict[str, Any]]:
        filters = (filters or StatsFilters()).normalized()
        params: list[Any] = []
        where = self._batch_where("b", filters, params)
        rows = self.db.connect().execute(
            f"SELECT b.* FROM batches b WHERE {where}"
            " ORDER BY COALESCE(b.submit_date, b.created_at) DESC, b.created_at DESC",
            params,
        ).fetchall()
        groups = self._group_stats(filters)
        formats = self._latest_format()
        translations = self._latest_translation()
        imports = self._import_stats(filters)
        archives = self._archive_stats()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            identity = _identity(item.get("identity_json"))
            group = groups.get(item["id"], {})
            imported = imports.get(item["id"], {})
            format_run = formats.get(item["id"], {})
            translation = translations.get(item["id"], {})
            archive = archives.get(item["id"], {})
            team = _first_text(
                identity.get("team"),
                item.get("team_id"),
                (group.get("user_names") or "").split(",")[0],
            )
            supplier = _first_text(identity.get("supplier"), item.get("supplier_id"))
            last_activity = max(
                filter(
                    None,
                    (
                        str(item.get("updated_at") or ""),
                        str(format_run.get("finished_at") or format_run.get("started_at") or ""),
                        str(
                            translation.get("finished_at")
                            or translation.get("started_at")
                            or translation.get("created_at")
                            or ""
                        ),
                        str(imported.get("last_record") or ""),
                    ),
                ),
                default="",
            )
            result.append(
                {
                    "id": item["id"],
                    "display_id": item.get("display_id") or item.get("id") or "",
                    "name": item.get("name") or "",
                    "team": team,
                    "supplier": supplier,
                    "submit_date": item.get("submit_date") or "",
                    "category_l1": item.get("category_l1") or identity.get("category_l1") or "",
                    "category_l2": item.get("category_l2") or identity.get("category_l2") or "",
                    "status": item.get("status") or "",
                    "task_count": int(group.get("task_count") or item.get("data_count") or 0),
                    "image_count": int(group.get("image_count") or 0),
                    "format_checked": int(format_run.get("scanned_tasks") or 0),
                    "format_failed": int(format_run.get("failed_tasks") or 0),
                    "format_warnings": int(format_run.get("warning_tasks") or 0),
                    "format_status": item.get("format_status") or "not_checked",
                    "translated": int(translation.get("translated_items") or 0),
                    "translation_failed": int(translation.get("failed_items") or 0),
                    "translation_status": item.get("translation_status") or "not_started",
                    "batch_qualified": int(item.get("qualified_count") or 0),
                    "batch_rejected": int(item.get("rejected_count") or 0),
                    "batch_remaining": int(item.get("remaining_count") or 0),
                    "imported_records": int(imported.get("records") or 0),
                    "imported_qualified": int(imported.get("qualified") or 0),
                    "imported_rejected": int(imported.get("rejected") or 0),
                    "archived": int(archive.get("archived") or 0),
                    "last_activity": last_activity,
                }
            )
        return result

    def _overview_from_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        def total(key: str) -> int:
            return sum(_as_int(row.get(key)) for row in rows)

        return {
            "batches": len(rows),
            "task_count": total("task_count"),
            "image_count": total("image_count"),
            "format_checked": total("format_checked"),
            "format_failed": total("format_failed"),
            "format_warnings": total("format_warnings"),
            "translated": total("translated"),
            "translation_failed": total("translation_failed"),
            "imported_records": total("imported_records"),
            "imported_qualified": total("imported_qualified"),
            "imported_rejected": total("imported_rejected"),
            "batch_qualified": total("batch_qualified"),
            "batch_rejected": total("batch_rejected"),
            "batch_remaining": total("batch_remaining"),
            "archived": total("archived"),
        }

    def batch_stats_and_overview(
        self, filters: StatsFilters | None = None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """一次批次查询同时返回总览与批次明细，避免页面重复查询。"""
        rows = self.batch_stats(filters)
        return self._overview_from_rows(rows), rows

    def overview(self, filters: StatsFilters | None = None) -> dict[str, Any]:
        return self._overview_from_rows(self.batch_stats(filters))

    # ── 人员统计 ─────────────────────────────────────────
    def operator_stats(
        self,
        filters: StatsFilters | None = None,
        workload_mode: str = WORKLOAD_RECORDS,
    ) -> list[dict[str, Any]]:
        filters = (filters or StatsFilters()).normalized()
        where_params: list[Any] = []
        where = self._record_where(filters, where_params)
        rows = self.db.connect().execute(
            f"""{_RECORD_BASE}
            SELECT operator,
                   substr(record_ts, 1, 10) AS date,
                   batch_id,
                   MAX(batch_name) AS batch_name,
                   COUNT(*) AS records,
                   SUM(CASE WHEN result IN (?, ?) THEN 1 ELSE 0 END) AS qualified,
                   SUM(CASE WHEN result IN (?, ?) THEN 1 ELSE 0 END) AS rejected,
                   SUM(CASE WHEN result IS NULL OR result = ''
                             OR result NOT IN (?, ?, ?, ?) THEN 1 ELSE 0 END) AS other,
                   MAX(record_ts) AS last_record
            FROM base
            WHERE {where}
            GROUP BY operator, substr(record_ts, 1, 10), batch_id
            ORDER BY date DESC, operator COLLATE NOCASE
            """,
            [
                *RESULT_QUALIFIED,
                *RESULT_REJECTED,
                *RESULT_QUALIFIED,
                *RESULT_REJECTED,
                *where_params,
            ],
        ).fetchall()

        scope_params: list[Any] = []
        scope_where = self._record_where(filters, scope_params)
        scope_rows = self.db.connect().execute(
            f"""{_RECORD_BASE},
            scope AS (
                SELECT DISTINCT operator,
                       substr(record_ts, 1, 10) AS date,
                       batch_id,
                       data_group_id
                FROM base
                WHERE {scope_where} AND data_group_id IS NOT NULL
            )
            SELECT scope.operator, scope.date, scope.batch_id,
                   COUNT(*) AS groups,
                   COALESCE(SUM(g.image_count), 0) AS images
            FROM scope
            LEFT JOIN data_groups g ON g.id = scope.data_group_id
            GROUP BY scope.operator, scope.date, scope.batch_id
            """,
            scope_params,
        ).fetchall()
        scope = {
            (row["operator"], row["date"], row["batch_id"]): {
                "groups": _as_int(row["groups"]),
                "images": _as_int(row["images"]),
            }
            for row in scope_rows
        }
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            key = (row["operator"], row["date"], row["batch_id"])
            extra = scope.get(key, {})
            qualified = _as_int(item.get("qualified"))
            rejected = _as_int(item.get("rejected"))
            records = _as_int(item.get("records"))
            images = _as_int(extra.get("images"))
            if workload_mode == WORKLOAD_IMAGES:
                workload = float(images)
            elif workload_mode == WORKLOAD_MIXED:
                workload = records + images * 0.05
            else:
                workload = float(records)
            denominator = qualified + rejected
            item.update(
                {
                    "qualified": qualified,
                    "rejected": rejected,
                    "other": _as_int(item.get("other")),
                    "records": records,
                    "groups": _as_int(extra.get("groups")),
                    "images": images,
                    "qualified_rate": (qualified / denominator) if denominator else 0.0,
                    "workload": round(workload, 2),
                }
            )
            result.append(item)
        return result

    # ── 错误类型 / 标签 ──────────────────────────────────
    def error_type_stats(self, filters: StatsFilters | None = None) -> list[dict[str, Any]]:
        filters = (filters or StatsFilters()).normalized()
        params: list[Any] = []
        where = self._batch_where("b", _scope_filters(filters), params)
        date_clauses: list[str] = []
        if filters.date_from:
            date_clauses.append(
                "substr(COALESCE(r.finished_at, r.started_at, ''), 1, 10) >= ?"
            )
            params.append(filters.date_from)
        if filters.date_to:
            date_clauses.append(
                "substr(COALESCE(r.finished_at, r.started_at, ''), 1, 10) <= ?"
            )
            params.append(filters.date_to)
        date_sql = (" AND " + " AND ".join(date_clauses)) if date_clauses else ""
        rows = self.db.connect().execute(
            f"""
            SELECT i.code, i.severity, COUNT(*) AS count,
                   COUNT(DISTINCT i.task_id) AS tasks,
                   COUNT(DISTINCT r.batch_id) AS batches,
                   MIN(r.started_at) AS first_seen,
                   MAX(r.started_at) AS last_seen
            FROM format_check_issues i
            JOIN format_check_runs r ON r.id = i.run_id
            LEFT JOIN batches b ON b.id = r.batch_id
            WHERE {where}{date_sql}
            GROUP BY i.code, i.severity
            ORDER BY count DESC, i.code
            """,
            params,
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            code = row["code"]
            catalog = ERROR_CATALOG.get(code, ("", code, ""))
            result.append(
                {
                    "code": code,
                    "severity": row["severity"] or catalog[0],
                    "severity_label": SEVERITY_LABELS.get(
                        row["severity"] or catalog[0], row["severity"] or ""
                    ),
                    "message": catalog[1],
                    "count": _as_int(row["count"]),
                    "tasks": _as_int(row["tasks"]),
                    "batches": _as_int(row["batches"]),
                    "first_seen": row["first_seen"] or "",
                    "last_seen": row["last_seen"] or "",
                }
            )
        return result

    def tag_stats(self, filters: StatsFilters | None = None) -> list[dict[str, Any]]:
        filters = (filters or StatsFilters()).normalized()
        params: list[Any] = []
        where = self._record_where(filters, params)
        buckets: dict[str, dict[str, Any]] = {}
        last_id = ""
        while True:
            rows = self.db.connect().execute(
                f"""{_RECORD_BASE}
                SELECT record_id, tags_raw, result, batch_id, batch_name, operator
                FROM base
                WHERE {where} AND tags_raw IS NOT NULL AND tags_raw <> ''
                  AND record_id > ?
                ORDER BY record_id
                LIMIT 5000
                """,
                [*params, last_id],
            ).fetchall()
            if not rows:
                break
            for row in rows:
                raw = str(row["tags_raw"] or "")
                for tag in _split_tags(raw):
                    item = buckets.setdefault(
                        tag,
                        {"tag": tag, "count": 0, "batches": set(), "operators": set(),
                         "qualified": 0, "rejected": 0},
                    )
                    item["count"] += 1
                    if row["batch_id"]:
                        item["batches"].add(row["batch_id"])
                    if row["operator"]:
                        item["operators"].add(row["operator"])
                    if row["result"] in RESULT_QUALIFIED:
                        item["qualified"] += 1
                    elif row["result"] in RESULT_REJECTED:
                        item["rejected"] += 1
            last_id = str(rows[-1]["record_id"])
            if len(rows) < 5000:
                break
        result = []
        for item in buckets.values():
            result.append(
                {
                    "tag": item["tag"],
                    "count": item["count"],
                    "batches": len(item["batches"]),
                    "operators": "、".join(sorted(item["operators"])[:8]),
                    "qualified": item["qualified"],
                    "rejected": item["rejected"],
                }
            )
        return sorted(result, key=lambda row: (-row["count"], row["tag"]))

    # ── 日报 / 缓存 ──────────────────────────────────────
    def daily_report(self, date: str, filters: StatsFilters | None = None) -> dict[str, Any]:
        filters = _scope_filters((filters or StatsFilters()).normalized())
        day = (date or today_str())[:10]
        conn = self.db.connect()

        params: list[Any] = []
        where = self._batch_where("b", filters, params)
        new_batches = conn.execute(
            f"SELECT COUNT(*) AS c FROM batches b WHERE {where}"
            " AND substr(COALESCE(b.created_at, b.submit_date, ''), 1, 10) = ?",
            [*params, day],
        ).fetchone()["c"]

        params = []
        where = self._batch_where("b", filters, params)
        new_groups = conn.execute(
            f"""SELECT COUNT(*) AS c FROM data_groups g JOIN batches b ON b.id = g.batch_id
                WHERE {where} AND substr(COALESCE(g.first_seen_at, ''), 1, 10) = ?""",
            [*params, day],
        ).fetchone()["c"]

        params = []
        where = self._batch_where("b", filters, params)
        format_row = conn.execute(
            f"""SELECT COALESCE(SUM(r.scanned_tasks), 0) AS checked,
                       COALESCE(SUM(r.failed_tasks), 0) AS failed,
                       COALESCE(SUM(r.warning_tasks), 0) AS warnings
                FROM format_check_runs r LEFT JOIN batches b ON b.id = r.batch_id
                WHERE {where}
                  AND substr(COALESCE(r.finished_at, r.started_at, ''), 1, 10) = ?""",
            [*params, day],
        ).fetchone()

        params = []
        where = self._batch_where("b", filters, params)
        translation_row = conn.execute(
            f"""SELECT COALESCE(SUM(i.status = 'translated'), 0) AS translated,
                       COALESCE(SUM(i.status = 'failed'), 0) AS failed
                FROM translation_items i
                JOIN translation_jobs j ON j.id = i.job_id
                LEFT JOIN batches b ON b.id = j.batch_id
                WHERE {where}
                  AND substr(COALESCE(i.translated_at, j.finished_at, j.started_at, j.created_at, ''), 1, 10) = ?""",
            [*params, day],
        ).fetchone()

        record_params: list[Any] = [*RESULT_QUALIFIED, *RESULT_REJECTED]
        record_where = self._record_where(filters, record_params)
        record_row = conn.execute(
            f"""{_RECORD_BASE}
            SELECT COUNT(*) AS accepted,
                   SUM(CASE WHEN result IN (?, ?) THEN 1 ELSE 0 END) AS qualified,
                   SUM(CASE WHEN result IN (?, ?) THEN 1 ELSE 0 END) AS rejected
            FROM base
            WHERE {record_where} AND substr(record_ts, 1, 10) = ?
            """,
            [*record_params, day],
        ).fetchone()

        params = []
        where = self._batch_where("b", filters, params)
        archive_row = conn.execute(
            f"""SELECT COALESCE(SUM(i.status IN ('verified', 'skipped')), 0) AS archived
                FROM archive_items i
                JOIN archive_jobs j ON j.id = i.job_id
                LEFT JOIN batches b ON b.id = j.batch_id
                WHERE {where}
                  AND substr(COALESCE(j.finished_at, j.updated_at, ''), 1, 10) = ?""",
            [*params, day],
        ).fetchone()

        params = []
        where = self._batch_where("b", filters, params)
        pending = conn.execute(
            f"SELECT COALESCE(SUM(b.remaining_count), 0) AS c FROM batches b"
            f" WHERE {where} AND b.status <> 'archived'",
            params,
        ).fetchone()["c"]

        return {
            "date": day,
            "new_batches": _as_int(new_batches),
            "new_groups": _as_int(new_groups),
            "format_checked": _as_int(format_row["checked"]),
            "format_failed": _as_int(format_row["failed"]),
            "format_warnings": _as_int(format_row["warnings"]),
            "translated": _as_int(translation_row["translated"]),
            "translation_failed": _as_int(translation_row["failed"]),
            "accepted": _as_int(record_row["accepted"]),
            "qualified": _as_int(record_row["qualified"]),
            "rejected": _as_int(record_row["rejected"]),
            "archived": _as_int(archive_row["archived"]),
            "pending": _as_int(pending),
        }

    def daily_series(
        self,
        start: str,
        end: str,
        filters: StatsFilters | None = None,
    ) -> list[dict[str, Any]]:
        start_date = _dt.date.fromisoformat((start or today_str())[:10])
        end_date = _dt.date.fromisoformat((end or start)[:10])
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        if (end_date - start_date).days > 1000:
            raise ValueError("日报范围不能超过 1000 天")
        result: list[dict[str, Any]] = []
        current = start_date
        while current <= end_date:
            result.append(self.daily_report(current.isoformat(), filters))
            current += _dt.timedelta(days=1)
        return result

    def rebuild_daily_cache(
        self,
        start: str,
        end: str,
        filters: StatsFilters | None = None,
    ) -> int:
        rows = self.daily_series(start, end, filters)
        conn = self.db.connect()
        conn.execute(
            "DELETE FROM stats_daily WHERE date >= ? AND date <= ?"
            " AND batch_id IS NULL AND operator IS NULL",
            (start[:10], end[:10]),
        )
        now = now_iso()
        for row in rows:
            conn.execute(
                """
                INSERT INTO stats_daily(
                    id, date, batch_id, operator, new_batches, new_groups,
                    format_checked, format_failed, format_warnings, translated,
                    translation_failed, accepted, qualified, rejected, archived,
                    pending, built_at, source
                ) VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rebuild')
                """,
                (
                    f"{row['date']}||",
                    row["date"],
                    row["new_batches"],
                    row["new_groups"],
                    row["format_checked"],
                    row["format_failed"],
                    row["format_warnings"],
                    row["translated"],
                    row["translation_failed"],
                    row["accepted"],
                    row["qualified"],
                    row["rejected"],
                    row["archived"],
                    row["pending"],
                    now,
                ),
            )
        conn.commit()
        return len(rows)

    def cache_rows(self, start: str, end: str) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT * FROM stats_daily WHERE date >= ? AND date <= ?"
            " AND batch_id IS NULL AND operator IS NULL ORDER BY date",
            (start[:10], end[:10]),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── 可追溯明细 ───────────────────────────────────────
    def trace_imported_records(
        self,
        filters: StatsFilters | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        filters = (filters or StatsFilters()).normalized()
        params: list[Any] = []
        where = self._record_where(filters, params)
        params.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
        rows = self.db.connect().execute(
            f"""{_RECORD_BASE}
            SELECT record_id, substr(record_ts, 1, 10) AS date, operator, result,
                   task_id, batch_id, batch_name, data_group_id, source_path,
                   source_row, tags_raw, note, record_ts
            FROM base
            WHERE {where}
            ORDER BY record_ts DESC, record_id
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def count_imported_records(self, filters: StatsFilters | None = None) -> int:
        filters = (filters or StatsFilters()).normalized()
        params: list[Any] = []
        where = self._record_where(filters, params)
        row = self.db.connect().execute(
            f"{_RECORD_BASE} SELECT COUNT(*) AS c FROM base WHERE {where}",
            params,
        ).fetchone()
        return _as_int(row["c"])

    def trace_format_issues(
        self,
        filters: StatsFilters | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        filters = (filters or StatsFilters()).normalized()
        params: list[Any] = []
        where = self._batch_where("b", _scope_filters(filters), params)
        date_clauses: list[str] = []
        if filters.date_from:
            date_clauses.append("substr(COALESCE(r.finished_at, r.started_at, ''), 1, 10) >= ?")
            params.append(filters.date_from)
        if filters.date_to:
            date_clauses.append("substr(COALESCE(r.finished_at, r.started_at, ''), 1, 10) <= ?")
            params.append(filters.date_to)
        date_sql = (" AND " + " AND ".join(date_clauses)) if date_clauses else ""
        params.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
        rows = self.db.connect().execute(
            f"""
            SELECT i.code, i.severity, r.batch_id, b.name AS batch_name,
                   i.task_id, i.task_path, i.file_path, i.expected, i.actual,
                   i.message, r.started_at, r.finished_at
            FROM format_check_issues i
            JOIN format_check_runs r ON r.id = i.run_id
            LEFT JOIN batches b ON b.id = r.batch_id
            WHERE {where}{date_sql}
            ORDER BY COALESCE(r.finished_at, r.started_at) DESC, i.code
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if not item.get("message"):
                item["message"] = ERROR_CATALOG.get(
                    item.get("code") or "", ("", item.get("code") or "", "")
                )[1]
            result.append(item)
        return result

    def count_format_issues(self, filters: StatsFilters | None = None) -> int:
        filters = (filters or StatsFilters()).normalized()
        params: list[Any] = []
        where = self._batch_where("b", _scope_filters(filters), params)
        date_clauses: list[str] = []
        if filters.date_from:
            date_clauses.append("substr(COALESCE(r.finished_at, r.started_at, ''), 1, 10) >= ?")
            params.append(filters.date_from)
        if filters.date_to:
            date_clauses.append("substr(COALESCE(r.finished_at, r.started_at, ''), 1, 10) <= ?")
            params.append(filters.date_to)
        date_sql = (" AND " + " AND ".join(date_clauses)) if date_clauses else ""
        row = self.db.connect().execute(
            f"""SELECT COUNT(*) AS c
                FROM format_check_issues i
                JOIN format_check_runs r ON r.id = i.run_id
                LEFT JOIN batches b ON b.id = r.batch_id
                WHERE {where}{date_sql}""",
            params,
        ).fetchone()
        return _as_int(row["c"])

    def iter_imported_records(
        self,
        filters: StatsFilters | None = None,
        *,
        batch_size: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """流式读取验收明细，避免一次加载全部记录。"""
        offset = 0
        while True:
            rows = self.trace_imported_records(filters, limit=batch_size, offset=offset)
            if not rows:
                break
            for row in rows:
                yield row
            if len(rows) < batch_size:
                break
            offset += batch_size

    def iter_format_issues(
        self,
        filters: StatsFilters | None = None,
        *,
        batch_size: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            rows = self.trace_format_issues(filters, limit=batch_size, offset=offset)
            if not rows:
                break
            for row in rows:
                yield row
            if len(rows) < batch_size:
                break
            offset += batch_size

    # ── Excel 导出 ───────────────────────────────────────
    def export_excel(
        self,
        filters: StatsFilters | None = None,
        *,
        path: str | Path | None = None,
        trace_limit: int = 5000,
    ) -> Path:
        try:
            import xlsxwriter
        except ImportError as exc:  # pragma: no cover - 环境缺少依赖
            raise RuntimeError("缺少 xlsxwriter，无法导出统计 Excel") from exc

        filters = (filters or StatsFilters()).normalized()
        overview, batches = self.batch_stats_and_overview(filters)
        operators = self.operator_stats(filters)
        errors = self.error_type_stats(filters)
        tags = self.tag_stats(filters)
        daily_rows = self._export_daily(filters)
        record_total = self.count_imported_records(filters)
        issue_total = self.count_format_issues(filters)
        records = list(
            itertools.islice(self.iter_imported_records(filters), max(0, int(trace_limit)))
        )
        issues = list(
            itertools.islice(self.iter_format_issues(filters), max(0, int(trace_limit)))
        )

        if path:
            target = Path(normalize_path(path))
            target.parent.mkdir(parents=True, exist_ok=True)
        else:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            target = self.reports_dir / (
                "stats_" + _dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".xlsx"
            )
            if target.exists():
                target = target.with_name(f"stats_{uuid.uuid4().hex[:8]}.xlsx")

        workbook = xlsxwriter.Workbook(str(target))
        header = workbook.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": "#305496", "border": 1}
        )
        note = workbook.add_format({"italic": True, "font_color": "#666666"})
        cell = workbook.add_format({"border": 1})

        def write_sheet(name: str, columns: tuple[str, ...], rows: Iterable[Iterable[Any]]) -> None:
            sheet = workbook.add_worksheet(name[:31])
            materialized = [list(row) for row in rows]
            sheet.write_row(0, 0, list(columns), header)
            sheet.freeze_panes(1, 0)
            if materialized:
                sheet.autofilter(0, 0, len(materialized), len(columns) - 1)
            for index, values in enumerate(materialized, start=1):
                for col, value in enumerate(values):
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        sheet.write_number(index, col, value, cell)
                    else:
                        sheet.write_string(index, col, _excel_text(value), cell)
            for col, title in enumerate(columns):
                sheet.set_column(col, col, max(12, min(40, len(title) * 2 + 4)))

        filter_rows = [
            ["日期范围", f"{filters.date_from or '全部'} ~ {filters.date_to or '全部'}"],
            ["批次", filters.batch_id or "全部"],
            ["团队", filters.team or "全部"],
            ["人员", filters.operator or "全部"],
            ["导出时间", now_iso()],
            ["数据来源", "管理数据库；未同步数据不臆造"],
        ]
        write_sheet("筛选与总览", ("项目", "值"), filter_rows + [
            ["批次数", overview["batches"]],
            ["任务数", overview["task_count"]],
            ["图片数", overview["image_count"]],
            ["格式已检查", overview["format_checked"]],
            ["格式失败", overview["format_failed"]],
            ["翻译已翻译", overview["translated"]],
            ["翻译失败", overview["translation_failed"]],
            ["导入记录", overview["imported_records"]],
            ["导入合格", overview["imported_qualified"]],
            ["导入不合格", overview["imported_rejected"]],
            ["批次合格", overview["batch_qualified"]],
            ["批次不合格", overview["batch_rejected"]],
            ["批次剩余", overview["batch_remaining"]],
            ["归档已校验", overview["archived"]],
        ])
        write_sheet(
            "批次统计",
            BATCH_STATS_COLUMNS,
            (
                [
                    row["display_id"], row["name"], row["team"], row["supplier"],
                    row["submit_date"], row["category_l1"], row["category_l2"],
                    row["status"], row["task_count"], row["image_count"],
                    row["format_checked"], row["format_failed"], row["translated"],
                    row["translation_failed"], row["batch_qualified"],
                    row["batch_rejected"], row["batch_remaining"],
                    row["imported_records"], row["imported_qualified"],
                    row["imported_rejected"], row["archived"], row["last_activity"],
                ]
                for row in batches
            ),
        )
        write_sheet(
            "人员统计",
            OPERATOR_STATS_COLUMNS,
            (
                [
                    row["operator"], row["date"], row.get("batch_name") or row["batch_id"],
                    row["records"], row["qualified"], row["rejected"], row["other"],
                    f"{row['qualified_rate']:.2%}", row["groups"], row["images"],
                    row["workload"],
                ]
                for row in operators
            ),
        )
        write_sheet(
            "错误类型",
            ERROR_STATS_COLUMNS,
            (
                [
                    row["code"], row["message"], row["severity_label"], row["count"],
                    row["tasks"], row["batches"], row["first_seen"], row["last_seen"],
                ]
                for row in errors
            ),
        )
        write_sheet(
            "标签统计",
            TAG_STATS_COLUMNS,
            (
                [
                    row["tag"], row["count"], row["batches"], row["operators"],
                    row["qualified"], row["rejected"],
                ]
                for row in tags
            ),
        )
        write_sheet("日报", DAILY_STATS_COLUMNS, (
            [
                row["date"], row["new_batches"], row["new_groups"], row["format_checked"],
                row["format_failed"], row["format_warnings"], row["translated"],
                row["translation_failed"], row["accepted"], row["qualified"],
                row["rejected"], row["archived"], row["pending"],
            ]
            for row in daily_rows
        ))
        write_sheet("口径说明", ("指标", "源表", "说明"), (list(row) for row in METRIC_SOURCES))
        record_sheet = workbook.add_worksheet("验收明细")
        record_sheet.write_row(0, 0, list(TRACE_RECORD_COLUMNS), header)
        record_sheet.freeze_panes(1, 0)
        for index, row in enumerate(records, start=1):
            values = [
                row["record_id"], row["date"], row["operator"], row["result"],
                row["task_id"], row["batch_name"] or row["batch_id"], row["data_group_id"],
                row["source_path"], row["source_row"], row["tags_raw"], row["note"],
            ]
            record_sheet.write_row(index, 0, [_excel_text(value) for value in values], cell)
        if record_total > len(records):
            record_sheet.write_string(
                len(records) + 2,
                0,
                f"仅导出前 {len(records)} 条，共 {record_total} 条；请在页面分页查看完整明细。",
                note,
            )
        issue_sheet = workbook.add_worksheet("格式错误明细")
        issue_sheet.write_row(0, 0, list(TRACE_ISSUE_COLUMNS), header)
        issue_sheet.freeze_panes(1, 0)
        for index, row in enumerate(issues, start=1):
            values = [
                row["code"], SEVERITY_LABELS.get(row["severity"] or "", row["severity"] or ""),
                row["batch_name"] or row["batch_id"], row["task_id"], row["task_path"],
                row["file_path"], row["expected"], row["actual"], row["message"],
                row["finished_at"] or row["started_at"],
            ]
            issue_sheet.write_row(index, 0, [_excel_text(value) for value in values], cell)
        if issue_total > len(issues):
            issue_sheet.write_string(
                len(issues) + 2,
                0,
                f"仅导出前 {len(issues)} 条，共 {issue_total} 条；请在页面分页查看完整明细。",
                note,
            )
        workbook.close()
        return target

    def _export_daily(self, filters: StatsFilters) -> list[dict[str, Any]]:
        end = filters.date_to or today_str()
        start = filters.date_from or (
            _dt.date.fromisoformat(end[:10]) - _dt.timedelta(days=29)
        ).isoformat()
        try:
            return self.daily_series(start, end, filters)
        except ValueError:
            return []
