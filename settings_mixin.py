# settings_mixin.py — 设置/配置/标签/UI 控件方法，提取自 app.py
import streamlit as st
import streamlit.components.v1 as components
import os
import re
import json
import logging
import platform
import subprocess

from utils import (
    BASE_DIR, DEFAULT_CATEGORIES,
    save_categories_config, save_scan_rules, DEFAULT_SCAN_RULES,
)

# 二级分类快捷键序列（按顺序分配）
SECOND_LEVEL_SHORTCUTS = ["D", "F", "G", "H", "J", "K", "L"]


class SettingsMixin:
    """设置读写、标签管理、UI 控件、配置面板"""

    # ── 设置/标签 I/O ──

    def _load_tags_from_disk(self):
        """从本地 JSON 加载标签库"""
        tags_file = os.path.join(BASE_DIR, "config", "tags.json")
        if os.path.exists(tags_file):
            try:
                with open(tags_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        return json.loads(content)
            except json.JSONDecodeError as e:
                logging.warning("标签库JSON解析失败: %s - %s", tags_file, str(e))
                st.session_state['_tags_load_error'] = f"JSON格式错误: {str(e)}"
            except Exception as e:
                logging.warning("标签库加载失败: %s - %s", tags_file, str(e))
                st.session_state['_tags_load_error'] = str(e)
        return {"tags": [], "frequent": []}

    def _save_tags_to_disk(self, tags_data):
        """保存标签库到本地 JSON"""
        tags_file = os.path.join(BASE_DIR, "config", "tags.json")
        try:
            os.makedirs(os.path.dirname(tags_file), exist_ok=True)
            with open(tags_file, 'w', encoding='utf-8') as f:
                json.dump(tags_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.warning("标签库保存失败: %s", tags_file)
            st.warning(f"⚠️ 标签库保存失败：{e}")

    def _load_settings(self):
        settings_file = os.path.join(BASE_DIR, "config", "settings.json")
        if os.path.exists(settings_file):
            try:
                with open(settings_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        return json.loads(content)
            except json.JSONDecodeError as e:
                logging.warning("设置文件JSON解析失败: %s - %s", settings_file, str(e))
                st.session_state['_settings_load_error'] = f"JSON格式错误: {str(e)}"
            except Exception as e:
                logging.warning("设置文件加载失败: %s - %s", settings_file, str(e))
                st.session_state['_settings_load_error'] = str(e)
        return {"layout_width": 80, "layout_height": 85, "view_mode": "四宫格", "root_path": "",
                "annotator_confirm_enabled": False, "operator_name": "", "task_type": "新标"}

    def _save_settings(self):
        settings_file = os.path.join(BASE_DIR, "config", "settings.json")
        try:
            os.makedirs(os.path.dirname(settings_file), exist_ok=True)
            settings = {
                "layout_width": st.session_state.get('layout_width', 80),
                "layout_height": st.session_state.get('layout_height', 85),
                "view_mode": st.session_state.get('view_mode', "四宫格"),
                "root_path": st.session_state.get('root_path', ""),
                "annotator_confirm_enabled": st.session_state.get('annotator_confirm_enabled', False),
                "operator_name": st.session_state.get('operator_name', ""),
                "task_type": st.session_state.get('task_type', "新标"),
                "enable_hotkeys": st.session_state.get('enable_hotkeys', True),
            }
            with open(settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.warning("设置保存失败: %s", settings_file)
            st.warning(f"⚠️ 设置保存失败：{e}")

    # ── UI 组件 ──

    def render_fullscreen_button(self):
        btn_html = """
        <script>
        function toggleFullScreen() {
            var doc = window.parent.document;
            if (!doc.fullscreenElement) { doc.documentElement.requestFullscreen(); }
            else { if (doc.exitFullscreen) { doc.exitFullscreen(); } }
        }
        </script>
        <style>
        .fs-btn {
            width: 100%; padding: 0.4rem; background-color: #ffffff; color: #31333F;
            border: 1px solid #d6d6d8; border-radius: 4px; cursor: pointer;
            font-size: 14px; font-family: "Source Sans Pro", sans-serif; font-weight: 600;
            transition: all 0.2s; display: flex; align-items: center; justify-content: center; gap: 8px;
        }
        .fs-btn:hover { border-color: #6366f1; color: #6366f1; background-color: #eef2ff; }
        body { margin: 0; }
        </style>
        <button class="fs-btn" onclick="toggleFullScreen()"><span>📺</span> 切换全屏 (F11)</button>
        """
        components.html(btn_html, height=45)

    def inject_hotkeys(self):
        # 获取二级分类快捷键映射
        l2_shortcuts = self._get_l2_shortcuts_json()
        js_code = f"""
        <script>
        (function() {{
            const doc = window.parent.document;

            // 二级分类快捷键映射
            const l2Shortcuts = {l2_shortcuts};

            function findBtn(test) {{
                const btns = doc.getElementsByTagName('button');
                for (let i = 0; i < btns.length; i++) {{
                    if (test(btns[i])) return btns[i];
                }}
                return null;
            }}

            function findL2Button(key) {{
                const mapping = l2Shortcuts[key];
                if (!mapping) return null;
                // 查找所有按钮，匹配二级分类名称
                const btns = doc.getElementsByTagName('button');
                for (let i = 0; i < btns.length; i++) {{
                    const text = btns[i].innerText.trim();
                    // 匹配格式: "🟢 分类名  [快捷键]" 或 "🟢 分类名"
                    if (text.includes(mapping) && text.includes('🟢')) return btns[i];
                }}
                return null;
            }}

            function handleHotkeys(e) {{
                const tag = doc.activeElement.tagName;
                if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

                // 二级分类快捷键（仅在有映射时生效）
                const key = e.key.toUpperCase();
                if (l2Shortcuts[key]) {{
                    const btn = findL2Button(key);
                    if (btn && !btn.disabled) {{
                        e.preventDefault();
                        btn.click();
                        return;
                    }}
                }}

                // 提交并下一条 — 空格
                if (e.key === ' ') {{
                    const btn = findBtn(b => b.innerText.includes('提交并下一条'));
                    if (btn && !btn.disabled) {{
                        e.preventDefault();
                        btn.click();
                    }}
                    return;
                }}

                // 快捷合格提交 — X
                if (e.key === 'x' || e.key === 'X') {{
                    const btn = findBtn(b => b.innerText.includes('快捷合格'));
                    if (btn && !btn.disabled) {{
                        e.preventDefault();
                        btn.click();
                    }}
                    return;
                }}

                // 修改合格提交 — V
                if (e.key === 'v' || e.key === 'V') {{
                    const btn = findBtn(b => b.innerText.includes('修改合格'));
                    if (btn && !btn.disabled) {{
                        e.preventDefault();
                        btn.click();
                    }}
                    return;
                }}

                // 上一条
                if (e.key === 'ArrowLeft') {{
                    e.preventDefault();
                    const btn = findBtn(b => b.innerText.includes('⬅️') || b.innerText.includes('上一条'));
                    if (btn && !btn.disabled) btn.click();
                    return;
                }}

                // 下一条
                if (e.key === 'ArrowRight') {{
                    e.preventDefault();
                    const btn = findBtn(b => b.innerText.includes('下一条 ➡️') || (b.innerText.includes('下一条') && !b.innerText.includes('提交')));
                    if (btn && !btn.disabled) btn.click();
                    return;
                }}

                // 视图切换 1-4
                const viewKeys = {{'1': '🔍', '2': '🔴 原图', '3': '图 1', '4': '图 2'}};
                if (viewKeys[e.key]) {{
                    const needle = viewKeys[e.key];
                    const btn = findBtn(b => b.innerText.includes(needle));
                    if (btn && !btn.disabled) {{
                        e.preventDefault();
                        btn.click();
                    }}
                    return;
                }}

                // 系统查看器
                if (e.key === '`' || e.key === '~') {{
                    const btn = findBtn(b => b.innerText.includes('🖼️'));
                    if (btn && !btn.disabled) {{
                        e.preventDefault();
                        btn.click();
                    }}
                }}
            }}
            doc.removeEventListener('keydown', window.parent.myHotkeysHandler);
            window.parent.myHotkeysHandler = handleHotkeys;
            doc.addEventListener('keydown', window.parent.myHotkeysHandler);
        }})();
        </script>
        """
        components.html(js_code, height=0, width=0)

    def _get_l2_shortcuts_json(self):
        """生成二级分类快捷键映射JSON"""
        l2_mapping = st.session_state.categories_config.get('L2', DEFAULT_CATEGORIES['L2'])
        l1 = st.session_state.get('sticky_l1', '')
        l2_opts = l2_mapping.get(l1, [])

        shortcuts = {}
        for i, opt in enumerate(l2_opts):
            if i < len(SECOND_LEVEL_SHORTCUTS):
                shortcuts[SECOND_LEVEL_SHORTCUTS[i]] = opt
        return json.dumps(shortcuts, ensure_ascii=False)

    def open_in_system(self, path):
        if not os.path.exists(path):
            st.error(f"路径不存在: {path}"); return
        try:
            if platform.system() == "Windows": os.startfile(path)
            elif platform.system() == "Darwin": subprocess.call(["open", path])
            else: subprocess.call(["xdg-open", path])
        except Exception as e: st.error(f"打开失败: {e}")

    # ── 分类管理 ──

    def render_category_manager(self):
        with st.expander("📋 分类管理", expanded=False):
            config = st.session_state.categories_config
            l1_list = config.get('L1', [])
            l2_dict = config.get('L2', {})

            st.markdown("**一级分类 (L1)**")
            for i, l1 in enumerate(l1_list):
                c1, c2 = st.columns([4, 1])
                with c1:
                    new_l1 = st.text_input(f"L1_{i}", value=l1, label_visibility="collapsed", key=f"l1_edit_{i}")
                    if new_l1 != l1:
                        if new_l1 and new_l1 not in l1_list:
                            l1_list[i] = new_l1
                            if l1 in l2_dict:
                                l2_dict[new_l1] = l2_dict.pop(l1)
                with c2:
                    if st.button("🗑️", key=f"l1_del_{i}", help=f"删除: {l1}"):
                        l1_list.pop(i)
                        if l1 in l2_dict:
                            del l2_dict[l1]
                        st.rerun()

            new_l1_input = st.text_input("新增一级分类", placeholder="输入新分类名称...", key="new_l1_input", label_visibility="collapsed")
            if st.button("➕ 添加一级分类", key="add_l1_btn", use_container_width=True):
                if new_l1_input and new_l1_input not in l1_list:
                    l1_list.append(new_l1_input)
                    l2_dict[new_l1_input] = []
                    st.rerun()

            st.divider()
            st.markdown("**二级分类 (L2)**")

            if l1_list:
                selected_l1 = st.selectbox("选择一级分类", l1_list, key="l2_parent_select", label_visibility="collapsed")
                l2_options = l2_dict.get(selected_l1, [])

                for i, l2 in enumerate(l2_options):
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        new_l2 = st.text_input(f"L2_{i}", value=l2, label_visibility="collapsed", key=f"l2_edit_{selected_l1}_{i}")
                        if new_l2 != l2:
                            if new_l2 and new_l2 not in l2_options:
                                l2_options[i] = new_l2
                    with c2:
                        if st.button("🗑️", key=f"l2_del_{selected_l1}_{i}", help=f"删除: {l2}"):
                            l2_options.pop(i)
                            st.rerun()

                new_l2_input = st.text_input("新增二级分类", placeholder="输入子类名称...", key=f"new_l2_input_{selected_l1}", label_visibility="collapsed")
                if st.button("➕ 添加二级分类", key=f"add_l2_btn_{selected_l1}", use_container_width=True):
                    if new_l2_input and new_l2_input not in l2_options:
                        l2_options.append(new_l2_input)
                        st.rerun()
            else:
                st.info("请先添加一级分类")

            st.divider()
            c_save, c_reset = st.columns(2)
            with c_save:
                if st.button("💾 保存配置", type="primary", use_container_width=True):
                    config['L1'] = l1_list
                    config['L2'] = l2_dict
                    if save_categories_config(config):
                        st.session_state.categories_config = config
                        st.success("✅ 配置已保存并生效！")
                    else:
                        st.error("❌ 保存失败")
            with c_reset:
                if st.button("🔄 恢复默认", use_container_width=True):
                    st.session_state.categories_config = DEFAULT_CATEGORIES.copy()
                    save_categories_config(DEFAULT_CATEGORIES)
                    st.rerun()

    # ── 标签选择器 ──

    @st.fragment
    def render_tag_selector(self, group):
        """快捷标签选择器"""
        current_id = group['id']

        if current_id not in st.session_state.selected_tags:
            st.session_state.selected_tags[current_id] = []

        selected = st.session_state.selected_tags[current_id]
        tags_data = st.session_state.custom_tags

        all_tags = tags_data.get("tags", [])
        frequent_tags = tags_data.get("frequent", [])

        if not all_tags:
            st.caption("暂无标签，请在下方「标签管理」中添加")
            return

        # 常用标签
        if frequent_tags:
            freq_default = [t for t in selected if t in frequent_tags]
            try:
                freq_selected = st.pills(
                    "常用标签", frequent_tags,
                    selection_mode="multi",
                    default=freq_default,
                    key=f"freq_pills_{current_id}",
                    label_visibility="collapsed"
                )
            except Exception:
                logging.warning("常用标签 pills 渲染失败", exc_info=True)
                freq_selected = None
        else:
            freq_selected = None

        # 全部标签（排除常用）
        other_tags = [t for t in all_tags if t not in frequent_tags]
        if other_tags:
            other_default = [t for t in selected if t in other_tags]
            try:
                other_selected = st.pills(
                    "全部标签", other_tags,
                    selection_mode="multi",
                    default=other_default,
                    key=f"other_pills_{current_id}",
                    label_visibility="collapsed"
                )
            except Exception:
                logging.warning("全部标签 pills 渲染失败", exc_info=True)
                other_selected = None
        else:
            other_selected = None

        # 合并两个pills的选择结果
        merged = (freq_selected or []) + (other_selected or [])
        if set(merged) != set(selected):
            st.session_state.selected_tags[current_id] = merged
            st.session_state[f"_pending_tag_sync_{current_id}"] = True
            st.rerun()

    def _del_tag(self, tag, tags_data):
        tags_data["tags"].remove(tag)
        if tag in tags_data.get("frequent", []):
            tags_data["frequent"].remove(tag)
        st.session_state.custom_tags = tags_data
        self._save_tags_to_disk(tags_data)

    def _clear_evidence_pool(self, pool_key):
        st.session_state[pool_key] = []

    # ── 布局/设置面板 ──

    def _render_layout_sliders(self, key_prefix=""):
        """布局调节滑块 + 应用按钮（多处复用）"""
        lc1, lc2 = st.columns(2)
        with lc1:
            new_width = st.slider("横向占比", 50, 90, st.session_state.layout_width, key=f"{key_prefix}layout_width_slider")
            if new_width != st.session_state.layout_width:
                st.session_state.layout_width = new_width
                self._save_settings()
        with lc2:
            new_height = st.slider("纵向占比", 60, 95, st.session_state.layout_height, key=f"{key_prefix}layout_height_slider")
            if new_height != st.session_state.layout_height:
                st.session_state.layout_height = new_height
                self._save_settings()
        if st.button("应用布局", use_container_width=True, key=f"{key_prefix}apply_layout_btn"):
            st.rerun()

    def _render_datasource_loader(self):
        """数据源加载器：任务类型 + 操作员名称 + 路径输入 + 扫描按钮 + 标注员确认（多处复用）"""
        # 任务类型
        st.caption("📋 任务类型")
        task_type = st.radio(
            "任务类型",
            options=["新标", "返修"],
            index=0 if st.session_state.get('task_type', '新标') == '新标' else 1,
            key="_task_type_ds",
            label_visibility="collapsed",
            horizontal=True
        )
        if task_type != st.session_state.get('task_type'):
            st.session_state.task_type = task_type
            self._save_settings()

        # 操作员名称
        st.caption("👤 操作员 *")
        op_raw = st.text_input("操作员名称", value=st.session_state.operator_name,
                               placeholder="请输入您的姓名（必填）...", key="_op_name_ds")
        op_clean = self._sanitize_operator_name(op_raw)
        if op_clean != st.session_state.operator_name:
            st.session_state.operator_name = op_clean
            self._save_settings()

        path_in = st.text_input("任务根目录", value=st.session_state.root_path, placeholder="请输入任务根目录路径...", label_visibility="collapsed")
        is_scanning = st.session_state.get('is_scanning', False)
        op_empty = not st.session_state.operator_name.strip()
        btn_disabled = is_scanning or op_empty
        btn_label = "⏳ 加载中..." if is_scanning else "🔄 加载文件夹"
        if op_empty:
            st.warning("⚠️ 请先填写操作员名称")
        if st.button(btn_label, use_container_width=True, type="primary", disabled=btn_disabled):
            if path_in:
                st.session_state.is_scanning = True
                st.session_state._scan_path = path_in
                st.rerun()

        # CSV 历史文件选择器
        existing_csvs = self._scan_existing_csvs()
        csv_options = ["📅 今天（自动）"] + [os.path.basename(f) for f in existing_csvs]
        current_sel = st.session_state.get('selected_csv')
        if current_sel and current_sel in existing_csvs:
            init_idx = existing_csvs.index(current_sel) + 1
        else:
            init_idx = 0
        chosen = st.selectbox("📂 CSV 数据文件", csv_options, index=init_idx, key="_csv_selector_ds")
        if chosen == csv_options[0]:
            st.session_state.selected_csv = None
        else:
            chosen_idx = csv_options.index(chosen) - 1
            st.session_state.selected_csv = existing_csvs[chosen_idx]

        confirm_enabled = st.checkbox("👤 验收组名称预确认", value=st.session_state.annotator_confirm_enabled,
                                      key="annotator_confirm_cb", help="此功能适用于路径为父级总文件夹场景，当父文件夹内包含多个标注员独立子文件夹，且每个子文件夹下存有对应数据集时，可在此处选定验收对象、统一规范子文件夹命名格式，规范后数据可直接同步录入最终验收报表。")
        if confirm_enabled != st.session_state.annotator_confirm_enabled:
            st.session_state.annotator_confirm_enabled = confirm_enabled
            self._save_settings()

    @staticmethod
    def _clear_sr_widget_keys():
        """清除自定义读取规则面板的 widget 缓存 key（增删槽位后调用，避免 key 错位）"""
        for k in list(st.session_state.keys()):
            if k.startswith("_sr_img_") or k.startswith("_sr_txt_"):
                del st.session_state[k]

    @staticmethod
    def _sanitize_operator_name(name):
        """剔除路径非法字符"""
        return re.sub(r'[\\/:*?"<>|]', '', name)

    def _render_scan_rules_panel(self):
        """自定义读取规则面板：文件夹位数、图片槽位、文本槽位"""
        with st.expander("🔍 自定义读取规则", expanded=False):
            st.caption("修改后需重新点击「加载文件夹」生效。")

            # 初始化工作副本（首次渲染时从已保存规则复制）
            if "_sr_working" not in st.session_state:
                st.session_state._sr_working = json.loads(json.dumps(
                    st.session_state.get('scan_rules', DEFAULT_SCAN_RULES)
                ))
            w = st.session_state._sr_working

            # 文件夹识别
            new_digit_len = st.number_input(
                "文件夹名称位数", min_value=1, max_value=20,
                value=w.get("folder_digit_length", 8),
                key="_sr_digit_len"
            )

            # ---- 图片读取规则 ----
            st.write("**图片读取规则**")
            image_slots = w.get("image_slots", [])
            for i in range(len(image_slots)):
                slot = image_slots[i]
                col1, col2 = st.columns([5, 1])
                with col1:
                    suffixes_str = ",".join(slot.get("stem_suffixes", [""]))
                    new_val = st.text_input(
                        f"图片槽位 {i+1}（名称后缀，逗号分隔）",
                        value=suffixes_str,
                        key=f"_sr_img_{i}",
                        help="仅填写文件名的后缀部分（不包含 .jpg），如 _result。空=直接匹配 {id}.jpg"
                    )
                    parsed = [s.strip() for s in new_val.split(",") if s.strip()]
                    image_slots[i] = {"stem_suffixes": parsed if parsed else [""]}
                with col2:
                    st.write("")
                    if len(image_slots) > 1:
                        if st.button("−", key=f"_sr_img_del_{i}"):
                            w["image_slots"].pop(i)
                            self._clear_sr_widget_keys()
                            st.rerun()

            col_img1, col_img2 = st.columns([1, 4])
            with col_img1:
                if st.button("+ 添加图片槽位", key="_sr_img_add"):
                    w["image_slots"].append({"stem_suffixes": [""]})
                    self._clear_sr_widget_keys()
                    st.rerun()
            with col_img2:
                st.caption(f"匹配规则: {{文件夹名}}{{后缀}}.jpg，如 {{{{id}}}}_result.jpg")

            st.divider()

            # ---- 文本读取规则 ----
            st.write("**文本读取规则**")
            text_slots = w.get("text_slots", [])
            for i in range(len(text_slots)):
                slot = text_slots[i]
                col1, col2 = st.columns([5, 1])
                with col1:
                    suffixes_str = ",".join(slot.get("suffixes", []))
                    new_val = st.text_input(
                        f"文本框 {i+1}（名称后缀，逗号分隔）",
                        value=suffixes_str,
                        key=f"_sr_txt_{i}",
                        help="查找以该后缀结尾的 .txt 文件，如 _ZH,_CH 表示匹配 xxx_ZH.txt 或 xxx_CH.txt"
                    )
                    parsed = [s.strip() for s in new_val.split(",") if s.strip()]
                    text_slots[i] = {"suffixes": parsed}
                with col2:
                    st.write("")
                    if len(text_slots) > 1:
                        if st.button("−", key=f"_sr_txt_del_{i}"):
                            w["text_slots"].pop(i)
                            self._clear_sr_widget_keys()
                            st.rerun()

            col_txt1, col_txt2 = st.columns([1, 4])
            with col_txt1:
                if st.button("+ 添加文本框", key="_sr_txt_add"):
                    w["text_slots"].append({"suffixes": [""]})
                    self._clear_sr_widget_keys()
                    st.rerun()
            with col_txt2:
                st.caption("匹配规则: 找到以「任一后缀」结尾的 .txt，与文件夹名无关")

            st.divider()

            col_save, col_reset = st.columns(2)
            with col_save:
                if st.button("💾 保存规则", use_container_width=True, key="_sr_save"):
                    new_rules = {
                        "folder_digit_length": new_digit_len,
                        "image_slots": json.loads(json.dumps(w["image_slots"])),
                        "text_slots": json.loads(json.dumps(w["text_slots"]))
                    }
                    if save_scan_rules(new_rules):
                        st.session_state.scan_rules = new_rules
                        st.session_state._sr_working = json.loads(json.dumps(new_rules))
                        st.success("规则已保存，请重新加载文件夹生效")
                    else:
                        st.error("保存失败，请检查文件权限")
            with col_reset:
                if st.button("🔄 恢复默认", use_container_width=True, key="_sr_reset"):
                    st.session_state.scan_rules = json.loads(json.dumps(DEFAULT_SCAN_RULES))
                    st.session_state._sr_working = json.loads(json.dumps(DEFAULT_SCAN_RULES))
                    save_scan_rules(st.session_state.scan_rules)
                    st.rerun()

    # ── 视图/快捷键/设置面板 ──

    def _render_view_mode_toggle(self):
        """视图模式切换（四宫格/单图对比）"""
        st.caption("👁️ 视图")
        try:
            v_mode = st.pills("View", ["四宫格", "单图对比"], selection_mode="single",
                              default=st.session_state.view_mode, label_visibility="collapsed")
        except Exception:
            v_mode = st.radio("View", ["四宫格", "单图对比"], horizontal=True, label_visibility="collapsed")
        if v_mode != st.session_state.view_mode:
            st.session_state.view_mode = v_mode
            self._save_settings()
            st.rerun()

    def _render_hotkeys_section(self):
        """快捷键开关 + 说明面板"""
        enable_hotkeys = st.toggle("⌨️ 启用快捷键 (1-4, ~)",
                                    value=st.session_state.get('enable_hotkeys', True))
        if enable_hotkeys != st.session_state.get('enable_hotkeys'):
            st.session_state.enable_hotkeys = enable_hotkeys
            self._save_settings()
            st.rerun()
        with st.expander("⌨️ 快捷键说明", expanded=False):
            st.markdown("""
            | 按键 | 功能 |
            |------|------|
            | `←` 左箭头 | 上一条 |
            | `→` 右箭头 | 下一条 |
            | `空格` | 提交并下一条 |
            | `X` | 快捷合格提交 |
            | `V` | 修改合格提交 |
            | `1` - `4` | 切换图片/视图 |
            | `` ` `` 反引号 | 在系统查看器中打开图片 |
            """)

    def _render_settings_panel(self, key_prefix="", show_operator=False, show_datasource=False):
        """统一设置面板（活跃状态和冷启动共用）
        Args:
            key_prefix: 布局滑块 key 前缀（冷启动用 "cs_"）
            show_operator: 是否显示操作员名称输入（冷启动时为 True）
            show_datasource: 是否显示数据源加载器（活跃状态时为 True）
        """
        # 显示配置加载错误提示
        if st.session_state.get('_settings_load_error'):
            st.warning(f"⚠️ 设置文件加载失败，已使用默认设置。错误: {st.session_state['_settings_load_error']}")
        if st.session_state.get('_tags_load_error'):
            st.warning(f"⚠️ 标签库加载失败，已使用空标签库。错误: {st.session_state['_tags_load_error']}")

        self.render_fullscreen_button()
        st.write("")

        if show_operator:
            st.caption("👤 操作员")
            op_raw = st.text_input("操作员名称", value=st.session_state.operator_name,
                                   placeholder="请输入您的姓名...", key=f"{key_prefix}_op_name_ds", label_visibility="collapsed")
            op_clean = self._sanitize_operator_name(op_raw)
            if op_clean != st.session_state.operator_name:
                st.session_state.operator_name = op_clean
                self._save_settings()
            st.divider()

        self._render_layout_sliders(key_prefix=key_prefix)

        if show_datasource:
            st.divider()
            st.caption("📂 数据源")
            self._render_datasource_loader()

        st.divider()
        self._render_view_mode_toggle()

        st.divider()
        self._render_scan_rules_panel()

        st.divider()
        self._render_hotkeys_section()
