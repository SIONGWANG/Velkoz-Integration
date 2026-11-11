"""格式检查页面（1.25.2）。

只读检查源目录；报告只写入管理数据目录或用户指定目录。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import pandas as pd
import streamlit as st

from modules.common.timeutil import format_duration
from modules.config import app_config
from modules.format_checker.errors import code_title
from modules.format_checker.models import FormatCheckRequest, FormatCheckResult, Template
from modules.format_checker.report import issue_catalog_rows
from modules.format_checker.service import FormatCheckerService
from modules.format_checker.templates import (
    SCOPE_LABELS,
    parse_template_json,
)

_RESULT_LABELS = {
    "pass": "合格",
    "fail": "异常",
    "warn": "警告",
    "skipped": "跳过",
}
_SEVERITY_LABELS = {"error": "错误", "warn": "警告", "info": "提示"}


def _fmt_time(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ")[:19]


def _result_label(value: str) -> str:
    return _RESULT_LABELS.get(value, value)


def _mode_label(value: str | None) -> str:
    return {"single": "单图", "multi": "多图"}.get(value or "", value or "")


def _task_frame(result: FormatCheckResult) -> pd.DataFrame:
    rows = [
        {
            "任务ID": task.task_id,
            "路径": task.task_path,
            "模式": _mode_label(task.detected_mode),
            "结果": _result_label(task.result),
            "图片": task.image_count,
            "TXT": task.txt_count,
            "文件": task.file_count,
            "问题数": task.issue_count,
            "指纹": task.fingerprint[:12],
        }
        for task in result.tasks
    ]
    return pd.DataFrame(rows)


def _issue_frame(result: FormatCheckResult) -> pd.DataFrame:
    rows = []
    for issue in result.run_issues:
        rows.append(
            {
                "任务ID": issue.task_id,
                "路径": issue.task_path,
                "错误码": issue.code,
                "级别": _SEVERITY_LABELS.get(issue.severity, issue.severity),
                "文件": issue.file_path or "",
                "期望": issue.expected or "",
                "实际": issue.actual or "",
                "说明": issue.message,
                "建议": issue.suggestion or "",
            }
        )
    for task in result.tasks:
        for issue in task.issues:
            rows.append(
                {
                    "任务ID": issue.task_id or task.task_id,
                    "路径": issue.task_path or task.task_path,
                    "错误码": issue.code,
                    "级别": _SEVERITY_LABELS.get(issue.severity, issue.severity),
                    "文件": issue.file_path or "",
                    "期望": issue.expected or "",
                    "实际": issue.actual or "",
                    "说明": issue.message,
                    "建议": issue.suggestion or "",
                }
            )
    return pd.DataFrame(rows)


def _template_options(service: FormatCheckerService) -> dict[str, str]:
    options: dict[str, str] = {}
    for item in service.list_templates():
        builtin = "内置" if int(item.get("builtin") or 0) else "自定义"
        options[item["id"]] = (
            f"{item['name']}（{builtin}，v{item.get('active_version') or '?'}）"
        )
    if not options:
        options["builtin_default"] = "内置默认模板"
    return options


def _progress_callback(state: dict):
    def callback(done: int, total: int, message: str) -> None:
        state["progress"] = {
            "done": int(done),
            "total": int(total),
            "message": message,
        }

    return callback


def _run_worker(state: dict, request: FormatCheckRequest) -> None:
    service: FormatCheckerService | None = None
    try:
        service = FormatCheckerService()
        state["result"] = service.run_check(
            request,
            progress=_progress_callback(state),
            should_cancel=state["cancel"].is_set,
            diff_with_previous=bool(request.previous_run_id),
            batch_name=os.path.basename(request.root_path),
        )
    except Exception as exc:  # 线程内异常传回 UI
        state["error"] = str(exc)
    finally:
        state["done"] = True
        if service is not None:
            service.close()


def _render_runner() -> None:
    state = st.session_state.get("_fmt_runner")
    if not state:
        return
    if not state["done"]:
        progress = state.get("progress") or {}
        total = int(progress.get("total") or 0)
        done = int(progress.get("done") or 0)
        ratio = min(0.99, done / total) if total else 0.0
        st.progress(ratio, text=progress.get("message") or "正在检查…")
        if st.button("⏹ 停止检查", use_container_width=True, key="fmt_stop"):
            state["cancel"].set()
            st.info("已请求停止；已检查的结果会保留。")
        time.sleep(0.4)
        st.rerun()
        return

    st.session_state["_fmt_result"] = state.get("result")
    st.session_state["_fmt_error"] = state.get("error") or ""
    st.session_state["_fmt_runner"] = None
    st.rerun()


def _render_result(result: FormatCheckResult, service: FormatCheckerService) -> None:
    summary = result.summary
    if summary.status == "cancelled":
        st.warning("检查已取消，以下为已检查部分的结果。")
    elif summary.status == "failed":
        st.error("检查失败。")
    else:
        st.success(
            f"检查完成：{summary.scanned_tasks} 个任务，"
            f"合格 {summary.passed_tasks}，异常 {summary.failed_tasks}，"
            f"警告 {summary.warning_tasks}，耗时 {format_duration(summary.duration_ms)}"
        )

    if summary.truncated:
        st.warning("达到最大任务数上限，结果为部分结果。可提高上限后重扫。")
    if summary.integrity_status != "ok":
        st.error("检查期间检测到源目录发生变化（完整性校验未通过），建议确认占用程序后重跑。")
        if result.integrity_changes:
            st.dataframe(
                pd.DataFrame(result.integrity_changes),
                use_container_width=True,
                hide_index=True,
            )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("任务", summary.scanned_tasks)
    c2.metric("合格", summary.passed_tasks)
    c3.metric("异常", summary.failed_tasks)
    c4.metric("警告", summary.warning_tasks)
    c5.metric("完整性", "正常" if summary.integrity_status == "ok" else "有变化")

    if result.report_paths:
        st.caption("报告：" + "；".join(f"{key} = {path}" for key, path in result.report_paths.items()))
        xlsx = result.report_paths.get("xlsx")
        if xlsx and os.path.isfile(xlsx):
            try:
                with open(xlsx, "rb") as handle:
                    st.download_button(
                        "⬇️ 下载 Excel 报告",
                        data=handle.read(),
                        file_name=os.path.basename(xlsx),
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                        key=f"fmt_download_{summary.run_id}",
                    )
            except OSError as exc:
                st.warning(f"报告读取失败：{exc}")

    task_frame = _task_frame(result)
    issue_frame = _issue_frame(result)
    tab_tasks, tab_issues, tab_codes, tab_json = st.tabs(
        ["任务结果", "异常明细", "错误码汇总", "完整结果 JSON"]
    )
    with tab_tasks:
        if task_frame.empty:
            st.info("没有检查到任务。")
        else:
            col_search, col_result = st.columns([3, 1])
            keyword = col_search.text_input("搜索任务 ID / 路径", key=f"fmt_task_search_{summary.run_id}")
            result_filter = col_result.selectbox(
                "结果",
                ["全部", "合格", "异常", "警告", "跳过"],
                key=f"fmt_task_filter_{summary.run_id}",
            )
            filtered = task_frame
            if keyword.strip():
                mask = filtered["任务ID"].str.contains(keyword.strip(), case=False, na=False) | filtered[
                    "路径"
                ].str.contains(keyword.strip(), case=False, na=False)
                filtered = filtered[mask]
            if result_filter != "全部":
                filtered = filtered[filtered["结果"] == result_filter]
            st.dataframe(filtered, use_container_width=True, hide_index=True)

            tasks = {task.task_path: task for task in result.tasks}
            if tasks:
                chosen = st.selectbox(
                    "打开任务文件夹",
                    options=list(tasks.keys()),
                    format_func=lambda path: tasks[path].task_path,
                    key=f"fmt_task_open_{summary.run_id}",
                )
                if st.button("📂 在资源管理器中打开", key=f"fmt_task_open_btn_{summary.run_id}"):
                    try:
                        os.startfile(chosen)  # type: ignore[attr-defined]
                    except Exception as exc:
                        st.error(f"打开失败：{exc}")
                task = tasks[chosen]
                if task.issues:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "错误码": item.code,
                                    "级别": _SEVERITY_LABELS.get(item.severity, item.severity),
                                    "文件": item.file_path or "",
                                    "期望": item.expected or "",
                                    "实际": item.actual or "",
                                    "说明": item.message,
                                    "建议": item.suggestion or "",
                                }
                                for item in task.issues
                            ]
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.success("该任务没有发现问题。")
    with tab_issues:
        if issue_frame.empty:
            st.success("没有异常。")
        else:
            codes = ["全部"] + sorted(issue_frame["错误码"].dropna().unique().tolist())
            chosen_code = st.selectbox(
                "错误码", codes, key=f"fmt_issue_code_{summary.run_id}"
            )
            filtered = issue_frame if chosen_code == "全部" else issue_frame[issue_frame["错误码"] == chosen_code]
            st.dataframe(filtered, use_container_width=True, hide_index=True)
    with tab_codes:
        if summary.issues_by_code:
            rows = [
                {"错误码": code, "数量": count, "说明": code_title(code)}
                for code, count in summary.issues_by_code.items()
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.success("本次检查没有产生错误码。")
        with st.expander("错误码字典"):
            st.dataframe(
                pd.DataFrame(
                    issue_catalog_rows(),
                    columns=["错误码", "级别", "说明", "建议"],
                ),
                use_container_width=True,
                hide_index=True,
            )
    with tab_json:
        st.download_button(
            "⬇️ 下载结果 JSON",
            data=json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            file_name=f"{summary.run_id}.json",
            mime="application/json",
            use_container_width=True,
            key=f"fmt_json_{summary.run_id}",
        )
        st.json(result.to_dict())


def _batch_list(service: FormatCheckerService) -> list[dict[str, Any]]:
    rows = service.db.connect().execute(
        "SELECT id, display_id, name, batch_path, data_root, category_l1, category_l2 FROM batches"
        " ORDER BY COALESCE(submit_date, created_at) DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def _render_check(service: FormatCheckerService) -> None:
    if st.session_state.get("_fmt_runner"):
        _render_runner()
        return

    error = st.session_state.pop("_fmt_error", "")
    if error:
        st.error(f"检查失败：{error}")

    context_batch: dict[str, Any] | None = None
    context_id = str(st.session_state.get("fmt_batch_id") or "")
    if context_id:
        for item in _batch_list(service):
            if item["id"] == context_id:
                context_batch = item
                break
        if context_batch is None:
            st.warning("批次上下文已失效，已切换为手动路径模式。")
            st.session_state.pop("fmt_batch_id", None)
    batch_id = str(context_batch["id"]) if context_batch else ""
    if context_batch:
        st.success(
            f"当前批次：{context_batch.get('display_id') or ''} {context_batch.get('name') or ''} · "
            f"路径：`{context_batch.get('batch_path') or context_batch.get('data_root') or ''}`"
        )
        if st.button("退出批次模式（改用手动路径）", key="fmt_exit_batch"):
            st.session_state.pop("fmt_batch_id", None)
            st.session_state.pop("format_root", None)
            st.rerun()
        preferred = ""
        for item in service.list_templates():
            scope = str(item.get("scope") or "")
            scope_id = str(item.get("scope_id") or "")
            if scope == "category" and scope_id and scope_id in {
                context_batch.get("category_l1") or "",
                context_batch.get("category_l2") or "",
            }:
                preferred = str(item["id"])
                break
        if preferred and "fmt_template_id" not in st.session_state:
            st.session_state["fmt_template_id"] = preferred

    options = _template_options(service)
    col_template, col_version = st.columns([3, 2])
    template_id = col_template.selectbox(
        "命名模板",
        options=list(options.keys()),
        format_func=lambda key: options[key],
        key="fmt_template_id",
    )
    loaded = service.get_template_with_version(template_id)
    if loaded is None:
        st.error("模板读取失败")
        return
    template, version_id, version = loaded
    col_version.caption(f"当前版本：v{version}；模板作用域：{SCOPE_LABELS.get(template.scope, template.scope)}")

    paths = app_config.describe_paths()
    with st.expander("模板摘要与只读说明", expanded=False):
        st.write(f"模板：`{template.name}`（{template.id} v{version}）")
        st.write(f"ID 规则：{_describe_id(template)}")
        st.write("检查模式：" + "、".join(_mode_label(item) for item in template.modes))
        st.write(f"额外文件策略：{template.extra_files}；警告是否算失败：{template.warn_as_fail}")
        st.write(f"默认报告目录：`{service.report_directory()}`")
        st.caption("检查只读取目录项、文件大小和修改时间；不写入、不重命名、不删除任何源文件。")
        st.json(template.to_dict())

    if context_batch:
        root = context_batch.get("batch_path") or context_batch.get("data_root") or ""
        operator = st.text_input(
            "操作员", value=st.session_state.get("operator_name", ""), key="fmt_operator"
        )
        st.caption(f"检查根目录（来自批次）：`{root}`")
    else:
        choices = {
            item["id"]: f"{item.get('display_id') or ''} {item.get('name') or ''}".strip()
            for item in _batch_list(service)
        }
        c_batch, c_operator = st.columns([2, 1])
        chosen_batch = c_batch.selectbox(
            "关联批次（可选；选择后无需填写路径）",
            options=["", *list(choices.keys())],
            format_func=lambda key: "不关联批次（手动路径）" if key == "" else choices[key],
            key="fmt_context_batch",
        )
        operator = c_operator.text_input(
            "操作员", value=st.session_state.get("operator_name", ""), key="fmt_operator"
        )
        if chosen_batch:
            row = service.db.connect().execute(
                "SELECT batch_path, data_root FROM batches WHERE id = ?", (chosen_batch,)
            ).fetchone()
            batch_id = chosen_batch
            root = (row["batch_path"] or row["data_root"] or "") if row else ""
            st.caption(f"检查根目录（来自批次）：`{root}`")
        else:
            default_root = st.session_state.get("format_root") or st.session_state.get("root_path", "")
            root = st.text_input("检查根目录", value=default_root, key="fmt_root_input")

    col_recursive, col_max = st.columns(2)
    recursive = col_recursive.checkbox(
        "递归扫描全部层级",
        value=True if batch_id else bool(template.scan.recursive),
        key="fmt_recursive",
    )
    max_tasks = col_max.number_input(
        "最大任务数",
        min_value=100,
        max_value=1_000_000,
        value=int(template.scan.max_tasks or 200000),
        step=1000,
        key="fmt_max_tasks",
    )

    history = service.list_runs(100)
    col_prev, col_only = st.columns(2)
    previous_run_id = ""
    only_anomalies = False
    if history:
        labels = {
            item["id"]: (
                f"{_fmt_time(item.get('started_at'))} | {item.get('status')} | "
                f"{item.get('failed_tasks', 0)} 异常 / {item.get('warning_tasks', 0)} 警告 | "
                f"{item.get('root_path', '')}"
            )
            for item in history
        }
        prev_options = ["", *list(labels.keys())]
        remembered_prev = str(st.session_state.pop("fmt_previous_run_id", "") or "")
        prev_index = prev_options.index(remembered_prev) if remembered_prev in prev_options else 0
        previous_run_id = col_prev.selectbox(
            "上次检查记录（用于对比/仅异常）",
            options=prev_options,
            index=prev_index,
            format_func=lambda key: "不关联" if key == "" else labels[key],
            key="fmt_previous_run",
        )
        remembered_only = bool(st.session_state.pop("fmt_only_anomalies", False))
        only_anomalies = col_only.checkbox(
            "仅检查上次异常/警告任务",
            value=remembered_only,
            disabled=not previous_run_id,
            key="fmt_only_anomalies",
        )
    else:
        st.caption("还没有检查记录。首次检查会创建记录并生成报告。")

    col_preview, col_run = st.columns(2)
    preview_clicked = col_preview.button(
        "🔍 仅预览（不入库）",
        use_container_width=True,
        disabled=not root.strip(),
        key="fmt_preview_btn",
    )
    run_clicked = col_run.button(
        "▶️ 开始检查",
        type="primary",
        use_container_width=True,
        disabled=not root.strip() or bool(st.session_state.get("_fmt_runner")),
        key="fmt_run_btn",
    )

    if preview_clicked:
        try:
            result = service.preview(
                root.strip(),
                template_id=template_id,
                recursive=recursive,
                max_tasks=int(max_tasks),
            )
            st.session_state["_fmt_result"] = result
            st.session_state["format_root"] = root.strip()
        except Exception as exc:
            st.error(f"预览失败：{exc}")

    if run_clicked:
        request = FormatCheckRequest(
            root_path=root.strip(),
            template_id=template_id,
            template_version_id=version_id,
            recursive=bool(recursive),
            only_anomalies=bool(only_anomalies),
            previous_run_id=previous_run_id or None,
            max_tasks=int(max_tasks),
            operator=operator.strip(),
            batch_id=batch_id,
            write_report=True,
        )
        state = {
            "done": False,
            "error": "",
            "result": None,
            "cancel": threading.Event(),
            "progress": {"done": 0, "total": 0, "message": "准备检查…"},
        }
        thread = threading.Thread(
            target=_run_worker, args=(state, request), daemon=True
        )
        state["thread"] = thread
        st.session_state["_fmt_runner"] = state
        st.session_state["format_root"] = root.strip()
        thread.start()
        st.rerun()

    result = st.session_state.get("_fmt_result")
    if result is not None:
        st.divider()
        _render_result(result, service)


def _describe_id(template: Template) -> str:
    rule = template.id_rule
    if rule.mode == "digits":
        return f"{rule.length} 位纯数字"
    if rule.mode == "prefix_digits":
        return f"{rule.prefix} + {rule.length} 位数字"
    return f"正则 {rule.regex}"


def _render_history(service: FormatCheckerService) -> None:
    runs = service.list_runs(200)
    if not runs:
        st.info("还没有检查记录。")
        return
    st.write(f"检查记录：{len(runs)}")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "运行ID": item["id"],
                    "开始时间": _fmt_time(item.get("started_at")),
                    "状态": item.get("status"),
                    "任务": item.get("scanned_tasks"),
                    "合格": item.get("passed_tasks"),
                    "异常": item.get("failed_tasks"),
                    "警告": item.get("warning_tasks"),
                    "完整性": item.get("integrity_status"),
                    "模板": item.get("template_name"),
                    "根目录": item.get("root_path"),
                    "报告": item.get("report_path"),
                }
                for item in runs
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    labels = {item["id"]: f"{_fmt_time(item.get('started_at'))} | {item.get('root_path')}" for item in runs}
    chosen = st.selectbox(
        "查看检查记录",
        options=list(labels.keys()),
        format_func=lambda key: labels[key],
        key="fmt_history_run",
    )
    run = service.get_run(chosen)
    if not run:
        st.error("检查记录读取失败")
        return
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("任务", run.get("scanned_tasks", 0))
    c2.metric("合格", run.get("passed_tasks", 0))
    c3.metric("异常", run.get("failed_tasks", 0))
    c4.metric("警告", run.get("warning_tasks", 0))
    c5.metric("完整性", "正常" if run.get("integrity_status") == "ok" else "有变化")
    st.caption(
        f"模板：{run.get('template_id')} / {run.get('template_version_id') or '?'}；"
        f"报告：{run.get('report_path') or '无'}"
    )

    tasks = service.run_tasks(chosen)
    if tasks:
        st.dataframe(pd.DataFrame(tasks), use_container_width=True, hide_index=True)
    issues = service.run_issues(chosen)
    if issues:
        with st.expander(f"异常明细（{len(issues)}）", expanded=False):
            st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)

    col_rerun, col_rerun_anomalies = st.columns(2)
    if col_rerun.button("🔁 重跑本次检查", use_container_width=True, key=f"fmt_rerun_{chosen}"):
        try:
            with st.spinner("正在重跑…"):
                result = service.rerun(chosen, operator=st.session_state.get("operator_name", ""))
            st.session_state["_fmt_result"] = result
            st.success(f"已重跑并生成新记录：{result.summary.run_id}")
            st.rerun()
        except Exception as exc:
            st.error(f"重跑失败：{exc}")
    if col_rerun_anomalies.button(
        "🔁 仅重跑异常/警告",
        use_container_width=True,
        key=f"fmt_rerun_bad_{chosen}",
    ):
        try:
            with st.spinner("正在重跑异常任务…"):
                result = service.rerun(
                    chosen,
                    only_anomalies=True,
                    operator=st.session_state.get("operator_name", ""),
                )
            st.session_state["_fmt_result"] = result
            st.success(f"已重跑异常任务：{result.summary.run_id}")
            st.rerun()
        except Exception as exc:
            st.error(f"重跑失败：{exc}")

    st.divider()
    st.subheader("对比两次检查")
    if len(runs) < 2:
        st.info("至少需要两条检查记录才能对比。")
        return
    keys = list(labels.keys())
    col_old, col_new = st.columns(2)
    old_run = col_old.selectbox(
        "旧记录", options=keys, format_func=lambda key: labels[key], key="fmt_diff_old"
    )
    default_new = keys[1] if keys[0] == old_run else keys[0]
    new_run = col_new.selectbox(
        "新记录",
        options=keys,
        index=keys.index(default_new),
        format_func=lambda key: labels[key],
        key="fmt_diff_new",
    )
    if st.button("🔍 对比差异", type="primary", use_container_width=True, key="fmt_diff_btn"):
        if old_run == new_run:
            st.warning("请选择两条不同的检查记录。")
        else:
            try:
                st.session_state["_fmt_diff"] = service.diff_runs(old_run, new_run)
            except Exception as exc:
                st.error(f"对比失败：{exc}")
    diff = st.session_state.get("_fmt_diff")
    if not diff:
        return
    st.caption(f"{diff['old_run_id']} → {diff['new_run_id']}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("新增任务", len(diff["added"]))
    c2.metric("消失任务", len(diff["removed"]))
    c3.metric("通过→失败", len(diff["pass_to_fail"]))
    c4.metric("失败→通过", len(diff["fail_to_pass"]))
    rows = []
    for item in diff["added"]:
        rows.append({"类型": "新增", "路径": item["task_path"], "说明": item.get("result", "")})
    for item in diff["removed"]:
        rows.append({"类型": "消失", "路径": item["task_path"], "说明": item.get("result", "")})
    for item in diff["pass_to_fail"]:
        rows.append({"类型": "通过→失败", "路径": item["task_path"], "说明": "结果变化"})
    for item in diff["fail_to_pass"]:
        rows.append({"类型": "失败→通过", "路径": item["task_path"], "说明": "结果变化"})
    for item in diff["code_changed"]:
        rows.append(
            {
                "类型": "错误码变化",
                "路径": item["task_path"],
                "说明": f"新增 {item['added']}；消失 {item['removed']}",
            }
        )
    for item in diff["file_count_changed"]:
        rows.append(
            {
                "类型": "文件数变化",
                "路径": item["task_path"],
                "说明": f"{item['before']} → {item['after']}",
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.success("没有差异。")
    with st.expander("完整差异 JSON"):
        st.json(diff)


def _render_template_manager(service: FormatCheckerService) -> None:
    templates = service.list_templates()
    options = {item["id"]: f"{item['name']}（v{item.get('active_version') or '?'}）" for item in templates}
    if not options:
        st.info("没有模板。")
        return
    chosen = st.selectbox(
        "选择模板",
        options=list(options.keys()),
        format_func=lambda key: options[key],
        key="fmt_tpl_select",
    )
    item = next((row for row in templates if row["id"] == chosen), None)
    loaded = service.get_template_with_version(chosen)
    if item is None or loaded is None:
        st.error("模板读取失败")
        return
    template, version_id, version = loaded
    is_builtin = bool(int(item.get("builtin") or 0))
    st.caption(
        f"版本 v{version}；作用域 {SCOPE_LABELS.get(template.scope, template.scope)}；"
        f"{'内置模板只读' if is_builtin else '自定义模板'}"
    )

    text = st.text_area(
        "模板 JSON",
        value=json.dumps(template.to_dict(), ensure_ascii=False, indent=2),
        height=420,
        key=f"fmt_tpl_json_{chosen}_{version}",
    )
    col_validate, col_save, col_copy = st.columns(3)
    if col_validate.button("✅ 校验 JSON", use_container_width=True, key=f"fmt_tpl_validate_{chosen}"):
        try:
            parsed, unknown = parse_template_json(text)
            st.success("校验通过。")
            if unknown:
                st.warning("未知字段将被忽略：" + "、".join(unknown))
            st.json(parsed.to_dict())
        except ValueError as exc:
            st.error(str(exc))

    copy_name = st.text_input(
        "另存为新模板名称",
        value=f"{template.name}（副本）",
        key=f"fmt_tpl_copy_name_{chosen}",
    )
    if col_save.button(
        "💾 保存为新版本",
        use_container_width=True,
        disabled=is_builtin,
        key=f"fmt_tpl_save_{chosen}",
    ):
        try:
            parsed, unknown = parse_template_json(text)
            if unknown:
                st.warning("未知字段将被忽略：" + "、".join(unknown))
            parsed.id = chosen
            parsed.builtin = False
            service.save_template(
                parsed,
                operator=st.session_state.get("operator_name", ""),
                note="页面保存",
            )
            st.success("已保存为新版本。")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if col_copy.button("📋 复制为新模板", use_container_width=True, key=f"fmt_tpl_copy_{chosen}"):
        try:
            parsed, _ = parse_template_json(text)
            parsed.id = ""
            parsed.name = copy_name.strip() or f"{template.name}（副本）"
            parsed.builtin = False
            service.save_template(
                parsed,
                operator=st.session_state.get("operator_name", ""),
                note=f"复制自 {chosen} v{version}",
            )
            st.success("已复制为新模板。")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    if not is_builtin:
        with st.expander("删除模板", expanded=False):
            st.caption("删除只影响管理数据库中的模板与版本，不删除任何源文件。")
            if st.button("🗑️ 删除该模板", key=f"fmt_tpl_delete_{chosen}"):
                try:
                    service.delete_template(chosen)
                    st.success("已删除模板。")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    with st.expander("模板版本历史", expanded=False):
        versions = service.template_versions(chosen)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "版本": row["version"],
                        "创建时间": _fmt_time(row.get("created_at")),
                        "创建人": row.get("created_by", ""),
                        "备注": row.get("note", ""),
                        "当前": "是" if row["id"] == version_id else "",
                    }
                    for row in versions
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.download_button(
            "⬇️ 导出当前模板 JSON",
            data=json.dumps(template.to_dict(), ensure_ascii=False, indent=2),
            file_name=f"{chosen}_v{version}.json",
            mime="application/json",
            use_container_width=True,
        )

    st.divider()
    st.subheader("导入模板 JSON")
    uploaded = st.file_uploader("选择模板 JSON", type=["json"], key="fmt_tpl_upload")
    if uploaded is not None:
        try:
            raw = uploaded.getvalue().decode("utf-8-sig")
            parsed, unknown = parse_template_json(raw)
            if unknown:
                st.warning("未知字段将被忽略：" + "、".join(unknown))
            st.json(parsed.to_dict())
            if st.button("导入为新模板", type="primary", use_container_width=True, key="fmt_tpl_import"):
                parsed.id = ""
                parsed.version = 1
                parsed.builtin = False
                parsed.name = parsed.name or uploaded.name
                service.save_template(
                    parsed,
                    operator=st.session_state.get("operator_name", ""),
                    note=f"导入自 {uploaded.name}",
                )
                st.success("已导入模板。")
                st.rerun()
        except (ValueError, UnicodeDecodeError) as exc:
            st.error(f"导入失败：{exc}")


def render() -> None:
    st.title("🔎 格式检查（1.25.2）")
    st.caption(
        "只读检查任务目录的单图/多图结构、ID、扩展名与 TXT/PSD；"
        "检查过程不修改源图片/TXT/CSV，报告只写入管理数据目录。"
    )

    try:
        service = FormatCheckerService()
    except Exception as exc:
        st.error(f"管理数据库初始化失败：{exc}")
        return

    try:
        _render_runner()
        tab_check, tab_history, tab_templates = st.tabs(["检查", "历史与对比", "模板管理"])
        with tab_check:
            _render_check(service)
        with tab_history:
            _render_history(service)
        with tab_templates:
            _render_template_manager(service)
    finally:
        service.close()
