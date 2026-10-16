"""MiniMax H3: сетка длины клипа. length = 17·k + 5 кадров при 24 fps (277 → 11.54 с, 294 → 12.25 с)."""
from __future__ import annotations

import re

FPS = 24
STEP = 17
OFFSET = 5
MIN_K, MAX_K = 1, 40          # 22 кадра (0.92 с) … 685 кадров (28.5 с)


def frames(k: int) -> int:
    return STEP * k + OFFSET


def seconds(n_frames: int) -> float:
    return n_frames / FPS


def is_valid(n_frames: int) -> bool:
    return n_frames >= OFFSET + STEP and (n_frames - OFFSET) % STEP == 0


def nearest(sec: float) -> tuple[int, int]:
    """Ближайшие допустимые длины снизу и сверху (кадры) для желаемой длительности."""
    target = max(sec, 0.0) * FPS
    k_lo = max(MIN_K, min(MAX_K, int((target - OFFSET) // STEP)))
    lo = frames(k_lo)
    hi = lo if lo >= target else frames(min(MAX_K, k_lo + 1))
    return lo, hi


def fmt_time(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:06.3f}"


def length_instruction(n_frames: int) -> str:
    sec = seconds(n_frames)
    return (f"TARGET LENGTH (MiniMax H3): length {n_frames} frames at {FPS} fps = {sec:.2f} s. "
            f"State this length explicitly (\"length {n_frames}\", {sec:.2f} s) and keep every shot timecode "
            f"between 00:00.000 and {fmt_time(sec)}.")


def is_h3(model) -> bool:
    key = f"{getattr(model, 'id', '')} {getattr(model, 'name', '')}".lower()
    return "minimax" in key or "h3" in key


def mentions_length(text: str, n_frames: int) -> bool:
    """Проверка ответа: упомянута ли выбранная длина (кадры или секунды)."""
    sec = seconds(n_frames)
    if re.search(rf"(?<!\d){n_frames}(?!\d)", text):
        return True
    return f"{sec:.2f}" in text or f"{sec:.2f}".rstrip("0").rstrip(".") + " s" in text
