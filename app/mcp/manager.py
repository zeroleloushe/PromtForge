"""McpManager: конфигурация MCP-серверов, подключение в фоне, реестр инструментов, вызовы.

Файл config/mcp_servers.json совместим с Claude Desktop/Cursor ({"mcpServers": {...}}), поэтому конфиги из README
любых MCP-серверов можно вставлять как есть. Служебные поля: disabled, trust, timeout, description.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from app.mcp.builtin import BUILTIN, BuiltinTools, ToolContext, ToolDef, ToolError
from app.mcp.client import McpClient, McpError, McpTool
from app.mcp.presets import NEEDS_HINT, RUNTIME_OF, default_servers
from app.utils import paths
from app.utils.config import Config
from app.utils.log import get_logger

log = get_logger("mcp")

MAX_TOOL_CHARS = 14000


class ServerState(str, Enum):
    OFF = "off"
    STARTING = "starting"
    READY = "ready"
    ERROR = "error"


@dataclass
class Server:
    name: str
    spec: dict
    state: ServerState = ServerState.OFF
    error: str = ""
    tools: list[McpTool] = field(default_factory=list)
    client: McpClient | None = None
    info: str = ""

    @property
    def enabled(self) -> bool:
        return not self.spec.get("disabled", False)

    @property
    def trusted(self) -> bool:
        return bool(self.spec.get("trust", False))

    @property
    def description(self) -> str:
        return str(self.spec.get("description", ""))

    @property
    def transport(self) -> str:
        return McpClient.transport_of(self.spec)


def safe_name(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s)


def estimate_tokens(defs: list[ToolDef]) -> int:
    """Грубая оценка: сколько токенов контекста занимают описания инструментов."""
    return int(sum(len(json.dumps(d.as_openai(), ensure_ascii=False)) for d in defs) / 3.6)


class McpManager(QObject):
    changed = Signal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.builtin = BuiltinTools()
        self.servers: dict[str, Server] = {}
        self._lock = threading.RLock()
        self._names: dict[str, tuple[str, str]] = {}       # имя для модели -> (сервер, инструмент)
        self._started = False
        self.load()

    # ------------------------------------------------------------------ конфиг
    @property
    def file(self) -> Path:
        return paths.CONFIG_DIR / "mcp_servers.json"

    def workspace(self) -> Path:
        ws = self.config.get("chat.workspace") or ""
        p = paths.resolve(ws) if ws else paths.ROOT / "workspace"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def load(self) -> None:
        data: dict = {}
        try:
            if self.file.exists():
                raw = json.loads(self.file.read_text(encoding="utf-8-sig"))
                data = raw.get("mcpServers", raw.get("servers", {})) if isinstance(raw, dict) else {}
            else:
                data = default_servers()
                self._write(data)
        except Exception as exc:  # noqa: BLE001
            log.error("Не удалось прочитать mcp_servers.json: %s", exc)
        with self._lock:
            self.servers = {n: Server(n, s) for n, s in data.items() if isinstance(s, dict)}

    def _write(self, data: dict) -> None:
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"mcpServers": data}, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.file)
        except OSError as exc:
            log.error("Не удалось сохранить mcp_servers.json: %s", exc)

    def save(self) -> None:
        with self._lock:
            self._write({n: s.spec for n, s in self.servers.items()})

    def export_text(self) -> str:
        with self._lock:
            return json.dumps({"mcpServers": {n: s.spec for n, s in self.servers.items()}}, ensure_ascii=False, indent=2)

    def replace_all_from_text(self, text: str) -> int:
        """Полная замена конфигурации JSON-текстом (редактор в диалоге). Возвращает число серверов."""
        raw = json.loads(text)
        data = raw.get("mcpServers", raw) if isinstance(raw, dict) else None
        if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
            raise ValueError("Ожидается объект {\"mcpServers\": {\"имя\": {…}}}")
        self.stop_all()
        with self._lock:
            self.servers = {n: Server(n, s) for n, s in data.items()}
        self.save()
        self.start_enabled(force=True)
        self.changed.emit()
        return len(data)

    # ------------------------------------------------------------------ подключение
    def start_enabled(self, force: bool = False) -> None:
        if self._started and not force:
            return
        self._started = True
        for s in list(self.servers.values()):
            if s.enabled and s.state in (ServerState.OFF, ServerState.ERROR if force else ServerState.OFF):
                self._connect_async(s.name)

    def _expand(self, value):
        if isinstance(value, str):
            return value.replace("${workspace}", str(self.workspace())).replace("${data}", str(paths.DATA_DIR))
        if isinstance(value, list):
            return [self._expand(v) for v in value]
        if isinstance(value, dict):
            return {k: self._expand(v) for k, v in value.items()}
        return value

    def _set(self, s: Server, state: ServerState, error: str = "") -> None:
        s.state, s.error = state, error
        self.changed.emit()

    def _connect_async(self, name: str) -> None:
        s = self.servers.get(name)
        if s is None or s.state == ServerState.STARTING:
            return
        self._set(s, ServerState.STARTING)
        threading.Thread(target=self._connect, args=(s,), daemon=True, name=f"mcp-{name}").start()

    def _connect(self, s: Server) -> None:
        spec = self._expand(copy.deepcopy(s.spec))
        cmd = spec.get("command")
        if cmd and not shutil.which(str(cmd)):
            hint = NEEDS_HINT.get(RUNTIME_OF.get(Path(str(cmd)).stem.lower(), ""), "")
            self._set(s, ServerState.ERROR, f"Команда «{cmd}» не найдена." + (f"\n{hint}" if hint else ""))
            return
        self.workspace()           # папка из плейсхолдера ${workspace} должна существовать
        client = McpClient(s.name, spec, on_tools_changed=lambda n=s.name: self._refresh_async(n))
        t0 = time.time()
        try:
            client.connect(timeout=float(spec.get("init_timeout", 150 if cmd else 30)))
        except McpError as exc:
            tail = client.stderr_tail.strip().splitlines()[-3:]
            msg = str(exc) + ("\n" + "\n".join(tail) if tail and tail[-1] not in str(exc) else "")
            log.warning("MCP %s: подключение не удалось: %s", s.name, msg.replace("\n", " | "))
            client.close()
            self._set(s, ServerState.ERROR, msg)
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("MCP %s", s.name)
            client.close()
            self._set(s, ServerState.ERROR, str(exc))
            return
        if not s.enabled:          # пока подключались — пользователь выключил сервер
            client.close()
            self._set(s, ServerState.OFF)
            return
        old, s.client, s.tools = s.client, client, client.tools
        if old:
            old.close()
        info = client.server_info
        s.info = f"{info.get('name', '')} {info.get('version', '')}".strip()
        log.info("MCP %s: готов за %.1f с, инструментов: %d", s.name, time.time() - t0, len(s.tools))
        self._set(s, ServerState.READY)

    def _refresh_async(self, name: str) -> None:
        s = self.servers.get(name)
        if not s or not s.client:
            return

        def work():
            try:
                s.client.refresh_tools()  # type: ignore[union-attr]
                s.tools = s.client.tools  # type: ignore[union-attr]
                self.changed.emit()
            except Exception as exc:  # noqa: BLE001
                log.warning("refresh tools %s: %s", name, exc)

        threading.Thread(target=work, daemon=True).start()

    def set_enabled(self, name: str, on: bool) -> None:
        s = self.servers.get(name)
        if not s:
            return
        s.spec["disabled"] = not on
        self.save()
        if on:
            self._connect_async(name)
        else:
            self._stop(s)
            self.changed.emit()

    def restart(self, name: str) -> None:
        s = self.servers.get(name)
        if not s:
            return
        self._stop(s)
        if s.enabled:
            self._connect_async(name)

    def upsert(self, name: str, spec: dict, old_name: str = "") -> None:
        with self._lock:
            if old_name and old_name != name and old_name in self.servers:
                self._stop(self.servers.pop(old_name))
            cur = self.servers.get(name)
            if cur:
                self._stop(cur)
                cur.spec = spec
            else:
                self.servers[name] = Server(name, spec)
        self.save()
        if not spec.get("disabled"):
            self._connect_async(name)
        self.changed.emit()

    def remove(self, name: str) -> None:
        with self._lock:
            s = self.servers.pop(name, None)
        if s:
            self._stop(s)
        self.save()
        self.changed.emit()

    def _stop(self, s: Server) -> None:
        client, s.client = s.client, None
        s.tools, s.state, s.error = [], ServerState.OFF, ""
        if client:
            threading.Thread(target=client.close, daemon=True).start()

    def stop_all(self) -> None:
        for s in list(self.servers.values()):
            client, s.client = s.client, None
            s.tools, s.state = [], ServerState.OFF
            if client:
                client.close()
        self._started = False

    def shutdown(self) -> None:
        self.stop_all()

    # ------------------------------------------------------------------ реестр инструментов
    def disabled_tools(self) -> set[str]:
        return set(self.config.get("chat.disabled_tools", []) or [])

    def set_tool_disabled(self, full_name: str, disabled: bool) -> None:
        cur = self.disabled_tools()
        (cur.add if disabled else cur.discard)(full_name)
        self.config.set("chat.disabled_tools", sorted(cur))
        self.changed.emit()

    def all_defs(self) -> list[ToolDef]:
        """Все инструменты, включая выключенные пользователем (для списка в настройках)."""
        defs = list(self.builtin.defs())
        names = {d.name for d in defs}
        mapping: dict[str, tuple[str, str]] = {}
        with self._lock:
            servers = list(self.servers.values())
        for s in servers:
            if s.state != ServerState.READY:
                continue
            for tl in s.tools:
                full = safe_name(f"{s.name}__{tl.name}")[:64]
                base, n = full, 2
                while full in names:
                    full, n = f"{base[:60]}_{n}", n + 1
                names.add(full)
                mapping[full] = (s.name, tl.name)
                risk = "safe" if tl.read_only else "write"
                defs.append(ToolDef(full, tl.description or tl.title or tl.name, tl.schema, s.name, tl.name, risk,
                                    tl.title or tl.name, trusted=s.trusted or bool(tl.read_only)))
        self._names = mapping
        return defs

    def active_defs(self) -> list[ToolDef]:
        off = self.disabled_tools()
        return [d for d in self.all_defs() if d.name not in off]

    def ready_count(self) -> int:
        return sum(1 for s in self.servers.values() if s.state == ServerState.READY)

    # ------------------------------------------------------------------ вызов
    def call(self, defn: ToolDef, args: dict, cancel: threading.Event) -> tuple[str, bool]:
        """Выполняет инструмент. Возвращает (текст, is_error). Исключения наружу не выходят."""
        try:
            if defn.server == BUILTIN:
                ctx = ToolContext(self.workspace(), cancel, self.config)
                return self._limit(self.builtin.call(defn.tool, args, ctx)), False
            s = self.servers.get(defn.server)
            if s is None or s.client is None or not s.client.alive:
                if s is not None and s.enabled:
                    self._connect_async(s.name)
                return f"MCP-сервер «{defn.server}» сейчас не подключён.", True
            timeout = float(s.spec.get("timeout", 60))
            text, is_err = s.client.call_tool(defn.tool, args, timeout=timeout, cancel=cancel)
            return self._limit(text or "(пустой ответ)"), is_err
        except ToolError as exc:
            return str(exc), True
        except McpError as exc:
            return f"Ошибка MCP: {exc}", True
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s", defn.name)
            return f"Внутренняя ошибка инструмента: {exc}", True

    @staticmethod
    def _limit(text: str) -> str:
        if len(text) <= MAX_TOOL_CHARS:
            return text
        return text[:MAX_TOOL_CHARS - 2500] + f"\n… [обрезано {len(text) - MAX_TOOL_CHARS + 2500} символов] …\n" + text[-2500:]
