"""工作台扫描记录与批次快照写入（v1.25.8）。"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

from modules.common.path_guard import normalize_path
from modules.common.timeutil import now_iso
from modules.database.connection import Database
from modules.scanning.scanner import ScanResult


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def save_workspace_run(
    db: Database,
    result: ScanResult,
    *,
    operator: str = "",
    rule_set_id: str | None = None,
    rule_version_id: str | None = None,
) -> str:
    """保存一次业务目录发现；已存在的待确认候选原位更新，避免重复。"""
    conn = db.connect()
    run_id = _new_id("run")
    summary = {
        "stats": result.stats,
        "ruleset_id": result.ruleset_id,
        "ruleset_name": result.ruleset_name,
        "source_type": result.source_type,
        "unrecognized_dirs": result.unrecognized_dirs,
        "access_failures": result.access_failures,
    }
    conn.execute(
        """
        INSERT INTO scan_runs(
            id, root_path, rule_set_id, rule_version_id, started_at, finished_at, status,
            total_dirs, candidate_count, group_count, issue_count, operator, summary_json,
            source_type, project_root_id, project_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            result.root,
            rule_set_id or result.ruleset_id,
            rule_version_id,
            result.started_at,
            result.finished_at,
            result.status,
            result.total_dirs,
            len(result.candidates),
            len(result.groups),
            len(result.issues),
            operator,
            json.dumps(summary, ensure_ascii=False),
            result.source_type or "manual",
            result.project_root_id or None,
            result.project_name or None,
        ),
    )

    existing_batch_paths = {
        normalize_path(row["batch_path"])
        for row in conn.execute(
            "SELECT batch_path FROM batches WHERE source_type = ? AND batch_path IS NOT NULL",
            (result.source_type,),
        ).fetchall()
    }
    for candidate in result.candidates:
        if normalize_path(candidate.batch_path) in existing_batch_paths:
            continue
        previous = conn.execute(
            """
            SELECT c.id FROM scan_candidates c
            JOIN scan_runs r ON r.id = c.run_id
            WHERE c.batch_path = ? AND r.source_type = ?
              AND c.status IN ('pending', 'partial', 'duplicate', 'rescan')
              AND c.confirmed_batch_id IS NULL
            ORDER BY COALESCE(r.finished_at, r.started_at) DESC
            LIMIT 1
            """,
            (candidate.batch_path, result.source_type),
        ).fetchone()
        payload = (
            run_id,
            candidate.batch_path,
            candidate.batch_name,
            json.dumps(candidate.identity, ensure_ascii=False),
            candidate.status,
            candidate.confidence,
            json.dumps(
                {"reasons": candidate.reasons, "explain": candidate.explain},
                ensure_ascii=False,
            ),
            candidate.group_count,
            candidate.file_count,
            json.dumps([group.path for group in candidate.groups], ensure_ascii=False),
            now_iso(),
        )
        if previous:
            conn.execute(
                """
                UPDATE scan_candidates
                SET run_id = ?, batch_path = ?, batch_name = ?, identity_json = ?,
                    status = ?, confidence = ?, reason_json = ?, group_count = ?,
                    file_count = ?, group_paths_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (*payload, previous["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO scan_candidates(
                    id, run_id, batch_path, batch_name, identity_json, status, confidence,
                    reason_json, group_count, file_count, group_paths_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (_new_id("cand"), *payload),
            )

    for group in result.groups:
        conn.execute(
            """
            INSERT INTO scan_run_items(id, run_id, path, item_type, task_id, reason_code, detail_json)
            VALUES (?, ?, ?, 'group', ?, NULL, ?)
            """,
            (
                _new_id("item"),
                run_id,
                group.path,
                group.task_id,
                json.dumps(
                    {
                        "images": group.images,
                        "txt_zh": group.txt_zh,
                        "txt_en": group.txt_en,
                        "file_count": group.file_count,
                        "fingerprint": group.fingerprint,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    for issue in result.issues[:2000]:
        conn.execute(
            """
            INSERT INTO scan_run_items(id, run_id, path, item_type, task_id, reason_code, detail_json)
            VALUES (?, ?, ?, 'issue', NULL, ?, ?)
            """,
            (_new_id("item"), run_id, issue.path, issue.code, json.dumps(issue.detail, ensure_ascii=False)),
        )
    conn.commit()
    return run_id


def find_batch_by_path(db: Database, source_type: str, batch_path: str) -> dict[str, Any] | None:
    row = db.connect().execute(
        """
        SELECT * FROM batches
        WHERE source_type = ? AND lower(batch_path) = lower(?)
        ORDER BY created_at DESC LIMIT 1
        """,
        (source_type, batch_path),
    ).fetchone()
    return dict(row) if row else None


def upsert_groups(db: Database, batch_id: str, groups: list[Any], *, root_path: str) -> tuple[int, int]:
    """更新/新增数据组；不删除本次未发现的历史数据组。返回 (新增, 变化)。"""
    conn = db.connect()
    now = now_iso()
    added = 0
    changed = 0
    for item in groups:
        path = normalize_path(item.path)
        images = list(item.images or [])
        txt_zh = item.txt_zh
        txt_en = item.txt_en
        file_count = int(item.file_count or 0)
        image_count = int(item.image_count or len(images))
        txt_count = int(item.txt_count or (1 if txt_zh else 0) + (1 if txt_en else 0))
        fingerprint = item.fingerprint or ""
        user_name = item.user_name or "未知"
        existing = conn.execute(
            "SELECT * FROM data_groups WHERE batch_id = ? AND task_id = ? AND normalized_path = ?",
            (batch_id, item.task_id, path),
        ).fetchone()
        if existing:
            old = dict(existing)
            if (
                int(old.get("image_count") or 0) != image_count
                or int(old.get("txt_count") or 0) != txt_count
                or int(old.get("file_count") or 0) != file_count
                or (old.get("fingerprint") or "") != fingerprint
            ):
                changed += 1
            conn.execute(
                """
                UPDATE data_groups
                SET image_count = ?, txt_count = ?, file_count = ?, fingerprint = ?,
                    user_name = ?, root_path = ?, last_seen_at = ?
                WHERE id = ?
                """,
                (image_count, txt_count, file_count, fingerprint, user_name, root_path, now, old["id"]),
            )
            group_id = old["id"]
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
                    group_id, batch_id, item.task_id, path, root_path, user_name,
                    image_count, txt_count, file_count, fingerprint, now, now,
                ),
            )
            added += 1
        # 文件元信息只补缺失/更新同路径，不删除旧记录。
        for index, image in enumerate(images):
            role = "result" if "_result" in os.path.basename(image).casefold() else (
                "primary" if index == 0 else "other"
            )
            _insert_file(conn, group_id, path, image, role)
        if txt_zh:
            _insert_file(conn, group_id, path, txt_zh, "zh")
        if txt_en:
            _insert_file(conn, group_id, path, txt_en, "en")
    conn.commit()
    return added, changed


def _insert_file(conn, group_id: str, group_path: str, file_path: str, role: str) -> None:
    from pathlib import Path

    path = Path(file_path)
    try:
        stat = path.stat()
        size, mtime_ns = stat.st_size, stat.st_mtime_ns
    except OSError:
        size, mtime_ns = None, None
    relative = os.path.relpath(str(path), group_path) if group_path else path.name
    existing = conn.execute(
        "SELECT id FROM data_group_files WHERE data_group_id = ? AND relative_path = ?",
        (group_id, relative),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE data_group_files SET role = ?, file_name = ?, size = ?, mtime_ns = ?, ext = ? WHERE id = ?",
            (role, path.name, size, mtime_ns, path.suffix.lower().lstrip("."), existing["id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO data_group_files(
            id, data_group_id, role, file_name, relative_path, size, mtime_ns, ext, fingerprint
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')
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
        ),
    )


def update_batch_snapshot(
    db: Database,
    batch_id: str,
    *,
    candidate: Any,
    project_name: str,
    source_root_id: str,
    source_root_path: str,
    relative_path: str,
    data_count: int,
) -> None:
    conn = db.connect()
    identity = candidate.identity or {}
    conn.execute(
        """
        UPDATE batches
        SET name = COALESCE(NULLIF(name, ''), ?),
            category_l1 = COALESCE(NULLIF(category_l1, ''), ?),
            category_l2 = COALESCE(NULLIF(category_l2, ''), ?),
            submit_date = COALESCE(NULLIF(submit_date, ''), ?),
            data_root = ?,
            batch_path = ?,
            data_count = ?,
            identity_json = ?,
            source_root_id = ?,
            source_root_path = ?,
            relative_path = ?,
            project_name = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            candidate.batch_name,
            identity.get("category_l1", ""),
            identity.get("category_l2", ""),
            identity.get("date", ""),
            source_root_path,
            candidate.batch_path,
            int(data_count),
            json.dumps(identity, ensure_ascii=False),
            source_root_id,
            source_root_path,
            relative_path,
            project_name,
            now_iso(),
            batch_id,
        ),
    )
    conn.commit()
