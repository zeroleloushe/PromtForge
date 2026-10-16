"""Жизненный цикл Strata: START / READY / GENERATING / UNLOAD / STOP.

UI вызывает только методы этого класса и слушает сигналы. Вся работа с сетью и процессом — в фоновых потоках.
"""
from __future__ import annotations

import threading
import time
from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal

from app.strata.strata_api import StrataAPI
from app.strata.strata_process import StrataProcess
from app.utils.config import Config
from app.utils.i18n import t
from app.utils.log import get_logger

log = get_logger("strata")


class StrataState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    STARTED = "STARTED"
    LOADING = "LOADING"
    READY = "READY"
    GENERATING = "GENERATING"
    STOPPING = "STOPPING"
    UNLOADING = "UNLOADING"
    ERROR = "ERROR"


class StrataManager(QObject):
    state_changed = Signal(object, str)   # StrataState, подробность
    notify = Signal(str, str)             # уровень (info|success|warning|error), текст
    info_changed = Signal(dict)           # {"model": str, "context": int|None, "unloaded": bool}
    _poll_done = Signal(str)

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.process = StrataProcess()
        self._state = StrataState.STOPPED
        self._detail = ""
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._polling = False
        self.model_unloaded = False
        self.pre_load_hook = None   # callable(cancel_event): вызывается перед обращением к Strata (освобождение памяти ComfyUI)
        self.info: dict = {"model": "", "context": None, "unloaded": False}
        self._poll_done.connect(self._on_poll)
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._poll)

    # --- состояние ---------------------------------------------------------
    @property
    def state(self) -> StrataState:
        return self._state

    @property
    def detail(self) -> str:
        return self._detail

    def _set_state(self, state: StrataState, detail: str = "") -> None:
        with self._lock:
            changed = state != self._state or detail != self._detail
            self._state, self._detail = state, detail
        if changed:
            log.info("Strata state -> %s", state.value)
            self.state_changed.emit(state, detail)

    def api(self) -> StrataAPI:
        c = self.config
        return StrataAPI(c.get("strata.api_base"), c.get("strata.health_endpoint", "/health"),
                         c.get("generation.timeout", 120), c.get("strata.unload_endpoint", ""))

    def begin_monitoring(self) -> None:
        self._timer.start()
        self._poll()

    def shutdown_monitoring(self) -> None:
        self._timer.stop()

    # --- мониторинг --------------------------------------------------------
    def _poll(self) -> None:
        if self._polling or self._state in (StrataState.STARTING, StrataState.STOPPING, StrataState.UNLOADING):
            return
        self._polling = True
        api = self.api()

        def work():
            try:
                self._poll_done.emit(api.health(timeout=1.5))
            except Exception:
                self._poll_done.emit("down")

        threading.Thread(target=work, daemon=True).start()

    def _on_poll(self, health: str) -> None:
        self._polling = False
        st = self._state
        if st in (StrataState.STARTING, StrataState.STOPPING, StrataState.UNLOADING):
            return
        if health == "ok":
            if st in (StrataState.STOPPED, StrataState.ERROR, StrataState.LOADING):
                self._set_state(StrataState.READY, "")
                self._refresh_info()
        elif health == "loading":
            if st in (StrataState.STOPPED, StrataState.ERROR, StrataState.READY):
                self._set_state(StrataState.LOADING, t("strata_loading_model"))
        else:  # down
            if st in (StrataState.READY, StrataState.STARTED, StrataState.LOADING):
                if self.process.has_process() and self.process.poll() is not None:
                    log.warning("Strata завершилась unexpectedly")
                    self.notify.emit("warning", t("strata_died"))
                self._set_state(StrataState.STOPPED, "")
            elif st == StrataState.GENERATING:
                pass  # ошибку покажет движок генерации

    def _refresh_info(self) -> None:
        api = self.api()

        def work():
            models = api.list_models()
            ctx = api.context_size()
            self.info = {"model": models[0] if models else "", "context": ctx, "unloaded": self.model_unloaded}
            self.info_changed.emit(dict(self.info))

        threading.Thread(target=work, daemon=True).start()

    # --- запуск --------------------------------------------------------------
    def start(self) -> None:
        """Асинхронный запуск (кнопка / автозапуск)."""
        def work():
            if not self.ensure_ready(None):
                self.notify.emit("error", self._detail or t("err_start_failed", detail=""))

        threading.Thread(target=work, daemon=True).start()

    def ensure_ready(self, cancel: threading.Event | None, timeout: float | None = None) -> bool:
        """Блокирующе: убедиться, что API отвечает; при необходимости запустить Strata. Вызывать из потока."""
        hook = self.pre_load_hook
        if hook:
            try:
                hook(cancel)
            except Exception:
                log.exception("pre_load_hook failed")
        api = self.api()
        if api.health(timeout=2.0) == "ok":
            if self._state in (StrataState.STOPPED, StrataState.ERROR, StrataState.STARTING):
                self._set_state(StrataState.READY, "")
                self._refresh_info()
            return True
        if not self._start_lock.acquire(blocking=False):
            return self._wait_ready(api, cancel, timeout or 180)  # запуск уже идёт в другом потоке
        try:
            return self._start_blocking(api, cancel, timeout or float(self.config.get("strata.start_timeout", 180)))
        finally:
            self._start_lock.release()

    def _wait_ready(self, api: StrataAPI, cancel, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if cancel is not None and cancel.is_set():
                return False
            if api.health(timeout=2.0) == "ok":
                return True
            time.sleep(0.7)
        return False

    def _start_blocking(self, api: StrataAPI, cancel, timeout: float) -> bool:
        exe = (self.config.get("strata.executable") or "").strip()
        args = list(self.config.get("strata.arguments") or [])
        if not self.process.is_running():
            if not exe:
                self._fail(t("err_no_executable"))
                return False
            self._set_state(StrataState.STARTING, "")
            try:
                self.process.start(exe, args)
            except FileNotFoundError:
                self._fail(t("err_exe_not_found", path=exe))
                return False
            except OSError as exc:
                log.error("Не удалось запустить Strata: %s", exc)
                self._fail(t("err_start_failed", detail=str(exc)))
                return False
        else:
            self._set_state(StrataState.STARTING, "")
        end = time.time() + timeout
        died_at = None
        while time.time() < end:
            if cancel is not None and cancel.is_set():
                return False
            h = api.health(timeout=2.0)
            if h == "ok":
                self.model_unloaded = False
                self._set_state(StrataState.STARTED, "")
                self._set_state(StrataState.READY, "")
                self._refresh_info()
                return True
            if h == "loading":
                self._set_state(StrataState.LOADING, t("strata_loading_model"))
            code = self.process.poll()
            if code is not None:
                # Некоторые лаунчеры завершаются, оставляя сервер работать — даём API шанс подняться
                died_at = died_at or time.time()
                if time.time() - died_at > 20:
                    log.warning("Strata завершилась unexpectedly (код %s)", code)
                    tail = self.process.log_tail()
                    self._fail(t("err_exited", code=code) + (f"\n{tail}" if tail else ""))
                    return False
            time.sleep(0.7)
        self._fail(t("err_start_timeout", sec=int(timeout)))
        return False

    def _fail(self, message: str) -> None:
        log.error("Strata: %s", message.splitlines()[0])
        self._set_state(StrataState.ERROR, message)

    # --- состояния генерации ----------------------------------------------------
    def mark_loading(self) -> None:
        self.model_unloaded = False
        self._set_state(StrataState.LOADING, t("strata_loading_model"))

    def mark_generating(self) -> None:
        self._set_state(StrataState.GENERATING, "")

    def mark_idle(self) -> None:
        if self._state in (StrataState.GENERATING, StrataState.LOADING):
            self._set_state(StrataState.READY, "")

    # --- unload / stop --------------------------------------------------------------
    def unload(self, model_id: str = "", quiet: bool = False) -> None:
        def work():
            api = self.api()
            if api.health(timeout=2.0) == "down":
                if not quiet:
                    self.notify.emit("warning", t("unload_offline"))
                return
            prev = self._state
            self._set_state(StrataState.UNLOADING, "")
            log.info("Unload requested")
            res = api.unload(model_id)
            if res.status == "ok":
                self.model_unloaded = True
                self.info["unloaded"] = True
                self.info_changed.emit(dict(self.info))
                log.info("Model unloaded")
                self._set_state(StrataState.READY, "")
                self.notify.emit("success", t("unload_ok"))
            elif res.status == "unsupported":
                log.warning("Unload not supported by Strata API")
                self._set_state(prev if prev != StrataState.UNLOADING else StrataState.READY, "")
                self.notify.emit("warning", t("unload_unsupported"))
            else:
                log.error("Unload failed: %s", res.message)
                self._set_state(StrataState.READY, "")
                self.notify.emit("error", t("unload_failed", detail=res.message))

        threading.Thread(target=work, daemon=True).start()

    def _api_port(self) -> int:
        from urllib.parse import urlparse

        try:
            return urlparse(self.config.get("strata.api_base", "")).port or 0
        except ValueError:
            return 0

    def stop_process(self, wait: bool = False) -> None:
        def work():
            if not self.process.has_process():
                if self.api().health(timeout=1.5) != "down":
                    self.notify.emit("warning", t("stop_external"))
                else:
                    self._set_state(StrataState.STOPPED, "")
                return
            self._set_state(StrataState.STOPPING, "")
            ok = self.process.stop()
            if ok and self.api().health(timeout=1.5) != "down":
                # лаунчер (.bat) уже завершился, а сервер остался жить — гасим того, кто слушает порт API
                self.process.kill_listener(self._api_port())
                for _ in range(16):
                    if self.api().health(timeout=1.0) == "down":
                        break
                    time.sleep(0.5)
            self.model_unloaded = False
            if ok:
                self._set_state(StrataState.STOPPED, "")
                self.notify.emit("success", t("stop_ok"))
            else:
                self._set_state(StrataState.ERROR, t("stop_failed"))
                self.notify.emit("error", t("stop_failed"))

        th = threading.Thread(target=work, daemon=True)
        th.start()
        if wait:
            th.join(15)
