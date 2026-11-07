"""数据扫描页面（1.25.0 预览）。"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from modules.common.timeutil import format_duration
from modules.config import app_config
from modules.scanning.diagnostics import REASON_TEXT, summarize
from modules.scanning.rules import RuleSet, load_default_ruleset, parse_ruleset_json, validate_ruleset
from modules.scanning.scanner import ScanOptions, scan_root
from modules.scanning.service import ScanService


def _fmt_identity(identity: dict) -> str:
    return " / ".join(f"{key}={value}" for key, value in identity.items())


def _candidate_rows(result) -> list[dict]:
    rows = []
    for item in result.candidates:
        rows.append(
            {
                "状态": item.status,
                "批次": item.batch_name,
                "路径": item.batch_path,
                "数据组": item.group_count,
                "文件数": item.file_count,
                "置信度": round(item.confidence, 2),
                "身份": _fmt_identity(item.identity),
                "原因": "、".join(REASON_TEXT.get(code, code) for code in item.reasons),
                "说明": item.explain,
            }
        )
    return rows


def _group_rows(result) -> list[dict]:
    rows = []
    for item in result.groups:
        rows.append(
            {
                "任务ID": item.task_id,
                "路径": item.path,
                "图片": item.image_count,
                "TXT": item.txt_count,
                "文件": item.file_count,
                "中文TXT": item.txt_zh or "",
                "英文TXT": item.txt_en or "",
                "团队": item.user_name,
                "置信度": round(item.match.confidence, 2) if item.match else 0,
            }
        )
    return rows


def _issue_rows(result) -> list[dict]:
    return [
        {
            "原因码": issue.code,
            "说明": REASON_TEXT.get(issue.code, issue.message),
            "路径": issue.path,
            "详情": json.dumps(issue.detail, ensure_ascii=False),
        }
        for issue in result.issues
    ]


def _load_selected_ruleset(service: ScanService) -> tuple[RuleSet | None, str]:
    rulesets = service.list_rule_sets()
    mode = st.radio(
        "规则来源",
        ["数据库方案", "从 JSON 导入", "内置默认"],
        horizontal=True,
        key="scan_ruleset_mode",
    )
    if mode == "从 JSON 导入":
        uploaded = st.file_uploader("选择规则 JSON", type=["json"], key="scan_ruleset_file")
        text = st.text_area(
            "或直接粘贴规则 JSON",
            height=160,
            key="scan_ruleset_text",
            placeholder='{"id": "...", "name": "...", "layers": [...]}',
        )
        raw = ""
        if uploaded is not None:
            raw = uploaded.getvalue().decode("utf-8-sig")
        elif text.strip():
            raw = text
        if not raw.strip():
            return None, "请上传或粘贴规则 JSON"
        try:
            ruleset = parse_ruleset_json(raw)
            return ruleset, f"导入规则：{ruleset.name}"
        except ValueError as exc:
            st.error(str(exc))
            return None, ""
    if mode == "内置默认":
        return load_default_ruleset(), "内置默认规则"

    if not rulesets:
        default = service.default_ruleset()
        return default, f"内置默认：{default.name}"
    labels = {item["id"]: f"{item['name']}（v{item.get('active_version') or '?'}）" for item in rulesets}
    chosen = st.selectbox(
        "选择规则方案",
        options=list(labels.keys()),
        format_func=lambda key: labels[key],
        key="scan_ruleset_id",
    )
    loaded = service.get_rule_set(chosen)
    if loaded is None:
        st.error("规则方案读取失败")
        return None, ""
    return loaded, labels[chosen]


def render() -> None:
    st.title("📂 数据扫描（1.25.0 预览）")
    st.caption("只读扫描：只读取目录名、文件名、大小和修改时间，不修改源图片/TXT/CSV。")

    paths = app_config.describe_paths()
    with st.expander("数据目录与规则说明", expanded=False):
        st.write(f"管理数据目录：`{paths['data_dir']}`")
        st.write(f"管理数据库：`{paths['db_path']}`")
        st.write("默认规则方案会把所有组织层视为可选，只要求任务层（8 位 ID 或迁移后的旧规则）匹配。")
        st.write("扫描结果先作为预览；保存只写管理数据库，不确认批次、不写 data_groups。")

    try:
        service = ScanService()
    except Exception as exc:
        st.error(f"管理数据库初始化失败：{exc}")
        return
    try:
        ruleset, ruleset_label = _load_selected_ruleset(service)
        if ruleset:
            errors = validate_ruleset(ruleset)
            if errors:
                st.error("规则校验失败：" + "；".join(errors))
                ruleset = None
            else:
                st.caption(f"当前规则：{ruleset_label}")

        col_root, col_operator = st.columns([3, 1])
        default_root = st.session_state.get("scan_root") or st.session_state.get("root_path", "")
        root = col_root.text_input("扫描根目录", value=default_root, key="scan_root_input")
        operator = col_operator.text_input("操作员", value=st.session_state.get("operator_name", ""), key="scan_operator")

        with st.expander("扫描选项", expanded=False):
            c1, c2, c3 = st.columns(3)
            max_dirs = c1.number_input("最大目录数", min_value=100, max_value=1_000_000, value=20000, step=1000)
            max_groups = c2.number_input("最大数据组", min_value=100, max_value=1_000_000, value=10000, step=1000)
            max_depth = c3.number_input("最大深度", min_value=1, max_value=64, value=12)
            count_fingerprint = st.checkbox("计算目录轻量指纹（名称+大小+mtime）", value=True)
            include_hidden = st.checkbox("包含隐藏目录", value=False)

        if st.button("开始只读预览", type="primary", disabled=ruleset is None, use_container_width=True):
            if not root.strip():
                st.error("请填写扫描根目录")
            else:
                options = ScanOptions(
                    max_dirs=int(max_dirs),
                    max_groups=int(max_groups),
                    max_depth=int(max_depth),
                    count_fingerprint=bool(count_fingerprint),
                    include_hidden=bool(include_hidden),
                )
                progress_bar = st.progress(0, text="准备扫描…")

                def on_progress(done: int, total: int, message: str) -> None:
                    pct = min(0.99, done / max(total, 1))
                    progress_bar.progress(pct, text=message)

                with st.spinner("正在只读扫描…"):
                    result = scan_root(root.strip(), ruleset, options=options, progress=on_progress)
                progress_bar.progress(1.0, text="扫描完成")
                st.session_state["_scan_result"] = result
                st.session_state["_scan_result_ruleset_id"] = ruleset.id
                st.session_state["_scan_result_ruleset_name"] = ruleset.name
                st.session_state["scan_root"] = root.strip()
                st.session_state.pop("_scan_saved_run_id", None)

        result = st.session_state.get("_scan_result")
        if result is None:
            st.info("填写根目录后点击“开始只读预览”。")
            return

        summary = summarize(result)
        st.subheader("扫描摘要")
        if result.truncated:
            st.warning("扫描达到上限，结果为部分结果。可提高上限后重扫。")
        if result.status == "failed":
            st.error(summary["text"])
        else:
            st.success(summary["text"])

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("目录", result.total_dirs)
        m2.metric("数据组", len(result.groups))
        m3.metric("候选批次", len(result.candidates))
        m4.metric("问题", len(result.issues))
        m5.metric("耗时", format_duration(result.duration_ms))

        tab_candidates, tab_groups, tab_issues, tab_diag = st.tabs(["候选批次", "数据组", "问题与原因", "诊断"])
        with tab_candidates:
            rows = _candidate_rows(result)
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.info("没有发现候选批次。查看“问题与原因”了解原因。")
        with tab_groups:
            rows = _group_rows(result)
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.info("没有发现数据组。")
        with tab_issues:
            rows = _issue_rows(result)
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.success("没有发现问题。")
        with tab_diag:
            st.json(summary)
            st.download_button(
                "下载扫描结果 JSON",
                data=json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                file_name=f"scan_preview_{result.started_at.replace(':', '-')}.json",
                mime="application/json",
                use_container_width=True,
            )

        st.divider()
        st.subheader("保存扫描记录")
        st.caption("只保存到管理数据库，供后续版本做候选确认；不会创建批次、不会移动/删除任何源文件。")
        if result.status == "failed":
            st.info("扫描失败，不保存记录。")
        elif st.button("保存到管理数据库", use_container_width=True):
            run_id = service.save_result(
                result,
                operator=operator,
                rule_set_id=st.session_state.get("_scan_result_ruleset_id"),
            )
            st.session_state["_scan_saved_run_id"] = run_id
            st.success(f"已保存扫描记录：{run_id}")
        if st.session_state.get("_scan_saved_run_id"):
            st.code(st.session_state["_scan_saved_run_id"])
    finally:
        service.close()
