"""ComfyBridge: совместное использование RAM/VRAM между PromptForge (Strata) и ComfyUI.

Направление 1 (PromptForge -> ComfyUI): перед запросом к Strata вызывается prepare_for_llm() — он шлёт ComfyUI
    POST /free {unload_models, free_memory}, чтобы освободить память под LLM. Если ComfyUI занята — ждёт конца очереди.
Направление 2 (ComfyUI -> Strata): раз в N секунд опрашивается GET /queue; когда у ComfyUI появляется работа,
    испускается busy_changed(True), а главное окно выгружает модель Strata.

ComfyUI не нужно модифицировать — используются её штатные HTTP-эндпоинты. Если ComfyUI не запущена, всё молча пропускается.
"""
from __future__ import annotations

import threading
import time

import requests
from PySide6.QtCore import QObject, QTimer, Signal

from app.utils.config import Config
from app.utils.i18n import t
from app.utils.log import get_logger

log = get_logger("comfy")

DEFAULT_URL = "http://127.0.0.1:8188"


class ComfyBridge(QObject):
    notify = Signal(str, str)          # уровень, текст -> тосты
    busy_changed = Signal(bool)        # у ComfyUI началась / закончилась работа (по очереди)
    _poll_result = Signal(bool, bool)  # online, busy (из потока опроса)
    _notify_thread = Signal(str, str)  # уведомление из рабочего потока

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._last_busy: bool | None = None
        self._polling = False
        self.online = False
        self._poll_result.connect(self._on_poll_result)
        self._notify_thread.connect(self.notify)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # --- настройки (читаются «вживую») ------------------------------------------------------
    @property
    def url(self) -> str:
        return (self.config.get("comfy.url", DEFAULT_URL) or DEFAULT_URL).strip().rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("comfy.enabled", True))

    # --- HTTP ---------------------------------------------------------------------------------
    def _get(self, path: str, timeout: float = 1.0) -> dict | None:
        try:
            r = requests.get(self.url + path, timeout=timeout)
            return r.json() if r.ok else None
        except Exception:
            return None

    def stats(self, timeout: float = 1.0) -> dict | None:
        return self._get("/system_stats", timeout)

    def queue_state(self, timeout: float = 1.0) -> tuple[bool, bool]:
        """(online, busy). busy — в очереди ComfyUI есть выполняющиеся или ожидающие задачи."""
        q = self._get("/queue", timeout)
        if q is None:
            return False, False
        return True, bool(q.get("queue_running") or q.get("queue_pending"))

    @staticmethod
    def vram_free(stats: dict | None) -> int:
        if not stats:
            return 0
        return sum(int(d.get("vram_free", 0) or 0) for d in stats.get("devices", []))

    def probe(self) -> str:
        """Для кнопки «Проверить»: offline | idle | busy."""
        online, busy = self.queue_state(2.0)
        return "offline" if not online else ("busy" if busy else "idle")

    # --- направление 1: освободить память ComfyUI перед LLM (вызывается из рабочего потока) -------
    def prepare_for_llm(self, cancel: threading.Event | None = None) -> None:
        if not self.enabled or not self.config.get("comfy.free_before_generate", True):
            return
        online, busy = self.queue_state(0.8)
        if not online:
            return
        if busy:
            self._notify_thread.emit("info", t("comfy_waiting"))
            deadline = time.time() + float(self.config.get("comfy.wait_busy_timeout", 600))
            while busy and time.time() < deadline and not (cancel and cancel.is_set()):
                time.sleep(1.0)
                online, busy = self.queue_state(1.0)
                if not online:
                    return
            if busy or (cancel and cancel.is_set()):
                if busy:
                    self._notify_thread.emit("warning", t("comfy_still_busy"))
                return
        before = self.vram_free(self.stats())
        try:
            requests.post(self.url + "/free", json={"unload_models": True, "free_memory": True}, timeout=3.0)
        except Exception as exc:
            log.warning("ComfyUI /free failed: %s", exc)
            return
        log.info("ComfyUI /free sent (vram_free before=%d MB)", before // 2**20)
        # ждём, пока ComfyUI реально выгрузит модели: VRAM растёт и стабилизируется
        t0, last, grew = time.time(), before, False
        while time.time() - t0 < 6.0 and not (cancel and cancel.is_set()):
            time.sleep(0.25)
            cur = self.vram_free(self.stats())
            if cur - before >= 256 * 2**20:
                grew = True
            if grew and abs(cur - last) < 32 * 2**20:
                last = cur
                break
            if not grew and time.time() - t0 > 1.5:
                break
            last = cur
        freed = max(0, last - before)
        if freed >= 256 * 2**20:
            self._notify_thread.emit("success", t("comfy_freed", gb=f"{freed / 2**30:.1f}"))
            log.info("ComfyUI freed %.2f GB VRAM", freed / 2**30)

    # --- направление 2: слежение за очередью ComfyUI ----------------------------------------------
    def apply_settings(self) -> None:
        """Перезапускает опрос по текущим настройкам (вызывать при старте и после диалога настроек)."""
        self._timer.stop()
        self._last_busy = None
        if self.enabled and self.config.get("comfy.unload_strata_on_run", True):
            self._timer.setInterval(int(float(self.config.get("comfy.poll_interval", 2)) * 1000))
            self._timer.start()

    def shutdown(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        if self._polling:
            return
        self._polling = True

        def work():
            try:
                online, busy = self.queue_state(1.0)
                self._poll_result.emit(online, busy)
            finally:
                self._polling = False

        threading.Thread(target=work, daemon=True).start()

    def _on_poll_result(self, online: bool, busy: bool) -> None:   # главный поток
        self.online = online
        if not online:
            self._last_busy = None
            return
        prev, self._last_busy = self._last_busy, busy
        if prev is None:          # первое наблюдение — просто запоминаем состояние
            return
        if busy != prev:
            log.info("ComfyUI queue busy=%s", busy)
            self.busy_changed.emit(busy)
