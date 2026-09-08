# protocol.py — 主程序 <-> 独立图片查看器 跨进程通信协议
"""分工：
- 主程序（Streamlit 进程）：write_cmd() 写命令、is_viewer_alive() 判断单实例
- 查看器（subprocess）：read_cmd() 读命令、heartbeat() 更新心跳、release_lock() 退出
通信介质：本地消息文件 + 锁文件（含 PID 与心跳），跨平台、无额外依赖，避免占端口。
"""
import os
import json
import time

HEARTBEAT_TIMEOUT = 20.0       # 心跳超时（秒），超过则视为查看器已退出
HEARTBEAT_INTERVAL = 5.0       # 查看器心跳间隔


def runtime_dir():
    """运行期目录（用户级），主程序与 subprocess 共用。"""
    return os.path.join(os.path.expanduser("~"), ".velkoz_image_viewer")


def _ensure_dir():
    d = runtime_dir()
    os.makedirs(d, exist_ok=True)
    return d


def cmd_path():
    return os.path.join(runtime_dir(), "cmd.json")


def lock_path():
    return os.path.join(runtime_dir(), "lock.json")


def log_path():
    return os.path.join(runtime_dir(), "viewer.log")


# ── 命令写入（主程序侧） ──
def write_cmd(payload):
    """原子写入命令文件。payload 为 dict，会被整包替换。
    加入重试以缓解 Windows 下与读取进程的瞬时文件占用冲突。"""
    _ensure_dir()
    tmp = cmd_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    last_err = None
    for attempt in range(5):
        try:
            os.replace(tmp, cmd_path())
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.1 * (attempt + 1))
    raise last_err if last_err else RuntimeError("写入命令文件失败")


def read_cmd():
    """读取命令文件；不存在或损坏时返回 None。"""
    p = cmd_path()
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── 上传队列（查看器 -> 主程序） ──
def uploads_path():
    return os.path.join(runtime_dir(), "uploads.json")


def push_upload(payload):
    """查看器把一个已截取的截图追加到上传队列，主程序消费后清除。"""
    _ensure_dir()
    path = uploads_path()
    queue = []
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    queue = data
        except Exception:
            queue = []
    queue.append(payload)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False)
    try:
        os.replace(tmp, path)
    except PermissionError:
        import time as _t
        for _ in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                _t.sleep(0.1)
    return payload


def pop_uploads():
    """主程序读取并清空上传队列，返回条目列表。"""
    path = uploads_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            queue = data
        else:
            queue = []
    except Exception:
        queue = []
    # 清空
    try:
        os.remove(path)
    except OSError:
        pass
    return queue


# ── 锁 / 心跳（查看器侧） ──
def write_lock(pid):
    """查看器启动时写锁文件，记录 PID 与当前时间。"""
    _ensure_dir()
    tmp = lock_path() + ".tmp"
    data = {"pid": pid, "beat": time.time()}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, lock_path())


def heartbeat():
    """查看器周期刷新心跳时间戳（原子写，避免关断时读到半文件）。"""
    _ensure_dir()
    tmp = lock_path() + ".tmp"
    data = {"pid": os.getpid(), "beat": time.time()}
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, lock_path())
    except Exception:
        pass


def release_lock():
    try:
        if os.path.isfile(lock_path()):
            os.remove(lock_path())
    except OSError:
        pass


def _pid_alive(pid):
    """跨平台判断进程是否存活。Windows 用 ctypes 打开进程句柄，不需 psutil。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            # 返回非零表示进程仍在
            code = wintypes.DWORD(0)
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        # posix: os.kill(pid, 0) 无异常则存活
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def is_viewer_alive(timeout=None):
    """判断查看器是否存活。依据：锁文件存在、心跳未超时、且 PID 确实在运行。"""
    if timeout is None:
        timeout = HEARTBEAT_TIMEOUT
    p = lock_path()
    if not os.path.isfile(p):
        return False
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        beat = float(data.get("beat", 0))
        if (time.time() - beat) >= timeout:
            return False
        # 心跳新鲜但 PID 已死（硬崩溃/残留锁）→ 视为未存活
        return _pid_alive(data.get("pid", 0))
    except Exception:
        return False
