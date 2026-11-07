"""文件/目录指纹。

扫描默认只做轻量指纹（名称 + 大小 + mtime），不读取图片内容；
需要强校验时再显式调用 hash_file。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

_CHUNK = 1024 * 1024


def hash_file(path: str | os.PathLike[str], algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def stat_fingerprint(path: str | os.PathLike[str]) -> str:
    st = os.stat(path)
    raw = f"{os.path.basename(path)}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()


def directory_fingerprint(files: list[os.DirEntry] | list[tuple[str, int, int]]) -> str:
    """目录轻量指纹：相对名 + 大小 + mtime_ns，排序后哈希。"""
    rows: list[str] = []
    for item in files:
        if isinstance(item, tuple):
            name, size, mtime_ns = item
        else:
            st = item.stat(follow_symlinks=False)
            name, size, mtime_ns = item.name, st.st_size, st.st_mtime_ns
        rows.append(f"{name}|{size}|{mtime_ns}")
    rows.sort(key=str.casefold)
    return hashlib.sha256("\n".join(rows).encode("utf-8", "surrogatepass")).hexdigest()


def file_entries(path: str | os.PathLike[str]) -> list[tuple[str, int, int]]:
    entries: list[tuple[str, int, int]] = []
    with os.scandir(path) as it:
        for entry in it:
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
                entries.append((entry.name, st.st_size, st.st_mtime_ns))
            except OSError:
                continue
    return entries


def directory_fingerprint_from_path(path: str | os.PathLike[str]) -> str:
    return directory_fingerprint(file_entries(path))
