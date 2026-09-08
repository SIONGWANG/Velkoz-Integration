# annotation.py — 标注层数据模型与绘制（Phase 4）
"""独立 Annotation Layer：
- 标注对象以「原图坐标」存储（点、矩形、半径等均用图像素坐标），与缩放/平移无关。
- 绘制时用 scene 坐标（==原图坐标），并依据当前 scale 归一化线宽，保证任意缩放下可见。
- 只读原图 + 内存叠加，绝不写入原图数据。
- 导出截图时把标注按原图像素宽高重绘到裁剪结果上。
"""
import math

from PySide6.QtCore import QPointF, QRectF, QLineF, Qt
from PySide6.QtGui import QPen, QColor, QPainter, QPainterPath, QFont, QBrush, QPixmap

TOOL_ARROW = "arrow"
TOOL_RECT = "rect"
TOOL_ELLIPSE = "ellipse"
TOOL_PEN = "pen"
TOOL_TEXT = "text"
TOOL_SELECT = "select"

ALL_TOOLS = (TOOL_ARROW, TOOL_RECT, TOOL_ELLIPSE, TOOL_PEN, TOOL_TEXT, TOOL_SELECT)

COLOR_PRESETS = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#3b82f6", "#8b5cf6", "#ffffff", "#000000"]


class Annotation:
    """单条标注。type 决定 shape= / pen= / text 含义。"""
    __slots__ = ("type", "points", "color", "width", "font_size", "text", "shape")

    def __init__(self, type_=TOOL_RECT, points=None, color="#ef4444", width=4,
                 font_size=32, text="", shape=None):
        self.type = type_
        self.points = points or []            # [QPointF,...]
        self.color = color
        self.width = width                    # 原图像素线宽
        self.font_size = font_size            # 原图像素字号（文字）
        self.text = text
        self.shape = shape                    # 预留：矩形/椭圆等几何类型

    def to_geometry(self):
        """返回 (bbox QRectF, path QPainterPath, text str, font_size) 供绘制。"""
        return _build_geometry(self)


def _build_geometry(a):
    t = a.type
    bbox = QRectF()
    if t == TOOL_PEN:
        path = QPainterPath()
        pts = a.points
        if pts:
            path.moveTo(pts[0])
            for p in pts[1:]:
                path.lineTo(p)
        if not pts:
            bbox = QRectF()
        else:
            xs = [p.x() for p in pts]
            ys = [p.y() for p in pts]
            bbox = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        return bbox, path, a.text, a.font_size
    if t == TOOL_ARROW:
        # 起点 -> 终点 + 箭头
        p0 = a.points[0] if a.points else QPointF()
        p1 = a.points[-1] if len(a.points) > 1 else p0
        path = QPainterPath()
        path.moveTo(p0)
        path.lineTo(p1)
        bbox = QRectF(p0, p1).normalized()
        return bbox, path, a.text, a.font_size
    if t == TOOL_RECT:
        p0 = a.points[0] if a.points else QPointF()
        p1 = a.points[-1] if len(a.points) > 1 else p0
        r = QRectF(p0, p1).normalized()
        path = QPainterPath()
        path.addRect(r)
        return r, path, a.text, a.font_size
    if t == TOOL_ELLIPSE:
        p0 = a.points[0] if a.points else QPointF()
        p1 = a.points[-1] if len(a.points) > 1 else p0
        r = QRectF(p0, p1).normalized()
        path = QPainterPath()
        path.addEllipse(r)
        return r, path, a.text, a.font_size
    if t == TOOL_TEXT:
        pos = a.points[0] if a.points else QPointF()
        path = QPainterPath()
        path.moveTo(pos)
        return QRectF(pos, pos), path, a.text, a.font_size
    return QRectF(), QPainterPath(), a.text, a.font_size


def draw_annotations(painter, annotations, scale=1.0, in_image_space=True):
    """把标注绘制到 painter。scale 为当前视图缩放，用于归一化线宽。
    in_image_space=False 时按原图像素宽度直接画（导出场景用）。"""
    pen_width_div = scale if scale > 0 else 1.0
    for a in annotations:
        _draw_annotation(painter, a, pen_width_div, in_image_space)


def _draw_annotation(painter, a, div, in_image_space):
    t = a.type
    color = QColor(a.color)
    # 线宽：原图像素宽 / 视图scale，保证屏幕上约等于设定像素；导出时 div=1->原图宽
    w = a.width / div if in_image_space else a.width
    pen = QPen(color)
    pen.setWidthF(max(0.5, w))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    bbox, path, text, font_size = _build_geometry(a)

    if t == TOOL_TEXT:
        fs = font_size / div if in_image_space else font_size
        font = QFont()
        font.setPixelSize(int(max(6, fs)))
        painter.setFont(font)
        painter.setPen(pen)
        pos = a.points[0]
        painter.drawText(QPointF(pos.x(), pos.y()), text)
        return

    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)

    if t == TOOL_ARROW:
        _draw_arrow_head(painter, a.points, w)


def _draw_arrow_head(painter, points, width):
    if len(points) < 2:
        return
    p0 = points[0]
    p1 = points[-1]
    angle = math.atan2(p1.y() - p0.y(), p1.x() - p0.x())
    arrow_len = max(8.0, width * 3.5)
    spread = math.radians(25)
    left = QPointF(
        p1.x() - arrow_len * math.cos(angle - spread),
        p1.y() - arrow_len * math.sin(angle - spread),
    )
    right = QPointF(
        p1.x() - arrow_len * math.cos(angle + spread),
        p1.y() - arrow_len * math.sin(angle + spread),
    )
    path = QPainterPath()
    path.moveTo(left)
    path.lineTo(p1)
    path.lineTo(right)
    painter.setBrush(QColor(painter.pen().color()))
    painter.drawPath(path)


def annotations_to_pixmap(base_pixmap, annotations, scale=1.0):
    """把标注叠加到 base_pixmap 的副本上，返回新 QPixmap（不修改原图）。
    用于导出带标注的截图。"""
    out = QPixmap(base_pixmap)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    draw_annotations(painter, annotations, scale=1.0, in_image_space=True)
    painter.end()
    return out


def translate(annotation, dx, dy):
    """按偏移平移标注的所有点（返回新 Annotation，不改原对象）。"""
    import copy
    a = copy.copy(annotation)
    a.points = [QPointF(p.x() + dx, p.y() + dy) for p in annotation.points]
    return a


def bake_annotations_onto_crop(crop_pixmap, annotations, crop_rect, clip=True):
    """把标注叠加到裁剪结果上。
    - crop_rect: 原图坐标系的裁剪选区（QRect/QRectF）
    - 标注以原图坐标存储，需平移到裁剪坐标系（减 crop_rect.topLeft()）
    - clip=True 时用裁剪范围作 clip，避免标注溢出；但这样只显示框内的标注。
      clip=False 时绘出完整标注（含超出部分，可能延伸到裁剪图边缘）。
    返回新的 QPixmap（不修改 crop_pixmap / 原图）。"""
    dx = -crop_rect.x()
    dy = -crop_rect.y()
    out = QPixmap(crop_pixmap)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    translated = [translate(a, dx, dy) for a in annotations]
    if clip:
        painter.setClipRect(QRectF(0, 0, out.width(), out.height()))
    draw_annotations(painter, translated, scale=1.0, in_image_space=True)
    painter.end()
    return out


def annotation_bbox(annotations):
    """所有标注的联合包围盒（原图坐标 QRectF）。"""
    bbox = QRectF()
    for a in annotations:
        b, _, _, _ = _build_geometry(a)
        bbox = bbox.united(b)
    return bbox
