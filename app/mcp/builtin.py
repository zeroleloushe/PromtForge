"""Встроенные инструменты ассистента: работают внутри приложения, без Node.js/uvx.

Поиск в сети, чтение страниц, файлы в рабочей папке, запуск Python/команд, время, калькулятор.
Опасные действия (запись, выполнение) помечены risk="write"/"exec" — их подтверждает пользователь.
"""
from __future__ import annotations

import ast
import fnmatch
import ipaddress
import json
import math
import operator
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests

from app.mcp.client import NO_WINDOW, kill_tree
from app.utils.log import get_logger

log = get_logger("tools")

BUILTIN = "builtin"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 PromptForge/1.0"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", "dist", "build", ".pf_tmp"}
MAX_READ = 400_000


class ToolError(Exception):
    """Ожидаемая ошибка инструмента — текст уйдёт модели, чтобы она могла исправиться."""


@dataclass
class ToolDef:
    name: str                     # имя, которое видит модель (уникальное)
    description: str
    schema: dict
    server: str = BUILTIN
    tool: str = ""                # исходное имя у сервера
    risk: str = "safe"            # safe | write | exec
    title: str = ""               # короткая подпись для интерфейса
    trusted: bool = False         # сервер помечен доверенным — подтверждение не нужно

    def __post_init__(self):
        self.tool = self.tool or self.name

    def as_openai(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description[:1500],
                                                 "parameters": self.schema}}


@dataclass
class ToolContext:
    workspace: Path
    cancel: threading.Event = field(default_factory=threading.Event)
    config: object | None = None

    def cfg(self, key: str, default=None):
        return self.config.get(key, default) if self.config is not None else default

    def resolve(self, path: str, must_exist: bool = False) -> Path:
        """Путь внутри рабочей папки. Выход за её пределы запрещён."""
        root = self.workspace.resolve()
        raw = Path(str(path or ".").strip().strip('"'))
        p = (raw if raw.is_absolute() else root / raw).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            raise ToolError(f"Путь вне рабочей папки запрещён: {path}. Рабочая папка: {root}") from None
        if must_exist and not p.exists():
            raise ToolError(f"Не найдено: {path}")
        return p


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def _clip(text: str, limit: int, tail: int = 0) -> str:
    if len(text) <= limit:
        return text
    if tail:
        head = limit - tail
        return text[:head] + f"\n… [обрезано {len(text) - limit} символов] …\n" + text[-tail:]
    return text[:limit] + f"\n… [обрезано, всего {len(text)} символов]"


# ============================================================================ веб
class _Html2Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "head", "form", "button", "select",
            "nav", "footer", "aside"}
    BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "nav", "aside", "ul", "ol", "table",
             "blockquote", "pre", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "li", "hr", "details", "figure"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._pre = 0

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "pre":
            self._pre += 1
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("#" * int(tag[1]) + " ")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "pre":
            self._pre = max(0, self._pre - 1)
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip:
            return
        self.out.append(data if self._pre else re.sub(r"\s+", " ", data))

    def text(self) -> str:
        t = "".join(self.out)
        t = re.sub(r"[ \t]+\n", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()


def html_to_text(html: str) -> tuple[str, str]:
    p = _Html2Text()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001
        pass
    return p.title.strip(), p.text()


def _unwrap_ddg(href: str) -> str:
    if "uddg=" in href:
        q = parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query)
        if q.get("uddg"):
            return unquote(q["uddg"][0])
    return ("https:" + href) if href.startswith("//") else href


class _DdgParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._cur: dict | None = None
        self._mode = ""
        self._tag = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "a" and ("result__a" in cls or "result-link" in cls):
            self._cur = {"title": "", "url": _unwrap_ddg(a.get("href", "")), "snippet": ""}
            self.results.append(self._cur)
            self._mode, self._tag = "title", "a"
        elif self._cur is not None and ("result__snippet" in cls or "result-snippet" in cls):
            self._mode, self._tag = "snippet", tag

    def handle_endtag(self, tag):
        if self._mode and tag == self._tag:
            self._mode = ""

    def handle_data(self, data):
        if self._cur is not None and self._mode:
            self._cur[self._mode] += data


def _search_ddg(query: str, n: int) -> list[dict]:
    last = ""
    for url, method in (("https://html.duckduckgo.com/html/", "post"), ("https://lite.duckduckgo.com/lite/", "post")):
        try:
            r = requests.post(url, data={"q": query}, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"}, timeout=15)
        except requests.RequestException as exc:
            last = str(exc)
            continue
        p = _DdgParser()
        p.feed(r.text)
        res = [x for x in p.results if x["url"].startswith("http") and "duckduckgo.com/y.js" not in x["url"]]
        if res:
            return res[:n]
        last = f"HTTP {r.status_code}, результатов нет (возможна капча DuckDuckGo)"
    raise ToolError("DuckDuckGo не вернул результатов: " + last + ". Задайте SearXNG или Brave API в настройках чата.")


def _search_searxng(base: str, query: str, n: int) -> list[dict]:
    try:
        r = requests.get(base.rstrip("/") + "/search", params={"q": query, "format": "json"}, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        data = r.json().get("results", [])
    except (requests.RequestException, ValueError) as exc:
        raise ToolError(f"SearXNG недоступен или JSON-формат выключен: {exc}") from None
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")} for x in data[:n]]


def _search_brave(key: str, query: str, n: int) -> list[dict]:
    try:
        r = requests.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": n},
                         headers={"X-Subscription-Token": key, "Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        data = r.json().get("web", {}).get("results", [])
    except (requests.RequestException, ValueError) as exc:
        raise ToolError(f"Brave Search: {exc}") from None
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": re.sub(r"<[^>]+>", "", x.get("description", ""))} for x in data[:n]]


def tool_web_search(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("Пустой запрос")
    n = max(1, min(int(args.get("max_results") or 6), 15))
    provider = ctx.cfg("chat.search_provider", "duckduckgo")
    if provider == "searxng" and ctx.cfg("chat.searxng_url"):
        res = _search_searxng(ctx.cfg("chat.searxng_url"), query, n)
    elif provider == "brave" and ctx.cfg("chat.brave_key"):
        res = _search_brave(ctx.cfg("chat.brave_key"), query, n)
    else:
        res = _search_ddg(query, n)
    if not res:
        return "Ничего не найдено. Попробуйте переформулировать запрос."
    lines = [f"Результаты поиска: {query}"]
    for i, r in enumerate(res, 1):
        snippet = re.sub(r"\s+", " ", r["snippet"]).strip()
        lines.append(f"{i}. {r['title'].strip()}\n   {r['url']}\n   {snippet}")
    lines.append("\nЧтобы прочитать страницу целиком, вызовите fetch_url с нужным адресом.")
    return "\n".join(lines)


def _check_public(url: str, ctx: ToolContext) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ToolError("Разрешены только http/https адреса")
    if ctx.cfg("chat.allow_private_net", False):
        return
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 80, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ToolError(f"Не удалось найти хост {p.hostname}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ToolError("Адреса локальной сети и localhost заблокированы (включить можно в настройках чата).")


def tool_fetch_url(args: dict, ctx: ToolContext) -> str:
    url = str(args.get("url", "")).strip()
    if "://" not in url:
        url = "https://" + url
    start = max(0, int(args.get("start") or 0))
    limit = max(500, min(int(args.get("max_chars") or 12000), 40000))
    cur, resp = url, None
    for _ in range(6):                      # редиректы проверяем вручную — каждый адрес на SSRF
        _check_public(cur, ctx)
        try:
            resp = requests.get(cur, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"}, timeout=20,
                                allow_redirects=False, stream=True)
        except requests.RequestException as exc:
            raise ToolError(f"Не удалось загрузить страницу: {exc}") from None
        if resp.is_redirect and resp.headers.get("Location"):
            cur = urljoin(cur, resp.headers["Location"])
            resp.close()
            continue
        break
    else:
        raise ToolError("Слишком много перенаправлений")
    assert resp is not None
    try:
        if resp.status_code >= 400:
            raise ToolError(f"HTTP {resp.status_code} для {cur}")
        ctype = resp.headers.get("Content-Type", "").lower()
        raw = resp.raw.read(3_000_000, decode_content=True)
        enc = resp.encoding if resp.encoding and "html" not in ctype.split(";")[0] else None
        text = raw.decode(enc or resp.apparent_encoding or "utf-8", errors="replace")
    finally:
        resp.close()
    if "html" in ctype or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        title, body = html_to_text(text)
    elif any(x in ctype for x in ("json", "text", "xml", "javascript")):
        title, body = "", text
    else:
        raise ToolError(f"Тип содержимого не поддерживается: {ctype or 'неизвестен'}")
    total = len(body)
    chunk = body[start:start + limit]
    head = f"URL: {cur}\n" + (f"Заголовок: {title}\n" if title else "") + f"Символы {start}–{start + len(chunk)} из {total}"
    if start + len(chunk) < total:
        head += f" (продолжение: fetch_url с start={start + len(chunk)})"
    return head + "\n\n" + chunk


# ============================================================================ файлы
def _read_text(p: Path) -> str:
    data = p.read_bytes()[:MAX_READ]
    if b"\x00" in data[:4096]:
        raise ToolError(f"Бинарный файл: {p.name}")
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ToolError(f"Не удалось определить кодировку: {p.name}")


def tool_read_file(args: dict, ctx: ToolContext) -> str:
    p = ctx.resolve(args.get("path", ""), True)
    if not p.is_file():
        raise ToolError(f"Это не файл: {args.get('path')}")
    lines = _read_text(p).splitlines()
    a = max(1, int(args.get("start_line") or 1))
    b = min(len(lines), int(args.get("end_line") or a + 399))
    body = "\n".join(lines[a - 1:b])
    return f"{p.name}: строки {a}–{b} из {len(lines)}\n\n{_clip(body, 30000)}"


def tool_list_directory(args: dict, ctx: ToolContext) -> str:
    root = ctx.resolve(args.get("path", "."), True)
    depth = max(1, min(int(args.get("depth") or 2), 5))
    out: list[str] = []

    def walk(d: Path, level: int, prefix: str) -> None:
        try:
            items = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except OSError:
            return
        for it in items:
            if len(out) >= 300:
                return
            if it.is_dir():
                if it.name in SKIP_DIRS:
                    continue
                out.append(f"{prefix}{it.name}/")
                if level < depth:
                    walk(it, level + 1, prefix + "  ")
            else:
                try:
                    size = it.stat().st_size
                except OSError:
                    size = 0
                out.append(f"{prefix}{it.name}  ({size} Б)")

    walk(root, 1, "")
    head = f"{root}:" if root != ctx.workspace.resolve() else "Рабочая папка:"
    return head + "\n" + ("\n".join(out) if out else "(пусто)") + ("\n… (список обрезан)" if len(out) >= 300 else "")


def tool_search_files(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", ""))
    if not query:
        raise ToolError("Пустой запрос")
    root = ctx.resolve(args.get("path", "."), True)
    pat = str(args.get("glob") or "*")
    try:
        rx = re.compile(query if args.get("regex") else re.escape(query), re.IGNORECASE)
    except re.error as exc:
        raise ToolError(f"Некорректное регулярное выражение: {exc}") from None
    limit = max(1, min(int(args.get("max_results") or 40), 200))
    hits: list[str] = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if not fnmatch.fnmatch(fn, pat):
                continue
            fp = Path(dirpath) / fn
            try:
                if fp.stat().st_size > 2_000_000:
                    continue
                text = _read_text(fp)
            except (OSError, ToolError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{fp.relative_to(ctx.workspace.resolve())}:{i}: {line.strip()[:200]}")
                    if len(hits) >= limit:
                        return "\n".join(hits) + "\n… (достигнут лимит результатов)"
        if ctx.cancel.is_set():
            break
    return "\n".join(hits) if hits else "Совпадений нет."


def tool_write_file(args: dict, ctx: ToolContext) -> str:
    p = ctx.resolve(args.get("path", ""))
    if p.is_dir():
        raise ToolError("Это папка, а не файл")
    content = str(args.get("content", ""))
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".pf_tmp")
    tmp.write_text(content, encoding="utf-8", newline="")
    tmp.replace(p)
    return f"{'Файл перезаписан' if existed else 'Файл создан'}: {p.relative_to(ctx.workspace.resolve())} ({len(content)} символов)"


def tool_edit_file(args: dict, ctx: ToolContext) -> str:
    p = ctx.resolve(args.get("path", ""), True)
    old, new = str(args.get("old_str", "")), str(args.get("new_str", ""))
    if not old:
        raise ToolError("old_str пустой")
    text = _read_text(p)
    n = text.count(old)
    if n == 0:
        raise ToolError("old_str не найден в файле. Прочитайте файл заново и скопируйте фрагмент точно, с пробелами.")
    if n > 1 and not args.get("replace_all"):
        raise ToolError(f"old_str встречается {n} раз. Добавьте контекст, чтобы фрагмент был уникальным, или replace_all=true.")
    p.write_text(text.replace(old, new), encoding="utf-8", newline="")
    return f"Изменено: {p.name} ({n} замен{'а' if n == 1 else ''})"


# ============================================================================ запуск кода
BLOCKED = re.compile(
    r"(?i)(rm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(/|~|\*|--no-preserve-root)|\bmkfs\b|\bdd\s+if=.*of=/dev/|:\(\)\s*\{|"
    r"\bformat\s+[a-z]:|\bdel\s+/[sfq].*\b[a-z]:\\\s*$|\brd\s+/s\s+/q\s+[a-z]:\\|\bshutdown\b|\breg\s+delete\b|"
    r"\bdiskpart\b|\bcipher\s+/w|>\s*/dev/sd[a-z])")


def _decode(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp866" if os.name == "nt" else "latin-1", errors="replace")


def _run(cmd, ctx: ToolContext, timeout: float, shell: bool = False) -> str:
    try:
        proc = subprocess.Popen(cmd, cwd=str(ctx.workspace), shell=shell, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    except OSError as exc:
        raise ToolError(f"Не удалось запустить: {exc}") from None
    end, out, why = time.monotonic() + timeout, b"", ""
    while True:
        try:
            out, _ = proc.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if ctx.cancel.is_set():
                why = "прервано пользователем"
            elif time.monotonic() > end:
                why = f"превышен лимит {int(timeout)} с"
            if why:
                kill_tree(proc)
                try:
                    out, _ = proc.communicate(timeout=3)
                except Exception:  # noqa: BLE001
                    out = b""
                break
    text = _clip(_decode(out or b"").strip(), 12000, 3000)
    if why:
        return f"[Процесс остановлен: {why}]\n{text}".strip()
    return f"[код возврата: {proc.returncode}]\n{text}".strip() if proc.returncode else (text or "[выполнено, вывода нет]")


def _python_exe() -> str:
    if not getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("python") or shutil.which("py") or "python"


def tool_run_python(args: dict, ctx: ToolContext) -> str:
    code = str(args.get("code", ""))
    if not code.strip():
        raise ToolError("Пустой код")
    timeout = max(1, min(float(args.get("timeout") or 30), 300))
    tmp = ctx.workspace / ".pf_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    f = Path(tempfile.mkstemp(suffix=".py", dir=str(tmp))[1])
    try:
        f.write_text(code, encoding="utf-8")
        return _run([_python_exe(), "-X", "utf8", str(f)], ctx, timeout)
    finally:
        f.unlink(missing_ok=True)


def tool_run_command(args: dict, ctx: ToolContext) -> str:
    command = str(args.get("command", "")).strip()
    if not command:
        raise ToolError("Пустая команда")
    if BLOCKED.search(command):
        raise ToolError("Команда заблокирована как потенциально разрушительная.")
    timeout = max(1, min(float(args.get("timeout") or 60), 600))
    if os.name == "nt":
        command = f"chcp 65001>nul & {command}"
    return _run(command, ctx, timeout, shell=True)


# ============================================================================ мелочи
def tool_get_datetime(args: dict, ctx: ToolContext) -> str:
    tz = str(args.get("timezone") or "").strip()
    now = datetime.now().astimezone()
    if tz:
        try:
            from zoneinfo import ZoneInfo

            now = datetime.now(ZoneInfo(tz))
        except Exception:  # noqa: BLE001
            raise ToolError(f"Неизвестный часовой пояс: {tz} (пример: Europe/Moscow)") from None
    return now.strftime("%A, %d %B %Y, %H:%M:%S %Z (UTC%z)") + f"\nISO: {now.isoformat(timespec='seconds')}"


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg,
        ast.UAdd: operator.pos}
_FUNCS = {k: getattr(math, k) for k in ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "log10", "log2", "exp",
                                       "floor", "ceil", "factorial", "gcd", "radians", "degrees", "hypot")}
_FUNCS.update({"abs": abs, "round": round, "min": min, "max": max, "sum": sum})
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(b) > 1000:
            raise ToolError("Слишком большая степень")
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(x) for x in node.elts]
    raise ToolError("Недопустимое выражение")


def tool_calculate(args: dict, ctx: ToolContext) -> str:
    expr = str(args.get("expression", "")).replace("^", "**")
    try:
        val = _eval(ast.parse(expr.strip(), mode="eval"))
    except ToolError:
        raise
    except (SyntaxError, ZeroDivisionError, ValueError, TypeError, OverflowError) as exc:
        raise ToolError(f"Не удалось вычислить: {exc}") from None
    return f"{expr} = {val}"


# ============================================================================ реестр
@dataclass
class _Spec:
    fn: object
    defn: ToolDef


class BuiltinTools:
    def __init__(self):
        self._tools: dict[str, _Spec] = {}
        add = self._add
        add("web_search", "Search the web (DuckDuckGo by default). Returns titles, URLs and snippets. Use for current events, facts, documentation, anything you are unsure about.",
            _obj({"query": {"type": "string", "description": "Search query"},
                  "max_results": {"type": "integer", "description": "1-15, default 6"}}, ["query"]),
            tool_web_search, "safe", "Поиск в сети")
        add("fetch_url", "Download a web page and return its readable text (also works for JSON/plain text). Use after web_search to read a source. Long pages: pass start to continue.",
            _obj({"url": {"type": "string"}, "start": {"type": "integer", "description": "Character offset to continue reading"},
                  "max_chars": {"type": "integer", "description": "Default 12000, max 40000"}}, ["url"]),
            tool_fetch_url, "safe", "Чтение страницы")
        add("read_file", "Read a text file from the workspace folder (relative path). Returns up to 400 lines; use start_line/end_line to page.",
            _obj({"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]),
            tool_read_file, "safe", "Чтение файла")
        add("list_directory", "List files and folders in the workspace (tree). Skips .git, node_modules, __pycache__.",
            _obj({"path": {"type": "string", "description": "Relative folder, default '.'"}, "depth": {"type": "integer", "description": "1-5, default 2"}}),
            tool_list_directory, "safe", "Список файлов")
        add("search_files", "Search text inside workspace files (like grep). Returns file:line: text.",
            _obj({"query": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string", "description": "e.g. *.py"},
                  "regex": {"type": "boolean"}, "max_results": {"type": "integer"}}, ["query"]),
            tool_search_files, "safe", "Поиск по файлам")
        add("write_file", "Create or overwrite a text file in the workspace. Creates folders as needed. Always send the COMPLETE file content.",
            _obj({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            tool_write_file, "write", "Запись файла")
        add("edit_file", "Edit a workspace file by replacing an exact unique fragment old_str with new_str. Read the file first.",
            _obj({"path": {"type": "string"}, "old_str": {"type": "string"}, "new_str": {"type": "string"},
                  "replace_all": {"type": "boolean"}}, ["path", "old_str", "new_str"]),
            tool_edit_file, "write", "Правка файла")
        add("run_python", "Run Python 3 code in the workspace folder and return stdout/stderr. Use to compute, test code, process data. Print results explicitly.",
            _obj({"code": {"type": "string"}, "timeout": {"type": "number", "description": "Seconds, default 30"}}, ["code"]),
            tool_run_python, "exec", "Запуск Python")
        add("run_command", "Run a shell command in the workspace folder (cmd on Windows, sh elsewhere) and return its output.",
            _obj({"command": {"type": "string"}, "timeout": {"type": "number", "description": "Seconds, default 60"}}, ["command"]),
            tool_run_command, "exec", "Команда")
        add("get_datetime", "Get the current date and time (you do not know it by yourself). Optional IANA timezone like Europe/Moscow.",
            _obj({"timezone": {"type": "string"}}), tool_get_datetime, "safe", "Дата и время")
        add("calculate", "Evaluate a math expression exactly (+ - * / // % ** and sqrt, sin, cos, log, round, min, max, pi, e).",
            _obj({"expression": {"type": "string"}}, ["expression"]), tool_calculate, "safe", "Калькулятор")

    def _add(self, name, desc, schema, fn, risk, title) -> None:
        self._tools[name] = _Spec(fn, ToolDef(name, desc, schema, BUILTIN, name, risk, title, trusted=risk == "safe"))

    def defs(self) -> list[ToolDef]:
        return [s.defn for s in self._tools.values()]

    def has(self, name: str) -> bool:
        return name in self._tools

    def call(self, name: str, args: dict, ctx: ToolContext) -> str:
        spec = self._tools.get(name)
        if spec is None:
            raise ToolError(f"Неизвестный инструмент: {name}")
        if not isinstance(args, dict):
            raise ToolError("Аргументы должны быть JSON-объектом")
        for req in spec.defn.schema.get("required", []):
            if req not in args:
                raise ToolError(f"Не хватает обязательного аргумента «{req}»")
        try:
            return str(spec.fn(args, ctx))  # type: ignore[operator]
        except ToolError:
            raise
        except (ValueError, TypeError) as exc:
            raise ToolError(f"Неверные аргументы: {exc}") from None
        except OSError as exc:
            raise ToolError(f"Ошибка файловой системы: {exc}") from None
