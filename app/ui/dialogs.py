"""Диалоги: подтверждение, редактор стиля, новая модель, настройки, сравнение версий, история."""
from __future__ import annotations

import difflib
import html
import re
import shlex
import threading
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QSpinBox, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from app.core.history_manager import HistoryManager, Version
from app.core.style_manager import ICON_CHOICES, TONE_CHOICES, Style
from app.strata.strata_api import StrataAPI
from app.ui import anim
from app.ui.anim import FadeStack
from app.ui import icons
from app.ui.system_prompt_box import SystemPromptBox
from app.ui import theme as theme_mod
from app.ui.theme import T, tone
from app.ui.widgets import Button, Toggle, icon_button, label
from app.utils import paths
from app.utils.config import Config
from app.utils.formatting import fmt_date, snippet
from app.utils.i18n import t


class BaseDialog(QDialog):
    def __init__(self, parent, title: str, width: int = 560):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumWidth(width)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        self.frame = QFrame()
        self.frame.setObjectName("Panel")
        outer.addWidget(self.frame)
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(14)
        head = QHBoxLayout()
        self.title_label = label(title, "H2")
        head.addWidget(self.title_label)
        head.addStretch(1)
        close = icon_button("x", t("close"), 28, 16)
        close.clicked.connect(self.reject)
        head.addWidget(close)
        lay.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        lay.addLayout(self.body)
        self.footer = QHBoxLayout()
        self.footer.setSpacing(10)
        lay.addLayout(self.footer)
        self._drag = False

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        anim.fade_window(self, 170)

    def add_buttons(self, ok_text: str | None = None, cancel_text: str | None = None, ok_variant: str = "primary") -> Button | None:
        self.footer.addStretch(1)
        if cancel_text is not None:
            c = Button(cancel_text, None, "secondary")
            c.clicked.connect(self.reject)
            self.footer.addWidget(c)
        ok = None
        if ok_text is not None:
            ok = Button(ok_text, None, ok_variant)
            ok.setDefault(True)
            ok.clicked.connect(self.accept)
            self.footer.addWidget(ok)
        return ok

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 64 and self.windowHandle():
            self.windowHandle().startSystemMove()
        super().mousePressEvent(e)


def confirm(parent, title: str, text: str, ok_text: str | None = None, danger: bool = False) -> bool:
    d = BaseDialog(parent, title, 420)
    lb = label(text, "Muted", wrap=True)
    d.body.addWidget(lb)
    d.add_buttons(ok_text or t("ok"), t("cancel"), "danger" if danger else "primary")
    return d.exec() == QDialog.Accepted


def info(parent, title: str, text: str) -> None:
    d = BaseDialog(parent, title, 420)
    d.body.addWidget(label(text, "Muted", wrap=True))
    d.add_buttons(t("ok"), None)
    d.exec()


def _row(lbl: str, widget: QWidget, hint: str = "") -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(5)
    v.addWidget(label(lbl, "Muted"))
    v.addWidget(widget)
    if hint:
        v.addWidget(label(hint, "Faint", wrap=True))
    return w


def _toggle_row(lbl: str, tg: Toggle, hint: str = "") -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(3)
    h = QHBoxLayout()
    h.addWidget(label(lbl))
    h.addStretch(1)
    h.addWidget(tg)
    v.addLayout(h)
    if hint:
        v.addWidget(label(hint, "Faint", wrap=True))
    return w


# =========================================================================== стиль
class StyleEditorDialog(BaseDialog):
    def __init__(self, parent, style: Style | None, categories: list[str]):
        super().__init__(parent, t("style_edit") if style else t("style_new"), 600)
        s = style
        self.name = QLineEdit(s.name if s else "")
        self.name.setPlaceholderText(t("style_name_ph"))
        self.desc = QLineEdit(s.description if s else "")
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems(categories)
        self.category.setCurrentText(s.category if s else (categories[0] if categories else ""))
        self.icon_combo = QComboBox()
        for n in ICON_CHOICES:
            self.icon_combo.addItem(icons.icon(n, "#c9d4ff", 18), n, n)
        self.tone_combo = QComboBox()
        for n in TONE_CHOICES:
            pm = QPixmap(14, 14)
            pm.fill(QColor(tone(n)))
            from PySide6.QtGui import QIcon
            self.tone_combo.addItem(QIcon(pm), n, n)
        if s:
            self.icon_combo.setCurrentIndex(max(0, ICON_CHOICES.index(s.icon)) if s.icon in ICON_CHOICES else 0)
            self.tone_combo.setCurrentIndex(TONE_CHOICES.index(s.tone) if s.tone in TONE_CHOICES else 0)
        self.priority = QSpinBox()
        self.priority.setRange(0, 100)
        self.priority.setValue(s.priority if s else 50)
        self.instructions = QPlainTextEdit("\n".join(s.instructions) if s else "")
        self.instructions.setPlaceholderText(t("style_instr_ph"))
        self.instructions.setMinimumHeight(170)
        self.body.addWidget(_row(t("style_name"), self.name))
        self.body.addWidget(_row(t("style_desc"), self.desc))
        g = QHBoxLayout()
        g.setSpacing(12)
        g.addWidget(_row(t("style_category"), self.category), 2)
        g.addWidget(_row(t("style_icon"), self.icon_combo), 1)
        g.addWidget(_row(t("style_tone"), self.tone_combo), 1)
        g.addWidget(_row(t("style_priority"), self.priority), 1)
        self.body.addLayout(g)
        self.body.addWidget(_row(t("style_instructions"), self.instructions, t("style_instr_hint")))
        self.error = label("", "Warn")
        self.body.addWidget(self.error)
        ok = self.add_buttons(t("save"), t("cancel"))
        ok.clicked.disconnect()
        ok.clicked.connect(self._try_accept)

    def _try_accept(self) -> None:
        if not self.name.text().strip():
            self.error.setText(t("style_err_name"))
            return
        if not [ln for ln in self.instructions.toPlainText().splitlines() if ln.strip()]:
            self.error.setText(t("style_err_instr"))
            return
        self.accept()

    def data(self) -> dict:
        return {
            "name": self.name.text().strip(), "description": self.desc.text().strip(),
            "category": self.category.currentText().strip(), "icon": self.icon_combo.currentData(),
            "tone": self.tone_combo.currentData(), "priority": self.priority.value(),
            "instructions": [ln.strip() for ln in self.instructions.toPlainText().splitlines() if ln.strip()],
        }


# =========================================================================== модель
class ModelDialog(BaseDialog):
    def __init__(self, parent, defaults: dict):
        super().__init__(parent, t("model_new"), 620)
        self.name = QLineEdit()
        self.name.setPlaceholderText("LTX 2")
        self.api = QLineEdit(defaults.get("api_base", ""))
        self.model_id = QLineEdit()
        self.model_id.setPlaceholderText("model-id")
        self.temp = QDoubleSpinBox()
        self.temp.setRange(0, 2)
        self.temp.setSingleStep(0.05)
        self.temp.setValue(defaults.get("temperature", 1.0))
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(64, 262144)
        self.max_tokens.setSingleStep(256)
        self.max_tokens.setValue(defaults.get("max_tokens", 32000))
        self.vision = Toggle(False)
        self.system = SystemPromptBox(t("model_system_ph"), min_height=120, max_height=180, padded=False)
        self.body.addWidget(_row(t("model_name"), self.name))
        g = QHBoxLayout()
        g.setSpacing(12)
        g.addWidget(_row(t("model_api"), self.api), 2)
        g.addWidget(_row(t("model_id"), self.model_id), 1)
        self.body.addLayout(g)
        g2 = QHBoxLayout()
        g2.setSpacing(12)
        g2.addWidget(_row(t("param_temperature"), self.temp))
        g2.addWidget(_row(t("param_max_tokens"), self.max_tokens))
        g2.addWidget(_toggle_row(t("model_vision"), self.vision), 1)
        self.body.addLayout(g2)
        self.body.addWidget(_row(t("model_system"), self.system))
        self.error = label("", "Warn")
        self.body.addWidget(self.error)
        ok = self.add_buttons(t("add"), t("cancel"))
        ok.clicked.disconnect()
        ok.clicked.connect(self._try_accept)

    def _try_accept(self) -> None:
        if not self.name.text().strip():
            self.error.setText(t("model_err_name"))
            return
        self.accept()

    def data(self) -> dict:
        text, path = self.system.value()
        return {
            "name": self.name.text().strip(), "api_base": self.api.text().strip(),
            "model": self.model_id.text().strip() or self.name.text().strip().lower().replace(" ", "-"),
            "system_prompt": text, "system_prompt_file": path,
            "temperature": self.temp.value(),
            "max_tokens": self.max_tokens.value(), "supports_vision": self.vision.isChecked(), "enabled": True,
        }


# =========================================================================== настройки
class SettingsDialog(BaseDialog):
    _health = Signal(str)
    _comfy_health = Signal(str)

    def __init__(self, parent, config: Config):
        super().__init__(parent, t("settings"), 680)
        self.config = config
        self._health.connect(self._show_health)
        self._comfy_health.connect(self._show_comfy)

        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        self.group = QButtonGroup(self)
        self.stack = FadeStack()
        pages = [(t("set_general"), self._page_general()), (t("set_strata"), self._page_strata()),
                 (t("set_comfy"), self._page_comfy()), (t("set_generation"), self._page_generation()),
                 (t("set_storage"), self._page_storage())]
        for i, (name, page) in enumerate(pages):
            b = Button(name, None, "secondary", "sm")
            b.setProperty("tab", "pill")
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.group.addButton(b, i)
            tabs.addWidget(b)
            self.stack.addWidget(page)
        tabs.addStretch(1)
        self.group.idClicked.connect(self.stack.setCurrentIndex)
        self.body.addLayout(tabs)
        self.body.addWidget(self.stack)
        self.note = label(t("set_restart_note"), "Faint", wrap=True)
        self.body.addWidget(self.note)
        ok = self.add_buttons(t("save"), t("cancel"))
        ok.clicked.disconnect()
        ok.clicked.connect(self._save)
        self.setMinimumHeight(560)

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(12)
        return w, v

    def _page_general(self) -> QWidget:
        w, v = self._page()
        self.lang = QComboBox()
        self.lang.addItem("Русский", "ru")
        self.lang.addItem("English", "en")
        self.lang.setCurrentIndex(0 if self.config.get("ui.language", "ru") == "ru" else 1)
        self.theme = QComboBox()
        cur = self.config.get("ui.theme", "dark")
        for tid in theme_mod.theme_ids():
            self.theme.addItem(t(theme_mod.THEMES[tid]["name_key"]), tid)
        idx = self.theme.findData(cur)
        self.theme.setCurrentIndex(max(0, idx))
        self.auto_start = Toggle(self.config.get("strata.auto_start", True))
        self.native = Toggle(self.config.get("ui.native_titlebar", False))
        self.animations = Toggle(self.config.get("ui.animations", True))
        v.addWidget(_row(t("set_language"), self.lang))
        v.addWidget(_row(t("set_theme"), self.theme, t("set_theme_hint")))
        v.addWidget(_toggle_row(t("set_autostart"), self.auto_start, t("set_autostart_hint")))
        v.addWidget(_toggle_row(t("set_native_title"), self.native, t("set_native_title_hint")))
        v.addWidget(_toggle_row(t("set_animations"), self.animations, t("set_animations_hint")))
        v.addStretch(1)
        return w

    def _page_strata(self) -> QWidget:
        w, v = self._page()
        c = self.config
        self.exe = QLineEdit(c.get("strata.executable", ""))
        self.exe.setPlaceholderText(r"C:\STRATA_AI\Strata\run-iq2_xs.bat")
        browse = Button(t("browse"), "folder", "secondary")
        browse.clicked.connect(self._browse_exe)
        row = QHBoxLayout()
        row.addWidget(self.exe, 1)
        row.addWidget(browse)
        default_btn = Button(t("set_exe_default"), None, "secondary")
        default_btn.setToolTip(r"C:\STRATA_AI\Strata\run-iq2_xs.bat")
        default_btn.clicked.connect(lambda: self.exe.setText(r"C:\STRATA_AI\Strata\run-iq2_xs.bat"))
        row.addWidget(default_btn)
        exe_box = QWidget()
        exe_box.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.args = QLineEdit(" ".join(shlex.quote(a) if " " in a else a for a in c.get("strata.arguments", [])))
        self.api_url = QLineEdit(c.get("strata.api_base", ""))
        self.health_ep = QLineEdit(c.get("strata.health_endpoint", "/health"))
        self.unload_ep = QLineEdit(c.get("strata.unload_endpoint", ""))
        self.unload_ep.setPlaceholderText(t("set_unload_auto"))
        self.start_timeout = QSpinBox()
        self.start_timeout.setRange(10, 1800)
        self.start_timeout.setSuffix(" " + t("sec"))
        self.start_timeout.setValue(int(c.get("strata.start_timeout", 180)))
        self.auto_unload = Toggle(c.get("strata.auto_unload", False))
        self.unload_delay = QSpinBox()
        self.unload_delay.setRange(10, 86400)
        self.unload_delay.setSuffix(" " + t("sec"))
        self.unload_delay.setValue(int(c.get("strata.auto_unload_delay", 300)))
        self.auto_shutdown = Toggle(c.get("strata.auto_shutdown", False))
        v.addWidget(_row(t("set_exe"), exe_box))
        v.addWidget(_row(t("set_args"), self.args))
        g = QHBoxLayout()
        g.setSpacing(12)
        g.addWidget(_row(t("set_api_url"), self.api_url), 2)
        g.addWidget(_row(t("set_health"), self.health_ep), 1)
        v.addLayout(g)
        g2 = QHBoxLayout()
        g2.setSpacing(12)
        g2.addWidget(_row(t("set_unload_ep"), self.unload_ep), 2)
        g2.addWidget(_row(t("set_start_timeout"), self.start_timeout), 1)
        v.addLayout(g2)
        v.addWidget(_toggle_row(t("set_auto_unload"), self.auto_unload, t("set_auto_unload_hint")))
        v.addWidget(_row(t("set_auto_unload_delay"), self.unload_delay))
        v.addWidget(_toggle_row(t("set_auto_shutdown"), self.auto_shutdown, t("set_auto_shutdown_hint")))
        test = Button(t("set_test"), "refresh", "secondary", "sm")
        test.clicked.connect(self._test)
        self.test_result = label("", "Muted")
        h = QHBoxLayout()
        h.addWidget(test)
        h.addWidget(self.test_result, 1)
        v.addLayout(h)
        v.addStretch(1)
        return w

    def _page_comfy(self) -> QWidget:
        w, v = self._page()
        c = self.config
        self.comfy_on = Toggle(c.get("comfy.enabled", True))
        self.comfy_url = QLineEdit(c.get("comfy.url", "http://127.0.0.1:8188"))
        self.comfy_free = Toggle(c.get("comfy.free_before_generate", True))
        self.comfy_unload = Toggle(c.get("comfy.unload_strata_on_run", True))
        self.comfy_poll = QSpinBox()
        self.comfy_poll.setRange(1, 30)
        self.comfy_poll.setSuffix(" " + t("sec"))
        self.comfy_poll.setValue(int(c.get("comfy.poll_interval", 2)))
        self.comfy_wait = QSpinBox()
        self.comfy_wait.setRange(10, 7200)
        self.comfy_wait.setSuffix(" " + t("sec"))
        self.comfy_wait.setValue(int(c.get("comfy.wait_busy_timeout", 600)))
        v.addWidget(_toggle_row(t("set_comfy_enabled"), self.comfy_on, t("set_comfy_enabled_hint")))
        v.addWidget(_row(t("set_comfy_url"), self.comfy_url))
        v.addWidget(_toggle_row(t("set_comfy_free"), self.comfy_free, t("set_comfy_free_hint")))
        v.addWidget(_toggle_row(t("set_comfy_unload"), self.comfy_unload, t("set_comfy_unload_hint")))
        g = QHBoxLayout()
        g.setSpacing(12)
        g.addWidget(_row(t("set_comfy_poll"), self.comfy_poll))
        g.addWidget(_row(t("set_comfy_wait"), self.comfy_wait))
        v.addLayout(g)
        test = Button(t("set_test"), "refresh", "secondary", "sm")
        test.clicked.connect(self._test_comfy)
        self.comfy_result = label("", "Muted")
        h = QHBoxLayout()
        h.addWidget(test)
        h.addWidget(self.comfy_result, 1)
        v.addLayout(h)
        v.addStretch(1)
        return w

    def _test_comfy(self) -> None:
        self.comfy_result.setText(t("set_testing"))
        url = self.comfy_url.text().strip().rstrip("/")
        import requests

        def work():
            try:
                r = requests.get(url + "/queue", timeout=2.5)
                q = r.json()
                self._comfy_health.emit("busy" if (q.get("queue_running") or q.get("queue_pending")) else "idle")
            except Exception:
                self._comfy_health.emit("offline")

        threading.Thread(target=work, daemon=True).start()

    def _show_comfy(self, res: str) -> None:
        self.comfy_result.setText({"idle": t("comfy_ok_idle"), "busy": t("comfy_ok_busy")}.get(res, t("comfy_offline")))

    def _page_generation(self) -> QWidget:
        w, v = self._page()
        c = self.config
        self.temp = QDoubleSpinBox()
        self.temp.setRange(0, 2)
        self.temp.setSingleStep(0.05)
        self.temp.setValue(float(c.get("generation.temperature", 1.0)))
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(64, 262144)
        self.max_tokens.setSingleStep(256)
        self.max_tokens.setValue(int(c.get("generation.max_tokens", 32000)))
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 3600)
        self.timeout.setSuffix(" " + t("sec"))
        self.timeout.setValue(int(c.get("generation.timeout", 120)))
        self.streaming = Toggle(c.get("generation.streaming", True))
        self.use_refs = Toggle(c.get("options.use_references", True))
        g = QHBoxLayout()
        g.setSpacing(12)
        g.addWidget(_row(t("param_temperature"), self.temp))
        g.addWidget(_row(t("param_max_tokens"), self.max_tokens))
        g.addWidget(_row(t("set_timeout"), self.timeout))
        v.addLayout(g)
        v.addWidget(_label_hint(t("set_gen_hint")))
        v.addWidget(_toggle_row(t("param_streaming"), self.streaming))
        v.addWidget(_toggle_row(t("set_use_refs"), self.use_refs, t("set_use_refs_hint")))
        v.addStretch(1)
        return w

    def _page_storage(self) -> QWidget:
        w, v = self._page()
        for lbl, p in ((t("set_data_dir"), paths.DATA_DIR), (t("set_history_dir"), paths.HISTORY_DIR),
                       (t("set_refs_dir"), paths.REFERENCES_DIR), (t("set_logs_dir"), paths.LOGS_DIR)):
            e = QLineEdit(str(p))
            e.setReadOnly(True)
            b = Button(t("open_folder"), "folder", "secondary")
            b.clicked.connect(lambda _=False, pp=p: _open_path(pp))
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(e, 1)
            row.addWidget(b)
            box = QWidget()
            box.setLayout(row)
            v.addWidget(_row(lbl, box))
        v.addWidget(_label_hint(t("set_storage_hint")))
        v.addStretch(1)
        return w

    def _browse_exe(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, t("set_exe"), self.exe.text() or str(Path.home()),
                                           "Strata launcher (*.bat *.cmd *.exe);;All (*)")
        if f:
            self.exe.setText(f)

    def _test(self) -> None:
        self.test_result.setText(t("set_testing"))
        api = StrataAPI(self.api_url.text(), self.health_ep.text())
        threading.Thread(target=lambda: self._health.emit(api.health(2.5)), daemon=True).start()

    def _show_health(self, res: str) -> None:
        msg = {"ok": t("set_test_ok"), "loading": t("set_test_loading")}.get(res, t("set_test_fail"))
        self.test_result.setText(msg)

    def _save(self) -> None:
        c = self.config
        try:
            args = shlex.split(self.args.text(), posix=False)
        except ValueError:
            args = self.args.text().split()
        c.set("ui.language", self.lang.currentData(), False)
        c.set("ui.theme", self.theme.currentData() or "dark", False)
        c.set("ui.native_titlebar", self.native.isChecked(), False)
        c.set("ui.animations", self.animations.isChecked(), False)
        anim.set_enabled(self.animations.isChecked())
        c.set("strata.auto_start", self.auto_start.isChecked(), False)
        c.set("strata.executable", self.exe.text().strip(), False)
        c.set("strata.arguments", [a.strip('"') for a in args], False)
        c.set("strata.api_base", self.api_url.text().strip(), False)
        c.set("strata.health_endpoint", self.health_ep.text().strip() or "/health", False)
        c.set("strata.unload_endpoint", self.unload_ep.text().strip(), False)
        c.set("strata.start_timeout", self.start_timeout.value(), False)
        c.set("strata.auto_unload", self.auto_unload.isChecked(), False)
        c.set("strata.auto_unload_delay", self.unload_delay.value(), False)
        c.set("strata.auto_shutdown", self.auto_shutdown.isChecked(), False)
        c.set("comfy.enabled", self.comfy_on.isChecked(), False)
        c.set("comfy.url", self.comfy_url.text().strip() or "http://127.0.0.1:8188", False)
        c.set("comfy.free_before_generate", self.comfy_free.isChecked(), False)
        c.set("comfy.unload_strata_on_run", self.comfy_unload.isChecked(), False)
        c.set("comfy.poll_interval", self.comfy_poll.value(), False)
        c.set("comfy.wait_busy_timeout", self.comfy_wait.value(), False)
        c.set("generation.temperature", self.temp.value(), False)
        c.set("generation.max_tokens", self.max_tokens.value(), False)
        c.set("generation.timeout", self.timeout.value(), False)
        c.set("generation.streaming", self.streaming.isChecked(), False)
        c.set("options.use_references", self.use_refs.isChecked(), False)
        c.save()
        self.accept()


def _label_hint(text: str) -> QLabel:
    return label(text, "Faint", wrap=True)


def _open_path(p: Path) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    Path(p).mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


# =========================================================================== сравнение версий
class CompareDialog(BaseDialog):
    def __init__(self, parent, versions: list[Version], current_index: int = 0):
        super().__init__(parent, t("compare_title"), 820)
        self.versions = versions
        self.a, self.b = QComboBox(), QComboBox()
        for v in versions:
            lab = f"v{v.version} · {fmt_date(v.date)}"
            self.a.addItem(lab, v.version)
            self.b.addItem(lab, v.version)
        newest = len(versions) - 1
        self.a.setCurrentIndex(max(0, newest - 1))
        self.b.setCurrentIndex(newest)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(_row(t("compare_from"), self.a))
        row.addWidget(_row(t("compare_to"), self.b))
        self.body.addLayout(row)
        self.view = QTextBrowser()
        self.view.setMinimumHeight(360)
        self.body.addWidget(self.view)
        legend = label(t("compare_legend"), "Faint")
        self.body.addWidget(legend)
        self.a.currentIndexChanged.connect(self._render)
        self.b.currentIndexChanged.connect(self._render)
        self.add_buttons(t("close"), None, "secondary")
        self.resize(860, 640)
        self._render()

    def _render(self) -> None:
        a, b = self.versions[self.a.currentIndex()], self.versions[self.b.currentIndex()]
        self.view.setHtml(diff_html(a.prompt, b.prompt))


def diff_html(old: str, new: str) -> str:
    tok = lambda s: re.findall(r"\s+|[^\s]+", s)
    ta, tb = tok(old), tok(new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape("".join(ta[i1:i2])))
        if op in ("delete", "replace"):
            out.append('<span style="background-color:rgba(229,56,59,0.30);color:#ffb3b5;text-decoration:line-through;">'
                       + html.escape("".join(ta[i1:i2])) + "</span>")
        if op in ("insert", "replace"):
            out.append('<span style="background-color:rgba(34,197,94,0.28);color:#b6f5cd;">'
                       + html.escape("".join(tb[j1:j2])) + "</span>")
    body = "".join(out).replace("\n", "<br>")
    return f'<div style="white-space:pre-wrap;line-height:150%;color:#e8edff;">{body}</div>'


# =========================================================================== история генераций
class GenerationsDialog(BaseDialog):
    def __init__(self, parent, history: HistoryManager):
        super().__init__(parent, t("gens_title"), 680)
        self.history = history
        self.selected_id: int | None = None
        self.list = QListWidget()
        self.list.setMinimumHeight(340)
        self.list.itemDoubleClicked.connect(lambda _: self._open())
        self.body.addWidget(self.list)
        self.empty = label(t("gens_empty"), "Muted")
        self.body.addWidget(self.empty)
        self._fill()
        self.footer.addStretch(1)
        d = Button(t("delete"), "trash", "secondary")
        d.clicked.connect(self._delete)
        c = Button(t("close"), None, "secondary")
        c.clicked.connect(self.reject)
        o = Button(t("open"), None, "primary")
        o.clicked.connect(self._open)
        for b in (d, c, o):
            self.footer.addWidget(b)

    def _fill(self) -> None:
        self.list.clear()
        gens = self.history.list_generations()
        for g in gens:
            it = QListWidgetItem(f"{fmt_date(g.date)}  ·  {g.model_name}\n{snippet(g.user_request, 110)}")
            it.setData(Qt.UserRole, g.id)
            self.list.addItem(it)
        self.empty.setVisible(not gens)
        self.list.setVisible(bool(gens))

    def _open(self) -> None:
        it = self.list.currentItem()
        if it:
            self.selected_id = it.data(Qt.UserRole)
            self.accept()

    def _delete(self) -> None:
        it = self.list.currentItem()
        if it and confirm(self, t("delete"), t("gens_delete_q"), t("delete"), True):
            self.history.delete_generation(it.data(Qt.UserRole))
            self._fill()
