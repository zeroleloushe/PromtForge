"""Пути проекта. Все каталоги данных вычисляются здесь, а не в остальном коде."""
from __future__ import annotations

from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[2]

CONFIG_DIR: Path = ROOT / "config"
CONFIG_FILE: Path = CONFIG_DIR / "config.json"
LOGS_DIR: Path = ROOT / "logs"
LOG_FILE: Path = LOGS_DIR / "promptforge.log"
STRATA_LOG_FILE: Path = LOGS_DIR / "strata_process.log"

DATA_DIR: Path = ROOT / "data"
MODELS_DIR: Path = DATA_DIR / "models"
STYLES_DIR: Path = DATA_DIR / "styles"
REFERENCES_DIR: Path = DATA_DIR / "references"
HISTORY_DIR: Path = DATA_DIR / "history"
DB_PATH: Path = DATA_DIR / "promptforge.db"


def resolve(value: str | Path) -> Path:
    """Относительные пути из конфига считаются от корня проекта."""
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def apply_storage(storage: dict) -> None:
    """Применяет секцию `storage` из config.json."""
    global DATA_DIR, MODELS_DIR, STYLES_DIR, REFERENCES_DIR, HISTORY_DIR, DB_PATH
    if storage.get("data_dir"):
        DATA_DIR = resolve(storage["data_dir"])
        MODELS_DIR = DATA_DIR / "models"
        STYLES_DIR = DATA_DIR / "styles"
        DB_PATH = DATA_DIR / "promptforge.db"
        REFERENCES_DIR = DATA_DIR / "references"
        HISTORY_DIR = DATA_DIR / "history"
    if storage.get("references_dir"):
        REFERENCES_DIR = resolve(storage["references_dir"])
    if storage.get("history_dir"):
        HISTORY_DIR = resolve(storage["history_dir"])
    ensure_dirs()


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, LOGS_DIR, DATA_DIR, MODELS_DIR, STYLES_DIR, REFERENCES_DIR, HISTORY_DIR):
        d.mkdir(parents=True, exist_ok=True)
