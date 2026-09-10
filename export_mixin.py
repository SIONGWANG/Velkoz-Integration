# export_mixin.py — 导出/弹窗方法，提取自 app.py
import streamlit as st
import os
import time
import shutil
import logging
import json
import pandas as pd
from PIL import Image

from utils import BASE_DIR, auto_rotate_image
from disk_io import scan_files_from_disk
from export_utils import collect_reject_folders, verify_exported_data as _export_verify
from easter_eggs import on_export_complete


class ExportMixin:
    """导出、归集、弹窗相关方法"""

    def export_reject_data(self):
        csv_file = self.get_csv_filename()
        if not os.path.isfile(csv_file): return False, f"❌ 未找到记录文件: {csv_file}"

        try:
            df = pd.read_csv(csv_file, dtype={'图片ID': str}, encoding='utf-8-sig')
            reject_df = df[df['结果'].isin(["不合格", "待定"])]
            current_ids = [str(g['id']) for g in st.session_state.data_groups]
            reject_df = reject_df[reject_df['图片ID'].isin(current_ids)]
            if reject_df.empty: return False, "⚠️ 记录中没有任何不合格/待定数据。"

            first_path = reject_df.iloc[0]['路径']
            parent_dir = os.path.dirname(first_path)
            prefix = self.get_output_prefix()
            target_dir = os.path.join(parent_dir, f"{prefix}_不合格_{len(reject_df)}组")

            with st.status("正在归集不合格数据...", expanded=True) as reject_status:
                count, missing, msg = collect_reject_folders(reject_df, target_dir)
                reject_status.update(label=f"✅ 归集完成！共 {count} 组", state="complete")
            return True, msg
        except Exception as e:
            return False, f"❌ 归集异常：{str(e)}"

    @st.dialog("⚠️ 确认归集操作", width="medium")
    def show_reject_confirm_dialog(self):
        prefix = self.get_output_prefix()
        st.warning(f"此操作将**物理移动**不合格/待定数据到同级 `{prefix}_不合格_{{数量}}组` 文件夹。\n\n移动后原始位置的数据将被删除，不可撤销！")
        csv_file = self.get_csv_filename()
        if os.path.exists(csv_file):
            try:
                df = pd.read_csv(csv_file, dtype={'图片ID': str}, encoding='utf-8-sig')
                current_ids = [str(g['id']) for g in st.session_state.data_groups]
                df = df[df['图片ID'].isin(current_ids)]
                reject_count = df[df['结果'].isin(["不合格", "待定"])].shape[0]
                st.info(f"将归集 **{reject_count}** 条不合格/待定记录")
            except Exception as e:
                logging.warning("读取不合格数量失败: %s", e)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("取消", use_container_width=True):
                st.rerun()
        with c2:
            if st.button("确认归集", type="primary", use_container_width=True):
                success, msg = self.export_reject_data()
                if success: st.balloons(); st.success(msg)
                else: st.error(msg)

    def export_qualified_data(self, target_dir):
        if not target_dir or not target_dir.strip():
            return False, "❌ 导出路径不能为空"

        csv_file = self.get_csv_filename()
        if not os.path.isfile(csv_file): return False, f"❌ 未找到记录文件: {csv_file}"

        try:
            df = pd.read_csv(csv_file, dtype={'图片ID': str}, encoding='utf-8-sig')
            valid_df = df[df['结果'].isin(["合格", "修改后合格"])]
            current_ids = [str(g['id']) for g in st.session_state.data_groups]
            valid_df = valid_df[valid_df['图片ID'].isin(current_ids)]
            if valid_df.empty: return False, "⚠️ 记录中没有任何合格数据。"

            if not os.path.exists(target_dir): os.makedirs(target_dir)

            count = 0
            missing = 0
            progress_bar = st.progress(0)
            total = len(valid_df)
            for counter, (index, row) in enumerate(valid_df.iterrows()):
                src_path = row['路径']
                dst_path = os.path.join(target_dir, str(row['图片ID']))
                if os.path.exists(src_path):
                    if os.path.exists(dst_path): shutil.rmtree(dst_path)
                    shutil.copytree(src_path, dst_path, ignore=shutil.ignore_patterns('Thumbs.db', 'desktop.ini', '.DS_Store'))
                    count += 1
                else:
                    missing += 1
                progress_bar.progress(min(1.0, (counter + 1) / total))
            msg = f"✅ 已导出 {count} 组数据至：\n{target_dir}"
            if missing > 0:
                msg += f"\n⚠️ {missing} 条记录源路径不存在，已跳过"
            return True, msg
        except Exception as e:
            return False, f"❌ 导出异常：{str(e)}"

    def render_inspection_panel(self):
        with st.expander("📊 抽检验收", expanded=False):
            if st.session_state.sampling_acceptance_mode:
                st.success("🎯 抽检验收模式已激活")
                total = len(st.session_state.data_groups)
                done = 0
                samp_csv = self.get_csv_filename()
                if os.path.exists(samp_csv):
                    try:
                        df_done = pd.read_csv(samp_csv, dtype=str, encoding='utf-8-sig')
                        sampled_ids = {str(g['id']) for g in st.session_state.data_groups}
                        done = len(df_done[df_done['图片ID'].astype(str).isin(sampled_ids)])
                    except Exception as e:
                        logging.warning("抽检验收进度读取失败: %s", e)
                st.progress(min(1.0, done / total) if total > 0 else 0)
                st.caption(f"已验收 {done}/{total} 条 (抽检比例 {st.session_state.sampling_acceptance_ratio}%)")

                if st.button("🔙 退出抽检验收", use_container_width=True):
                    st.session_state.sampling_acceptance_mode = False
                    if st.session_state._saved_data_groups is not None:
                        st.session_state.data_groups = st.session_state._saved_data_groups
                        st.session_state.current_id = st.session_state._saved_current_id
                        st.session_state._saved_data_groups = None
                        st.session_state._saved_current_id = None
                    st.rerun()
            else:
                st.markdown("#### 🎯 抽检验收")
                if st.session_state.root_path and os.path.isdir(st.session_state.root_path):
                    all_folders = scan_files_from_disk(st.session_state.root_path, st.session_state.scan_cache_buster,
                                                         rules_json=json.dumps(st.session_state.scan_rules, sort_keys=True, ensure_ascii=False))
                    if all_folders:
                        st.success(f"📂 已加载文件夹: {len(all_folders)} 组数据")
                    else:
                        st.warning("⚠️ 当前文件夹未扫描到数据")
                        all_folders = []

                    st.caption("📈 抽检比例")
                    ratio = st.slider("抽检验收比例", 5, 50, st.session_state.sampling_acceptance_ratio, step=5,
                                      label_visibility="collapsed", key="samp_ratio")
                    if ratio != st.session_state.sampling_acceptance_ratio:
                        st.session_state.sampling_acceptance_ratio = ratio

                    st.write("")
                    if st.button("🎲 开始抽检验收", type="primary", use_container_width=True, key="btn_samp_acc"):
                        if not all_folders:
                            st.error("没有可抽检的数据！")
                        else:
                            self.start_sampling_acceptance(all_folders)
                else:
                    st.info("请先在左侧 📂 列表 中加载文件夹")

    def export_qualifies_with_check(self, force=False):
        df = self._get_df()
        if df.empty:
            return False, "❌ 找不到 CSV 记录文件", {}

        all_folder_ids = set(g['id'] for g in st.session_state.data_groups)
        csv_ids = set(df['图片ID'].astype(str).tolist())

        if not force:
            uncheck_ids = all_folder_ids - csv_ids
            if uncheck_ids:
                df_current = df[df['图片ID'].astype(str).isin(all_folder_ids)]
                stats = {
                    "qualified": len(df_current[df_current['结果'] == '合格']),
                    "modified": len(df_current[df_current['结果'] == '修改后合格']),
                    "unqualified": len(df_current[df_current['结果'] == '不合格']),
                    "pending": len(df_current[df_current['结果'] == '待定']),
                    "unchecked": len(uncheck_ids),
                }
                st.session_state._pending_export_stats = stats
                return False, "", {}

        df_in_scope = df[df['图片ID'].astype(str).isin(all_folder_ids)]
        qualified_df = df_in_scope[df_in_scope['结果'].isin(["合格", "修改后合格"])].drop_duplicates(subset=['图片ID'], keep='last')
        qualified_ids = set(qualified_df['图片ID'].astype(str).tolist())

        if not qualified_ids:
            return False, "⚠️ 没有合格的数据可导出", {}

        id_to_group = {str(g['id']): g for g in st.session_state.data_groups}

        raw_path = st.session_state.root_path
        if not raw_path:
            return False, "❌ 未设置原始文件夹路径", {}

        if raw_path.startswith('\\\\'):
            parent_dir = os.path.dirname(raw_path.rstrip('\\'))
            if parent_dir == raw_path.rstrip('\\'):
                parent_dir = os.path.dirname(raw_path)
        else:
            parent_dir = os.path.dirname(os.path.normpath(raw_path))

        prefix = self.get_output_prefix()
        total = len(qualified_ids)
        export_dir = os.path.join(parent_dir, f"{prefix}_合格数据_{total}组")

        if not os.path.exists(export_dir):
            os.makedirs(export_dir)

        start_time = time.time()
        success_count = 0
        error_ids = []
        missing_count = 0
        missing_ids = set()

        with st.status("正在导出合格数据...", expanded=True) as export_status:
            for idx, (index, row) in enumerate(qualified_df.iterrows()):
                folder_id = str(row['图片ID'])
                group = id_to_group.get(folder_id)
                src_path = group['root'] if group else row['路径']

                elapsed = time.time() - start_time
                avg_time = elapsed / (idx + 1) if idx > 0 else 0
                remaining = int(avg_time * (total - idx - 1))

                if remaining >= 60:
                    remaining_str = f"{remaining//60}分{remaining%60}秒"
                else:
                    remaining_str = f"{remaining}秒"

                if idx % 3 == 0 or idx == total - 1:
                    export_status.update(label=f"正在导出：{folder_id} ({idx+1}/{total})  |  已用 {int(elapsed)}秒  |  剩余 {remaining_str}")

                if not os.path.exists(src_path):
                    missing_count += 1
                    missing_ids.add(folder_id)
                    continue

                try:
                    dst_path = os.path.join(export_dir, folder_id)
                    if os.path.exists(dst_path):
                        shutil.rmtree(dst_path)
                    shutil.copytree(src_path, dst_path, ignore=shutil.ignore_patterns('Thumbs.db', 'desktop.ini', '.DS_Store'))

                    ignored_lower = {'thumbs.db', 'desktop.ini', '.ds_store'}
                    src_all = set(os.listdir(src_path))
                    dst_all = set(os.listdir(dst_path))
                    src_norm = {f.lower() for f in src_all} - ignored_lower
                    dst_norm = {f.lower() for f in dst_all} - ignored_lower
                    if src_norm != dst_norm:
                        missing = src_norm - dst_norm
                        extra = dst_norm - src_norm
                        detail = []
                        if missing: detail.append(f"缺失: {', '.join(sorted(missing))}")
                        if extra: detail.append(f"多出: {', '.join(sorted(extra))}")
                        error_ids.append(f"{folder_id}(文件不一致: {'; '.join(detail)})")
                    else:
                        success_count += 1
                except Exception as e:
                    error_ids.append(f"{folder_id}({str(e)})")

            if error_ids:
                export_status.update(label=f"⚠️ 导出完成，{len(error_ids)} 个异常", state="error")
            elif missing_count:
                export_status.update(label=f"⚠️ 导出完成，{missing_count} 个源文件夹不存在", state="complete")
            else:
                export_status.update(label=f"✅ 导出完成！共 {success_count} 条", state="complete")

        if error_ids:
            msg = f"⚠️ 导出完成，但有 {len(error_ids)} 个文件夹复制失败：\n" + ", ".join(error_ids)
            if missing_count:
                msg += f"\n⚠️ 另有 {missing_count} 个源文件夹不存在（可能已移动或删除）"
            return True, msg, {"export_dir": export_dir}

        verify_result = self.verify_exported_data(qualified_ids - missing_ids, export_dir)

        missing_msg = f"\n⚠️ {missing_count} 个源文件夹不存在（可能已移动或删除）" if missing_count else ""

        if verify_result["success"]:
            return True, f"✅ 导出成功：{success_count} 条{missing_msg}\n{verify_result['message']}", {"export_dir": export_dir}
        else:
            return True, f"⚠️ 导出成功，但验券失败：{missing_msg}\n{verify_result['message']}", {"export_dir": export_dir}

    def verify_exported_data(self, expected_ids=None, export_dir=None):
        if export_dir is None:
            return {"success": False, "message": "❌ 未指定导出文件夹"}

        df = self._get_df()
        if df.empty:
            return {"success": False, "message": "❌ 找不到 CSV 记录文件"}

        try:
            qualified_df = df[df['结果'].isin(["合格", "修改后合格"])]
            if expected_ids is None:
                expected_ids = set(qualified_df['图片ID'].astype(str).tolist())
        except Exception as e:
            return {"success": False, "message": f"❌ CSV读取失败: {e}"}

        id_to_group = {str(g['id']): g for g in st.session_state.data_groups}
        return _export_verify(expected_ids, export_dir, id_to_group, df)

    def export_excel_with_images(self, include_screenshots=True):
        csv_file = self._get_active_csv_path()
        df = self._get_df(csv_file)
        if df.empty:
            return None, "❌ 找不到 CSV 记录文件"

        groups = st.session_state.data_groups
        all_ids = set(str(g['id']) for g in groups)
        df = df[df['图片ID'].astype(str).isin(all_ids)]
        if df.empty:
            return None, "❌ 当前数据组内无记录"

        records_dir = os.path.dirname(csv_file) or BASE_DIR
        os.makedirs(records_dir, exist_ok=True)
        prefix = self.get_output_prefix()
        excel_filename = os.path.join(records_dir, f"{prefix}_验收记录.xlsx")

        # 如果备注列为空，则用标签列填充（保持备注和标签一致）
        if '备注' in df.columns and '标签' in df.columns:
            df['备注'] = df.apply(
                lambda row: row['标签'] if (pd.isna(row['备注']) or str(row['备注']).strip() == '') else row['备注'],
                axis=1
            )

        try:
            with pd.ExcelWriter(excel_filename, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='验收记录')
                workbook = writer.book
                worksheet = writer.sheets['验收记录']

                failed_images = []
                if include_screenshots and "错误截图" in df.columns:
                    try:
                        img_col_idx = df.columns.get_loc("错误截图")
                    except KeyError:
                        img_col_idx = df.columns.get_loc("路径")

                    worksheet.set_column(img_col_idx, img_col_idx, 55)

                    for index, row in df.iterrows():
                        excel_row = index + 1
                        img_paths_str = str(row.get('错误截图', ''))
                        if pd.isna(img_paths_str) or not img_paths_str or img_paths_str == "nan":
                            continue

                        worksheet.set_row(excel_row, 120)
                        paths = img_paths_str.split(";")
                        x_offset = 5

                        for img_rel_path in paths:
                            # 兼容旧记录：绝对/相对/数据根目录/BASE_DIR 多级解析
                            full_img_path = self._resolve_screenshot_path(img_rel_path)

                            if full_img_path:
                                try:
                                    try:
                                        with Image.open(full_img_path) as _probe:
                                            img_w = _probe.width
                                    except Exception:
                                        img_w = 1000
                                    target_w = 150
                                    scale = max(0.05, min(1.0, target_w / max(img_w, 1)))
                                    worksheet.insert_image(excel_row, img_col_idx, full_img_path, {
                                        'x_offset': x_offset,
                                        'y_offset': 5,
                                        'x_scale': scale,
                                        'y_scale': scale,
                                        'object_position': 1
                                    })
                                    x_offset += 120
                                except Exception:
                                    failed_images.append(img_rel_path)

            msg = f"✅ 已生成报表：\n{excel_filename}"
            if failed_images:
                msg += f"\n⚠️ {len(failed_images)} 张图片插入失败（文件损坏或格式不支持）"
            return excel_filename, msg
        except ImportError:
            return None, "❌ 缺少库，请运行: pip install XlsxWriter"
        except Exception as e:
            return None, f"❌ 导出失败: {str(e)}"

    @st.dialog("🔍 高清大图查看", width="large")
    def show_large_image(self, img_path, img_name):
        img = auto_rotate_image(img_path)
        if img is None:
            st.error("无法加载该图片")
            return
        w, h = img.size
        max_dim = max(w, h)
        if max_dim > 3840:
            ratio = 3840 / max_dim
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        st.image(img, caption=img_name, use_container_width=True)
        if st.button("关闭 (Esc)", use_container_width=True): st.rerun()

    @st.dialog("⚠️ 导出拦截：发现未保存的数据", width="large")
    def show_missing_data_dialog(self, missing_ids, total_loaded):
        st.error(f"**拦截原因**：当前共加载了 {total_loaded} 条数据，但检测到有 **{len(missing_ids)}** 条数据根本没有保存（未写入 CSV）。\n\n为保证验收台账完整，请补充以下数据后再导出！")
        st.markdown("### 📌 漏报的图片 ID 明细：")

        ids_text = "\n".join(missing_ids)
        st.code(ids_text, language="text")

        if st.button("知道了，我去补充", use_container_width=True, type="primary"):
            st.rerun()

    def render_export_panel(self):
        """A 区：导出管理面板（合格导出、归集、Excel 报表）"""
        st.divider()
        st.caption("📤 导出管理")

        # ── 导出结果展示（持久化，rerun 后仍可见）──
        export_result = st.session_state.get('_export_result')
        if export_result:
            if export_result.get('success'):
                st.success(export_result['msg'])
                if export_result.get('export_dir'):
                    st.info(f"📁 {export_result['export_dir']}")
            else:
                st.error(export_result['msg'])
            if st.button("✖ 清除结果", use_container_width=True):
                st.session_state._export_result = None
                st.rerun()

        # ── Excel 导出结果展示（持久化下载按钮）──
        excel_path = st.session_state.get('_excel_export_path')
        if excel_path and os.path.exists(excel_path):
            st.success(f"✅ 已生成报表：{os.path.basename(excel_path)}")
            export_dir = os.path.dirname(excel_path)
            c_dl, c_open = st.columns(2)
            with c_dl:
                with open(excel_path, "rb") as f:
                    st.download_button("⬇️ 下载 Excel", f, file_name=os.path.basename(excel_path), use_container_width=True, key="download_excel_panel")
            with c_open:
                if st.button("📂 打开文件夹", use_container_width=True):
                    self.open_in_system(export_dir)
            if st.button("✖ 清除报表", use_container_width=True):
                st.session_state._excel_export_path = None
                st.rerun()

        # ── 一键导出合格数据 ──
        if st.button("🚀 一键导出合格数据", use_container_width=True):
            if not st.session_state.data_groups:
                st.error("请先加载数据源！")
            else:
                success, msg, result = self.export_qualifies_with_check()
                if success:
                    st.session_state._export_result = {'success': True, 'msg': msg, 'export_dir': result.get('export_dir', '')}
                    st.balloons()
                    on_export_complete()
                    st.rerun()
                else:
                    if msg:
                        st.session_state._export_result = {'success': False, 'msg': msg}
                        st.rerun()
                    else:
                        st.session_state.show_export_confirm = True
                        st.rerun()

        # ── 确认导出面板 ──
        if st.session_state.get('show_export_confirm'):
            s = st.session_state.get('_pending_export_stats', {})
            if s:
                export_count = s['qualified'] + s['modified']

                st.divider()
                st.caption("📊 当前质检状态")

                c1, c2, c3 = st.columns(3)
                with c1:
                    st.metric("🟢 合格", s['qualified'])
                    st.metric("🔴 不合格", s['unqualified'])
                with c2:
                    st.metric("🔵 修改后合格", s['modified'])
                    st.metric("🟡 待定", s['pending'])
                with c3:
                    st.metric("⚪ 未检", s['unchecked'])
                    total = s['qualified'] + s['modified'] + s['unqualified'] + s['pending'] + s['unchecked']
                    st.metric("📦 总计", total)

                st.info(
                    f"✅ 本次将导出 **{export_count}** 条合格数据\n\n"
                    f"❌ 🟡 待定 {s['pending']} 条 + ⚪ 未检 {s['unchecked']} 条**不会**被导出"
                )

                bc1, bc2 = st.columns(2)
                with bc1:
                    if st.button("❌ 取消", use_container_width=True):
                        st.session_state.show_export_confirm = False
                        st.rerun()
                with bc2:
                    if st.button("✅ 已知悉，确认导出", use_container_width=True, type="primary"):
                        st.session_state.show_export_confirm = False
                        success, msg, result = self.export_qualifies_with_check(force=True)
                        if success:
                            st.session_state._export_result = {'success': True, 'msg': msg, 'export_dir': result.get('export_dir', '')}
                            st.balloons()
                            on_export_complete()
                        else:
                            st.session_state._export_result = {'success': False, 'msg': msg}
                        st.rerun()

        # ── 一键归集 ──
        if st.button("⚠️ 一键归集不合格/待定", use_container_width=True):
            if not st.session_state.data_groups:
                st.error("请先加载数据源！")
            else:
                missing_ids, total_loaded = self._get_missing_ids()
                if missing_ids:
                    self.show_missing_data_dialog(missing_ids, total_loaded)
                else:
                    self.show_reject_confirm_dialog()

        # ── Excel 报表 ──
        if st.button("📊 导出带图Excel报表", use_container_width=True):
            if not st.session_state.data_groups:
                st.error("请先加载数据！")
            else:
                file_path, msg = self.export_excel_with_images()
                if file_path:
                    st.session_state._excel_export_path = file_path
                    st.balloons()
                    on_export_complete()
                    st.rerun()
                else:
                    st.session_state._export_result = {'success': False, 'msg': msg}
                    st.rerun()
