"""候选批次确认页面（1.25.1）。

只读写管理数据库；确认批次不会移动/复制/删除任何源文件。
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from modules.batch_management.service import BatchService
from modules.common.timeutil import now_iso


def _fmt_time(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ")[:19]


def _run_options(service: BatchService) -> dict[str, str]:
    runs = service.list_runs(100)
    options: dict[str, str] = {}
    for item in runs:
        label = (
            f"{_fmt_time(item.get('started_at'))} | {item.get('status')} | "
            f"{item.get('group_count', 0)} 组 / {item.get('candidate_count', 0)} 候选 | "
            f"{item.get('root_path', '')}"
        )
        options[item["id"]] = label
    return options


def _candidate_label(item: dict) -> str:
    name = item.get("batch_name") or item.get("batch_path") or item.get("id")
    return f"{name}（{item.get('status')}，{item.get('group_count', 0)} 组）"


def _candidate_rows(candidates: list[dict]) -> list[dict]:
    return [
        {
            "候选ID": item["id"],
            "批次名": item.get("batch_name", ""),
            "状态": item.get("status", ""),
            "数据组": item.get("group_count", 0),
            "文件数": item.get("file_count", 0),
            "置信度": round(float(item.get("confidence") or 0), 2),
            "路径": item.get("batch_path", ""),
            "已确认批次": item.get("confirmed_batch_id") or "",
            "备注": item.get("notes") or "",
        }
        for item in candidates
    ]


def _render_candidates(service: BatchService, run_id: str) -> None:
    candidates = service.list_candidates(run_id)
    st.write(f"候选批次：{len(candidates)}")
    if not candidates:
        st.info("该扫描记录没有候选批次。")
        return
    st.dataframe(pd.DataFrame(_candidate_rows(candidates)), use_container_width=True, hide_index=True)

    actionable = [item for item in candidates if item.get("status") not in {"confirmed", "ignored"}]
    if not actionable:
        st.caption("该扫描记录的候选都已确认或忽略。")
        return
    labels = {item["id"]: _candidate_label(item) for item in actionable}

    st.divider()
    st.subheader("人工确认")
    chosen_id = st.selectbox(
        "选择候选",
        options=list(labels.keys()),
        format_func=lambda key: labels[key],
        key=f"batch_confirm_candidate_{run_id}",
    )
    candidate = service.get_candidate(chosen_id)
    if not candidate:
        st.error("候选读取失败")
        return

    groups = service.candidate_groups(chosen_id)
    group_labels = {item["path"]: item["path"] for item in groups}
    identity = {}
    try:
        identity = json.loads(candidate.get("identity_json") or "{}")
    except ValueError:
        identity = {}

    col_name, col_group = st.columns([2, 3])
    name = col_name.text_input(
        "批次名",
        value=candidate.get("batch_name") or "",
        key=f"batch_confirm_name_{run_id}_{chosen_id}",
    )
    selected_groups = col_group.multiselect(
        "确认的数据组（默认全部）",
        options=list(group_labels.keys()),
        default=list(group_labels.keys()),
        format_func=lambda path: path,
        key=f"batch_confirm_groups_{run_id}_{chosen_id}",
    )
    identity_text = st.text_area(
        "身份字段 JSON（可选修改）",
        value=json.dumps(identity, ensure_ascii=False, indent=2),
        height=120,
        key=f"batch_confirm_identity_{run_id}_{chosen_id}",
    )

    col_confirm, col_ignore = st.columns(2)
    if col_confirm.button("✅ 确认生成批次", type="primary", use_container_width=True, key=f"batch_confirm_btn_{run_id}"):
        try:
            overrides = json.loads(identity_text) if identity_text.strip() else {}
            if not isinstance(overrides, dict):
                raise ValueError("身份字段必须是 JSON 对象")
            batch_id = service.confirm_candidate(
                chosen_id,
                operator=st.session_state.get("operator_name", ""),
                name=name.strip() or None,
                identity_overrides={str(k): str(v) for k, v in overrides.items()},
                selected_group_paths=selected_groups,
            )
            st.success(f"已确认批次：{batch_id}")
            st.rerun()
        except Exception as exc:
            st.error(f"确认失败：{exc}")

    ignore_reason = col_ignore.text_input(
        "忽略原因",
        key=f"batch_ignore_reason_{run_id}_{chosen_id}",
        placeholder="可选，例如：重复批次",
    )
    if col_ignore.button("🚫 忽略候选", use_container_width=True, key=f"batch_ignore_btn_{run_id}"):
        try:
            service.ignore_candidate(
                chosen_id,
                operator=st.session_state.get("operator_name", ""),
                reason=ignore_reason.strip(),
            )
            st.success("已忽略候选")
            st.rerun()
        except Exception as exc:
            st.error(f"忽略失败：{exc}")


def _render_edit(service: BatchService, run_id: str) -> None:
    candidates = [
        item
        for item in service.list_candidates(run_id)
        if item.get("status") not in {"confirmed", "ignored"}
    ]
    if not candidates:
        st.caption("没有可修改的候选。")
        return
    labels = {item["id"]: _candidate_label(item) for item in candidates}
    chosen_id = st.selectbox(
        "选择候选",
        options=list(labels.keys()),
        format_func=lambda key: labels[key],
        key=f"batch_edit_candidate_{run_id}",
    )
    candidate = service.get_candidate(chosen_id)
    if not candidate:
        st.error("候选读取失败")
        return

    identity = {}
    try:
        identity = json.loads(candidate.get("identity_json") or "{}")
    except ValueError:
        identity = {}

    name = st.text_input(
        "批次名",
        value=candidate.get("batch_name") or "",
        key=f"batch_edit_name_{run_id}_{chosen_id}",
    )
    identity_text = st.text_area(
        "身份字段 JSON",
        value=json.dumps(identity, ensure_ascii=False, indent=2),
        height=140,
        key=f"batch_edit_identity_{run_id}_{chosen_id}",
    )
    notes = st.text_area(
        "备注",
        value=candidate.get("notes") or "",
        height=80,
        key=f"batch_edit_notes_{run_id}_{chosen_id}",
    )
    status_labels = {
        "pending": "待确认",
        "abnormal": "异常",
        "rescan": "待重扫",
        "partial": "部分识别",
        "duplicate": "重复",
    }
    status_keys = list(status_labels.keys())
    current_status = candidate.get("status") if candidate.get("status") in status_labels else "pending"
    status = st.selectbox(
        "候选状态",
        options=status_keys,
        index=status_keys.index(current_status),
        format_func=lambda key: status_labels[key],
        key=f"batch_edit_status_{run_id}_{chosen_id}",
    )
    if st.button("💾 保存修改", use_container_width=True, key=f"batch_edit_btn_{run_id}"):
        try:
            overrides = json.loads(identity_text) if identity_text.strip() else {}
            if not isinstance(overrides, dict):
                raise ValueError("身份字段必须是 JSON 对象")
            service.update_candidate(
                chosen_id,
                operator=st.session_state.get("operator_name", ""),
                name=name.strip() or None,
                identity={str(k): str(v) for k, v in overrides.items()},
                notes=notes,
                status=status,
            )
            st.success("已保存候选修改")
            st.rerun()
        except Exception as exc:
            st.error(f"保存失败：{exc}")


def _render_merge(service: BatchService, run_id: str) -> None:
    candidates = [
        item
        for item in service.list_candidates(run_id)
        if item.get("status") not in {"confirmed", "ignored"}
    ]
    if len(candidates) < 2:
        st.info("至少需要两个未确认候选才能合并。")
        return
    labels = {item["id"]: _candidate_label(item) for item in candidates}
    chosen = st.multiselect(
        "选择要合并的候选",
        options=list(labels.keys()),
        format_func=lambda key: labels[key],
        key=f"batch_merge_candidates_{run_id}",
    )
    name = st.text_input(
        "合并后的批次名",
        placeholder="留空则使用第一个候选的批次名",
        key=f"batch_merge_name_{run_id}",
    )
    if st.button("🔗 合并并确认", type="primary", use_container_width=True, key=f"batch_merge_btn_{run_id}"):
        try:
            batch_id = service.merge_candidates(
                chosen,
                operator=st.session_state.get("operator_name", ""),
                name=name.strip() or None,
            )
            st.success(f"已合并为批次：{batch_id}")
            st.rerun()
        except Exception as exc:
            st.error(f"合并失败：{exc}")


def _render_split(service: BatchService, run_id: str) -> None:
    candidates = [
        item
        for item in service.list_candidates(run_id)
        if item.get("status") not in {"confirmed", "ignored"}
    ]
    if not candidates:
        st.info("没有可拆分的候选。")
        return
    labels = {item["id"]: _candidate_label(item) for item in candidates}
    chosen_id = st.selectbox(
        "选择要拆分的候选",
        options=list(labels.keys()),
        format_func=lambda key: labels[key],
        key=f"batch_split_candidate_{run_id}",
    )
    groups = service.candidate_groups(chosen_id)
    if not groups:
        st.warning("该候选没有数据组。")
        return
    group_labels = {item["path"]: item["path"] for item in groups}
    selected = st.multiselect(
        "选择要拆出的数据组",
        options=list(group_labels.keys()),
        format_func=lambda path: path,
        key=f"batch_split_groups_{run_id}_{chosen_id}",
    )
    name = st.text_input(
        "拆出批次名",
        placeholder="留空则使用原批次名",
        key=f"batch_split_name_{run_id}_{chosen_id}",
    )
    if st.button("✂️ 拆分并确认选中数据组", use_container_width=True, key=f"batch_split_btn_{run_id}"):
        try:
            outcome = service.split_candidate(
                chosen_id,
                selected,
                operator=st.session_state.get("operator_name", ""),
                name=name.strip() or None,
            )
            st.success(
                f"已拆出批次：{outcome['batch_id']}；剩余候选：{outcome['new_candidate_id']}"
            )
            st.rerun()
        except Exception as exc:
            st.error(f"拆分失败：{exc}")


def _render_diff(service: BatchService) -> None:
    options = _run_options(service)
    if len(options) < 2:
        st.info("至少需要两条扫描记录才能对比差异。")
        return
    keys = list(options.keys())
    col_old, col_new = st.columns(2)
    old_run = col_old.selectbox(
        "旧扫描记录",
        options=keys,
        format_func=lambda key: options[key],
        key="batch_diff_old",
    )
    default_new = keys[1] if keys[0] == old_run else keys[0]
    new_run = col_new.selectbox(
        "新扫描记录",
        options=keys,
        format_func=lambda key: options[key],
        index=keys.index(default_new),
        key="batch_diff_new",
    )
    if st.button("🔍 对比差异", type="primary", use_container_width=True, key="batch_diff_btn"):
        if old_run == new_run:
            st.warning("请选择两条不同的扫描记录。")
        else:
            try:
                st.session_state["_batch_diff_result"] = service.diff_runs(old_run, new_run)
            except Exception as exc:
                st.error(f"对比失败：{exc}")

    diff = st.session_state.get("_batch_diff_result")
    if not diff:
        return
    st.caption(f"当前显示：{diff['old_run_id']} → {diff['new_run_id']}")
    st.divider()
    groups = diff["groups"]
    candidates = diff["candidates"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("新增数据组", len(groups["added"]))
    c2.metric("删除数据组", len(groups["removed"]))
    c3.metric("修改数据组", len(groups["modified"]))
    c4.metric("未变化数据组", groups["unchanged_count"])

    st.subheader("数据组差异")
    rows = []
    for path in groups["removed"]:
        rows.append({"类型": "删除", "路径": path, "说明": "新扫描中不存在"})
    for path in groups["added"]:
        rows.append({"类型": "新增", "路径": path, "说明": "新扫描中新增"})
    for item in groups["modified"]:
        before = item["before"]
        after = item["after"]
        detail = (
            f"文件 {before.get('file_count')}→{after.get('file_count')}；"
            f"图片 {len(before.get('images') or [])}→{len(after.get('images') or [])}"
        )
        rows.append({"类型": "修改", "路径": item["path"], "说明": detail})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.success("没有数据组差异。")

    st.subheader("候选批次差异")
    if candidates["added"] or candidates["removed"] or candidates["changed"]:
        for path in candidates["removed"]:
            st.write(f"- 🗑️ 删除：`{path}`")
        for path in candidates["added"]:
            st.write(f"- ➕ 新增：`{path}`")
        for item in candidates["changed"]:
            st.write(f"- ✏️ 变化：`{item['batch_path']}`")
    else:
        st.success("没有候选批次差异。")

    with st.expander("完整差异 JSON"):
        st.json(diff)


def _render_batches(service: BatchService) -> None:
    batches = service.list_batches(100)
    st.write(f"已确认批次：{len(batches)}")
    if not batches:
        st.info("还没有已确认批次。")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "显示ID": item.get("display_id", ""),
                    "批次名": item.get("name", ""),
                    "来源": item.get("source", ""),
                    "状态": item.get("status", ""),
                    "数据组": item.get("data_count", 0),
                    "实际组数": item.get("actual_group_count", 0),
                    "提交日期": item.get("submit_date", ""),
                    "创建时间": _fmt_time(item.get("created_at")),
                }
                for item in batches
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    labels = {item["id"]: f"{item.get('display_id') or item['id']} | {item.get('name')}" for item in batches}
    chosen_id = st.selectbox(
        "查看批次数据组",
        options=list(labels.keys()),
        format_func=lambda key: labels[key],
        key="batch_view_batch",
    )
    groups = service.batch_groups(chosen_id)
    if groups:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "任务ID": item.get("task_id", ""),
                        "路径": item.get("normalized_path", ""),
                        "图片": item.get("image_count", 0),
                        "TXT": item.get("txt_count", 0),
                        "文件": item.get("file_count", 0),
                        "状态": item.get("status", ""),
                        "指纹": (item.get("fingerprint") or "")[:12],
                    }
                    for item in groups
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("该批次没有数据组。")

    log_rows = service.db.connect().execute(
        "SELECT * FROM batch_status_log WHERE batch_id = ? ORDER BY created_at",
        (chosen_id,),
    ).fetchall()
    with st.expander("批次状态日志"):
        st.dataframe(pd.DataFrame([dict(row) for row in log_rows]), use_container_width=True, hide_index=True)


def render() -> None:
    st.title("🧩 候选批次确认（1.25.1）")
    st.caption(
        "确认只在管理数据库中创建批次和数据组索引；不会移动、复制、删除或修改任何源图片/TXT/CSV。"
    )

    try:
        service = BatchService()
    except Exception as exc:
        st.error(f"管理数据库初始化失败：{exc}")
        return

    try:
        options = _run_options(service)
        if not options:
            st.info("还没有扫描记录。请先在“数据扫描”页执行扫描并保存记录。")
            return

        run_id = st.selectbox(
            "扫描记录",
            options=list(options.keys()),
            format_func=lambda key: options[key],
            key="batch_run_select",
        )

        tab_confirm, tab_edit, tab_merge, tab_diff, tab_batches = st.tabs(
            ["候选确认", "修改候选", "合并 / 拆分", "扫描差异", "已确认批次"]
        )
        with tab_confirm:
            _render_candidates(service, run_id)
        with tab_edit:
            _render_edit(service, run_id)
        with tab_merge:
            st.subheader("合并候选")
            _render_merge(service, run_id)
            st.divider()
            st.subheader("拆分候选")
            _render_split(service, run_id)
        with tab_diff:
            _render_diff(service)
        with tab_batches:
            _render_batches(service)
    finally:
        service.close()
