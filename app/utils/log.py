"""Логирование в logs/promptforge.log. Секреты (ключи, токены) маскируются."""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler

from app.utils import paths

_SECRET = re.compile(r"(?i)(api[_-]?key|authorization|bearer|token|secret)(['\"]?\s*[:=]?\s*['\"]?)([A-Za-z0-9_\-\.]{6,})")


class _MaskSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            masked = _SECRET.sub(r"\1\2***", msg)
            if masked != msg:
                record.msg, record.args = masked, ()
        except Exception:
            pass
        return True


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("promptforge")
    if root.handlers:
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(paths.LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(_MaskSecrets())
        root.addHandler(fh)
    except Exception:
        pass
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.addFilter(_MaskSecrets())
        root.addHandler(sh)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"promptforge.{name}")
