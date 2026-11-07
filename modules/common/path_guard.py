"""路径规范化与边界保护。

只做纯路径计算，不创建/删除/移动任何文件。
"""
from __future__ import annotations

import fnmatch
import os
import stat
from pathlib import Path


def to_long_path(path: str | os.PathLike[str]) -> str:
    """Windows 长路径前缀；非 Windows 原样返回。"""
    p = os.path.abspath(os.fspath(path))
    if os.name != "nt":
        return p
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def normalize_path(path: str | os.PathLike[str], base: str | os.PathLike[str] | None = None) -> str:
    """展开环境变量/用户目录并返回绝对规范路径。"""
    raw = os.path.expandvars(os.path.expanduser(os.fspath(path)))
    if base and not os.path.isabs(raw):
        raw = os.path.join(os.fspath(base), raw)
    return os.path.normpath(os.path.abspath(raw))


def is_relative_to(child: str | os.PathLike[str], parent: str | os.PathLike[str]) -> bool:
    """大小写不敏感地判断 child 是否位于 parent 内。"""
    try:
        child_n = os.path.normcase(normalize_path(child))
        parent_n = os.path.normcase(normalize_path(parent))
        return os.path.commonpath([child_n, parent_n]) == parent_n
    except (ValueError, OSError):
        return False


def is_within(child: str | os.PathLike[str], parent: str | os.PathLike[str]) -> bool:
    return is_relative_to(child, parent)


def safe_join(root: str | os.PathLike[str], *parts: str) -> str:
    """安全拼接；拒绝绝对路径与 .. 越界。"""
    root_n = normalize_path(root)
    for part in parts:
        if os.path.isabs(part):
            raise ValueError(f"不允许绝对路径片段: {part!r}")
        if any(seg == ".." for seg in Path(part).parts):
            raise ValueError(f"不允许 .. 路径片段: {part!r}")
    joined = normalize_path(os.path.join(root_n, *parts))
    if not is_relative_to(joined, root_n):
        raise ValueError(f"路径越界: {joined}")
    return joined


def is_unc(path: str | os.PathLike[str]) -> bool:
    p = os.fspath(path)
    return p.startswith("\\\\") and not p.startswith("\\\\?\\")


def is_ignored_name(name: str, patterns: list[str] | tuple[str, ...] | None) -> bool:
    if not patterns:
        return False
    lower = name.casefold()
    for pattern in patterns:
        if fnmatch.fnmatch(lower, pattern.casefold()):
            return True
    return False


def is_reparse_point(path: str | os.PathLike[str]) -> bool:
    """符号链接 / junction / 其他重解析点。"""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def is_forbidden_data_dir(path: str | os.PathLike[str], program_dir: str | os.PathLike[str]) -> tuple[bool, str]:
    """数据目录不能放在程序目录、UNC 或文件路径上。"""
    p = normalize_path(path)
    if is_unc(path):
        return True, "默认不允许把管理数据放在 UNC 共享目录"
    if os.path.exists(p) and not os.path.isdir(p):
        return True, "目标路径已存在且不是目录"
    if is_within(p, program_dir):
        return True, "管理数据不能放在程序安装目录内"
    return False, ""


def validate_root(path: str | os.PathLike[str]) -> tuple[bool, str]:
    """只读扫描根目录校验，不创建目录。"""
    if not path:
        return False, "未填写扫描根目录"
    p = normalize_path(path)
    if not os.path.exists(p):
        return False, "根目录不存在"
    if not os.path.isdir(p):
        return False, "根路径不是目录"
    if not os.access(p, os.R_OK):
        return False, "根目录不可读"
    return True, p
