"""应用数据目录与数据库路径。

默认数据目录：D:\\VelkozDATA（用户 2026-11-07 指定）。
可通过环境变量 VELKOZ_DATA_DIR 或设置页改到其他本地目录。
"""
from __future__ import annotations

import os
from pathlib import Path

from modules.common.path_guard import normalize_path

PROGRAM_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = Path(r"D:\VelkozDATA")
ENV_DATA_DIR = "VELKOZ_DATA_DIR"
DB_FILENAME = "management.db"


def _bootstrap_file() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "VelkozPro" / "data_dir.txt"


def get_data_dir() -> Path:
    env_value = os.environ.get(ENV_DATA_DIR, "").strip()
    if env_value:
        return Path(normalize_path(env_value))
    bootstrap = _bootstrap_file()
    try:
        if bootstrap.is_file():
            value = bootstrap.read_text(encoding="utf-8").strip()
            if value:
                return Path(normalize_path(value))
    except OSError:
        pass
    return DEFAULT_DATA_DIR


def set_data_dir(path: str | os.PathLike[str]) -> Path:
    """记录用户选择的数据目录；下次启动/重新初始化时生效。"""
    target = Path(normalize_path(path))
    bootstrap = _bootstrap_file()
    bootstrap.parent.mkdir(parents=True, exist_ok=True)
    bootstrap.write_text(str(target), encoding="utf-8")
    return target


def reset_data_dir() -> None:
    try:
        _bootstrap_file().unlink()
    except OSError:
        pass


def get_db_path(data_dir: str | os.PathLike[str] | None = None) -> Path:
    base = Path(data_dir) if data_dir else get_data_dir()
    return base / "data" / DB_FILENAME


def get_backup_dir(data_dir: str | os.PathLike[str] | None = None) -> Path:
    base = Path(data_dir) if data_dir else get_data_dir()
    return base / "backups"


def get_reports_dir(data_dir: str | os.PathLike[str] | None = None) -> Path:
    base = Path(data_dir) if data_dir else get_data_dir()
    return base / "reports"


def get_logs_dir(data_dir: str | os.PathLike[str] | None = None) -> Path:
    base = Path(data_dir) if data_dir else get_data_dir()
    return base / "logs"


def ensure_data_dirs(data_dir: str | os.PathLike[str] | None = None) -> dict[str, Path]:
    """创建管理数据目录（只创建本平台自己的目录，不碰源数据）。"""
    base = Path(data_dir) if data_dir else get_data_dir()
    dirs = {
        "data": base / "data",
        "backups": base / "backups",
        "reports": base / "reports",
        "logs": base / "logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def describe_paths(data_dir: str | os.PathLike[str] | None = None) -> dict[str, str]:
    base = Path(data_dir) if data_dir else get_data_dir()
    return {
        "data_dir": str(base),
        "db_path": str(get_db_path(base)),
        "backup_dir": str(get_backup_dir(base)),
        "reports_dir": str(get_reports_dir(base)),
        "logs_dir": str(get_logs_dir(base)),
    }
