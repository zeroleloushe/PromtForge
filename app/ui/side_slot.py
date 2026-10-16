"""Сворачиваемая боковая колонка: панель + тонкая рейка «развернуть»."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from app.ui import anim
from app.ui.widgets import Button


class SideSlot(QWidget):
    """Панель фиксированной ширины; при сворачивании остаётся вертикальная рейка."""

    toggled = Signal(bool)

    def __init__(self, content: QWidget, width: int, *, edge: str = "left",
                 expand_tip: str = "", collapse_tip: str = "", icon: str = "brush", parent=None):
        super().__init__(parent)
        self._width = width
        self._open = True
        self._edge = edge
        self._collapse_tip = collapse_tip
        self._expand_tip = expand_tip

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.rail = QFrame()
        self.rail.setObjectName("SideRail")
        self.rail.setFixedWidth(40)
        self.rail.hide()
        rl = QVBoxLayout(self.rail)
        rl.setContentsMargins(4, 10, 4, 10)
        rl.setSpacing(8)
        self.expand_btn = Button("", icon, "ghost", "sm")
        self.expand_btn.setFixedSize(32, 32)
        self.expand_btn.setToolTip(expand_tip)
        self.expand_btn.setAccessibleName(expand_tip)
        self.expand_btn.clicked.connect(lambda: self.set_open(True))
        rl.addWidget(self.expand_btn, 0, Qt.AlignHCenter)
        rl.addSpacing(4)
        rl.addStretch(1)
        self.rail_lay = rl

        self.host = QWidget()
        self.host.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        hl = QHBoxLayout(self.host)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)

        self.collapse_btn = Button("", "chevron-left" if edge == "left" else "chevron-right", "ghost", "sm")
        self.collapse_btn.setFixedSize(22, 36)
        self.collapse_btn.setToolTip(collapse_tip)
        self.collapse_btn.setAccessibleName(collapse_tip)
        self.collapse_btn.setStyleSheet("QPushButton { min-height: 0; padding: 0; border-radius: 8px; }")
        self.collapse_btn.clicked.connect(lambda: self.set_open(False))

        content.setParent(self.host)
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if edge == "left":
            hl.addWidget(content, 1)
            hl.addWidget(self.collapse_btn, 0, Qt.AlignVCenter)
        else:
            hl.addWidget(self.collapse_btn, 0, Qt.AlignVCenter)
            hl.addWidget(content, 1)

        if edge == "left":
            root.addWidget(self.rail, 0)
            root.addWidget(self.host, 0)
        else:
            root.addWidget(self.host, 0)
            root.addWidget(self.rail, 0)

        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._anim = QPropertyAnimation(self.host, b"maximumWidth", self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_width)
        self._anim.finished.connect(self._done)
        self._lock_open()

    def add_rail_button(self, icon: str, tip: str, callback) -> Button:
        """Быстрая кнопка на свёрнутой рейке: разворачивает панель и выполняет callback (переход на вкладку)."""
        b = Button("", icon, "ghost", "sm")
        b.setFixedSize(32, 32)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.clicked.connect(lambda: (self.set_open(True), callback()))
        self.rail_lay.insertWidget(self.rail_lay.count() - 1, b, 0, Qt.AlignHCenter)
        return b

    def is_open(self) -> bool:
        return self._open

    def set_open(self, open_: bool, animate: bool = True) -> None:
        if open_ == self._open:
            if open_ and self.host.isVisible() and self.host.width() >= self._width - 2:
                return
            if not open_ and self.rail.isVisible():
                return
        self._open = open_
        self.collapse_btn.setToolTip(self._collapse_tip if open_ else self._expand_tip)
        if self._edge == "left":
            self.collapse_btn.set_icon("chevron-left" if open_ else "chevron-right")
        else:
            self.collapse_btn.set_icon("chevron-right" if open_ else "chevron-left")

        self._anim.stop()
        ms = 0 if not animate or not anim.enabled() else 260
        self._anim.setDuration(ms)

        if open_:
            self.rail.hide()
            self.host.show()
            self.collapse_btn.show()
            self.host.setMinimumWidth(0)
            self.host.setMaximumWidth(0)
            self._anim.setStartValue(0)
            self._anim.setEndValue(self._width)
            if ms == 0:
                self._lock_open()
            else:
                self._anim.start()
        else:
            start = max(self.host.width(), 1)
            self.host.setMinimumWidth(0)
            self.host.setMaximumWidth(start)
            self._anim.setStartValue(start)
            self._anim.setEndValue(0)
            if ms == 0:
                self._lock_closed()
            else:
                self._anim.start()
        self.toggled.emit(open_)

    def _on_width(self, v) -> None:
        # simultaneous min=max keeps layout from jumping while still animating
        w = int(v)
        self.host.setMinimumWidth(w)
        self.host.setMaximumWidth(w)

    def _done(self) -> None:
        if self._open:
            self._lock_open()
        else:
            self._lock_closed()
            anim.fade_in(self.rail, 160)

    def _lock_open(self) -> None:
        self.host.setMinimumWidth(self._width)
        self.host.setMaximumWidth(self._width)
        self.host.show()
        self.rail.hide()

    def _lock_closed(self) -> None:
        self.host.hide()
        self.host.setMinimumWidth(0)
        self.host.setMaximumWidth(0)
        self.rail.show()
