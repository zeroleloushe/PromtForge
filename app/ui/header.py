"""Шапка: логотип, вкладки моделей, статус Strata, RAM/VRAM, чат, настройки, кнопки окна."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from app.strata.strata_manager import StrataState
from app.ui import theme
from app.ui.model_tabs import ModelTabs
from app.ui.theme import T
from app.ui.widgets import Button, LogoTile, StatusDot, WindowButton, label, repolish
from app.utils.i18n import t


def _state_style(state: StrataState) -> tuple[str, bool, str]:
    table = {
        StrataState.STOPPED: (T["text_3"], False, "st_stopped"),
        StrataState.STARTING: (T["warning"], True, "st_starting"),
        StrataState.STARTED: (T["teal"], False, "st_started"),
        StrataState.LOADING: (T["warning"], True, "st_loading"),
        StrataState.READY: (T["success"], True, "st_ready"),
        StrataState.GENERATING: (T["primary_hover"], True, "st_generating"),
        StrataState.STOPPING: (T["warning"], True, "st_stopping"),
        StrataState.UNLOADING: (T["warning"], True, "st_unloading"),
        StrataState.ERROR: (T["danger"], False, "st_error"),
    }
    return table[state]


class HeaderBar(QFrame):
    settings_clicked = Signal()
    chat_toggled = Signal(bool)
    minimize = Signal()
    maximize = Signal()
    close = Signal()

    def __init__(self, native_titlebar: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(64)
        self._native = native_titlebar
        self._last_state = StrataState.STOPPED
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(12)

        self.logo = LogoTile(44)
        lay.addWidget(self.logo)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addStretch(1)
        brand = QHBoxLayout()
        brand.setSpacing(0)
        brand.setContentsMargins(0, 0, 0, 0)
        self.brand_left = QLabel(theme.APP_NAME_LEFT)
        self.brand_left.setObjectName("BrandLeft")
        self.brand_right = QLabel(theme.APP_NAME_RIGHT)
        self.brand_right.setObjectName("BrandRight")
        brand.addWidget(self.brand_left)
        brand.addWidget(self.brand_right)
        brand.addStretch(1)
        titles.addLayout(brand)
        self.sub = label(t("app_subtitle"), "Faint")
        self.sub.setMinimumWidth(10)
        self.sub.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.sub.setToolTip(t("app_subtitle_full"))
        titles.addWidget(self.sub)
        titles.addStretch(1)
        lay.addLayout(titles, 0)
        lay.addSpacing(14)

        self.tabs = ModelTabs()
        lay.addWidget(self.tabs, 100)
        lay.addStretch(1)

        self.dot = StatusDot(T["text_3"], 8, False)
        lay.addWidget(self.dot)
        st = QVBoxLayout()
        st.setSpacing(0)
        st.addStretch(1)
        st.addWidget(label("Strata", "H2"))
        self.state_label = QLabel(t("st_stopped"))
        self.state_label.setStyleSheet(f"color:{T['text_3']};font-size:11px;")
        st.addWidget(self.state_label)
        st.addStretch(1)
        lay.addLayout(st)
        lay.addSpacing(4)

        mem = QVBoxLayout()
        mem.setSpacing(1)
        mem.addStretch(1)
        self.ram = QLabel("RAM  —")
        self.vram = QLabel("VRAM  —")
        for lb in (self.ram, self.vram):
            lb.setStyleSheet(f"color:{T['text_2']};font-size:11px;")
            mem.addWidget(lb)
        mem.addStretch(1)
        lay.addLayout(mem)
        lay.addSpacing(6)

        self.chat_btn = Button(t("chat_header"), "message", "secondary")
        self.chat_btn.setProperty("chat", "true")
        self.chat_btn.setCheckable(True)
        self.chat_btn.setToolTip(t("chat_tab_tip"))
        self.chat_btn.setAccessibleName(t("chat_tab_tip"))
        self.chat_btn.clicked.connect(lambda: self.chat_toggled.emit(self.chat_btn.isChecked()))
        lay.addWidget(self.chat_btn)

        self.settings_btn = Button(t("settings"), "sliders", "secondary")
        self.settings_btn.clicked.connect(self.settings_clicked)
        lay.addWidget(self.settings_btn)

        self.win_box = QWidget()
        wl = QHBoxLayout(self.win_box)
        wl.setContentsMargins(6, 0, 0, 0)
        wl.setSpacing(2)
        for icon_name, sig, close in (("minus", self.minimize, False), ("square", self.maximize, False), ("x", self.close, True)):
            b = WindowButton(icon_name, close)
            b.clicked.connect(sig)
            wl.addWidget(b)
        lay.addWidget(self.win_box)
        self.win_box.setVisible(not native_titlebar)

    def refresh_theme(self) -> None:
        self.logo.refresh_theme()
        repolish(self.brand_left)
        repolish(self.brand_right)
        for lb in (self.ram, self.vram):
            lb.setStyleSheet(f"color:{T['text_2']};font-size:11px;")
        self.set_state(self._last_state, self.state_label.toolTip())

    def set_chat_mode(self, on: bool) -> None:
        self.chat_btn.blockSignals(True)
        self.chat_btn.setChecked(on)
        self.chat_btn.blockSignals(False)
        self.chat_btn.setText(t("chat_exit_short") if on else t("chat_header"))
        self.chat_btn.set_icon("x" if on else "message")
        self.chat_btn.set_variant("outline" if on else "secondary")
        repolish(self.chat_btn)
        self.chat_btn.set_pulse(bool(on))

    def set_state(self, state: StrataState, detail: str = "") -> None:
        self._last_state = state
        color, pulse, key = _state_style(state)
        self.dot.set_color(color, pulse)
        text = t(key)
        if state == StrataState.READY and getattr(self, "unloaded", False):
            text += " · " + t("st_unloaded")
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"color:{color};font-size:11px;font-weight:500;")
        self.state_label.setToolTip(detail)

    def set_metrics(self, ram: tuple[float, float] | None, vram: tuple[float, float] | None) -> None:
        self.ram.setText(f"RAM  {ram[0]:.1f} GB" if ram else "RAM  —")
        self.vram.setText(f"VRAM  {vram[0]:.1f} GB" if vram else "VRAM  —")

    def mousePressEvent(self, e):
        if not self._native and e.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if not self._native and e.button() == Qt.LeftButton:
            self.maximize.emit()
        super().mouseDoubleClickEvent(e)
