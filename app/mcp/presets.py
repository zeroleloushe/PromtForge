"""Каталог готовых MCP-серверов. Формат spec совместим с Claude Desktop / Cursor (mcpServers).

Плейсхолдеры в args/env/cwd: ${workspace} — рабочая папка ассистента, ${data} — папка данных PromptForge.
Поле trust=true — инструменты сервера выполняются без подтверждения (только для безопасных, «читающих» серверов).
"""
from __future__ import annotations

import copy

PRESETS: list[dict] = [
    {"id": "context7", "title": "Context7 — документация библиотек", "category": "Код", "needs": "node",
     "desc": "Актуальная документация и примеры кода для тысяч библиотек. Модель перестаёт выдумывать устаревшие API.",
     "default": True,
     "spec": {"command": "npx", "args": ["-y", "@upstash/context7-mcp"], "trust": True, "timeout": 90}},
    {"id": "sequential-thinking", "title": "Sequential Thinking — пошаговое мышление", "category": "Мышление", "needs": "node",
     "desc": "Структурированное рассуждение по шагам с ветвлениями. Полезно для моделей без режима «thinking».",
     "spec": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"], "trust": True}},
    {"id": "memory", "title": "Memory — долговременная память", "category": "Память", "needs": "node",
     "desc": "Граф знаний: ассистент запоминает факты о вас и проектах между чатами. Файл: data/mcp_memory.json. (9 инструментов — заметно расходует контекст.)",
     "spec": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"],
              "env": {"MEMORY_FILE_PATH": "${data}/mcp_memory.json"}, "trust": True}},
    {"id": "filesystem", "title": "Filesystem — расширенная работа с файлами", "category": "Файлы", "needs": "node",
     "desc": "Официальный файловый сервер (перенос, дерево, чтение нескольких файлов, медиа). Доступ — только к рабочей папке.",
     "spec": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "${workspace}"]}},
    {"id": "git", "title": "Git — работа с репозиторием", "category": "Код", "needs": "uv",
     "desc": "status, diff, log, commit, branch для репозитория в рабочей папке (она должна быть git-репозиторием).",
     "spec": {"command": "uvx", "args": ["mcp-server-git", "--repository", "${workspace}"]}},
    {"id": "playwright", "title": "Playwright — управление браузером", "category": "Браузер", "needs": "node",
     "desc": "Открывает сайты в настоящем браузере, кликает, читает страницы с JavaScript. Первый запуск скачивает браузер (~150 МБ).",
     "spec": {"command": "npx", "args": ["-y", "@playwright/mcp@latest", "--headless"], "timeout": 120}},
    {"id": "fetch", "title": "Fetch — загрузка страниц (Markdown)", "category": "Поиск", "needs": "uv",
     "desc": "Альтернатива встроенному fetch_url: конвертирует страницы в Markdown, учитывает robots.txt.",
     "spec": {"command": "uvx", "args": ["mcp-server-fetch"], "trust": True}},
    {"id": "duckduckgo", "title": "DuckDuckGo — поиск и чтение страниц", "category": "Поиск", "needs": "uv",
     "desc": "Альтернатива встроенному web_search (отдельный сервер, без ключей).",
     "spec": {"command": "uvx", "args": ["duckduckgo-mcp-server"], "trust": True}},
    {"id": "time", "title": "Time — часовые пояса", "category": "Утилиты", "needs": "uv",
     "desc": "Текущее время в любом поясе и конвертация между поясами.",
     "spec": {"command": "uvx", "args": ["mcp-server-time"], "trust": True}},
    {"id": "everything", "title": "Everything — демо-сервер для проверки", "category": "Тест", "needs": "node",
     "desc": "Тестовый сервер из репозитория MCP: echo, сложение, картинки. Нужен, чтобы убедиться, что MCP работает.",
     "spec": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"], "trust": True}},
]

NEEDS_HINT = {
    "node": "Нужен Node.js (команда npx): https://nodejs.org — установите LTS-версию и перезапустите PromptForge.",
    "uv": "Нужен uv (команда uvx): https://docs.astral.sh/uv/ — на Windows: powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"",
}
RUNTIME_OF = {"npx": "node", "npm": "node", "node": "node", "uvx": "uv", "uv": "uv"}


def default_servers() -> dict:
    """Серверы, которые попадают в mcp_servers.json при первом запуске (включены только помеченные default)."""
    out = {}
    for p in PRESETS:
        spec = copy.deepcopy(p["spec"])
        spec["description"] = p["desc"]
        spec["disabled"] = not p.get("default", False)
        out[p["id"]] = spec
    return out


def preset(pid: str) -> dict | None:
    return next((p for p in PRESETS if p["id"] == pid), None)
