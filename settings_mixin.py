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

    def _tags_paths(self):
        """返回 (本地镜像, 跨版本共享文件) 两个 tags.json 路径。

        共享位置优先 D:\\VelkozDATA\\config\\tags.json（本机数据盘），
        没有该目录时用 %LOCALAPPDATA%\\Velkoz\\config\\tags.json。
        切换/解压新版本后仍读同一份标签库，不再丢标签。
        可用环境变量 VELKOZ_TAGS_FILE 指定共享文件。
        """
        local = os.path.join(BASE_DIR, "config", "tags.json")
        env = (os.environ.get("VELKOZ_TAGS_FILE") or "").strip()
        if env:
            return local, env
        data_root = r"D:\VelkozDATA"
        if os.path.isdir(data_root):
            return local, os.path.join(data_root, "config", "tags.json")
        localappdata = (os.environ.get("LOCALAPPDATA") or "").strip()
        if localappdata:
            return local, os.path.join(localappdata, "Velkoz", "config", "tags.json")
        return local, None

    @staticmethod
    def _normalize_tags_data(data):
        """把 tags.json 内容规范成 {"tags": [...], "frequent": [...]}。"""
        if isinstance(data, list):
            data = {"tags": data}
        if not isinstance(data, dict):
            return None
        tags, frequent = [], []
        for t in data.get("tags") or []:
            t = str(t).strip()
            if t and t not in tags:
                tags.append(t)
        for t in data.get("frequent") or []:
            t = str(t).strip()
            if t and t not in frequent:
                frequent.append(t)
        # 常用标签必须属于标签库，避免快捷区出现无法取消的“幽灵标签”
        frequent = [t for t in frequent if t in tags]
        return {"tags": tags, "frequent": frequent}

    @classmethod
    def _read_tags_file(cls, path):
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                return None
            return cls._normalize_tags_data(json.loads(content))
        except Exception as e:
            logging.warning("标签库读取失败: %s - %s", path, e)
            return None

    @staticmethod
    def _write_tags_file(path, tags_data):
        """原子写入单个 tags.json，失败返回 False。"""
        if not path:
            return False
        tmp = path + ".tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(tags_data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return True
        except Exception as e:
            logging.warning("标签库写入失败: %s - %s", path, e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False

    def _load_tags_from_disk(self):
        """加载标签库：共享文件优先，其次本地镜像。

        首次使用新版本时若还没有共享文件，会自动把当前 config/tags.json
        迁移过去；之后各版本共用同一份标签库。
        """
        local, shared = self._tags_paths()
        data = self._read_tags_file(shared)
        if data is None:
            data = self._read_tags_file(local)
        if data is None:
            return {"tags": [], "frequent": []}
        # 双写：共享文件给以后的版本用，本地镜像留作备份/降级兼容
        self._write_tags_file(shared, data)
        if local != shared:
            self._write_tags_file(local, data)
        return data

    def _save_tags_to_disk(self, tags_data):
        """保存标签库：共享文件 + 本地镜像双写。"""
        data = self._normalize_tags_data(tags_data) or {"tags": [], "frequent": []}
        local, shared = self._tags_paths()
        ok_shared = self._write_tags_file(shared, data)
        ok_local = self._write_tags_file(local, data)
        if not ok_local and not ok_shared:
            st.warning("⚠️ 标签库保存失败：请检查 config 目录或数据盘是否可写。")
        elif shared and not ok_shared:
            logging.warning("标签库未能写入共享位置: %s（本次仍保存到本地）", shared)

    @classmethod
    def _merge_tags_data(cls, base, incoming):
        """把导入的标签库合并进现有标签库，返回 (合并结果, 新增标签数, 新增常用数)。"""
        base = cls._normalize_tags_data(base) or {"tags": [], "frequent": []}
        incoming = cls._normalize_tags_data(incoming) or {"tags": [], "frequent": []}
        tags = list(base["tags"])
        frequent = list(base["frequent"])
        added_tags = 0
        added_freq = 0
        for t in incoming["tags"]:
            if t not in tags:
                tags.append(t)
                added_tags += 1
        for t in incoming["frequent"]:
            if t in tags and t not in frequent:
                frequent.append(t)
                added_freq += 1
        return {"tags": tags, "frequent": frequent}, added_tags, added_freq


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
                "textarea_mode": "auto", "textarea_height": 68}

    def _save_settings(self):
        settings_file = os.path.join(BASE_DIR, "config", "settings.json")
        try:
            os.makedirs(os.path.dirname(settings_file), exist_ok=True)
            # 读取现有配置，保留 image_viewer 等非本方法管理的键，避免被覆盖
            existing = {}
            if os.path.isfile(settings_file):
                try:
                    with open(settings_file, 'r', encoding='utf-8') as f:
                        loaded = f.read().strip()
                        if loaded:
                            loaded = json.loads(loaded)
                            if isinstance(loaded, dict):
                                existing = loaded
                except Exception:
                    existing = {}
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
            }
            # 合并：保留其他模块写入的键（如 image_viewer）
            if "image_viewer" in existing:
                settings["image_viewer"] = existing["image_viewer"]
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

            // 二级分类快捷键映射：写到父窗口全局，旧监听器也能读到最新配置
            const l2Shortcuts = {l2_shortcuts};
            window.parent.__vkL2Shortcuts = l2Shortcuts;

            // 取按钮文本：innerText 对折叠/隐藏元素可能为空，回退 textContent
            function btnText(el) {{
                if (!el) return '';
                const t = el.innerText;
                return (t && t.length) ? t : (el.textContent || '');
            }}

            function findBtn(test) {{
                const btns = doc.getElementsByTagName('button');
                for (let i = 0; i < btns.length; i++) {{
                    if (test(btns[i])) return btns[i];
                }}
                return null;
            }}

            // 去掉「🟢」前缀和「[D]」后缀，得到纯分类名
            function l2Label(text) {{
                return (text || '')
                    .replace(/🟢/g, '')
                    .replace(/\s*\[[A-Z]\]\s*$/, '')
                    .trim();
            }}

            function findL2Button(key) {{
                const name = (window.parent.__vkL2Shortcuts || {{}})[key];
                if (!name) return null;

                // 优先只看 pills 按钮，避免误点其它带分类名的按钮
                let candidates = doc.querySelectorAll('button[data-testid^="stBaseButton-pills"]');
                if (!candidates.length) candidates = doc.getElementsByTagName('button');

                let loose = null;
                for (let i = 0; i < candidates.length; i++) {{
                    const text = btnText(candidates[i]).trim();
                    if (!text) continue;
                    if (l2Label(text) !== name) continue;
                    // 文本带 [快捷键] 标记时，要求标记与当前按键一致
                    if (!/\[[A-Z]\]\s*$/.test(text)) {{ if (!loose) loose = candidates[i]; continue; }}
                    if (text.endsWith('[' + key + ']')) return candidates[i];
                }}
                if (loose) return loose;

                // 兼容旧渲染：分类名后面可能还有额外文字，退化为包含匹配
                for (let i = 0; i < candidates.length; i++) {{
                    const text = btnText(candidates[i]).trim();
                    if (text.includes('🟢') && text.includes(name) && !/\[[A-Z]\]\s*$/.test(text)) {{
                        return candidates[i];
                    }}
                }}
                return null;
            }}

            // 输入框/文本域/下拉框/contenteditable 正在编辑时不拦截按键
            function isEditable(el) {{
                if (!el) return false;
                const tag = (el.tagName || '').toUpperCase();
                return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable === true;
            }}

            // 中文输入法激活时 e.key 可能是 Process / Unidentified / 空，用物理键位兜底
            function keyName(e) {{
                let k = (e.key || '').toUpperCase();
                if (k === '' || k === 'PROCESS' || k === 'UNIDENTIFIED' || e.keyCode === 229) {{
                    const code = e.code || '';
                    if (code.indexOf('Key') === 0) k = code.slice(3).toUpperCase();
                }}
                return k;
            }}

            // 已选中的 pill 不再点击，避免快捷键重复按下把分类取消
            function isPressed(btn) {{
                const testid = btn.getAttribute('data-testid') || '';
                if (testid.indexOf('Active') >= 0) return true;
                return btn.getAttribute('aria-checked') === 'true'
                    || btn.getAttribute('aria-selected') === 'true'
                    || btn.getAttribute('aria-pressed') === 'true';
            }}

            // 仅当真正命中按钮并触发时才阻止默认行为；查不到按钮则不改动默认行为
            function clickIfBtn(test) {{
                const btn = findBtn(test);
                if (btn && !btn.disabled) {{ btn.click(); return true; }}
                return false;
            }}

            function handleHotkeys(e) {{
                try {{
                    // Ctrl/Alt/Meta 组合键交给浏览器（如 Ctrl+D 收藏、Ctrl+F 查找）
                    if (e.ctrlKey || e.metaKey || e.altKey) return;
                    if (isEditable(doc.activeElement)) return;

                    const key = keyName(e);
                    const l2Map = window.parent.__vkL2Shortcuts || {{}};

                    // 二级分类快捷键（仅在有映射时生效）
                    if (l2Map[key]) {{
                        const btn = findL2Button(key);
                        if (btn && !btn.disabled) {{
                            e.preventDefault();
                            // 长按只算一次，避免反复切换；已选中则不重复点击（pills 再点一次会取消）
                            if (!e.repeat && !isPressed(btn)) btn.click();
                            return;
                        }}
                        // 找不到按钮时不吞键，继续尝试其它快捷键
                    }}

                    // 提交并下一条 — 空格
                    if (e.key === ' ' || e.code === 'Space') {{
                        if (clickIfBtn(b => btnText(b).includes('提交并下一条'))) e.preventDefault();
                        return;
                    }}

                    // 快捷合格提交 — X
                    if (key === 'X') {{
                        if (clickIfBtn(b => btnText(b).includes('快捷合格'))) e.preventDefault();
                        return;
                    }}

                    // 修改合格提交 — V
                    if (key === 'V') {{
                        if (clickIfBtn(b => btnText(b).includes('修改合格'))) e.preventDefault();
                        return;
                    }}

                    // 上一条
                    if (e.key === 'ArrowLeft') {{
                        if (clickIfBtn(b => btnText(b).includes('⬅️') || btnText(b).includes('上一条'))) e.preventDefault();
                        return;
                    }}

                    // 下一条
                    if (e.key === 'ArrowRight') {{
                        if (clickIfBtn(b => btnText(b).includes('下一条 ➡️') || (btnText(b).includes('下一条') && !btnText(b).includes('提交')))) e.preventDefault();
                        return;
                    }}

                    // 视图切换 1-4
                    const viewKeys = {{'1': '🔍', '2': '🔴 原图', '3': '图 1', '4': '图 2'}};
                    if (viewKeys[e.key]) {{
                        const needle = viewKeys[e.key];
                        if (clickIfBtn(b => btnText(b).includes(needle))) e.preventDefault();
                        return;
                    }}

                    // 打开/关闭图片查看器（内置模式 toggle；系统模式打开）
                    if (e.key === '`' || e.key === '~') {{
                        // 优先按稳定的 key 类定位快捷入口（不受折叠/文本影响）
                        const q = doc.querySelector('.st-key-iv_open_viewer button');
                        if (q && !q.disabled) {{
                            e.preventDefault();
                            q.click();
                        }} else if (clickIfBtn(b => btnText(b).includes('查看器'))) {{
                            e.preventDefault();
                        }}
                    }}
                }} catch (err) {{
                    // 单个快捷键处理失败不得使其它监听器失效
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

    # ── 统一"打开图片"入口（内置 / 系统 双模式 + toggle） ──

    def _current_group_images(self):
        """返回当前组的绝对图片路径列表（按 group['images'] 解析）。"""
        cid = st.session_state.get('current_id')
        group = None
        for g in st.session_state.get('data_groups', []):
            if str(g.get('id')) == str(cid):
                group = g
                break
        if not group:
            return None, "请先加载并进入某组数据。"
        root = group.get('root', '')
        images = [os.path.join(root, f) for f in group.get('images', [])]
        images = [p for p in images if os.path.isfile(p)]
        if not images:
            return None, "当前组没有可查看的图片。"
        return images, ""

    def _open_viewer_app(self, image_path=None, image_index=None):
        """统一打开图片：依据查看器模式打开。内置模式支持 toggle（点/按·开、再按关）。
        返回 (msg, ok)。

        Args:
            image_path: 明确要打开的图片绝对路径（优先，最稳妥）
            image_index: 在当前组图片列表中的索引（无 image_path 时使用）
            image_path / image_index 都为空时，沿用原有默认行为（第一张 / toggle）。
        """
        try:
            from image_viewer import open_image_viewer, is_viewer_running, close_image_viewer
            from image_viewer import config as iv_config
        except Exception as e:
            return f"图片查看模块不可用：{e}", False

        images, err = self._current_group_images()
        if images is None:
            return err, False

        explicit = bool(image_path) or (image_index is not None)

        # 计算目标索引 / 目标路径（点击哪张就打开哪张）
        target_path = None
        target_index = 0
        if image_path:
            if not os.path.isfile(image_path):
                return f"图片不存在：{os.path.basename(str(image_path))}", False
            target_path = image_path
            if image_path in images:
                target_index = images.index(image_path)
            else:
                images = list(images) + [image_path]
                target_index = len(images) - 1
        elif image_index is not None:
            try:
                target_index = int(image_index)
            except (TypeError, ValueError):
                return "图片索引无效。", False
            if target_index < 0 or target_index >= len(images):
                return "指定的图片不存在（索引越界）。", False
            target_path = images[target_index]
        else:
            target_index = 0
            target_path = images[0] if images else None

        mode = iv_config.get_viewer_mode()
        if mode == iv_config.MODE_SYSTEM:
            # 系统默认查看器：打开明确的目标图片（外部程序，不 toggle）
            if not target_path or not os.path.isfile(target_path):
                return "图片不存在，无法打开。", False
            self.open_in_system(target_path)
            return f"已用系统默认查看器打开：{os.path.basename(target_path)}", True

        # 内置模式：仅在“无明确目标”时保持原有 toggle 行为；
        # 有明确目标时直接打开/切换到该图片，不做关闭。
        if not explicit and is_viewer_running():
            close_image_viewer()
            return "已关闭内置图片查看器。", True
        sc = iv_config.get_viewer_settings()["shortcuts"]
        ok, msg = open_image_viewer(images, current_index=target_index,
                                    sample_id=str(st.session_state.get('current_id') or ''),
                                    data_root=self.get_data_root(),
                                    evidence_folder=self.get_evidence_folder_name(),
                                    shortcuts=sc,
                                    layout_mode=iv_config.get_layout_mode())
        if not ok:
            return msg, False
        return "已打开内置图片查看器。", True

    def _view_error_screenshots(self):
        """查看当前记录已保存的错误截图 —— 始终使用内置 Viewer，绝不调用浏览器。
        错误截图来源为质检记录（CSV 的「错误截图」列）中已有的路径。"""
        cid = st.session_state.get('current_id')
        if not cid:
            return "当前没有选中的记录。", False
        record = self._get_record_by_id(str(cid))
        if record is None:
            return "当前记录暂无已保存的错误截图。", False
        try:
            import pandas as pd
            img_paths_str = str(record.get('错误截图', '')) if pd.notna(record.get('错误截图', '')) else ''
        except Exception:
            img_paths_str = str(record.get('错误截图', '')) if record.get('错误截图') is not None else ''
        if not img_paths_str or img_paths_str == 'nan':
            return "当前记录暂无已保存的错误截图。", False

        paths = []
        for rel_path in img_paths_str.split(';'):
            rel_path = rel_path.strip()
            if not rel_path:
                continue
            full = self._resolve_screenshot_path(rel_path)
            if full:
                paths.append(full)
        if not paths:
            return "错误截图文件不存在（可能已被移动或删除）。", False

        try:
            from image_viewer import open_image_viewer
            from image_viewer import config as iv_config
        except Exception as e:
            return f"图片查看模块不可用：{e}", False

        sc = iv_config.get_viewer_settings()["shortcuts"]
        ok, msg = open_image_viewer(paths, current_index=0, sample_id=str(cid),
                                    data_root=self.get_data_root(),
                                    evidence_folder=self.get_evidence_folder_name(),
                                    shortcuts=sc,
                                    layout_mode=iv_config.get_layout_mode())
        if not ok:
            return msg, False
        return f"已在内置查看器中打开 {len(paths)} 张错误截图。", True

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

    def render_tag_selector(self, group):
        """快捷标签选择器（作为 C 区 fragment 的一部分渲染，避免整页 rerun 造成卡顿）"""
        current_id = group['id']

        if current_id not in st.session_state.selected_tags:
            st.session_state.selected_tags[current_id] = []

        selected = st.session_state.selected_tags[current_id]
        tags_data = st.session_state.custom_tags

        all_tags = tags_data.get("tags", [])
        frequent_tags = tags_data.get("frequent", [])

        if not all_tags and not selected:
            st.caption("暂无标签，请在下方「标签管理」中添加")
            return

        # 全部标签选项 = 本地标签（排除常用）+ 当前记录里已选但不在本地库的历史标签。
        # 历史标签必须显示并保持选中，否则会在合并时被过滤丢失。
        other_tags = [t for t in all_tags if t not in frequent_tags]
        for t in selected:
            if t not in frequent_tags and t not in other_tags:
                other_tags.append(t)

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
            self._rerun_c_fragment()

    def _rerun_c_fragment(self):
        """优先只重跑当前 C 区 fragment（而非整页），显著降低快捷标签卡顿。
        若处于整页 rerun 中不允许 fragment 级 rerun，或旧版 Streamlit 不支持
        scope 参数，则回退为整页 rerun。"""
        try:
            st.rerun(scope="fragment")
        except TypeError:
            st.rerun()
        except Exception as e:
            try:
                from streamlit.errors import StreamlitAPIException
            except Exception:
                st.rerun()
                return
            if isinstance(e, StreamlitAPIException):
                st.rerun()
            else:
                raise

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

    def _render_excel_importer(self, embedded=False):
        """质检记录恢复：上传历史质检 Excel，匹配后恢复到当前数据
        Args:
            embedded: True 时省略外层 expander（用于嵌套在"更多设置"内）
        """
        from excel_importer import parse_excel, build_import_preview, apply_restore

        data_loaded = bool(st.session_state.get("data_groups"))
        has_imported = st.session_state.get("_import_done", False)

        if embedded:
            st.caption("📥 质检记录恢复（上传以前导出的质检 Excel，恢复验收结果与错误截图）")
            self._render_excel_importer_body(data_loaded, has_imported)
        else:
            with st.expander("📥 质检记录恢复", expanded=False):
                st.caption("如果你有以前导出的质检记录 Excel，可以在这里恢复历史质检结果。")
                st.caption("仅用于恢复历史质检结果，不是原始数据。")
                self._render_excel_importer_body(data_loaded, has_imported)

    def _render_excel_importer_body(self, data_loaded, has_imported):
        """Excel 导入器内部逻辑（不含外层 expander）"""
        from excel_importer import parse_excel, build_import_preview, apply_restore

        if not data_loaded and not has_imported:
            st.info("请先加载当前数据，再恢复质检记录。")

        uploaded = st.file_uploader(
            "上传质检记录 Excel",
            type=["xlsx"],
            key="_import_excel_uploader",
            help="请上传以前导出的质检记录 Excel。程序会根据唯一ID与当前已加载数据进行匹配。Excel中不存在于当前数据的记录不会进入当前质检队列。"
        )

        if uploaded and data_loaded:
            file_size = uploaded.size
            if file_size < 1024:
                size_str = f"{file_size} B"
            elif file_size < 1024 * 1024:
                size_str = f"{file_size / 1024:.1f} KB"
            else:
                size_str = f"{file_size / 1024 / 1024:.1f} MB"
            st.caption(f"文件：{uploaded.name}（{size_str}）")

            if st.session_state.get("_import_uploader_key") != uploaded.name:
                st.session_state["_import_uploader_key"] = uploaded.name
                st.session_state["_import_df"] = None
                st.session_state["_import_preview"] = None
                st.session_state["_import_done"] = False
                st.session_state["_import_summary"] = None

            if st.session_state.get("_import_df") is None:
                with st.spinner("正在分析质检记录..."):
                    df_excel, err = parse_excel(uploaded)
                    if err:
                        st.error(err)
                        return
                    st.session_state["_import_df"] = df_excel
                    st.session_state["_import_file_name"] = uploaded.name

            df_excel = st.session_state.get("_import_df")
            if df_excel is None:
                return

            current_csv_df = self._get_df() if hasattr(self, "_get_df") else None
            data_groups = st.session_state.get("data_groups", [])

            if st.session_state.get("_import_preview") is None:
                preview = build_import_preview(df_excel, data_groups, current_csv_df)
                preview["file_name"] = uploaded.name
                st.session_state["_import_preview"] = preview

            preview = st.session_state.get("_import_preview")
            if not preview:
                return

            st.divider()
            st.markdown("**📋 质检记录恢复预览**")
            c1, c2 = st.columns(2)
            with c1:
                st.metric("当前数据", f"{preview['total_current']} 条")
                st.metric("匹配成功", f"{preview['total_matched']} 条")
                st.metric("Excel孤立记录", f"{preview['total_orphan']} 条")
            with c2:
                st.metric("Excel记录", f"{preview['total_excel']} 条")
                st.metric("保持原状态", f"{preview['total_unmatched_current']} 条")
                st.metric("异常记录", f"{preview['total_duplicates'] + preview['total_anomalies']} 条")

            if preview["overwrite_count"] > 0:
                st.warning(f"⚠️ 将覆盖已有质检：{preview['overwrite_count']} 条")

            if preview["total_orphan"] > 0:
                with st.expander(f"📎 Excel孤立记录（{preview['total_orphan']} 条）", expanded=False):
                    orphan_ids = [item["id"] for item in preview["orphan_excel"]]
                    for oid in orphan_ids[:50]:
                        st.text(oid)
                    if len(orphan_ids) > 50:
                        st.caption(f"... 还有 {len(orphan_ids) - 50} 条")

            if preview["total_duplicates"] + preview["total_anomalies"] > 0:
                with st.expander(f"⚠️ 异常记录（{preview['total_duplicates'] + preview['total_anomalies']} 条）", expanded=False):
                    for item in preview["duplicates"]:
                        st.text(f"{item['id']}  —  {item['reason']}")
                    for item in preview["anomalies"]:
                        st.text(f"{item['id']}  —  {item['reason']}")

            if preview["matched"]:
                with st.expander(f"👀 预览将恢复的验收结果（{preview['total_matched']} 条）", expanded=False):
                    try:
                        import pandas as _pd
                        preview_rows = []
                        for item in preview["matched"][:200]:
                            row = item["excel_row"]
                            shot = str(row.get("错误截图", "") or "")
                            shot_cnt = len([p for p in shot.split(";") if p.strip()])
                            preview_rows.append({
                                "图片ID": item["id"],
                                "结果": row.get("结果", ""),
                                "标签": row.get("标签", ""),
                                "备注": str(row.get("备注", ""))[:40],
                                "截图数": shot_cnt,
                            })
                        st.dataframe(_pd.DataFrame(preview_rows), use_container_width=True, height=280)
                        if len(preview["matched"]) > 200:
                            st.caption(f"... 仅显示前 200 条，共 {len(preview['matched'])} 条")
                    except Exception as e:
                        st.caption(f"预览生成失败：{e}")

            if not has_imported:
                if preview["overwrite_count"] > 0:
                    st.warning(f"确认后，Excel中匹配到的历史质检结果将写入当前数据；如果当前已有相同ID的质检结果，将被Excel中的结果覆盖。")
                if st.button("✅ 确认恢复", type="primary", use_container_width=True, key="_import_confirm_btn"):
                    csv_path = self.get_csv_filename() if hasattr(self, "get_csv_filename") else None
                    if not csv_path:
                        st.error("未找到当前 CSV 路径")
                        return
                    current_csv = self._get_df() if hasattr(self, "_get_df") else None
                    # 从 Excel 提取内嵌错误截图，按旧命名规则存到证据目录。
                    # 这样即使截图文件已丢失（换电脑/旧版本存在软件目录），导入后仍能查看。
                    extracted_count = 0
                    try:
                        from excel_importer import extract_embedded_images
                        embedded = extract_embedded_images(uploaded, self.get_evidence_dir())
                        if embedded:
                            for item in preview["matched"]:
                                rid = item["id"]
                                if rid in embedded:
                                    item["excel_row"]["错误截图"] = ";".join(embedded[rid])
                            extracted_count = sum(len(v) for v in embedded.values())
                    except Exception as e:
                        logging.warning("Excel 内嵌截图提取失败: %s", e)
                    summary, err = apply_restore(preview, data_groups, current_csv, csv_path)
                    if err:
                        st.error(err)
                        return
                    summary["file_name"] = uploaded.name
                    summary["extracted_screenshots"] = extracted_count
                    st.session_state["_import_done"] = True
                    st.session_state["_import_summary"] = summary
                    # Clear all CSV caches to force fresh read
                    for k in list(st.session_state.keys()):
                        if k.startswith("_df_cache_") or k.startswith("_df_lookup_"):
                            del st.session_state[k]
                    # Force sync_state_from_history to re-run for current image
                    st.session_state["last_loaded_id"] = None
                    st.rerun()
            else:
                summary = st.session_state.get("_import_summary", {})
                st.success("✅ 质检记录恢复完成")
                st.caption(f"文件：{summary.get('file_name', '')}")
                c1, c2 = st.columns(2)
                with c1:
                    st.metric("当前数据", f"{summary.get('total_current', 0)} 条")
                    st.metric("成功恢复", f"{summary.get('restored', 0)} 条")
                    st.metric("Excel孤立记录", f"{summary.get('orphan', 0)} 条")
                with c2:
                    st.metric("Excel记录", f"{summary.get('total_excel', 0)} 条")
                    st.metric("保持原状态", f"{summary.get('unmatched_current', 0)} 条")
                    st.metric("异常记录", f"{summary.get('duplicates', 0) + summary.get('anomalies', 0)} 条")
                if summary.get("extracted_screenshots"):
                    st.success(f"🖼️ 已从 Excel 提取并恢复 {summary['extracted_screenshots']} 张错误截图")
                st.info("恢复的数据已经进入正常质检流程，可以继续修改和提交。")

    @staticmethod
    def _clear_sr_widget_keys(prefix=None):
        """清除自定义读取规则面板的 widget 缓存 key（增删槽位后调用，避免 key 错位）。
        prefix 为空时清理全部三类；否则只清理指定前缀（如 "_sr_img_"）。"""
        prefixes = (prefix,) if prefix else ("_sr_img_", "_sr_txt_", "_sr_blk_")
        for k in list(st.session_state.keys()):
            if any(k.startswith(p) for p in prefixes):
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

    def _render_block_length_inputs(self, block, i, compact=False, narrow=False, gen=0):
        """字符类块的长度输入：count（精确）或 min/max（范围），依据块数据自动判断。
        compact=True 时隐藏标签，供横排紧凑布局使用。"""
        vis = "collapsed" if compact else "visible"
        width = "stretch" if (narrow or not compact) else 78
        if "min" in block or "max" in block:
            lo = st.number_input("最小", min_value=1, max_value=30,
                                 value=int(block.get("min") or 1),
                                 key=f"_sr_blk_min_{gen}_{i}", label_visibility=vis,
                                 help="最小长度" if compact else None,
                                 width=width)
            hi = st.number_input("最大", min_value=1, max_value=30,
                                 value=int(block.get("max") or lo),
                                 key=f"_sr_blk_max_{gen}_{i}", label_visibility=vis,
                                 help="最大长度" if compact else None,
                                 width=width)
            block["min"], block["max"] = int(lo), int(hi)
            block.pop("count", None)
        else:
            cnt = st.number_input("长度（字符数）", min_value=1, max_value=30,
                                  value=int(block.get("count") or 1),
                                  key=f"_sr_blk_cnt_{gen}_{i}", label_visibility=vis,
                                  help="长度（字符数）" if compact else None,
                                  width=width)
            block["count"] = int(cnt)
            block.pop("min", None)
            block.pop("max", None)

    def _render_folder_blocks_editor(self, blocks, narrow=False, gen=0):
        """块编辑器：每块一条紧凑横排，所有块同时可见。
        blocks: 工作副本中的块列表（直接就地修改）。"""
        rule_opts = {
            "digits": "数字 0-9",
            "lowercase": "小写 a-z",
            "uppercase": "大写 A-Z",
            "letters": "任意字母",
            "alnum": "字母/数字",
            "literal": "固定字符",
            "charset": "字符集",
            "wildcard": "任意字符",
        }
        rule_short = {
            "digits": "数字",
            "lowercase": "小写",
            "uppercase": "大写",
            "letters": "字母",
            "alnum": "混合",
            "literal": "固定",
            "charset": "集合",
            "wildcard": "任意",
        }
        rule_labels = ["digits", "lowercase", "uppercase", "letters", "alnum",
                       "literal", "charset", "wildcard"]

        # 顶部规则概览（正则放到测试区，平时不占高度）
        _, preview_err = build_folder_pattern_regex({"blocks": blocks})
        if preview_err:
            st.warning(f"⚠️ 当前规则不完整：{preview_err}")
        else:
            chips = []
            for n, b in enumerate(blocks):
                if n:
                    chips.append('<span class="vk-chip-plus">+</span>')
                chips.append(
                    f'<span class="vk-chip"><b>{n+1}</b>'
                    f'{self._describe_block(b, rule_opts)}</span>'
                )
            st.markdown(
                f'<div class="vk-rule-chips">{"".join(chips)}</div>',
                unsafe_allow_html=True,
            )

        # 所有块逐条横排，不折叠、不切换
        for i, block in enumerate(blocks):
            with st.container(horizontal=True, vertical_alignment="center",
                              gap="small", key=f"sr_blk_row_{gen}_{i}"):
                st.markdown(f'<span class="vk-row-no">{i+1}</span>',
                            unsafe_allow_html=True)
                cur_rule = block.get("rule", "digits")
                idx = rule_labels.index(cur_rule) if cur_rule in rule_labels else 0
                new_rule = st.selectbox("类型", rule_labels,
                                        index=idx, key=f"_sr_blk_rule_{gen}_{i}",
                                        format_func=lambda r, rs=rule_short: rs.get(r, r),
                                        label_visibility="collapsed",
                                        help="字符类型",
                                        width="stretch" if narrow else 100)
                # 类型变更时清空该块的内容/长度 key，避免旧值残留导致测试误判
                if new_rule != cur_rule:
                    block["rule"] = new_rule
                    block.pop("value", None)
                    block.pop("chars", None)
                    for k in (f"_sr_blk_val_{gen}_{i}", f"_sr_blk_chars_{gen}_{i}",
                              f"_sr_blk_cnt_{gen}_{i}", f"_sr_blk_min_{gen}_{i}",
                              f"_sr_blk_max_{gen}_{i}"):
                        st.session_state.pop(k, None)
                    st.session_state["_sr_gen"] = gen + 1
                    st.rerun()

                if new_rule == "literal":
                    block["value"] = st.text_input(
                        "固定字符串", value=block.get("value", ""),
                        key=f"_sr_blk_val_{gen}_{i}", placeholder="固定字符串",
                        label_visibility="collapsed",
                        help="原样匹配：这一整段文字会完整出现在文件夹名中",
                        width="stretch" if narrow else 180)
                elif new_rule == "charset":
                    block["chars"] = st.text_input(
                        "字符集", value=block.get("chars", ""),
                        key=f"_sr_blk_chars_{gen}_{i}", placeholder="字符集，^ 开头=排除",
                        label_visibility="collapsed",
                        help="允许的字符集合；^ 开头表示排除这些字符",
                        width="stretch" if narrow else 170)
                    self._render_block_length_inputs(block, i, compact=True,
                                                     narrow=narrow, gen=gen)
                else:
                    self._render_block_length_inputs(block, i, compact=True,
                                                     narrow=narrow, gen=gen)

                if st.button("↑", key=f"_sr_blk_up_{gen}_{i}", help="上移", disabled=(i == 0)):
                    blocks[i-1], blocks[i] = blocks[i], blocks[i-1]
                    self._clear_sr_widget_keys("_sr_blk_")
                    st.session_state["_sr_gen"] = gen + 1
                    st.rerun()
                if st.button("↓", key=f"_sr_blk_dn_{gen}_{i}", help="下移",
                             disabled=(i == len(blocks) - 1)):
                    blocks[i+1], blocks[i] = blocks[i], blocks[i+1]
                    self._clear_sr_widget_keys("_sr_blk_")
                    st.session_state["_sr_gen"] = gen + 1
                    st.rerun()
                if st.button("✕", key=f"_sr_blk_del_{gen}_{i}", help="删除",
                             disabled=(len(blocks) <= 1)):
                    blocks.pop(i)
                    self._clear_sr_widget_keys("_sr_blk_")
                    st.session_state["_sr_gen"] = gen + 1
                    st.rerun()

        if st.button("+ 添加块", key="_sr_blk_add"):
            blocks.append({"rule": "digits", "count": 1})
            self._clear_sr_widget_keys("_sr_blk_")
            st.session_state["_sr_gen"] = gen + 1
            st.rerun()

        # 测试收进折叠区，平时不占页面高度
        with st.expander("🧪 测试匹配", expanded=False):
            sample = "".join(self._generate_block_sample(b) for b in blocks)
            if st.button("🎲 填入示例", key="_sr_blk_gen", use_container_width=True,
                         help="按当前规则生成一个合法文件夹名并填入下方"):
                st.session_state["_sr_blk_test"] = sample
                st.rerun()
            test_name = st.text_input("测试文件夹名",
                                      key="_sr_blk_test",
                                      placeholder=f"例如 {sample}")
            regex, err = build_folder_pattern_regex({"blocks": blocks})
            if err:
                st.error(f"⚠️ 规则无效：{err}")
            elif test_name.strip():
                if regex.fullmatch(test_name.strip()):
                    st.success(f"✅ 匹配：「{test_name.strip()}」符合当前规则")
                else:
                    st.error(f"❌ 不匹配：「{test_name.strip()}」不符合当前规则")
            if not err:
                st.markdown(f'<div class="vk-rule-regex">正则：{regex.pattern}</div>',
                            unsafe_allow_html=True)

    def _render_scan_rules_panel(self, narrow=False):
        """自定义读取规则：所有配置逐条横排展示，直接修改。"""
        with st.expander("🔍 自定义读取规则", expanded=False):
            st.caption("改完保存，再重新加载文件夹生效。")

            # 初始化工作副本（首次渲染时从已保存规则复制）
            if "_sr_working" not in st.session_state:
                st.session_state._sr_working = json.loads(json.dumps(
                    st.session_state.get('scan_rules', DEFAULT_SCAN_RULES)
                ))
            w = st.session_state._sr_working

            # widget key 代次：增删/排序后 +1，避免旧的前端控件状态套到新的一行
            if "_sr_gen" not in st.session_state:
                st.session_state["_sr_gen"] = 0
            gen = st.session_state["_sr_gen"]

            # 文件夹识别：块式命名规则（兼容旧 folder_digit_length）
            if "folder_pattern" not in w or not w["folder_pattern"].get("blocks"):
                digit_len = w.get("folder_digit_length", 8)
                w["folder_pattern"] = folder_pattern_from_digit_length(digit_len)
            blocks = w["folder_pattern"]["blocks"]

            with st.container(key="sr_rules_wrap"):
                st.caption("文件夹命名 · 每块：类型 / 长度 / 排序")
                self._render_folder_blocks_editor(blocks, narrow=narrow, gen=gen)

                st.divider()

                st.caption("图片槽位 · 后缀留空 = 匹配「编号.png」")
                image_slots = w.get("image_slots") or [{"stem_suffixes": [""]}]
                w["image_slots"] = image_slots
                for i, slot in enumerate(image_slots):
                    stripped = False
                    with st.container(horizontal=True, vertical_alignment="center",
                                      gap="small", key=f"sr_img_row_{gen}_{i}"):
                        st.markdown(f'<span class="vk-row-no">{i+1}</span>',
                                    unsafe_allow_html=True)
                        suffixes_str = ",".join(slot.get("stem_suffixes", [""]))
                        new_val = st.text_input(
                            "后缀", value=suffixes_str, key=f"_sr_img_{gen}_{i}",
                            placeholder="后缀，如 _result（留空=无）",
                            label_visibility="collapsed",
                            help="只填后缀，不要带 .png/.jpg；留空表示直接匹配 编号.png",
                            width="stretch" if narrow else 190)
                        parsed = [s.strip() for s in new_val.split(",") if s.strip() and not s.startswith(".")]
                        cleaned, _stripped = clean_suffix_list(parsed)
                        parsed = cleaned or parsed
                        stripped = bool(_stripped)

                        ext_str = ",".join(slot.get("extensions", []))
                        new_ext = st.text_input(
                            "扩展名", value=ext_str, key=f"_sr_img_ext_{gen}_{i}",
                            placeholder="扩展名（留空=全部）",
                            label_visibility="collapsed",
                            help="留空支持 png/jpg/jpeg；可填 png,jpg 限定",
                            width="stretch" if narrow else 170)
                        parsed_ext = [e.strip().lstrip(".").lower()
                                      for e in new_ext.split(",") if e.strip().lstrip(".")]
                        if parsed_ext:
                            image_slots[i] = {"stem_suffixes": parsed if parsed else [""],
                                              "extensions": parsed_ext}
                        else:
                            image_slots[i] = {"stem_suffixes": parsed if parsed else [""]}

                        if st.button("✕", key=f"_sr_img_del_{gen}_{i}", help="删除此槽位",
                                     disabled=(len(image_slots) <= 1)):
                            image_slots.pop(i)
                            self._clear_sr_widget_keys("_sr_img_")
                            st.session_state["_sr_gen"] = gen + 1
                            st.rerun()
                    if stripped:
                        st.caption(f"槽位 {i+1}：已自动去掉后缀里的扩展名")

                if st.button("+ 图片槽位", key="_sr_img_add"):
                    w["image_slots"].append({"stem_suffixes": [""]})
                    self._clear_sr_widget_keys("_sr_img_")
                    st.session_state["_sr_gen"] = gen + 1
                    st.rerun()

                st.divider()

                st.caption("文本槽位 · 按后缀读取 .txt，与文件夹名无关")
                text_slots = w.get("text_slots") or [{"suffixes": [""]}]
                w["text_slots"] = text_slots
                for i, slot in enumerate(text_slots):
                    with st.container(horizontal=True, vertical_alignment="center",
                                      gap="small", key=f"sr_txt_row_{gen}_{i}"):
                        st.markdown(f'<span class="vk-row-no">{i+1}</span>',
                                    unsafe_allow_html=True)
                        suffixes_str = ",".join(slot.get("suffixes", []))
                        new_val = st.text_input(
                            "后缀", value=suffixes_str, key=f"_sr_txt_{gen}_{i}",
                            placeholder="后缀，如 _ZH,_CH",
                            label_visibility="collapsed",
                            help="查找以该后缀结尾的 .txt，多个用英文逗号分隔",
                            width="stretch" if narrow else 300)
                        parsed = [s.strip() for s in new_val.split(",") if s.strip()]
                        text_slots[i] = {"suffixes": parsed}
                        if st.button("✕", key=f"_sr_txt_del_{gen}_{i}", help="删除此文本框",
                                     disabled=(len(text_slots) <= 1)):
                            text_slots.pop(i)
                            self._clear_sr_widget_keys("_sr_txt_")
                            st.session_state["_sr_gen"] = gen + 1
                            st.rerun()

                if st.button("+ 文本框", key="_sr_txt_add"):
                    w["text_slots"].append({"suffixes": [""]})
                    self._clear_sr_widget_keys("_sr_txt_")
                    st.session_state["_sr_gen"] = gen + 1
                    st.rerun()

                st.divider()

                # ---- 保存 / 恢复默认 ----
                col_save, col_reset = st.columns(2)
                with col_save:
                    if st.button("💾 保存规则", use_container_width=True,
                                 key="_sr_save", type="primary"):
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

        self._render_image_viewer_section()

    def _render_image_viewer_section(self):
        """独立图片查看器：模式切换(Phase 6) + 快捷键设置(Phase 2)"""
        try:
            from image_viewer import open_image_viewer, is_viewer_running
            from image_viewer import config as iv_config
        except Exception:
            st.caption("🔍 独立图片查看器（模块不可用，已跳过）")
            return
        st.divider()
        st.caption("🔍 图片查看")
        group = None
        cid = st.session_state.get('current_id')
        for g in st.session_state.get('data_groups', []):
            if str(g.get('id')) == str(cid):
                group = g
                break

        # ── 查看器模式：内置 / 系统默认 ──
        mode = iv_config.get_viewer_mode()
        mode_labels = iv_config.VIEWER_MODE_LABELS
        sel_mode = st.radio(
            "打开方式",
            options=[iv_config.MODE_BUILTIN, iv_config.MODE_SYSTEM],
            format_func=lambda m: mode_labels.get(m, m),
            index=0 if mode == iv_config.MODE_BUILTIN else 1,
            key="_iviewer_mode",
            horizontal=True,
        )
        if sel_mode != mode:
            iv_config.save_viewer_mode(sel_mode)
            st.session_state['_iviewer_opened'] = False
            st.rerun()

        # ── 查看布局：单图 / 双图并排（v1.22.0，仅内置查看器） ──
        # 只在用户真正改变时保存（on_change），避免每次 rerun/提交都把磁盘配置覆盖回旧值。
        layout_mode = iv_config.get_layout_mode()
        # 让选择器与磁盘配置保持一致（查看器内切换布局后，这里也能同步显示）
        if st.session_state.get('_iviewer_layout') != layout_mode:
            st.session_state['_iviewer_layout'] = layout_mode
        layout_labels = iv_config.LAYOUT_MODE_LABELS
        st.radio(
            "查看布局",
            options=[iv_config.LAYOUT_SINGLE, iv_config.LAYOUT_DUAL],
            format_func=lambda m: layout_labels.get(m, m),
            index=0 if layout_mode == iv_config.LAYOUT_SINGLE else 1,
            key="_iviewer_layout",
            horizontal=True,
            on_change=self._on_iviewer_layout_change,
        )

        click_col, status_col = st.columns([1.5, 1])
        with click_col:
            # 统一"打开图片"按钮：内置模式 toggle（开/关），系统模式打开第一张
            toggle_label = "🖼️ 关闭内置查看器" if (mode == iv_config.MODE_BUILTIN and is_viewer_running()) else \
                           ("🔍 打开内置图片查看器" if mode == iv_config.MODE_BUILTIN else "🖼️ 用系统默认查看器打开")
            if st.button(toggle_label, use_container_width=True, key="_iviewer_toggle"):
                msg, ok = self._open_viewer_app()
                if ok:
                    st.toast(msg)
                    st.session_state['_iviewer_opened'] = True
                    st.rerun()
                else:
                    st.warning(msg)
        with status_col:
            if mode == iv_config.MODE_BUILTIN:
                if is_viewer_running():
                    st.caption("🟢 内置查看器运行中")
                else:
                    st.caption("⚪ 内置查看器未打开")
            else:
                st.caption("🖼️ 系统默认查看器")

        # ── 快捷键设置（仅内置模式需要） ──
        self._render_iviewer_shortcuts(iv_config)

        # ── 局域网图片显示缓存（v1.24.2） ──
        self._render_display_cache_section()

    def _render_display_cache_section(self):
        """显示图本地缓存：网络盘繁忙时用本机缓存兜底。"""
        try:
            import image_cache
        except Exception:
            return
        try:
            info = image_cache.stats()
        except Exception:
            info = {"root": "", "files": 0, "mb": 0}
        with st.expander("🗄 局域网图片显示缓存", expanded=False):
            st.caption(
                "主页面图片会缓存在本机：局域网/共享盘读取变慢时先用缓存显示，"
                "不会因为百度网盘下载等占满网络就整页空白。缓存只存显示用 JPEG，不改源文件。"
            )
            st.caption(
                "🧹 **自动清理**：只保留当前正在做的批次。打开/加载新批次时，"
                "会自动删除其他批次的缓存，不需要手动清理。"
            )
            st.caption(f"缓存位置：`{info.get('root', '')}`")
            c1, c2, c3 = st.columns([1, 1, 1.4])
            c1.metric("当前缓存", f"{info.get('mb', 0)} MB")
            c2.metric("缓存图片", f"{info.get('files', 0)} 张")
            with c3:
                if st.button("🧹 清空图片缓存", use_container_width=True, key="_display_cache_clear"):
                    removed = image_cache.clear()
                    st.session_state.pop("_display_img_cache", None)
                    st.success(f"已清空：{removed.get('files', 0)} 张 / {removed.get('mb', 0)} MB")
                    st.rerun()
            st.caption(
                f"保险上限 {info.get('max_mb', 500)} MB：只有单个批次特别大时才会触发；"
                f"磁盘剩余空间不足时会自动收紧或暂停写入。"
            )
            st.caption(
                "如果下载大文件时仍然卡顿：可运行根目录的「修复网络限流.bat」（需管理员），"
                "关闭 Windows 的多媒体网络限流；或把百度网盘下载速度限制在 80% 左右。"
            )

    def _render_iviewer_shortcuts(self, iv_config):
        """图片查看器快捷键配置面板（复用项目设置系统持久化）"""
        with st.expander("⌨️ 查看器快捷键设置", expanded=False):
            st.caption("设置图片查看器的快捷键。修改后点「保存」会立即应用到运行中的查看器；重启程序仍有效。")
            flash = st.session_state.pop('_iv_short_msg', None)
            if flash:
                st.success(flash)
            cur = iv_config.get_viewer_settings()["shortcuts"]
            keys = list(iv_config.DEFAULT_SHORTCUTS.keys())
            inputs = {}
            # 两列布局
            for i in range(0, len(keys), 2):
                r = st.columns(2)
                chunk = keys[i:i + 2]
                for j, k in enumerate(chunk):
                    label = iv_config.SHORTCUT_LABELS.get(k, k)
                    inputs[k] = r[j].text_input(label, value=cur.get(k, iv_config.DEFAULT_SHORTCUTS[k]),
                                                key=f"_iv_short_{k}", label_visibility="collapsed")
                    r[j].caption(label)
            st.caption("支持组合键，如 Ctrl+Z、Shift+A；单键如 Left、Right、0、Esc、+、-。")
            save_btn, reset_btn, _ = st.columns([1, 1, 0.2])
            with save_btn:
                if st.button("💾 保存快捷键", use_container_width=True, key="_iv_short_save"):
                    issues = iv_config.save_shortcuts(inputs)
                    applied = False
                    try:
                        from image_viewer import update_viewer_shortcuts
                        applied = update_viewer_shortcuts(iv_config.get_viewer_settings()["shortcuts"])
                    except Exception:
                        logging.warning("热更新查看器快捷键失败", exc_info=True)
                    if issues:
                        st.warning("部分快捷键非法或冲突，已回退默认：" + "；".join(issues))
                    elif applied:
                        st.success("已保存并即时应用到运行中的查看器。")
                    else:
                        st.success("已保存；查看器当前未运行，下次打开时生效。")
            with reset_btn:
                if st.button("↺ 恢复默认", use_container_width=True, key="_iv_short_reset"):
                    iv_config.save_shortcuts(iv_config.DEFAULT_SHORTCUTS)
                    try:
                        from image_viewer import update_viewer_shortcuts
                        update_viewer_shortcuts(iv_config.get_viewer_settings()["shortcuts"])
                    except Exception:
                        logging.warning("热更新查看器快捷键失败", exc_info=True)
                    # 让输入框刷新为默认值（text_input 的 key 已存在时 value 参数不生效）
                    for k, dv in iv_config.DEFAULT_SHORTCUTS.items():
                        st.session_state[f"_iv_short_{k}"] = dv
                    st.session_state['_iv_short_msg'] = "已恢复默认快捷键并应用。"
                    st.rerun()

    def _on_iviewer_layout_change(self):
        """查看布局选择变化时：保存并热更新运行中的查看器（仅用户改变时触发）。"""
        try:
            from image_viewer import config as iv_config, update_viewer_layout
        except Exception:
            return
        sel = st.session_state.get('_iviewer_layout')
        if sel not in (iv_config.LAYOUT_SINGLE, iv_config.LAYOUT_DUAL):
            return
        iv_config.save_layout_mode(sel)
        try:
            update_viewer_layout(sel)
        except Exception:
            logging.warning("热更新查看器布局失败", exc_info=True)

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
            self._render_excel_importer(embedded=True)

        st.divider()
        self._render_view_mode_toggle()

        st.divider()
        self._render_dock_section()

        st.divider()
        self._render_scan_rules_panel(narrow=(key_prefix == ""))

        st.divider()
        self._render_hotkeys_section()
