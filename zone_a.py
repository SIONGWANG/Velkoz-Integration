# zone_a.py — A区渲染方法，拆分为顶部通栏 + 左侧精简文件列表
import streamlit as st
import streamlit.components.v1 as components
import os

from disk_io import preload_next_images
from utils import BASE_DIR, parse_csv_date


class ZoneAMixin:
    """A 区：顶部通栏(统计+筛选+导出+设置) + 左侧精简文件列表"""

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
        """顶部通栏：统计卡片 + 筛选 + 历史选择 + 数据源信息"""
        groups = st.session_state.data_groups
        if not groups:
            return

        all_ids = list(dict.fromkeys(g['id'] for g in groups))

        # ── 历史批次选择器 ──
        csv_list = self._get_all_csv_paths()
        if csv_list:
            csv_options = ["📅 全量历史数据（合并）"]
            csv_paths = ["_merged_"]
            for date_str, path in csv_list:
                basename = os.path.basename(path)
                csv_options.append(f"📋 {basename}")
                csv_paths.append(path)

            current_sel = st.session_state.get('selected_csv')
            if current_sel and current_sel in csv_paths:
                init_idx = csv_paths.index(current_sel)
            else:
                init_idx = 0

            chosen = st.selectbox(
                "📂 质检数据源",
                csv_options, index=init_idx,
                key="topbar_csv_selector", label_visibility="collapsed"
            )
            chosen_idx = csv_options.index(chosen)
            new_sel = csv_paths[chosen_idx]
            if new_sel != st.session_state.get('selected_csv'):
                st.session_state.selected_csv = new_sel
                self._invalidate_merged_cache()
                st.session_state.last_loaded_id = None
                st.rerun()

            stats_mode = st.session_state.get('history_stats_mode', 'merged')
            mode_c1, mode_c2 = st.columns(2)
            with mode_c1:
                if st.button("📊 全量统计", key="tb_stats_merged",
                             type="primary" if stats_mode == 'merged' else "secondary",
                             use_container_width=True,
                             on_click=lambda: st.session_state.update({'history_stats_mode': 'merged', 'selected_csv': '_merged_'})):
                    pass
            with mode_c2:
                if st.button("📋 当日批次", key="tb_stats_today",
                             type="primary" if stats_mode == 'single' else "secondary",
                             use_container_width=True,
                             on_click=lambda: st.session_state.update({'history_stats_mode': 'single', 'selected_csv': None})):
                    pass

        # ── 统计卡片（横向4列） ──
        status_map = self.get_record_status_map(self._get_active_csv_path())
        stats = self._compute_stats(all_ids, status_map)

        stats_mode = st.session_state.get('history_stats_mode', 'merged')
        if stats_mode == 'merged' and csv_list and len(csv_list) > 1:
            st.caption(f"📊 全量统计: 合并 {len(csv_list)} 个CSV文件")
        elif stats_mode == 'single' and st.session_state.get('selected_csv'):
            sel_name = os.path.basename(st.session_state['selected_csv'])
            st.caption(f"📋 单日批次: {sel_name}")

        c1, c2, c3, c4 = st.columns(4)
        with c1: st.metric("🟢 合格", stats['qualified'])
        with c2: st.metric("🔵 修改后合格", stats['modified'])
        with c3: st.metric("🔴 不合格", stats['unqualified'])
        with c4: st.metric("📊 合格率", f"{stats['pass_rate']}%")

        c5, c6, c7, c8 = st.columns(4)
        with c5: st.metric("⚪ 未检", stats['unchecked'])
        with c6: st.metric("🟡 待定", stats['pending'])
        with c7: st.metric("📦 总计", stats['total'])
        with c8:
            done = stats['qualified'] + stats['modified'] + stats['unqualified'] + stats['pending']
            progress = min(1.0, done / stats['total']) if stats['total'] > 0 else 0
            st.progress(progress, text=f"{done}/{stats['total']} ({int(progress*100)}%)")

        # ── 筛选按钮（横向6个） ──
        filter_options = ["全部", "未检", "合格", "修改后合格", "不合格", "待定"]
        current_filter = st.session_state.get('filter_pills', '全部')
        cols = st.columns(6)
        for i, opt in enumerate(filter_options):
            with cols[i]:
                is_sel = (current_filter == opt)
                if st.button(opt, key=f"fbtn_{opt}", use_container_width=True,
                             type="primary" if is_sel else "secondary",
                             on_click=lambda _opt=opt: st.session_state.update({'filter_pills': _opt})):
                    pass

        # ── 确保 current_id 在筛选列表中 ──
        filter_val = st.session_state.get('filter_pills', '全部')
        filtered_ids = self._get_filtered_ids(stats['current_status_map'])
        if filtered_ids and st.session_state.current_id not in filtered_ids:
            st.session_state.current_id = filtered_ids[0]
            st.session_state.focus_img_idx = 0

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

        # 滚动到选中项
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
