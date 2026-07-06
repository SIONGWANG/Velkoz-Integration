# zone_a.py — A区渲染方法：Ribbon多标签工具栏 + 左侧精简文件列表
import streamlit as st
import streamlit.components.v1 as components
import os

from disk_io import preload_next_images
from utils import BASE_DIR, parse_csv_date


class ZoneAMixin:
    """A 区：Ribbon多标签工具栏(首页/导出/工具/设置) + 左侧精简文件列表"""

    def _compute_stats(self, all_ids, status_map):
        """计算统计数据，返回 dict"""
        current_status_map = {k: v for k, v in status_map.items() if k in set(all_ids)}
        processed_ids = {k for k, v in current_status_map.items() if v}
        total = len(all_ids)
        qualified = sum(1 for v in current_status_map.values() if v == '合格')
        modified = sum(1 for v in current_status_map.values() if v == '修改后合格')
        unqualified = sum(1 for v in current_status_map.values() if v == '不合格')
        unchecked = total - len(processed_ids)
        pending = sum(1 for v in current_status_map.values() if v == '待定')
        pass_rate = round((qualified + modified) / total * 100, 1) if total > 0 else 0
        return {
            'total': total, 'qualified': qualified, 'modified': modified,
            'unqualified': unqualified, 'unchecked': unchecked, 'pending': pending,
            'pass_rate': pass_rate, 'current_status_map': current_status_map,
        }

    @st.fragment
    def render_topbar(self):
        """Ribbon多标签工具栏：首页/导出/工具/设置"""
        groups = st.session_state.data_groups
        if not groups:
            return

        # ── Ribbon 标签栏 ──
        tabs = ["🏠 首页", "📤 导出", "🔧 工具", "⚙️ 设置"]
        active_tab = st.session_state.get('ribbon_tab', '🏠 首页')
        tab_cols = st.columns(len(tabs) + 1)
        for i, tab in enumerate(tabs):
            with tab_cols[i]:
                if st.button(tab, key=f"ribbon_{i}",
                             type="primary" if active_tab == tab else "secondary",
                             use_container_width=True,
                             on_click=lambda _t=tab: st.session_state.update({'ribbon_tab': _t})):
                    pass
        with tab_cols[len(tabs)]:
            if st.button("✕", key="ribbon_collapse", use_container_width=False,
                         help="收起工具栏"):
                st.session_state.topbar_collapsed = True
                st.rerun()

        st.divider()

        # ── 根据标签渲染内容 ──
        if active_tab == "🏠 首页":
            self._render_ribbon_home()
        elif active_tab == "📤 导出":
            self._render_ribbon_export()
        elif active_tab == "🔧 工具":
            self._render_ribbon_tools()
        elif active_tab == "⚙️ 设置":
            self._render_ribbon_settings()

    def _render_ribbon_home(self):
        """Ribbon 首页：统计 + 筛选 + 历史选择 + 布局预设"""
        groups = st.session_state.data_groups
        all_ids = list(dict.fromkeys(g['id'] for g in groups))

        # ── 历史批次选择器 ──
        csv_list = self._get_all_csv_paths()
        if csv_list:
            csv_options = ["📅 全量历史数据（合并）"]
            csv_paths = ["_merged_"]
            for date_str, path in csv_list:
                csv_options.append(f"📋 {os.path.basename(path)}")
                csv_paths.append(path)

            current_sel = st.session_state.get('selected_csv')
            init_idx = csv_paths.index(current_sel) if current_sel and current_sel in csv_paths else 0

            chosen = st.selectbox("📂 质检数据源", csv_options, index=init_idx,
                                  key="ribbon_csv_sel", label_visibility="collapsed")
            new_sel = csv_paths[csv_options.index(chosen)]
            if new_sel != st.session_state.get('selected_csv'):
                st.session_state.selected_csv = new_sel
                self._invalidate_merged_cache()
                st.session_state.last_loaded_id = None
                st.rerun()

            stats_mode = st.session_state.get('history_stats_mode', 'merged')
            mc1, mc2 = st.columns(2)
            with mc1:
                if st.button("📊 全量统计", key="r_stats_m",
                             type="primary" if stats_mode == 'merged' else "secondary",
                             use_container_width=True,
                             on_click=lambda: st.session_state.update({'history_stats_mode': 'merged', 'selected_csv': '_merged_'})):
                    pass
            with mc2:
                if st.button("📋 当日批次", key="r_stats_t",
                             type="primary" if stats_mode == 'single' else "secondary",
                             use_container_width=True,
                             on_click=lambda: st.session_state.update({'history_stats_mode': 'single', 'selected_csv': None})):
                    pass

        # ── 统计卡片 ──
        status_map = self.get_record_status_map(self._get_active_csv_path())
        stats = self._compute_stats(all_ids, status_map)

        c1, c2, c3, c4 = st.columns(4)
        with c1: st.metric("🟢 合格", stats['qualified'])
        with c2: st.metric("🔵 修改后合格", stats['modified'])
        with c3: st.metric("🔴 不合格", stats['unqualified'])
        with c4: st.metric("📊 合格率", f"{stats['pass_rate']}%")

        # ── 筛选按钮 ──
        filter_options = ["全部", "未检", "合格", "修改后合格", "不合格", "待定"]
        current_filter = st.session_state.get('filter_pills', '全部')
        cols = st.columns(6)
        for i, opt in enumerate(filter_options):
            with cols[i]:
                is_sel = (current_filter == opt)
                if st.button(opt, key=f"fbtn_{opt}", use_container_width=True,
                             type="primary" if is_sel else "secondary",
                             on_click=lambda _o=opt: st.session_state.update({'filter_pills': _o})):
                    pass

        # ── 布局预设 ──
        p1, p2, p3 = st.columns(3)
        with p1:
            if st.button("🖥️ 全屏看图", key="preset_fs", use_container_width=True):
                st.session_state.sidebar_visible = False
                st.session_state.topbar_collapsed = True
                st.rerun()
        with p2:
            if st.button("📋 标准质检", key="preset_std", use_container_width=True):
                st.session_state.sidebar_visible = False
                st.session_state.topbar_collapsed = False
                st.rerun()
        with p3:
            if st.button("✨ 极简模式", key="preset_min", use_container_width=True):
                st.session_state.sidebar_visible = False
                st.session_state.topbar_collapsed = True
                st.rerun()

        # ── 确保 current_id 在筛选列表中 ──
        filtered_ids = self._get_filtered_ids(stats['current_status_map'])
        if filtered_ids and st.session_state.current_id not in filtered_ids:
            st.session_state.current_id = filtered_ids[0]
            st.session_state.focus_img_idx = 0

    def _render_ribbon_export(self):
        """Ribbon 导出标签：全部导出功能"""
        self.render_inspection_panel()
        st.divider()
        self.render_export_panel()

    def _render_ribbon_tools(self):
        """Ribbon 工具标签：AI预识别 + 分类管理"""
        # ── AI 预识别 ──
        with st.expander("🤖 AI 预识别", expanded=True):
            import pandas as pd
            from disk_io import load_qa_report
            uploaded_qa = st.file_uploader("手动上传以切换 (可选)", type=['csv'], label_visibility="collapsed")
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
                    st.session_state.qa_source = "默认文件: final_report.csv"
                else:
                    st.session_state.qa_df = pd.DataFrame()
                    st.session_state.qa_source = "⚠️ 未加载"

            if st.session_state.get('_qa_load_failed'):
                st.warning(f"⚠️ QA报告加载失败")
            if not st.session_state.qa_df.empty:
                st.success(st.session_state.qa_source)
            else:
                st.info(st.session_state.qa_source)

        st.divider()
        self.render_category_manager()

    def _render_ribbon_settings(self):
        """Ribbon 设置标签：布局 + 图片缩放 + 全局参数"""
        self._render_settings_panel(show_datasource=True)

    @st.fragment
    def render_sidebar_mini(self):
        """左侧精简文件列表：仅 radio 列表 + 分页"""
        groups = st.session_state.data_groups
        if not groups:
            return

        all_ids = list(dict.fromkeys(g['id'] for g in groups))
        status_map = self.get_record_status_map(self._get_active_csv_path())
        filtered_ids = self._get_filtered_ids(status_map)

        if not filtered_ids:
            filter_val = st.session_state.get('filter_pills', '全部')
            st.info(f"筛选「{filter_val}」下无数据")
            return

        if st.session_state.current_id not in filtered_ids:
            st.session_state.current_id = filtered_ids[0]
            st.session_state.focus_img_idx = 0

        id_to_group = {g['id']: g for g in groups}

        def _fmt(item_id):
            s = status_map.get(str(item_id), '')
            emoji = {'合格': '🟢', '修改后合格': '🔵', '不合格': '🔴', '待定': '🟡'}.get(s, '⚪')
            group = id_to_group.get(item_id, {})
            user_name = group.get('user_name', '未知')
            if user_name and user_name != '未知':
                display_name = user_name if len(user_name) <= 6 else user_name[:5] + '…'
                return f"{emoji} {item_id}  \u00b7 {display_name}"
            return f"{emoji} {item_id}"

        if st.session_state.current_id not in all_ids:
            st.session_state.current_id = all_ids[0]

        # 分页
        PAGE_SIZE = 20
        total_items = len(filtered_ids)
        total_pages = max(1, (total_items + PAGE_SIZE - 1) // PAGE_SIZE)
        current_idx = filtered_ids.index(st.session_state.current_id) if st.session_state.current_id in filtered_ids else 0
        current_page = current_idx // PAGE_SIZE + 1

        if total_pages > 1:
            MAX_VISIBLE = 4
            window_size = min(MAX_VISIBLE, total_pages)
            page_start = ((current_page - 1) // MAX_VISIBLE) * MAX_VISIBLE + 1
            page_end = min(page_start + window_size - 1, total_pages)
            if page_end - page_start + 1 < window_size and page_start > 1:
                page_start = max(1, page_end - window_size + 1)

            visible_pages = list(range(page_start, page_end + 1))
            total_buttons = len(visible_pages) + 2
            page_cols = st.columns(total_buttons)

            def _go_to_page(p):
                target_idx = (p - 1) * PAGE_SIZE
                if target_idx < total_items:
                    new_id = filtered_ids[target_idx]
                    st.session_state.current_id = new_id
                    st.session_state.focus_img_idx = 0
                    st.session_state.needs_scroll_top = True
                    st.session_state._batch_completed = False
                    st.session_state._completion_balloons_shown = False

            with page_cols[0]:
                st.button("◀", disabled=(current_page == 1), key="page_prev",
                          use_container_width=True, on_click=_go_to_page, args=(current_page - 1,))
            for i, p in enumerate(visible_pages):
                with page_cols[1 + i]:
                    btn_type = "primary" if p == current_page else "secondary"
                    st.button(str(p), key=f"page_{p}", type=btn_type,
                              use_container_width=True, on_click=_go_to_page, args=(p,))
            with page_cols[total_buttons - 1]:
                st.button("▶", disabled=(current_page == total_pages), key="page_next",
                          use_container_width=True, on_click=_go_to_page, args=(current_page + 1,))

        # 当前页列表
        start_idx = (current_page - 1) * PAGE_SIZE
        end_idx = min(start_idx + PAGE_SIZE, total_items)
        page_ids = filtered_ids[start_idx:end_idx]

        selected_id = st.radio(
            "List", page_ids,
            index=current_idx - start_idx,
            label_visibility="collapsed",
            format_func=_fmt
        )
        if selected_id != st.session_state.current_id:
            st.session_state.current_id = selected_id
            st.session_state.focus_img_idx = 0
            st.session_state.needs_scroll_top = True
            st.session_state._batch_completed = False
            st.session_state._completion_balloons_shown = False
            all_groups = st.session_state.get('data_groups', [])
            new_idx = all_ids.index(selected_id) if selected_id in all_ids else 0
            preload_next_images(new_idx, all_groups)
            try:
                st.rerun(scope="app")
            except TypeError:
                st.rerun()

        if total_pages > 1:
            st.caption(f"第 {current_page}/{total_pages} 页 (共 {total_items} 项)")

        components.html("""
        <script>
        (function() {
            var doc = window.parent.document;
            var radios = doc.querySelectorAll('input[type="radio"]');
            for (var i = 0; i < radios.length; i++) {
                if (radios[i].checked) {
                    radios[i].scrollIntoView({behavior: 'smooth', block: 'nearest'});
                    break;
                }
            }
        })();
        </script>
        """, height=0, width=0)
