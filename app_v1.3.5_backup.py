import streamlit as st
import streamlit.components.v1 as components
import os
import sys
import pandas as pd
import datetime
import shutil
import re
import platform
import subprocess
import time
import json
import random
import logging
from PIL import Image
from io import BytesIO

from utils import (
    BASE_DIR, IMAGE_EXTENSIONS, PRELOAD_AHEAD, MAX_DISPLAY_PX, DISPLAY_JPEG_QUALITY,
    DEFAULT_CATEGORIES, CONFIG_FILENAME, EVIDENCE_FOLDER_NAME,
    is_supported_image, find_image_file, _load_and_rotate, auto_rotate_image,
    resize_image_for_display, get_config_path, load_categories_config, save_categories_config,
    read_txt, extract_user_name,
    DEFAULT_SCAN_RULES, load_scan_rules, save_scan_rules
)

from export_utils import get_missing_ids as _export_get_missing_ids, collect_reject_folders, verify_exported_data as _export_verify

# === 📦 引用 ===
try:
    from streamlit_paste_button import paste_image_button
    HAS_PASTE_LIB = True
except ImportError:
    HAS_PASTE_LIB = False

# ==========================================
# ⚙️ 业务配置 (V47 - 新项目)
# ==========================================
STATUS_OPTIONS = ["合格", "不合格", "修改后合格", "待定"]

# 日志配置：错误持久化到文件
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "app_error.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

@st.cache_data(show_spinner=False)
def scan_files_from_disk(path, cache_buster=0, rules_json=None):
    """嵌套目录扫描：每个N位数字文件夹为一个图片组（可配置）。
    支持: 多图片槽位、多文本槽位，大小写不敏感。
    支持批次文件夹：batch_YYYYMMDD_NNN_keyword 格式的中间目录会自动识别为批次。
    cache_buster: 传入递增计数器以强制刷新缓存。
    rules_json: JSON 字符串，包含扫描规则配置（无 _ 前缀，作为缓存键）。"""
    groups = []
    if not os.path.exists(path):
        return []

    rules = json.loads(rules_json) if rules_json else DEFAULT_SCAN_RULES
    digit_len = rules.get("folder_digit_length", 8)
    image_slots = rules.get("image_slots", DEFAULT_SCAN_RULES["image_slots"])
    text_slots = rules.get("text_slots", DEFAULT_SCAN_RULES["text_slots"])

    batch_pattern = re.compile(r'^batch_(\d{8})_(\d{3})_(.+)$')
    current_batch = None

    for root, dirs, files in os.walk(path):
        if EVIDENCE_FOLDER_NAME in root or "_00_Evidence_" in root:
            continue
        if os.path.basename(root) == "不合格":
            continue

        folder_name = os.path.basename(root)

        # 检测批次文件夹：batch_YYYYMMDD_NNN_keyword
        batch_match = batch_pattern.match(folder_name)
        if batch_match:
            batch_date, seq_str, keyword = batch_match.groups()
            current_batch = {
                "batch_name": folder_name,
                "batch_date": batch_date,
                "daily_seq": int(seq_str),
                "keyword": keyword,
                "batch_path": root,
            }
            # 读取已有的批次元数据文件（如果有）
            meta_path = os.path.join(root, "batch_meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, 'r', encoding='utf-8') as mf:
                        current_batch["meta"] = json.load(mf)
                except Exception:
                    pass
            continue

        if not folder_name.isdigit() or len(folder_name) != digit_len:
            continue

        # 图片匹配：每个 slot 按 suffix 顺序尝试匹配
        image_results = []
        for slot in image_slots:
            found = None
            for suffix in slot.get("stem_suffixes", [""]):
                stem = f"{folder_name}{suffix}"
                found = find_image_file(files, stem)
                if found:
                    break
            image_results.append(found)

        has_any_image = any(r is not None for r in image_results)

        # TXT 匹配：对每个 text_slot 按 suffix 顺序尝试
        txt_results = []
        for slot in text_slots:
            found = None
            for suffix in slot.get("suffixes", []):
                suffix_lower = suffix.lower()
                for f in files:
                    if f.lower().endswith('.txt'):
                        name_lower = f[:-4].lower()
                        if name_lower.endswith(suffix_lower):
                            found = f
                            break
                if found:
                    break
            txt_results.append(found)

        if not has_any_image:
            continue

        # 提取标注员名：从路径段中智能识别中文姓名
        try:
            rel_path = os.path.relpath(root, path)
            path_parts = rel_path.split(os.sep)
            user_name = extract_user_name(path_parts, digit_length=digit_len)
        except Exception:
            user_name = "未知"

        group = {
            "root": root,
            "original": next((r for r in image_results if r is not None), None),
            "images": [r for r in image_results if r is not None],
            "txt_zh": txt_results[0] if len(txt_results) > 0 else None,
            "txt_en": txt_results[1] if len(txt_results) > 1 else None,
            "id": folder_name,
            "originals": [r for r in image_results if r is not None],
            "user_name": user_name
        }
        if current_batch:
            group["batch"] = current_batch
        groups.append(group)

    return sorted(groups, key=lambda x: x['root'])


_BATCH_PATTERN = re.compile(r'^batch_(\d{8})_(\d{3})_(.+)$')


def _parse_batch_name(batch_name):
    """解析批次文件夹名，返回 (date, seq, keyword) 或 None。"""
    m = _BATCH_PATTERN.match(batch_name)
    if m:
        return m.group(1), int(m.group(2)), m.group(3)
    return None


def ensure_batch_metadata(batch_dir, batch_name, keyword="", template_id=None):
    """确保批次目录下存在 batch_meta.json，不存在则创建。
    返回批次元数据字典。"""
    meta_path = os.path.join(batch_dir, "batch_meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    parsed = _parse_batch_name(batch_name)
    if not parsed:
        return {}
    meta = {
        "batch_name": batch_name,
        "batch_date": parsed[0],
        "daily_seq": parsed[1],
        "keyword": keyword or parsed[2],
        "template_id": template_id,
        "image_count": len([
            f for f in os.listdir(batch_dir)
            if os.path.isdir(os.path.join(batch_dir, f))
        ]),
        "status": "completed",
        "created_at": datetime.datetime.now().isoformat(),
    }
    try:
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning("批次元数据写入失败: %s — %s", meta_path, e)
    return meta


@st.cache_data(show_spinner=False)
def get_image_resolution(img_path):
    """获取图片分辨率，返回格式如 "1920×1080" """
    try:
        img = Image.open(img_path)
        w, h = img.size
        return f"{w}×{h}"
    except Exception:
        logging.warning("无法获取图片分辨率: %s", img_path)
        return "未知"


def get_display_image_bytes(img_path):
    """获取显示用图片的 bytes（缩放后的 JPEG），带 session_state 缓存"""
    cache = st.session_state.get("_display_img_cache")
    if cache is None:
        cache = {}
        st.session_state._display_img_cache = cache
    mtime = os.path.getmtime(img_path)
    cache_key = f"{img_path}_{mtime}"
    if cache_key in cache:
        return cache[cache_key]
    img = _load_and_rotate(img_path, mtime)
    if img is None:
        placeholder = Image.new("RGB", (400, 300), color=(80, 80, 80))
        buf = BytesIO()
        placeholder.save(buf, format="JPEG", quality=60)
        return buf.getvalue(), "加载失败"
    img, res_str = resize_image_for_display(img)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=DISPLAY_JPEG_QUALITY)
    img_bytes = buf.getvalue()
    entry = (img_bytes, res_str)
    cache[cache_key] = entry
    # 限制缓存：最多 50 条且总大小不超过 100MB
    MAX_CACHE_ENTRIES = 50
    MAX_CACHE_BYTES = 100 * 1024 * 1024  # 100MB
    while len(cache) > MAX_CACHE_ENTRIES:
        oldest = next(iter(cache))
        del cache[oldest]
    # 估算缓存总大小
    total_bytes = sum(len(v[0]) for v in cache.values() if isinstance(v, tuple))
    while total_bytes > MAX_CACHE_BYTES and len(cache) > 1:
        oldest = next(iter(cache))
        total_bytes -= len(cache[oldest][0])
        del cache[oldest]
    return entry

def preload_next_images(current_idx, all_groups):
    """滑动窗口预加载：预取当前索引之后 N 张图的原图至 LRU 缓存 + 显示缓存"""
    if not all_groups or current_idx < 0:
        return
    preload_count = min(PRELOAD_AHEAD, len(all_groups) - 1 - current_idx)
    for i in range(1, preload_count + 1):
        target_idx = current_idx + i
        if target_idx >= len(all_groups):
            break
        group = all_groups[target_idx]
        for img_path in group.get("images", []):
            if img_path and os.path.exists(img_path):
                try:
                    _load_and_rotate(img_path, os.path.getmtime(img_path))
                    get_display_image_bytes(img_path)
                except Exception as e:
                    logging.debug("预加载失败 %s: %s", img_path, e)

@st.cache_data(show_spinner=False)
def load_qa_report(filepath):
    if os.path.exists(filepath):
        try:
            df = pd.read_csv(filepath, dtype=str, encoding='utf-8-sig')
            return df
        except Exception as e:
            return pd.DataFrame()
    return pd.DataFrame()

class AcceptanceApp:
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

        if 'qa_df' not in st.session_state: st.session_state.qa_df = pd.DataFrame()
        if 'qa_source' not in st.session_state: st.session_state.qa_source = "未加载"

        if 'error_screenshots' not in st.session_state: st.session_state.error_screenshots = {}
        if 'uploaded_file_tokens' not in st.session_state: st.session_state.uploaded_file_tokens = {}
        if 'generated_excel_path' not in st.session_state: st.session_state.generated_excel_path = None
        if 'is_navigating' not in st.session_state: st.session_state.is_navigating = False
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

    def _load_tags_from_disk(self):
        """从本地 JSON 加载标签库"""
        tags_file = os.path.join(BASE_DIR, "config", "tags.json")
        if os.path.exists(tags_file):
            try:
                with open(tags_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        return json.loads(content)
            except Exception:
                logging.warning("标签库 JSON 解析失败，将使用空标签库")
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
            except Exception:
                logging.warning("设置文件 JSON 解析失败，将使用默认设置")
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
            }
            with open(settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.warning("设置保存失败: %s", settings_file)
            st.warning(f"⚠️ 设置保存失败：{e}")

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
                        df = pd.read_csv(csv_file, dtype=str, encoding='gbk')
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
            # 用 with 打开文件，df.to_csv 写入文件对象，fsync 用同句柄
            # 避免 pd.to_csv 关闭文件后立即 os.open 重新打开的竞争（Windows NTFS 句柄释放延迟 / 杀毒扫描持锁）
            with open(tmp_file, 'w', encoding='utf-8-sig', newline='') as f:
                df.to_csv(f, index=False)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(csv_file):
                try:
                    os.replace(csv_file, bak_file)
                except OSError:
                    pass
            # 带重试的 replace（Windows 上文件同步盘/杀毒软件可能短暂持锁）
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

        # 如果用户手动编辑过备注，禁止自动同步
        if st.session_state.get(manual_edit_key, False):
            st.session_state[last_tags_key] = tags_text
            return

        if not current_notes.strip() or current_notes.strip() == last_tags:
            st.session_state[notes_key] = tags_text
            st.session_state[last_tags_key] = tags_text
        else:
            # 检查备注是否完全等于旧标签（允许前后空格）
            if current_notes.strip() == last_tags.strip():
                st.session_state[notes_key] = tags_text
                st.session_state[last_tags_key] = tags_text
                return

            # 尝试移除旧标签前缀
            clean_notes = current_notes
            if last_tags and clean_notes.startswith(last_tags):
                suffix = clean_notes[len(last_tags):]
                if suffix.startswith("; ") or suffix.startswith("；"):
                    suffix = suffix[2:]
                clean_notes = suffix.lstrip()

            # 如果清理后内容为空或与原备注相同，说明无法可靠分离，保留原备注
            if clean_notes == current_notes and last_tags:
                # 无法确定哪些是标签哪些是用户输入，保留原备注
                st.session_state[last_tags_key] = tags_text
                return

            if tags_text and clean_notes:
                st.session_state[notes_key] = f"{tags_text}; {clean_notes}"
            elif tags_text:
                st.session_state[notes_key] = tags_text
            else:
                st.session_state[notes_key] = clean_notes

            st.session_state[last_tags_key] = tags_text

    def sync_state_from_history(self, current_id):
        if st.session_state.last_loaded_id == current_id: return

        found_record = self._get_record_by_id(current_id)

        if found_record is not None:
            l1_list = st.session_state.categories_config.get('L1', DEFAULT_CATEGORIES['L1'])
            st.session_state.sticky_l1 = found_record['一级'] if pd.notna(found_record['一级']) else (l1_list[0] if l1_list else "")
            st.session_state.sticky_l2 = found_record['二级'] if pd.notna(found_record['二级']) else None
            st.session_state['status_pills'] = found_record['结果'] if pd.notna(found_record['结果']) else None
            st.session_state[f"feedback_{current_id}"] = found_record.get('备注', found_record.get('错误反馈', '')) if pd.notna(found_record.get('备注', found_record.get('错误反馈', ''))) else ""

            # 恢复标签选择
            try:
                if '标签' in found_record and pd.notna(found_record['标签']):
                    tags_str = str(found_record['标签'])
                    st.session_state.selected_tags[current_id] = [t.strip() for t in tags_str.split(';') if t.strip()]
                else:
                    st.session_state.selected_tags[current_id] = []
            except Exception:
                st.session_state.selected_tags[current_id] = []

            # 清除 pills 缓存，确保 pills 显示与恢复的标签一致
            st.session_state.pop(f"freq_pills_{current_id}", None)
            st.session_state.pop(f"other_pills_{current_id}", None)
        else:
            st.session_state.sticky_l2 = None
            st.session_state['status_pills'] = None
            st.session_state[f"feedback_{current_id}"] = ""
            st.session_state.selected_tags[current_id] = []

        # 恢复 _last_tags_ 标记（防止 _sync_tags_to_notes 中标签重复）
        last_tags_key = f"_last_tags_{current_id}"
        tags_text = "; ".join(st.session_state.selected_tags.get(current_id, []))
        st.session_state[last_tags_key] = tags_text

        # 如果备注非空且与标签不同，标记为手动编辑（防止标签同步覆盖手写备注）
        restored_notes = st.session_state.get(f"feedback_{current_id}", "").strip()
        if restored_notes and restored_notes != tags_text.strip():
            st.session_state[f"_manual_edit_{current_id}"] = True

        st.session_state.last_loaded_id = current_id

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
        st.session_state.error_screenshots = {}
        st.session_state.uploaded_file_tokens = {}
        st.session_state.generated_excel_path = None
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
        st.session_state.show_export_confirm = False
        st.session_state._pending_export_stats = {}
        st.session_state.annotator_map = {}
        st.session_state.pop('_sr_working', None)

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

    def _get_filtered_ids(self, status_map=None):
        """获取当前筛选条件下的 ID 列表（已去重）"""
        groups = st.session_state.data_groups
        # 使用 dict.fromkeys 去重但保持顺序
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

    def render_cold_start_animation(self):
        """极简现代风载入动画：呼吸之眼 + 脉冲环 + 淡入标题"""
        version = os.path.basename(BASE_DIR)
        is_scanning = st.session_state.get('is_scanning', False)
        anim_html = f"""
        <style>
        @keyframes fade-up {{
            0% {{ opacity: 0; transform: translateY(12px); }}
            100% {{ opacity: 1; transform: translateY(0); }}
        }}
        @keyframes breathe {{
            0%, 100% {{ transform: scale(1); }}
            50% {{ transform: scale(1.08); }}
        }}
        @keyframes pulse-ring {{
            0% {{ transform: scale(0.5); opacity: 0.6; }}
            100% {{ transform: scale(2.5); opacity: 0; }}
        }}
        @keyframes ring-appear {{
            0% {{ transform: scale(0); opacity: 0; }}
            100% {{ transform: scale(1); opacity: 1; }}
        }}
        @keyframes spin {{
            0% {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}
        @keyframes spinner-fade {{
            0% {{ opacity: 0; transform: scale(0.5); }}
            100% {{ opacity: 1; transform: scale(1); }}
        }}
        .coldstart-wrapper {{
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            height: 560px; font-family: "Source Sans Pro", -apple-system, "Segoe UI", sans-serif;
            position: relative;
        }}
        .eye-container {{
            position: relative; width: 180px; height: 180px; margin-bottom: 48px;
            opacity: 0; animation: fade-up 0.8s 0.2s forwards;
        }}
        .eye-ring {{
            position: absolute; top: 0; left: 0; width: 180px; height: 180px;
            border: 2px solid #6366f1; border-radius: 50%;
            opacity: 0; animation: ring-appear 0.6s 0.3s forwards;
            box-shadow: 0 0 15px rgba(99, 102, 241, 0.2);
        }}
        .eye-pulse {{
            position: absolute; top: 0; left: 0; width: 180px; height: 180px;
            border: 1px solid #6366f1; border-radius: 50%;
            animation: pulse-ring 2.5s ease-out 1.2s infinite;
        }}
        .eye-pulse:nth-child(3) {{
            animation-delay: 2.2s;
        }}
        .eye-pupil {{
            position: absolute; top: 50%; left: 50%; width: 54px; height: 54px;
            margin-left: -27px; margin-top: -27px; background: #6366f1;
            border-radius: 50%; animation: breathe 3s ease-in-out infinite;
            box-shadow: 0 0 25px rgba(99, 102, 241, 0.5), 0 0 50px rgba(99, 102, 241, 0.2);
        }}
        .spinner-container {{
            position: relative; width: 120px; height: 120px; margin-bottom: 48px;
            opacity: 0; animation: spinner-fade 0.5s 0.2s forwards;
        }}
        .spinner-ring {{
            position: absolute; top: 0; left: 0; width: 120px; height: 120px;
            border: 3px solid transparent;
            border-top-color: #6366f1;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }}
        .spinner-ring:nth-child(2) {{
            width: 90px; height: 90px;
            top: 15px; left: 15px;
            border-top-color: #8b5cf6;
            animation-duration: 0.8s;
            animation-direction: reverse;
        }}
        .spinner-ring:nth-child(3) {{
            width: 60px; height: 60px;
            top: 30px; left: 30px;
            border-top-color: #a78bfa;
            animation-duration: 0.6s;
        }}
        .coldstart-title {{
            font-size: 40px; font-weight: 400; color: #1a1a2e; letter-spacing: 3px;
            opacity: 0; animation: fade-up 0.8s 0.8s forwards; margin-bottom: 8px;
        }}
        .coldstart-subtitle {{
            font-size: 16px; color: #666; letter-spacing: 4px;
            opacity: 0; animation: fade-up 0.8s 1.4s forwards; margin-bottom: 28px;
        }}
        .coldstart-loading-text {{
            font-size: 15px; color: #6366f1; letter-spacing: 2px;
            opacity: 0; animation: fade-up 0.5s 0.6s forwards; margin-bottom: 20px;
        }}
        .coldstart-version {{
            font-size: 12px; color: #bbb; letter-spacing: 2px;
            opacity: 0; animation: fade-up 0.6s 2s forwards;
            position: absolute;
            bottom: 24px;
        }}
        </style>
        <div class="coldstart-wrapper">
        """
        if is_scanning:
            anim_html += """
            <div class="spinner-container">
                <div class="spinner-ring"></div>
                <div class="spinner-ring"></div>
                <div class="spinner-ring"></div>
            </div>
            <div class="coldstart-loading-text">正在扫描文件夹...</div>
            """
        else:
            anim_html += f"""
            <div class="eye-container">
                <div class="eye-ring"></div>
                <div class="eye-pulse"></div>
                <div class="eye-pulse"></div>
                <div class="eye-pupil"></div>
            </div>
            <div class="coldstart-title">审视之眼pro V{version}</div>
            <div class="coldstart-subtitle">视觉质检</div>
            """
        anim_html += f"""
            <div class="coldstart-version">v{version}</div>
        </div>
        """
        components.html(anim_html, height=600)

    def render_confirm_panel(self):
        """渲染标注员确认面板（B区）"""
        st.info("📋 **检测到负极目录下有多个子标注员项，请确认它们的名称**")

        pending = st.session_state.pending_groups
        from collections import OrderedDict
        annotator_info = OrderedDict()
        for g in pending:
            name = g['user_name']
            if name not in annotator_info:
                annotator_info[name] = {'count': 0, 'sample_path': g['root']}
            annotator_info[name]['count'] += 1

        total_pending = len(pending)
        edited_map = {}

        for name, info in annotator_info.items():
            is_unknown = (name == "未知")
            icon = "⚠️ " if is_unknown else ""

            with st.container(border=True):
                c_include, c1, c2, c3 = st.columns([0.5, 2, 3.5, 1])
                with c_include:
                    default_val = st.session_state.annotator_inclusion.get(name, True)
                    included = st.checkbox("纳入", value=default_val, key=f"include_{name}")
                    st.session_state.annotator_inclusion[name] = included
                with c1:
                    placeholder = "请手动输入" if is_unknown else name
                    edited = st.text_input(
                        f"{icon}{name}",
                        value=placeholder if is_unknown else name,
                        key=f"confirm_name_{name}",
                        label_visibility="visible"
                    )
                    edited_map[name] = edited
                with c2:
                    st.caption(f"📂 ...{os.sep.join(info['sample_path'].split(os.sep)[-3:])}")
                with c3:
                    st.metric("条数", info['count'])

        st.write("")
        selected_total = sum(
            info['count'] for name, info in annotator_info.items()
            if st.session_state.annotator_inclusion.get(name, True)
        )
        c_btn1, c_btn2 = st.columns(2)
        with c_btn1:
            if st.button("❌ 跳过确认，直接开始", use_container_width=True):
                st.session_state.data_groups = st.session_state.pending_groups
                if st.session_state.data_groups:
                    st.session_state.current_id = st.session_state.data_groups[0]['id']
                st.session_state.confirm_pending = False
                st.session_state.pending_groups = []
                st.session_state._batch_completed = False
                st.session_state._completion_balloons_shown = False
                st.rerun()
        with c_btn2:
            if st.button(f"✅ 确认并开始验收（共 {selected_total} 条）", type="primary", use_container_width=True):
                has_empty_unknown = False
                for name, edited in edited_map.items():
                    # 跳过未纳入验收的标注员
                    if not st.session_state.annotator_inclusion.get(name, True):
                        continue
                    if name == "未知" and (not edited or edited.strip() == "" or edited == "请手动输入"):
                        has_empty_unknown = True
                        break

                if has_empty_unknown:
                    st.error('⚠️ 检测到【未知】标注员未填写姓名，请为所有【未知】标注员输入有效名称！')
                else:
                    with st.spinner("正在启动验收，数据加载中..."):
                        st.session_state.annotator_map = edited_map
                        # 1. 先用原始名过滤（inclusion key 是原始名）
                        included_groups = [
                            g for g in st.session_state.pending_groups
                            if st.session_state.annotator_inclusion.get(g['user_name'], True)
                        ]
                        # 2. 再对过滤后的数据改名
                        for g in included_groups:
                            old_name = g['user_name']
                            if old_name in edited_map:
                                g['user_name'] = edited_map[old_name]
                        st.session_state.data_groups = included_groups
                        if st.session_state.data_groups:
                            st.session_state.current_id = st.session_state.data_groups[0]['id']
                        st.session_state.confirm_pending = False
                        st.session_state.pending_groups = []
                        st.session_state._batch_completed = False
                        st.session_state._completion_balloons_shown = False
                    st.rerun()

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
        js_code = """
        <script>
        (function() {
            const doc = window.parent.document;

            function isViewBtn(b) {
                const t = b.innerText;
                return t.includes('🔍') || t.includes('🔴 原图') || t.startsWith('图 ');
            }

            // 标记快捷键目标按钮（按文本匹配 → 写入 data-hotkey 属性）
            function tagButtons() {
                const btns = Array.from(doc.getElementsByTagName('button'));
                const rules = [
                    ['prev',    b => b.innerText.includes('⬅️') || b.innerText.includes('上一条')],
                    ['next',    b => b.innerText.includes('下一条') || b.innerText.includes('➡️')],
                    ['submit',  b => b.innerText.includes('提交并下一条')],
                    ['open',    b => b.innerText.includes('🖼️')],
                ];
                for (const [key, test] of rules) {
                    for (const b of btns) {
                        if (!b.dataset.hotkey && test(b)) { b.dataset.hotkey = key; break; }
                    }
                }
                // 视图按钮：按出现顺序匹配，排除已标记的
                const viewBtns = btns.filter(b => !b.dataset.hotkey && isViewBtn(b));
                const viewKeys = ['view-1', 'view-2', 'view-3', 'view-4'];
                for (let i = 0; i < Math.min(viewBtns.length, viewKeys.length); i++) {
                    viewBtns[i].dataset.hotkey = viewKeys[i];
                }
            }

            function handleHotkeys(e) {
                const activeTag = doc.activeElement.tagName;
                if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return;

                tagButtons(); // 确保最新按钮已标记

                // 导航 & 提交
                const simpleMap = { 'ArrowLeft': 'prev', 'ArrowRight': 'next', ' ': 'submit' };
                if (simpleMap[e.key]) {
                    e.preventDefault();
                    const btn = doc.querySelector('[data-hotkey="' + simpleMap[e.key] + '"]');
                    if (btn && !btn.disabled) btn.click();
                    return;
                }

                // 视图切换 1-4
                const keyMap = {'1': 'view-1', '2': 'view-2', '3': 'view-3', '4': 'view-4'};
                if (keyMap[e.key]) {
                    const btn = doc.querySelector('[data-hotkey="' + keyMap[e.key] + '"]');
                    if (btn && !btn.disabled) btn.click();
                    return;
                }

                // 系统查看器
                if (e.key === '`' || e.key === '~') {
                    const btn = doc.querySelector('[data-hotkey="open"]');
                    if (btn && !btn.disabled) btn.click();
                }
            }
            doc.removeEventListener('keydown', window.parent.myHotkeysHandler);
            window.parent.myHotkeysHandler = handleHotkeys;
            doc.addEventListener('keydown', window.parent.myHotkeysHandler);
        })();
        </script>
        """
        components.html(js_code, height=0, width=0)

    def open_in_system(self, path):
        if not os.path.exists(path):
            st.error(f"路径不存在: {path}"); return
        try:
            if platform.system() == "Windows": os.startfile(path)
            elif platform.system() == "Darwin": subprocess.call(["open", path])
            else: subprocess.call(["xdg-open", path])
        except Exception as e: st.error(f"打开失败: {e}")

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

    def get_csv_filename(self):
        raw_path = st.session_state.get('root_path', '') or BASE_DIR
        operator = st.session_state.get('operator_name', '').strip()
        prefix = self.get_output_prefix()
        if operator:
            return os.path.join(raw_path, "_质检记录", operator, f"{prefix}_验收记录.csv")
        return os.path.join(raw_path, "_质检记录", f"{prefix}_验收记录.csv")

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
                # 归一化图片ID：去空格、补前导零对齐 folder_digit_length（修复 Excel 编辑导致的前导零丢失）
                if '图片ID' in df.columns:
                    df['图片ID'] = df['图片ID'].astype(str).str.strip()
                mask = df['图片ID'] == target_id
                if mask.any():
                    for key, value in record.items(): df.loc[mask, key] = str(value)
                    # 删除同一 ID 的其他残留重复行（保留 mask 命中的最后一条）
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

        # 清除 _get_df 内存缓存，下次读取直读磁盘
        cache_key = f"_df_cache_{csv_file}"
        st.session_state.pop(cache_key, None)
        lookup_key = f"_df_lookup_{csv_file}"
        st.session_state.pop(lookup_key, None)

        if failed_screenshots:
            return True, f"⚠️ 记录已保存，但第 {', '.join(map(str, failed_screenshots)) } 张截图保存失败"
        return True, ""

    def start_sampling_acceptance(self, all_folders):
        try:
            ratio = st.session_state.sampling_acceptance_ratio
            n = max(1, int(len(all_folders) * ratio / 100))
            sampled = random.sample(all_folders, n)

            # 保存原始全集快照，退出时可恢复
            st.session_state._saved_data_groups = st.session_state.data_groups.copy()
            st.session_state._saved_current_id = st.session_state.current_id

            st.session_state.data_groups = sampled
            st.session_state.current_id = sampled[0]['id']
            st.session_state.sampling_acceptance_mode = True
            st.rerun()
        except Exception as e:
            st.error(f"❌ 抽检验收失败: {e}")

    def _get_missing_ids(self):
        all_loaded_ids = [str(g['id']) for g in st.session_state.data_groups]
        csv_file = self.get_csv_filename()
        return _export_get_missing_ids(all_loaded_ids, csv_file)

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
                    # 恢复原始全集
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
                # 只统计当前数据组内的记录
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

        # 只导出当前数据组内的合格数据
        df_in_scope = df[df['图片ID'].astype(str).isin(all_folder_ids)]
        qualified_df = df_in_scope[df_in_scope['结果'].isin(["合格", "修改后合格"])].drop_duplicates(subset=['图片ID'], keep='last')
        qualified_ids = set(qualified_df['图片ID'].astype(str).tolist())
        
        if not qualified_ids:
            return False, "⚠️ 没有合格的数据可导出", {}
        
        # 构建 id -> group 映射，用当前加载的路径而非CSV里的旧路径
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
                # 优先用当前加载的路径，找不到再用CSV里的路径
                group = id_to_group.get(folder_id)
                src_path = group['root'] if group else row['路径']

                elapsed = time.time() - start_time
                avg_time = elapsed / (idx + 1) if idx > 0 else 0
                remaining = int(avg_time * (total - idx - 1))

                if remaining >= 60:
                    remaining_str = f"{remaining//60}分{remaining%60}秒"
                else:
                    remaining_str = f"{remaining}秒"

                # 每 3 条或最后一条更新状态，减少 UI 刷新
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

                    # 复制后立即比对源和目标文件（统一小写避免大小写不一致）
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

        # 只导出当前数据组内的记录
        groups = st.session_state.data_groups
        all_ids = set(str(g['id']) for g in groups)
        df = df[df['图片ID'].astype(str).isin(all_ids)]
        if df.empty:
            return None, "❌ 当前数据组内无记录"

        records_dir = os.path.dirname(csv_file) or BASE_DIR
        os.makedirs(records_dir, exist_ok=True)
        prefix = self.get_output_prefix()
        excel_filename = os.path.join(records_dir, f"{prefix}_验收记录.xlsx")

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
                            norm_path = img_rel_path.replace("\\", os.sep).replace("/", os.sep)
                            # 优先从 CSV 所在目录查找，回退到 BASE_DIR
                            csv_dir = os.path.dirname(csv_file) or BASE_DIR
                            full_img_path = os.path.join(csv_dir, norm_path)
                            if not os.path.exists(full_img_path):
                                full_img_path = os.path.join(BASE_DIR, norm_path)
                            
                            if os.path.exists(full_img_path):
                                try:
                                    # 动态计算缩放比例，使插入后宽度约 150px
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
        # 限制最大边长，防止大图导致 st.image 崩
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
        
        # 将ID转换为字符串方便展示，并用逗号/换行排列
        ids_text = "\n".join(missing_ids)
        st.code(ids_text, language="text")
        
        if st.button("知道了，我去补充", use_container_width=True, type="primary"):
            st.rerun()

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

    @st.fragment
    def render_a_zone(self):
        """A 区状态标签 + 筛选 + 列表"""
        groups = st.session_state.data_groups
        if groups:
            all_ids = list(dict.fromkeys(g['id'] for g in groups))
            status_map = self.get_record_status_map(self._get_active_csv_path())
            # 只统计当前数据组内的记录状态
            current_status_map = {k: v for k, v in status_map.items() if k in set(all_ids)}
            processed_ids = {k for k, v in current_status_map.items() if v}

            total_count = len(all_ids)
            qualified_count = sum(1 for v in current_status_map.values() if v == '合格')
            modified_pass_count = sum(1 for v in current_status_map.values() if v == '修改后合格')
            unqualified_count = sum(1 for v in current_status_map.values() if v == '不合格')
            unchecked_count = total_count - len(processed_ids)
            pending_count = sum(1 for v in current_status_map.values() if v == '待定')

            r1c1, r1c2 = st.columns(2)
            with r1c1: st.metric("🟢 合格", qualified_count)
            with r1c2: st.metric("🔵 修改后合格", modified_pass_count)
            r2c1, r2c2 = st.columns(2)
            with r2c1: st.metric("🔴 不合格", unqualified_count)
            with r2c2: st.metric("⚪ 未检", unchecked_count)
            r3c1, r3c2 = st.columns(2)
            with r3c1: st.metric("🟡 待定", pending_count)
            with r3c2:
                pass_rate = round((qualified_count + modified_pass_count) / total_count * 100, 1) if total_count > 0 else 0
                st.metric("📊 合格率", f"{pass_rate}%")

            done_count = qualified_count + modified_pass_count + unqualified_count + pending_count
            progress = min(1.0, done_count / total_count) if total_count > 0 else 0
            st.progress(progress, text=f"{done_count}/{total_count} ({int(progress*100)}%)")

            # 3×2 筛选按钮网格（对齐上方 metric 卡片）
            filter_options = ["全部", "未检", "合格", "修改后合格", "不合格", "待定"]
            current_filter = st.session_state.get('filter_pills', '全部')
            for row_idx in range(3):
                c1, c2 = st.columns(2)
                for col_idx, col in enumerate([c1, c2]):
                    opt = filter_options[row_idx * 2 + col_idx]
                    with col:
                        is_sel = (current_filter == opt)
                        if st.button(opt, key=f"fbtn_{opt}", use_container_width=True,
                                     type="primary" if is_sel else "secondary",
                                     on_click=lambda _opt=opt: st.session_state.update({'filter_pills': _opt})):
                            pass
            filter_val = st.session_state.get('filter_pills', '全部')

            filtered_ids = self._get_filtered_ids(current_status_map)

            if not filtered_ids:
                st.info(f"筛选「{filter_val}」下无数据，请切换筛选条件")
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

            if st.session_state.current_id not in all_ids: st.session_state.current_id = all_ids[0]
            
            # 分页参数
            PAGE_SIZE = 20
            total_items = len(filtered_ids)
            total_pages = max(1, (total_items + PAGE_SIZE - 1) // PAGE_SIZE)
            
            # 计算当前项所在页
            current_idx = filtered_ids.index(st.session_state.current_id) if st.session_state.current_id in filtered_ids else 0
            current_page = current_idx // PAGE_SIZE + 1
            
            # 分页按钮组
            if total_pages > 1:
                MAX_VISIBLE = 4
                window_size = min(MAX_VISIBLE, total_pages)
                page_start = ((current_page - 1) // MAX_VISIBLE) * MAX_VISIBLE + 1
                page_end = min(page_start + window_size - 1, total_pages)
                # If at the end with fewer pages, shift window left
                if page_end - page_start + 1 < window_size and page_start > 1:
                    page_start = max(1, page_end - window_size + 1)

                visible_pages = list(range(page_start, page_end + 1))
                num_page_buttons = len(visible_pages)
                total_buttons = num_page_buttons + 2  # ◀ + pages + ▶
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

                # ◀ 上一页
                with page_cols[0]:
                    st.button("◀", disabled=(current_page == 1), key="page_prev",
                              use_container_width=True, on_click=_go_to_page, args=(current_page - 1,))

                # 页码按钮
                for i, p in enumerate(visible_pages):
                    with page_cols[1 + i]:
                        btn_type = "primary" if p == current_page else "secondary"
                        st.button(str(p), key=f"page_{p}", type=btn_type,
                                  use_container_width=True, on_click=_go_to_page, args=(p,))

                # ▶ 下一页
                with page_cols[total_buttons - 1]:
                    st.button("▶", disabled=(current_page == total_pages), key="page_next",
                              use_container_width=True, on_click=_go_to_page, args=(current_page + 1,))
            
            # 只渲染当前页
            start_idx = (current_page - 1) * PAGE_SIZE
            end_idx = min(start_idx + PAGE_SIZE, total_items)
            page_ids = filtered_ids[start_idx:end_idx]
            
            selected_id = st.radio(
                "List",
                page_ids,
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
            
            # 页码提示
            if total_pages > 1:
                st.caption(f"第 {current_page}/{total_pages} 页 (共 {total_items} 项)")

            # 筛选联动：滚动到当前选中项
            components.html(f"""
            <script>
            (function() {{
                var doc = window.parent.document;
                var radios = doc.querySelectorAll('input[type="radio"]');
                for (var i = 0; i < radios.length; i++) {{
                    if (radios[i].checked) {{
                        radios[i].scrollIntoView({{behavior: 'smooth', block: 'nearest'}});
                        break;
                    }}
                }}
            }})();
            </script>
            """, height=0, width=0)

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

    @st.fragment
    def render_control_panel(self, group):
        if st.session_state.get('_batch_completed'):
            st.info("✅ 本轮验收已完成，C 区已锁定。点击 A 区列表条目可复查，或通过 B 区面板导出结果")
            return

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
                             help="打开原图", disabled=not original_path):
                    if original_path:
                        self.open_in_system(original_path)

        st.divider()

        # 2. 错误截图
        st.write("")
        st.markdown(f"**📷 错误截图 ({len(st.session_state[pool_key])}/3)**")

        c_paste, c_clear = st.columns([2, 1])
        with c_paste:
            if len(st.session_state[pool_key]) < 3:
                if HAS_PASTE_LIB:
                    paste_result = paste_image_button(
                        label="📋 粘贴 (Ctrl+V)",
                        background_color="#FF4B4B",
                        hover_background_color="#FF0000",
                        key=f"paste_btn_{group['id']}_{len(st.session_state[pool_key])}"
                    )
                    if paste_result.image_data is not None:
                        st.session_state[pool_key].append(paste_result.image_data)
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
                l2_display = [f"🟢 {opt}" for opt in l2_opts]
                l2_default = f"🟢 {st.session_state.sticky_l2}" if st.session_state.sticky_l2 else None
                try: l2_sel_display = st.pills("L2", l2_display, selection_mode="single", default=l2_default, label_visibility="collapsed")
                except Exception: l2_sel_display = st.radio("L2", l2_display, label_visibility="collapsed")
                l2_sel = l2_sel_display.replace("🟢 ", "") if l2_sel_display else None
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

        # 6. 备注 + 保存/提交
        with st.form(key=f"form_submit_{group['id']}", clear_on_submit=False):
            feedback_text = st.text_area("备注", height=68, placeholder="在此输入备注 (选填)", key=f"feedback_{group['id']}")
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

            b1, b2 = st.columns([1, 2])
            with b1: is_save = st.form_submit_button("💾 仅保存", type="primary", use_container_width=True)
            with b2:
                if st.session_state.sampling_acceptance_mode:
                    is_submit = st.form_submit_button("🚀 提交并下一条（抽检）", type="primary", use_container_width=True)
                else:
                    is_submit = st.form_submit_button("🚀 提交并下一条", type="primary", use_container_width=True)

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
                    st.session_state.selected_tags[current_id] = all_tags.copy()
                    self._sync_tags_to_notes(group)
            with bc2:
                if st.button("🗑️ 清空已选标签", use_container_width=True, key=f"clear_all_{current_id}"):
                    st.session_state.selected_tags[current_id] = []
                    self._sync_tags_to_notes(group)

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
                    st.session_state[pool_key] = []
                    if save_msg:
                        st.toast(save_msg, icon="⚠️")
                    if is_save:
                        st.toast("✓ 已保存", icon="💾")
                    elif is_submit:
                        st.toast("✓ 已提交", icon="🚀")
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

    def render_completion_panel(self):
        """B 区：整批验收完成面板"""
        if not st.session_state.get('_completion_balloons_shown'):
            st.balloons()
            st.session_state._completion_balloons_shown = True

        status_map = self.get_record_status_map(self._get_active_csv_path())
        comp_groups = st.session_state.data_groups
        all_ids = list(dict.fromkeys(g['id'] for g in comp_groups))
        total = len(all_ids)
        # 只统计当前数据组内的记录状态
        current_status_map = {k: v for k, v in status_map.items() if k in set(all_ids)}
        qualified = sum(1 for v in current_status_map.values() if v == '合格')
        modified = sum(1 for v in current_status_map.values() if v == '修改后合格')
        unqualified = sum(1 for v in current_status_map.values() if v == '不合格')
        pending = sum(1 for v in current_status_map.values() if v == '待定')
        processed = qualified + modified + unqualified + pending
        unchecked = total - processed
        pass_rate = round((qualified + modified) / total * 100, 1) if total > 0 else 0

        st.success(f"🎉 全部验收完成！共 {total} 条，已完成 {processed} 条")

        r1c1, r1c2, r1c3, r1c4 = st.columns(4)
        with r1c1: st.metric("🟢 合格", qualified)
        with r1c2: st.metric("🔴 不合格", unqualified)
        with r1c3: st.metric("🔵 修改后合格", modified)
        with r1c4: st.metric("🟡 待定", pending)
        r2c1, r2c2, r2c3, r2c4 = st.columns(4)
        with r2c1: st.metric("⚪ 未检", unchecked)
        with r2c2: st.metric("📊 合格率", f"{pass_rate}%")
        with r2c3: st.metric("📦 总计", total)
        with r2c4:
            pass  # 占位对齐

        csv_path = self.get_csv_filename()
        if os.path.exists(csv_path):
            st.info(f"📄 记录已保存至: {csv_path}")

        st.write("")

        # Excel 导出结果展示（持久化）
        excel_path = st.session_state.get('_excel_export_path')
        if excel_path and os.path.exists(excel_path):
            st.success(f"✅ 已生成报表：{os.path.basename(excel_path)}")
            export_dir = os.path.dirname(excel_path)
            c_dl, c_open = st.columns(2)
            with c_dl:
                with open(excel_path, "rb") as f:
                    st.download_button("⬇️ 下载 Excel", f, file_name=os.path.basename(excel_path), use_container_width=True)
            with c_open:
                if st.button("📂 打开文件夹", use_container_width=True, key="comp_open_excel_dir"):
                    self.open_in_system(export_dir)

        b1, b2 = st.columns(2)
        with b1:
            if st.button("🔄 返回第一条", use_container_width=True, type="primary"):
                st.session_state._batch_completed = False
                st.session_state._completion_balloons_shown = False
                st.session_state.current_id = all_ids[0]
                st.session_state.focus_img_idx = 0
                st.session_state._excel_export_path = None
                st.rerun()
        with b2:
            if st.button("📊 导出 Excel 报表", use_container_width=True):
                file_path, msg = self.export_excel_with_images()
                if file_path:
                    st.session_state._excel_export_path = file_path
                    st.rerun()
                else:
                    st.error(msg)

        b3, b4 = st.columns(2)
        with b3:
            if st.button("📂 打开 CSV 目录", use_container_width=True):
                csv_dir = os.path.dirname(csv_path)
                if os.path.exists(csv_dir):
                    self.open_in_system(csv_dir)
                else:
                    st.warning("_质检记录目录尚不存在")
        with b4:
            if st.button("⚠️ 归集不合格/待定", use_container_width=True):
                missing_ids, total_loaded = self._get_missing_ids()
                if missing_ids:
                    self.show_missing_data_dialog(missing_ids, total_loaded)
                else:
                    self.show_reject_confirm_dialog()

    def render_single_image_view(self, group, images):
        if st.session_state.focus_img_idx >= len(images):
            st.session_state.focus_img_idx = 0
        st.markdown('<div class="switch-btn">', unsafe_allow_html=True)
        cols = st.columns(len(images))
        for i, img in enumerate(images):
            with cols[i]:
                originals = group.get('originals', [group['original']])
                is_orig = (img in originals)
                if is_orig and len(originals) > 1:
                    if re.search(r'_1\.(jpg|png|jpeg)$', img, re.IGNORECASE):
                        label = "🔴 原图1"
                    elif re.search(r'_2\.(jpg|png|jpeg)$', img, re.IGNORECASE):
                        label = "🔴 原图2"
                    else:
                        label = "🔴 原图"
                else:
                    label = "🔴 原图" if is_orig else "🔵 结果" if "_result" in img else "🎭 Mask" if "_mask" in img else f"图 {i+1}"
                b_type = "primary" if i == st.session_state.focus_img_idx else "secondary"
                if st.button(label, key=f"focus_{group['id']}_{i}", type=b_type, use_container_width=True):
                    st.session_state.focus_img_idx = i
        st.markdown('</div>', unsafe_allow_html=True)
        curr_img = images[st.session_state.focus_img_idx]
        curr_img_path = os.path.join(group['root'], curr_img)
        img_bytes, res = get_display_image_bytes(curr_img_path)
        st.image(img_bytes, caption=f"展示: {curr_img}", use_container_width=True)
        st.caption(f"分辨率: {res}")

    @st.fragment
    def render_b_image_area(self, group):
        """B 区图片渲染"""
        img_groups = st.session_state.data_groups
        all_ids_b = [g['id'] for g in img_groups]
        curr_idx_b = all_ids_b.index(st.session_state.current_id) if st.session_state.current_id in all_ids_b else 0
        total_b = len(all_ids_b)
        
        pct = (curr_idx_b + 1) / total_b * 100 if total_b > 0 else 0
        components.html(f"""
        <div style="display:flex; align-items:center; gap:8px; height:20px; overflow:visible;">
            <span style="font-size:0.8rem; white-space:nowrap;">{curr_idx_b + 1}/{total_b}</span>
            <div style="flex:1; height:4px; background:#e0e0e0; border-radius:2px;">
                <div style="width:{pct}%; height:100%; background:#4CAF50; border-radius:2px;"></div>
            </div>
        </div>
        """, height=28)

        if group.get('fallback_image'):
            st.warning("⚠️ 图片未按规则匹配，显示的是文件夹内第一张图片")

        images = group['images']
        if st.session_state.view_mode == "四宫格":
            r1 = st.columns(2); r2 = st.columns(2) if len(images)>2 else []
            for i, img in enumerate(images):
                tgt = r1[i] if i<2 else r2[i-2]
                with tgt:
                    p = os.path.join(group['root'], img)
                    img_bytes, res = get_display_image_bytes(p)
                    st.image(img_bytes, use_container_width=True)
                    cc1, cc2 = st.columns([4, 1])
                    with cc1:
                        st.caption(res)
                    with cc2:
                        st.button("📂", key=f"open_bimg_{group['id']}_{i}", help=f"双击图片或用此按钮在系统查看器中打开 {img}", use_container_width=True,
                                  on_click=self.open_in_system, args=(p,))
            # 双击图片 → 触发对应 📂 按钮
            components.html("""
            <script>
            (function() {
                const doc = window.parent.document;
                const allBtns = Array.from(doc.querySelectorAll('button'));
                const openBtns = allBtns.filter(b => b.innerText.trim() === '📂');
                openBtns.forEach(btn => {
                    btn.style.fontSize = '0.7rem';
                    btn.style.padding = '0 2px';
                    btn.style.minHeight = '20px';
                    btn.style.opacity = '0.5';
                    const block = btn.closest('[data-testid="stVerticalBlock"]');
                    if (!block) return;
                    const img = block.querySelector('img');
                    if (!img || img.dataset.dblclicked) return;
                    img.dataset.dblclicked = '1';
                    img.style.cursor = 'pointer';
                    img.title = '双击在系统查看器中打开';
                    img.addEventListener('dblclick', function(e) {
                        e.preventDefault();
                        e.stopPropagation();
                        btn.click();
                    });
                });
            })();
            </script>
            """, height=0)
        else:
            self.render_single_image_view(group, images)

        st.markdown("---")
        t1, t2 = st.columns(2)
        with t1:
            c_zh = read_txt(os.path.join(group['root'], group['txt_zh']) if group['txt_zh'] else None)
            st.text_area("ZH", value=c_zh, height=60, label_visibility="collapsed", key=f"zh_{group['id']}")
        with t2:
            c_en = read_txt(os.path.join(group['root'], group['txt_en']) if group['txt_en'] else None)
            st.text_area("EN", value=c_en, height=60, label_visibility="collapsed", key=f"en_{group['id']}")

        st.markdown("---")
        qa_df = st.session_state.qa_df
        if not qa_df.empty and '所在文件夹(ID)' in qa_df.columns:
            qa_record = qa_df[qa_df['所在文件夹(ID)'] == str(group['id'])]
            if not qa_record.empty:
                qa_row = qa_record.iloc[-1]
                qa_status = str(qa_row.get('质检结论', '未知')).lower()
                qa_reason = qa_row.get('拒绝原因分析', '无')
                if qa_status == 'true':
                    st.success(f"**🤖 AI 质检通过** ✅ | **原因分析**：{qa_reason}")
                else:
                    st.error(f"**🤖 AI 质检未通过** ❌ | **原因分析**：{qa_reason}")
                with st.expander("👀 查看完整 JSON 数据"):
                    json_str = str(qa_row.get('JSON_Output', '{}'))
                    try:
                        parsed_json = json.loads(json_str.replace('""', '"'))
                        st.json(parsed_json)
                    except:
                        st.code(json_str, language='json')
            else:
                st.info("ℹ️ 质检表中未找到当前 ID 的对应数据。")
        else:
            if qa_df.empty:
                st.caption("ℹ️ 当前未加载质检表，或内容为空。")
            else:
                st.warning("⚠️ 已加载数据源，但未找到列名：`所在文件夹(ID)`。请检查表头是否匹配。")

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
                    st.download_button("⬇️ 下载 Excel", f, file_name=os.path.basename(excel_path), use_container_width=True)
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
                    st.rerun()
                else:
                    st.session_state._export_result = {'success': False, 'msg': msg}
                    st.rerun()

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
        enable_hotkeys = st.toggle("⌨️ 启用快捷键 (1-4, ~)", value=True)
        if enable_hotkeys:
            self.inject_hotkeys()
        with st.expander("⌨️ 快捷键说明", expanded=False):
            st.markdown("""
            | 按键 | 功能 |
            |------|------|
            | `←` 左箭头 | 上一条 |
            | `→` 右箭头 | 下一条 |
            | `空格` | 提交并下一条 |
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

    def run(self):
        version = os.path.basename(BASE_DIR)
        st.set_page_config(layout="wide", page_title=f"审视之眼pro V{version}")
        
        st.markdown("""
            <style>
            header[data-testid="stHeader"], footer {display: none;}
            .appview-container .main .block-container {
                padding-top: 0.4rem !important; padding-bottom: 1rem !important; padding-left: 0.6rem; padding-right: 0.6rem;
            }
            div[data-testid="column"] { padding: 0 4px !important; }
            div[data-testid="column"]:nth-of-type(2) > div[data-testid="stVerticalBlock"] {
                display: flex; flex-direction: column; justify-content: flex-start; min-height: auto; gap: 0.3rem !important;
            }
            div[data-testid="stImage"] img {
                border-top-left-radius: 6px; border-top-right-radius: 6px;
                max-height: calc(100vh * var(--b-height-percent, 85) / 100 - 200px) !important; object-fit: contain !important;
                width: auto !important; margin: 0 auto !important;
            }
            div[data-testid="stImage"] { margin-bottom: -8px !important; text-align: center; }
            .compact-btn button {
                width: 100%; border-radius: 0px; border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;
                border: 1px solid #ddd; border-top: none; background-color: #f8f9fa;
                color: #444; font-size: 0.8rem; padding: 0.1rem 0; min-height: 0px; line-height: 1.2;
                margin-bottom: 1px;
            }
            .compact-btn button:hover { background-color: #e2e6ea; }
            .switch-btn button {
                width: 100%; border-radius: 4px; border: 1px solid #ddd; background-color: #fff;
                padding: 0.1rem 0.2rem; font-weight: bold; margin-bottom: 2px; font-size: 0.85rem; min-height: 0px;
            }
            .switch-btn button:hover { border-color: #007bff; color: #007bff; background-color: #f0f8ff;}
            /* stPills — metric卡片按钮风格 (L1/L2/标签/不一致) */
            div[data-testid="stPills"] { gap: 6px; flex-wrap: wrap; margin-bottom: 0.3rem; }
            div[data-testid="stPills"] > button, div[data-testid="stPills"] > label {
                background-color: #f0f2f6 !important; border-radius: 4px !important; padding: 5px 8px !important;
                font-size: 0.8rem !important; text-align: center !important; border: 2px solid transparent !important; color: #333 !important;
            }
            div[data-testid="stPills"] > button:hover, div[data-testid="stPills"] > label:hover {
                border-color: #6366f1 !important; background-color: #eef2ff !important;
            }
            div[data-testid="stPills"] > [aria-checked="true"] {
                border-color: #6366f1 !important; background-color: #e0e7ff !important; color: #3730a3 !important; font-weight: 600 !important;
            }
            /* stSegmentedControl — metric卡片按钮风格 (最终结果备选) */
            div[data-testid="stSegmentedControl"] > * {
                background-color: #f0f2f6 !important; border-radius: 4px !important; padding: 6px 8px !important;
                font-size: 0.85rem !important; text-align: center !important; border: 2px solid transparent !important; color: #333 !important;
            }
            div[data-testid="stSegmentedControl"] > *:hover {
                border-color: #6366f1 !important; background-color: #eef2ff !important;
            }
            div[data-testid="stSegmentedControl"] > [data-selected="true"] {
                border-color: #6366f1 !important; background-color: #e0e7ff !important; color: #3730a3 !important; font-weight: 600 !important;
            }
            /* stButton — 默认按钮风格 */
            div[data-testid="stButton"] button[kind="secondary"] {
                background-color: #f0f2f6 !important; border: 2px solid transparent !important;
                border-radius: 4px !important; padding: 5px 10px !important;
                font-size: 0.8rem !important; text-align: center !important; color: #333 !important;
            }
            div[data-testid="stButton"] button[kind="secondary"]:hover {
                border-color: #6366f1 !important; background-color: #eef2ff !important;
            }
            div[data-testid="stButton"] button[kind="primary"] {
                background-color: #e0e7ff !important; border: 2px solid #6366f1 !important;
                border-radius: 4px !important; padding: 5px 10px !important;
                font-size: 0.8rem !important; text-align: center !important;
                color: #3730a3 !important; font-weight: 600 !important;
            }
            div[data-testid="stButton"] button[kind="primary"]:hover {
                background-color: #c7d2fe !important;
            }
            div[data-testid="stButton"] { margin-bottom: 0px; }
            div[data-testid="stButton"] button:disabled {
                background-color: #e0e0e0 !important;
                color: #888 !important;
                border-color: #d0d0d0 !important;
                cursor: not-allowed;
                opacity: 0.8;
            }
            div[data-testid="stButton"] button:disabled:hover {
                background-color: #e0e0e0 !important;
                color: #888 !important;
                border-color: #d0d0d0 !important;
            }
            /* 状态按钮颜色 — 通过 JS 注入 data-status 属性后生效 */
            button[data-status="pass"]    { background-color: #dcfce7 !important; color: #166534 !important; border-color: #86efac !important; }
            button[data-status="pass"]:hover { background-color: #bbf7d0 !important; }
            button[data-status="fail"]    { background-color: #fee2e2 !important; color: #991b1b !important; border-color: #fca5a5 !important; }
            button[data-status="fail"]:hover { background-color: #fecaca !important; }
            button[data-status="modified"] { background-color: #dbeafe !important; color: #1e40af !important; border-color: #93c5fd !important; }
            button[data-status="modified"]:hover { background-color: #bfdbfe !important; }
            button[data-status="pending"]  { background-color: #fef9c3 !important; color: #854d0e !important; border-color: #fde047 !important; }
            button[data-status="pending"]:hover { background-color: #fef08a !important; }
            button[data-status="pass"][kind="primary"]    { background-color: #16a34a !important; color: #fff !important; border-color: #16a34a !important; }
            button[data-status="fail"][kind="primary"]    { background-color: #dc2626 !important; color: #fff !important; border-color: #dc2626 !important; }
            button[data-status="modified"][kind="primary"] { background-color: #2563eb !important; color: #fff !important; border-color: #2563eb !important; }
            button[data-status="pending"][kind="primary"]  { background-color: #ca8a04 !important; color: #fff !important; border-color: #ca8a04 !important; }
            div[data-testid="stMetric"] { background-color: #f0f2f6; padding: 8px 12px !important; border-radius: 6px; text-align: center; margin-bottom: 6px !important; }
            div[data-testid="stMetric"] p { margin: 0 !important; font-size: 0.8rem !important; }
            div[data-testid="stMetric"] div[data-testid="stMetricValue"] { font-size: 1rem !important; }
            details[data-testid="stExpander"] { margin-bottom: 0.3rem !important; }
            details[data-testid="stExpander"] summary { padding: 0.3rem 0.5rem !important; font-size: 0.85rem !important; }
            div[data-testid="stHorizontalBlock"] { gap: 0.3rem !important; }
            /* 细滚动条 — 不遮挡，但让用户知道能滚 */
            div[data-testid="element-container"]::-webkit-scrollbar { width: 4px; }
            div[data-testid="element-container"]::-webkit-scrollbar-track { background: transparent; }
            div[data-testid="element-container"]::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.12); border-radius: 2px; }
            div[data-testid="element-container"]::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.25); }
            div[data-testid="element-container"] { scrollbar-width: thin; scrollbar-color: rgba(0,0,0,0.12) transparent; }
            /* B区纵向间距 */
            hr { margin: 0.3rem 0 !important; }
            div[data-testid="column"]:nth-of-type(2) div[data-testid="stCaptionContainer"] { margin: 0 !important; }
            div[data-testid="column"]:nth-of-type(2) div[data-testid="stNotification"] { padding: 0.2rem 0.5rem !important; }
            div[data-testid="column"]:nth-of-type(2) textarea { margin-bottom: -4px !important; }
            div[data-testid="column"]:nth-of-type(2) details[data-testid="stExpander"] { margin: 0 !important; }
            </style>
            <script>
            (function() {
                var doc = window.parent.document;
                function tagStatusButtons() {
                    var btns = doc.querySelectorAll('div[data-testid="stButton"] button');
                    btns.forEach(function(btn) {
                        if (btn.dataset.status) return;
                        var t = btn.innerText.trim();
                        if (t === '合格') btn.dataset.status = 'pass';
                        else if (t === '不合格') btn.dataset.status = 'fail';
                        else if (t === '修改后合格') btn.dataset.status = 'modified';
                        else if (t === '待定') btn.dataset.status = 'pending';
                    });
                }
                tagStatusButtons();
                new MutationObserver(tagStatusButtons).observe(doc.body, {childList: true, subtree: true});
            })();
            </script>
        """, unsafe_allow_html=True)

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
                else:
                    st.session_state.data_groups = scanned
                    st.session_state.current_id = scanned[0]['id']
                    st.session_state.confirm_pending = False
                    st.session_state.pending_groups = []
            else:
                st.session_state.data_groups = []
                st.session_state.current_id = None
                st.session_state.confirm_pending = False
                st.session_state.pending_groups = []
            st.session_state.is_scanning = False
            st.session_state._batch_completed = False
            st.session_state._completion_balloons_shown = False
            st.rerun()

        if not current_group:
            # === B 区 ===
            with col_b:
                self.render_cold_start_animation()
                with st.container(border=True):
                    self._render_datasource_loader()

                with st.expander("⚙️ 更多设置", expanded=False):
                    self._render_settings_panel(key_prefix="cs_", show_operator=True)
        else:
            group = current_group
            self.sync_state_from_history(group['id'])

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
