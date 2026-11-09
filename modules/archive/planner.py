"""归档计划：模板渲染、起始层级、只读扫描、冲突检测与目录树预览。

本模块只读取源目录元信息（路径/大小），不读取图片内容，也不创建、
修改或删除任何源文件与目标文件。
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable

from modules.common.path_guard import is_relative_to, is_reparse_point, normalize_path, safe_join
from modules.common.timeutil import parse_date, today_str

from .models import (
    DEFAULT_NAME_TEMPLATE,
    START_LAYER_KEYS,
    TEMPLATE_VARIABLES,
    ArchiveFile,
    ArchiveGroupPlan,
    ArchivePreview,
)

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_NAME_LENGTH = 150


def sanitize_name(value: Any, fallback: str = "") -> str:
    """把任意值变成安全的单层目录/文件名。不改变源文件。"""
    text = str(value if value is not None else "").strip()
    text = _INVALID_CHARS.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(" .")
    if not text or text in {".", ".."}:
        return fallback
    stem = text.split(".", 1)[0].upper()
    if stem in _RESERVED_NAMES:
        text = "_" + text
    if len(text) > _MAX_NAME_LENGTH:
        text = text[:_MAX_NAME_LENGTH].rstrip(" .")
    return text


def parse_identity(text: str | None) -> dict[str, str]:
    import json

    if not text:
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): str(val) for key, val in value.items() if val is not None}


def render_template(
    template: str,
    values: dict[str, str],
) -> tuple[str, list[str]]:
    """渲染命名模板；未知变量报错，缺失变量按回退规则处理。

    返回 (目录名, 缺失变量列表)。目录名已做 Windows 非法字符清洗。
    """
    text = (template or "").strip() or DEFAULT_NAME_TEMPLATE
    missing: list[str] = []
    unknown = [name for name in _PLACEHOLDER.findall(text) if name not in TEMPLATE_VARIABLES]
    if unknown:
        raise ValueError("模板包含未知变量：" + "、".join(sorted(set(unknown))))

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = str(values.get(name) or "").strip()
        if not value:
            missing.append(name)
        return value

    rendered = _PLACEHOLDER.sub(replace, text)
    rendered = re.sub(r"[_\-\s]{2,}", "_", rendered)
    rendered = rendered.strip(" _-")
    return sanitize_name(rendered, "未命名批次"), missing


def _batch_context(
    batch: dict[str, Any],
    identity: dict[str, str],
    *,
    operator: str,
    qualified_count: int,
    custom_prefix: str,
) -> dict[str, str]:
    submit_date = str(batch.get("submit_date") or identity.get("date") or "").strip()
    date_value = parse_date(submit_date) or submit_date or today_str()
    return {
        "批次名": str(batch.get("name") or identity.get("batch") or "未命名批次"),
        "验收员": operator or "未指定",
        "合格数量": str(max(0, int(qualified_count))),
        "日期": date_value,
        "大类目": str(batch.get("category_l1") or identity.get("category_l1") or ""),
        "小类目": str(batch.get("category_l2") or identity.get("category_l2") or ""),
        "团队": str(identity.get("team") or batch.get("team_id") or ""),
        "自定义": str(custom_prefix or ""),
    }


def parse_custom_prefix(value: str) -> list[str]:
    """把自定义前缀拆成安全目录段；拒绝绝对路径与 ..。"""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("起始层级为“自定义”时必须填写自定义前缀")
    if os.path.isabs(raw) or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("自定义前缀必须是相对目录，不能是绝对路径")
    parts = [part for part in re.split(r"[\\/]+", raw) if part]
    if not parts:
        raise ValueError("自定义前缀不能为空")
    result: list[str] = []
    for part in parts:
        if part in {".", ".."}:
            raise ValueError("自定义前缀不能包含 . 或 ..")
        name = sanitize_name(part)
        if not name:
            raise ValueError(f"自定义前缀包含非法目录名：{part!r}")
        result.append(name)
    return result


_LAYER_ORDER = ("root", "category_l1", "category_l2", "team", "date", "batch")
_LAYER_VALUE_KEYS = {
    "category_l1": "大类目",
    "category_l2": "小类目",
    "team": "团队",
    "date": "日期",
}


def build_prefix(
    start_layer: str,
    context: dict[str, str],
    *,
    source_root: str,
    custom_prefix: str,
) -> tuple[list[str], list[str]]:
    """根据起始层级保留源目录结构，返回 (目录段, 缺失层级说明)。

    批次层由命名模板替换，因此不直接出现在前缀里。
    """
    if start_layer not in START_LAYER_KEYS:
        raise ValueError(f"不支持的起始层级：{start_layer}")
    missing: list[str] = []
    if start_layer == "custom":
        return parse_custom_prefix(custom_prefix), missing
    if start_layer == "batch":
        return [], missing

    index = _LAYER_ORDER.index(start_layer)
    keys = [key for key in _LAYER_ORDER[index:] if key != "batch"]
    segments: list[str] = []
    if "root" in keys:
        root_name = sanitize_name(os.path.basename(normalize_path(source_root)), "根目录")
        segments.append(root_name)
    for key in keys:
        if key == "root":
            continue
        value = context.get(_LAYER_VALUE_KEYS[key], "")
        if value:
            segments.append(sanitize_name(value, ""))
        else:
            missing.append(_LAYER_VALUE_KEYS[key])
    return [segment for segment in segments if segment], missing


def scan_group_files(source_path: str) -> tuple[list[ArchiveFile], list[str]]:
    """只读列出数据组内所有普通文件；不跟随符号链接/junction。"""
    root = normalize_path(source_path)
    if not os.path.isdir(root):
        return [], [f"源目录不存在或不是目录：{root}"]
    if is_reparse_point(root):
        return [], [f"源目录是符号链接/重解析点，已跳过：{root}"]
    files: list[ArchiveFile] = []
    notes: list[str] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if not is_reparse_point(os.path.join(current, name))
        ]
        for name in names:
            path = os.path.join(current, name)
            if is_reparse_point(path):
                notes.append(f"跳过重解析点：{os.path.relpath(path, root)}")
                continue
            try:
                size = os.stat(path).st_size
            except OSError as exc:
                notes.append(f"无法读取文件信息：{os.path.relpath(path, root)}（{exc}）")
                continue
            relative = os.path.relpath(path, root)
            if relative.startswith(".."):
                notes.append(f"跳过越界路径：{relative}")
                continue
            files.append(
                ArchiveFile(
                    source_path=path,
                    relative_path=relative,
                    size=int(size),
                )
            )
    files.sort(key=lambda item: item.relative_path.casefold())
    return files, notes


def existing_conflicts(dest_path: str, *, limit: int = 200) -> list[str]:
    """列出目标位置已有内容；只读，不修改目标。"""
    if not os.path.exists(dest_path):
        return []
    if not os.path.isdir(dest_path):
        return ["目标路径已存在且不是目录"]
    conflicts: list[str] = []
    for current, dirs, names in os.walk(dest_path, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if not is_reparse_point(os.path.join(current, name))
        ]
        for name in names:
            conflicts.append(os.path.relpath(os.path.join(current, name), dest_path))
            if len(conflicts) >= limit:
                return conflicts
    if not conflicts:
        conflicts.append("目标目录已存在（空目录）")
    return conflicts


def build_dest_path(
    output_root: str,
    prefix_segments: Iterable[str],
    folder_name: str,
    task_id: str,
) -> str:
    """安全拼接目标目录；拒绝路径穿越。"""
    parts = [*prefix_segments, folder_name, sanitize_name(task_id, "任务")]
    path = normalize_path(output_root)
    for part in parts:
        path = safe_join(path, part)
    return path


def validate_output_root(
    output_root: str,
    *,
    source_root: str,
    group_sources: Iterable[str],
) -> tuple[str, list[str]]:
    """校验输出根目录，返回 (规范路径, 错误列表)。不创建目录。"""
    errors: list[str] = []
    raw = (output_root or "").strip()
    if not raw:
        return "", ["未填写输出根目录"]
    try:
        target = normalize_path(raw)
    except (OSError, ValueError) as exc:
        return "", [f"输出根目录无效：{exc}"]
    if os.path.exists(target) and not os.path.isdir(target):
        errors.append("输出根目录已存在且不是目录")
    if source_root:
        source = normalize_path(source_root)
        if os.path.normcase(target) == os.path.normcase(source):
            errors.append("输出根目录不能与源数据根目录相同")
        elif is_relative_to(target, source):
            errors.append("输出根目录不能位于源数据目录内部")
    for group_source in group_sources:
        source = normalize_path(group_source)
        if os.path.normcase(target) == os.path.normcase(source) or is_relative_to(target, source):
            errors.append("输出根目录不能位于数据组源目录内部")
            break
    return target, errors


def _tree_rows(groups: list[ArchiveGroupPlan], output_root: str) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    root = normalize_path(output_root)
    for group in groups:
        try:
            relative = os.path.relpath(group.dest_path, root)
        except ValueError:
            continue
        if relative.startswith(".."):
            continue
        parts = relative.split(os.sep)
        for index in range(1, len(parts) + 1):
            current = os.sep.join(parts[:index])
            rows.setdefault(
                current,
                {
                    "relative": current,
                    "depth": index,
                    "items": 0,
                    "files": 0,
                    "bytes": 0,
                    "conflict": False,
                },
            )
        leaf = rows.get(relative)
        if leaf is not None:
            leaf["items"] += 1
            leaf["files"] += group.file_count
            leaf["bytes"] += group.bytes_total
            leaf["conflict"] = leaf["conflict"] or group.has_conflict
    return [rows[key] for key in sorted(rows, key=lambda text: text.casefold())]


def format_tree(rows: list[dict[str, Any]], output_root: str) -> str:
    """把目录树行渲染成缩进文本，供预览展示。"""
    if not rows:
        return "（没有可预览的目录）"
    lines = [normalize_path(output_root)]
    for row in rows:
        indent = "    " * max(0, int(row.get("depth", 1)) - 1)
        suffix = ""
        if row.get("items"):
            suffix = f"  [{row['items']} 组 / {row['files']} 文件 / {row['bytes']} 字节"
            if row.get("conflict"):
                suffix += " / 目标已存在"
            suffix += "]"
        lines.append(f"{indent}{row['relative'].split(os.sep)[-1]}/{suffix}")
    return "\n".join(lines)


def build_preview(
    *,
    batch: dict[str, Any],
    identity: dict[str, str],
    groups: list[dict[str, Any]],
    output_root: str,
    start_layer: str,
    name_template: str,
    operator: str,
    filter_mode: str,
    filter_value: str,
    custom_prefix: str = "",
) -> ArchivePreview:
    """构建复制前预览。全程只读源目录，不创建输出目录。"""
    source_root = str(batch.get("data_root") or batch.get("batch_path") or "").strip()
    group_sources = [str(group.get("normalized_path") or "") for group in groups if group.get("normalized_path")]
    normalized_output, root_errors = validate_output_root(
        output_root,
        source_root=source_root,
        group_sources=group_sources,
    )
    folder_name, missing_variables = render_template(
        name_template,
        _batch_context(
            batch,
            identity,
            operator=operator,
            qualified_count=len(groups),
            custom_prefix=custom_prefix,
        ),
    )
    prefix, prefix_missing = build_prefix(
        start_layer,
        _batch_context(
            batch,
            identity,
            operator=operator,
            qualified_count=len(groups),
            custom_prefix=custom_prefix,
        ),
        source_root=source_root,
        custom_prefix=custom_prefix,
    )
    preview = ArchivePreview(
        batch_id=str(batch.get("id") or ""),
        batch_name=str(batch.get("name") or ""),
        source_root=source_root,
        output_root=normalized_output,
        start_layer=start_layer,
        name_template=name_template,
        operator=operator,
        filter_mode=filter_mode,
        filter_value=filter_value,
        custom_prefix=custom_prefix,
        missing_variables=sorted(set(missing_variables + prefix_missing)),
        warnings=[],
        errors=list(root_errors),
        rendered_folder=folder_name,
    )
    if not groups:
        preview.errors.append("当前筛选条件下没有数据组可归档")
        return preview
    if preview.errors:
        return preview

    seen_dest: dict[str, str] = {}
    for group in groups:
        group_source = str(group.get("normalized_path") or "")
        task_id = str(group.get("task_id") or os.path.basename(group_source.rstrip("\\/")) or "任务")
        plan = ArchiveGroupPlan(
            data_group_id=str(group.get("id") or ""),
            task_id=task_id,
            source_path=normalize_path(group_source) if group_source else "",
            dest_path="",
        )
        if not group_source:
            plan.error = "数据组缺少源路径"
            preview.groups.append(plan)
            continue
        try:
            plan.dest_path = build_dest_path(normalized_output, prefix, folder_name, task_id)
        except ValueError as exc:
            plan.error = f"目标路径无效：{exc}"
            preview.groups.append(plan)
            continue
        key = os.path.normcase(plan.dest_path)
        if key in seen_dest:
            plan.error = f"目标目录与数据组 {seen_dest[key]} 重复，未生成复制计划"
        else:
            seen_dest[key] = plan.data_group_id
        files, notes = scan_group_files(plan.source_path)
        plan.files = files
        plan.notes.extend(notes)
        if not files and not plan.error:
            plan.error = "源目录中没有可复制文件"
        if plan.dest_path:
            plan.conflict_paths = existing_conflicts(plan.dest_path)
        preview.groups.append(plan)

    preview.tree = _tree_rows(preview.groups, normalized_output) if normalized_output else []
    if preview.conflict_groups:
        preview.warnings.append(
            f"{preview.conflict_groups} 个数据组的目标目录已存在；复制时不会覆盖，"
            "相同文件跳过，不同文件标记冲突。"
        )
    if preview.error_groups:
        preview.warnings.append(f"{preview.error_groups} 个数据组无法生成完整计划，详见下表。")
    if preview.missing_variables:
        preview.warnings.append(
            "模板/目录变量缺失，已按回退规则处理：" + "、".join(preview.missing_variables)
        )
    if not preview.errors and preview.error_groups == len(preview.groups):
        preview.errors.append("所有数据组都无法生成归档计划")
    return preview
