# disk_io.py — 模块级缓存函数，提取自 app.py
import streamlit as st
import os
import re
import json
import datetime
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from PIL import Image, ImageOps, ImageCms
from io import BytesIO

from utils import (
    PRELOAD_AHEAD, DISPLAY_JPEG_QUALITY, MAX_DISPLAY_PX,
    find_image_file, resize_image_for_display,
    extract_user_name, DEFAULT_SCAN_RULES, EVIDENCE_FOLDER_NAME,
    normalize_folder_pattern, build_folder_pattern_regex,
    get_pattern_digit_length,
)
import image_cache


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


def _stat_image(img_path):
    """读取图片的 mtime/size；网络盘抖动时返回 (None, None)，不抛异常。"""
    try:
        st = os.stat(img_path)
        return st.st_mtime, st.st_size
    except OSError:
        return None, None


_DISPLAY_READ_ATTEMPTS = 3
_DISPLAY_RETRY_DELAYS = (0.25, 0.8)


def _tag_stale(entry):
    """给回退缓存的分辨率文本加标记，避免把旧图误当最新图。"""
    data, res = entry
    return data, (res + " · 本机缓存") if res else "本机缓存"


def _profile_to_srgb(img):
    """把带 ICC 的图片统一转到 sRGB；无 ICC 或转换失败时原样返回。"""
    icc = img.info.get("icc_profile")
    if not icc:
        return img
    try:
        src = ImageCms.ImageCmsProfile(BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(img, src, dst, outputMode="RGB")
    except Exception:
        logging.debug("ICC 转 sRGB 失败，按原值显示", exc_info=True)
        return img


def _open_for_display(img_path):
    """快速打开图片用于主界面显示。

    与 _load_and_rotate 的区别：
    - JPEG 先用 draft() 让解码器直接按 1/2、1/4、1/8 降采样，大图解码快很多；
    - 不做 verify()+二次打开（会整份读两遍），失败由上层重试/兜底；
    - 返回 (图像, 原图宽, 原图高)，分辨率显示仍用原图尺寸。
    """
    img = Image.open(img_path)
    orig_w, orig_h = img.size
    try:
        orientation = img.getexif().get(274)
    except Exception:
        orientation = None
    if orientation in (5, 6, 7, 8):
        orig_w, orig_h = orig_h, orig_w
    try:
        img.draft("RGB", (MAX_DISPLAY_PX, MAX_DISPLAY_PX))
    except Exception:
        pass
    img.load()
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = _profile_to_srgb(img)
    return img, orig_w, orig_h


def _placeholder_entry():
    placeholder = Image.new("RGB", (400, 300), color=(80, 80, 80))
    buf = BytesIO()
    placeholder.save(buf, format="JPEG", quality=60)
    return buf.getvalue(), "加载失败"


def _build_display_entry(img_path, mtime=None, size=None):
    """生成/读取显示图缓存（纯函数，不依赖 Streamlit，可安全用于后台线程）。"""
    if mtime is None:
        mtime, size = _stat_image(img_path)

    # 精确版本命中：直接本地读取，完全不碰网络盘
    if mtime is not None:
        hit = image_cache.load(img_path, mtime, size)
        if hit is not None:
            image_cache.mark_success()
            return hit

    # 已经判定网络盘异常：先给旧缓存；连旧缓存都没有就直接占位，
    # 不要再排 3 次 SMB 超时把页面拖住（20 秒后会重新尝试）
    if image_cache.is_degraded():
        stale = image_cache.load(img_path)
        if stale is not None:
            return _tag_stale(stale)
        return _placeholder_entry()

    img = None
    orig_w = orig_h = 0
    last_error = ""
    for attempt in range(_DISPLAY_READ_ATTEMPTS):
        try:
            img, orig_w, orig_h = _open_for_display(img_path)
            break
        except Exception as e:  # 网络中断/文件被占用/解码失败
            last_error = str(e)
            img = None
            if attempt < _DISPLAY_READ_ATTEMPTS - 1:
                time.sleep(_DISPLAY_RETRY_DELAYS[attempt])

    if img is None:
        image_cache.mark_failure()
        stale = image_cache.load(img_path)
        if stale is not None:
            logging.warning("图片读取失败，已回退本地缓存: %s (%s)", img_path, last_error)
            return _tag_stale(stale)
        logging.warning("图片读取失败且无缓存: %s (%s)", img_path, last_error)
        return _placeholder_entry()

    image_cache.mark_success()
    img, _ = resize_image_for_display(img)
    if img.mode in ("RGBA", "LA", "PA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "PA":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img = ImageOps.expand(img, border=2, fill=(180, 180, 180))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=DISPLAY_JPEG_QUALITY)
    img_bytes = buf.getvalue()
    res_str = f"{orig_w}×{orig_h}" if orig_w and orig_h else ""
    entry = (img_bytes, res_str)
    # 写入本地缓存；失败不影响本次显示
    try:
        image_cache.store(img_path, mtime or 0, size or 0, img_bytes, res_str)
    except Exception:
        logging.debug("写入显示图缓存失败: %s", img_path, exc_info=True)
    return entry


def _safe_build(path):
    try:
        return _build_display_entry(path)
    except Exception as e:
        logging.warning("预热图片失败 %s: %s", path, e)
        return None


def _trim_session_cache(cache):
    MAX_CACHE_ENTRIES = 50
    MAX_CACHE_BYTES = 100 * 1024 * 1024  # 100MB
    while len(cache) > MAX_CACHE_ENTRIES:
        oldest = next(iter(cache))
        del cache[oldest]
    total_bytes = sum(len(v[0]) for v in cache.values() if isinstance(v, tuple))
    while total_bytes > MAX_CACHE_BYTES and len(cache) > 1:
        oldest = next(iter(cache))
        total_bytes -= len(cache[oldest][0])
        del cache[oldest]


def get_display_image_bytes(img_path):
    """获取显示用图片的 bytes（缩放后的 JPEG），带 session_state + 本地磁盘缓存。

    网络盘（SMB）被百度网盘等占满时，读取可能超时。这里做三层兜底：
    1. session_state 内存缓存；
    2. 本机显示图磁盘缓存（路径 + mtime + 大小精确匹配）；
    3. 读取失败时回退到最近一次成功缓存，避免主页只显示“加载失败”。
    """
    cache = st.session_state.get("_display_img_cache")
    if cache is None:
        cache = {}
        st.session_state._display_img_cache = cache
    mtime, size = _stat_image(img_path)
    cache_key = f"{img_path}_{mtime}" if mtime is not None else None
    if cache_key and cache_key in cache:
        return cache[cache_key]

    entry = _build_display_entry(img_path, mtime, size)
    # 旧缓存/占位图不进 session 缓存，下一次仍会先尝试读源图，网络恢复后自动更新
    if cache_key and entry[1] != "加载失败" and "本机缓存" not in (entry[1] or ""):
        cache[cache_key] = entry
        _trim_session_cache(cache)
    return entry


def prime_display_cache(paths, workers=4):
    """并行预热一组显示图（首次打开网格时把串行读盘变成并行）。

    只写磁盘缓存，不碰 session_state，可在主线程等待，也可安全用于后台。
    """
    unique, seen = [], set()
    for p in paths or []:
        if p and p not in seen:
            seen.add(p)
            unique.append(p)
    if not unique:
        return
    if len(unique) == 1:
        _safe_build(unique[0])
        return
    workers = max(1, min(int(workers), len(unique), 4))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="velkoz-prime") as ex:
        list(ex.map(_safe_build, unique))


# ── 后台预加载：不阻塞页面渲染 ──
_PREFETCH_LOCK = threading.Lock()
_PREFETCH_GEN = 0
_PREFETCH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="velkoz-prefetch")
_MAX_PREFETCH_IMAGES = 12


def _prefetch_task(path, gen):
    with _PREFETCH_LOCK:
        if gen != _PREFETCH_GEN:
            return
    _safe_build(path)


def prime_display_cache_async(paths):
    """后台预热（不等待），用于单图模式下预取同组其他图片。

    注意：这里不使用预取“代际”取消机制——当前组是同组图片，永远值得读；
    否则后面的换组预取一启动，会把刚排队的当前组任务全部作废。
    """
    for p in paths or []:
        if p:
            _PREFETCH_EXECUTOR.submit(_safe_build, p)


def preload_next_images(current_idx, all_groups):
    """后台预取当前索引之后的若干组图片（不阻塞界面）。

    注意：group['images'] 存的是文件名，需要和 group['root'] 拼接；
    旧实现漏了这一步，实际从未预取成功，也是首次打开图片慢的原因之一。
    """
    global _PREFETCH_GEN
    if not all_groups or current_idx < 0:
        return
    if image_cache.is_degraded():
        return

    paths, seen = [], set()
    preload_count = min(PRELOAD_AHEAD, len(all_groups) - 1 - current_idx)
    for i in range(1, preload_count + 1):
        target_idx = current_idx + i
        if target_idx >= len(all_groups):
            break
        group = all_groups[target_idx]
        root = group.get("root", "")
        for name in group.get("images", []):
            if not name:
                continue
            path = name if os.path.isabs(name) else os.path.join(root, name)
            if path not in seen:
                seen.add(path)
                paths.append(path)
            if len(paths) >= _MAX_PREFETCH_IMAGES:
                break
        if len(paths) >= _MAX_PREFETCH_IMAGES:
            break

    with _PREFETCH_LOCK:
        _PREFETCH_GEN += 1
        gen = _PREFETCH_GEN
    for p in paths:
        _PREFETCH_EXECUTOR.submit(_prefetch_task, p, gen)



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
