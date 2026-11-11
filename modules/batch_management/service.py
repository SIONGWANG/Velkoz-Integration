"""候选批次确认、合并/拆分与扫描差异。"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable

from modules.audit_log.service import AuditLogger
from modules.common.path_guard import is_relative_to, normalize_path
from modules.common.timeutil import now_iso, today_str
from modules.database.connection import Database, open_database
from modules.database.repositories.management_repo import recent_scan_runs

VALID_CANDIDATE_STATUS = {
    "pending",
    "confirmed",
    "ignored",
    "abnormal",
    "partial",
    "duplicate",
    "rescan",
}

VALID_BATCH_STATUS = {"confirmed", "paused", "completed", "archived"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def _json_loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _detail(row: Any) -> dict[str, Any]:
    return _json_loads(row["detail_json"] if "detail_json" in row.keys() else None, {})


class BatchService:
    def __init__(self, db_path: str | Path | None = None):
        self.db = open_database(db_path)

    # ── 读取 ──────────────────────────────────────────────

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return recent_scan_runs(self.db, limit)

    def list_candidates(self, run_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        conn = self.db.connect()
        sql = """
            SELECT c.*, r.root_path, r.started_at AS run_started_at, r.rule_set_id, r.rule_version_id,
                   r.source_type, r.project_root_id, r.project_name
            FROM scan_candidates c
            JOIN scan_runs r ON r.id = c.run_id
        """
        where: list[str] = []
        params: list[Any] = []
        if run_id:
            where.append("c.run_id = ?")
            params.append(run_id)
        if status:
            where.append("c.status = ?")
            params.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY c.batch_path COLLATE NOCASE"
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        conn = self.db.connect()
        row = conn.execute(
            """
            SELECT c.*, r.root_path, r.started_at AS run_started_at, r.rule_set_id, r.rule_version_id,
                   r.source_type, r.project_root_id, r.project_name
            FROM scan_candidates c
            JOIN scan_runs r ON r.id = c.run_id
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()
        return dict(row) if row else None

    def candidate_groups(self, candidate_id: str) -> list[dict[str, Any]]:
        candidate = self.get_candidate(candidate_id)
        if not candidate:
            return []
        return self._candidate_group_items(candidate)

    def _candidate_group_items(self, candidate: dict[str, Any]) -> list[dict[str, Any]]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM scan_run_items WHERE run_id = ? AND item_type = 'group' ORDER BY path COLLATE NOCASE",
            (candidate["run_id"],),
        ).fetchall()
        allowed = _json_loads(candidate.get("group_paths_json"), None)
        allowed_set = set(allowed) if isinstance(allowed, list) else None
        batch_path = candidate.get("batch_path") or candidate.get("root_path") or ""
        items: list[dict[str, Any]] = []
        for row in rows:
            path = row["path"]
            if allowed_set is not None:
                if path not in allowed_set:
                    continue
            elif batch_path and not is_relative_to(path, batch_path) and normalize_path(path) != normalize_path(batch_path):
                continue
            item = dict(row)
            item["detail"] = _detail(row)
            items.append(item)
        return items

    def list_batches(self, limit: int = 100) -> list[dict[str, Any]]:
        conn = self.db.connect()
        rows = conn.execute(
            """
            SELECT b.*,
                   (SELECT COUNT(*) FROM data_groups g WHERE g.batch_id = b.id) AS actual_group_count
            FROM batches b
            ORDER BY b.created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def batch_groups(self, batch_id: str) -> list[dict[str, Any]]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM data_groups WHERE batch_id = ? ORDER BY normalized_path COLLATE NOCASE",
            (batch_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_batch_status(
        self,
        batch_id: str,
        status: str,
        *,
        operator: str = "",
        reason: str = "",
    ) -> None:
        """更新批次处理状态并写入状态日志与操作日志；不改动任务或源文件。"""
        if status not in VALID_BATCH_STATUS:
            raise ValueError(f"不支持的批次状态：{status}")
        conn = self.db.connect()
        row = conn.execute("SELECT status FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if row is None:
            raise ValueError("批次不存在")
        previous = row["status"] or ""
        if previous == status:
            return
        now = now_iso()
        conn.execute(
            "UPDATE batches SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, batch_id),
        )
        conn.execute(
            "INSERT INTO batch_status_log(id, batch_id, from_status, to_status, operator, reason, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_new_id("bsl"), batch_id, previous, status, operator, reason, now),
        )
        conn.commit()
        AuditLogger(self.db).log(
            module="batch_management",
            action="update_batch_status",
            operator=operator,
            batch_id=batch_id,
            detail={"from": previous, "to": status, "reason": reason},
        )

    # ── 确认 / 忽略 / 修改 ────────────────────────────────

    def confirm_candidate(
        self,
        candidate_id: str,
        *,
        operator: str = "",
        name: str | None = None,
        identity_overrides: dict[str, str] | None = None,
        selected_group_paths: list[str] | None = None,
    ) -> str:
        candidate = self.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("候选批次不存在")
        if candidate["status"] == "confirmed" and candidate.get("confirmed_batch_id"):
            return candidate["confirmed_batch_id"]
        if candidate["status"] == "ignored":
            raise ValueError("候选已被忽略，不能直接确认")

        groups = self._candidate_group_items(candidate)
        if selected_group_paths is not None:
            selected = {normalize_path(path) for path in selected_group_paths}
            groups = [item for item in groups if normalize_path(item["path"]) in selected]
        if not groups:
            raise ValueError("没有可确认的数据组")

        identity = _json_loads(candidate.get("identity_json"), {})
        if identity_overrides:
            identity.update(identity_overrides)
        batch_name = name or candidate.get("batch_name") or os.path.basename(candidate["batch_path"])

        conn = self.db.connect()
        try:
            conn.execute("BEGIN")
            batch_id = self._create_batch(
                candidate,
                groups,
                identity,
                batch_name,
                operator,
                source="scan",
                source_type=candidate.get("source_type") or "legacy",
                source_root_id=candidate.get("project_root_id") or "",
                source_root_path=candidate.get("root_path") or "",
                project_name=candidate.get("project_name") or "",
            )
            self._insert_groups(batch_id, groups)
            conn.execute(
                """
                UPDATE scan_candidates
                SET status = 'confirmed', confirmed_batch_id = ?, confirmed_by = ?, confirmed_at = ?,
                    batch_name = ?, identity_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (batch_id, operator, now_iso(), batch_name, json.dumps(identity, ensure_ascii=False), now_iso(), candidate_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        AuditLogger(self.db).log(
            module="batch_management",
            action="confirm_candidate",
            operator=operator,
            target_path=candidate["batch_path"],
            batch_id=batch_id,
            detail={"candidate_id": candidate_id, "group_count": len(groups), "name": batch_name},
        )
        return batch_id

    def ignore_candidate(self, candidate_id: str, *, operator: str = "", reason: str = "") -> None:
        candidate = self.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("候选批次不存在")
        conn = self.db.connect()
        conn.execute(
            """
            UPDATE scan_candidates
            SET status = 'ignored', notes = ?, confirmed_by = ?, confirmed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (reason, operator, now_iso(), now_iso(), candidate_id),
        )
        conn.commit()
        AuditLogger(self.db).log(
            module="batch_management",
            action="ignore_candidate",
            operator=operator,
            target_path=candidate["batch_path"],
            detail={"candidate_id": candidate_id, "reason": reason},
        )

    def update_candidate(
        self,
        candidate_id: str,
        *,
        operator: str = "",
        name: str | None = None,
        identity: dict[str, str] | None = None,
        notes: str | None = None,
        status: str | None = None,
    ) -> None:
        candidate = self.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("候选批次不存在")
        if candidate["status"] == "confirmed":
            raise ValueError("已确认候选不能直接修改；请修改批次或重新扫描")
        if status is not None and (status not in VALID_CANDIDATE_STATUS or status in {"confirmed", "ignored"}):
            raise ValueError(f"不允许手工设置状态：{status}")
        new_name = name if name is not None else candidate.get("batch_name")
        new_identity = _json_loads(candidate.get("identity_json"), {})
        if identity is not None:
            new_identity.update(identity)
        new_notes = notes if notes is not None else candidate.get("notes")
        new_status = status if status is not None else candidate.get("status")
        conn = self.db.connect()
        conn.execute(
            """
            UPDATE scan_candidates
            SET batch_name = ?, identity_json = ?, notes = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_name, json.dumps(new_identity, ensure_ascii=False), new_notes, new_status, now_iso(), candidate_id),
        )
        conn.commit()
        AuditLogger(self.db).log(
            module="batch_management",
            action="update_candidate",
            operator=operator,
            target_path=candidate["batch_path"],
            detail={"candidate_id": candidate_id, "name": new_name, "identity": new_identity, "status": new_status},
        )

    # ── 合并 / 拆分 ───────────────────────────────────────

    def merge_candidates(
        self,
        candidate_ids: Iterable[str],
        *,
        operator: str = "",
        name: str | None = None,
        identity_overrides: dict[str, str] | None = None,
    ) -> str:
        ids = [item for item in dict.fromkeys(candidate_ids) if item]
        if len(ids) < 2:
            raise ValueError("至少选择两个候选批次才能合并")
        candidates = [self.get_candidate(item) for item in ids]
        candidates = [item for item in candidates if item]
        if len(candidates) < 2:
            raise ValueError("候选批次读取失败")
        for candidate in candidates:
            if candidate["status"] in {"ignored", "confirmed"}:
                raise ValueError(f"候选 {candidate.get('batch_name')} 已忽略或已确认，不能合并")

        groups: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            for group in self._candidate_group_items(candidate):
                path = normalize_path(group["path"])
                if path in seen:
                    continue
                seen.add(path)
                groups.append(group)
        if not groups:
            raise ValueError("候选批次没有可合并的数据组")

        primary = candidates[0]
        identity = _json_loads(primary.get("identity_json"), {})
        if identity_overrides:
            identity.update(identity_overrides)
        batch_name = name or primary.get("batch_name") or os.path.basename(primary["batch_path"])

        conn = self.db.connect()
        try:
            conn.execute("BEGIN")
            batch_id = self._create_batch(
                primary,
                groups,
                identity,
                batch_name,
                operator,
                source="merge",
                source_type=primary.get("source_type") or "legacy",
                source_root_id=primary.get("project_root_id") or "",
                source_root_path=primary.get("root_path") or "",
                project_name=primary.get("project_name") or "",
            )
            self._insert_groups(batch_id, groups)
            for candidate in candidates:
                conn.execute(
                    """
                    UPDATE scan_candidates
                    SET status = 'confirmed', confirmed_batch_id = ?, confirmed_by = ?, confirmed_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (batch_id, operator, now_iso(), now_iso(), candidate["id"]),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        AuditLogger(self.db).log(
            module="batch_management",
            action="merge_candidates",
            operator=operator,
            target_path=primary["batch_path"],
            batch_id=batch_id,
            detail={"candidate_ids": ids, "group_count": len(groups), "name": batch_name},
        )
        return batch_id

    def split_candidate(
        self,
        candidate_id: str,
        selected_group_paths: Iterable[str],
        *,
        operator: str = "",
        name: str | None = None,
    ) -> dict[str, str]:
        candidate = self.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("候选批次不存在")
        if candidate["status"] in {"ignored", "confirmed"}:
            raise ValueError("已忽略或已确认候选不能拆分")
        groups = self._candidate_group_items(candidate)
        selected = {normalize_path(path) for path in selected_group_paths}
        selected_groups = [item for item in groups if normalize_path(item["path"]) in selected]
        remaining_groups = [item for item in groups if normalize_path(item["path"]) not in selected]
        if not selected_groups:
            raise ValueError("请选择要拆出的数据组")
        if not remaining_groups:
            raise ValueError("不能把所有数据组都拆出；请直接确认整个候选")

        identity = _json_loads(candidate.get("identity_json"), {})
        base_name = name or candidate.get("batch_name") or os.path.basename(candidate["batch_path"])
        batch_name = base_name
        conn = self.db.connect()
        try:
            conn.execute("BEGIN")
            batch_id = self._create_batch(
                candidate,
                selected_groups,
                identity,
                batch_name,
                operator,
                source="split",
                source_type=candidate.get("source_type") or "legacy",
                source_root_id=candidate.get("project_root_id") or "",
                source_root_path=candidate.get("root_path") or "",
                project_name=candidate.get("project_name") or "",
            )
            self._insert_groups(batch_id, selected_groups)
            conn.execute(
                """
                UPDATE scan_candidates
                SET status = 'confirmed', confirmed_batch_id = ?, confirmed_by = ?, confirmed_at = ?,
                    group_paths_json = ?, batch_name = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    batch_id,
                    operator,
                    now_iso(),
                    json.dumps([item["path"] for item in selected_groups], ensure_ascii=False),
                    f"{batch_name}（拆分）",
                    now_iso(),
                    candidate_id,
                ),
            )
            new_candidate_id = _new_id("cand")
            conn.execute(
                """
                INSERT INTO scan_candidates(
                    id, run_id, batch_path, batch_name, identity_json, status, confidence,
                    reason_json, group_count, file_count, group_paths_json, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_candidate_id,
                    candidate["run_id"],
                    candidate["batch_path"],
                    f"{base_name}（剩余）",
                    json.dumps(identity, ensure_ascii=False),
                    candidate.get("confidence", 0),
                    candidate.get("reason_json"),
                    len(remaining_groups),
                    sum(int(item.get("detail", {}).get("file_count", 0) or 0) for item in remaining_groups),
                    json.dumps([item["path"] for item in remaining_groups], ensure_ascii=False),
                    candidate.get("notes"),
                    now_iso(),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        AuditLogger(self.db).log(
            module="batch_management",
            action="split_candidate",
            operator=operator,
            target_path=candidate["batch_path"],
            batch_id=batch_id,
            detail={
                "candidate_id": candidate_id,
                "new_candidate_id": new_candidate_id,
                "selected": len(selected_groups),
                "remaining": len(remaining_groups),
            },
        )
        return {"batch_id": batch_id, "new_candidate_id": new_candidate_id}

    # ── 扫描差异 ─────────────────────────────────────────

    def diff_runs(self, old_run_id: str, new_run_id: str) -> dict[str, Any]:
        old = self._run_snapshot(old_run_id)
        new = self._run_snapshot(new_run_id)
        old_groups = old["groups"]
        new_groups = new["groups"]
        added = sorted(set(new_groups) - set(old_groups))
        removed = sorted(set(old_groups) - set(new_groups))
        modified = []
        for path in sorted(set(old_groups) & set(new_groups)):
            before = old_groups[path]
            after = new_groups[path]
            if (
                before.get("fingerprint") != after.get("fingerprint")
                or before.get("file_count") != after.get("file_count")
                or before.get("images") != after.get("images")
                or before.get("txt_zh") != after.get("txt_zh")
                or before.get("txt_en") != after.get("txt_en")
            ):
                modified.append({"path": path, "before": before, "after": after})

        old_candidates = old["candidates"]
        new_candidates = new["candidates"]
        candidate_added = sorted(set(new_candidates) - set(old_candidates))
        candidate_removed = sorted(set(old_candidates) - set(new_candidates))
        candidate_changed = []
        for path in sorted(set(old_candidates) & set(new_candidates)):
            before = old_candidates[path]
            after = new_candidates[path]
            if before.get("identity") != after.get("identity") or before.get("confidence") != after.get("confidence"):
                candidate_changed.append({"batch_path": path, "before": before, "after": after})

        return {
            "old_run_id": old_run_id,
            "new_run_id": new_run_id,
            "groups": {
                "added": added,
                "removed": removed,
                "modified": modified,
                "unchanged_count": len(set(old_groups) & set(new_groups)) - len(modified),
            },
            "candidates": {
                "added": candidate_added,
                "removed": candidate_removed,
                "changed": candidate_changed,
            },
        }

    def _run_snapshot(self, run_id: str) -> dict[str, Any]:
        conn = self.db.connect()
        run = conn.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            raise ValueError("扫描记录不存在")
        groups: dict[str, dict[str, Any]] = {}
        for row in conn.execute(
            "SELECT * FROM scan_run_items WHERE run_id = ? AND item_type = 'group'",
            (run_id,),
        ).fetchall():
            detail = _detail(row)
            groups[normalize_path(row["path"])] = {
                "path": row["path"],
                "task_id": row["task_id"],
                "fingerprint": detail.get("fingerprint", ""),
                "file_count": detail.get("file_count", 0),
                "images": detail.get("images", []),
                "txt_zh": detail.get("txt_zh"),
                "txt_en": detail.get("txt_en"),
            }
        candidates: dict[str, dict[str, Any]] = {}
        for row in conn.execute("SELECT * FROM scan_candidates WHERE run_id = ?", (run_id,)).fetchall():
            path = normalize_path(row["batch_path"])
            candidates[path] = {
                "batch_path": row["batch_path"],
                "batch_name": row["batch_name"],
                "identity": _json_loads(row["identity_json"], {}),
                "status": row["status"],
                "confidence": row["confidence"],
            }
        return {"run": dict(run), "groups": groups, "candidates": candidates}

    # ── 内部 ──────────────────────────────────────────────

    def _create_batch(
        self,
        candidate: dict[str, Any],
        groups: list[dict[str, Any]],
        identity: dict[str, str],
        batch_name: str,
        operator: str,
        *,
        source: str,
        source_type: str = "legacy",
        source_root_id: str = "",
        source_root_path: str = "",
        project_name: str = "",
    ) -> str:
        conn = self.db.connect()
        now = now_iso()
        batch_path = candidate.get("batch_path") or candidate.get("root_path") or ""
        root_path = source_root_path or candidate.get("root_path") or ""
        resolved_type = source_type if source_type in {"pending_acceptance", "final_acceptance"} else "legacy"
        try:
            relative_path = os.path.relpath(batch_path, root_path) if root_path else ""
        except ValueError:
            relative_path = ""
        if relative_path == ".":
            relative_path = ""
        existing = conn.execute(
            "SELECT id FROM batches WHERE source_type = ? AND lower(batch_path) = lower(?) LIMIT 1",
            (resolved_type, batch_path),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE batches
                SET name = COALESCE(NULLIF(name, ''), ?),
                    category_l1 = COALESCE(NULLIF(category_l1, ''), ?),
                    category_l2 = COALESCE(NULLIF(category_l2, ''), ?),
                    submit_date = COALESCE(NULLIF(submit_date, ''), ?),
                    data_root = ?, batch_path = ?, data_count = ?, identity_json = ?,
                    source_root_id = ?, source_root_path = ?, relative_path = ?,
                    project_name = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    batch_name,
                    identity.get("category_l1", ""),
                    identity.get("category_l2", ""),
                    identity.get("date", ""),
                    root_path,
                    batch_path,
                    len(groups),
                    json.dumps(identity, ensure_ascii=False),
                    source_root_id,
                    root_path,
                    relative_path,
                    project_name,
                    now,
                    existing["id"],
                ),
            )
            return str(existing["id"])

        batch_id = _new_id("batch")
        display_id = self._next_display_id()
        unique_name = self._unique_batch_name(batch_name, root_path or batch_path)
        conn.execute(
            """
            INSERT INTO batches(
                id, display_id, name, category_l1, category_l2, submit_date, data_root, batch_path,
                data_count, status, source, rule_set_id, rule_version_id, identity_json,
                created_at, updated_at, source_type, source_root_id, source_root_path,
                relative_path, parent_batch_id, project_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                batch_id,
                display_id,
                unique_name,
                identity.get("category_l1", ""),
                identity.get("category_l2", ""),
                identity.get("date") or today_str(),
                root_path,
                batch_path,
                len(groups),
                source,
                candidate.get("rule_set_id"),
                candidate.get("rule_version_id"),
                json.dumps(identity, ensure_ascii=False),
                now,
                now,
                resolved_type,
                source_root_id,
                root_path,
                relative_path,
                project_name,
            ),
        )
        conn.execute(
            "INSERT INTO batch_status_log(id, batch_id, from_status, to_status, operator, reason, created_at) VALUES (?, ?, NULL, 'confirmed', ?, ?, ?)",
            (_new_id("bsl"), batch_id, operator, f"来源：{source}", now),
        )
        return batch_id

    def _next_display_id(self) -> str:
        conn = self.db.connect()
        prefix = f"B-{today_str()}-"
        row = conn.execute("SELECT COUNT(*) AS c FROM batches WHERE display_id LIKE ?", (prefix + "%",)).fetchone()
        return f"{prefix}{int(row['c']) + 1:03d}"

    def _unique_batch_name(self, name: str, data_root: str) -> str:
        conn = self.db.connect()
        candidate = name
        index = 2
        while True:
            row = conn.execute(
                "SELECT 1 FROM batches WHERE name = ? AND data_root = ?",
                (candidate, data_root),
            ).fetchone()
            if not row:
                return candidate
            candidate = f"{name}（{index}）"
            index += 1

    def _insert_groups(self, batch_id: str, groups: list[dict[str, Any]]) -> None:
        conn = self.db.connect()
        now = now_iso()
        for item in groups:
            detail = item.get("detail") or {}
            path = normalize_path(item["path"])
            root_path = detail.get("root_path") or ""
            existing = conn.execute(
                "SELECT id FROM data_groups WHERE batch_id = ? AND task_id = ? AND normalized_path = ?",
                (batch_id, item.get("task_id") or "", path),
            ).fetchone()
            if existing:
                group_id = existing["id"]
                conn.execute(
                    """
                    UPDATE data_groups
                    SET user_name = ?, image_count = ?, txt_count = ?, file_count = ?,
                        fingerprint = ?, root_path = ?, last_seen_at = ?
                    WHERE id = ?
                    """,
                    (
                        detail.get("user_name") or "未知",
                        len(detail.get("images") or []),
                        (1 if detail.get("txt_zh") else 0) + (1 if detail.get("txt_en") else 0),
                        int(detail.get("file_count", 0) or 0),
                        detail.get("fingerprint", ""),
                        root_path,
                        now,
                        group_id,
                    ),
                )
            else:
                group_id = _new_id("dg")
                conn.execute(
                    """
                    INSERT INTO data_groups(
                        id, batch_id, task_id, normalized_path, root_path, user_name,
                        image_count, txt_count, file_count, fingerprint, status,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        group_id,
                        batch_id,
                        item.get("task_id") or "",
                        path,
                        root_path,
                        detail.get("user_name") or "",
                        len(detail.get("images") or []),
                        1 if detail.get("txt_zh") else 0,
                        int(detail.get("file_count", 0) or 0),
                        detail.get("fingerprint", ""),
                        now,
                        now,
                    ),
                )
                if detail.get("txt_en"):
                    conn.execute(
                        "UPDATE data_groups SET txt_count = txt_count + 1 WHERE id = ?",
                        (group_id,),
                    )
            self._insert_files(group_id, path, detail)

    def _insert_files(self, group_id: str, group_path: str, detail: dict[str, Any]) -> None:
        conn = self.db.connect()
        for index, image in enumerate(detail.get("images") or []):
            self._insert_file(group_id, group_path, image, "result" if "_result" in os.path.basename(image).casefold() else ("primary" if index == 0 else "other"))
        if detail.get("txt_zh"):
            self._insert_file(group_id, group_path, detail["txt_zh"], "zh")
        if detail.get("txt_en"):
            self._insert_file(group_id, group_path, detail["txt_en"], "en")

    def _insert_file(self, group_id: str, group_path: str, file_path: str, role: str) -> None:
        conn = self.db.connect()
        path = Path(file_path)
        relative = os.path.relpath(str(path), group_path) if group_path else path.name
        existing = conn.execute(
            "SELECT id FROM data_group_files WHERE data_group_id = ? AND relative_path = ?",
            (group_id, relative),
        ).fetchone()
        try:
            st = path.stat()
            size, mtime_ns = st.st_size, st.st_mtime_ns
        except OSError:
            size, mtime_ns = None, None
        if existing:
            conn.execute(
                "UPDATE data_group_files SET role = ?, file_name = ?, size = ?, mtime_ns = ?, ext = ? WHERE id = ?",
                (
                    role,
                    path.name,
                    size,
                    mtime_ns,
                    path.suffix.lower().lstrip("."),
                    existing["id"],
                ),
            )
            return
        conn.execute(
            """
            INSERT INTO data_group_files(id, data_group_id, role, file_name, relative_path, size, mtime_ns, ext, fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("dgf"),
                group_id,
                role,
                path.name,
                relative,
                size,
                mtime_ns,
                path.suffix.lower().lstrip("."),
                "",
            ),
        )

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "BatchService":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
