"""数据统计与报表页面（v1.25.7）。

页面只读管理数据库；统计口径全部可追溯到源表。唯一的写入是：
1. 用户点击“重建日报缓存”时写入 `stats_daily`（可按源表重建）；
2. 用户点击“生成 Excel”时写入管理数据目录 `reports/stats`。
不访问、不修改任何源图片/TXT/CSV。
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from modules.stats.models import (
    BATCH_STATS_COLUMNS,
    DAILY_STATS_COLUMNS,
    ERROR_STATS_COLUMNS,
    OPERATOR_STATS_COLUMNS,
    TAG_STATS_COLUMNS,
    TRACE_ISSUE_COLUMNS,
    TRACE_RECORD_COLUMNS,
    WORKLOAD_LABELS,
    WORKLOAD_RECORDS,
    StatsFilters,
)
from modules.stats.service import StatsService

_PRESETS = ("全部", "今天", "最近 7 天", "最近 30 天", "本月", "自定义")


def _today() -> str:
    return _dt.date.today().isoformat()


def _valid_date(text: str) -> bool:
    if not text:
        return True
    try:
        _dt.date.fromisoformat(text[:10])
        return True
    except ValueError:
        return False


def _date_range(preset: str, custom_from: str, custom_to: str) -> tuple[str, str]:
    today = _dt.date.today()
    if preset == "今天":
        return today.isoformat(), today.isoformat()
    if preset == "最近 7 天":
        return (today - _dt.timedelta(days=6)).isoformat(), today.isoformat()
    if preset == "最近 30 天":
        return (today - _dt.timedelta(days=29)).isoformat(), today.isoformat()
    if preset == "本月":
        return today.replace(day=1).isoformat(), today.isoformat()
    if preset == "自定义":
        return custom_from.strip(), custom_to.strip()
    return "", ""


def _fmt_time(value: str | None) -> str:
    if not value:
        return ""
    return str(value).replace("T", " ")[:19]


def _batch_options(service: StatsService) -> dict[str, str]:
    options: dict[str, str] = {}
    for item in service.list_batches(500):
        label = (
            f"{item.get('display_id') or item.get('id')} | "
            f"{item.get('name') or '未命名批次'} | "
            f"{item.get('submit_date') or ''}"
        ).strip(" |")
        options[str(item["id"])] = label
    return options


def _filters_ui(service: StatsService) -> StatsFilters | None:
    """常驻筛选条。返回 None 表示日期输入不合法。"""
    batches = _batch_options(service)
    teams = ["（全部团队）", *service.list_teams()]
    operators = ["（全部人员）", *service.list_operators()]

    with st.container(border=True):
        row1 = st.columns([2, 2, 2])
        preset = row1[0].selectbox("日期范围", _PRESETS, index=0, key="stats_preset")
        custom_from = custom_to = ""
        if preset == "自定义":
            custom_from = row1[1].text_input(
                "开始日期", placeholder="YYYY-MM-DD", key="stats_from"
            )
            custom_to = row1[2].text_input(
                "结束日期", placeholder="YYYY-MM-DD", key="stats_to"
            )
        else:
            date_from, date_to = _date_range(preset, "", "")
            row1[1].caption(f"开始日期：{date_from or '不限'}")
            row1[2].caption(f"结束日期：{date_to or '不限'}")

        row2 = st.columns([2, 1, 1])
        batch_ids = list(batches.keys())
        batch_choice = row2[0].selectbox(
            "批次",
            ["（全部批次）", *batch_ids],
            format_func=lambda value: (
                "（全部批次）" if value == "（全部批次）" else batches.get(value, value)
            ),
            key="stats_batch",
        )
        team = row2[1].selectbox("团队", teams, key="stats_team")
        operator = row2[2].selectbox("人员", operators, key="stats_operator")

    if preset == "自定义":
        date_from, date_to = custom_from, custom_to
        if not _valid_date(date_from) or not _valid_date(date_to):
            st.error("日期格式应为 YYYY-MM-DD，或留空表示不限。")
            return None
        if date_from and date_to and date_from > date_to:
            st.error("开始日期不能晚于结束日期。")
            return None

    return StatsFilters(
        date_from=date_from,
        date_to=date_to,
        batch_id="" if batch_choice == "（全部批次）" else batch_choice,
        team="" if team == "（全部团队）" else team,
        operator="" if operator == "（全部人员）" else operator,
    )


def _overview_tab(service: StatsService, filters: StatsFilters) -> None:
    overview, rows = service.batch_stats_and_overview(filters)
    st.subheader("总体概览")
    st.caption(
        "批次按提交日期筛选；任务/图片/格式/翻译/批次快照为批次口径，"
        "导入验收为该批次全部已导入记录。未同步时显示 0，不做推算。"
    )
    row1 = st.columns(6)
    row1[0].metric("批次", overview["batches"])
    row1[1].metric("任务数", overview["task_count"])
    row1[2].metric("图片数", overview["image_count"])
    row1[3].metric("格式失败", overview["format_failed"])
    row1[4].metric("翻译完成", overview["translated"])
    row1[5].metric("翻译失败", overview["translation_failed"])
    row2 = st.columns(6)
    row2[0].metric("格式已检查", overview["format_checked"])
    row2[1].metric("导入验收记录", overview["imported_records"])
    row2[2].metric("导入合格", overview["imported_qualified"])
    row2[3].metric("导入不合格", overview["imported_rejected"])
    row2[4].metric("批次剩余", overview["batch_remaining"])
    row2[5].metric("归档已校验", overview["archived"])

    st.divider()
    st.subheader("批次统计")
    if not rows:
        st.info("当前筛选下没有批次数据。")
        return
    frame = pd.DataFrame(
        [
            [
                row["display_id"], row["name"], row["team"], row["supplier"],
                row["submit_date"], row["category_l1"], row["category_l2"],
                row["status"], row["task_count"], row["image_count"],
                row["format_checked"], row["format_failed"], row["translated"],
                row["translation_failed"], row["batch_qualified"], row["batch_rejected"],
                row["batch_remaining"], row["imported_records"], row["imported_qualified"],
                row["imported_rejected"], row["archived"], _fmt_time(row["last_activity"]),
            ]
            for row in rows
        ],
        columns=list(BATCH_STATS_COLUMNS),
    )
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.caption(f"共 {len(rows)} 个批次。")


def _operator_tab(service: StatsService, filters: StatsFilters) -> None:
    st.subheader("人员统计")
    mode = st.selectbox(
        "工作量口径",
        list(WORKLOAD_LABELS.keys()),
        format_func=lambda value: WORKLOAD_LABELS[value],
        index=list(WORKLOAD_LABELS.keys()).index(WORKLOAD_RECORDS),
        key="stats_workload_mode",
    )
    rows = service.operator_stats(filters, workload_mode=mode)
    if not rows:
        st.info("当前筛选下没有人员验收记录。")
        return

    summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = summary.setdefault(
            row["operator"],
            {"operator": row["operator"], "records": 0, "qualified": 0,
             "rejected": 0, "other": 0, "groups": 0, "images": 0, "workload": 0.0},
        )
        for key in ("records", "qualified", "rejected", "other", "groups", "images"):
            item[key] += int(row.get(key) or 0)
        item["workload"] += float(row.get("workload") or 0)

    st.markdown("**按人员汇总**")
    summary_frame = pd.DataFrame(
        [
            [
                item["operator"], item["records"], item["qualified"], item["rejected"],
                item["other"],
                f"{(item['qualified'] / (item['qualified'] + item['rejected'])):.2%}"
                if (item["qualified"] + item["rejected"]) else "—",
                item["groups"], item["images"], round(item["workload"], 2),
            ]
            for item in sorted(summary.values(), key=lambda row: -row["records"])
        ],
        columns=["人员", "验收记录", "合格", "不合格", "其他", "合格率", "涉及任务", "图片数", "工作量"],
    )
    st.dataframe(summary_frame, use_container_width=True, hide_index=True)
    st.caption(f"工作量口径：{WORKLOAD_LABELS[mode]}")

    with st.expander("按人员 × 日期 × 批次明细", expanded=False):
        detail_frame = pd.DataFrame(
            [
                [
                    row["operator"], row["date"], row.get("batch_name") or row["batch_id"],
                    row["records"], row["qualified"], row["rejected"], row["other"],
                    f"{row['qualified_rate']:.2%}", row["groups"], row["images"], row["workload"],
                ]
                for row in rows
            ],
            columns=list(OPERATOR_STATS_COLUMNS),
        )
        st.dataframe(detail_frame, use_container_width=True, hide_index=True)


def _error_tag_tab(service: StatsService, filters: StatsFilters) -> None:
    errors = service.error_type_stats(filters)
    tags = service.tag_stats(filters)
    st.subheader("错误类型（格式检查）")
    if errors:
        frame = pd.DataFrame(
            [
                [
                    row["code"], row["message"], row["severity_label"], row["count"],
                    row["tasks"], row["batches"], _fmt_time(row["first_seen"]),
                    _fmt_time(row["last_seen"]),
                ]
                for row in errors
            ],
            columns=list(ERROR_STATS_COLUMNS),
        )
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.caption(f"共 {len(errors)} 种错误码。说明取自错误码目录，未知错误码保留原文。")
    else:
        st.info("当前筛选下没有格式检查错误。")

    st.divider()
    st.subheader("标签统计（验收记录）")
    if tags:
        frame = pd.DataFrame(
            [
                [
                    row["tag"], row["count"], row["batches"], row["operators"],
                    row["qualified"], row["rejected"],
                ]
                for row in tags
            ],
            columns=list(TAG_STATS_COLUMNS),
        )
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.caption("标签保留历史原文，未定义标签也会统计。")
    else:
        st.info("当前筛选下没有标签数据。")


def _daily_tab(service: StatsService, filters: StatsFilters) -> None:
    st.subheader("日报")
    day = st.text_input(
        "日报日期", value=st.session_state.get("stats_daily_day", _today()),
        placeholder="YYYY-MM-DD", key="stats_daily_day",
    )
    if not _valid_date(day):
        st.error("日报日期格式应为 YYYY-MM-DD。")
        return
    report = service.daily_report(day or _today(), filters)
    row1 = st.columns(6)
    row1[0].metric("新增批次", report["new_batches"])
    row1[1].metric("新增数据组", report["new_groups"])
    row1[2].metric("格式检查", report["format_checked"])
    row1[3].metric("格式失败", report["format_failed"])
    row1[4].metric("翻译完成", report["translated"])
    row1[5].metric("翻译失败", report["translation_failed"])
    row2 = st.columns(6)
    row2[0].metric("验收记录", report["accepted"])
    row2[1].metric("合格", report["qualified"])
    row2[2].metric("不合格", report["rejected"])
    row2[3].metric("归档数据组", report["archived"])
    row2[4].metric("剩余", report["pending"])
    row2[5].metric("格式警告", report["format_warnings"])

    st.divider()
    st.markdown("**日期区间日报**")
    st.caption("区间默认使用上方筛选；若未设置日期，则显示最近 14 天。日报读取实时源表，缓存仅用于导出留档。")
    end = filters.date_to or _today()
    start = filters.date_from or (
        _dt.date.fromisoformat(end) - _dt.timedelta(days=13)
    ).isoformat()
    try:
        series = service.daily_series(start, end, filters)
    except ValueError as exc:
        st.error(f"日报区间无效：{exc}")
        return
    if series:
        frame = pd.DataFrame(
            [
                [
                    row["date"], row["new_batches"], row["new_groups"], row["format_checked"],
                    row["format_failed"], row["format_warnings"], row["translated"],
                    row["translation_failed"], row["accepted"], row["qualified"],
                    row["rejected"], row["archived"], row["pending"],
                ]
                for row in series
            ],
            columns=list(DAILY_STATS_COLUMNS),
        )
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.caption(f"共 {len(series)} 天。")

    st.divider()
    columns = st.columns([2, 1, 2])
    cache_start = columns[0].text_input(
        "缓存开始日期", value=start, key="stats_cache_start", placeholder="YYYY-MM-DD"
    )
    cache_end = columns[1].text_input(
        "缓存结束日期", value=end, key="stats_cache_end", placeholder="YYYY-MM-DD"
    )
    if columns[2].button("重建日报缓存", key="stats_rebuild_cache"):
        if not _valid_date(cache_start) or not _valid_date(cache_end):
            st.error("缓存日期格式应为 YYYY-MM-DD。")
        elif cache_start > cache_end:
            st.error("缓存开始日期不能晚于结束日期。")
        else:
            try:
                with st.spinner("正在按源表重建 stats_daily…"):
                    count = service.rebuild_daily_cache(cache_start, cache_end, filters)
            except ValueError as exc:
                st.error(f"缓存范围无效：{exc}")
            except Exception as exc:  # pragma: no cover - 数据库/IO 异常
                st.error(f"重建缓存失败：{exc}")
            else:
                st.success(f"已重建 {count} 天的日报缓存。缓存可随时按源表重建。")
    try:
        cached = service.cache_rows(start, end)
    except Exception:
        cached = []
    if cached:
        with st.expander(f"当前缓存（{len(cached)} 条）", expanded=False):
            st.dataframe(
                pd.DataFrame(
                    [
                        [
                            row["date"], row["new_batches"], row["new_groups"],
                            row["format_checked"], row["format_failed"], row["format_warnings"],
                            row["translated"], row["translation_failed"], row["accepted"],
                            row["qualified"], row["rejected"], row["archived"],
                            row["pending"], _fmt_time(row["built_at"]), row["source"],
                        ]
                        for row in cached
                    ],
                    columns=[*DAILY_STATS_COLUMNS, "重建时间", "来源"],
                ),
                use_container_width=True,
                hide_index=True,
            )


def _trace_tab(service: StatsService, filters: StatsFilters) -> None:
    st.subheader("可追溯明细")
    st.caption("每条验收记录都能回到来源文件与行号；格式错误能回到检查批次与任务。")
    record_total = service.count_imported_records(filters)
    issue_total = service.count_format_issues(filters)
    tabs = st.tabs([f"验收明细（{record_total}）", f"格式错误明细（{issue_total}）"])

    with tabs[0]:
        page_size = st.selectbox("每页", [50, 100, 200, 500], index=1, key="stats_record_page_size")
        pages = max(1, (record_total + page_size - 1) // page_size)
        if int(st.session_state.get("stats_record_page", 1)) > pages:
            st.session_state["stats_record_page"] = pages
        page = st.number_input(
            "页码", min_value=1, max_value=pages, step=1, key="stats_record_page"
        )
        rows = service.trace_imported_records(
            filters, limit=page_size, offset=(int(page) - 1) * page_size
        )
        if rows:
            frame = pd.DataFrame(
                [
                    [
                        row["record_id"], row["date"], row["operator"], row["result"],
                        row["task_id"], row["batch_name"] or row["batch_id"],
                        row["data_group_id"], row["source_path"], row["source_row"],
                        row["tags_raw"], row["note"],
                    ]
                    for row in rows
                ],
                columns=list(TRACE_RECORD_COLUMNS),
            )
            st.dataframe(frame, use_container_width=True, hide_index=True)
            st.caption(f"第 {page}/{pages} 页，共 {record_total} 条。")
        else:
            st.info("当前筛选下没有验收记录。")

    with tabs[1]:
        page_size = st.selectbox("每页", [50, 100, 200, 500], index=1, key="stats_issue_page_size")
        pages = max(1, (issue_total + page_size - 1) // page_size)
        if int(st.session_state.get("stats_issue_page", 1)) > pages:
            st.session_state["stats_issue_page"] = pages
        page = st.number_input(
            "页码", min_value=1, max_value=pages, step=1, key="stats_issue_page"
        )
        rows = service.trace_format_issues(
            filters, limit=page_size, offset=(int(page) - 1) * page_size
        )
        if rows:
            frame = pd.DataFrame(
                [
                    [
                        row["code"], row["severity"], row["batch_name"] or row["batch_id"],
                        row["task_id"], row["task_path"], row["file_path"], row["expected"],
                        row["actual"], row["message"],
                        _fmt_time(row["finished_at"] or row["started_at"]),
                    ]
                    for row in rows
                ],
                columns=list(TRACE_ISSUE_COLUMNS),
            )
            st.dataframe(frame, use_container_width=True, hide_index=True)
            st.caption(f"第 {page}/{pages} 页，共 {issue_total} 条。")
        else:
            st.info("当前筛选下没有格式错误明细。")


def _export_tab(service: StatsService, filters: StatsFilters) -> None:
    st.subheader("Excel 报表")
    st.caption(
        "导出写入管理数据目录的 reports/stats，包含总览、批次、人员、错误、标签、日报、"
        "口径说明和可追溯明细。不会写入任何源目录。"
    )
    if st.button("生成 Excel 报表", type="primary", key="stats_export"):
        with st.spinner("正在生成 Excel…"):
            try:
                path = service.export_excel(filters)
            except Exception as exc:  # pragma: no cover - 依赖/IO 异常
                st.error(f"生成失败：{exc}")
            else:
                st.session_state["_stats_export_path"] = str(path)
                st.success(f"已生成：{path}")

    export_path = st.session_state.get("_stats_export_path")
    if export_path and Path(export_path).exists():
        data = Path(export_path).read_bytes()
        st.download_button(
            "下载 Excel",
            data=data,
            file_name=Path(export_path).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="stats_download",
        )
        st.caption(f"当前文件：`{export_path}`")

    with st.expander("导出列说明", expanded=False):
        st.markdown(
            "- **批次统计**：批次号、名称、团队、供应商、日期、一级/二级、状态、任务、图片、"
            "格式、翻译、批次快照、导入验收、归档、最近活动。\n"
            "- **人员统计**：人员、日期、批次、验收记录、合格、不合格、其他、合格率、"
            "涉及任务、图片数、工作量。\n"
            "- **错误类型**：错误码、说明、级别、次数、涉及任务/批次、首次/最近时间。\n"
            "- **标签统计**：标签原文、次数、涉及批次/人员、合格、不合格。\n"
            "- **日报**：新增、格式、翻译、验收、合格、不合格、归档、剩余。\n"
            "- **明细**：验收明细最多导出 5000 条，超出时在页面分页查看。"
        )


def render() -> None:
    st.title("📊 数据统计（1.25.7）")
    st.caption(
        "统计只读管理数据库，口径可追溯到批次、检查、翻译、验收与归档事件；"
        "未同步或不存在的记录显示 0，不做推测。"
    )

    try:
        service = StatsService()
    except Exception as exc:
        st.error(f"管理数据库初始化失败：{exc}")
        return

    try:
        filters = _filters_ui(service)
        if filters is None:
            return
        tabs = st.tabs(["总览与批次", "人员统计", "错误与标签", "日报", "可追溯明细", "Excel 导出"])
        with tabs[0]:
            _overview_tab(service, filters)
        with tabs[1]:
            _operator_tab(service, filters)
        with tabs[2]:
            _error_tag_tab(service, filters)
        with tabs[3]:
            _daily_tab(service, filters)
        with tabs[4]:
            _trace_tab(service, filters)
        with tabs[5]:
            _export_tab(service, filters)
    finally:
        service.close()
