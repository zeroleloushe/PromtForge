"""Запуск и остановка процесса Strata через subprocess.Popen (поддерживается .exe и .bat/.cmd-лаунчер)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.utils import paths
from app.utils.log import get_logger

log = get_logger("process")


def is_script(path: str | Path) -> bool:
    return Path(path).suffix.lower() in (".bat", ".cmd")


def build_command(exe: Path, arguments: list[str] | None, nt: bool | None = None) -> list[str]:
    """Для .bat/.cmd на Windows запускаем через cmd.exe (call), иначе — напрямую."""
    nt = (os.name == "nt") if nt is None else nt
    args = list(arguments or [])
    if nt and is_script(exe):
        return ["cmd.exe", "/d", "/c", "call", str(exe), *args]
    return [str(exe), *args]


class StrataProcess:
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._script = False
        self._logfile = None
        self.started_by_us = False

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def start(self, executable: str, arguments: list[str] | None = None) -> None:
        exe = Path(executable)
        if not executable or not exe.is_file():
            raise FileNotFoundError(executable or "")
        paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self._logfile = open(paths.STRATA_LOG_FILE, "ab")
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        self._script = is_script(exe)
        self._proc = subprocess.Popen(
            build_command(exe, arguments), cwd=str(exe.parent), stdin=subprocess.DEVNULL,
            stdout=self._logfile, stderr=subprocess.STDOUT, creationflags=flags,
        )
        self.started_by_us = True
        log.info("Strata process started pid=%s", self._proc.pid)

    def poll(self) -> int | None:
        """None — процесс жив; число — код выхода; None также если процесса нет (см. is_running)."""
        return self._proc.poll() if self._proc else None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def has_process(self) -> bool:
        return self._proc is not None

    def stop(self, timeout: float = 10.0) -> bool:
        proc = self._proc
        if proc is None:
            return True
        if proc.poll() is None and self._script:
            # .bat запускает сервер дочерним процессом — завершаем всё дерево, а не только cmd.exe
            self._kill_tree(proc)
        elif proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill_tree(proc)
            except OSError as exc:
                log.warning("terminate failed: %s", exc)
        ok = proc.poll() is not None
        log.info("Strata process stopped=%s", ok)
        if ok:
            self._proc = None
            self.started_by_us = False
            if self._logfile:
                try:
                    self._logfile.close()
                except OSError:
                    pass
                self._logfile = None
        return ok

    @staticmethod
    def kill_listener(port: int) -> bool:
        """Windows: убивает процесс (с деревом), слушающий порт. Нужен, если .bat запустил сервер отдельным окном/процессом."""
        if os.name != "nt" or not port:
            return False
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
                                 creationflags=0x08000000, timeout=10).stdout
            pids = set()
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].rsplit(":", 1)[-1] == str(port):
                    pids.add(parts[4])
            for pid in pids:
                subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True, creationflags=0x08000000, timeout=15)
            return bool(pids)
        except Exception as exc:
            log.warning("kill_listener failed: %s", exc)
            return False

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                               creationflags=0x08000000, timeout=15)
            else:
                proc.kill()
            proc.wait(timeout=5)
        except Exception as exc:
            log.warning("kill failed: %s", exc)

    @staticmethod
    def log_tail(lines: int = 12) -> str:
        try:
            data = paths.STRATA_LOG_FILE.read_bytes()[-4000:].decode("utf-8", errors="replace")
            return "\n".join(data.strip().splitlines()[-lines:])
        except OSError:
            return ""
