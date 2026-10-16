"""PromptEngine: собирает контекст, при необходимости поднимает Strata и стримит ответ модели.

Работает в QThread, чтобы GUI никогда не зависал. История пишется вызывающим кодом (HistoryManager).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QThread, Signal

from app.core.context_builder import BuiltContext, ContextBuilder, ContextOptions
from app.core.model_manager import Model
from app.core.reference_manager import Reference
from app.core.style_manager import Style
from app.strata.strata_api import StrataAPI, StrataError
from app.strata.strata_manager import StrataManager
from app.utils.config import Config
from app.utils.i18n import t
from app.utils.log import get_logger

# мягкий бюджет рассуждений (токены) по уровню — показывается в UI; сервер получает reasoning_effort
THINKING_BUDGET = {"off": 0, "low": 2048, "med": 6144, "high": 0}   # 0 = без лимита
THINKING_LEVELS = ("off", "low", "med", "high")

log = get_logger("engine")


@dataclass
class GenerationRequest:
    model: Model
    styles: list[Style]
    user_request: str
    references: list[Reference] = field(default_factory=list)
    current_prompt: str = ""
    edit_request: str = ""
    previous_edits: list[str] = field(default_factory=list)
    temperature: float = 1.0
    max_tokens: int = 32000
    streaming: bool = True
    options: ContextOptions = field(default_factory=ContextOptions)
    enable_thinking: bool = True     # False -> просим модель отвечать без рассуждений
    thinking_level: str = "high"     # off | low | med | high
    show_reasoning: bool = False     # True -> рассуждения модели уходят в сигнал reasoning


class _Worker(QThread):
    chunk = Signal(str)
    reasoning = Signal(str)
    first_token = Signal()
    warning = Signal(str)
    done = Signal(str, bool)       # text, stopped
    stats = Signal(dict)           # usage/ttft — перед done
    progress = Signal(int, int)    # куски рассуждений (≈токены), куски ответа
    failed = Signal(str)

    def __init__(self, req: GenerationRequest, builder: ContextBuilder, strata: StrataManager, config: Config):
        super().__init__()
        self.req, self.builder, self.strata, self.config = req, builder, strata, config
        self.cancel = threading.Event()
        self.api: StrataAPI | None = None
        self._t0 = time.time()
        self._ttft: float | None = None
        self._r_n = 0
        self._c_n = 0
        self._last_prog = 0.0

    def stop(self) -> None:
        self.cancel.set()
        if self.api:
            self.api.cancel()

    def run(self) -> None:
        req, text = self.req, ""
        try:
            log.info("Generation start model=%s", req.model.id)
            self.strata.mark_loading()
            if not self.strata.ensure_ready(self.cancel):
                if self.cancel.is_set():
                    self.done.emit("", True)
                    return
                raise StrataError("start", self.strata.detail)
            ctx = self.builder.build(
                req.model, req.styles, req.user_request, req.references, req.current_prompt,
                req.edit_request, req.previous_edits, req.options if self.config.get("options.use_references", True)
                else ContextOptions(req.options.aspect_ratio, req.options.creativity, req.options.include_history, False))
            for w in ctx.warnings:
                self.warning.emit(w)
            self.api = StrataAPI(req.model.api_base, self.config.get("strata.health_endpoint", "/health"),
                                 self.config.get("generation.timeout", 120),
                                 stream_timeout=self.config.get("generation.stream_timeout", 600))
            self.strata.mark_loading()
            self._t0 = time.time()      # TTFT считаем от отправки запроса, а не от старта Strata
            text = self._run(ctx, text)
            log.info("Generation end chars=%d stopped=%s", len(text), self.cancel.is_set())
            self.done.emit(text, self.cancel.is_set())
        except StrataError as exc:
            log.error("Generation failed: %s %s", exc.kind, exc.detail)
            if exc.kind == "timeout":
                log.error("timeout")
            self.failed.emit(self._friendly(exc))
        except Exception as exc:  # приложение не должно падать
            log.exception("Generation crashed")
            self.failed.emit(t("err_other", detail=str(exc)))
        finally:
            self.strata.mark_idle()

    def _run(self, ctx: BuiltContext, text: str) -> str:
        req = self.req
        payload = {"model": req.model.model, "temperature": req.temperature, "max_tokens": req.max_tokens}
        level = req.thinking_level if req.enable_thinking else "off"
        if level == "off":
            # llama.cpp-совместимые серверы: отключает режим рассуждений в chat-шаблоне
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        elif level in ("low", "med"):
            # OpenAI-совместимое поле; серверы, которые его не знают, просто игнорируют
            payload["reasoning_effort"] = "medium" if level == "med" else "low"
            payload["chat_template_kwargs"] = {"enable_thinking": True, "reasoning_effort": payload["reasoning_effort"]}
        first = True
        try:
            text = self._stream_once(ctx, payload, first)
        except StrataError as exc:
            retryable = exc.kind == "http" and exc.status in (400, 415, 422) and not self.cancel.is_set()
            if retryable and ctx.has_images:
                log.warning("API отклонил изображения (HTTP %s) — повтор без них", exc.status)
                self.warning.emit("warn_no_vision")
                text = self._stream_once(ContextBuilder.without_images(ctx), payload, True)
            elif retryable and "chat_template_kwargs" in payload:
                log.warning("API не принял chat_template_kwargs (HTTP %s) — повтор без них", exc.status)
                payload.pop("chat_template_kwargs")
                payload.pop("reasoning_effort", None)
                text = self._stream_once(ctx, payload, True)
            else:
                raise
        return text

    def _stream_once(self, ctx: BuiltContext, payload: dict, first: bool) -> str:
        assert self.api is not None
        body = {**payload, "messages": ctx.messages}
        sent_chars = sum(len(m["content"]) if isinstance(m["content"], str)
                         else sum(len(x.get("text", "")) for x in m["content"] if x.get("type") == "text")
                         for m in ctx.messages)
        sys_chars = len(ctx.messages[0]["content"]) if isinstance(ctx.messages[0]["content"], str) else 0
        log.info("Запрос: system %d симв., всего текста %d симв., max_tokens=%s", sys_chars, sent_chars, payload.get("max_tokens"))
        out: list[str] = []
        got_first = False
        def on_r(text: str) -> None:      # всегда считаем рассуждения (живой счётчик), показываем по флагу
            if self._ttft is None:
                self._ttft = time.time() - self._t0
            self._r_n += 1
            self._tick()
            self.reasoning.emit(text)     # UI сам решает: инлайн-блок (show_reasoning) и/или вкладка «Мысли»

        if self.req.streaming:
            for piece in self.api.chat_stream(body, on_reasoning=on_r):
                if self.cancel.is_set():
                    break
                if self._ttft is None:
                    self._ttft = time.time() - self._t0
                self._c_n += 1
                self._tick()
                if not got_first:
                    got_first = True
                    self.strata.mark_generating()
                    self.first_token.emit()
                out.append(piece)
                self.chunk.emit(piece)
        else:
            full = self.api.chat(body, on_reasoning=on_r)
            if not self.cancel.is_set():
                self.strata.mark_generating()
                self.first_token.emit()
                out.append(full)
                self.chunk.emit(full)
        u = getattr(self.api, "last_usage", {}) or {}
        self.progress.emit(self._r_n, self._c_n)
        self.stats.emit({**u, "ttft": self._ttft, "reasoning_pieces": self._r_n, "content_pieces": self._c_n,
                         "max_tokens": payload.get("max_tokens")})
        if u:
            est = int(sent_chars / 3.2)      # грубая оценка: ~3.2 символа на токен для смеси англ./рус.
            log.info("Сервер принял prompt=%s ток. (оценка по символам ≈%d), выдал %s ток., finish=%s",
                     u.get("prompt", "?"), est, u.get("completion", "?"), u.get("finish", "?"))
            if u.get("finish") == "length":
                log.warning("Ответ оборван по лимиту max_tokens=%s — увеличьте «Макс. токенов» в настройках модели", payload.get("max_tokens"))
        return "".join(out)

    def _tick(self) -> None:
        now = time.time()
        if now - self._last_prog >= 0.25:     # не чаще 4 раз в секунду
            self._last_prog = now
            self.progress.emit(self._r_n, self._c_n)

    @staticmethod
    def _friendly(exc: StrataError) -> str:
        if exc.kind == "start" and exc.detail:
            return exc.detail
        if exc.kind in ("offline", "start"):
            return t("err_offline", detail=(exc.detail or "").splitlines()[0] if exc.detail else "")
        if exc.kind == "timeout":
            return t("err_timeout")
        if exc.kind == "http":
            if exc.status in (404,) and "model" in (exc.detail or "").lower():
                return t("err_bad_model", detail=exc.detail)
            return t("err_http", status=exc.status, detail=exc.detail)
        if exc.kind == "json":
            return t("err_json", detail=exc.detail)
        return t("err_other", detail=exc.detail)


class PromptEngine(QObject):
    chunk = Signal(str)
    reasoning = Signal(str)
    first_token = Signal()
    warning = Signal(str)
    finished = Signal(str, bool)     # итоговый текст, остановлено пользователем
    progress = Signal(int, int)      # ≈токены рассуждений, ≈токены ответа (живой счётчик)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, config: Config, strata: StrataManager, parent=None):
        super().__init__(parent)
        self.config = config
        self.strata = strata
        self.builder = ContextBuilder()
        self._worker: _Worker | None = None
        self._threads: set[_Worker] = set()  # живые потоки: нельзя отдавать GC до завершения
        self._started = 0.0
        self.last_elapsed = 0.0          # длительность последней генерации (фиксируется до _release)
        self.last_stats: dict = {}

    @property
    def busy(self) -> bool:
        return self._worker is not None

    @property
    def elapsed(self) -> float:
        return time.time() - self._started if self._worker else 0.0

    def generate(self, req: GenerationRequest) -> bool:
        if self._worker is not None:
            return False
        w = _Worker(req, self.builder, self.strata, self.config)
        w.chunk.connect(self.chunk)
        w.reasoning.connect(self.reasoning)
        w.first_token.connect(self.first_token)
        w.warning.connect(self.warning)
        w.progress.connect(self.progress)
        w.stats.connect(self._on_stats)
        w.done.connect(self._on_done)
        w.failed.connect(self._on_failed)
        self._threads.add(w)
        w.finished.connect(lambda w=w: self._reap(w))
        self._worker = w
        self._started = time.time()
        self.last_elapsed = 0.0
        self.last_stats = {}
        self.busy_changed.emit(True)
        w.start()
        return True

    def stop(self) -> None:
        if self._worker:
            log.info("Stop generation requested")
            self._worker.stop()

    def _reap(self, w: _Worker) -> None:
        self._threads.discard(w)
        w.deleteLater()

    def wait_idle(self, ms: int = 5000) -> None:
        for w in list(self._threads):
            w.wait(ms)

    def _release(self) -> None:
        self._worker = None
        self.busy_changed.emit(False)

    def _on_stats(self, st: dict) -> None:
        self.last_stats = dict(st)

    def _on_done(self, text: str, stopped: bool) -> None:
        # время фиксируем ДО _release(): после него self._worker=None и elapsed == 0
        self.last_elapsed = time.time() - self._started
        self.last_stats["elapsed"] = self.last_elapsed
        self._release()
        self.finished.emit(text, stopped)

    def _on_failed(self, message: str) -> None:
        self.last_elapsed = time.time() - self._started
        self._release()
        self.failed.emit(message)
