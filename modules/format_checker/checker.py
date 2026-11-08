"""格式检查编排：发现 → 匹配 → 完整性校验 → 结果汇总。

本模块只读取源目录元信息；不写源目录，不修改文件。
"""
from __future__ import annotations

import dataclasses
import os
import time
import uuid
from collections import Counter
from datetime import datetime
from typing import Any, Callable

from modules.common.fingerprint import directory_fingerprint_from_path
from modules.common.path_guard import normalize_path
from modules.common.timeutil import now_iso

from .discovery import TaskDir, discover_tasks
from .errors import make_issue
from .id_rules import IdMatcher
from .matcher import check_task
from .models import (
    FormatCheckResult,
    RunSummary,
    TaskResult,
    Template,
)

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


def _new_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"fmt_{stamp}_{uuid.uuid4().hex[:8]}"


def _with_recursive(template: Template, recursive: bool | None) -> Template:
    if recursive is None or recursive == template.scan.recursive:
        return template
    return dataclasses.replace(
        template,
        scan=dataclasses.replace(template.scan, recursive=bool(recursive)),
    )


def _duplicate_map(discovery) -> dict[str, list[str]]:
    return discovery.duplicate_ids


def check_path(
    root_path: str,
    template: Template,
    *,
    recursive: bool | None = None,
    max_tasks: int | None = None,
    only_paths: set[str] | None = None,
    operator: str = "",
    batch_id: str | None = None,
    previous_run_id: str | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> FormatCheckResult:
    """只读检查一个根目录。

    only_paths 不为 None 时，只检查该集合中的任务路径（用于“仅异常/重跑”）。
    该函数不写数据库、不写报告。
    """
    started = time.perf_counter()
    started_at = now_iso()
    effective = _with_recursive(template, recursive)
    matcher = IdMatcher(effective.id_rule)

    summary = RunSummary(
        run_id=_new_run_id(),
        root_path=normalize_path(root_path),
        template_id=effective.id,
        template_name=effective.name,
        template_version=effective.version,
        started_at=started_at,
        batch_id=batch_id or "",
        operator=operator,
        previous_run_id=previous_run_id or "",
    )
    result = FormatCheckResult(summary=summary)

    discovery = discover_tasks(
        root_path,
        effective,
        matcher,
        max_tasks=max_tasks,
        progress=(lambda done, message: progress(done, max(done, 1), message))
        if progress
        else None,
        should_cancel=should_cancel,
    )
    result.run_issues.extend(
        issue for issue in discovery.issues if issue.code != "FC-004"
    )
    summary.truncated = discovery.truncated

    selected: list[TaskDir] = discovery.tasks
    if only_paths is not None:
        wanted = {os.path.normcase(normalize_path(item)) for item in only_paths if item}
        selected = [task for task in selected if os.path.normcase(task.path) in wanted]

    duplicates = _duplicate_map(discovery)
    total = len(selected)
    cancelled = discovery.cancelled

    for index, task in enumerate(selected, start=1):
        if should_cancel and should_cancel():
            cancelled = True
            break
        if progress:
            progress(index - 1, total, f"检查任务 {index}/{total}：{task.path}")

        try:
            before = directory_fingerprint_from_path(task.path)
        except OSError:
            before = ""

        try:
            task_result = check_task(
                task.path,
                task.task_id,
                effective,
                matcher,
                duplicate_of=[
                    other
                    for other in duplicates.get(task.task_id.casefold(), [])
                    if other != task.path
                ],
            )
        except Exception as exc:  # 单任务异常不终止全量检查
            task_result = TaskResult(
                task_id=task.task_id,
                task_path=task.path,
                detected_mode=None,
                result="fail",
                issues=[
                    make_issue(
                        "FC-304",
                        task_id=task.task_id,
                        task_path=task.path,
                        message=f"检查任务时发生错误：{exc}",
                    )
                ],
            )

        if before and task_result.fingerprint and before != task_result.fingerprint:
            task_result.issues.append(
                make_issue(
                    "FC-311",
                    task_id=task.task_id,
                    task_path=task.path,
                    expected=before,
                    actual=task_result.fingerprint,
                )
            )
            task_result.result = _determine_result(
                task_result.issues, effective.warn_as_fail
            )
            summary.integrity_status = "warning"
            result.integrity_changes.append(
                {
                    "task_id": task.task_id,
                    "task_path": task.path,
                    "before": before,
                    "after": task_result.fingerprint,
                }
            )

        result.tasks.append(task_result)

    if cancelled:
        summary.status = "cancelled"
        summary.skipped_tasks = max(0, total - len(result.tasks))
    else:
        summary.status = "completed"

    counts = Counter(item.result for item in result.tasks)
    summary.scanned_tasks = len(result.tasks)
    summary.passed_tasks = counts.get("pass", 0)
    summary.failed_tasks = counts.get("fail", 0)
    summary.warning_tasks = counts.get("warn", 0)
    summary.skipped_tasks = max(summary.skipped_tasks, counts.get("skipped", 0))

    codes = Counter(issue.code for issue in result.run_issues)
    for item in result.tasks:
        codes.update(issue.code for issue in item.issues)
    summary.issues_by_code = dict(sorted(codes.items()))

    summary.finished_at = now_iso()
    summary.duration_ms = int((time.perf_counter() - started) * 1000)
    if progress:
        progress(len(result.tasks), max(total, 1), "检查完成")
    return result


def preview_path(
    root_path: str,
    template: Template,
    *,
    recursive: bool | None = None,
    max_tasks: int | None = None,
) -> FormatCheckResult:
    """不落库的快速预览。"""
    return check_path(
        root_path,
        template,
        recursive=recursive,
        max_tasks=max_tasks,
    )


def _determine_result(issues, warn_as_fail: bool) -> str:
    if any(item.severity == "error" for item in issues):
        return "fail"
    if any(item.severity == "warn" for item in issues):
        return "fail" if warn_as_fail else "warn"
    return "pass"


def _task_key(path: str) -> str:
    return os.path.normcase(normalize_path(path))


def diff_task_results(
    old: dict[str, dict[str, Any]],
    new: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """对比两次检查的任务结果。

    入参键应为任务路径；值至少包含 task_path/task_id/result/file_count/issue_codes。
    """
    old_map = {_task_key(key): value for key, value in old.items()}
    new_map = {_task_key(key): value for key, value in new.items()}

    added_keys = sorted(set(new_map) - set(old_map))
    removed_keys = sorted(set(old_map) - set(new_map))
    common = sorted(set(old_map) & set(new_map))

    pass_to_fail: list[dict[str, Any]] = []
    fail_to_pass: list[dict[str, Any]] = []
    code_changed: list[dict[str, Any]] = []
    file_count_changed: list[dict[str, Any]] = []
    unchanged = 0

    for key in common:
        before = old_map[key]
        after = new_map[key]
        before_result = str(before.get("result") or "")
        after_result = str(after.get("result") or "")
        path = after.get("task_path") or before.get("task_path") or key

        if before_result in {"pass", "warn"} and after_result == "fail":
            pass_to_fail.append(
                {"task_path": path, "before": before_result, "after": after_result}
            )
        if before_result == "fail" and after_result in {"pass", "warn"}:
            fail_to_pass.append(
                {"task_path": path, "before": before_result, "after": after_result}
            )

        before_codes = set(before.get("issue_codes") or [])
        after_codes = set(after.get("issue_codes") or [])
        if before_codes != after_codes:
            code_changed.append(
                {
                    "task_path": path,
                    "added": sorted(after_codes - before_codes),
                    "removed": sorted(before_codes - after_codes),
                }
            )

        if int(before.get("file_count") or 0) != int(after.get("file_count") or 0):
            file_count_changed.append(
                {
                    "task_path": path,
                    "before": int(before.get("file_count") or 0),
                    "after": int(after.get("file_count") or 0),
                }
            )

        if (
            before_result == after_result
            and before_codes == after_codes
            and int(before.get("file_count") or 0) == int(after.get("file_count") or 0)
        ):
            unchanged += 1

    return {
        "added": [
            {
                "task_path": new_map[key].get("task_path") or key,
                "task_id": new_map[key].get("task_id") or "",
                "result": new_map[key].get("result") or "",
            }
            for key in added_keys
        ],
        "removed": [
            {
                "task_path": old_map[key].get("task_path") or key,
                "task_id": old_map[key].get("task_id") or "",
                "result": old_map[key].get("result") or "",
            }
            for key in removed_keys
        ],
        "pass_to_fail": pass_to_fail,
        "fail_to_pass": fail_to_pass,
        "code_changed": code_changed,
        "file_count_changed": file_count_changed,
        "unchanged_count": unchanged,
    }
