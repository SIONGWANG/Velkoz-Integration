# window.py — 独立图片查看器窗口
"""负责：原图加载（不 resize）、Fit、100%、滚轮缩放（以光标为中心）、右键平移、
图片切换、状态栏、工具栏与快捷键；v1.22.0 起支持单图 / 双图并排（左右独立面板）。
本窗口为独立 QMainWindow，运行在 subprocess 中，不依赖主程序 / 现有 viewer/ 的任何 UI 状态。
"""
import os
import time

from PySide6.QtCore import Qt, QRectF, QRect, QSize, QPointF, QPoint
from PySide6.QtGui import QPixmap, QPainter, QKeySequence, QColor, QBrush, QIcon, QAction, QPen
from PySide6.QtWidgets import (
    QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsRectItem, QStatusBar, QLabel, QToolBar, QApplication, QStyle,
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QMessageBox, QFileDialog, QInputDialog, QComboBox, QSpinBox,
    QWidget,
)

from . import protocol
from . import config as viewer_config
from . import branding
from .annotation import Annotation, TOOL_ARROW, TOOL_RECT, TOOL_ELLIPSE, TOOL_PEN, TOOL_TEXT, TOOL_SELECT, ALL_TOOLS

RESOLUTION = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def _relative_source(path):
    """返回源图片的显示名（相对路径），用于来源关系记录。"""
    if not path:
        return ""
    # 尽量转为项目相对路径；否则返回文件名
    try:
        from utils import BASE_DIR
        rel = os.path.relpath(path, BASE_DIR)
        if not rel.startswith(".."):
            return rel
    except Exception:
        pass
    return os.path.basename(path)

# 缩放上下限（相对 100%）与每档步进
ZOOM_MIN = 0.05
ZOOM_MAX = 8.0
ZOOM_STEP_IN = 1.25
ZOOM_STEP_OUT = 1 / 1.25

CANVAS_BG = QColor(30, 30, 32)


class ImageCanvas(QGraphicsView):
    """承载原图的画布：缩放、右键平移、状态回调。
    owner 为 ImagePanel（每侧一个），所有状态访问经 panel 路由到活动面板。
    """

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
        self._capture_dragged = False  # 截图时是否真的拖动过（区分"点击截全图/拖动截选区"）
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
        # 点击任意一侧即激活该面板（操作路由到活动面板）
        self._owner.activate()
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
            self._capture_dragged = False
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
            # 拖动超过阈值即视为"框选区域"，否则视为"点击截全图"
            if self._sel_start is not None:
                cur = self.mapToScene(event.pos())
                if (cur - self._sel_start).manhattanLength() > 4:
                    self._capture_dragged = True
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
            was_dragged = self._capture_dragged
            self._sel_start = None
            self._capture_dragged = False
            self.setCursor(Qt.CrossCursor)
            rect = None
            if was_dragged:
                rect = self._current_sel_image_rect()
            else:
                # 点击（未拖动）→ 截取整张图片
                if self._image_size[0] > 0 and self._image_size[1] > 0:
                    rect = QRect(0, 0, self._image_size[0], self._image_size[1])
                else:
                    self._owner.statusBar().showMessage("当前没有可截图的图片。", 5000)
                    return
            self._sel_item.setVisible(False)
            self._sel_item.setRect(QRectF())
            if rect is None or rect.isNull():
                self._owner.statusBar().showMessage("截图框选过小，已取消。", 5000)
                return
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

    # ── 键盘：处理撤销/重做/删除/关闭，确保焦点在画布时也能生效 ──
    def keyPressEvent(self, event):
        mods = event.modifiers()
        key = event.key()
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        if ctrl and key == Qt.Key_Z:
            if shift:
                self._owner.redo()
            else:
                self._owner.undo()
            event.accept()
            return
        if ctrl and key == Qt.Key_Y:
            self._owner.redo()
            event.accept()
            return
        if key == Qt.Key_Delete:
            self._owner.delete_selected()
            event.accept()
            return
        if key == Qt.Key_Escape:
            self._owner.close()
            event.accept()
            return
        super().keyPressEvent(event)


class ImagePanel:
    """单侧面板：拥有自己的画布、当前图片路径、标注/撤销重做/选择状态。
    标注按“图片路径”绑定，左右互换或切换图片都不会串图。"""

    def __init__(self, window, side):
        self.window = window
        self.side = side                      # 0=左, 1=右
        self.canvas = ImageCanvas(self)       # owner = 本 panel
        self.image_path = None
        self.index = 0
        self.annotations = []
        self.undo_stack = []
        self.redo_stack = []
        self.select_rect = None
        self._select_anchor_pt = None
        self.annotations_by_image = {}        # {path: [Annotation...]}
        self.undo_by_image = {}               # {path: [[...]]}
        self.redo_by_image = {}

        # 头部标签 + 画布 容器（便于并排布局与活动高亮）
        self.header = QLabel("")
        self.header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.container = QWidget()
        lay = QVBoxLayout(self.container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addWidget(self.canvas)
        self.set_active_style(False)

    # ── 与主窗口共享的工具/颜色状态 ──
    def current_tool(self):
        return self.window.current_tool()

    def current_color(self):
        return self.window.current_color()

    def current_pen_width(self):
        return self.window.current_pen_width()

    def current_font_size(self):
        return self.window.current_font_size()

    def activate(self):
        self.window.set_active_panel(self.side)

    def statusBar(self):
        return self.window.statusBar()

    def update_status(self, image_size, zoom):
        self.window.update_status(self, image_size, zoom)

    def close(self):
        self.window.close()

    # ── 标注状态（按图片路径隔离） ──
    def get_annotations(self):
        return self.annotations

    def get_sel_rect(self):
        return self.select_rect

    def add_annotation(self, ann):
        self.undo_stack.append(list(self.annotations))
        self.redo_stack.clear()
        self.annotations.append(ann)
        self.canvas.viewport().update()

    def add_text_at(self, pos, color, font_size):
        text, ok = QInputDialog.getText(self.window, "添加文字标注", "输入文字：")
        if ok and text.strip():
            ann = Annotation(type_=TOOL_TEXT, points=[pos], color=color,
                             width=self.current_pen_width(), font_size=font_size, text=text.strip())
            self.add_annotation(ann)

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(list(self.annotations))
        self.annotations = list(self.undo_stack.pop())
        self.canvas.viewport().update()

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(list(self.annotations))
        self.annotations = list(self.redo_stack.pop())
        self.canvas.viewport().update()

    def delete_selected(self):
        if self.select_rect is None or self.select_rect.isNull():
            self.window.statusBar().showMessage("请先用「⬚ 选择」框选要删除的标注区域。", 3000)
            return
        from .annotation import annotation_bbox
        keep = []
        removed = 0
        for a in self.annotations:
            bbox = annotation_bbox([a])
            if bbox.isNull() or not self.select_rect.intersects(bbox):
                keep.append(a)
            else:
                removed += 1
        if removed:
            self.undo_stack.append(list(self.annotations))
            self.redo_stack.clear()
            self.annotations = keep
            self.select_rect = None
            self.canvas.viewport().update()
            self.window.statusBar().showMessage(f"已删除 {removed} 条标注。", 3000)
        else:
            self.window.statusBar().showMessage("选中区域内没有标注（先用「⬚ 选择」框选）。", 3000)

    # ── 选择工具 ──
    def begin_select(self, scene_pos):
        if self.window.current_tool() != "select":
            return
        self._select_anchor_pt = QPointF(scene_pos)
        self.select_rect = QRectF(scene_pos, scene_pos).normalized()
        self.canvas.viewport().update()

    def move_select_scene(self, scene_pos):
        if self.window.current_tool() != "select" or self.select_rect is None:
            return
        if self._select_anchor_pt is not None:
            self.select_rect = QRectF(self._select_anchor_pt, QPointF(scene_pos)).normalized()
        else:
            self.select_rect = QRectF(scene_pos, scene_pos)
        self.canvas.viewport().update()

    def end_select(self):
        if self.select_rect is not None and self.select_rect.isNull():
            self.select_rect = None
        self.canvas.viewport().update()

    # ── 图片加载与状态存取 ──
    def store_state(self):
        path = self.image_path
        if not path:
            return
        self.annotations_by_image[path] = list(self.annotations)
        self.undo_by_image[path] = list(self.undo_stack)
        self.redo_by_image[path] = list(self.redo_stack)

    def load_index(self, idx):
        imgs = self.window._images
        if not imgs:
            self.set_empty("")
            return
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = 0
        idx = max(0, min(idx, len(imgs) - 1))
        # 先保存上一张图的标注，再加载本张图自己的标注（标注不跨图显示）
        self.store_state()
        path = imgs[idx]
        self.index = idx
        self.image_path = path
        self.annotations = list(self.annotations_by_image.get(path, []))
        self.undo_stack = list(self.undo_by_image.get(path, []))
        self.redo_stack = list(self.redo_by_image.get(path, []))
        self.select_rect = None
        self._select_anchor_pt = None
        ok, msg = self.canvas.load_image(path)
        if not ok:
            self.set_empty(msg)
        self._update_header()
        self.canvas.viewport().update()
        self.window.refresh_status()

    def set_empty(self, msg=""):
        self.canvas.load_image("")
        self.canvas._image_size = (0, 0)
        self.image_path = None
        self.annotations = []
        self.undo_stack = []
        self.redo_stack = []
        self.select_rect = None
        self._set_header_text(self._side_name() + "  无图")
        self.window.refresh_status()
        if msg:
            self.window.statusBar().showMessage(msg, 8000)

    def set_no_comparison(self):
        """双图模式下另一侧无比较图时的占位。"""
        self.canvas.load_image("")
        self.canvas._image_size = (0, 0)
        self.image_path = None
        self._set_header_text(self._side_name() + "  无比较图")
        self.window.refresh_status()

    def _side_name(self):
        return "左图" if self.side == 0 else "右图"

    def _update_header(self):
        imgs = self.window._images
        if self.image_path and self.image_path in imgs:
            idx = self.index + 1
            name = os.path.basename(self.image_path)
            self._set_header_text(f"{self._side_name()}  {idx}/{len(imgs)}  {name}")
        else:
            self._set_header_text(self._side_name())

    def _set_header_text(self, text):
        self.header.setText(text)

    def set_active_style(self, active):
        if active:
            self.header.setStyleSheet(
                "QLabel{color:#ffffff;font-weight:700;padding:4px 10px;background:#4f46e5;}")
            self.canvas.setStyleSheet("QGraphicsView{border:2px solid #6366f1;}")
        else:
            self.header.setStyleSheet(
                "QLabel{color:#9aa4f2;font-weight:600;padding:4px 10px;background:#23232a;}")
            self.canvas.setStyleSheet("QGraphicsView{border:2px solid #2b2b30;}")

    # ── 截图：由画布回调，路由到主窗口完成裁剪/预览/保存 ──
    def on_capture(self, image_rect):
        self.window.on_capture(self, image_rect)


class ImageViewerWindow(QMainWindow):
    """独立图片查看器主窗口（单图 / 双图并排）。"""

    def __init__(self, cmd=None):
        super().__init__()
        self._cmd = cmd or {}
        self._images = self._cmd.get("images", []) or []
        self._current = int(self._cmd.get("current_index", 0) or 0)
        self._on_top = bool(self._cmd.get("on_top", True))
        # 快捷键设置：优先取本次命令携带的，否则从配置文件读取
        self._shortcuts = self._cmd.get("shortcuts") or None
        if not self._shortcuts:
            self._shortcuts = viewer_config.get_viewer_settings()["shortcuts"]
        self._qkeys = viewer_config.qkeys_for(self._shortcuts)

        # 布局模式：single / dual（优先取命令携带的，否则读配置）
        self._layout_mode = self._cmd.get("layout_mode") or viewer_config.get_layout_mode()
        if self._layout_mode not in (viewer_config.LAYOUT_SINGLE, viewer_config.LAYOUT_DUAL):
            self._layout_mode = viewer_config.DEFAULT_LAYOUT_MODE

        # 全局（两侧共享）工具/颜色状态
        self._tool = TOOL_RECT
        self._color = "#ef4444"
        self._pen_width = 8.0           # 原图像素线宽（默认加粗，易于观察）
        self._font_size = 36.0          # 原图像素字号

        # 面板：0=左（主），1=右（比较）
        self.panels = [ImagePanel(self, 0), ImagePanel(self, 1)]
        self.active_side = 0

        # 应用图标 + 视觉样式
        self.setWindowIcon(branding.make_app_icon())
        self.setWindowTitle("Velkoz 独立图片查看器")
        self.setMinimumSize(680, 480)
        self.resize(1180, 800)
        self.setStyleSheet(branding.window_qss())

        # ── UI ──
        self._build_central()
        self._build_toolbar()
        self._build_statusbar()

        # 应用快捷键（覆盖硬编码默认值）
        self._apply_shortcuts()
        # 按初始 layout_mode 显示/隐藏右侧面板
        self._apply_layout_mode()

        # 置顶（可在打开时切换）
        if self._on_top:
            self._set_topmost(True)

        # 打开后加载
        self.set_active_panel(0, force=True)
        self._reload_panels(self._current)

    # ── 面板与布局 ──
    def _build_central(self):
        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        # 左右等分（隐藏右侧时左侧自动占满），比 QSplitter.setSizes 更可预测
        h.addWidget(self.panels[0].container, 1)
        h.addWidget(self.panels[1].container, 1)
        self.setCentralWidget(central)

    def _apply_layout_mode(self):
        dual = (self._layout_mode == viewer_config.LAYOUT_DUAL)
        self.panels[1].container.setVisible(dual)
        if hasattr(self, "act_dual"):
            self.act_dual.blockSignals(True)
            self.act_dual.setChecked(dual)
            self.act_dual.blockSignals(False)

    def set_layout_mode(self, mode, persist=True):
        if mode not in (viewer_config.LAYOUT_SINGLE, viewer_config.LAYOUT_DUAL):
            mode = viewer_config.DEFAULT_LAYOUT_MODE
        changed = (mode != self._layout_mode)
        self._layout_mode = mode
        self._apply_layout_mode()
        if persist:
            try:
                viewer_config.save_layout_mode(mode)
            except Exception:
                pass
        # 重新同步两侧内容（单图只显示左侧）
        self._reload_panels(self.panels[0].index if self.panels[0].image_path else self._current)
        if changed:
            self.window_status_hint()

    def _on_dual_toggled(self, checked):
        self.set_layout_mode(viewer_config.LAYOUT_DUAL if checked else viewer_config.LAYOUT_SINGLE)

    def active_panel(self):
        return self.panels[self.active_side]

    def set_active_panel(self, side, force=False):
        if side not in (0, 1):
            return
        if not force and side == self.active_side:
            return
        self.active_side = side
        for p in self.panels:
            p.set_active_style(p.side == side)
        # 用户点击切换时，按当前全局工具恢复该画布模式（除非该面板正在截图）；
        # force（初始化）时不改变画布模式，保持初始的 view 状态。
        p = self.panels[side]
        if not force and not p.canvas.is_capture_mode():
            p.canvas.set_mode("select" if self._tool == "select" else "draw")
        self.refresh_status()

    def window_status_hint(self):
        if self._layout_mode == viewer_config.LAYOUT_DUAL:
            self.statusBar().showMessage(
                "双图并排：点击任一侧激活；上一张/下一张、缩放、标注、截图均作用于活动面板。", 5000)
        else:
            self.statusBar().showMessage("单图模式。", 3000)

    # ── 重新加载两侧内容 ──
    def _reload_panels(self, base_index):
        imgs = self._images
        if not imgs:
            self.panels[0].set_empty("")
            self.panels[1].set_empty("")
            self.update_title()
            self.refresh_status()
            return
        try:
            base = int(base_index)
        except (TypeError, ValueError):
            base = 0
        base = max(0, min(base, len(imgs) - 1))
        self.panels[0].load_index(base)
        if self._layout_mode == viewer_config.LAYOUT_DUAL:
            if len(imgs) >= 2:
                nxt = base + 1 if base + 1 < len(imgs) else base - 1
                self.panels[1].load_index(max(0, nxt))
            else:
                self.panels[1].set_no_comparison()
        else:
            self.panels[1].set_empty("")
        self._current = self.panels[0].index
        self.update_title()
        self.refresh_status()

    # ── 快捷键 ──
    def _action_map(self):
        return {
            "prev": self.act_prev,
            "next": self.act_next,
            "zoom_in": self.act_zoomin,
            "zoom_out": self.act_zoomout,
            "fit": self.act_fit,
            "hundred": self.act_100,
            "close": self.act_quit,
        }

    def _apply_shortcuts(self):
        """把配置的快捷键覆盖式绑定到对应 action。
        每个 action 都通过 setShortcut 整体覆盖，不会残留旧的重复 QShortcut/QAction；
        非法或空值回退默认值，冲突时保留先到者、后者回退默认。"""
        qk = self._qkeys or {}
        defaults = viewer_config.qkeys_for(viewer_config.DEFAULT_SHORTCUTS)
        mapping = self._action_map()
        seen = {}
        for key, act in mapping.items():
            seq = qk.get(key) or defaults.get(key) or ""
            canon = seq.lower()
            if canon and canon in seen:
                seq = defaults.get(key) or ""
                canon = seq.lower()
                if canon and canon in seen:
                    seq = ""  # 仍与已绑定键冲突 → 清空该绑定，避免一键多触发
            if canon:
                seen[canon] = key
            try:
                act.setShortcut(QKeySequence(seq) if seq else QKeySequence())
            except Exception:
                pass
            try:
                base = act.text().split(" (")[0]
                act.setToolTip(f"{base} ({seq})" if seq else base)
            except Exception:
                pass

    def set_shortcuts(self, shortcuts):
        """热更新快捷键：校验→归一化→覆盖式重绑。"""
        merged = viewer_config.normalize_shortcuts(shortcuts, log_issues=True)
        self._shortcuts = merged
        self._qkeys = viewer_config.qkeys_for(merged)
        self._apply_shortcuts()

    # ── UI 构建 ──
    def _build_toolbar(self):
        # 分两条工具栏，避免按钮过多被挤进折叠菜单导致不可见
        self._build_toolbar_nav()
        self._build_toolbar_annot()

    def _mk_toolbar(self, name):
        tb = QToolBar(name)
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        return tb

    def _build_toolbar_nav(self):
        tb = self._mk_toolbar("查看")
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
        self.act_zoomout.triggered.connect(self.zoom_out)
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
        self.act_zoomin.triggered.connect(self.zoom_in)
        tb.addAction(self.act_zoomin)

        tb.addSeparator()

        self.act_fit = QAction(ic_fit, "适应窗口", self)
        self.act_fit.setToolTip("适应窗口 (0)")
        self.act_fit.setShortcut("0")
        self.act_fit.triggered.connect(self.fit_active)
        tb.addAction(self.act_fit)

        self.act_100 = QAction("1:1", self)
        self.act_100.setToolTip("实际大小 (1)")
        self.act_100.setShortcut("1")
        self.act_100.triggered.connect(self.set100_active)
        tb.addAction(self.act_100)

        tb.addSeparator()

        # 单图 / 双图并排切换
        self.act_dual = QAction("双图", self)
        self.act_dual.setToolTip("单图 / 双图并排（左右独立缩放、标注、截图）")
        self.act_dual.setCheckable(True)
        self.act_dual.toggled.connect(self._on_dual_toggled)
        tb.addAction(self.act_dual)

        tb.addSeparator()

        # 截图：框选原图区域 -> 从原图裁剪
        self.act_capture = QAction(style.standardIcon(QStyle.SP_DialogSaveButton), "🖼️ 截图", self)
        self.act_capture.setToolTip("截图：框选活动面板区域，从原图裁剪（不从屏幕截图）")
        self.act_capture.setCheckable(True)
        self.act_capture.toggled.connect(self._on_capture_toggle)
        tb.addAction(self.act_capture)

        tb.addSeparator()

        self.act_quit = QAction("✕ 关闭", self)
        self.act_quit.setShortcut("Esc")
        self.act_quit.setToolTip("关闭 (Esc)")
        self.act_quit.triggered.connect(self.close)
        tb.addAction(self.act_quit)

    def _build_toolbar_annot(self):
        tb = self._mk_toolbar("标注")
        tb.setStyleSheet(branding.window_qss())

        # ── 标注工具 ──
        self._tool_actions = {}
        tools = [
            ("select", "⬚ 选择", "选择/删除标注"),
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

        # 撤销 / 前进（撤消的反向：撤销后点"前进"把内容找回来）
        style = self.style()
        ic_undo = style.standardIcon(QStyle.SP_MediaSeekBackward)
        ic_redo = style.standardIcon(QStyle.SP_MediaSeekForward)
        self.act_undo = QAction(ic_undo, "↺ 撤销", self)
        self.act_undo.setToolTip("撤销 (Ctrl+Z)")
        self.act_undo.triggered.connect(lambda: self.active_panel().undo())
        tb.addAction(self.act_undo)

        self.act_redo = QAction(ic_redo, "⟳ 前进", self)
        self.act_redo.setToolTip("前进：把撤销的恢复回来 (Ctrl+Y / Ctrl+Shift+Z)")
        self.act_redo.triggered.connect(lambda: self.active_panel().redo())
        tb.addAction(self.act_redo)

        self.act_del = QAction("🗑 删除所选", self)
        self.act_del.setToolTip("删除所选标注 (Delete)")
        self.act_del.triggered.connect(lambda: self.active_panel().delete_selected())
        tb.addAction(self.act_del)

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

    # ── 导航（作用于活动面板） ──
    def goto_prev(self):
        self._step_active(-1)

    def goto_next(self):
        self._step_active(1)

    def _step_active(self, delta):
        if not self._images:
            return
        p = self.active_panel()
        if not p.image_path:
            return
        new = p.index + delta
        if 0 <= new < len(self._images):
            p.load_index(new)

    def zoom_in(self):
        self.active_panel().canvas.zoom_in()

    def zoom_out(self):
        self.active_panel().canvas.zoom_out()

    def fit_active(self):
        self.active_panel().canvas.fit_to_window()

    def set100_active(self):
        self.active_panel().canvas.set_100()

    # ── 图片集更新（来自主程序） ──
    def set_images(self, images, current_index=0):
        new_images = list(images or [])
        new_set = set(new_images)
        # 先保存当前图的标注，再丢弃不在新图集中的旧标注（避免内存无限增长）
        for p in self.panels:
            p.store_state()
            for k in list(p.annotations_by_image.keys()):
                if k not in new_set:
                    p.annotations_by_image.pop(k, None)
                    p.undo_by_image.pop(k, None)
                    p.redo_by_image.pop(k, None)
            if p.image_path and p.image_path not in new_set:
                p.image_path = None
        self._images = new_images
        self._reload_panels(int(current_index or 0))

    def set_current_index(self, index):
        self._reload_panels(int(index))

    # ── 状态栏 ──
    def refresh_status(self):
        p = self.active_panel()
        self.update_status(p, p.canvas.image_size(), p.canvas.transform().m11())

    def update_status(self, panel, image_size, zoom):
        imgs = self._images
        if imgs and panel.image_path:
            idx = panel.index + 1
            name = os.path.basename(panel.image_path)
            side = "左" if panel.side == 0 else "右"
            self.status_index.setText(f"[{side}] {idx} / {len(imgs)}")
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

    def update_title(self):
        imgs = self._images
        if imgs:
            idx = self.panels[0].index + 1
            name = os.path.basename(imgs[self.panels[0].index]) if imgs else ""
            mode = "双图" if self._layout_mode == viewer_config.LAYOUT_DUAL else "单图"
            self.setWindowTitle(f"Velkoz 独立图片查看器 [{mode}] — {idx}/{len(imgs)}  {name}")
        else:
            self.setWindowTitle("Velkoz 独立图片查看器")

    # ── 截图：框选 -> 预览 -> 保存 ──
    def _on_capture_toggle(self, checked):
        # 截图按钮是 checkable，与工具互斥；进入截图模式（作用于活动面板）
        p = self.active_panel()
        if checked:
            if not p.canvas.can_load():
                self.act_capture.blockSignals(True)
                self.act_capture.setChecked(False)
                self.act_capture.blockSignals(False)
                self.statusBar().showMessage("当前没有可截图的图片。", 5000)
                return
            self._uncheck_tools()
            self.act_capture.setChecked(True)
            p.canvas.set_mode("capture")
            self.setCursor(Qt.CrossCursor)
            side = "左图" if p.side == 0 else "右图"
            self.statusBar().showMessage(f"截图模式（{side}）：按住左键框选要截取的原图区域。", 6000)
        else:
            p.canvas.set_mode("view")
            self.setCursor(Qt.OpenHandCursor)

    def on_capture(self, panel, image_rect):
        """画布框选完成后回调：从该面板原图裁剪 -> 叠加标注 -> 预览 -> 保存。"""
        from . import capture
        from .annotation import bake_annotations_onto_crop
        # 退出截图模式（按钮取消选中）
        if self.act_capture.isChecked():
            self.act_capture.blockSignals(True)
            self.act_capture.setChecked(False)
            self.act_capture.blockSignals(False)
        panel.canvas.set_mode("view")
        self.setCursor(Qt.OpenHandCursor)

        if not panel.canvas.can_load():
            return
        pm = panel.canvas._item.pixmap()   # 原始 pixmap（未缩放）
        cropped, err = capture.crop_from_pixmap(pm, image_rect)
        if cropped is None:
            self.statusBar().showMessage(err or "裁剪失败。", 6000)
            return
        # 关键：把当前标注烧录进截图（标注以原图坐标存储，平移到裁剪坐标系）
        annotations = list(panel.get_annotations())
        if annotations:
            cropped = bake_annotations_onto_crop(cropped, annotations, image_rect, clip=False)
        self._capture_preview(panel, cropped, image_rect)

    def _capture_preview(self, panel, cropped, image_rect):
        """截图预览对话框：显示裁剪结果 + 保存 / 重新截图 / 取消。"""
        dlg = QDialog(self)
        dlg.setWindowIcon(branding.make_app_icon())
        dlg.setWindowTitle("截图预览")
        dlg.adjustSize()
        lay = QVBoxLayout(dlg)

        src_name = os.path.basename(panel.image_path) if panel.image_path else "—"
        src_idx = (panel.index + 1) if self._images else 0
        side = "左图" if panel.side == 0 else "右图"
        info = QLabel(f"来源：{side}（{src_idx}/{len(self._images)}）{src_name}\n"
                      f"原图选区：x={image_rect.x()}  y={image_rect.y()}  "
                      f"w={image_rect.width()}  h={image_rect.height()}  "
                      f"({image_rect.width()}×{image_rect.height()}px)")
        info.setStyleSheet("color:#e8e8e8;padding:4px 0;")
        lay.addWidget(info)

        from PySide6.QtWidgets import QLabel as L
        # 预览图按 crop 宽高比自适应，避免小图四周出现大黑框
        max_w, max_h = 640, 480
        scaled = cropped
        if scaled.width() > max_w or scaled.height() > max_h:
            scaled = cropped.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img_lbl = L()
        img_lbl.setPixmap(scaled)
        img_lbl.setMinimumSize(120, 80)
        img_lbl.setStyleSheet("background:#121218;border-radius:6px;padding:6px;")
        lay.addWidget(img_lbl, 0, Qt.AlignCenter)
        note = QLabel("裁剪来源：直接从该面板原图数据裁剪，非屏幕截图。已叠加该图片的标注。")
        note.setStyleSheet("color:#9aa4f2;")
        lay.addWidget(note, 0)

        btns = QHBoxLayout()
        upload_btn = QPushButton("📤 上传到质检")
        upload_btn.setStyleSheet("background:#22c55e;color:white;font-weight:600;border-radius:5px;padding:6px 14px;")
        save_btn = QPushButton("💾 保存截图")
        save_btn.setStyleSheet("background:#6366f1;color:white;font-weight:600;border-radius:5px;padding:6px 14px;")
        recapture_btn = QPushButton("↺ 重新截图")
        recapture_btn.setStyleSheet("background:#2b2b2d;color:#e8e8e8;border-radius:5px;padding:6px 14px;")
        cancel_btn = QPushButton("✕ 取消")
        cancel_btn.setStyleSheet("background:#2b2b2d;color:#e8e8e8;border-radius:5px;padding:6px 14px;")
        btns.addWidget(upload_btn)
        btns.addWidget(save_btn)
        btns.addWidget(recapture_btn)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        lay.addLayout(btns)

        saved_path = {"v": None, "action": None}   # action: "save" | "upload"
        _uploading = {"v": False}                   # 防止重复点击上传

        def _save_crop(cap_dir, sample_id):
            from . import capture as cap
            base = os.path.join(cap_dir, str(sample_id))
            path, err = cap.save_capture(cropped, base)
            return path, err

        def _evidence_dir():
            """确定截图最终保存目录：优先当前质检数据根目录/证据目录，否则回退用户捕获目录。"""
            data_root = self._cmd.get("data_root", "") or ""
            evidence_folder = self._cmd.get("evidence_folder", "") or ""
            if data_root and evidence_folder:
                return os.path.join(data_root, evidence_folder)
            from . import capture as cap
            return cap.default_capture_dir()

        def _source_image_rel():
            return _relative_source(panel.image_path or "")

        def do_upload():
            # 防止重复上传：上传期间按钮禁用，完成后恢复
            if _uploading["v"] or saved_path["action"]:
                return
            _uploading["v"] = True
            upload_btn.setEnabled(False)
            upload_btn.setText("上传中……")
            save_btn.setEnabled(False)
            recapture_btn.setEnabled(False)
            try:
                from . import capture as cap
                sample_id = str(self._cmd.get("sample_id", "") or "") or \
                    (os.path.basename(panel.image_path) if panel.image_path else "capture")
                cap_dir = _evidence_dir()
                # 先按旧规则保存截图到证据目录，校验写入完成再入队上传
                path, err = cap.save_evidence_capture(cropped, cap_dir, sample_id)
                if err:
                    QMessageBox.warning(dlg, "上传失败", err)
                    return
                # 记录来源关系并推送到主程序上传队列（保持旧协议字段）
                rel = _source_image_rel()
                from . import protocol as pr
                pr.push_upload({
                    "kind": "screenshot",
                    "path": path,                       # 截图文件绝对路径
                    "sample_id": str(sample_id),        # 当前样本/记录ID
                    "source_image": rel,                # 源图片（相对/绝对路径）
                    "crop_rect": [image_rect.x(), image_rect.y(), image_rect.width(), image_rect.height()],
                    "ts": int(time.time()),
                })
                saved_path["v"] = path
                saved_path["action"] = "upload"
                dlg.accept()
            except Exception as e:
                import traceback
                traceback.print_exc()
                QMessageBox.warning(dlg, "上传失败", f"上传截图时出错：{e}")
            finally:
                _uploading["v"] = False
                upload_btn.setEnabled(True)
                upload_btn.setText("📤 上传到质检")
                save_btn.setEnabled(True)
                recapture_btn.setEnabled(True)

        def do_save():
            from . import capture as cap
            # 保存到用户捕获目录
            cap_dir = cap.default_capture_dir()
            os.makedirs(cap_dir, exist_ok=True)
            sample_id = self._cmd.get("sample_id", "") or (os.path.basename(panel.image_path) if panel.image_path else "capture")
            path, err = _save_crop(cap_dir, sample_id)
            if err:
                QMessageBox.warning(dlg, "保存失败", err)
            else:
                saved_path["v"] = path
                saved_path["action"] = "save"
                dlg.accept()

        def do_again():
            dlg.accept()
            # 重新进入截图模式（作用于同一活动面板）
            self.act_capture.blockSignals(True)
            self.act_capture.setChecked(True)
            self.act_capture.blockSignals(False)
            self._on_capture_toggle(True)

        upload_btn.clicked.connect(do_upload)
        save_btn.clicked.connect(do_save)
        recapture_btn.clicked.connect(do_again)
        cancel_btn.clicked.connect(dlg.reject)

        exec_res = dlg.exec()
        if saved_path["v"]:
            action = saved_path.get("action")
            base = os.path.basename(saved_path["v"])
            if action == "upload":
                self.statusBar().showMessage(
                    f"🟢 已上传到质检：{base}（来源 {side} {src_name}）", 8000)
            else:
                self.statusBar().showMessage(
                    f"✅ 截图已保存：{base}（{image_rect.width()}×{image_rect.height()}px）", 8000)

    # ── 标注状态访问（供画布 / 工具调用；作用于活动面板） ──
    def current_tool(self):
        return self._tool

    def current_color(self):
        return self._color

    def current_pen_width(self):
        return self._pen_width

    def current_font_size(self):
        return self._font_size

    def set_tool(self, tool):
        if tool not in ALL_TOOLS:
            return
        self._tool = tool
        self._uncheck_tools()
        self.act_capture.setChecked(False)
        self._tool_actions[tool].setChecked(True)
        p = self.active_panel()
        if tool == "select":
            p.canvas.set_mode("select")
        else:
            p.canvas.set_mode("draw")
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
        if self.active_panel().canvas._draw_preview:
            self.active_panel().canvas.viewport().update()

    def _on_font_size(self, val):
        self._font_size = float(val)

    def bring_to_front(self):
        self.show()
        self.raise_()
        self.activateWindow()

    # ── 关闭：释放锁并请求应用退出，确保进程真正结束（不只是删锁文件）──
    def closeEvent(self, event):
        try:
            protocol.release_lock()
        except Exception:
            pass
        super().closeEvent(event)
        # 退出 Qt 事件循环，保证 runner 的轮询/心跳定时器停止、进程结束
        try:
            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:
            pass
