"""ChatPanel: режим диалога — полноценный чат-ассистент: инструменты (MCP), подтверждения, размышления, сохранённые чаты."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QPlainTextEdit, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from app.core.chat_store import ChatStore, SessionInfo, sanitize_history
from app.core.reference_manager import IMAGE_EXT
from app.ui import anim, icons
from app.ui.theme import T
from app.ui.widgets import (Button, ElidedLabel, FlowLayout, IconTile, Toggle, VScroll, clear_layout, hline, icon_button,
                            label)
from app.utils.i18n import t

TEXT_EXT = {".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".xml", ".yaml", ".yml", ".html", ".htm", ".css", ".js",
            ".ts", ".py", ".log", ".ini", ".toml", ".srt", ".vtt", ".sql", ".sh", ".bat", ".cfg", ".rst"}
MAX_FILES = 6
MAX_TEXT_CHARS = 30000
MAX_READ_BYTES = 600_000
MAX_DETAIL_CHARS = 6000
EXTRA_KEYS = ("reasoning", "stats", "truncated", "synthetic", "is_error", "duration", "denied", "title", "server",
              "risk", "args")      # служебные поля истории (в API не уходят)


def read_text_file(path: Path):
    """Текст файла (и признак «обрезан») или None, если файл не текстовый."""
    try:
        raw = path.read_bytes()[:MAX_READ_BYTES]
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    txt = None
    for enc in ("utf-8-sig", "cp1251"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if txt is None:
        return None
    sample = txt[:2000]
    if path.suffix.lower() not in TEXT_EXT and sum(ch.isprintable() or ch in "\n\r\t" for ch in sample) < 0.95 * max(1, len(sample)):
        return None
    return (txt[:MAX_TEXT_CHARS], len(txt) > MAX_TEXT_CHARS)


def _thumb(path: str, size: int):
    pm = QPixmap(path)
    if pm.isNull():
        return None
    return pm.scaled(size * 2, size * 2, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation).copy(0, 0, size * 2, size * 2)


def _clip(text: str, limit: int = MAX_DETAIL_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… [{len(text) - limit}]"


def _short_args(args: dict, limit: int = 110) -> str:
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        parts.append(f"{k}={s.replace(chr(10), ' ')}")
    s = ", ".join(parts)
    return s if len(s) <= limit else s[:limit - 1] + "…"


class _Bubble(QFrame):
    """Одно сообщение. Пользователь — справа (синий), модель — слева (карточка) с блоком размышлений и статистикой."""

    def __init__(self, role: str, text: str, on_regen=None, atts=None):
        super().__init__()
        self.role = role
        self.text = text
        self._on_regen = on_regen
        self._reasoning = ""
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.card = QFrame()
        self.card.setObjectName("ChatUser" if role == "user" else "ChatAI")
        if role == "user":
            self.card.setStyleSheet(
                "QFrame#ChatUser{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #3b78ff,stop:1 #2158ee);"
                "border:1px solid rgba(120,160,255,0.5);border-radius:14px;border-bottom-right-radius:4px;}")
        else:
            self.card.setStyleSheet(
                f"QFrame#ChatAI{{background:{T['bg_card']};border:1px solid {T['border']};"
                f"border-radius:14px;border-top-left-radius:4px;}}")
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(14, 10, 14, 10)
        cl.setSpacing(6)

        # блок размышлений (только у ответов модели)
        self.think_box = QWidget()
        tv = QVBoxLayout(self.think_box)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(4)
        self.think_btn = QPushButton()
        self.think_btn.setCursor(Qt.PointingHandCursor)
        self.think_btn.setCheckable(True)
        self.think_btn.setStyleSheet(
            f"QPushButton{{border:none;background:transparent;color:{T['text_3']};font-size:12px;text-align:left;padding:0;}}"
            f"QPushButton:hover{{color:{T['text_2']};}}")
        self.think_btn.toggled.connect(self._toggle_think)
        tv.addWidget(self.think_btn)
        self.think_body = QLabel()
        self.think_body.setWordWrap(True)
        self.think_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.think_body.setStyleSheet(f"color:{T['text_3']};font-size:12px;font-style:italic;background:transparent;"
                                      f"border-left:2px solid {T['border']};padding-left:8px;")
        self.think_body.hide()
        tv.addWidget(self.think_body)
        self.think_box.hide()
        cl.addWidget(self.think_box)

        self.dots = anim.TypingDots()
        self.dots.hide()
        cl.addWidget(self.dots)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.MarkdownText if role != "user" else Qt.PlainText)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.body.setOpenExternalLinks(True)
        self.body.setStyleSheet(f"color:{'#fff' if role == 'user' else T['text']};font-size:14px;background:transparent;")
        self.body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        cl.addWidget(self.body)
        if atts:
            row = QHBoxLayout()
            row.setSpacing(6)
            for a in atts:
                row.addWidget(self._att_widget(a), 0, Qt.AlignTop)
            row.addStretch(1)
            cl.addLayout(row)
        self.tools = QWidget()
        tl = QHBoxLayout(self.tools)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        self.copy_btn = icon_button("copy", t("copy"), 26, 14)
        self.copy_btn.clicked.connect(self._copy)
        tl.addWidget(self.copy_btn)
        self.regen_btn = icon_button("refresh", t("chat_regen"), 26, 14)
        self.regen_btn.clicked.connect(lambda: self._on_regen and self._on_regen())
        self.regen_btn.hide()
        tl.addWidget(self.regen_btn)
        self.stats_lb = QLabel()
        self.stats_lb.setStyleSheet(f"color:{T['text_3']};font-size:11px;background:transparent;")
        tl.addSpacing(6)
        tl.addWidget(self.stats_lb)
        tl.addStretch(1)
        cl.addWidget(self.tools)
        self.tools.setVisible(role != "user")
        if role == "user":
            outer.addStretch(1)
            outer.addWidget(self.card)
        else:
            outer.addWidget(self.card)
            outer.addStretch(1)
        self.set_text(text)

    @staticmethod
    def _att_widget(a: dict) -> QWidget:
        if a["kind"] == "image":
            pm = _thumb(a["path"], 64)
            if pm:
                lb = QLabel()
                lb.setPixmap(pm.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                lb.setToolTip(a["name"])
                lb.setStyleSheet("border:1px solid rgba(255,255,255,0.35);border-radius:8px;")
                return lb
        lb = ElidedLabel(a["name"])
        lb.setStyleSheet("background:rgba(255,255,255,0.16);border-radius:10px;padding:0 9px;color:#fff;font-size:12px;")
        lb.setFixedHeight(26)
        lb.setMinimumWidth(min(220, lb.fontMetrics().horizontalAdvance(a["name"]) + 30))
        lb.setMaximumWidth(220)
        lb.setToolTip(a["name"])
        return lb

    def set_text(self, text: str, typing: bool = False) -> None:
        self.text = text
        self.body.setVisible(bool(text))
        self.body.setText(text)
        self.dots.setVisible(typing and not text)

    def set_reasoning(self, text: str, live: bool = False) -> None:
        self._reasoning = text
        self.think_box.setVisible(bool(text.strip()))
        if not text.strip():
            return
        self.think_btn.setText(("▾ " if self.think_btn.isChecked() else "▸ ") + (t("chat_thinking") if live else t("chat_thought")))
        if self.think_btn.isChecked():
            self.think_body.setText(_clip(text.strip(), 12000))

    def _toggle_think(self, on: bool) -> None:
        self.think_body.setVisible(on)
        self.set_reasoning(self._reasoning)

    def set_stats(self, stats: dict, truncated: bool = False) -> None:
        bits = []
        if stats.get("tps"):
            bits.append(f"{stats['tps']} {t('chat_tps')}")
        if stats.get("completion"):
            bits.append(f"{stats['completion']} {t('chat_tokens')}")
        if stats.get("elapsed"):
            bits.append(f"{stats['elapsed']} {t('sec')}")
        if truncated:
            bits.append(t("chat_truncated"))
        self.stats_lb.setText(" · ".join(bits))

    def set_limit(self, width: int) -> None:
        self.card.setMaximumWidth(max(240, width))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text)
        self.copy_btn.set_icon("check")
        QTimer.singleShot(1300, lambda: self.copy_btn.set_icon("copy"))


class _ToolCard(QFrame):
    """Вызов инструмента: название, аргументы, статус, результат (раскрывается) и кнопки подтверждения."""
    decided = Signal(str, str)         # id вызова, решение: once | always | deny

    STATES = {"wait": ("clock", "text_3", "chat_tool_wait"), "approve": ("shield", "warning", "chat_tool_approve"),
              "run": ("refresh", "warning", "chat_tool_run"), "ok": ("check", "success", "chat_tool_ok"),
              "error": ("alert", "danger", "chat_tool_error"), "denied": ("x", "text_3", "chat_tool_denied")}

    def __init__(self, info: dict):
        super().__init__()
        self.role = "tool"
        self.call_id = info.get("id", "")
        self.setObjectName("ToolCard")
        self.setStyleSheet(f"QFrame#ToolCard{{background:{T['bg_input']};border:1px solid {T['border']};border-radius:12px;}}")
        self._args = info.get("args") or {}
        self._result = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 10, 8)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.state_icon = QLabel()
        self.state_icon.setStyleSheet("background:transparent;border:none;")
        head.addWidget(self.state_icon)
        self.spinner = anim.Spinner(T["warning"], 15)
        self.spinner.hide()
        head.addWidget(self.spinner)
        title = info.get("title") or info.get("name") or "tool"
        server = info.get("server") or ""
        self.title_lb = QLabel(f"<b>{self._esc(title)}</b>" + (f" <span style='color:{T['text_3']}'>· {self._esc(server)}</span>"
                                                                if server and server != "builtin" else ""))
        self.title_lb.setTextFormat(Qt.RichText)
        self.title_lb.setStyleSheet(f"color:{T['text']};font-size:13px;background:transparent;border:none;")
        head.addWidget(self.title_lb)
        self.args_lb = ElidedLabel(_short_args(self._args))
        self.args_lb.setStyleSheet(f"color:{T['text_3']};font-size:12px;background:transparent;border:none;")
        head.addWidget(self.args_lb, 1)
        self.status_lb = QLabel()
        self.status_lb.setStyleSheet("font-size:12px;background:transparent;border:none;")
        head.addWidget(self.status_lb)
        self.more = QPushButton()
        self.more.setIcon(icons.icon("chevron-down", T["text_2"], 14))
        self.more.setFixedSize(22, 22)
        self.more.setCheckable(True)
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.setToolTip(t("chat_tool_details"))
        self.more.setStyleSheet("QPushButton{border:none;background:transparent;padding:0;border-radius:11px;}"
                                f"QPushButton:hover{{background:{T['bg_hover']};}}")
        self.more.toggled.connect(self._toggle)
        head.addWidget(self.more)
        v.addLayout(head)

        self.approve_row = QWidget()
        ar = QHBoxLayout(self.approve_row)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.setSpacing(8)
        risk = info.get("risk", "write")
        ar.addWidget(label(t("chat_tool_risk_exec") if risk == "exec" else t("chat_tool_risk_write"), "Muted"), 1)
        for text, variant, decision in ((t("chat_allow_once"), "primary", "once"), (t("chat_allow_always"), "secondary", "always"),
                                        (t("chat_deny"), "danger", "deny")):
            b = Button(text, None, variant, "sm")
            b.clicked.connect(lambda _=False, d=decision: self._decide(d))
            ar.addWidget(b)
        self.approve_row.hide()
        v.addWidget(self.approve_row)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(240)
        self.detail.setStyleSheet(f"QPlainTextEdit{{background:{T['bg_app']};border:1px solid {T['border_subtle']};"
                                  f"border-radius:8px;color:{T['text_2']};font-family:Consolas,'Cascadia Mono',monospace;font-size:12px;}}")
        self.detail.hide()
        v.addWidget(self.detail)
        self.set_state("wait")

    @staticmethod
    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _decide(self, decision: str) -> None:
        self.approve_row.hide()
        self.set_state("wait" if decision != "deny" else "denied")
        self.decided.emit(self.call_id, decision)

    def set_state(self, state: str) -> None:
        icon, color, key = self.STATES[state]
        running = state == "run"
        self.state_icon.setVisible(not running)
        if running:
            self.spinner.start()
        else:
            self.spinner.stop()
            self.state_icon.setPixmap(icons.pixmap(icon, T[color], 15))
        self.status_lb.setText(t(key))
        self.status_lb.setStyleSheet(f"color:{T[color]};font-size:12px;background:transparent;border:none;")

    def ask_approval(self) -> None:
        self.set_state("approve")
        self.approve_row.show()

    def set_running(self) -> None:
        self.approve_row.hide()
        self.set_state("run")

    def finish(self, text: str, is_error: bool, duration: float, denied: bool) -> None:
        self.approve_row.hide()
        self._result = text
        self.set_state("denied" if denied else ("error" if is_error else "ok"))
        if duration:
            self.status_lb.setText(f"{self.status_lb.text()} · {duration} {t('sec')}")
        if self.more.isChecked():
            self._fill()

    def _fill(self) -> None:
        args = json.dumps(self._args, ensure_ascii=False, indent=2) if self._args else ""
        parts = []
        if args:
            parts.append(f"{t('chat_tool_args')}:\n{_clip(args, 3000)}")
        if self._result:
            parts.append(f"{t('chat_tool_result')}:\n{_clip(self._result)}")
        self.detail.setPlainText("\n\n".join(parts))

    def _toggle(self, on: bool) -> None:
        self.more.setIcon(icons.icon("chevron-up" if on else "chevron-down", T["text_2"], 14))
        if on:
            self._fill()
        if not anim.enabled():
            self.detail.setVisible(on)
            return
        a = getattr(self, "_exp", None)
        if a is not None:
            a.stop()
        full = 240
        self._exp = QPropertyAnimation(self.detail, b"maximumHeight", self)
        self._exp.setDuration(220)
        self._exp.setEasingCurve(QEasingCurve.OutCubic)
        if on:
            self.detail.setMaximumHeight(0)
            self.detail.setVisible(True)
            self._exp.setStartValue(0)
            self._exp.setEndValue(full)
        else:
            self._exp.setStartValue(self.detail.height())
            self._exp.setEndValue(0)
            self._exp.finished.connect(lambda: (self.detail.setVisible(False), self.detail.setMaximumHeight(full)))
        self._exp.start()

    def set_limit(self, width: int) -> None:
        self.setMaximumWidth(max(240, width))


class _ChatInput(QPlainTextEdit):
    """Поле ввода: Enter отправляет, Shift+Enter переносит строку, файлы из буфера — вложения.

    Высота поля равна высоте его текста (строки + поля), поэтому кнопки строки ввода стоят
    на одной линии и на пустом поле, и при нескольких строках.
    """

    submit = Signal()
    files_pasted = Signal(list)

    MIN_H = 36      # нижняя граница высоты: её задаёт ChatPanel по высоте кнопок строки ввода
    MAX_H = 168     # дальше — прокрутка внутри поля

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChatInput")
        self._pad: int | None = None
        self._fitting = False
        self.document().setDocumentMargin(0)     # поля задаёт сам виджет (_fit), а не макет документа
        self.textChanged.connect(self._fit)

    def set_row_height(self, h: int) -> None:
        """Нижняя граница высоты поля = высоте кнопок ряда; высоту кнопкам задала тема.

        Поля (padding) вычисляются из этой высоты и межстрочного интервала, поэтому строка
        текста стоит на уровне надписей кнопок — текст и кнопки выглядят одной линией,
        а не разнесёнными по высоте.
        """
        self.MIN_H = max(_ChatInput.MIN_H, h)
        self.setMinimumHeight(self.MIN_H)
        self._fit()

    def _fit(self) -> None:
        """Высота = строки текста (с учётом переносов) + поля, которые задаёт само поле.

        Одна строка = MIN_H, то есть высота кнопок ряда: пустое поле и кнопки стоят на одной
        линии. Дальше поле растёт строками текста, а кнопки остаются прижаты к нижней кромке.
        Межстрочный интервал и поля берутся у фактического шрифта и темы, поэтому совпадение
        сохраняется при любой теме и при любом масштабе экрана.
        """
        ls = self.fontMetrics().lineSpacing()
        lines = max(1, int(self.document().lineCount()))
        pad = max(4, (self.MIN_H - ls) // 2)
        if pad != self._pad:
            self._pad = pad       # поля пересчитываются только при реальном изменении метрик
            self.setStyleSheet(f"QPlainTextEdit {{ padding: {pad}px 12px; }}")
        h = max(self.MIN_H, min(self.MAX_H, lines * ls + 2 * pad))
        if h != self.height():
            self.setFixedHeight(h)
            self._relayout()

    def _relayout(self) -> None:
        """Пересчитывает макет сразу после смены высоты поля.

        Иначе после первой же компоновки поле уже выросло, а кнопки ещё нет, и на один кадр
        они окажутся прижаты не к нижней кромке, а к верху выросшего поля.
        """
        if self._fitting:
            return
        self._fitting = True
        try:
            parent = self.parentWidget()
            while parent is not None:
                if parent.layout() is not None:
                    parent.layout().activate()
                parent = parent.parentWidget()
        finally:
            self._fitting = False

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)      # новая ширина -> перенос строк другой -> высоту считаем заново
        self._fit()

    def insertFromMimeData(self, source):  # noqa: N802
        """Вставка файлов из проводника (Ctrl+V) -> вложения; обычный текст вставляется как всегда."""
        if source.hasUrls() and all(u.isLocalFile() for u in source.urls()):
            self.files_pasted.emit([u.toLocalFile() for u in source.urls()])
            return
        super().insertFromMimeData(source)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)


class ChatPanel(QFrame):
    send_requested = Signal(list)       # история (без системного сообщения), последним — сообщение пользователя
    stop_requested = Signal()
    exit_requested = Signal()
    approval_decided = Signal(str, str)  # id вызова, решение
    tools_requested = Signal()          # открыть диалог «Инструменты и MCP»
    settings_requested = Signal()       # открыть настройки чата
    notify = Signal(str, str)           # (текст, уровень) — для тостов

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[dict] = []
        self._bubbles: list[QWidget] = []          # пузыри и карточки инструментов по порядку
        self._cards: dict[str, _ToolCard] = {}
        self._meta: dict[str, dict] = {}
        self._current: _Bubble | None = None
        self._attachments: list[dict] = []
        self._busy = False
        self._store: ChatStore | None = None
        self._session_id: int | None = None
        self._model_id = ""
        self.setAcceptDrops(True)
        self._pending = ""
        self._pending_think = ""
        self._flush = QTimer(self)
        self._flush.setInterval(60)
        self._flush.timeout.connect(self._render_stream)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # --- шапка
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(IconTile("sparkles", "blue", 38))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(t("chat_title"), "H2"))
        self.model_lb = ElidedLabel("", "Faint")
        col.addWidget(self.model_lb)
        head.addLayout(col, 1)
        self.tools_btn = Button(t("chat_tools"), "wand", "secondary", "sm")
        self.tools_btn.setToolTip(t("chat_tools_tip"))
        self.tools_btn.clicked.connect(self.tools_requested)
        head.addWidget(self.tools_btn)
        self.new_btn = icon_button("plus", t("chat_new"), 32, 16)
        self.new_btn.clicked.connect(self.new_chat)
        head.addWidget(self.new_btn)
        self.hist_btn = icon_button("clock", t("chat_sessions"), 32, 16)
        self.hist_btn.clicked.connect(self._show_sessions)
        head.addWidget(self.hist_btn)
        self.set_btn = icon_button("sliders", t("chat_settings"), 32, 16)
        self.set_btn.clicked.connect(self.settings_requested)
        head.addWidget(self.set_btn)
        self.clear_btn = icon_button("trash", t("chat_clear"), 32, 16)
        self.clear_btn.clicked.connect(self.clear)
        head.addWidget(self.clear_btn)
        self.exit_btn = Button(t("chat_exit"), "x", "outline", "sm")
        self.exit_btn.setToolTip(t("chat_exit_tip"))
        self.exit_btn.clicked.connect(self.exit_requested)
        head.addWidget(self.exit_btn)
        root.addLayout(head)
        root.addWidget(hline())

        # --- сообщения
        self.scroll = VScroll()
        self.scroll.lay.setContentsMargins(0, 0, 10, 0)
        self.scroll.lay.setSpacing(12)
        self.empty = self._build_empty()
        self.scroll.lay.addWidget(self.empty)
        self.msgs = QVBoxLayout()
        self.msgs.setSpacing(10)
        self.scroll.lay.addLayout(self.msgs)
        self.scroll.lay.addStretch(1)
        root.addWidget(self.scroll, 1)

        self.status_lb = QLabel()
        self.status_lb.setStyleSheet(f"color:{T['text_3']};font-size:12px;")
        self.status_lb.hide()
        root.addWidget(self.status_lb)

        # --- ввод
        box = QFrame()
        box.setObjectName("Inset")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(12, 8, 8, 8)
        bv.setSpacing(6)
        self.chips_host = QWidget()
        self.chips_flow = FlowLayout(self.chips_host, spacing=6)
        self.chips_host.hide()
        bv.addWidget(self.chips_host)
        bl = QHBoxLayout()
        bl.setSpacing(8)
        # Кнопка «прикрепить файл» — той же высоты, что и «Отправить»: обе получают высоту из темы
        # (метрики кнопки размера md), поэтому элементы строки ввода стоят на одной линии.
        self.attach_btn = Button("", "paperclip", "ghost", "md", icon_size=18)
        self.attach_btn.setStyleSheet("QPushButton { padding: 0 8px; }")   # компактная, но не ниже остальных
        self.attach_btn.setToolTip(t("chat_attach"))
        self.attach_btn.setAccessibleName(t("chat_attach"))
        self.attach_btn.clicked.connect(self.pick_files)
        bl.addWidget(self.attach_btn, 0, Qt.AlignBottom)
        self.input = _ChatInput()
        self.input.setProperty("flat", "true")
        self.input.setPlaceholderText(t("chat_placeholder"))
        self.input.submit.connect(self._on_send)
        self.input.files_pasted.connect(self.add_attachments)
        bl.addWidget(self.input, 1, Qt.AlignBottom)
        self.send_btn = Button(t("chat_send"), "send", "primary")
        self.send_btn.setMinimumWidth(130)
        self.send_btn.clicked.connect(self._on_send)
        bl.addWidget(self.send_btn, 0, Qt.AlignBottom)
        self.input.set_row_height(max(self.input.MIN_H, self.send_btn.sizeHint().height(),
                                      self.attach_btn.sizeHint().height()))
        bv.addLayout(bl)
        root.addWidget(box)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addWidget(label(t("chat_hint"), "Faint"), 1)
        tl = label(t("chat_use_tools"), "Muted")
        self.tools_toggle = Toggle(True)
        self.tools_toggle.setToolTip(t("chat_use_tools_tip"))
        tl.setToolTip(t("chat_use_tools_tip"))
        foot.addWidget(tl)
        foot.addWidget(self.tools_toggle)
        foot.addSpacing(10)
        ctx_lbl = label(t("chat_context"), "Muted")
        self.ctx_toggle = Toggle(False)
        self.ctx_toggle.setToolTip(t("chat_context_tip"))
        ctx_lbl.setToolTip(t("chat_context_tip"))
        foot.addWidget(ctx_lbl)
        foot.addWidget(self.ctx_toggle)
        root.addLayout(foot)

    # --- пустое состояние -------------------------------------------------------------------------
    def _build_empty(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(0, 36, 0, 12)
        outer.addStretch(1)
        col = QWidget()
        col.setMaximumWidth(460)
        col.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("sparkles", "#7ea6ff", 34))
        ic.setAlignment(Qt.AlignHCenter)
        v.addWidget(ic)
        h = label(t("chat_empty_title"), "H2")
        h.setAlignment(Qt.AlignHCenter)
        v.addWidget(h)
        sub = label(t("chat_empty_sub"), "Muted", wrap=True)
        sub.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        v.addWidget(sub)
        v.addSpacing(6)
        for key in ("chat_s1", "chat_s2", "chat_s3", "chat_s4"):
            row = QHBoxLayout()
            row.addStretch(1)
            b = Button(t(key), None, "secondary", "sm")
            b.clicked.connect(lambda _=False, k=key: self._suggest(t(k)))
            row.addWidget(b)
            row.addStretch(1)
            v.addLayout(row)
        outer.addWidget(col, 10)
        outer.addStretch(1)
        return w

    def _suggest(self, text: str) -> None:
        self.input.setPlainText(text)
        self._on_send()

    # --- публичное API ------------------------------------------------------------------------------
    def set_store(self, store: ChatStore, last_session: int = 0) -> None:
        self._store = store
        if last_session and not self._history:
            self.open_session(int(last_session), quiet=True)

    def session_id(self) -> int:
        return self._session_id or 0

    def set_model_name(self, name: str, model_id: str = "") -> None:
        self.model_lb.setText(name)
        self._model_id = model_id or self._model_id

    def set_tools_summary(self, text: str, enabled_any: bool) -> None:
        self.tools_btn.setText(text)
        self.tools_btn.setToolTip(t("chat_tools_tip"))
        self.tools_toggle.setEnabled(True)
        if not enabled_any and self.tools_toggle.isChecked():
            self.tools_btn.setToolTip(t("chat_tools_none"))

    def context_enabled(self) -> bool:
        return self.ctx_toggle.isChecked()

    def tools_enabled(self) -> bool:
        return self.tools_toggle.isChecked()

    def set_tools_enabled(self, on: bool) -> None:
        self.tools_toggle.setChecked(on)

    def focus_input(self) -> None:
        self.input.setFocus()

    def is_empty(self) -> bool:
        return not self._history

    def history(self) -> list[dict]:
        return sanitize_history(self._history)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_btn.setText(t("chat_stop") if busy else t("chat_send"))
        self.send_btn.set_icon("stop" if busy else "send")
        self.send_btn.set_variant("danger" if busy else "primary")
        self.send_btn.set_pulse(busy)
        for b in (self.clear_btn, self.new_btn, self.hist_btn):
            b.setEnabled(not busy)
        if not busy:
            self.status_lb.hide()

    # --- ход ответа (сигналы AgentEngine) ------------------------------------------------------------------
    def begin_answer(self) -> None:
        self._pending = self._pending_think = ""
        self._current = self._add_bubble("assistant", "", typing=True)
        self._flush.start()

    def on_step(self, step: int) -> None:
        if self._current is None:
            self._pending = self._pending_think = ""
            self._current = self._add_bubble("assistant", "", typing=True)
            self._flush.start()
        if step > 1:
            self._show_status(t("chat_step", n=step))

    def append_answer(self, chunk: str) -> None:
        self._pending += chunk

    def append_reasoning(self, chunk: str) -> None:
        self._pending_think += chunk

    def _render_stream(self) -> None:
        if self._current is not None:
            self._current.set_text(self._pending, typing=not self._pending_think)
            if self._pending_think:
                self._current.set_reasoning(self._pending_think, live=not self._pending)
            self._scroll_bottom()

    def _show_status(self, text: str) -> None:
        self.status_lb.setText(text)
        self.status_lb.setVisible(bool(text))

    def on_notice(self, text: str) -> None:
        lb = QLabel(f"ⓘ {text}")
        lb.setWordWrap(True)
        lb.setStyleSheet(f"color:{T['text_3']};font-size:12px;")
        lb.role = "notice"
        self.msgs.addWidget(lb)
        self._bubbles.append(lb)
        self._scroll_bottom()

    def on_tool_started(self, info: dict) -> None:
        self._meta[info["id"]] = info
        card = _ToolCard(info)
        card.set_limit(self._limit())
        card.decided.connect(self.approval_decided)
        self._cards[info["id"]] = card
        self.msgs.addWidget(card)
        self._bubbles.append(card)
        anim.fade_in(card, 240)
        self._show_status(t("chat_tool_working", name=info.get("title") or info.get("name", "")))
        self._scroll_bottom(force=True)

    def on_approval_needed(self, info: dict) -> None:
        card = self._cards.get(info["id"])
        if card:
            card.ask_approval()
            self._scroll_bottom(force=True)
        self._show_status(t("chat_waiting_approval"))

    def on_tool_running(self, call_id: str) -> None:
        card = self._cards.get(call_id)
        if card:
            card.set_running()

    def on_message(self, msg: dict) -> None:
        """Завершён ход модели (assistant) или выполнен инструмент (tool)."""
        if msg.get("role") == "assistant":
            self._finish_assistant(msg)
        elif msg.get("role") == "tool":
            cid = msg.get("tool_call_id", "")
            meta = self._meta.get(cid, {})
            card = self._cards.get(cid)
            if card is None:
                card = _ToolCard({"id": cid, "name": msg.get("name", ""), "args": meta.get("args", {})})
                card.set_limit(self._limit())
                self.msgs.addWidget(card)
                self._bubbles.append(card)
                self._cards[cid] = card
            card.finish(msg.get("content", ""), bool(msg.get("is_error")), msg.get("duration", 0.0), bool(msg.get("denied")))
            entry = {k: msg[k] for k in ("role", "tool_call_id", "name", "content") if k in msg}
            entry.update({"is_error": bool(msg.get("is_error")), "duration": msg.get("duration", 0.0),
                          "denied": bool(msg.get("denied")), "title": meta.get("title", msg.get("name", "")),
                          "server": meta.get("server", ""), "risk": meta.get("risk", ""), "args": meta.get("args", {})})
            self._history.append(entry)
            self._show_status(t("chat_thinking_status"))
            self._scroll_bottom(force=True)

    def _finish_assistant(self, msg: dict) -> None:
        self._flush.stop()
        text = (msg.get("content") or "").strip()
        think = (msg.get("reasoning") or self._pending_think or "").strip()
        calls = msg.get("tool_calls")
        bubble, self._current = self._current, None
        self._pending = self._pending_think = ""
        if msg.get("synthetic"):                       # служебная заметка движка (лимит шагов) — только показать
            if bubble is not None:
                self._remove(bubble)
            self.on_notice(text)
            return
        if bubble is not None:
            if text or think:
                bubble.set_text(text)
                bubble.set_reasoning(think)
                bubble.set_stats(msg.get("stats") or {}, bool(msg.get("truncated")))
            else:
                self._remove(bubble)                  # ход состоял только из вызовов инструментов
        if text or think or calls:
            entry = {"role": "assistant", "content": text, "reasoning": think, "stats": msg.get("stats") or {},
                     "truncated": bool(msg.get("truncated"))}
            if calls:
                entry["tool_calls"] = calls
            self._history.append(entry)
        self._scroll_bottom(force=True)

    def end_answer(self, stopped: bool) -> None:
        self._flush.stop()
        if self._current is not None:
            text = self._pending.strip()
            if not text:
                self._remove(self._current)
            else:                       # оборванный потоком текст, который движок не успел сообщить целиком
                self._current.set_text(text)
                self._history.append({"role": "assistant", "content": text})
        self._current = None
        self._show_status("")
        self._refresh_regen()
        self._save()
        self._scroll_bottom(force=True)

    def fail_answer(self, message: str) -> None:
        self._flush.stop()
        if self._current is not None:
            self._remove(self._current)
        self._current = None
        self._show_status("")
        # неотвеченный вопрос возвращаем в поле ввода, чтобы можно было повторить
        if self._history and self._history[-1]["role"] == "user":
            last = self._history.pop()
            for b in reversed(self._bubbles):
                if getattr(b, "role", "") == "user":
                    self._remove(b)
                    break
            self.input.setPlainText(last.get("display", last["content"]))
            self._attachments = list(last.get("atts", []))
            self._refresh_chips()
        elif message:
            self.on_notice(message)
        self._refresh_regen()
        self._save()

    # --- сессии ------------------------------------------------------------------------------------------------
    def _save(self) -> None:
        if self._store is not None and self._history:
            self._session_id = self._store.save(self._session_id, self._history, self._model_id)

    def new_chat(self) -> None:
        if self._busy:
            return
        self._save()
        self._reset_view()
        self._session_id = None

    def clear(self) -> None:
        """Удаляет текущий чат (и из сохранённых)."""
        if self._busy:
            return
        if self._store is not None and self._session_id:
            self._store.delete(self._session_id)
        self._session_id = None
        self._reset_view()

    def _reset_view(self) -> None:
        for b in list(self._bubbles):
            self._remove(b, keep_empty=True)
        self._history.clear()
        self._cards.clear()
        self._meta.clear()
        self.empty.show()

    def _show_sessions(self) -> None:
        if self._store is None or self._busy:
            return
        items: list[SessionInfo] = self._store.list()
        menu = QMenu(self)
        if not items:
            a = menu.addAction(t("chat_no_sessions"))
            a.setEnabled(False)
        for s in items:
            a = menu.addAction(("● " if s.id == self._session_id else "") + s.title)
            a.setToolTip(s.updated.replace("T", " "))
            a.triggered.connect(lambda _=False, sid=s.id: self.open_session(sid))
        menu.exec(self.hist_btn.mapToGlobal(self.hist_btn.rect().bottomLeft()))

    def open_session(self, session_id: int, quiet: bool = False) -> None:
        if self._store is None or self._busy:
            return
        data = self._store.load(session_id)
        if not data:
            if not quiet:
                self.notify.emit(t("chat_session_missing"), "warning")
            return
        self._save()
        self._reset_view()
        self._session_id = session_id
        self._history = sanitize_history(data)
        self._rebuild()

    def _rebuild(self) -> None:
        for h in self._history:
            role = h.get("role")
            if role == "user":
                self._add_bubble("user", h.get("display", h.get("content", "")), atts=h.get("atts"))
            elif role == "assistant":
                txt, think = h.get("content", ""), h.get("reasoning", "")
                if txt or think:
                    b = self._add_bubble("assistant", txt)
                    b.set_reasoning(think)
                    b.set_stats(h.get("stats") or {}, bool(h.get("truncated")))
            elif role == "tool":
                info = {"id": h.get("tool_call_id", ""), "name": h.get("name", ""), "title": h.get("title", ""),
                        "server": h.get("server", ""), "args": h.get("args", {}), "risk": h.get("risk", "")}
                card = _ToolCard(info)
                card.set_limit(self._limit())
                card.finish(h.get("content", ""), bool(h.get("is_error")), h.get("duration", 0.0), bool(h.get("denied")))
                self.msgs.addWidget(card)
                self._bubbles.append(card)
                self._cards[info["id"]] = card
        self._refresh_regen()
        self._scroll_bottom(force=True)

    # --- вложения ----------------------------------------------------------------------------------------
    def pick_files(self) -> None:
        img = " ".join(f"*{e}" for e in sorted(IMAGE_EXT))
        txt = " ".join(f"*{e}" for e in sorted(TEXT_EXT))
        files, _ = QFileDialog.getOpenFileNames(
            self.window(), t("chat_attach"), "",
            f"{t('chat_filter_all')} ({img} {txt});;{t('ref_filter_image')} ({img});;{t('chat_filter_text')} ({txt});;All (*)")
        self.add_attachments(files)

    def add_attachments(self, files: list) -> None:
        bad: list[str] = []
        for f in files:
            if len(self._attachments) >= MAX_FILES:
                self.notify.emit(t("chat_too_many", n=MAX_FILES), "warning")
                break
            path = Path(f)
            if not path.is_file() or any(a["path"] == str(path) for a in self._attachments):
                continue
            if path.suffix.lower() in IMAGE_EXT:
                self._attachments.append({"kind": "image", "path": str(path), "name": path.name})
                continue
            res = read_text_file(path)
            if res is None:
                bad.append(path.name)
                continue
            text, cut = res
            self._attachments.append({"kind": "text", "path": str(path), "name": path.name, "text": text, "cut": cut})
        if bad:
            self.notify.emit(t("chat_unsupported", names=", ".join(bad)), "warning")
        self._refresh_chips()

    def _refresh_chips(self) -> None:
        clear_layout(self.chips_flow)
        for i, a in enumerate(self._attachments):
            chip = QFrame()
            chip.setStyleSheet(f"QFrame{{background:{T['bg_card']};border:1px solid {T['border']};border-radius:12px;}}")
            h = QHBoxLayout(chip)
            h.setContentsMargins(6, 3, 4, 3)
            h.setSpacing(6)
            ic = QLabel()
            ic.setStyleSheet("border:none;background:transparent;")
            pm = _thumb(a["path"], 22) if a["kind"] == "image" else None
            ic.setPixmap(pm.scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation) if pm else icons.pixmap("file-text", "#8fa6ff", 16))
            h.addWidget(ic)
            nm = ElidedLabel(a["name"])
            nm.setMaximumWidth(170)
            nm.setToolTip(a["name"] + (("\n" + t("chat_cut")) if a.get("cut") else ""))
            nm.setStyleSheet(f"border:none;background:transparent;color:{T['text']};font-size:12px;")
            h.addWidget(nm)
            x = QPushButton()
            x.setIcon(icons.icon("x", T["text_2"], 12))
            x.setFixedSize(20, 20)
            x.setCursor(Qt.PointingHandCursor)
            x.setToolTip(t("ref_remove"))
            x.setStyleSheet("QPushButton{border:none;background:transparent;border-radius:10px;padding:0;}QPushButton:hover{background:rgba(229,56,59,0.6);}")
            x.clicked.connect(lambda _=False, k=i: self._remove_attachment(k))
            h.addWidget(x)
            self.chips_flow.addWidget(chip)
        self.chips_host.setVisible(bool(self._attachments))

    def _remove_attachment(self, i: int) -> None:
        if 0 <= i < len(self._attachments):
            self._attachments.pop(i)
            self._refresh_chips()

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if files:
            self.add_attachments(files)
            e.acceptProposedAction()

    # --- внутреннее -------------------------------------------------------------------------------------
    def _on_send(self) -> None:
        if self._busy:
            self.stop_requested.emit()
            return
        text = self.input.toPlainText().strip()
        atts = list(self._attachments)
        if not text and not atts:
            return
        content = text
        for a in atts:
            if a["kind"] == "text":
                content += f"\n\n[Attached file: {a['name']}]\n```\n{a['text']}\n```" + ("\n[file was truncated]" if a.get("cut") else "")
            else:
                content += f"\n\n[Attached image: {a['name']}]"
        content = content.strip()
        self.input.clear()
        self._attachments = []
        self._refresh_chips()
        entry = {"role": "user", "content": content, "display": text, "atts": atts,
                 "images": [a["path"] for a in atts if a["kind"] == "image"]}
        self._history = sanitize_history(self._history)
        self._history.append(entry)
        self._add_bubble("user", text, atts=atts)
        self.send_requested.emit(list(self._history))

    def _regenerate(self) -> None:
        """Повторить последний ответ: отбрасываем всё после последнего сообщения пользователя."""
        if self._busy or not self._history:
            return
        while self._history and self._history[-1]["role"] != "user":
            self._history.pop()
        if not self._history:
            return
        while self._bubbles and getattr(self._bubbles[-1], "role", "") != "user":
            self._remove(self._bubbles[-1], keep_empty=True)
        self.send_requested.emit(list(self._history))

    def _add_bubble(self, role: str, text: str, typing: bool = False, atts=None) -> _Bubble:
        self.empty.hide()
        b = _Bubble(role, text, self._regenerate, atts)
        b.set_text(text, typing)
        b.set_limit(self._limit())
        self.msgs.addWidget(b)
        self._bubbles.append(b)
        anim.fade_in(b, 220)
        self._scroll_bottom(force=True)
        return b

    def _remove(self, b: QWidget, keep_empty: bool = False) -> None:
        if b in self._bubbles:
            self._bubbles.remove(b)
        if isinstance(b, _ToolCard):
            self._cards.pop(b.call_id, None)
        self.msgs.removeWidget(b)
        b.deleteLater()
        if not self._bubbles and not keep_empty:
            self.empty.show()

    def _refresh_regen(self) -> None:
        last_ai = None
        for b in self._bubbles:
            if isinstance(b, _Bubble):
                b.regen_btn.hide()
                if b.role == "assistant" and b.text.strip():
                    last_ai = b
        if last_ai is not None and not self._busy:
            last_ai.regen_btn.show()

    def _limit(self) -> int:
        return int(self.scroll.viewport().width() * 0.84)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        lim = self._limit()
        for b in self._bubbles:
            if hasattr(b, "set_limit"):
                b.set_limit(lim)

    def _scroll_bottom(self, force: bool = False) -> None:
        sb = self.scroll.verticalScrollBar()
        if force or sb.maximum() - sb.value() < 120:
            QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))
