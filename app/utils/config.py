"""Конфигурация приложения (config/config.json) с значениями по умолчанию."""
from __future__ import annotations

import copy
import json
import threading
from typing import Any

from app.utils import paths
from app.utils.log import get_logger

log = get_logger("config")

DEFAULTS: dict[str, Any] = {
    "comfy": {
        "enabled": True,
        "url": "http://127.0.0.1:8188",
        "free_before_generate": True,
        "unload_strata_on_run": True,
        "poll_interval": 2,
        "wait_busy_timeout": 600,
    },
    "strata": {
        "executable": r"C:\STRATA_AI\Strata\run-iq2_xs.bat",
        "launcher_set": False,
        "arguments": [],
        "api_base": "http://127.0.0.1:8080/v1",
        "health_endpoint": "/health",
        "unload_endpoint": "",
        "start_timeout": 180,
        "auto_start": True,
        "auto_unload": False,
        "auto_unload_delay": 300,
        "auto_shutdown": False,
        "after_stop": "unload",
    },
    "generation": {
        "temperature": 1.0,
        "max_tokens": 32000,
        "streaming": True,
        "timeout": 120,            # обычные (не потоковые) запросы
        "stream_timeout": 600,     # пауза между чанками потока: длинный thinking не рвётся
        "thinking_level": "high",  # off | low | med | high
        "enable_thinking": True,
        "show_reasoning": False,
    },
    "ui": {
        "theme": "dark",
        "language": "ru",
        "animations": True,
        "native_titlebar": False,
        "last_model": "",
        "model_card_open": False,
        "styles_open": True,
        "right_open": True,
        "translate_lang": "Russian",
    },
    "storage": {
        "data_dir": "data",
        "history_dir": "data/history",
        "references_dir": "data/references",
    },
    "chat": {
        "tools_enabled": True,
        "approval": "ask",            # ask — спрашивать перед записью/запуском; auto — выполнять сразу
        "max_steps": 12,
        "system_prompt": "",          # пусто — стандартный системный промт
        "system_prompt_file": "",     # .md / .txt вместо текста; пусто — не используется
        "workspace": "",              # пусто — папка workspace рядом с приложением
        "search_provider": "duckduckgo",
        "searxng_url": "",
        "brave_key": "",
        "allow_private_net": False,
        "disabled_tools": [],
        "show_reasoning": False,
        "last_session": 0,
    },
    "options": {
        "enhance_with_llm": True,
        "add_negative_prompt": True,
        "use_references": True,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path=None):
        self._path = path or paths.CONFIG_FILE
        self._lock = threading.RLock()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()
        paths.apply_storage(self._data.get("storage", {}))

    def load(self) -> None:
        with self._lock:
            try:
                if self._path.exists():
                    raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
                    self._data = _merge(DEFAULTS, raw)
            except Exception as exc:  # битый JSON не должен ронять приложение
                log.error("Не удалось прочитать config.json: %s", exc)

    def save(self) -> None:
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=4), encoding="utf-8")
                tmp.replace(self._path)
            except Exception as exc:
                log.error("Не удалось сохранить config.json: %s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            cur: Any = self._data
            for part in key.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return default
                cur = cur[part]
            return copy.deepcopy(cur)

    def set(self, key: str, value: Any, save: bool = True) -> None:
        with self._lock:
            parts = key.split(".")
            cur = self._data
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value
        if save:
            self.save()
