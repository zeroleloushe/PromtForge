from __future__ import annotations

from datetime import datetime


def fmt_date(value: str) -> str:
    """'2026-10-04 21:42:00' -> '04.10.2026 21:42'."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt).strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            continue
    return value or ""


def snippet(text: str, n: int = 90) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= n else one[: n - 1] + "…"
