"""Просмотр markdown системного промта: Qt-рендер (GitHub-диалект: таблицы, заголовки, `code`, \\_) + тёмная тема.

Только отображение — в модель по-прежнему уходит исходный текст.
"""
from __future__ import annotations

import re

from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QTextBrowser

from app.ui.theme import T

_BORDER = "#3a4366"
_HEAD_BG = "#1d2440"
_CODE_BG = "#232a44"
_CODE_FG = "#c9d4ff"


def render_html(md: str) -> str:
    doc = QTextDocument()
    doc.setMarkdown(md or "", QTextDocument.MarkdownDialectGitHub)
    html = doc.toHtml()
    # шрифт/цвет берём из темы виджета, а не из «Sans Serif 9pt» Qt
    html = re.sub(r"<body style=\"[^\"]*\">", "<body>", html, count=1)
    # таблицы: видимая сетка, отступы в ячейках, ширина по колонке
    html = re.sub(r"<table[^>]*>",
                  f'<table border="1" cellspacing="0" cellpadding="7" width="100%" '
                  f'style="border-collapse:collapse; border-style:solid; border-color:{_BORDER}; margin-top:6px; margin-bottom:10px;">',
                  html)
    # первая строка каждой таблицы — шапка с фоном
    html = re.sub(r"(<table[^>]*>\s*)<tr>", rf'\1<tr bgcolor="{_HEAD_BG}">', html)
    # inline-код: моноширинный, с подложкой
    html = html.replace("<span style=\" font-family:'monospace';\">",
                        f"<span style=\" font-family:'Consolas','Cascadia Mono','monospace'; background-color:{_CODE_BG}; color:{_CODE_FG};\">")
    # заголовки: воздух сверху и акцентный цвет
    html = re.sub(r"<h([1-6]) style=\" margin-top:0px;",
                  lambda m: f'<h{m.group(1)} style=" color:{T.get("text", "#e8ecff")}; margin-top:{14 if m.group(1) in "12" else 10}px;', html)
    return html


class MarkdownView(QTextBrowser):
    """Только чтение. Рендер ленивый и кэшируется по тексту — 40k+ символов не тормозят переключение."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setStyleSheet(f"QTextBrowser{{color:{T.get('text', '#e8ecff')}; font-size:13px; padding:6px 10px;}}")
        self._src: str | None = None

    def show_markdown(self, text: str) -> None:
        if text == self._src:
            return
        self._src = text
        bar = self.verticalScrollBar().value()
        self.setHtml(render_html(text))
        self.verticalScrollBar().setValue(bar)
