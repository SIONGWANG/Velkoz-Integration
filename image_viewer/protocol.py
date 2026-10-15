# protocol.py — 主程序 <-> 独立图片查看器 跨进程通信协议
"""分工：
- 主程序（Streamlit 进程）：write_cmd() 写命令、is_viewer_alive() 判断单实例
- 查看器（subprocess）：read_cmd() 读命令、heartbeat() 更新心跳、release_lock() 退出
通信介质：本地消息文件 + 锁文件（含 PID 与心跳），跨平台、无额外依赖，避免占端口。
"""
import os
import json
import time
import logging

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = 20.0       # 心跳超时（秒），超过则视为查看器已退出
HEARTBEAT_INTERVAL = 5.0       # 查看器心跳间隔
CLOSE_WAIT_TIMEOUT = 5.0       # 请求关闭后等待进程自行退出的最长秒数


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


def request_close():
    """请求查看器正常退出：写入显式关闭命令。
    关闭命令整包替换命令文件（不需要保留 images，退出即失效）。"""
    write_cmd({"close": True})


def request_shortcuts(shortcuts):
    """请求运行中的查看器热更新快捷键（不触碰 images，避免重新加载图片）。"""
    write_cmd({"shortcuts": dict(shortcuts or {}), "activate": False})


def request_layout_mode(mode):
    """请求运行中的查看器切换布局（single/dual），不触碰 images。"""
    write_cmd({"layout_mode": str(mode), "activate": False})


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


def lock_pid():
    """读取锁文件中记录的查看器 PID；无锁/损坏返回 None。"""
    p = lock_path()
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("pid")
    except Exception:
        return None


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


def terminate_viewer():
    """兜底：强制结束锁文件记录的查看器 PID（仅该 PID，绝不误杀其他进程）。
    返回 True 表示该 PID 已不存在。"""
    pid = lock_pid()
    if not pid or not _pid_alive(pid):
        release_lock()
        return True
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_TERMINATE = 0x0001
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
            if handle:
                try:
                    ctypes.windll.kernel32.TerminateProcess(handle, 1)
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
        else:
            import signal
            os.kill(int(pid), signal.SIGTERM)
    except Exception as e:
        logger.warning("终止查看器进程失败 pid=%s: %s", pid, e)
        return False
    time.sleep(0.3)
    alive = _pid_alive(pid)
    if not alive:
        release_lock()
    return not alive


def close_viewer(timeout=None):
    """安全关闭查看器：显式关闭命令 → 等待进程退出 → 超时兜底终止该 PID。
    返回 True 表示查看器已结束（或本就不存在）。

    注意：这里不依赖“心跳是否新鲜”来决定是否发送关闭命令。
    只要锁文件存在，就发送关闭命令（心跳可能暂时不新鲜但进程仍在运行），
    再以“PID 是否存活 / 锁是否释放”作为退出判据。"""
    if timeout is None:
        timeout = CLOSE_WAIT_TIMEOUT
    if not os.path.isfile(lock_path()):
        return True
    pid = lock_pid()
    try:
        request_close()
    except Exception as e:
        logger.warning("发送查看器关闭命令失败: %s", e)
    deadline = time.time() + max(0.0, float(timeout))
    while time.time() < deadline:
        if pid and not _pid_alive(pid):
            release_lock()
            return True
        if not os.path.isfile(lock_path()) and (not pid or not _pid_alive(pid)):
            return True
        time.sleep(0.2)
    logger.warning("查看器未在 %.1fs 内退出，执行兜底终止", timeout)
    if pid and _pid_alive(pid):
        return terminate_viewer()
    release_lock()
    return True
