"""Чтение системного промта из файла .md / .txt.

Файл не копируется в конфиг: при генерации он читается с диска и подставляется вместо текста поля.
"""
from __future__ import annotations

from pathlib import Path

from app.utils import paths
from app.utils.log import get_logger

log = get_logger("prompt_file")

ALLOWED_EXT = {".md", ".txt", ".markdown"}
MAX_CHARS = 1_000_000
PREVIEW_CHARS = 80_000

_WARN = {
    "missing": "warn_sys_file_missing",
    "empty": "warn_sys_file_empty",
    "read": "warn_sys_file_read",
    "ext": "warn_sys_file_ext",
    "big": "warn_sys_file_big",
}


class PromptFileError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def is_prompt_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in ALLOWED_EXT


def locate(path: str | Path) -> Path:
    """Абсолютный путь, если файл на месте; иначе исходный (для сообщения об ошибке)."""
    p = Path(path)
    if p.is_file():
        return p
    if not p.is_absolute():
        alt = paths.ROOT / p
        if alt.is_file():
            return alt
    return p


def read_prompt_file(path: str | Path) -> str:
    p = Path(path)
    if p.suffix.lower() not in ALLOWED_EXT:
        raise PromptFileError("ext")
    found = locate(p)
    if not found.is_file():
        raise PromptFileError("missing")
    try:
        raw = found.read_bytes()
    except OSError as exc:
        log.warning("Не удалось прочитать файл промта %s: %s", found, exc)
        raise PromptFileError("read") from exc
    text = _decode(raw)
    if len(text) > MAX_CHARS:
        log.warning("Файл промта слишком большой (%d симв.): %s", len(text), found)
        raise PromptFileError("big")
    if not text.strip():
        raise PromptFileError("empty")
    return text


def resolve_system_text(inline: str, file_path: str) -> tuple[str, str]:
    """Текст системного промта и ключ предупреждения (пустой, если всё в порядке).

    Прикреплённый файл заменяет текст поля. Если файл недоступен — остаётся текст поля.
    """
    inline = (inline or "").strip()
    path = (file_path or "").strip()
    if not path:
        return inline, ""
    try:
        return read_prompt_file(path).strip(), ""
    except PromptFileError as exc:
        log.warning("Системный промт из файла не использован (%s): %s", exc.code, path)
        return inline, _WARN.get(exc.code, "warn_sys_file_read")


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
