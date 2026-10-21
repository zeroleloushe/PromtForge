"""Подключение к Strata. Единственный источник — глобальные настройки (config.json -> "strata").

Раньше у каждой модели-пресета (data/models/*.json) были свои api_base / model (Model ID). Из интерфейса
их убрали, но ключи остались в старых файлах пресетов и продолжали подставляться в запросы: при смене
адреса/порта Strata в глобальных настройках генерация всё равно шла на старый адрес пресета.
Теперь эти ключи игнорируются при загрузке и не используются при построении запросов.
"""
from __future__ import annotations

from app.strata.strata_api import StrataAPI

DEFAULT_API_BASE = "http://127.0.0.1:8080/v1"

# устаревшие поля подключения в пресетах моделей — при загрузке отбрасываются
LEGACY_PRESET_KEYS = frozenset({
    "api_base", "base_url", "api_url", "url", "host", "port", "endpoint", "api_key", "key",
    "backend", "provider", "model", "health_endpoint", "unload_endpoint",
})


def api_base(config) -> str:
    return str(config.get("strata.api_base", "") or "").strip() or DEFAULT_API_BASE


def make_api(config, timeout: float | None = None, stream_timeout: float | None = None) -> StrataAPI:
    return StrataAPI(api_base(config), config.get("strata.health_endpoint", "/health") or "/health",
                     config.get("generation.timeout", 120) if timeout is None else timeout,
                     config.get("strata.unload_endpoint", "") or "",
                     stream_timeout=stream_timeout)


def model_id(config, strata=None, api: StrataAPI | None = None) -> str:
    """Имя модели для поля "model": strata.model из настроек (необязательно) -> модель, которую
    отдаёт сама Strata (/v1/models) -> "" (поле не отправляется; llama.cpp-совместимым серверам не нужно)."""
    m = str(config.get("strata.model", "") or "").strip()
    if m:
        return m
    info = getattr(strata, "info", None) or {}
    m = str(info.get("model") or "").strip()
    if m:
        return m
    if api is not None:
        ids = api.list_models()
        if ids:
            return ids[0]
    return ""


def apply_model(body: dict, model: str) -> dict:
    if model:
        body["model"] = model
    else:
        body.pop("model", None)
    return body
