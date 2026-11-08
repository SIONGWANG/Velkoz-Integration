"""1.25.x 管理平台页面路由。

默认页仍是正式质检；只有用户从“更多设置”主动进入管理页面时才切换。
"""
from __future__ import annotations

import streamlit as st


def go(page: str) -> None:
    st.session_state["nav_page"] = page
    st.rerun()


def render_management_entry(key_prefix: str = "mgmt") -> None:
    """放在旧界面“更多设置”里的入口，不改变三栏布局。"""
    st.markdown("**1.25 管理平台（预览）**")
    st.caption("只读扫描、候选确认与规则诊断；不修改源图片/TXT/CSV。")
    col1, col2, col3 = st.columns(3)
    if col1.button("📂 数据扫描", key=f"{key_prefix}_open_scan", use_container_width=True):
        go("scan")
    if col2.button("🧩 候选确认", key=f"{key_prefix}_open_batches", use_container_width=True):
        go("batches")
    if col3.button("⚙️ 管理设置", key=f"{key_prefix}_open_settings", use_container_width=True):
        go("settings")


def render_management_page(page: str) -> None:
    if st.button("← 返回正式质检", key="nav_back_acceptance"):
        go("acceptance")

    if page == "scan":
        from ui.pages import scan

        scan.render()
    elif page == "batches":
        from ui.pages import batches

        batches.render()
    elif page == "settings":
        from ui.pages import settings

        settings.render()
    else:
        st.warning(f"未知页面：{page}")
        if st.button("返回正式质检"):
            go("acceptance")
