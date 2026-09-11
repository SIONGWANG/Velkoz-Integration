# zone_c.py — C区渲染方法，提取自 app.py
import streamlit as st
import os

from utils import DEFAULT_CATEGORIES
from disk_io import preload_next_images
from easter_eggs import on_status_judged, on_screenshot_pasted, on_undo_status
from settings_mixin import SECOND_LEVEL_SHORTCUTS

STATUS_OPTIONS = ["合格", "不合格", "修改后合格", "待定"]

# === 引用 ===
try:
    from streamlit_paste_button import paste_image_button
    _HAS_PASTE_LIB = True
except ImportError:
    _HAS_PASTE_LIB = False


class ZoneCMixin:
    """C 区：控制面板（信息、截图、导航、分类、状态、备注、标签）"""

    @st.fragment
    def render_control_panel(self, group):
        if st.session_state.get('_batch_completed'):
            st.info("✅ 本轮验收已完成，C 区已锁定。点击 A 区列表条目可复查，或通过 B 区面板导出结果")
            return

        # 提交成功提示（延迟到下一轮 rerun 再弹，避免被跳转冲掉）
        if st.session_state.pop('_ee_submit_flash', None):
            st.toast("✓ 已提交", icon="🚀")

        # 提前同步标签到备注（必须在任何 widget 渲染前完成，否则 Streamlit 会报
        # "cannot be modified after the widget is instantiated"）
        current_id = group['id']
        if st.session_state.get(f"_pending_tag_sync_{current_id}"):
            st.session_state[f"_pending_tag_sync_{current_id}"] = False
            self._sync_tags_to_notes(group)

        pool_key = f"evidence_pool_{group['id']}"
        if pool_key not in st.session_state:
            st.session_state[pool_key] = []

        # 1. 数据信息：谁的数据 + 哪组数据 + 打开文件夹
        col_info, col_btn = st.columns([4, 1])
        with col_info:
            info_text = f"👤 {group.get('user_name', '未知')}　|　🆔 {group['id']}"
            st.caption(info_text)
        with col_btn:
            btn_c1, btn_c2 = st.columns(2)
            with btn_c1:
                if st.button("📂", key=f"open_folder_{group['id']}", use_container_width=True, help="打开此组文件夹"):
                    self.open_in_system(group['root'])
            with btn_c2:
                original_path = os.path.join(group['root'], group['original']) if group.get('original') else None
                if st.button("🖼️", key=f"open_original_{group['id']}", use_container_width=True,
                             help="打开图片（按所选方式）", disabled=not original_path):
                    msg, ok = self._open_viewer_app(image_path=original_path)
                    if ok:
                        st.toast(msg)
                        st.rerun()
                    else:
                        st.warning(msg)

        st.divider()

        # 2. 错误截图
        st.write("")
        st.markdown(f"**📷 错误截图 ({len(st.session_state[pool_key])}/3)**")

        if st.button("🔍 查看错误截图", key=f"view_err_{group['id']}", use_container_width=True,
                     help="在内置查看器中查看质检记录里已保存的错误截图（缩放/平移/翻页）"):
            msg, ok = self._view_error_screenshots()
            if ok:
                st.toast(msg)
            else:
                st.warning(msg)

        c_paste, c_clear = st.columns([2, 1])
        with c_paste:
            if len(st.session_state[pool_key]) < 3:
                if _HAS_PASTE_LIB:
                    paste_result = paste_image_button(
                        label="📋 粘贴 (Ctrl+V)",
                        background_color="#FF4B4B",
                        hover_background_color="#FF0000",
                        key=f"paste_btn_{group['id']}_{len(st.session_state[pool_key])}"
                    )
                    if paste_result.image_data is not None:
                        st.session_state[pool_key].append(paste_result.image_data)
                        on_screenshot_pasted()
                else:
                    st.warning("⚠️ 请先安装: pip install streamlit-paste-button")
            else:
                st.caption("✅ 已达3张上限")

        with c_clear:
            if st.session_state[pool_key]:
                if st.button("🗑️ 清空截图", key=f"clr_{group['id']}",
                             on_click=lambda pk=pool_key: self._clear_evidence_pool(pk)):
                    pass

        if st.session_state[pool_key]:
            cols = st.columns(3)
            for idx, img in enumerate(st.session_state[pool_key]):
                with cols[idx]:
                    st.image(img, use_container_width=True)

        # 3. 上一条 / 下一条（跟随 A 区筛选条件）
        st.divider()
        nav_groups = st.session_state.data_groups
        all_ids = list(dict.fromkeys(g['id'] for g in nav_groups))
        filtered_ids = self._get_filtered_ids()
        nav_ids = filtered_ids if filtered_ids else all_ids
        curr_idx = nav_ids.index(st.session_state.current_id) if st.session_state.current_id in nav_ids else 0

        nav_c1, nav_c2 = st.columns(2)
        with nav_c1:
            if st.button("⬅️ 上一条", use_container_width=True, disabled=(curr_idx == 0)):
                new_id = nav_ids[curr_idx - 1]
                st.session_state.current_id = new_id; st.session_state.focus_img_idx = 0
                st.session_state.needs_scroll_top = True
                all_idx = all_ids.index(new_id)
                preload_next_images(all_idx, nav_groups)
                self._rerun_app()
        with nav_c2:
            if st.button("下一条 ➡️", use_container_width=True, disabled=(curr_idx == len(nav_ids) - 1)):
                new_id = nav_ids[curr_idx + 1]
                st.session_state.current_id = new_id; st.session_state.focus_img_idx = 0
                st.session_state.needs_scroll_top = True
                all_idx = all_ids.index(new_id)
                preload_next_images(all_idx, nav_groups)
                self._rerun_app()

        # 4. 分类标签 L1 / L2
        config = st.session_state.categories_config
        l1_options = config.get('L1', DEFAULT_CATEGORIES['L1'])
        l2_mapping = config.get('L2', DEFAULT_CATEGORIES['L2'])

        if l1_options:
            l1_display = [f"🔵 {opt}" for opt in l1_options]
            l1_default = f"🔵 {st.session_state.sticky_l1}" if st.session_state.sticky_l1 else None
            try: l1_sel_display = st.pills("L1", l1_display, selection_mode="single", default=l1_default, label_visibility="collapsed")
            except Exception: l1_sel_display = st.radio("L1", l1_display, label_visibility="collapsed")
            l1_sel = l1_sel_display.replace("🔵 ", "") if l1_sel_display else None
            if l1_sel != st.session_state.sticky_l1:
                st.session_state.sticky_l1 = l1_sel; st.session_state.sticky_l2 = None

            l2_opts = l2_mapping.get(st.session_state.sticky_l1, [])
            if l2_opts:
                # 生成带快捷键提示的显示文本
                l2_display = []
                for i, opt in enumerate(l2_opts):
                    if i < len(SECOND_LEVEL_SHORTCUTS):
                        l2_display.append(f"🟢 {opt}  [{SECOND_LEVEL_SHORTCUTS[i]}]")
                    else:
                        l2_display.append(f"🟢 {opt}")

                # 查找当前选中项的显示文本
                l2_default = None
                if st.session_state.sticky_l2:
                    for display in l2_display:
                        if st.session_state.sticky_l2 in display:
                            l2_default = display
                            break

                try: l2_sel_display = st.pills("L2", l2_display, selection_mode="single", default=l2_default, label_visibility="collapsed")
                except Exception: l2_sel_display = st.radio("L2", l2_display, label_visibility="collapsed")

                # 提取实际分类名称（去掉快捷键标记）
                if l2_sel_display:
                    l2_sel = l2_sel_display.replace("🟢 ", "").split("  [")[0].strip()
                else:
                    l2_sel = None
                if l2_sel != st.session_state.sticky_l2: st.session_state.sticky_l2 = l2_sel
            else: l2_sel = None
        else:
            l1_sel = None; l2_sel = None

        # 预读当前状态值（按钮值来自上一轮 on_click 更新）
        status_sel = st.session_state.get('status_pills')

        # 5. 最终结果（2×2 网格）— 状态选择在备注上方
        st.markdown('<style>.status-btn-group button { font-weight: 900 !important; font-family: "SimHei", "黑体", "Microsoft YaHei", sans-serif !important; }</style>', unsafe_allow_html=True)
        st.markdown('<div class="status-btn-group">', unsafe_allow_html=True)
        for row_idx in range(2):
            c1, c2 = st.columns(2)
            for col_idx, col in enumerate([c1, c2]):
                opt = STATUS_OPTIONS[row_idx * 2 + col_idx]
                with col:
                    is_sel = (status_sel == opt)
                    if st.button(opt, key=f"sbtn_{opt}", use_container_width=True,
                                 type="primary" if is_sel else "secondary",
                                 on_click=lambda _opt=opt: st.session_state.update({'status_pills': _opt})):
                        pass
        st.markdown('</div>', unsafe_allow_html=True)

        # 检测状态变更（撤销判定彩蛋）
        prev_status_key = f"_ee_prev_status_{group['id']}"
        prev_status = st.session_state.get(prev_status_key)
        if prev_status and status_sel and status_sel != prev_status:
            on_undo_status()
        if status_sel:
            st.session_state[prev_status_key] = status_sel

        # 6. 备注 + 保存/提交
        with st.form(key=f"form_submit_{group['id']}", clear_on_submit=False):
            _ta_h = st.session_state.get('textarea_height', 68)
            feedback_text = st.text_area("备注", height=_ta_h, placeholder="在此输入备注 (选填)", key=f"feedback_{group['id']}")
            # 检测用户是否手动编辑了备注（与标签同步后的内容不同）
            last_tags_for_check = st.session_state.get(f"_last_tags_{group['id']}", "")
            manual_key = f"_manual_edit_{group['id']}"
            if feedback_text and feedback_text.strip() != last_tags_for_check.strip():
                st.session_state[manual_key] = True
            elif not feedback_text.strip():
                st.session_state[manual_key] = False

            missing_fields = []
            if l1_options and status_sel in ["合格", "修改后合格"]:
                if not l1_sel: missing_fields.append("一级目录")
                if not l2_sel: missing_fields.append("二级目录")
            if not status_sel: missing_fields.append("验收结果")

            btn_row1 = st.columns([1, 1.1, 1.1])
            with btn_row1[0]:
                is_save = st.form_submit_button("💾 仅保存", type="secondary", use_container_width=True)
            with btn_row1[1]:
                is_pass_submit = st.form_submit_button("✅ 快捷合格 (X)", type="primary", use_container_width=True)
            with btn_row1[2]:
                is_modified_submit = st.form_submit_button("🔄 修改合格 (V)", type="primary", use_container_width=True)
            
            col_spacer, submit_col, col_spacer2 = st.columns([0.2, 1.6, 0.2])
            with submit_col:
                if st.session_state.sampling_acceptance_mode:
                    is_submit = st.form_submit_button("🚀 提交并下一条（空格 / 抽检）", type="primary", use_container_width=True)
                else:
                    is_submit = st.form_submit_button("🚀 提交并下一条（空格）", type="primary", use_container_width=True)

        # 7. 快捷标签
        st.write("")
        st.caption("🏷️ 快捷标签")
        self.render_tag_selector(group)

        # 8. 标签管理（折叠，最底部）
        st.divider()
        with st.expander("⚙️ 标签管理", expanded=False):
            current_id = group['id']
            tags_data = st.session_state.custom_tags

            st.caption("📝 新增标签")
            col_input, col_btn = st.columns([3, 1])
            with col_input:
                new_tag = st.text_input("标签名称", placeholder="输入标签名...", key=f"mgmt_new_tag_{current_id}", label_visibility="collapsed")
            with col_btn:
                if st.button("➕ 添加", use_container_width=True, key=f"mgmt_add_{current_id}",
                             disabled=not (new_tag and new_tag.strip())):
                    if new_tag and new_tag.strip() and new_tag.strip() not in tags_data.get("tags", []):
                        if "tags" not in tags_data:
                            tags_data["tags"] = []
                        tags_data["tags"].append(new_tag.strip())
                        if "frequent" not in tags_data:
                            tags_data["frequent"] = []
                        if len(tags_data["frequent"]) < 5:
                            tags_data["frequent"].append(new_tag.strip())
                        st.session_state.custom_tags = tags_data
                        self._save_tags_to_disk(tags_data)

            st.caption("📂 标签库管理")
            all_tags = tags_data.get("tags", [])
            if all_tags:
                for row_start in range(0, len(all_tags), 4):
                    row_tags = all_tags[row_start:row_start + 4]
                    cols = st.columns(len(row_tags))
                    for i, tag in enumerate(row_tags):
                        with cols[i]:
                            if st.button(f"{tag} ✕", key=f"mgmt_del_{current_id}_{tag}", use_container_width=True,
                                         on_click=lambda _tag=tag: self._del_tag(_tag, tags_data)):
                                pass
            else:
                st.caption("暂无标签")

            st.divider()

            st.caption("📋 批量操作")
            bc1, bc2 = st.columns(2)
            with bc1:
                if st.button("✅ 全选所有标签", use_container_width=True, key=f"select_all_{current_id}"):
                    # 全选 = 本地全部标签 ∪ 当前已选（含历史标签），避免误删历史标签
                    merged_all = list(dict.fromkeys(
                        list(all_tags) + list(st.session_state.selected_tags.get(current_id, []))
                    ))
                    st.session_state.selected_tags[current_id] = merged_all
                    self._sync_tags_to_notes(group)
            with bc2:
                if st.button("🗑️ 清空已选标签", use_container_width=True, key=f"clear_all_{current_id}"):
                    st.session_state.selected_tags[current_id] = []
                    self._sync_tags_to_notes(group)

        if is_pass_submit:
            status_sel = "合格"
            missing_fields = []
            if l1_options:
                if not l1_sel: missing_fields.append("一级目录")
                if not l2_sel: missing_fields.append("二级目录")
            if missing_fields:
                st.error(f"🛑 无法提交！请补充：{'、'.join(missing_fields)}")
            else:
                is_submit = True

        if is_modified_submit:
            status_sel = "修改后合格"
            missing_fields = []
            if l1_options:
                if not l1_sel: missing_fields.append("一级目录")
                if not l2_sel: missing_fields.append("二级目录")
            if missing_fields:
                st.error(f"🛑 无法提交！请补充：{'、'.join(missing_fields)}")
            else:
                is_submit = True

        if is_save or is_submit:
            if missing_fields:
                st.error(f"🛑 无法提交！请补充：{'、'.join(missing_fields)}")
            else:
                final_zh = st.session_state.get(f"zh_{group['id']}", "")
                final_en = st.session_state.get(f"en_{group['id']}", "")
                current_imgs = st.session_state[pool_key]

                success, save_msg = self.save_to_disk(
                    group, l1_sel, l2_sel, status_sel,
                    final_zh, final_en, feedback_text, current_imgs
                )

                if success:
                    if save_msg:
                        st.toast(save_msg, icon="⚠️")
                    if is_save:
                        st.toast("✓ 已保存", icon="💾")
                        on_status_judged(status_sel)
                    elif is_submit:
                        st.session_state['_ee_submit_flash'] = True
                        on_status_judged(status_sel)
                        # 使用 nav_ids（已筛选）而非 all_ids（全量），避免筛选条件下跳转错误
                        if curr_idx < len(nav_ids) - 1:
                            st.session_state.current_id = nav_ids[curr_idx + 1]
                            st.session_state.focus_img_idx = 0
                            # 跳转下一条需要在 fragment 外更新 B 区，触发 app 级别单次 rerun
                            self._rerun_app()
                        else:
                            # 筛选条件下不触发全量完成面板，跳回筛选列表首条
                            if st.session_state.get('filter_pills', '全部') != '全部':
                                st.session_state.current_id = nav_ids[0]
                                st.session_state.focus_img_idx = 0
                                st.toast("📋 筛选列表已全部处理完，回到首条", icon="✅")
                                self._rerun_app()
                            else:
                                st.session_state._batch_completed = True
                                self._rerun_app()
                else:
                    if save_msg:
                        st.error(save_msg)
