"""翻译与预审页面（1.25.3）。

- 扫描只读取目录与 TXT；
- 翻译只调用模型，译文暂存在管理数据目录，不写源文件；
- 用户确认后才写英文 TXT（写前备份、原子写入、失败不覆盖）；
- 语法/一致性检查只出建议，确认后才写。
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime

import pandas as pd
import streamlit as st

from modules.common.timeutil import format_duration
from modules.config import app_config
from modules.precheck.errors import catalog_rows
from modules.precheck.models import (
    PAIR_STATUS_LABELS,
    STATUS_EMPTY_EN,
    STATUS_EMPTY_ZH,
    STATUS_MISSING_EN,
    STATUS_NO_ZH,
    STATUS_TRANSLATED,
    PairingOptions,
    ProviderProfile,
    TranslationRequest,
)
from modules.precheck.providers import PROVIDER_LABELS, PROVIDER_PRESETS
from modules.precheck.secrets import environment_note, mask_key
from modules.precheck.service import PrecheckService

_STATUS_ORDER = [
    STATUS_MISSING_EN,
    STATUS_EMPTY_EN,
    STATUS_TRANSLATED,
    STATUS_EMPTY_ZH,
    STATUS_NO_ZH,
]
_RESULT_LABELS = {
    "translated": "翻译成功",
    "failed": "失败",
    "skipped": "跳过",
}
_CHECK_LABELS = {"grammar": "英文语法检查", "consistency": "中英文一致性检查"}


def _fmt_time(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ")[:19]


def _status_label(value: str) -> str:
    if value.startswith("unknown:"):
        return f"未知（{value.split(':', 1)[1]}）"
    for key, label in _RESULT_LABELS.items():
        if key == value:
            return label
    return PAIR_STATUS_LABELS.get(value, value)


def _pairs_frame(items) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "任务ID": item.task_id,
                "状态": item.status_label,
                "中文TXT": os.path.basename(item.zh_txt_path) if item.zh_txt_path else "",
                "英文TXT": os.path.basename(item.en_txt_path) if item.en_txt_path else "",
                "中文字数": item.zh_length,
                "英文词数": item.en_length,
                "错误码": item.error_code,
                "说明": item.error_message,
                "路径": item.folder_path,
            }
            for item in items
        ]
    )


def _progress_callback(state: dict):
    def callback(done: int, total: int, message: str) -> None:
        state["progress"] = {
            "done": int(done),
            "total": int(total),
            "message": message,
        }

    return callback


def _translate_worker(state: dict, request: TranslationRequest) -> None:
    service: PrecheckService | None = None
    try:
        service = PrecheckService()
        state["result"] = service.translate(
            request,
            progress=_progress_callback(state),
            should_cancel=state["cancel"].is_set,
        )
    except Exception as exc:  # 线程内异常回传 UI，不打印密钥
        state["error"] = str(exc)
    finally:
        state["done"] = True
        if service is not None:
            service.close()


def _check_worker(state: dict, job_id: str, kind: str, task_paths: list[str]) -> None:
    service: PrecheckService | None = None
    try:
        service = PrecheckService()
        state["result"] = service.check(
            job_id,
            kind,
            task_paths=task_paths or None,
            progress=_progress_callback(state),
            should_cancel=state["cancel"].is_set,
        )
    except Exception as exc:
        state["error"] = str(exc)
    finally:
        state["done"] = True
        if service is not None:
            service.close()


def _render_runner() -> None:
    state = st.session_state.get("_pc_runner")
    if not state:
        return
    if not state["done"]:
        progress = state.get("progress") or {}
        total = int(progress.get("total") or 0)
        done = int(progress.get("done") or 0)
        ratio = min(0.99, done / total) if total else 0.0
        st.progress(ratio, text=progress.get("message") or "正在处理…")
        if st.button("⏹ 停止", use_container_width=True, key="pc_stop"):
            state["cancel"].set()
            st.info("已请求停止；已完成的结果会保留。")
        time.sleep(0.4)
        st.rerun()
        return

    st.session_state["_pc_result"] = state.get("result")
    st.session_state["_pc_result_kind"] = state.get("kind")
    st.session_state["_pc_error"] = state.get("error") or ""
    st.session_state["_pc_runner"] = None
    st.rerun()


def _start_runner(kind: str, target, args: tuple) -> None:
    state = {
        "done": False,
        "error": "",
        "result": None,
        "kind": kind,
        "cancel": threading.Event(),
        "progress": {"done": 0, "total": 0, "message": "准备中…"},
    }
    thread = threading.Thread(target=target, args=(state, *args), daemon=True)
    state["thread"] = thread
    st.session_state["_pc_runner"] = state
    thread.start()
    st.rerun()


def _profile_options(service: PrecheckService) -> dict[str, str]:
    options: dict[str, str] = {}
    for profile in service.list_profiles():
        options[profile.id] = f"{profile.name}（{profile.model or '未填模型'}）"
    return options


# ── 翻译 ─────────────────────────────────────────────────


def _render_translate(service: PrecheckService) -> None:
    if st.session_state.get("_pc_runner") and st.session_state.get("_pc_runner", {}).get(
        "kind"
    ) == "translate":
        _render_runner()
        return

    if st.session_state.get("_pc_result_kind") == "translate":
        error = st.session_state.pop("_pc_error", "")
        if error:
            st.error(f"翻译失败：{error}")

    profiles = _profile_options(service)
    if not profiles:
        st.warning("还没有模型配置，请先到“模型与提示词”添加。")
        return
    default_root = (
        st.session_state.get("precheck_root") or st.session_state.get("root_path", "")
    )
    col_root, col_operator, col_profile = st.columns([3, 1, 2])
    root = col_root.text_input("根目录", value=default_root, key="pc_root_input")
    operator = col_operator.text_input(
        "操作员", value=st.session_state.get("operator_name", ""), key="pc_operator"
    )
    active = service.active_profile_id()
    profile_ids = list(profiles.keys())
    profile_id = col_profile.selectbox(
        "模型配置",
        options=profile_ids,
        index=profile_ids.index(active) if active in profile_ids else 0,
        format_func=lambda key: profiles[key],
        key="pc_profile",
    )
    profile = service.get_profile(profile_id)
    if profile is not None:
        st.caption(
            f"提供方：{PROVIDER_LABELS.get(profile.provider, profile.provider)}；"
            f"模型：{profile.model or '未填写'}；并发 {profile.concurrency}；"
            f"超时 {profile.timeout:g}s；重试 {profile.max_retries}"
        )
        if not service.has_api_key(profile_id):
            st.warning("该配置还没有保存 API Key；可在“模型与提示词”中填写。")

    col_recursive, col_only_missing = st.columns(2)
    recursive = col_recursive.checkbox(
        "递归扫描全部层级", value=False, key="pc_recursive"
    )
    only_missing = col_only_missing.checkbox(
        "只翻译缺失/为空的英文", value=True, key="pc_only_missing"
    )

    if st.button(
        "🔍 扫描 TXT 配对",
        type="primary",
        use_container_width=True,
        disabled=not root.strip(),
        key="pc_scan_btn",
    ):
        try:
            with st.spinner("正在读取目录与 TXT…"):
                result = service.scan(
                    root.strip(), PairingOptions(recursive=bool(recursive))
                )
            st.session_state["_pc_pairs"] = result
            st.session_state["precheck_root"] = root.strip()
        except Exception as exc:
            st.error(f"扫描失败：{exc}")

    scan = st.session_state.get("_pc_pairs")
    if scan is None:
        st.caption("扫描只读取任务目录与 TXT，不修改任何源文件。")
        return
    if scan.cancelled:
        st.warning("扫描已取消。")
    if scan.issues:
        with st.expander(f"扫描提示（{len(scan.issues)}）", expanded=False):
            for issue in scan.issues:
                st.write(f"`{issue.code}` {issue.message}")

    counts = scan.counts()
    columns = st.columns(5)
    columns[0].metric("任务", len(scan.items))
    columns[1].metric("缺英文", counts.get(STATUS_MISSING_EN, 0))
    columns[2].metric("英文为空", counts.get(STATUS_EMPTY_EN, 0))
    columns[3].metric("已有英文", counts.get(STATUS_TRANSLATED, 0))
    columns[4].metric("缺中文", counts.get(STATUS_NO_ZH, 0))

    if not scan.items:
        st.info("没有扫描到任务。可检查根目录、ID 规则与 TXT 后缀。")
        return

    tab_all, tab_pending = st.tabs(["全部任务", "待翻译任务"])
    with tab_all:
        st.dataframe(_pairs_frame(scan.items), use_container_width=True, hide_index=True)
    pending_items = [
        item
        for item in scan.items
        if item.status in {STATUS_MISSING_EN, STATUS_EMPTY_EN}
        or (not only_missing and item.status == STATUS_TRANSLATED)
    ]
    with tab_pending:
        if not pending_items:
            st.success("没有待翻译任务。")
        else:
            st.dataframe(
                _pairs_frame(pending_items), use_container_width=True, hide_index=True
            )

    if not pending_items:
        st.success("没有待翻译任务。")
        return

    selected = st.multiselect(
        "本次翻译的任务（默认全选待翻译任务）",
        options=[item.folder_path for item in pending_items],
        default=[item.folder_path for item in pending_items],
        format_func=lambda path: os.path.basename(path),
        key="pc_selected_paths",
    )
    if st.button(
        "🌐 开始翻译（生成预览，不写源文件）",
        type="primary",
        use_container_width=True,
        disabled=not selected,
        key="pc_translate_btn",
    ):
        request = TranslationRequest(
            root_path=st.session_state.get("precheck_root") or root.strip(),
            profile_id=profile_id,
            task_paths=tuple(selected),
            only_missing=bool(only_missing),
            operator=operator.strip(),
            recursive=bool(recursive),
        )
        _start_runner("translate", _translate_worker, (request,))

    result = st.session_state.get("_pc_result")
    if result is not None and st.session_state.get("_pc_result_kind") == "translate":
        st.divider()
        _render_translation_result(service, result)


def _render_translation_result(service: PrecheckService, result) -> None:
    if result.status == "cancelled":
        st.warning("翻译已取消，以下为已完成部分。")
    elif result.status == "partial":
        st.warning(
            f"翻译部分完成：成功 {len(result.translated)}，失败 {len(result.failed)}，"
            f"跳过 {len(result.skipped)}，耗时 {format_duration(_duration(result))}"
        )
    else:
        st.success(
            f"翻译完成：成功 {len(result.translated)}，失败 {len(result.failed)}，"
            f"跳过 {len(result.skipped)}，耗时 {format_duration(_duration(result))}"
        )
    st.caption(
        f"任务 {result.job_id}；Token 输入 {result.token_input} / 输出 "
        f"{result.token_output}；预估费用 {result.cost:.4f} {result.currency}。"
        "译文暂存在管理数据目录，确认后才写英文 TXT。"
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "任务ID": item.item.task_id,
                    "状态": _RESULT_LABELS.get(item.status, item.status),
                    "错误码": item.error_code,
                    "说明": item.error_message,
                    "Token输入": item.token_input,
                    "Token输出": item.token_output,
                    "费用": round(item.cost, 6),
                    "重试": item.attempts,
                    "英文预览": item.english,
                }
                for item in result.results
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    if not result.translated:
        return
    st.divider()
    st.subheader("确认写入英文 TXT")
    st.caption(
        "写入前会备份到管理数据目录；目标英文 TXT 已存在且非空时默认拒绝覆盖。"
    )
    overwrite = st.checkbox(
        "覆盖已存在的非空英文 TXT（会先备份）", value=False, key="pc_overwrite"
    )
    if st.button(
        "💾 确认写入英文 TXT",
        type="primary",
        use_container_width=True,
        key="pc_write_btn",
    ):
        try:
            outcome = service.write_results(
                result.job_id, overwrite=bool(overwrite)
            )
            st.success(
                f"写入完成：成功 {outcome['written']}，失败 {outcome['failed']}。"
            )
            if outcome["failed"]:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "任务ID": row["task_id"],
                                "错误码": row["error_code"],
                                "说明": row["error_message"],
                                "目标": row["en_txt_path"],
                            }
                            for row in outcome["rows"]
                            if not row["written"]
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            st.session_state["_pc_last_write"] = outcome
        except Exception as exc:
            st.error(f"写入失败：{exc}")
    _render_export(service, result.job_id)


def _duration(result) -> int:
    try:
        start = datetime.fromisoformat(result.started_at)
        end = datetime.fromisoformat(result.finished_at)
        return max(0, int((end - start).total_seconds() * 1000))
    except (TypeError, ValueError):
        return 0


def _render_export(service: PrecheckService, job_id: str | None) -> None:
    if not job_id:
        return
    st.divider()
    st.subheader("导出报告")
    default_dir = str(service.report_root / datetime.now().strftime("%Y-%m-%d"))
    out_dir = st.text_input(
        "报告输出目录（不能位于源目录内）", value=default_dir, key=f"pc_export_dir_{job_id}"
    )
    col_xlsx, col_csv = st.columns(2)
    for column, fmt, label in (
        (col_xlsx, "xlsx", "⬇️ 生成 Excel 报告"),
        (col_csv, "csv", "⬇️ 生成 CSV 报告"),
    ):
        if column.button(label, use_container_width=True, key=f"pc_export_{fmt}_{job_id}"):
            try:
                paths = service.export_report(
                    job_id, out_dir or None, formats=(fmt,)
                )
                st.success("；".join(f"{key} = {path}" for key, path in paths.items()))
            except Exception as exc:
                st.error(f"导出失败：{exc}")


# ── 语法与一致性 ─────────────────────────────────────────


def _job_options(service: PrecheckService) -> dict[str, str]:
    labels: dict[str, str] = {}
    for job in service.list_jobs(200):
        if job.get("provider") == "external_report":
            continue
        counts = (job.get("summary") or {}).get("counts") or {}
        labels[job["id"]] = (
            f"{_fmt_time(job.get('started_at'))} | {job.get('status')} | "
            f"成功 {counts.get('translated', 0)} / 失败 {counts.get('failed', 0)} | "
            f"{job.get('root_path', '')}"
        )
    return labels


def _render_checks(service: PrecheckService) -> None:
    if st.session_state.get("_pc_runner") and st.session_state.get("_pc_runner", {}).get(
        "kind"
    ) == "check":
        _render_runner()
        return

    if st.session_state.get("_pc_result_kind") == "check":
        error = st.session_state.pop("_pc_error", "")
        if error:
            st.error(f"检查失败：{error}")

    labels = _job_options(service)
    if not labels:
        st.info("还没有翻译任务记录，请先在“翻译”页完成一次翻译。")
        return
    job_id = st.selectbox(
        "翻译任务",
        options=list(labels.keys()),
        format_func=lambda key: labels[key],
        key="pc_check_job",
    )
    kind_label = st.radio(
        "检查类型",
        options=list(_CHECK_LABELS.values()),
        horizontal=True,
        key="pc_check_kind",
    )
    kind = next(key for key, label in _CHECK_LABELS.items() if label == kind_label)
    items = service.job_items(job_id, include_text=False)
    selectable = [row["folder_path"] for row in items if row.get("status") == "translated"]
    selected = st.multiselect(
        "检查任务（默认全部已翻译任务）",
        options=selectable,
        default=selectable,
        format_func=lambda path: os.path.basename(path),
        key="pc_check_paths",
    )
    if st.button(
        "🔍 开始检查（只出建议，不自动修改）",
        type="primary",
        use_container_width=True,
        disabled=not selected,
        key="pc_check_btn",
    ):
        _start_runner("check", _check_worker, (job_id, kind, selected))

    result = st.session_state.get("_pc_result")
    if result is not None and st.session_state.get("_pc_result_kind") == "check":
        st.divider()
        _render_check_result(service, result)


def _render_check_result(service: PrecheckService, result) -> None:
    if result.cancelled:
        st.warning("检查已取消，以下为已完成部分。")
    else:
        st.success(
            f"检查完成：通过 {result.passed}，未通过 {result.failed}，"
            f"错误 {result.errored}；Token 输入 {result.token_input} / 输出 "
            f"{result.token_output}；费用 {result.cost:.4f}。"
        )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "任务ID": item.task_id,
                    "结果": (
                        "通过"
                        if item.passed is True
                        else "未通过"
                        if item.passed is False
                        else "未知"
                    ),
                    "问题": "；".join(item.issues),
                    "错误码": item.error_code,
                    "说明": item.error_message,
                    "Token输入": item.token_input,
                    "Token输出": item.token_output,
                    "费用": round(item.cost, 6),
                }
                for item in result.results
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    with st.expander("查看原始返回", expanded=False):
        for item in result.results:
            if item.raw:
                st.markdown(f"**{item.task_id}**")
                st.code(item.raw, language="json")
    suggestions = [item for item in result.results if item.suggestion.strip()]
    if not suggestions:
        st.caption("本次没有产生修正建议；检查不会自动修改任何文件。")
        return
    st.divider()
    st.subheader("智能修正建议（确认后才写）")
    rows = {row["folder_path"]: row for row in service.job_items(result.job_id, include_text=False)}
    for index, item in enumerate(suggestions):
        row = rows.get(item.folder_path)
        if not row:
            continue
        with st.expander(f"{item.task_id}：查看建议并确认", expanded=False):
            st.caption(f"当前英文：{item.original[:500]}")
            text = st.text_area(
                "修正后的英文（写入前会备份）",
                value=item.suggestion,
                height=140,
                key=f"pc_suggest_{row['id']}_{index}",
            )
            st.caption("原始返回：" + item.raw[:1000])
            if st.button(
                "✅ 确认写回英文 TXT",
                type="primary",
                use_container_width=True,
                key=f"pc_apply_{row['id']}_{index}",
            ):
                try:
                    outcome = service.apply_suggestion(
                        row["id"], text, overwrite=True
                    )
                    st.success(f"已写入：{outcome['path']}")
                    if outcome.get("backup_path"):
                        st.caption(f"备份：{outcome['backup_path']}")
                except Exception as exc:
                    st.error(f"写入失败：{exc}")


# ── 历史与费用 ───────────────────────────────────────────


def _render_history(service: PrecheckService) -> None:
    jobs = service.list_jobs(200)
    if not jobs:
        st.info("还没有翻译/预审记录。")
    else:
        st.write(f"任务记录：{len(jobs)}")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "任务ID": job["id"],
                        "开始时间": _fmt_time(job.get("started_at")),
                        "状态": job.get("status"),
                        "提供方": job.get("provider"),
                        "模型": job.get("model"),
                        "根目录": job.get("root_path"),
                        "成功": (job.get("summary") or {}).get("counts", {}).get("translated", 0),
                        "失败": (job.get("summary") or {}).get("counts", {}).get("failed", 0),
                        "外部报告": job.get("source_report") or "",
                    }
                    for job in jobs
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        labels = {
            job["id"]: f"{_fmt_time(job.get('started_at'))} | {job.get('status')} | "
            f"{job.get('root_path', '')}"
            for job in jobs
        }
        job_id = st.selectbox(
            "查看任务详情",
            options=list(labels.keys()),
            format_func=lambda key: labels[key],
            key="pc_history_job",
        )
        _render_job_detail(service, job_id)

    st.divider()
    st.subheader("费用汇总")
    summary = service.usage_summary()
    columns = st.columns(5)
    columns[0].metric("记录数", summary.get("rows_count", 0))
    columns[1].metric("总任务", summary.get("total", 0))
    columns[2].metric("成功", summary.get("success", 0))
    columns[3].metric("Token 总计", summary.get("token_total", 0))
    columns[4].metric("预估费用", f"{float(summary.get('cost') or 0):.4f}")
    usage_rows = service.list_usage(200)
    if usage_rows:
        st.dataframe(pd.DataFrame(usage_rows), use_container_width=True, hide_index=True)

    _render_import(service)


def _render_job_detail(service: PrecheckService, job_id: str) -> None:
    job = service.get_job(job_id)
    if not job:
        st.error("任务不存在")
        return
    counts = (job.get("summary") or {}).get("counts") or {}
    columns = st.columns(5)
    columns[0].metric("成功", counts.get("translated", 0))
    columns[1].metric("失败", counts.get("failed", 0))
    columns[2].metric("跳过", counts.get("skipped", 0))
    columns[3].metric("Token", counts.get("token_total", 0))
    columns[4].metric("费用", f"{float(counts.get('cost') or 0):.4f}")
    st.caption(
        f"任务 {job_id}；提示词版本 {job.get('prompt_version') or '—'}；"
        f"备注 {job.get('note') or '—'}；外部报告 {job.get('source_report') or '—'}"
    )
    items = service.job_items(job_id, include_text=False)
    if items:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "任务ID": row["task_id"],
                        "状态": _status_label(row.get("status", "")),
                        "错误码": row.get("error_code") or "",
                        "说明": row.get("error_message") or "",
                        "Token输入": row.get("token_input", 0),
                        "Token输出": row.get("token_output", 0),
                        "费用": row.get("cost", 0),
                        "写入时间": _fmt_time(row.get("written_at")),
                        "路径": row.get("folder_path", ""),
                    }
                    for row in items
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    usage = service.job_usage(job_id)
    if usage:
        with st.expander(f"费用记录（{len(usage)}）", expanded=False):
            st.dataframe(pd.DataFrame(usage), use_container_width=True, hide_index=True)
    _render_export(service, job_id)

    with st.expander("删除任务记录", expanded=False):
        st.caption(
            "只删除管理数据库中的任务/条目/费用记录与暂存译文，"
            "不会删除源目录中的任何 TXT。"
        )
        confirm = st.checkbox("我确认删除该任务记录", key=f"pc_delete_confirm_{job_id}")
        if st.button(
            "🗑️ 删除任务记录",
            disabled=not confirm,
            key=f"pc_delete_{job_id}",
        ):
            try:
                service.delete_job(job_id)
                st.success("已删除任务记录。")
                st.rerun()
            except Exception as exc:
                st.error(f"删除失败：{exc}")


def _render_import(service: PrecheckService) -> None:
    st.divider()
    st.subheader("导入外部历史报告（只读）")
    st.caption(
        "支持外部工具的 translation_report_*.csv 与 cost_report.csv；"
        "只读取所选文件，不修改外部工具目录与文件。重复导入会按行去重。"
    )
    uploaded = st.file_uploader(
        "上传报告 CSV", type=["csv"], key="pc_import_upload"
    )
    path_input = st.text_input(
        "或填写报告文件路径", value="", key="pc_import_path"
    )
    report_path = ""
    if uploaded is not None:
        try:
            folder = service.report_root / "imports"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / uploaded.name
            target.write_bytes(uploaded.getvalue())
            report_path = str(target)
            st.caption(f"已保存到管理数据目录：{target}")
        except OSError as exc:
            st.error(f"保存上传文件失败：{exc}")
    elif path_input.strip():
        report_path = path_input.strip()

    if st.button(
        "📥 只读导入",
        use_container_width=True,
        disabled=not report_path,
        key="pc_import_btn",
    ):
        try:
            outcome = service.import_external_report(
                report_path, operator=st.session_state.get("operator_name", "")
            )
            st.session_state["_pc_import"] = outcome
        except Exception as exc:
            st.error(f"导入失败：{exc}")
    outcome = st.session_state.get("_pc_import")
    if outcome:
        st.success(
            f"导入完成：类型 {outcome['kind']}，总行 {outcome['rows_total']}，"
            f"新增条目 {outcome['imported']}，重复跳过 {outcome['skipped']}，"
            f"费用记录新增 {outcome['usage_inserted']}，跳过 {outcome['usage_skipped']}。"
        )
        if outcome.get("unknown_columns"):
            st.warning("未知列已忽略：" + "、".join(outcome["unknown_columns"]))
        if outcome.get("errors"):
            with st.expander(f"解析失败行（{len(outcome['errors'])}）", expanded=False):
                for line in outcome["errors"]:
                    st.write(line)

    with st.expander("错误码字典", expanded=False):
        st.dataframe(
            pd.DataFrame(
                catalog_rows(), columns=["错误码", "级别", "说明", "建议"]
            ),
            use_container_width=True,
            hide_index=True,
        )


# ── 模型与提示词 ─────────────────────────────────────────


def _render_settings(service: PrecheckService) -> None:
    profiles = service.list_profiles()
    if not profiles:
        st.warning("没有模型配置。")
        return
    options = {profile.id: f"{profile.name}（{profile.provider}）" for profile in profiles}
    selected = st.selectbox(
        "模型配置",
        options=list(options.keys()),
        format_func=lambda key: options[key],
        key="pc_settings_profile",
    )
    profile = service.get_profile(selected)
    if profile is None:
        st.error("配置读取失败")
        return

    st.caption(environment_note())
    with st.form(f"pc_profile_form_{selected}"):
        col1, col2 = st.columns(2)
        name = col1.text_input("配置名称", value=profile.name)
        provider = col2.selectbox(
            "提供方",
            options=list(PROVIDER_PRESETS.keys()),
            index=list(PROVIDER_PRESETS.keys()).index(profile.provider)
            if profile.provider in PROVIDER_PRESETS
            else 0,
            format_func=lambda key: PROVIDER_LABELS.get(key, key),
        )
        base_url = st.text_input("Base URL", value=profile.base_url)
        model = st.text_input("模型名 / 接入点 ID", value=profile.model)
        col3, col4, col5 = st.columns(3)
        timeout = col3.number_input(
            "超时（秒）", min_value=1.0, max_value=600.0, value=float(profile.timeout), step=5.0
        )
        retries = col4.number_input(
            "重试次数", min_value=0, max_value=10, value=int(profile.max_retries)
        )
        concurrency = col5.number_input(
            "并发数", min_value=1, max_value=32, value=int(profile.concurrency)
        )
        col6, col7 = st.columns(2)
        temperature = col6.number_input(
            "temperature", min_value=0.0, max_value=2.0, value=float(profile.temperature), step=0.1
        )
        currency = col7.text_input("货币", value=profile.currency)
        col8, col9, col10 = st.columns(3)
        input_price = col8.number_input(
            "输入单价 / 单位 Token", min_value=0.0, value=float(profile.input_price), step=0.1, format="%.4f"
        )
        output_price = col9.number_input(
            "输出单价 / 单位 Token", min_value=0.0, value=float(profile.output_price), step=0.1, format="%.4f"
        )
        unit_tokens = col10.number_input(
            "每单位 Token 数", min_value=1, value=int(profile.unit_tokens), step=1000
        )
        note = st.text_input("备注", value=profile.note)
        saved = st.form_submit_button("💾 保存配置", use_container_width=True)
    if saved:
        try:
            service.save_profile(
                ProviderProfile(
                    id=profile.id,
                    name=name,
                    provider=provider,
                    base_url=base_url,
                    model=model,
                    timeout=float(timeout),
                    max_retries=int(retries),
                    concurrency=int(concurrency),
                    temperature=float(temperature),
                    input_price=float(input_price),
                    output_price=float(output_price),
                    unit_tokens=int(unit_tokens),
                    currency=currency,
                    builtin=profile.builtin,
                    extra_headers=profile.extra_headers,
                    note=note,
                )
            )
            st.success("已保存模型配置。")
        except ValueError as exc:
            st.error(str(exc))

    col_active, col_delete = st.columns(2)
    if col_active.button("✅ 设为当前配置", use_container_width=True, key="pc_set_active"):
        service.set_active_profile(selected)
        st.success("已设为当前配置。")
    if col_delete.button(
        "🗑️ 删除该配置",
        use_container_width=True,
        disabled=profile.builtin,
        key="pc_delete_profile",
    ):
        try:
            service.delete_profile(selected)
            st.success("已删除配置。")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    st.divider()
    st.subheader("API Key")
    st.caption(
        "Key 只用 Windows DPAPI 加密保存到管理数据目录；界面与日志只显示掩码。"
    )
    has_key = service.has_api_key(selected)
    if has_key:
        st.caption(f"已保存：{service.api_key_preview(selected)}")
    else:
        st.caption("尚未保存 API Key。")
    api_key = st.text_input(
        "新的 API Key（留空表示不修改）",
        value="",
        type="password",
        key=f"pc_key_input_{selected}",
    )
    col_save_key, col_clear_key, col_test = st.columns(3)
    if col_save_key.button(
        "🔐 保存 Key", use_container_width=True, key=f"pc_key_save_{selected}"
    ):
        try:
            if not api_key.strip():
                st.warning("请输入 API Key。")
            else:
                service.save_api_key(selected, api_key)
                st.success(f"已保存：{mask_key(api_key.strip())}")
        except Exception as exc:
            st.error(f"保存失败：{exc}")
    if col_clear_key.button(
        "🗑️ 清除 Key", use_container_width=True, key=f"pc_key_clear_{selected}"
    ):
        try:
            service.clear_api_key(selected)
            st.success("已清除 API Key。")
        except Exception as exc:
            st.error(f"清除失败：{exc}")
    if col_test.button(
        "📡 测试连接", use_container_width=True, key=f"pc_key_test_{selected}"
    ):
        try:
            with st.spinner("正在调用模型…"):
                outcome = service.test_connection(
                    selected, api_key=api_key.strip() or None
                )
            st.success(
                f"连接成功：{outcome['model']}；延迟 {outcome['latency_ms']} ms；"
                f"返回：{outcome['reply']}"
            )
        except Exception as exc:
            st.error(f"连接失败：{exc}")

    st.divider()
    st.subheader("提示词")
    st.caption(
        "可编辑并恢复默认；不保证与原外部工具的 Prompt/质量完全一致。"
    )
    prompts = service.prompts()
    prompt_options = {item.name: item.label for item in prompts}
    prompt_name = st.selectbox(
        "提示词",
        options=list(prompt_options.keys()),
        format_func=lambda key: prompt_options[key],
        key="pc_prompt_name",
    )
    prompt = service.prompt(prompt_name)
    st.caption(
        f"版本：{prompt.version}；来源：{'内置' if prompt.source == 'builtin' else '自定义'}"
    )
    system_text = st.text_area(
        "System 提示词", value=prompt.system, height=140, key=f"pc_prompt_sys_{prompt_name}"
    )
    user_text = st.text_area(
        "User 提示词", value=prompt.user, height=220, key=f"pc_prompt_user_{prompt_name}"
    )
    st.caption("可用占位符：{zh_text}、{en_text}；JSON 花括号会原样保留。")
    col_save_prompt, col_reset_prompt = st.columns(2)
    if col_save_prompt.button(
        "💾 保存提示词", use_container_width=True, key=f"pc_prompt_save_{prompt_name}"
    ):
        try:
            service.save_prompt(prompt_name, system_text, user_text)
            st.success("已保存提示词。")
        except ValueError as exc:
            st.error(str(exc))
    if col_reset_prompt.button(
        "↩️ 恢复默认",
        use_container_width=True,
        disabled=prompt.source == "builtin",
        key=f"pc_prompt_reset_{prompt_name}",
    ):
        try:
            service.reset_prompt(prompt_name)
            st.success("已恢复默认提示词。")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    with st.expander("数据目录与安全说明", expanded=False):
        for key, value in app_config.describe_paths(str(service.data_dir)).items():
            st.write(f"{key}：`{value}`")
        st.write(
            "翻译/检查只读源目录；只有确认写入时才写英文 TXT，且写前备份、"
            "原子写入、失败不覆盖。"
        )
        st.write(
            "外部历史报告只读导入；报告不包含 API Key。"
        )


def render() -> None:
    st.title("🌐 翻译与预审（1.25.3）")
    st.caption(
        "识别中英文 TXT 配对、翻译预览、确认写入、语法/一致性检查与费用报告；"
        "翻译/检查/预览不修改源文件。"
    )
    try:
        service = PrecheckService()
    except Exception as exc:
        st.error(f"管理数据库初始化失败：{exc}")
        return

    try:
        _render_runner()
        tab_translate, tab_checks, tab_history, tab_settings = st.tabs(
            ["翻译", "语法与一致性", "历史与费用", "模型与提示词"]
        )
        with tab_translate:
            _render_translate(service)
        with tab_checks:
            _render_checks(service)
        with tab_history:
            _render_history(service)
        with tab_settings:
            _render_settings(service)
    finally:
        service.close()
