# viewer/dock_manager.py — DockManager: 进程内托管 ImageDock（方案 A）
# 由主程序后台线程维护 PySide6 事件循环，通过 queue.Queue 桥接主程序，
# 彻底消除进程顺序依赖、端口冲突、重连问题。
import threading
import queue
import logging
import os

_DOCK_AVAILABLE = None


def is_dock_available():
    """检查 PySide6 是否可用（只探测一次）。"""
    global _DOCK_AVAILABLE
    if _DOCK_AVAILABLE is None:
        try:
            from PySide6 import __version__  # noqa: F401
            _DOCK_AVAILABLE = True
        except Exception:
            _DOCK_AVAILABLE = False
    return _DOCK_AVAILABLE


class DockManager:
    """模块级单例：后台线程运行 QApplication + ImageDock，队列桥接更新。

    线程安全：主程序任意时刻调用 post/send 均立即返回，不阻塞。
    Dock 未运行时（PySide6 缺失或线程未启动）所有调用静默降级。
    """

    def __init__(self):
        self._queue = queue.Queue(maxsize=32)
        self._thread = None
        self._app = None
        self._dock = None
        self._started = False
        self._lock = threading.Lock()
        self._visible = False

    # ── 生命周期 ──

    def ensure_started(self):
        """确保后台 Qt 线程已启动（幂等）。返回当前是否可用。"""
        if not is_dock_available():
            return False
        with self._lock:
            if self._started:
                return self.is_alive()
            self._started = True
        try:
            self._thread = threading.Thread(target=self._run_qt, name="imagedock-qt", daemon=True)
            self._thread.start()
        except Exception as e:
            logging.warning("ImageDock 线程启动失败: %s", e)
            self._started = False
            return False
        return True

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def _run_qt(self):
        """后台线程：创建 QApplication + Dock，跑 Qt 事件循环，轮询队列。"""
        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import QTimer
            from viewer.image_dock import ImageDock
        except Exception as e:
            logging.warning("ImageDock 初始化失败: %s", e)
            return
        try:
            self._app = QApplication.instance() or QApplication([])
            self._dock = ImageDock(server_mode=False)
            self._dock.hide()

            def poll():
                try:
                    while True:
                        item = self._queue.get_nowait()
                        self._dispatch(item)
                except queue.Empty:
                    pass

            timer = QTimer()
            timer.timeout.connect(poll)
            timer.start(50)
            self._app.exec()
        except Exception as e:
            logging.warning("ImageDock 事件循环异常: %s", e)

    def _dispatch(self, item):
        """处理队列中的指令（在 Qt 线程内执行）。"""
        try:
            cmd = item.get("cmd")
            if cmd == "update_images" and self._dock is not None:
                self._dock.update_images_inline(
                    item.get("sample_id", ""),
                    item.get("current_index", 0),
                    item.get("total", 0),
                    item.get("images", []),
                )
            elif cmd == "show" and self._dock is not None:
                if not self._visible:
                    self._dock.show()
                    self._dock.raise_()
                    self._visible = True
            elif cmd == "hide" and self._dock is not None:
                self._dock.hide()
                self._visible = False
        except Exception as e:
            logging.warning("ImageDock 指令处理失败: %s", e)

    # ── 对外 API（线程安全，非阻塞） ──

    def sync_images(self, sample_id, current_index, total, images):
        """推送当前样本图片到悬浮窗。返回是否已入队。"""
        if not self.ensure_started():
            return False
        try:
            self._queue.put({
                "cmd": "update_images",
                "sample_id": sample_id,
                "current_index": current_index,
                "total": total,
                "images": images or [],
            }, timeout=0.1)
            return True
        except (queue.Full, Exception):
            return False

    def show(self):
        if not self.ensure_started():
            return False
        try:
            self._queue.put({"cmd": "show"}, timeout=0.1)
            return True
        except (queue.Full, Exception):
            return False

    def hide(self):
        if not self.ensure_started():
            return False
        try:
            self._queue.put({"cmd": "hide"}, timeout=0.1)
            return True
        except (queue.Full, Exception):
            return False

    def toggle(self):
        """切换悬浮窗显示状态，返回操作后是否可见（未知时返回 None）。"""
        if not self._visible:
            self.show()
        else:
            self.hide()
        return None


_dock_manager = None


def get_dock_manager() -> DockManager:
    """获取 DockManager 单例"""
    global _dock_manager
    if _dock_manager is None:
        _dock_manager = DockManager()
    return _dock_manager


__all__ = ["DockManager", "get_dock_manager", "is_dock_available"]