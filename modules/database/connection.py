"""SQLite 连接、迁移与健康检查。"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Iterable

from modules.config import app_config

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _split_sql(script: str) -> Iterable[str]:
    """按分号拆分简单 DDL；跳过注释与空语句。"""
    buf: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
    text = "\n".join(buf)
    for part in text.split(";"):
        statement = part.strip()
        if statement:
            yield statement


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            checksum TEXT NOT NULL
        )
        """
    )


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row[0]) for row in rows}


class Database:
    """管理数据库包装。

    只用于管理索引与日志，不保存图片/TXT/CSV 原文。
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else app_config.get_db_path()
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA temp_store = MEMORY")
        self._conn = conn
        return conn

    def migrate(self) -> list[int]:
        conn = self.connect()
        _ensure_migrations_table(conn)
        conn.commit()
        applied = _applied_versions(conn)
        newly: list[int] = []
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            try:
                version = int(migration.name.split("_", 1)[0])
            except (IndexError, ValueError):
                continue
            if version in applied:
                continue
            script = migration.read_text(encoding="utf-8")
            checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
            conn.execute("BEGIN")
            try:
                for statement in _split_sql(script):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at, checksum) VALUES (?, datetime('now'), ?)",
                    (version, checksum),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            newly.append(version)
        return newly

    def schema_version(self) -> int:
        conn = self.connect()
        _ensure_migrations_table(conn)
        row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0)

    def integrity_check(self) -> tuple[bool, str]:
        conn = self.connect()
        row = conn.execute("PRAGMA integrity_check").fetchone()
        value = str(row[0]) if row else "no result"
        return value.lower() == "ok", value

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def open_database(db_path: str | Path | None = None, migrate: bool = True) -> Database:
    db = Database(db_path)
    if migrate:
        db.migrate()
    return db
