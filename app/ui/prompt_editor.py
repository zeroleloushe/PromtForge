"""Центр: выбранные стили, ввод запроса, кнопка «Создать промт», дополнительные параметры."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from app.core import h3
from app.core.model_manager import Model
from app.core.prompt_engine import THINKING_BUDGET, THINKING_LEVELS
from app.core.style_manager import Style
from app.ui import icons
from app.ui.theme import T
from app.ui.widgets import (Button, CountedEdit, ElidedLabel, FlowLayout, IconTile, Section, SplitButton, Toggle, clear_layout,
                            counter_text, icon_button, label, repolish)
from app.utils.i18n import t

REQUEST_LIMIT = 4000
ASPECTS = ["", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "2.39:1"]
CREATIVITY = ["precise", "standard", "bold"]


class StyleChip(QFrame):
    removed = Signal(str)

    def __init__(self, style: Style, parent=None):
        super().__init__(parent)
        self.setObjectName("Chip")
        self.setFixedHeight(36)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 8, 0)
        lay.setSpacing(8)
        lay.addWidget(IconTile(style.icon, style.tone, 24))
        lb = QLabel(style.name)
        lb.setStyleSheet("font-size:12px;font-weight:500;")
        lay.addWidget(lb)
        x = icon_button("x", t("style_remove_chip"), 20, 12)
        x.clicked.connect(lambda: self.removed.emit(style.id))
        lay.addWidget(x)
        self.setToolTip(style.description)


class SelectedStyles(Section):
    toggled = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(t("selected_styles"), "sparkles", False, True, "", parent)
        self.manage = Button(t("style_manage"), "chevron-down", "secondary", "sm")
        self.manage.setLayoutDirection(Qt.RightToLeft)
        self.actions.addWidget(self.manage)
        self.host = QWidget()
        self.flow = FlowLayout(self.host, 8)
        self.body_lay.addWidget(self.host)
        self.empty = label(t("styles_none_selected"), "Muted", wrap=True)
        self.body_lay.addWidget(self.empty)
        self._styles: list[Style] = []

    def set_styles(self, all_styles: list[Style]) -> None:
        self._styles = all_styles
        enabled = [s for s in all_styles if s.enabled]
        self.set_title(f"{t('selected_styles')} ({len(enabled)})")
        clear_layout(self.flow)
        for s in sorted(enabled, key=lambda x: -x.priority):
            chip = StyleChip(s)
            chip.removed.connect(lambda sid: self.toggled.emit(sid, False))
            self.flow.addWidget(chip)
        self.empty.setVisible(not enabled)
        self.host.setVisible(bool(enabled))
        menu = QMenu(self.manage)
        for s in all_styles:
            act = QAction(s.name, menu)
            act.setCheckable(True)
            act.setChecked(s.enabled)
            act.setIcon(icons.icon(s.icon, "#c9d4ff", 16))
            act.toggled.connect(lambda on, sid=s.id: self.toggled.emit(sid, on))
            menu.addAction(act)
        if not all_styles:
            menu.addAction(t("style_none_found")).setEnabled(False)
        self.manage.setMenu(menu)


class RequestSection(Section):
    generate = Signal(str)   # режим: normal | no_refs | reset
    stop = Signal()

    def __init__(self, parent=None):
        # без собственной Card — контейнер RequestDock в главном окне
        super().__init__(t("request_title"), "sparkles", False, False, "", parent)
        self.counter = label(counter_text(0, REQUEST_LIMIT), "Faint")
        self.actions.addWidget(self.counter)
        self.edit = CountedEdit(t("request_ph"))
        self.edit.setMinimumHeight(112)
        self.edit.lengthChanged.connect(self._count)
        self.edit.submitted.connect(lambda: self.generate.emit("normal"))
        self.body_lay.addWidget(self.edit)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.examples_btn = Button(t("examples"), "bulb", "secondary", "sm")
        self.examples_menu = QMenu(self.examples_btn)
        for i in range(1, 6):
            text = t(f"example_{i}")
            self.examples_menu.addAction(text if len(text) < 70 else text[:68] + "…", lambda tx=text: self.edit.setPlainText(tx))
        self.examples_btn.setMenu(self.examples_menu)
        self.clear_btn = Button("", "trash", "secondary", "sm")
        self.clear_btn.setToolTip(t("clear"))
        self.clear_btn.setAccessibleName(t("clear"))
        self.clear_btn.setFixedWidth(36)
        self.clear_btn.clicked.connect(lambda: (self.edit.clear(), self.edit.setFocus()))
        row.addWidget(self.examples_btn)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        menu = QMenu(self)   # родитель обязателен: иначе меню удаляется GC и стрелка не работает
        menu.addAction(icons.icon("sparkles", T["text_2"], 16), t("create_normal"), lambda: self.generate.emit("normal"))
        menu.addAction(icons.icon("image", T["text_2"], 16), t("create_no_refs"), lambda: self.generate.emit("no_refs"))
        menu.addSeparator()
        menu.addAction(icons.icon("refresh", T["text_2"], 16), t("create_reset"), lambda: self.generate.emit("reset"))
        self.split = SplitButton(t("create_prompt"), "sparkles", menu)
        self.split.setMinimumWidth(200)
        self.split.arrow.setToolTip(t("create_modes_tip"))
        self.split.arrow.setAccessibleName(t("create_modes_tip"))
        self.split.clicked.connect(self._clicked)
        row.addWidget(self.split)
        self.body_lay.addLayout(row)
        self._busy = False

    def _count(self, n: int) -> None:
        self.counter.setText(counter_text(n, REQUEST_LIMIT))
        self.counter.setStyleSheet(f"color:{'#ff6b6e' if n > REQUEST_LIMIT else T['text_3']};font-size:11px;")

    def _clicked(self) -> None:
        if self._busy:
            self.stop.emit()
        else:
            self.generate.emit("normal")

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.split.set_state(t("stop_generation"), "stop", "danger")
            self.split.main.set_pulse(True)
        else:
            self.split.set_state(t("create_prompt"), "sparkles", "primary")
            self.split.main.set_pulse(False)
        self.split.arrow.setEnabled(not busy)


class ParamsSection(Section):
    def __init__(self, parent=None):
        super().__init__(t("params_title"), "sliders", True, True, "", parent)
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(12)
        # колонка 1: температура + max tokens
        self.temp = QSlider(Qt.Horizontal)
        self.temp.setRange(0, 200)
        self.temp.setValue(100)
        self.temp_val = label("1.00", "Muted")
        self.temp_val.setMinimumWidth(34)
        self.temp_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.temp.valueChanged.connect(lambda v: self.temp_val.setText(f"{v / 100:.2f}".rstrip("0").rstrip(".") if v % 5 else f"{v / 100:.2f}"))
        self.tokens = QSpinBox()
        self.tokens.setRange(64, 262144)
        self.tokens.setSingleStep(256)
        self.tokens.setValue(32000)
        self.tokens.setMinimumWidth(84)
        self.tokens.setToolTip(t("param_max_tokens"))
        # уровень рассуждений — живёт в компактной панели под запросом (CompactBar)
        self.think_level = QComboBox()
        for k in THINKING_LEVELS:
            self.think_level.addItem(t(f"think_{k}"), k)
        self.think_level.setCurrentIndex(3)
        self.think_level.setToolTip(t("think_level_tip"))
        c1 = QVBoxLayout()
        c1.setSpacing(6)
        r = QHBoxLayout()
        r.addWidget(label(t("param_temperature"), "Muted"))
        r.addStretch(1)
        r.addWidget(self.temp_val)
        c1.addLayout(r)
        c1.addWidget(self.temp)
        c1.addStretch(1)
        # колонка 2: переключатели
        self.streaming = Toggle(True)
        self.history = Toggle(True)
        self.show_reasoning = Toggle(False)
        self.think_level.currentIndexChanged.connect(lambda _: self.show_reasoning.setEnabled(self.level() != "off"))
        c2 = QVBoxLayout()
        c2.setSpacing(12)
        for lbl, tg, tip in ((t("param_streaming"), self.streaming, ""), (t("param_history"), self.history, ""),
                             (t("param_show_reasoning"), self.show_reasoning, t("param_show_reasoning_tip"))):
            h = QHBoxLayout()
            h.setSpacing(8)
            lb = ElidedLabel(lbl)
            h.addWidget(lb, 1)
            h.addWidget(tg)
            if tip:
                lb.setToolTip(tip)
                tg.setToolTip(tip)
            c2.addLayout(h)
        c2.addStretch(1)
        # колонка 3: соотношение сторон, креативность
        self.aspect = QComboBox()
        for a in ASPECTS:
            self.aspect.addItem(a or t("aspect_auto"), a)
        self.aspect.setCurrentIndex(1)
        self.aspect.setMinimumContentsLength(6)
        self.aspect.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.creativity = QComboBox()
        for k in CREATIVITY:
            self.creativity.addItem(t(f"creativity_{k}"), k)
        self.creativity.setCurrentIndex(1)
        self.creativity.setMinimumContentsLength(6)
        self.creativity.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        c3 = QVBoxLayout()
        c3.setSpacing(6)
        # соотношение сторон вынесено в закреплённую панель под запросом (CompactBar) — быстрый доступ
        c3.addWidget(label(t("param_creativity"), "Muted"))
        c3.addWidget(self.creativity)
        g.addLayout(c1, 0, 0)
        g.addWidget(self._vsep(), 0, 1)
        g.addLayout(c2, 0, 2)
        g.addWidget(self._vsep(), 0, 3)
        g.addLayout(c3, 0, 4)
        g.setColumnStretch(0, 3)
        g.setColumnStretch(2, 3)
        g.setColumnStretch(4, 3)
        self.body_lay.addLayout(g)
        self.set_open(True)    # во вкладке «Параметры» — сразу развёрнуты

    @staticmethod
    def _vsep() -> QFrame:
        f = QFrame()
        f.setObjectName("Sep")
        return f

    def set_model_defaults(self, m: Model) -> None:
        self.temp.setValue(int(round(m.temperature * 100)))
        self.tokens.setValue(m.max_tokens)

    def level(self) -> str:
        return self.think_level.currentData() or "high"

    def set_level(self, level: str) -> None:
        i = self.think_level.findData(level if level in THINKING_LEVELS else "high")
        self.think_level.setCurrentIndex(max(i, 0))

    def values(self) -> dict:
        return {
            "temperature": self.temp.value() / 100, "max_tokens": self.tokens.value(),
            "streaming": self.streaming.isChecked(), "include_history": self.history.isChecked(),
            "enable_thinking": self.level() != "off", "thinking_level": self.level(),
            "show_reasoning": self.show_reasoning.isChecked() and self.level() != "off",
            "aspect_ratio": self.aspect.currentData() or "", "creativity": self.creativity.currentData(),
        }


class H3LengthBox(QWidget):
    """Калькулятор длины MiniMax H3: секунды -> ближайшие допустимые length (17·k+5 @ 24 fps)."""
    changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(label(t("h3_length"), "Muted"))
        self.sec = QDoubleSpinBox()
        self.sec.setRange(1.0, 28.0)
        self.sec.setSingleStep(0.5)
        self.sec.setDecimals(2)
        self.sec.setSuffix(" " + t("sec"))
        self.sec.setValue(12.0)
        self.sec.setToolTip(t("h3_tip"))
        self.sec.setMinimumWidth(78)
        self.pick = QComboBox()
        self.pick.setToolTip(t("h3_tip"))
        self.pick.setMinimumContentsLength(16)
        self.pick.setMinimumWidth(150)
        lay.addWidget(self.sec)
        lay.addWidget(self.pick)
        self.sec.valueChanged.connect(self._refill)
        self.pick.currentIndexChanged.connect(lambda _: self.changed.emit(self.frames()))
        self._refill()

    def _refill(self, *_):
        prev = self.frames()
        lo, hi = h3.nearest(self.sec.value())
        self.pick.blockSignals(True)
        self.pick.clear()
        self.pick.addItem(t("h3_none"), 0)
        for n in dict.fromkeys((lo, hi)):
            self.pick.addItem(t("h3_frames", n=n, s=f"{h3.seconds(n):.2f}"), n)
        # по умолчанию — ближайший к цели вариант, не короче (12 с -> 294)
        self.pick.setCurrentIndex(self.pick.findData(prev if prev in (lo, hi) else hi))
        self.pick.blockSignals(False)
        self.changed.emit(self.frames())

    def frames(self) -> int:
        return int(self.pick.currentData() or 0) if hasattr(self, "pick") and self.pick.count() else 0

    def set_frames(self, n: int) -> None:
        if n and h3.is_valid(n):
            self.sec.setValue(h3.seconds(n))
            i = self.pick.findData(n)
            if i >= 0:
                self.pick.setCurrentIndex(i)
        elif hasattr(self, "pick"):
            self.pick.setCurrentIndex(0)


class CompactBar(QFrame):
    """Закреплённая строка над кнопкой «Создать»: мышление, токены, длина H3, живой счётчик."""

    def __init__(self, params: ParamsSection, parent=None):
        super().__init__(parent)
        self.setObjectName("CompactBar")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 2)
        outer.setSpacing(6)
        # строка 1: мышление · токены · кадр (всегда видна)
        lay = QHBoxLayout()
        lay.setSpacing(8)
        lay.addWidget(label(t("think_level"), "Muted"))
        lay.addWidget(params.think_level)
        lay.addSpacing(6)
        lay.addWidget(label(t("param_tokens_short"), "Muted"))
        lay.addWidget(params.tokens)
        lay.addSpacing(6)
        lay.addWidget(label(t("aspect_short"), "Muted"))
        params.aspect.setToolTip(t("param_aspect"))
        params.aspect.setMinimumWidth(90)
        lay.addWidget(params.aspect)
        lay.addStretch(1)
        outer.addLayout(lay)
        # строка 2: длина H3 (только для H3) · живой счётчик
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.h3 = H3LengthBox()
        row2.addWidget(self.h3)
        row2.addStretch(1)
        self.live = label("", "Faint")
        row2.addWidget(self.live)
        outer.addLayout(row2)
        self.h3.hide()
        self._params = params

    def set_live(self, phase: str, secs: float, n: int) -> None:
        if not phase:
            self.live.setText("")
            return
        key = {"think": "live_thinking", "answer": "live_answer"}.get(phase, "live_wait")
        text = t(key, s=f"{secs:.0f}", n=n)
        budget = THINKING_BUDGET.get(self._params.level(), 0)
        over = phase == "think" and budget and n > budget
        if over:
            text += " · " + t("live_budget_over", b=budget)
        self.live.setText(text)
        self.live.setStyleSheet(f"color:{'#ff6b6e' if over else T['text_3']};font-size:11px;")
