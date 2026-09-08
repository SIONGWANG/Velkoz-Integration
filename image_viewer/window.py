# window.py — 独立图片查看器窗口
"""负责：原图加载（不 resize）、Fit、100%、滚轮缩放（以光标为中心）、右键平移、
图片切换、状态栏、工具栏与快捷键。
本窗口为独立 QMainWindow，运行在 subprocess 中，不依赖主程序 / 现有 viewer/ 的任何 UI 状态。
"""
import os

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QPixmap, QPainter, QKeySequence, QColor, QBrush, QIcon, QAction
from PySide6.QtWidgets import (
    QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QStatusBar, QLabel, QToolBar, QApplication, QStyle,
)

from . import protocol

RESOLUTION = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

# 缩放上下限（相对 100%）与每档步进
ZOOM_MIN = 0.05
ZOOM_MAX = 8.0
ZOOM_STEP_IN = 1.25
ZOOM_STEP_OUT = 1 / 1.25

CANVAS_BG = QColor(30, 30, 32)
TOOLBAR_QSS = """
QToolBar {
    background: #2b2b2d;
    border: none;
    padding: 4px;
    spacing: 4px;
}
QToolButton {
    color: #e8e8e8;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 5px 10px;
    font-size: 14px;
}
QToolButton:hover { background: #3d3d40; }
QToolButton:pressed { background: #4a4a4e; }
QToolBar::separator { background: #4a4a4e; width: 1px; margin: 4px 4px; }
"""
STATUS_QSS = """
QStatusBar {
    background: #1e1e20;
    color: #c8c8c8;
    border-top: 1px solid #333;
    font-size: 13px;
}
QStatusBar QLabel { color: #c8c8c8; padding: 0 10px; }
"""


class ImageCanvas(QGraphicsView):
    """承载原图的画布：缩放、右键平移、状态回调。"""

    def __init__(self, owner):
        super().__init__()
        self._owner = owner
        self._image_size = (0, 0)
        self._img_path = None
        self._panning = False

        self.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setBackgroundBrush(QBrush(CANVAS_BG))

        self._scene = QGraphicsScene(self)
        self._item = QGraphicsPixmapItem()
        self._scene.addItem(self._item)
        self.setScene(self._scene)
        self.setAcceptDrops(False)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

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
            self._notify_status()

    def set_100(self):
        if self.can_load():
            self.resetTransform()
            self._notify_status()

    def zoom_in(self):
        self._apply_zoom(ZOOM_STEP_IN)

    def zoom_out(self):
        self._apply_zoom(ZOOM_STEP_OUT)

    def _apply_zoom(self, factor):
        if not self.can_load():
            return
        current = self.transform().m11()
        target = current * factor
        if target < ZOOM_MIN or target > ZOOM_MAX:
            return
        self.scale(factor, factor)
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

    def _notify_status(self):
        self._owner.update_status(self._image_size, self.transform().m11())


class ImageViewerWindow(QMainWindow):
    """独立图片查看器主窗口。"""

    def __init__(self, cmd=None):
        super().__init__()
        self._cmd = cmd or {}
        self._images = self._cmd.get("images", []) or []
        self._current = int(self._cmd.get("current_index", 0))
        self._on_top = bool(self._cmd.get("on_top", True))

        self.setWindowTitle("Velkoz 独立图片查看器")
        self.setMinimumSize(780, 520)
        self.resize(1180, 800)
        self.setStyleSheet(TOOLBAR_QSS)

        # ── 画布 ──
        self.canvas = ImageCanvas(self)
        self.setCentralWidget(self.canvas)

        # ── 工具栏 ──
        self._build_toolbar()

        # ── 状态栏 ──
        self._build_statusbar()

        # 置顶（可在打开时切换）
        if self._on_top:
            self._set_topmost(True)

        # 打开后居中
        self._load_current()

    # ── UI 构建 ──
    def _build_toolbar(self):
        tb = QToolBar("查看")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)

        style = self.style()
        ic_prev = style.standardIcon(QStyle.SP_ArrowLeft)
        ic_next = style.standardIcon(QStyle.SP_ArrowRight)
        ic_zoomin = style.standardIcon(QStyle.SP_TitleBarMaxButton)
        ic_zoomout = style.standardIcon(QStyle.SP_TitleBarMinButton)
        ic_fit = style.standardIcon(QStyle.SP_TitleBarNormalButton)

        self.act_prev = QAction(ic_prev, "上一张", self)
        self.act_prev.setToolTip("上一张 (Left)")
        self.act_prev.setShortcut("Left")
        self.act_prev.triggered.connect(self.goto_prev)
        tb.addAction(self.act_prev)

        self.act_next = QAction(ic_next, "下一张", self)
        self.act_next.setToolTip("下一张 (Right)")
        self.act_next.setShortcut("Right")
        self.act_next.triggered.connect(self.goto_next)
        tb.addAction(self.act_next)

        tb.addSeparator()

        self.act_zoomout = QAction(ic_zoomout, "缩小", self)
        self.act_zoomout.setToolTip("缩小 (-)")
        self.act_zoomout.setShortcut("-")
        self.act_zoomout.triggered.connect(self.canvas.zoom_out)
        tb.addAction(self.act_zoomout)

        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet("color:#e8e8e8;padding:0 10px;")
        tb.addWidget(self._zoom_label)

        self.act_zoomin = QAction(ic_zoomin, "放大", self)
        self.act_zoomin.setToolTip("放大 (+ / =)")
        self.act_zoomin.setShortcut("=")
        self.act_zoomin.triggered.connect(self.canvas.zoom_in)
        tb.addAction(self.act_zoomin)

        tb.addSeparator()

        self.act_fit = QAction(ic_fit, "适应窗口", self)
        self.act_fit.setToolTip("适应窗口 (0)")
        self.act_fit.setShortcut("0")
        self.act_fit.triggered.connect(self.canvas.fit_to_window)
        tb.addAction(self.act_fit)

        self.act_100 = QAction("1:1", self)
        self.act_100.setToolTip("实际大小 (1)")
        self.act_100.setShortcut("1")
        self.act_100.triggered.connect(self.canvas.set_100)
        tb.addAction(self.act_100)

        self.act_quit = QAction("✕ 关闭 (Esc)", self)
        self.act_quit.setShortcut("Esc")
        self.act_quit.triggered.connect(self.close)
        tb.addSeparator()
        tb.addAction(self.act_quit)

    def _build_statusbar(self):
        status = QStatusBar()
        status.setStyleSheet(STATUS_QSS)
        self.setStatusBar(status)
        self.status_index = QLabel("— / —")
        self.status_res = QLabel("—")
        self.status_zoom_lbl = QLabel("—")
        self.status_names = QLabel("")
        status.addWidget(self.status_names, 1)
        status.addPermanentWidget(self.status_index)
        status.addPermanentWidget(self.status_res)
        status.addPermanentWidget(self.status_zoom_lbl)

    def _set_topmost(self, on):
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    # ── 图片加载与切换 ──
    def _load_current(self):
        if not self._images:
            self._set_empty("")
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
        self.canvas.load_image("")
        self.canvas._image_size = (0, 0)
        self.status_index.setText("— / —")
        self.status_res.setText("—")
        self.status_zoom_lbl.setText("—")
        if msg:
            self.statusBar().showMessage(msg, 8000)

    def _update_title(self):
        if self._images:
            idx = self._current + 1
            name = os.path.basename(self._images[self._current]) if self._images else ""
            self.setWindowTitle(f"Velkoz 独立图片查看器 — {idx}/{len(self._images)}  {name}")

    def set_images(self, images, current_index=0):
        self._images = list(images or [])
        self._current = int(current_index or 0)
        self._load_current()

    def goto_prev(self):
        if not self._images:
            return
        if self._current > 0:
            self._current -= 1
            self._load_current()

    def goto_next(self):
        if not self._images:
            return
        if self._current < len(self._images) - 1:
            self._current += 1
            self._load_current()

    # ── 状态栏 ──
    def update_status(self, image_size, zoom):
        if self._images:
            idx = self._current + 1
            total = len(self._images)
            name = os.path.basename(self._images[self._current]) if self._images else ""
            self.status_index.setText(f"{idx} / {total}")
            self.status_names.setText(name)
        else:
            self.status_index.setText("— / —")
            self.status_names.setText("")
        w, h = image_size
        if w and h:
            self.status_res.setText(f"{w}px × {h}px")
        else:
            self.status_res.setText("—")
        self.status_zoom_lbl.setText(f"{zoom * 100:.0f}%")
        self._zoom_label.setText(f"{zoom * 100:.0f}%")

    def set_current_index(self, index):
        self._current = int(index)
        self._load_current()

    def bring_to_front(self):
        self.show()
        self.raise_()
        self.activateWindow()

    # ── 关闭：立即释放锁，让主程序可快速重开 ──
    def closeEvent(self, event):
        try:
            protocol.release_lock()
        except Exception:
            pass
        super().closeEvent(event)
