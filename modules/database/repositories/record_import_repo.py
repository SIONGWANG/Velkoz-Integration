"""记录导入持久化（v1.25.4）。"""
from __future__ import annotations

import uuid
from typing import Any

from modules.common.timeutil import now_iso
from modules.database.connection import Database


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


IMPORT_FIELDS = {
    "source_path",
    "source_type",
    "file_size",
    "file_mtime_ns",
    "file_hash",
    "rows_total",
    "rows_new",
    "rows_skipped",
    "rows_error",
    "operator_detected",
    "batch_id",
    "status",
    "imported_at",
    "imported_by",
    "note",
}

INT_FIELDS = {"file_size", "file_mtime_ns", "rows_total", "rows_new", "rows_skipped", "rows_error"}


def create_import(db: Database, data: dict[str, Any]) -> str:
    import_id = str(data.get("id") or _new_id("rim"))
    payload = {key: data.get(key) for key in IMPORT_FIELDS}
    for key in INT_FIELDS:
        if payload.get(key) is None:
            payload[key] = 0
    payload["status"] = payload.get("status") or "failed"
    payload["source_path"] = payload.get("source_path") or ""
    payload["source_type"] = payload.get("source_type") or "unclassified"
    payload["imported_at"] = payload.get("imported_at") or now_iso()
    columns = ["id", *IMPORT_FIELDS, "created_at"]
    placeholders = ",".join("?" for _ in columns)
    conn = db.connect()
    conn.execute(
        f"INSERT INTO record_imports({','.join(columns)}) VALUES ({placeholders})",
        (
            import_id,
            *(payload.get(key) for key in IMPORT_FIELDS),
            data.get("created_at") or now_iso(),
        ),
    )
    conn.commit()
    return import_id


def update_import(db: Database, import_id: str, **fields: Any) -> None:
    updates = {key: value for key, value in fields.items() if key in IMPORT_FIELDS}
    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    conn = db.connect()
    conn.execute(
        f"UPDATE record_imports SET {assignments} WHERE id = ?",
        (*updates.values(), import_id),
    )
    conn.commit()


def get_import(db: Database, import_id: str) -> dict[str, Any] | None:
    row = db.connect().execute(
        "SELECT * FROM record_imports WHERE id = ?", (import_id,)
    ).fetchone()
    return dict(row) if row else None


def list_imports(db: Database, limit: int = 200) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        """
        SELECT * FROM record_imports
        ORDER BY COALESCE(imported_at, created_at) DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(row) for row in rows]


RECORD_FIELDS = {
    "import_id",
    "data_group_id",
    "task_id",
    "operator",
    "result",
    "l1",
    "l2",
    "note",
    "tags_raw",
    "evidence_raw",
    "record_time",
    "source_row",
    "source_row_fingerprint",
    "accepted_event_id",
    "link_status",
}


def record_exists(db: Database, fingerprint: str) -> bool:
    if not fingerprint:
        return False
    row = db.connect().execute(
        "SELECT 1 FROM imported_records WHERE source_row_fingerprint = ? LIMIT 1",
        (fingerprint,),
    ).fetchone()
    return bool(row)


def insert_record(db: Database, data: dict[str, Any]) -> bool:
    """插入一条导入记录；全局指纹已存在时返回 False。"""
    fingerprint = str(data.get("source_row_fingerprint") or "")
    if not fingerprint:
        raise ValueError("缺少行指纹")
    if record_exists(db, fingerprint):
        return False
    record_id = str(data.get("id") or _new_id("rir"))
    payload = {key: data.get(key) for key in RECORD_FIELDS}
    payload["source_row_fingerprint"] = fingerprint
    payload["import_id"] = data.get("import_id") or ""
    payload["source_row"] = int(data.get("source_row") or 0)
    columns = ["id", *RECORD_FIELDS, "created_at"]
    placeholders = ",".join("?" for _ in columns)
    conn = db.connect()
    conn.execute(
        f"INSERT INTO imported_records({','.join(columns)}) VALUES ({placeholders})",
        (
            record_id,
            *(payload.get(key) for key in RECORD_FIELDS),
            data.get("created_at") or now_iso(),
        ),
    )
    conn.commit()
    return True


def list_records(
    db: Database, import_id: str | None = None, *, limit: int = 5000
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM imported_records"
    params: list[Any] = []
    if import_id:
        sql += " WHERE import_id = ?"
        params.append(import_id)
    sql += " ORDER BY COALESCE(record_time, created_at) DESC, source_row LIMIT ?"
    params.append(int(limit))
    rows = db.connect().execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def recent_records(db: Database, limit: int = 200) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        """
        SELECT r.*, i.source_path
        FROM imported_records r
        JOIN record_imports i ON i.id = r.import_id
        ORDER BY COALESCE(r.record_time, r.created_at) DESC, r.source_row
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def find_data_groups(
    db: Database, task_id: str, batch_id: str | None = None
) -> list[dict[str, Any]]:
    if not task_id:
        return []
    sql = "SELECT * FROM data_groups WHERE task_id = ?"
    params: list[Any] = [task_id]
    if batch_id:
        sql += " AND batch_id = ?"
        params.append(batch_id)
    sql += " ORDER BY first_seen_at, id"
    rows = db.connect().execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def list_batches(db: Database, limit: int = 200) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        """
        SELECT id, display_id, name, data_root, status, data_count
        FROM batches
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def import_summary(db: Database) -> dict[str, Any]:
    row = db.connect().execute(
        """
        SELECT COUNT(*) AS imports,
               COALESCE(SUM(rows_new), 0) AS rows_new,
               COALESCE(SUM(rows_skipped), 0) AS rows_skipped,
               COALESCE(SUM(rows_error), 0) AS rows_error
        FROM record_imports
        """
    ).fetchone()
    return dict(row) if row else {}
