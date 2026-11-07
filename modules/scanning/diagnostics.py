"""扫描诊断：把结果整理成可解释的摘要。"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .scanner import ScanResult

REASON_TEXT = {
    "DR-001": "根目录不存在/不可读",
    "DR-002": "任务目录名不符合 ID 规则",
    "DR-003": "层级深度不足",
    "DR-004": "层级深度超出或超过最大深度",
    "DR-005": "必需层无法匹配",
    "DR-006": "可选层被跳过",
    "DR-007": "存在额外中间层",
    "DR-008": "多个分配歧义",
    "DR-009": "目录下无图片",
    "DR-010": "目录下无 TXT",
    "DR-011": "同 ID 重复批次",
    "DR-012": "目录被忽略规则跳过",
    "DR-013": "符号链接/junction 跳过",
    "DR-014": "权限不足或读取失败",
    "DR-015": "超过扫描上限",
    "DR-016": "批次名重复",
    "DR-017": "日期格式不匹配",
    "DR-018": "规则版本冲突",
}


def summarize(result: ScanResult) -> dict[str, Any]:
    status_counts = Counter(candidate.status for candidate in result.candidates)
    reason_counts = Counter(issue.code for issue in result.issues)
    return {
        "text": (
            f"扫描 {result.total_dirs} 个目录，发现 {len(result.groups)} 个数据组、"
            f"{len(result.candidates)} 个候选批次、{len(result.issues)} 个问题。"
        ),
        "status_counts": dict(status_counts),
        "reason_counts": dict(reason_counts),
        "top_reasons": [
            {"code": code, "count": count, "text": REASON_TEXT.get(code, code)}
            for code, count in reason_counts.most_common(10)
        ],
        "truncated": result.truncated,
    }


def explain_candidate(candidate) -> str:
    parts = [f"批次：{candidate.batch_name}", f"置信度：{candidate.confidence:.2f}"]
    if candidate.identity:
        parts.append("身份：" + "、".join(f"{k}={v}" for k, v in candidate.identity.items()))
    if candidate.reasons:
        parts.append("原因：" + "、".join(REASON_TEXT.get(code, code) for code in candidate.reasons))
    if candidate.explain:
        parts.append(candidate.explain)
    return "；".join(parts)
