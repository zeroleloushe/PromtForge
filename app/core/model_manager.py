"""Модели: каждая — отдельный JSON в data/models. Добавление модели не требует правки кода."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.core.connection import LEGACY_PRESET_KEYS
from app.utils import paths
from app.utils.log import get_logger

log = get_logger("models")


@dataclass
class Model:
    id: str
    name: str
    system_prompt: str = ""
    system_prompt_file: str = ""   # .md / .txt вместо текста system_prompt
    temperature: float = 1.0
    max_tokens: int = 32000
    enabled: bool = True
    supports_vision: bool = False
    rules: list[str] = field(default_factory=list)
    order: int = 100

    @classmethod
    def from_dict(cls, d: dict) -> "Model":
        # подключение берётся только из глобальных настроек; старые api_base/model и т.п. игнорируются
        # (из файла они уйдут при следующем сохранении модели — пресет пользователя не удаляется)
        stale = sorted(k for k in d if k in LEGACY_PRESET_KEYS)
        if stale:
            log.info("Model %s: ignoring legacy connection keys %s", d.get("id") or d.get("name"), stale)
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        known.setdefault("id", slugify(d.get("name", "model")))
        known.setdefault("name", known["id"])
        m = cls(**known)
        m.temperature = float(m.temperature)
        m.max_tokens = int(m.max_tokens)
        return m

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["rules"]:
            d.pop("rules")
        if not d.get("system_prompt_file"):
            d.pop("system_prompt_file", None)
        return d


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s or "model"


class ModelManager:
    def __init__(self):
        self._models: dict[str, Model] = {}
        self.reload()

    def reload(self) -> None:
        self._models.clear()
        for f in sorted(paths.MODELS_DIR.glob("*.json")):
            try:
                m = Model.from_dict(json.loads(f.read_text(encoding="utf-8-sig")))
                self._models[m.id] = m
            except Exception as exc:
                log.error("Не удалось загрузить модель %s: %s", f.name, exc)

    # --- чтение ---------------------------------------------------------
    def all(self) -> list[Model]:
        return sorted(self._models.values(), key=lambda m: (m.order, m.name.lower()))

    def get(self, model_id: str) -> Model | None:
        return self._models.get(model_id)

    # --- запись ---------------------------------------------------------
    def _file(self, model_id: str) -> Path:
        return paths.MODELS_DIR / f"{model_id}.json"

    def save(self, model: Model) -> None:
        self._models[model.id] = model
        self._file(model.id).write_text(json.dumps(model.to_dict(), ensure_ascii=False, indent=4), encoding="utf-8")
        log.info("Model saved: %s", model.id)

    def create(self, data: dict) -> Model:
        data = dict(data)
        base = slugify(data.get("id") or data.get("name", "model"))
        mid, n = base, 2
        while mid in self._models:
            mid, n = f"{base}_{n}", n + 1
        data["id"] = mid
        data.setdefault("order", max([m.order for m in self._models.values() if m.order < 100] + [0]) + 1)
        model = Model.from_dict(data)
        self.save(model)
        return model

    def delete(self, model_id: str) -> bool:
        if model_id not in self._models:
            return False
        del self._models[model_id]
        try:
            self._file(model_id).unlink(missing_ok=True)
        except OSError as exc:
            log.error("Не удалось удалить файл модели: %s", exc)
        log.info("Model deleted: %s", model_id)
        return True

    def export_to(self, model_id: str, dest: Path) -> None:
        dest.write_text(json.dumps(self._models[model_id].to_dict(), ensure_ascii=False, indent=4), encoding="utf-8")

    def import_from(self, src: Path) -> Model:
        data = json.loads(Path(src).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or "name" not in data:
            raise ValueError("Файл не похож на конфигурацию модели")
        return self.create(data)
