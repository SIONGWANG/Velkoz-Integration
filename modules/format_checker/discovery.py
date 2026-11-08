"""只读目录发现：找出候选任务目录。

不写任何文件，不跟随符号链接/junction。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

from modules.common.path_guard import is_ignored_name, is_reparse_point, normalize_path

from .errors import make_issue
from .id_rules import IMAGE_EXTENSIONS, IdMatcher
from .models import Issue, Template

ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


@dataclass(frozen=True)
class TaskDir:
    path: str
    name: str
    task_id: str
    depth: int


@dataclass
class DiscoveryResult:
    root: str
    tasks: list[TaskDir] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    duplicate_ids: dict[str, list[str]] = field(default_factory=dict)
    total_dirs: int = 0
    truncated: bool = False
    cancelled: bool = False


def _path_too_long(path: str) -> bool:
    return len(path) > 260


def discover_tasks(
    root_path: str,
    template: Template,
    matcher: IdMatcher,
    *,
    max_tasks: int | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> DiscoveryResult:
    root = normalize_path(root_path)
    result = DiscoveryResult(root=root)
    limit = int(max_tasks if max_tasks is not None else template.scan.max_tasks)

    if not os.path.exists(root):
        result.issues.append(make_issue("FC-000", task_path=root, actual=root))
        return result
    if not os.path.isdir(root):
        result.issues.append(make_issue("FC-000", task_path=root, actual=root))
        return result

    seen_paths: set[str] = set()

    def add_task(path: str, name: str, depth: int) -> None:
        key = os.path.normcase(path)
        if key in seen_paths:
            return
        seen_paths.add(key)
        result.tasks.append(TaskDir(path=path, name=name, task_id=name, depth=depth))
        result.total_dirs += 1
        if _path_too_long(path):
            result.issues.append(
                make_issue("FC-009", task_id=name, task_path=path, actual=str(len(path)))
            )

    def check_limit() -> bool:
        if len(result.tasks) >= limit:
            result.truncated = True
            result.issues.append(
                make_issue("FC-008", task_path=root, expected=str(limit), actual=str(len(result.tasks)))
            )
            return True
        return False

    def scan_dir(path: str, depth: int, descend: bool) -> None:
        if result.truncated or result.cancelled:
            return
        if should_cancel and should_cancel():
            result.cancelled = True
            return
        name = os.path.basename(path)
        if is_reparse_point(path):
            result.issues.append(
                make_issue("FC-007", task_id=name, task_path=path)
            )
            return
        if is_ignored_name(name, template.scan.ignore_names):
            return
        if matcher.matches(name):
            add_task(path, name, depth)
            if progress:
                progress(len(result.tasks), f"发现任务：{path}")
            return
        if not descend:
            return
        try:
            with os.scandir(path) as it:
                entries = [entry for entry in it if entry.is_dir(follow_symlinks=False)]
        except OSError as exc:
            result.issues.append(
                make_issue("FC-006", task_id=name, task_path=path, message=f"目录不可读：{exc}")
            )
            return
        for entry in sorted(entries, key=lambda item: item.name.casefold()):
            if result.truncated or result.cancelled:
                return
            if check_limit():
                return
            scan_dir(entry.path, depth + 1, True)

    try:
        if matcher.matches(os.path.basename(root)):
            add_task(root, os.path.basename(root), 0)
        elif template.scan.recursive:
            scan_dir(root, 0, True)
        else:
            try:
                with os.scandir(root) as it:
                    entries = [entry for entry in it if entry.is_dir(follow_symlinks=False)]
            except OSError as exc:
                result.issues.append(
                    make_issue("FC-006", task_path=root, message=f"根目录不可读：{exc}")
                )
                return result
            for entry in sorted(entries, key=lambda item: item.name.casefold()):
                if check_limit():
                    break
                if should_cancel and should_cancel():
                    result.cancelled = True
                    break
                if is_ignored_name(entry.name, template.scan.ignore_names):
                    continue
                if is_reparse_point(entry.path):
                    result.issues.append(
                        make_issue("FC-007", task_id=entry.name, task_path=entry.path)
                    )
                    continue
                if matcher.matches(entry.name):
                    add_task(entry.path, entry.name, 1)
                    if progress:
                        progress(len(result.tasks), f"发现任务：{entry.path}")
    except OSError as exc:
        result.issues.append(make_issue("FC-006", task_path=root, message=f"扫描失败：{exc}"))

    # 重复 ID
    by_id: dict[str, list[str]] = {}
    for task in result.tasks:
        by_id.setdefault(task.task_id.casefold(), []).append(task.path)
    result.duplicate_ids = {key: value for key, value in by_id.items() if len(value) > 1}
    for paths in result.duplicate_ids.values():
        for path in paths:
            name = os.path.basename(path)
            result.issues.append(
                make_issue(
                    "FC-004",
                    task_id=name,
                    task_path=path,
                    expected="同一 ID 只出现一次",
                    actual="；".join(item for item in paths if item != path),
                )
            )

    # 根目录本身看起来像任务目录，但名称不符合 ID 规则
    if (
        not result.tasks
        and not result.cancelled
        and not result.truncated
        and not matcher.matches(os.path.basename(root))
        and _has_business_files(root)
    ):
        result.issues.append(
            make_issue(
                "FC-001",
                task_path=root,
                expected=matcher.describe(),
                actual=os.path.basename(root),
                message="根目录名不符合 ID 规则，且未发现任务子目录",
            )
        )

    result.tasks.sort(key=lambda item: item.path.casefold())
    return result


def _has_business_files(path: str) -> bool:
    allowed = set(IMAGE_EXTENSIONS) | {"txt"}
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                ext = os.path.splitext(entry.name)[1].lower().lstrip(".")
                if ext in allowed:
                    return True
    except OSError:
        return False
    return False
