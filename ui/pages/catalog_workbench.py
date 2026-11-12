"""管理中心卡片层级工作台（v1.25.11 P2）。

只读目录结构、只写管理数据库；不修改源图片/TXT/CSV。
层级：大类 → 小类 → 三级类 → 批次。缺失层级自动跳过。
"""
from __future__ import annotations

import hashlib
import html
from typing import Any, Iterable

import streamlit as st

from modules.config import app_config
from modules.database.connection import open_database
from modules.workspace.catalog_service import (
    FORMAT_CHECKED,
    FORMAT_FAILED,
    MAIN_COMPLETED,
    MAIN_NEW,
    TRANSLATION_FAILED,
    TRANSLATION_TRANSLATED,
    CatalogService,
)
from modules.workspace.catalog_scan import apply_scan, preview_scan
from modules.workspace.models import SOURCE_FINAL, SOURCE_LABELS, SOURCE_PENDING

LEVEL_FIELDS = {1: "category_l1", 2: "category_l2", 3: "category_l3"}
LEVEL_LABELS = {1: "大类", 2: "小类", 3: "三级类"}
SOURCE_CHOICES = (
    (SOURCE_PENDING, SOURCE_LABELS[SOURCE_PENDING]),
    (SOURCE_FINAL, SOURCE_LABELS[SOURCE_FINAL]),
)

_CSS = """
<style>
.vk-hero {
    padding: 1.05rem 1.2rem;
    border-radius: 18px;
    background: linear-gradient(135deg, rgba(32, 96, 255, .12), rgba(0, 190, 160, .08));
    border: 1px solid rgba(49, 51, 63, .10);
    margin-bottom: .9rem;
}
.vk-hero h2 { margin: 0 0 .2rem 0; font-size: 1.42rem; }
.vk-hero p { margin: 0; opacity: .72; font-size: .92rem; }
.vk-card-title {
    font-size: 1.03rem;
    font-weight: 680;
    line-height: 1.35;
    word-break: break-all;
    margin-bottom: .15rem;
}
.vk-card-sub {
    color: rgba(49, 51, 63, .62);
    font-size: .82rem;
    margin-bottom: .55rem;
    word-break: break-all;
}
.vk-meter { margin: .35rem 0 .5rem 0; }
.vk-meter-head {
    display: flex;
    justify-content: space-between;
    gap: .5rem;
    font-size: .78rem;
    color: rgba(49, 51, 63, .72);
    margin-bottom: .18rem;
}
.vk-track {
    height: 6px;
    background: rgba(49, 51, 63, .10);
    border-radius: 999px;
    overflow: hidden;
    margin-bottom: .38rem;
}
.vk-track > span {
    display: block;
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, #2f6bff, #12b886);
}
.vk-pill {
    display: inline-block;
    padding: .12rem .48rem;
    border-radius: 999px;
    font-size: .74rem;
    line-height: 1.5;
    margin: 0 .22rem .22rem 0;
    background: rgba(49, 51, 63, .08);
    color: rgba(49, 51, 63, .80);
    white-space: nowrap;
}
.vk-pill.ok { background: rgba(18, 184, 134, .14); color: #0b7a5b; }
.vk-pill.new { background: rgba(47, 107, 255, .14); color: #244ecb; }
.vk-pill.warn { background: rgba(255, 145, 0, .16); color: #a35100; }
.vk-pill.muted { background: rgba(49, 51, 63, .08); color: rgba(49, 51, 63, .62); }
.vk-line {
    font-size: .82rem;
    color: rgba(49, 51, 63, .70);
    margin: .18rem 0;
    word-break: break-all;
}
.vk-crumb-current { font-weight: 700; padding-top: .32rem; }
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 16px !important;
    border-color: rgba(49, 51, 63, .10) !important;
    background: rgba(255, 255, 255, .72);
    box-shadow: 0 2px 10px rgba(20, 30, 60, .035);
    transition: box-shadow .16s ease, border-color .16s ease, transform .16s ease;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {
    border-color: rgba(47, 107, 255, .28) !important;
    box-shadow: 0 8px 24px rgba(20, 30, 60, .08);
}
div.stButton > button {
    border-radius: 10px;
    font-weight: 600;
    transition: transform .14s ease, box-shadow .14s ease;
}
div.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 16px rgba(20, 30, 60, .10);
}
div.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #2f6bff, #12b886);
    border: none;
}
div[data-testid="stMetric"] {
    background: linear-gradient(160deg, rgba(47, 107, 255, .07), rgba(18, 184, 134, .05));
    border: 1px solid rgba(49, 51, 63, .09);
    border-radius: 14px;
    padding: .55rem .7rem;
}
div[data-testid="stMetric"] label {
    font-size: .78rem;
    opacity: .78;
}
div[data-testid="stMetricValue"] {
    font-size: 1.35rem;
}
div[data-testid="stTabs"] button[role="tab"] {
    border-radius: 999px;
    padding: .18rem .8rem;
}
div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    background: rgba(47, 107, 255, .12);
    color: #244ecb;
}
</style>
"""


def _key(prefix: str, *parts: Any) -> str:
    raw = "::".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]
    return f"{prefix}_{digest}"


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _go(page: str) -> None:
    from ui.router import go

    go(page)


def _nav_key(project_id: str, source_type: str) -> str:
    return f"catalog_nav::{project_id}::{source_type}"


def _get_path(project_id: str, source_type: str) -> list[dict[str, Any]]:
    value = st.session_state.get(_nav_key(project_id, source_type))
    if not isinstance(value, list):
        value = []
        st.session_state[_nav_key(project_id, source_type)] = value
    return value


def _set_path(project_id: str, source_type: str, path: Iterable[dict[str, Any]]) -> None:
    st.session_state[_nav_key(project_id, source_type)] = [
        {"level": int(item["level"]), "name": str(item.get("name") or "")} for item in path
    ]


def _projects(db) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT id, project_name, pending_path, final_path, enabled"
        " FROM project_roots ORDER BY project_name"
    ).fetchall()
    return [dict(row) for row in rows]


def _summary(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    items = list(rows)
    completed = [row for row in items if row.get("main_status") == MAIN_COMPLETED]
    total_data = sum(int(row.get("data_count") or 0) for row in items)
    completed_data = sum(int(row.get("data_count") or 0) for row in completed)
    return {
        "batches": len(items),
        "data": total_data,
        "completed_batches": len(completed),
        "completed_data": completed_data,
        "pending_batches": len(items) - len(completed),
        "pending_data": max(0, total_data - completed_data),
    }


def _filter_rows(rows: Iterable[dict[str, Any]], path: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    items = list(rows)
    for item in path:
        field = LEVEL_FIELDS[int(item["level"])]
        name = str(item.get("name") or "")
        items = [row for row in items if str(row.get(field) or "").strip() == name]
    return items


def _next_level(rows: Iterable[dict[str, Any]], after: int) -> int:
    items = list(rows)
    for level in range(after + 1, 4):
        field = LEVEL_FIELDS[level]
        if any(str(row.get(field) or "").strip() for row in items):
            return level
    return 4


def _bucket_rows(rows: Iterable[dict[str, Any]], level: int) -> list[dict[str, Any]]:
    field = LEVEL_FIELDS[level]
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name = str(row.get(field) or "").strip()
        buckets.setdefault(name, []).append(row)
    ordered = sorted(buckets.items(), key=lambda item: (item[0] == "", item[0]))
    return [{"name": name, "rows": items} for name, items in ordered]


def _bucket_label(name: str, level: int) -> str:
    return name or f"未设置{LEVEL_LABELS[level]}"


def _percent(done: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, round(done * 100 / total)))


def _meter(label: str, done: int, total: int) -> str:
    return (
        f'<div class="vk-meter">'
        f'<div class="vk-meter-head"><span>{_esc(label)}</span>'
        f"<span>已完成 {done} · 未完成 {max(0, total - done)} · 共 {total}</span></div>"
        f'<div class="vk-track"><span style="width:{_percent(done, total)}%"></span></div>'
        f"</div>"
    )


def _summary_html(rows: Iterable[dict[str, Any]]) -> str:
    data = _summary(rows)
    return (
        _meter("批次", data["completed_batches"], data["batches"])
        + _meter("数据量", data["completed_data"], data["data"])
    )


def _status_pills(row: dict[str, Any]) -> str:
    main = str(row.get("main_status") or MAIN_NEW)
    main_class = "ok" if main == MAIN_COMPLETED else "new"
    fmt = str(row.get("format_status") or "")
    fmt_class = "ok" if fmt == FORMAT_CHECKED else ("warn" if fmt == FORMAT_FAILED else "muted")
    tr = str(row.get("translation_status") or "")
    tr_class = "ok" if tr == TRANSLATION_TRANSLATED else ("warn" if tr == TRANSLATION_FAILED else "muted")
    pills = [
        f'<span class="vk-pill {main_class}">{_esc(row.get("main_status_label") or "新提交")}</span>',
        f'<span class="vk-pill {fmt_class}">检查：{_esc(row.get("format_status_label") or "未检查")}</span>',
        f'<span class="vk-pill {tr_class}">翻译：{_esc(row.get("translation_status_label") or "未翻译")}</span>',
    ]
    if row.get("reviewer_conflict"):
        pills.append('<span class="vk-pill warn">验收员冲突</span>')
    return "".join(pills)


def _render_breadcrumb(project_id: str, source_type: str, path: list[dict[str, Any]]) -> None:
    labels = ["🏠 全部"]
    for item in path:
        labels.append(_bucket_label(str(item.get("name") or ""), int(item["level"])))
    weights = [max(1.2, min(4.0, len(label) * 0.9)) for label in labels]
    if path:
        columns = st.columns([*weights, 1.4], vertical_alignment="center")
    else:
        columns = st.columns([*weights], vertical_alignment="center")
    for index, label in enumerate(labels):
        column = columns[index]
        if index == len(labels) - 1:
            column.markdown(f'<div class="vk-crumb-current">{_esc(label)}</div>', unsafe_allow_html=True)
        elif column.button(label, key=_key("crumb", project_id, source_type, index), use_container_width=True):
            _set_path(project_id, source_type, path[:index])
            st.rerun()
    if path:
        if columns[-1].button("← 上一级", key=_key("crumb_back", project_id, source_type), use_container_width=True):
            _set_path(project_id, source_type, path[:-1])
            st.rerun()


def _render_overview(rows: list[dict[str, Any]]) -> None:
    data = _summary(rows)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("批次数", data["batches"], help="当前项目和来源下识别到的批次数")
    c2.metric("数据量", data["data"], help="编号文件夹总数")
    c3.metric(
        "已完成批次",
        f'{data["completed_batches"]} / {data["batches"]}',
        help="主状态为“已完成”的批次",
    )
    c4.metric(
        "已完成数据",
        f'{data["completed_data"]} / {data["data"]}',
        help="已完成批次包含的编号数据量",
    )


def _render_category_card(
    column,
    *,
    project_id: str,
    source_type: str,
    level: int,
    bucket: dict[str, Any],
    path: list[dict[str, Any]],
) -> None:
    name = str(bucket["name"])
    rows = bucket["rows"]
    label = _bucket_label(name, level)
    data = _summary(rows)
    with column:
        with st.container(border=True, key=_key("catcard", project_id, source_type, level, name)):
            st.markdown(
                f'<div class="vk-card-title">{_esc(label)}</div>'
                f'<div class="vk-card-sub">{data["batches"]} 个批次 · {data["data"]} 条数据</div>'
                + _summary_html(rows),
                unsafe_allow_html=True,
            )
            if st.button(
                "进入 →",
                key=_key("catgo", project_id, source_type, level, name),
                use_container_width=True,
            ):
                _set_path(
                    project_id,
                    source_type,
                    [*path, {"level": level, "name": name}],
                )
                st.rerun()


def _render_category_grid(
    rows: list[dict[str, Any]],
    *,
    project_id: str,
    source_type: str,
    level: int,
    path: list[dict[str, Any]],
) -> None:
    buckets = _bucket_rows(rows, level)
    st.caption(
        f"当前 {len(rows)} 个批次，按{LEVEL_LABELS[level]}分组；点击卡片进入下一层。"
    )
    columns = st.columns(3, gap="medium")
    for index, bucket in enumerate(buckets):
        _render_category_card(
            columns[index % 3],
            project_id=project_id,
            source_type=source_type,
            level=level,
            bucket=bucket,
            path=path,
        )


def _pick_key(batch_id: str) -> str:
    return f"catalog_pick_{batch_id}"


def _render_batch_card(
    column,
    row: dict[str, Any],
    *,
    project_id: str,
    source_type: str,
    queued_ids: set[str],
) -> None:
    batch_id = str(row.get("id") or "")
    name = row.get("name") or "未命名批次"
    team = row.get("team_name") or "未识别团队"
    date = row.get("submit_date") or "日期未知"
    reviewer = row.get("reviewer_name") or "未匹配验收员"
    with column:
        with st.container(border=True, key=_key("batchcard", project_id, source_type, batch_id)):
            st.markdown(
                f'<div class="vk-card-title">{_esc(name)}</div>'
                f'<div class="vk-card-sub">{_esc(team)} · {_esc(date)} · '
                f'{int(row.get("data_count") or 0)} 条数据</div>'
                f'<div>{_status_pills(row)}</div>'
                f'<div class="vk-line">验收员：{_esc(reviewer)}</div>'
                f'<div class="vk-line">合格 {int(row.get("qualified_count") or 0)} · '
                f'不合格 {int(row.get("rejected_count") or 0)} · '
                f'未覆盖编号 {int(row.get("remaining_count") or 0)}</div>',
                unsafe_allow_html=True,
            )
            if batch_id in queued_ids:
                st.caption("✅ 已在扫描队列")
            st.checkbox("勾选此批次", key=_pick_key(batch_id))
            if st.button(
                "打开批次",
                key=_key("batchgo", project_id, source_type, batch_id),
                type="primary",
                use_container_width=True,
            ):
                st.session_state["catalog_batch_id"] = batch_id
                _go("catalog_batch_detail")


def _render_queue_panel(
    service: CatalogService,
    rows: list[dict[str, Any]],
    *,
    project_id: str,
    source_type: str,
) -> None:
    queued = {str(item["batch_id"]): item for item in service.queue_list("scan")}
    queued_ids = set(queued)
    selected = [row for row in rows if st.session_state.get(_pick_key(str(row["id"])))]
    current_queued = [row for row in rows if str(row["id"]) in queued_ids]
    done_count = sum(
        1 for row in current_queued if str(queued[str(row["id"])].get("status") or "") == "done"
    )
    with st.container(border=True):
        info, add_col, remove_col = st.columns([4, 1.5, 1.5], vertical_alignment="center")
        info.markdown(
            f"**扫描队列** · 已勾选 {len(selected)} 个批次 · 当前层级已在队列 "
            f"{len(current_queued)} 个 · 已扫描 {done_count} 个"
        )
        info.caption("勾选批次后点“加入扫描队列”。队列写入数据库，切换页面不会丢失。")
        if add_col.button(
            "加入扫描队列",
            key=_key("queue_add", project_id, source_type),
            type="primary",
            use_container_width=True,
            disabled=not selected,
        ):
            for row in selected:
                service.queue_add(str(row["id"]), "scan")
                st.session_state.pop(_pick_key(str(row["id"])), None)
            st.rerun()
        removable = [row for row in selected if str(row["id"]) in queued_ids]
        if remove_col.button(
            "移出队列",
            key=_key("queue_remove", project_id, source_type),
            use_container_width=True,
            disabled=not removable,
        ):
            for row in removable:
                service.queue_remove(str(queued[str(row["id"])]["id"]))
                st.session_state.pop(_pick_key(str(row["id"])), None)
            st.rerun()

    current = [row for row in rows if str(row["id"]) in queued_ids]
    if current:
        with st.expander(f"查看当前层级队列（{len(current)}）", expanded=False):
            for row in current:
                c1, c2 = st.columns([5, 1])
                item = queued[str(row["id"])]
                status = str(item.get("status") or "")
                status_text = {
                    "queued": "待扫描",
                    "running": "扫描中",
                    "done": "已扫描",
                    "failed": "扫描失败",
                    "skipped": "已跳过",
                }.get(status, status)
                c1.write(
                    f"**{row.get('name') or '未命名批次'}** · "
                    f"{row.get('team_name') or '未识别团队'} · {row.get('submit_date') or ''}"
                )
                c1.caption(f"{status_text} · {item.get('message') or ''}")
                if c2.button("移除", key=_key("queue_del", project_id, source_type, row["id"])):
                    service.queue_remove(str(item["id"]))
                    st.session_state.pop(_pick_key(str(row["id"])), None)
                    st.rerun()


def _render_batch_level(
    service: CatalogService,
    rows: list[dict[str, Any]],
    *,
    project_id: str,
    source_type: str,
) -> None:
    queued = {str(item["batch_id"]): item for item in service.queue_list("scan")}
    queued_ids = set(queued)
    _render_queue_panel(service, rows, project_id=project_id, source_type=source_type)
    st.caption(f"当前层级 {len(rows)} 个批次；打开批次可查看编号与验收记录。")
    columns = st.columns(3, gap="medium")
    for index, row in enumerate(sorted(rows, key=lambda item: (item.get("submit_date") or "", item.get("name") or ""))):
        _render_batch_card(
            columns[index % 3],
            row,
            project_id=project_id,
            source_type=source_type,
            queued_ids=queued_ids,
        )


def _append_scan_result(item: dict[str, Any]) -> None:
    results = st.session_state.get("catalog_scan_results")
    if not isinstance(results, list):
        results = []
    results.append(item)
    st.session_state["catalog_scan_results"] = results


def _pop_pending(batch_id: str) -> None:
    pending = st.session_state.get("catalog_scan_pending")
    if not isinstance(pending, list):
        pending = []
    st.session_state["catalog_scan_pending"] = [
        str(item) for item in pending if str(item) != str(batch_id)
    ]


def _start_queue_scan(service: CatalogService) -> None:
    queue = service.queue_list("scan")
    pending: list[str] = []
    results: list[dict[str, Any]] = []
    skipped_done = 0
    for item in queue:
        batch_id = str(item["batch_id"])
        name = str(item.get("batch_name") or batch_id)
        if str(item.get("status") or "") == "done":
            skipped_done += 1
            continue
        try:
            preview = preview_scan(service.db, batch_id)
        except Exception as exc:
            message = f"读取验收记录失败：{exc}"
            service.queue_set_status(batch_id, "scan", "failed", message)
            results.append(
                {
                    "batch_id": batch_id,
                    "batch_name": name,
                    "applied": False,
                    "message": message,
                    "warnings": [],
                }
            )
            continue
        if not preview.reviewers:
            message = "没有匹配到验收员，主状态保持新提交"
            service.queue_set_status(batch_id, "scan", "skipped", message)
            results.append(
                {
                    "batch_id": batch_id,
                    "batch_name": name,
                    "applied": False,
                    "message": message,
                    "warnings": [],
                }
            )
            continue
        if preview.conflict:
            pending.append(batch_id)
            continue
        try:
            result = apply_scan(service.db, batch_id, preview.reviewers)
        except Exception as exc:
            message = f"扫描失败：{exc}"
            service.queue_set_status(batch_id, "scan", "failed", message)
            result = None
            results.append(
                {
                    "batch_id": batch_id,
                    "batch_name": name,
                    "applied": False,
                    "message": message,
                    "warnings": [],
                }
            )
        if result is not None:
            results.append(result.to_dict())
    if skipped_done and not pending:
        results.append(
            {
                "batch_id": "",
                "batch_name": "扫描队列",
                "applied": False,
                "skipped": True,
                "message": (
                    f"{skipped_done} 个批次已经扫描过，已跳过。"
                    "需要重新扫描请打开批次详情，用“重新扫描验收记录”。"
                ),
                "warnings": [],
            }
        )
    st.session_state["catalog_scan_pending"] = pending
    st.session_state["catalog_scan_results"] = results
    st.rerun()


def _render_scan_results() -> None:
    results = st.session_state.pop("catalog_scan_results", None)
    if not results:
        return
    with st.container(border=True):
        st.markdown("**扫描结果**")
        for item in results:
            name = item.get("batch_name") or item.get("batch_id") or "批次"
            if item.get("skipped"):
                st.info(f"{name}：{item.get('message') or '已跳过'}")
            elif item.get("applied"):
                st.success(f"{name}：{item.get('message') or '扫描完成'}")
            else:
                st.warning(f"{name}：{item.get('message') or '没有写入记录'}")
            warnings = item.get("warnings") or []
            if warnings:
                with st.expander(f"{name} 的提示（{len(warnings)}）", expanded=False):
                    for warning in warnings[:30]:
                        st.caption(f"⚠️ {warning}")


@st.dialog("选择验收员绑定", width="large")
def _binding_dialog(batch_id: str) -> None:
    db = open_database(app_config.get_db_path())
    try:
        service = CatalogService(db)
        preview = preview_scan(db, batch_id)
        st.markdown(f"**{preview.batch_name or batch_id}**")
        st.caption(
            f"检测到 {len(preview.reviewers)} 个验收员：{'、'.join(preview.reviewers)}。"
            "需要决定本次扫描绑定哪些验收员。"
        )
        for folder in preview.folders:
            matched = "、".join(folder.reviewers) if folder.reviewers else "未匹配"
            st.caption(f"📁 {folder.name} → {matched} · {len(folder.files)} 个记录文件")
        choice = st.radio(
            "绑定方式",
            ["全部绑定", "只绑一个", "都不绑定"],
            key=f"catalog_bind_mode_{batch_id}",
        )
        selected: list[str] = []
        if choice == "全部绑定":
            selected = list(preview.reviewers)
        elif choice == "只绑一个":
            reviewer = st.selectbox(
                "验收员",
                options=list(preview.reviewers),
                key=f"catalog_bind_reviewer_{batch_id}",
            )
            selected = [str(reviewer)]
        c1, c2 = st.columns(2)
        if c1.button("确认并扫描", type="primary", use_container_width=True):
            try:
                result = apply_scan(db, batch_id, selected)
                _append_scan_result(result.to_dict())
            except Exception as exc:
                _append_scan_result(
                    {
                        "batch_id": batch_id,
                        "batch_name": preview.batch_name,
                        "applied": False,
                        "message": f"扫描失败：{exc}",
                        "warnings": [],
                    }
                )
            _pop_pending(batch_id)
            st.rerun()
        if c2.button("稍后处理", use_container_width=True):
            _pop_pending(batch_id)
            st.rerun()
    finally:
        db.close()



def _sync(service: CatalogService, project_id: str, source_type: str, root: str) -> None:
    if not (root or "").strip():
        st.warning("这个来源还没有配置根目录，请先到设置页填写。")
        return
    with st.spinner("正在只读扫描目录结构并写入索引…"):
        result = service.sync_root(
            project_id=project_id,
            source_type=source_type,
            root=root.strip(),
            limit=500,
        )
    st.session_state["_catalog_last_sync"] = {
        "batches": result.batches,
        "created": result.created,
        "updated": result.updated,
        "groups": result.data_groups,
        "conflicts": result.conflicts,
        "warnings": len(result.warnings),
    }
    st.rerun()


def render() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="vk-hero"><h2>🏠 验收工作台</h2>'
        "<p>以批次为核心：大类 → 小类 → 三级类 → 批次。缺哪一层就跳过哪一层；"
        "打开页面只读数据库，只有点“刷新目录索引”才读取网络目录。</p></div>",
        unsafe_allow_html=True,
    )

    db = open_database(app_config.get_db_path())
    try:
        service = CatalogService(db)
        projects = _projects(db)
        if not projects:
            st.info("还没有业务目录。先到设置页添加项目根目录和待验/终验目录。")
            if st.button("打开设置", type="primary", use_container_width=True):
                _go("settings")
            return

        options = {str(item["id"]): item for item in projects}
        labels = {
            str(item["id"]): f"{item['project_name']}{'（已停用）' if not item['enabled'] else ''}"
            for item in projects
        }
        select_col, source_col = st.columns([1.2, 2.2], vertical_alignment="center")
        project_id = select_col.selectbox(
            "项目",
            options=list(options),
            format_func=lambda value: labels[value],
            key="catalog_workbench_project",
        )
        project = options[project_id]
        source_labels = [label for _, label in SOURCE_CHOICES]
        chosen_label = source_col.radio(
            "来源",
            source_labels,
            horizontal=True,
            key="catalog_workbench_source",
            label_visibility="collapsed",
        )
        source_type = next(key for key, label in SOURCE_CHOICES if label == chosen_label)
        root = project.get("pending_path") if source_type == SOURCE_PENDING else project.get("final_path")

        last_sync = st.session_state.pop("_catalog_last_sync", None)
        if last_sync:
            st.success(
                f"目录索引已刷新：{last_sync['batches']} 个批次，"
                f"新增 {last_sync['created']}，更新 {last_sync['updated']}，"
                f"编号数据 {last_sync['groups']} 条，验收员冲突 {last_sync['conflicts']} 个。"
            )
            if last_sync.get("warnings"):
                st.caption(f"有 {last_sync['warnings']} 条识别提示，已在目录索引页展示。")

        queue_items = service.queue_list("scan")
        queue_count = len(queue_items)
        toolbar = st.columns([1.2, 1.2, 1, 1, 1.7, 2.2], vertical_alignment="center")
        if toolbar[0].button(
            "刷新目录索引",
            key="catalog_workbench_refresh",
            use_container_width=True,
            disabled=not bool(project.get("enabled")),
        ):
            _sync(service, project_id, source_type, str(root or ""))
        if toolbar[1].button("目录索引页", key="catalog_workbench_open_sync", use_container_width=True):
            _go("catalog_sync")
        if toolbar[2].button("格式检查", key="catalog_workbench_open_format", use_container_width=True):
            _go("catalog_format")
        if toolbar[3].button("批次翻译", key="catalog_workbench_open_translation", use_container_width=True):
            _go("catalog_translation")
        if toolbar[4].button(
            f"开始扫描队列（{queue_count}）",
            key="catalog_workbench_scan_queue",
            type="primary",
            use_container_width=True,
            disabled=not queue_count,
        ):
            with st.spinner("正在扫描验收记录，只读 CSV/XLSX…"):
                _start_queue_scan(service)
        toolbar[5].caption(
            f"只读根目录：`{root or '未配置'}` · 刷新只写管理数据库，不修改源文件。"
        )
        _render_scan_results()
        pending = st.session_state.get("catalog_scan_pending")
        if isinstance(pending, list) and pending:
            _binding_dialog(str(pending[0]))
        if not project.get("enabled"):
            st.warning("这个项目已停用。历史批次仍保留，可在设置页重新启用。")
            return

        rows = service.batches(project_id=project_id, source_type=source_type)
        if not rows:
            st.info("这个来源还没有索引。点“刷新目录索引”读取目录结构。")
            st.caption("刷新只读目录名，不读取图片/TXT/CSV 内容。")
            return

        _render_overview(rows)
        nav_key = _nav_key(project_id, source_type)
        path = _get_path(project_id, source_type)
        _render_breadcrumb(project_id, source_type, path)
        filtered = _filter_rows(rows, path)
        if not filtered:
            st.warning("当前层级下暂时没有批次。目录可能已变化，可返回上一层或刷新索引。")
            if path and st.button("返回全部", key="catalog_workbench_reset"):
                _set_path(project_id, source_type, [])
                st.rerun()
            return

        after = int(path[-1]["level"]) if path else 0
        next_level = _next_level(filtered, after)
        if next_level <= 3:
            _render_category_grid(
                filtered,
                project_id=project_id,
                source_type=source_type,
                level=next_level,
                path=path,
            )
        else:
            _render_batch_level(
                service,
                filtered,
                project_id=project_id,
                source_type=source_type,
            )
    finally:
        db.close()
