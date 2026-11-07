"""只读目录扫描器。

约束：
- 不写源目录；
- 不跟随符号链接/junction；
- 不读取图片内容，只读取文件名/大小/mtime；
- 默认有目录数与深度上限，异常时返回部分结果和原因。
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from modules.common.fingerprint import directory_fingerprint_from_path
from modules.common.path_guard import is_ignored_name, is_reparse_point, normalize_path
from modules.common.timeutil import duration_ms, now_iso
from .matcher import MatchResult, match_layer, match_path
from .rules import RuleSet

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


@dataclass
class ScanOptions:
    max_dirs: int = 20000
    max_groups: int = 10000
    max_depth: int = 12
    count_fingerprint: bool = True
    include_hidden: bool = False
    follow_symlinks: bool = False


@dataclass
class DataGroup:
    path: str
    task_id: str
    images: list[str] = field(default_factory=list)
    txt_zh: str | None = None
    txt_en: str | None = None
    file_count: int = 0
    image_count: int = 0
    txt_count: int = 0
    user_name: str = "未知"
    fingerprint: str = ""
    match: MatchResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "task_id": self.task_id,
            "images": list(self.images),
            "txt_zh": self.txt_zh,
            "txt_en": self.txt_en,
            "file_count": self.file_count,
            "image_count": self.image_count,
            "txt_count": self.txt_count,
            "user_name": self.user_name,
            "fingerprint": self.fingerprint,
            "confidence": self.match.confidence if self.match else 0.0,
            "identity": dict(self.match.assignment) if self.match else {},
        }


@dataclass
class ScanIssue:
    code: str
    path: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "path": self.path, "message": self.message, "detail": dict(self.detail)}


@dataclass
class Candidate:
    batch_path: str
    batch_name: str
    identity: dict[str, str] = field(default_factory=dict)
    status: str = "pending"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    explain: str = ""
    group_count: int = 0
    file_count: int = 0
    groups: list[DataGroup] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_path": self.batch_path,
            "batch_name": self.batch_name,
            "identity": dict(self.identity),
            "status": self.status,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "explain": self.explain,
            "group_count": self.group_count,
            "file_count": self.file_count,
            "groups": [group.to_dict() for group in self.groups],
        }


@dataclass
class ScanResult:
    root: str
    ruleset_id: str
    ruleset_name: str
    started_at: str
    finished_at: str = ""
    status: str = "completed"
    total_dirs: int = 0
    files_seen: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    groups: list[DataGroup] = field(default_factory=list)
    issues: list[ScanIssue] = field(default_factory=list)
    duration_ms: int = 0
    truncated: bool = False
    options: ScanOptions = field(default_factory=ScanOptions)

    @property
    def stats(self) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        reason_counts: dict[str, int] = {}
        for candidate in self.candidates:
            status_counts[candidate.status] = status_counts.get(candidate.status, 0) + 1
        for issue in self.issues:
            reason_counts[issue.code] = reason_counts.get(issue.code, 0) + 1
        return {
            "root": self.root,
            "status": self.status,
            "total_dirs": self.total_dirs,
            "files_seen": self.files_seen,
            "groups": len(self.groups),
            "candidates": len(self.candidates),
            "issues": len(self.issues),
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
            "candidate_status": status_counts,
            "issue_reasons": reason_counts,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "ruleset_id": self.ruleset_id,
            "ruleset_name": self.ruleset_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "stats": self.stats,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "groups": [group.to_dict() for group in self.groups],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _find_image(files: list[str], stem: str, extensions: list[str]) -> str | None:
    valid = {ext.lower().lstrip(".") for ext in extensions}
    stem_cf = stem.casefold()
    for name in sorted(files):
        base, ext = os.path.splitext(name)
        if base.casefold() == stem_cf and ext.lower().lstrip(".") in valid:
            return name
    return None


def _find_txt(files: list[str], suffixes: list[str]) -> str | None:
    lower_suffixes = [suffix.casefold() for suffix in suffixes]
    for suffix in lower_suffixes:
        for name in files:
            if not name.casefold().endswith(".txt"):
                continue
            if name[:-4].casefold().endswith(suffix):
                return name
    return None


def _is_hidden(name: str) -> bool:
    return name.startswith(".") or name.startswith("~$")


def _looks_like_id(name: str) -> bool:
    return bool(re.fullmatch(r"(?:JD)?\d{6,12}", name, re.IGNORECASE))


def _derive_batch(result: MatchResult, segments: list[str], root: str, task_path: str) -> tuple[str, str]:
    if "batch" in result.indices:
        idx = result.indices["batch"]
        rel = segments[: idx + 1]
        batch_path = os.path.join(root, *rel) if rel else root
        return batch_path, result.assignment.get("batch") or os.path.basename(batch_path)
    parent = os.path.dirname(task_path)
    return parent or root, os.path.basename(parent) or os.path.basename(root)


def _build_group(
    path: str,
    task_id: str,
    match: MatchResult,
    ruleset: RuleSet,
    options: ScanOptions,
) -> tuple[DataGroup | None, ScanIssue | None]:
    try:
        with os.scandir(path) as it:
            entries = [entry for entry in it]
    except OSError as exc:
        return None, ScanIssue("DR-014", path, f"目录不可读: {exc}")

    files: list[str] = []
    for entry in entries:
        try:
            if entry.is_file(follow_symlinks=False):
                files.append(entry.name)
        except OSError:
            continue

    images: list[str] = []
    for slot in ruleset.image_slots:
        extensions = slot.get("extensions") or ruleset.image_extensions
        found = None
        for suffix in slot.get("stem_suffixes", [""]):
            found = _find_image(files, f"{task_id}{suffix}", extensions)
            if found:
                break
        if found:
            images.append(os.path.join(path, found))

    txts: list[str | None] = []
    for slot in ruleset.text_slots:
        found = _find_txt(files, slot.get("suffixes", []))
        txts.append(os.path.join(path, found) if found else None)

    if not images:
        return None, ScanIssue("DR-009", path, "任务目录下没有匹配到图片", {"file_count": len(files)})

    fingerprint = ""
    if options.count_fingerprint:
        try:
            fingerprint = directory_fingerprint_from_path(path)
        except OSError:
            fingerprint = ""

    team = match.assignment.get("team") or "未知"
    group = DataGroup(
        path=path,
        task_id=task_id,
        images=images,
        txt_zh=txts[0] if len(txts) > 0 else None,
        txt_en=txts[1] if len(txts) > 1 else None,
        file_count=len(files),
        image_count=len(images),
        txt_count=sum(1 for item in txts if item),
        user_name=team,
        fingerprint=fingerprint,
        match=match,
    )
    return group, None


def scan_root(
    root: str,
    ruleset: RuleSet,
    options: ScanOptions | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> ScanResult:
    opts = options or ScanOptions()
    root_path = normalize_path(root)
    started_dt = _dt.datetime.now()
    result = ScanResult(
        root=root_path,
        ruleset_id=ruleset.id,
        ruleset_name=ruleset.name,
        started_at=now_iso(),
        options=opts,
    )

    if not os.path.isdir(root_path):
        result.status = "failed"
        result.issues.append(ScanIssue("DR-001", root_path, "根目录不存在或不是目录"))
        result.finished_at = now_iso()
        result.duration_ms = duration_ms(started_dt)
        return result

    layers = ruleset.enabled_layers()
    task_layer = ruleset.task_layer()
    if task_layer is None:
        result.status = "failed"
        result.issues.append(ScanIssue("DR-018", root_path, "规则方案没有可用的任务层"))
        result.finished_at = now_iso()
        result.duration_ms = duration_ms(started_dt)
        return result

    candidates: dict[str, Candidate] = {}
    issues: list[ScanIssue] = []
    groups: list[DataGroup] = []
    state = {"dirs": 0, "files": 0, "cancelled": False}

    def add_issue(code: str, path: str, message: str, detail: dict | None = None) -> None:
        if len(issues) < 5000:
            issues.append(ScanIssue(code, path, message, detail or {}))

    def process_dir(path: str, depth: int, segments: list[str]) -> bool:
        """返回 True 表示该目录是任务层，不再向下递归。"""
        name = os.path.basename(path)
        if is_ignored_name(name, ruleset.ignore_names):
            add_issue("DR-012", path, "目录被忽略规则跳过", {"name": name})
            return True
        if is_reparse_point(path):
            add_issue("DR-013", path, "符号链接/junction 已跳过")
            return True

        state["dirs"] += 1
        if state["dirs"] > opts.max_dirs:
            result.truncated = True
            add_issue("DR-015", path, "超过扫描目录上限，已停止", {"max_dirs": opts.max_dirs})
            return True
        if progress and state["dirs"] % 50 == 0:
            progress(state["dirs"], opts.max_dirs, f"正在扫描：{path}")

        if len(groups) >= opts.max_groups:
            result.truncated = True
            add_issue("DR-015", path, "超过数据组上限，已停止", {"max_groups": opts.max_groups})
            return True

        if not segments:
            return False

        if match_layer(task_layer, name):
            match = match_path(segments, ruleset)
            if not match.matched or "task" not in match.assignment:
                add_issue("DR-005", path, "任务目录结构不满足规则", {"explain": match.explain})
                return False
            group, issue = _build_group(path, name, match, ruleset, opts)
            if issue:
                add_issue(issue.code, issue.path, issue.message, issue.detail)
                # 没有图片的目录可能是日期/批次等上层目录，继续向下扫描
                return False
            if group is None:
                return False
            groups.append(group)
            batch_path, batch_name = _derive_batch(match, segments, root_path, path)
            candidate = candidates.get(batch_path)
            if candidate is None:
                candidate = Candidate(
                    batch_path=batch_path,
                    batch_name=batch_name,
                    identity={
                        layer.key: match.assignment[layer.key]
                        for layer in layers
                        if layer.identity and layer.key in match.assignment
                    },
                    status="pending" if match.confidence >= 0.6 else "partial",
                    confidence=match.confidence,
                    reasons=list(match.reasons),
                    explain=match.explain,
                )
                candidates[batch_path] = candidate
            candidate.groups.append(group)
            candidate.group_count += 1
            candidate.file_count += group.file_count
            candidate.confidence = max(candidate.confidence, match.confidence)
            if match.required_missing:
                candidate.status = "partial"
            if match.reasons:
                for reason in match.reasons:
                    if reason not in candidate.reasons:
                        candidate.reasons.append(reason)
            return True

        # 任务层模式相近但不匹配，记录原因样本
        if _looks_like_id(name) and len(issues) < 200:
            add_issue("DR-002", path, "目录名不符合任务 ID 规则", {"name": name})
        return False

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
            with os.scandir(path) as it:
                entries = list(it)
        except OSError as exc:
            add_issue("DR-014", path, f"目录不可读: {exc}")
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
            if not opts.include_hidden and _is_hidden(entry.name):
                continue
            dirs.append(entry)

        for entry in sorted(dirs, key=lambda item: item.name.casefold()):
            if result.truncated or state["cancelled"]:
                return
            if should_cancel and should_cancel():
                state["cancelled"] = True
                return
            child_segments = segments + [entry.name]
            consumed = process_dir(entry.path, depth + 1, child_segments)
            if not consumed:
                walk(entry.path, depth + 1, child_segments)

    try:
        walk(root_path, 0, [])
    except Exception as exc:  # 保证部分结果可返回
        result.status = "failed"
        add_issue("DR-014", root_path, f"扫描异常: {exc}")
    if state["cancelled"]:
        result.status = "cancelled"

    result.groups = groups
    result.issues = issues
    result.total_dirs = state["dirs"]
    result.files_seen = state["files"]

    # 同名批次跨路径标记重复
    name_map: dict[str, list[Candidate]] = {}
    for candidate in candidates.values():
        name_map.setdefault(candidate.batch_name.casefold(), []).append(candidate)
    for same_name in name_map.values():
        if len(same_name) > 1:
            paths = {item.batch_path for item in same_name}
            if len(paths) > 1:
                for item in same_name:
                    item.status = "duplicate"
                    if "DR-016" not in item.reasons:
                        item.reasons.append("DR-016")

    result.candidates = sorted(candidates.values(), key=lambda item: item.batch_path.casefold())
    result.finished_at = now_iso()
    result.duration_ms = duration_ms(started_dt)
    if progress:
        progress(result.total_dirs, max(opts.max_dirs, result.total_dirs, 1), "扫描完成")
    return result


def new_run_id() -> str:
    return str(uuid.uuid4())
