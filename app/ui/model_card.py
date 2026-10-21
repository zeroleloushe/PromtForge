"""Карточка модели: компактная шапка + вкладки «Настройки» / «Системный промт»."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QButtonGroup, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QMenu,
                               QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from app.core.model_manager import Model
from app.ui.anim import FadeStack
from app.ui import icons
from app.ui.system_prompt_box import SystemPromptBox
from app.ui.theme import T
from app.ui.widgets import Button, ModelLogo, Toggle, icon_button, label, repolish
from app.utils.i18n import t


class ModelCard(QFrame):
    save_requested = Signal(dict)
    delete_requested = Signal()
    export_requested = Signal()
    import_requested = Signal()
    open_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._model: Model | None = None
        self._open = True
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 10)
        root.setSpacing(4)

        # --- одна строка шапки -------------------------------------------------
        top = QHBoxLayout()
        top.setSpacing(6)

        self.model_btn = Button("", None, "secondary", "sm")
        self.model_btn.setMinimumWidth(120)
        self.model_btn.setMaximumWidth(260)
        self.model_btn.setIconSize(QSize(18, 18))
        self.model_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.model_btn.setStyleSheet(
            "QPushButton { text-align: left; padding: 0 10px; font-weight: 600; font-size: 12px; min-height: 30px; }")
        menu = QMenu(self.model_btn)
        menu.addAction(icons.icon("upload", T["text_2"], 16), t("model_export"), self.export_requested.emit)
        menu.addAction(icons.icon("download", T["text_2"], 16), t("model_import"), self.import_requested.emit)
        menu.addSeparator()
        menu.addAction(icons.icon("trash", "#ff8a8d", 16), t("model_delete"), self.delete_requested.emit)
        self.model_btn.setMenu(menu)
        top.addWidget(self.model_btn, 0)

        self.group = QButtonGroup(self)
        self.tab_settings = Button(t("model_tab_settings"), "sliders", "secondary", "sm")
        self.tab_system = Button(t("model_tab_system"), "file-text", "secondary", "sm")
        for i, b in enumerate((self.tab_settings, self.tab_system)):
            b.setProperty("tab", "pill")
            b.setCheckable(True)
            b.setFixedHeight(30)
            b.setMinimumWidth(0)
            self.group.addButton(b, i)
            top.addWidget(b, 0)
        # настройки модели (имя, ID, URL, температура…) убраны из карточки — хватает общих настроек;
        # поля остаются скрытыми, чтобы «Сохранить» не терял значения
        self.tab_system.setChecked(True)
        self.tab_settings.hide()
        self.tab_system.hide()

        top.addStretch(1)

        self.save_btn = Button(t("save"), "check", "primary", "sm")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setMinimumWidth(120)
        self.save_btn.clicked.connect(self._emit_save)

        self.chev = icon_button("chevron-up", t("model_card_collapse"), 28, 14)
        self.chev.clicked.connect(lambda: self.set_open(not self._open, notify=True))
        self.chev.hide()      # карточка всегда открыта во вкладке «Системный промт»
        root.addLayout(top)

        self.stack = FadeStack()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._hidden_settings = self._settings_page()   # виджеты нужны для collect(), на экран не выводятся
        self._hidden_settings.setParent(self)
        self._hidden_settings.hide()
        self.stack.addWidget(self._system_page())
        self.stack.setCurrentIndex(0)
        root.addWidget(self.stack, 1)

    # --- сворачивание ------------------------------------------------------------
    def is_open(self) -> bool:
        return self._open

    def set_open(self, open_: bool, notify: bool = False) -> None:
        self._open = open_
        for w in (self.stack, self.save_btn):
            w.setVisible(open_)
        self.chev.set_icon("chevron-up" if open_ else "chevron-down")
        tip = t("model_card_collapse") if open_ else t("model_card_expand")
        self.chev.setToolTip(tip)
        self.chev.setAccessibleName(tip)
        self.updateGeometry()
        if notify:
            self.open_changed.emit(open_)

    # --- страницы --------------------------------------------------------------
    def _settings_page(self) -> QWidget:
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(2)   # плотно между строками — без «воздуха»

        self.f_name = QLineEdit()
        self.f_id = QLineEdit()
        self.f_api = QLineEdit()
        self.f_temp = QDoubleSpinBox()
        self.f_temp.setRange(0, 2)
        self.f_temp.setSingleStep(0.05)
        self.f_temp.setDecimals(2)
        self.f_tokens = QSpinBox()
        self.f_tokens.setRange(64, 262144)
        self.f_tokens.setSingleStep(256)
        self.f_vision = Toggle()
        self.f_enabled = Toggle()
        for edit in (self.f_name, self.f_id, self.f_api, self.f_temp, self.f_tokens):
            edit.setObjectName("CompactInput")

        g.addWidget(self._field(t("model_name"), self.f_name), 0, 0)
        g.addWidget(self._field(t("model_id"), self.f_id), 0, 1)
        g.addWidget(self._field(t("model_api"), self.f_api), 1, 0, 1, 2)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._field(t("model_temp_short"), self.f_temp), 1)
        row.addWidget(self._field(t("model_tokens_short"), self.f_tokens), 1)
        row.addWidget(self._toggle(t("model_vision_short"), self.f_vision), 0)
        row.addWidget(self._toggle(t("model_enabled_short"), self.f_enabled), 0)
        wrap = QWidget()
        wrap.setLayout(row)
        g.addWidget(wrap, 2, 0, 1, 2)

        self.f_temp.setToolTip(t("model_defaults_tip"))
        self.f_tokens.setToolTip(t("model_defaults_tip"))
        self.f_vision.setToolTip(t("model_vision"))
        self.f_enabled.setToolTip(t("model_enabled"))
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        return w

    def _system_page(self) -> QWidget:
        w = QFrame()
        w.setObjectName("Inset")
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.system = SystemPromptBox(
            t("model_system_ph"), flat=True, remind_save=True,
            min_height=220, max_height=16777215)
        self.system.fileChanged.connect(self._file_badge)
        v.addWidget(self.system, 1)
        # «Сохранить» — в одной строке с «Прикрепить / Заменить файл», справа, а не над редактором
        self.system.bar.addWidget(self.save_btn, 0)
        return w

    @staticmethod
    def _field(lbl: str, widget: QWidget) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        v.addWidget(label(lbl, "Faint"))
        v.addWidget(widget)
        return w

    @staticmethod
    def _toggle(lbl: str, tg: Toggle) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        v.addWidget(label(lbl, "Faint"))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(tg)
        row.addStretch(1)
        v.addLayout(row)
        return w

    def _file_badge(self, path: str) -> None:
        if path:
            self.tab_system.set_icon("paperclip")
            self.tab_system.setToolTip(t("sys_attached_tip", name=Path(path).name))
        else:
            self.tab_system.set_icon("file-text")
            self.tab_system.setToolTip("")

    # --- данные --------------------------------------------------------------------
    def set_model(self, m: Model | None) -> None:
        self._model = m
        if m is None:
            return
        self.model_btn.setText("  " + m.name)
        self.model_btn.setIcon(QIcon(ModelLogo(m.name, m.id, 18).grab()))
        self.model_btn.setToolTip(
            m.name if m.enabled else f"{m.name} — {t('model_disabled')}")
        # визуально приглушаем имя, если модель выключена (вместо чипа «Активна»)
        self.model_btn.setProperty("off", not m.enabled)
        repolish(self.model_btn)
        self.f_name.setText(m.name)
        self.f_temp.setValue(m.temperature)
        self.f_tokens.setValue(m.max_tokens)
        self.f_vision.setChecked(m.supports_vision)
        self.f_enabled.setChecked(m.enabled)
        self.system.set_prompt(m.system_prompt, m.system_prompt_file)

    def collect(self) -> dict:
        text, path = self.system.value()
        return {
            "name": self.f_name.text().strip() or (self._model.name if self._model else "Model"),
            "temperature": self.f_temp.value(), "max_tokens": self.f_tokens.value(),
            "supports_vision": self.f_vision.isChecked(), "enabled": self.f_enabled.isChecked(),
            "system_prompt": text, "system_prompt_file": path,
        }

    def _emit_save(self) -> None:
        self.save_requested.emit(self.collect())
