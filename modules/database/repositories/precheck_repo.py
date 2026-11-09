"""翻译与预审持久化：配置、任务、条目、用量。"""
from __future__ import annotations

import json
import uuid
from typing import Any

from modules.common.timeutil import now_iso
from modules.database.connection import Database


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def _loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


# ── 应用配置 ─────────────────────────────────────────────


def get_setting(db: Database, key: str, default: str | None = None) -> str | None:
    row = db.connect().execute(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else default


def set_setting(db: Database, key: str, value: str) -> None:
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value, now_iso()),
    )
    conn.commit()


def delete_setting(db: Database, key: str) -> None:
    conn = db.connect()
    conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    conn.commit()


# ── 任务 ─────────────────────────────────────────────────

JOB_FIELDS = {
    "batch_id",
    "provider",
    "model",
    "status",
    "prompt_version",
    "started_at",
    "finished_at",
    "operator",
    "source_report",
    "note",
    "root_path",
    "profile_id",
    "summary_json",
}


def create_job(db: Database, data: dict[str, Any]) -> str:
    job_id = str(data.get("id") or _new_id("trj"))
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO translation_jobs(
            id, batch_id, provider, model, status, prompt_version, started_at,
            finished_at, operator, source_report, note, root_path, profile_id,
            summary_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            data.get("batch_id"),
            data.get("provider") or "openai_compat",
            data.get("model") or "",
            data.get("status") or "pending",
            data.get("prompt_version") or "",
            data.get("started_at"),
            data.get("finished_at"),
            data.get("operator") or "",
            data.get("source_report"),
            data.get("note") or "",
            data.get("root_path") or "",
            data.get("profile_id") or "",
            json.dumps(data.get("summary") or {}, ensure_ascii=False)
            if not isinstance(data.get("summary_json"), str)
            else data.get("summary_json"),
            data.get("created_at") or now_iso(),
        ),
    )
    conn.commit()
    return job_id


def update_job(db: Database, job_id: str, **fields: Any) -> None:
    updates = {key: value for key, value in fields.items() if key in JOB_FIELDS}
    if "summary" in fields:
        updates["summary_json"] = json.dumps(fields["summary"], ensure_ascii=False)
    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    conn = db.connect()
    conn.execute(
        f"UPDATE translation_jobs SET {assignments} WHERE id = ?",
        (*updates.values(), job_id),
    )
    conn.commit()


def get_job(db: Database, job_id: str) -> dict[str, Any] | None:
    row = db.connect().execute(
        "SELECT * FROM translation_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if not row:
        return None
    job = dict(row)
    job["summary"] = _loads(job.pop("summary_json", None), {})
    return job


def list_jobs(db: Database, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        """
        SELECT * FROM translation_jobs
        ORDER BY COALESCE(started_at, created_at) DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    jobs: list[dict[str, Any]] = []
    for row in rows:
        job = dict(row)
        job["summary"] = _loads(job.pop("summary_json", None), {})
        jobs.append(job)
    return jobs


def delete_job(db: Database, job_id: str) -> None:
    conn = db.connect()
    conn.execute("DELETE FROM translation_items WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM translation_usage WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM translation_jobs WHERE id = ?", (job_id,))
    conn.commit()


# ── 条目 ─────────────────────────────────────────────────

ITEM_FIELDS = {
    "data_group_id",
    "task_id",
    "folder_path",
    "zh_txt_path",
    "en_txt_path",
    "status",
    "zh_length",
    "en_length",
    "model",
    "translated_at",
    "grammar_checked",
    "grammar_passed",
    "consistency_checked",
    "consistency_passed",
    "suggestion",
    "manual_fixed",
    "error_code",
    "error_message",
    "token_input",
    "token_output",
    "cost",
    "report_row_hash",
    "written_at",
    "backup_path",
    "updated_at",
}


ITEM_DEFAULTS = {
    "status": "pending",
    "zh_length": 0,
    "en_length": 0,
    "grammar_checked": 0,
    "grammar_passed": 0,
    "consistency_checked": 0,
    "consistency_passed": 0,
    "manual_fixed": 0,
    "token_input": 0,
    "token_output": 0,
    "cost": 0.0,
}


def insert_item(db: Database, data: dict[str, Any]) -> str:
    item_id = str(data.get("id") or _new_id("tri"))
    payload = {key: data.get(key) for key in ITEM_FIELDS}
    for key, value in ITEM_DEFAULTS.items():
        if payload.get(key) is None:
            payload[key] = value
    payload["id"] = item_id
    payload["job_id"] = data.get("job_id")
    payload["updated_at"] = data.get("updated_at") or now_iso()
    columns = [
        "id",
        "job_id",
        "data_group_id",
        "task_id",
        "folder_path",
        "zh_txt_path",
        "en_txt_path",
        "status",
        "zh_length",
        "en_length",
        "model",
        "translated_at",
        "grammar_checked",
        "grammar_passed",
        "consistency_checked",
        "consistency_passed",
        "suggestion",
        "manual_fixed",
        "error_code",
        "error_message",
        "token_input",
        "token_output",
        "cost",
        "report_row_hash",
        "written_at",
        "backup_path",
        "updated_at",
    ]
    placeholders = ",".join("?" for _ in columns)
    conn = db.connect()
    conn.execute(
        f"INSERT INTO translation_items({','.join(columns)}) VALUES ({placeholders})",
        tuple(payload.get(column) for column in columns),
    )
    conn.commit()
    return item_id


def update_item(db: Database, item_id: str, **fields: Any) -> None:
    updates = {key: value for key, value in fields.items() if key in ITEM_FIELDS}
    if not updates:
        return
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    conn = db.connect()
    conn.execute(
        f"UPDATE translation_items SET {assignments} WHERE id = ?",
        (*updates.values(), item_id),
    )
    conn.commit()


def list_items(
    db: Database, job_id: str, *, statuses: list[str] | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM translation_items WHERE job_id = ?"
    params: list[Any] = [job_id]
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        sql += f" AND status IN ({placeholders})"
        params.extend(statuses)
    sql += " ORDER BY folder_path COLLATE NOCASE"
    return [dict(row) for row in db.connect().execute(sql, params).fetchall()]


def get_item(db: Database, item_id: str) -> dict[str, Any] | None:
    row = db.connect().execute(
        "SELECT * FROM translation_items WHERE id = ?", (item_id,)
    ).fetchone()
    return dict(row) if row else None


def item_hash_exists(db: Database, row_hash: str) -> bool:
    if not row_hash:
        return False
    row = db.connect().execute(
        "SELECT 1 FROM translation_items WHERE report_row_hash = ?", (row_hash,)
    ).fetchone()
    return bool(row)


# ── 用量 ─────────────────────────────────────────────────

USAGE_FIELDS = (
    "job_id",
    "usage_date",
    "usage_time",
    "api_type",
    "endpoint",
    "total",
    "success",
    "failed",
    "token_in",
    "token_out",
    "token_total",
    "cost",
    "currency",
    "note",
    "source_report",
    "row_hash",
    "created_at",
)


def insert_usage(db: Database, data: dict[str, Any]) -> bool:
    """插入用量；row_hash 重复时返回 False（幂等导入）。"""
    row_hash = data.get("row_hash")
    conn = db.connect()
    if row_hash:
        existing = conn.execute(
            "SELECT 1 FROM translation_usage WHERE row_hash = ?", (row_hash,)
        ).fetchone()
        if existing:
            return False
    payload = {key: data.get(key) for key in USAGE_FIELDS}
    for key in (
        "total",
        "success",
        "failed",
        "token_in",
        "token_out",
        "token_total",
        "cost",
    ):
        if payload.get(key) is None:
            payload[key] = 0
    if not payload.get("currency"):
        payload["currency"] = "CNY"
    payload["id"] = data.get("id") or _new_id("tru")
    payload["created_at"] = data.get("created_at") or now_iso()
    columns = ["id", *USAGE_FIELDS]
    placeholders = ",".join("?" for _ in columns)
    try:
        conn.execute(
            f"INSERT INTO translation_usage({','.join(columns)}) VALUES ({placeholders})",
            tuple(payload.get(column) for column in columns),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def list_usage(
    db: Database, *, limit: int = 200, job_id: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM translation_usage"
    params: list[Any] = []
    if job_id:
        sql += " WHERE job_id = ?"
        params.append(job_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    return [dict(row) for row in db.connect().execute(sql, params).fetchall()]


def usage_summary(db: Database) -> dict[str, Any]:
    row = db.connect().execute(
        """
        SELECT COUNT(*) AS rows_count,
               COALESCE(SUM(total), 0) AS total,
               COALESCE(SUM(success), 0) AS success,
               COALESCE(SUM(failed), 0) AS failed,
               COALESCE(SUM(token_in), 0) AS token_in,
               COALESCE(SUM(token_out), 0) AS token_out,
               COALESCE(SUM(token_total), 0) AS token_total,
               COALESCE(SUM(cost), 0) AS cost
        FROM translation_usage
        """
    ).fetchone()
    return dict(row) if row else {}
