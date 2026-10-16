"""Стили: каждый стиль — отдельный JSON в data/styles. Можно создавать, править, импортировать."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.utils import paths
from app.utils.log import get_logger

log = get_logger("styles")

# Оформление встроенных стилей (если в JSON нет полей category/icon/tone)
DEFAULT_META = {
    "cinematic": ("Кинематографичный", "film", "blue"),
    "character": ("Персонаж", "user", "cyan"),
    "camera_motion": ("Камера и композиция", "camera", "teal"),
    "lighting_atmosphere": ("Освещение", "sun", "amber"),
    "art": ("Арт", "brush", "pink"),
    "postprocessing": ("Детали и текстуры", "wand", "violet"),
    "reference_consistency": ("Референсы", "link", "orange"),
    "base_prompt": ("Базовые", "layers", "slate"),
}
ICON_CHOICES = ["film", "user", "camera", "sun", "brush", "wand", "link", "layers", "sparkles", "image"]
TONE_CHOICES = ["blue", "cyan", "teal", "amber", "pink", "violet", "orange", "green", "slate"]
DEFAULT_CATEGORY = "Мои стили"


@dataclass
class Style:
    id: str
    name: str
    description: str = ""
    instructions: list[str] = field(default_factory=list)
    priority: int = 50
    enabled: bool = False
    category: str = ""
    icon: str = ""
    tone: str = ""
    custom: bool = False

    def __post_init__(self):
        meta = DEFAULT_META.get(self.id)
        if not self.category:
            self.category = meta[0] if meta else DEFAULT_CATEGORY
        if not self.icon:
            self.icon = meta[1] if meta else "sparkles"
        if not self.tone:
            self.tone = meta[2] if meta else "blue"

    @classmethod
    def from_dict(cls, d: dict) -> "Style":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        known.setdefault("id", slugify(d.get("name", "style")))
        known.setdefault("name", known["id"])
        ins = known.get("instructions", [])
        if isinstance(ins, str):
            ins = [ln.strip() for ln in ins.splitlines() if ln.strip()]
        known["instructions"] = [str(x) for x in ins]
        known["priority"] = int(known.get("priority", 50))
        return cls(**known)

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "name": self.name, "description": self.description,
            "instructions": self.instructions, "priority": self.priority, "enabled": self.enabled,
            "category": self.category, "icon": self.icon, "tone": self.tone,
        }
        if self.custom:
            d["custom"] = True
        return d


def slugify(text: str) -> str:
    s = re.sub(r"[^\w]+", "_", text.lower(), flags=re.UNICODE).strip("_")
    return s or "style"


class StyleManager:
    def __init__(self):
        self._styles: dict[str, Style] = {}
        self.reload()

    def reload(self) -> None:
        self._styles.clear()
        for f in sorted(paths.STYLES_DIR.glob("*.json")):
            try:
                s = Style.from_dict(json.loads(f.read_text(encoding="utf-8-sig")))
                self._styles[s.id] = s
            except Exception as exc:
                log.error("Не удалось загрузить стиль %s: %s", f.name, exc)

    @staticmethod
    def _rank(s: Style) -> int:
        order = list(DEFAULT_META)
        return order.index(s.id) if s.id in order else 100

    def all(self) -> list[Style]:
        return sorted(self._styles.values(), key=lambda s: (self._rank(s), s.name.lower()))

    def get(self, style_id: str) -> Style | None:
        return self._styles.get(style_id)

    def enabled(self) -> list[Style]:
        return [s for s in self._styles.values() if s.enabled]

    def categories(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        rank: dict[str, int] = {}
        for s in self._styles.values():
            counts[s.category] = counts.get(s.category, 0) + 1
            rank[s.category] = min(rank.get(s.category, 999), self._rank(s))
        return sorted(counts.items(), key=lambda kv: (rank[kv[0]], kv[0].lower()))

    # --- запись ---------------------------------------------------------
    def _file(self, style_id: str) -> Path:
        return paths.STYLES_DIR / f"{style_id}.json"

    def save(self, style: Style) -> None:
        self._styles[style.id] = style
        self._file(style.id).write_text(json.dumps(style.to_dict(), ensure_ascii=False, indent=4), encoding="utf-8")

    def set_enabled(self, style_id: str, enabled: bool) -> None:
        s = self._styles.get(style_id)
        if s and s.enabled != enabled:
            s.enabled = enabled
            self.save(s)

    def _unique_id(self, base: str) -> str:
        sid, n = base, 2
        while sid in self._styles:
            sid, n = f"{base}_{n}", n + 1
        return sid

    def create(self, data: dict) -> Style:
        data = dict(data)
        data["id"] = self._unique_id(slugify(data.get("id") or data.get("name", "style")))
        data["custom"] = True
        s = Style.from_dict(data)
        self.save(s)
        log.info("Style created: %s", s.id)
        return s

    def duplicate(self, style_id: str) -> Style | None:
        src = self._styles.get(style_id)
        if not src:
            return None
        d = src.to_dict()
        d["name"] = f"{src.name} (копия)"
        d["id"] = f"{src.id}_copy"
        d["enabled"] = False
        return self.create(d)

    def delete(self, style_id: str) -> bool:
        if style_id not in self._styles:
            return False
        del self._styles[style_id]
        try:
            self._file(style_id).unlink(missing_ok=True)
        except OSError as exc:
            log.error("Не удалось удалить файл стиля: %s", exc)
        return True

    # --- импорт / экспорт -----------------------------------------------
    def export_one(self, style_id: str, dest: Path) -> None:
        d = self._styles[style_id].to_dict()
        d.pop("enabled", None)
        Path(dest).write_text(json.dumps(d, ensure_ascii=False, indent=4), encoding="utf-8")

    def export_all(self, dest: Path) -> None:
        items = []
        for s in self._styles.values():
            d = s.to_dict()
            d.pop("enabled", None)
            items.append(d)
        Path(dest).write_text(json.dumps(items, ensure_ascii=False, indent=4), encoding="utf-8")

    def import_file(self, src: Path) -> list[Style]:
        """Принимает JSON одного стиля или список стилей. Возвращает созданные стили."""
        data = json.loads(Path(src).read_text(encoding="utf-8-sig"))
        items = data if isinstance(data, list) else [data]
        created = []
        for item in items:
            if not isinstance(item, dict) or "name" not in item:
                raise ValueError("В файле нет описания стиля (нужны поля name и instructions)")
            item = dict(item)
            item["enabled"] = False
            created.append(self.create(item))
        return created

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:8]
