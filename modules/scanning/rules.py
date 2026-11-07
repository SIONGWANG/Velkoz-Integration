"""目录规则模型：层级、匹配方式、多方案、版本快照。

不依赖 Streamlit，也不读写源目录。
"""
from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATE_REGEX = r"^(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])$"
DEFAULT_IGNORE_NAMES = [
    "_00_Evidence_*",
    "_00_Backup_*",
    "不合格",
    "归档",
    "node_modules",
    "$RECYCLE.BIN",
    "System Volume Information",
    ".git",
    "__pycache__",
]

FOLDER_BLOCK_RULES = {
    "digits": r"[0-9]",
    "lowercase": r"[a-z]",
    "uppercase": r"[A-Z]",
    "letters": r"[^\W\d_]",
    "alnum": r"[0-9A-Za-z]",
    "wildcard": r".",
}


@dataclass
class LayerRule:
    key: str
    label: str
    enabled: bool = True
    required: bool = False
    skippable: bool = True
    order: int = 0
    match: dict[str, Any] = field(default_factory=lambda: {"mode": "any"})
    allow_extra_between: bool = True
    human_confirm: bool = False
    identity: bool = True
    output: bool = True
    fallback: str = "skip"
    default_value: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_order: int = 0) -> "LayerRule":
        match = data.get("match") or {"mode": "any"}
        return cls(
            key=str(data.get("key", "")).strip(),
            label=str(data.get("label", data.get("key", ""))),
            enabled=bool(data.get("enabled", True)),
            required=bool(data.get("required", False)),
            skippable=bool(data.get("skippable", True)),
            order=int(data.get("order", default_order)),
            match=dict(match),
            allow_extra_between=bool(data.get("allow_extra_between", True)),
            human_confirm=bool(data.get("human_confirm", False)),
            identity=bool(data.get("identity", True)),
            output=bool(data.get("output", True)),
            fallback=str(data.get("fallback", "skip")),
            default_value=str(data.get("default_value", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "enabled": self.enabled,
            "required": self.required,
            "skippable": self.skippable,
            "order": self.order,
            "match": copy.deepcopy(self.match),
            "allow_extra_between": self.allow_extra_between,
            "human_confirm": self.human_confirm,
            "identity": self.identity,
            "output": self.output,
            "fallback": self.fallback,
            "default_value": self.default_value,
        }


@dataclass
class RuleSet:
    id: str
    name: str
    version: int = 1
    schema_version: int = 1
    scope: str = "global"
    scope_id: str = ""
    layers: list[LayerRule] = field(default_factory=list)
    ignore_names: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_NAMES))
    max_extra_between: int = 2
    image_slots: list[dict[str, Any]] = field(default_factory=lambda: [{"stem_suffixes": [""]}])
    text_slots: list[dict[str, Any]] = field(default_factory=lambda: [{"suffixes": ["_CH"]}, {"suffixes": ["_EN"]}])
    image_extensions: list[str] = field(default_factory=lambda: ["jpg", "jpeg", "png"])

    def enabled_layers(self) -> list[LayerRule]:
        return sorted([layer for layer in self.layers if layer.enabled], key=lambda x: x.order)

    def task_layer(self) -> LayerRule | None:
        for layer in self.enabled_layers():
            if layer.key == "task":
                return layer
        enabled = self.enabled_layers()
        return enabled[-1] if enabled else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "version": self.version,
            "layers": [layer.to_dict() for layer in self.layers],
            "extra_dirs": {
                "allow": True,
                "max_extra_between": self.max_extra_between,
                "ignore_names": list(self.ignore_names),
            },
            "file_rules": {
                "image_slots": copy.deepcopy(self.image_slots),
                "text_slots": copy.deepcopy(self.text_slots),
                "image_extensions": list(self.image_extensions),
            },
            "diagnostics": {"explain": True, "sample_limit": 20},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleSet":
        extra = data.get("extra_dirs") or {}
        file_rules = data.get("file_rules") or {}
        layers = [
            LayerRule.from_dict(item, default_order=index)
            for index, item in enumerate(data.get("layers") or [])
        ]
        return cls(
            id=str(data.get("id", "custom")),
            name=str(data.get("name", "自定义规则")),
            version=int(data.get("version", 1)),
            schema_version=int(data.get("schema_version", 1)),
            scope=str(data.get("scope", "global")),
            scope_id=str(data.get("scope_id", "")),
            layers=layers,
            ignore_names=list(extra.get("ignore_names") or DEFAULT_IGNORE_NAMES),
            max_extra_between=int(extra.get("max_extra_between", 2)),
            image_slots=list(file_rules.get("image_slots") or [{"stem_suffixes": [""]}]),
            text_slots=list(file_rules.get("text_slots") or [{"suffixes": ["_CH"]}, {"suffixes": ["_EN"]}]),
            image_extensions=list(file_rules.get("image_extensions") or ["jpg", "jpeg", "png"]),
        )


def _layer(
    key: str,
    label: str,
    order: int,
    *,
    required: bool = False,
    skippable: bool = True,
    match: dict[str, Any] | None = None,
    allow_extra_between: bool = True,
) -> LayerRule:
    return LayerRule(
        key=key,
        label=label,
        order=order,
        required=required,
        skippable=skippable,
        match=match or {"mode": "any"},
        allow_extra_between=allow_extra_between,
    )


def builtin_ruleset() -> RuleSet:
    """通用规则：所有组织层可选，任务层必需。

    这样不会因为某个供应商缺少小类目/团队目录就整棵目录判失败；
    需要严格时可复制方案并勾选 required。
    """
    return RuleSet(
        id="builtin_generic",
        name="内置通用扫描规则",
        version=1,
        layers=[
            _layer("category_l1", "大类目", 1, match={"mode": "any"}),
            _layer("category_l2", "小类目", 2, match={"mode": "any"}),
            _layer("team", "团队", 3, match={"mode": "any"}),
            _layer("date", "日期", 4, match={"mode": "date", "regex": DATE_REGEX}),
            _layer("batch", "批次名", 5, match={"mode": "any"}),
            _layer("task", "数据", 6, required=True, skippable=False, match={"mode": "id", "id_rule": {"mode": "digits", "length": 8}}, allow_extra_between=False),
        ],
    )


def _block_to_regex(block: dict[str, Any]) -> str | None:
    rule = block.get("rule")
    if rule == "literal":
        value = block.get("value", "")
        return re.escape(value) if isinstance(value, str) and value else None
    if rule == "charset":
        chars = block.get("chars", "")
        if not isinstance(chars, str) or not chars:
            return None
        exclude = chars.startswith("^")
        body = chars[1:] if exclude else chars
        if not body:
            return None
        return (r"[^" + re.escape(body) + r"]") if exclude else (r"[" + re.escape(body) + r"]")
    return FOLDER_BLOCK_RULES.get(rule)


def legacy_folder_regex(legacy: dict[str, Any]) -> str | None:
    folder_pattern = legacy.get("folder_pattern")
    if not folder_pattern or not folder_pattern.get("blocks"):
        length = legacy.get("folder_digit_length")
        if length:
            return rf"^[0-9]{{{int(length)}}}$"
        return None
    parts: list[str] = []
    for block in folder_pattern.get("blocks") or []:
        frag = _block_to_regex(block)
        if not frag:
            return None
        if block.get("rule") == "literal":
            parts.append(frag)
            continue
        count = block.get("count")
        if count is not None:
            try:
                count = int(count)
            except (TypeError, ValueError):
                return None
            if count < 1:
                return None
            parts.append(frag + "{" + str(count) + "}")
            continue
        lo, hi = block.get("min"), block.get("max")
        try:
            lo = int(lo) if lo is not None else 0
            hi = int(hi) if hi is not None else lo
        except (TypeError, ValueError):
            return None
        if hi < lo or hi < 1:
            return None
        parts.append(frag + ("{" + str(lo) + "}" if lo == hi else "{" + f"{lo},{hi}" + "}"))
    return "^" + "".join(parts) + "$"


def ruleset_from_legacy(legacy: dict[str, Any] | None, legacy_path: str | Path | None = None) -> RuleSet:
    """把现有 scan_rules.json 迁移为默认方案（只读，不修改原文件）。"""
    data = copy.deepcopy(legacy or {})
    ruleset = builtin_ruleset()
    if legacy_path:
        ruleset.id = f"legacy_{Path(legacy_path).stem}"
        ruleset.name = f"从 {Path(legacy_path).name} 迁移"
    image_slots = data.get("image_slots")
    if isinstance(image_slots, list) and image_slots:
        ruleset.image_slots = image_slots
    text_slots = data.get("text_slots")
    if isinstance(text_slots, list) and text_slots:
        ruleset.text_slots = text_slots
    task = ruleset.task_layer()
    if task is not None:
        regex = legacy_folder_regex(data)
        if regex:
            task.match = {"mode": "regex", "regex": regex}
        else:
            task.match = {
                "mode": "id",
                "id_rule": {"mode": "digits", "length": int(data.get("folder_digit_length", 8) or 8)},
            }
    return ruleset


def load_legacy_scan_rules(path: str | Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_default_ruleset(program_dir: str | Path | None = None) -> RuleSet:
    base = Path(program_dir) if program_dir else Path(__file__).resolve().parents[2]
    legacy_path = base / "scan_rules.json"
    if legacy_path.is_file():
        return ruleset_from_legacy(load_legacy_scan_rules(legacy_path), legacy_path)
    return builtin_ruleset()


def validate_ruleset(ruleset: RuleSet) -> list[str]:
    errors: list[str] = []
    if not ruleset.name.strip():
        errors.append("规则方案名不能为空")
    if not ruleset.layers:
        errors.append("至少需要一个层级")
    keys = [layer.key for layer in ruleset.layers]
    if len(keys) != len(set(keys)):
        errors.append("层级 key 不能重复")
    if not any(layer.key == "task" for layer in ruleset.layers):
        errors.append("必须包含 task 层")
    for layer in ruleset.layers:
        if not layer.key:
            errors.append("层级 key 不能为空")
        if layer.required and layer.skippable:
            errors.append(f"{layer.key}: required=true 时 skippable 应为 false")
        mode = (layer.match or {}).get("mode", "any")
        if mode in {"regex", "date"}:
            pattern = (layer.match or {}).get("regex")
            if not pattern:
                errors.append(f"{layer.key}: regex/date 模式需要 regex")
            else:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    errors.append(f"{layer.key}: 正则无效 {exc}")
        if mode == "list" and not (layer.match or {}).get("values"):
            errors.append(f"{layer.key}: list 模式需要 values")
        if mode == "id":
            id_rule = (layer.match or {}).get("id_rule") or {}
            if id_rule.get("mode", "digits") not in {"digits", "prefix_digits", "regex"}:
                errors.append(f"{layer.key}: 未知 id_rule.mode")
    return errors


def parse_ruleset_json(text: str) -> RuleSet:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("规则 JSON 顶层必须是对象")
    ruleset = RuleSet.from_dict(data)
    errors = validate_ruleset(ruleset)
    if errors:
        raise ValueError("；".join(errors))
    return ruleset
