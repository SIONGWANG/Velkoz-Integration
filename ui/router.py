"""1.25.x 管理平台页面路由与导航。

管理平台已从“更多设置”中的入口独立出来，默认进入批次总览；
默认正式质检三栏布局不变。所有管理页面只读或只写管理数据库，
不修改源图片/TXT/CSV。
"""
from __future__ import annotations

import streamlit as st

PAGES: tuple[tuple[str, str], ...] = (
    ("workbench", "🏠 数据验收工作台"),
    ("batch_overview", "📦 批次总览"),
    ("team_category", "👥 团队与类别"),
    ("scan", "📂 数据扫描"),
    ("batches", "🧩 候选确认"),
    ("format", "🔎 格式检查"),
    ("precheck", "🌐 翻译预审"),
    ("record_import", "📥 记录导入"),
    ("archive", "🗄️ 合格归档"),
    ("stats", "📊 数据统计"),
    ("settings", "⚙️ 管理设置"),
)

PAGE_KEYS = {key for key, _ in PAGES} | {"batch_detail"}


def go(page: str) -> None:
    st.session_state["nav_page"] = page
    st.rerun()


def _render_sidebar(page: str) -> None:
    with st.sidebar:
        st.markdown("## 🧭 管理中心")
        st.caption("按批次、团队和类别查看真实数据。")
        for key, label in PAGES:
            active = key == page
            if st.button(
                label,
                key=f"mgmt_nav_{key}",
                use_container_width=True,
                type="primary" if active else "secondary",
            ):
                go(key)
        st.divider()
        if st.button("← 返回正式质检", use_container_width=True, key="mgmt_nav_back"):
            go("acceptance")


def render_management_page(page: str) -> None:
    if page not in PAGE_KEYS:
        page = "workbench"
    _render_sidebar(page)

    if page == "workbench":
        from ui.pages import management_home
        from modules.batch_management.service import BatchService
        from modules.workspace.service import WorkspaceService

        service = BatchService()
        workspace = WorkspaceService()
        try:
            management_home.render_workbench(service, workspace)
        finally:
            workspace.close()
            service.close()
    elif page == "batch_overview":
        from ui.pages import management_home
        from modules.batch_management.service import BatchService

        service = BatchService()
        try:
            management_home.render_batch_overview(service)
        finally:
            service.close()
    elif page == "team_category":
        from ui.pages import management_home
        from modules.batch_management.service import BatchService

        service = BatchService()
        try:
            management_home.render_team_category(service)
        finally:
            service.close()
    elif page == "batch_detail":
        from ui.pages import management_home
        from modules.batch_management.service import BatchService
        from modules.workspace.service import WorkspaceService

        service = BatchService()
        workspace = WorkspaceService()
        try:
            management_home.render_batch_detail(service, workspace)
        finally:
            workspace.close()
            service.close()
    elif page == "scan":
        from ui.pages import scan

        scan.render()
    elif page == "batches":
        from ui.pages import batches

        batches.render()
    elif page == "format":
        from ui.pages import format_check

        format_check.render()
    elif page == "precheck":
        from ui.pages import precheck

        precheck.render()
    elif page == "record_import":
        from ui.pages import record_import

        record_import.render()
    elif page == "archive":
        from ui.pages import archive

        archive.render()
    elif page == "stats":
        from ui.pages import stats

        stats.render()
    elif page == "settings":
        from ui.pages import settings

        settings.render()
    else:
        st.warning(f"未知页面：{page}")
