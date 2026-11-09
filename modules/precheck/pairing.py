"""TXT 配对扫描：只读发现任务目录、中文/英文 TXT 与状态。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

from modules.common.path_guard import (
    is_ignored_name,
    is_reparse_point,
    normalize_path,
)
from modules.format_checker.id_rules import IdMatcher

from .errors import PrecheckError
from .models import (
    PairItem,
    PairingOptions,
    STATUS_EMPTY_EN,
    STATUS_EMPTY_ZH,
    STATUS_INVALID,
    STATUS_MISSING_EN,
    STATUS_NO_ZH,
    STATUS_TRANSLATED,
)
from .safety import read_text_file, text_length, word_count

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


@dataclass
class PairScanIssue:
    code: str
    message: str
    path: str = ""


@dataclass
class PairScanResult:
    items: list[PairItem] = field(default_factory=list)
    issues: list[PairScanIssue] = field(default_factory=list)
    truncated: bool = False
    cancelled: bool = False
    scanned_dirs: int = 0
    root_path: str = ""

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in self.items:
            result[item.status] = result.get(item.status, 0) + 1
        return result

    @property
    def missing_en(self) -> list[PairItem]:
        return [
            item
            for item in self.items
            if item.status in {STATUS_MISSING_EN, STATUS_EMPTY_EN, STATUS_EMPTY_ZH}
        ]


def _list_dirs(path: str) -> list[str]:
    items: list[str] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if is_reparse_point(entry.path):
                    continue
                items.append(entry.path)
    except OSError:
        return []
    return sorted(items, key=os.path.basename)


def _find_txt(files: list[str], suffixes: tuple[str, ...], extension: str) -> tuple[str, list[str]]:
    """返回 (首个匹配文件, 全部匹配文件)。大小写不敏感。"""
    ext = extension.lower()
    suffix_lower = tuple(item.lower() for item in suffixes)
    matches = [
        name
        for name in files
        if name.lower().endswith(ext)
        and name.lower()[: -len(ext)].endswith(suffix_lower)
    ]
    matches.sort(key=str.casefold)
    return (matches[0] if matches else ""), matches


def _has_content(path: str) -> bool:
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def _scan_task_dir(
    folder: str,
    task_id: str,
    options: PairingOptions,
) -> PairItem:
    item = PairItem(task_id=task_id, folder_path=folder)
    try:
        with os.scandir(folder) as it:
            files = [
                entry.name
                for entry in it
                if entry.is_file(follow_symlinks=False)
            ]
    except OSError as exc:
        item.status = STATUS_INVALID
        item.error_code = "PC-006"
        item.error_message = f"目录无法读取：{exc}"
        return item

    suffixes = options.txt_extension
    zh_name, zh_all = _find_txt(files, options.zh_suffixes, suffixes)
    en_name, en_all = _find_txt(files, options.en_suffixes, suffixes)
    if en_name and en_name == zh_name:
        en_name = en_all[1] if len(en_all) > 1 else ""

    item.zh_txt_path = os.path.join(folder, zh_name) if zh_name else ""
    item.en_txt_path = os.path.join(folder, en_name) if en_name else ""

    if not item.zh_txt_path:
        item.status = STATUS_NO_ZH
        item.error_code = "PC-002"
        item.error_message = "未找到中文 TXT"
        return item

    if options.load_text:
        try:
            item.zh_text, item.zh_encoding = read_text_file(
                item.zh_txt_path, max_bytes=options.max_text_bytes
            )
            item.zh_length = text_length(item.zh_text)
        except PrecheckError as exc:
            item.status = STATUS_INVALID
            item.error_code = exc.code
            item.error_message = exc.message
            return item
        if not item.zh_text.strip():
            item.status = STATUS_EMPTY_ZH
            item.error_code = "PC-005"
            item.error_message = "中文 TXT 为空"
            return item
    elif not _has_content(item.zh_txt_path):
        item.status = STATUS_EMPTY_ZH
        item.error_code = "PC-005"
        item.error_message = "中文 TXT 为空"
        return item

    if not item.en_txt_path:
        item.status = STATUS_MISSING_EN
        item.error_code = "PC-003"
        item.error_message = "未找到英文 TXT"
        return item

    if options.load_text:
        try:
            item.en_text, item.en_encoding = read_text_file(
                item.en_txt_path, max_bytes=options.max_text_bytes
            )
            item.en_length = word_count(item.en_text)
        except PrecheckError as exc:
            item.status = STATUS_INVALID
            item.error_code = exc.code
            item.error_message = exc.message
            return item
        if not item.en_text.strip():
            item.status = STATUS_EMPTY_EN
            item.error_code = "PC-004"
            item.error_message = "英文 TXT 为空"
            return item
    elif not _has_content(item.en_txt_path):
        item.status = STATUS_EMPTY_EN
        item.error_code = "PC-004"
        item.error_message = "英文 TXT 为空"
        return item

    item.status = STATUS_TRANSLATED
    return item


def scan_pairs(
    root_path: str,
    options: PairingOptions | None = None,
    *,
    task_paths: set[str] | tuple[str, ...] | list[str] | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> PairScanResult:
    """只读扫描根目录，返回 TXT 配对结果。"""
    opts = options or PairingOptions()
    root = normalize_path(root_path)
    result = PairScanResult(root_path=root)
    if not os.path.isdir(root):
        result.issues.append(
            PairScanIssue("PC-000", f"根目录不存在或不是目录：{root}", root)
        )
        return result

    wanted: set[str] | None = None
    if task_paths:
        wanted = {os.path.normcase(normalize_path(item)) for item in task_paths if item}

    matcher = IdMatcher(opts.id_rule)
    dirs: list[str] = []
    if opts.recursive:
        stack = [root]
        while stack:
            current = stack.pop()
            children = _list_dirs(current)
            dirs.extend(children)
            stack.extend(reversed(children))
            result.scanned_dirs += 1
            if len(dirs) > opts.max_tasks * 4:
                break
    else:
        dirs = _list_dirs(root)
        result.scanned_dirs = 1

    candidates = [
        path
        for path in dirs
        if matcher.matches(os.path.basename(path))
        and (wanted is None or os.path.normcase(path) in wanted)
    ]

    if len(candidates) > opts.max_tasks:
        candidates = candidates[: opts.max_tasks]
        result.truncated = True
        result.issues.append(
            PairScanIssue(
                "PC-001",
                f"任务数超过上限 {opts.max_tasks}，结果已截断",
                root,
            )
        )

    total = len(candidates)
    for index, folder in enumerate(candidates, start=1):
        if should_cancel and should_cancel():
            result.cancelled = True
            break
        if progress:
            progress(index - 1, total, f"扫描任务 {index}/{total}：{folder}")
        result.items.append(_scan_task_dir(folder, os.path.basename(folder), opts))

    # 重复 ID 提示
    by_id: dict[str, list[str]] = {}
    for item in result.items:
        by_id.setdefault(item.task_id.casefold(), []).append(item.folder_path)
    for key, paths in by_id.items():
        if len(paths) > 1:
            result.issues.append(
                PairScanIssue(
                    "PC-001",
                    f"任务 ID 重复 {len(paths)} 次：{key}",
                    paths[0],
                )
            )

    if not result.items and not result.cancelled:
        result.issues.append(
            PairScanIssue(
                "PC-001", "未发现符合 ID 规则的任务目录", root
            )
        )
    if progress:
        progress(len(result.items), max(total, 1), "配对扫描完成")
    return result


def derive_en_path(zh_path: str, options: PairingOptions) -> str:
    """根据中文 TXT 推导英文 TXT 路径（同目录、同任务 ID）。"""
    folder = os.path.dirname(zh_path)
    name = os.path.basename(zh_path)
    stem, ext = os.path.splitext(name)
    for suffix in options.zh_suffixes:
        if stem.lower().endswith(suffix.lower()):
            base = stem[: -len(suffix)]
            return os.path.join(folder, f"{base}{options.en_suffixes[0]}{ext}")
    return os.path.join(folder, f"{stem}{options.en_suffixes[0]}{ext}")


def is_system_noise(name: str, ignore_names: tuple[str, ...]) -> bool:
    return is_ignored_name(name, ignore_names)
