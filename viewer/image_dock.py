# viewer/image_dock.py — ImageDock: Qt工具窗口 (Server端)
import socket
import json
import threading
import sys
import os
import json as json_mod
from collections import OrderedDict
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QLabel, QScrollArea
)
from PySide6.QtCore import Qt, Signal, QObject, QRunnable, QThreadPool, Slot
from PySide6.QtGui import QPixmap, QImage


class DockSignals(QObject):
    """Qt信号"""
    image_loaded = Signal(int, object)  # index, QPixmap
    log_message = Signal(str)
    load_images = Signal(list)  # 图片路径列表


class ImageLoader(QRunnable):
    """后台图片加载器 - 支持本地和局域网NAS路径，自动修复EXIF旋转"""
    def __init__(self, index, path, signals, cache):
        super().__init__()
        self.index = index
        self.path = path
        self.signals = signals
        self.cache = cache

    @Slot()
    def run(self):
        try:
            if self.path in self.cache:
                self.signals.image_loaded.emit(self.index, self.cache[self.path])
                return

            pixmap = self._load_with_pil()
            if pixmap.isNull():
                pixmap = QPixmap(self.path)

            if not pixmap.isNull():
                self.signals.image_loaded.emit(self.index, pixmap)
            else:
                self.signals.log_message.emit(f"加载失败: {self.path}")
        except Exception as e:
            self.signals.log_message.emit(f"加载错误: {e}")

    def _load_with_pil(self):
        """用PIL加载，自动修复EXIF旋转"""
        try:
            from PIL import Image
            from io import BytesIO
            img = Image.open(self.path)
            try:
                exif = img._getexif()
                if exif:
                    orientation = exif.get(274)
                    if orientation in (3, 6, 8):
                        rotate_map = {3: 180, 6: 270, 8: 90}
                        img = img.rotate(rotate_map[orientation], expand=True)
            except:
                pass
            buf = BytesIO()
            img.save(buf, format='PNG')
            qimg = QImage.fromData(buf.getvalue())
            if not qimg.isNull():
                return QPixmap.fromImage(qimg)
        except:
            pass
        return QPixmap()


class ImageDock(QMainWindow):
    """ImageDock: Windows原生Qt工具窗口"""

    HOST = '127.0.0.1'
    PORT = 56789

    def __init__(self):
        super().__init__()
        self.setWindowTitle("图片工作台")
        self.setMinimumSize(300, 400)
        self.resize(600, 800)
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint | Qt.Tool)

        self.signals = DockSignals()
        self.signals.image_loaded.connect(self._on_image_loaded)
        self.signals.log_message.connect(self._on_log)
        self.signals.load_images.connect(self._load_images)

        self.thread_pool = QThreadPool()
        self.image_labels = []
        self.current_images = []
        self.image_cache = OrderedDict()  # LRU缓存
        self.cache_max = 50
        self.settings_file = os.path.join(os.path.expanduser('~'), '.imagedock_settings.json')

        self._setup_ui()
        self._load_settings()
        self._start_server()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        self.status_label = QLabel("就绪 - 等待连接...")
        self.status_label.setStyleSheet("color: #666; font-size: 11px; padding: 2px;")
        main_layout.addWidget(self.status_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_layout.addWidget(self.scroll_area)

        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(4)
        self.scroll_area.setWidget(self.content_widget)

        self.setStyleSheet("""
            QMainWindow { background-color: #f5f5f5; }
            QScrollArea { border: none; background-color: #f5f5f5; }
        """)

    def _start_server(self):
        """启动Socket Server"""
        def server_loop():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((self.HOST, self.PORT))
                    s.listen(1)
                    self.signals.log_message.emit(f"Server监听: {self.HOST}:{self.PORT}")
                    while True:
                        conn, addr = s.accept()
                        threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()
            except Exception as e:
                self.signals.log_message.emit(f"Server失败: {e}")
        threading.Thread(target=server_loop, daemon=True).start()

    def _handle_client(self, conn):
        try:
            with conn:
                length_bytes = conn.recv(4)
                if not length_bytes:
                    return
                length = int.from_bytes(length_bytes, 'big')
                data = b''
                while len(data) < length:
                    chunk = conn.recv(min(4096, length - len(data)))
                    if not chunk:
                        break
                    data += chunk
                message = json.loads(data.decode('utf-8'))
                self._process_message(message)
        except Exception as e:
            self.signals.log_message.emit(f"处理失败: {e}")

    def _process_message(self, message):
        """处理消息"""
        msg_type = message.get('type', '')
        if msg_type == 'update_images':
            sample_id = message.get('sample_id', '')
            current_index = message.get('current_index', 0)
            total = message.get('total', 0)
            images = message.get('images', [])
            self.signals.log_message.emit(f"收到: {sample_id} ({current_index}/{total})")
            self.signals.load_images.emit(images)

    def _load_images(self, images):
        """加载图片列表"""
        # 清除旧内容
        self.image_labels.clear()
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self.current_images = images

        # 检查缓存命中数量
        cached_count = sum(1 for p in images if p in self.image_cache)
        if cached_count == len(images):
            self.status_label.setText(f"{len(images)} 张图片 - 缓存命中")
        else:
            self.status_label.setText(f"{len(images)} 张图片 - 加载中...")

        # 创建标签
        for i, path in enumerate(images):
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumSize(100, 100)
            self.image_labels.append(label)
            self.content_layout.addWidget(label)

            # 优先使用缓存
            if path in self.image_cache:
                self._show_image(i, self.image_cache[path])
            else:
                # 后台加载
                loader = ImageLoader(i, path, self.signals, self.image_cache)
                self.thread_pool.start(loader)

    @Slot(int, object)
    def _on_image_loaded(self, index, pixmap):
        """图片加载完成"""
        if index < len(self.image_labels):
            # 存入缓存
            if index < len(self.current_images):
                path = self.current_images[index]
                self.image_cache[path] = pixmap
                if len(self.image_cache) > self.cache_max:
                    self.image_cache.popitem(last=False)
            self._show_image(index, pixmap)

        if index == len(self.current_images) - 1:
            self.status_label.setText(f"{len(self.current_images)} 张图片 - 完成")

    def _show_image(self, index, pixmap):
        """显示图片（保持比例缩放）"""
        if index < len(self.image_labels):
            label = self.image_labels[index]
            available_width = max(100, self.scroll_area.width() - 20)
            scaled = pixmap.scaled(
                available_width, available_width * 2,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            label.setPixmap(scaled)
            label.setFixedHeight(scaled.height())

    def _on_log(self, msg):
        self.status_label.setText(msg)

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def _relayout_images(self):
        pass

    def _load_settings(self):
        """加载窗口设置"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    settings = json_mod.load(f)
                self.resize(settings.get('width', 600), settings.get('height', 800))
                self.move(settings.get('x', 100), settings.get('y', 100))
        except:
            pass

    def _save_settings(self):
        """保存窗口设置"""
        try:
            settings = {
                'x': self.x(),
                'y': self.y(),
                'width': self.width(),
                'height': self.height()
            }
            with open(self.settings_file, 'w') as f:
                json_mod.dump(settings, f)
        except:
            pass

    def closeEvent(self, event):
        self._save_settings()
        event.ignore()
        self.hide()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._save_settings()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    dock = ImageDock()
    dock.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
