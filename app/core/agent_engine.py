"""AgentEngine: цикл «модель → инструменты → модель» в фоновом потоке.

Модель получает список инструментов (встроенных и MCP); пока она просит вызвать инструмент, движок выполняет его
(при необходимости — после подтверждения пользователя) и возвращает результат модели. Ответ идёт потоком.
Если сервер не умеет нативные tools — автоматически переключается на режим «инструменты в промте».
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QThread, Signal

from app.core import connection
from app.core.model_manager import Model
from app.core.toolcalls import fit_messages, new_call_id, parse_args, to_prompt_messages
from app.mcp.builtin import ToolDef
from app.mcp.manager import McpManager
from app.strata.strata_api import StrataAPI, StrataError
from app.strata.strata_manager import StrataManager
from app.utils.config import Config
from app.utils.i18n import t
from app.utils.log import get_logger

log = get_logger("agent")

CHARS_PER_TOKEN = 2.6          # консервативная оценка для русского текста
DEFAULT_BUDGET_CHARS = 60000
_API_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name")


@dataclass
class RunParams:
    messages: list[dict]
    model: Model
    tools: list[ToolDef] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 4096
    enable_thinking: bool = True
    approval: str = "ask"            # ask | auto
    max_steps: int = 12
    tool_mode: str = "auto"          # auto | native | prompt
    ctx_tokens: int | None = None
    always_allowed: set[str] = field(default_factory=set)


def _clean(m: dict) -> dict:
    return {k: m[k] for k in _API_KEYS if k in m and not (k == "name" and m["role"] != "tool")}


class _Worker(QThread):
    step_started = Signal(int)
    delta = Signal(str)
    reasoning = Signal(str)
    tool_started = Signal(dict)
    approval_needed = Signal(dict)
    tool_running = Signal(str)
    message = Signal(dict)
    notice = Signal(str)
    done = Signal(bool)
    failed = Signal(str)

    def __init__(self, p: RunParams, strata: StrataManager, mcp: McpManager, config: Config, sticky: dict):
        super().__init__()
        self.p, self.strata, self.mcp, self.config, self.sticky = p, strata, mcp, config, sticky
        self.cancel = threading.Event()
        self.api: StrataAPI | None = None
        self._approvals: dict[str, list] = {}
        self._lock = threading.Lock()

    # --- управление извне ----------------------------------------------------------
    def stop(self) -> None:
        self.cancel.set()
        if self.api:
            self.api.cancel()

    def respond(self, call_id: str, decision: str) -> None:
        with self._lock:
            slot = self._approvals.get(call_id)
        if slot:
            slot[1] = decision
            slot[0].set()

    # --- основной цикл ---------------------------------------------------------------
    def run(self) -> None:
        stopped = False
        try:
            self.strata.mark_loading()
            if not self.strata.ensure_ready(self.cancel):
                if self.cancel.is_set():
                    self.done.emit(True)
                    return
                raise StrataError("start", self.strata.detail)
            self.api = connection.make_api(self.config)
            self._model = connection.model_id(self.config, self.strata, self.api)
            if not self.p.ctx_tokens:
                self.p.ctx_tokens = self.api.context_size()
            stopped = self._loop()
            self.done.emit(stopped)
        except StrataError as exc:
            log.error("Agent failed: %s %s", exc.kind, exc.detail)
            self.failed.emit(self._friendly(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("Agent crashed")
            self.failed.emit(t("err_other", detail=str(exc)))
        finally:
            self.strata.mark_idle()

    def _key(self) -> str:
        return connection.api_base(self.config)

    def _loop(self) -> bool:
        p = self.p
        messages = list(p.messages)
        defs = {d.name: d for d in p.tools}
        mode = "native"
        if p.tool_mode == "prompt" or (p.tool_mode == "auto" and self.sticky.get(self._key()) == "prompt"):
            mode = "prompt"
        seen: dict[str, int] = {}
        nudged = False
        for step in range(1, p.max_steps + 1):
            if self.cancel.is_set():
                return True
            self.step_started.emit(step)
            turn = self._turn(messages, p.tools, mode)
            mode = turn["mode"]
            if self.cancel.is_set() and not (turn["content"] or turn["calls"]):
                return True
            calls = turn["calls"]
            if not calls and not turn["content"].strip() and step > 1 and not nudged and not self.cancel.is_set():
                nudged = True       # модель промолчала после результатов инструментов — подталкиваем один раз
                messages.append({"role": "user", "content": "Use the tool results above to answer the user now."})
                continue
            api_msg: dict = {"role": "assistant", "content": turn["content"]}
            if calls:
                api_msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                          "function": {"name": c["name"], "arguments": c["args_json"]}} for c in calls]
            self.message.emit({**api_msg, "reasoning": turn["reasoning"], "stats": turn["stats"],
                               "truncated": turn["finish"] == "length"})
            messages.append(api_msg)
            if not calls:
                return self.cancel.is_set()
            for c in calls:
                text, is_err, dur, denied = self._run_tool(c, defs, seen)
                tool_msg = {"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": text}
                self.message.emit({**tool_msg, "is_error": is_err, "duration": dur, "denied": denied})
                messages.append(tool_msg)
            if self.cancel.is_set():
                return True
        note = t("agent_step_limit", n=p.max_steps)
        self.message.emit({"role": "assistant", "content": note, "reasoning": "", "stats": {}, "synthetic": True})
        return False

    # --- один ход модели ---------------------------------------------------------------
    def _budget(self) -> int:
        p = self.p
        if not p.ctx_tokens:
            return DEFAULT_BUDGET_CHARS
        avail = p.ctx_tokens - min(p.max_tokens, p.ctx_tokens // 3) - 400
        return max(4000, int(avail * CHARS_PER_TOKEN))

    def _payload(self, messages: list[dict], defs: list[ToolDef], mode: str, drop: set[str]) -> dict:
        p = self.p
        msgs = [_clean(m) for m in messages]
        if mode == "prompt":
            msgs = to_prompt_messages(msgs, defs)
        body: dict = connection.apply_model({"temperature": p.temperature, "max_tokens": p.max_tokens, "messages": msgs},
                                            getattr(self, "_model", ""))
        if defs and mode == "native":
            body["tools"] = [d.as_openai() for d in defs]
            body["tool_choice"] = "auto"
        if not p.enable_thinking and "chat_template_kwargs" not in drop:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if "stream_options" not in drop:
            body["stream_options"] = {"include_usage": True}
        return body

    @staticmethod
    def _fallback(exc: StrataError, mode: str, drop: set[str], has_tools: bool) -> str | None:
        msg = (exc.detail or "").lower()
        if mode == "native" and has_tools and ("jinja" in msg or "tool" in msg):
            return "prompt"
        if exc.status in (400, 415, 422):
            for k in ("stream_options", "chat_template_kwargs"):
                if k not in drop:
                    return k
            if mode == "native" and has_tools:
                return "prompt"
        return None

    def _turn(self, messages: list[dict], defs: list[ToolDef], mode: str) -> dict:
        assert self.api is not None
        fitted, dropped = fit_messages(messages, self._budget())
        if dropped:
            self.notice.emit(t("agent_trimmed", n=dropped))
        drop: set[str] = set()
        content: list[str] = []
        reasoning: list[str] = []
        calls_raw: list[dict] = []
        usage: dict = {}
        finish = ""
        t0 = time.time()
        first = True
        for _ in range(5):
            payload = self._payload(fitted, defs, mode, drop)
            got = False
            try:
                for ev in self.api.chat_events(payload):
                    if self.cancel.is_set():
                        break
                    kind = ev["type"]
                    if kind in ("content", "reasoning"):
                        got = True
                        if first:
                            first = False
                            self.strata.mark_generating()
                        if kind == "content":
                            content.append(ev["text"])
                            self.delta.emit(ev["text"])
                        else:
                            reasoning.append(ev["text"])
                            self.reasoning.emit(ev["text"])
                    elif kind == "tool_calls":
                        got = True
                        calls_raw = ev["calls"]
                    elif kind == "usage":
                        usage = ev
                    elif kind == "finish":
                        finish = ev["reason"]
                break
            except StrataError as exc:
                nxt = None if (got or self.cancel.is_set() or exc.kind != "http") else self._fallback(exc, mode, drop, bool(defs))
                if nxt is None:
                    raise
                if nxt == "prompt":
                    mode = "prompt"
                    self.sticky[self._key()] = "prompt"
                    log.warning("Сервер не принял tools (%s) — режим «инструменты в промте»", exc.detail[:120])
                    self.notice.emit(t("agent_prompt_mode"))
                else:
                    drop.add(nxt)
                    log.info("Сервер отклонил %s — повторяю без него", nxt)
        calls = []
        for c in calls_raw:
            args, err = parse_args(c["arguments"])
            calls.append({"id": c["id"] or new_call_id(), "name": c["name"], "args": args, "parse_error": err,
                          "args_json": json.dumps(args, ensure_ascii=False) if not err else (c["arguments"] or "{}")})
        text = "".join(content)
        elapsed = time.time() - t0
        comp = usage.get("completion") or max(1, int(len(text + "".join(reasoning)) / 3.2))
        stats = {"elapsed": round(elapsed, 2), "completion": comp, "prompt": usage.get("prompt", 0),
                 "tps": round(usage.get("tps") or (comp / elapsed if elapsed > 0.2 else 0), 1), "mode": mode}
        return {"content": text, "reasoning": "".join(reasoning), "calls": calls, "finish": finish, "mode": mode,
                "stats": stats}

    # --- выполнение инструмента -------------------------------------------------------------
    def _run_tool(self, c: dict, defs: dict[str, ToolDef], seen: dict[str, int]) -> tuple[str, bool, float, bool]:
        d = defs.get(c["name"])
        info = {"id": c["id"], "name": c["name"], "args": c["args"], "title": d.title if d else c["name"],
                "server": d.server if d else "", "risk": d.risk if d else "safe", "error": c["parse_error"]}
        self.tool_started.emit(info)
        if self.cancel.is_set():
            return t("agent_cancelled"), True, 0.0, True
        if d is None:
            return t("agent_unknown_tool", name=c["name"], names=", ".join(sorted(defs)[:40])), True, 0.0, False
        if c["parse_error"]:
            return t("agent_bad_args", detail=c["parse_error"]), True, 0.0, False
        sig = c["name"] + json.dumps(c["args"], sort_keys=True, ensure_ascii=False)
        seen[sig] = seen.get(sig, 0) + 1
        if seen[sig] > 2:
            return t("agent_loop_guard"), True, 0.0, False
        if not self._allowed(c, d, info):
            return t("agent_denied"), True, 0.0, True
        self.tool_running.emit(c["id"])
        t0 = time.time()
        text, is_err = self.mcp.call(d, c["args"], self.cancel)
        if self.cancel.is_set() and is_err:
            text = t("agent_cancelled")
        return text, is_err, round(time.time() - t0, 2), False

    def _allowed(self, c: dict, d: ToolDef, info: dict) -> bool:
        if d.risk == "safe" or d.trusted or self.p.approval == "auto" or d.name in self.p.always_allowed:
            return True
        ev = threading.Event()
        slot: list = [ev, None]
        with self._lock:
            self._approvals[c["id"]] = slot
        self.approval_needed.emit(info)
        while not ev.wait(0.2):
            if self.cancel.is_set():
                break
        with self._lock:
            self._approvals.pop(c["id"], None)
        decision = slot[1]
        if decision == "always":
            self.p.always_allowed.add(d.name)
        return decision in ("once", "always")

    @staticmethod
    def _friendly(exc: StrataError) -> str:
        if exc.kind == "start" and exc.detail:
            return exc.detail
        if exc.kind in ("offline", "start"):
            return t("err_offline", detail=(exc.detail or "").splitlines()[0] if exc.detail else "")
        if exc.kind == "timeout":
            return t("err_timeout")
        if exc.kind == "http":
            return t("err_http", status=exc.status, detail=exc.detail)
        return t("err_other", detail=exc.detail)


class AgentEngine(QObject):
    step_started = Signal(int)
    delta = Signal(str)
    reasoning = Signal(str)
    tool_started = Signal(dict)
    approval_needed = Signal(dict)
    tool_running = Signal(str)
    message = Signal(dict)
    notice = Signal(str)
    finished = Signal(bool)        # остановлено пользователем?
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, config: Config, strata: StrataManager, mcp: McpManager, parent=None):
        super().__init__(parent)
        self.config, self.strata, self.mcp = config, strata, mcp
        self._worker: _Worker | None = None
        self._threads: set[_Worker] = set()
        self.always_allowed: set[str] = set()
        self._sticky: dict = {}

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def tool_mode_of(self, model: Model) -> str:
        return self._sticky.get(connection.api_base(self.config), "native")

    def send(self, params: RunParams) -> bool:
        if self._worker is not None:
            return False
        params.always_allowed = self.always_allowed
        w = _Worker(params, self.strata, self.mcp, self.config, self._sticky)
        for name in ("step_started", "delta", "reasoning", "tool_started", "approval_needed", "tool_running", "message",
                     "notice"):
            getattr(w, name).connect(getattr(self, name))
        w.done.connect(self._on_done)
        w.failed.connect(self._on_failed)
        self._threads.add(w)
        w.finished.connect(lambda w=w: (self._threads.discard(w), w.deleteLater()))
        self._worker = w
        self.busy_changed.emit(True)
        w.start()
        return True

    def stop(self) -> None:
        if self._worker:
            self._worker.stop()

    def respond_approval(self, call_id: str, decision: str) -> None:
        if self._worker:
            self._worker.respond(call_id, decision)

    def wait_idle(self, ms: int = 5000) -> None:
        for w in list(self._threads):
            w.wait(ms)

    def _release(self) -> None:
        self._worker = None
        self.busy_changed.emit(False)

    def _on_done(self, stopped: bool) -> None:
        self._release()
        self.finished.emit(stopped)

    def _on_failed(self, message: str) -> None:
        self._release()
        self.failed.emit(message)
