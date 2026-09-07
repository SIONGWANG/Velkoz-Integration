import streamlit as st
import streamlit.components.v1 as components
import os
import pandas as pd
import logging

from utils import (
    APP_NAME, APP_VERSION, BASE_DIR, DEFAULT_CATEGORIES,
    load_categories_config,
    load_scan_rules,
)

from css_styles import MAIN_CSS, STATUS_BUTTON_JS, build_textarea_auto_js
from disk_io import load_qa_report
from data_mixin import DataMixin
from viewer import get_sync_manager
from settings_mixin import SettingsMixin
from export_mixin import ExportMixin
from zone_a import ZoneAMixin
from zone_b import ZoneBMixin
from zone_c import ZoneCMixin


# ==========================================
# ⚙️ 业务配置 (V47 - 新项目)
# ==========================================

# 日志配置：错误持久化到文件
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "app_error.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

class AcceptanceApp(DataMixin, SettingsMixin, ExportMixin, ZoneAMixin, ZoneBMixin, ZoneCMixin):
    def __init__(self):
        settings = self._load_settings()
        if 'data_groups' not in st.session_state: st.session_state.data_groups = []
        if 'root_path' not in st.session_state: st.session_state.root_path = settings.get('root_path', "")
        if 'current_id' not in st.session_state: st.session_state.current_id = None
        if 'last_loaded_id' not in st.session_state: st.session_state.last_loaded_id = None

        if 'layout_width' not in st.session_state: st.session_state.layout_width = settings.get('layout_width', 80)
        if 'layout_height' not in st.session_state: st.session_state.layout_height = settings.get('layout_height', 85)
        if 'view_mode' not in st.session_state: st.session_state.view_mode = settings.get('view_mode', "四宫格")
        if 'focus_img_idx' not in st.session_state: st.session_state.focus_img_idx = 0
        if 'needs_scroll_top' not in st.session_state: st.session_state.needs_scroll_top = False
        if '_batch_completed' not in st.session_state: st.session_state._batch_completed = False
        if '_completion_balloons_shown' not in st.session_state: st.session_state._completion_balloons_shown = False
        if 'filter_pills' not in st.session_state: st.session_state.filter_pills = "全部"
        if 'enable_hotkeys' not in st.session_state: st.session_state.enable_hotkeys = settings.get('enable_hotkeys', True)
        if 'textarea_mode' not in st.session_state: st.session_state.textarea_mode = settings.get('textarea_mode', 'auto')
        if 'textarea_height' not in st.session_state: st.session_state.textarea_height = settings.get('textarea_height', 68)
        if 'textarea_auto_max' not in st.session_state: st.session_state.textarea_auto_max = settings.get('textarea_auto_max', 400)

        if 'qa_df' not in st.session_state: st.session_state.qa_df = pd.DataFrame()
        if 'qa_source' not in st.session_state: st.session_state.qa_source = "未加载"

        if 'is_scanning' not in st.session_state: st.session_state.is_scanning = False
        if 'scan_cache_buster' not in st.session_state: st.session_state.scan_cache_buster = 0

        if 'categories_config' not in st.session_state:
            st.session_state.categories_config = load_categories_config()

        if 'scan_rules' not in st.session_state:
            st.session_state.scan_rules = load_scan_rules()

        if 'operator_name' not in st.session_state:
            st.session_state.operator_name = settings.get('operator_name', "")

        if 'task_type' not in st.session_state:
            st.session_state.task_type = settings.get('task_type', "新标")

        config = st.session_state.categories_config
        l1_list = config.get('L1', DEFAULT_CATEGORIES['L1'])
        if 'sticky_l1' not in st.session_state: st.session_state.sticky_l1 = l1_list[0] if l1_list else ""
        if 'sticky_l2' not in st.session_state: st.session_state.sticky_l2 = None

        if '_saved_data_groups' not in st.session_state: st.session_state._saved_data_groups = None
        if '_saved_current_id' not in st.session_state: st.session_state._saved_current_id = None
        if 'sampling_acceptance_mode' not in st.session_state: st.session_state.sampling_acceptance_mode = False
        if 'sampling_acceptance_ratio' not in st.session_state: st.session_state.sampling_acceptance_ratio = 20
        if 'confirm_pending' not in st.session_state: st.session_state.confirm_pending = False
        if 'pending_groups' not in st.session_state: st.session_state.pending_groups = []
        if 'annotator_map' not in st.session_state: st.session_state.annotator_map = {}
        if 'annotator_confirm_enabled' not in st.session_state:
            st.session_state.annotator_confirm_enabled = settings.get('annotator_confirm_enabled', False)
        if 'annotator_inclusion' not in st.session_state:
            st.session_state.annotator_inclusion = {}

        if 'custom_tags' not in st.session_state: st.session_state.custom_tags = self._load_tags_from_disk()
        if 'selected_tags' not in st.session_state: st.session_state.selected_tags = {}

    def run(self):
        version = APP_VERSION
        st.set_page_config(layout="wide", page_title=f"{APP_NAME} V{version}")
        
        st.markdown(MAIN_CSS + STATUS_BUTTON_JS, unsafe_allow_html=True)

        # 文本框高度脚本（固定/自适应），随设置注入
        try:
            components.html(
                build_textarea_auto_js(
                    mode=st.session_state.get('textarea_mode', 'auto'),
                    max_height=st.session_state.get('textarea_auto_max', 400),
                    fixed_height=st.session_state.get('textarea_height', 68),
                ),
                height=0, width=0
            )
        except Exception:
            logging.warning("文本框高度脚本注入失败", exc_info=True)

        if st.session_state.get('enable_hotkeys', True):
            self.inject_hotkeys()
        else:
            components.html("""
            <script>
            (function() {
                var doc = window.parent.document;
                if (window.parent.myHotkeysHandler) {
                    doc.removeEventListener('keydown', window.parent.myHotkeysHandler);
                    window.parent.myHotkeysHandler = null;
                }
            })();
            </script>
            """, height=0, width=0)

        b_width_percent = st.session_state.layout_width
        b_height_percent = st.session_state.layout_height

        needs_scroll = st.session_state.get('needs_scroll_top', False)
        if needs_scroll:
            st.session_state.needs_scroll_top = False

        components.html(f"""
        <script>
        (function() {{
            var d = window.parent.document;
            d.documentElement.style.setProperty('--b-height-percent', {b_height_percent});
            {"d.querySelector('section.main').scrollTop = 0; d.documentElement.scrollTop = 0;" if needs_scroll else ""}
        }})();
        </script>
        """, height=0, width=0)
        
        remain = (100 - b_width_percent) / 2
        col_a, col_b, col_c = st.columns([remain, b_width_percent, remain])

        lookup_groups = st.session_state.data_groups
        current_group = None
        if lookup_groups and st.session_state.current_id:
            current_group = next((g for g in lookup_groups if g['id'] == st.session_state.current_id), None)

        # === A 区 ===
        if current_group:
            with col_a:
                self.render_a_zone()
                self.render_inspection_panel()

                # ── 导出管理 ──
                self.render_export_panel()

                # ── AI 预识别 ──
                st.divider()
                with st.expander("🤖 AI 预识别", expanded=False):
                    uploaded_qa = st.file_uploader("手动上传以切换 (可选)", type=['csv'], label_visibility="collapsed",
                                                   help="如果不上传，默认读取上方文件夹中的 final_report.csv")

                    if uploaded_qa is not None:
                        try:
                            st.session_state.qa_df = pd.read_csv(uploaded_qa, dtype=str, encoding='utf-8-sig')
                            st.session_state.qa_source = f"手动切换: {uploaded_qa.name}"
                        except Exception as e:
                            st.error(f"读取失败: {e}")
                    else:
                        default_qa_path = os.path.join(st.session_state.root_path, "final_report.csv") if st.session_state.root_path else ""
                        if default_qa_path and os.path.exists(default_qa_path):
                            st.session_state.qa_df = load_qa_report(default_qa_path)
                            st.session_state.qa_source = f"默认文件: final_report.csv"
                        else:
                            st.session_state.qa_df = pd.DataFrame()
                            st.session_state.qa_source = "⚠️ 未加载 (文件夹下无 final_report.csv)"

                    # QA报告加载失败提示
                    if st.session_state.get('_qa_load_failed'):
                        st.warning(f"⚠️ QA报告加载失败，AI预识别功能暂不可用。错误: {st.session_state.get('_qa_load_error', '未知错误')}")

                    if not st.session_state.qa_df.empty:
                        st.success(st.session_state.qa_source)
                    else:
                        st.info(st.session_state.qa_source)

                # ── 更多设置 ──
                st.divider()
                with st.expander("⚙️ 更多设置", expanded=False):
                    self._render_settings_panel(show_datasource=True)

                # ── 分类管理 ──
                st.divider()
                self.render_category_manager()

        # --- 标注员确认面板 ---
        if st.session_state.confirm_pending and st.session_state.pending_groups:
            with col_b:
                self.render_confirm_panel()
            return

        # 处理"更多设置"中触发的重新加载（数据已加载时点加载按钮）
        if st.session_state.get('is_scanning', False):
            self._handle_scanning()

        if not current_group:
            # === B 区 ===
            with col_b:
                self.render_cold_start_animation()
                with st.container(border=True):
                    self._render_datasource_loader()

                self._render_excel_importer()

                with st.expander("⚙️ 更多设置", expanded=False):
                    self._render_settings_panel(key_prefix="cs_", show_operator=True)
        else:
            group = current_group
            self.sync_state_from_history(group['id'])

            # 同步图片到ImageDock（发送文件夹所有图片；进程内，失败静默降级）
            try:
                sync = get_sync_manager()
                img_groups = st.session_state.data_groups
                all_ids = [g['id'] for g in img_groups]
                curr_idx = all_ids.index(group['id']) if group['id'] in all_ids else 0
                # 获取文件夹中所有图片
                root = group.get('root', '')
                all_images = []
                try:
                    all_images = [os.path.join(root, f) for f in os.listdir(root)
                                 if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.webp'))
                                 and os.path.isfile(os.path.join(root, f))]
                except Exception:
                    pass
                st.session_state._dock_sample = {
                    "sample_id": group['id'],
                    "current_index": curr_idx + 1,
                    "total": len(all_ids),
                    "images": all_images,
                }
                sync.update_images(group['id'], curr_idx + 1, len(all_ids), all_images)
            except Exception:
                pass

            # === B 区 ===
            with col_b:
                if st.session_state.get('_batch_completed'):
                    self.render_completion_panel()
                else:
                    with st.container():
                        self.render_b_image_area(group)

            # === C 区 ===
            with col_c:
                self.render_control_panel(group)

if __name__ == "__main__":
    app = AcceptanceApp()
    app.run()
