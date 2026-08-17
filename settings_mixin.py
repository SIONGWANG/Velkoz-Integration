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
    clean_suffix_list, clean_suffix,
    folder_pattern_from_digit_length, build_folder_pattern_regex,
    FOLDER_BLOCK_RULES, normalize_folder_pattern, match_folder_name,
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
                "annotator_confirm_enabled": False, "operator_name": "", "task_type": "新标",
                "textarea_mode": "auto", "textarea_height": 68, "ai_badge_enabled": False}

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
                "textarea_mode": st.session_state.get('textarea_mode', 'auto'),
                "textarea_height": st.session_state.get('textarea_height', 68),
                "textarea_auto_max": st.session_state.get('textarea_auto_max', 400),
                "ai_badge_enabled": st.session_state.get('ai_badge_enabled', False),
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

    def _render_textarea_settings(self, key_prefix=""):
        """文本框高度设置：固定高度 或 自适应内容（多处复用）"""
        st.caption("📝 文本框高度")

        mode_key = f"{key_prefix}textarea_height_mode"
        height_key = f"{key_prefix}textarea_height_slider"
        cur_mode = st.session_state.get('textarea_mode', 'auto')
        cur_height = st.session_state.get('textarea_height', 68)

        try:
            mode = st.pills("文本框高度模式", ["固定值", "自适应"], selection_mode="single",
                            default="自适应" if cur_mode == "auto" else "固定值",
                            key=mode_key, label_visibility="collapsed")
        except Exception:
            mode = st.radio("文本框高度模式", ["固定值", "自适应"], horizontal=True,
                            index=0 if cur_mode == "fixed" else 1,
                            key=mode_key, label_visibility="collapsed")

        if mode == "固定值":
            new_height = st.slider("固定高度(px)", 40, 400, cur_height,
                                   key=height_key, label_visibility="collapsed")
            st.session_state.textarea_height = new_height
            st.session_state.textarea_mode = "fixed"
        else:
            max_h = st.slider("自适应最大高度(px)", 80, 800,
                              st.session_state.get('textarea_auto_max', 400),
                              key=f"{key_prefix}textarea_auto_max_slider", label_visibility="collapsed")
            st.session_state.textarea_auto_max = max_h
            st.session_state.textarea_mode = "auto"

        if st.button("应用文本框设置", use_container_width=True, key=f"{key_prefix}apply_textarea_btn"):
            self._save_settings()
            st.rerun()

        st.divider()
        ai_badge_val = st.checkbox(
            "编号栏显示 AI 初审标记",
            value=st.session_state.get('ai_badge_enabled', False),
            key=f"{key_prefix}ai_badge_toggle",
            help="在左侧编号后显示 🤖✓ / 🤖✗ 初审标记。默认关闭，避免干扰验收。",
        )
        if ai_badge_val != st.session_state.get('ai_badge_enabled', False):
            st.session_state.ai_badge_enabled = ai_badge_val
            self._save_settings()
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
            if k.startswith("_sr_img_") or k.startswith("_sr_txt_") or k.startswith("_sr_blk_"):
                del st.session_state[k]

    @staticmethod
    def _sanitize_operator_name(name):
        """剔除路径非法字符"""
        return re.sub(r'[\\/:*?"<>|]', '', name)

    @staticmethod
    def _describe_block(block, rule_opts):
        """把单个块翻译为人话描述，用于卡片标题与规则预览。"""
        rule = block.get("rule", "digits")
        if rule == "literal":
            return f"固定字符「{block.get('value') or ''}」"
        base = rule_opts.get(rule, rule)
        if "min" in block or "max" in block:
            lo, hi = block.get("min"), block.get("max")
            if lo is not None and hi is not None:
                q = f"{lo} 位" if lo == hi else f"{lo}-{hi} 位"
            elif lo is not None:
                q = f"至少 {lo} 位"
            else:
                q = f"最多 {hi} 位"
        else:
            q = f"{int(block.get('count') or 1)} 位"
        return f"{base} {q}"

    @staticmethod
    def _generate_block_sample(block):
        """为单个块生成一个合法示例片段（供"填入有效示例"使用）。"""
        rule = block.get("rule", "digits")
        n = int(block.get("count") or 1)
        if "min" in block or "max" in block:
            lo = block.get("min")
            if lo is None:
                lo = block.get("max")
            n = int(lo or 1)
        if rule == "digits":
            return "".join(str((i + 2) % 10) for i in range(n))
        if rule == "lowercase":
            return "abcdefghijklmnopqrstuvwxyz"[:n]
        if rule == "uppercase":
            return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:n]
        if rule == "letters":
            return "张" + "试" * (n - 1)
        if rule == "alnum":
            return "a1b2c3d4e5f6g7h8i9j0"[:n]
        if rule == "wildcard":
            return "x" * n
        if rule == "literal":
            return block.get("value", "")
        if rule == "charset":
            chars = block.get("chars", "")
            body = chars[1:] if chars.startswith("^") else chars
            if not body:
                return "a"
            if chars.startswith("^"):
                return "a" * n
            return "".join((body[0] * n))
        return "a" * n

    def _render_block_length_inputs(self, block, i):
        """字符类块的长度输入：count（精确）或 min/max（范围），依据块数据自动判断。"""
        if "min" in block or "max" in block:
            lo = st.number_input("最小长度", min_value=1, max_value=30,
                                 value=int(block.get("min") or 1),
                                 key=f"_sr_blk_min_{i}", label_visibility="collapsed")
            hi = st.number_input("最大长度", min_value=1, max_value=30,
                                 value=int(block.get("max") or lo),
                                 key=f"_sr_blk_max_{i}", label_visibility="collapsed")
            block["min"], block["max"] = int(lo), int(hi)
            block.pop("count", None)
        else:
            cnt = st.number_input("长度", min_value=1, max_value=30,
                                  value=int(block.get("count") or 1),
                                  key=f"_sr_blk_cnt_{i}", label_visibility="collapsed")
            block["count"] = int(cnt)
            block.pop("min", None)
            block.pop("max", None)

    def _render_folder_blocks_editor(self, blocks):
        """块编辑器：卡片式增删/排序/类型/长度 + 规则预览 + 实时测试匹配。
        blocks: 工作副本中的块列表（直接就地修改）。"""
        rule_opts = {
            "digits": "数字 0-9",
            "lowercase": "小写字母 a-z",
            "uppercase": "大写字母 A-Z",
            "letters": "任意字母（含中文）",
            "alnum": "字母或数字",
            "literal": "固定字符（值）",
            "charset": "自定义字符集",
            "wildcard": "任意字符",
        }
        rule_labels = ["digits", "lowercase", "uppercase", "letters", "alnum",
                       "literal", "charset", "wildcard"]

        # 顶部规则预览：人类化描述 + 正则表达式
        preview_regex, preview_err = build_folder_pattern_regex({"blocks": blocks})
        if preview_err:
            st.warning(f"⚠️ 当前规则不完整：{preview_err}")
        else:
            desc = " + ".join(self._describe_block(b, rule_opts) for b in blocks)
            st.caption(f"📐 当前规则：{desc}")
            st.caption(f"🔍 正则：{preview_regex.pattern}")

        for i in range(len(blocks)):
            block = blocks[i]
            with st.container(border=True):
                # 类型 + 块描述
                tcol1, tcol2 = st.columns([1.3, 3.0])
                with tcol1:
                    cur_rule = block.get("rule", "digits")
                    idx = rule_labels.index(cur_rule) if cur_rule in rule_labels else 0
                    new_rule = st.selectbox(f"块 {i+1} 类型", rule_labels,
                                            index=idx, key=f"_sr_blk_rule_{i}",
                                            format_func=lambda r, ro=rule_opts: ro.get(r, r),
                                            label_visibility="collapsed")
                    # 类型变更时清空该块的内容/长度 key，避免旧值残留导致测试误判
                    if new_rule != cur_rule:
                        block["rule"] = new_rule
                        block.pop("value", None)
                        block.pop("chars", None)
                        for k in (f"_sr_blk_val_{i}", f"_sr_blk_chars_{i}",
                                  f"_sr_blk_cnt_{i}", f"_sr_blk_min_{i}", f"_sr_blk_max_{i}"):
                            st.session_state.pop(k, None)
                        st.rerun()
                with tcol2:
                    st.markdown(self._describe_block(block, rule_opts)
                                if new_rule == cur_rule
                                else rule_opts.get(new_rule, new_rule))
                    st.caption(rule_opts.get(new_rule, new_rule))

                # 内容/长度输入（随类型变化）
                if new_rule == "literal":
                    block["value"] = st.text_input("固定字符串（原样匹配）",
                                                   value=block.get("value", ""),
                                                   key=f"_sr_blk_val_{i}",
                                                   placeholder="例如 ABC / 样本 / F-01",
                                                   help="这一整段文字会原样出现在文件夹名中")
                elif new_rule == "charset":
                    cc2 = st.columns([2, 1])
                    with cc2[0]:
                        block["chars"] = st.text_input("字符集（可输入一组允许字符）",
                                                       value=block.get("chars", ""),
                                                       key=f"_sr_blk_chars_{i}",
                                                       placeholder="例如 0123456789 或 ^0x（^ 开头=排除）",
                                                       help="^ 开头表示排除这些字符，长度仍需填写")
                    with cc2[1]:
                        self._render_block_length_inputs(block, i)
                else:
                    self._render_block_length_inputs(block, i)

                # 标记 + 操作按钮
                act = st.columns([3.4, 1, 1, 1])
                with act[0]:
                    block["label"] = st.text_input("标记（可选）",
                                                   value=block.get("label", ""),
                                                   key=f"_sr_blk_label_{i}",
                                                   placeholder="如 样本ID",
                                                   label_visibility="collapsed")
                with act[1]:
                    if i > 0 and st.button("↑", key=f"_sr_blk_up_{i}", help="上移"):
                        blocks[i-1], blocks[i] = blocks[i], blocks[i-1]
                        self._clear_sr_widget_keys()
                        st.rerun()
                with act[2]:
                    if i < len(blocks)-1 and st.button("↓", key=f"_sr_blk_dn_{i}", help="下移"):
                        blocks[i+1], blocks[i] = blocks[i], blocks[i+1]
                        self._clear_sr_widget_keys()
                        st.rerun()
                with act[3]:
                    if len(blocks) > 1 and st.button("✕", key=f"_sr_blk_del_{i}", help="删除"):
                        blocks.pop(i)
                        self._clear_sr_widget_keys()
                        st.rerun()

        # 添加块 + 生成有效示例
        cc = st.columns([1, 2])
        with cc[0]:
            if st.button("+ 添加块", key="_sr_blk_add"):
                blocks.append({"rule": "digits", "count": 1})
                self._clear_sr_widget_keys()
                st.rerun()
        with cc[1]:
            if st.button("🎲 自动填入有效示例", key="_sr_blk_gen",
                         help="按当前规则自动生成一串文件夹名填入测试框，便于验证"):
                sample = "".join(self._generate_block_sample(b) for b in blocks)
                st.session_state["_sr_blk_test"] = sample
                st.rerun()

        # 实时测试
        regex, err = build_folder_pattern_regex({"blocks": blocks})
        test_name = st.text_input("🧪 测试文件夹名",
                                  key="_sr_blk_test",
                                  placeholder="输入示例文件夹名，即时判断是否匹配",
                                  help="输入后立刻显示是否匹配当前规则；可点击上方「自动填入有效示例」")
        if err:
            st.error(f"⚠️ 规则无效：{err}")
        elif test_name.strip():
            if regex.fullmatch(test_name.strip()):
                st.success(f"✅ 匹配：「{test_name.strip()}」符合当前规则")
            else:
                st.error(f"❌ 不匹配：「{test_name.strip()}」不符合当前规则")
                st.caption(f"当前规则正则为 {regex.pattern}，可参考上方卡片描述检查长度/字符类型")

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

            # 文件夹识别：块式命名规则（兼容旧 folder_digit_length）
            if "folder_pattern" not in w or not w["folder_pattern"].get("blocks"):
                digit_len = w.get("folder_digit_length", 8)
                w["folder_pattern"] = folder_pattern_from_digit_length(digit_len)
            blocks = w["folder_pattern"]["blocks"]

            st.write("**文件夹命名规则**")
            self._render_folder_blocks_editor(blocks)

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
                        help="仅填写文件名的后缀部分（不包含 .png/.jpg 等扩展名），如 _result。空=直接匹配 {id}.png/.jpg/.jpeg"
                    )
                    parsed = [s.strip() for s in new_val.split(",") if s.strip() and not s.startswith(".")]
                    cleaned, _stripped = clean_suffix_list(parsed)
                    if _stripped:
                        st.caption("⚠️ 已自动剥离后缀中的扩展名（后缀不应包含 .png/.jpg）")
                    parsed = cleaned or parsed
                    ext_str = ",".join(slot.get("extensions", []))
                    new_ext = st.text_input(
                        f"槽位 {i+1} 扩展名过滤（可选）",
                        value=ext_str,
                        key=f"_sr_img_ext_{i}",
                        help="留空=支持 .png/.jpg/.jpeg 全部；可限定如 png,jpg。同名不同扩展名时按此处精确匹配"
                    )
                    parsed_ext = [e.strip().lstrip(".").lower() for e in new_ext.split(",") if e.strip().lstrip(".")]
                    if parsed_ext:
                        image_slots[i] = {"stem_suffixes": parsed if parsed else [""],
                                          "extensions": parsed_ext}
                    else:
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
                st.caption(f"匹配规则: {{文件夹名}}{{后缀}}，扩展名自动识别 png/jpg/jpeg")

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
                    blocks = json.loads(json.dumps(w.get("folder_pattern", {}).get("blocks", [])))
                    if not blocks:
                        st.error("文件夹命名规则不能为空")
                    else:
                        regex, err = build_folder_pattern_regex({"blocks": blocks})
                        if err:
                            st.error(f"❌ 规则无效：{err}")
                        else:
                            digit_len = w.get("folder_digit_length", 8)
                            if len(blocks) == 1 and blocks[0].get("rule") == "digits" and blocks[0].get("count") is not None:
                                digit_len = int(blocks[0]["count"])
                            new_rules = {
                                "folder_digit_length": digit_len,
                                "folder_pattern": {"blocks": blocks},
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

    def _render_dock_section(self):
        """悬浮窗开关：打开/关闭 ImageDock（方案 A 单进程内嵌）"""
        from viewer import get_sync_manager, is_dock_available
        if not is_dock_available():
            st.caption("🖼️ 悬浮窗（需安装 PySide6，当前不可用）")
            st.info("未检测到 PySide6，图片悬浮窗已禁用。主程序可正常使用，不影响质检。")
            return
        st.caption("🖼️ 悬浮窗")
        sync = get_sync_manager()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("📂 打开悬浮窗", use_container_width=True, key="_dock_open"):
                sync.open_dock()
                st.rerun()
        with col2:
            if st.button("🚫 关闭悬浮窗", use_container_width=True, key="_dock_close"):
                sync.close_dock()
                st.rerun()
        st.caption("悬浮窗显示当前样本全部图片；关闭后可随时重新打开并自动同步。")
        st.caption("悬浮窗工具栏点「小精灵」可最小化为桌面小精灵（👁 大眼睛），点击小精灵或右键菜单可恢复悬浮窗，可拖动到任意位置。")

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

        st.divider()
        self._render_textarea_settings(key_prefix=key_prefix)

        if show_datasource:
            st.divider()
            st.caption("📂 数据源")
            self._render_datasource_loader()

        st.divider()
        self._render_view_mode_toggle()

        st.divider()
        self._render_dock_section()

        st.divider()
        self._render_scan_rules_panel()

        st.divider()
        self._render_hotkeys_section()
