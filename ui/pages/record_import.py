"""其他验收员记录导入页面（1.25.4）。

页面只读发现与导入 CSV/Excel；导入只写管理数据库，不修改、删除、
移动或重命名任何源文件。
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from modules.record_import.errors import catalog_rows
from modules.record_import.models import (
    IMPORT_STATUS_LABELS,
    LINK_LABELS,
    SOURCE_TYPE_LABELS,
)
from modules.record_import.service import RecordImportService


def _fmt_time(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ")[:19]


def _batch_options(service: RecordImportService) -> dict[str, str]:
    options: dict[str, str] = {}
    for item in service.list_batches(500):
        label = f"{item.get('name') or item.get('id')}（{item.get('data_count', 0)} 组）"
        options[str(item["id"])] = label
    return options


def _file_rows(files) -> list[dict]:
    return [
        {
            "文件名": item.name,
            "类型": item.type_label,
            "状态": item.status_label,
            "验收员": item.operator,
            "行数": item.rows_total,
            "编码": item.encoding,
            "错误码": item.error_code,
            "说明": item.note,
            "路径": item.path,
        }
        for item in files
    ]


def _import_rows(results) -> list[dict]:
    return [
        {
            "文件": os.path.basename(item.source_path),
            "类型": SOURCE_TYPE_LABELS.get(item.source_type, item.source_type),
            "状态": item.status_label,
            "总行数": item.rows_total,
            "新增": item.rows_new,
            "重复跳过": item.rows_skipped,
            "错误": item.rows_error,
            "验收员": item.operator_detected,
            "说明": item.note,
            "来源": item.source_path,
        }
        for item in results
    ]


def _record_rows(records) -> list[dict]:
    return [
        {
            "任务ID": item.get("task_id") or "",
            "验收员": item.get("operator") or "",
            "结果": item.get("result") or "",
            "一级": item.get("l1") or "",
            "二级": item.get("l2") or "",
            "备注": item.get("note") or "",
            "标签（原文）": item.get("tags_raw") or "",
            "证据（原文）": item.get("evidence_raw") or "",
            "质检时间": item.get("record_time") or "",
            "关联": LINK_LABELS.get(item.get("link_status") or "", item.get("link_status") or ""),
            "数据组": item.get("data_group_id") or "",
            "来源行": item.get("source_row") or "",
            "来源文件": os.path.basename(item.get("source_path") or ""),
        }
        for item in records
    ]


def _render_discover(service: RecordImportService) -> None:
    st.subheader("发现与导入")
    st.caption(
        "扫描只读取文件名、表头与内容；导入只写管理数据库。"
        "不会修改、删除、移动或重命名源 CSV/Excel，也不会改动源图片/TXT。"
    )

    default_root = (
        st.session_state.get("record_import_root")
        or st.session_state.get("root_path", "")
    )
    col_root, col_operator = st.columns([3, 1])
    root = col_root.text_input("根目录或单个文件", value=default_root, key="ri_root_input")
    operator = col_operator.text_input(
        "操作员", value=st.session_state.get("operator_name", ""), key="ri_operator"
    )

    col_recursive, col_batch = st.columns([1, 2])
    recursive = col_recursive.checkbox("递归扫描子目录", value=False, key="ri_recursive")
    batches = _batch_options(service)
    batch_ids = [""] + list(batches.keys())
    batch_id = col_batch.selectbox(
        "限定批次（可空）",
        options=batch_ids,
        format_func=lambda key: batches.get(key, "不限定批次（按任务ID自动关联）"),
        key="ri_batch",
    )

    if st.button(
        "🔍 扫描 CSV / Excel",
        type="primary",
        use_container_width=True,
        disabled=not root.strip(),
        key="ri_scan_btn",
    ):
        try:
            with st.spinner("正在只读检查文件…"):
                result = service.discover(root.strip(), recursive=bool(recursive))
            st.session_state["_ri_discovery"] = result
            st.session_state["record_import_root"] = root.strip()
            st.session_state["_ri_results"] = []
            st.session_state["_ri_results_dry"] = False
        except Exception as exc:
            st.error(f"扫描失败：{exc}")

    discovery = st.session_state.get("_ri_discovery")
    if discovery is None:
        st.info("先选择一个目录或单个 CSV/Excel 文件，再执行只读扫描。")
        return

    if discovery.cancelled:
        st.warning("扫描已取消。")
    if discovery.truncated:
        st.warning("文件数超过上限，结果已截断。")
    if discovery.issues:
        for issue in discovery.issues:
            st.warning(issue)

    counts = discovery.counts()
    columns = st.columns(5)
    columns[0].metric("发现文件", len(discovery.files))
    columns[1].metric("可导入", len(discovery.ready_files))
    columns[2].metric("无法识别", counts.get("unrecognized", 0))
    columns[3].metric("格式不支持", counts.get("unsupported", 0))
    columns[4].metric("读取失败", counts.get("error", 0))

    if not discovery.files:
        st.info("尚未发现可导入的 CSV/Excel 表。")
        return

    st.dataframe(
        pd.DataFrame(_file_rows(discovery.files)),
        use_container_width=True,
        hide_index=True,
    )

    ready = discovery.ready_files
    if not ready:
        st.warning("发现的文件均不可导入；请查看状态与错误码说明。")
        return

    selected = st.multiselect(
        "本次导入的文件（默认全部可导入文件）",
        options=[item.path for item in ready],
        default=[item.path for item in ready],
        format_func=lambda path: os.path.basename(path),
        key="ri_selected_files",
    )
    dry_run = st.checkbox(
        "试运行（只检查，不写入数据库）", value=False, key="ri_dry_run"
    )
    if st.button(
        "📥 开始导入",
        type="primary",
        use_container_width=True,
        disabled=not selected,
        key="ri_import_btn",
    ):
        try:
            with st.spinner("正在导入…"):
                results = service.import_files(
                    selected,
                    operator=operator.strip(),
                    batch_id=batch_id,
                    dry_run=bool(dry_run),
                )
            st.session_state["_ri_results"] = results
            st.session_state["_ri_results_dry"] = bool(dry_run)
        except Exception as exc:
            st.error(f"导入失败：{exc}")

    results = st.session_state.get("_ri_results") or []
    if results:
        _render_results(results, bool(st.session_state.get("_ri_results_dry")))


def _render_results(results, dry_run: bool) -> None:
    st.divider()
    st.subheader("本次导入结果")
    if dry_run:
        st.info("这是试运行结果，未写入管理数据库。")
    new_total = sum(item.rows_new for item in results)
    skipped_total = sum(item.rows_skipped for item in results)
    error_total = sum(item.rows_error for item in results)
    columns = st.columns(4)
    columns[0].metric("文件", len(results))
    columns[1].metric("新增记录", new_total)
    columns[2].metric("重复跳过", skipped_total)
    columns[3].metric("错误行", error_total)
    st.dataframe(
        pd.DataFrame(_import_rows(results)),
        use_container_width=True,
        hide_index=True,
    )
    warnings = [text for item in results for text in item.warnings]
    if warnings:
        with st.expander(f"提示与错误（{len(warnings)}）", expanded=False):
            for text in warnings:
                st.write(f"- {text}")
    linked = [
        row
        for item in results
        for row in item.rows
        if row.link_status == "matched"
    ]
    if linked:
        with st.expander(f"已关联任务（{len(linked)}）", expanded=False):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "任务ID": row.task_id,
                            "数据组": row.data_group_id,
                            "批次": row.batch_id,
                            "结果": row.result,
                            "标签（原文）": row.tags_raw,
                        }
                        for row in linked
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )


def _render_history(service: RecordImportService) -> None:
    st.subheader("导入历史")
    summary = service.summary()
    columns = st.columns(4)
    columns[0].metric("导入次数", summary.get("imports", 0))
    columns[1].metric("新增记录", summary.get("rows_new", 0))
    columns[2].metric("重复跳过", summary.get("rows_skipped", 0))
    columns[3].metric("错误行", summary.get("rows_error", 0))

    imports = service.list_imports(500)
    if not imports:
        st.info("还没有导入记录。")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "时间": _fmt_time(item.get("imported_at") or item.get("created_at")),
                    "文件": os.path.basename(item.get("source_path") or ""),
                    "类型": SOURCE_TYPE_LABELS.get(
                        item.get("source_type") or "", item.get("source_type") or ""
                    ),
                    "状态": IMPORT_STATUS_LABELS.get(
                        item.get("status") or "", item.get("status") or ""
                    ),
                    "总行数": item.get("rows_total", 0),
                    "新增": item.get("rows_new", 0),
                    "重复": item.get("rows_skipped", 0),
                    "错误": item.get("rows_error", 0),
                    "验收员": item.get("operator_detected") or "",
                    "操作员": item.get("imported_by") or "",
                    "说明": item.get("note") or "",
                    "来源": item.get("source_path") or "",
                }
                for item in imports
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )


def _render_records(service: RecordImportService) -> None:
    st.subheader("记录明细")
    imports = service.list_imports(500)
    if not imports:
        st.info("还没有导入记录。")
        return
    options = {"": "最近导入的记录"}
    for item in imports:
        label = (
            f"{_fmt_time(item.get('imported_at') or item.get('created_at'))} | "
            f"{os.path.basename(item.get('source_path') or '')} | "
            f"{IMPORT_STATUS_LABELS.get(item.get('status') or '', item.get('status') or '')}"
        )
        options[str(item["id"])] = label
    choice = st.selectbox(
        "按导入批次查看",
        options=list(options.keys()),
        format_func=lambda key: options[key],
        key="ri_record_import",
    )
    if choice:
        records = service.list_records(choice, limit=5000)
    else:
        records = service.recent_records(500)
    if not records:
        st.info("该导入没有记录明细。")
        return
    st.caption(f"共 {len(records)} 条；标签、备注、证据路径均为原文保留。")
    st.dataframe(
        pd.DataFrame(_record_rows(records)),
        use_container_width=True,
        hide_index=True,
    )


def _render_errors() -> None:
    st.subheader("错误码说明")
    st.caption("状态诚实：未发现、无法识别、格式不支持与导入失败都会明确显示。")
    st.dataframe(
        pd.DataFrame(catalog_rows(), columns=["错误码", "级别", "说明", "建议"]),
        use_container_width=True,
        hide_index=True,
    )


def render() -> None:
    st.title("📥 其他验收员记录导入（1.25.4）")
    st.caption(
        "只读发现 CSV/Excel，识别工作记录、导出表、统计表与备份表；"
        "导入只写管理数据库，原文件保持不变。"
    )
    try:
        service = RecordImportService()
    except Exception as exc:
        st.error(f"管理数据库初始化失败：{exc}")
        return

    try:
        tab_discover, tab_history, tab_records, tab_errors = st.tabs(
            ["发现与导入", "导入历史", "记录明细", "错误码说明"]
        )
        with tab_discover:
            _render_discover(service)
        with tab_history:
            _render_history(service)
        with tab_records:
            _render_records(service)
        with tab_errors:
            _render_errors()
    finally:
        service.close()
