"""Поле системного промта: текст или прикреплённый файл .md / .txt."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from app.core.prompt_file import PREVIEW_CHARS, PromptFileError, is_prompt_file, locate, read_prompt_file
from app.ui import icons
from app.ui.theme import T
from app.ui.widgets import Button, CountedEdit, ElidedLabel, icon_button, label, repolish
from app.utils.i18n import t

TEXT_LIMIT = 64000


class PlainPasteEdit(CountedEdit):
    """Редактор системного промта: вставка только как обычный текст (табы, пробелы, | таблицы сохраняются),
    моноширинный шрифт и без переноса строк — markdown-таблицы не «съезжают»."""

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(placeholder, parent)
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtWidgets import QPlainTextEdit
        f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        f.setPointSize(max(f.pointSize(), 10))
        f.setStyleHint(QFont.Monospace)
        self.setFont(f)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabChangesFocus(False)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)

    def insertFromMimeData(self, source):  # noqa: N802
        text = source.text() if source.hasText() else ""
        if text:
            text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
            self.textCursor().insertText(text)
        else:
            super().insertFromMimeData(source)

    def canInsertFromMimeData(self, source):  # noqa: N802
        return source.hasText() or super().canInsertFromMimeData(source)


class SystemPromptBox(QWidget):
    """Редактор системного промта. Прикреплённый файл заменяет текст и читается с диска."""

    fileChanged = Signal(str)

    def __init__(self, placeholder: str = "", *, min_height: int = 150, max_height: int = 260,
                 flat: bool = False, remind_save: bool = False, padded: bool = True, parent=None):
        super().__init__(parent)
        self._inline = ""
        self._path = ""
        self._remind_save = remind_save
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 8) if padded else root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.attach_btn = Button(t("sys_attach"), "paperclip", "secondary", "sm")
        self.attach_btn.clicked.connect(self._browse)
        bar.addWidget(self.attach_btn)

        self.chip = QFrame()
        self.chip.setObjectName("Chip")
        self.chip.setFixedHeight(32)
        self.chip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        cl = QHBoxLayout(self.chip)
        cl.setContentsMargins(8, 0, 4, 0)
        cl.setSpacing(4)
        ico = QLabel()
        ico.setPixmap(icons.pixmap("paperclip", T["text_2"], 14))
        cl.addWidget(ico)
        self.file_name = ElidedLabel("", "Muted")
        self.file_name.setMinimumWidth(40)
        self.file_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        cl.addWidget(self.file_name, 1)
        self.reload_btn = icon_button("refresh", t("sys_reload"), 24, 14)
        self.open_btn = icon_button("folder", t("sys_open_file"), 24, 14)
        self.detach_btn = icon_button("x", t("sys_detach"), 24, 12)
        self.reload_btn.clicked.connect(self._reload)
        self.open_btn.clicked.connect(self._open)
        self.detach_btn.clicked.connect(self.detach)
        cl.addWidget(self.reload_btn)
        cl.addWidget(self.open_btn)
        cl.addWidget(self.detach_btn)
        self.chip.hide()
        bar.addWidget(self.chip, 3)
        # «Просмотр / Редактор»: рендер markdown (таблицы, заголовки) или обычный моноширинный редактор
        from PySide6.QtWidgets import QButtonGroup
        self.mode_group = QButtonGroup(self)
        self.view_btn = Button(t("sys_mode_view"), None, "secondary", "sm")
        self.editor_btn = Button(t("sys_mode_edit"), None, "secondary", "sm")
        for i, b in enumerate((self.view_btn, self.editor_btn)):
            b.setProperty("tab", "pill")
            b.setCheckable(True)
            self.mode_group.addButton(b, i)
        self.view_btn.setToolTip(t("sys_mode_view_tip"))
        self.editor_btn.setChecked(True)
        self.mode_group.idClicked.connect(lambda i: self.set_mode("view" if i == 0 else "edit"))
        self.bar_spacer = QWidget()
        self.bar_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bar.addWidget(self.bar_spacer, 1)
        self.bar = bar          # сюда владелец может добавить свои кнопки (например, «Сохранить»)
        root.addLayout(bar)

        self.status = label(t("sys_text_hint"), "Faint", wrap=True)
        # строка 2: подсказка слева, переключатель «Просмотр / Редактор» справа — файловая строка не тесная
        srow = QHBoxLayout()
        srow.setSpacing(6)
        srow.addWidget(self.status, 1)
        srow.addWidget(self.view_btn, 0, Qt.AlignTop)
        srow.addWidget(self.editor_btn, 0, Qt.AlignTop)
        root.addLayout(srow)

        self.edit = PlainPasteEdit(placeholder)
        if flat:
            self.edit.setProperty("flat", "true")
        self.edit.setAcceptDrops(False)
        self.edit.viewport().setAcceptDrops(False)
        self.edit.installEventFilter(self)
        self.edit.viewport().installEventFilter(self)
        self.edit.setMinimumHeight(min_height)
        self.edit.setMaximumHeight(max_height)
        self.edit.textChanged.connect(self._on_typed)
        from PySide6.QtWidgets import QStackedWidget
        from app.ui.md_view import MarkdownView
        self.view = MarkdownView()
        self.view.setMinimumHeight(min_height)
        self.view.setMaximumHeight(max_height)
        self.view.setAcceptDrops(False)
        self.text_stack = QStackedWidget()
        self.text_stack.addWidget(self.edit)
        self.text_stack.addWidget(self.view)
        root.addWidget(self.text_stack, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.addStretch(1)
        self.counter = QLabel(f"0/{TEXT_LIMIT}")
        self.counter.setObjectName("Faint")
        foot.addWidget(self.counter)
        root.addLayout(foot)
        self._paint_count(0, False)

    # --- данные ----------------------------------------------------------------
    def set_prompt(self, text: str, file_path: str = "") -> None:
        self._inline = text or ""
        self._path = (file_path or "").strip()
        if self._path:
            self._show_file(self._path)
        else:
            self._show_text()
        self.fileChanged.emit(self._path)

    def value(self) -> tuple[str, str]:
        """Текст поля (не содержимое файла) и путь к прикреплённому файлу."""
        if not self._path:
            self._inline = self.edit.toPlainText()
        return self._inline, self._path

    def attach(self, path: str) -> None:
        self._show_file(path)
        self.fileChanged.emit(self._path)

    def detach(self) -> None:
        self._path = ""
        self._show_text()
        self.fileChanged.emit("")

    # --- режим просмотра --------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        view = mode == "view"
        (self.view_btn if view else self.editor_btn).setChecked(True)
        if view:
            self.view.show_markdown(self.edit.toPlainText())
            self.text_stack.setCurrentWidget(self.view)
        else:
            self.text_stack.setCurrentWidget(self.edit)

    def mode(self) -> str:
        return "view" if self.text_stack.currentWidget() is self.view else "edit"

    # --- вид -------------------------------------------------------------------
    def _show_text(self) -> None:
        self.chip.hide()
        self.attach_btn.setText(t("sys_attach"))
        self._set_status(t("sys_text_hint"), False)
        self._set_editor(self._inline, readonly=False)
        self._paint_count(len(self._inline), False)
        self.set_mode("edit")          # свой текст — сразу в редакторе

    def _show_file(self, path: str) -> None:
        try:
            text = read_prompt_file(path)
        except PromptFileError as exc:
            self._fail(path, exc.code)
            return
        found = locate(path).resolve()
        try:
            from app.utils import paths as _paths
            self._path = found.relative_to(_paths.ROOT.resolve()).as_posix()   # переносимо: data/prompts/...
        except ValueError:
            self._path = str(found)
        found_path = str(found)
        name = Path(self._path).name
        self.file_name.setText(name)
        self.file_name.setToolTip(found_path)
        self.chip.show()
        self.attach_btn.setText(t("sys_replace"))
        preview = text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS]
        hint = t("sys_file_hint_save") if self._remind_save else t("sys_file_hint")
        if len(text) > PREVIEW_CHARS:
            hint = t("sys_preview_cut", n=_grouped(len(text))) + " " + hint
        self._set_status(hint, False)
        self._set_editor(preview, readonly=True)
        self._paint_count(len(text), True)
        self.set_mode("view")          # промт из файла — по умолчанию отрендеренный просмотр

    def _fail(self, path: str, code: str) -> None:
        self._path = str(Path(path))
        name = Path(path).name or path
        self.file_name.setText(name)
        self.file_name.setToolTip(self._path)
        self.chip.show()
        self.attach_btn.setText(t("sys_replace"))
        self._set_status(t(f"sys_file_{code}"), True)
        self._set_editor(self._inline, readonly=True)
        self._paint_count(len(self._inline), True)

    def _set_editor(self, text: str, readonly: bool) -> None:
        self.edit.blockSignals(True)
        self.edit.setReadOnly(readonly)
        self.edit.setPlainText(text)
        self.edit.blockSignals(False)

    def _set_status(self, text: str, error: bool) -> None:
        self.status.setText(text)
        self.status.setObjectName("Warn" if error else "Faint")
        repolish(self.status)

    def _paint_count(self, n: int, from_file: bool) -> None:
        if from_file:
            self.counter.setText(t("sys_chars", n=_grouped(n)))
            hot = n > 400_000
        else:
            self.counter.setText(f"{n}/{TEXT_LIMIT}")
            hot = n > TEXT_LIMIT
        self.counter.setStyleSheet(f"color:{'#ff6b6e' if hot else T['text_3']};font-size:11px;")

    def _on_typed(self) -> None:
        if self.edit.isReadOnly() or self._path:
            return
        self._inline = self.edit.toPlainText()
        self._paint_count(len(self._inline), False)

    # --- действия --------------------------------------------------------------
    def _browse(self) -> None:
        start = str(Path(self._path).parent) if self._path else ""
        f, _ = QFileDialog.getOpenFileName(self.window(), t("sys_attach_title"), start, t("sys_file_filter"))
        if f:
            self.attach(f)

    def _reload(self) -> None:
        if self._path:
            self._show_file(self._path)

    def _open(self) -> None:
        if self._path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._path))

    def dragEnterEvent(self, e):  # noqa: N802
        _accept_drop(e)

    def dragMoveEvent(self, e):  # noqa: N802
        _accept_drop(e)

    def dropEvent(self, e):  # noqa: N802
        path = _dropped_prompt(e)
        if path:
            self.attach(path)
            e.acceptProposedAction()
        else:
            e.ignore()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj in (self.edit, self.edit.viewport()) and event.type() in (
                QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            if event.type() == QEvent.Type.Drop:
                self.dropEvent(event)
            else:
                _accept_drop(event)
            return True
        return super().eventFilter(obj, event)


def _grouped(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _dropped_prompt(e) -> str:
    md = e.mimeData()
    if not md.hasUrls():
        return ""
    for url in md.urls():
        if url.isLocalFile() and is_prompt_file(url.toLocalFile()):
            return url.toLocalFile()
    return ""


def _accept_drop(e) -> None:
    if _dropped_prompt(e):
        e.acceptProposedAction()
    else:
        e.ignore()
