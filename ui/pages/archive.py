"""合格数据归档页面（v1.25.6）。

页面只负责选择、预览、创建任务与显示进度；所有复制逻辑在
`modules.archive.service`，源图片/TXT/CSV 始终只读，目标不覆盖。
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from modules.archive.models import (
    DEFAULT_NAME_TEMPLATE,
    FILTER_ALL,
    FILTER_IMPORTED,
    FILTER_LABELS,
    FILTER_MANUAL,
    FILTER_QUALIFIED,
    FILTER_STATUS,
    ITEM_STATUS_LABELS,
    JOB_COMPLETED,
    JOB_PAUSED,
    JOB_PREVIEW,
    JOB_RUNNING,
    JOB_STATUS_LABELS,
    START_LAYERS,
    ArchivePreview,
)
from modules.archive.planner import format_tree
from modules.archive.service import ArchiveService


def _fmt_time(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ")[:19]


def _human_bytes(value: int | None) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value or 0)} B"


def _batch_options(service: ArchiveService) -> dict[str, str]:
    options: dict[str, str] = {}
    for item in service.list_batches(500):
        label = (
            f"{item.get('display_id') or item.get('id')} | "
            f"{item.get('name') or '未命名批次'} | "
            f"{item.get('group_count', 0)} 组"
        )
        options[str(item["id"])] = label
    return options


def _flash() -> None:
    message = st.session_state.pop("_archive_flash", "")
    if message:
        st.success(message)


def _config_key(**values: Any) -> tuple:
    return tuple(
        (key, tuple(value) if isinstance(value, list) else value)
        for key, value in sorted(values.items())
    )


def _preview_frame(preview: ArchivePreview) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "任务ID": group.task_id,
                "数据组": group.data_group_id,
                "文件": group.file_count,
                "字节": group.bytes_total,
                "目标冲突": "是" if group.has_conflict else "",
                "目标路径": group.dest_path,
                "说明": group.error or "；".join(group.notes),
            }
            for group in preview.groups
        ]
    )


def _render_preview(preview: ArchivePreview) -> None:
    st.subheader("复制前预览")
    columns = st.columns(5)
    columns[0].metric("数据组", preview.total_items)
    columns[1].metric("文件", preview.total_files)
    columns[2].metric("占用空间", _human_bytes(preview.total_bytes))
    columns[3].metric("目标已存在", preview.conflict_groups)
    columns[4].metric("计划错误", preview.error_groups)
    st.caption(
        f"命名目录：`{preview.rendered_folder}` · 起始层级："
        f"{dict(START_LAYERS).get(preview.start_layer, preview.start_layer)} · "
        f"输出根目录：`{preview.output_root or '未填写'}`"
    )
    for error in preview.errors:
        st.error(error)
    for warning in preview.warnings:
        st.warning(warning)
    if preview.missing_variables:
        st.caption("缺失变量已按回退规则处理：" + "、".join(preview.missing_variables))
    if preview.groups:
        st.dataframe(
            _preview_frame(preview),
            use_container_width=True,
            hide_index=True,
        )
    if preview.tree:
        with st.expander("目录树预览", expanded=True):
            st.code(format_tree(preview.tree, preview.output_root), language="text")


def _run_selected_job(service: ArchiveService, job_id: str) -> None:
    bar = st.progress(0.0, text="准备复制…")

    def progress(done: int, total: int, message: str) -> None:
        bar.progress(min(1.0, done / max(1, total)), text=message)

    result = service.run_job(job_id, progress=progress, verify_hash=None)
    if result.cancelled or result.status == JOB_PAUSED:
        st.session_state["_archive_flash"] = (
            f"归档任务已暂停：{result.verified_items} 组已校验，可稍后继续。"
        )
    elif result.status == JOB_COMPLETED:
        st.session_state["_archive_flash"] = (
            f"归档完成：{result.verified_items} 组校验通过，"
            f"{result.skipped_items} 组已存在跳过，复制 {_human_bytes(result.bytes_copied)}。"
        )
    else:
        st.session_state["_archive_flash"] = (
            f"归档结束但有异常：冲突 {result.conflict_items} 组，失败 {result.failed_items} 组。"
            "请查看条目与报告后继续。"
        )
    st.rerun()


def render() -> None:
    st.title("🗄️ 合格数据归档")
    st.caption(
        "默认只复制：不移动、不删除、不修改源图片/TXT/CSV；目标已有文件不覆盖。"
        "复制后按大小/哈希校验，中断可从已完成的数据组继续。"
    )
    _flash()
    service = ArchiveService()
    try:
        batches = _batch_options(service)
        if not batches:
            st.info("还没有已确认批次。请先在“候选确认”中确认扫描结果。")
            return

        st.subheader("1. 选择批次与合格范围")
        col_batch, col_operator = st.columns([3, 1])
        batch_id = col_batch.selectbox(
            "批次",
            options=list(batches),
            format_func=lambda key: batches[key],
            key="archive_batch",
        )
        operator = col_operator.text_input(
            "验收员 / 操作员",
            value=st.session_state.get("operator_name", ""),
            key="archive_operator",
        )
        filter_mode = st.radio(
            "合格筛选",
            options=list(FILTER_LABELS),
            format_func=lambda key: FILTER_LABELS[key],
            horizontal=True,
            key="archive_filter_mode",
        )
        filter_value = ""
        group_ids: list[str] = []
        if filter_mode == FILTER_QUALIFIED:
            st.caption(
                "默认合格条件：数据组状态为 qualified/passed/completed，"
                "或导入记录结果为“合格/修改后合格”。若数据库尚未同步验收结果，"
                "可切换为按状态、按导入记录或手动选择。"
            )
        elif filter_mode == FILTER_STATUS:
            filter_value = st.text_input(
                "数据组状态（逗号分隔）",
                value="qualified,passed,completed",
                key="archive_filter_status",
            )
        elif filter_mode == FILTER_IMPORTED:
            filter_value = st.text_input(
                "导入记录结果（逗号分隔）",
                value="合格,修改后合格",
                key="archive_filter_result",
            )
        elif filter_mode == FILTER_MANUAL:
            groups = service.list_groups(batch_id)
            labels = {
                str(group["id"]): f"{group.get('task_id') or group['id']} | "
                f"{group.get('user_name') or ''} | {group.get('normalized_path') or ''}"
                for group in groups
            }
            group_ids = st.multiselect(
                "选择数据组",
                options=list(labels),
                format_func=lambda key: labels.get(key, key),
                key="archive_manual_groups",
            )

        st.subheader("2. 输出目录与命名")
        col_output, col_layer = st.columns([3, 1])
        output_root = col_output.text_input(
            "输出根目录（必须是本地目录，且不能位于源数据目录内）",
            value=st.session_state.get("archive_output_root", ""),
            placeholder=r"例如 D:\合格归档",
            key="archive_output_root",
        )
        start_layer = col_layer.selectbox(
            "起始层级",
            options=[key for key, _ in START_LAYERS],
            format_func=lambda key: dict(START_LAYERS)[key],
            index=3,
            key="archive_start_layer",
        )
        col_template, col_custom = st.columns([2, 1])
        name_template = col_template.text_input(
            "命名模板",
            value=DEFAULT_NAME_TEMPLATE,
            key="archive_name_template",
        )
        custom_prefix = ""
        if start_layer == "custom":
            custom_prefix = col_custom.text_input(
                "自定义前缀（相对目录）",
                placeholder="例如 客户A/2026",
                key="archive_custom_prefix",
            )
        else:
            col_custom.caption(
                "变量：{批次名} {验收员} {合格数量} {日期} {大类目} {小类目} {团队} {自定义}"
            )
        verify_hash = st.checkbox(
            "复制后使用哈希校验（推荐；大文件会多读一遍）",
            value=True,
            key="archive_verify_hash",
        )

        config_key = _config_key(
            batch_id=batch_id,
            operator=operator,
            filter_mode=filter_mode,
            filter_value=filter_value,
            group_ids=group_ids,
            output_root=output_root,
            start_layer=start_layer,
            name_template=name_template,
            custom_prefix=custom_prefix,
        )

        if st.button(
            "🔍 预览目录树与冲突",
            type="primary",
            use_container_width=True,
            key="archive_preview_btn",
        ):
            try:
                preview = service.preview(
                    batch_id,
                    output_root,
                    start_layer=start_layer,
                    name_template=name_template,
                    operator=operator,
                    filter_mode=filter_mode,
                    filter_value=filter_value,
                    group_ids=group_ids,
                    custom_prefix=custom_prefix,
                )
            except Exception as exc:
                st.error(f"预览失败：{exc}")
            else:
                st.session_state["_archive_preview"] = preview
                st.session_state["_archive_preview_key"] = config_key

        preview = st.session_state.get("_archive_preview")
        if isinstance(preview, ArchivePreview):
            if st.session_state.get("_archive_preview_key") != config_key:
                st.warning("设置已变化，请重新预览后再创建归档任务。")
            else:
                _render_preview(preview)
                disabled = bool(preview.errors) or not preview.groups
                if st.button(
                    "📦 创建归档任务（不复制）",
                    use_container_width=True,
                    disabled=disabled,
                    key="archive_create_job",
                ):
                    try:
                        job_id = service.create_job(
                            preview,
                            operator=operator,
                            verify_hash=verify_hash,
                        )
                    except Exception as exc:
                        st.error(f"创建归档任务失败：{exc}")
                    else:
                        st.session_state["_archive_flash"] = (
                            f"已创建归档任务：{job_id}。任务尚未复制，请在下方点击开始。"
                        )
                        st.session_state.pop("_archive_preview", None)
                        st.session_state.pop("_archive_preview_key", None)
                        st.rerun()

        st.divider()
        st.subheader("3. 归档任务与断点恢复")
        jobs = service.list_jobs(100, batch_id=batch_id)
        if not jobs:
            st.info("该批次还没有归档任务。先预览并创建任务。")
            return
        job_labels = {
            str(job["id"]): (
                f"{_fmt_time(job.get('created_at'))} | "
                f"{JOB_STATUS_LABELS.get(job.get('status') or '', job.get('status') or '')} | "
                f"{job.get('verified_items', 0)}/{job.get('total_items', 0)} 组 | "
                f"{job['id'][:18]}"
            )
            for job in jobs
        }
        job_id = st.selectbox(
            "归档任务",
            options=list(job_labels),
            format_func=lambda key: job_labels[key],
            key="archive_job_select",
        )
        job = service.get_job(job_id)
        if not job:
            st.warning("归档任务不存在。")
            return
        columns = st.columns(6)
        columns[0].metric("状态", JOB_STATUS_LABELS.get(job.get("status") or "", job.get("status") or ""))
        columns[1].metric("数据组", job.get("total_items", 0))
        columns[2].metric("已校验", job.get("verified_items", 0))
        columns[3].metric("跳过", job.get("skipped_items", 0))
        columns[4].metric("冲突", job.get("conflict_items", 0))
        columns[5].metric("失败", job.get("failed_items", 0))
        st.caption(
            f"输出根目录：`{job.get('output_root') or ''}` · "
            f"模板：`{job.get('name_template') or ''}` · "
            f"起始层级：{dict(START_LAYERS).get(job.get('start_layer') or '', job.get('start_layer') or '')} · "
            f"哈希校验：{'开' if job.get('verify_hash') else '关'}"
        )
        items = service.list_items(job_id)
        if items:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "任务ID": item.get("task_id") or "",
                            "状态": ITEM_STATUS_LABELS.get(
                                item.get("status") or "", item.get("status") or ""
                            ),
                            "文件": item.get("file_count", 0),
                            "字节": item.get("bytes", 0),
                            "源路径": item.get("source_path") or "",
                            "目标路径": item.get("dest_path") or "",
                            "说明": (item.get("error_message") or "")[:300],
                        }
                        for item in items
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        confirm_copy = st.checkbox(
            "我确认执行复制：只复制，不移动/不删除/不覆盖源与目标已有文件",
            value=False,
            key=f"archive_confirm_{job_id}",
        )
        if job.get("status") in {JOB_PREVIEW, JOB_PAUSED, JOB_RUNNING} or int(
            job.get("failed_items") or 0
        ) or int(job.get("conflict_items") or 0):
            label = (
                "▶️ 开始复制"
                if job.get("status") == JOB_PREVIEW
                else "🔄 继续 / 重试未完成项"
            )
            if st.button(
                label,
                type="primary",
                use_container_width=True,
                disabled=not confirm_copy,
                key=f"archive_run_{job_id}",
            ):
                _run_selected_job(service, job_id)
        else:
            st.success("该任务已完成并校验。")

        report = service.report_text(job_id)
        if report:
            with st.expander("归档报告", expanded=False):
                st.markdown(report)
    finally:
        service.close()
