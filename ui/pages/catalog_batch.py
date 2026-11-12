"""单个目录批次详情（v1.25.11 P2）。

只读管理数据库；源目录仅展示，不修改。
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from modules.config import app_config
from modules.database.connection import open_database
from modules.workspace.catalog_service import MAIN_COMPLETED, MAIN_LABELS, MAIN_NEW, CatalogService
from modules.workspace.catalog_scan import apply_scan, preview_scan
from modules.workspace.catalog_format import (
    category_rules,
    check_batch,
    latest_run as latest_format_run,
    resolve_rule,
)
from modules.workspace import catalog_translation as tr
from modules.workspace.models import SOURCE_LABELS
from ui.pages.catalog_workbench import _CSS, _esc, _key, _status_pills, _summary


def _go(page: str) -> None:
    from ui.router import go

    go(page)


def _meta_html(batch: dict[str, Any]) -> str:
    parts = [
        ("大类", batch.get("category_l1")),
        ("小类", batch.get("category_l2")),
        ("三级类", batch.get("category_l3")),
        ("团队", batch.get("team_name")),
        ("日期", batch.get("submit_date")),
        ("验收员", batch.get("reviewer_name")),
        ("来源", SOURCE_LABELS.get(str(batch.get("source_type") or ""), batch.get("source_type"))),
    ]
    chips = "".join(
        f'<span class="vk-pill muted">{_esc(label)}：{_esc(value or "—")}</span>'
        for label, value in parts
    )
    return f'<div>{chips}</div>'


def render(batch_id: str) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    db = open_database(app_config.get_db_path())
    try:
        service = CatalogService(db)
        batch = service.batch(batch_id)
        if not batch:
            st.error("批次不存在，可能已被重新扫描替换。")
            if st.button("返回工作台", type="primary"):
                _go("workbench")
            return

        notice = st.session_state.pop(f"catalog_notice_{batch_id}", "")
        if notice:
            st.success(str(notice))

        top = st.columns([1, 1, 4], vertical_alignment="center")
        if top[0].button("← 返回工作台", key="catalog_detail_back", use_container_width=True):
            _go("workbench")
        if top[1].button("目录索引", key="catalog_detail_sync", use_container_width=True):
            _go("catalog_sync")

        st.markdown(
            f'<div class="vk-hero"><h2>{_esc(batch.get("name") or "未命名批次")}</h2>'
            f'<p>{_esc(batch.get("team_name") or "未识别团队")} · '
            f'{_esc(batch.get("submit_date") or "日期未知")} · '
            f'{int(batch.get("data_count") or 0)} 条编号数据</p></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            _meta_html(batch) + f'<div style="margin-top:.55rem">{_status_pills(batch)}</div>',
            unsafe_allow_html=True,
        )

        data = _summary([batch])
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("编号数据", data["data"])
        m2.metric("批次状态", batch.get("main_status_label") or "新提交")
        m3.metric("检查", batch.get("format_status_label") or "未检查")
        m4.metric("翻译", batch.get("translation_status_label") or "未翻译")

        with st.expander("格式检查（图片数量规则）", expanded=False):
            rule = resolve_rule(batch, category_rules(service))
            if rule.configured:
                st.caption(
                    f"当前规则：{rule.source}「{rule.category}」要求 {rule.expected} 张图片。"
                )
            else:
                st.caption("当前批次的大类/小类/三级类还没有配置图片数量规则。")
            last = latest_format_run(db, batch_id)
            if last:
                st.caption(
                    f"最近一次：{'检查失败' if last.get('status') == 'failed' else '已检查'}"
                    f" · 编号 {int(last.get('total') or 0)}"
                    f" · 通过 {int(last.get('passed') or 0)}"
                    f" · 失败 {int(last.get('failed') or 0)}"
                    f" · 重复 {int(last.get('duplicates') or 0)}"
                    f" · {last.get('finished_at') or ''}"
                )
            if st.button("重新检查格式", key=f"detail_format_{batch_id}"):
                try:
                    result = check_batch(
                        db,
                        batch_id,
                        operator=str(st.session_state.get("operator_name") or ""),
                    )
                    st.session_state[f"catalog_notice_{batch_id}"] = result.message
                    st.rerun()
                except Exception as exc:
                    st.error(f"格式检查失败：{exc}")

        translation_run = tr.latest_run(db, batch_id)
        if translation_run:
            with st.expander("最近一次翻译（8765）", expanded=False):
                st.caption(
                    f"状态：{translation_run.get('status') or ''}"
                    f" · 成功 {int(translation_run.get('translated') or 0)}"
                    f" · 失败 {int(translation_run.get('failed') or 0)}"
                    f" · {translation_run.get('finished_at') or ''}"
                )
                if translation_run.get("backup_dir"):
                    st.caption(f"英文备份：`{translation_run['backup_dir']}`")
                items = tr.run_items(db, str(translation_run.get("id") or ""))
                if items:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "编号": row.get("number") or "",
                                    "结果": row.get("status") or "",
                                    "说明": row.get("message") or "",
                                }
                                for row in items
                            ]
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )

        groups = service.groups(batch_id)
        records = service.records(batch_id)
        st.divider()
        st.subheader(f"编号数据（{len(groups)}）")
        if groups:
            frame = pd.DataFrame(
                [
                    {
                        "编号": row.get("task_id") or "",
                        "状态": row.get("status") or "",
                        "图片": int(row.get("image_count") or 0),
                        "TXT": int(row.get("txt_count") or 0),
                        "文件": int(row.get("file_count") or 0),
                    }
                    for row in groups
                ]
            )
            st.dataframe(frame, use_container_width=True, hide_index=True)
            with st.expander("查看编号文件夹路径（只读）", expanded=False):
                for row in groups:
                    st.caption(f'{row.get("task_id")}：`{row.get("path") or ""}`')
        else:
            st.caption("没有识别到编号文件夹。")

        st.divider()
        st.subheader(f"验收记录（{len(records)}）")
        if records:
            frame = pd.DataFrame(
                [
                    {
                        "编号": row.get("number") or "",
                        "结果": row.get("result") or "",
                        "原因": row.get("reason") or "",
                        "标签": row.get("tags") or "",
                        "质检时间": row.get("record_time") or "",
                        "验收员": row.get("reviewer_name") or "",
                    }
                    for row in records
                ]
            )
            st.dataframe(frame, use_container_width=True, hide_index=True)
            with st.expander("查看截图与源文件（只读）", expanded=False):
                for row in records:
                    st.caption(
                        f'{row.get("number")} · {row.get("result") or "未填写"} · '
                        f'截图：`{row.get("evidence") or "—"}` · '
                        f'源文件：`{row.get("source_file") or "—"}`'
                    )
        else:
            st.caption("还没有扫描验收记录。回到工作台勾选批次并加入扫描队列。")

        preview = preview_scan(db, batch_id)
        with st.expander("重新扫描验收记录", expanded=False):
            st.caption("只读 `_质检记录` 下的 CSV/XLSX，重新写入本批次记录并更新主状态。")
            st.caption(
                f"当前检测到 {len(preview.reviewers)} 个验收员："
                f"{'、'.join(preview.reviewers) if preview.reviewers else '未匹配'}"
                f" · {preview.file_count} 个记录文件"
            )
            if not preview.reviewers:
                st.caption("没有匹配到验收员；重新扫描不会改变主状态。")
            selected: list[str] = []
            if preview.conflict:
                mode = st.radio(
                    "绑定方式",
                    ["全部绑定", "只绑一个", "都不绑定"],
                    key=f"detail_rescan_mode_{batch_id}",
                    horizontal=True,
                )
                if mode == "全部绑定":
                    selected = list(preview.reviewers)
                elif mode == "只绑一个":
                    selected = [
                        str(
                            st.selectbox(
                                "验收员",
                                options=list(preview.reviewers),
                                key=f"detail_rescan_reviewer_{batch_id}",
                            )
                        )
                    ]
            else:
                selected = list(preview.reviewers)
            if st.button(
                "开始重新扫描",
                key=f"detail_rescan_{batch_id}",
                type="primary",
                use_container_width=False,
                disabled=not preview.reviewers,
            ):
                try:
                    result = apply_scan(db, batch_id, selected)
                    st.session_state[f"catalog_notice_{batch_id}"] = (
                        result.message if result.applied else f"未写入记录：{result.message}"
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"重新扫描失败：{exc}")

        st.divider()
        st.subheader("验收员文件夹")
        candidates = batch.get("reviewer_candidates") or []
        if candidates:
            frame = pd.DataFrame(
                [
                    {
                        "验收员文件夹": row.get("folder_name") or "",
                        "匹配验收员": row.get("reviewer_name") or "",
                        "文件数": int(row.get("file_count") or 0),
                    }
                    for row in candidates
                ]
            )
            st.dataframe(frame, use_container_width=True, hide_index=True)
            with st.expander("查看验收记录文件夹路径（只读）", expanded=False):
                for row in candidates:
                    st.caption(f'`{row.get("folder_path") or ""}`')
            if batch.get("reviewer_conflict"):
                st.warning("检测到多个验收员。扫描时会让操作员选择绑定方式。")
        else:
            st.caption("没有 _质检记录 或没有匹配到验收员；主状态保持新提交。")

        st.divider()
        st.subheader("手动修改主状态")
        st.caption("主状态只有“新提交 / 已完成”。修改会写入数据库并记录日志。")
        with st.form(f"catalog_status_{batch_id}"):
            c1, c2, c3 = st.columns([1.2, 1.2, 2.6])
            target_label = c1.selectbox(
                "目标状态",
                [MAIN_LABELS[MAIN_NEW], MAIN_LABELS[MAIN_COMPLETED]],
                index=0 if batch.get("main_status") == MAIN_NEW else 1,
            )
            operator = c2.text_input("操作员", value=st.session_state.get("operator_name", ""))
            reason = c3.text_input("原因", placeholder="例如：验收记录已人工核对")
            submitted = st.form_submit_button("确认修改", type="primary", use_container_width=True)
            if submitted:
                target = MAIN_NEW if target_label == MAIN_LABELS[MAIN_NEW] else MAIN_COMPLETED
                try:
                    service.set_main_status(batch_id, target, operator=operator.strip(), reason=reason.strip())
                    st.session_state[f"catalog_notice_{batch_id}"] = "状态已写入数据库。"
                    st.rerun()
                except Exception as exc:
                    st.error(f"修改失败：{exc}")

        logs = service.status_logs(batch_id)
        if logs:
            with st.expander(f"状态变更记录（{len(logs)}）", expanded=False):
                for log in logs:
                    st.caption(
                        f'{log["created_at"]} · {log["from_status"]} → {log["to_status"]}'
                        f' · {log.get("operator") or "未填写"} · {log.get("reason") or ""}'
                    )

        with st.expander("批次路径（只读）", expanded=False):
            st.code(batch.get("batch_path") or "", language="text")
    finally:
        db.close()
