"""Базовые компоненты единой дизайн-системы."""
from __future__ import annotations

import math

from PySide6.QtCore import (Property, QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt,
                            QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QLayout, QLayoutItem, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from app.ui import anim, icons
from app.ui.theme import T, rgba, tone

QWIDGETSIZE_MAX = 16777215


def add_glow(widget: QWidget, color: str = T["primary"], blur: int = 24, alpha: float = 0.45, dy: int = 4) -> None:
    eff = QGraphicsDropShadowEffect(widget)
    c = QColor(color)
    c.setAlphaF(alpha)
    eff.setColor(c)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    widget.setGraphicsEffect(eff)


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("HLine")
    f.setFixedHeight(1)
    return f


def label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


class ElidedLabel(QLabel):
    """Однострочная подпись: если не помещается — сокращается «…», а не раздвигает контейнер."""

    def __init__(self, text: str = "", name: str = "", parent=None):
        super().__init__(parent)
        if name:
            self.setObjectName(name)
        self._full = text
        self._fitting = False
        QLabel.setText(self, text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._fit()

    def fullText(self) -> str:  # noqa: N802
        return self._full

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(8, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, super().sizeHint().height())

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit()

    def changeEvent(self, e):  # noqa: N802
        super().changeEvent(e)
        if e.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit()

    def _fit(self) -> None:
        if self._fitting:
            return
        self._fitting = True
        try:
            w = self.width()
            txt = self.fontMetrics().elidedText(self._full, Qt.ElideRight, w - 2) if w > 20 else self._full
            if txt != QLabel.text(self):
                QLabel.setText(self, txt)
        finally:
            self._fitting = False


# --------------------------------------------------------------------------- кнопки
_ICON_COLORS = {"primary": "#ffffff", "secondary": "#c9d4ff", "outline": "#7ea6ff", "ghost": T["text_2"], "danger": "#ff8a8d"}


class Button(QPushButton):
    def __init__(self, text: str = "", icon_name: str | None = None, variant: str = "secondary", size: str = "md",
                 parent=None, icon_size: int = 16):
        super().__init__(text, parent)
        self.setProperty("variant", variant)
        self.setProperty("size", size)
        self.setCursor(Qt.PointingHandCursor)
        self._icon_name = icon_name
        self._variant = variant
        self._icon_size = icon_size
        self._text = text
        self._spin = 0
        self._timer: QTimer | None = None
        self.setIconSize(QSize(icon_size, icon_size))
        self._apply_icon()
        # --- анимации: подсветка под курсором, волна при нажатии, «дыхание» (busy)
        self._hov = 0.0
        self._mouse = QPointF()
        self._rip: tuple[QPointF, float] | None = None
        self._glow = 0.0
        self.setMouseTracking(True)
        self._hov_anim = QVariantAnimation(self)
        self._hov_anim.setDuration(170)
        self._hov_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hov_anim.valueChanged.connect(self._on_hov)
        self._rip_anim = QVariantAnimation(self)
        self._rip_anim.setStartValue(0.0)
        self._rip_anim.setEndValue(1.0)
        self._rip_anim.setDuration(480)
        self._rip_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._rip_anim.valueChanged.connect(self._on_rip)
        self._rip_anim.finished.connect(self._end_rip)
        self._pulse_anim: QVariantAnimation | None = None

    # --- эффекты
    def _on_hov(self, v) -> None:
        self._hov = float(v)
        self.update()

    def _on_rip(self, v) -> None:
        if self._rip is not None:
            self._rip = (self._rip[0], float(v))
            self.update()

    def _end_rip(self) -> None:
        self._rip = None
        self.update()

    def _animate_hover(self, to: float) -> None:
        self._hov_anim.stop()
        self._hov_anim.setStartValue(self._hov)
        self._hov_anim.setEndValue(to)
        self._hov_anim.start()

    def enterEvent(self, e):  # noqa: N802
        super().enterEvent(e)
        if anim.enabled() and self.isEnabled():
            self._mouse = QPointF(self.mapFromGlobal(self.cursor().pos()))
            self._animate_hover(1.0)

    def leaveEvent(self, e):  # noqa: N802
        super().leaveEvent(e)
        if self._hov > 0:
            self._animate_hover(0.0)

    def mouseMoveEvent(self, e):  # noqa: N802
        super().mouseMoveEvent(e)
        if self._hov > 0.01:
            self._mouse = QPointF(e.position())
            self.update()

    def mousePressEvent(self, e):  # noqa: N802
        if anim.enabled() and self.isEnabled() and e.button() == Qt.LeftButton:
            self._rip = (QPointF(e.position()), 0.0)
            self._rip_anim.stop()
            self._rip_anim.start()
        super().mousePressEvent(e)

    def set_pulse(self, on: bool) -> None:
        """Мягкое «дыхание» кнопки, пока идёт долгая операция."""
        if on and anim.enabled():
            if self._pulse_anim is None:
                self._pulse_anim = QVariantAnimation(self)
                self._pulse_anim.setStartValue(0.0)
                self._pulse_anim.setEndValue(1.0)
                self._pulse_anim.setDuration(1500)
                self._pulse_anim.setLoopCount(-1)
                self._pulse_anim.valueChanged.connect(self._on_pulse)
            self._pulse_anim.start()
        elif self._pulse_anim is not None:
            self._pulse_anim.stop()
            self._glow = 0.0
            self.update()

    def _on_pulse(self, v) -> None:
        self._glow = 0.5 - 0.5 * math.cos(float(v) * 2 * math.pi)
        self.update()

    def _radius(self) -> float:
        if self.property("chip") == "true":
            return 13.0
        if self.property("win") in ("true", "close"):
            return 6.0
        size = self.property("size")
        r = T["r_sm"] if size == "sm" else T["r_lg"] if (size == "lg" or self.property("tab") == "model") else T["r_md"]
        return float(min(r, self.height() / 2))

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        if not self.isEnabled() or (self._hov < 0.01 and self._rip is None and self._glow < 0.01):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(rect, self._radius(), self._radius())
        p.setClipPath(clip)
        if self._hov >= 0.01:
            g = QRadialGradient(self._mouse, max(self.width(), 90) * 0.85)
            g.setColorAt(0.0, QColor(255, 255, 255, int(52 * self._hov)))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.fillRect(rect, g)
        if self._glow >= 0.01:
            p.fillRect(rect, QColor(255, 255, 255, int(30 * self._glow)))
        if self._rip is not None:
            c, k = self._rip
            R = math.hypot(self.width(), self.height()) * k
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, int(70 * (1 - k))))
            p.drawEllipse(c, R, R)

    def set_icon(self, name: str | None) -> None:
        self._icon_name = name or ""
        if self._icon_name:
            self._apply_icon()
        else:
            self.setIcon(QIcon())

    def set_variant(self, variant: str) -> None:
        self._variant = variant
        self.setProperty("variant", variant)
        repolish(self)
        self._apply_icon()

    def _apply_icon(self) -> None:
        if self._icon_name:
            self.setIcon(icons.icon(self._icon_name, _ICON_COLORS.get(self._variant, T["text_2"]), self._icon_size))

    def set_loading(self, on: bool) -> None:
        if on and self._timer is None:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(90)
        elif not on and self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
            self._apply_icon()

    def _tick(self) -> None:
        from PySide6.QtGui import QIcon, QPixmap, QTransform

        self._spin = (self._spin + 45) % 360
        base = icons.pixmap("refresh", _ICON_COLORS.get(self._variant, "#fff"), self._icon_size)
        rot = base.transformed(QTransform().rotate(self._spin), Qt.SmoothTransformation)
        self.setIcon(QIcon(rot))


class SplitButton(QWidget):
    """Главная кнопка с отделённой частью-стрелкой (выпадающее меню)."""
    clicked = Signal()

    def __init__(self, text: str, icon_name: str, menu, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.main = Button(text, icon_name, "primary", "lg")
        self.main.setProperty("seg", "left")
        self.arrow = Button("", "chevron-down", "primary", "lg")
        self.arrow.setProperty("seg", "right")
        self.arrow.setMenu(menu)
        self.arrow.setFixedWidth(44)
        self.main.clicked.connect(self.clicked)
        lay.addWidget(self.main, 1)
        lay.addWidget(self.arrow)
        add_glow(self, T["primary"], 26, 0.40, 6)

    def set_state(self, text: str, icon_name: str, variant: str) -> None:
        self.main.setText(text)
        self.main.set_icon(icon_name)
        for b in (self.main, self.arrow):
            b.set_variant(variant)
            b.setProperty("seg", "left" if b is self.main else "right")
            repolish(b)
        add_glow(self, T["primary"] if variant == "primary" else T["danger"], 26, 0.40, 6)


class WindowButton(Button):
    def __init__(self, icon_name: str, close: bool = False, parent=None):
        super().__init__("", icon_name, "ghost", "md", parent, icon_size=16)
        self.setProperty("variant", "")
        self.setProperty("win", "close" if close else "true")
        self.setFixedSize(34, 28)
        self._close = close
        self.setFocusPolicy(Qt.NoFocus)

    def enterEvent(self, e):
        if self._close:
            self.setIcon(icons.icon(self._icon_name, "#ffffff", 16))
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setIcon(icons.icon(self._icon_name, T["text_2"], 16))
        super().leaveEvent(e)


def icon_button(name: str, tooltip: str = "", size: int = 28, icon_size: int = 16) -> Button:
    b = Button("", name, "ghost", "sm", icon_size=icon_size)
    b.setProperty("size", "sm")
    b.setFixedSize(size, size)
    b.setStyleSheet("QPushButton { min-height: 0; padding: 0; }")
    if tooltip:
        b.setToolTip(tooltip)
        b.setAccessibleName(tooltip)
    return b


# --------------------------------------------------------------------------- переключатель
class Toggle(QAbstractButton):
    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get(self) -> float:
        return self._pos

    def _set(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get, _set)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off, on = QColor(255, 255, 255, 36), QColor(T["primary"])
        k = self._pos
        col = QColor(int(off.red() + (on.red() - off.red()) * k), int(off.green() + (on.green() - off.green()) * k),
                     int(off.blue() + (on.blue() - off.blue()) * k), int(off.alpha() + (255 - off.alpha()) * k))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        if not self.isEnabled():
            p.setOpacity(0.45)
        x = 3 + k * (40 - 22)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, 3, 16, 16))


# --------------------------------------------------------------------------- плитки и значки
class IconTile(QWidget):
    def __init__(self, icon_name: str, tone_name: str = "blue", size: int = 28, solid: bool = False, parent=None):
        super().__init__(parent)
        self._icon, self._tone, self._solid = icon_name, tone_name, solid
        self._size = size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_icon(self, icon_name: str, tone_name: str | None = None) -> None:
        self._icon = icon_name
        if tone_name:
            self._tone = tone_name
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(tone(self._tone))
        radius = max(7, self._size * 0.29)
        if self._solid:
            g = QLinearGradient(0, 0, 0, self._size)
            g.setColorAt(0, col.lighter(115))
            g.setColorAt(1, col)
            p.setBrush(g)
        else:
            bg = QColor(col)
            bg.setAlphaF(0.20)
            p.setBrush(bg)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, self._size, self._size), radius, radius)
        isz = int(self._size * 0.58)
        pm = icons.pixmap(self._icon, "#ffffff" if self._solid else col.lighter(125).name(), isz)
        p.drawPixmap(int((self._size - isz) / 2), int((self._size - isz) / 2), pm)


class ModelLogo(QWidget):
    """Монограмма модели на градиентной плитке."""
    PALETTE = ["#3b78ff", "#8b5cf6", "#14b8a6", "#f59e0b", "#ec4899", "#22b8e8", "#f97316"]

    def __init__(self, name: str, key: str = "", size: int = 28, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.set_model(name, key)

    def set_model(self, name: str, key: str = "") -> None:
        words = [w for w in name.replace("_", " ").split() if w]
        self._text = ((words[0][0] + (words[1][0] if len(words) > 1 and not words[1][0].isdigit() else "")) if words else "?").upper()
        self._color = QColor(self.PALETTE[sum(map(ord, key or name)) % len(self.PALETTE)])
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, self._size, self._size)
        g.setColorAt(0, self._color.lighter(120))
        g.setColorAt(1, self._color.darker(130))
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        r = self._size * 0.28
        p.drawRoundedRect(QRectF(0, 0, self._size, self._size), r, r)
        p.setPen(QColor("#fff"))
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(int(self._size * 0.42))
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self._text)


class LogoTile(QWidget):
    """Знак бренда: лист промта + искра «кузницы»."""

    def __init__(self, size: int = 44, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size
        self.refresh_theme()

    def refresh_theme(self) -> None:
        add_glow(self, T.get("violet", T["primary"]), 22, 0.42, 3)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = float(self._size)
        # фон
        g = QLinearGradient(0, 0, s, s)
        g.setColorAt(0, QColor(T.get("logo_a", T["primary"])))
        g.setColorAt(1, QColor(T.get("logo_b", T["violet"])))
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0.5, 0.5, s - 1, s - 1), s * 0.28, s * 0.28)
        # лист
        sheet = QRectF(s * 0.22, s * 0.18, s * 0.48, s * 0.58)
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawRoundedRect(sheet, 3.5, 3.5)
        # строки промта
        p.setBrush(QColor(T.get("logo_a", T["primary"])))
        for i, w in enumerate((0.62, 0.78, 0.48)):
            y = sheet.top() + s * (0.14 + i * 0.13)
            p.drawRoundedRect(QRectF(sheet.left() + s * 0.07, y, sheet.width() * w, s * 0.045), 1.5, 1.5)
        # искра «кузницы»
        cx, cy = s * 0.74, s * 0.26
        spark = QPainterPath()
        tips = ((0, -1), (0.55, -0.55), (1, 0), (0.55, 0.55), (0, 1), (-0.55, 0.55), (-1, 0), (-0.55, -0.55))
        for i, (dx, dy) in enumerate(tips):
            r = s * (0.12 if i % 2 == 0 else 0.055)
            pt = QPointF(cx + dx * r, cy + dy * r)
            if i == 0:
                spark.moveTo(pt)
            else:
                spark.lineTo(pt)
        spark.closeSubpath()
        p.setBrush(QColor("#ffffff"))
        p.drawPath(spark)
        p.setBrush(QColor(255, 214, 110, 240))
        p.drawEllipse(QRectF(cx - s * 0.035, cy - s * 0.035, s * 0.07, s * 0.07))


class StatusDot(QWidget):
    def __init__(self, color: str = T["success"], size: int = 8, pulse: bool = True, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._d = size
        self._phase = 0.0
        self.setFixedSize(size + 8, size + 8)
        self._pulse = pulse
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(2000)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on)
        self.set_pulse(pulse)

    def set_color(self, color: str, pulse: bool | None = None) -> None:
        self._color = QColor(color)
        if pulse is not None:
            self.set_pulse(pulse)
        self.update()

    def set_pulse(self, on: bool) -> None:
        self._pulse = on
        if on and not _reduced_motion():
            self._anim.start()
        else:
            self._anim.stop()
            self._phase = 0.0
        self.update()

    def _on(self, v) -> None:
        self._phase = float(v)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self.rect().center()
        if self._pulse:
            halo = QColor(self._color)
            halo.setAlphaF(0.35 * (1 - self._phase))
            r = self._d / 2 + 4 * self._phase
            p.setBrush(halo)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(c.x() - r + 0.5, c.y() - r + 0.5, 2 * r, 2 * r))
        p.setBrush(self._color)
        p.setPen(Qt.NoPen)
        r = self._d / 2
        p.drawEllipse(QRectF(c.x() - r + 0.5, c.y() - r + 0.5, self._d, self._d))


def _reduced_motion() -> bool:
    import os
    return os.environ.get("PROMPTFORGE_REDUCED_MOTION") == "1"


# --------------------------------------------------------------------------- flow layout
class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect: QRect, test: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for it in self._items:
            sz = it.sizeHint()
            if x + sz.width() > r.right() + 1 and line_h > 0:
                x, y, line_h = r.x(), y + line_h + self._sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), sz))
            x += sz.width() + self._sp
            line_h = max(line_h, sz.height())
        return y + line_h - rect.y() + m.bottom()


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        it = layout.takeAt(0)
        w = it.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())


# --------------------------------------------------------------------------- секции и прокрутка
class VScroll(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        self.body = QWidget()
        self.body.setAutoFillBackground(False)
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 8, 0)
        self.lay.setSpacing(16)
        self.setWidget(self.body)
        anim.smooth_scroll(self)


class Section(QFrame):
    """Заголовок (иконка, название, действия справа, сворачивание) + тело."""

    def __init__(self, title: str, icon_name: str | None = None, collapsible: bool = False, card: bool = True,
                 info: str = "", parent=None):
        super().__init__(parent)
        if card:
            self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(12)
        self.head = QHBoxLayout()
        self.head.setSpacing(8)
        if icon_name:
            ic = QLabel()
            ic.setPixmap(icons.pixmap(icon_name, "#8fa6ff", 18))
            self.head.addWidget(ic)
        self.title = label(title, "H2")
        self.head.addWidget(self.title)
        if info:
            i = QLabel()
            i.setPixmap(icons.pixmap("info", T["text_3"], 14))
            i.setToolTip(info)
            self.head.addWidget(i)
        self.head.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.head.addLayout(self.actions)
        self._chev: Button | None = None
        self._open = True
        if collapsible:
            self._chev = icon_button("chevron-up", "", 26, 16)
            self._chev.clicked.connect(self.toggle)
            self.head.addWidget(self._chev)
        outer.addLayout(self.head)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(10)
        outer.addWidget(self.body)
        self._anim = QPropertyAnimation(self.body, b"maximumHeight", self)
        self._anim.setDuration(0 if _reduced_motion() else 240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._done)

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def toggle(self) -> None:
        self.set_open(not self._open)

    def set_open(self, open_: bool) -> None:
        if open_ == self._open:
            return
        self._open = open_
        if self._chev:
            self._chev.set_icon("chevron-up" if open_ else "chevron-down")
        self._anim.stop()
        if open_:
            self.body.setVisible(True)
            self._anim.setStartValue(0)
            self._anim.setEndValue(self.body.sizeHint().height())
        else:
            self._anim.setStartValue(self.body.height())
            self._anim.setEndValue(0)
        self._anim.start()

    def _done(self) -> None:
        if self._open:
            self.body.setMaximumHeight(QWIDGETSIZE_MAX)
        else:
            self.body.setVisible(False)


# --------------------------------------------------------------------------- ввод текста
class CountedEdit(QPlainTextEdit):
    """Текстовое поле с сигналом длины (для счётчиков N/LIMIT)."""
    lengthChanged = Signal(int)
    submitted = Signal()

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setTabChangesFocus(True)
        self.textChanged.connect(lambda: self.lengthChanged.emit(len(self.toPlainText())))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and e.modifiers() & Qt.ControlModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(e)


def counter_text(n: int, limit: int) -> str:
    return f"{n}/{limit}"


# --------------------------------------------------------------------------- уведомления
class Toast(QFrame):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setProperty("kind", "info")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.icon, 0, Qt.AlignTop)
        lay.addWidget(self.text, 1)
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)
        self.hide()

    def show_message(self, text: str, kind: str = "info") -> None:
        names = {"info": ("info", "#7ea6ff"), "success": ("check", T["success"]),
                 "warning": ("alert", T["warning"]), "error": ("alert", "#ff6b6e")}
        ic, col = names.get(kind, names["info"])
        self.icon.setPixmap(icons.pixmap(ic, col, 18))
        self.text.setText(text)
        self.setProperty("kind", kind)
        repolish(self)
        self.setFixedWidth(min(560, max(280, self.parentWidget().width() - 80)))
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setDuration(0 if _reduced_motion() else 160)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        if anim.enabled():                     # плавный подъём снизу
            end = self.pos()
            self._slide = QPropertyAnimation(self, b"pos", self)
            self._slide.setDuration(260)
            self._slide.setEasingCurve(QEasingCurve.OutBack)
            self._slide.setStartValue(QPoint(end.x(), end.y() + 18))
            self._slide.setEndValue(end)
            self._slide.start()
        self._hide_timer.start(7000 if kind == "error" else 3800)

    def reposition(self) -> None:
        p = self.parentWidget()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 52)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._after_fade)
        self._anim.start()

    def _after_fade(self) -> None:
        try:
            self._anim.finished.disconnect(self._after_fade)
        except (RuntimeError, TypeError):
            pass
        if self._fx.opacity() < 0.05:
            self.hide()


class Skeleton(QWidget):
    """Мерцающие полосы — «ждём первый токен»."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1400)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def _on(self, v):
        self._t = float(v)
        self.update()

    def showEvent(self, e):
        if not _reduced_motion():
            self._anim.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._anim.stop()
        super().hideEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        widths = [0.92, 0.78, 0.86, 0.55, 0.7]
        y = 16
        for w in widths:
            rect = QRectF(14, y, (self.width() - 28) * w, 10)
            g = QLinearGradient(rect.left(), 0, rect.right(), 0)
            pos = (self._t * 1.6) - 0.3
            base, hi = QColor(255, 255, 255, 14), QColor(120, 150, 255, 46)
            g.setColorAt(0, base)
            g.setColorAt(max(0.0, min(1.0, pos)), hi)
            g.setColorAt(1, base)
            p.setBrush(g)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, 5, 5)
            y += 22
