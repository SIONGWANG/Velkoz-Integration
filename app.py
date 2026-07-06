import streamlit as st
import streamlit.components.v1 as components
import os
import pandas as pd
import logging

from utils import (
    BASE_DIR, DEFAULT_CATEGORIES,
    load_categories_config,
    load_scan_rules,
)

from css_styles import MAIN_CSS, STATUS_BUTTON_JS, BZ_TOPBAR_TOGGLE_JS
from disk_io import load_qa_report
from data_mixin import DataMixin
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
        if 'history_stats_mode' not in st.session_state: st.session_state.history_stats_mode = 'merged'
        if 'selected_csv' not in st.session_state: st.session_state.selected_csv = None
        if 'bz_view_mode' not in st.session_state: st.session_state.bz_view_mode = '自动'
        if 'bz_image_ratio' not in st.session_state: st.session_state.bz_image_ratio = settings.get('bz_image_ratio', 65)
        if 'layout_mode' not in st.session_state: st.session_state.layout_mode = settings.get('layout_mode', 'topbar')
        if 'topbar_collapsed' not in st.session_state: st.session_state.topbar_collapsed = False
        if 'sidebar_visible' not in st.session_state: st.session_state.sidebar_visible = False

    def _render_a_zone_panels(self):
        """渲染A区的非统计面板（导出、AI预识别、设置、分类管理）"""
        self.render_inspection_panel()

        st.divider()
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

    def run(self):
        version = os.path.basename(BASE_DIR)
        st.set_page_config(layout="wide", page_title=f"审视之眼pro V{version}")

        st.markdown(MAIN_CSS + STATUS_BUTTON_JS, unsafe_allow_html=True)

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

        b_height_percent = st.session_state.layout_height

        needs_scroll = st.session_state.get('needs_scroll_top', False)
        if needs_scroll:
            st.session_state.needs_scroll_top = False

        bz_ratio = st.session_state.get('bz_image_ratio', 65)
        components.html(f"""
        <script>
        (function() {{
            var d = window.parent.document;
            d.documentElement.style.setProperty('--b-height-percent', {b_height_percent});
            d.documentElement.style.setProperty('--bz-image-ratio', {bz_ratio});
            {"d.querySelector('section.main').scrollTop = 0; d.documentElement.scrollTop = 0;" if needs_scroll else ""}
        }})();
        </script>
        """, height=0, width=0)

        layout_mode = st.session_state.get('layout_mode', 'topbar')

        lookup_groups = st.session_state.data_groups
        current_group = None
        if lookup_groups and st.session_state.current_id:
            current_group = next((g for g in lookup_groups if g['id'] == st.session_state.current_id), None)

        # --- 标注员确认面板 ---
        if st.session_state.confirm_pending and st.session_state.pending_groups:
            self.render_confirm_panel()
            return

        if st.session_state.get('is_scanning', False):
            self._handle_scanning()

        # ═══ 冷启动状态 ═══
        if not current_group:
            if layout_mode == 'topbar':
                # 顶部通栏：数据源加载器居中
                self.render_cold_start_animation()
                with st.container(border=True):
                    self._render_datasource_loader()
                with st.expander("⚙️ 更多设置", expanded=False):
                    self._render_settings_panel(key_prefix="cs_", show_operator=True)
            else:
                # 其他布局：同样居中
                self.render_cold_start_animation()
                with st.container(border=True):
                    self._render_datasource_loader()
                with st.expander("⚙️ 更多设置", expanded=False):
                    self._render_settings_panel(key_prefix="cs_", show_operator=True)
            return

        # ═══ 活跃状态：根据布局模式路由 ═══
        group = current_group
        self.sync_state_from_history(group['id'])

        if layout_mode == 'topbar':
            self._run_topbar_layout(group)
        elif layout_mode == 'bottom':
            self._run_bottom_layout(group)
        else:  # old3col
            self._run_old3col_layout(group)

    def _run_topbar_layout(self, group):
        """顶部通栏布局：A区顶部 + 中B + 右C（左侧列表可折叠）"""
        # ── 顶部折叠按钮 + 通栏A区 ──
        collapsed = st.session_state.get('topbar_collapsed', False)
        toggle_label = "▶ 展开控制栏" if collapsed else "▼ 收起控制栏"
        if st.button(toggle_label, key="topbar_toggle", use_container_width=False):
            st.session_state.topbar_collapsed = not collapsed
            st.rerun()

        if not collapsed:
            with st.container(border=True):
                self.render_topbar()
                self._render_a_zone_panels()

        # ── 左侧列表折叠开关 ──
        sidebar_visible = st.session_state.get('sidebar_visible', False)
        sidebar_label = "📂 隐藏列表" if sidebar_visible else "📂 文件列表"
        if st.button(sidebar_label, key="sidebar_toggle", use_container_width=False):
            st.session_state.sidebar_visible = not sidebar_visible
            st.rerun()

        # ── 主体区域 ──
        if sidebar_visible:
            left_w = 12
            b_width = st.session_state.layout_width
            right_w = max(100 - left_w - b_width, 10)
            col_left, col_b, col_c = st.columns([left_w, b_width, right_w])

            with col_left:
                self.render_sidebar_mini()
            with col_b:
                self._render_b_area(group)
            with col_c:
                self.render_control_panel(group)
        else:
            b_width = st.session_state.layout_width
            right_w = max(100 - b_width, 10)
            col_b, col_c = st.columns([b_width, right_w])

            with col_b:
                self._render_b_area(group)
            with col_c:
                self.render_control_panel(group)

    def _render_b_area(self, group):
        """B区渲染（复用）"""
        if st.session_state.get('_batch_completed'):
            self.render_completion_panel()
        else:
            with st.container():
                self.render_b_image_area(group)

    def _run_bottom_layout(self, group):
        """底部通栏布局：B+C在上，A区在下"""
        b_width = st.session_state.layout_width
        right_w = max(100 - b_width, 10)
        col_b, col_c = st.columns([b_width, right_w])

        with col_b:
            if st.session_state.get('_batch_completed'):
                self.render_completion_panel()
            else:
                with st.container():
                    self.render_b_image_area(group)

        with col_c:
            self.render_control_panel(group)

        # ── 底部A区 ──
        st.divider()
        self.render_topbar()
        self._render_a_zone_panels()

    def _run_old3col_layout(self, group):
        """旧版三栏布局：左A + 中B + 右C"""
        b_width = st.session_state.layout_width
        remain = (100 - b_width) / 2
        col_a, col_b, col_c = st.columns([remain, b_width, remain])

        with col_a:
            self.render_topbar()
            self._render_a_zone_panels()

        with col_b:
            if st.session_state.get('_batch_completed'):
                self.render_completion_panel()
            else:
                with st.container():
                    self.render_b_image_area(group)

        with col_c:
            self.render_control_panel(group)

if __name__ == "__main__":
    app = AcceptanceApp()
    app.run()
