# disk_io.py — 模块级缓存函数，提取自 app.py
import streamlit as st
import os
import re
import json
import datetime
import logging
import time
import pandas as pd
from PIL import Image
from io import BytesIO

from utils import (
    PRELOAD_AHEAD, DISPLAY_JPEG_QUALITY,
    find_image_file, _load_and_rotate, resize_image_for_display,
    extract_user_name, DEFAULT_SCAN_RULES, EVIDENCE_FOLDER_NAME,
    normalize_folder_pattern, build_folder_pattern_regex,
    get_pattern_digit_length,
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
    image_slots = rules.get("image_slots", DEFAULT_SCAN_RULES["image_slots"])
    text_slots = rules.get("text_slots", DEFAULT_SCAN_RULES["text_slots"])

    # 文件夹命名规则：块式 folder_pattern（兼容旧 folder_digit_length）
    folder_pattern, pattern_err = normalize_folder_pattern(rules)
    folder_regex, _regex_err = build_folder_pattern_regex(folder_pattern)
    pattern_digit_len = get_pattern_digit_length(folder_pattern) if folder_regex else None

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

        if folder_regex is None or not folder_regex.fullmatch(folder_name):
            continue

        # 图片匹配：每个 slot 按 suffix 顺序尝试匹配（支持扩展名白名单过滤）
        image_results = []
        for slot in image_slots:
            found = None
            slot_exts = slot.get("extensions") or None
            for suffix in slot.get("stem_suffixes", [""]):
                stem = f"{folder_name}{suffix}"
                found = find_image_file(files, stem, extensions=slot_exts)
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
            user_name = extract_user_name(path_parts, digit_length=pattern_digit_len)
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

    failed_count = 0
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
                    failed_count += 1
                    logging.warning("预加载失败 %s: %s", img_path, e)

    # 如果预加载失败较多，在UI上提示（仅在debug模式下）
    if failed_count > 0 and st.session_state.get('_debug_mode', False):
        st.toast(f"⚠️ {failed_count} 张图片预加载失败", icon="⚠️")


@st.cache_data(show_spinner=False)
def load_qa_report(filepath):
    if os.path.exists(filepath):
        try:
            df = pd.read_csv(filepath, dtype=str, encoding='utf-8-sig')
            return df
        except Exception as e:
            # 记录警告日志
            logging.warning("QA报告加载失败: %s - %s", filepath, str(e))
            # 标记加载失败状态
            st.session_state['_qa_load_failed'] = True
            st.session_state['_qa_load_error'] = str(e)
            return pd.DataFrame()
    return pd.DataFrame()
