"""目录索引（v1.25.11 试运行）。

只写管理数据库，不修改源图片/TXT/CSV。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.config import app_config
from modules.database.connection import open_database
from modules.workspace.catalog_service import (
    MAIN_COMPLETED,
    MAIN_LABELS,
    MAIN_NEW,
    CatalogService,
)


def _project_options(db) -> list[tuple[str, str, str]]:
    rows = db.connect().execute(
        "SELECT id, project_name, pending_path, final_path FROM project_roots"
        " WHERE enabled = 1 ORDER BY project_name"
    ).fetchall()
    options: list[tuple[str, str, str]] = []
    for row in rows:
        if row["pending_path"]:
            options.append((f"{row['id']}|pending", f"{row['project_name']} · 待验数据", row["pending_path"]))
        if row["final_path"]:
            options.append((f"{row['id']}|final", f"{row['project_name']} · 申请终验", row["final_path"]))
    return options


def render() -> None:
    st.title("🗂 目录索引")
    st.caption(
        "按“大类 / 小类 / 团队 / 日期 / 三级类目 / 批次 / 编号”识别目录，"
        "只写管理数据库，不修改源图片/TXT/CSV。"
    )
    db = open_database(app_config.get_db_path())
    try:
        service = CatalogService(db)
        options = _project_options(db)
        if options:
            labels = {key: label for key, label, _ in options}
            default_root = {key: root for key, _, root in options}
            choice = st.selectbox(
                "项目与来源",
                options=list(labels),
                format_func=lambda key: labels[key],
                key="catalog_sync_source",
            )
            project_id, source_type_key = choice.split("|", 1)
            source_type = "pending_acceptance" if source_type_key == "pending" else "final_acceptance"
            default = default_root[choice]
        else:
            project_id, source_type = "", "pending_acceptance"
            default = ""

        root = st.text_input("根目录（可改成测试目录）", value=default, key="catalog_sync_root")
        c1, c2 = st.columns([1, 3])
        limit = c1.number_input("最多批次", min_value=1, max_value=2000, value=200, step=50)
        run = c2.button("扫描并写入索引", type="primary", use_container_width=True, key="catalog_sync_run")
        if run:
            if not root.strip():
                st.error("请先填写根目录。")
            else:
                with st.spinner("正在只读扫描目录并写入索引…"):
                    result = service.sync_root(
                        project_id=project_id,
                        source_type=source_type,
                        root=root.strip(),
                        limit=int(limit),
                    )
                st.session_state["catalog_sync_result"] = result
                st.session_state["catalog_sync_scope"] = (project_id, source_type)
                st.rerun()

        result = st.session_state.get("catalog_sync_result")
        if result is not None:
            st.success(
                f"扫描完成：{result.batches} 个批次，新增 {result.created}，更新 {result.updated}，"
                f"编号数据 {result.data_groups} 条，验收员冲突 {result.conflicts} 个。"
            )
            if result.warnings:
                with st.expander(f"识别提示（{len(result.warnings)}）", expanded=False):
                    for warning in result.warnings[:100]:
                        st.caption(f"⚠️ {warning}")

        scope = st.session_state.get("catalog_sync_scope", (project_id, source_type))
        rows = service.batches(project_id=scope[0], source_type=scope[1]) if scope[0] else []
        st.divider()
        st.subheader("批次索引")
        if rows:
            frame = pd.DataFrame(
                [
                    {
                        "批次": row["name"],
                        "大类": row.get("category_l1") or "",
                        "小类": row.get("category_l2") or "",
                        "三级类": row.get("category_l3") or "",
                        "团队": row.get("team_name") or "",
                        "日期": row.get("submit_date") or "",
                        "数据量": int(row.get("data_count") or 0),
                        "验收员": row.get("reviewer_name") or "",
                        "主状态": row.get("main_status_label") or "",
                        "检查状态": row.get("format_status_label") or "",
                        "翻译状态": row.get("translation_status_label") or "",
                        "路径": row.get("batch_path") or "",
                    }
                    for row in rows
                ]
            )
            st.dataframe(frame, use_container_width=True, hide_index=True)
            st.caption(f"共 {len(rows)} 个批次。路径只在这里显示，后续卡片页不展示长路径。")
        else:
            st.info("还没有索引。选择项目根目录后点“扫描并写入索引”。")

        st.divider()
        st.subheader("扫描队列（试运行）")
        if rows:
            labels_by_id = {
                row["id"]: f"{row['name']} · {row.get('team_name') or '未识别团队'} · {row.get('submit_date') or ''}"
                for row in rows
            }
            chosen = st.multiselect(
                "选择要加入队列的批次",
                options=list(labels_by_id),
                format_func=lambda value: labels_by_id[value],
                key="catalog_queue_pick",
            )
            c1, c2 = st.columns(2)
            if c1.button("加入扫描队列", use_container_width=True, key="catalog_queue_add"):
                for batch_id in chosen:
                    service.queue_add(batch_id, "scan")
                st.rerun()
            if c2.button("清空队列", use_container_width=True, key="catalog_queue_clear"):
                for item in service.queue_list("scan"):
                    service.queue_remove(item["id"])
                st.rerun()

        queue = service.queue_list("scan")
        if queue:
            for item in queue:
                c1, c2, c3 = st.columns([5, 2, 1])
                c1.write(f"**{item.get('batch_name') or item['batch_id']}**")
                c2.caption(f"{item.get('team_name') or '未识别团队'} · {item.get('submit_date') or ''}")
                if c3.button("移除", key=f"catalog_queue_del_{item['id']}"):
                    service.queue_remove(item["id"])
                    st.rerun()
            st.caption("队列写在数据库里，切换页面/返回上层不会丢失。")
        else:
            st.caption("队列为空。")

        if rows:
            st.divider()
            st.subheader("手动改状态")
            labels_by_id = {row["id"]: f"{row['name']} · {row.get('team_name') or '未识别团队'}" for row in rows}
            status_batch = st.selectbox(
                "批次",
                options=list(labels_by_id),
                format_func=lambda value: labels_by_id[value],
                key="catalog_status_batch",
            )
            current = next(row for row in rows if row["id"] == status_batch)
            c1, c2, c3 = st.columns([2, 2, 3])
            target_label = c1.radio(
                "目标状态",
                options=[MAIN_LABELS[MAIN_NEW], MAIN_LABELS[MAIN_COMPLETED]],
                index=1,
                key="catalog_status_target",
            )
            operator = c2.text_input("操作员", key="catalog_status_operator")
            reason = c3.text_input("原因", key="catalog_status_reason")
            st.caption(
                f"当前：{current.get('main_status_label')} · 检查：{current.get('format_status_label')}"
                f" · 翻译：{current.get('translation_status_label')}"
            )
            if st.button("确认修改状态", type="primary", key="catalog_status_apply"):
                target = MAIN_NEW if target_label == MAIN_LABELS[MAIN_NEW] else MAIN_COMPLETED
                service.set_main_status(status_batch, target, operator=operator, reason=reason)
                st.success("状态已写入数据库。")
                st.rerun()
            logs = service.status_logs(status_batch)
            if logs:
                with st.expander("状态变更记录", expanded=False):
                    for log in logs[:20]:
                        st.caption(
                            f"{log['created_at']} · {log['from_status']} → {log['to_status']}"
                            f" · {log.get('operator') or '未填写'} · {log.get('reason') or ''}"
                        )
    finally:
        db.close()
