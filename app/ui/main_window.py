"""Главное окно: собирает панели и связывает их с Core/Strata. Бизнес-логики здесь нет — только вызовы менеджеров."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QRadialGradient, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QPlainTextEdit, QTextBrowser, QStackedWidget, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QSizeGrip,
                               QVBoxLayout, QWidget)

from app.core import h3
from app.core.agent_engine import AgentEngine, RunParams
from app.core.chat_store import ChatStore, sanitize_history
from app.core.comfy_bridge import ComfyBridge
from app.core.history_manager import HistoryManager, Version
from app.core.model_manager import Model, ModelManager
from app.core.prompt_engine import GenerationRequest, PromptEngine
from app.core.reference_manager import ReferenceManager
from app.core.context_builder import ContextOptions
from app.core.style_manager import StyleManager
from app.core.translator import Translator
from app.database.database import Database
from app.mcp.manager import McpManager
from app.strata.strata_manager import StrataManager, StrataState
from app.ui.anim import FadeStack
from app.ui import icons, theme
from app.ui.chat_dialogs import ChatSettingsDialog, ToolsDialog
from app.ui.chat_panel import ChatPanel
from app.ui.dialogs import (BaseDialog, CompareDialog, diff_html, GenerationsDialog, ModelDialog, SettingsDialog, confirm, info)
from app.ui.header import HeaderBar
from app.ui.history_panel import HistoryPanel
from app.ui.model_card import ModelCard
from app.ui.prompt_editor import CompactBar, ParamsSection, RequestSection, SelectedStyles
from app.ui.references_panel import ReferencesPanel
from app.ui.result_panel import EditPanel, ResultPanel
from app.ui.side_slot import SideSlot
from app.ui.strata_panel import StrataPanel
from app.ui.styles_panel import StylesPanel
from app.ui.widgets import Button, Toast, VScroll, label
from app.utils import paths, system_metrics
from app.utils.config import Config
from app.utils.i18n import t
from app.utils.log import get_logger

log = get_logger("main")
EDGE = 7


class Root(QWidget):
    """Фон окна с «туманностями» + изменение размера за края (окно без системной рамки)."""

    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.setObjectName("Root")
        self._w = window
        self.setMouseTracking(True)
        self.frameless = False

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.T["bg_app"]))
        for cx, cy, r, rgba in theme.T.get("nebula", ()):
            col = QColor(*rgba)
            rad = max(self.width(), self.height()) * r
            g = QRadialGradient(QPointF(self.width() * cx, self.height() * cy), rad)
            g.setColorAt(0, col)
            c2 = QColor(col)
            c2.setAlpha(0)
            g.setColorAt(1, c2)
            p.fillRect(self.rect(), g)

    def _edges(self, pos) -> Qt.Edges:
        e = Qt.Edges()
        if self.window().isMaximized() or not self.frameless:
            return e
        if pos.x() <= EDGE:
            e |= Qt.LeftEdge
        if pos.x() >= self.width() - EDGE:
            e |= Qt.RightEdge
        if pos.y() <= EDGE:
            e |= Qt.TopEdge
        if pos.y() >= self.height() - EDGE:
            e |= Qt.BottomEdge
        return e

    def mouseMoveEvent(self, ev):
        e = self._edges(ev.position().toPoint())
        if e in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeFDiagCursor)
        elif e in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeBDiagCursor)
        elif e & (Qt.LeftEdge | Qt.RightEdge):
            self.setCursor(Qt.SizeHorCursor)
        elif e & (Qt.TopEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, ev):
        e = self._edges(ev.position().toPoint())
        if e and ev.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemResize(e)


class MainWindow(QMainWindow):
    _metrics = Signal(object, object)

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.setWindowTitle(theme.APP_NAME)
        self.setMinimumSize(1180, 720)
        native = bool(config.get("ui.native_titlebar", False))
        if not native:
            self.setWindowFlag(Qt.FramelessWindowHint, True)

        # --- core ---------------------------------------------------------
        self.styles = StyleManager()
        self.models = ModelManager()
        self.db = Database()
        self.history = HistoryManager(self.db)
        self.refs = ReferenceManager(self.history)
        self.strata = StrataManager(config, self)
        self.engine = PromptEngine(config, self.strata, self)
        self.translator = Translator(config, self.strata, self)
        self.mcp = McpManager(config, self)
        self.chat_engine = AgentEngine(config, self.strata, self.mcp, self)
        self.comfy = ComfyBridge(config, self)
        self.strata.pre_load_hook = self.comfy.prepare_for_llm

        # --- состояние сессии -----------------------------------------------
        self.model_id = ""
        self.gen_id: int | None = None
        self.versions: list[Version] = []
        self.current_version: Version | None = None
        self.session_request = ""
        self.dialog: list[tuple[str, str]] = []
        self._pending: dict | None = None
        self._after_stop = False
        self._after_stop_tr = False
        self._prev_text = ""
        self._stream_started = 0.0
        self._think_buf = ""             # рассуждения последней генерации (вкладка «Мысли»)
        self._show_reasoning_now = False
        self._prog = (0, 0)              # ≈токены рассуждений / ответа текущей генерации
        self._h3_frames = 0              # длина H3, отправленная в последнем запросе
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(500)
        self._live_timer.timeout.connect(self._tick_live)

        self._build_ui(native)
        self._wire()
        self._init_models()
        self.selected.set_styles(self.styles.all())
        self._render_session()
        self.toast_widget = Toast(self.root)
        self._metrics.connect(self._on_metrics)
        self._metric_timer = QTimer(self)
        self._metric_timer.setInterval(3000)
        self._metric_timer.timeout.connect(self._poll_metrics)
        self._metric_timer.start()
        self._poll_metrics()
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._idle_unload)
        self._on_state(self.strata.state, "")

    # =================================================================== UI
    def _build_ui(self, native: bool) -> None:
        self.root = Root(self)
        self.root.frameless = not native
        self.setCentralWidget(self.root)
        outer = QVBoxLayout(self.root)
        outer.setContentsMargins(16, 10, 16, 6)
        outer.setSpacing(12)
        self.header = HeaderBar(native)
        outer.addWidget(self.header)

        # рабочая область: стр. 0 — три колонки (режим промтов), стр. 1 — чат на всё окно
        self.workspace = FadeStack()

        work = QWidget()
        work.setObjectName("WorkArea")
        cols = QHBoxLayout(work)
        cols.setSpacing(12)

        # «Студия»: слева сцены/история, в центре запрос+вкладки, справа результат во всю высоту.
        # Стили — выдвижная панель (диалог), чтобы не занимать центральную прокрутку.
        self.styles_panel = StylesPanel(self.styles)
        self.styles_panel.setMinimumWidth(300)

        # левая — две вкладки: «История версий» и «Стили» (сворачивается в рейку с быстрыми кнопками)
        left = QFrame()
        left.setObjectName("Panel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 14, 8, 12)
        ll.setSpacing(10)
        ltabs = QHBoxLayout()
        ltabs.setSpacing(6)
        self.left_group = QButtonGroup(self)
        for i, (key, ic) in enumerate((("history_title", "clock"), ("styles", "brush"))):
            b = Button(t(key), ic, "secondary", "sm")
            b.setProperty("tab", "pill")
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.left_group.addButton(b, i)
            ltabs.addWidget(b)
        ltabs.addStretch(1)
        ll.addLayout(ltabs)
        self.left_stack = FadeStack()
        self.history_panel = HistoryPanel()
        self.left_scroll = VScroll()
        self.left_scroll.lay.addWidget(self.history_panel)
        self.left_scroll.lay.addStretch(1)
        self.left_stack.addWidget(self.left_scroll)
        self.left_stack.addWidget(self.styles_panel)
        self.left_group.idClicked.connect(self._set_left_tab)
        ll.addWidget(self.left_stack, 1)
        self.left_slot = SideSlot(
            left, 360, edge="left", icon="panel-left",
            expand_tip=t("panel_history_show"), collapse_tip=t("panel_history_hide"))
        self.left_slot.add_rail_button("clock", t("history_title"), lambda: self._set_left_tab(0))
        self.left_slot.add_rail_button("brush", t("styles"), lambda: self._set_left_tab(1))
        cols.addWidget(self.left_slot, 0)

        # центр: вкладки (модель и стили / референсы / параметры) + закреплённый запрос с компактной панелью
        center = QFrame()
        center.setObjectName("Panel")
        cl = QVBoxLayout(center)
        cl.setContentsMargins(14, 14, 8, 12)
        cl.setSpacing(10)
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.center_group = QButtonGroup(self)
        self.center_tabs: list[Button] = []
        for i, key in enumerate(("ctab_refs", "ctab_setup", "ctab_params")):
            b = Button(t(key), None, "secondary", "sm")
            b.setProperty("tab", "pill")
            b.setCheckable(True)
            self.center_group.addButton(b, i)
            self.center_tabs.append(b)
            tabs.addWidget(b)
        tabs.addStretch(1)
        cl.addLayout(tabs)
        self.center_stack = FadeStack()
        self.model_card = ModelCard()
        self.refs_panel = ReferencesPanel(self.refs)
        self.selected = SelectedStyles()
        self.params = ParamsSection()
        # 0 — референсы (по умолчанию), 1 — системный промт (на всю высоту), 2 — параметры.
        # Выбор стилей живёт в левой вкладке «Стили»; self.selected оставлен для совместимости, но не показывается.
        refs_page = VScroll()
        refs_page.lay.addWidget(self.refs_panel)
        # под референсами — какие стили сейчас активны (чипы; × выключает стиль, выбор — во вкладке «Стили» слева)
        refs_page.lay.addWidget(self.selected)
        self.selected.manage.hide()
        refs_page.lay.addStretch(1)
        self.center_stack.addWidget(refs_page)
        self.center_stack.addWidget(self.model_card)
        params_page = VScroll()
        params_page.lay.addWidget(self.params)
        params_page.lay.addStretch(1)
        self.center_stack.addWidget(params_page)
        self.center_scroll = refs_page
        self.center_group.idClicked.connect(self._set_center_tab)
        cl.addWidget(self.center_stack, 1)
        self._center_cl = cl
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self._fit_center)
        for page in (refs_page, params_page):
            page.body.installEventFilter(self)     # контент изменился -> пересчитать высоту верхнего блока
        self._set_center_tab(0, save=False)   # при запуске всегда открыты «Референсы»

        self.request = RequestSection()
        self.compact = CompactBar(self.params)
        self.request.body_lay.insertWidget(1, self.compact)
        dock = QFrame()
        dock.setObjectName("RequestDock")
        dl = QVBoxLayout(dock)
        dl.setContentsMargins(12, 10, 12, 10)
        dl.setSpacing(0)
        dl.addWidget(self.request)
        cl.addWidget(dock, 0)
        self._dock = dock

        self.model_card.set_open(True)
        center.setMinimumWidth(480)
        cols.addWidget(center, 1)

        # правая — результат на всю высоту, снизу вкладки: правки / diff / мысли / лог Strata
        right = QFrame()
        right.setObjectName("Panel")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(14, 14, 10, 12)
        rl.setSpacing(10)
        self.result = ResultPanel()
        self.edit_panel = EditPanel()
        self.result.set_language(self.config.get("ui.translate_lang", "Russian"))
        rl.addWidget(self.result, 3)
        btabs = QHBoxLayout()
        btabs.setSpacing(6)
        self.bottom_group = QButtonGroup(self)
        for i, key in enumerate(("btab_edit", "btab_diff", "btab_thinking", "btab_log")):
            b = Button(t(key), None, "secondary", "sm")
            b.setProperty("tab", "pill")
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.bottom_group.addButton(b, i)
            btabs.addWidget(b)
        btabs.addStretch(1)
        self.log_refresh = Button("", "refresh", "secondary", "sm")
        self.log_refresh.setToolTip(t("log_refresh"))
        self.log_refresh.setFixedWidth(36)
        self.log_refresh.clicked.connect(self._load_strata_log)
        self.log_refresh.hide()
        btabs.addWidget(self.log_refresh)
        rl.addLayout(btabs)
        self.bottom_stack = FadeStack()
        self.bottom_stack.addWidget(self.edit_panel)
        self.diff_view = QTextBrowser()
        self.diff_view.setOpenLinks(False)
        self.thinking_view = QPlainTextEdit()
        self.thinking_view.setReadOnly(True)
        self.thinking_view.setPlaceholderText(t("thinking_empty"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log_view.setPlaceholderText(t("log_empty"))
        for w in (self.diff_view, self.thinking_view, self.log_view):
            w.setMinimumHeight(150)
            self.bottom_stack.addWidget(w)
        self.bottom_group.idClicked.connect(self._on_bottom_tab)
        rl.addWidget(self.bottom_stack, 2)
        self.strata_panel = StrataPanel(
            self.config.get("strata.after_stop", "unload"),
            bool(self.config.get("strata.auto_start", True)))
        rl.addWidget(self.strata_panel, 0)
        self.right_slot = SideSlot(
            right, 520, edge="right", icon="sparkles",
            expand_tip=t("panel_result_show"), collapse_tip=t("panel_result_hide"))
        # быстрые переходы по вкладкам прямо с узкой рейки свёрнутой правой колонки
        for i, (key, ic) in enumerate((("tab_result", "sparkles"), ("tab_session", "message"), ("tab_translate", "globe"))):
            self.right_slot.add_rail_button(ic, t(key), lambda i=i: self.result.group.button(i).click())
        for i, (key, ic) in enumerate((("btab_edit", "pencil"), ("btab_diff", "columns"), ("btab_thinking", "brain"),
                                       ("btab_log", "file-text"))):
            self.right_slot.add_rail_button(ic, t(key), lambda i=i: self.bottom_group.button(i).click())
        cols.addWidget(self.right_slot, 0)
        self.workspace.addWidget(work)   # 0 — режим промтов

        # --- режим чата: одна большая панель вместо всех колонок -----------
        self.chat = ChatPanel()
        self.chat.set_store(ChatStore(self.db), int(self.config.get("chat.last_session", 0) or 0))
        self.chat.set_tools_enabled(bool(self.config.get("chat.tools_enabled", True)))
        chat_page = QFrame()
        chat_page.setObjectName("Panel")
        cpl = QVBoxLayout(chat_page)
        cpl.setContentsMargins(16, 16, 10, 16)
        cpl.addWidget(self.chat)
        self.workspace.addWidget(chat_page)   # 1 — режим диалога
        outer.addWidget(self.workspace, 1)

        # восстановить свёрнутость боковых панелей
        if not bool(self.config.get("ui.history_open", True)):
            self.left_slot.set_open(False, animate=False)
        if not bool(self.config.get("ui.right_open", True)):
            self.right_slot.set_open(False, animate=False)

        # статус-бар
        bar = QWidget()
        bar.setObjectName("StatusBar")
        bl2 = QHBoxLayout(bar)
        bl2.setContentsMargins(4, 0, 0, 0)
        bl2.setSpacing(12)
        from app.ui.widgets import StatusDot

        self.sb_dot = StatusDot("#6c78a6", 6, False)
        self.sb_api = QLabel()
        self.sb_model = QLabel()
        self.sb_ctx = QLabel()
        self.sb_load = QLabel()
        self.sb_tps = QLabel()
        self.sb_tps.setToolTip(t("sb_tps_tip"))
        bl2.addWidget(self.sb_dot)
        bl2.addWidget(self.sb_api)
        bl2.addStretch(1)
        for lb in (self.sb_tps, self.sb_model, self.sb_ctx, self.sb_load):
            sep = QFrame()
            sep.setObjectName("Sep")
            sep.setFixedHeight(12)
            bl2.addWidget(lb)
            bl2.addWidget(sep)
        bl2.removeWidget(sep)
        sep.deleteLater()
        if not native:
            bl2.addWidget(QSizeGrip(self))
        outer.addWidget(bar)
        self._refresh_statusbar()
        self._last_tps: float | None = None
        self._ans_t0 = 0.0
        self._set_tps(None)

    def _set_tps(self, v: float | None, live: bool = False) -> None:
        txt = f"{v:.1f}" if v else t("sb_none")
        self.sb_tps.setText(f"{t('sb_tps')}: {txt}" + (" ●" if live and v else ""))

    def _wire(self) -> None:
        h = self.header
        h.settings_clicked.connect(self.open_settings)
        h.chat_toggled.connect(self.set_dialog_mode)
        h.minimize.connect(self.showMinimized)
        h.maximize.connect(self._toggle_max)
        h.close.connect(self.close)
        h.tabs.selected.connect(self.select_model)
        h.tabs.add_clicked.connect(self._add_menu)
        h.tabs.delete_requested.connect(self.delete_model)
        self.left_slot.toggled.connect(lambda o: self.config.set("ui.history_open", o))
        self.right_slot.toggled.connect(lambda o: self.config.set("ui.right_open", o))
        self.styles_panel.styles_changed.connect(lambda: self.selected.set_styles(self.styles.all()))
        self.selected.toggled.connect(self._toggle_style)
        mc = self.model_card
        mc.save_requested.connect(self.save_model)
        mc.delete_requested.connect(self.delete_model)
        mc.export_requested.connect(self.export_model)
        mc.import_requested.connect(self.import_model)
        mc.open_changed.connect(lambda o: self.config.set("ui.model_card_open", o))
        self.params.think_level.currentIndexChanged.connect(self._on_think_level)
        self.compact.h3.changed.connect(lambda n: self.config.set(f"ui.h3_length.{self.model_id}", int(n)))
        self.result.bump_tokens_requested.connect(self._bump_tokens)
        self.params.show_reasoning.toggled.connect(lambda on: self.config.set("generation.show_reasoning", on))
        self.request.generate.connect(self.on_generate)
        self.request.stop.connect(self.stop_generation_only)
        self.edit_panel.apply_requested.connect(self.on_apply_edit)
        self.result.manual_edit_committed.connect(self.on_manual_edit)
        self.history_panel.open_version.connect(self.open_version)
        self.history_panel.compare_requested.connect(self.compare_versions)
        self.history_panel.generations_requested.connect(self.open_generations)
        sp = self.strata_panel
        sp.autostart_changed.connect(lambda on: self.config.set("strata.auto_start", bool(on)))
        sp.unload_clicked.connect(lambda: self.strata.unload(self._model_api_id()))
        sp.stop_clicked.connect(self.on_stop)
        sp.mode_changed.connect(lambda m: self.config.set("strata.after_stop", m))
        s = self.strata
        s.state_changed.connect(self._on_state)
        s.notify.connect(lambda level, text: self.toast(text, level))
        self.comfy.notify.connect(self._on_comfy_notify)
        self.comfy.busy_changed.connect(self._on_comfy_busy)
        s.info_changed.connect(lambda _: self._refresh_statusbar())
        e = self.engine
        e.chunk.connect(self.result.append)
        e.reasoning.connect(self._on_reasoning)
        e.progress.connect(self._on_progress)
        e.warning.connect(lambda k: self.toast(t(k), "warning"))
        e.finished.connect(self._on_finished)
        e.failed.connect(self._on_failed)
        e.busy_changed.connect(self._on_busy)
        r = self.result
        r.translate_requested.connect(self.on_translate)
        r.translate_stop_requested.connect(self.translator.stop)
        r.language_changed.connect(lambda lang: self.config.set("ui.translate_lang", lang))
        ch = self.chat
        ch.exit_requested.connect(self._exit_dialog_mode)
        ch.send_requested.connect(self.on_chat_send)
        ch.notify.connect(self._on_chat_notify)
        ch.stop_requested.connect(self.chat_engine.stop)
        ch.approval_decided.connect(self.chat_engine.respond_approval)
        ch.tools_requested.connect(self.open_tools_dialog)
        ch.settings_requested.connect(self.open_chat_settings)
        ch.tools_toggle.toggled.connect(self._on_tools_toggled)
        ce = self.chat_engine
        ce.step_started.connect(ch.on_step)
        ce.delta.connect(ch.append_answer)
        ce.reasoning.connect(ch.append_reasoning)
        ce.tool_started.connect(ch.on_tool_started)
        ce.approval_needed.connect(ch.on_approval_needed)
        ce.tool_running.connect(ch.on_tool_running)
        ce.message.connect(ch.on_message)
        ce.notice.connect(ch.on_notice)
        ce.finished.connect(self._on_chat_finished)
        ce.failed.connect(self._on_chat_failed)
        ce.busy_changed.connect(self._on_chat_busy)
        self.mcp.changed.connect(self._update_tools_summary)
        self._update_tools_summary()
        tr = self.translator
        tr.chunk.connect(r.append_translation)
        tr.finished.connect(self._on_translated)
        tr.failed.connect(self._on_translate_failed)
        tr.busy_changed.connect(self._on_translate_busy)
        self._chat_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)   # переключить режим чата
        self._chat_shortcut.activated.connect(self._toggle_chat_mode)

    # =================================================================== окно
    def _toggle_max(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def toast(self, text: str, kind: str = "info") -> None:
        if hasattr(self, "toast_widget"):
            self.toast_widget.show_message(text, kind)


    def start_strata(self) -> None:
        self.strata.begin_monitoring()
        self.comfy.apply_settings()
        if self.config.get("strata.auto_start", True):
            self.strata.start()

    # =================================================================== модели
    def _init_models(self) -> None:
        models = self.models.all()
        if not models:
            m = self.models.create({"name": "Default", "model": "default", "system_prompt": t("model_system_ph")})
            models = [m]
        last = self.config.get("ui.last_model", "")
        self.model_id = last if self.models.get(last) else models[0].id
        self.header.tabs.set_models(models, self.model_id)
        self._load_model()

    def _load_model(self) -> None:
        m = self.models.get(self.model_id)
        if not m:
            return
        self.model_card.set_model(m)
        self.chat.set_model_name(m.name, m.id)
        self.params.set_model_defaults(m)
        self.params.streaming.setChecked(bool(self.config.get("generation.streaming", True)))
        level = self.config.get("generation.thinking_level", "high") or "high"
        if not bool(self.config.get("generation.enable_thinking", True)):
            level = "off"
        self.params.think_level.blockSignals(True)
        self.params.set_level(level)
        self.params.think_level.blockSignals(False)
        self.params.show_reasoning.setChecked(bool(self.config.get("generation.show_reasoning", False)))
        self.params.show_reasoning.setEnabled(level != "off")
        is_h3 = h3.is_h3(m)
        self.compact.h3.setVisible(is_h3)
        if is_h3:
            saved = self.config.get(f"ui.h3_length.{m.id}", None)
            self.compact.h3.set_frames(int(saved) if saved is not None else 294)
        self.refs_panel.set_vision(m.supports_vision and bool(self.config.get("options.use_references", True)))

    def _model_api_id(self) -> str:
        m = self.models.get(self.model_id)
        return m.model if m else ""

    def select_model(self, model_id: str) -> None:
        if self.engine.busy or self.translator.busy or self.chat_engine.busy or model_id == self.model_id:
            self.header.tabs.set_current(self.model_id)
            return
        self.model_id = model_id
        self.config.set("ui.last_model", model_id)
        self._load_model()

    def _add_menu(self, btn) -> None:
        menu = QMenu(self)
        menu.addAction(icons.icon("plus", theme.T["text_2"], 16), t("model_new"), self.add_model)
        menu.addAction(icons.icon("download", theme.T["text_2"], 16), t("model_import"), self.import_model)
        cur = self.models.get(self.model_id)
        if cur:
            menu.addSeparator()
            menu.addAction(icons.icon("trash", "#ff8a8d", 16), t("model_delete_named", name=cur.name), self.delete_model)
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def add_model(self) -> None:
        dlg = ModelDialog(self, {"api_base": self.config.get("strata.api_base"),
                                 "temperature": self.config.get("generation.temperature", 1.0),
                                 "max_tokens": self.config.get("generation.max_tokens", 32000)})
        if dlg.exec():
            m = self.models.create(dlg.data())
            self.model_id = m.id
            self.header.tabs.set_models(self.models.all(), m.id)
            self._load_model()
            self.toast(t("model_added", name=m.name), "success")

    def save_model(self, data: dict) -> None:
        m = self.models.get(self.model_id)
        if not m:
            return
        for k, v in data.items():
            setattr(m, k, v)
        self.models.save(m)
        self.header.tabs.set_models(self.models.all(), m.id)
        self._load_model()
        self.toast(t("model_saved"), "success")

    def delete_model(self, model_id: str | None = None) -> None:
        if self.engine.busy or self.translator.busy or self.chat_engine.busy:
            self.toast(t("mw_busy"), "warning")
            return
        m = self.models.get(model_id or self.model_id)
        if not m:
            return
        if len(self.models.all()) <= 1:
            self.toast(t("model_last"), "warning")
            return
        if confirm(self, t("model_delete"), t("model_delete_q", name=m.name), t("delete"), True):
            self.models.delete(m.id)
            if m.id == self.model_id or not self.models.get(self.model_id):
                self.model_id = self.models.all()[0].id
                self.config.set("ui.last_model", self.model_id)
            self.header.tabs.set_models(self.models.all(), self.model_id)
            self._load_model()
            self.toast(t("model_deleted"), "success")

    def export_model(self) -> None:
        f, _ = QFileDialog.getSaveFileName(self, t("model_export"), f"{self.model_id}.json", "JSON (*.json)")
        if f:
            self.models.export_to(self.model_id, Path(f))

    def import_model(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, t("model_import"), "", "JSON (*.json)")
        if not f:
            return
        try:
            m = self.models.import_from(Path(f))
        except Exception as exc:
            log.error("Импорт модели не удался: %s", exc)
            info(self, t("model_import"), t("model_import_err", detail=str(exc)))
            return
        self.model_id = m.id
        self.header.tabs.set_models(self.models.all(), m.id)
        self._load_model()
        self.toast(t("model_added", name=m.name), "success")

    # =================================================================== стили
    def _toggle_style(self, style_id: str, on: bool) -> None:
        self.styles.set_enabled(style_id, on)
        self.styles_panel.refresh()
        self.selected.set_styles(self.styles.all())

    # =================================================================== генерация
    def _make_request(self, user_request: str, current: str = "", edit: str = "", use_refs: bool = True) -> GenerationRequest:
        m = self.models.get(self.model_id)
        pv = self.params.values()
        is_h3 = h3.is_h3(m)
        prev_edits = []
        if edit:
            upto = self.current_version.version if self.current_version else 0
            prev_edits = [v.edit_request for v in self.versions if v.version <= upto and v.edit_request]
        return GenerationRequest(
            model=m, styles=self.styles.enabled(), user_request=user_request,
            references=self.refs.items() if use_refs else [], current_prompt=current, edit_request=edit,
            previous_edits=prev_edits, temperature=pv["temperature"], max_tokens=pv["max_tokens"],
            streaming=pv["streaming"], enable_thinking=pv["enable_thinking"], show_reasoning=pv["show_reasoning"],
            thinking_level=pv["thinking_level"],
            options=ContextOptions(pv["aspect_ratio"], pv["creativity"], pv["include_history"], True,
                                   h3_length=self.compact.h3.frames() if is_h3 else 0, picture_labels=is_h3))

    def on_generate(self, mode: str) -> None:
        if self.engine.busy:
            self.toast(t("mw_busy"), "warning")
            return
        if self.translator.busy or self.chat_engine.busy:
            self.toast(t("translate_busy"), "warning")
            return
        text = self.request.edit.toPlainText().strip()
        if not text:
            self.toast(t("mw_empty_request"), "warning")
            self.request.edit.setFocus()
            return
        regen = (mode == "normal" and self.gen_id is not None and text == self.session_request
                 and self.result.has_prompt())
        if mode == "reset":
            self._reset_session()
        self._pending = {"kind": "regen" if regen else "new", "request": text, "edit": t("version_regen") if regen else "",
                         "refs": [r.to_dict() for r in self.refs.items()] if mode != "no_refs" else []}
        self._start(self._make_request(text, use_refs=mode != "no_refs"), user_msg=text)

    def on_apply_edit(self, edit_text: str) -> None:
        if self.engine.busy:
            self.toast(t("mw_busy"), "warning")
            return
        if self.translator.busy or self.chat_engine.busy:
            self.toast(t("translate_busy"), "warning")
            return
        if not self.result.has_prompt() or self.gen_id is None:
            self.toast(t("edit_no_prompt"), "warning")
            return
        self._pending = {"kind": "edit", "request": self.session_request, "edit": edit_text, "refs": []}
        req = self._make_request(self.session_request, self.result.prompt, edit_text)
        self._start(req, user_msg=edit_text)

    def _start(self, req: GenerationRequest, user_msg: str) -> None:
        self._prev_text = self.result.prompt if self.result.has_prompt() else ""
        self.dialog.append(("user", user_msg))
        self.result.begin_stream()
        self._show_reasoning_now = bool(req.show_reasoning and req.enable_thinking)
        self._think_buf = ""
        self._prog = (0, 0)
        self._ans_t0 = 0.0
        self._h3_frames = req.options.h3_length
        if self._show_reasoning_now:
            self.result.begin_reasoning()
        self._idle_timer.stop()
        if not self.engine.generate(req):
            self.toast(t("mw_busy"), "warning")

    def _on_busy(self, busy: bool) -> None:
        self.request.set_busy(busy)
        self.edit_panel.set_enabled_apply(not busy)
        if busy:
            self._live_timer.start()
            self._tick_live()
        else:
            self._live_timer.stop()
            self.compact.set_live("", 0, 0)
            QTimer.singleShot(0, lambda: self._set_tps(self._last_tps))   # после _commit: итог последней генерации

    # --- живой счётчик / рассуждения / лимит токенов --------------------------------------------
    def _on_reasoning(self, chunk: str) -> None:
        self._think_buf += chunk
        if self._show_reasoning_now:
            self.result.append_reasoning(chunk)

    def _on_progress(self, r_n: int, c_n: int) -> None:
        import time as _t
        if (r_n or c_n) and not self._ans_t0:
            self._ans_t0 = _t.time()          # первый токен (мысли или ответ) — от него считаем скорость
        self._prog = (r_n, c_n)

    def _tick_live(self) -> None:
        if not self.engine.busy:
            return
        r_n, c_n = self._prog
        phase = "answer" if c_n else ("think" if r_n else "wait")
        self.compact.set_live(phase, self.engine.elapsed, c_n or r_n)
        import time as _t
        dt = _t.time() - self._ans_t0 if self._ans_t0 else 0
        self._set_tps((r_n + c_n) / dt if dt > 1 else self._last_tps, live=dt > 1)

    def _on_think_level(self, _=None) -> None:
        level = self.params.level()
        self.config.set("generation.thinking_level", level)
        self.config.set("generation.enable_thinking", level != "off")

    def _bump_tokens(self) -> None:
        n = min(self.params.tokens.value() * 2, self.params.tokens.maximum())
        self.params.tokens.setValue(n)
        self.result.set_truncated(False)
        self.toast(t("tokens_bumped", n=n), "info")

    def _meta_text(self, text: str) -> str:
        st = self.engine.last_stats or {}
        el = st.get("elapsed") or self.engine.last_elapsed
        parts = [t("chars", n=len(text))]
        if st.get("prompt") or st.get("completion"):
            parts.append(t("meta_tokens", p=st.get("prompt") or "?", c=st.get("completion") or "?"))
        tps = st.get("tps")
        if not tps and st.get("completion") and el:
            gen_time = el - (st.get("ttft") or 0)
            tps = st["completion"] / gen_time if gen_time > 0 else 0
        if tps:
            parts.append(t("meta_tps", v=f"{tps:.1f}"))
            self._last_tps = float(tps)
        if st.get("ttft") is not None:
            parts.append(t("meta_ttft", v=f"{st['ttft']:.1f}"))
        if st.get("reasoning_pieces"):
            parts.append(t("meta_reason", n=st["reasoning_pieces"]))
        parts.append(f"{el:.1f} {t('sec')}")
        return " · ".join(parts)

    def _on_bottom_tab(self, i: int) -> None:
        self.bottom_stack.setCurrentIndex(i)
        self.log_refresh.setVisible(i == 3)
        if i == 1:
            self._render_diff()
        elif i == 3:
            self._load_strata_log()

    def _render_diff(self) -> None:
        vs = sorted(self.versions, key=lambda v: v.version)
        cur = self.current_version
        if len(vs) < 2 or cur is None:
            self.diff_view.setHtml(f'<p style="color:{theme.T["text_3"]}">{t("diff_empty")}</p>')
            return
        idx = next((i for i, v in enumerate(vs) if v.id == cur.id), len(vs) - 1)
        old = vs[idx - 1] if idx > 0 else vs[0]
        new = vs[idx] if idx > 0 else vs[1]
        self.diff_view.setHtml(f"<p><b>v{old.version} → v{new.version}</b></p>" + diff_html(old.prompt, new.prompt))

    def _load_strata_log(self) -> None:
        try:
            lines = paths.STRATA_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
        except OSError:
            lines = []
        self.log_view.setPlainText("\n".join(lines))
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_left_tab(self, i: int) -> None:
        self.left_group.button(i).setChecked(True)
        self.left_stack.setCurrentIndex(i)

    def eventFilter(self, obj, ev):  # noqa: N802
        from PySide6.QtCore import QEvent
        if ev.type() in (QEvent.LayoutRequest, QEvent.Resize) and hasattr(self, "_fit_timer"):
            self._fit_timer.start()
        return super().eventFilter(obj, ev)

    def _fit_center(self) -> None:
        """Единая раскладка центра: референсы/стили/параметры занимают ровно свою высоту (без мёртвого зазора),
        поле «Опишите, что нужно создать» забирает всё свободное место. Системный промт — наоборот, растёт он."""
        if not hasattr(self, "_dock"):
            return
        cl, i = self._center_cl, self.center_stack.currentIndex()
        if i == 1:                                   # системный промт — на всю высоту
            self.center_stack.setMaximumHeight(16777215)
            cl.setStretchFactor(self.center_stack, 1)
            cl.setStretchFactor(self._dock, 0)
            return
        page = self.center_stack.currentWidget()
        need = page.body.sizeHint().height() + 4
        # не даём верху съесть всё окно: остаток >= минимум для поля запроса; иначе — прокрутка внутри вкладки
        room = max(120, self.centralWidget().height() - 380) if self.centralWidget() else need
        self.center_stack.setMaximumHeight(min(need, room))
        cl.setStretchFactor(self.center_stack, 0)
        cl.setStretchFactor(self._dock, 1)

    def resizeEvent(self, e):  # noqa: N802 — дубль ниже вызывает super; держим подгонку центра
        super().resizeEvent(e)
        if hasattr(self, "_fit_timer"):
            self._fit_timer.start()
        if hasattr(self, "toast_widget") and self.toast_widget.isVisible():
            self.toast_widget.reposition()

    def _set_center_tab(self, i: int, save: bool = True) -> None:
        i = i if 0 <= i < self.center_stack.count() else 0
        self.center_tabs[i].setChecked(True)
        self.center_stack.setCurrentIndex(i)
        if hasattr(self, "_dock"):
            self._fit_center()
        if save:
            self.config.set("ui.center_tab", i)

    def open_styles_drawer(self) -> None:      # совместимость: стили теперь во вкладке слева
        self.left_slot.set_open(True)
        self._set_left_tab(1)

    def stop_generation_only(self) -> None:
        self._after_stop = False
        self.engine.stop()

    def on_stop(self) -> None:
        """STOP: прервать генерацию (если идёт) и выполнить выбранное действие."""
        if self.chat_engine.busy:
            self._after_stop_tr = True
            self.chat_engine.stop()
        elif self.translator.busy:
            self._after_stop_tr = True
            self.translator.stop()
        elif self.engine.busy:
            self._after_stop = True
            self.engine.stop()
        else:
            self._run_after_stop(announce=True)

    def _run_after_stop(self, announce: bool = False) -> None:
        mode = self.strata_panel.mode()
        if mode == "unload":
            self.strata.unload(self._model_api_id(), quiet=False)
        elif mode == "shutdown":
            self.strata.stop_process()
        elif announce:
            self.toast(t("gen_stopped"), "info")

    def _on_finished(self, text: str, stopped: bool) -> None:
        pend, self._pending = self._pending, None
        after, self._after_stop = self._after_stop, False
        text = text.strip()
        self.thinking_view.setPlainText(self._think_buf)
        if stopped:
            self.dialog = self.dialog[:-1] if self.dialog and self.dialog[-1][0] == "user" else self.dialog
            self._restore_previous(text if not self._prev_text else "")
            self.toast(t("gen_stopped"), "info")
        elif not text or pend is None:
            self.dialog = self.dialog[:-1] if self.dialog else self.dialog
            self._restore_previous()
            self.toast(t("gen_empty"), "warning")
        else:
            self._commit(pend, text)
        if after:
            self._run_after_stop()
        elif self.config.get("strata.auto_unload", False) and not stopped:
            self._idle_timer.start(int(self.config.get("strata.auto_unload_delay", 300)) * 1000)

    def _on_failed(self, message: str) -> None:
        self._pending = None
        after, self._after_stop = self._after_stop, False
        if self.dialog and self.dialog[-1][0] == "user":
            self.dialog.pop()
        self._restore_previous()
        self.toast(message, "error")
        if after:
            self._run_after_stop()

    def _restore_previous(self, fallback: str = "") -> None:
        if self._prev_text:
            self.result.set_prompt(self._prev_text)
        elif fallback:
            self.result.set_prompt(fallback)
        else:
            self.result.show_error("")
            self.result.set_prompt("")
        self._render_session()

    def _commit(self, pend: dict, text: str) -> None:
        m = self.models.get(self.model_id)
        style_ids = [s.id for s in self.styles.enabled()]
        try:
            if pend["kind"] == "new":
                self.gen_id, v = self.history.create_generation(m.id, m.name, pend["request"], style_ids, pend["refs"], text)
                self.session_request = pend["request"]
                self.versions = [v]
            else:
                v = self.history.add_version(self.gen_id, text, pend["edit"], style_ids, m.name)
                self.versions.append(v)
            self.current_version = v
        except Exception as exc:
            log.exception("Не удалось сохранить историю")
            self.toast(t("err_other", detail=str(exc)), "error")
            self.result.set_prompt(text)
            return
        self.dialog.append(("assistant", text))
        self.result.end_stream(text, self._meta_text(text))
        st = self.engine.last_stats or {}
        if st.get("finish") == "length":
            self.result.set_truncated(True)
            self.toast(t("truncated_badge"), "warning")
        if self._h3_frames and m and h3.is_h3(m) and not h3.mentions_length(text, self._h3_frames):
            self.toast(t("h3_len_missing", n=self._h3_frames, s=f"{h3.seconds(self._h3_frames):.2f}"), "warning")
        if pend["kind"] == "edit":
            self.edit_panel.clear()
        self._render_session()

    # =================================================================== перевод
    def on_translate(self, text: str, language: str) -> None:
        if self.engine.busy or self.chat_engine.busy:
            self.toast(t("translate_busy"), "warning")
            return
        if not text.strip():
            self.toast(t("translate_no_prompt"), "warning")
            return
        m = self.models.get(self.model_id)
        if not m:
            return
        self._idle_timer.stop()
        self.result.begin_translation()
        self.translator.translate(text, language, m, self.params.values()["max_tokens"])

    def _on_translate_busy(self, busy: bool) -> None:
        self.result.set_translate_busy(busy)
        self.request.split.main.setEnabled(not busy)
        self.edit_panel.set_enabled_apply(not busy)

    def _on_translated(self, text: str, stopped: bool) -> None:
        after, self._after_stop_tr = self._after_stop_tr, False
        self.result.end_translation(text, stopped)
        if stopped:
            self.toast(t("translate_stopped"), "info")
        if after:
            self._run_after_stop()
        elif self.config.get("strata.auto_unload", False) and not stopped:
            self._idle_timer.start(int(self.config.get("strata.auto_unload_delay", 300)) * 1000)

    def _on_translate_failed(self, message: str) -> None:
        after, self._after_stop_tr = self._after_stop_tr, False
        self.result.fail_translation()
        self.toast(message, "error")
        if after:
            self._run_after_stop()

    # =================================================================== версии / сессия
    def _render_session(self) -> None:
        self.history_panel.set_versions(self.versions, self.current_version.id if self.current_version else None)
        self.result.set_dialog(self.dialog)
        if self.bottom_stack.currentIndex() == 1:
            self._render_diff()

    def _reset_session(self) -> None:
        self.gen_id, self.versions, self.current_version = None, [], None
        self.session_request, self.dialog = "", []
        self.result.set_prompt("")
        self._render_session()

    def open_version(self, v: Version) -> None:
        if self.engine.busy:
            return
        self.current_version = v
        self.result.set_prompt(v.prompt)
        self._render_session()

    def compare_versions(self) -> None:
        if len(self.versions) >= 2:
            CompareDialog(self, self.versions).exec()

    def on_manual_edit(self, text: str) -> None:
        if self.gen_id is None or self.engine.busy:
            return
        m = self.models.get(self.model_id)
        v = self.history.add_version(self.gen_id, text, t("manual_edit"), [s.id for s in self.styles.enabled()], m.name)
        self.versions.append(v)
        self.current_version = v
        self.result.set_prompt(text)
        self._render_session()
        self.toast(t("manual_edit_saved"), "success")

    def open_generations(self) -> None:
        if self.engine.busy:
            return
        dlg = GenerationsDialog(self, self.history)
        if not dlg.exec() or dlg.selected_id is None:
            return
        g = self.history.get_generation(dlg.selected_id)
        if not g:
            return
        self.gen_id, self.session_request = g.id, g.user_request
        self.versions = self.history.versions(g.id)
        self.current_version = self.versions[-1] if self.versions else None
        self.request.edit.setPlainText(g.user_request)
        if self.models.get(g.model_id):
            self.select_model(g.model_id)
            self.header.tabs.set_current(g.model_id)
        for s in self.styles.all():
            self.styles.set_enabled(s.id, s.id in g.selected_styles)
        self.styles_panel.refresh()
        self.selected.set_styles(self.styles.all())
        missing = self.refs.set_items(g.references)
        self.refs_panel.rebuild()
        if missing:
            self.toast(t("restore_missing", names=", ".join(missing)), "warning")
        self.dialog = [("user", g.user_request)] + ([("assistant", self.current_version.prompt)] if self.current_version else [])
        self.result.set_prompt(self.current_version.prompt if self.current_version else g.final_prompt)
        self._render_session()

    # =================================================================== Strata / статус
    def _on_state(self, state: StrataState, detail: str) -> None:
        self.header.unloaded = self.strata.model_unloaded
        self.header.set_state(state, detail)
        self.strata_panel.set_state(state)
        color = {StrataState.READY: theme.T["success"], StrataState.GENERATING: "#5b9bff",
                 StrataState.ERROR: "#ff5a5f"}.get(state, theme.T["text_3"])
        self.sb_dot.set_color(color, state in (StrataState.READY, StrataState.GENERATING))
        self._refresh_statusbar()

    def _refresh_statusbar(self) -> None:
        info_ = self.strata.info
        self.sb_api.setText(f"{t('sb_api')}: {self.strata.api().api_base}")
        self.sb_model.setText(f"{t('sb_model')}: {info_.get('model') or t('sb_none')}")
        ctx = info_.get("context")
        self.sb_ctx.setText(f"{t('sb_context')}: {ctx if ctx else t('sb_none')}")
        self._fill_load()

    def _fill_load(self) -> None:
        ram, vram = getattr(self, "_last_ram", None), getattr(self, "_last_vram", None)
        parts = []
        if ram:
            parts.append(f"RAM {ram[0]:.1f} / {ram[1]:.0f} GB")
        if vram:
            parts.append(f"VRAM {vram[0]:.1f} / {vram[1]:.0f} GB")
        self.sb_load.setText(f"{t('sb_load')}: " + (" · ".join(parts) if parts else t("sb_none")))

    def _poll_metrics(self) -> None:
        threading.Thread(target=lambda: self._metrics.emit(system_metrics.ram_usage(), system_metrics.vram_usage()),
                         daemon=True).start()

    def _on_metrics(self, ram, vram) -> None:
        self._last_ram, self._last_vram = ram, vram
        self.header.set_metrics(ram, vram)
        self._fill_load()

    def _idle_unload(self) -> None:
        if (not self.engine.busy and not self.translator.busy and not self.chat_engine.busy
                and self.strata.state == StrataState.READY
                and not self.strata.model_unloaded):
            self.strata.unload(self._model_api_id(), quiet=True)

    # =================================================================== режим диалога
    CHAT_SYSTEM = (
        "You are a capable, honest local AI assistant running inside PromptForge on the user's computer. "
        "Help with any task: questions, writing, analysis, coding, research, planning. When the user asks for prompts "
        "for image or video generation models, craft them carefully. Be concise and practical; think step by step "
        "on hard problems. Reply in the same language the user writes in. Use Markdown for lists, tables and code blocks.")
    TOOLS_HINT = (
        "You can use tools. Use them whenever they make the answer more accurate: search the web for anything recent "
        "or uncertain, read pages, calculate exactly, run code to check results, read or write files in the workspace. "
        "Never guess facts you can look up. After using tools, give a clear final answer; do not narrate every step. "
        "If a tool fails, read the error, fix the arguments and retry once before giving up.")

    def set_dialog_mode(self, on: bool) -> None:
        if on and (self.engine.busy or self.translator.busy):
            self.toast(t("mw_busy"), "warning")
            self.header.set_chat_mode(False)
            return
        self.workspace.setCurrentIndex(1 if on else 0)
        self.header.set_chat_mode(on)
        if on:
            self.mcp.start_enabled()
            self.chat.focus_input()
        else:
            self.request.edit.setFocus()

    def _exit_dialog_mode(self) -> None:
        self.set_dialog_mode(False)

    def _toggle_chat_mode(self) -> None:
        self.set_dialog_mode(self.workspace.currentIndex() != 1)

    def _chat_system_prompt(self, with_tools: bool) -> str:
        from datetime import datetime

        from app.core.prompt_file import resolve_system_text

        inline = self.config.get("chat.system_prompt", "") or ""
        path = self.config.get("chat.system_prompt_file", "") or ""
        text, warn = resolve_system_text(inline, path)
        if warn:
            self.toast(t(warn), "warning")
        sys_text = text or self.CHAT_SYSTEM
        sys_text += f"\n\nCurrent date and time: {datetime.now().strftime('%Y-%m-%d %H:%M')} (local)."
        if with_tools:
            sys_text += "\n\n" + self.TOOLS_HINT
        if self.chat.context_enabled():
            m = self.models.get(self.model_id)
            req = self.request.edit.toPlainText().strip()
            prompt = self.result.prompt.strip() if self.result.has_prompt() else ""
            parts = []
            if m:
                parts.append(f"Target model for the prompts: {m.name}")
            if req:
                parts.append(f"User's current scene request:\n{req}")
            if prompt:
                parts.append(f"Latest generated prompt:\n{prompt}")
            if parts:
                sys_text += "\n\nCurrent project context (use it only when relevant):\n" + "\n\n".join(parts)
        return sys_text

    # --- инструменты / MCP ---------------------------------------------------------------------------------
    def _update_tools_summary(self) -> None:
        n = len(self.mcp.active_defs())
        ready, total = self.mcp.ready_count(), len(self.mcp.servers)
        self.chat.set_tools_summary(t("chat_tools_n", n=n, ready=ready, total=total), n > 0)

    def _on_tools_toggled(self, on: bool) -> None:
        self.config.set("chat.tools_enabled", bool(on))

    def open_tools_dialog(self) -> None:
        ToolsDialog(self, self.mcp).exec()
        self._update_tools_summary()

    def open_chat_settings(self) -> None:
        if ChatSettingsDialog(self, self.config, self.CHAT_SYSTEM).exec():
            self.toast(t("saved"), "success")

    def on_chat_send(self, history: list) -> None:
        m = self.models.get(self.model_id)
        if self.engine.busy or self.translator.busy or self.chat_engine.busy or not m:
            self.toast(t("mw_busy"), "warning")
            self.chat.fail_answer("")      # вернуть вопрос в поле ввода
            return
        pv = self.params.values()
        use_tools = self.chat.tools_enabled()
        tools = self.mcp.active_defs() if use_tools else []
        messages = [{"role": "system", "content": self._chat_system_prompt(bool(tools))}] + self._chat_api_history(history, m)
        approval = "auto" if self.config.get("chat.approval", "ask") == "auto" else "ask"
        params = RunParams(messages=messages, model=m, tools=tools, temperature=pv["temperature"],
                           max_tokens=max(256, int(pv["max_tokens"])), enable_thinking=pv["enable_thinking"],
                           approval=approval, max_steps=int(self.config.get("chat.max_steps", 12)))
        self._idle_timer.stop()
        self.chat.begin_answer()
        if not self.chat_engine.send(params):
            self.chat.fail_answer("")

    def _on_chat_notify(self, text: str, level: str) -> None:
        self.toast(text, level)

    def _chat_api_history(self, history: list, model) -> list:
        """История чата -> сообщения API: изображения уходят как image_url, если модель умеет vision;
        вызовы инструментов и их результаты передаются как есть."""
        from pathlib import Path

        from app.core.reference_manager import Reference, ReferenceManager

        out: list = []
        warned = False
        for h in sanitize_history(history):
            role = h["role"]
            if role == "tool":
                out.append({"role": "tool", "tool_call_id": h.get("tool_call_id", ""), "name": h.get("name", ""),
                            "content": h.get("content", "")})
                continue
            if role == "assistant":
                msg = {"role": "assistant", "content": h.get("content", "")}
                if h.get("tool_calls"):
                    msg["tool_calls"] = h["tool_calls"]
                out.append(msg)
                continue
            content, imgs = h["content"], [p for p in (h.get("images") or []) if Path(p).is_file()]
            if role == "user" and imgs:
                if model.supports_vision:
                    parts = [{"type": "text", "text": content}]
                    for pth in imgs:
                        parts.append({"type": "image_url", "image_url": {
                            "url": ReferenceManager.image_data_url(Reference(pth, "image", Path(pth).name, ""))}})
                    out.append({"role": "user", "content": parts})
                    continue
                content += "\n\n[Note: the attached images could not be sent - the model has no vision support.]"
                if not warned:
                    self.toast(t("warn_no_vision"), "warning")
                    warned = True
            out.append({"role": role, "content": content})
        return out

    def _on_chat_busy(self, busy: bool) -> None:
        self.chat.set_busy(busy)
        self.edit_panel.set_enabled_apply(not busy)

    def _on_chat_finished(self, stopped: bool) -> None:
        after, self._after_stop_tr = self._after_stop_tr, False
        self.chat.end_answer(stopped)
        self.config.set("chat.last_session", self.chat.session_id())
        if after:
            self._run_after_stop()
        elif self.config.get("strata.auto_unload", False) and not stopped:
            self._idle_timer.start(int(self.config.get("strata.auto_unload_delay", 300)) * 1000)

    def _on_chat_failed(self, message: str) -> None:
        after, self._after_stop_tr = self._after_stop_tr, False
        self.chat.fail_answer(message)
        self.config.set("chat.last_session", self.chat.session_id())
        self.toast(message, "error")
        if after:
            self._run_after_stop()

    # =================================================================== ComfyUI
    def _on_comfy_notify(self, level: str, text: str) -> None:
        self.toast(text, level)

    def _on_comfy_busy(self, busy: bool) -> None:
        """У ComfyUI началась генерация -> выгружаем модель Strata, чтобы освободить память под Comfy."""
        if not busy or not self.config.get("comfy.unload_strata_on_run", True):
            return
        if self.engine.busy or self.translator.busy or self.chat_engine.busy:
            # не обрываем идущую генерацию LLM; если PromptForge только ждёт освобождения ComfyUI (LOADING) — молчим
            if self.strata.state == StrataState.GENERATING:
                self.toast(t("comfy_llm_busy"), "warning")
            return
        if self.strata.state in (StrataState.STOPPED, StrataState.ERROR, StrataState.STARTING, StrataState.STOPPING,
                                 StrataState.UNLOADING) or self.strata.model_unloaded:
            return
        log.info("ComfyUI started generating -> unloading Strata model")
        self.strata.unload(self._model_api_id(), quiet=True)

    # =================================================================== настройки
    def open_settings(self) -> None:
        before = (self.config.get("ui.language"), self.config.get("ui.native_titlebar"))
        prev_theme = self.config.get("ui.theme", "dark")
        if SettingsDialog(self, self.config).exec():
            self._load_model()
            self._refresh_statusbar()
            new_theme = self.config.get("ui.theme", "dark")
            if new_theme != prev_theme:
                self.apply_theme(new_theme)
            if before != (self.config.get("ui.language"), self.config.get("ui.native_titlebar")):
                self.toast(t("set_restart_note"), "info")
            self.strata.begin_monitoring()
            self.comfy.apply_settings()
            self.strata_panel.set_autostart(bool(self.config.get("strata.auto_start", True)))

    def apply_theme(self, name: str | None = None) -> None:
        from app.ui.widgets import Button, LogoTile

        key = theme.apply_theme(name or self.config.get("ui.theme", "dark"), QApplication.instance())
        self.config.set("ui.theme", key)
        self.header.refresh_theme()
        for logo in self.findChildren(LogoTile):
            logo.refresh_theme()
        for btn in self.findChildren(Button):
            btn._apply_icon()
        self.root.update()
        self.update()

    # =================================================================== выход
    def closeEvent(self, e):
        if self.engine.busy:
            if not confirm(self, t("close_title"), t("close_busy_q"), t("ok"), True):
                e.ignore()
                return
            self.engine.stop()
            self.engine.wait_idle(5000)
        if self.translator.busy:
            self.translator.stop()
            self.translator.wait_idle(5000)
        if self.chat_engine.busy:
            self.chat_engine.stop()
            self.chat_engine.wait_idle(5000)
        self._metric_timer.stop()
        self.config.set("chat.last_session", self.chat.session_id())
        self.mcp.shutdown()
        self.comfy.shutdown()
        self.strata.shutdown_monitoring()
        self.config.set("ui.last_model", self.model_id)
        if self.config.get("strata.auto_shutdown", False) and self.strata.process.started_by_us:
            self.strata.stop_process(wait=True)
        log.info("PromptForge closing")
        e.accept()
