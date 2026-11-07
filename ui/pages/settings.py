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


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def render() -> None:
    st.title("⚙️ 管理设置（1.25.0）")
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
