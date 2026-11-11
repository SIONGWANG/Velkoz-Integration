"""管理设置页面：数据目录、数据库、备份与日志。"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from modules.common.path_guard import is_forbidden_data_dir
from modules.config import app_config
from modules.database.backup import backup_database, list_backups, verify_database
from modules.database.connection import Database
from modules.database.repositories import recent_operation_logs, recent_scan_runs
from modules.scanning.service import ScanService
from modules.workspace.models import DEFAULT_FINAL_DIR, DEFAULT_PENDING_DIR
from modules.workspace.service import WorkspaceService


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def render() -> None:
    st.title("⚙️ 管理设置（1.25.8）")
    st.caption("管理数据库只存路径、状态和日志，不存图片/TXT/CSV 原文。")

    paths = app_config.describe_paths()

    st.subheader("数据目录")
    st.write(f"当前管理数据目录：`{paths['data_dir']}`")
    st.write(f"当前数据库：`{paths['db_path']}`")
    new_dir = st.text_input("修改数据目录（保存后重启生效）", value=paths["data_dir"], key="settings_data_dir")
    c1, c2, c3 = st.columns(3)
    if c1.button("保存目录设置", use_container_width=True):
        forbidden, reason = is_forbidden_data_dir(new_dir, app_config.PROGRAM_DIR)
        if forbidden:
            st.error(reason)
        else:
            app_config.set_data_dir(new_dir)
            st.success("已保存。重启应用后使用新目录。")
    if c2.button("恢复默认 D:\\VelkozDATA", use_container_width=True):
        app_config.reset_data_dir()
        st.success("已恢复默认目录。重启应用后生效。")
    if c3.button("创建管理目录", use_container_width=True):
        try:
            created = app_config.ensure_data_dirs()
            st.success("已创建：" + "、".join(str(path) for path in created.values()))
        except OSError as exc:
            st.error(f"创建失败：{exc}")

    st.divider()
    st.subheader("业务数据目录（待验 / 申请终验）")
    st.caption(
        f"只接入 {DEFAULT_PENDING_DIR} 与 {DEFAULT_FINAL_DIR}；不会递归扫描项目根目录、"
        "00-试做情况 或 01-素材仓库。目录不存在时只报告错误，不会自动创建。"
    )
    workspace: WorkspaceService | None = None
    try:
        workspace = WorkspaceService()
        projects = workspace.projects.list()
        if projects:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "项目": item.project_name,
                            "待验目录": item.pending_path or "未配置",
                            "申请终验目录": item.final_path or "未配置",
                            "启用": "是" if item.enabled else "否",
                            "最近刷新": item.last_scan_at or "尚未刷新",
                        }
                        for item in projects
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
            options = {
                item.id: f"{item.project_name}（{'启用' if item.enabled else '停用'}）"
                for item in projects
            }
            chosen = st.selectbox(
                "选择要修改的项目",
                options=list(options.keys()),
                format_func=lambda value: options[value],
                key="settings_project_id",
            )
            current = next(item for item in projects if item.id == chosen)
            with st.expander(f"编辑：{current.project_name}", expanded=False):
                name = st.text_input("项目名称", value=current.project_name, key=f"settings_project_name_{current.id}")
                parent = st.text_input("项目根目录（可选）", value=current.parent_path, key=f"settings_project_parent_{current.id}")
                pending = st.text_input("待验数据目录", value=current.pending_path, key=f"settings_project_pending_{current.id}")
                final = st.text_input("申请终验目录", value=current.final_path, key=f"settings_project_final_{current.id}")
                note = st.text_input("备注（可选）", value=current.note, key=f"settings_project_note_{current.id}")
                enabled = st.checkbox("启用该项目", value=current.enabled, key=f"settings_project_enabled_{current.id}")
                c1, c2, c3 = st.columns(3)
                if c1.button("保存修改", type="primary", use_container_width=True, key="settings_project_save"):
                    try:
                        workspace.projects.update(
                            current.id,
                            project_name=name,
                            parent_path=parent,
                            pending_path=pending,
                            final_path=final,
                            enabled=enabled,
                            note=note,
                        )
                        st.success("已保存；只影响当前项目，其他项目配置不变。")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"保存失败：{exc}")
                if c2.button("检测两个目录", use_container_width=True, key="settings_project_detect"):
                    for path, label in ((pending, "待验数据目录"), (final, "申请终验目录")):
                        item = workspace.projects.detect_path(path, label=label)
                        st.success(f"{label}：可用 `{item.path}`") if item.ok else st.error(item.error)
                if c3.button("刷新该项目目录", use_container_width=True, key="settings_project_refresh"):
                    with st.spinner("正在刷新待验与申请终验目录…"):
                        result = workspace.refresh_project(
                            current.id,
                            operator=st.session_state.get("operator_name", ""),
                        )
                    for summary in result.get("summaries", []):
                        st.info(
                            f"{summary.get('source_label')}：{summary.get('status')}，"
                            f"新发现 {summary.get('new_batches', 0)}，"
                            f"已有 {summary.get('existing_batches', 0)}"
                        )
                        for message in summary.get("errors") or []:
                            st.error(message)
                confirm_delete = st.checkbox(
                    "我确认删除该配置（历史批次与检查记录会保留）",
                    key=f"settings_project_delete_confirm_{current.id}",
                )
                if st.button(
                    "删除该项目配置",
                    disabled=not confirm_delete,
                    key="settings_project_delete",
                ):
                    workspace.projects.delete(current.id)
                    st.success("已删除配置；历史批次没有删除。")
                    st.rerun()
        else:
            st.info("尚未配置业务目录。可在下方新增项目，或到工作台完成首次配置。")

        with st.expander("新增项目目录", expanded=not projects):
            new_name = st.text_input("项目名称", key="settings_new_project_name")
            new_parent = st.text_input(
                "项目根目录（推荐）",
                placeholder=r"\\10.10.111.227\美学\lizhiru\库\卢岳-混编项目-11月",
                key="settings_new_project_parent",
            )
            if st.button("检测根目录下的两个业务目录", key="settings_new_project_detect"):
                detection = workspace.projects.detect_parent(new_parent)
                st.session_state["_settings_new_detection"] = detection
            detection = st.session_state.get("_settings_new_detection")
            new_pending = new_final = ""
            if detection is not None:
                for label, item in (("待验数据目录", detection.pending), ("申请终验目录", detection.final)):
                    if item is None:
                        continue
                    st.success(f"{label}：已找到 `{item.path}`") if item.ok else st.warning(f"{label}：{item.error}")
                for error in detection.errors:
                    st.error(error)
                if detection.pending:
                    new_pending = detection.pending.path
                if detection.final:
                    new_final = detection.final.path
            new_pending = st.text_input(
                "待验数据目录（可单独填写）", value=new_pending, key="settings_new_project_pending"
            )
            new_final = st.text_input(
                "申请终验目录（可单独填写）", value=new_final, key="settings_new_project_final"
            )
            if st.button("保存新项目", type="primary", key="settings_new_project_save"):
                try:
                    workspace.projects.create(
                        new_name or os.path.basename(os.path.normpath(new_parent or "")) or "新项目",
                        parent_path=new_parent,
                        pending_path=new_pending,
                        final_path=new_final,
                    )
                    st.success("已新增项目；其他项目配置不受影响。")
                    st.rerun()
                except Exception as exc:
                    st.error(f"保存失败：{exc}")
    except Exception as exc:
        st.error(f"读取业务目录配置失败：{exc}")
    finally:
        if workspace is not None:
            workspace.close()

    st.divider()
    st.subheader("数据库状态")
    db_path = app_config.get_db_path()
    if not db_path.is_file():
        st.warning("数据库尚未创建。打开本页或保存扫描记录时会自动创建。")
    db = Database(db_path)
    try:
        try:
            applied = db.migrate()
            if applied:
                st.info("已应用迁移：" + "、".join(str(item) for item in applied))
            ok, detail = db.integrity_check()
            version = db.schema_version()
            size = db_path.stat().st_size if db_path.is_file() else 0
            c1, c2, c3 = st.columns(3)
            c1.metric("Schema 版本", version)
            c2.metric("数据库大小", _human_size(size))
            c3.metric("完整性", "正常" if ok else "异常")
            if not ok:
                st.error(detail)
            if st.button("重新检查完整性", use_container_width=True):
                ok2, detail2 = db.integrity_check()
                st.success("完整性正常") if ok2 else st.error(detail2)
            if st.button("立即备份数据库", use_container_width=True):
                try:
                    target = backup_database(db_path)
                    st.success(f"备份完成：{target}")
                except Exception as exc:
                    st.error(f"备份失败：{exc}")
        except Exception as exc:
            st.error(f"数据库检查失败：{exc}")
    finally:
        db.close()

    backups = list_backups()
    if backups:
        st.write("最近备份")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "文件": item["name"],
                        "大小": _human_size(item["size"]),
                        "路径": item["path"],
                    }
                    for item in backups[:10]
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        with st.expander("校验备份"):
            chosen = st.selectbox("选择备份文件", [item["path"] for item in backups], key="backup_verify_path")
            if st.button("校验完整性", key="backup_verify_btn"):
                ok, detail = verify_database(chosen)
                st.success("备份完整") if ok else st.error(detail)
    else:
        st.caption("尚无备份。")

    st.divider()
    st.subheader("扫描记录与操作日志")
    service = None
    try:
        service = ScanService()
        rulesets = service.list_rule_sets()
        st.write(f"规则方案数：{len(rulesets)}")
        if rulesets:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "ID": item["id"],
                            "名称": item["name"],
                            "版本": item.get("active_version"),
                            "默认": "是" if item.get("is_default") else "",
                            "更新时间": item.get("updated_at", ""),
                        }
                        for item in rulesets
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        runs = service.recent_runs(20)
        st.write("最近扫描记录")
        if runs:
            st.dataframe(pd.DataFrame(runs), use_container_width=True, hide_index=True)
        else:
            st.caption("尚无扫描记录。")

        logs = recent_operation_logs(service.db, 50)
        st.write("最近操作日志")
        if logs:
            st.dataframe(pd.DataFrame(logs), use_container_width=True, hide_index=True)
        else:
            st.caption("尚无操作日志。")
    except Exception as exc:
        st.error(f"读取管理记录失败：{exc}")
    finally:
        if service is not None:
            service.close()

    st.divider()
    st.caption(
        "说明：本版本不会改变正式质检的 CSV/TXT 行为；扫描、预览、保存扫描记录都不修改源目录。"
    )
