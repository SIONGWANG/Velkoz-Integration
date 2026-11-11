"""飞书摘要（v1.25.10）。

只在本机生成可粘贴文本与 CSV，不联网、不需要密钥。
后续接飞书开放平台时，实现 ``FeishuAdapter.send`` 即可，本模块其余部分不变。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol


def _text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _status_word(row: dict[str, Any]) -> str:
    state = str(row.get("acceptance_state") or "")
    if state == "completed":
        return "已完成"
    if state == "paused":
        return "已暂停"
    if state == "in_progress":
        return "进行中"
    if int(row.get("accepted_records") or 0) <= 0:
        return "待验收"
    return "待验收" if int(row.get("remaining_count") or 0) > 0 else "已完成"


def batch_line(row: dict[str, Any], *, library_root: str = "") -> str:
    """单个批次的一行摘要。"""
    name = _text(row, "name", "display_id") or "未命名批次"
    team = _text(row, "team") or "未指定团队"
    category = " / ".join(
        part for part in (_text(row, "category_l1"), _text(row, "category_l2")) if part
    ) or "未分类"
    total = int(row.get("task_count") or 0)
    records = int(row.get("accepted_records") or 0)
    if records <= 0:
        detail = f"共 {total} 组，暂无已导入验收记录"
    else:
        detail = (
            f"共 {total} 组，合格 {int(row.get('accepted_qualified') or 0)}，"
            f"不合格 {int(row.get('accepted_rejected') or 0)}"
        )
    line = f"- {team} · {category} · {name} | {detail} | {_status_word(row)}"
    if library_root and row.get("batch_path"):
        line += f" | 已导出：{library_root}"
    return line


def daily_summary(
    rows: Iterable[dict[str, Any]],
    *,
    date: str = "",
    library_root: str = "",
) -> str:
    """生成当天可粘贴到飞书表格的摘要文本。"""
    rows = list(rows)
    title = f"{date} 验收日报" if date else "验收日报"
    if not rows:
        return f"{title}\n暂无已导入验收记录。"

    lines = [f"{title}（共 {len(rows)} 批 / {sum(int(r.get('task_count') or 0) for r in rows)} 组）"]
    lines.extend(batch_line(row, library_root=library_root) for row in rows)

    qualified = sum(int(row.get("accepted_qualified") or 0) for row in rows)
    rejected = sum(int(row.get("accepted_rejected") or 0) for row in rows)
    finished = sum(1 for row in rows if _status_word(row) == "已完成")
    pending = len(rows) - finished
    lines.append("")
    lines.append(f"合计：合格 {qualified}，不合格 {rejected}，已完成 {finished} 批，待处理 {pending} 批。")
    return "\n".join(lines)


def summary_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """摘要对应的明细行，可直接写 CSV。"""
    result = []
    for row in rows:
        result.append(
            {
                "日期": _text(row, "submit_date"),
                "团队": _text(row, "team"),
                "大类目": _text(row, "category_l1"),
                "小类目": _text(row, "category_l2"),
                "批次": _text(row, "name", "display_id"),
                "数据量": int(row.get("task_count") or 0),
                "合格": int(row.get("accepted_qualified") or 0),
                "不合格": int(row.get("accepted_rejected") or 0),
                "状态": _status_word(row),
            }
        )
    return result


@dataclass
class FeishuSendResult:
    sent: bool
    reason: str
    text: str


class FeishuAdapter(Protocol):
    """飞书写入适配器；接开放平台时实现 send 即可。"""

    def send(self, text: str) -> FeishuSendResult:  # pragma: no cover - 协议定义
        ...


class LocalSummaryAdapter:
    """默认适配器：只把文本返回给页面，不发起任何网络请求。"""

    def send(self, text: str) -> FeishuSendResult:
        return FeishuSendResult(sent=False, reason="local_only", text=text)
