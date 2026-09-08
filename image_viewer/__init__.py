# image_viewer/__init__.py — 主程序侧公开接口
"""独立图片查看器（Phase 1）。

主程序只通过本模块定义的清晰接口打开新 Image Viewer：
    from image_viewer import open_image_viewer, is_viewer_running

实现细节：
- 以 subprocess 启动独立子进程 `python -m image_viewer.runner`
- 通过 protocol 的消息文件传递图片列表 + 当前索引
- 通过锁文件（PID+心跳）保证单实例，重复打开时复用已有窗口
与现有 viewer/（ImageDock）完全隔离，不改动其职责、同步机制或窗口行为。
"""
import os
import sys
import subprocess

from . import protocol

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _python():
    """用于启动子进程的解释器：优先复用当前（打包的）解释器。"""
    exe = getattr(sys, "executable", None)
    if exe and os.path.isfile(exe):
        return exe
    return "python"


def _subprocess_cmd():
    # 用启动脚本而非 -m，绕开打包版 Python 忽略 PYTHONPATH 的问题
    return [_python(), os.path.join(PROJECT_ROOT, "image_viewer", "run.py")]


def is_viewer_running():
    return protocol.is_viewer_alive()


def open_image_viewer(images, current_index=0, sample_id="", total=None, on_top=True, shortcuts=None):
    """打开（或复用）独立图片查看器。

    Args:
        images: 绝对路径的图片列表
        current_index: 当前图片索引（从 0 开始）
        sample_id: 当前样本/记录ID（用于显示与后续上传关系）
        total: 图片总数（默认取 len(images)）
        on_top: 打开时置顶显示
        shortcuts: 快捷键映谢 dict（None 则查看器读取配置文件）
    """
    images = list(images or [])
    if not images:
        return False, "没有可查看的图片"

    shortcuts = shortcuts or None

    payload = {
        "images": images,
        "current_index": int(current_index) if current_index else 0,
        "total": int(total) if total else len(images),
        "sample_id": sample_id,
        "on_top": bool(on_top),
    }
    if shortcuts is not None:
        payload["shortcuts"] = shortcuts

    if protocol.is_viewer_alive():
        # 已存活：更新内容 + 请求前台激活（快速重开/唤醒）
        payload["activate"] = True
        protocol.write_cmd(payload)
        return True, "查看器已在前台运行"

    # 不存在或已退出：启动子进程（先写内容，再拉起窗口）
    protocol.write_cmd(payload)
    try:
        env = dict(os.environ)
        # 确保 subprocess 能 import image_viewer 包
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        subprocess.Popen(
            _subprocess_cmd(),
            cwd=PROJECT_ROOT,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return True, ""
    except Exception as e:
        return False, f"启动图片查看器失败：{e}"


def close_image_viewer():
    """提示查看器退出（写一个空命令 + 释放锁）；失败则忽略。"""
    protocol.release_lock()
    return True


__all__ = [
    "open_image_viewer",
    "is_viewer_running",
    "close_image_viewer",
]
