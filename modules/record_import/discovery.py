"""只读发现 CSV/Excel 文件并识别类型。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from modules.common.path_guard import (
    is_ignored_name,
    is_reparse_point,
    normalize_path,
)

from .errors import RecordImportError
from .models import (
    DEFAULT_IGNORE_NAMES,
    FILE_ERROR,
    FILE_READY,
    FILE_UNRECOGNIZED,
    FILE_UNSUPPORTED,
    MAX_FILES,
    SUPPORTED_EXTENSIONS,
    DiscoveredRecordFile,
)
from .parser import inspect_table

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


@dataclass
class DiscoveryResult:
    root: str = ""
    files: list[DiscoveredRecordFile] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    truncated: bool = False
    cancelled: bool = False
    scanned_dirs: int = 0

    @property
    def ready_files(self) -> list[DiscoveredRecordFile]:
        return [item for item in self.files if item.importable]

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in self.files:
            result[item.status] = result.get(item.status, 0) + 1
        return result


def _iter_files(root: str, recursive: bool) -> tuple[list[str], int]:
    """返回 ``(文件列表, 已扫描目录数)``；只读遍历，不跟随符号链接。"""
    if os.path.isfile(root):
        return [root], 1
    files: list[str] = []
    if not os.path.isdir(root):
        return files, 0
    if not recursive:
        try:
            with os.scandir(root) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            files.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            return [], 0
        return sorted(files, key=os.path.basename), 1
    scanned = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                scanned += 1
                for entry in it:
                    try:
                        if is_reparse_point(entry.path):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            files.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(files, key=lambda item: item.casefold()), scanned


def _discover_one(path: str, ignore_names: tuple[str, ...]) -> DiscoveredRecordFile | None:
    name = os.path.basename(path)
    if is_ignored_name(name, ignore_names):
        return None
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    if ext not in SUPPORTED_EXTENSIONS:
        return None
    entry = DiscoveredRecordFile(path=normalize_path(path), name=name, ext=ext)
    try:
        stat = os.stat(entry.path)
        entry.size = int(stat.st_size)
        entry.mtime_ns = int(stat.st_mtime_ns)
    except OSError as exc:
        entry.status = FILE_ERROR
        entry.error_code = "RI-002"
        entry.note = f"无法读取文件信息：{exc}"
        return entry
    if ext == "xls":
        entry.status = FILE_UNSUPPORTED
        entry.error_code = "RI-005"
        entry.note = "旧版 .xls 暂不支持，请另存为 .xlsx"
        return entry
    try:
        info = inspect_table(entry.path)
    except RecordImportError as exc:
        entry.status = FILE_ERROR
        entry.error_code = exc.code
        entry.note = exc.message
        return entry
    except Exception as exc:
        entry.status = FILE_ERROR
        entry.error_code = "RI-002"
        entry.note = f"读取失败：{type(exc).__name__}"
        return entry
    entry.source_type = str(info.get("source_type") or "")
    entry.operator = str(info.get("operator") or "")
    entry.rows_total = int(info.get("rows_total") or 0)
    entry.columns = list(info.get("headers") or [])
    entry.encoding = str(info.get("encoding") or "")
    if entry.source_type == "unclassified":
        entry.status = FILE_UNRECOGNIZED
        entry.error_code = "RI-004"
        entry.note = "未找到图片ID/备注/标签等可识别列"
    else:
        entry.status = FILE_READY
    return entry


def discover_files(
    root_path: str,
    *,
    recursive: bool = False,
    max_files: int = MAX_FILES,
    ignore_names: tuple[str, ...] = DEFAULT_IGNORE_NAMES,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> DiscoveryResult:
    root = normalize_path(root_path)
    result = DiscoveryResult(root=root)
    if not os.path.exists(root):
        result.issues.append(f"RI-000 根目录不存在：{root}")
        return result
    if not os.path.isdir(root) and not os.path.isfile(root):
        result.issues.append(f"RI-000 不是有效的目录或文件：{root}")
        return result
    all_files, result.scanned_dirs = _iter_files(root, recursive)
    if len(all_files) > max_files:
        all_files = all_files[:max_files]
        result.truncated = True
        result.issues.append(f"文件数超过上限 {max_files}，结果已截断")
    total = len(all_files)
    for index, path in enumerate(all_files, start=1):
        if should_cancel and should_cancel():
            result.cancelled = True
            break
        if progress:
            progress(index - 1, total, f"检查文件 {index}/{total}：{os.path.basename(path)}")
        entry = _discover_one(path, ignore_names)
        if entry is not None:
            result.files.append(entry)
    if not result.files and not result.cancelled:
        result.issues.append("RI-001 未发现 CSV/Excel 文件")
    if progress:
        progress(len(result.files), max(total, 1), "文件发现完成")
    return result


def summary(result: DiscoveryResult) -> dict[str, Any]:
    counts = result.counts()
    return {
        "root": result.root,
        "files": len(result.files),
        "ready": counts.get(FILE_READY, 0),
        "unrecognized": counts.get(FILE_UNRECOGNIZED, 0),
        "unsupported": counts.get(FILE_UNSUPPORTED, 0),
        "errors": counts.get(FILE_ERROR, 0),
        "truncated": result.truncated,
        "cancelled": result.cancelled,
        "issues": list(result.issues),
    }
