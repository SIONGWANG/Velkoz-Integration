"""管理中心首页：数据验收工作台、批次总览、团队/类别聚合与批次详情（v1.25.8）。

全部查询只读管理数据库；不修改源图片/TXT/CSV。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

import pandas as pd
import streamlit as st

from modules.batch_management.dashboard import (
    BatchDashboard,
    status_label,
)
from modules.batch_management.service import BatchService
from modules.common.timeutil import today_str
from modules.export_library import (
    build_library_target,
    export_qualified_groups,
    get_library_root,
    qualified_groups,
    resolve_source_root,
    save_library_root,
    validate_library_root,
)
from modules.feishu_summary import daily_summary, summary_rows
from modules.workspace.models import SOURCE_FINAL, SOURCE_LABELS, SOURCE_PENDING
from modules.workspace.service import WorkspaceService
from modules.workbench_view import (
    filter_rows as wb_filter_rows,
    group_by_category,
    group_by_team,
    is_anomaly,
    next_action,
    progress_text,
    result_text,
    status_word,
    todo_counts,
)

_ALL = "全部"


def _fmt_time(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ")[:19]


def _go(page: str) -> None:
    from ui.router import go

    go(page)


def _metric_row(items: list[tuple[str, Any, str | None]]) -> None:
    columns = st.columns(len(items))
    for column, (label, value, help_text) in zip(columns, items):
        column.metric(label, value, help=help_text)


def _open_batch(batch_id: str) -> None:
    st.session_state["management_batch_id"] = batch_id
    _go("batch_detail")


def _batch_label(row: dict[str, Any]) -> str:
    display = row.get("display_id") or row.get("id") or ""
    name = row.get("name") or ""
    team = row.get("team") or "未指定团队"
    return f"{display} | {name} | {team}"


def _batch_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "批次": row.get("display_id") or row.get("id") or "",
                "批次名": row.get("name") or "",
                "团队": row.get("team") or "",
                "供应商": row.get("supplier") or "",
                "日期": row.get("submit_date") or "",
                "一级": row.get("category_l1") or "",
                "二级": row.get("category_l2") or "",
                "任务数": int(row.get("task_count") or 0),
                "合格": int(row.get("qualified_count") or 0),
                "不合格": int(row.get("rejected_count") or 0),
                "剩余": int(row.get("remaining_count") or 0),
                "导入记录": int(row.get("imported_records") or 0),
                "格式": row.get("format_label") or "",
                "翻译": row.get("translation_label") or "",
                "验收": row.get("acceptance_label") or "",
                "状态": row.get("status_label") or "",
                "最近活动": _fmt_time(row.get("last_activity")),
            }
            for row in rows
        ]
    )


def _filters(rows: list[dict[str, Any]]) -> dict[str, str]:
    options = BatchDashboard.filter_options(rows)
    col1, col2, col3, col4, col5 = st.columns(5)
    team = col1.selectbox("团队", [_ALL, *options["teams"]], key="mgmt_filter_team")
    supplier = col2.selectbox("供应商", [_ALL, *options["suppliers"]], key="mgmt_filter_supplier")
    date = col3.selectbox("日期", [_ALL, *options["dates"]], key="mgmt_filter_date")
    l1 = col4.selectbox("一级类别", [_ALL, *options["categories_l1"]], key="mgmt_filter_l1")
    l2 = col5.selectbox("二级类别", [_ALL, *options["categories_l2"]], key="mgmt_filter_l2")
    col6, col7 = st.columns([1, 2])
    status = col6.selectbox("批次状态", [_ALL, *options["statuses"]], key="mgmt_filter_status")
    keyword = col7.text_input("搜索批次名 / 批次号 / 路径", key="mgmt_filter_keyword")
    return {
        "team": "" if team == _ALL else team,
        "supplier": "" if supplier == _ALL else supplier,
        "date": "" if date == _ALL else date,
        "category_l1": "" if l1 == _ALL else l1,
        "category_l2": "" if l2 == _ALL else l2,
        "status": "" if status == _ALL else status,
        "keyword": keyword,
    }


# ── 批次总览 ─────────────────────────────────────────────


def render_batch_overview(service: BatchService) -> None:
    st.title("📦 批次总览")
    st.caption(
        "以批次为单位查看团队、类别、任务数、验收统计、导入记录、格式与翻译状态；"
        "需要时才下钻到数据组或单条记录。"
    )
    dashboard = BatchDashboard(service.db)
    try:
        all_rows = dashboard.batches()
    except Exception as exc:
        st.error(f"读取批次失败：{exc}")
        return
    if not all_rows:
        st.info("还没有已确认批次。请先在“候选确认”中确认扫描结果。")
        return

    filters = _filters(all_rows)
    rows = BatchDashboard.filter_rows(all_rows, **filters)
    _metric_row(
        [
            ("批次数", len(rows), "当前筛选结果"),
            ("任务数", sum(int(row.get("task_count") or 0) for row in rows), "数据组数量"),
            ("导入记录", sum(int(row.get("imported_records") or 0) for row in rows), "其他验收员记录"),
            (
                "待处理批次",
                sum(1 for row in rows if row.get("acceptance_status") in {"not_started", "in_progress"}),
                "验收未完成",
            ),
            (
                "异常批次",
                sum(
                    1
                    for row in rows
                    if int(row.get("format_failed") or 0) > 0
                    or int(row.get("import_errors") or 0) > 0
                ),
                "格式失败或导入错误",
            ),
        ]
    )
    st.caption(
        "说明：合格/不合格优先取批次验收统计；尚未同步时显示 0，不虚构其他验收员的实时进度。"
    )
    if not rows:
        st.warning("当前筛选条件下没有批次。")
        return
    frame = _batch_frame(rows)
    event = st.dataframe(
        frame,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="mgmt_batch_table",
    )
    selected = getattr(getattr(event, "selection", None), "rows", []) if event is not None else []
    if selected:
        index = int(selected[0])
        if 0 <= index < len(rows):
            row = rows[index]
            if st.button(
                f"打开批次：{row.get('display_id') or row.get('name')}",
                type="primary",
                use_container_width=True,
                key="mgmt_open_selected_batch",
            ):
                _open_batch(str(row["id"]))
    else:
        st.caption("在上方表格中选择一行，或使用下面的下拉框打开批次。")
        choice = st.selectbox(
            "选择批次",
            options=[row["id"] for row in rows],
            format_func=lambda value: _batch_label(next(row for row in rows if row["id"] == value)),
            key="mgmt_batch_select",
        )
        if st.button("打开批次详情", use_container_width=True, key="mgmt_open_batch_select"):
            _open_batch(str(choice))


# ── 工作台 ───────────────────────────────────────────────


# ── 数据验收工作台 ───────────────────────────────────────


def _set_format_context(batch: dict[str, Any], *, only_anomalies: bool = False) -> None:
    st.session_state["fmt_batch_id"] = str(batch["id"])
    st.session_state["format_root"] = batch.get("batch_path") or batch.get("data_root") or ""
    st.session_state.pop("_fmt_result", None)
    st.session_state.pop("_fmt_error", None)
    if only_anomalies and batch.get("format_last_run_id"):
        st.session_state["fmt_previous_run_id"] = batch["format_last_run_id"]
        st.session_state["fmt_only_anomalies"] = True
    else:
        st.session_state.pop("fmt_previous_run_id", None)
        st.session_state.pop("fmt_only_anomalies", None)
    _go("format")


def _set_translation_context(
    batch: dict[str, Any], *, mode: str = "missing", preset_paths: list[str] | None = None
) -> None:
    st.session_state["pc_batch_id"] = str(batch["id"])
    st.session_state["precheck_root"] = batch.get("batch_path") or batch.get("data_root") or ""
    st.session_state["pc_translation_mode"] = mode
    st.session_state["pc_auto_scan"] = True
    st.session_state.pop("_pc_pairs", None)
    st.session_state.pop("_pc_result", None)
    if preset_paths is not None:
        st.session_state["pc_preselect_paths"] = list(preset_paths)
    else:
        st.session_state.pop("pc_preselect_paths", None)
    _go("precheck")


def _open_acceptance(batch: dict[str, Any]) -> None:
    """从批次直接进入正式质检：自动加载该批次目录，不需要再粘贴路径。"""
    path = batch.get("batch_path") or batch.get("data_root") or ""
    st.session_state["root_path"] = path
    st.session_state["data_groups"] = []
    st.session_state["current_id"] = None
    st.session_state["last_loaded_id"] = None
    st.session_state["scan_cache_buster"] = int(st.session_state.get("scan_cache_buster", 0)) + 1
    st.session_state["_scan_path"] = path
    st.session_state["is_scanning"] = bool(path)
    st.session_state["nav_page"] = "acceptance"
    st.rerun()


def _run_batch_action(batch: dict[str, Any], action: str, *, key_prefix: str) -> None:
    """批次主按钮：路径完全隐藏，所有操作都以 batch_id 为上下文。"""
    if action == "format":
        _set_format_context(batch)
    elif action == "translate":
        _set_translation_context(batch, mode="missing")
    elif action == "acceptance":
        _open_acceptance(batch)
    elif action == "archive":
        st.session_state["archive_batch"] = str(batch["id"])
        _go("archive")
    else:
        _open_batch(str(batch["id"]))


def _render_batch_card(row: dict[str, Any], *, key_prefix: str) -> None:
    """一行一个批次：左边事实，右边一个主按钮 + 详情。"""
    batch_id = str(row.get("id") or "")
    action, action_label = next_action(row)
    total = int(row.get("task_count") or 0)
    accepted = int(row.get("accepted_records") or 0)
    percent = min(1.0, accepted / total) if total else 0.0

    with st.container(border=True):
        info, buttons = st.columns([5, 2])
        with info:
            title = row.get("name") or row.get("display_id") or "未命名批次"
            st.markdown(f"**{title}**")
            meta = " · ".join(
                part
                for part in (
                    row.get("display_id") or "",
                    row.get("submit_date") or "",
                    f"{total} 组",
                    f"{int(row.get('image_count') or 0)} 张",
                )
                if part
            )
            st.caption(meta)
            st.caption(f"{status_word(row)} · {progress_text(row)}")
            st.caption(result_text(row))
            if is_anomaly(row):
                st.caption("⚠️ 有异常，详情里可查看和处理")
            st.progress(percent)
        with buttons:
            if st.button(
                action_label,
                type="primary",
                use_container_width=True,
                key=f"{key_prefix}_go_{batch_id}",
            ):
                _run_batch_action(row, action, key_prefix=key_prefix)
            if st.button(
                "详情",
                use_container_width=True,
                key=f"{key_prefix}_detail_{batch_id}",
            ):
                _open_batch(batch_id)


def _render_batch_cards(rows: list[dict[str, Any]], *, key_prefix: str) -> None:
    for team, team_rows in group_by_team(rows):
        st.markdown(f"#### 👥 {team}")
        st.caption(
            f"{len(team_rows)} 批 · "
            f"{sum(int(row.get('task_count') or 0) for row in team_rows)} 组 · "
            f"{sum(int(row.get('image_count') or 0) for row in team_rows)} 张"
        )
        for category, category_rows in group_by_category(team_rows):
            st.markdown(f"**{category}**")
            for row in category_rows:
                _render_batch_card(row, key_prefix=key_prefix)


def _render_workbench_filters(rows: list[dict[str, Any]]) -> dict[str, Any]:
    teams = sorted({row.get("team") or "未指定团队" for row in rows})
    categories = sorted(
        {
            " / ".join(part for part in (row.get("category_l1") or "", row.get("category_l2") or "") if part)
            or "未分类"
            for row in rows
        }
    )
    dates = sorted({row.get("submit_date") or "" for row in rows if row.get("submit_date")}, reverse=True)
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    keyword = c1.text_input(
        "搜索",
        key="ws_search",
        placeholder="批次名、批次号、团队、类目",
        label_visibility="collapsed",
    )
    team = c2.selectbox("团队", ["全部", *teams], key="ws_team", label_visibility="collapsed")
    category = c3.selectbox("类目", ["全部", *categories], key="ws_category", label_visibility="collapsed")
    date = c4.selectbox("日期", ["全部", *dates], key="ws_date", label_visibility="collapsed")
    only_todo = st.toggle("只看未完成", value=True, key="ws_only_todo")
    return {
        "team": "" if team == "全部" else team,
        "category": "" if category == "全部" else category,
        "date": "" if date == "全部" else date,
        "keyword": keyword,
        "only_todo": only_todo,
    }


def _render_candidates(workspace: WorkspaceService, candidates: list[dict[str, Any]], *, project_id: str) -> None:
    if not candidates:
        st.success("没有待确认的新批次。刷新目录后，新发现的批次会出现在这里。")
        return
    frame = pd.DataFrame(
        [
            {
                "批次名称": item.get("batch_name") or "",
                "来源": item.get("source_label") or "",
                "大类目": item.get("category_l1") or "",
                "小类目": item.get("category_l2") or "",
                "团队": item.get("team") or "",
                "日期": item.get("date") or "",
                "数据组": int(item.get("group_count") or 0),
                "文件数": int(item.get("file_count") or 0),
                "置信度": round(float(item.get("confidence") or 0.0), 2),
                "发现时间": _fmt_time(item.get("discovered_at")),
                "说明": item.get("reason") or "",
            }
            for item in candidates
        ]
    )
    st.dataframe(frame, use_container_width=True, hide_index=True)
    labels = {
        item["candidate_id"]: (
            f"{item.get('source_label')} | {item.get('batch_name')} | {item.get('team') or '团队未知'}"
        )
        for item in candidates
    }
    chosen = st.selectbox(
        "选择待确认批次",
        options=list(labels.keys()),
        format_func=lambda value: labels[value],
        key=f"ws_candidate_choose_{project_id}",
    )
    operator = st.text_input(
        "操作员",
        value=st.session_state.get("operator_name", ""),
        key="ws_candidate_operator",
    )
    c1, c2, c3 = st.columns([1, 1, 2])
    if c1.button("确认并创建批次", type="primary", use_container_width=True, key="ws_candidate_confirm"):
        try:
            batch_id = workspace.confirm_candidate(chosen, operator=operator.strip())
            st.success(f"已创建批次：{batch_id}")
            st.rerun()
        except Exception as exc:
            st.error(f"确认失败：{exc}")
    if c2.button("忽略候选", use_container_width=True, key="ws_candidate_ignore"):
        try:
            workspace.ignore_candidate(chosen, operator=operator.strip(), reason="工作台忽略")
            st.success("已忽略该候选；历史目录不会被删除。")
            st.rerun()
        except Exception as exc:
            st.error(f"忽略失败：{exc}")
    with c3.expander("查看候选路径", expanded=False):
        item = next(item for item in candidates if item["candidate_id"] == chosen)
        st.code(item.get("batch_path") or "", language="text")


def _render_onboarding(workspace: WorkspaceService) -> None:
    st.info(
        "首次使用请配置一次业务目录。系统只接入 02-待验数据 与 03-申请终验，"
        "不会扫描 00-试做情况 或 01-素材仓库。"
    )
    parent = st.text_input(
        "项目根目录（推荐）",
        placeholder=r"\\10.10.111.227\美学\lizhiru\库\卢岳-混编项目-11月",
        key="ws_onboard_parent",
    )
    project_name = st.text_input(
        "项目名称",
        value=st.session_state.get("ws_onboard_name", ""),
        placeholder="例如：卢岳-混编项目-11月",
        key="ws_onboard_name",
    )
    if st.button("检测待验 / 申请终验目录", use_container_width=True, key="ws_onboard_detect"):
        detection = workspace.projects.detect_parent(parent)
        st.session_state["_ws_onboard_detection"] = detection
    detection = st.session_state.get("_ws_onboard_detection")
    pending_path = final_path = ""
    if detection is not None:
        st.write(f"项目根目录：`{detection.parent_path or '未填写'}`")
        for label, item in (("待验数据目录", detection.pending), ("申请终验目录", detection.final)):
            if item is None:
                continue
            if item.ok:
                st.success(f"{label}：已找到 `{item.path}`")
            elif item.exists:
                st.error(f"{label}：{item.error}")
            else:
                st.warning(f"{label}：{item.error}")
        for warning in detection.warnings:
            st.caption(warning)
        for error in detection.errors:
            st.error(error)
        if detection.pending:
            pending_path = detection.pending.path
        if detection.final:
            final_path = detection.final.path
    with st.expander("分别填写两个独立网络路径", expanded=False):
        pending_path = st.text_input("待验数据目录", value=pending_path, key="ws_onboard_pending")
        final_path = st.text_input("申请终验目录", value=final_path, key="ws_onboard_final")
        if st.button("检测这两个目录", key="ws_onboard_detect_manual"):
            for path, label in ((pending_path, "待验数据目录"), (final_path, "申请终验目录")):
                item = workspace.projects.detect_path(path, label=label)
                st.success(f"{label}：可用 `{item.path}`") if item.ok else st.error(item.error)
    if st.button("保存并进入工作台", type="primary", use_container_width=True, key="ws_onboard_save"):
        try:
            workspace.projects.create(
                project_name or os.path.basename(os.path.normpath(parent or "")) or "默认项目",
                parent_path=parent,
                pending_path=pending_path,
                final_path=final_path,
            )
            st.success("已保存业务目录配置。")
            st.rerun()
        except Exception as exc:
            st.error(f"保存失败：{exc}")


def _refresh_worker(state: dict, db_path: str | None, project_id: str, sources: tuple[str, ...], operator: str) -> None:
    service: WorkspaceService | None = None
    try:
        service = WorkspaceService(db_path)

        def on_progress(done: int, total: int, message: str) -> None:
            state["progress"] = {"done": int(done), "total": int(total), "message": message}

        state["result"] = service.refresh_project(
            project_id,
            sources=sources,
            operator=operator,
            progress=on_progress,
            should_cancel=state["cancel"].is_set,
        )
    except Exception as exc:
        state["error"] = str(exc)
    finally:
        state["done"] = True
        if service is not None:
            service.close()


def _start_refresh(
    workspace: WorkspaceService,
    project_id: str,
    sources: tuple[str, ...],
    operator: str,
) -> None:
    state = {
        "done": False,
        "error": "",
        "result": None,
        "cancel": threading.Event(),
        "progress": {"done": 0, "total": 0, "message": "准备刷新…"},
    }
    thread = threading.Thread(
        target=_refresh_worker,
        args=(state, str(workspace.db_path) if workspace.db_path else None, project_id, sources, operator),
        daemon=True,
    )
    state["thread"] = thread
    st.session_state["_ws_runner"] = state
    thread.start()
    st.rerun()


def _render_refresh_runner() -> bool:
    state = st.session_state.get("_ws_runner")
    if not state:
        return False
    if not state["done"]:
        progress = state.get("progress") or {}
        total = int(progress.get("total") or 0)
        done = int(progress.get("done") or 0)
        ratio = min(0.99, done / total) if total else 0.0
        st.progress(ratio, text=progress.get("message") or "正在刷新目录…")
        if st.button("⏹ 取消刷新", use_container_width=True, key="ws_refresh_cancel"):
            state["cancel"].set()
            st.info("已请求取消；已完成的扫描结果会保留，历史批次不会被删除。")
        time.sleep(0.4)
        st.rerun()
        return True
    st.session_state["_ws_last_refresh"] = state.get("result")
    st.session_state["_ws_refresh_error"] = state.get("error") or ""
    st.session_state["_ws_runner"] = None
    st.rerun()
    return True


def _render_refresh_summary() -> None:
    error = st.session_state.pop("_ws_refresh_error", "")
    if error:
        st.error(f"刷新失败：{error}")
    result = st.session_state.pop("_ws_last_refresh", None)
    if not result:
        return
    project = result.get("project")
    with st.expander(
        f"本次刷新结果：{getattr(project, 'project_name', '')}", expanded=True
    ):
        for summary in result.get("summaries", []):
            label = summary.get("source_label") or summary.get("source_type")
            if summary.get("status") == "failed":
                st.error(f"{label}：刷新失败；历史批次已保留。")
            elif summary.get("status") == "not_configured":
                st.warning(f"{label}：未配置目录。")
            else:
                st.success(
                    f"{label}：新发现 {summary.get('new_batches', 0)}，"
                    f"已有 {summary.get('existing_batches', 0)}，"
                    f"变化 {summary.get('changed_batches', 0)}，"
                    f"耗时 {round(int(summary.get('duration_ms') or 0) / 1000, 1)} 秒。"
                )
            st.caption(
                f"根目录：`{summary.get('root_path') or '未配置'}` · "
                f"数据组 {summary.get('groups', 0)} · 无法识别 {summary.get('unrecognized_dirs', 0)} · "
                f"访问失败 {summary.get('access_failures', 0)}"
            )
            for message in summary.get("errors") or []:
                st.error(message)
            if summary.get("truncated"):
                st.warning("达到扫描上限，本次为部分结果；可提高上限后重试。")


def _render_daily_summary(rows: list[dict[str, Any]]) -> None:
    today = today_str()
    include_all = st.checkbox("包含非今日批次", value=False, key="ws_summary_all")
    scoped = (
        rows
        if include_all
        else [
            row
            for row in rows
            if row.get("submit_date") == today
            or str(row.get("last_activity") or "").startswith(today)
        ]
    )
    text = daily_summary(scoped, date=today)
    st.code(text, language="text")
    frame = pd.DataFrame(summary_rows(scoped))
    st.download_button(
        "⬇️ 下载摘要 CSV",
        frame.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"验收日报_{today}.csv",
        mime="text/csv",
        use_container_width=True,
        key="ws_summary_download",
    )
    st.caption("复制上面文本粘贴到飞书表格即可；本工具不联网，也不会修改任何源文件。")


def render_workbench(service: BatchService, workspace: WorkspaceService | None = None) -> None:
    if workspace is None:
        workspace = WorkspaceService()
    st.title("🏠 数据验收工作台")
    st.caption(
        "路径只配置一次。打开工作台只读数据库；只有点刷新才扫描网络目录，"
        "且不修改任何源图片/TXT/CSV。"
    )
    if _render_refresh_runner():
        return
    projects = workspace.projects.list()
    if not projects:
        _render_onboarding(workspace)
        return

    enabled = [item for item in projects if item.enabled]
    options = {item.id: f"{item.project_name}（{'启用' if item.enabled else '停用'}）" for item in projects}
    default = st.session_state.get("ws_project_id")
    if default not in options:
        default = enabled[0].id if enabled else projects[0].id
    project_id = st.selectbox(
        "项目",
        options=list(options.keys()),
        index=list(options.keys()).index(default),
        format_func=lambda value: options[value],
        key="ws_project_id",
    )
    project = next(item for item in projects if item.id == project_id)
    if not project.enabled:
        st.warning("该项目目录已停用。历史批次仍保留，可在设置中重新启用。")
        if st.button("启用该项目", use_container_width=True, key="ws_enable_project"):
            workspace.projects.set_enabled(project.id, True)
            st.rerun()
        return

    op_col, b1, b2, b3, b4 = st.columns([2, 1, 1, 1, 1])
    operator = op_col.text_input(
        "操作员",
        value=st.session_state.get("operator_name", ""),
        key="ws_operator",
        label_visibility="collapsed",
        placeholder="操作员（可选）",
    )
    if b1.button("刷新待验", type="primary", use_container_width=True, key="ws_refresh_pending"):
        _start_refresh(workspace, project.id, (SOURCE_PENDING,), operator.strip())
    if b2.button("刷新终验", type="primary", use_container_width=True, key="ws_refresh_final"):
        _start_refresh(workspace, project.id, (SOURCE_FINAL,), operator.strip())
    if b3.button("全部刷新", use_container_width=True, key="ws_refresh_all"):
        _start_refresh(workspace, project.id, (SOURCE_PENDING, SOURCE_FINAL), operator.strip())
    if b4.button("目录设置", use_container_width=True, key="ws_open_settings"):
        _go("settings")

    _render_refresh_summary()
    with st.expander("📁 目录与刷新信息", expanded=False):
        st.write(f"待验数据：`{project.pending_path or '未配置'}`")
        st.write(f"申请终验：`{project.final_path or '未配置'}`")
        st.write(f"最近刷新：{_fmt_time(project.last_scan_at) or '尚未刷新'}")
        st.caption("页面加载不扫描网络目录。断网时保留历史批次，只提示访问失败。")

    dashboard = BatchDashboard(service.db)
    try:
        rows = dashboard.batches()
        candidates = dashboard.pending_candidates(project.id)
    except Exception as exc:
        st.error(f"读取工作台数据失败：{exc}")
        return
    project_rows = [
        row
        for row in rows
        if row.get("source_type") in (SOURCE_PENDING, SOURCE_FINAL)
        and (not row.get("source_root_id") or row.get("source_root_id") == project.id)
    ]
    legacy_rows = [row for row in rows if row.get("source_type") not in (SOURCE_PENDING, SOURCE_FINAL)]

    counts = todo_counts(project_rows, len(candidates))
    today = today_str()
    done_today = sum(
        1
        for row in project_rows
        if status_word(row) == "已完成" and str(row.get("last_activity") or "").startswith(today)
    )
    metrics = st.columns(4)
    metrics[0].metric("待办批次", counts["pending"])
    metrics[1].metric("进行中", counts["doing"])
    metrics[2].metric("今日完成", done_today)
    metrics[3].metric("异常批次", counts["anomaly"])
    st.caption(
        f"共 {len(project_rows)} 批 · {counts['task_count']} 组 · {counts['image_count']} 张 · "
        f"待确认 {len(candidates)} 个"
    )

    with st.expander("📋 今日飞书摘要", expanded=False):
        _render_daily_summary(project_rows)

    if candidates:
        with st.expander(f"🧾 待确认新批次（{len(candidates)}）", expanded=False):
            st.caption("新批次需要人工确认；不会按名称自动合并到已有批次。")
            _render_candidates(workspace, candidates, project_id=project.id)

    if not project_rows:
        st.info("当前项目还没有已确认批次。点上方“刷新待验 / 刷新终验”，再展开“待确认新批次”。")
    else:
        scope = st.radio(
            "范围",
            ["全部", "待验数据", "申请终验"],
            horizontal=True,
            key="ws_source_scope",
            label_visibility="collapsed",
        )
        scoped = {
            "待验数据": [row for row in project_rows if row.get("source_type") == SOURCE_PENDING],
            "申请终验": [row for row in project_rows if row.get("source_type") == SOURCE_FINAL],
            "全部": project_rows,
        }[scope]
        filters = _render_workbench_filters(scoped)
        filtered = wb_filter_rows(scoped, **filters)
        if not filtered:
            st.warning("当前筛选条件下没有批次。")
        else:
            st.caption(f"显示 {len(filtered)} 批；按团队和类目分组。")
            _render_batch_cards(filtered, key_prefix=f"ws_{project.id}")

    if legacy_rows:
        with st.expander(f"🗂 历史批次（未接入业务目录，{len(legacy_rows)}）", expanded=False):
            labels = {
                row["id"]: f"{row.get('display_id') or ''} | {row.get('name') or ''} | {row.get('team') or ''}"
                for row in legacy_rows
            }
            chosen = st.selectbox(
                "选择历史批次",
                options=list(labels.keys()),
                format_func=lambda value: labels[value],
                key="ws_legacy_choice",
            )
            if st.button("打开历史批次详情", use_container_width=True, key="ws_legacy_open"):
                _open_batch(str(chosen))


# ── 团队与类别 ───────────────────────────────────────────


def render_team_category(service: BatchService) -> None:
    st.title("👥 团队与类别")
    st.caption("按团队、一级/二级类别汇总批次与任务；不展开到单条记录。")
    dashboard = BatchDashboard(service.db)
    all_rows = dashboard.batches()
    if not all_rows:
        st.info("还没有已确认批次。")
        return
    options = BatchDashboard.filter_options(all_rows)
    col1, col2, col3 = st.columns([1, 1, 2])
    team = col1.selectbox("团队", [_ALL, *options["teams"]], key="mgmt_summary_team")
    date = col2.selectbox("日期", [_ALL, *options["dates"]], key="mgmt_summary_date")
    keyword = col3.text_input("搜索批次名 / 路径", key="mgmt_summary_keyword")
    rows = BatchDashboard.filter_rows(
        all_rows,
        team="" if team == _ALL else team,
        date="" if date == _ALL else date,
        keyword=keyword,
    )
    if not rows:
        st.warning("当前筛选条件下没有批次。")
        return

    tab_team, tab_category = st.tabs(["按团队", "按类别"])
    with tab_team:
        summary = dashboard.team_summary(rows)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "团队": item["team"],
                        "批次数": item["batches"],
                        "任务数": item["task_count"],
                        "合格": item["qualified_count"],
                        "不合格": item["rejected_count"],
                        "剩余": item["remaining_count"],
                        "导入记录": item["imported_records"],
                        "格式失败": item["format_failed"],
                        "翻译任务": item["translation_jobs"],
                        "品类": "、".join(item["categories"]),
                        "最近活动": _fmt_time(item["last_activity"]),
                    }
                    for item in summary
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        if summary:
            chart = pd.DataFrame(
                {"任务数": [item["task_count"] for item in summary]},
                index=[item["team"] for item in summary],
            )
            st.bar_chart(chart)
    with tab_category:
        summary = dashboard.category_summary(rows)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "一级": item["category_l1"],
                        "二级": item["category_l2"],
                        "批次数": item["batches"],
                        "团队": "、".join(item["teams"]),
                        "任务数": item["task_count"],
                        "合格": item["qualified_count"],
                        "不合格": item["rejected_count"],
                        "剩余": item["remaining_count"],
                        "导入记录": item["imported_records"],
                        "格式失败": item["format_failed"],
                    }
                    for item in summary
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        if summary:
            chart = pd.DataFrame(
                {"任务数": [item["task_count"] for item in summary]},
                index=[f"{item['category_l1']} / {item['category_l2']}" for item in summary],
            )
            st.bar_chart(chart)


def _render_batch_actions(service: BatchService, batch: dict[str, Any]) -> None:
    status = batch.get("status") or ""
    with st.expander("⚙️ 批次状态操作", expanded=False):
        st.caption("只更新管理数据库中的批次状态与状态日志，不移动、不删除、不修改任何任务或源文件。")
        col_operator, col_reason = st.columns([1, 2])
        operator = col_operator.text_input(
            "操作员",
            value=st.session_state.get("operator_name", ""),
            key="mgmt_status_operator",
        )
        reason = col_reason.text_input("原因（可选）", key="mgmt_status_reason")
        confirm = st.checkbox(
            "我确认执行该状态变更",
            value=False,
            key="mgmt_status_confirm",
        )
        actions: list[tuple[str, str]] = []
        if status == "paused":
            actions.append(("confirmed", "▶️ 重开批次"))
        elif status == "archived":
            actions.append(("confirmed", "♻️ 恢复为已确认"))
        else:
            actions.append(("paused", "⏸️ 暂停批次"))
            actions.append(("archived", "📦 归档批次"))
        if status == "confirmed":
            actions.append(("completed", "✅ 标记完成"))
        columns = st.columns(max(1, len(actions)))
        for column, (target, label) in zip(columns, actions):
            if column.button(
                label,
                use_container_width=True,
                disabled=not confirm or not operator.strip(),
                key=f"mgmt_status_action_{target}",
            ):
                try:
                    service.update_batch_status(
                        str(batch["id"]),
                        target,
                        operator=operator.strip(),
                        reason=reason.strip(),
                    )
                    st.success(f"批次状态已更新为：{status_label(target)}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"状态更新失败：{exc}")


# ── 批次详情 ─────────────────────────────────────────────


def _stage_frame(detail: dict[str, Any]) -> pd.DataFrame:
    batch = detail["batch"]
    rows = [
        {
            "阶段": "批次发现",
            "状态": SOURCE_LABELS.get(batch.get("source_type") or "", "已发现"),
            "处理数量": int(batch.get("task_count") or 0),
            "成功": int(batch.get("task_count") or 0),
            "失败": 0,
            "最近时间": _fmt_time(batch.get("created_at")),
            "说明": f"{int(batch.get('image_count') or 0)} 张图片",
        },
        {
            "阶段": "格式检查",
            "状态": batch.get("format_state_label") or "未检查",
            "处理数量": int(batch.get("format_checked_tasks") or 0),
            "成功": int(batch.get("format_passed_tasks") or 0),
            "失败": int(batch.get("format_failed") or 0),
            "最近时间": _fmt_time(batch.get("format_last_time")),
            "说明": f"警告 {int(batch.get('format_warnings') or 0)}",
        },
        {
            "阶段": "翻译预审",
            "状态": batch.get("translation_state_label") or "",
            "处理数量": int(batch.get("translation_item_total") or 0),
            "成功": int(batch.get("translated_items") or 0),
            "失败": int(batch.get("translation_failed_items") or 0),
            "最近时间": _fmt_time(batch.get("translation_last_time")),
            "说明": f"缺中文 {int(batch.get('translation_missing_zh') or 0)}",
        },
        {
            "阶段": "正式验收",
            "状态": batch.get("acceptance_state_label") or "",
            "处理数量": int(batch.get("accepted_records") or 0),
            "成功": int(batch.get("accepted_qualified") or 0),
            "失败": int(batch.get("accepted_rejected") or 0),
            "最近时间": _fmt_time(batch.get("last_accepted")),
            "说明": f"剩余 {int(batch.get('remaining_count') or 0)}",
        },
        {
            "阶段": "验收记录汇总",
            "状态": "有导入记录" if detail.get("imports") else "暂无已导入验收记录",
            "处理数量": len(detail.get("imports") or []),
            "成功": int(batch.get("accepted_qualified") or 0),
            "失败": int(batch.get("accepted_rejected") or 0),
            "最近时间": _fmt_time(batch.get("last_accepted")),
            "说明": "按验收员导出表统计",
        },
        {
            "阶段": "合格归档",
            "状态": "已归档" if int(batch.get("archived_groups") or 0) else "未归档",
            "处理数量": int(batch.get("archived_groups") or 0),
            "成功": int(batch.get("archived_groups") or 0),
            "失败": int(batch.get("archive_failed") or 0),
            "最近时间": _fmt_time(batch.get("last_archive")),
            "说明": f"冲突 {int(batch.get('archive_conflicts') or 0)}",
        },
    ]
    return pd.DataFrame(rows)


def _render_format_tab(detail: dict[str, Any]) -> None:
    batch = detail["batch"]
    latest = detail.get("latest_format") or {}
    if not detail["format_runs"]:
        st.info("该批次还没有格式检查记录。点击下方按钮从当前批次直接启动，无需填写路径。")
        if st.button("🔎 从当前批次开始格式检查", type="primary", key="detail_fmt_start"):
            _set_format_context(batch)
        return
    columns = st.columns(5)
    columns[0].metric("检查次数", len(detail["format_runs"]))
    columns[1].metric("检查任务", int(latest.get("scanned_tasks") or 0))
    columns[2].metric("通过", int(latest.get("passed_tasks") or 0))
    columns[3].metric("失败", int(latest.get("failed_tasks") or 0))
    columns[4].metric("警告", int(latest.get("warning_tasks") or 0))
    st.caption(
        f"最近检查：{_fmt_time(latest.get('finished_at') or latest.get('started_at'))} · "
        f"状态：{batch.get('format_state_label')} · 模板：{latest.get('template_name') or '未记录'}"
    )
    actions = st.columns(3)
    if actions[0].button("重新检查当前批次", type="primary", use_container_width=True, key="detail_fmt_recheck"):
        _set_format_context(batch)
    if actions[1].button(
        "仅检查上次异常/警告",
        use_container_width=True,
        disabled=not latest.get("id"),
        key="detail_fmt_only_anomalies",
    ):
        _set_format_context(batch, only_anomalies=True)
    if actions[2].button("打开高级格式检查页", use_container_width=True, key="detail_fmt_advanced"):
        _set_format_context(batch)
    st.markdown("**检查历史**")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "时间": _fmt_time(item.get("finished_at") or item.get("started_at")),
                    "状态": status_label(item.get("status")),
                    "任务": item.get("scanned_tasks", 0),
                    "通过": item.get("passed_tasks", 0),
                    "失败": item.get("failed_tasks", 0),
                    "警告": item.get("warning_tasks", 0),
                    "模板": item.get("template_name") or "",
                    "只读校验": item.get("integrity_status") or "",
                }
                for item in detail["format_runs"]
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    if detail.get("format_issues"):
        from modules.format_checker.errors import ERROR_CATALOG

        st.markdown("**最近一次错误明细**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "错误码": item.get("code") or "",
                        "说明": ERROR_CATALOG.get(item.get("code") or "", ("", item.get("message") or "", ""))[1],
                        "级别": item.get("severity") or "",
                        "任务ID": item.get("task_id") or "",
                        "文件": item.get("file_path") or "",
                        "期望": item.get("expected") or "",
                        "实际": item.get("actual") or "",
                    }
                    for item in detail["format_issues"]
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.success("最近一次检查没有错误明细。")


def _render_translation_tab(detail: dict[str, Any]) -> None:
    batch = detail["batch"]
    latest = detail.get("latest_translation") or {}
    counts = detail.get("translation_counts") or {}
    if not detail["translation_jobs"]:
        st.info(
            "该批次还没有翻译任务。可从当前批次扫描中文/英文 TXT 并生成预览；"
            "确认写入前不会覆盖英文 TXT。"
        )
        columns = st.columns(3)
        columns[0].metric("中文 TXT", int(batch.get("zh_files") or 0))
        columns[1].metric("英文 TXT", int(batch.get("en_files") or 0))
        columns[2].metric("数据组", int(batch.get("task_count") or 0))
        if st.button("🌐 从当前批次扫描并翻译", type="primary", key="detail_pc_start"):
            _set_translation_context(batch)
        return
    columns = st.columns(5)
    columns[0].metric("中文 TXT", int(batch.get("zh_files") or 0))
    columns[1].metric("英文 TXT", int(batch.get("en_files") or 0))
    columns[2].metric("已翻译", int(counts.get("translated") or 0))
    columns[3].metric("失败", int(counts.get("failed") or 0))
    columns[4].metric("待翻译/缺英文", int(counts.get("missing_en", 0)) + int(counts.get("empty_en", 0)))
    st.caption(
        f"最近翻译：{_fmt_time(latest.get('finished_at') or latest.get('started_at') or latest.get('created_at'))} · "
        f"模型：{latest.get('model') or '未记录'} · Token：{int(batch.get('translation_tokens') or 0)} · "
        f"费用：{float(batch.get('translation_cost') or 0):.4f}"
    )
    actions = st.columns(3)
    if actions[0].button("翻译缺失项", type="primary", use_container_width=True, key="detail_pc_missing"):
        _set_translation_context(batch, mode="missing")
    failed_paths = [
        item.get("folder_path") or ""
        for item in detail.get("translation_items") or []
        if item.get("status") == "failed" and item.get("folder_path")
    ]
    if actions[1].button(
        "失败重试",
        use_container_width=True,
        disabled=not failed_paths,
        key="detail_pc_retry",
    ):
        _set_translation_context(batch, mode="retry", preset_paths=failed_paths)
    if actions[2].button("打开高级翻译页", use_container_width=True, key="detail_pc_advanced"):
        _set_translation_context(batch)
    st.caption(
        "译文预览、语法/一致性建议和“确认写入英文 TXT”仍在高级翻译页完成；"
        "本页只展示真实任务记录，不会自动写源文件。"
    )
    st.markdown("**最近一次翻译明细**")
    if detail.get("translation_items"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "任务ID": item.get("task_id") or "",
                        "状态": item.get("status") or "",
                        "中文长度": item.get("zh_length", 0),
                        "英文长度": item.get("en_length", 0),
                        "模型": item.get("model") or "",
                        "错误": item.get("error_message") or "",
                        "路径": item.get("folder_path") or "",
                    }
                    for item in detail["translation_items"]
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("最近一次任务没有明细。")
    st.markdown("**翻译历史**")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "时间": _fmt_time(item.get("finished_at") or item.get("started_at") or item.get("created_at")),
                    "状态": status_label(item.get("status")),
                    "提供方": item.get("provider") or "",
                    "模型": item.get("model") or "",
                    "操作员": item.get("operator") or "",
                    "说明": item.get("note") or "",
                }
                for item in detail["translation_jobs"]
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )


def _render_acceptance_tab(detail: dict[str, Any]) -> None:
    batch = detail["batch"]
    records = detail.get("acceptance_records") or []
    columns = st.columns(4)
    columns[0].metric("验收记录", len(records))
    columns[1].metric("合格", int(batch.get("accepted_qualified") or 0))
    columns[2].metric("不合格", int(batch.get("accepted_rejected") or 0))
    columns[3].metric("其他", int(batch.get("accepted_other") or 0))
    if not records:
        st.info(
            "暂无已导入验收记录。这不代表验收未开始；如有验收员导出的 CSV/Excel，"
            "请到“记录导入”导入后再查看。"
        )
        if st.button("📥 为当前批次导入验收记录", type="primary", key="detail_accept_import"):
            st.session_state["ri_batch"] = str(batch["id"])
            _go("record_import")
        return
    operators = sorted({str(item.get("operator") or "未指定") for item in records})
    st.caption(f"已导入验收员：{'、'.join(operators)}")
    if detail.get("imports"):
        st.markdown("**来源文件**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "导入时间": _fmt_time(item.get("imported_at") or item.get("created_at")),
                        "来源": item.get("source_path") or "",
                        "状态": status_label(item.get("status")),
                        "新增": item.get("rows_new", 0),
                        "跳过": item.get("rows_skipped", 0),
                        "错误": item.get("rows_error", 0),
                    }
                    for item in detail["imports"]
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    st.markdown("**验收记录**")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "时间": _fmt_time(item.get("record_time") or item.get("created_at")),
                    "验收员": item.get("operator") or "",
                    "结果": item.get("result") or "",
                    "任务ID": item.get("task_id") or "",
                    "标签": item.get("tags_raw") or "",
                    "备注": item.get("note") or "",
                    "来源": item.get("import_source") or "",
                }
                for item in records
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    if st.button("📥 继续导入验收记录", key="detail_accept_import_more"):
        st.session_state["ri_batch"] = str(batch["id"])
        _go("record_import")


def _render_archive_tab(detail: dict[str, Any]) -> None:
    batch = detail["batch"]
    items = detail.get("archive_items") or []
    columns = st.columns(4)
    columns[0].metric("已校验/跳过", int(batch.get("archived_groups") or 0))
    columns[1].metric("冲突", int(batch.get("archive_conflicts") or 0))
    columns[2].metric("失败", int(batch.get("archive_failed") or 0))
    columns[3].metric("最近归档", _fmt_time(batch.get("last_archive")) or "无")
    if items:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "任务ID": item.get("task_id") or "",
                        "状态": item.get("status") or "",
                        "源路径": item.get("source_path") or "",
                        "目标路径": item.get("dest_path") or "",
                        "文件数": item.get("file_count", 0),
                        "错误": item.get("error_message") or "",
                    }
                    for item in items
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("该批次还没有归档条目。")
    if st.button("🗄️ 打开当前批次的合格归档", type="primary", key="detail_archive_open"):
        st.session_state["archive_batch"] = str(batch["id"])
        _go("archive")


def _render_source_link_tab(workspace: WorkspaceService, detail: dict[str, Any]) -> None:
    batch = detail["batch"]
    parent = detail.get("parent_batch") or {}
    if parent:
        st.success(
            f"已关联原始待验批次：{parent.get('display_id') or ''} {parent.get('name') or ''}"
        )
        st.code(parent.get("batch_path") or "", language="text")
        c1, c2 = st.columns(2)
        if c1.button("打开原始批次", use_container_width=True, key="detail_parent_open"):
            _open_batch(str(parent["id"]))
        if c2.button("解除关联", use_container_width=True, key="detail_parent_clear"):
            try:
                workspace.clear_parent_batch(str(batch["id"]), operator=st.session_state.get("operator_name", ""))
                st.success("已解除关联；两个批次的数据都不会被修改。")
                st.rerun()
            except Exception as exc:
                st.error(f"解除失败：{exc}")
    elif batch.get("source_type") == SOURCE_FINAL:
        st.info(
            "申请终验批次保留独立业务身份。系统只在共同任务 ID 唯一匹配时建议关联，"
            "不会因批次名称相似而自动合并。"
        )
        suggestions = detail.get("parent_suggestions") or []
        if not suggestions:
            st.warning("未找到共同任务 ID 的待验批次。可导入原始批次或人工核对后再关联。")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "批次": f"{item.get('display_id') or ''} {item.get('name') or ''}",
                            "日期": item.get("submit_date") or "",
                            "共同任务": int(item.get("overlap") or 0),
                            "合格": int(item.get("qualified_count") or 0),
                            "不合格": int(item.get("rejected_count") or 0),
                            "路径": item.get("batch_path") or "",
                        }
                        for item in suggestions
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
            if len(suggestions) == 1:
                st.caption("仅找到唯一候选，可人工确认关联。")
            options = {
                str(item["id"]): f"{item.get('display_id') or ''} {item.get('name') or ''}（共同任务 {int(item.get('overlap') or 0)}）"
                for item in suggestions
            }
            chosen = st.selectbox(
                "选择原始待验批次",
                options=list(options.keys()),
                format_func=lambda value: options[value],
                key="detail_parent_choice",
            )
            if st.button("确认关联（不合并数据）", type="primary", key="detail_parent_link"):
                try:
                    workspace.link_parent_batch(
                        str(batch["id"]),
                        chosen,
                        operator=st.session_state.get("operator_name", ""),
                    )
                    st.success("已建立来源关联；两个批次仍保持独立身份。")
                    st.rerun()
                except Exception as exc:
                    st.error(f"关联失败：{exc}")
    else:
        st.info("待验批次通常作为来源批次；申请终验批次可在这里关联原始待验批次。")
    if detail.get("child_batches"):
        st.markdown("**已关联的申请终验批次**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "批次": f"{item.get('display_id') or ''} {item.get('name') or ''}",
                        "日期": item.get("submit_date") or "",
                        "路径": item.get("batch_path") or "",
                    }
                    for item in detail["child_batches"]
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )


def _render_library_export(service: BatchService, batch: dict[str, Any]) -> None:
    """批次内导出：合格数据复制到库目录 + 飞书摘要，路径可见但不要求粘贴。"""
    batch_id = str(batch["id"])
    batch_path = batch.get("batch_path") or batch.get("data_root") or ""
    db = service.db

    st.markdown("**导出合格数据到库目录**")
    st.caption(
        "只复制，不移动源目录；按批次相对业务根目录的层级镜像到库根目录。"
        "已有层级直接使用，没有则新建；默认不覆盖库中同名数据组。"
    )
    current_root = get_library_root(db)
    root_value = st.text_input(
        "库根目录",
        value=current_root,
        placeholder=r"例如 D:\合格数据库",
        key=f"detail_lib_root_{batch_id}",
    )
    c1, c2 = st.columns([1, 2])
    if c1.button("保存库根目录", use_container_width=True, key=f"detail_lib_save_{batch_id}"):
        ok, detail = validate_library_root(root_value, db=db)
        if not ok:
            st.error(detail)
        else:
            save_library_root(db, detail)
            st.success(f"已保存库根目录：{detail}")
            current_root = detail

    source_root = batch.get("source_root_path") or resolve_source_root(db, batch_path)
    target = ""
    if root_value.strip() and batch_path:
        try:
            target = build_library_target(
                root_value,
                batch_path=batch_path,
                source_root_path=source_root,
                fallback_name=batch.get("name") or "",
            )
            st.caption(f"本次目标：`{target}`")
        except ValueError as exc:
            st.warning(str(exc))
    else:
        st.caption("填写库根目录后显示本次目标路径。")

    groups = qualified_groups(db, batch_id)
    if groups:
        st.caption(f"数据库中的合格数据组：{len(groups)} 组（按每组最新验收记录判定）")
    else:
        st.info("数据库暂无合格数据。请先在“验收记录”导入记录，或在正式质检页导出。")
    confirm = st.checkbox(
        "我确认复制合格数据（不移动、不删除源目录）",
        value=False,
        key=f"detail_lib_confirm_{batch_id}",
    )
    overwrite = st.checkbox(
        "覆盖库中已存在的同名数据组",
        value=False,
        key=f"detail_lib_overwrite_{batch_id}",
    )
    if st.button(
        "📚 导出合格数据到库目录",
        type="primary",
        use_container_width=True,
        disabled=not (groups and target and confirm),
        key=f"detail_lib_export_{batch_id}",
    ):
        with st.status("正在导出合格数据…", expanded=True) as status:
            def on_progress(done: int, total: int, message: str) -> None:
                status.update(label=f"{message}（{done}/{total}）")

            result = export_qualified_groups(
                groups,
                target,
                overwrite=overwrite,
                progress=on_progress,
            )
            status.update(
                label="导出完成" if result.ok else "导出完成，但有异常",
                state="complete" if result.ok else "error",
            )
        st.session_state[f"_lib_result_{batch_id}"] = result
        st.rerun()

    result = st.session_state.get(f"_lib_result_{batch_id}")
    if result is not None:
        if result.ok:
            st.success(result.message())
        else:
            st.warning(result.message())
        if result.errors:
            st.code("\n".join(result.errors), language="text")

    st.divider()
    st.markdown("**飞书摘要**")
    summary = daily_summary([batch], date=batch.get("submit_date") or today_str())
    st.code(summary, language="text")
    st.caption("复制上面一行粘贴到飞书表格即可；本工具不联网。")


def render_batch_detail(service: BatchService, workspace: WorkspaceService | None = None) -> None:
    if workspace is None:
        workspace = WorkspaceService()
    dashboard = BatchDashboard(service.db)
    all_rows = dashboard.batches()
    if not all_rows:
        st.info("还没有已确认批次。请先到工作台刷新业务目录并确认新批次。")
        return
    batch_id = str(st.session_state.get("management_batch_id") or "")
    if not batch_id or not any(row["id"] == batch_id for row in all_rows):
        choice = st.selectbox(
            "选择批次",
            options=[row["id"] for row in all_rows],
            format_func=lambda value: _batch_label(next(row for row in all_rows if row["id"] == value)),
            key="mgmt_detail_select",
        )
        if st.button("打开批次详情", type="primary", key="mgmt_detail_open"):
            _open_batch(str(choice))
        return

    detail = dashboard.batch_detail(batch_id)
    if detail is None:
        st.warning("批次不存在或已被删除。")
        return
    batch = detail["batch"]
    if st.button("← 返回工作台", key="mgmt_detail_back"):
        _go("workbench")

    st.title(f"📦 {batch.get('name') or batch.get('id')}")
    st.caption(
        f"{batch.get('display_id') or ''} · {batch.get('source_label')} · "
        f"项目 {batch.get('project_name') or batch.get('project') or '未填写'} · "
        f"团队 {batch.get('team') or '未指定'} · "
        f"{batch.get('category_l1') or '未分类'} / {batch.get('category_l2') or '未分类'} · "
        f"日期 {batch.get('submit_date') or '未填写'}"
    )

    action, action_label = next_action(batch)
    total = int(batch.get("task_count") or 0)
    accepted = int(batch.get("accepted_records") or 0)
    action_col, record_col, note_col = st.columns([1, 1, 3])
    if action_col.button(
        action_label,
        type="primary",
        use_container_width=True,
        key="detail_primary_action",
    ):
        _run_batch_action(batch, action, key_prefix="detail")
    if record_col.button("验收记录", use_container_width=True, key="detail_records_open"):
        st.session_state["ri_batch"] = batch_id
        _go("record_import")
    note_col.caption(f"{status_word(batch)} · {progress_text(batch)}")
    st.progress(min(1.0, accepted / total) if total else 0.0)

    _metric_row(
        [
            ("数据量", total, "数据组数量"),
            ("图片数", batch.get("image_count", 0), "扫描得到"),
            ("已验收", accepted, "已导入验收记录"),
            ("合格", batch.get("accepted_qualified", 0), "已导入验收记录"),
            ("不合格", batch.get("accepted_rejected", 0), "已导入验收记录"),
            ("剩余", batch.get("remaining_count", 0), "批次统计"),
        ]
    )

    with st.expander("📤 导出与完成本批", expanded=False):
        _render_library_export(service, batch)

    tab_progress, tab_acceptance, tab_advanced = st.tabs(["进度", "验收记录", "高级"])
    with tab_progress:
        st.dataframe(_stage_frame(detail), use_container_width=True, hide_index=True)
        if not batch.get("accepted_records"):
            st.info("暂无已导入验收记录；系统不会把“没有导出表”判断为验收未开始。")
        st.write(f"最近活动：{_fmt_time(batch.get('last_activity')) or '无'}")
        st.write(f"供应商：{batch.get('supplier') or '未填写'}")
        st.write(f"格式最近时间：{_fmt_time(batch.get('format_last_time')) or '未检查'}")
        st.write(f"翻译最近时间：{_fmt_time(batch.get('translation_last_time')) or '未翻译'}")
        with st.expander("来源目录与路径", expanded=False):
            st.write(f"项目：{batch.get('project_name') or '未填写'}")
            st.write(f"业务来源：{batch.get('source_label')}")
            st.write(f"来源根目录：`{batch.get('source_root_path') or batch.get('data_root') or '未填写'}`")
            st.write(f"批次目录：`{batch.get('batch_path') or '未填写'}`")
            st.write(f"相对路径：`{batch.get('relative_path') or '未填写'}`")
            if batch.get("identity"):
                st.markdown("**提交身份信息（原始）**")
                st.json(batch["identity"])
        if detail["sub_batches"]:
            st.markdown("**子批次**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "子批次": item.get("name") or "",
                            "数据量": item.get("data_count", 0),
                            "状态": status_label(item.get("status")),
                            "备注": item.get("note") or "",
                            "创建时间": _fmt_time(item.get("created_at")),
                        }
                        for item in detail["sub_batches"]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        if detail["status_logs"]:
            st.markdown("**状态记录**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "时间": _fmt_time(item.get("created_at")),
                            "从": status_label(item.get("from_status")),
                            "到": status_label(item.get("to_status")),
                            "操作员": item.get("operator") or "",
                            "原因": item.get("reason") or "",
                        }
                        for item in detail["status_logs"]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
    with tab_acceptance:
        _render_acceptance_tab(detail)
    with tab_advanced:
        with st.expander("🔎 格式检查", expanded=False):
            _render_format_tab(detail)
        with st.expander("🌐 翻译预审", expanded=False):
            _render_translation_tab(detail)
        with st.expander("🗄️ 合格归档（哈希校验）", expanded=False):
            _render_archive_tab(detail)
        with st.expander("🔗 来源关联", expanded=False):
            _render_source_link_tab(workspace, detail)
        with st.expander("🧩 数据组", expanded=False):
            if detail["groups"]:
                st.caption(f"共 {len(detail['groups'])} 个数据组；只显示索引，不加载图片。")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "任务ID": item.get("task_id") or "",
                                "验收员": item.get("user_name") or "",
                                "图片": item.get("image_count", 0),
                                "TXT": item.get("txt_count", 0),
                                "文件": item.get("file_count", 0),
                                "状态": status_label(item.get("status")),
                                "路径": item.get("normalized_path") or "",
                            }
                            for item in detail["groups"]
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("该批次没有数据组。")
        with st.expander("⚙️ 批次状态操作", expanded=False):
            _render_batch_actions(service, batch)
