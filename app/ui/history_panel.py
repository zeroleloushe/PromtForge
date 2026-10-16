"""История версий текущей генерации."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from app.core.history_manager import Version
from app.ui.theme import T
from app.ui.widgets import Button, Section, add_glow, clear_layout, icon_button, label
from app.utils.formatting import fmt_date, snippet
from app.utils.i18n import t


class VersionRow(QFrame):
    open_clicked = Signal(object)

    def __init__(self, v: Version, current: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("VersionRow")
        self.setProperty("current", current)
        self.setFixedHeight(64)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 12, 0)
        lay.setSpacing(12)
        badge = QLabel(f"v{v.version}")
        badge.setObjectName("VersionBadge")
        badge.setProperty("current", current)
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(44, 44)
        lay.addWidget(badge)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addStretch(1)
        title_text = v.edit_request or t("version_base")
        if current:
            title = label(t("version_current"), "Success")
            sub = f"{fmt_date(v.date)} · {snippet(title_text, 22)}"
        else:
            title = label(snippet(title_text, 26), "")
            title.setStyleSheet("font-weight:500;font-size:12px;")
            sub = fmt_date(v.date)
        sub_lb = label(sub, "Faint")
        for lb in (title, sub_lb):
            lb.setMinimumWidth(10)
        col.addWidget(title)
        col.addWidget(sub_lb)
        col.addStretch(1)
        lay.addLayout(col, 1)
        self.setToolTip(title_text)
        btn = Button(t("open"), None, "secondary", "sm")
        btn.setEnabled(not current)
        btn.clicked.connect(lambda: self.open_clicked.emit(v))
        lay.addWidget(btn)
        if current:
            add_glow(self, T["primary"], 18, 0.30, 0)


class HistoryPanel(Section):
    open_version = Signal(object)
    compare_requested = Signal()
    generations_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(t("history_title"), "clock", True, True, "", parent)
        # по умолчанию свёрнута — место отдаём результату; развернётся при первой версии
        self.gens_btn = icon_button("clock", t("gens_title"), 28, 16)
        self.gens_btn.clicked.connect(self.generations_requested)
        self.actions.addWidget(self.gens_btn)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        self.body_lay.addLayout(self.rows)
        self.empty = label(t("history_empty"), "Muted", wrap=True)
        self.body_lay.addWidget(self.empty)
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.compare_btn = Button(t("compare"), "columns", "secondary", "sm")
        self.compare_btn.clicked.connect(self.compare_requested)
        foot.addWidget(self.compare_btn)
        self.body_lay.addLayout(foot)
        self.set_versions([], None)
        self.set_open(False)

    def set_versions(self, versions: list[Version], current_id: int | None) -> None:
        clear_layout(self.rows)
        for v in sorted(versions, key=lambda x: -x.version):
            row = VersionRow(v, v.id == current_id)
            row.open_clicked.connect(self.open_version)
            self.rows.addWidget(row)
        self.empty.setVisible(not versions)
        self.compare_btn.setEnabled(len(versions) >= 2)
        if versions and not self._open:
            self.set_open(True)
        elif not versions and self._open:
            self.set_open(False)
