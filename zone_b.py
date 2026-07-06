# zone_b.py — B区渲染方法，提取自 app.py
import streamlit as st
import streamlit.components.v1 as components
import os
import re
import json

from utils import BASE_DIR, read_txt
from disk_io import get_display_image_bytes
from easter_eggs import on_batch_complete
from css_styles import BZ_DRAG_JS


class ZoneBMixin:
    """B 区：冷启动动画、确认面板、图片展示、完成面板"""

    def render_cold_start_animation(self):
        """极简现代风载入动画：呼吸之眼 + 脉冲环 + 淡入标题"""
        version = os.path.basename(BASE_DIR)
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
                    st.download_button("⬇️ 下载 Excel", f, file_name=os.path.basename(excel_path), use_container_width=True)
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
        """B 区图片渲染 — 1×N单行横向平铺 + 上下分层布局"""
        import base64

        img_groups = st.session_state.data_groups
        all_ids_b = [g['id'] for g in img_groups]
        curr_idx_b = all_ids_b.index(st.session_state.current_id) if st.session_state.current_id in all_ids_b else 0
        total_b = len(all_ids_b)

        # ── 进度条 ──
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
        num_images = len(images)

        # ── 视图模式 + 缩放滑块 ──
        current_view = st.session_state.get('bz_view_mode', '自动')
        bz_ratio = st.session_state.get('bz_image_ratio', 65)
        vc1, vc2, vc3 = st.columns(3)
        with vc1:
            if st.button("🔍 自动", key=f"bv_auto_{group['id']}",
                         type="primary" if current_view == '自动' else "secondary",
                         use_container_width=True,
                         on_click=lambda: st.session_state.update({'bz_view_mode': '自动'})):
                pass
        with vc2:
            if st.button("⬜ 双图", key=f"bv_2_{group['id']}",
                         type="primary" if current_view == '双图' else "secondary",
                         use_container_width=True,
                         on_click=lambda: st.session_state.update({'bz_view_mode': '双图'})):
                pass
        with vc3:
            if st.button("📐 四图", key=f"bv_4_{group['id']}",
                         type="primary" if current_view == '四图' else "secondary",
                         use_container_width=True,
                         on_click=lambda: st.session_state.update({'bz_view_mode': '四图'})):
                pass

        # ── 确定实际列数 ──
        view_mode = st.session_state.get('bz_view_mode', '自动')
        if view_mode == '自动':
            actual_cols = 2 if num_images <= 2 else 4
        elif view_mode == '双图':
            actual_cols = 2
        else:  # 四图
            actual_cols = 4

        # ── 上层：1×N 单行横向图片预览 ──
        display_images = images[:actual_cols]
        # 固定容器高度：所有图片在同一行，高度由容器决定
        container_h = max(300, int(500 * bz_ratio / 65))
        html_parts = [f'<div class="bz-linear-row" style="height:{container_h}px;">']
        for i, img in enumerate(display_images):
            p = os.path.join(group['root'], img)
            img_bytes, res = get_display_image_bytes(p)
            b64 = base64.b64encode(img_bytes).decode() if isinstance(img_bytes, bytes) else ''
            suffix = os.path.splitext(img)[0].replace(group['id'], '').strip('_')
            label = f"图{i+1}" if not suffix else suffix
            html_parts.append(f'''
            <div class="bz-img-cell" style="height:{container_h}px;">
                <img src="data:image/jpeg;base64,{b64}" alt="{img}" title="双击打开: {img}" />
                <div class="bz-img-info">
                    <span>{label} | {res}</span>
                </div>
            </div>''')
        html_parts.append('</div>')
        components.html(''.join(html_parts), height=container_h + 30)

        # Streamlit 图片按钮（用于双击触发打开）
        img_cols = st.columns(min(actual_cols, len(display_images)))
        for i, img in enumerate(display_images[:actual_cols]):
            if i < len(img_cols):
                with img_cols[i]:
                    p = os.path.join(group['root'], img)
                    st.button("📂", key=f"open_bimg_{group['id']}_{i}",
                              help=f"在系统查看器中打开 {img}", use_container_width=True,
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

        # ── 拖拽分割线 ──
        components.html("""
        <div class="bz-divider" style="width:100%;"></div>
        """ + BZ_DRAG_JS, height=20)

        # ── 下层：文本 + AI质检区（固定最小高度） ──
        st.markdown('<div class="bz-bottom-area">', unsafe_allow_html=True)

        t1, t2 = st.columns(2)
        with t1:
            c_zh = read_txt(os.path.join(group['root'], group['txt_zh']) if group['txt_zh'] else None)
            st.text_area("ZH", value=c_zh, height=60, label_visibility="collapsed", key=f"zh_{group['id']}")
        with t2:
            c_en = read_txt(os.path.join(group['root'], group['txt_en']) if group['txt_en'] else None)
            st.text_area("EN", value=c_en, height=60, label_visibility="collapsed", key=f"en_{group['id']}")

        st.markdown("---")
        qa_df = st.session_state.qa_df
        if not qa_df.empty and '所在文件夹(ID)' in qa_df.columns:
            qa_record = qa_df[qa_df['所在文件夹(ID)'] == str(group['id'])]
            if not qa_record.empty:
                qa_row = qa_record.iloc[-1]
                qa_status = str(qa_row.get('质检结论', '未知')).lower()
                qa_reason = qa_row.get('拒绝原因分析', '无')
                if qa_status == 'true':
                    st.success(f"**🤖 AI 质检通过** ✅ | **原因分析**：{qa_reason}")
                else:
                    st.error(f"**🤖 AI 质检未通过** ❌ | **原因分析**：{qa_reason}")
                with st.expander("👀 查看完整 JSON 数据"):
                    json_str = str(qa_row.get('JSON_Output', '{}'))
                    try:
                        parsed_json = json.loads(json_str.replace('""', '"'))
                        st.json(parsed_json)
                    except:
                        st.code(json_str, language='json')
            else:
                st.info("ℹ️ 质检表中未找到当前 ID 的对应数据。")
        else:
            if qa_df.empty:
                st.caption("ℹ️ 当前未加载质检表，或内容为空。")
            else:
                st.warning("⚠️ 已加载数据源，但未找到列名：`所在文件夹(ID)`。请检查表头是否匹配。")

        st.markdown('</div>', unsafe_allow_html=True)
