# image_cache.py — 显示图本地磁盘缓存（对抗局域网/SMB 抖动）
"""主界面图片显示缓存。

背景：图片放在局域网共享盘（SMB）。当百度网盘等程序占满网络时，SMB 读取会
变慢甚至超时，主页面因此只显示“加载失败”。本模块把已经成功读取过的显示图
（最长边 1920 的 JPEG）缓存到本机磁盘：

- 网络抖动时优先回退到最近一次成功的缓存，页面不再空白；
- 缓存按“路径 + mtime + 大小”区分版本，源图更新后自动失效；
- 缓存只服务“当前正在做的批次”：打开/扫描新批次时自动清掉其他批次的缓存，
  不会越积越多；
- 另有一道 500MB 硬上限和磁盘剩余空间保护，作为异常情况的保险。

只缓存显示用 JPEG，不缓存原图，不修改任何源文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time

CACHE_VERSION = 2   # v2：显示图已统一转换到 sRGB，旧缓存需要重建
DEFAULT_MAX_BYTES = 500 * 1024 * 1024      # 单批次缓存硬上限 500MB
LOW_SPACE_BYTES = 500 * 1024 * 1024        # 剩余空间低于此值：暂停写新缓存
TIGHT_SPACE_BYTES = 2 * 1024 * 1024 * 1024  # 剩余空间低于此值：收紧到 100MB
TIGHT_BUDGET_BYTES = 100 * 1024 * 1024
PRUNE_TARGET_RATIO = 0.8                   # 超限时一次清到 80%，避免频繁抖动
DEGRADED_SECONDS = 20                      # 读取失败后 20 秒内不再主动碰网络盘

_DEGRADED_UNTIL = 0.0
_STORE_COUNTER = 0
_STORE_LOCK = threading.Lock()
_PRUNE_EVERY = 50          # 每写入 50 次检查一次上限
_MAINT_LOCK = threading.Lock()
_RETAIN_LOCK = threading.Lock()
_LAST_RETAIN_ROOT = ""
_STARTED = False
_START_LOCK = threading.Lock()


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


def _normalize(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(path or "")))
    except (OSError, ValueError):
        return ""


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


def _configured_max_bytes() -> int:
    """缓存上限。允许用环境变量覆盖（测试/特殊部署用）。"""
    raw = os.environ.get("VELKOZ_DISPLAY_CACHE_MAX_MB", "").strip()
    if raw:
        try:
            return max(0, int(float(raw) * 1024 * 1024))
        except ValueError:
            pass
    return DEFAULT_MAX_BYTES


def _free_bytes() -> float | None:
    try:
        return shutil.disk_usage(_base_dir()).free
    except OSError:
        return None


def _effective_budget() -> int:
    """实际可用预算：磁盘紧张时自动收紧或停写。"""
    budget = _configured_max_bytes()
    free = _free_bytes()
    if free is None:
        return budget
    if free < LOW_SPACE_BYTES:
        return 0
    if free < TIGHT_SPACE_BYTES:
        return min(budget, TIGHT_BUDGET_BYTES)
    return budget


def _iter_entries():
    """遍历所有条目 JSON，产出 (条目文件路径, 数据或 None)。"""
    entries, _ = _dirs()
    try:
        names = os.listdir(entries)
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        ep = os.path.join(entries, name)
        try:
            with open(ep, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or int(data.get("v", 0)) != CACHE_VERSION:
                data = None
        except (OSError, ValueError):
            data = None
        yield ep, data


def _remove_cached_file(data: dict | None) -> int:
    """删除条目引用的缓存文件，返回释放字节数。"""
    if not data:
        return 0
    fp = data.get("file") or ""
    if not fp:
        return 0
    _, files = _dirs()
    try:
        if os.path.dirname(os.path.abspath(fp)) != os.path.abspath(files):
            return 0
        size = os.path.getsize(fp)
        os.remove(fp)
        return size
    except OSError:
        return 0


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
    # 磁盘空间不足时不写新缓存，避免把用户磁盘撑满
    if _effective_budget() <= 0:
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
    with _STORE_LOCK:
        _STORE_COUNTER += 1
        need_prune = (_STORE_COUNTER % _PRUNE_EVERY == 0)
    if need_prune:
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
    """返回缓存统计（文件数 / 总字节 / 路径 / 上限）。只读。"""
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
    budget = _effective_budget()
    return {
        "root": _base_dir(),
        "files": count,
        "bytes": total,
        "mb": round(total / (1024 * 1024), 1),
        "max_bytes": budget,
        "max_mb": round(budget / (1024 * 1024), 1),
        "percent": round(total / budget * 100, 1) if budget > 0 else 100.0,
        "free_mb": round((_free_bytes() or 0) / (1024 * 1024), 0),
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


def _drop_entry(entry_file: str, data: dict | None) -> int:
    removed = _remove_cached_file(data)
    try:
        os.remove(entry_file)
    except OSError:
        pass
    return removed


def _prune_orphan_entries() -> int:
    """删除指向的文件已不存在的条目 JSON。"""
    removed = 0
    for ep, data in _iter_entries():
        if not data:
            try:
                os.remove(ep)
            except OSError:
                pass
            continue
        fp = data.get("file") or ""
        if not fp or not os.path.isfile(fp):
            removed += _drop_entry(ep, data)
    return removed


def prune(max_bytes: int = None, max_age_days: int = None) -> int:
    """清理缓存，返回删除的字节数。

    默认只按“当前批次的硬上限”做 LRU 清理（超限时清到 80%）。
    max_age_days 仅保留给旧调用/特殊场景，日常不再按天数清理。
    """
    if max_bytes is None:
        max_bytes = _effective_budget()
    max_bytes = max(0, int(max_bytes))
    with _MAINT_LOCK:
        _, files = _dirs()
        try:
            names = os.listdir(files)
        except OSError:
            return 0
        items = []
        for name in names:
            p = os.path.join(files, name)
            try:
                stt = os.stat(p)
            except OSError:
                continue
            items.append((stt.st_mtime, stt.st_size, p))

        now = time.time()
        doomed: dict[str, int] = {}
        if max_age_days:
            max_age = max(1, int(max_age_days)) * 86400
            for mtime, size, p in items:
                if now - mtime > max_age:
                    doomed[p] = size

        alive = [i for i in items if i[2] not in doomed]
        alive.sort(key=lambda x: x[0])
        total = sum(x[1] for x in alive)
        target = int(max_bytes * PRUNE_TARGET_RATIO)
        for _mtime, size, p in alive:
            if total <= target:
                break
            doomed[p] = size
            total -= size

        removed = 0
        for p, size in doomed.items():
            try:
                os.remove(p)
                removed += size
            except OSError:
                pass
        removed += _prune_orphan_entries()
        return removed


def _is_under(path: str, root: str) -> bool:
    if not path or not root:
        return False
    if path == root:
        return True
    prefix = root if root.endswith(os.sep) else root + os.sep
    return path.startswith(prefix)


def retain_only(root_path: str) -> int:
    """只保留指定批次目录下的缓存，删除其他批次的缓存。

    返回释放的字节数。root_path 为空时不做任何操作，避免误删。
    """
    if not str(root_path or "").strip():
        return 0
    root = _normalize(root_path)
    if not root:
        return 0
    removed = 0
    with _MAINT_LOCK:
        for ep, data in list(_iter_entries()):
            if not data:
                try:
                    os.remove(ep)
                except OSError:
                    pass
                continue
            if _is_under(_normalize(data.get("path", "")), root):
                continue
            removed += _drop_entry(ep, data)
    return removed


def retain_only_async(root_path: str) -> None:
    """后台清理其他批次缓存（不阻塞界面）。"""
    global _LAST_RETAIN_ROOT
    root = _normalize(root_path)
    if not root:
        return
    with _RETAIN_LOCK:
        if _LAST_RETAIN_ROOT == root:
            return
        _LAST_RETAIN_ROOT = root

    def _work():
        try:
            retain_only(root)
        except Exception:
            pass

    threading.Thread(target=_work, daemon=True, name="velkoz-cache-retain").start()


def cleanup_tmp(max_age_seconds: int = 3600) -> int:
    """清理异常退出留下的临时文件，返回释放字节数。"""
    entries, files = _dirs()
    removed = 0
    now = time.time()
    for folder in (entries, files):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not name.startswith(".tmp_"):
                continue
            p = os.path.join(folder, name)
            try:
                stt = os.stat(p)
                if now - stt.st_mtime < max_age_seconds:
                    continue
                removed += stt.st_size
                os.remove(p)
            except OSError:
                continue
    return removed


def _startup_work() -> None:
    try:
        cleanup_tmp()
    except Exception:
        pass
    try:
        prune()
    except Exception:
        pass


def startup_maintenance() -> None:
    """程序启动时后台执行一次清理（每个进程只跑一次，不阻塞首屏）。"""
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True
    threading.Thread(target=_startup_work, daemon=True, name="velkoz-cache-maint").start()
