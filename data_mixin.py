# data_mixin.py — 数据/CSV/导航相关方法，提取自 app.py
import streamlit as st
import os
import time
import random
import datetime
import logging
import json
import hashlib
import pandas as pd
from PIL import Image as PILImage

from utils import (
    BASE_DIR, DEFAULT_CATEGORIES,
    _load_and_rotate,
)
from export_utils import get_missing_ids as _export_get_missing_ids
from disk_io import scan_files_from_disk, preload_next_images
from easter_eggs import on_data_loaded, on_sampling_start


class DataMixin:
    """数据读写、CSV 管理、导航控制相关方法"""

    # ── CSV/查找 ──

    def _build_lookup(self, df):
        """从 DataFrame 构建 图片ID → 行记录 的 O(1) 查找字典（保留每个 ID 最后一条）"""
        lookup = {}
        if not df.empty and '图片ID' in df.columns:
            for _, row in df.iterrows():
                lookup[str(row['图片ID'])] = row
        return lookup

    def _get_df(self, csv_file=None):
        if csv_file is None:
            csv_file = self.get_csv_filename()
        cache_key = f"_df_cache_{csv_file}"
        if cache_key in st.session_state:
            return st.session_state[cache_key].copy()
        if os.path.isfile(csv_file):
            df = pd.DataFrame()
            last_err = None
            encoding_fallback = False
            for attempt in range(3):
                try:
                    df = pd.read_csv(csv_file, dtype=str, encoding='utf-8-sig')
                    break
                except PermissionError as e:
                    last_err = e
                    time.sleep(0.1 * (attempt + 1))
                except Exception as e:
                    last_err = e
                    try:
                        # 编码降级：记录警告
                        logging.warning("CSV文件编码异常，尝试GBK解码: %s", csv_file)
                        df = pd.read_csv(csv_file, dtype=str, encoding='gbk')
                        encoding_fallback = True
                        break
                    except PermissionError as e:
                        last_err = e
                        time.sleep(0.1 * (attempt + 1))
                    except Exception as e:
                        last_err = e
                        break
            else:
                raise last_err
            if df.empty and os.path.isfile(csv_file):
                logging.warning("_get_df: CSV 存在但读取为空: %s", csv_file)
                err_detail = f"错误详情: {last_err}" if last_err else "可能原因: 文件被其他程序占用或格式损坏"
                st.error(f"❌ CSV 文件读取失败: {os.path.basename(csv_file)}\n\n{err_detail}\n\n💡 建议: 关闭占用该文件的程序（如 Excel），然后刷新页面重试")
            # 编码降级提示
            if encoding_fallback and not df.empty:
                st.session_state[f"_encoding_fallback_{csv_file}"] = True
                st.warning(f"⚠️ 检测到CSV文件编码异常，已自动用GBK解码。如显示乱码，请用Excel重新保存为UTF-8格式")
        else:
            df = pd.DataFrame()
        # 归一化图片ID（修复 Excel 编辑导致的前导零丢失等格式问题）
        if not df.empty and '图片ID' in df.columns:
            df['图片ID'] = df['图片ID'].astype(str).str.strip()
            df = df.drop_duplicates(subset=['图片ID'], keep='last').reset_index(drop=True)
        st.session_state[cache_key] = df.copy()
        st.session_state[f"_df_lookup_{csv_file}"] = self._build_lookup(df)
        return df.copy()

    def _get_record_by_id(self, current_id):
        """按 ID 查找历史记录（O(1) 字典查找，切图不再扫全量 DataFrame）"""
        csv_file = self.get_csv_filename()
        lookup_key = f"_df_lookup_{csv_file}"
        lookup = st.session_state.get(lookup_key)
        if lookup:
            return lookup.get(str(current_id))
        # 兜底：缓存不存在时走 DataFrame 扫描
        df = self._get_df()
        if not df.empty:
            record = df[df['图片ID'] == str(current_id)]
            if not record.empty:
                return record.iloc[-1]
        return None

    def _save_df(self, df):
        """原子写入 CSV：流式写入 + 同句柄 fsync + os.replace，带重试机制"""
        csv_file = self.get_csv_filename()
        os.makedirs(os.path.dirname(csv_file), exist_ok=True)
        tmp_file = csv_file + ".tmp"
        bak_file = csv_file + ".bak"
        try:
            with open(tmp_file, 'w', encoding='utf-8-sig', newline='') as f:
                df.to_csv(f, index=False)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(csv_file):
                try:
                    os.replace(csv_file, bak_file)
                except OSError:
                    pass
            last_err = None
            for attempt in range(5):
                try:
                    os.replace(tmp_file, csv_file)
                    break
                except PermissionError as e:
                    last_err = e
                    time.sleep(0.3 * (attempt + 1))
            else:
                raise last_err
        except Exception:
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except OSError:
                pass
            raise

    @staticmethod
    def _rerun_app():
        """兼容新旧版本 Streamlit 的 app 级别 rerun"""
        try:
            st.rerun(scope="app")
        except TypeError:
            st.rerun()

    @staticmethod
    def _fsync_path(filepath):
        """跨平台 fsync：确保文件内容和目录元数据写入磁盘"""
        fd = None
        try:
            fd = os.open(filepath, os.O_RDONLY)
            os.fsync(fd)
        except Exception:
            logging.warning("fsync 失败: %s", filepath)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    # ── 标签同步 ──

    def _sync_tags_to_notes(self, group):
        """将已选标签同步到备注栏（仅在用户未手动编辑时同步）"""
        current_id = group['id']
        selected = st.session_state.selected_tags.get(current_id, [])
        tags_text = "; ".join(selected)

        notes_key = f"feedback_{current_id}"
        current_notes = st.session_state.get(notes_key, "")
        last_tags_key = f"_last_tags_{current_id}"
        last_tags = st.session_state.get(last_tags_key, "")
        manual_edit_key = f"_manual_edit_{current_id}"
        content_hash_key = f"_content_hash_{current_id}"

        # 计算当前备注内容的哈希值
        current_hash = hashlib.md5(current_notes.encode()).hexdigest()
        last_hash = st.session_state.get(content_hash_key, "")

        # 如果用户手动编辑过，或者内容哈希变化，则禁止同步
        if st.session_state.get(manual_edit_key, False) or (last_hash and current_hash != last_hash):
            st.session_state[last_tags_key] = tags_text
            st.session_state[content_hash_key] = hashlib.md5(current_notes.encode()).hexdigest()
            return

        if not current_notes.strip() or current_notes.strip() == last_tags:
            st.session_state[notes_key] = tags_text
            st.session_state[last_tags_key] = tags_text
            st.session_state[content_hash_key] = hashlib.md5(tags_text.encode()).hexdigest()
        else:
            if current_notes.strip() == last_tags.strip():
                st.session_state[notes_key] = tags_text
                st.session_state[last_tags_key] = tags_text
                st.session_state[content_hash_key] = hashlib.md5(tags_text.encode()).hexdigest()
                return

            clean_notes = current_notes
            if last_tags and clean_notes.startswith(last_tags):
                suffix = clean_notes[len(last_tags):]
                if suffix.startswith("; ") or suffix.startswith("；"):
                    suffix = suffix[2:]
                clean_notes = suffix.lstrip()

            if clean_notes == current_notes and last_tags:
                st.session_state[last_tags_key] = tags_text
                st.session_state[content_hash_key] = hashlib.md5(current_notes.encode()).hexdigest()
                return

            if tags_text and clean_notes:
                st.session_state[notes_key] = f"{tags_text}; {clean_notes}"
            elif tags_text:
                st.session_state[notes_key] = tags_text
            else:
                st.session_state[notes_key] = clean_notes

            st.session_state[last_tags_key] = tags_text
            st.session_state[content_hash_key] = hashlib.md5(st.session_state[notes_key].encode()).hexdigest()

    # ── 状态同步 ──

    def sync_state_from_history(self, current_id):
        if st.session_state.last_loaded_id == current_id: return

        found_record = self._get_record_by_id(current_id)

        if found_record is not None:
            l1_list = st.session_state.categories_config.get('L1', DEFAULT_CATEGORIES['L1'])
            st.session_state.sticky_l1 = found_record['一级'] if pd.notna(found_record['一级']) else (l1_list[0] if l1_list else "")
            st.session_state.sticky_l2 = found_record['二级'] if pd.notna(found_record['二级']) else None
            st.session_state['status_pills'] = found_record['结果'] if pd.notna(found_record['结果']) else None
            st.session_state[f"feedback_{current_id}"] = found_record.get('备注', found_record.get('错误反馈', '')) if pd.notna(found_record.get('备注', found_record.get('错误反馈', ''))) else ""

            try:
                if '标签' in found_record and pd.notna(found_record['标签']):
                    tags_str = str(found_record['标签'])
                    st.session_state.selected_tags[current_id] = [t.strip() for t in tags_str.split(';') if t.strip()]
                else:
                    st.session_state.selected_tags[current_id] = []
            except Exception:
                st.session_state.selected_tags[current_id] = []

            st.session_state.pop(f"freq_pills_{current_id}", None)
            st.session_state.pop(f"other_pills_{current_id}", None)

            # 恢复已保存的截图到 evidence_pool
            pool_key = f"evidence_pool_{current_id}"
            if pool_key not in st.session_state or not st.session_state[pool_key]:
                img_paths_str = str(found_record.get('错误截图', '')) if pd.notna(found_record.get('错误截图', '')) else ''
                if img_paths_str and img_paths_str != 'nan':
                    loaded_images = []
                    for rel_path in img_paths_str.split(';'):
                        rel_path = rel_path.strip()
                        if not rel_path:
                            continue
                        full_path = os.path.join(BASE_DIR, rel_path)
                        if os.path.exists(full_path):
                            try:
                                loaded_images.append(PILImage.open(full_path).copy())
                            except Exception:
                                pass
                    st.session_state[pool_key] = loaded_images
                else:
                    st.session_state[pool_key] = []
        else:
            st.session_state.sticky_l2 = None
            st.session_state['status_pills'] = None
            st.session_state[f"feedback_{current_id}"] = ""
            st.session_state.selected_tags[current_id] = []

        last_tags_key = f"_last_tags_{current_id}"
        tags_text = "; ".join(st.session_state.selected_tags.get(current_id, []))
        st.session_state[last_tags_key] = tags_text

        restored_notes = st.session_state.get(f"feedback_{current_id}", "").strip()
        if restored_notes and restored_notes != tags_text.strip():
            st.session_state[f"_manual_edit_{current_id}"] = True

        # 记录初始状态用于撤销检测
        loaded_status = st.session_state.get('status_pills')
        if loaded_status:
            st.session_state[f"_ee_prev_status_{current_id}"] = loaded_status

        st.session_state.last_loaded_id = current_id

    def _consume_viewer_uploads(self):
        """消费独立查看器回传的截图上传：加入当前组的 evidence_pool。
        只把截图作为证据加入，不自动改标签/状态/提交。上传队列为空则直接返回。"""
        try:
            from image_viewer import protocol as iv_protocol
        except Exception:
            return
        entries = iv_protocol.pop_uploads()
        if not entries:
            return
        from PIL import Image as _PILImage
        current_id = str(st.session_state.get('current_id') or '')
        pool_key = f"evidence_pool_{current_id}"
        added = 0
        missing = 0
        for e in entries:
            if not isinstance(e, dict) or e.get("kind") != "screenshot":
                continue
            path = e.get("path", "")
            sid = str(e.get("sample_id", ""))
            # 只把当前组（样本ID匹配）的截图并入；其他样本的暂不处理
            if sid and current_id and sid != current_id:
                continue
            if not os.path.isfile(path):
                missing += 1
                continue
            try:
                img = _PILImage.open(path).copy()
            except Exception:
                missing += 1
                continue
            if pool_key not in st.session_state:
                st.session_state[pool_key] = []
            pool = st.session_state[pool_key]
            if len(pool) >= 3:
                continue
            pool.append(img)
            added += 1
        if added or missing:
            st.session_state[f"_viewer_upload_msg"] = f"已从图片查看器加入 {added} 张截图"
            if missing:
                st.session_state[f"_viewer_upload_msg"] += f"，{missing} 张无法读取"

    def _poll_viewer_uploads(self):
        """每隔几秒轮询独立查看器回传的上传队列，自动把截图并入当前组 evidence_pool。
        用 st.fragment(run_every) 实现流式轮询，无需用户手动刷新主程序。
        只加入证据，不自动改标签/状态/提交。"""
        try:
            from image_viewer import protocol as iv_protocol
        except Exception:
            return

        @st.fragment(run_every=3.0)
        def _fragment():
            # 在此消费上传并提示
            self._consume_viewer_uploads()
            msg = st.session_state.get('_viewer_upload_msg')
            if msg:
                st.toast(f"🖼️ {msg}")
                st.session_state.pop('_viewer_upload_msg', None)

        _fragment()

    def reset_task_state(self):
        prefixes = ("preview_", "feedback_", "status_pills_", "paste_key_", "upload_btn_",
                    "_df_cache_", "_df_lookup_", "_last_tags_", "_manual_edit_", "_pending_tag_sync_",
                    "zh_", "en_", "evidence_pool_", "_sr_", "freq_pills_", "other_pills_")
        for key in list(st.session_state.keys()):
            if key.startswith(prefixes):
                del st.session_state[key]

        st.session_state._export_result = None
        st.session_state._excel_export_path = None
        st.session_state.show_export_confirm = False
        st.session_state.last_loaded_id = None
        st.session_state.focus_img_idx = 0
        st.session_state._display_img_cache = {}
        st.session_state.selected_tags = {}
        st.session_state.status_pills = None
        _load_and_rotate.cache_clear()
        st.session_state.filter_pills = "全部"
        st.session_state.needs_scroll_top = False
        st.session_state.is_scanning = False
        st.session_state._batch_completed = False
        st.session_state._completion_balloons_shown = False
        st.session_state.annotator_inclusion = {}
        st.session_state._import_done = False
        st.session_state._import_df = None
        st.session_state._import_preview = None
        st.session_state._import_summary = None
        st.session_state._import_uploader_key = None
        st.session_state.show_export_confirm = False
        st.session_state._pending_export_stats = {}
        st.session_state.annotator_map = {}
        st.session_state.pop('_sr_working', None)
        st.session_state.pop('selected_csv', None)

    # ── CSV 路径 ──

    def _get_active_csv_path(self):
        return self.get_csv_filename()

    def load_record_df(self):
        return self._get_df(self._get_active_csv_path())

    def get_record_status_map(_self, csv_path=None):
        if csv_path is None:
            csv_path = _self._get_active_csv_path()
        df = _self._get_df(csv_path)
        if df.empty or '图片ID' not in df.columns or '结果' not in df.columns:
            return {}

        latest = df.dropna(subset=['图片ID']).drop_duplicates(subset=['图片ID'], keep='last')
        return dict(zip(latest['图片ID'].astype(str), latest['结果'].fillna('')))

    def get_processed_ids(self):
        df = self.load_record_df()
        if df.empty or '图片ID' not in df.columns:
            return set()
        groups = st.session_state.data_groups
        valid_ids = [str(group['id']) for group in groups]
        return set(df[df['图片ID'].astype(str).isin(valid_ids)]['图片ID'].astype(str).tolist())

    # ── 导航 ──

    def _get_filtered_ids(self, status_map=None):
        """获取当前筛选条件下的 ID 列表（已去重）"""
        groups = st.session_state.data_groups
        all_ids = list(dict.fromkeys(g['id'] for g in groups))
        filter_val = st.session_state.get('filter_pills', '全部')
        if status_map is None:
            status_map = self.get_record_status_map(self._get_active_csv_path())
        processed_ids = {k for k, v in status_map.items() if v and k in set(all_ids)}

        if filter_val == "未检":
            return [x for x in all_ids if x not in processed_ids]
        elif filter_val == "合格":
            return [x for x in all_ids if status_map.get(x, '') in ('合格',)]
        elif filter_val == "修改后合格":
            return [x for x in all_ids if status_map.get(x, '') == '修改后合格']
        elif filter_val == "不合格":
            return [x for x in all_ids if status_map.get(x, '') == '不合格']
        elif filter_val == "待定":
            return [x for x in all_ids if status_map.get(x, '') == '待定']
        else:
            return all_ids

    def get_next_group_id(self, current_id):
        filtered_ids = self._get_filtered_ids()
        if not filtered_ids:
            return None
        if current_id not in filtered_ids:
            return filtered_ids[0]

        current_index = filtered_ids.index(current_id)
        if current_index < len(filtered_ids) - 1:
            return filtered_ids[current_index + 1]

        processed_ids = self.get_processed_ids()
        for group_id in filtered_ids:
            if group_id not in processed_ids:
                return group_id
        return current_id

    def go_to_previous_group(self):
        now = time.time()
        last = st.session_state.get('_last_nav_time', 0)
        if now - last < 0.3:
            return
        st.session_state._last_nav_time = now
        filtered_ids = self._get_filtered_ids()
        if st.session_state.current_id in filtered_ids:
            curr_idx = filtered_ids.index(st.session_state.current_id)
            if curr_idx > 0:
                new_id = filtered_ids[curr_idx - 1]
                st.session_state.current_id = new_id
                st.session_state.focus_img_idx = 0
                st.session_state.needs_scroll_top = True
                all_groups = st.session_state.get('data_groups', [])
                id_to_group = {g['id']: g for g in all_groups}
                preload_groups = [id_to_group[gid] for gid in filtered_ids if gid in id_to_group]
                preload_next_images(curr_idx - 1, preload_groups)

    def go_to_next_group(self):
        now = time.time()
        last = st.session_state.get('_last_nav_time', 0)
        if now - last < 0.3:
            return
        st.session_state._last_nav_time = now
        filtered_ids = self._get_filtered_ids()
        if st.session_state.current_id in filtered_ids:
            curr_idx = filtered_ids.index(st.session_state.current_id)
            if curr_idx < len(filtered_ids) - 1:
                new_id = filtered_ids[curr_idx + 1]
                st.session_state.current_id = new_id
                st.session_state.focus_img_idx = 0
                st.session_state.needs_scroll_top = True
                all_groups = st.session_state.get('data_groups', [])
                id_to_group = {g['id']: g for g in all_groups}
                preload_groups = [id_to_group[gid] for gid in filtered_ids if gid in id_to_group]
                preload_next_images(curr_idx + 1, preload_groups)

    # ── 输出路径 ──

    def get_output_prefix(self):
        """获取输出文件名前缀：{标签}_{操作员}_{日期}"""
        task_type = st.session_state.get('task_type', '新标')
        operator = st.session_state.get('operator_name', '').strip()
        date_str = datetime.datetime.now().strftime("%Y%m%d")
        return f"{task_type}_{operator}_{date_str}"

    def get_evidence_folder_name(self):
        """获取截图证据文件夹名称：_00_Evidence_{标签}"""
        task_type = st.session_state.get('task_type', '新标')
        return f"_00_Evidence_{task_type}"

    def _scan_existing_csvs(self):
        """扫描当前操作员目录下所有历史验收记录 CSV，按日期倒序返回路径列表"""
        raw_path = st.session_state.get('root_path', '') or BASE_DIR
        operator = st.session_state.get('operator_name', '').strip()
        if operator:
            csv_dir = os.path.join(raw_path, "_质检记录", operator)
        else:
            csv_dir = os.path.join(raw_path, "_质检记录")
        if not os.path.isdir(csv_dir):
            return []
        try:
            files = [f for f in os.listdir(csv_dir) if f.endswith("_验收记录.csv")]
            files.sort(reverse=True)
            return [os.path.join(csv_dir, f) for f in files]
        except OSError:
            return []

    def get_csv_filename(self):
        """确定当前生效的验收记录 CSV 路径。

        规则：
        1. 用户显式选中的 CSV（selected_csv）优先；
        2. 否则取当天文件 {任务}_{操作员}_{日期}_验收记录.csv；
        3. 若当天文件不存在（如跨天继续昨天的质检），自动回退到同任务、同操作员
           「最近一份」历史 CSV，避免「前一天做的记录第二天加载不进去」。
        """
        selected = st.session_state.get('selected_csv')
        if selected and os.path.isfile(selected):
            return selected
        raw_path = st.session_state.get('root_path', '') or BASE_DIR
        operator = st.session_state.get('operator_name', '').strip()
        prefix = self.get_output_prefix()
        if operator:
            csv_dir = os.path.join(raw_path, "_质检记录", operator)
        else:
            csv_dir = os.path.join(raw_path, "_质检记录")
        today_file = os.path.join(csv_dir, f"{prefix}_验收记录.csv")
        if os.path.isfile(today_file):
            return today_file

        # 当天无记录文件 → 回退到最近一份同任务类型的历史 CSV
        fallback = self._get_latest_csv_for_task(csv_dir)
        return fallback if fallback else today_file

    def _get_latest_csv_for_task(self, csv_dir):
        """在 csv_dir 中查找与当前任务类型、操作员匹配的最近一份历史 CSV。"""
        try:
            task_type = st.session_state.get('task_type', '新标')
            operator = st.session_state.get('operator_name', '').strip()
            prefix_head = task_type
            if operator:
                prefix_head = f"{task_type}_{operator}_"
            if not os.path.isdir(csv_dir):
                return None
            files = [f for f in os.listdir(csv_dir)
                     if f.endswith("_验收记录.csv") and f.startswith(prefix_head)]
            if not files:
                return None
            files.sort(reverse=True)  # 文件名含日期，倒序即最近优先
            return os.path.join(csv_dir, files[0])
        except OSError:
            return None

    # ── 保存 ──

    def save_to_disk(self, group, l1, l2, status, content_zh, content_en, feedback, image_list):
        csv_file = self.get_csv_filename()
        os.makedirs(os.path.dirname(csv_file), exist_ok=True)

        saved_img_paths = []
        failed_screenshots = []
        if image_list:
            evidence_folder = self.get_evidence_folder_name()
            evidence_dir = os.path.join(BASE_DIR, evidence_folder)
            if not os.path.exists(evidence_dir): os.makedirs(evidence_dir)

            timestamp = int(time.time())
            for i, img_obj in enumerate(image_list):
                filename = f"{group['id']}_{timestamp}_{i}.png"
                save_path = os.path.join(evidence_dir, filename)
                try:
                    img_obj.save(save_path, "PNG")
                    rel_path = os.path.join(evidence_folder, filename)
                    saved_img_paths.append(rel_path)
                except Exception as e:
                    failed_screenshots.append(i + 1)
                    logging.warning("截图 %d 保存失败: %s", i + 1, e)

        img_paths_str = ";".join(saved_img_paths) if saved_img_paths else ""

        current_tags = st.session_state.selected_tags.get(str(group['id']), [])
        tags_str = "; ".join(current_tags) if current_tags else ""

        record = {
            "姓名": group.get('user_name', '未知'),
            "图片ID": str(group['id']),
            "一级": l1 if l1 else "",
            "二级": l2 if l2 else "",
            "结果": status,
            "备注": feedback if feedback else "",
            "标签": tags_str,
            "错误截图": img_paths_str,
            "质检时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "路径": group['root']
        }

        try:
            target_id = str(group['id']).strip()
            if os.path.isfile(csv_file):
                df = pd.read_csv(csv_file, dtype=str, encoding='utf-8-sig')
                if '图片ID' in df.columns:
                    df['图片ID'] = df['图片ID'].astype(str).str.strip()
                mask = df['图片ID'] == target_id
                if mask.any():
                    for key, value in record.items(): df.loc[mask, key] = str(value)
                    dup_mask = (df['图片ID'] == target_id)
                    if dup_mask.sum() > 1:
                        keep_idx = df[dup_mask].index[-1]
                        drop_idx = df[dup_mask].index.difference([keep_idx])
                        df = df.drop(drop_idx)
                else:
                    new_df = pd.DataFrame([record], dtype=str)
                    df = pd.concat([df, new_df], ignore_index=True)
                df = df.reset_index(drop=True)
            else:
                df = pd.DataFrame([record], dtype=str)
            self._save_df(df)
        except PermissionError:
            return False, f"❌ CSV被占用，请关闭: {csv_file}"
        except Exception as e:
            return False, f"CSV写入错误: {e}"

        if group['txt_zh'] and content_zh is not None:
            try:
                zh_path = os.path.join(group['root'], group['txt_zh'])
                zh_tmp = zh_path + ".tmp"
                with open(zh_tmp, 'w', encoding='utf-8') as f: f.write(content_zh)
                os.replace(zh_tmp, zh_path)
            except Exception as e:
                st.warning(f"⚠️ 中文指令保存失败: {e}")
        if group['txt_en'] and content_en is not None:
            try:
                en_path = os.path.join(group['root'], group['txt_en'])
                en_tmp = en_path + ".tmp"
                with open(en_tmp, 'w', encoding='utf-8') as f: f.write(content_en)
                os.replace(en_tmp, en_path)
            except Exception as e:
                st.warning(f"⚠️ 英文指令保存失败: {e}")

        cache_key = f"_df_cache_{csv_file}"
        st.session_state.pop(cache_key, None)
        lookup_key = f"_df_lookup_{csv_file}"
        st.session_state.pop(lookup_key, None)

        if failed_screenshots:
            return True, f"⚠️ 记录已保存，但第 {', '.join(map(str, failed_screenshots)) } 张截图保存失败"
        return True, ""

    # ── 抽检 ──

    def start_sampling_acceptance(self, all_folders):
        try:
            ratio = st.session_state.sampling_acceptance_ratio
            n = max(1, int(len(all_folders) * ratio / 100))
            sampled = random.sample(all_folders, n)

            st.session_state._saved_data_groups = st.session_state.data_groups.copy()
            st.session_state._saved_current_id = st.session_state.current_id

            st.session_state.data_groups = sampled
            st.session_state.current_id = sampled[0]['id']
            st.session_state.sampling_acceptance_mode = True
            on_sampling_start()
            st.rerun()
        except Exception as e:
            st.error(f"❌ 抽检验收失败: {e}")

    def _get_missing_ids(self):
        all_loaded_ids = [str(g['id']) for g in st.session_state.data_groups]
        csv_file = self.get_csv_filename()
        return _export_get_missing_ids(all_loaded_ids, csv_file)

    # ── 扫描处理（从 run() 提取） ──

    def _handle_scanning(self):
        """处理扫描状态：扫描文件夹、设置标注员确认等"""
        scan_path = st.session_state.get('_scan_path', '')
        self.reset_task_state()
        st.session_state.root_path = scan_path
        self._save_settings()
        st.session_state.scan_cache_buster += 1
        scanned = scan_files_from_disk(scan_path, st.session_state.scan_cache_buster,
                                       rules_json=json.dumps(st.session_state.scan_rules, sort_keys=True, ensure_ascii=False))
        st.session_state.sampling_acceptance_mode = False
        st.session_state._saved_data_groups = None
        st.session_state._saved_current_id = None
        if scanned:
            if st.session_state.annotator_confirm_enabled:
                st.session_state.pending_groups = scanned
                st.session_state.data_groups = []
                st.session_state.current_id = None
                unique_names = sorted(set(g['user_name'] for g in scanned))
                st.session_state.annotator_map = {n: n for n in unique_names}
                st.session_state.annotator_inclusion = {n: True for n in unique_names}
                st.session_state.confirm_pending = True
                on_data_loaded()
            else:
                st.session_state.data_groups = scanned
                st.session_state.current_id = scanned[0]['id']
                st.session_state.confirm_pending = False
                st.session_state.pending_groups = []
                on_data_loaded()
        else:
            st.session_state.data_groups = []
            st.session_state.current_id = None
            st.session_state.confirm_pending = False
            st.session_state.pending_groups = []
        st.session_state.is_scanning = False
        st.session_state._batch_completed = False
        st.session_state._completion_balloons_shown = False
        st.rerun()
