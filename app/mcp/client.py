"""Минимальный MCP-клиент (JSON-RPC 2.0) без внешних зависимостей, кроме requests.

Транспорты: stdio (запуск процесса), Streamable HTTP и устаревший HTTP+SSE. Поддержаны только инструменты (tools):
initialize → tools/list → tools/call. Серверные запросы (ping, roots/list) отвечаются, sampling/elicitation — отклоняются.
"""
from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests

from app.utils.log import get_logger

log = get_logger("mcp")

PROTOCOL = "2025-06-18"
CLIENT_INFO = {"name": "PromptForge", "version": "1.0"}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class McpError(Exception):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


@dataclass
class McpTool:
    name: str
    description: str = ""
    schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    title: str = ""
    read_only: bool | None = None       # annotations.readOnlyHint
    destructive: bool | None = None     # annotations.destructiveHint

    @classmethod
    def from_dict(cls, d: dict) -> "McpTool":
        ann = d.get("annotations") or {}
        schema = d.get("inputSchema") or {"type": "object", "properties": {}}
        if not isinstance(schema, dict) or schema.get("type") not in (None, "object"):
            schema = {"type": "object", "properties": {}}
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        return cls(d.get("name", ""), d.get("description") or d.get("title") or "", schema,
                   d.get("title") or (ann.get("title") or ""), ann.get("readOnlyHint"), ann.get("destructiveHint"))


def result_to_text(res: dict) -> tuple[str, bool]:
    """Результат tools/call -> (текст, is_error)."""
    parts: list[str] = []
    for c in res.get("content") or []:
        kind = c.get("type")
        if kind == "text":
            parts.append(str(c.get("text", "")))
        elif kind == "image":
            parts.append(f"[изображение {c.get('mimeType', '')}, ~{len(c.get('data', '')) * 3 // 4 // 1024} КБ]")
        elif kind == "audio":
            parts.append(f"[аудио {c.get('mimeType', '')}]")
        elif kind == "resource":
            r = c.get("resource") or {}
            parts.append(str(r.get("text") or f"[ресурс {r.get('uri', '')}]"))
        elif kind == "resource_link":
            parts.append(f"[ссылка на ресурс: {c.get('uri', '')}]")
    if not parts and res.get("structuredContent") is not None:
        parts.append(json.dumps(res["structuredContent"], ensure_ascii=False, indent=2))
    return "\n".join(parts).strip(), bool(res.get("isError"))


def kill_tree(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is not None:
            return
        if os.name == "nt":      # npx/uvx запускают дочерние процессы — гасим всё дерево
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=NO_WINDOW, timeout=15)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        proc.wait(timeout=5)
    except Exception as exc:  # noqa: BLE001
        log.warning("kill_tree: %s", exc)


class _Pending:
    __slots__ = ("event", "result", "error")

    def __init__(self):
        self.event = threading.Event()
        self.result: dict = {}
        self.error: McpError | None = None


# ============================================================================ базовый RPC
class _Rpc:
    def __init__(self, on_notify=None):
        self._ids = itertools.count(1)
        self._pending: dict[int, _Pending] = {}
        self._lock = threading.Lock()
        self.on_notify = on_notify
        self.closed = False
        self.death = ""
        self.protocol = PROTOCOL
        self.negotiated = False
        self.stderr_tail = ""

    # --- приём ----------------------------------------------------------------
    def _dispatch(self, msg) -> None:
        if isinstance(msg, list):
            for m in msg:
                self._dispatch(m)
            return
        if not isinstance(msg, dict):
            return
        if "method" in msg:
            if "id" in msg:
                self._answer(msg)
            elif self.on_notify:
                try:
                    self.on_notify(msg["method"], msg.get("params") or {})
                except Exception:  # noqa: BLE001
                    log.exception("on_notify")
            return
        with self._lock:
            p = self._pending.get(msg.get("id"))
        if p is None:
            return
        if msg.get("error"):
            err = msg["error"] if isinstance(msg["error"], dict) else {"message": str(msg["error"])}
            p.error = McpError(str(err.get("message", "ошибка сервера")), int(err.get("code", 0) or 0))
        else:
            p.result = msg.get("result") or {}
        p.event.set()

    def _answer(self, msg: dict) -> None:
        method = msg["method"]
        if method == "ping":
            body = {"result": {}}
        elif method == "roots/list":
            body = {"result": {"roots": []}}
        else:
            body = {"error": {"code": -32601, "message": "PromptForge не поддерживает этот запрос сервера"}}
        try:
            self._send_raw({"jsonrpc": "2.0", "id": msg["id"], **body})
        except Exception as exc:  # noqa: BLE001
            log.warning("answer to server request failed: %s", exc)

    def _fail_all(self, reason: str) -> None:
        self.closed = True
        self.death = self.death or reason
        with self._lock:
            pend = list(self._pending.values())
        for p in pend:
            if not p.event.is_set():
                p.error = McpError(self.death)
                p.event.set()

    # --- отправка (реализуют транспорты) -----------------------------------------
    def _send_raw(self, msg: dict) -> None:
        raise NotImplementedError

    def _send_request(self, msg: dict, rid: int) -> None:
        self._send_raw(msg)

    def start(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        self.closed = True

    # --- публичное ----------------------------------------------------------------
    def notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        try:
            self._send_raw(msg)
        except Exception as exc:  # noqa: BLE001
            log.warning("notify %s failed: %s", method, exc)

    def request(self, method: str, params: dict | None = None, timeout: float = 60.0, cancel=None) -> dict:
        if self.closed:
            raise McpError(self.death or "Соединение закрыто")
        rid = next(self._ids)
        p = _Pending()
        with self._lock:
            self._pending[rid] = p
        try:
            self._send_request({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, rid)
            end = time.monotonic() + timeout
            while not p.event.wait(0.2):
                if self.closed:
                    raise McpError(self.death or "Соединение закрыто")
                if cancel is not None and cancel.is_set():
                    self.notify("notifications/cancelled", {"requestId": rid, "reason": "cancelled by user"})
                    raise McpError("Отменено", -1)
                if time.monotonic() > end:
                    self.notify("notifications/cancelled", {"requestId": rid, "reason": "timeout"})
                    raise McpError(f"Сервер не ответил за {int(timeout)} с", -2)
            if p.error:
                raise p.error
            return p.result
        finally:
            with self._lock:
                self._pending.pop(rid, None)


# ============================================================================ stdio
class StdioRpc(_Rpc):
    def __init__(self, command: str, args: list[str], env: dict[str, str], cwd: str | None, on_notify=None):
        super().__init__(on_notify)
        self.command, self.args, self.env, self.cwd = command, args, env, cwd
        self.proc: subprocess.Popen | None = None
        self._wlock = threading.Lock()

    def start(self) -> None:
        full_env = {**os.environ, **{k: str(v) for k, v in self.env.items()}}
        full_env.setdefault("PYTHONIOENCODING", "utf-8")
        exe = shutil.which(self.command, path=full_env.get("PATH")) or self.command
        try:
            self.proc = subprocess.Popen(
                [exe, *self.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=full_env, cwd=self.cwd or None, creationflags=NO_WINDOW, bufsize=0)
        except FileNotFoundError as exc:
            raise McpError(f"Не найдена команда «{self.command}»") from exc
        except OSError as exc:
            raise McpError(f"Не удалось запустить «{self.command}»: {exc}") from exc
        threading.Thread(target=self._read_out, daemon=True, name="mcp-out").start()
        threading.Thread(target=self._read_err, daemon=True, name="mcp-err").start()

    def _read_out(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self.stderr_tail = (self.stderr_tail + "\n" + line)[-3000:]   # мусор в stdout — в диагностику
                    continue
                self._dispatch(msg)
        except Exception as exc:  # noqa: BLE001
            log.debug("stdout reader: %s", exc)
        code = proc.poll()
        if code is None:
            try:
                code = proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                code = None
        tail = self.stderr_tail.strip().splitlines()[-4:]
        self._fail_all(f"Сервер завершился (код {code})" + (": " + " | ".join(tail) if tail else ""))

    def _read_err(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stderr is not None
        try:
            for raw in iter(proc.stderr.readline, b""):
                self.stderr_tail = (self.stderr_tail + "\n" + raw.decode("utf-8", errors="replace").rstrip())[-3000:]
        except Exception:  # noqa: BLE001
            pass

    def _send_raw(self, msg: dict) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise McpError(self.death or "Процесс сервера не запущен")
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._wlock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise McpError(f"Не удалось отправить сообщение серверу: {exc}") from exc

    def close(self) -> None:
        super().close()
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
        if proc.poll() is None:
            kill_tree(proc)


# ============================================================================ HTTP
def _iter_sse(resp: requests.Response):
    event, data = "message", []
    for raw in resp.iter_lines(decode_unicode=False):
        line = raw.decode("utf-8", errors="replace")
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:][1:] if line[5:6] == " " else line[5:])
    if data:
        yield event, "\n".join(data)


class HttpRpc(_Rpc):
    """Streamable HTTP: каждое сообщение — POST; ответ приходит JSON-ом или SSE-потоком."""

    def __init__(self, url: str, headers: dict[str, str], on_notify=None):
        super().__init__(on_notify)
        self.url, self.headers = url, dict(headers or {})
        self.session_id = ""
        self._http = requests.Session()
        self._inflight: set[requests.Response] = set()

    def start(self) -> None:
        pass

    def _hdrs(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.negotiated:
            h["MCP-Protocol-Version"] = self.protocol
        return h

    def _post(self, msg: dict, rid: int | None) -> None:
        try:
            resp = self._http.post(self.url, data=json.dumps(msg, ensure_ascii=False).encode("utf-8"),
                                   headers=self._hdrs(), stream=True, timeout=(10, 600))
        except requests.RequestException as exc:
            self._post_failed(rid, McpError(f"Нет соединения с MCP-сервером: {exc}"))
            return
        self._inflight.add(resp)
        try:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            if resp.status_code in (202, 204):
                return
            if resp.status_code >= 400:
                body = resp.text[:300] if resp.status_code != 404 or not self.session_id else "сессия сервера истекла"
                self._post_failed(rid, McpError(f"HTTP {resp.status_code}: {body}", resp.status_code))
                return
            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                for _ev, data in _iter_sse(resp):
                    try:
                        self._dispatch(json.loads(data))
                    except json.JSONDecodeError:
                        continue
            else:
                try:
                    self._dispatch(resp.json())
                except ValueError:
                    self._post_failed(rid, McpError("Сервер вернул не-JSON ответ"))
        except requests.RequestException as exc:
            if not self.closed:
                self._post_failed(rid, McpError(f"Соединение прервано: {exc}"))
        finally:
            self._inflight.discard(resp)
            resp.close()

    def _post_failed(self, rid: int | None, err: McpError) -> None:
        if rid is None:
            log.warning("MCP POST failed: %s", err)
            return
        with self._lock:
            p = self._pending.get(rid)
        if p and not p.event.is_set():
            p.error = err
            p.event.set()

    def _send_raw(self, msg: dict) -> None:
        threading.Thread(target=self._post, args=(msg, None), daemon=True).start()

    def _send_request(self, msg: dict, rid: int) -> None:
        threading.Thread(target=self._post, args=(msg, rid), daemon=True, name="mcp-http").start()

    def close(self) -> None:
        super().close()
        for r in list(self._inflight):
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass
        if self.session_id:
            try:
                self._http.delete(self.url, headers=self._hdrs(), timeout=3)
            except requests.RequestException:
                pass


class SseRpc(_Rpc):
    """Устаревший HTTP+SSE: GET-поток для ответов, POST на endpoint из первого события."""

    def __init__(self, url: str, headers: dict[str, str], on_notify=None):
        super().__init__(on_notify)
        self.url, self.headers = url, dict(headers or {})
        self._http = requests.Session()
        self._endpoint = ""
        self._ready = threading.Event()
        self._resp: requests.Response | None = None
        self._err: McpError | None = None

    def start(self) -> None:
        threading.Thread(target=self._stream, daemon=True, name="mcp-sse").start()
        if not self._ready.wait(15):
            self.close()
            raise self._err or McpError("SSE-сервер не прислал endpoint за 15 с")
        if self._err:
            raise self._err

    def _stream(self) -> None:
        try:
            resp = self._resp = self._http.get(self.url, headers={"Accept": "text/event-stream", **self.headers},
                                               stream=True, timeout=(10, None))
            if resp.status_code >= 400:
                self._err = McpError(f"HTTP {resp.status_code}", resp.status_code)
                self._ready.set()
                return
            for ev, data in _iter_sse(resp):
                if ev == "endpoint":
                    self._endpoint = urljoin(self.url, data.strip())
                    self._ready.set()
                elif ev == "message":
                    try:
                        self._dispatch(json.loads(data))
                    except json.JSONDecodeError:
                        continue
        except requests.RequestException as exc:
            self._err = self._err or McpError(f"Нет соединения с MCP-сервером: {exc}")
        self._ready.set()
        self._fail_all("SSE-поток сервера закрыт")

    def _send_raw(self, msg: dict) -> None:
        try:
            r = self._http.post(self._endpoint, data=json.dumps(msg, ensure_ascii=False).encode("utf-8"),
                                headers={"Content-Type": "application/json", **self.headers}, timeout=30)
        except requests.RequestException as exc:
            raise McpError(f"Не удалось отправить сообщение: {exc}") from exc
        if r.status_code >= 400:
            raise McpError(f"HTTP {r.status_code}: {r.text[:200]}", r.status_code)

    def close(self) -> None:
        super().close()
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:  # noqa: BLE001
                pass


# ============================================================================ клиент
class McpClient:
    def __init__(self, name: str, spec: dict, on_tools_changed=None):
        self.name, self.spec = name, spec
        self.tools: list[McpTool] = []
        self.server_info: dict = {}
        self.instructions = ""
        self._rpc: _Rpc | None = None
        self._on_tools_changed = on_tools_changed

    @property
    def stderr_tail(self) -> str:
        return self._rpc.stderr_tail if self._rpc else ""

    def _notify(self, method: str, params: dict) -> None:
        if method == "notifications/tools/list_changed" and self._on_tools_changed:
            self._on_tools_changed()

    def _make(self, kind: str) -> _Rpc:
        s = self.spec
        if kind == "stdio":
            return StdioRpc(str(s["command"]), [str(a) for a in s.get("args") or []], s.get("env") or {},
                            s.get("cwd"), self._notify)
        if kind == "sse":
            return SseRpc(s["url"], s.get("headers") or {}, self._notify)
        return HttpRpc(s["url"], s.get("headers") or {}, self._notify)

    @staticmethod
    def transport_of(spec: dict) -> str:
        if spec.get("command"):
            return "stdio"
        kind = str(spec.get("type") or spec.get("transport") or "").lower()
        if kind == "sse" or (not kind and str(spec.get("url", "")).rstrip("/").endswith("/sse")):
            return "sse"
        return "http"

    def connect(self, timeout: float = 60.0) -> None:
        kind = self.transport_of(self.spec)
        if kind != "stdio" and not self.spec.get("url"):
            raise McpError("В настройках сервера нет ни command, ни url")
        try:
            self._handshake(kind, timeout)
        except McpError as exc:
            auto = not (self.spec.get("type") or self.spec.get("transport"))
            if kind == "http" and auto and exc.code in (400, 404, 405):
                log.info("%s: Streamable HTTP не подошёл (%s) — пробую SSE", self.name, exc)
                self.close()
                self._handshake("sse", timeout)
            else:
                self.close()
                raise

    def _handshake(self, kind: str, timeout: float) -> None:
        rpc = self._rpc = self._make(kind)
        rpc.start()
        res = rpc.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                         "clientInfo": CLIENT_INFO}, timeout=timeout)
        self.server_info = res.get("serverInfo") or {}
        self.instructions = res.get("instructions") or ""
        rpc.protocol = res.get("protocolVersion") or PROTOCOL
        rpc.negotiated = True
        rpc.notify("notifications/initialized")
        self.tools = self.list_tools()

    def list_tools(self, timeout: float = 30.0) -> list[McpTool]:
        assert self._rpc is not None
        out: list[McpTool] = []
        cursor = None
        for _ in range(50):
            res = self._rpc.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=timeout)
            out += [McpTool.from_dict(d) for d in res.get("tools") or [] if d.get("name")]
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return out

    def refresh_tools(self) -> None:
        self.tools = self.list_tools()

    def call_tool(self, name: str, arguments: dict, timeout: float = 60.0, cancel=None) -> tuple[str, bool]:
        if self._rpc is None:
            raise McpError("Сервер не подключён")
        res = self._rpc.request("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout,
                                cancel=cancel)
        return result_to_text(res)

    @property
    def alive(self) -> bool:
        return self._rpc is not None and not self._rpc.closed

    def close(self) -> None:
        rpc, self._rpc = self._rpc, None
        if rpc is not None:
            try:
                rpc.close()
            except Exception:  # noqa: BLE001
                log.exception("close %s", self.name)
