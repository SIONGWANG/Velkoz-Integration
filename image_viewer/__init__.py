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


def open_image_viewer(images, current_index=0, sample_id="", total=None, on_top=True, shortcuts=None,
                      data_root=None, evidence_folder=None):
    """打开（或复用）独立图片查看器。

    Args:
        images: 绝对路径的图片列表
        current_index: 当前图片索引（从 0 开始）
        sample_id: 当前样本/记录ID（用于显示与后续上传关系）
        total: 图片总数（默认取 len(images)）
        on_top: 打开时置顶显示
        shortcuts: 快捷键映谢 dict（None 则查看器读取配置文件）
        data_root: 当前质检数据根目录（截图最终保存位置基准）
        evidence_folder: 截图证据文件夹名称（如 _00_Evidence_新标）
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
    if data_root:
        payload["data_root"] = data_root
    if evidence_folder:
        payload["evidence_folder"] = evidence_folder
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
        # 子进程 stderr 写到日志文件，便于定位启动失败原因
        log_path = protocol.log_path()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as logf:
            subprocess.Popen(
                _subprocess_cmd(),
                cwd=PROJECT_ROOT,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0),
                stdin=subprocess.DEVNULL,
                stdout=logf,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
        # 等待心跳，确认子进程已真正起来（避免"看似成功实则失败"）
        import time as _time
        for _ in range(30):
            if protocol.is_viewer_alive():
                return True, ""
            _time.sleep(0.2)
        return False, "查看器启动超时，请查看日志或关闭残留进程后重试"
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
