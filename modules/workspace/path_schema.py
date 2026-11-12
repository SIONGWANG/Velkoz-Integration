"""路径结构识别（v1.25.11，只读）。

业务目录约定（每一层都可能不存在）：

    02-待验数据\\大类\\小类\\团队提交\\日期\\三级类目\\批次\\编号
    03-申请终验\\...

识别原则：
1. 编号文件夹（6~12 位数字）= 一条数据；它的直接上层才是批次。
2. 批次以上按“团队/日期/类目名称”匹配，不按固定第几段猜。
3. 缺少的层级直接跳过，不把日期/团队误判成类目。
4. 本模块只读取目录名，不创建、不修改、不删除任何文件。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable

from modules.workspace.models import JUNK_DIR_NAMES

ID_RE = re.compile(r"(?:JD)?\d{6,12}", re.IGNORECASE)
DATE_PATTERNS = (
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{8}$"),
    re.compile(r"^\d{4}$"),
    re.compile(r"^\d{1,2}\.\d{1,2}$"),
    re.compile(r"^\d{1,2}月\d{1,2}日$"),
    re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日$"),
)
TEAM_SUFFIX_RE = re.compile(r"(?:提交|交付)(?:[（(].*[）)])?$")
RECORD_DIR_NAME = "_质检记录"
IGNORED_DATA_DIR_NAMES = {"试标数据", "试标", "测试数据"}
BATCH_LIKE_RE = re.compile(
    r"组|张|提交|交付|返修|新标|合格数据|分割|返工|^\d{2,4}[.\-]\d{1,2}"
)


def is_data_group_name(name: str) -> bool:
    return bool(ID_RE.fullmatch((name or "").strip()))


def is_date_segment(name: str) -> bool:
    text = (name or "").strip()
    return any(pattern.fullmatch(text) for pattern in DATE_PATTERNS)


def team_from_segment(name: str) -> str:
    """从“河南铸星提交”“学校提交(不变动作）”中取出团队名。"""
    text = (name or "").strip()
    match = TEAM_SUFFIX_RE.search(text)
    if not match:
        return ""
    return text[: match.start()].strip()


def _is_junk(name: str) -> bool:
    if name in IGNORED_DATA_DIR_NAMES or "试标" in name or "测试数据" in name:
        return True
    for pattern in JUNK_DIR_NAMES:
        if pattern.endswith("*"):
            if name.startswith(pattern[:-1]):
                return True
        elif name == pattern:
            return True
    return name.startswith(".") or name in {"__pycache__", "node_modules"}


def _collapse_segments(segments: list[str]) -> list[str]:
    """折叠连续同名的层级，例如 11.7批次\\11.7批次。"""
    result: list[str] = []
    for segment in segments:
        if result and result[-1] == segment:
            continue
        result.append(segment)
    return result


def _relative_segments(root: str, path: str) -> list[str]:
    rel = os.path.relpath(path, root)
    if rel in (".", ""):
        return []
    return [part for part in rel.split(os.sep) if part]


def _direct_data_groups(batch_path: str) -> list[str]:
    try:
        return sorted(
            entry.name
            for entry in os.scandir(batch_path)
            if entry.is_dir(follow_symlinks=False) and is_data_group_name(entry.name)
        )
    except OSError:
        return []


@dataclass
class RecordFolder:
    name: str
    path: str
    matched_reviewers: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


@dataclass
class BatchInfo:
    path: str
    name: str
    segments: list[str] = field(default_factory=list)
    category_l1: str = ""
    category_l2: str = ""
    category_l3: str = ""
    team: str = ""
    date: str = ""
    groups: list[str] = field(default_factory=list)
    data_groups: list[str] = field(default_factory=list)
    record_dir: str = ""
    record_folders: list[RecordFolder] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)
    conflict: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "新提交"

    @property
    def reviewer_text(self) -> str:
        if self.conflict:
            return "冲突：" + "、".join(self.reviewers)
        return "、".join(self.reviewers) if self.reviewers else "未匹配到"

    @property
    def record_text(self) -> str:
        if not self.record_dir:
            return "没有 _质检记录"
        if not self.record_folders:
            return "_质检记录 为空"
        with_files = [item for item in self.record_folders if item.files]
        return f"{len(self.record_folders)} 个验收员文件夹，{len(with_files)} 个含文件"


def parse_batch(
    root: str,
    batch_path: str,
    *,
    reviewer_names: Iterable[str] = (),
    team_names: Iterable[str] = (),
    category_names: Iterable[str] = (),
    category_levels: dict[str, int] | None = None,
) -> BatchInfo:
    """解析一个批次路径；只读目录名，不访问文件内容。"""
    reviewers = [name.strip() for name in reviewer_names if str(name).strip()]
    teams = [name.strip() for name in team_names if str(name).strip()]
    categories = {name.strip() for name in category_names if str(name).strip()}
    levels = {str(name).strip(): int(level) for name, level in (category_levels or {}).items() if str(name).strip()}
    for name in categories:
        levels.setdefault(name, 0)
    categories |= set(levels)

    segments = _collapse_segments(_relative_segments(root, batch_path))
    name = os.path.basename(os.path.normpath(batch_path))
    info = BatchInfo(path=batch_path, name=name, segments=segments)
    info.data_groups = _direct_data_groups(batch_path)

    # 编号文件夹的直接上层才是批次；批次本身不参与类目解析。
    work = list(segments[:-1])

    # 批次本身可能就是日期（例如 03-申请终验\\...\\2026-11-04\\60122354）
    if is_date_segment(name):
        info.date = name

    # 日期：取批次之前最后一个日期段
    date_index = -1
    if not info.date:
        for index in range(len(work) - 1, -1, -1):
            if is_date_segment(work[index]):
                date_index = index
                info.date = work[index]
                break

    # 团队：先识别“提交/交付”后缀；没有后缀时只接受“不是类目名”的精确团队标签。
    team_index = -1
    for index, segment in enumerate(work):
        stripped = team_from_segment(segment)
        if stripped:
            info.team = stripped
            team_index = index
            break
        if segment in categories:
            continue
        matched = next((item for item in teams if item and item == segment), "")
        if matched:
            info.team = matched
            team_index = index
            break

    if team_index < 0:
        info.warnings.append("未识别到团队层（没有“提交/交付”后缀，也没有配置团队名）")
    if not info.date:
        info.warnings.append("未识别到日期层")

    assigned: set[int] = set()
    if team_index >= 0:
        assigned.add(team_index)
    if date_index >= 0:
        assigned.add(date_index)

    # 先按设置里的类目层级精确匹配；团队/日期段已占位，不会误判。
    for index in range(len(work)):
        if index in assigned:
            continue
        segment = work[index]
        level = levels.get(segment)
        if not level:
            continue
        if level == 3 and not info.category_l3:
            info.category_l3 = segment
        elif level == 2 and not info.category_l2:
            info.category_l2 = segment
        elif level == 1 and not info.category_l1:
            info.category_l1 = segment
        else:
            continue
        assigned.add(index)

    # 三级类目：日期之后、批次之前的第一个“类目样”段。
    # “51组”“11月6日提交-…”这类批次样段不当作三级类目，记入额外层级。
    if date_index >= 0 and not info.category_l3:
        for index in range(date_index + 1, len(work)):
            if index in assigned:
                continue
            segment = work[index]
            if categories and segment not in categories:
                continue
            if not categories and BATCH_LIKE_RE.search(segment):
                continue
            info.category_l3 = segment
            assigned.add(index)
            break

    # 大类/小类：未配置的段按剩余顺序填
    remaining = [index for index in range(len(work)) if index not in assigned]
    anchored = team_index >= 0 or date_index >= 0 or bool(info.date)
    for index in remaining:
        segment = work[index]
        if not info.category_l1:
            if anchored and BATCH_LIKE_RE.search(segment):
                info.groups.append(segment)
            else:
                info.category_l1 = segment
        elif not info.category_l2:
            if anchored and BATCH_LIKE_RE.search(segment):
                info.groups.append(segment)
            else:
                info.category_l2 = segment
        elif not info.category_l3 and not BATCH_LIKE_RE.search(segment):
            info.category_l3 = segment
        else:
            info.groups.append(segment)

    # 批次之前的额外层（例如“51组”下面再套“34组”）
    for index, segment in enumerate(work):
        if index in assigned:
            continue
        if segment in {info.category_l1, info.category_l2, info.category_l3}:
            continue
        if segment not in info.groups:
            info.groups.append(segment)

    record_dir = os.path.join(batch_path, RECORD_DIR_NAME)
    if os.path.isdir(record_dir):
        info.record_dir = record_dir
        try:
            entries = sorted(
                (entry for entry in os.scandir(record_dir) if entry.is_dir(follow_symlinks=False)),
                key=lambda item: item.name,
            )
        except OSError:
            entries = []
        matched_names: list[str] = []
        for entry in entries:
            folder = RecordFolder(name=entry.name, path=entry.path)
            if reviewers:
                folder.matched_reviewers = [name for name in reviewers if name in entry.name]
                if not folder.matched_reviewers:
                    info.warnings.append(f"验收员文件夹未匹配：{entry.name}")
            try:
                folder.files = sorted(
                    item.name
                    for item in os.scandir(entry.path)
                    if item.is_file() and not item.name.lower().endswith((".jpg", ".jpeg", ".png", ".db"))
                )[:20]
            except OSError:
                folder.files = []
            info.record_folders.append(folder)
            for name in folder.matched_reviewers:
                if name not in matched_names:
                    matched_names.append(name)
        info.reviewers = matched_names
        if len(matched_names) > 1:
            info.conflict = True
            info.warnings.append("同一批次匹配到多个验收员，需要人工选择绑定")
        if not matched_names:
            info.warnings.append("有 _质检记录，但没有匹配到验收员")
    else:
        info.warnings.append("没有 _质检记录")

    return info


def _child_dirs(path: str) -> list[os.DirEntry]:
    try:
        return [
            entry
            for entry in os.scandir(path)
            if entry.is_dir(follow_symlinks=False) and not _is_junk(entry.name)
        ]
    except OSError:
        return []


def detect_batches(
    root: str,
    *,
    max_depth: int = 8,
    limit: int = 200,
    reviewer_names: Iterable[str] = (),
    team_names: Iterable[str] = (),
    category_names: Iterable[str] = (),
    category_levels: dict[str, int] | None = None,
    ignore_patterns: Iterable[str] = (),
) -> list[BatchInfo]:
    """从根目录向下找“直接包含编号文件夹”的目录，作为批次。只读。"""
    root = os.path.normpath(root)
    patterns = [str(item).strip().casefold() for item in ignore_patterns if str(item).strip()]

    def ignored(value: str) -> bool:
        text = str(value or "").casefold()
        return any(pattern in text for pattern in patterns)

    batches: list[BatchInfo] = []
    seen: set[str] = set()
    stack: list[tuple[str, int]] = [(root, 0)]
    while stack and len(batches) < limit:
        current, depth = stack.pop()
        if depth > max_depth:
            continue
        if ignored(os.path.relpath(current, root)):
            continue
        children = _child_dirs(current)
        has_ids = any(is_data_group_name(entry.name) for entry in children)
        if has_ids and os.path.normcase(current) != os.path.normcase(root):
            key = os.path.normcase(current)
            if key not in seen:
                seen.add(key)
                batches.append(
                    parse_batch(
                        root,
                        current,
                        reviewer_names=reviewer_names,
                        team_names=team_names,
                        category_names=category_names,
                        category_levels=category_levels,
                    )
                )
        for entry in sorted(children, key=lambda item: item.name, reverse=True):
            if is_data_group_name(entry.name) or ignored(entry.name):
                continue
            stack.append((entry.path, depth + 1))
    batches.sort(key=lambda item: item.path)
    return batches


def summarize(batches: Iterable[BatchInfo]) -> dict[str, object]:
    items = list(batches)
    return {
        "batches": len(items),
        "data_groups": sum(len(item.data_groups) for item in items),
        "with_records": sum(1 for item in items if item.record_folders),
        "conflicts": sum(1 for item in items if item.conflict),
        "unmatched": sum(1 for item in items if item.record_folders and not item.reviewers),
        "teams": sorted({item.team for item in items if item.team}),
        "categories": sorted(
            {
                " / ".join(part for part in (item.category_l1, item.category_l2, item.category_l3) if part)
                for item in items
                if item.category_l1
            }
        ),
    }
