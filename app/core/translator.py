"""Translator: перевод готового промта на выбранный язык через ту же локальную Strata (в фоновом потоке).

Перевод не попадает в историю версий — это только удобство для чтения. Блоки <think> вырезаются.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QThread, Signal

from app.core import connection
from app.core.model_manager import Model
from app.strata.strata_api import StrataAPI, StrataError
from app.strata.strata_manager import StrataManager
from app.utils.config import Config
from app.utils.i18n import t
from app.utils.log import get_logger

log = get_logger("translator")

# (название в интерфейсе, название языка для модели)
LANGUAGES: list[tuple[str, str]] = [
    ("Русский", "Russian"), ("English", "English"), ("中文", "Simplified Chinese"), ("Español", "Spanish"),
    ("Deutsch", "German"), ("Français", "French"), ("日本語", "Japanese"), ("한국어", "Korean"),
    ("Українська", "Ukrainian"),
]

SYSTEM = (
    "You are a precise technical translator. Translate the user's text into {lang}. "
    "Keep the exact structure and formatting: line breaks, lists, headings, XML-like tags, bracketed placeholders "
    "(for example <Subject 1>), numbers, units, camera/lighting terms and proper names. "
    "Do not add comments, notes or explanations and do not omit anything. Return ONLY the translation."
)


class _Worker(QThread):
    chunk = Signal(str)
    done = Signal(str, bool)      # текст, остановлено
    failed = Signal(str)

    def __init__(self, text: str, lang: str, model: Model, max_tokens: int, strata: StrataManager, config: Config):
        super().__init__()
        self.text, self.lang, self.model, self.max_tokens = text, lang, model, max_tokens
        self.strata, self.config = strata, config
        self.cancel = threading.Event()
        self.api: StrataAPI | None = None

    def stop(self) -> None:
        self.cancel.set()
        if self.api:
            self.api.cancel()

    def run(self) -> None:
        try:
            log.info("Translation start lang=%s chars=%d", self.lang, len(self.text))
            self.strata.mark_loading()
            if not self.strata.ensure_ready(self.cancel):
                if self.cancel.is_set():
                    self.done.emit("", True)
                    return
                raise StrataError("start", self.strata.detail)
            self.api = connection.make_api(self.config)
            body = {
                "model": connection.model_id(self.config, self.strata, self.api),
                "temperature": 0.2, "max_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": SYSTEM.format(lang=self.lang)},
                             {"role": "user", "content": self.text}],
                # перевод не требует рассуждений — так он быстрее
                "chat_template_kwargs": {"enable_thinking": False},
            }
            connection.apply_model(body, body.get("model", ""))
            out = self._stream(body)
            log.info("Translation end chars=%d stopped=%s", len(out), self.cancel.is_set())
            self.done.emit(out, self.cancel.is_set())
        except StrataError as exc:
            log.error("Translation failed: %s %s", exc.kind, exc.detail)
            self.failed.emit(self._friendly(exc))
        except Exception as exc:
            log.exception("Translation crashed")
            self.failed.emit(t("err_other", detail=str(exc)))
        finally:
            self.strata.mark_idle()

    def _stream(self, body: dict) -> str:
        assert self.api is not None
        out: list[str] = []
        try:
            gen = self.api.chat_stream(body)
            for piece in gen:
                if self.cancel.is_set():
                    break
                if not out:
                    self.strata.mark_generating()
                out.append(piece)
                self.chunk.emit(piece)
        except StrataError as exc:
            if exc.kind == "http" and exc.status in (400, 415, 422) and not out and not self.cancel.is_set():
                body = {k: v for k, v in body.items() if k != "chat_template_kwargs"}
                return self._stream(body)
            raise
        return "".join(out)

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


class Translator(QObject):
    chunk = Signal(str)
    finished = Signal(str, bool)     # перевод, остановлено
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, config: Config, strata: StrataManager, parent=None):
        super().__init__(parent)
        self.config, self.strata = config, strata
        self._worker: _Worker | None = None
        self._threads: set[_Worker] = set()

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def translate(self, text: str, language: str, model: Model, max_tokens: int = 8192) -> bool:
        if self._worker is not None or not text.strip():
            return False
        w = _Worker(text, language, model, max(1024, max_tokens), self.strata, self.config)
        w.chunk.connect(self.chunk)
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

    def wait_idle(self, ms: int = 5000) -> None:
        for w in list(self._threads):
            w.wait(ms)

    def _release(self) -> None:
        self._worker = None
        self.busy_changed.emit(False)

    def _on_done(self, text: str, stopped: bool) -> None:
        self._release()
        self.finished.emit(text, stopped)

    def _on_failed(self, message: str) -> None:
        self._release()
        self.failed.emit(message)
