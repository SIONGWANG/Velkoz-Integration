"""业务目录增量发现（v1.25.8）。

只扫描配置的待验/申请终验目录；复用扫描规则的任务层与文件槽位。
不读取图片内容，不写源目录，不因网络错误删除历史批次。
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from typing import Callable

from modules.common.path_guard import is_ignored_name, is_reparse_point, normalize_path
from modules.common.timeutil import duration_ms, now_iso
from modules.scanning.matcher import MatchResult, match_layer, match_path
from modules.scanning.rules import DATE_REGEX, RuleSet
from modules.scanning.scanner import (
    Candidate,
    DataGroup,
    ScanIssue,
    ScanOptions,
    ScanResult,
    _build_group,
)

from .models import (
    DEFAULT_FINAL_DIR,
    DEFAULT_PENDING_DIR,
    FORBIDDEN_SCAN_DIR_NAMES,
    JUNK_DIR_NAMES,
    SOURCE_FINAL,
    SOURCE_PENDING,
)

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]

_DATE_RE = re.compile(DATE_REGEX)
_MMDD_RE = re.compile(r"^(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$")
_YMD_RE = re.compile(r"^20\d{6}$")
_TEAM_SUFFIX = "提交"
_FINAL_TASK_CONTAINERS = ("验收员", "验收结果", "验收数据")
_ID_RE = re.compile(r"(?:JD)?\d{6,12}", re.IGNORECASE)


def _dateish(name: str) -> bool:
    text = (name or "").strip()
    return bool(_DATE_RE.fullmatch(text) or _MMDD_RE.fullmatch(text) or _YMD_RE.fullmatch(text))


def _teamish(name: str) -> bool:
    return (name or "").strip().endswith(_TEAM_SUFFIX)


def _is_task_container(name: str) -> bool:
    return any(token in (name or "") for token in _FINAL_TASK_CONTAINERS)


def _is_forbidden_root(root: str) -> str:
    base = os.path.basename(os.path.normpath(root))
    if base in FORBIDDEN_SCAN_DIR_NAMES:
        return f"不允许扫描“{base}”：本版本只接入 {DEFAULT_PENDING_DIR} 与 {DEFAULT_FINAL_DIR}。"
    try:
        children = {
            entry.name for entry in os.scandir(root) if entry.is_dir(follow_symlinks=False)
        }
    except OSError:
        return ""
    if DEFAULT_PENDING_DIR in children or DEFAULT_FINAL_DIR in children:
        return (
            "不允许扫描项目父目录。请选择其中的 "
            f"{DEFAULT_PENDING_DIR} 或 {DEFAULT_FINAL_DIR}，避免扫描素材仓库/试做目录。"
        )
    return ""


def derive_identity(
    *,
    root_path: str,
    batch_path: str,
    batch_name: str,
    source_type: str,
    project_name: str,
    match: MatchResult | None = None,
) -> dict[str, str]:
    """从相对路径推导业务身份；不按批次名称做模糊匹配。"""
    rel = os.path.relpath(batch_path, root_path)
    segments = [] if rel in (".", "") else [part for part in rel.split(os.sep) if part]
    identity: dict[str, str] = {}
    if match and match.assignment:
        identity.update({key: value for key, value in match.assignment.items() if value})

    team = ""
    for segment in segments:
        if _teamish(segment):
            team = segment[: -len(_TEAM_SUFFIX)].strip() or segment
    if team:
        identity["team"] = team

    date = ""
    for segment in reversed(segments):
        if _dateish(segment):
            date = segment
            break
    if date:
        identity["date"] = date

    category_l2 = ""
    for segment in segments:
        if "类目" in segment:
            category_l2 = segment
            break
    if not category_l2 and len(segments) >= 2:
        candidate_l2 = segments[1]
        if (
            candidate_l2 not in {team, date, batch_name}
            and not _teamish(candidate_l2)
            and not _dateish(candidate_l2)
        ):
            category_l2 = candidate_l2
    if category_l2:
        identity["category_l2"] = category_l2

    category_l1 = ""
    for segment in segments:
        if segment in {team, date, category_l2, batch_name}:
            continue
        if _teamish(segment) or _dateish(segment):
            continue
        category_l1 = segment
        break
    if category_l1:
        identity["category_l1"] = category_l1

    identity["batch"] = batch_name
    identity["source_type"] = source_type
    if project_name:
        identity["project"] = project_name
    identity["relative_path"] = rel if rel != "." else ""
    identity["path_segments"] = " / ".join(segments)
    return identity


def _derive_batch_path(
    *,
    root_path: str,
    task_path: str,
    segments: list[str],
    ruleset: RuleSet,
    source_type: str,
) -> tuple[str, MatchResult]:
    match = match_path(segments, ruleset)
    candidate = ""
    if match.matched and "batch" in match.indices:
        index = match.indices["batch"]
        if 0 <= index < len(segments):
            candidate = os.path.join(root_path, *segments[: index + 1])
    if not candidate:
        candidate = os.path.dirname(task_path) or root_path
    name = os.path.basename(os.path.normpath(candidate))
    if (
        os.path.normcase(normalize_path(candidate)) == os.path.normcase(normalize_path(root_path))
        or _dateish(name)
        or _teamish(name)
        or name == os.path.basename(os.path.normpath(task_path))
    ):
        candidate = os.path.dirname(task_path) or root_path
    if source_type == SOURCE_FINAL:
        while (
            os.path.normcase(normalize_path(candidate)) != os.path.normcase(normalize_path(root_path))
            and _is_task_container(os.path.basename(os.path.normpath(candidate)))
        ):
            parent = os.path.dirname(candidate)
            if not parent or os.path.normcase(normalize_path(parent)) == os.path.normcase(normalize_path(candidate)):
                break
            candidate = parent
    return candidate, match


def discover_source(
    root_path: str,
    source_type: str,
    ruleset: RuleSet,
    *,
    project_name: str = "",
    project_root_id: str = "",
    options: ScanOptions | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> ScanResult:
    opts = options or ScanOptions(max_dirs=100000, max_groups=50000, max_depth=20)
    root = normalize_path(root_path)
    started_dt = _dt.datetime.now()
    result = ScanResult(
        root=root,
        ruleset_id=ruleset.id,
        ruleset_name=ruleset.name,
        started_at=now_iso(),
        options=opts,
        ruleset_json=ruleset.to_json(),
        source_type=source_type,
        project_root_id=project_root_id,
        project_name=project_name,
    )

    if not os.path.isdir(root):
        result.status = "failed"
        result.issues.append(
            ScanIssue(
                "DR-001",
                root,
                "目录不存在或无法访问；请检查网络共享、路径和权限。历史批次不会被删除。",
            )
        )
        result.access_failures += 1
        result.finished_at = now_iso()
        result.duration_ms = duration_ms(started_dt)
        return result

    forbidden = _is_forbidden_root(root)
    if forbidden:
        result.status = "failed"
        result.issues.append(ScanIssue("DR-020", root, forbidden))
        result.finished_at = now_iso()
        result.duration_ms = duration_ms(started_dt)
        return result

    task_layer = ruleset.task_layer()
    if task_layer is None:
        result.status = "failed"
        result.issues.append(ScanIssue("DR-018", root, "规则方案没有可用的任务层"))
        result.finished_at = now_iso()
        result.duration_ms = duration_ms(started_dt)
        return result

    ignore_names = list(dict.fromkeys([*JUNK_DIR_NAMES, *(ruleset.ignore_names or [])]))
    candidates: dict[str, Candidate] = {}
    groups: list[DataGroup] = []
    issues: list[ScanIssue] = []
    state = {"dirs": 0, "files": 0, "cancelled": False, "unrecognized": 0}

    def add_issue(code: str, path: str, message: str, detail: dict | None = None) -> None:
        if len(issues) < 5000:
            issues.append(ScanIssue(code, path, message, detail or {}))

    def walk(path: str, depth: int, segments: list[str]) -> None:
        if result.truncated or state["cancelled"]:
            return
        if should_cancel and should_cancel():
            state["cancelled"] = True
            return
        if depth > opts.max_depth:
            add_issue("DR-004", path, "超过最大扫描深度", {"max_depth": opts.max_depth})
            return
        try:
            with os.scandir(path) as iterator:
                entries = list(iterator)
        except OSError as exc:
            result.access_failures += 1
            add_issue("DR-014", path, f"目录无法读取：{exc}")
            return
        dirs = []
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False):
                    state["files"] += 1
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if entry.name.startswith(".") or is_ignored_name(entry.name, ignore_names):
                continue
            dirs.append(entry)
        for entry in sorted(dirs, key=lambda item: item.name.casefold()):
            if result.truncated or state["cancelled"]:
                return
            if should_cancel and should_cancel():
                state["cancelled"] = True
                return
            state["dirs"] += 1
            if state["dirs"] > opts.max_dirs:
                result.truncated = True
                add_issue("DR-015", entry.path, "超过扫描目录上限，已停止", {"max_dirs": opts.max_dirs})
                return
            if progress and state["dirs"] % 25 == 0:
                progress(state["dirs"], opts.max_dirs, f"正在发现：{entry.path}")
            if is_reparse_point(entry.path):
                add_issue("DR-013", entry.path, "符号链接/junction 已跳过")
                continue
            child_segments = segments + [entry.name]
            if match_layer(task_layer, entry.name):
                group, issue = _build_group(
                    entry.path,
                    entry.name,
                    match_path(child_segments, ruleset),
                    ruleset,
                    opts,
                )
                if issue or group is None:
                    state["unrecognized"] += 1
                    if issue:
                        add_issue(issue.code, issue.path, issue.message, issue.detail)
                    continue
                batch_path, match = _derive_batch_path(
                    root_path=root,
                    task_path=entry.path,
                    segments=child_segments,
                    ruleset=ruleset,
                    source_type=source_type,
                )
                batch_name = os.path.basename(os.path.normpath(batch_path)) or os.path.basename(root)
                candidate = candidates.get(batch_path)
                if candidate is None:
                    identity = derive_identity(
                        root_path=root,
                        batch_path=batch_path,
                        batch_name=batch_name,
                        source_type=source_type,
                        project_name=project_name,
                        match=match,
                    )
                    candidate = Candidate(
                        batch_path=batch_path,
                        batch_name=batch_name,
                        identity=identity,
                        status="pending",
                        confidence=1.0 if identity.get("team") and identity.get("date") else 0.85,
                        reasons=[],
                        explain="业务目录发现",
                    )
                    candidates[batch_path] = candidate
                candidate.groups.append(group)
                candidate.group_count += 1
                candidate.file_count += group.file_count
                groups.append(group)
                continue
            # 任务层模式相近但不匹配，记录原因
            if _ID_RE.fullmatch(entry.name) and len(issues) < 200:
                state["unrecognized"] += 1
                add_issue("DR-002", entry.path, "目录名不符合任务 ID 规则", {"name": entry.name})
                continue
            walk(entry.path, depth + 1, child_segments)

    try:
        walk(root, 0, [])
    except Exception as exc:
        result.status = "failed"
        add_issue("DR-014", root, f"扫描异常：{exc}")
    if state["cancelled"]:
        result.status = "cancelled"
    if result.truncated and result.status == "completed":
        result.status = "partial"

    result.groups = groups
    result.issues = issues
    result.total_dirs = state["dirs"]
    result.files_seen = state["files"]
    result.unrecognized_dirs = state["unrecognized"]
    result.candidates = sorted(candidates.values(), key=lambda item: item.batch_path.casefold())
    result.finished_at = now_iso()
    result.duration_ms = duration_ms(started_dt)
    return result
