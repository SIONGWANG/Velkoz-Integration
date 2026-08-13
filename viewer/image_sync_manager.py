# viewer/image_sync_manager.py — ImageSyncManager: 主程序与 ImageDock 的同步管理
# 方案 A（单进程内嵌）：由 DockManager 后台线程托管 Qt，Queue 桥接，无 Socket。
# 保留 send() Socket 兼容层备查（方案 B 已存档，不主动使用）。
import logging
from typing import Dict

from viewer.dock_manager import get_dock_manager, is_dock_available


class ImageSyncManager:
    """ImageSyncManager: 进程内同步主程序当前样本到 ImageDock。

    使用方式保持不变：update_images(sample_id, index, total, images)。
    依赖 PySide6：未安装时所有调用静默降级，不报错不卡顿。
    """

    HOST = '127.0.0.1'
    PORT = 56789  # 保留常量，供病情诊断/日志，Socket 已不再使用

    def __init__(self):
        self._dock = get_dock_manager()

    # ── 进程内主路径 ──

    def available(self) -> bool:
        """悬浮窗功能是否可用（PySide6 是否安装）。"""
        return is_dock_available()

    def update_images(self, sample_id: str, current_index: int, total: int, images: list) -> bool:
        """推送当前样本图片到悬浮窗（进程内，非阻塞）。"""
        try:
            return self._dock.sync_images(sample_id, current_index, total, images)
        except Exception as e:
            logging.warning("ImageDock 同步失败: %s", e)
            return False

    def open_dock(self) -> bool:
        """显示悬浮窗并立即同步当前样本。"""
        ok = self._dock.show()
        if ok:
            self.sync_current_sample()
        return ok

    def close_dock(self) -> bool:
        """隐藏悬浮窗（保留后台线程）。"""
        return self._dock.hide()

    def toggle_dock(self):
        """切换悬浮窗显隐。"""
        if not is_dock_available():
            return False
        if self._dock._visible:
            return self.close_dock()
        return self.open_dock()

    def sync_current_sample(self):
        """把当前的 session 状态（若有）重新同步过去。"""
        try:
            import streamlit as st
            synced = False
            for k, v in st.session_state.items():
                if k == "_dock_sample":
                    sample = v
                    synced = True
                    self.update_images(sample.get("sample_id", ""),
                                       sample.get("current_index", 0),
                                       sample.get("total", 0),
                                       sample.get("images", []))
                    break
            if not synced:
                self.update_images("", 0, 0, [])
        except Exception as e:
            logging.warning("同步当前样本失败: %s", e)

    # ── 兼容层（方案 B 存档，不主动使用） ──

    def send(self, message: Dict) -> bool:
        """（已弃用）Socket 发送。保留以兼容旧日志/外部调用。"""
        try:
            import socket
            import json
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect((self.HOST, self.PORT))
                data = json.dumps(message, ensure_ascii=False).encode('utf-8')
                s.sendall(len(data).to_bytes(4, 'big'))
                s.sendall(data)
                return True
        except Exception:
            return False


_manager_instance = None


def get_sync_manager() -> ImageSyncManager:
    """获取 ImageSyncManager 单例"""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = ImageSyncManager()
    return _manager_instance