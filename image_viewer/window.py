# window.py — 独立图片查看器窗口
"""负责：原图加载（不 resize）、Fit、100%、滚轮缩放（以光标为中心）、右键平移、
图片切换、状态栏、工具栏与快捷键。
本窗口为独立 QMainWindow，运行在 subprocess 中，不依赖主程序 / 现有 viewer/ 的任何 UI 状态。
"""
import os

from PySide6.QtCore import Qt, QRectF, QSize, QPointF, QPoint
from PySide6.QtGui import QPixmap, QPainter, QKeySequence, QColor, QBrush, QIcon, QAction, QPen, QShortcut
from PySide6.QtWidgets import (
    QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsRectItem, QStatusBar, QLabel, QToolBar, QApplication, QStyle,
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QMessageBox, QFileDialog, QInputDialog, QComboBox, QSpinBox,
)

from . import protocol
from . import config as viewer_config
from . import branding
from .annotation import Annotation, TOOL_ARROW, TOOL_RECT, TOOL_ELLIPSE, TOOL_PEN, TOOL_TEXT, TOOL_SELECT, ALL_TOOLS

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
        # 截图框选覆盖层（虚线高亮）
        self._sel_item = QGraphicsRectItem()
        pen = QPen(QColor("#8b5cf6"), 2, Qt.DashLine)
        self._sel_item.setPen(pen)
        self._sel_item.setBrush(QBrush(QColor(99, 102, 241, 50)))
        self._sel_item.setZValue(100)
        self._sel_item.setVisible(False)
        self._scene.addItem(self._sel_item)
        self.setScene(self._scene)
        self.setAcceptDrops(False)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

        # 模式状态：view / capture / draw / select
        self._mode = "view"
        self._sel_start = None       # 视图/场景坐标起点
        self._sel_rect = None        # 框选/标注构建结果
        # 标注实时预览（场景坐标，用 ImageCanvas 绘制）
        self._draw_preview = None    # (type, points) 进行中的标注
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

    # ── 事件：滚轮缩放 / 右键平移 / 框选 / 标注 ──
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
        if event.button() == Qt.LeftButton and self._mode in ("capture", "draw", "select"):
            self._on_left_press(event)
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
        if event.buttons() & Qt.LeftButton and self._mode in ("capture", "draw"):
            self._on_left_drag(event)
            event.accept()
            return
        if self._mode == "draw":
            # 悬停时更新画笔预览
            self._on_left_drag(event)
            event.accept()
            return
        if self._mode == "select":
            self._on_select_move(event)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self._panning = False
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._mode in ("capture", "draw", "select"):
            self._on_left_release(event)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ── 模式分发 ──
    def _on_left_press(self, event):
        if self._mode == "capture":
            self._sel_start = self.mapToScene(event.pos())
            self._sel_rect = None
            self._sel_item.setVisible(True)
            self._update_sel_rect(event.pos())
            self.setCursor(Qt.CrossCursor)
            return
        if self._mode == "draw":
            tool = self._owner.current_tool()
            img_pt = self.mapToScene(event.pos())
            if tool == "pen":
                self._draw_preview = ("pen", [img_pt])
            else:
                # arrow/rect/ellipse/text 只需记录起点；text 在 release 输入
                self._draw_preview = (tool, [img_pt])
            self.viewport().update()
            return
        if self._mode == "select":
            self._owner.begin_select(self.mapToScene(event.pos()))
            return

    def _on_left_drag(self, event):
        if self._mode == "capture":
            self._update_sel_rect(event.pos())
            return
        if self._mode == "draw" and self._draw_preview is not None:
            tool, pts = self._draw_preview
            img_pt = self.mapToScene(event.pos())
            if tool == "pen":
                pts.append(img_pt)
            else:
                # 单点锚定：用第二个点作为终点
                if len(pts) >= 1:
                    pts = pts[:1]
                pts.append(img_pt)
                self._draw_preview = (tool, pts)
            self.viewport().update()
            return

    def _on_left_release(self, event):
        if self._mode == "capture":
            rect = self._current_sel_image_rect()
            self._sel_start = None
            self.setCursor(Qt.CrossCursor)
            if rect is None or rect.isNull():
                self._sel_item.setVisible(False)
                self._sel_item.setRect(QRectF())
                self._owner.statusBar().showMessage("截图框选过小，已取消。", 5000)
                return
            self._sel_item.setVisible(False)
            self._sel_item.setRect(QRectF())
            self._owner.on_capture(rect)
            return
        if self._mode == "draw":
            self._finish_draw(event)
            return
        if self._mode == "select":
            self._owner.end_select()
            return

    def _finish_draw(self, event):
        import copy
        tool, pts = self._draw_preview or (None, [])
        self._draw_preview = None
        if not pts:
            return
        # 剔除过小形状（拖拽太短）
        if tool in ("arrow", "rect", "ellipse") and len(pts) >= 2:
            d = (pts[1] - pts[0])
            if abs(d.x()) < 3 and abs(d.y()) < 3:
                self.viewport().update()
                return
        pen_width = self._owner.current_pen_width()
        color = self._owner.current_color()
        font_size = self._owner.current_font_size()
        if tool == "text":
            # 弹出文字输入
            self._owner.add_text_at(pts[0], color, font_size)
            self.viewport().update()
            return
        a = Annotation(type_=tool, points=list(pts), color=color, width=pen_width, font_size=font_size)
        self._owner.add_annotation(a)
        self.viewport().update()

    def _on_select_move(self, event):
        self._owner.move_select_scene(self.mapToScene(event.pos()))

    # ── 截图框选辅助 ──
    def _update_sel_rect(self, view_pos):
        """在场景中绘制当前拖拽框（视图坐标 -> 场景坐标）。"""
        if self._sel_start is None:
            return
        cur = self.mapToScene(view_pos)
        rect = QRectF(self._sel_start, cur).normalized()
        self._sel_item.setRect(rect)

    def _current_sel_image_rect(self):
        """当前框选（场景坐标）换算成原图坐标系 QRect。"""
        if self._sel_item.rect().isNull():
            return None
        from . import capture
        # 直接以场景坐标矩形裁剪即可，场景坐标 == 原图坐标
        r = self._sel_item.rect().normalized()
        return capture.clamp_rect_to_image(r, self._image_size[0], self._image_size[1])

    # ── 模式开关 ──
    def set_mode(self, mode):
        self._mode = mode
        self._draw_preview = None
        self._sel_item.setVisible(False)
        self._sel_item.setRect(QRectF())
        if mode == "capture":
            self.setCursor(Qt.CrossCursor)
        elif mode == "draw":
            self.setCursor(Qt.CrossCursor)
        elif mode == "select":
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)
        self.viewport().update()

    def is_capture_mode(self):
        return self._mode == "capture"

    # ── 标注渲染 ──
    def drawForeground(self, painter, rect):
        """在场景之上绘制标注与进行中的预览（不修改原图）。"""
        owner = self._owner
        # 已提交标注
        anns = owner.get_annotations()
        scale = self.transform().m11()
        if anns:
            from .annotation import draw_annotations
            painter.setRenderHint(QPainter.Antialiasing)
            draw_annotations(painter, anns, scale=scale, in_image_space=True)
        # 进行中的标注预览
        if self._draw_preview:
            tool, pts = self._draw_preview
            if pts:
                from .annotation import Annotation, draw_annotations
                a = Annotation(type_=tool, points=list(pts), color=owner.current_color(),
                               width=owner.current_pen_width(), font_size=owner.current_font_size())
                draw_annotations(painter, [a], scale=scale, in_image_space=True)
        # 选择框高亮（醒目）
        if self._mode == "select":
            sel = owner.get_sel_rect()
            if sel is not None and not sel.isNull():
                # 半透明填充 + 亮绿加粗虚线
                fill = QColor("#22c55e"); fill.setAlpha(40)
                painter.setBrush(QBrush(fill))
                painter.setPen(QPen(QColor("#22c55e"), 3, Qt.DashLine))
                painter.drawRect(sel)
        super().drawForeground(painter, rect)

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

        # 标注状态
        self._annotations = []          # 已提交标注 Annotation 列表
        self._undo_stack = []           # [[Annotation...] 快照]
        self._redo_stack = []
        self._tool = TOOL_RECT          # 当前工具
        self._color = "#ef4444"
        self._pen_width = 8.0           # 原图像素线宽（默认加粗，易于观察）
        self._font_size = 36.0          # 原图像素字号
        self._select_rect = None        # 选择工具的框选矩形（场景坐标）

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
        self._register_shortcuts()

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

    def _register_shortcuts(self):
        """用 QShortcut 注册关键快捷键：即使焦点在画布(QGraphicsView 会吞按键)也能触发。
        撤销 / 重做 / 删除所选。QShortcut 默认上下文为窗口级。"""
        try:
            self._sc_undo = QShortcut(QKeySequence("Ctrl+Z"), self)
            self._sc_undo.activated.connect(self.undo)

            self._sc_redo = QShortcut(QKeySequence("Ctrl+Y"), self)
            self._sc_redo.activated.connect(self.redo)
            # 兼容 Ctrl+Shift+Z
            self._sc_redo2 = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
            self._sc_redo2.activated.connect(self.redo)

            self._sc_del = QShortcut(QKeySequence("Delete"), self)
            self._sc_del.activated.connect(self.delete_selected)
        except Exception as e:
            import logging
            logging.warning("注册查看器快捷键失败: %s", e)

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

        tb.addSeparator()

        # 截图：框选原图区域 -> 从原图裁剪
        self.act_capture = QAction(style.standardIcon(QStyle.SP_DialogSaveButton), "🖼️ 截图", self)
        self.act_capture.setToolTip("截图：框选区域，从原图裁剪（不从屏幕截图）")
        self.act_capture.setCheckable(True)
        self.act_capture.toggled.connect(self._on_capture_toggle)
        tb.addAction(self.act_capture)

        tb.addSeparator()

        # ── 标注工具 ──
        self._tool_actions = {}
        tools = [
            ("select", "⬚ 选择", "选择/移动标注"),
            ("arrow", "↗ 箭头", "画箭头"),
            ("rect", "▭ 矩形", "画矩形"),
            ("ellipse", "◯ 椭圆", "画椭圆"),
            ("pen", "✎ 画笔", "自由画笔"),
            ("text", "T 文字", "添加文字"),
        ]
        for key, label, tip in tools:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setToolTip(tip)
            act.triggered.connect(lambda _=False, k=key: self.set_tool(k))
            tb.addAction(act)
            self._tool_actions[key] = act
        self._tool_actions["select"].setChecked(True)

        # 颜色
        tb.addSeparator()
        self._color_combo = self._build_color_selector()
        tb.addWidget(self._color_combo)

        # 线宽（原图像素）
        lb_w = QLabel("线宽")
        lb_w.setStyleSheet("color:#9aa4f2;padding-left:6px;")
        tb.addWidget(lb_w)
        self._pen_width_spin = self._spin(2, 40, int(self._pen_width), self._on_pen_width)
        tb.addWidget(self._pen_width_spin)

        lb_f = QLabel("字号")
        lb_f.setStyleSheet("color:#9aa4f2;")
        tb.addWidget(lb_f)
        self._font_size_spin = self._spin(8, 200, int(self._font_size), self._on_font_size)
        tb.addWidget(self._font_size_spin)

        tb.addSeparator()

        # 撤销 / 重做（快捷键用 QShortcut 注册，见 _register_shortcuts）
        self.act_undo = QAction("↺ 撤销", self)
        self.act_undo.setToolTip("撤销 (Ctrl+Z)")
        self.act_undo.triggered.connect(self.undo)
        tb.addAction(self.act_undo)

        self.act_redo = QAction("↻ 重做", self)
        self.act_redo.setToolTip("重做 (Ctrl+Y / Ctrl+Shift+Z)")
        self.act_redo.triggered.connect(self.redo)
        tb.addAction(self.act_redo)

        self.act_del = QAction("🗑 删除所选", self)
        self.act_del.setToolTip("删除所选标注 (Delete)")
        self.act_del.triggered.connect(self.delete_selected)
        tb.addAction(self.act_del)

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

    # ── 截图：框选 -> 预览 -> 保存 ──
    def _on_capture_toggle(self, checked):
        # 截图按钮是 checkable，与工具互斥；进入截图模式
        if checked:
            if not self.canvas.can_load():
                self.act_capture.blockSignals(True)
                self.act_capture.setChecked(False)
                self.act_capture.blockSignals(False)
                self.statusBar().showMessage("当前没有可截图的图片。", 5000)
                return
            self._uncheck_tools()
            self.act_capture.setChecked(True)
            self.canvas.set_mode("capture")
            self.setCursor(Qt.CrossCursor)
            self.statusBar().showMessage("截图模式：按住左键框选要截取的原图区域。", 6000)
        else:
            self.canvas.set_mode("view")
            self.setCursor(Qt.OpenHandCursor)

    def on_capture(self, image_rect):
        """画布框选完成后回调：从原图裁剪 -> 叠加标注 -> 预览 -> 保存。"""
        from . import capture
        from .annotation import bake_annotations_onto_crop
        # 退出截图模式（按钮取消选中）
        if self.act_capture.isChecked():
            self.act_capture.blockSignals(True)
            self.act_capture.setChecked(False)
            self.act_capture.blockSignals(False)
        self.canvas.set_mode("view")
        self.setCursor(Qt.OpenHandCursor)

        if not self.canvas.can_load():
            return
        pm = self.canvas._item.pixmap()   # 原始 pixmap（未缩放）
        cropped, err = capture.crop_from_pixmap(pm, image_rect)
        if cropped is None:
            self.statusBar().showMessage(err or "裁剪失败。", 6000)
            return
        # 关键：把当前标注烧录进截图（标注以原图坐标存储，平移到裁剪坐标系）
        annotations = list(self.get_annotations())
        if annotations:
            cropped = bake_annotations_onto_crop(cropped, annotations, image_rect, clip=False)
        self._capture_preview(cropped, image_rect)

    def _capture_preview(self, cropped, image_rect):
        """截图预览对话框：显示裁剪结果 + 保存 / 重新截图 / 取消。"""
        dlg = QDialog(self)
        dlg.setWindowIcon(branding.make_app_icon())
        dlg.setWindowTitle("截图预览")
        dlg.setMinimumSize(480, 420)
        lay = QVBoxLayout(dlg)

        info = QLabel(f"原图选区：x={image_rect.x()}  y={image_rect.y()}  "
                      f"w={image_rect.width()}  h={image_rect.height()}  "
                      f"({image_rect.width()}×{image_rect.height()}px)")
        info.setStyleSheet("color:#e8e8e8;padding:4px 0;")
        lay.addWidget(info)

        from PySide6.QtWidgets import QLabel as L
        img_lbl = L()
        img_lbl.setMinimumSize(420, 320)
        img_lbl.setStyleSheet("background:#121218;border-radius:6px;")
        import PySide6.QtCore as QC
        scaled = cropped
        max_w, max_h = 440, 320
        if scaled.width() > max_w or scaled.height() > max_h:
            scaled = cropped.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img_lbl.setPixmap(scaled)
        lay.addWidget(img_lbl, 1)
        note = QLabel(f"裁剪来源：直接从原图数据裁剪，非屏幕截图。已叠加当前图片的标注。")
        note.setStyleSheet("color:#9aa4f2;")
        lay.addWidget(note, 0)

        btns = QHBoxLayout()
        save_btn = QPushButton("💾 保存截图")
        save_btn.setStyleSheet("background:#6366f1;color:white;font-weight:600;border-radius:5px;padding:6px 14px;")
        recapture_btn = QPushButton("↺ 重新截图")
        recapture_btn.setStyleSheet("background:#2b2b2d;color:#e8e8e8;border-radius:5px;padding:6px 14px;")
        cancel_btn = QPushButton("✕ 取消")
        cancel_btn.setStyleSheet("background:#2b2b2d;color:#e8e8e8;border-radius:5px;padding:6px 14px;")
        btns.addWidget(save_btn)
        btns.addWidget(recapture_btn)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        lay.addLayout(btns)

        saved_path = {"v": None}

        def do_save():
            from . import capture as cap
            # 保存到用户捕获目录
            cap_dir = cap.default_capture_dir()
            os.makedirs(cap_dir, exist_ok=True)
            sample_id = self._cmd.get("sample_id", "") or os.path.basename(self._images[self._current]) if self._images else "capture"
            base = os.path.join(cap_dir, str(sample_id))
            path, err = cap.save_capture(cropped, base)
            if err:
                QMessageBox.warning(dlg, "保存失败", err)
            else:
                saved_path["v"] = path
                dlg.accept()

        def do_again():
            dlg.accept()
            # 重新进入截图模式
            self.act_capture.blockSignals(True)
            self.act_capture.setChecked(True)
            self.act_capture.blockSignals(False)
            self._on_capture_toggle(True)

        save_btn.clicked.connect(do_save)
        recapture_btn.clicked.connect(do_again)
        cancel_btn.clicked.connect(dlg.reject)

        exec_res = dlg.exec()
        if saved_path["v"]:
            self.statusBar().showMessage(
                f"✅ 截图已保存：{os.path.basename(saved_path['v'])}（{image_rect.width()}×{image_rect.height()}px）",
                8000)

    # ── 标注管理 ──
    def current_tool(self):
        return self._tool

    def current_color(self):
        return self._color

    def current_pen_width(self):
        return self._pen_width

    def current_font_size(self):
        return self._font_size

    def get_annotations(self):
        return self._annotations

    def get_sel_rect(self):
        return self._select_rect

    def set_tool(self, tool):
        if tool not in ALL_TOOLS:
            return
        self._tool = tool
        self._uncheck_tools()
        self.act_capture.setChecked(False)
        self._tool_actions[tool].setChecked(True)
        if tool == "select":
            self.canvas.set_mode("select")
        else:
            self.canvas.set_mode("draw")
        self.statusBar().showMessage(f"当前工具：{tool}", 3000)

    def _uncheck_tools(self):
        for act in self._tool_actions.values():
            act.setChecked(False)

    def _build_color_selector(self):
        from PySide6.QtWidgets import QComboBox
        combo = QComboBox()
        combo.setStyleSheet(
            "QComboBox{background:#2b2b2d;color:#e8e8e8;border-radius:5px;padding:3px 8px;}"
            "QComboBox QAbstractItemView{background:#2b2b2d;color:#e8e8e8;}"
        )
        combo.addItems(["红色", "橙色", "黄色", "绿色", "蓝色", "紫色", "白色", "黑色"])
        combo.setCurrentIndex(0)
        combo.currentIndexChanged.connect(self._on_color_changed)
        return combo

    def _on_color_changed(self, idx):
        from .annotation import COLOR_PRESETS
        self._color = COLOR_PRESETS[idx]
        self.statusBar().showMessage(f"颜色：{self._color}", 2000)

    def _spin(self, low, high, value, callback):
        sp = QSpinBox()
        sp.setRange(low, high)
        sp.setValue(value)
        sp.setStyleSheet(
            "QSpinBox{background:#2b2b2d;color:#e8e8e8;border-radius:5px;padding:2px 6px;}"
            "QSpinBox::up-button,QSpinBox::down-button{width:14px;background:#3a3a40;}"
        )
        sp.setMaximumWidth(62)
        sp.valueChanged.connect(callback)
        return sp

    def _on_pen_width(self, val):
        self._pen_width = float(val)
        if self.canvas._draw_preview:
            self.canvas.viewport().update()

    def _on_font_size(self, val):
        self._font_size = float(val)

    def add_annotation(self, ann):
        self._undo_stack.append(list(self._annotations))
        self._redo_stack.clear()
        self._annotations.append(ann)
        self.canvas.viewport().update()
        self._update_toolbar_state()

    def add_text_at(self, pos, color, font_size):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "添加文字标注", "输入文字：")
        if ok and text.strip():
            ann = Annotation(type_=TOOL_TEXT, points=[pos], color=color,
                             width=self._pen_width, font_size=font_size, text=text.strip())
            self.add_annotation(ann)

    def undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(list(self._annotations))
        self._annotations = list(self._undo_stack.pop())
        self.canvas.viewport().update()
        self._update_toolbar_state()

    def redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(list(self._annotations))
        self._annotations = list(self._redo_stack.pop())
        self.canvas.viewport().update()
        self._update_toolbar_state()

    def delete_selected(self):
        if self._select_rect is None or self._select_rect.isNull():
            self.statusBar().showMessage("请先用「⬚ 选择」框选要删除的标注区域。", 3000)
            return
        from .annotation import annotation_bbox
        keep = []
        removed = 0
        for a in self._annotations:
            bbox = annotation_bbox([a])
            if bbox.isNull() or not self._select_rect.intersects(bbox):
                keep.append(a)
            else:
                removed += 1
        if removed:
            self._undo_stack.append(list(self._annotations))
            self._redo_stack.clear()
            self._annotations = keep
            self._select_rect = None
            self.canvas.viewport().update()
            self._update_toolbar_state()
            self.statusBar().showMessage(f"已删除 {removed} 条标注。", 3000)
        else:
            self.statusBar().showMessage("选中区域内没有标注（先用「⬚ 选择」框选）。", 3000)

    # 选择工具：begin / move / end
    def _select_anchor(self):
        return getattr(self, "_select_anchor_pt", None)

    def begin_select(self, scene_pos):
        if self._tool != "select":
            return
        self._select_anchor_pt = QPointF(scene_pos)
        self._select_rect = QRectF(scene_pos, scene_pos).normalized()
        self.canvas.viewport().update()

    def move_select_scene(self, scene_pos):
        if self._tool != "select" or self._select_rect is None:
            return
        if self._select_anchor() is not None:
            self._select_rect = QRectF(self._select_anchor(), QPointF(scene_pos)).normalized()
        else:
            self._select_rect = QRectF(scene_pos, scene_pos)
        self.canvas.viewport().update()

    def end_select(self):
        if self._select_rect is not None and self._select_rect.isNull():
            self._select_rect = None
        self.canvas.viewport().update()

    def _update_toolbar_state(self):
        pass

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
