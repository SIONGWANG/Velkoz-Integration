"""标签与类目设置（v1.25.11）。"""
from __future__ import annotations

import streamlit as st

from modules.config import app_config
from modules.database.connection import open_database
from modules.workspace.catalog_service import CatalogService


def _tag_section(service: CatalogService, kind: str, title: str, key: str) -> None:
    st.markdown(f"**{title}**")
    rows = service.teams() if kind == "team" else service.reviewers()
    table = "catalog_teams" if kind == "team" else "catalog_reviewers"
    if rows:
        for row in rows:
            c1, c2, c3, c4 = st.columns([3, 3, 1, 1])
            c1.write(row["name"])
            c2.caption(row["aliases"] or "—")
            c3.caption("启用" if row["enabled"] else "停用")
            if c4.button("删除", key=f"cat_del_{key}_{row['id']}"):
                service.delete_tag(table, row["id"])
                st.rerun()
    else:
        st.caption("还没有标签。")

    with st.form(f"cat_add_{key}", clear_on_submit=True):
        c1, c2 = st.columns([2, 2])
        name = c1.text_input("名称", key=f"cat_name_{key}", placeholder="例如 同正")
        aliases = c2.text_input("别名（逗号分隔）", key=f"cat_alias_{key}")
        submitted = st.form_submit_button("添加 / 更新")
        if submitted:
            try:
                service.save_tag(table, name, aliases=aliases)
                st.success(f"已保存：{name.strip()}")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def _category_section(service: CatalogService) -> None:
    st.markdown("**类目层级**")
    st.caption("大类必填；小类、三级类可空。没有小类/三级类的批次不会被误判。")
    rows = service.categories()
    st.caption("图片数用于批次格式检查：三级类优先，其次小类，最后大类；0 表示未配置。")
    if rows:
        for row in rows:
            indent = "　" * max(0, int(row["level"]) - 1)
            c1, c2, c3, c4, c5 = st.columns([4, 3, 1.2, 1, 1])
            c1.write(f"{indent}{row['name']}")
            c2.caption(
                f"{int(row['level'])} 级 · 图片 {int(row['image_count'] or 0)} 张"
                + ("" if row["enabled"] else " · 停用")
            )
            image_count = c3.number_input(
                "图片数",
                min_value=0,
                max_value=99,
                value=int(row["image_count"] or 0),
                key=f"cat_img_{row['id']}",
                label_visibility="collapsed",
            )
            if c4.button("保存", key=f"cat_save_{row['id']}"):
                service.save_category(
                    row["name"],
                    int(row["level"]),
                    parent_id=row.get("parent_id") or "",
                    image_count=int(image_count),
                    rule_set_id=row.get("rule_set_id") or "",
                    enabled=bool(row["enabled"]),
                    category_id=row["id"],
                )
                st.success(f"已保存：{row['name']} · {int(image_count)} 张")
                st.rerun()
            if c5.button("停用" if row["enabled"] else "启用", key=f"cat_toggle_{row['id']}"):
                service.save_category(
                    row["name"],
                    int(row["level"]),
                    parent_id=row.get("parent_id") or "",
                    image_count=int(row["image_count"] or 0),
                    rule_set_id=row.get("rule_set_id") or "",
                    enabled=not bool(row["enabled"]),
                    category_id=row["id"],
                )
                st.rerun()
    else:
        st.caption("还没有类目。")

    with st.form("cat_add_category", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([3, 1, 2, 1])
        name = c1.text_input("类目名称", key="cat_new_name")
        level = c2.selectbox("层级", [1, 2, 3], key="cat_new_level", format_func=lambda x: f"{x} 级")
        parents = [row for row in rows if int(row["level"]) == int(level) - 1]
        parent_options = {"": "无（作为顶层）", **{row["id"]: row["name"] for row in parents}}
        parent_id = c3.selectbox(
            "上级类目",
            options=list(parent_options),
            format_func=lambda value: parent_options[value],
            key="cat_new_parent",
        )
        image_count = c4.number_input("图片数", min_value=0, max_value=99, value=0, key="cat_new_images")
        submitted = st.form_submit_button("添加 / 更新类目")
        if submitted:
            try:
                service.save_category(
                    name,
                    int(level),
                    parent_id=parent_id,
                    image_count=int(image_count),
                )
                st.success(f"已保存类目：{name.strip()}")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def render() -> None:
    db = open_database(app_config.get_db_path())
    try:
        service = CatalogService(db)
        st.subheader("团队、验收员与类目")
        st.caption(
            "团队/验收员用于从路径和 _质检记录 文件夹名匹配；类目用于卡片层级和格式检查规则。"
            "这些配置只写入管理数据库。"
        )
        tab_team, tab_reviewer, tab_category = st.tabs(["团队标签", "验收员标签", "类目层级"])
        with tab_team:
            _tag_section(service, "team", "团队标签", "team")
        with tab_reviewer:
            _tag_section(service, "reviewer", "验收员标签", "reviewer")
        with tab_category:
            _category_section(service)

        st.divider()
        st.markdown("**忽略规则**")
        st.caption("每行一个关键词，路径或批次名包含它就跳过，例如：试标数据、60。")
        current = "\n".join(service.ignore_patterns())
        value = st.text_area("忽略关键词", value=current, key="catalog_ignore_text", height=90)
        if st.button("保存忽略规则", key="catalog_ignore_save"):
            saved = service.save_ignore_patterns(value.splitlines())
            st.success(f"已保存 {len(saved)} 条忽略规则。")
            st.rerun()
    finally:
        db.close()
