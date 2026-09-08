# capture.py — 原图坐标裁剪 / 截图生成（与 View 解耦，便于测试）
"""规范第七、八条核心：
质检截图必须直接从「原始图片数据」裁剪生成，绝不能通过屏幕截图。
本模块把「视图框选矩形 -> 原图坐标 -> 从原图 crop」做成纯函数，
以便在 Fit / 100% / 200% / 400% 及放大平移后验证坐标一致。
"""
import os

from PySide6.QtCore import QRect, QPoint, QPointF
from PySide6.QtGui import QImage, QPixmap


def clamp_rect_to_image(rect, size_w, size_h):
    """把（原图坐标系的）QPointF 矩形裁剪/夹到 [0, size] 内，返回整数 QRect（含上下界）。"""
    if size_w <= 0 or size_h <= 0:
        return QRect()
    left = max(0, int(round(rect.left())))
    top = max(0, int(round(rect.top())))
    right = min(size_w, int(round(rect.right())))
    bottom = min(size_h, int(round(rect.bottom())))
    if right <= left or bottom <= top:
        return QRect()
    return QRect(QPoint(left, top), QPoint(right, bottom))


def crop_from_pixmap(pixmap, image_rect, dpr=1.0):
    """从原始 pixmap（即原图）裁剪 image_rect 区域，并还原 DPR 得到真实像素。"""
    rr = image_rect
    if rr.isNull() or pixmap.isNull():
        return None, "无效裁剪区域"
    # 处理逻辑像素到物理像素（高 DPI）
    px = pixmap.toImage()
    # image_rect 是原图像素坐标（QGraphicsScene 坐标 == 像素坐标）
    cropped = px.copy(rr)
    out = QPixmap.fromImage(cropped)
    return out, None


def select_rect_from_view(view, v0, v1):
    """把视图坐标的两角（QPoint）换算成原图坐标系矩形。
    v0 / v1 是用户在视图中按下与释放的点（QPoint）。返回 QRect（原图坐标）。
    若选中区域过小（<1 像素）返回空 QRect。"""
    from . import coords
    p0 = coords.view_to_image(view, v0.x(), v0.y())
    p1 = coords.view_to_image(view, v1.x(), v1.y())
    x0, x1 = min(p0.x(), p1.x()), max(p0.x(), p1.x())
    y0, y1 = min(p0.y(), p1.y()), max(p0.y(), p1.y())
    return QRect(QPoint(int(round(x0)), int(round(y0))), QPoint(int(round(x1)), int(round(y1))))


def save_capture(pixmap_or_image, path_base, prefix="qa", suffix_no=0):
    """把裁剪结果保存为独立 PNG 文件，绝不覆盖原图。
    返回 (保存路径, 错误信息)。文件名为 {base}_{prefix}_{ts}_{idx}.png"""
    import time
    if isinstance(pixmap_or_image, QPixmap):
        img = pixmap_or_image.toImage()
    else:
        img = pixmap_or_image
    if img.isNull():
        return None, "裁剪结果为空"
    ts = int(time.time())
    filename = f"{path_base}_{prefix}_{ts}_{suffix_no}.png"
    if not img.save(filename, "PNG"):
        return None, f"保存截图失败：{filename}"
    return filename, None


def default_capture_dir():
    """默认截图保存目录：项目 EVIDENCE 命名规则下 _00_Evidence_新标 同级。
    但为避免污染，独立文件查看器截图存到用户目录下 .velkoz_captures/。"""
    return os.path.join(os.path.expanduser("~"), ".velkoz_captures")
