"""Вкладки моделей в шапке: если не помещаются — прокручиваются (стрелки ‹ ›, колесо мыши), «+» всегда на виду."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QMenu, QScrollArea, QSizePolicy, QWidget)

from app.core.model_manager import Model
from app.ui import icons
from app.ui.theme import T
from app.ui.widgets import Button, ModelLogo, add_glow, icon_button
from app.utils.i18n import t

TAB_MAX_TEXT = 150      # длинные названия сокращаются «…» (полное — во всплывающей подсказке)
STEP = 180


class _TabScroll(QScrollArea):
    """Горизонтальная лента вкладок без полос прокрутки; её ширина = ширине вкладок, но может сжиматься."""

    def __init__(self, inner: QWidget):
        super().__init__()
        self.setWidget(inner)
        self.setWidgetResizable(True)       # лента растягивается до ширины окна; если вкладки шире — появляется прокрутка
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.viewport().setStyleSheet("background:transparent;")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setFixedHeight(60)           # запас по высоте под свечение активной вкладки

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.widget().sizeHint().width() + 4, 60)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(120, 60)

    def wheelEvent(self, e):  # noqa: N802
        d = e.angleDelta().y() or e.angleDelta().x()
        sb = self.horizontalScrollBar()
        sb.setValue(sb.value() - d)
        e.accept()


class ModelTabs(QWidget):
    selected = Signal(str)
    add_clicked = Signal(object)  # кнопка «+», чтобы показать меню рядом с ней
    delete_requested = Signal(str)  # ПКМ по вкладке -> «Удалить модель»

    def __init__(self, parent=None):
        super().__init__(parent)
        self._inner = QWidget()
        self._inner.setAttribute(Qt.WA_TranslucentBackground)
        self._ilay = QHBoxLayout(self._inner)
        self._ilay.setContentsMargins(4, 8, 4, 8)
        self._ilay.setSpacing(10)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, Button] = {}

        self._scroll = _TabScroll(self._inner)
        self._left = icon_button("chevron-left", "", 28, 16)
        self._right = icon_button("chevron-right", "", 28, 16)
        for b, d in ((self._left, -STEP), (self._right, STEP)):
            b.setAutoRepeat(True)
            b.setAutoRepeatInterval(60)
            b.clicked.connect(lambda _=False, dx=d: self._nudge(dx))
            b.hide()
        self._add = Button("", "plus", "secondary")
        self._add.setProperty("tab", "model")
        self._add.setFixedSize(44, 44)
        self._add.setToolTip(t("model_add"))
        self._add.setAccessibleName(t("model_add"))
        self._add.clicked.connect(lambda: self.add_clicked.emit(self._add))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._left)
        lay.addWidget(self._scroll, 1)
        lay.addWidget(self._right)
        lay.addSpacing(4)
        lay.addWidget(self._add)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        sb = self._scroll.horizontalScrollBar()
        sb.rangeChanged.connect(lambda *_: self._update_arrows())
        sb.valueChanged.connect(lambda *_: self._update_arrows())

    # --- прокрутка -------------------------------------------------------------------------
    def _nudge(self, dx: int) -> None:
        sb = self._scroll.horizontalScrollBar()
        sb.setValue(sb.value() + dx)

    def _update_arrows(self) -> None:
        sb = self._scroll.horizontalScrollBar()
        over = sb.maximum() > 0
        self._left.setVisible(over)
        self._right.setVisible(over)
        self._left.setEnabled(sb.value() > 0)
        self._right.setEnabled(sb.value() < sb.maximum())

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        QTimer.singleShot(0, self._update_arrows)

    def _reveal_current(self) -> None:
        for mid, b in self._buttons.items():
            if b.isChecked():
                self._scroll.ensureWidgetVisible(b, 40, 0)
                break
        self._update_arrows()

    # --- модели ------------------------------------------------------------------------------
    def set_models(self, models: list[Model], current: str) -> None:
        for b in self._buttons.values():
            self._group.removeButton(b)
            b.setParent(None)
            b.deleteLater()
        self._buttons.clear()
        while self._ilay.count():                 # убрать старые элементы вместе с растяжкой
            self._ilay.takeAt(0)
        for m in models:
            b = Button(self._elide(m.name), None, "secondary")
            b.setProperty("tab", "model")
            b.setCheckable(True)
            b.setIcon(QIcon(ModelLogo(m.name, m.id, 24).grab()))
            b.setIconSize(QSize(24, 24))
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            b.setToolTip(m.name + ("" if m.enabled else f" — {t('model_disabled')}"))
            b.toggled.connect(lambda on, btn=b: self._glow(btn, on))
            b.clicked.connect(lambda _=False, mid=m.id: self.selected.emit(mid))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, btn=b, mid=m.id: self._menu(btn, mid, pos))
            b.setToolTip(b.toolTip() + "\n" + t("model_tab_menu_tip"))
            self._group.addButton(b)
            self._buttons[m.id] = b
            self._ilay.addWidget(b)
        self._ilay.addStretch(1)
        # потолок ширины = ширина всех вкладок + «+»: пока всё помещается, «+» стоит сразу за последней вкладкой
        n = len(self._buttons)
        natural = sum(b.sizeHint().width() for b in self._buttons.values()) + 10 * max(0, n - 1) + 8
        self.setMaximumWidth(natural + 4 + 6 + 4 + 44)
        self.set_current(current)
        QTimer.singleShot(0, self._reveal_current)

    def _menu(self, btn: Button, model_id: str, pos) -> None:
        menu = QMenu(btn)
        menu.addAction(icons.icon("trash", "#ff8a8d", 16), t("model_delete"), lambda: self.delete_requested.emit(model_id))
        menu.exec(btn.mapToGlobal(pos))

    def _elide(self, text: str) -> str:
        return self.fontMetrics().elidedText(text, Qt.ElideRight, TAB_MAX_TEXT)

    def set_current(self, model_id: str) -> None:
        b = self._buttons.get(model_id)
        if b:
            b.setChecked(True)
            QTimer.singleShot(0, self._reveal_current)

    @staticmethod
    def _glow(btn: Button, on: bool) -> None:
        if on:
            add_glow(btn, T["primary"], 22, 0.45, 0)
        else:
            btn.setGraphicsEffect(None)
