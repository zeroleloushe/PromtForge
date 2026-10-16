"""Клиент OpenAI-совместимого API локальной Strata: health, потоковый чат, отмена, unload."""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urlparse

import requests

from app.utils.log import get_logger

log = get_logger("api")


class StrataError(Exception):
    """kind: offline | timeout | http | json | other"""

    def __init__(self, kind: str, detail: str = "", status: int = 0):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.status = status


def normalize_url(url: str) -> str:
    """localhost -> 127.0.0.1: на Windows localhost может сначала резолвиться в ::1 и давать задержку."""
    return re.sub(r"//localhost(?=[:/]|$)", "//127.0.0.1", (url or "").strip().rstrip("/"))


@dataclass
class UnloadResult:
    status: str  # ok | unsupported | error
    message: str = ""


class ThinkFilter:
    """Вырезает <think>...</think> из потока (рассуждающие модели), учитывая разрыв тегов между чанками."""
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self, on_think=None):
        self.inside = False
        self.carry = ""
        self.on_think = on_think   # необязательный callback: сюда уходит текст рассуждений

    def _think(self, text: str) -> None:
        if text and self.on_think:
            self.on_think(text)

    def feed(self, text: str) -> str:
        buf = self.carry + text
        self.carry = ""
        out = []
        while buf:
            if self.inside:
                i = buf.find(self.CLOSE)
                if i < 0:
                    keep = self._tail(buf, self.CLOSE)
                    self._think(buf[: len(buf) - len(keep)])
                    self.carry = keep
                    return "".join(out)
                self._think(buf[:i])
                buf = buf[i + len(self.CLOSE):].lstrip("\n")
                self.inside = False
            else:
                i = buf.find(self.OPEN)
                if i < 0:
                    keep = self._tail(buf, self.OPEN)
                    out.append(buf[: len(buf) - len(keep)])
                    self.carry = keep
                    return "".join(out)
                out.append(buf[:i])
                buf = buf[i + len(self.OPEN):]
                self.inside = True
        return "".join(out)

    def flush(self) -> str:
        rest, self.carry = ("" if self.inside else self.carry), ""
        return rest

    @staticmethod
    def _tail(buf: str, tag: str) -> str:
        for n in range(min(len(tag) - 1, len(buf)), 0, -1):
            if tag.startswith(buf[-n:]):
                return buf[-n:]
        return ""


class StrataAPI:
    def __init__(self, api_base: str, health_endpoint: str = "/health", timeout: float = 120,
                 unload_endpoint: str = "", stream_timeout: float | None = None):
        self.api_base = normalize_url(api_base)
        self.health_endpoint = health_endpoint or "/health"
        self.timeout = float(timeout)
        # для потока важна пауза между чанками, а не общее время: thinking может идти минутами.
        # 0 / None -> без ограничения чтения (connect-таймаут остаётся коротким)
        st = float(stream_timeout) if stream_timeout is not None else max(self.timeout, 600.0)
        self.stream_timeout = st if st > 0 else None
        self.unload_endpoint = unload_endpoint or ""
        self._session = requests.Session()
        self._session.trust_env = False  # локальный сервер не должен ходить через системный прокси
        self._resp: requests.Response | None = None
        self.last_usage: dict = {}      # prompt/completion токены и finish_reason последнего потока
        self._cancel = threading.Event()

    @property
    def root(self) -> str:
        p = urlparse(self.api_base)
        return f"{p.scheme}://{p.netloc}"

    # --- состояние сервера -----------------------------------------------
    def health(self, timeout: float = 2.0) -> str:
        """'ok' | 'loading' | 'down'"""
        try:
            r = self._session.get(self.root + self.health_endpoint, timeout=timeout)
            if r.status_code == 503:
                return "loading"
            if r.status_code < 400:
                return "ok"
            if r.status_code == 404:
                r2 = self._session.get(self.api_base + "/models", timeout=timeout)
                return "ok" if r2.status_code < 400 else "down"
            return "down"
        except requests.RequestException:
            return "down"

    def list_models(self, timeout: float = 3.0) -> list[str]:
        try:
            r = self._session.get(self.api_base + "/models", timeout=timeout)
            r.raise_for_status()
            return [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
        except Exception:
            return []

    def context_size(self, timeout: float = 3.0) -> int | None:
        try:
            r = self._session.get(self.root + "/props", timeout=timeout)
            if r.ok:
                n = r.json().get("default_generation_settings", {}).get("n_ctx")
                if n:
                    return int(n)
        except Exception:
            pass
        try:
            r = self._session.get(self.api_base + "/models", timeout=timeout)
            for m in r.json().get("data", []):
                meta = m.get("meta") or {}
                for key in ("n_ctx", "n_ctx_train", "context_length", "max_model_len"):
                    if meta.get(key) or m.get(key):
                        return int(meta.get(key) or m.get(key))
        except Exception:
            pass
        return None

    # --- чат -------------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()
        resp = self._resp
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _post(self, payload: dict, stream: bool) -> requests.Response:
        try:
            resp = self._session.post(
                self.api_base + "/chat/completions", json=payload, stream=stream,
                timeout=(10, self.stream_timeout if stream else self.timeout), headers={"Accept": "text/event-stream" if stream else "application/json"},
            )
        except requests.exceptions.ConnectionError as exc:
            raise StrataError("offline", str(exc)) from exc
        except requests.exceptions.Timeout as exc:
            raise StrataError("timeout", str(exc)) from exc
        except requests.RequestException as exc:
            raise StrataError("other", str(exc)) from exc
        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json()
                err = body.get("error", body)
                detail = err.get("message", "") if isinstance(err, dict) else str(err)
            except Exception:
                detail = resp.text[:300]
            resp.close()
            raise StrataError("http", detail or resp.reason or "", resp.status_code)
        return resp

    def chat_stream(self, payload: dict, on_reasoning=None) -> Iterator[str]:
        """Генератор текстовых фрагментов ответа. cancel() прерывает HTTP-запрос.

        on_reasoning(text) — необязательный callback для рассуждений модели (поле reasoning_content или <think>…</think>).
        """
        self._cancel.clear()
        self.last_usage = {}
        payload = {**payload, "stream": True}
        resp = self._resp = self._post(payload, stream=True)
        flt = ThinkFilter(on_reasoning)
        try:
            for raw in resp.iter_lines(decode_unicode=False):
                if self._cancel.is_set():
                    return
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if "error" in obj:
                    err = obj["error"]
                    raise StrataError("http", err.get("message", str(err)) if isinstance(err, dict) else str(err))
                u, tm = obj.get("usage") or {}, obj.get("timings") or {}
                if u or tm:           # сколько токенов сервер реально принял/выдал (для диагностики)
                    self.last_usage.update({"prompt": u.get("prompt_tokens") or tm.get("prompt_n") or 0,
                                            "completion": u.get("completion_tokens") or tm.get("predicted_n") or 0})
                    if tm.get("predicted_per_second"):
                        self.last_usage["tps"] = float(tm["predicted_per_second"])
                for ch in obj.get("choices", []):
                    if ch.get("finish_reason"):
                        self.last_usage["finish"] = ch["finish_reason"]
                    delta = ch.get("delta") or {}
                    rpiece = delta.get("reasoning_content") or delta.get("reasoning")
                    if rpiece and on_reasoning:
                        on_reasoning(rpiece)
                    piece = delta.get("content")
                    if piece:
                        text = flt.feed(piece)
                        if text:
                            yield text
            tail = flt.flush()
            if tail:
                yield tail
        except requests.exceptions.Timeout as exc:
            if not self._cancel.is_set():
                raise StrataError("timeout", str(exc)) from exc
        except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError, OSError) as exc:
            if not self._cancel.is_set():
                raise StrataError("offline", str(exc)) from exc
        finally:
            try:
                resp.close()
            except Exception:
                pass
            self._resp = None

    def chat_events(self, payload: dict) -> Iterator[dict]:
        """Потоковый чат с инструментами. Генератор событий:

        {"type":"content","text"} · {"type":"reasoning","text"} · {"type":"usage","prompt","completion","tps"} ·
        {"type":"finish","reason"} · {"type":"tool_calls","calls":[{"id","name","arguments"}],"source":"native|text"} (в конце).
        Текстовые блоки <tool_call>…</tool_call> (серверы без разбора tools) вырезаются из content и превращаются в вызовы.
        """
        from app.core.toolcalls import ToolTagFilter, new_call_id, parse_text_tool_call

        self._cancel.clear()
        payload = {**payload, "stream": True}
        resp = self._resp = self._post(payload, stream=True)
        side: list[dict] = []
        think = ThinkFilter(lambda txt: side.append({"type": "reasoning", "text": txt}))
        tags = ToolTagFilter()
        native: dict[int, dict] = {}
        reasoned_natively = False

        def pump(text: str):
            vis = tags.feed(think.feed(text))
            if vis:
                side.append({"type": "content", "text": vis})
            yield from _drain(side)

        def _drain(buf: list):
            while buf:
                yield buf.pop(0)

        try:
            for raw in resp.iter_lines(decode_unicode=False):
                if self._cancel.is_set():
                    return
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if "error" in obj:
                    err = obj["error"]
                    raise StrataError("http", err.get("message", str(err)) if isinstance(err, dict) else str(err))
                if obj.get("usage") or obj.get("timings"):
                    u, tm = obj.get("usage") or {}, obj.get("timings") or {}
                    yield {"type": "usage", "prompt": u.get("prompt_tokens") or tm.get("prompt_n") or 0,
                           "completion": u.get("completion_tokens") or tm.get("predicted_n") or 0,
                           "tps": float(tm.get("predicted_per_second") or 0)}
                for ch in obj.get("choices", []):
                    delta = ch.get("delta") or {}
                    rpiece = delta.get("reasoning_content") or delta.get("reasoning")
                    if rpiece:
                        reasoned_natively = True
                        yield {"type": "reasoning", "text": rpiece}
                    piece = delta.get("content")
                    if piece:
                        yield from pump(piece)
                    for tc in delta.get("tool_calls") or []:
                        slot = native.setdefault(tc.get("index", len(native)), {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
                    if ch.get("finish_reason"):
                        yield {"type": "finish", "reason": ch["finish_reason"]}
            tail = tags.feed(think.flush()) + tags.flush()
            if tail:
                yield {"type": "content", "text": tail}
            yield from _drain(side)
            calls = [{"id": c["id"] or new_call_id(), "name": c["name"], "arguments": c["arguments"]}
                     for _, c in sorted(native.items()) if c["name"]]
            source = "native"
            if not calls:
                for block in tags.blocks:
                    parsed = parse_text_tool_call(block)
                    if parsed:
                        calls.append({"id": new_call_id(), **parsed})
                source = "text"
            if calls:
                yield {"type": "tool_calls", "calls": calls, "source": source}
        except requests.exceptions.Timeout as exc:
            if not self._cancel.is_set():
                raise StrataError("timeout", str(exc)) from exc
        except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError, OSError) as exc:
            if not self._cancel.is_set():
                raise StrataError("offline", str(exc)) from exc
        finally:
            try:
                resp.close()
            except Exception:
                pass
            self._resp = None

    def chat(self, payload: dict, on_reasoning=None) -> str:
        self._cancel.clear()
        payload = {**payload, "stream": False}
        resp = self._resp = self._post(payload, stream=False)
        try:
            data = resp.json()
            msg = data["choices"][0]["message"]
            text = msg["content"] or ""
            r = msg.get("reasoning_content") or msg.get("reasoning")
            if r and on_reasoning:
                on_reasoning(r)
        except Exception as exc:
            raise StrataError("json", str(exc)) from exc
        finally:
            self._resp = None
        flt = ThinkFilter(on_reasoning)
        return flt.feed(text) + flt.flush()

    # --- unload ------------------------------------------------------------
    def unload(self, model_id: str = "") -> UnloadResult:
        """Запрашивает выгрузку модели. Успех объявляется только при реальном ответе 2xx."""
        body = {"model": model_id} if model_id else {}
        last_error = ""
        try:
            # Ollama: keep_alive=0 выгружает модель
            tags = self._session.get(self.root + "/api/tags", timeout=2)
            if tags.ok and "models" in tags.json():
                r = self._session.post(self.root + "/api/generate", json={"model": model_id, "keep_alive": 0}, timeout=15)
                if r.ok:
                    return UnloadResult("ok")
        except Exception:
            pass
        if self.unload_endpoint:
            candidates = [("POST", self.unload_endpoint)]
        else:
            candidates = [("POST", "/models/unload"), ("POST", "/v1/models/unload"), ("POST", "/unload"),
                          ("GET", "/unload"), ("POST", "/api/unload")]
        for method, ep in candidates:
            url = ep if ep.startswith("http") else self.root + (ep if ep.startswith("/") else "/" + ep)
            try:
                r = self._session.request(method, url, json=body if method == "POST" else None, timeout=30)
            except requests.exceptions.ConnectionError as exc:
                return UnloadResult("error", str(exc))
            except requests.RequestException as exc:
                last_error = str(exc)
                continue
            if r.status_code < 300:
                return UnloadResult("ok")
            if r.status_code in (404, 405, 501):
                continue
            last_error = f"HTTP {r.status_code}: {r.text[:200]}"
        return UnloadResult("error", last_error) if last_error else UnloadResult("unsupported")
