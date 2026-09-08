# window.py — 独立图片查看器窗口（Phase 1）
"""负责：原图加载（不 resize）、Fit、100%、滚轮缩放（以光标为中心）、右键平移、状态栏。
本窗口为独立 QMainWindow，运行在 subprocess 中，不依赖主程序 / 现有 viewer/ 的任何 UI 状态。
"""
import os

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPixmap, QPainter, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QStatusBar, QLabel, QApplication,
)

from . import coords

RESOLUTION = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

# 缩放上下限（相对于 100%）与每档步进
ZOOM_MIN = 0.05
ZOOM_MAX = 8.0
ZOOM_STEP_IN = 1.25
ZOOM_STEP_OUT = 1 / 1.25


class ImageCanvas(QGraphicsView):
    """承载原图的画布：缩放、右键平移、状态回调。"""

    def __init__(self, owner):
        super().__init__()
        self._owner = owner
        self._image_size = (0, 0)
        self._img_path = None
        self._view_zoom = 1.0
        self._panning = False

        self.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setFocusPolicy(Qt.StrongFocus)

        self._scene = QGraphicsScene(self)
        self._item = QGraphicsPixmapItem()
        self._scene.addItem(self._item)
        self.setScene(self._scene)
        self.setAcceptDrops(False)

    # ── 图片加载 ──
    def load_image(self, path):
        if not path or not os.path.isfile(path):
            return False, f"图片不存在或无法读取：{path}"
        pm = QPixmap(path)
        if pm.isNull():
            return False, f"无法解码图片：{path}"
        self._item.setPixmap(pm)
        self._img_path = path
        self._image_size = (pm.width(), pm.height())
        self._scene.setSceneRect(QRectF(0, 0, pm.width(), pm.height()))
        self.resetTransform()
        self._view_zoom = 1.0
        self.fit_to_window()
        return True, ""

    def can_load(self):
        return self._img_path is not None

    def image_size(self):
        return self._image_size

    # ── 缩放 ──
    def fit_to_window(self):
        if not self._item.pixmap().isNull():
            self.fitInView(self._item, Qt.KeepAspectRatio)
            self._view_zoom = self.transform().m11()
            self._notify_status()

    def set_100(self):
        if self.can_load():
            self.resetTransform()
            self._view_zoom = 1.0
            self._notify_status()

    def zoom_in(self):
        self._apply_zoom(ZOOM_STEP_IN)

    def zoom_out(self):
        self._apply_zoom(ZOOM_STEP_OUT)

    def _apply_zoom(self, factor):
        if not self.can_load():
            return
        current = self.transform().m11()
        # 限制在 [ZOOM_MIN, ZOOM_MAX] 范围（相对 100%）
        target = current * factor
        if target < ZOOM_MIN or target > ZOOM_MAX:
            return
        self.scale(factor, factor)
        self._view_zoom = self.transform().m11()
        self._notify_status()

    # ── 事件：滚轮缩放 / 右键平移 ──
    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_in()
        elif delta < 0:
            self.zoom_out()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._panning = True
            self._last_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._last_pos
            self._last_pos = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self._panning = False
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.ZoomIn):
            self.zoom_in()
            return
        if event.matches(QKeySequence.ZoomOut):
            self.zoom_out()
            return
        super().keyPressEvent(event)

    def _notify_status(self):
        self._owner.update_status(self._image_size, self._view_zoom)


class ImageViewerWindow(QMainWindow):
    """独立图片查看器主窗口。"""

    def __init__(self, cmd=None):
        super().__init__()
        self._cmd = cmd or {}
        self.setWindowTitle("Velkoz 独立图片查看器")
        self.resize(1100, 760)

        self.canvas = ImageCanvas(self)
        self.setCentralWidget(self.canvas)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status_index = QLabel("—")
        self.status_res = QLabel("—")
        self.status_zoom = QLabel("—")
        status.addPermanentWidget(self.status_index)
        status.addPermanentWidget(self.status_res)
        status.addPermanentWidget(self.status_zoom)

        # 图片列表状态
        self._images = self._cmd.get("images", []) or []
        self._current = self._cmd.get("current_index", 0)
        self._load_current()

    # ── 图片加载与切换 ──
    def _load_current(self):
        if not self._images:
            self._set_empty()
            return
        if self._current < 0:
            self._current = 0
        if self._current >= len(self._images):
            self._current = len(self._images) - 1
        path = self._images[self._current]
        ok, msg = self.canvas.load_image(path)
        if not ok:
            self._set_empty(msg)
        self._update_title()
        self.update_status(self.canvas.image_size(), self.canvas.transform().m11())

    def _set_empty(self, msg=""):
        self._images = []
        self.canvas.load_image("")
        self.canvas._image_size = (0, 0)
        self.status_index.setText("无图片")
        self.status_res.setText("—")
        self.status_zoom.setText("—")
        if msg:
            self.statusBar().showMessage(msg, 8000)

    def _update_title(self):
        if self._images:
            idx = self._current + 1
            name = os.path.basename(self._images[self._current]) if self._images else ""
            self.setWindowTitle(f"Velkoz 独立图片查看器 — {idx}/{len(self._images)}  {name}")

    def set_images(self, images, current_index=0):
        self._images = list(images or [])
        self._current = current_index
        self._load_current()

    # ── 状态栏 ──
    def update_status(self, image_size, zoom):
        if self._images:
            idx = self._current + 1
            total = len(self._images)
            self.status_index.setText(f"{idx}/{total}")
        else:
            self.status_index.setText("—")
        w, h = image_size
        if w and h:
            self.status_res.setText(f"{w}×{h}")
        else:
            self.status_res.setText("—")
        self.status_zoom.setText(f"{zoom * 100:.0f}%")

    def set_current_index(self, index):
        self._current = int(index)
        self._load_current()
