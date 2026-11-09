"""管理中心首页：批次总览、工作台、团队/类别聚合与批次详情（v1.25.5）。

全部查询只读管理数据库；不修改源图片/TXT/CSV。
"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd
import streamlit as st

from modules.batch_management.dashboard import (
    BatchDashboard,
    acceptance_label,
    format_label,
    status_label,
    translation_label,
)
from modules.batch_management.service import BatchService

_ALL = "全部"


def _fmt_time(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ")[:19]


def _go(page: str) -> None:
    from ui.router import go

    go(page)


def _metric_row(items: list[tuple[str, Any, str | None]]) -> None:
    columns = st.columns(len(items))
    for column, (label, value, help_text) in zip(columns, items):
        column.metric(label, value, help=help_text)


def _open_batch(batch_id: str) -> None:
    st.session_state["management_batch_id"] = batch_id
    _go("batch_detail")


def _batch_label(row: dict[str, Any]) -> str:
    display = row.get("display_id") or row.get("id") or ""
    name = row.get("name") or ""
    team = row.get("team") or "未指定团队"
    return f"{display} | {name} | {team}"


def _batch_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "批次": row.get("display_id") or row.get("id") or "",
                "批次名": row.get("name") or "",
                "团队": row.get("team") or "",
                "供应商": row.get("supplier") or "",
                "日期": row.get("submit_date") or "",
                "一级": row.get("category_l1") or "",
                "二级": row.get("category_l2") or "",
                "任务数": int(row.get("task_count") or 0),
                "合格": int(row.get("qualified_count") or 0),
                "不合格": int(row.get("rejected_count") or 0),
                "剩余": int(row.get("remaining_count") or 0),
                "导入记录": int(row.get("imported_records") or 0),
                "格式": row.get("format_label") or "",
                "翻译": row.get("translation_label") or "",
                "验收": row.get("acceptance_label") or "",
                "状态": row.get("status_label") or "",
                "最近活动": _fmt_time(row.get("last_activity")),
            }
            for row in rows
        ]
    )


def _filters(rows: list[dict[str, Any]]) -> dict[str, str]:
    options = BatchDashboard.filter_options(rows)
    col1, col2, col3, col4, col5 = st.columns(5)
    team = col1.selectbox("团队", [_ALL, *options["teams"]], key="mgmt_filter_team")
    supplier = col2.selectbox("供应商", [_ALL, *options["suppliers"]], key="mgmt_filter_supplier")
    date = col3.selectbox("日期", [_ALL, *options["dates"]], key="mgmt_filter_date")
    l1 = col4.selectbox("一级类别", [_ALL, *options["categories_l1"]], key="mgmt_filter_l1")
    l2 = col5.selectbox("二级类别", [_ALL, *options["categories_l2"]], key="mgmt_filter_l2")
    col6, col7 = st.columns([1, 2])
    status = col6.selectbox("批次状态", [_ALL, *options["statuses"]], key="mgmt_filter_status")
    keyword = col7.text_input("搜索批次名 / 批次号 / 路径", key="mgmt_filter_keyword")
    return {
        "team": "" if team == _ALL else team,
        "supplier": "" if supplier == _ALL else supplier,
        "date": "" if date == _ALL else date,
        "category_l1": "" if l1 == _ALL else l1,
        "category_l2": "" if l2 == _ALL else l2,
        "status": "" if status == _ALL else status,
        "keyword": keyword,
    }


# ── 批次总览 ─────────────────────────────────────────────


def render_batch_overview(service: BatchService) -> None:
    st.title("📦 批次总览")
    st.caption(
        "以批次为单位查看团队、类别、任务数、验收统计、导入记录、格式与翻译状态；"
        "需要时才下钻到数据组或单条记录。"
    )
    dashboard = BatchDashboard(service.db)
    try:
        all_rows = dashboard.batches()
    except Exception as exc:
        st.error(f"读取批次失败：{exc}")
        return
    if not all_rows:
        st.info("还没有已确认批次。请先在“候选确认”中确认扫描结果。")
        return

    filters = _filters(all_rows)
    rows = BatchDashboard.filter_rows(all_rows, **filters)
    _metric_row(
        [
            ("批次数", len(rows), "当前筛选结果"),
            ("任务数", sum(int(row.get("task_count") or 0) for row in rows), "数据组数量"),
            ("导入记录", sum(int(row.get("imported_records") or 0) for row in rows), "其他验收员记录"),
            (
                "待处理批次",
                sum(1 for row in rows if row.get("acceptance_status") in {"not_started", "in_progress"}),
                "验收未完成",
            ),
            (
                "异常批次",
                sum(
                    1
                    for row in rows
                    if int(row.get("format_failed") or 0) > 0
                    or int(row.get("import_errors") or 0) > 0
                ),
                "格式失败或导入错误",
            ),
        ]
    )
    st.caption(
        "说明：合格/不合格优先取批次验收统计；尚未同步时显示 0，不虚构其他验收员的实时进度。"
    )
    if not rows:
        st.warning("当前筛选条件下没有批次。")
        return
    frame = _batch_frame(rows)
    event = st.dataframe(
        frame,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="mgmt_batch_table",
    )
    selected = getattr(getattr(event, "selection", None), "rows", []) if event is not None else []
    if selected:
        index = int(selected[0])
        if 0 <= index < len(rows):
            row = rows[index]
            if st.button(
                f"打开批次：{row.get('display_id') or row.get('name')}",
                type="primary",
                use_container_width=True,
                key="mgmt_open_selected_batch",
            ):
                _open_batch(str(row["id"]))
    else:
        st.caption("在上方表格中选择一行，或使用下面的下拉框打开批次。")
        choice = st.selectbox(
            "选择批次",
            options=[row["id"] for row in rows],
            format_func=lambda value: _batch_label(next(row for row in rows if row["id"] == value)),
            key="mgmt_batch_select",
        )
        if st.button("打开批次详情", use_container_width=True, key="mgmt_open_batch_select"):
            _open_batch(str(choice))


# ── 工作台 ───────────────────────────────────────────────


def render_workbench(service: BatchService) -> None:
    st.title("🏠 工作台")
    st.caption("只看真实可计算的批次、检查与导入状态；不生成虚假进度。")
    dashboard = BatchDashboard(service.db)
    data = dashboard.workbench()
    totals = data["totals"]
    _metric_row(
        [
            ("批次数", totals["batches"], "全部已确认批次"),
            ("任务数", totals["task_count"], "全部数据组"),
            ("待处理批次", totals["pending_batches"], "验收未完成或仍有剩余"),
            ("异常批次", totals["anomaly_batches"], "格式失败或导入错误"),
            ("导入记录", totals["imported_records"], "其他验收员记录"),
        ]
    )
    st.caption(f"今日：{data['today']}")

    tab_today, tab_pending, tab_anomaly, tab_recent = st.tabs(
        ["今日批次", "待处理", "异常提醒", "最近动态"]
    )
    with tab_today:
        if data["today_batches"]:
            st.dataframe(
                _batch_frame(data["today_batches"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("今天还没有新的批次或活动。")
    with tab_pending:
        if data["pending"]:
            st.dataframe(
                _batch_frame(data["pending"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("没有待处理批次。")
    with tab_anomaly:
        if data["anomalies"]:
            st.dataframe(
                _batch_frame(data["anomalies"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("没有发现格式失败或导入错误。")
    with tab_recent:
        col_import, col_format = st.columns(2)
        with col_import:
            st.subheader("最近导入")
            if data["recent_imports"]:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "时间": _fmt_time(item.get("imported_at") or item.get("created_at")),
                                "文件": os.path.basename(item.get("source_path") or ""),
                                "状态": status_label(item.get("status")),
                                "新增": item.get("rows_new", 0),
                                "跳过": item.get("rows_skipped", 0),
                                "错误": item.get("rows_error", 0),
                            }
                            for item in data["recent_imports"]
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("还没有导入记录。")
        with col_format:
            st.subheader("最近格式检查")
            if data["recent_formats"]:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "时间": _fmt_time(item.get("finished_at") or item.get("started_at")),
                                "任务": item.get("scanned_tasks", 0),
                                "通过": item.get("passed_tasks", 0),
                                "失败": item.get("failed_tasks", 0),
                                "警告": item.get("warning_tasks", 0),
                                "状态": status_label(item.get("status")),
                            }
                            for item in data["recent_formats"]
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("还没有格式检查记录。")


# ── 团队与类别 ───────────────────────────────────────────


def render_team_category(service: BatchService) -> None:
    st.title("👥 团队与类别")
    st.caption("按团队、一级/二级类别汇总批次与任务；不展开到单条记录。")
    dashboard = BatchDashboard(service.db)
    all_rows = dashboard.batches()
    if not all_rows:
        st.info("还没有已确认批次。")
        return
    options = BatchDashboard.filter_options(all_rows)
    col1, col2, col3 = st.columns([1, 1, 2])
    team = col1.selectbox("团队", [_ALL, *options["teams"]], key="mgmt_summary_team")
    date = col2.selectbox("日期", [_ALL, *options["dates"]], key="mgmt_summary_date")
    keyword = col3.text_input("搜索批次名 / 路径", key="mgmt_summary_keyword")
    rows = BatchDashboard.filter_rows(
        all_rows,
        team="" if team == _ALL else team,
        date="" if date == _ALL else date,
        keyword=keyword,
    )
    if not rows:
        st.warning("当前筛选条件下没有批次。")
        return

    tab_team, tab_category = st.tabs(["按团队", "按类别"])
    with tab_team:
        summary = dashboard.team_summary(rows)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "团队": item["team"],
                        "批次数": item["batches"],
                        "任务数": item["task_count"],
                        "合格": item["qualified_count"],
                        "不合格": item["rejected_count"],
                        "剩余": item["remaining_count"],
                        "导入记录": item["imported_records"],
                        "格式失败": item["format_failed"],
                        "翻译任务": item["translation_jobs"],
                        "品类": "、".join(item["categories"]),
                        "最近活动": _fmt_time(item["last_activity"]),
                    }
                    for item in summary
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        if summary:
            chart = pd.DataFrame(
                {"任务数": [item["task_count"] for item in summary]},
                index=[item["team"] for item in summary],
            )
            st.bar_chart(chart)
    with tab_category:
        summary = dashboard.category_summary(rows)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "一级": item["category_l1"],
                        "二级": item["category_l2"],
                        "批次数": item["batches"],
                        "团队": "、".join(item["teams"]),
                        "任务数": item["task_count"],
                        "合格": item["qualified_count"],
                        "不合格": item["rejected_count"],
                        "剩余": item["remaining_count"],
                        "导入记录": item["imported_records"],
                        "格式失败": item["format_failed"],
                    }
                    for item in summary
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        if summary:
            chart = pd.DataFrame(
                {"任务数": [item["task_count"] for item in summary]},
                index=[f"{item['category_l1']} / {item['category_l2']}" for item in summary],
            )
            st.bar_chart(chart)


def _render_batch_actions(service: BatchService, batch: dict[str, Any]) -> None:
    status = batch.get("status") or ""
    with st.expander("⚙️ 批次状态操作", expanded=False):
        st.caption("只更新管理数据库中的批次状态与状态日志，不移动、不删除、不修改任何任务或源文件。")
        col_operator, col_reason = st.columns([1, 2])
        operator = col_operator.text_input(
            "操作员",
            value=st.session_state.get("operator_name", ""),
            key="mgmt_status_operator",
        )
        reason = col_reason.text_input("原因（可选）", key="mgmt_status_reason")
        confirm = st.checkbox(
            "我确认执行该状态变更",
            value=False,
            key="mgmt_status_confirm",
        )
        actions: list[tuple[str, str]] = []
        if status == "paused":
            actions.append(("confirmed", "▶️ 重开批次"))
        elif status == "archived":
            actions.append(("confirmed", "♻️ 恢复为已确认"))
        else:
            actions.append(("paused", "⏸️ 暂停批次"))
            actions.append(("archived", "📦 归档批次"))
        if status == "confirmed":
            actions.append(("completed", "✅ 标记完成"))
        columns = st.columns(max(1, len(actions)))
        for column, (target, label) in zip(columns, actions):
            if column.button(
                label,
                use_container_width=True,
                disabled=not confirm or not operator.strip(),
                key=f"mgmt_status_action_{target}",
            ):
                try:
                    service.update_batch_status(
                        str(batch["id"]),
                        target,
                        operator=operator.strip(),
                        reason=reason.strip(),
                    )
                    st.success(f"批次状态已更新为：{status_label(target)}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"状态更新失败：{exc}")


# ── 批次详情 ─────────────────────────────────────────────


def render_batch_detail(service: BatchService) -> None:
    dashboard = BatchDashboard(service.db)
    all_rows = dashboard.batches()
    if not all_rows:
        st.info("还没有已确认批次。")
        return
    batch_id = str(st.session_state.get("management_batch_id") or "")
    if not batch_id or not any(row["id"] == batch_id for row in all_rows):
        choice = st.selectbox(
            "选择批次",
            options=[row["id"] for row in all_rows],
            format_func=lambda value: _batch_label(next(row for row in all_rows if row["id"] == value)),
            key="mgmt_detail_select",
        )
        if st.button("打开批次详情", type="primary", key="mgmt_detail_open"):
            _open_batch(str(choice))
        return

    detail = dashboard.batch_detail(batch_id)
    if detail is None:
        st.warning("批次不存在或已被删除。")
        return
    batch = detail["batch"]
    if st.button("← 返回批次总览", key="mgmt_detail_back"):
        _go("batch_overview")
    st.title(f"📦 {batch.get('name') or batch.get('id')}")
    st.caption(
        f"{batch.get('display_id') or ''} · 团队 {batch.get('team') or '未指定'} · "
        f"一级 {batch.get('category_l1') or '未分类'} / 二级 {batch.get('category_l2') or '未分类'} · "
        f"日期 {batch.get('submit_date') or '未填写'}"
    )
    _metric_row(
        [
            ("任务数", batch.get("task_count", 0), "数据组数量"),
            ("合格", batch.get("qualified_count", 0), "批次验收统计"),
            ("不合格", batch.get("rejected_count", 0), "批次验收统计"),
            ("剩余", batch.get("remaining_count", 0), "批次验收统计"),
            ("导入记录", batch.get("imported_records", 0), "其他验收员记录"),
        ]
    )
    st.caption(
        f"状态：{status_label(batch.get('status'))}；验收：{acceptance_label(batch.get('acceptance_status'))}；"
        f"格式：{format_label(batch.get('format_status'))}；翻译：{translation_label(batch.get('translation_status'))}"
    )
    st.caption(
        "合格/不合格/剩余来自批次验收统计；格式与翻译状态来自对应检查任务；"
        "所有数字均为数据库真实记录。"
    )
    _render_batch_actions(service, batch)

    tab_overview, tab_groups, tab_imports, tab_format, tab_translation = st.tabs(
        ["概览", "数据组", "导入记录", "格式检查", "翻译预审"]
    )
    with tab_overview:
        st.markdown("**来源与状态**")
        st.write(f"数据根目录：`{batch.get('data_root') or '未填写'}`")
        st.write(f"批次目录：`{batch.get('batch_path') or '未填写'}`")
        st.write(f"供应商：{batch.get('supplier') or '未填写'}；项目：{batch.get('project') or '未填写'}")
        st.write(f"最近活动：{_fmt_time(batch.get('last_activity')) or '无'}")
        if batch.get("identity"):
            with st.expander("提交身份信息（原始）", expanded=False):
                st.json(batch["identity"])
        if detail["sub_batches"]:
            st.markdown("**子批次**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "子批次": item.get("name") or "",
                            "数据量": item.get("data_count", 0),
                            "状态": status_label(item.get("status")),
                            "备注": item.get("note") or "",
                            "创建时间": _fmt_time(item.get("created_at")),
                        }
                        for item in detail["sub_batches"]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        if detail["status_logs"]:
            st.markdown("**状态记录**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "时间": _fmt_time(item.get("created_at")),
                            "从": status_label(item.get("from_status")),
                            "到": status_label(item.get("to_status")),
                            "操作员": item.get("operator") or "",
                            "原因": item.get("reason") or "",
                        }
                        for item in detail["status_logs"]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
    with tab_groups:
        if detail["groups"]:
            st.caption(f"共 {len(detail['groups'])} 个数据组；只显示索引，不加载图片。")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "任务ID": item.get("task_id") or "",
                            "验收员": item.get("user_name") or "",
                            "图片": item.get("image_count", 0),
                            "TXT": item.get("txt_count", 0),
                            "文件": item.get("file_count", 0),
                            "状态": status_label(item.get("status")),
                            "路径": item.get("normalized_path") or "",
                        }
                        for item in detail["groups"]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("该批次没有数据组。")
    with tab_imports:
        if detail["imports"]:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "时间": _fmt_time(item.get("imported_at") or item.get("created_at")),
                            "文件": os.path.basename(item.get("source_path") or ""),
                            "状态": status_label(item.get("status")),
                            "新增": item.get("rows_new", 0),
                            "跳过": item.get("rows_skipped", 0),
                            "错误": item.get("rows_error", 0),
                            "操作员": item.get("imported_by") or "",
                            "说明": item.get("note") or "",
                        }
                        for item in detail["imports"]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("该批次还没有导入记录。")
    with tab_format:
        if detail["format_runs"]:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "时间": _fmt_time(item.get("finished_at") or item.get("started_at")),
                            "状态": status_label(item.get("status")),
                            "任务": item.get("scanned_tasks", 0),
                            "通过": item.get("passed_tasks", 0),
                            "失败": item.get("failed_tasks", 0),
                            "警告": item.get("warning_tasks", 0),
                            "只读校验": item.get("integrity_status") or "",
                        }
                        for item in detail["format_runs"]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("该批次还没有格式检查记录。")
    with tab_translation:
        if detail["translation_jobs"]:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "时间": _fmt_time(item.get("finished_at") or item.get("started_at") or item.get("created_at")),
                            "状态": status_label(item.get("status")),
                            "提供方": item.get("provider") or "",
                            "模型": item.get("model") or "",
                            "操作员": item.get("operator") or "",
                            "说明": item.get("note") or "",
                        }
                        for item in detail["translation_jobs"]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("该批次还没有翻译预审记录。")
