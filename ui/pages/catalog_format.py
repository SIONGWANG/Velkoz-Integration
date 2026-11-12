"""批次格式检查（v1.25.11 P4）。

按大类/小类/三级类绑定的图片数量规则，批量检查批次；
页面只显示总数/成功/失败，明细导出 Excel；与库中合格编号查重。
全程只读源目录，不修改图片/TXT/CSV。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from modules.config import app_config
from modules.database.connection import open_database
from modules.workspace.catalog_format import (
    check_batches,
    export_excel,
    resolve_rule,
    category_rules,
)
from modules.workspace.catalog_service import FORMAT_FAILED, CatalogService
from modules.workspace.models import SOURCE_FINAL, SOURCE_LABELS, SOURCE_PENDING
from ui.pages.catalog_workbench import _CSS, _esc, _go, _projects

SOURCE_CHOICES = (
    (SOURCE_PENDING, SOURCE_LABELS[SOURCE_PENDING]),
    (SOURCE_FINAL, SOURCE_LABELS[SOURCE_FINAL]),
)


def _pick_key(batch_id: str) -> str:
    return f"catalog_format_pick_{batch_id}"


def _select_all(rows: list[dict[str, Any]], value: bool) -> None:
    for row in rows:
        st.session_state[_pick_key(str(row["id"]))] = value


def _render_filters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    l1_values = sorted({str(row.get("category_l1") or "未分类") for row in rows})
    team_values = sorted({str(row.get("team_name") or "未识别团队") for row in rows})
    status_values = ["全部", "未检查", "已检查", "检查失败"]
    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.2, 2])
    l1 = c1.selectbox("大类", ["全部", *l1_values], key="catalog_format_l1")
    team = c2.selectbox("团队", ["全部", *team_values], key="catalog_format_team")
    status = c3.selectbox("检查状态", status_values, key="catalog_format_status")
    keyword = c4.text_input("搜索批次、编号", key="catalog_format_keyword").strip()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if l1 != "全部" and str(row.get("category_l1") or "未分类") != l1:
            continue
        if team != "全部" and str(row.get("team_name") or "未识别团队") != team:
            continue
        fmt = str(row.get("format_status") or "not_checked")
        if status == "未检查" and fmt == "checked":
            continue
        if status == "已检查" and fmt != "checked":
            continue
        if status == "检查失败" and fmt != FORMAT_FAILED:
            continue
        if keyword:
            haystack = f"{row.get('name') or ''} {row.get('category_l1') or ''} " \
                       f"{row.get('category_l2') or ''} {row.get('category_l3') or ''} " \
                       f"{row.get('batch_path') or ''}"
            if keyword.casefold() not in haystack.casefold():
                continue
        filtered.append(row)
    return filtered


def _render_batch_cards(rows: list[dict[str, Any]]) -> list[str]:
    selected: list[str] = []
    columns = st.columns(3, gap="medium")
    for index, row in enumerate(
        sorted(rows, key=lambda item: (item.get("submit_date") or "", item.get("name") or ""))
    ):
        batch_id = str(row["id"])
        with columns[index % 3]:
            with st.container(border=True):
                st.markdown(
                    f'<div class="vk-card-title">{_esc(row.get("name") or "未命名批次")}</div>'
                    f'<div class="vk-card-sub">{_esc(row.get("team_name") or "未识别团队")} · '
                    f'{_esc(row.get("submit_date") or "日期未知")} · '
                    f'{int(row.get("data_count") or 0)} 条编号</div>',
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"大类：{row.get('category_l1') or '—'} · 小类：{row.get('category_l2') or '—'} · "
                    f"三级类：{row.get('category_l3') or '—'}"
                )
                st.caption(
                    f"检查：{row.get('format_status_label') or '未检查'} · "
                    f"主状态：{row.get('main_status_label') or '新提交'}"
                )
                if st.checkbox("选择此批次", key=_pick_key(batch_id)):
                    selected.append(batch_id)
    return selected


def _render_summary(summary: dict[str, Any]) -> None:
    st.markdown("**本次检查结果**")
    m = st.columns(4)
    m[0].metric("批次总数", summary.get("batches", 0))
    m[1].metric("全部通过", summary.get("checked", 0))
    m[2].metric("检查失败", summary.get("failed", 0))
    m[3].metric("未配置规则", summary.get("skipped", 0))
    n = st.columns(4)
    n[0].metric("编号总数", summary.get("total", 0))
    n[1].metric("通过编号", summary.get("passed", 0))
    n[2].metric("失败编号", summary.get("failed_groups", 0))
    n[3].metric("重复编号", summary.get("duplicates", 0))

    results = summary.get("results") or []
    if results:
        frame = pd.DataFrame(
            [
                {
                    "批次": item.get("batch_name") or "",
                    "规则": (
                        f"{item['rule']['source']}：{item['rule']['category']} "
                        f"（{item['rule']['expected']} 张）"
                        if item.get("rule", {}).get("configured")
                        else "未配置"
                    ),
                    "编号数": item.get("total", 0),
                    "通过": item.get("passed", 0),
                    "失败": item.get("failed", 0),
                    "未判定": item.get("skipped", 0),
                    "重复": item.get("duplicates", 0),
                    "结果": "检查失败" if item.get("status") == FORMAT_FAILED else "已检查",
                    "说明": item.get("message") or "",
                }
                for item in results
            ]
        )
        with st.expander(f"批次汇总（{len(results)}）", expanded=True):
            st.dataframe(frame, use_container_width=True, hide_index=True)

        duplicated: list[dict[str, Any]] = []
        for item in results:
            for hit in item.get("duplicate_hits") or []:
                duplicated.append(
                    {
                        "批次": item.get("batch_name") or "",
                        "编号": hit.get("number") or "",
                        "库中批次": hit.get("other_batch_name") or "",
                        "库中验收员": hit.get("other_reviewer") or "",
                    }
                )
        if duplicated:
            st.warning(
                f"发现 {len(duplicated)} 个编号已在库中合格。"
                "已写入 Excel 的“重复编号”工作表，请人工确认是否重复提交。"
            )
            with st.expander(f"重复编号（{len(duplicated)}）", expanded=False):
                st.dataframe(
                    pd.DataFrame(duplicated),
                    use_container_width=True,
                    hide_index=True,
                )


def render() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="vk-hero"><h2>🧮 批次格式检查</h2>'
        "<p>按大类/小类/三级类绑定的图片数量规则批量检查批次。"
        "页面只显示总数/成功/失败，编号明细导出 Excel；全程只读，不修改源文件。</p></div>",
        unsafe_allow_html=True,
    )

    db = open_database(app_config.get_db_path())
    try:
        service = CatalogService(db)
        projects = _projects(db)
        if not projects:
            st.info("还没有业务目录。先到设置页添加项目根目录和待验/终验目录。")
            return
        options = {str(item["id"]): item for item in projects}
        labels = {
            str(item["id"]): f"{item['project_name']}{'（已停用）' if not item['enabled'] else ''}"
            for item in projects
        }
        c1, c2 = st.columns([1.2, 2.2], vertical_alignment="center")
        project_id = c1.selectbox(
            "项目",
            options=list(options),
            format_func=lambda value: labels[value],
            key="catalog_format_project",
        )
        project = options[project_id]
        chosen_label = c2.radio(
            "来源",
            [label for _, label in SOURCE_CHOICES],
            horizontal=True,
            key="catalog_format_source",
            label_visibility="collapsed",
        )
        source_type = next(key for key, label in SOURCE_CHOICES if label == chosen_label)
        root = project.get("pending_path") if source_type == SOURCE_PENDING else project.get("final_path")

        rows = service.batches(project_id=project_id, source_type=source_type)
        if not rows:
            st.info("这个来源还没有索引。先回到工作台点“刷新目录索引”。")
            if st.button("打开验收工作台", type="primary", use_container_width=True):
                _go("workbench")
            return

        rules = category_rules(service)
        missing_rule = [
            row for row in rows if not resolve_rule(row, rules).configured
        ]
        toolbar = st.columns([1.4, 1.2, 1.8, 3], vertical_alignment="center")
        filtered = _render_filters(rows)
        if toolbar[0].button("全选当前筛选", key="catalog_format_all", use_container_width=True):
            _select_all(filtered, True)
            st.rerun()
        if toolbar[1].button("清空选择", key="catalog_format_none", use_container_width=True):
            _select_all(rows, False)
            st.rerun()
        if toolbar[2].button("设置类目图片数", key="catalog_format_settings", use_container_width=True):
            _go("settings")
        toolbar[3].caption(
            f"只读根目录：`{root or '未配置'}` · 报告写到管理数据目录，不写入源目录。"
        )
        if missing_rule:
            st.warning(
                f"有 {len(missing_rule)} 个批次的大类/小类/三级类还没有图片数量规则，"
                "检查会记为“未判定”。请到设置 → 类目层级填写图片数。"
            )

        if not filtered:
            st.caption("当前筛选没有批次。")
            return
        st.caption(f"当前筛选 {len(filtered)} 个批次；勾选后批量检查。")
        selected = _render_batch_cards(filtered)
        selected_set = {str(item) for item in selected}

        st.divider()
        operator = st.text_input(
            "操作员（写入检查日志）",
            value=str(st.session_state.get("operator_name") or ""),
            key="catalog_format_operator",
        )
        summary = st.session_state.get("catalog_format_summary")
        action = st.columns([1.6, 1.2, 3], vertical_alignment="center")
        if action[0].button(
            f"开始检查所选（{len(selected)}）",
            key="catalog_format_run",
            type="primary",
            use_container_width=True,
            disabled=not selected,
        ):
            with st.spinner("正在按类目规则检查编号文件夹，只读图片数量…"):
                result = check_batches(
                    db,
                    selected,
                    operator=str(st.session_state.get("catalog_format_operator") or ""),
                )
                st.session_state["catalog_format_summary"] = result.to_dict()
                st.session_state.pop("catalog_format_export_path", None)
            st.rerun()
        if summary:
            if action[1].button("导出 Excel 明细", key="catalog_format_export", use_container_width=True):
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                out = app_config.get_reports_dir() / f"批次格式检查_{stamp}.xlsx"
                try:
                    path = export_excel(
                        db,
                        _summary_object(summary),
                        str(out),
                        source_root=str(root or ""),
                    )
                    st.session_state["catalog_format_export_path"] = path
                    st.success(f"已导出：{path}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"导出失败：{exc}")
            export_path = str(st.session_state.get("catalog_format_export_path") or "")
            if export_path and Path(export_path).is_file():
                with open(export_path, "rb") as handle:
                    st.download_button(
                        "⬇️ 下载 Excel",
                        data=handle.read(),
                        file_name=Path(export_path).name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="catalog_format_download",
                        use_container_width=True,
                    )
            action[2].caption("明细包含：批次汇总、检查明细、重复编号对比。")
            _render_summary(summary)
        else:
            action[2].caption("勾选批次后点“开始检查所选”。未配置规则的类目不会判失败。")
    finally:
        db.close()


def _summary_object(summary: dict[str, Any]):
    """把页面保存的摘要还原为导出对象。"""
    from modules.workspace.catalog_format import BatchCheckResult, FormatRule, FormatSummary, GroupCheck

    restored = FormatSummary(
        batches=int(summary.get("batches") or 0),
        checked=int(summary.get("checked") or 0),
        failed=int(summary.get("failed") or 0),
        skipped=int(summary.get("skipped") or 0),
        total=int(summary.get("total") or 0),
        passed=int(summary.get("passed") or 0),
        failed_groups=int(summary.get("failed_groups") or 0),
        duplicates=int(summary.get("duplicates") or 0),
    )
    for item in summary.get("results") or []:
        rule_data = item.get("rule") or {}
        result = BatchCheckResult(
            batch_id=str(item.get("batch_id") or ""),
            batch_name=str(item.get("batch_name") or ""),
            batch_path=str(item.get("batch_path") or ""),
            category_l1=str(item.get("category_l1") or ""),
            category_l2=str(item.get("category_l2") or ""),
            category_l3=str(item.get("category_l3") or ""),
            rule=FormatRule(
                expected=int(rule_data.get("expected") or 0),
                source=str(rule_data.get("source") or "未配置"),
                category=str(rule_data.get("category") or ""),
            ),
            total=int(item.get("total") or 0),
            passed=int(item.get("passed") or 0),
            failed=int(item.get("failed") or 0),
            skipped=int(item.get("skipped") or 0),
            duplicates=int(item.get("duplicates") or 0),
            status=str(item.get("status") or ""),
            message=str(item.get("message") or ""),
            duplicate_hits=list(item.get("duplicate_hits") or []),
        )
        for detail in item.get("details") or []:
            result.details.append(
                GroupCheck(
                    number=str(detail.get("number") or ""),
                    group_path=str(detail.get("group_path") or ""),
                    expected=int(detail.get("expected") or 0),
                    actual=int(detail.get("actual") or 0),
                    txt_count=int(detail.get("txt_count") or 0),
                    status=str(detail.get("status") or ""),
                    reason=str(detail.get("reason") or ""),
                )
            )
        restored.results.append(result)
    return restored

