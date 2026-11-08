"""单图/多图/双模式匹配与问题判定。"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

from modules.common.fingerprint import directory_fingerprint_from_path

from .errors import make_issue
from .id_rules import IMAGE_EXTENSIONS, IdMatcher, has_width_issue
from .models import FileRule, Issue, Template, TaskResult

MISSING_CODE_BY_ROLE = {
    "primary": "FC-101",
    "result": "FC-102",
    "psd": "FC-103",
    "zh": "FC-104",
    "en": "FC-105",
}

WRONG_EXT_CODE_BY_ROLE = {
    "primary": "FC-106",
    "result": "FC-107",
}

# 这些扩展名属于“图片”，用于判断主图/结果图扩展名不符；
# PSD 有自己的规则，TXT 也由文本规则处理，不参与图片扩展名误判。
RASTER_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "gif", "avif"}


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str
    stem: str
    ext: str
    size: int
    mtime_ns: int
    is_hidden: bool


@dataclass
class ModeCheck:
    mode: str
    issues: list[Issue] = field(default_factory=list)
    consumed: set[str] = field(default_factory=set)
    image_count: int = 0
    txt_count: int = 0

    @property
    def error_count(self) -> int:
        return sum(1 for item in self.issues if item.severity == "error")

    @property
    def warn_count(self) -> int:
        return sum(1 for item in self.issues if item.severity == "warn")

    @property
    def passes(self) -> bool:
        return self.error_count == 0


def list_task_files(task_path: str) -> tuple[list[FileEntry], list[str], str]:
    """只读列出任务目录当前层文件；返回 (文件, 子目录名, 错误说明)。"""
    files: list[FileEntry] = []
    subdirs: list[str] = []
    try:
        with os.scandir(task_path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        subdirs.append(entry.name)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                stem, ext = os.path.splitext(entry.name)
                attrs = getattr(st, "st_file_attributes", 0)
                hidden = entry.name.startswith(".") or bool(
                    attrs & getattr(os.stat_result, "FILE_ATTRIBUTE_HIDDEN", 0x2)
                )
                files.append(
                    FileEntry(
                        name=entry.name,
                        path=entry.path,
                        stem=stem,
                        ext=ext.lower().lstrip("."),
                        size=st.st_size,
                        mtime_ns=st.st_mtime_ns,
                        is_hidden=hidden,
                    )
                )
    except OSError as exc:
        return [], [], str(exc)
    files.sort(key=lambda item: item.name.casefold())
    return files, sorted(subdirs, key=str.casefold), ""


def _file_issue(
    code: str,
    task_id: str,
    task_path: str,
    entry: FileEntry | None,
    **kwargs,
) -> Issue:
    actual = kwargs.pop("actual", None)
    if actual is None and entry is not None:
        actual = entry.name
    return make_issue(
        code,
        task_id=task_id,
        task_path=task_path,
        file_path=entry.path if entry else kwargs.pop("file_path", None),
        actual=actual,
        **kwargs,
    )


def _consume_common_issues(
    entries: list[FileEntry],
    task_id: str,
    task_path: str,
    issues: list[Issue],
) -> None:
    now_ns = int(time.time() * 1_000_000_000)
    case_seen: dict[str, str] = {}
    for entry in entries:
        if entry.size == 0:
            issues.append(
                _file_issue(
                    "FC-302",
                    task_id,
                    task_path,
                    entry,
                    expected="文件大小 > 0",
                    actual=f"{entry.name}（0 字节）",
                )
            )
        if entry.mtime_ns > now_ns + 3600 * 1_000_000_000:
            issues.append(_file_issue("FC-308", task_id, task_path, entry))
        if entry.is_hidden:
            issues.append(_file_issue("FC-309", task_id, task_path, entry))
        if has_width_issue(entry.name):
            issues.append(_file_issue("FC-303", task_id, task_path, entry, actual=entry.name))
        key = entry.name.casefold()
        if key in case_seen and case_seen[key] != entry.name:
            issues.append(
                _file_issue(
                    "FC-307",
                    task_id,
                    task_path,
                    entry,
                    expected=case_seen[key],
                    actual=entry.name,
                )
            )
        else:
            case_seen[key] = entry.name


def _is_ignored_extra(name: str, ignore_names: tuple[str, ...]) -> bool:
    import fnmatch

    lower = name.casefold()
    for pattern in ignore_names:
        if fnmatch.fnmatch(lower, pattern.casefold()):
            return True
    return False


def _check_single(
    files: list[FileEntry],
    task_id: str,
    task_path: str,
    template: Template,
    matcher: IdMatcher,
) -> ModeCheck:
    check = ModeCheck(mode="single")
    consumed: set[str] = set()

    for rule in template.single:
        expected_stem = f"{task_id}{rule.suffix}"
        same_stem = [
            entry for entry in files if entry.stem.casefold() == expected_stem.casefold()
        ]
        matched = [entry for entry in same_stem if entry.ext in rule.extensions]
        wrong_ext = [
            entry
            for entry in same_stem
            if entry.ext in RASTER_EXTENSIONS and entry.ext not in rule.extensions
        ]

        if len({entry.ext for entry in matched}) > 1:
            check.issues.append(
                _file_issue(
                    "FC-108",
                    task_id,
                    task_path,
                    None,
                    expected="；".join(rule.extensions),
                    actual="、".join(sorted({entry.name for entry in matched})),
                )
            )
        if matched:
            priority = {ext: index for index, ext in enumerate(rule.extensions)}
            matched.sort(key=lambda entry: (priority.get(entry.ext, 99), entry.name.casefold()))
            chosen = matched[0]
            for entry in matched:
                consumed.add(entry.name)
                if entry.stem != expected_stem:
                    check.issues.append(
                        _file_issue(
                            "FC-303",
                            task_id,
                            task_path,
                            entry,
                            expected=expected_stem,
                            actual=entry.stem,
                        )
                    )
                if rule.role in {"primary", "result"}:
                    check.image_count += 1
                elif rule.role in {"zh", "en"}:
                    check.txt_count += 1
            # 大小写冲突：同名文件只允许一个
            if len(matched) > 1 and len({entry.name.casefold() for entry in matched}) == 1:
                check.issues.append(
                    _file_issue(
                        "FC-307",
                        task_id,
                        task_path,
                        chosen,
                        expected=matched[0].name,
                        actual="、".join(entry.name for entry in matched),
                    )
                )
            continue

        if wrong_ext:
            code = WRONG_EXT_CODE_BY_ROLE.get(rule.role)
            if code:
                check.issues.append(
                    _file_issue(
                        code,
                        task_id,
                        task_path,
                        wrong_ext[0],
                        expected="、".join(rule.extensions),
                        actual="、".join(sorted({entry.ext for entry in wrong_ext})),
                    )
                )
            continue

        if rule.required:
            code = MISSING_CODE_BY_ROLE.get(rule.role, "FC-101")
            check.issues.append(
                make_issue(
                    code,
                    task_id=task_id,
                    task_path=task_path,
                    expected=f"{expected_stem}.{rule.extensions[0]}"
                    if rule.extensions
                    else expected_stem,
                    actual="缺失",
                )
            )

    check.consumed = consumed
    return check


def _check_multi(
    files: list[FileEntry],
    task_id: str,
    task_path: str,
    template: Template,
    matcher: IdMatcher,
) -> ModeCheck:
    check = ModeCheck(mode="multi")
    multi = template.multi
    if multi is None:
        return check

    prefix = re.escape(task_id) + re.escape(multi.suffix_prefix)
    numbered = re.compile(rf"^{prefix}(?P<index>\d+)$", re.IGNORECASE)
    malformed = re.compile(rf"^{prefix}", re.IGNORECASE)
    by_index: dict[int, list[FileEntry]] = {}
    wrong_ext: list[FileEntry] = []
    malformed_files: list[FileEntry] = []
    consumed: set[str] = set()

    for entry in files:
        match = numbered.match(entry.stem)
        if match:
            if entry.ext in multi.extensions:
                by_index.setdefault(int(match.group("index")), []).append(entry)
            elif entry.ext in IMAGE_EXTENSIONS:
                wrong_ext.append(entry)
            continue
        if malformed.match(entry.stem) and entry.ext in IMAGE_EXTENSIONS:
            # _result 在不允许时走额外文件策略，不算格式错误
            if entry.stem.casefold() == f"{task_id}_result".casefold():
                if multi.allow_result:
                    consumed.add(entry.name)
                    check.image_count += 1
                continue
            malformed_files.append(entry)

    for index, entries in sorted(by_index.items()):
        if len(entries) > 1:
            check.issues.append(
                _file_issue(
                    "FC-204",
                    task_id,
                    task_path,
                    entries[0],
                    expected=f"{task_id}{multi.suffix_prefix}{index} 只出现一次",
                    actual="、".join(
                        f"{entry.name}({entry.ext})" for entry in entries
                    ),
                )
            )
        priority = {ext: position for position, ext in enumerate(multi.extensions)}
        entries.sort(key=lambda entry: (priority.get(entry.ext, 99), entry.name.casefold()))
        chosen = entries[0]
        for entry in entries:
            consumed.add(entry.name)
            if entry.stem != f"{task_id}{multi.suffix_prefix}{index}":
                check.issues.append(
                    _file_issue(
                        "FC-303",
                        task_id,
                        task_path,
                        entry,
                        expected=f"{task_id}{multi.suffix_prefix}{index}",
                        actual=entry.stem,
                    )
                )
        check.image_count += 1

    indices = set(by_index)
    count = len(indices)
    if count < multi.min_count:
        check.issues.append(
            make_issue(
                "FC-201",
                task_id=task_id,
                task_path=task_path,
                expected=f"至少 {multi.min_count} 张（{task_id}{multi.suffix_prefix}n）",
                actual=f"{count} 张",
            )
        )
    if multi.max_count is not None and count > multi.max_count:
        check.issues.append(
            make_issue(
                "FC-202",
                task_id=task_id,
                task_path=task_path,
                expected=f"最多 {multi.max_count} 张",
                actual=f"{count} 张：{sorted(indices)}",
            )
        )
    if indices and multi.contiguous:
        top = max(indices)
        expected_range = set(range(multi.start, top + 1)) if top >= multi.start else set()
        missing = sorted(expected_range - indices)
        if missing:
            check.issues.append(
                make_issue(
                    "FC-203",
                    task_id=task_id,
                    task_path=task_path,
                    expected=f"{multi.start}..{top} 连续",
                    actual="缺少 " + "、".join(str(item) for item in missing),
                )
            )
    if indices:
        too_small = sorted(index for index in indices if index < multi.start)
        if too_small:
            check.issues.append(
                make_issue(
                    "FC-205",
                    task_id=task_id,
                    task_path=task_path,
                    expected=f"从 {multi.start} 开始",
                    actual="、".join(str(item) for item in too_small),
                )
            )
    for entry in wrong_ext:
        check.issues.append(
            _file_issue(
                "FC-206",
                task_id,
                task_path,
                entry,
                expected="、".join(multi.extensions),
                actual=entry.ext,
            )
        )
    for entry in malformed_files:
        check.issues.append(_file_issue("FC-207", task_id, task_path, entry))

    check.consumed = consumed
    return check


def _extra_issues(
    files: list[FileEntry],
    subdirs: list[str],
    consumed: set[str],
    task_id: str,
    task_path: str,
    template: Template,
) -> list[Issue]:
    issues: list[Issue] = []
    severity = {"ignore": "info", "warn": "warn", "error": "error"}.get(
        template.extra_files, "warn"
    )
    for entry in files:
        if entry.name in consumed:
            continue
        if _is_ignored_extra(entry.name, template.scan.ignore_names):
            issues.append(_file_issue("FC-306", task_id, task_path, entry))
            continue
        issues.append(
            _file_issue(
                "FC-301",
                task_id,
                task_path,
                entry,
                severity=severity,
                message=f"额外文件：{entry.name}",
            )
        )
    for name in subdirs:
        issues.append(
            make_issue(
                "FC-301",
                task_id=task_id,
                task_path=task_path,
                file_path=os.path.join(task_path, name),
                expected="任务目录下只有业务文件",
                actual=f"子目录：{name}",
                severity="info" if template.extra_files == "ignore" else "warn",
                message=f"任务目录内存在子目录：{name}",
            )
        )
    return issues


def _determine_result(issues: list[Issue], warn_as_fail: bool) -> str:
    if any(item.severity == "error" for item in issues):
        return "fail"
    if any(item.severity == "warn" for item in issues):
        return "fail" if warn_as_fail else "warn"
    return "pass"


def check_task(
    task_path: str,
    task_id: str,
    template: Template,
    matcher: IdMatcher,
    *,
    duplicate_of: list[str] | None = None,
) -> TaskResult:
    files, subdirs, read_error = list_task_files(task_path)
    read_issues: list[Issue] = []
    if read_error:
        read_issues.append(
            make_issue(
                "FC-304",
                task_id=task_id,
                task_path=task_path,
                message=f"任务目录无法读取：{read_error}",
            )
        )

    modes = list(template.modes)
    checks: dict[str, ModeCheck] = {}
    if "single" in modes:
        checks["single"] = _check_single(files, task_id, task_path, template, matcher)
    if "multi" in modes:
        checks["multi"] = _check_multi(files, task_id, task_path, template, matcher)

    chosen: ModeCheck | None = None
    if len(checks) == 1:
        chosen = next(iter(checks.values()))
    else:
        single = checks.get("single")
        multi = checks.get("multi")
        passing = [item for item in checks.values() if item.passes]
        if passing:
            # 同时通过时优先单图（有明确主图）
            chosen = single if single is not None and single.passes else passing[0]
        elif single is not None and multi is not None:
            # 都没通过时，先按“是否识别到对应结构的图片”判断，避免把多图任务误判成单图缺主图
            if single.image_count == 0 and multi.image_count > 0:
                chosen = multi
            elif multi.image_count == 0 and single.image_count > 0:
                chosen = single
            else:
                chosen = min(
                    checks.values(),
                    key=lambda item: (
                        item.error_count,
                        item.warn_count,
                        0 if item.mode == "single" else 1,
                    ),
                )
        else:
            chosen = min(
                checks.values(),
                key=lambda item: (item.error_count, item.warn_count),
            )

    assert chosen is not None
    issues = list(read_issues) + list(chosen.issues)
    if len(checks) > 1 and not any(item.passes for item in checks.values()):
        if chosen.image_count == 0:
            issues.append(
                make_issue(
                    "FC-002",
                    task_id=task_id,
                    task_path=task_path,
                    expected="单图主图或多图 ID_1..n",
                    actual="未发现可识别图片",
                )
            )

    _consume_common_issues(files, task_id, task_path, issues)

    # 文件名 ID 与目录 ID 不一致
    for entry in files:
        if _is_ignored_extra(entry.name, template.scan.ignore_names):
            continue
        leading = matcher.extract_leading_id(entry.stem)
        if leading and leading.casefold() != task_id.casefold():
            issues.append(
                _file_issue(
                    "FC-003",
                    task_id,
                    task_path,
                    entry,
                    expected=task_id,
                    actual=leading,
                )
            )

    if duplicate_of:
        issues.append(
            make_issue(
                "FC-004",
                task_id=task_id,
                task_path=task_path,
                expected="同一 ID 只出现一次",
                actual="；".join(duplicate_of),
            )
        )

    issues.extend(
        _extra_issues(files, subdirs, chosen.consumed, task_id, task_path, template)
    )

    result = _determine_result(issues, template.warn_as_fail)
    try:
        fingerprint = directory_fingerprint_from_path(task_path)
    except OSError:
        fingerprint = ""

    return TaskResult(
        task_id=task_id,
        task_path=task_path,
        detected_mode=chosen.mode,
        result=result,
        image_count=chosen.image_count,
        txt_count=chosen.txt_count,
        file_count=len(files),
        issues=issues,
        fingerprint=fingerprint,
    )
