"""数据库备份、校验与清理。"""
from __future__ import annotations

import datetime as _dt
import os
import sqlite3
from pathlib import Path

from modules.config import app_config


def verify_database(db_path: str | Path) -> tuple[bool, str]:
    path = Path(db_path)
    if not path.is_file():
        return False, "数据库文件不存在"
    try:
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            conn.execute("PRAGMA query_only = ON")
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        value = str(row[0]) if row else "no result"
        return value.lower() == "ok", value
    except sqlite3.Error as exc:
        return False, str(exc)


def backup_database(
    db_path: str | Path | None = None,
    backup_dir: str | Path | None = None,
    label: str = "manual",
) -> Path:
    """使用 SQLite 在线备份 API，避免直接复制 WAL 状态不一致。"""
    source = Path(db_path) if db_path else app_config.get_db_path()
    if not source.is_file():
        raise FileNotFoundError(f"数据库不存在: {source}")
    target_dir = Path(backup_dir) if backup_dir else app_config.get_backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(ch for ch in label if ch.isalnum() or ch in "-_") or "manual"
    target = target_dir / f"management_{stamp}_{safe_label}.db"
    src = sqlite3.connect(str(source), timeout=5.0)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    ok, detail = verify_database(target)
    if not ok:
        try:
            target.unlink()
        except OSError:
            pass
        raise RuntimeError(f"备份校验失败: {detail}")
    return target


def list_backups(backup_dir: str | Path | None = None) -> list[dict]:
    base = Path(backup_dir) if backup_dir else app_config.get_backup_dir()
    if not base.is_dir():
        return []
    items = []
    for path in sorted(base.glob("management_*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            st = path.stat()
        except OSError:
            continue
        items.append({"path": str(path), "name": path.name, "size": st.st_size, "mtime": st.st_mtime})
    return items


def prune_backups(backup_dir: str | Path | None = None, keep: int = 20) -> list[str]:
    """保留最近 N 份备份，返回被删除的文件路径。"""
    items = list_backups(backup_dir)
    removed: list[str] = []
    for item in items[max(0, keep):]:
        try:
            os.remove(item["path"])
            removed.append(item["path"])
        except OSError:
            continue
    return removed
