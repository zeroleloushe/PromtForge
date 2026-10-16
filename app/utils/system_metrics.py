"""RAM / VRAM без внешних зависимостей (ctypes, /proc, nvidia-smi)."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys

GB = 1024 ** 3


def ram_usage() -> tuple[float, float] | None:
    """(занято ГБ, всего ГБ) системной памяти."""
    try:
        if os.name == "nt":
            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMSTAT()
            st.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return (st.ullTotalPhys - st.ullAvailPhys) / GB, st.ullTotalPhys / GB
        info = {}
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                k, v = line.split(":", 1)
                info[k] = int(v.strip().split()[0]) * 1024
        total, avail = info["MemTotal"], info.get("MemAvailable", info.get("MemFree", 0))
        return (total - avail) / GB, total / GB
    except Exception:
        return None


def vram_usage() -> tuple[float, float] | None:
    """(занято ГБ, всего ГБ) первой NVIDIA GPU или None."""
    try:
        flags = 0x08000000 if os.name == "nt" else 0
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=flags,
        )
        if out.returncode != 0:
            return None
        used, total = out.stdout.strip().splitlines()[0].split(",")
        return float(used) / 1024, float(total) / 1024
    except Exception:
        return None
