"""内置模板、模板 JSON 校验与导入导出。"""
from __future__ import annotations

import json
from typing import Any

from .models import FileRule, IdRule, MultiRule, ScanRule, Template

SCOPE_LABELS = {
    "global": "全局",
    "supplier": "供应商",
    "team": "团队",
    "category": "类目",
    "project": "项目",
}

KNOWN_TOP_LEVEL = {
    "schema_version",
    "id",
    "name",
    "version",
    "scope",
    "scope_id",
    "builtin",
    "description",
    "id_rule",
    "modes",
    "single",
    "multi",
    "extra_files",
    "warn_as_fail",
    "scan",
}


def builtin_template() -> Template:
    """与图片编辑工具箱使用说明对齐的默认模板。

    - 主图必需；
    - 结果图、PSD、CH/EN TXT 可选（模板可改为必需）；
    - 多图 _1.._4，默认连续、至少 1 张；
    - 默认只检查所选目录及其直接子目录。
    """
    return Template(
        id="builtin_default",
        name="默认：单图 + 多图（对齐图片编辑工具箱）",
        version=1,
        schema_version=1,
        scope="global",
        builtin=True,
        description=(
            "单图：ID.jpg/jpeg/png（必需）、ID_result（可选）、ID.psd（可选）、"
            "ID_CH.txt / ID_EN.txt（可选）；多图：ID_1..ID_4，默认连续。"
        ),
        id_rule=IdRule(mode="digits", length=8),
        modes=("single", "multi"),
        single=(
            FileRule(suffix="", extensions=("jpg", "jpeg", "png"), required=True, role="primary"),
            FileRule(suffix="_result", extensions=("jpg", "jpeg", "png"), required=False, role="result"),
            FileRule(suffix="", extensions=("psd",), required=False, role="psd"),
            FileRule(suffix="_CH", extensions=("txt",), required=False, role="zh"),
            FileRule(suffix="_EN", extensions=("txt",), required=False, role="en"),
        ),
        multi=MultiRule(
            suffix_prefix="_",
            start=1,
            max_count=4,
            contiguous=True,
            min_count=1,
            extensions=("jpg", "jpeg", "png"),
            allow_result=False,
        ),
        extra_files="warn",
        warn_as_fail=False,
        scan=ScanRule(recursive=False),
    )


def sample_template() -> dict[str, Any]:
    return builtin_template().to_dict()


def validate_template(template: Template) -> list[str]:
    errors: list[str] = []
    if not template.id:
        errors.append("模板 id 不能为空")
    if not template.name:
        errors.append("模板名称不能为空")
    if template.schema_version != 1:
        errors.append(f"不支持的 schema_version：{template.schema_version}")
    if template.id_rule.mode not in {"digits", "prefix_digits", "regex"}:
        errors.append(f"未知 ID 规则模式：{template.id_rule.mode}")
    if template.id_rule.mode in {"digits", "prefix_digits"} and template.id_rule.length < 1:
        errors.append("ID 位数必须大于 0")
    if template.id_rule.mode == "prefix_digits" and not template.id_rule.prefix:
        errors.append("前缀 ID 模式必须提供前缀")
    if template.id_rule.mode == "regex":
        import re

        try:
            re.compile(template.id_rule.regex)
        except re.error as exc:
            errors.append(f"ID 正则无效：{exc}")
    if not template.modes:
        errors.append("至少需要启用一种检查模式")
    unknown_modes = set(template.modes) - {"single", "multi"}
    if unknown_modes:
        errors.append("未知检查模式：" + "、".join(sorted(unknown_modes)))
    if "single" in template.modes and not template.single:
        errors.append("启用单图模式时必须配置单图规则")
    if "multi" in template.modes and template.multi is None:
        errors.append("启用多图模式时必须配置多图规则")
    if template.extra_files not in {"ignore", "warn", "error"}:
        errors.append("extra_files 只能是 ignore/warn/error")
    if template.scan.max_tasks < 1:
        errors.append("max_tasks 必须大于 0")
    for index, rule in enumerate(template.single, start=1):
        if not rule.extensions:
            errors.append(f"单图规则 {index} 扩展名不能为空")
        if rule.role not in {"primary", "result", "psd", "zh", "en", "other"}:
            errors.append(f"单图规则 {index} role 未知：{rule.role}")
        if rule.suffix and not rule.suffix.startswith(("_", "-")):
            errors.append(f"单图规则 {index} 后缀建议以 _ 或 - 开头")
    if template.multi is not None:
        multi = template.multi
        if not multi.extensions:
            errors.append("多图扩展名不能为空")
        if multi.min_count < 1:
            errors.append("多图最小数量必须大于等于 1")
        if multi.max_count is not None and multi.max_count < multi.min_count:
            errors.append("多图最大数量不能小于最小数量")
        if multi.start < 0:
            errors.append("多图起始编号不能为负数")
    return errors


def unknown_fields(data: dict[str, Any]) -> list[str]:
    return sorted(set(data.keys()) - KNOWN_TOP_LEVEL)


def parse_template_json(text: str) -> tuple[Template, list[str]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("模板 JSON 顶层必须是对象")
    template = Template.from_dict(data)
    errors = validate_template(template)
    if errors:
        raise ValueError("；".join(errors))
    return template, unknown_fields(data)


def template_to_json(template: Template) -> str:
    return template.to_json()
