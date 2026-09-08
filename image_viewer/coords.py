# coords.py — 视图坐标 <-> 原图坐标 换算
"""独立封装 View Coordinate 与 Image Coordinate 之间的转换。

模型约定：
- QGraphicsView 场景中，QGraphicsPixmapItem 放在原点 (0,0)，且不缩放，
  因此「场景坐标」==「原图像素坐标」。
- view->scene：view.mapToScene()；scene->view：view.mapFromScene()。
- 外部统一使用 (x, y) 浮点坐标 + QPointF 交互。

本模块只做纯换算，不含图片加载/缩放逻辑，便于后续（Phase 3）截图
在 Fit / 100% / 200% / 400% 及放大后平移等任意状态下验证。
"""
from PySide6.QtCore import QPointF, QPoint


def view_to_image(view, view_x, view_y):
    """视图（widget 像素）坐标 → 原图像素坐标。返回 QPointF。"""
    # mapToScene 只接受整数 QPoint；先取整再从场景坐标换算成浮点
    pt = view.mapToScene(QPoint(int(round(view_x)), int(round(view_y))))
    return QPointF(float(pt.x()), float(pt.y()))


def image_to_view(view, image_x, image_y):
    """原图像素坐标 → 视图（widget 像素）坐标。返回 QPointF。"""
    pt = view.mapFromScene(QPointF(float(image_x), float(image_y)))
    return QPointF(float(pt.x()), float(pt.y()))


def image_to_scene(image_x, image_y):
    """原图像素坐标 → 场景坐标。原图映射在原点，二者一致。"""
    return QPointF(float(image_x), float(image_y))


def scene_to_image(scene_x, scene_y):
    """场景坐标 → 原图像素坐标。二者一致（映射在原点）。"""
    return QPointF(float(scene_x), float(scene_y))


def view_scale(view):
    """当前视图缩放比例（纵向系数）。返回 float。"""
    return view.transform().m11()


def view_rect_in_image(view):
    """返回当前视图矩形在地图上的范围 (x, y, w, h)，全浮点。"""
    scene_rect = view.mapToScene(view.viewport().rect()).boundingRect()
    return scene_rect


def image_rect_pixels(item):
    """返回场景项对应原图的尺寸 (w, h)。"""
    pm = item.pixmap()
    if pm.isNull():
        return 0, 0
    return pm.width(), pm.height()
