"""Референсы (изображения, видео и аудио) текущей сессии."""
from __future__ import annotations

import base64
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from app.utils import paths
from app.utils.log import get_logger

log = get_logger("references")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
MAX_SIDE = 1280  # для отправки в vision-модель


@dataclass
class Reference:
    path: str
    type: str  # image | video | audio
    name: str          # название (по умолчанию — имя файла); его можно переименовать, файл при этом не меняется
    created: str
    description: str = ""   # подпись: что на изображении / в видео / в аудио — уходит модели вместе с названием

    def exists(self) -> bool:
        return Path(self.path).is_file()

    def to_dict(self) -> dict:
        return asdict(self)


def classify(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return None


class ReferenceManager:
    def __init__(self, history=None):
        self._items: list[Reference] = []
        self._history = history  # HistoryManager — чтобы не удалять файлы, нужные истории

    def items(self) -> list[Reference]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()

    def add(self, src: str | Path) -> Reference:
        src = Path(src)
        kind = classify(src)
        if kind is None:
            raise ValueError(f"Формат не поддерживается: {src.suffix or src.name}")
        if not src.is_file():
            raise FileNotFoundError(str(src))
        dest = src
        refs_dir = paths.REFERENCES_DIR
        try:
            inside = refs_dir.resolve() in src.resolve().parents
        except OSError:
            inside = False
        if not inside:
            dest = refs_dir / src.name
            if dest.exists():
                dest = refs_dir / f"{src.stem}_{uuid.uuid4().hex[:6]}{src.suffix}"
            shutil.copy2(src, dest)
        ref = Reference(str(dest), kind, src.name, datetime.now().isoformat(timespec="seconds"))
        self._items.append(ref)
        log.info("Reference added: %s (%s)", ref.name, ref.type)
        return ref

    def add_image_data(self, image, name: str = "clipboard.png") -> Reference:
        """Сохраняет QImage (например, из буфера обмена) как референс."""
        dest = paths.REFERENCES_DIR / f"{Path(name).stem}_{uuid.uuid4().hex[:6]}.png"
        if not image.save(str(dest), "PNG"):
            raise OSError("Не удалось сохранить изображение")
        return self.add(dest)

    def remove(self, index: int) -> None:
        if 0 <= index < len(self._items):
            ref = self._items.pop(index)
            self._purge(ref)
            log.info("Reference removed: %s", ref.name)

    def set_caption(self, index: int, name: str, description: str) -> None:
        """Меняет название и описание референса (сам файл не трогается)."""
        if 0 <= index < len(self._items):
            ref = self._items[index]
            ref.name = name.strip() or ref.name
            ref.description = description.strip()

    def replace(self, index: int, src: str | Path) -> Reference:
        old = self._items[index]
        new = self.add(src)
        self._items.pop()  # add() дописал в конец — переносим на нужное место
        new.description = old.description   # подпись сохраняется при замене файла
        self._items[index] = new
        if old.path != new.path:
            self._purge(old)
        return new

    def _purge(self, ref: Reference) -> None:
        """Файл копии удаляется, только если он лежит в references и не нужен истории."""
        try:
            p = Path(ref.path)
            if paths.REFERENCES_DIR.resolve() not in p.resolve().parents or not p.exists():
                return
            if any(r.path == ref.path for r in self._items):
                return
            if self._history and self._history.is_reference_used(ref.path):
                return
            p.unlink()
        except OSError as exc:
            log.warning("Не удалось удалить файл референса: %s", exc)

    def set_items(self, refs: list[dict]) -> list[str]:
        """Восстанавливает референсы из истории. Возвращает имена пропавших файлов."""
        self._items.clear()
        missing = []
        for r in refs:
            ref = Reference(r.get("path", ""), r.get("type", "image"), r.get("name", ""), r.get("created", ""),
                            r.get("description", "") or "")
            if ref.exists():
                self._items.append(ref)
            else:
                missing.append(ref.name or ref.path)
        return missing

    # --- для vision API --------------------------------------------------
    @staticmethod
    def image_data_url(ref: Reference) -> str:
        """Data-URL изображения; крупные картинки уменьшаются, чтобы не раздувать запрос."""
        data = None
        mime = "image/jpeg"
        try:
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
            from PySide6.QtGui import QImage

            img = QImage(ref.path)
            if not img.isNull():
                if max(img.width(), img.height()) > MAX_SIDE:
                    img = img.scaled(MAX_SIDE, MAX_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                buf = QByteArray()
                dev = QBuffer(buf)
                dev.open(QIODevice.WriteOnly)
                img.save(dev, "JPEG", 90)
                data = bytes(buf)
        except Exception as exc:
            log.warning("Не удалось подготовить изображение: %s", exc)
        if data is None:
            data = Path(ref.path).read_bytes()
            mime = {"png": "image/png", "webp": "image/webp"}.get(Path(ref.path).suffix.lower().lstrip("."), "image/jpeg")
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
