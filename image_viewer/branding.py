# branding.py — 独立图片查看器 图标与视觉
"""程序内动态生成应用图标（无需外部资源文件），配合窗口视觉。
图标为一个「审审视之眼」风格的放大镜 + 图片符号，用 QPainter 绘制。
"""
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPixmap, QIcon, QPainter, QColor, QPen, QBrush, QLinearGradient, QFont, QRadialGradient,
)

ACCENT = QColor("#6366f1")      # 主色（靛蓝）
ACCENT_2 = QColor("#8b5cf6")    # 渐变次色
DARK = QColor("#1e1e24")
BG_TOP = QColor("#2b2b33")
BG_BOT = QColor("#121218")


def make_app_icon(size=256):
    """生成应用图标 QIcon。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    # 背景圆角方形（渐变）
    rect = QRectF(0, 0, size, size)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.0, BG_TOP)
    grad.setColorAt(1.0, BG_BOT)
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(rect, size * 0.22, size * 0.22)

    # 圆环高光
    ring_grad = QRadialGradient(QPointF(size * 0.5, size * 0.42), size * 0.6)
    ring_grad.setColorAt(0.0, QColor(255, 255, 255, 28))
    ring_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(QBrush(ring_grad))
    p.drawRoundedRect(rect, size * 0.22, size * 0.22)

    # 放大镜：外圈
    lx, ly = size * 0.44, size * 0.42
    lens_r = size * 0.20
    handle = size * 2 * 0.26

    # 镜片玻璃（半透明、带渐变）
    c1 = QColor("#6366f1"); c1.setAlpha(150)
    c2 = QColor("#8b5cf6"); c2.setAlpha(120)
    lens_grad = QLinearGradient(lx - lens_r, ly - lens_r, lx + lens_r, ly + lens_r)
    lens_grad.setColorAt(0.0, c1)
    lens_grad.setColorAt(1.0, c2)
    p.setBrush(QBrush(lens_grad))
    p.setPen(QPen(QColor(255, 255, 255, 210), max(4, int(size * 0.035))))
    p.drawEllipse(QPointF(lx, ly), lens_r, lens_r)

    # 镜片内渐变星点
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 60))
    p.drawEllipse(QPointF(lx + lens_r * 0.3, ly - lens_r * 0.3), lens_r * 0.1, lens_r * 0.1)

    # 放大镜手柄
    ang = 0.7854  # 45°
    hx1 = lx + lens_r * 0.71
    hy1 = ly + lens_r * 0.71
    hx2 = lx + (lens_r * 0.71 + handle * 0.62) * 1.41 * 0.72
    hy2 = ly + (lens_r * 0.71 + handle * 0.62) * 1.41 * 0.72
    p.setPen(QPen(QColor(255, 255, 255, 235), max(5, int(size * 0.052)),
                  Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawLine(QPointF(hx1, hy1), QPointF(hx2, hy2))

    # 底部「检查勾」
    p.setPen(QPen(QColor("#22c55e"), max(4, int(size * 0.045)),
                  Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPolyline([
        QPointF(size * 0.22, size * 0.86),
        QPointF(size * 0.34, size * 0.73),
        QPointF(size * 0.60, size * 0.80),
    ])

    p.end()
    return QIcon(pm)


def make_app_icon_for_dock(size=128):
    return make_app_icon(size)


# ── 窗口级样式表（比 window.py 内联更集中） ──
def window_qss(accent="#6366f1"):
    a = QColor(accent)
    return f"""
QMainWindow {{
    background: #1c1c22;
}}
QToolBar {{
    background: #2b2b2d;
    border: none;
    padding: 5px;
    spacing: 4px;
    border-bottom: 1px solid #3a3a40;
}}
QToolButton {{
    color: #e8e8e8;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 11px;
    font-size: 14px;
}}
QToolButton:hover {{ background: {a.name()}30; }}
QToolButton:pressed {{ background: {a.name()}55; }}
QToolBar::separator {{ background: #4a4a50; width: 1px; margin: 5px 6px; }}
QStatusBar {{
    background: #232329;
    color: #c8c8c8;
    border-top: 1px solid #3a3a40;
    font-size: 13px;
}}
QStatusBar QLabel {{ color: #c8c8c8; padding: 0 10px; }}
QStatusBar::item {{ border: none; }}
"""
