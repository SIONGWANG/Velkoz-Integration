"""只读文件系统封装。

本模块只提供读取操作，不包含写入/删除/移动 API。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterator

from .path_guard import is_reparse_point, normalize_path


@dataclass(frozen=True)
class DirEntryInfo:
    path: str
    name: str
    is_dir: bool
    is_reparse: bool


class ReadOnlyFS:
    """限制在 root 内的只读浏览。"""

    def __init__(self, root: str):
        self.root = normalize_path(root)

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def list_dir(self, path: str) -> list[DirEntryInfo]:
        items: list[DirEntryInfo] = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    items.append(
                        DirEntryInfo(
                            path=entry.path,
                            name=entry.name,
                            is_dir=entry.is_dir(follow_symlinks=False),
                            is_reparse=is_reparse_point(entry.path),
                        )
                    )
        except OSError:
            return []
        return items

    def iter_dirs(self, path: str) -> Iterator[DirEntryInfo]:
        for item in self.list_dir(path):
            if item.is_dir and not item.is_reparse:
                yield item

    def read_text(self, path: str, max_bytes: int = 256 * 1024) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_bytes)
