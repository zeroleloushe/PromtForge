"""Анимации интерфейса: плавное появление, каскад, переключение страниц, плавный скролл, спиннер, «печатающие» точки.

Всё отключается одним флагом (Настройки → «Анимации») или переменной PROMPTFORGE_REDUCED_MOTION=1.
Эффекты прозрачности всегда снимаются после анимации, чтобы не тормозить перерисовку и не конфликтовать с тенями.
"""
from __future__ import annotations

import math
import os

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QObject, QPropertyAnimation, QRectF, QTimer, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QGraphicsOpacityEffect, QStackedWidget, QWidget

_ENABLED = True


def set_enabled(on: bool) -> None:
    global _ENABLED
    _ENABLED = bool(on)


def enabled() -> bool:
    return _ENABLED and os.environ.get("PROMPTFORGE_REDUCED_MOTION") != "1"


def fade_in(w: QWidget, ms: int = 240, delay: int = 0, slide: int = 0) -> None:
    """Плавное появление. slide > 0 — лёгкий подъём снизу (px). Не трогает виджеты с тенью."""
    if not enabled() or w is None or w.graphicsEffect() is not None:
        return
    fx = QGraphicsOpacityEffect(w)
    fx.setOpacity(0.0)
    w.setGraphicsEffect(fx)
    anim = QPropertyAnimation(fx, b"opacity", w)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    move: QPropertyAnimation | None = None
    if slide:
        start = w.pos()
        w.move(start.x(), start.y() + slide)
        move = QPropertyAnimation(w, b"pos", w)
        move.setDuration(ms)
        move.setStartValue(w.pos())
        move.setEndValue(start)
        move.setEasingCurve(QEasingCurve.OutCubic)

    def done() -> None:
        if w.graphicsEffect() is fx:
            w.setGraphicsEffect(None)

    anim.finished.connect(done)

    def start() -> None:
        anim.start()
        if move is not None:
            move.start()

    if delay > 0:
        QTimer.singleShot(delay, start)
    else:
        start()


def stagger(widgets: list, step: int = 28, ms: int = 240, limit: int = 14, slide: int = 6) -> None:
    """Каскадное появление списка (первые limit элементов, остальные — сразу)."""
    if not enabled():
        return
    for i, w in enumerate(widgets[:limit]):
        fade_in(w, ms, delay=i * step, slide=slide)


def fade_window(w: QWidget, ms: int = 220) -> None:
    """Плавное появление окна/диалога через windowOpacity."""
    if not enabled():
        return
    w.setWindowOpacity(0.0)
    anim = QPropertyAnimation(w, b"windowOpacity", w)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.finished.connect(lambda: w.setWindowOpacity(1.0))
    anim.start()


class FadeStack(QStackedWidget):
    """QStackedWidget: высота по текущей странице (не по самой высокой), появление со сдвигом."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ready = False
        self.currentChanged.connect(self._changed)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self._ready = True

    def sizeHint(self):  # noqa: N802
        w = self.currentWidget()
        return w.sizeHint() if w is not None else super().sizeHint()

    def minimumSizeHint(self):  # noqa: N802
        w = self.currentWidget()
        return w.minimumSizeHint() if w is not None else super().minimumSizeHint()

    def _changed(self, _i: int) -> None:
        w = self.currentWidget()
        self.updateGeometry()
        if self.parentWidget() is not None:
            self.parentWidget().updateGeometry()
        if self._ready and self.isVisible() and w is not None:
            fade_in(w, 220, slide=8)


class _Smooth(QObject):
    """Плавная прокрутка колесом мыши (тачпад с pixelDelta и Ctrl+колесо не трогаем)."""

    def __init__(self, area: QAbstractScrollArea):
        super().__init__(area)
        self.bar = area.verticalScrollBar()
        self.anim = QPropertyAnimation(self.bar, b"value", self)
        self.anim.setDuration(240)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.target = 0
        self.bar.actionTriggered.connect(lambda *_: self.anim.stop())     # перетаскивание ползунка — без анимации
        area.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() == QEvent.Wheel and enabled():
            if ev.modifiers() != Qt.NoModifier or not ev.pixelDelta().isNull():
                return False
            dy = ev.angleDelta().y()
            if dy == 0 or self.bar.maximum() == 0:
                return False
            base = self.target if self.anim.state() == QAbstractAnimation.State.Running else self.bar.value()
            self.target = max(self.bar.minimum(), min(self.bar.maximum(), int(base - dy / 120 * 96)))
            self.anim.stop()
            self.anim.setStartValue(self.bar.value())
            self.anim.setEndValue(self.target)
            self.anim.start()
            ev.accept()
            return True
        return False


def smooth_scroll(area: QAbstractScrollArea) -> None:
    area._smooth = _Smooth(area)        # держим ссылку


class Spinner(QWidget):
    """Вращающаяся дуга (для «выполняется»)."""

    def __init__(self, color: str = "#ffb84d", size: int = 15, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._angle = 0
        self.setFixedSize(size, size)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0)
        self._anim.setEndValue(360)
        self._anim.setDuration(900)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on)

    def _on(self, v) -> None:
        self._angle = int(v)
        self.update()

    def start(self) -> None:
        self.show()
        if enabled():
            self._anim.start()
        self.update()

    def stop(self) -> None:
        self._anim.stop()
        self.hide()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self._color, 2.0)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        r = QRectF(2, 2, self.width() - 4, self.height() - 4)
        p.drawArc(r, -self._angle * 16, 270 * 16)


class TypingDots(QWidget):
    """Три прыгающие точки — «модель печатает»."""

    def __init__(self, color: str = "#8fa6ff", parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._phase = 0.0
        self.setFixedSize(34, 16)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1100)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on)

    def _on(self, v) -> None:
        self._phase = float(v)
        self.update()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        if enabled():
            self._anim.start()

    def hideEvent(self, e):  # noqa: N802
        super().hideEvent(e)
        self._anim.stop()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for i in range(3):
            k = (self._phase - i * 0.16) % 1.0
            wave = math.sin(k * math.pi) if k < 0.5 else 0.0      # короткий «подскок» каждой точки по очереди
            c = QColor(self._color)
            c.setAlphaF(0.35 + 0.65 * wave)
            p.setBrush(c)
            y = 9 - 5 * wave
            p.drawEllipse(QRectF(4 + i * 10, y - 2.5, 5, 5))
