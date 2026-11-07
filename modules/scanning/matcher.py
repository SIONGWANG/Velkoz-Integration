"""层级匹配：带回溯的动态匹配 + 原因码。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from modules.common.timeutil import parse_date
from .rules import LayerRule, RuleSet


@dataclass
class MatchResult:
    assignment: dict[str, str] = field(default_factory=dict)
    indices: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)
    score: float = 0.0
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    explain: str = ""
    ambiguous: bool = False
    matched_required: int = 0
    required_total: int = 0
    required_missing: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return not self.required_missing

    def reason_json(self) -> dict:
        return {
            "matched_layers": dict(self.assignment),
            "skipped_layers": list(self.skipped),
            "extra_segments": list(self.extras),
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "explain": self.explain,
        }


def _id_match(segment: str, id_rule: dict) -> bool:
    mode = id_rule.get("mode", "digits")
    if mode == "digits":
        length = int(id_rule.get("length", 8))
        return segment.isdigit() and len(segment) == length
    if mode == "prefix_digits":
        prefix = str(id_rule.get("prefix", "JD"))
        length = int(id_rule.get("length", 8))
        return segment.startswith(prefix) and segment[len(prefix):].isdigit() and len(segment[len(prefix):]) == length
    if mode == "regex":
        pattern = id_rule.get("regex")
        if not pattern:
            return False
        try:
            return bool(re.fullmatch(pattern, segment))
        except re.error:
            return False
    return False


def match_layer(layer: LayerRule, segment: str) -> bool:
    if not segment:
        return False
    match = layer.match or {"mode": "any"}
    mode = match.get("mode", "any")
    case_sensitive = bool(match.get("case_sensitive", False))
    value = segment if case_sensitive else segment.casefold()

    if mode == "any":
        return True
    if mode == "literal":
        expected = str(match.get("value", ""))
        return value == (expected if case_sensitive else expected.casefold())
    if mode == "list":
        values = [str(v) for v in (match.get("values") or [])]
        if case_sensitive:
            return segment in values
        return value in {v.casefold() for v in values}
    if mode == "regex":
        pattern = match.get("regex")
        if not pattern:
            return False
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            return bool(re.fullmatch(pattern, segment, flags))
        except re.error:
            return False
    if mode == "date":
        return parse_date(segment) is not None
    if mode == "id":
        return _id_match(segment, match.get("id_rule") or {})
    if mode == "name":
        return not segment.isdigit()
    return False


def match_path(segments: list[str], ruleset: RuleSet) -> MatchResult:
    """返回最佳层级分配。

    置信度按“必需层满足度”计算，避免通用规则下缺少可选组织层时全部低置信。
    """
    layers = ruleset.enabled_layers()
    required_total = sum(1 for layer in layers if layer.required)
    best: dict | None = None
    best_count = 0
    state_count = 0
    max_states = 30000

    def consider(assignment, indices, extras, skipped, score):
        nonlocal best, best_count
        candidate = (
            score,
            dict(assignment),
            dict(indices),
            list(extras),
            list(skipped),
        )
        if best is None or score > best[0] + 1e-9:
            best = candidate
            best_count = 1
        elif abs(score - best[0]) <= 1e-9:
            best_count += 1

    def dfs(i: int, j: int, assignment: dict, indices: dict, extras: list, skipped: list, score: float):
        nonlocal state_count
        state_count += 1
        if state_count > max_states:
            return
        if i == len(layers):
            if j == len(segments):
                consider(assignment, indices, extras, skipped, score)
            return

        layer = layers[i]

        # 可选层跳过
        if layer.skippable and not layer.required:
            dfs(i + 1, j, assignment, indices, extras, skipped + [layer.key], score - 0.05)

        # 当前段匹配当前层
        if j < len(segments) and match_layer(layer, segments[j]):
            assignment[layer.key] = segments[j]
            indices[layer.key] = j
            dfs(i + 1, j + 1, assignment, indices, extras, skipped, score + 1.0)
            assignment.pop(layer.key, None)
            indices.pop(layer.key, None)

        # 允许额外中间层：消费当前段，层不变
        if layer.allow_extra_between and j < len(segments) and len(extras) < ruleset.max_extra_between:
            extras.append(segments[j])
            dfs(i, j + 1, assignment, indices, extras, skipped, score - 0.2)
            extras.pop()

    dfs(0, 0, {}, {}, [], [], 0.0)

    if best is None:
        return MatchResult(
            confidence=0.0,
            reasons=["DR-005"],
            explain="必需层无法匹配：没有找到满足当前规则方案的目录结构。",
            required_total=required_total,
            required_missing=[layer.key for layer in layers if layer.required],
        )

    score, assignment, indices, extras, skipped = best
    matched_required = sum(1 for layer in layers if layer.required and layer.key in assignment)
    required_missing = [layer.key for layer in layers if layer.required and layer.key not in assignment]
    confidence = 1.0 if required_total == 0 else matched_required / required_total
    if not required_missing:
        # 额外层轻微降低置信度，但不把通用规则打成异常
        confidence = max(0.0, confidence - 0.03 * len(extras))

    reasons: list[str] = []
    if skipped:
        reasons.append("DR-006")
    if extras:
        reasons.append("DR-007")
    if best_count > 1:
        reasons.append("DR-008")
    if required_missing:
        reasons.append("DR-005")

    parts: list[str] = []
    if assignment:
        parts.append("已识别：" + "、".join(f"{k}={v}" for k, v in assignment.items()))
    if skipped:
        parts.append("跳过可选层：" + "、".join(skipped))
    if extras:
        parts.append("额外目录：" + "、".join(extras))
    if required_missing:
        parts.append("缺少必需层：" + "、".join(required_missing))
    if best_count > 1:
        parts.append("存在多个同分分配，建议人工确认")

    return MatchResult(
        assignment=assignment,
        indices=indices,
        skipped=skipped,
        extras=extras,
        score=score,
        confidence=max(0.0, min(1.0, confidence)),
        reasons=reasons,
        explain="；".join(parts) if parts else "匹配成功",
        ambiguous=best_count > 1,
        matched_required=matched_required,
        required_total=required_total,
        required_missing=required_missing,
    )
