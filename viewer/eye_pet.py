# viewer/eye_pet.py — EyePet: 「审视之眼」桌面小精灵
# 悬浮窗最小化后显示的精美眼睛主题小精灵：
#   - 无边框、透明背景、置顶，可拖动
#   - QPainter 手绘眼睛（虹膜渐变/高光/睫毛），QTimer 动画（眨眼/视线漂移/跟随鼠标）
#   - 单击恢复悬浮窗；右键菜单可恢复或退出
import math
import time
import random
from PySide6.QtWidgets import QWidget, QMenu
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import (
    QPainter, QPainterPath, QColor, QBrush, QPen,
    QRadialGradient, QLinearGradient,
)

# 主题色（可在此更换配色）
IRIS_COLOR = QColor(91, 84, 255)        # 主虹膜色（审视之眼 靛蓝紫）
IRIS_DARK = QColor(48, 38, 200)         # 虹膜外圈
IRIS_LIGHT = QColor(150, 130, 255)      # 虹膜高光环
SKIN = QColor(255, 224, 200)            # 眼周肤色
LID_LINE = QColor(70, 46, 36)           # 上眼睑线
LASH = QColor(60, 38, 30)               # 睫毛
BLUSH = QColor(255, 170, 170)           # 腮红


class EyePet(QWidget):
    """桌面小精灵：一只会眨眼、会看你的大眼睛。"""

    SIZE = (132, 116)

    def __init__(self, on_click=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(*self.SIZE)

        self._on_click = on_click or (lambda: None)
        # 眼睛状态机：open=睁眼(常态) / blink=快速一眨 / sleep=长时间闭眼
        # openness: 0=全闭, 1=全开
        self._state = "open"
        self._openness = 1.0
        self._blink_ts = 0.0            # 本次眨眼开始时间
        self._sleep_until = 0.0         # 本次闭眼结束时间
        self._next_blink = time.time() + random.uniform(4.0, 7.5)
        self._next_sleep = time.time() + random.uniform(12.0, 20.0)
        self._look_t = random.uniform(0, math.tau)
        self._look_dx = 0.0
        self._look_dy = 0.0
        self._target_dx = 0.0
        self._target_dy = 0.0
        self._excited = 0.0            # 点击瞬间的兴奋程度 0~1
        self._mouse_inside = False
        self._dragging = False
        self._drag_offset = QPointF()
        self._press_pos = None         # 用于区分点击 vs 拖动
        self._hover_start = 0.0

        self._anim = QTimer(self)
        self._anim.setInterval(30)
        self._anim.timeout.connect(self._tick)
        self._anim.start()

    # ── 窗口交互 ──

    def _tick(self):
        now = time.time()
        # ── 眼睛状态机 ──
        if self._state == "open":
            self._openness = 1.0
            if now >= self._next_sleep:
                # 进入长时间闭眼（睡觉），立即闭拢
                self._state = "sleep"
                self._sleep_until = now + 3.0
                self._openness = 0.0
            elif now >= self._next_blink:
                # 进入快速眨眼
                self._state = "blink"
                self._blink_ts = now

        elif self._state == "blink":
            elapsed = now - self._blink_ts
            # 快速一眨：~0.27s 闭拢 -> 0.27s 张开
            if elapsed <= 0.27:
                self._openness = max(0.0, 1.0 - elapsed / 0.27)
            elif elapsed <= 0.54:
                self._openness = min(1.0, (elapsed - 0.27) / 0.27)
            else:
                self._state = "open"
                self._openness = 1.0
                self._next_blink = now + random.uniform(4.0, 7.5)

        elif self._state == "sleep":
            self._openness = 0.0
            if now >= self._sleep_until:
                self._state = "open"
                self._openness = 1.0
                self._next_sleep = now + random.uniform(12.0, 20.0)
                # 睡醒后重新计时眨眼
                self._next_blink = now + random.uniform(2.0, 4.0)

        # ── 视线漂移（睡觉时不动）──
        if self._state != "sleep":
            if random.random() < 0.05:
                self._target_dx = random.uniform(-7, 7)
                self._target_dy = random.uniform(-4, 4)
            # 跟随鼠标
            if self._mouse_inside:
                pos = self.mapFromGlobal(self.cursor().pos())
                cx, cy = self.width() / 2, self.height() / 2
                self._target_dx = max(-8, min(8, (pos.x() - cx) * 0.18))
                self._target_dy = max(-5, min(5, (pos.y() - cy) * 0.14))
            self._look_dx += (self._target_dx - self._look_dx) * 0.12
            self._look_dy += (self._target_dy - self._look_dy) * 0.12

        # 兴奋衰减
        self._excited = max(0.0, self._excited - 0.03)
        self.update()

    def enterEvent(self, event):
        self._mouse_inside = True
        self._hover_start = time.time()
        self._target_dx = 0.0
        self._target_dy = 0.0
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._mouse_inside = False
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._press_pos = event.pos()
            self._drag_offset = event.globalPosition() - QPointF(self.x(), self.y())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and (event.buttons() & Qt.LeftButton):
            self.move((event.globalPosition() - self._drag_offset).toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            # 区分「点击」与「拖动」：移动距离小且未明显拖动 → 判定为点击
            is_click = self._press_pos is not None and (
                (event.pos() - self._press_pos).manhattanLength() < 8
            )
            self._press_pos = None
            # 仅「睁眼」状态点击才恢复悬浮窗；闭眼/眨眼瞬间不响应打开，但仍可拖动
            if is_click and self._state == "open":
                self._excited = 1.0
                QTimer.singleShot(220, self._on_click)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        restore = menu.addAction("🔍 恢复悬浮窗")
        quit_ = menu.addAction("✕ 退出悬浮窗")
        act = menu.exec(event.globalPos())
        if act == restore:
            self._on_click()
        elif act == quit_:
            from viewer.dock_manager import get_dock_manager
            get_dock_manager().hide()

    # ── 绘制 ──

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2 + 6

        # 眼睛实际开合程度（0=全闭,1=全开），兴奋时略舒展
        openness = max(0.02, min(1.0, self._openness + self._excited * 0.08))

        # 腮红
        self._draw_blush(p, cx - 34, cy + 16, 0.9)
        self._draw_blush(p, cx + 34, cy + 16, 0.9)

        # 眼形（杏眼）
        eye_path = self._eye_path(cx, cy, 46, 40 * openness)
        # 眼白
        p.setPen(QPen(QColor(60, 40, 36), 1.6))
        p.setBrush(QBrush(QColor(255, 253, 250)))
        p.drawPath(eye_path)

        # 虹膜 + 瞳孔（限制在眼白内部）
        p.save()
        p.setClipPath(eye_path)
        ix = cx + self._look_dx
        iy = cy + self._look_dy * 0.5
        iris_r = 26 * (0.55 + 0.45 * openness)
        self._draw_iris(p, ix, iy, iris_r)
        self._draw_eyelight(p, ix, iy, iris_r)
        p.restore()

        # 上眼睑线 + 睫毛
        openness_soft = max(0.02, openness)
        self._draw_lid(p, cx, cy, 46, 40 * openness_soft)

        # 闭眼（睡觉）时显示漂浮的 Zz 睡意符号
        if self._state == "sleep":
            self._draw_sleep_zzz(p, cx, cy)

        p.end()

    def _draw_sleep_zzz(self, p, cx, cy):
        """睡觉时在眼旁绘制轻轻浮动的 Z z。"""
        phase = (time.time() % 2.4)
        from PySide6.QtGui import QFont
        p.setFont(QFont("Segoe UI", 10, QFont.Bold))
        p.setPen(QPen(QColor(120, 100, 200), 1))
        # 两个 Z 依次浮现并上浮
        frags = [(0.0, "z", 10), (1.2, "Z", 14)]
        for base, ch, size in frags:
            t = (phase - base) % 2.4
            alpha = max(0, min(1, 1 - t / 1.9))
            if alpha <= 0:
                continue
            p.setFont(QFont("Segoe UI", size, QFont.Bold))
            col = QColor(96, 76, 204, int(220 * alpha))
            p.setPen(QPen(col, 1))
            y = cy - 34 - int(t * 16)
            p.drawText(int(cx + 34), y, ch)

    def _eye_path(self, cx, cy, rw, rh):
        """杏眼轮廓：左右尖、上下圆。"""
        path = QPainterPath()
        left = QPointF(cx - rw, cy)
        right = QPointF(cx + rw, cy)
        top = QPointF(cx, cy - rh)
        bottom = QPointF(cx, cy + rh * 0.72)
        path.moveTo(left)
        path.cubicTo(
            QPointF(cx - rw * 0.55, cy - rh * 0.18),
            QPointF(cx - rw * 0.45, cy - rh),
            top,
        )
        path.cubicTo(
            QPointF(cx + rw * 0.45, cy - rh),
            QPointF(cx + rw * 0.55, cy - rh * 0.18),
            right,
        )
        path.cubicTo(
            QPointF(cx + rw * 0.55, cy + rh * 0.85),
            QPointF(cx + rw * 0.12, cy + rh * 0.72),
            bottom,
        )
        path.cubicTo(
            QPointF(cx - rw * 0.12, cy + rh * 0.72),
            QPointF(cx - rw * 0.55, cy + rh * 0.85),
            left,
        )
        path.closeSubpath()
        return path

    def _draw_iris(self, p, ix, iy, r):
        # 虹膜渐变
        grad = QRadialGradient(ix - r * 0.3, iy - r * 0.35, r)
        grad.setColorAt(0.0, IRIS_LIGHT)
        grad.setColorAt(0.45, IRIS_COLOR)
        grad.setColorAt(1.0, IRIS_DARK)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(ix, iy), r, r)
        # 瞳孔
        p.setBrush(QBrush(QColor(22, 18, 34)))
        p.drawEllipse(QPointF(ix, iy), r * 0.46, r * 0.46)

    def _draw_eyelight(self, p, ix, iy, r):
        p.setBrush(QBrush(QColor(255, 255, 255, 235)))
        p.setPen(Qt.NoPen)
        # 主高光（左上大）
        p.drawEllipse(QPointF(ix - r * 0.35, iy - r * 0.42), r * 0.30, r * 0.30)
        # 次高光（右下小）
        p.drawEllipse(QPointF(ix + r * 0.38, iy + r * 0.30), r * 0.13, r * 0.13)

    def _draw_lid(self, p, cx, cy, rw, rh):
        # 上眼睑（肤色弧 + 深色线），随眨眼下压
        lid_top = cy - rh
        lid_drop = cy - rh * 0.42
        path = QPainterPath()
        path.moveTo(QPointF(cx - rw * 0.96, cy - rh * 0.12))
        path.cubicTo(
            QPointF(cx - rw * 0.7, lid_top),
            QPointF(cx + rw * 0.7, lid_top),
            QPointF(cx + rw * 0.96, cy - rh * 0.12),
        )
        path.cubicTo(
            QPointF(cx + rw * 0.6, lid_drop),
            QPointF(cx - rw * 0.6, lid_drop),
            QPointF(cx - rw * 0.96, cy - rh * 0.12),
        )
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(SKIN))
        p.drawPath(path)
        # 眼睑线
        pen = QPen(LID_LINE, 2.2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        line_p = QPainterPath()
        line_p.moveTo(QPointF(cx - rw * 0.94, cy - rh * 0.10))
        line_p.cubicTo(
            QPointF(cx - rw * 0.6, lid_top + 1),
            QPointF(cx + rw * 0.6, lid_top + 1),
            QPointF(cx + rw * 0.94, cy - rh * 0.10),
        )
        p.drawPath(line_p)
        # 睫毛（外眼角翘起）
        lash_pen = QPen(LASH, 2.0)
        lash_pen.setCapStyle(Qt.RoundCap)
        p.setPen(lash_pen)
        bx, by = cx + rw * 0.9, cy - rh * 0.14
        for ang, ln in [(-0.25, 8), (-0.05, 10), (0.12, 9), (0.3, 7)]:
            p.drawLine(
                QPointF(bx, by),
                QPointF(bx + math.cos(ang) * ln, by + math.sin(ang) * ln),
            )

    def _draw_blush(self, p, x, y, scale):
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(BLUSH.red(), BLUSH.green(), BLUSH.blue(), 90)))
        p.drawEllipse(QPointF(x, y), 7 * scale, 4.5 * scale)


__all__ = ["EyePet"]