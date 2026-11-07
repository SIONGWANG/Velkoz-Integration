"""结构化操作日志。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from modules.common.timeutil import now_iso
from modules.database.connection import Database


class AuditLogger:
    def __init__(self, database: Database):
        self.db = database

    def log(
        self,
        module: str,
        action: str,
        *,
        operator: str = "",
        status: str = "ok",
        target_path: str | None = None,
        source_path: str | None = None,
        detail: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
        run_id: str | None = None,
        batch_id: str | None = None,
    ) -> str:
        log_id = str(uuid.uuid4())
        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO operation_logs(
                id, operator, module, action, status, target_path, source_path,
                detail_json, error_code, error_message, duration_ms, run_id, batch_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                operator or "",
                module,
                action,
                status,
                target_path,
                source_path,
                json.dumps(detail or {}, ensure_ascii=False),
                error_code,
                error_message,
                duration_ms,
                run_id,
                batch_id,
                now_iso(),
            ),
        )
        conn.commit()
        return log_id


def log_operation(db_path: str | Path | None = None, **kwargs: Any) -> str:
    db = Database(db_path)
    db.migrate()
    try:
        return AuditLogger(db).log(**kwargs)
    finally:
        db.close()
