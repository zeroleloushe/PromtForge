"""Управление Strata: UNLOAD / STOP и режим после STOP."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QRadioButton, QVBoxLayout, QWidget

from app.strata.strata_manager import StrataState
from app.ui.theme import T
from app.ui.widgets import Button, Toggle, add_glow, label
from app.utils.i18n import t

MODES = ["stop", "unload", "shutdown"]


class StrataPanel(QFrame):
    unload_clicked = Signal()
    stop_clicked = Signal()
    mode_changed = Signal(str)
    autostart_changed = Signal(bool)

    def __init__(self, mode: str = "unload", autostart: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(10)
        row = QVBoxLayout()
        row.setSpacing(8)
        self.unload_btn = Button("UNLOAD", "download", "secondary")
        self.unload_btn.setToolTip(t("unload_tip"))
        self.stop_btn = Button("STOP", "shield", "danger")
        self.stop_btn.setToolTip(t("stop_tip"))
        add_glow(self.stop_btn, T["danger"], 18, 0.28, 0)
        row.addWidget(self.unload_btn)
        row.addWidget(self.stop_btn)
        left.addLayout(row)
        self.hint = label("", "Faint", wrap=True)
        self.hint.hide()
        left.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(left, 0)
        sep = QFrame()
        sep.setObjectName("Sep")
        lay.addWidget(sep)
        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(label(t("after_stop"), "Muted"))
        self.group = QButtonGroup(self)
        for i, m in enumerate(MODES):
            r = QRadioButton(t(f"after_stop_{m}"))
            r.setChecked(m == mode)
            self.group.addButton(r, i)
            right.addWidget(r)
        right.addSpacing(2)
        ar = QHBoxLayout()
        ar.setSpacing(8)
        alb = label(t("autostart_quick"), "Muted")
        alb.setToolTip(t("autostart_quick_tip"))
        self.autostart = Toggle(autostart)
        self.autostart.setToolTip(t("autostart_quick_tip"))
        self.autostart.setAccessibleName(t("autostart_quick"))
        self.autostart.toggled.connect(self.autostart_changed)
        ar.addWidget(alb, 1)
        ar.addWidget(self.autostart)
        right.addLayout(ar)
        lay.addLayout(right, 1)
        self.unload_btn.clicked.connect(self.unload_clicked)
        self.stop_btn.clicked.connect(self.stop_clicked)
        self.group.idClicked.connect(lambda i: self.mode_changed.emit(MODES[i]))

    def set_autostart(self, on: bool) -> None:
        self.autostart.blockSignals(True)
        self.autostart.setChecked(on)
        self.autostart.blockSignals(False)

    def mode(self) -> str:
        return MODES[max(0, self.group.checkedId())]

    def set_state(self, state: StrataState) -> None:
        busy = state in (StrataState.UNLOADING, StrataState.STOPPING)
        self.unload_btn.setEnabled(not busy)
        self.unload_btn.set_loading(state == StrataState.UNLOADING)
        self.stop_btn.setEnabled(state != StrataState.STOPPING)
        self.stop_btn.set_pulse(state == StrataState.GENERATING)
        self.unload_btn.setText("UNLOAD")
        self.hint.setText("")
