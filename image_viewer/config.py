# config.py — 独立图片查看器 设置存取
"""快捷键等查看器设置复用主项目的 config/settings.json（避免另造无关配置系统）。

结构：settings.json 下加一个 "image_viewer" 子键，例如：
    "image_viewer": {
        "shortcuts": { "prev": "Left", "next": "Right", ... },
        "pan_button": "right"
    }
本模块既可被主程序（Streamlit）调用，也可被查看器 subprocess 直接读取，
因为它是纯文件读写，不依赖 st.session_state。
"""
import os
import json

# 快捷键默认值（规范推荐）
DEFAULT_SHORTCUTS = {
    "prev": "Left",       # 上一张
    "next": "Right",      # 下一张
    "zoom_in": "+",       # 放大
    "zoom_out": "-",      # 缩小
    "fit": "0",           # 适应窗口
    "hundred": "1",       # 100%
    "close": "Esc",       # 关闭 Viewer
}

# 平移方式默认：鼠标右键拖动
DEFAULT_PAN_BUTTON = "right"

# 查看器模式：内置 Image Viewer / 系统默认查看器
MODE_BUILTIN = "builtin"     # 使用本次新增的独立 Image Viewer
MODE_SYSTEM = "system"       # 沿用系统默认图片打开方式
DEFAULT_VIEWER_MODE = MODE_BUILTIN

VIEWER_MODE_LABELS = {
    MODE_BUILTIN: "内置图片查看器",
    MODE_SYSTEM: "系统默认查看器",
}

# 小写后 -> 中文显示名（用于主程序配置面板）
SHORTCUT_LABELS = {
    "prev": "上一张",
    "next": "下一张",
    "zoom_in": "放大",
    "zoom_out": "缩小",
    "fit": "适应窗口",
    "hundred": "100%",
    "close": "关闭",
}


def _settings_file():
    # 复用主程序设置文件；若能拿到 BASE_DIR 则用之，否则回退到相对 config/
    try:
        from utils import BASE_DIR
        return os.path.join(BASE_DIR, "config", "settings.json")
    except Exception:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(os.path.dirname(here), "config", "settings.json")


def _read_all():
    """读取整个 settings.json，失败返回空 dict。"""
    p = _settings_file()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_all(all_data):
    """写回整个 settings.json（原子写），保留其他键。"""
    p = _settings_file()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def get_viewer_settings():
    """返回 image_viewer 配置（与默认值合并后的完整结构）。"""
    all_data = _read_all()
    v = all_data.get("image_viewer", {}) if isinstance(all_data, dict) else {}
    if not isinstance(v, dict):
        v = {}
    shortcuts = v.get("shortcuts", {}) or {}
    merged = {k: shortcuts.get(k, dv) for k, dv in DEFAULT_SHORTCUTS.items()}
    return {
        "shortcuts": merged,
        "pan_button": v.get("pan_button", DEFAULT_PAN_BUTTON),
        "viewer_mode": v.get("viewer_mode", DEFAULT_VIEWER_MODE),
    }


def save_viewer_mode(mode):
    """保存查看器模式（builtin/system），持久化到 settings.json。"""
    if mode not in (MODE_BUILTIN, MODE_SYSTEM):
        mode = DEFAULT_VIEWER_MODE
    all_data = _read_all()
    if not isinstance(all_data, dict):
        all_data = {}
    v = all_data.get("image_viewer", {}) or {}
    if not isinstance(v, dict):
        v = {}
    v["viewer_mode"] = mode
    all_data["image_viewer"] = v
    _write_all(all_data)


def get_viewer_mode():
    return get_viewer_settings().get("viewer_mode", DEFAULT_VIEWER_MODE)


def save_shortcuts(shortcuts):
    """保存快捷键映谢（校验合法键名 + 转换键到默认组）。"""
    clean = {}
    for k, dv in DEFAULT_SHORTCUTS.items():
        val = shortcuts.get(k, dv)
        if isinstance(val, str) and val.strip():
            clean[k] = val.strip()
        else:
            clean[k] = dv
    all_data = _read_all()
    if not isinstance(all_data, dict):
        all_data = {}
    v = all_data.get("image_viewer", {}) or {}
    if not isinstance(v, dict):
        v = {}
    v["shortcuts"] = clean
    all_data["image_viewer"] = v
    _write_all(all_data)


def save_pan_button(button):
    all_data = _read_all()
    if not isinstance(all_data, dict):
        all_data = {}
    v = all_data.get("image_viewer", {}) or {}
    if not isinstance(v, dict):
        v = {}
    v["pan_button"] = button if button in ("left", "right", "middle") else DEFAULT_PAN_BUTTON
    all_data["image_viewer"] = v
    _write_all(all_data)


def qkeys_for(shortcuts):
    """把快捷键名转成 Qt 可用的按键序列（部分别名归一化）。
    值可能是单个键名，或形如 "Ctrl+Z" 的组合；仅做去空格与别名归并。"""
    out = {}
    alias = {
        "esc": "Esc",
        "left": "Left",
        "right": "Right",
        "up": "Up",
        "down": "Down",
        "space": "Space",
        "enter": "Enter",
        "return": "Return",
    }
    for k, v in (shortcuts or {}).items():
        if not isinstance(v, str):
            continue
        tokens = [t.strip() for t in v.split("+") if t.strip()]
        norm = []
        for t in tokens:
            norm.append(alias.get(t.lower(), t))
        out[k] = "+".join(norm)
    return out
