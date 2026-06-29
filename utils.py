"""审视之眼 Pro — 纯工具函数与常量模块（不依赖 Streamlit 状态）"""
import os
import sys
import re
import json
import logging
from functools import lru_cache
from PIL import Image, ImageFile

# 允许加载不完整的图片文件（损坏/截断），避免整个质检流程因单张坏图崩溃
ImageFile.LOAD_TRUNCATED_IMAGES = True

# === 📍 路径 ===
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# === ⚙️ 常量 ===
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
PRELOAD_AHEAD = 5
MAX_DISPLAY_PX = 1920
MAX_LOAD_PX = 3840      # 内存中保留的最大边长（4K），防止大图撑爆内存
DISPLAY_JPEG_QUALITY = 92
CONFIG_FILENAME = "categories_config.json"
EVIDENCE_FOLDER_NAME = "_00_Evidence_All"

SCAN_RULES_FILENAME = "scan_rules.json"

DEFAULT_SCAN_RULES = {
    "folder_digit_length": 8,
    "image_slots": [
        {"stem_suffixes": [""]},
        {"stem_suffixes": ["_result"]}
    ],
    "text_slots": [
        {"suffixes": ["_ZH", "_CH"]},
        {"suffixes": ["_EN"]}
    ]
}

def get_scan_rules_path():
    return os.path.join(BASE_DIR, SCAN_RULES_FILENAME)

def load_scan_rules():
    config_path = get_scan_rules_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            if "folder_digit_length" in config and "image_slots" in config and "text_slots" in config:
                return config
        except Exception:
            logging.warning("扫描规则配置损坏，回退默认: %s", config_path)
    rules = json.loads(json.dumps(DEFAULT_SCAN_RULES))  # deep copy
    save_scan_rules(rules)
    return rules

def save_scan_rules(rules):
    config_path = get_scan_rules_path()
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

DEFAULT_CATEGORIES = {
    "L1": ["空间编辑", "人物/物体一致性"],
    "L2": {
        "空间编辑": ["位置改变", "位置推理", "位置标注"],
        "人物/物体一致性": ["人物一致性", "物体一致性"]
    }
}


# === 🖼️ 图片处理 ===
def is_supported_image(filename):
    return filename.lower().endswith(IMAGE_EXTENSIONS)


def find_image_file(files, stem):
    """按 stem 匹配图片文件（大小写不敏感）"""
    for file_name in sorted(files):
        name, ext = os.path.splitext(file_name)
        if name.lower() == stem.lower() and ext.lower() in IMAGE_EXTENSIONS:
            return file_name
    return None


@lru_cache(maxsize=100)
def _load_and_rotate(path, _mtime=0):
    """从磁盘加载图片并自动旋转，限制最大边长防止内存溢出（LRU 缓存）。
    _mtime: 文件修改时间戳，参与缓存键以感知文件变更。"""
    try:
        img = Image.open(path)
    except Exception:
        return None
    # 解压炸弹保护：拒绝超大像素图片
    try:
        Image.MAX_IMAGE_PIXELS = 200_000_000  # ~2亿像素，约 14000×14000
        img.verify()
        img = Image.open(path)
    except Exception:
        Image.MAX_IMAGE_PIXELS = None  # 无限制兜底
        img = Image.open(path)
    try:
        exif = img._getexif()
        if exif:
            orientation = exif.get(274)
            if orientation:
                rotate_map = {3: 180, 6: 270, 8: 90}
                if orientation in rotate_map:
                    img = img.rotate(rotate_map[orientation], expand=True)
    except Exception:
        pass
    # 限制最大边长，LRU 缓存不再存全分辨率原图
    w, h = img.size
    max_dim = max(w, h)
    if max_dim > MAX_LOAD_PX:
        ratio = MAX_LOAD_PX / max_dim
        new_w, new_h = int(w * ratio), int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
    return img


def auto_rotate_image(img_path):
    """自动纠正图片 EXIF 方向（带缓存）"""
    try:
        mtime = os.path.getmtime(img_path)
    except Exception:
        mtime = 0
    return _load_and_rotate(img_path, mtime)


def resize_image_for_display(img):
    """将 PIL Image 缩放到适合屏幕显示的大小并返回 (PIL.Image, 分辨率字符串)"""
    w, h = img.size
    res_str = f"{w}×{h}"
    max_dim = max(w, h)
    if max_dim > MAX_DISPLAY_PX:
        ratio = MAX_DISPLAY_PX / max_dim
        new_w, new_h = int(w * ratio), int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
    return img, res_str


# === 📋 配置读写 ===
def get_config_path():
    return os.path.join(BASE_DIR, CONFIG_FILENAME)


def load_categories_config():
    config_path = get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                if "L1" in config and "L2" in config:
                    return config
        except Exception:
            logging.warning("分类配置文件损坏，回退到默认配置: %s", config_path)
    config = DEFAULT_CATEGORIES.copy()
    save_categories_config(config)
    return config


def save_categories_config(config):
    config_path = get_config_path()
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# === 👤 姓名提取 ===
# 已知非姓名的路径段（状态标签、分类目录等）
_SKIP_SEGMENTS = frozenset({'合格', '不合格', '待定', '修改后合格', '未检'})

# 容易被误识别为姓名的常见非姓名词汇
_NON_NAME_WORDS = frozenset({
    '完成', '验收', '合格', '不合格', '待定', '修改', '未检',
    '新标', '数据', '返回', '结果', '通过', '失败', '提交',
    '图片', '文件', '目录', '路径', '处理', '审核', '中子',
    '生产', '制作', '空间', '人物', '物体', '标注', '质检',
})


def _find_name_in_segment(part):
    """在单个路径段内用上下文模式搜索中文姓名。
    返回姓名字符串或 None。"""
    # 策略1a：姓名 + 姓名后缀（保留后缀一起返回，如"张三返修"）
    m = re.search(r'([\u4e00-\u9fff]{2,3})(?:返修|新标|修改|重做|补做|复审)', part)
    if m and m.group(1) not in _NON_NAME_WORDS:
        return m.group(0)
    # 策略1b：姓名 + "生产"（上下文标记，不保留"生产"，如"杨佳奇生产的"→"杨佳奇"）
    m = re.search(r'([\u4e00-\u9fff]{2,3})生产', part)
    if m and m.group(1) not in _NON_NAME_WORDS:
        return m.group(1)
    # 策略2：姓名后紧跟全角括号（如"杨佳奇（3.6）"）
    m = re.search(r'([\u4e00-\u9fff]{2,3})（', part)
    if m and m.group(1) not in _NON_NAME_WORDS:
        return m.group(1)
    # 策略3：全角右括号后紧跟姓名（如"【中子】杨佳奇"）
    m = re.search(r'】([\u4e00-\u9fff]{2,3})', part)
    if m and m.group(1) not in _NON_NAME_WORDS:
        return m.group(1)
    # 策略4：段以 2-3 个中文字符开头（原始逻辑）
    m = re.match(r'^[\u4e00-\u9fff]{2,3}', part)
    if m and part not in _SKIP_SEGMENTS:
        return part
    return None


def extract_user_name(path_parts, digit_length=8):
    """从路径段列表中智能提取标注员姓名。

    规则（按优先级）：
    1. 从路径末端向上查找，跳过 N 位纯数字（数据文件夹 ID）
    2. 跳过已知状态/分类标签（合格、不合格等）
    3. 跳过批次文件夹（batch_YYYYMMDD_NNN_keyword）
    4. 在段内用上下文模式搜索中文姓名（返修/新标等后缀、全角括号、右括号后）
    5. 回退到段首 2-3 中文字符匹配
    6. 匹配不到则返回 "未知"
    """
    for part in reversed(path_parts):
        if not part or part == '.':
            continue
        if part.isdigit() and len(part) == digit_length:
            continue
        if part in _SKIP_SEGMENTS:
            continue
        if part.startswith('batch_'):
            continue
        result = _find_name_in_segment(part)
        if result:
            return result
    return "未知"


# === 📄 文本读取 ===
def read_txt(path):
    if not path:
        return ""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        try:
            with open(path, 'r', encoding='gbk') as f:
                return f.read()
        except Exception:
            return ""
