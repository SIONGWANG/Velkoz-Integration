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
from . import config as viewer_config
from . import branding

RESOLUTION = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

# 缩放上下限（相对 100%）与每档步进
ZOOM_MIN = 0.05
ZOOM_MAX = 8.0
ZOOM_STEP_IN = 1.25
ZOOM_STEP_OUT = 1 / 1.25

CANVAS_BG = QColor(30, 30, 32)


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
        # 自适应渲染质量，初始为平滑（缩小时）
        self._apply_quality()

    # ── 自适应渲染质量 ──
    def _apply_quality(self):
        """依据当前缩放比例切换渲染策略：
        - 放大到 >=100%：关闭平滑，像素级清晰（边缘锐利）
        - 缩小到 <100%：开启平滑下采样，避免粗糙锯齿
        -->
        Real image data is always used (no permanent resize); only the
        display hint changes."""
        z = self.transform().m11()
        if z >= 0.98:
            self.setRenderHint(QPainter.SmoothPixmapTransform, False)
        else:
            self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setRenderHint(QPainter.Antialiasing, True)

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
        self._apply_quality()
        self._owner.update_status(self._image_size, self.transform().m11())


class ImageViewerWindow(QMainWindow):
    """独立图片查看器主窗口。"""

    def __init__(self, cmd=None):
        super().__init__()
        self._cmd = cmd or {}
        self._images = self._cmd.get("images", []) or []
        self._current = int(self._cmd.get("current_index", 0))
        self._on_top = bool(self._cmd.get("on_top", True))
        # 快捷键设置：优先取本次命令携带的，否则从配置文件读取
        self._shortcuts = self._cmd.get("shortcuts") or None
        if not self._shortcuts:
            self._shortcuts = viewer_config.get_viewer_settings()["shortcuts"]
        self._qkeys = viewer_config.qkeys_for(self._shortcuts)

        # 应用图标 + 视觉样式
        self.setWindowIcon(branding.make_app_icon())
        self.setWindowTitle("Velkoz 独立图片查看器")
        self.setMinimumSize(780, 520)
        self.resize(1180, 800)
        self.setStyleSheet(branding.window_qss())

        # ── 画布 ──
        self.canvas = ImageCanvas(self)
        self.setCentralWidget(self.canvas)

        # ── 工具栏 ──
        self._build_toolbar()

        # ── 状态栏 ──
        self._build_statusbar()

        # 应用快捷键（覆盖硬编码默认值）
        self._apply_shortcuts()

        # 置顶（可在打开时切换）
        if self._on_top:
            self._set_topmost(True)

        # 打开后居中
        self._load_current()

    # ── 快捷键 ──
    def _apply_shortcuts(self):
        """把配置的快捷键绑定到对应 action / 键位。"""
        qk = self._qkeys
        mapping = {
            "prev": self.act_prev,
            "next": self.act_next,
            "zoom_in": self.act_zoomin,
            "zoom_out": self.act_zoomout,
            "fit": self.act_fit,
            "hundred": self.act_100,
            "close": self.act_quit,
        }
        for key, act in mapping.items():
            seq = qk.get(key)
            if seq:
                try:
                    act.setShortcut(seq)
                except Exception:
                    pass
            # 更新工具栏提示
            if key in ("prev", "next", "zoom_in", "zoom_out", "fit", "hundred", "close"):
                act = mapping[key]
                try:
                    act.setToolTip(f"{act.text()} ({seq})" if seq else act.text())
                except Exception:
                    pass

    def set_shortcuts(self, shortcuts):
        self._shortcuts = shortcuts or viewer_config.DEFAULT_SHORTCUTS
        self._qkeys = viewer_config.qkeys_for(self._shortcuts)
        self._apply_shortcuts()

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
        self._zoom_label.setAlignment(Qt.AlignCenter)
        self._zoom_label.setStyleSheet(
            "color:#8b5cf6;font-weight:600;font-size:14px;padding:2px 12px;"
            "border:1px solid #6366f1;border-radius:11px;background:rgba(99,102,241,0.12);"
        )
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
        status.setStyleSheet(branding.window_qss())
        self.setStatusBar(status)
        self.status_index = QLabel("— / —")
        self.status_res = QLabel("—")
        self.status_zoom_lbl = QLabel("—")
        self.status_names = QLabel("")
        # 分辨率标签用淡色强调
        self.status_res.setStyleSheet("color:#9aa4f2;")
        self.status_zoom_lbl.setStyleSheet("color:#8b5cf6;font-weight:600;padding-right:14px;")
        # 左下角小标
        self._brand_lbl = QLabel("👁 Velkoz")
        self._brand_lbl.setStyleSheet("color:#6b7280;padding:0 8px;")
        status.addWidget(self._brand_lbl, 0)
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
