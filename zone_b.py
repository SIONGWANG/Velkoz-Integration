# zone_b.py — B区渲染方法，提取自 app.py
import streamlit as st
import streamlit.components.v1 as components
import os
import re
import json
import html

from ai_review import (
    parse_json_output, get_ai_record,
    get_poster_issues, get_instruction_issues,
    compute_minimal_fixes, apply_fix_to_text, as_text, build_issue_chip_css,
)

from utils import APP_VERSION, BASE_DIR, read_txt
from disk_io import get_display_image_bytes
from easter_eggs import on_batch_complete


class ZoneBMixin:
    """B 区：冷启动动画、确认面板、图片展示、完成面板"""

    def render_cold_start_animation(self):
        """极简现代风载入动画：呼吸之眼 + 脉冲环 + 淡入标题"""
        version = APP_VERSION
        is_scanning = st.session_state.get('is_scanning', False)
        anim_html = f"""
        <style>
        @keyframes fade-up {{
            0% {{ opacity: 0; transform: translateY(12px); }}
            100% {{ opacity: 1; transform: translateY(0); }}
        }}
        @keyframes breathe {{
            0%, 100% {{ transform: scale(1); }}
            50% {{ transform: scale(1.08); }}
        }}
        @keyframes pulse-ring {{
            0% {{ transform: scale(0.5); opacity: 0.6; }}
            100% {{ transform: scale(2.5); opacity: 0; }}
        }}
        @keyframes ring-appear {{
            0% {{ transform: scale(0); opacity: 0; }}
            100% {{ transform: scale(1); opacity: 1; }}
        }}
        @keyframes spin {{
            0% {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}
        @keyframes spinner-fade {{
            0% {{ opacity: 0; transform: scale(0.5); }}
            100% {{ opacity: 1; transform: scale(1); }}
        }}
        .coldstart-wrapper {{
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            height: 560px; font-family: "Source Sans Pro", -apple-system, "Segoe UI", sans-serif;
            position: relative;
        }}
        .eye-container {{
            position: relative; width: 180px; height: 180px; margin-bottom: 48px;
            opacity: 0; animation: fade-up 0.8s 0.2s forwards;
        }}
        .eye-ring {{
            position: absolute; top: 0; left: 0; width: 180px; height: 180px;
            border: 2px solid #6366f1; border-radius: 50%;
            opacity: 0; animation: ring-appear 0.6s 0.3s forwards;
            box-shadow: 0 0 15px rgba(99, 102, 241, 0.2);
        }}
        .eye-pulse {{
            position: absolute; top: 0; left: 0; width: 180px; height: 180px;
            border: 1px solid #6366f1; border-radius: 50%;
            animation: pulse-ring 2.5s ease-out 1.2s infinite;
        }}
        .eye-pulse:nth-child(3) {{
            animation-delay: 2.2s;
        }}
        .eye-pupil {{
            position: absolute; top: 50%; left: 50%; width: 54px; height: 54px;
            margin-left: -27px; margin-top: -27px; background: #6366f1;
            border-radius: 50%; animation: breathe 3s ease-in-out infinite;
            box-shadow: 0 0 25px rgba(99, 102, 241, 0.5), 0 0 50px rgba(99, 102, 241, 0.2);
        }}
        .spinner-container {{
            position: relative; width: 120px; height: 120px; margin-bottom: 48px;
            opacity: 0; animation: spinner-fade 0.5s 0.2s forwards;
        }}
        .spinner-ring {{
            position: absolute; top: 0; left: 0; width: 120px; height: 120px;
            border: 3px solid transparent;
            border-top-color: #6366f1;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }}
        .spinner-ring:nth-child(2) {{
            width: 90px; height: 90px;
            top: 15px; left: 15px;
            border-top-color: #8b5cf6;
            animation-duration: 0.8s;
            animation-direction: reverse;
        }}
        .spinner-ring:nth-child(3) {{
            width: 60px; height: 60px;
            top: 30px; left: 30px;
            border-top-color: #a78bfa;
            animation-duration: 0.6s;
        }}
        .coldstart-title {{
            font-size: 40px; font-weight: 400; color: #1a1a2e; letter-spacing: 3px;
            opacity: 0; animation: fade-up 0.8s 0.8s forwards; margin-bottom: 8px;
        }}
        .coldstart-subtitle {{
            font-size: 16px; color: #666; letter-spacing: 4px;
            opacity: 0; animation: fade-up 0.8s 1.4s forwards; margin-bottom: 28px;
        }}
        .coldstart-loading-text {{
            font-size: 15px; color: #6366f1; letter-spacing: 2px;
            opacity: 0; animation: fade-up 0.5s 0.6s forwards; margin-bottom: 20px;
        }}
        .coldstart-version {{
            font-size: 12px; color: #bbb; letter-spacing: 2px;
            opacity: 0; animation: fade-up 0.6s 2s forwards;
            position: absolute;
            bottom: 24px;
        }}
        </style>
        <div class="coldstart-wrapper">
        """
        if is_scanning:
            anim_html += """
            <div class="spinner-container">
                <div class="spinner-ring"></div>
                <div class="spinner-ring"></div>
                <div class="spinner-ring"></div>
            </div>
            <div class="coldstart-loading-text">正在扫描文件夹...</div>
            """
        else:
            anim_html += f"""
            <div class="eye-container">
                <div class="eye-ring"></div>
                <div class="eye-pulse"></div>
                <div class="eye-pulse"></div>
                <div class="eye-pupil"></div>
            </div>
            <div class="coldstart-title">审视之眼pro V{version}</div>
            <div class="coldstart-subtitle">视觉质检</div>
            """
        anim_html += f"""
            <div class="coldstart-version">v{version}</div>
        </div>
        """
        components.html(anim_html, height=600)

    def render_confirm_panel(self):
        """渲染标注员确认面板（B区）"""
        st.info("📋 **检测到负极目录下有多个子标注员项，请确认它们的名称**")

        pending = st.session_state.pending_groups
        from collections import OrderedDict
        annotator_info = OrderedDict()
        for g in pending:
            name = g['user_name']
            if name not in annotator_info:
                annotator_info[name] = {'count': 0, 'sample_path': g['root']}
            annotator_info[name]['count'] += 1

        total_pending = len(pending)
        edited_map = {}

        for name, info in annotator_info.items():
            is_unknown = (name == "未知")
            icon = "⚠️ " if is_unknown else ""

            with st.container(border=True):
                c_include, c1, c2, c3 = st.columns([0.5, 2, 3.5, 1])
                with c_include:
                    default_val = st.session_state.annotator_inclusion.get(name, True)
                    included = st.checkbox("纳入", value=default_val, key=f"include_{name}")
                    st.session_state.annotator_inclusion[name] = included
                with c1:
                    placeholder = "请手动输入" if is_unknown else name
                    edited = st.text_input(
                        f"{icon}{name}",
                        value=placeholder if is_unknown else name,
                        key=f"confirm_name_{name}",
                        label_visibility="visible"
                    )
                    edited_map[name] = edited
                with c2:
                    st.caption(f"📂 ...{os.sep.join(info['sample_path'].split(os.sep)[-3:])}")
                with c3:
                    st.metric("条数", info['count'])

        st.write("")
        selected_total = sum(
            info['count'] for name, info in annotator_info.items()
            if st.session_state.annotator_inclusion.get(name, True)
        )
        c_btn1, c_btn2 = st.columns(2)
        with c_btn1:
            if st.button("❌ 跳过确认，直接开始", use_container_width=True):
                st.session_state.data_groups = st.session_state.pending_groups
                if st.session_state.data_groups:
                    st.session_state.current_id = st.session_state.data_groups[0]['id']
                st.session_state.confirm_pending = False
                st.session_state.pending_groups = []
                st.session_state._batch_completed = False
                st.session_state._completion_balloons_shown = False
                st.rerun()
        with c_btn2:
            if st.button(f"✅ 确认并开始验收（共 {selected_total} 条）", type="primary", use_container_width=True):
                has_empty_unknown = False
                for name, edited in edited_map.items():
                    # 跳过未纳入验收的标注员
                    if not st.session_state.annotator_inclusion.get(name, True):
                        continue
                    if name == "未知" and (not edited or edited.strip() == "" or edited == "请手动输入"):
                        has_empty_unknown = True
                        break

                if has_empty_unknown:
                    st.error('⚠️ 检测到【未知】标注员未填写姓名，请为所有【未知】标注员输入有效名称！')
                else:
                    with st.spinner("正在启动验收，数据加载中..."):
                        st.session_state.annotator_map = edited_map
                        # 1. 先用原始名过滤（inclusion key 是原始名）
                        included_groups = [
                            g for g in st.session_state.pending_groups
                            if st.session_state.annotator_inclusion.get(g['user_name'], True)
                        ]
                        # 2. 再对过滤后的数据改名
                        for g in included_groups:
                            old_name = g['user_name']
                            if old_name in edited_map:
                                g['user_name'] = edited_map[old_name]
                        st.session_state.data_groups = included_groups
                        if st.session_state.data_groups:
                            st.session_state.current_id = st.session_state.data_groups[0]['id']
                        st.session_state.confirm_pending = False
                        st.session_state.pending_groups = []
                        st.session_state._batch_completed = False
                        st.session_state._completion_balloons_shown = False
                    st.rerun()

    def render_completion_panel(self):
        """B 区：整批验收完成面板"""
        if not st.session_state.get('_completion_balloons_shown'):
            st.balloons()
            st.session_state._completion_balloons_shown = True
            on_batch_complete()

        status_map = self.get_record_status_map(self._get_active_csv_path())
        comp_groups = st.session_state.data_groups
        all_ids = list(dict.fromkeys(g['id'] for g in comp_groups))
        total = len(all_ids)
        # 只统计当前数据组内的记录状态
        current_status_map = {k: v for k, v in status_map.items() if k in set(all_ids)}
        qualified = sum(1 for v in current_status_map.values() if v == '合格')
        modified = sum(1 for v in current_status_map.values() if v == '修改后合格')
        unqualified = sum(1 for v in current_status_map.values() if v == '不合格')
        pending = sum(1 for v in current_status_map.values() if v == '待定')
        processed = qualified + modified + unqualified + pending
        unchecked = total - processed
        pass_rate = round((qualified + modified) / total * 100, 1) if total > 0 else 0

        st.success(f"🎉 全部验收完成！共 {total} 条，已完成 {processed} 条")

        r1c1, r1c2, r1c3, r1c4 = st.columns(4)
        with r1c1: st.metric("🟢 合格", qualified)
        with r1c2: st.metric("🔴 不合格", unqualified)
        with r1c3: st.metric("🔵 修改后合格", modified)
        with r1c4: st.metric("🟡 待定", pending)
        r2c1, r2c2, r2c3, r2c4 = st.columns(4)
        with r2c1: st.metric("⚪ 未检", unchecked)
        with r2c2: st.metric("📊 合格率", f"{pass_rate}%")
        with r2c3: st.metric("📦 总计", total)
        with r2c4:
            pass  # 占位对齐

        csv_path = self.get_csv_filename()
        if os.path.exists(csv_path):
            st.info(f"📄 记录已保存至: {csv_path}")

        st.write("")

        # Excel 导出结果展示（持久化）
        excel_path = st.session_state.get('_excel_export_path')
        if excel_path and os.path.exists(excel_path):
            st.success(f"✅ 已生成报表：{os.path.basename(excel_path)}")
            export_dir = os.path.dirname(excel_path)
            c_dl, c_open = st.columns(2)
            with c_dl:
                with open(excel_path, "rb") as f:
                    st.download_button("⬇️ 下载 Excel", f, file_name=os.path.basename(excel_path), use_container_width=True, key="download_excel_comp")
            with c_open:
                if st.button("📂 打开文件夹", use_container_width=True, key="comp_open_excel_dir"):
                    self.open_in_system(export_dir)

        b1, b2 = st.columns(2)
        with b1:
            if st.button("🔄 返回第一条", use_container_width=True, type="primary"):
                st.session_state._batch_completed = False
                st.session_state._completion_balloons_shown = False
                st.session_state.current_id = all_ids[0]
                st.session_state.focus_img_idx = 0
                st.session_state._excel_export_path = None
                st.rerun()
        with b2:
            if st.button("📊 导出 Excel 报表", use_container_width=True):
                file_path, msg = self.export_excel_with_images()
                if file_path:
                    st.session_state._excel_export_path = file_path
                    st.rerun()
                else:
                    st.error(msg)

        b3, b4 = st.columns(2)
        with b3:
            if st.button("📂 打开 CSV 目录", use_container_width=True):
                csv_dir = os.path.dirname(csv_path)
                if os.path.exists(csv_dir):
                    self.open_in_system(csv_dir)
                else:
                    st.warning("_质检记录目录尚不存在")
        with b4:
            if st.button("⚠️ 归集不合格/待定", use_container_width=True):
                missing_ids, total_loaded = self._get_missing_ids()
                if missing_ids:
                    self.show_missing_data_dialog(missing_ids, total_loaded)
                else:
                    self.show_reject_confirm_dialog()

    def render_single_image_view(self, group, images):
        if st.session_state.focus_img_idx >= len(images):
            st.session_state.focus_img_idx = 0
        st.markdown('<div class="switch-btn">', unsafe_allow_html=True)
        cols = st.columns(len(images))
        for i, img in enumerate(images):
            with cols[i]:
                originals = group.get('originals', [group['original']])
                is_orig = (img in originals)
                if is_orig and len(originals) > 1:
                    if re.search(r'_1\.(jpg|png|jpeg)$', img, re.IGNORECASE):
                        label = "🔴 原图1"
                    elif re.search(r'_2\.(jpg|png|jpeg)$', img, re.IGNORECASE):
                        label = "🔴 原图2"
                    else:
                        label = "🔴 原图"
                else:
                    label = "🔴 原图" if is_orig else "🔵 结果" if "_result" in img else "🎭 Mask" if "_mask" in img else f"图 {i+1}"
                b_type = "primary" if i == st.session_state.focus_img_idx else "secondary"
                if st.button(label, key=f"focus_{group['id']}_{i}", type=b_type, use_container_width=True):
                    st.session_state.focus_img_idx = i
        st.markdown('</div>', unsafe_allow_html=True)
        curr_img = images[st.session_state.focus_img_idx]
        curr_img_path = os.path.join(group['root'], curr_img)
        img_bytes, res = get_display_image_bytes(curr_img_path)
        st.image(img_bytes, caption=f"展示: {curr_img}", use_container_width=True)
        st.caption(f"分辨率: {res}")

    @st.fragment
    def render_b_image_area(self, group):
        """B 区图片渲染"""
        img_groups = st.session_state.data_groups
        all_ids_b = [g['id'] for g in img_groups]
        curr_idx_b = all_ids_b.index(st.session_state.current_id) if st.session_state.current_id in all_ids_b else 0
        total_b = len(all_ids_b)

        pct = (curr_idx_b + 1) / total_b * 100 if total_b > 0 else 0
        components.html(f"""
        <div style="display:flex; align-items:center; gap:8px; height:20px; overflow:visible;">
            <span style="font-size:0.8rem; white-space:nowrap;">{curr_idx_b + 1}/{total_b}</span>
            <div style="flex:1; height:4px; background:#e0e0e0; border-radius:2px;">
                <div style="width:{pct}%; height:100%; background:#4CAF50; border-radius:2px;"></div>
            </div>
        </div>
        """, height=28)

        if group.get('fallback_image'):
            st.warning("⚠️ 图片未按规则匹配，显示的是文件夹内第一张图片")

        images = group['images']
        if st.session_state.view_mode == "四宫格":
            r1 = st.columns(2); r2 = st.columns(2) if len(images)>2 else []
            for i, img in enumerate(images):
                tgt = r1[i] if i<2 else r2[i-2]
                with tgt:
                    p = os.path.join(group['root'], img)
                    img_bytes, res = get_display_image_bytes(p)
                    st.image(img_bytes, use_container_width=True)
                    cc1, cc2 = st.columns([4, 1])
                    with cc1:
                        st.caption(res)
                    with cc2:
                        st.button("📂", key=f"open_bimg_{group['id']}_{i}", help=f"双击图片或用此按钮在系统查看器中打开 {img}", use_container_width=True,
                                  on_click=self.open_in_system, args=(p,))
            # 双击图片 → 触发对应 📂 按钮
            components.html("""
            <script>
            (function() {
                const doc = window.parent.document;
                const allBtns = Array.from(doc.querySelectorAll('button'));
                const openBtns = allBtns.filter(b => b.innerText.trim() === '📂');
                openBtns.forEach(btn => {
                    btn.style.fontSize = '0.7rem';
                    btn.style.padding = '0 2px';
                    btn.style.minHeight = '20px';
                    btn.style.opacity = '0.5';
                    const block = btn.closest('[data-testid="stVerticalBlock"]');
                    if (!block) return;
                    const img = block.querySelector('img');
                    if (!img || img.dataset.dblclicked) return;
                    img.dataset.dblclicked = '1';
                    img.style.cursor = 'pointer';
                    img.title = '双击在系统查看器中打开';
                    img.addEventListener('dblclick', function(e) {
                        e.preventDefault();
                        e.stopPropagation();
                        btn.click();
                    });
                });
            })();
            </script>
            """, height=0)
        else:
            self.render_single_image_view(group, images)

        st.markdown("---")
        ai_rec = get_ai_record(st.session_state.get('qa_df'), str(group['id']))
        ai_has_data = bool(ai_rec)

        t1, t2 = st.columns(2)
        _ta_h = st.session_state.get('textarea_height', 68)
        with t1:
            c_zh = read_txt(os.path.join(group['root'], group['txt_zh']) if group['txt_zh'] else None)
            st.text_area("ZH", value=c_zh, height=_ta_h, label_visibility="collapsed", key=f"zh_{group['id']}")
            if ai_has_data:
                self._render_ai_fix_bar(group, 'zh', c_zh, ai_rec)
        with t2:
            c_en = read_txt(os.path.join(group['root'], group['txt_en']) if group['txt_en'] else None)
            st.text_area("EN", value=c_en, height=_ta_h, label_visibility="collapsed", key=f"en_{group['id']}")
            if ai_has_data:
                self._render_ai_fix_bar(group, 'en', c_en, ai_rec)

        if ai_has_data:
            self._render_ai_popover(group, ai_rec)

        st.markdown("---")

    # ============================================
    # 🤖 AI 初审（质检结果.csv）紧凑呈现 —— 只读参考，绝不写入验收结果
    # 设计目标：默认不占页面高度；有问题的片段在文本框下方一行展示；
    #          图片问题等细节统一放在悬浮窗内，点开才看到。
    # ============================================

    def _ai_issue_line(self, item):
        """把一条问题 dict 转成紧凑展示元组 (badge_html, desc, sug)。"""
        itype = str(item.get('type') or item.get('error_type') or '未分类')
        desc = str(item.get('desc') or item.get('reason') or '').strip()
        sug = str(item.get('suggestion') or '').strip()
        sev = str(item.get('severity') or '').strip()
        sev_cls = {'严重': 'ai-sev-high', '中等': 'ai-sev-mid', '轻微': 'ai-sev-low'}.get(sev, '')
        badge = (f'<span class="ai-chip ai-chip-info">{itype}</span> '
                 f'<span class="{sev_cls}">[{sev}]</span>' if sev else
                 f'<span class="ai-chip ai-chip-info">{itype}</span>')
        return badge, desc, sug

    def _write_txt_atomic(self, path, content):
        """原子写回文本文件，避免写一半。"""
        if not path:
            return False, "路径为空"
        try:
            tmp = path + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(tmp, path)
            return True, ""
        except Exception as e:
            return False, f"写回失败: {e}"

    def _text_path(self, group, lang):
        if lang == 'zh':
            return os.path.join(group['root'], group['txt_zh']) if group['txt_zh'] else None
        return os.path.join(group['root'], group['txt_en']) if group['txt_en'] else None

    def _orig_snapshot_key(self, group, lang):
        return f"_ai_orig_{lang}_{group['id']}"

    def _cur_text(self, group, lang):
        if lang == 'zh':
            return as_text(st.session_state.get(f"zh_{group['id']}", ""))
        return as_text(st.session_state.get(f"en_{group['id']}", ""))

    def _render_ai_fix_bar(self, group, lang, orig_text, ai_rec):
        """文本框正下方：一行式修复条。
        仅展示 AI 判定有误的片段（原片段 → 建议），正确部分完全不动；
        可「整体采纳」「仅采纳某片段」「恢复原文」，均写回 .txt 并更新文本框。
        """
        orig_text = as_text(orig_text)
        corr = ai_rec.get('Corrected_CH') if lang == 'zh' else ai_rec.get('Corrected_EN')
        fixes = compute_minimal_fixes(orig_text, as_text(corr))
        if not fixes:
            return

        st.markdown(build_issue_chip_css(), unsafe_allow_html=True)
        tkey = f"zh_{group['id']}" if lang == 'zh' else f"en_{group['id']}"
        sync_key = f"_ai_fix_state_{tkey}_{group['id']}"
        snap_key = self._orig_snapshot_key(group, lang)
        if snap_key not in st.session_state:
            st.session_state[snap_key] = orig_text

        # 当前文本框内容（可能已被采纳修改过）
        cur_text = self._cur_text(group, lang) or orig_text

        # 应用动作（pre-state：按钮 on_click 存储，这里消费并写盘）
        action = st.session_state.pop(f"_ai_fix_act_{tkey}_{group['id']}", None)
        if action and action.get('op'):
            op = action['op']
            if op == 'apply_all':
                new_text = cur_text
                for fx in fixes:
                    new_text = apply_fix_to_text(new_text, fx)
                ok, msg = self._write_txt_atomic(self._text_path(group, lang), new_text)
                if ok:
                    st.session_state[tkey] = new_text
                    st.session_state[sync_key] = new_text
                    st.session_state[f"_ai_fix_applied_{tkey}_{group['id']}"] = True
                    st.toast("✅ 已采纳全部修正")
                    st.rerun(scope="app")
                else:
                    st.warning(msg)
            elif op == 'apply_one':
                idx = action.get('idx', 0)
                if 0 <= idx < len(fixes):
                    new_text = apply_fix_to_text(cur_text, fixes[idx])
                    ok, msg = self._write_txt_atomic(self._text_path(group, lang), new_text)
                    if ok:
                        st.session_state[tkey] = new_text
                        st.session_state[sync_key] = new_text
                        st.toast(f"✅ 已采纳第 {idx+1} 处")
                        st.rerun(scope="app")
                    else:
                        st.warning(msg)
            elif op == 'restore':
                orig_restore = st.session_state.get(snap_key, orig_text)
                ok, msg = self._write_txt_atomic(self._text_path(group, lang), orig_restore)
                if ok:
                    st.session_state[tkey] = orig_restore
                    st.session_state[sync_key] = orig_restore
                    st.session_state[f"_ai_fix_applied_{tkey}_{group['id']}"] = False
                    st.toast("↩️ 已恢复原文本")
                    st.rerun(scope="app")
                else:
                    st.warning(msg)

        lang_label = "中文" if lang == 'zh' else "英文"
        did_apply = st.session_state.get(f"_ai_fix_applied_{tkey}_{group['id']}", False)
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px;font-size:0.78rem;color:#64748b;'
            f'background:#f8fafc;border:1px solid #e2e8f0;border-radius:4px;padding:2px 6px;margin:0 0 2px;">'
            f'<span>✍️ {lang_label} AI 修正（仅高亮疑误片段，其余未动）：</span>'
            f'{self._build_fix_chips(fixes)}'
            f'<span style="color:#16a34a;font-size:0.72rem;">{"● 已应用" if did_apply else ""}</span>'
            f'</div>',
            unsafe_allow_html=True)

        # 操作按钮
        btn_c = st.columns([2.2, 1, 1])
        with btn_c[0]:
            st.caption(f"共 {len(fixes)} 处")
        with btn_c[1]:
            st.button("✅ 整体采纳", key=f"ai_all_{tkey}_{group['id']}", use_container_width=True,
                      help="按 AI 建议修正全部片段并写回指令文件",
                      on_click=lambda: st.session_state.update(
                          {f"_ai_fix_act_{tkey}_{group['id']}": {'op': 'apply_all'}}))
        with btn_c[2]:
            st.button("↩️ 恢复原文", key=f"ai_rest_{tkey}_{group['id']}", use_container_width=True,
                      help="撤销 AI 修正，恢复为原指令",
                      on_click=lambda: st.session_state.update(
                          {f"_ai_fix_act_{tkey}_{group['id']}": {'op': 'restore'}}))

        # 逐片段采纳（更精确的局部替换）
        ncols = len(fixes) + 1
        seg_cols = st.columns(ncols)
        with seg_cols[0]:
            st.caption("仅改片段:")
        for i in range(len(fixes)):
            with seg_cols[i + 1]:
                old_frag = (fixes[i]['old'] or '(空)')
                st.button(old_frag[:6], key=f"ai_seg_{tkey}_{group['id']}_{i}", use_container_width=True,
                          help=f"仅采纳第 {i+1} 处：{old_frag} → {fixes[i]['new']}",
                          on_click=lambda _i=i: st.session_state.update(
                              {f"_ai_fix_act_{tkey}_{group['id']}": {'op': 'apply_one', 'idx': _i}}))

    def _build_fix_chips(self, fixes):
        """把修正片段渲染为静态度片（无按钮，只用于修复条展示）。"""
        rows = []
        for fx in fixes:
            if fx['kind'] == 'delete':
                rows.append(f'<span class="ai-chip ai-chip-wrong">删：{fx["old"]}</span>')
            elif fx['kind'] == 'insert':
                rows.append(f'<span class="ai-chip ai-chip-ok">增：{fx["new"]}</span>')
            else:
                rows.append(f'<span class="ai-chip ai-chip-wrong">{fx["old"]}</span>'
                            f'<span class="ai-fix-arrow">→</span>'
                            f'<span class="ai-chip ai-chip-ok">{fx["new"]}</span>')
        return ''.join(rows)

    def _render_ai_popover(self, group, ai_rec):
        """图片问题 + 指令问题悬浮窗：点开才显示，不占用验收主页面。"""
        json_data = parse_json_output(ai_rec.get('JSON_Output'))
        poster = get_poster_issues(json_data)
        instr = get_instruction_issues(json_data)
        if not poster and not instr:
            return

        st.markdown(build_issue_chip_css(), unsafe_allow_html=True)
        flag = "✗" if poster else "✓"
        lab = f"🖼️ 图片问题 {len(poster)} ｜ 📝 指令 {len(instr)} [{flag}]"
        with st.popover(lab, use_container_width=True):
            if poster:
                st.markdown(f"**🖼️ 图片问题（{len(poster)}）**")
                for it in poster:
                    badge, desc, sug = self._ai_issue_line(it)
                    st.markdown(f'<div class="ai-issue-item">{badge} {html_escape(desc or "")}' +
                                (f'<br><span style="color:#64748b">💡 {html_escape(sug)}</span>' if sug else '') +
                                '</div>', unsafe_allow_html=True)
            if instr:
                st.markdown(f"**📝 指令问题（{len(instr)}）**")
                for it in instr:
                    badge, desc, sug = self._ai_issue_line(it)
                    st.markdown(f'<div class="ai-issue-item">{badge} {html_escape(desc or "")}' +
                                (f'<br><span style="color:#64748b">💡 {html_escape(sug)}</span>' if sug else '') +
                                '</div>', unsafe_allow_html=True)
            st.caption("以上仅供参考，不影响你的验收判定。")
        return
