"""编号查询（v1.25.11 P6）。

只查管理数据库：编号 → 批次/位置/验收记录/格式检查/翻译历史。
不遍历源目录，不影响当前验收，不修改任何文件。
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd
import streamlit as st

from modules.config import app_config
from modules.database.connection import open_database
from modules.workspace.models import SOURCE_LABELS
from ui.pages.catalog_workbench import _CSS, _esc, _go

_NUMBERS = re.compile(r"[,\s，、;；]+")


def _parse(text: str) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for item in _NUMBERS.split(text or ""):
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        items.append(value)
    return items[:50]


def _locations(db, numbers: list[str]) -> list[dict[str, Any]]:
    if not numbers:
        return []
    marks = ",".join("?" for _ in numbers)
    rows = db.connect().execute(
        f"""
        SELECT g.task_id AS number, g.path AS group_path, g.status AS group_status,
               g.image_count, g.txt_count,
               b.id AS batch_id, b.name AS batch_name, b.batch_path,
               b.category_l1, b.category_l2, b.category_l3,
               b.team_name, b.submit_date, b.source_type,
               b.main_status, b.format_status, b.translation_status
        FROM catalog_groups g
        JOIN catalog_batches b ON b.id = g.batch_id
        WHERE g.task_id IN ({marks})
        ORDER BY g.task_id, b.name
        """,
        numbers,
    ).fetchall()
    return [dict(row) for row in rows]


def _record_history(db, numbers: list[str]) -> list[dict[str, Any]]:
    if not numbers:
        return []
    marks = ",".join("?" for _ in numbers)
    rows = db.connect().execute(
        f"""
        SELECT r.number, r.result, r.reason, r.tags, r.evidence, r.record_time,
               r.reviewer_name, r.source_file,
               b.id AS batch_id, b.name AS batch_name, b.batch_path,
               b.source_type
        FROM catalog_records r
        LEFT JOIN catalog_batches b ON b.id = r.batch_id
        WHERE r.number IN ({marks})
        ORDER BY r.record_time DESC, r.created_at DESC
        """,
        numbers,
    ).fetchall()
    return [dict(row) for row in rows]


def _format_history(db, numbers: list[str]) -> list[dict[str, Any]]:
    if not numbers:
        return []
    marks = ",".join("?" for _ in numbers)
    rows = db.connect().execute(
        f"""
        SELECT fr.number, fr.expected_images, fr.actual_images, fr.status,
               fr.reason, fr.created_at,
               b.id AS batch_id, b.name AS batch_name
        FROM catalog_format_results fr
        LEFT JOIN catalog_batches b ON b.id = fr.batch_id
        WHERE fr.number IN ({marks})
        ORDER BY fr.created_at DESC
        """,
        numbers,
    ).fetchall()
    return [dict(row) for row in rows]


def _translation_history(db, numbers: list[str]) -> list[dict[str, Any]]:
    if not numbers:
        return []
    marks = ",".join("?" for _ in numbers)
    rows = db.connect().execute(
        f"""
        SELECT ti.number, ti.action, ti.status, ti.message, ti.en_path, ti.created_at,
               b.id AS batch_id, b.name AS batch_name
        FROM catalog_translation_items ti
        LEFT JOIN catalog_batches b ON b.id = ti.batch_id
        WHERE ti.number IN ({marks})
        ORDER BY ti.created_at DESC
        """,
        numbers,
    ).fetchall()
    return [dict(row) for row in rows]


def _location_label(row: dict[str, Any]) -> str:
    parts = [
        row.get("category_l1"),
        row.get("category_l2"),
        row.get("category_l3"),
        row.get("team_name"),
        row.get("submit_date"),
        row.get("batch_name"),
    ]
    return " / ".join(str(item) for item in parts if item)


def render() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="vk-hero"><h2>🔎 编号查询</h2>'
        "<p>输入编号查位置、验收判定和历史记录。只读管理数据库，不遍历文件夹，不影响当前验收。</p></div>",
        unsafe_allow_html=True,
    )
    db = open_database(app_config.get_db_path())
    try:
        c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
        text = c1.text_area(
            "编号（多个用逗号、空格或换行分隔）",
            height=90,
            key="catalog_query_text",
            placeholder="例如：900001, 31105001",
        )
        search = c2.button("查询", type="primary", use_container_width=True)
        numbers = _parse(text)
        if search:
            st.session_state["catalog_query_numbers"] = numbers
        numbers = st.session_state.get("catalog_query_numbers") or numbers
        if not numbers:
            st.caption("输入编号后点“查询”。最多一次查询 50 个编号。")
            return

        st.caption(f"本次查询 {len(numbers)} 个编号：{'、'.join(numbers)}")
        locations = _locations(db, numbers)
        found = {row["number"] for row in locations}
        missing = [item for item in numbers if item not in found]
        if missing:
            st.warning("库中没有这些编号：" + "、".join(missing))

        if locations:
            st.subheader(f"位置（{len(locations)}）")
            for row in locations:
                with st.container(border=True):
                    c3, c4 = st.columns([4, 1], vertical_alignment="center")
                    c3.markdown(
                        f'<div class="vk-card-title">{_esc(row["number"])}</div>'
                        f'<div class="vk-card-sub">{_esc(_location_label(row))}</div>',
                        unsafe_allow_html=True,
                    )
                    c3.caption(
                        f"来源：{SOURCE_LABELS.get(str(row.get('source_type') or ''), row.get('source_type') or '—')} · "
                        f"编号状态：{row.get('group_status') or 'pending'} · "
                        f"图片 {int(row.get('image_count') or 0)} · TXT {int(row.get('txt_count') or 0)}"
                    )
                    c3.caption(f"编号文件夹：`{row.get('group_path') or '—'}`")
                    c3.caption(f"批次目录：`{row.get('batch_path') or '—'}`")
                    if c4.button(
                        "进入批次",
                        key=f"catalog_query_open_{row['number']}_{row['batch_id']}",
                        use_container_width=True,
                    ):
                        st.session_state["catalog_batch_id"] = row["batch_id"]
                        _go("catalog_batch_detail")

            with st.expander(f"位置明细表（{len(locations)}）", expanded=False):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "编号": row["number"],
                                "大类": row.get("category_l1") or "",
                                "小类": row.get("category_l2") or "",
                                "三级类": row.get("category_l3") or "",
                                "团队": row.get("team_name") or "",
                                "日期": row.get("submit_date") or "",
                                "批次": row.get("batch_name") or "",
                                "编号状态": row.get("group_status") or "",
                            }
                            for row in locations
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

        records = _record_history(db, numbers)
        st.subheader(f"验收历史（{len(records)}）")
        if records:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "编号": row["number"],
                            "判定": row.get("result") or "",
                            "原因": row.get("reason") or "",
                            "标签": row.get("tags") or "",
                            "验收员": row.get("reviewer_name") or "",
                            "质检时间": row.get("record_time") or "",
                            "批次": row.get("batch_name") or "",
                        }
                        for row in records
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
            with st.expander("查看截图与源文件（只读）", expanded=False):
                for row in records:
                    st.caption(
                        f"{row['number']} · {row.get('result') or '未填写'} · "
                        f"截图：`{row.get('evidence') or '—'}` · "
                        f"源文件：`{row.get('source_file') or '—'}`"
                    )
        else:
            st.caption("数据库里还没有这些编号的验收记录。")

        format_rows = _format_history(db, numbers)
        if format_rows:
            with st.expander(f"格式检查历史（{len(format_rows)}）", expanded=False):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "编号": row["number"],
                                "批次": row.get("batch_name") or "",
                                "期望图片": row.get("expected_images"),
                                "实际图片": row.get("actual_images"),
                                "结果": row.get("status") or "",
                                "说明": row.get("reason") or "",
                                "时间": row.get("created_at") or "",
                            }
                            for row in format_rows
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

        translation_rows = _translation_history(db, numbers)
        if translation_rows:
            with st.expander(f"翻译历史（{len(translation_rows)}）", expanded=False):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "编号": row["number"],
                                "批次": row.get("batch_name") or "",
                                "动作": row.get("action") or "",
                                "结果": row.get("status") or "",
                                "说明": row.get("message") or "",
                                "英文路径": row.get("en_path") or "",
                                "时间": row.get("created_at") or "",
                            }
                            for row in translation_rows
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
    finally:
        db.close()
