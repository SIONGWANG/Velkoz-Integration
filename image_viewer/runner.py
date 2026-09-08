# runner.py — 独立图片查看器子进程入口
"""以模块方式运行：python -m image_viewer.runner
由主程序通过 subprocess.Popen 启动。负责：
- 创建 QApplication + ImageViewerWindow
- 读取命令文件、周期心跳、监视命令文件实现热更新
- 窗口关闭时释放锁
"""
import sys
import os

from PySide6.QtCore import QTimer, QFileSystemWatcher, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from . import protocol
from .window import ImageViewerWindow

CMD_CHANGE_HANDLE_MS = 300


class ViewerRunner:
    def __init__(self, app):
        self.app = app
        self.window = ImageViewerWindow(protocol.read_cmd() or {})
        self._have_lock = False
        self._heartbeat_timer = None
        self._watcher = None

    def start(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.acquire_lock()
        self._start_heartbeat()
        self._start_watch()
        # 窗口销毁 / 应用退出双保险释放锁
        self.window.destroyed.connect(self.shutdown)
        self.app.aboutToQuit.connect(self.shutdown)

    def acquire_lock(self):
        # 若已有存活实例锁（本进程对象），刷新即可；否则写新锁
        protocol.write_lock(os.getpid())
        self._have_lock = True

    def _start_heartbeat(self):
        self._heartbeat_timer = QTimer()
        self._heartbeat_timer.timeout.connect(protocol.heartbeat)
        self._heartbeat_timer.start(int(protocol.HEARTBEAT_INTERVAL * 1000))

    def _start_watch(self):
        # 监视命令文件变化 → 热更新图片列表 / 当前索引。
        # 原子替换会创建新 inode，需在变更后重新添加文件；另加轮询兜底，
        # 避免 Windows 文件监视在 rename 时丢失事件。
        self._watcher = QFileSystemWatcher(self.app)
        self._watcher.fileChanged.connect(self._on_cmd_changed)
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll_cmd)
        self._poll_timer.start(int(CMD_CHANGE_HANDLE_MS))
        self._watch_cmd_path()

    def _watch_cmd_path(self):
        path = protocol.cmd_path()
        if not os.path.isfile(path):
            os.makedirs(protocol.runtime_dir(), exist_ok=True)
            try:
                protocol.write_cmd(protocol.read_cmd() or {"images": []})
            except Exception:
                pass
            path = protocol.cmd_path()
        self._watcher.addPath(path)
        self._cmd_sig = self._sig_of(path)

    def _sig_of(self, path):
        try:
            st = os.stat(path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _on_cmd_changed(self, path):
        # 原子替换后旧路径失效 → 重新监听
        self._watcher.removePath(path)
        self._watch_cmd_path()
        self._apply_cmd()

    def _poll_cmd(self):
        path = protocol.cmd_path()
        sig = self._sig_of(path)
        if sig is not None and sig != getattr(self, "_cmd_sig", None):
            self._cmd_sig = sig
            self._apply_cmd()

    def _apply_cmd(self):
        cmd = protocol.read_cmd()
        if not cmd:
            return
        images = cmd.get("images", [])
        idx = cmd.get("current_index", 0)
        self.window.set_images(images, idx)
        # 若附带快捷键更新，则应用
        if cmd.get("shortcuts"):
            self.window.set_shortcuts(cmd["shortcuts"])
        # 若主程序请求置于前台（再次打开时唤醒）
        if cmd.get("activate"):
            self.window.bring_to_front()

    def shutdown(self):
        if self._have_lock:
            protocol.release_lock()
            self._have_lock = False


def main():
    # 高DPI 原生渲染：使用整数缩放比例，避免 125%/150% 缩放导致图像发虚
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass
    # 子进程内确保无重复 QApplication（独立进程必然无；保险处理）
    app = QApplication.instance() or QApplication(sys.argv)
    runner = ViewerRunner(app)
    runner.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
