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


def is_viewer_alive(timeout=None):
    """判断查看器是否存活。依据：锁文件存在且心跳未超时。"""
    if timeout is None:
        timeout = HEARTBEAT_TIMEOUT
    p = lock_path()
    if not os.path.isfile(p):
        return False
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        beat = float(data.get("beat", 0))
        return (time.time() - beat) < timeout
    except Exception:
        return False
