"""批次翻译（v1.25.11 P5）：只处理 03-申请终验，调用本机 8765 HTTP API。

预览只读；确认后才调用翻译接口，并在调用前备份现有英文 TXT。
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from modules.config import app_config
from modules.database.connection import open_database
from modules.precheck.secrets import SecretsError
from modules.workspace import catalog_translation as tr
from modules.workspace.catalog_service import CatalogService
from modules.workspace.models import SOURCE_FINAL
from ui.pages.catalog_workbench import _CSS, _esc, _go, _projects

TR_LABELS = {
    "not_translated": "未翻译",
    "translated": "已翻译",
    "translation_failed": "翻译失败",
}


def _pick_key(batch_id: str) -> str:
    return f"catalog_tr_pick_{batch_id}"


def _select_all(rows: list[dict[str, Any]], value: bool) -> None:
    for row in rows:
        st.session_state[_pick_key(str(row["id"]))] = value


def _filtered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    l1_values = sorted({str(row.get("category_l1") or "未分类") for row in rows})
    team_values = sorted({str(row.get("team_name") or "未识别团队") for row in rows})
    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.2, 2])
    l1 = c1.selectbox("大类", ["全部", *l1_values], key="catalog_tr_l1")
    team = c2.selectbox("团队", ["全部", *team_values], key="catalog_tr_team")
    status = c3.selectbox(
        "翻译状态", ["全部", "未翻译", "已翻译", "翻译失败"], key="catalog_tr_status"
    )
    keyword = c4.text_input("搜索批次", key="catalog_tr_keyword").strip()
    result: list[dict[str, Any]] = []
    for row in rows:
        if l1 != "全部" and str(row.get("category_l1") or "未分类") != l1:
            continue
        if team != "全部" and str(row.get("team_name") or "未识别团队") != team:
            continue
        if status != "全部" and TR_LABELS.get(str(row.get("translation_status") or ""), "未翻译") != status:
            continue
        if keyword:
            haystack = f"{row.get('name') or ''} {row.get('batch_path') or ''}"
            if keyword.casefold() not in haystack.casefold():
                continue
        result.append(row)
    return result


def _cards(rows: list[dict[str, Any]]) -> list[str]:
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
                    f"翻译：{row.get('translation_status_label') or '未翻译'} · "
                    f"主状态：{row.get('main_status_label') or '新提交'}"
                )
                if st.checkbox("选择此批次", key=_pick_key(batch_id)):
                    selected.append(batch_id)
    return selected


def _settings_panel(db) -> None:
    settings = tr.load_settings(db)
    with st.expander("翻译设置（8765 工具 / 模型 / API Key）", expanded=not tr.has_api_key()):
        st.caption(
            "平台调用本机 8765 工具的公开接口；API Key 只加密保存在本机，不写入日志和报告。"
        )
        c1, c2 = st.columns([1.4, 2.2])
        base_url = c1.text_input("8765 地址", value=settings.base_url, key="catalog_tr_base")
        api_base = c2.text_input(
            "模型 API Base URL", value=settings.api_base_url, key="catalog_tr_api_base"
        )
        c3, c4, c5 = st.columns([1.2, 1.6, 1])
        provider_labels = list(tr.PROVIDERS.values())
        provider_keys = list(tr.PROVIDERS.keys())
        provider_index = provider_keys.index(settings.provider) if settings.provider in provider_keys else 0
        provider = c3.selectbox(
            "模型供应商", provider_labels, index=provider_index, key="catalog_tr_provider"
        )
        model = c4.text_input("模型名称", value=settings.model, key="catalog_tr_model")
        concurrency = c5.number_input(
            "并发", min_value=1, max_value=50, value=int(settings.concurrency), key="catalog_tr_conc"
        )
        c6, c7, c8, c9 = st.columns([1, 1, 1, 1.4])
        mode_labels = list(tr.MODES.values())
        mode_keys = list(tr.MODES.keys())
        mode_index = mode_keys.index(settings.mode) if settings.mode in mode_keys else 0
        mode = c6.selectbox("模式", mode_labels, index=mode_index, key="catalog_tr_mode")
        thinking = c7.selectbox(
            "思考程度",
            ["low", "medium", "high"],
            index=["low", "medium", "high"].index(settings.thinking)
            if settings.thinking in {"low", "medium", "high"}
            else 0,
            key="catalog_tr_thinking",
        )
        id_digits = c8.number_input(
            "编号位数", min_value=1, max_value=20, value=int(settings.id_digits), key="catalog_tr_digits"
        )
        c9.caption("英文写入由 8765 工具完成；平台只做备份与审计。")
        c10, c11 = st.columns([1, 1])
        ch_suffix = c10.text_input("中文 TXT 后缀", value=settings.ch_suffix, key="catalog_tr_ch")
        en_suffix = c11.text_input("英文 TXT 后缀", value=settings.en_suffix, key="catalog_tr_en")
        prompt = st.text_area(
            "翻译提示词（留空使用 8765 内置提示词）",
            value=settings.prompt,
            height=110,
            key="catalog_tr_prompt",
        )
        key_col, save_col, test_col = st.columns([2, 1, 1])
        key_input = key_col.text_input(
            "API Key（只保存密文；留空不修改）",
            type="password",
            key="catalog_tr_key",
            placeholder=tr.api_key_preview() or "输入 8765 使用的 API Key",
        )
        if save_col.button("保存设置", key="catalog_tr_save", use_container_width=True):
            tr.save_settings(
                db,
                tr.TranslationSettings(
                    base_url=base_url.strip().rstrip("/") or tr.DEFAULT_BASE_URL,
                    api_base_url=api_base.strip() or tr.DEFAULT_API_BASE_URL,
                    model=model.strip() or tr.DEFAULT_MODEL,
                    provider=provider_keys[provider_labels.index(provider)],
                    id_digits=int(id_digits),
                    ch_suffix=ch_suffix.strip() or tr.DEFAULT_CH_SUFFIX,
                    en_suffix=en_suffix.strip() or tr.DEFAULT_EN_SUFFIX,
                    mode=mode_keys[mode_labels.index(mode)],
                    concurrency=int(concurrency),
                    thinking=str(thinking),
                    prompt=prompt,
                ),
            )
            if key_input.strip():
                try:
                    tr.save_api_key(key_input.strip())
                except SecretsError as exc:
                    st.error(f"API Key 保存失败：{exc}")
                else:
                    st.success("设置与 API Key 已保存。")
            else:
                st.success("设置已保存。")
            st.rerun()
        if test_col.button("测试连接", key="catalog_tr_health", use_container_width=True):
            try:
                info = tr.health(base_url.strip().rstrip("/") or tr.DEFAULT_BASE_URL)
                st.success(
                    f"连接正常：{info.get('name') or info.get('app') or '8765'}"
                    f" · 版本 {info.get('version') or '未知'}"
                    f" · 端口 {info.get('port') or '—'}"
                )
            except tr.TranslationError as exc:
                st.error(str(exc))
        if tr.has_api_key():
            st.caption(f"已保存 API Key：{tr.api_key_preview()}")
            if st.button("删除已保存的 API Key", key="catalog_tr_key_del"):
                tr.delete_api_key()
                st.rerun()
        else:
            st.warning("还没有保存 API Key；保存后才能执行翻译。")
        st.text_input(
            "操作员（写入翻译日志）",
            value=str(st.session_state.get("operator_name") or ""),
            key="catalog_tr_operator",
        )


def _preview_panel(summary: dict[str, Any]) -> None:
    st.markdown("**预览结果（只读，不会修改任何文件）**")
    m = st.columns(4)
    m[0].metric("批次", summary.get("batches", 0))
    m[1].metric("待翻译编号", summary.get("to_translate", 0))
    m[2].metric("缺中文 TXT", summary.get("missing_ch", 0))
    m[3].metric("已有英文跳过", summary.get("skipped", 0))
    rows = summary.get("previews") or []
    if not rows:
        return
    frame = pd.DataFrame(
        [
            {
                "批次": item.get("batch_name") or "",
                "编号数": item.get("total", 0),
                "待翻译": item.get("to_translate", 0),
                "缺中文": item.get("missing_ch", 0),
                "跳过": item.get("skipped", 0),
                "说明": item.get("message") or "",
            }
            for item in rows
        ]
    )
    st.dataframe(frame, use_container_width=True, hide_index=True)
    details: list[dict[str, Any]] = []
    for item in rows:
        for folder in item.get("folders") or []:
            details.append(
                {
                    "批次": item.get("batch_name") or "",
                    "编号": folder.get("number") or "",
                    "中文": "有" if folder.get("has_ch") else "缺",
                    "英文": "有" if folder.get("has_en") else "无",
                    "动作": {
                        "translate": "翻译",
                        "retranslate": "重新翻译",
                        "skip": "跳过",
                        "missing_ch": "缺中文",
                    }.get(str(folder.get("action") or ""), folder.get("action") or ""),
                    "说明": folder.get("message") or "",
                }
            )
    if details:
        with st.expander(f"逐编号预览（{len(details)}）", expanded=False):
            st.dataframe(pd.DataFrame(details), use_container_width=True, hide_index=True)


def _result_panel(results: list[dict[str, Any]]) -> None:
    st.markdown("**本次翻译结果**")
    for item in results:
        name = item.get("batch_name") or item.get("batch_id") or "批次"
        if item.get("failed"):
            st.warning(f"{name}：{item.get('message') or '翻译失败'}")
        elif item.get("translated"):
            st.success(f"{name}：{item.get('message') or '翻译完成'}")
        else:
            st.info(f"{name}：{item.get('message') or '没有需要翻译的编号'}")
    rows: list[dict[str, Any]] = []
    for item in results:
        for detail in item.get("items") or []:
            rows.append(
                {
                    "批次": item.get("batch_name") or "",
                    "编号": detail.get("number") or "",
                    "动作": detail.get("action") or "",
                    "结果": detail.get("status") or "",
                    "说明": detail.get("message") or "",
                }
            )
    if rows:
        with st.expander(f"逐编号结果（{len(rows)}）", expanded=False):
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    backups = [item.get("backup_dir") for item in results if item.get("backup_dir")]
    if backups:
        st.caption("英文 TXT 备份目录：" + "；".join(f"`{path}`" for path in backups))


def render() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="vk-hero"><h2>🌐 批次翻译</h2>'
        "<p>只处理 03-申请终验。预览只读；确认后才调用本机 8765 工具，并在写入前备份英文 TXT。</p></div>",
        unsafe_allow_html=True,
    )
    db = open_database(app_config.get_db_path())
    try:
        if st.session_state.pop("catalog_tr_reset_confirm", False):
            st.session_state["catalog_tr_confirm"] = False
        service = CatalogService(db)
        projects = _projects(db)
        if not projects:
            st.info("还没有业务目录。先到设置页添加项目根目录和待验/终验目录。")
            return
        project = projects[0]
        _settings_panel(db)
        rows = service.batches(project_id=project["id"], source_type=SOURCE_FINAL)
        if not rows:
            st.info("03-申请终验还没有索引。先回到工作台，来源切到“申请终验”并刷新目录索引。")
            if st.button("打开验收工作台", type="primary"):
                _go("workbench")
            return

        toolbar = st.columns([1.2, 1.2, 2.8], vertical_alignment="center")
        filtered = _filtered(rows)
        if toolbar[0].button("全选当前筛选", key="catalog_tr_all", use_container_width=True):
            _select_all(filtered, True)
            st.rerun()
        if toolbar[1].button("清空选择", key="catalog_tr_none", use_container_width=True):
            _select_all(rows, False)
            st.rerun()
        toolbar[2].caption(
            f"只读根目录：`{project.get('final_path') or '未配置'}` · 仅 03-申请终验"
        )

        if not filtered:
            st.caption("当前筛选没有批次。")
            return
        st.caption(f"当前筛选 {len(filtered)} 个批次；勾选后先预览，再确认翻译。")
        selected = _cards(filtered)

        st.divider()
        action = st.columns([1.3, 1.6, 2.6], vertical_alignment="center")
        if action[0].button(
            f"预览所选（{len(selected)}）",
            key="catalog_tr_preview",
            use_container_width=True,
            disabled=not selected,
        ):
            previews: list[dict[str, Any]] = []
            errors: list[str] = []
            with st.spinner("正在读取 8765 扫描结果（只读）…"):
                for batch_id in selected:
                    try:
                        previews.append(tr.preview_batch(db, batch_id).to_dict())
                    except tr.TranslationError as exc:
                        errors.append(f"{batch_id}：{exc}")
            summary = {
                "batches": len(previews),
                "to_translate": sum(item.get("to_translate", 0) for item in previews),
                "missing_ch": sum(item.get("missing_ch", 0) for item in previews),
                "skipped": sum(item.get("skipped", 0) for item in previews),
                "previews": previews,
            }
            st.session_state["catalog_tr_preview_data"] = summary
            st.session_state["catalog_tr_preview_errors"] = errors
            st.session_state.pop("catalog_tr_results", None)
            st.rerun()
        preview_summary = st.session_state.get("catalog_tr_preview_data")
        preview_errors = st.session_state.get("catalog_tr_preview_errors") or []
        confirmed = action[1].checkbox(
            "我已确认：翻译会写入英文 TXT，平台会先备份现有英文文件",
            key="catalog_tr_confirm",
            disabled=not selected or not preview_summary,
        )
        if action[2].button(
            f"开始翻译所选（{len(selected)}）",
            key="catalog_tr_run",
            type="primary",
            use_container_width=True,
            disabled=not selected or not confirmed or not preview_summary or not tr.has_api_key(),
        ):
            results: list[dict[str, Any]] = []
            progress = st.progress(0.0, text="准备调用 8765…")
            total = max(1, len(selected))
            for index, batch_id in enumerate(selected):
                progress.progress(index / total, text=f"正在翻译第 {index + 1}/{len(selected)} 个批次…")
                try:
                    result = tr.translate_batch(
                        db,
                        batch_id,
                        operator=str(st.session_state.get("catalog_tr_operator") or ""),
                    )
                    results.append(result.to_dict())
                except tr.TranslationError as exc:
                    results.append(
                        {
                            "batch_id": batch_id,
                            "batch_name": batch_id,
                            "translated": 0,
                            "failed": 1,
                            "message": str(exc),
                            "items": [],
                        }
                    )
            progress.progress(1.0, text="翻译任务已结束")
            st.session_state["catalog_tr_results"] = results
            st.session_state.pop("catalog_tr_preview_data", None)
            st.session_state["catalog_tr_reset_confirm"] = True
            st.rerun()

        existing_results = st.session_state.get("catalog_tr_results")
        if not existing_results:
            if not tr.has_api_key():
                action[2].caption("先在上方保存 API Key，才能执行翻译。")
            elif not preview_summary:
                action[2].caption("先点“预览所选”，确认待翻译数量后再执行。")
            elif not confirmed:
                action[2].caption("勾选确认后，“开始翻译所选”才会启用。")

        if preview_errors:
            for message in preview_errors:
                st.warning(message)

        results = st.session_state.get("catalog_tr_results")
        if results:
            _result_panel(results)
        elif preview_summary:
            _preview_panel(preview_summary)
    finally:
        db.close()
