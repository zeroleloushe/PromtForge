"""Диалоги режима чата: «Инструменты и MCP» и «Настройки чата»."""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget)

from app.mcp.manager import McpManager, Server, ServerState, estimate_tokens
from app.mcp.presets import PRESETS
from app.ui.dialogs import BaseDialog, _open_path, _row, _toggle_row, confirm
from app.ui.system_prompt_box import SystemPromptBox
from app.ui.theme import T
from app.ui.widgets import Button, ElidedLabel, Toggle, VScroll, clear_layout, icon_button, label
from app.utils.config import Config
from app.utils.i18n import t

_STATE_COLOR = {ServerState.OFF: "text_3", ServerState.STARTING: "warning", ServerState.READY: "success",
                ServerState.ERROR: "danger"}
_MONO = "font-family:Consolas,'Cascadia Mono',monospace;font-size:12px;"


class ServerEditorDialog(BaseDialog):
    """Имя и JSON-описание одного сервера (формат как в Claude Desktop / Cursor)."""

    def __init__(self, parent, name: str = "", spec: dict | None = None):
        super().__init__(parent, t("srv_edit_title") if name else t("srv_add_title"), 620)
        self.result_name = name
        self.result_spec: dict = {}
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("my-server")
        self.body.addWidget(_row(t("srv_name"), self.name_edit))
        self.json_edit = QPlainTextEdit()
        self.json_edit.setStyleSheet(_MONO)
        self.json_edit.setMinimumHeight(220)
        self.json_edit.setPlainText(json.dumps(spec if spec is not None else {"command": "npx", "args": ["-y", "package-name"]},
                                               ensure_ascii=False, indent=2))
        self.body.addWidget(_row(t("srv_json"), self.json_edit, t("srv_json_hint")))
        self.err = label("", "Muted", wrap=True)
        self.err.setStyleSheet(f"color:{T['danger']};")
        self.body.addWidget(self.err)
        ok = self.add_buttons(t("save"), t("cancel"))
        ok.clicked.disconnect()
        ok.clicked.connect(self._save)

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.err.setText(t("srv_need_name"))
            return
        try:
            spec = json.loads(self.json_edit.toPlainText())
            if not isinstance(spec, dict):
                raise ValueError(t("srv_need_object"))
            if "mcpServers" in spec and isinstance(spec["mcpServers"], dict) and len(spec["mcpServers"]) == 1:
                name, spec = next(iter(spec["mcpServers"].items()))      # вставили целый блок из README сервера
            if not spec.get("command") and not spec.get("url"):
                raise ValueError(t("srv_need_command"))
        except (ValueError, json.JSONDecodeError) as exc:
            self.err.setText(str(exc))
            return
        self.result_name, self.result_spec = name, spec
        self.accept()


class ToolsDialog(BaseDialog):
    """Серверы MCP и список инструментов, доступных модели."""

    def __init__(self, parent, mcp: McpManager):
        super().__init__(parent, t("tools_title"), 780)
        self.mcp = mcp
        self.setMinimumHeight(640)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._rebuild)
        self.body.addWidget(label(t("tools_intro"), "Muted", wrap=True))

        head = QHBoxLayout()
        head.addWidget(label(t("tools_servers"), "H2"))
        head.addStretch(1)
        self.add_btn = Button(t("tools_add"), "plus", "primary", "sm")
        self.add_btn.clicked.connect(self._add_menu)
        head.addWidget(self.add_btn)
        raw = Button(t("tools_json"), "pencil", "secondary", "sm")
        raw.clicked.connect(self._edit_all)
        head.addWidget(raw)
        self.body.addLayout(head)

        self.servers_scroll = VScroll()
        self.servers_scroll.setMinimumHeight(210)
        self.servers_scroll.lay.setSpacing(8)
        self.servers_scroll.lay.setContentsMargins(0, 0, 6, 0)
        self.body.addWidget(self.servers_scroll, 1)

        head2 = QHBoxLayout()
        head2.addWidget(label(t("tools_tools"), "H2"))
        head2.addStretch(1)
        self.tokens_lb = label("", "Faint")
        head2.addWidget(self.tokens_lb)
        self.body.addLayout(head2)
        self.tools_scroll = VScroll()
        self.tools_scroll.setMinimumHeight(180)
        self.tools_scroll.lay.setSpacing(4)
        self.tools_scroll.lay.setContentsMargins(0, 0, 6, 0)
        self.body.addWidget(self.tools_scroll, 1)

        self.add_buttons(t("done"), None)
        mcp.changed.connect(self._schedule)
        self.finished.connect(lambda _=0: self._disconnect())
        mcp.start_enabled()
        self._rebuild()

    def _disconnect(self) -> None:
        try:
            self.mcp.changed.disconnect(self._schedule)
        except (RuntimeError, TypeError):
            pass

    def _schedule(self) -> None:
        self._timer.start()

    # --- серверы -------------------------------------------------------------------------------------
    def _rebuild(self) -> None:
        clear_layout(self.servers_scroll.lay)
        servers = list(self.mcp.servers.values())
        if not servers:
            self.servers_scroll.lay.addWidget(label(t("tools_no_servers"), "Faint", wrap=True))
        for s in servers:
            self.servers_scroll.lay.addWidget(self._server_row(s))
        self.servers_scroll.lay.addStretch(1)
        self._rebuild_tools()

    def _server_row(self, s: Server) -> QWidget:
        card = QFrame()
        card.setObjectName("Inset")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 8, 8)
        v.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)
        tg = Toggle(s.enabled)
        tg.toggled.connect(lambda on, n=s.name: self.mcp.set_enabled(n, on))
        row.addWidget(tg)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(s.name, ""))
        if s.description:
            d = ElidedLabel(s.description, "Faint")
            d.setToolTip(s.description)
            col.addWidget(d)
        row.addLayout(col, 1)
        state_text = {ServerState.OFF: t("srv_off"), ServerState.STARTING: t("srv_starting"),
                      ServerState.READY: t("srv_ready", n=len(s.tools)), ServerState.ERROR: t("srv_error")}[s.state]
        st = QLabel(f"● {state_text}")
        st.setStyleSheet(f"color:{T[_STATE_COLOR[s.state]]};font-size:12px;")
        row.addWidget(st)
        for icon, tip, fn in (("refresh", t("srv_restart"), lambda _=False, n=s.name: self.mcp.restart(n)),
                              ("pencil", t("srv_edit"), lambda _=False, sv=s: self._edit(sv)),
                              ("trash", t("srv_delete"), lambda _=False, n=s.name: self._delete(n))):
            b = icon_button(icon, tip, 28, 14)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)
        if s.state == ServerState.ERROR and s.error:
            e = QLabel(s.error)
            e.setWordWrap(True)
            e.setTextInteractionFlags(Qt.TextSelectableByMouse)
            e.setStyleSheet(f"color:{T['danger']};font-size:12px;")
            v.addWidget(e)
        return card

    def _add_menu(self) -> None:
        menu = QMenu(self)
        have = set(self.mcp.servers)
        for p in PRESETS:
            a = menu.addAction(("✓ " if p["id"] in have else "") + p["title"])
            a.setToolTip(p["desc"])
            a.setEnabled(p["id"] not in have)
            a.triggered.connect(lambda _=False, pr=p: self._add_preset(pr))
        menu.addSeparator()
        menu.addAction(t("tools_custom")).triggered.connect(self._add_custom)
        menu.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft()))

    def _add_preset(self, p: dict) -> None:
        spec = copy.deepcopy(p["spec"])
        spec["description"] = p["desc"]
        spec["disabled"] = False
        self.mcp.upsert(p["id"], spec)

    def _add_custom(self) -> None:
        d = ServerEditorDialog(self)
        if d.exec() == QDialog.Accepted:
            self.mcp.upsert(d.result_name, d.result_spec)

    def _edit(self, s: Server) -> None:
        d = ServerEditorDialog(self, s.name, s.spec)
        if d.exec() == QDialog.Accepted:
            self.mcp.upsert(d.result_name, d.result_spec, old_name=s.name)

    def _delete(self, name: str) -> None:
        if confirm(self, t("srv_delete"), t("srv_delete_q", name=name), t("delete"), True):
            self.mcp.remove(name)

    def _edit_all(self) -> None:
        d = BaseDialog(self, t("tools_json"), 680)
        ed = QPlainTextEdit()
        ed.setStyleSheet(_MONO)
        ed.setMinimumHeight(360)
        ed.setPlainText(self.mcp.export_text())
        d.body.addWidget(ed)
        err = label("", "Muted", wrap=True)
        err.setStyleSheet(f"color:{T['danger']};")
        d.body.addWidget(err)
        ok = d.add_buttons(t("save"), t("cancel"))
        ok.clicked.disconnect()

        def save() -> None:
            try:
                self.mcp.replace_all_from_text(ed.toPlainText())
            except (ValueError, json.JSONDecodeError) as exc:
                err.setText(str(exc))
                return
            d.accept()

        ok.clicked.connect(save)
        d.exec()

    # --- инструменты -----------------------------------------------------------------------------------------
    def _rebuild_tools(self) -> None:
        lay = self.tools_scroll.lay
        clear_layout(lay)
        defs = self.mcp.all_defs()
        off = self.mcp.disabled_tools()
        groups: dict[str, list] = {}
        for d in defs:
            groups.setdefault(d.server, []).append(d)
        for server, items in groups.items():
            title = t("tools_builtin") if server == "builtin" else server
            lay.addWidget(label(title, "Faint"))
            for d in items:
                row = QHBoxLayout()
                row.setSpacing(8)
                tg = Toggle(d.name not in off)
                tg.toggled.connect(lambda on, n=d.name: self.mcp.set_tool_disabled(n, not on))
                row.addWidget(tg)
                nm = QLabel(d.title or d.name)
                nm.setMinimumWidth(150)
                row.addWidget(nm)
                ds = ElidedLabel(d.description.replace("\n", " "), "Faint")
                ds.setToolTip(d.description[:600])
                row.addWidget(ds, 1)
                if d.risk != "safe" and not d.trusted:
                    badge = QLabel(t("tools_ask") if d.risk != "exec" else t("tools_ask_exec"))
                    badge.setStyleSheet(f"color:{T['warning']};font-size:11px;")
                    row.addWidget(badge)
                w = QWidget()
                w.setLayout(row)
                row.setContentsMargins(0, 0, 0, 0)
                lay.addWidget(w)
        lay.addStretch(1)
        active = [d for d in defs if d.name not in off]
        self.tokens_lb.setText(t("tools_tokens", n=estimate_tokens(active), k=len(active)))


class ChatSettingsDialog(BaseDialog):
    """Системный промт, подтверждения, лимиты агента, рабочая папка, поиск."""

    def __init__(self, parent, config: Config, default_system: str):
        super().__init__(parent, t("chat_set_title"), 640)
        self.config = config
        self.default_system = default_system
        c = config
        self.system = SystemPromptBox(default_system, min_height=110, max_height=160, padded=False)
        self.system.set_prompt(c.get("chat.system_prompt", "") or "", c.get("chat.system_prompt_file", "") or "")
        self.body.addWidget(_row(t("cs_system"), self.system, t("cs_system_hint")))

        self.approval = QComboBox()
        self.approval.addItem(t("cs_approval_ask"), "ask")
        self.approval.addItem(t("cs_approval_auto"), "auto")
        self.approval.setCurrentIndex(0 if c.get("chat.approval", "ask") == "ask" else 1)
        self.steps = QSpinBox()
        self.steps.setRange(1, 40)
        self.steps.setValue(int(c.get("chat.max_steps", 12)))
        line = QHBoxLayout()
        line.setSpacing(12)
        line.addWidget(_row(t("cs_approval"), self.approval), 2)
        line.addWidget(_row(t("cs_steps"), self.steps, t("cs_steps_hint")), 1)
        wrap = QWidget()
        wrap.setLayout(line)
        line.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(wrap)

        self.workspace = QLineEdit(c.get("chat.workspace", "") or "")
        self.workspace.setPlaceholderText(t("cs_workspace_default"))
        browse = Button(t("browse"), "folder", "secondary")
        browse.clicked.connect(self._browse)
        opn = Button(t("open_folder"), None, "secondary")
        opn.clicked.connect(self._open_ws)
        wl = QHBoxLayout()
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(self.workspace, 1)
        wl.addWidget(browse)
        wl.addWidget(opn)
        wbox = QWidget()
        wbox.setLayout(wl)
        self.body.addWidget(_row(t("cs_workspace"), wbox, t("cs_workspace_hint")))

        self.provider = QComboBox()
        for key, name in (("duckduckgo", "DuckDuckGo"), ("searxng", "SearXNG"), ("brave", "Brave Search API")):
            self.provider.addItem(name, key)
        idx = self.provider.findData(c.get("chat.search_provider", "duckduckgo"))
        self.provider.setCurrentIndex(max(0, idx))
        self.searx = QLineEdit(c.get("chat.searxng_url", "") or "")
        self.searx.setPlaceholderText("http://127.0.0.1:8888")
        self.brave = QLineEdit(c.get("chat.brave_key", "") or "")
        self.brave.setEchoMode(QLineEdit.Password)
        self.brave.setPlaceholderText("BSA…")
        self.body.addWidget(_row(t("cs_search"), self.provider, t("cs_search_hint")))
        sl = QHBoxLayout()
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(12)
        sl.addWidget(_row("SearXNG URL", self.searx), 1)
        sl.addWidget(_row("Brave API key", self.brave), 1)
        sbox = QWidget()
        sbox.setLayout(sl)
        self.body.addWidget(sbox)

        self.private = Toggle(bool(c.get("chat.allow_private_net", False)))
        self.body.addWidget(_toggle_row(t("cs_private"), self.private, t("cs_private_hint")))
        self.reasoning = Toggle(bool(c.get("chat.show_reasoning", False)))
        self.body.addWidget(_toggle_row(t("cs_reasoning"), self.reasoning, t("cs_reasoning_hint")))

        ok = self.add_buttons(t("save"), t("cancel"))
        ok.clicked.disconnect()
        ok.clicked.connect(self._save)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, t("cs_workspace"), self.workspace.text())
        if d:
            self.workspace.setText(d)

    def _open_ws(self) -> None:
        from app.utils import paths
        ws = self.workspace.text().strip()
        p = paths.resolve(ws) if ws else paths.ROOT / "workspace"
        p.mkdir(parents=True, exist_ok=True)
        _open_path(p)

    def _save(self) -> None:
        c = self.config
        text, path = self.system.value()
        c.set("chat.system_prompt", text.strip())
        c.set("chat.system_prompt_file", path)
        c.set("chat.approval", self.approval.currentData())
        c.set("chat.max_steps", int(self.steps.value()))
        c.set("chat.workspace", self.workspace.text().strip())
        c.set("chat.search_provider", self.provider.currentData())
        c.set("chat.searxng_url", self.searx.text().strip())
        c.set("chat.brave_key", self.brave.text().strip())
        c.set("chat.allow_private_net", self.private.isChecked())
        c.set("chat.show_reasoning", self.reasoning.isChecked())
        self.accept()
