"""1.25.x 管理平台页面路由与导航。

管理平台已从“更多设置”中的入口独立出来，默认进入卡片式验收工作台；
默认正式质检三栏布局不变。所有管理页面只读或只写管理数据库，
不修改源图片/TXT/CSV。
"""
from __future__ import annotations

import streamlit as st

# 主入口保留 5 个；高级工具只保留新中心页面；旧页面隐藏但深链接仍可用。
PRIMARY_PAGES: tuple[tuple[str, str], ...] = (
    ("workbench", "🏠 验收工作台"),
    ("catalog_query", "🔎 编号查询"),
    ("record_import", "📥 记录导入"),
    ("stats", "📊 数据统计"),
    ("settings", "⚙️ 设置"),
)

ADVANCED_PAGES: tuple[tuple[str, str], ...] = (
    ("catalog_sync", "🗂 目录索引"),
    ("catalog_format", "🧮 批次格式检查"),
    ("catalog_translation", "🌐 批次翻译"),
    ("archive", "🗄️ 合格归档"),
)

# 旧入口先隐藏，代码保留；深链接仍然可用，等新功能对齐后再删除。
HIDDEN_PAGES: tuple[tuple[str, str], ...] = (
    ("batch_overview", "📦 批次总览"),
    ("team_category", "👥 团队与类别"),
    ("scan", "📂 数据扫描"),
    ("batches", "🧩 候选确认"),
    ("format", "🔎 格式检查"),
    ("precheck", "🌐 翻译预审"),
)

PAGES: tuple[tuple[str, str], ...] = (*PRIMARY_PAGES, *ADVANCED_PAGES, *HIDDEN_PAGES)

PAGE_KEYS = {key for key, _ in PAGES} | {"batch_detail", "catalog_batch_detail"}


def go(page: str) -> None:
    st.session_state["nav_page"] = page
    st.rerun()


def _render_sidebar(page: str) -> None:
    with st.sidebar:
        st.markdown("## 🧭 管理中心")
        st.caption("批次、团队、类目都在工作台里；路径只配置一次。")
        for key, label in PRIMARY_PAGES:
            active = key == page or (key == "workbench" and page == "catalog_batch_detail")
            if st.button(
                label,
                key=f"mgmt_nav_{key}",
                use_container_width=True,
                type="primary" if active else "secondary",
            ):
                go(key)
        advanced_active = page in {key for key, _ in ADVANCED_PAGES}
        with st.expander("🧰 高级工具", expanded=advanced_active):
            st.caption("单批次操作已收进批次详情；这里保留独立页面入口。")
            for key, label in ADVANCED_PAGES:
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
        from ui.pages import catalog_workbench

        catalog_workbench.render()
    elif page == "catalog_batch_detail":
        from ui.pages import catalog_batch

        batch_id = str(st.session_state.get("catalog_batch_id") or "")
        if not batch_id:
            st.warning("没有选择批次。")
            if st.button("返回工作台", type="primary"):
                go("workbench")
        else:
            catalog_batch.render(batch_id)
    elif page == "catalog_sync":
        from ui.pages import catalog_sync

        catalog_sync.render()
    elif page == "catalog_query":
        from ui.pages import catalog_query

        catalog_query.render()
    elif page == "catalog_format":
        from ui.pages import catalog_format

        catalog_format.render()
    elif page == "catalog_translation":
        from ui.pages import catalog_translation

        catalog_translation.render()
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
