"""工作台视图逻辑（v1.25.10）。

把“下一步做什么、怎么分组、怎么筛选”放在纯函数里，
页面只负责渲染，便于测试且避免状态判断散落在 UI 中。
"""
from __future__ import annotations

from typing import Any, Iterable

from modules.workspace.models import (
    ACCEPTANCE_STATE_COMPLETED,
    ACCEPTANCE_STATE_IN_PROGRESS,
    ACCEPTANCE_STATE_NO_RECORDS,
    ACCEPTANCE_STATE_PENDING,
    FINAL_STATE_COMPLETED,
    FORMAT_STATE_FAILED,
    FORMAT_STATE_NOT_CHECKED,
    FORMAT_STATE_WARNING,
    SOURCE_FINAL,
    TRANSLATION_STATE_FAILED,
    TRANSLATION_STATE_MISSING_ZH,
    TRANSLATION_STATE_PARTIAL,
    TRANSLATION_STATE_PENDING,
    TRANSLATION_STATE_RUNNING,
)

TODO_TRANSLATION = {
    TRANSLATION_STATE_PENDING,
    TRANSLATION_STATE_PARTIAL,
    TRANSLATION_STATE_FAILED,
    TRANSLATION_STATE_MISSING_ZH,
    TRANSLATION_STATE_RUNNING,
}
TODO_ACCEPTANCE = {
    ACCEPTANCE_STATE_NO_RECORDS,
    ACCEPTANCE_STATE_PENDING,
    ACCEPTANCE_STATE_IN_PROGRESS,
}


def is_anomaly(row: dict[str, Any]) -> bool:
    return bool(
        row.get("format_state") in {FORMAT_STATE_FAILED, FORMAT_STATE_WARNING}
        or row.get("translation_state") == TRANSLATION_STATE_FAILED
        or int(row.get("import_errors") or 0) > 0
        or int(row.get("archive_conflicts") or 0) > 0
        or int(row.get("archive_failed") or 0) > 0
    )


def next_action(row: dict[str, Any]) -> tuple[str, str]:
    """返回 (动作 key, 按钮文字)。只给一个主按钮，避免状态链干扰。"""
    if row.get("source_type") == SOURCE_FINAL:
        if row.get("final_state") != FINAL_STATE_COMPLETED:
            return "acceptance", "开始终验"
        if int(row.get("archived_groups") or 0) <= 0:
            return "archive", "导出与归档"
        return "detail", "查看详情"

    if row.get("format_state") == FORMAT_STATE_FAILED:
        return "format", "处理格式异常"
    if row.get("format_state") == FORMAT_STATE_NOT_CHECKED:
        return "format", "开始格式检查"
    if row.get("translation_state") in TODO_TRANSLATION:
        return "translate", "继续翻译"
    if row.get("acceptance_state") in TODO_ACCEPTANCE:
        return "acceptance", "开始验收"
    if int(row.get("archived_groups") or 0) <= 0:
        return "archive", "导出与归档"
    return "detail", "查看详情"


def status_word(row: dict[str, Any]) -> str:
    state = row.get("final_state") if row.get("source_type") == SOURCE_FINAL else row.get("acceptance_state")
    return {
        ACCEPTANCE_STATE_COMPLETED: "已完成",
        "completed": "已完成",
        ACCEPTANCE_STATE_IN_PROGRESS: "进行中",
        "in_progress": "进行中",
        "paused": "已暂停",
        "pending": "待验收",
        ACCEPTANCE_STATE_NO_RECORDS: "待验收",
    }.get(str(state or ""), "待验收")


def progress_text(row: dict[str, Any]) -> str:
    total = int(row.get("task_count") or 0)
    accepted = int(row.get("accepted_records") or 0)
    if total <= 0:
        return "暂无数据组"
    if accepted <= 0:
        return f"待验收 · {total} 组"
    percent = min(100, round(accepted * 100 / total))
    return f"已验收 {accepted}/{total} 组 · {percent}%"


def result_text(row: dict[str, Any]) -> str:
    if int(row.get("accepted_records") or 0) <= 0:
        return "暂无已导入验收记录"
    return (
        f"合格 {int(row.get('accepted_qualified') or 0)} · "
        f"不合格 {int(row.get('accepted_rejected') or 0)} · "
        f"剩余 {int(row.get('remaining_count') or 0)}"
    )


def sort_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("submit_date") or ""),
            str(row.get("last_activity") or ""),
            str(row.get("name") or ""),
        ),
        reverse=True,
    )


def filter_rows(
    rows: Iterable[dict[str, Any]],
    *,
    team: str = "",
    category: str = "",
    date: str = "",
    keyword: str = "",
    only_todo: bool = False,
) -> list[dict[str, Any]]:
    needle = (keyword or "").strip().casefold()
    result = []
    for row in rows:
        if team and (row.get("team") or "未指定团队") != team:
            continue
        row_category = " / ".join(
            part for part in (row.get("category_l1") or "", row.get("category_l2") or "") if part
        ) or "未分类"
        if category and row_category != category:
            continue
        if date and row.get("submit_date") != date:
            continue
        if only_todo and status_word(row) == "已完成":
            continue
        if needle:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in ("name", "display_id", "team", "category_l1", "category_l2", "batch_path")
            ).casefold()
            if needle not in haystack:
                continue
        result.append(row)
    return result


def group_by_team(rows: Iterable[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in sort_rows(rows):
        buckets.setdefault(row.get("team") or "未指定团队", []).append(row)
    return [(team, buckets[team]) for team in sorted(buckets, key=lambda value: (value == "未指定团队", value))]


def group_by_category(rows: Iterable[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row.get("category_l1") or "未分类", row.get("category_l2") or "")
        buckets.setdefault(key, []).append(row)
    return [
        (" / ".join(part for part in key if part), buckets[key])
        for key in sorted(buckets, key=lambda value: (value[0], value[1]))
    ]


def todo_counts(rows: Iterable[dict[str, Any]], candidates: int = 0) -> dict[str, int]:
    rows = list(rows)
    pending = sum(1 for row in rows if status_word(row) != "已完成")
    doing = sum(1 for row in rows if status_word(row) == "进行中")
    done = sum(1 for row in rows if status_word(row) == "已完成")
    return {
        "pending": pending,
        "doing": doing,
        "done": done,
        "anomaly": sum(1 for row in rows if is_anomaly(row)),
        "candidates": int(candidates),
        "batches": len(rows),
        "task_count": sum(int(row.get("task_count") or 0) for row in rows),
        "image_count": sum(int(row.get("image_count") or 0) for row in rows),
    }
