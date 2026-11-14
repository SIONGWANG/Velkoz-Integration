"""管理中心目录索引服务（v1.25.11）。

只读写管理数据库，不修改源图片/TXT/CSV。
新工作台使用 catalog_* 表；旧 batches/data_groups 继续供正式质检使用。
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from modules.common.timeutil import now_iso
from modules.database.connection import Database
from modules.database.repositories.precheck_repo import get_setting, set_setting
from modules.workspace.models import SOURCE_FINAL, SOURCE_PENDING
from modules.workspace.path_schema import BatchInfo, detect_batches

IGNORE_KEY = "catalog_ignore_patterns"

# 02-待验数据 里回流的“合格数据”导出目录不是待验批次，自动忽略。
# 03-申请终验 本身就是合格数据批次，不能套用这条规则。
PENDING_IGNORE_KEYWORDS = ("合格数据",)

MAIN_NEW = "new"
MAIN_COMPLETED = "completed"
MAIN_LABELS = {MAIN_NEW: "新提交", MAIN_COMPLETED: "已完成"}

FORMAT_NOT_CHECKED = "not_checked"
FORMAT_CHECKED = "checked"
FORMAT_FAILED = "failed"
FORMAT_LABELS = {
    FORMAT_NOT_CHECKED: "未检查",
    FORMAT_CHECKED: "已检查",
    FORMAT_FAILED: "检查失败",
}

TRANSLATION_NOT_TRANSLATED = "not_translated"
TRANSLATION_TRANSLATED = "translated"
TRANSLATION_FAILED = "translation_failed"
TRANSLATION_LABELS = {
    TRANSLATION_NOT_TRANSLATED: "未翻译",
    TRANSLATION_TRANSLATED: "已翻译",
    TRANSLATION_FAILED: "翻译失败",
}

QUEUE_QUEUED = "queued"
QUEUE_RUNNING = "running"
QUEUE_DONE = "done"
QUEUE_FAILED = "failed"
QUEUE_SKIPPED = "skipped"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def default_ignore_patterns(source_type: str) -> tuple[str, ...]:
    """内置忽略规则：只对 02-待验数据 生效。"""
    if str(source_type) == SOURCE_PENDING:
        return PENDING_IGNORE_KEYWORDS
    return ()


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "是"}
    return bool(value)


def _split_aliases(value: Any) -> list[str]:
    text = str(value or "").replace("，", ",").replace(";", ",").replace("；", ",")
    return [item.strip() for item in text.split(",") if item.strip()]


@dataclass
class SyncResult:
    root: str
    batches: int = 0
    created: int = 0
    updated: int = 0
    data_groups: int = 0
    conflicts: int = 0
    warnings: list[str] = field(default_factory=list)


class CatalogService:
    """团队/验收员/类目标签、目录同步、扫描队列与状态。"""

    def __init__(self, db: Database):
        self.db = db

    # ── 标签 ──────────────────────────────────────────────

    def _tags(self, table: str) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            f"SELECT id, name, aliases, enabled FROM {table} ORDER BY name"
        ).fetchall()
        return [dict(row) for row in rows]

    def teams(self) -> list[dict[str, Any]]:
        return self._tags("catalog_teams")

    def reviewers(self) -> list[dict[str, Any]]:
        return self._tags("catalog_reviewers")

    def categories(self) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT id, parent_id, level, name, enabled, sort_order, image_count, rule_set_id
            FROM catalog_categories ORDER BY level, sort_order, name
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def _tag_maps(self, rows: Iterable[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
        """返回 (名称/别名 → id, 名称/别名 → 显示名)。"""
        ids: dict[str, str] = {}
        canonical: dict[str, str] = {}
        for row in rows:
            if not row.get("enabled"):
                continue
            name = str(row["name"])
            ids[name] = str(row["id"])
            canonical[name] = name
            for alias in _split_aliases(row.get("aliases")):
                ids.setdefault(alias, str(row["id"]))
                canonical[alias] = name
        return ids, canonical

    def save_tag(self, table: str, name: str, *, aliases: str = "", enabled: bool = True) -> str:
        if table not in {"catalog_teams", "catalog_reviewers"}:
            raise ValueError("未知标签表")
        value = (name or "").strip()
        if not value:
            raise ValueError("名称不能为空")
        conn = self.db.connect()
        row = conn.execute(f"SELECT id FROM {table} WHERE name = ?", (value,)).fetchone()
        if row:
            conn.execute(
                f"UPDATE {table} SET aliases = ?, enabled = ?, updated_at = ? WHERE id = ?",
                (aliases.strip(), 1 if enabled else 0, now_iso(), row["id"]),
            )
            conn.commit()
            return str(row["id"])
        tag_id = _new_id("team" if table == "catalog_teams" else "reviewer")
        conn.execute(
            f"INSERT INTO {table}(id, name, aliases, enabled, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (tag_id, value, aliases.strip(), 1 if enabled else 0, now_iso(), now_iso()),
        )
        conn.commit()
        return tag_id

    def delete_tag(self, table: str, tag_id: str) -> None:
        if table not in {"catalog_teams", "catalog_reviewers"}:
            raise ValueError("未知标签表")
        conn = self.db.connect()
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (tag_id,))
        conn.commit()

    def save_category(
        self,
        name: str,
        level: int,
        *,
        parent_id: str = "",
        image_count: int = 0,
        rule_set_id: str = "",
        enabled: bool = True,
        category_id: str = "",
    ) -> str:
        value = (name or "").strip()
        if not value:
            raise ValueError("类目名称不能为空")
        level = int(level)
        if level not in (1, 2, 3):
            raise ValueError("类目层级只能是 1/2/3")
        conn = self.db.connect()
        parent = (parent_id or "").strip()
        if category_id:
            conn.execute(
                """
                UPDATE catalog_categories
                SET parent_id = ?, level = ?, name = ?, image_count = ?,
                    rule_set_id = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    parent,
                    level,
                    value,
                    max(0, int(image_count)),
                    rule_set_id.strip(),
                    1 if enabled else 0,
                    now_iso(),
                    category_id,
                ),
            )
            conn.commit()
            return category_id
        row = conn.execute(
            "SELECT id FROM catalog_categories WHERE COALESCE(parent_id, '') = ? AND name = ?",
            (parent, value),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE catalog_categories
                SET level = ?, image_count = ?, rule_set_id = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    level,
                    max(0, int(image_count)),
                    rule_set_id.strip(),
                    1 if enabled else 0,
                    now_iso(),
                    row["id"],
                ),
            )
            conn.commit()
            return str(row["id"])
        category_id = _new_id("cat")
        conn.execute(
            """
            INSERT INTO catalog_categories(
                id, parent_id, level, name, enabled, sort_order, image_count,
                rule_set_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                category_id,
                parent,
                level,
                value,
                1 if enabled else 0,
                max(0, int(image_count)),
                rule_set_id.strip(),
                now_iso(),
                now_iso(),
            ),
        )
        conn.commit()
        return category_id

    def delete_category(self, category_id: str) -> None:
        conn = self.db.connect()
        conn.execute("DELETE FROM catalog_categories WHERE parent_id = ?", (category_id,))
        conn.execute("DELETE FROM catalog_categories WHERE id = ?", (category_id,))
        conn.commit()

    def category_levels(self) -> dict[str, int]:
        return {
            str(row["name"]): int(row["level"])
            for row in self.categories()
            if row.get("enabled")
        }

    def ignore_patterns(self) -> list[str]:
        raw = get_setting(self.db, IGNORE_KEY, "") or ""
        if not raw.strip():
            return []
        try:
            value = json.loads(raw)
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
        except (TypeError, ValueError):
            pass
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def save_ignore_patterns(self, patterns: Iterable[str]) -> list[str]:
        cleaned: list[str] = []
        for item in patterns:
            value = str(item).strip()
            if value and value not in cleaned:
                cleaned.append(value)
        set_setting(self.db, IGNORE_KEY, json.dumps(cleaned, ensure_ascii=False))
        return cleaned

    # ── 目录同步 ──────────────────────────────────────────

    def sync_root(
        self,
        *,
        project_id: str,
        source_type: str,
        root: str,
        max_depth: int = 8,
        limit: int = 500,
    ) -> SyncResult:
        root = os.path.normpath(root)
        result = SyncResult(root=root)
        teams, team_canonical = self._tag_maps(self.teams())
        reviewers, reviewer_canonical = self._tag_maps(self.reviewers())
        categories = self.category_levels()
        ignore_patterns = list(
            dict.fromkeys([*self.ignore_patterns(), *default_ignore_patterns(source_type)])
        )
        batches = detect_batches(
            root,
            max_depth=max_depth,
            limit=limit,
            reviewer_names=reviewers.keys(),
            team_names=teams.keys(),
            category_names=categories.keys(),
            category_levels=categories,
            ignore_patterns=ignore_patterns,
        )
        conn = self.db.connect()
        seen_ids: list[str] = []
        for info in batches:
            seen_ids.append(
                self._upsert_batch(
                    conn,
                    project_id=project_id,
                    source_type=source_type,
                    root=root,
                    info=info,
                    teams=teams,
                    team_canonical=team_canonical,
                    reviewers=reviewers,
                    reviewer_canonical=reviewer_canonical,
                    result=result,
                )
            )
        if seen_ids:
            placeholders = ",".join("?" for _ in seen_ids)
            conn.execute(
                f"""
                UPDATE catalog_batches SET is_active = 0
                WHERE project_id = ? AND source_type = ? AND source_root_path = ?
                  AND id NOT IN ({placeholders})
                """,
                (project_id, source_type, root, *seen_ids),
            )
        else:
            conn.execute(
                """
                UPDATE catalog_batches SET is_active = 0
                WHERE project_id = ? AND source_type = ? AND source_root_path = ?
                """,
                (project_id, source_type, root),
            )
        conn.commit()
        result.batches = len(batches)
        return result

    def _upsert_batch(
        self,
        conn,
        *,
        project_id: str,
        source_type: str,
        root: str,
        info: BatchInfo,
        teams: dict[str, str],
        team_canonical: dict[str, str],
        reviewers: dict[str, str],
        reviewer_canonical: dict[str, str],
        result: SyncResult,
    ) -> str:
        team_name = team_canonical.get(info.team, info.team)
        team_id = teams.get(info.team, "") or teams.get(team_name, "")
        reviewer_raw = "" if info.conflict else (info.reviewers[0] if info.reviewers else "")
        reviewer_name = reviewer_canonical.get(reviewer_raw, reviewer_raw)
        reviewer_id = reviewers.get(reviewer_raw, "") or reviewers.get(reviewer_name, "")
        identity = {
            "category_l1": info.category_l1,
            "category_l2": info.category_l2,
            "category_l3": info.category_l3,
            "team": team_name,
            "team_raw": info.team,
            "date": info.date,
            "batch": info.name,
            "data_groups": info.data_groups,
            "extra_levels": info.groups,
            "reviewers": info.reviewers,
            "reviewer": reviewer_name,
            "reviewer_conflict": info.conflict,
        }
        row = conn.execute(
            "SELECT id, main_status FROM catalog_batches WHERE batch_path = ?", (info.path,)
        ).fetchone()
        common = (
            info.category_l1,
            info.category_l2,
            info.category_l3,
            team_id,
            team_name,
            reviewer_id,
            reviewer_name,
            info.date,
            info.record_dir,
            json.dumps(info.groups, ensure_ascii=False),
            len(info.data_groups),
            json.dumps(identity, ensure_ascii=False),
            now_iso(),
        )
        if row:
            conn.execute(
                """
                UPDATE catalog_batches
                SET project_id = ?, source_type = ?, source_root_path = ?, relative_path = ?,
                    name = ?, category_l1 = ?, category_l2 = ?, category_l3 = ?,
                    team_id = ?, team_name = ?, reviewer_id = ?, reviewer_name = ?,
                    submit_date = ?, record_dir = ?, extra_levels_json = ?,
                    data_count = ?, identity_json = ?, updated_at = ?, is_active = 1
                WHERE id = ?
                """,
                (
                    project_id,
                    source_type,
                    root,
                    os.path.relpath(info.path, root),
                    info.name,
                    *common,
                    row["id"],
                ),
            )
            batch_id = str(row["id"])
            result.updated += 1
        else:
            batch_id = _new_id("cbatch")
            conn.execute(
                """
                INSERT INTO catalog_batches(
                    id, project_id, source_type, source_root_path, batch_path, relative_path, name,
                    category_l1, category_l2, category_l3, team_id, team_name,
                    reviewer_id, reviewer_name, submit_date, record_dir,
                    extra_levels_json, data_count, identity_json, created_at, updated_at,
                    main_status, format_status, translation_status,
                    qualified_count, rejected_count, remaining_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
                """,
                (
                    batch_id,
                    project_id,
                    source_type,
                    root,
                    info.path,
                    os.path.relpath(info.path, root),
                    info.name,
                    *common,
                    now_iso(),
                    MAIN_NEW,
                    FORMAT_NOT_CHECKED,
                    TRANSLATION_NOT_TRANSLATED,
                    len(info.data_groups),
                ),
            )
            result.created += 1

        self._sync_groups(conn, batch_id, info)
        self._sync_reviewer_candidates(conn, batch_id, info, reviewers, reviewer_canonical)
        if info.conflict:
            result.conflicts += 1
        result.warnings.extend(f"{info.name}: {warning}" for warning in info.warnings)
        result.data_groups += len(info.data_groups)
        return batch_id

    def _sync_groups(self, conn, batch_id: str, info: BatchInfo) -> None:
        existing = {
            row["task_id"]
            for row in conn.execute(
                "SELECT task_id FROM catalog_groups WHERE batch_id = ?", (batch_id,)
            )
        }
        for task_id in info.data_groups:
            path = os.path.join(info.path, task_id)
            if task_id in existing:
                conn.execute(
                    "UPDATE catalog_groups SET path = ?, updated_at = ?, is_active = 1"
                    " WHERE batch_id = ? AND task_id = ?",
                    (path, now_iso(), batch_id, task_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO catalog_groups(id, batch_id, task_id, path, status, updated_at, is_active)
                    VALUES (?, ?, ?, ?, 'pending', ?, 1)
                    """,
                    (_new_id("cgroup"), batch_id, task_id, path, now_iso()),
                )
        stale = existing - set(info.data_groups)
        for task_id in stale:
            conn.execute(
                "UPDATE catalog_groups SET is_active = 0 WHERE batch_id = ? AND task_id = ?",
                (batch_id, task_id),
            )
        conn.execute(
            "UPDATE catalog_batches SET remaining_count = MAX(0, data_count - qualified_count) WHERE id = ?",
            (batch_id,),
        )

    def _sync_reviewer_candidates(
        self, conn, batch_id: str, info: BatchInfo, reviewers: dict[str, str], reviewer_canonical: dict[str, str]
    ) -> None:
        conn.execute("DELETE FROM catalog_batch_reviewers WHERE batch_id = ?", (batch_id,))
        for folder in info.record_folders:
            raw = folder.matched_reviewers[0] if folder.matched_reviewers else ""
            name = reviewer_canonical.get(raw, raw)
            conn.execute(
                """
                INSERT INTO catalog_batch_reviewers(
                    id, batch_id, reviewer_id, reviewer_name, folder_name,
                    folder_path, file_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("cbr"),
                    batch_id,
                    reviewers.get(raw, "") or reviewers.get(name, ""),
                    name,
                    folder.name,
                    folder.path,
                    len(folder.files),
                    now_iso(),
                ),
            )

    # ── 查询 ──────────────────────────────────────────────

    def batches(self, project_id: str = "", source_type: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM catalog_batches WHERE is_active = 1"
        params: list[Any] = []
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)
        sql += " ORDER BY submit_date DESC, name"
        rows = self.db.connect().execute(sql, params).fetchall()
        return [self._batch_row(dict(row)) for row in rows]

    def batch(self, batch_id: str) -> dict[str, Any] | None:
        row = self.db.connect().execute(
            "SELECT * FROM catalog_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        return self._batch_row(dict(row)) if row else None

    def _batch_row(self, row: dict[str, Any]) -> dict[str, Any]:
        candidates = self.db.connect().execute(
            "SELECT reviewer_name, folder_name, folder_path, file_count"
            " FROM catalog_batch_reviewers WHERE batch_id = ? ORDER BY reviewer_name",
            (row["id"],),
        ).fetchall()
        reviewer_names = sorted({str(item["reviewer_name"]) for item in candidates if item["reviewer_name"]})
        row["reviewer_candidates"] = [dict(item) for item in candidates]
        row["reviewer_conflict"] = len(reviewer_names) > 1
        row["main_status_label"] = MAIN_LABELS.get(row.get("main_status", ""), row.get("main_status", ""))
        row["format_status_label"] = FORMAT_LABELS.get(
            row.get("format_status", ""), row.get("format_status", "")
        )
        row["translation_status_label"] = TRANSLATION_LABELS.get(
            row.get("translation_status", ""), row.get("translation_status", "")
        )
        return row

    def groups(self, batch_id: str) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT * FROM catalog_groups WHERE batch_id = ? AND is_active = 1 ORDER BY task_id",
            (batch_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_count(self, batch_id: str) -> int:
        row = self.db.connect().execute(
            "SELECT COUNT(*) AS total FROM catalog_records WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return int(row["total"] if row else 0)

    def records(self, batch_id: str, limit: int = 5000) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT * FROM catalog_records WHERE batch_id = ?
            ORDER BY number, record_time LIMIT ?
            """,
            (batch_id, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def group_cards(self, project_id: str = "", source_type: str = "", level: int = 1) -> list[dict[str, Any]]:
        field = {1: "category_l1", 2: "category_l2", 3: "category_l3"}[level]
        rows = self.batches(project_id, source_type)
        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = str(row.get(field) or "").strip()
            if not name:
                continue
            bucket = buckets.setdefault(
                name,
                {"name": name, "batches": 0, "data_count": 0, "completed_batches": 0, "completed_groups": 0},
            )
            data_count = int(row.get("data_count") or 0)
            bucket["batches"] += 1
            bucket["data_count"] += data_count
            if row.get("main_status") == MAIN_COMPLETED:
                bucket["completed_batches"] += 1
                # 上级卡片按“数据量”口径统计已完成，而不是只统计合格记录。
                bucket["completed_groups"] += data_count
        for bucket in buckets.values():
            bucket["pending_batches"] = bucket["batches"] - bucket["completed_batches"]
            bucket["pending_groups"] = max(0, bucket["data_count"] - bucket["completed_groups"])
        return sorted(buckets.values(), key=lambda item: item["name"])

    # ── 队列 ──────────────────────────────────────────────

    def queue_add(self, batch_id: str, action: str = "scan") -> str:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT id FROM catalog_queue WHERE batch_id = ? AND action = ?", (batch_id, action)
        ).fetchone()
        if row:
            return str(row["id"])
        queue_id = _new_id("queue")
        conn.execute(
            "INSERT INTO catalog_queue(id, batch_id, action, status, added_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (queue_id, batch_id, action, QUEUE_QUEUED, now_iso()),
        )
        conn.commit()
        return queue_id

    def queue_remove(self, queue_id: str) -> None:
        conn = self.db.connect()
        conn.execute("DELETE FROM catalog_queue WHERE id = ?", (queue_id,))
        conn.commit()

    def queue_list(self, action: str = "scan") -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT q.*, b.name AS batch_name, b.batch_path, b.team_name, b.submit_date,
                   b.category_l1, b.category_l2, b.category_l3, b.main_status
            FROM catalog_queue q
            LEFT JOIN catalog_batches b ON b.id = q.batch_id
            WHERE q.action = ? ORDER BY q.added_at
            """,
            (action,),
        ).fetchall()
        return [dict(row) for row in rows]

    def queue_set_status(
        self,
        batch_id: str,
        action: str,
        status: str,
        message: str = "",
    ) -> None:
        conn = self.db.connect()
        now = now_iso()
        finished = now if status in {QUEUE_DONE, QUEUE_FAILED, QUEUE_SKIPPED} else None
        conn.execute(
            """
            UPDATE catalog_queue
            SET status = ?, message = ?,
                started_at = COALESCE(started_at, ?),
                finished_at = COALESCE(?, finished_at)
            WHERE batch_id = ? AND action = ?
            """,
            (status, message, now, finished, batch_id, action),
        )
        conn.commit()

    # ── 状态 ──────────────────────────────────────────────

    def set_main_status(
        self, batch_id: str, status: str, *, operator: str = "", reason: str = ""
    ) -> None:
        if status not in {MAIN_NEW, MAIN_COMPLETED}:
            raise ValueError("主状态只能是 new/completed")
        conn = self.db.connect()
        row = conn.execute(
            "SELECT main_status FROM catalog_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            raise ValueError("批次不存在")
        old = str(row["main_status"])
        conn.execute(
            "UPDATE catalog_batches SET main_status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), batch_id),
        )
        conn.execute(
            """
            INSERT INTO catalog_status_log(id, batch_id, kind, from_status, to_status, operator, reason, created_at)
            VALUES (?, ?, 'main', ?, ?, ?, ?, ?)
            """,
            (_new_id("clog"), batch_id, old, status, operator, reason, now_iso()),
        )
        conn.commit()

    def status_logs(self, batch_id: str) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT * FROM catalog_status_log WHERE batch_id = ? ORDER BY created_at DESC",
            (batch_id,),
        ).fetchall()
        return [dict(row) for row in rows]
