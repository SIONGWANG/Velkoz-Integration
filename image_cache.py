# image_cache.py — 显示图本地磁盘缓存（对抗局域网/SMB 抖动）
"""主界面图片显示缓存。

背景：图片放在局域网共享盘（SMB）。当百度网盘等程序占满网络时，SMB 读取会
变慢甚至超时，主页面因此只显示“加载失败”。本模块把已经成功读取过的显示图
（最长边 1920 的 JPEG）缓存到本机磁盘：

- 网络抖动时优先回退到最近一次成功的缓存，页面不再空白；
- 缓存按“路径 + mtime + 大小”区分版本，源图更新后自动失效；
- 按总大小/天数做 LRU 清理（默认 2GB / 60 天）。

只缓存显示用 JPEG，不缓存原图，不修改任何源文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time

CACHE_VERSION = 1
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024      # 2 GB
DEFAULT_MAX_AGE_DAYS = 60
DEGRADED_SECONDS = 20                            # 读取失败后 20 秒内不再主动碰网络盘

_DEGRADED_UNTIL = 0.0
_STORE_COUNTER = 0
_PRUNE_EVERY = 200        # 每写入 200 次触发一次轻量清理


def _base_dir() -> str:
    """缓存根目录：优先 D:\\VelkozDATA\\cache\\display，其次用户本地目录。"""
    env = os.environ.get("VELKOZ_DISPLAY_CACHE", "").strip()
    if env:
        return env
    for candidate in (r"D:\VelkozDATA\cache\display",):
        parent = os.path.dirname(os.path.dirname(candidate))
        if os.path.isdir(parent):
            return candidate
    local = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(local, "Velkoz", "display_cache")


def _dirs() -> tuple[str, str]:
    root = _base_dir()
    return os.path.join(root, "entries"), os.path.join(root, "files")


def _ensure_dirs() -> None:
    entries, files = _dirs()
    os.makedirs(entries, exist_ok=True)
    os.makedirs(files, exist_ok=True)


def _path_key(path: str) -> str:
    norm = os.path.normcase(os.path.abspath(str(path or "")))
    return hashlib.sha1(norm.encode("utf-8", "surrogatepass")).hexdigest()


def _entry_path(path: str) -> str:
    entries, _ = _dirs()
    return os.path.join(entries, _path_key(path) + ".json")


def _read_entry(path: str) -> dict | None:
    try:
        with open(_entry_path(path), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and int(data.get("v", 0)) == CACHE_VERSION:
            return data
    except (OSError, ValueError):
        pass
    return None


def _write_json_atomic(target: str, data: dict) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load(path: str, mtime=None, size=None) -> tuple[bytes, str] | None:
    """读取显示图缓存；mtime/size 存在时必须完全匹配，否则返回 None。"""
    entry = _read_entry(path)
    if not entry:
        return None
    if mtime is not None and entry.get("mtime") != mtime:
        return None
    if size is not None and entry.get("size") != size:
        return None
    file_path = entry.get("file") or ""
    if not file_path or not os.path.isfile(file_path):
        return None
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        if not data:
            return None
        # 轻量更新访问时间，供 LRU 清理
        try:
            os.utime(file_path, None)
        except OSError:
            pass
        return data, str(entry.get("resolution") or "")
    except OSError:
        return None


def load_stale(path: str) -> tuple[bytes, str] | None:
    """忽略版本匹配，返回最近一次成功缓存（网络盘不可用时的兜底）。"""
    return load(path)


def store(path: str, mtime, size, data: bytes, resolution: str = "") -> None:
    global _STORE_COUNTER
    if not data:
        return
    try:
        _ensure_dirs()
    except OSError:
        return
    entries, files = _dirs()
    key = _path_key(path)
    stem = f"{key}_{mtime}_{size}"
    ext = ".jpg"
    file_path = os.path.join(files, stem + ext)
    if not os.path.isfile(file_path):
        fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=files)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, file_path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return
    entry = {
        "v": CACHE_VERSION,
        "path": os.path.abspath(str(path)),
        "mtime": mtime,
        "size": size,
        "file": file_path,
        "resolution": resolution,
        "updated": time.time(),
    }
    try:
        _write_json_atomic(_entry_path(path), entry)
    except Exception:
        return
    # 同一个路径换版本后，顺手删除旧文件
    try:
        for name in os.listdir(files):
            if name.startswith(key + "_") and name != os.path.basename(file_path):
                try:
                    os.remove(os.path.join(files, name))
                except OSError:
                    pass
    except OSError:
        pass
    # 每写入一定次数做一次轻量清理，防止缓存无限增长
    _STORE_COUNTER += 1
    if _STORE_COUNTER % _PRUNE_EVERY == 0:
        try:
            prune()
        except Exception:
            pass


def mark_failure() -> None:
    global _DEGRADED_UNTIL
    _DEGRADED_UNTIL = time.time() + DEGRADED_SECONDS


def mark_success() -> None:
    global _DEGRADED_UNTIL
    _DEGRADED_UNTIL = 0.0


def is_degraded() -> bool:
    return time.time() < _DEGRADED_UNTIL


def stats() -> dict:
    """返回缓存统计（文件数 / 总字节 / 路径）。只读。"""
    entries, files = _dirs()
    total = 0
    count = 0
    try:
        for name in os.listdir(files):
            p = os.path.join(files, name)
            try:
                if os.path.isfile(p):
                    total += os.path.getsize(p)
                    count += 1
            except OSError:
                continue
    except OSError:
        pass
    return {
        "root": _base_dir(),
        "files": count,
        "bytes": total,
        "mb": round(total / (1024 * 1024), 1),
    }


def clear() -> dict:
    """清空缓存，返回释放的统计。"""
    before = stats()
    root = _base_dir()
    try:
        if os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)
    except OSError:
        pass
    return before


def prune(max_bytes: int = DEFAULT_MAX_BYTES, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> int:
    """按总大小和天数清理缓存，返回删除的字节数。"""
    _, files = _dirs()
    try:
        items = []
        for name in os.listdir(files):
            p = os.path.join(files, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            items.append((st.st_mtime, st.st_size, p))
    except OSError:
        return 0
    now = time.time()
    removed = 0
    max_age = max(1, int(max_age_days)) * 86400
    # 先删过期
    alive = []
    for mtime, size, p in items:
        if now - mtime > max_age:
            try:
                os.remove(p)
                removed += size
                continue
            except OSError:
                pass
        alive.append((mtime, size, p))
    # 再按最久未访问删除，直到低于预算
    alive.sort(key=lambda x: x[0])
    total = sum(x[1] for x in alive)
    for mtime, size, p in alive:
        if total <= max_bytes:
            break
        try:
            os.remove(p)
            total -= size
            removed += size
        except OSError:
            pass
    return removed
