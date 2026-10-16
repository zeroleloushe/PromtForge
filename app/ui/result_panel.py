"""Правая колонка (верх): сгенерированный промт, диалог, быстрые правки."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
                               QSizePolicy, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from app.core.translator import LANGUAGES
from app.ui.anim import FadeStack
from app.ui import icons
from app.ui.theme import T
from app.ui.widgets import Button, ElidedLabel, FlowLayout, Section, Skeleton, hline, icon_button, label, repolish
from app.utils.i18n import t


class ResultPanel(QFrame):
    manual_edit_committed = Signal(str)
    translate_requested = Signal(str, str)     # текст промта, язык (название для модели)
    translate_stop_requested = Signal()
    language_changed = Signal(str)
    bump_tokens_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.group = QButtonGroup(self)
        self.tab_result = Button(t("tab_result"), None, "secondary", "sm")
        self.tab_session = Button(t("tab_session"), None, "secondary", "sm")
        self.tab_translate = Button(t("tab_translate"), None, "secondary", "sm")
        # 0 — промт, 1 — история правок сессии, 2 — перевод
        for i, b in enumerate((self.tab_result, self.tab_session, self.tab_translate)):
            b.setProperty("tab", "pill")
            b.setCheckable(True)
            self.group.addButton(b, i)
            tabs.addWidget(b)
        self.tab_session.setToolTip(t("tab_session_tip"))
        self.tab_result.setChecked(True)
        tabs.addStretch(1)
        root.addLayout(tabs)

        self.stack = FadeStack()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # --- страница результата
        page = QFrame()
        page.setObjectName("Inset")
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        self._build_reasoning(pl)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setProperty("flat", "true")
        self.text.setPlaceholderText(t("result_placeholder"))
        self.text.setMinimumHeight(160)
        self.text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.text.setStyleSheet("QPlainTextEdit{line-height:160%;}")
        pl.addWidget(self.text, 1)
        self.meta = label("", "Faint")
        self.meta.setContentsMargins(14, 0, 14, 8)
        pl.addWidget(self.meta)
        # красный бейдж «обрезано по max_tokens» + быстрый способ поднять лимит
        self.trunc = QFrame()
        tl = QHBoxLayout(self.trunc)
        tl.setContentsMargins(14, 0, 14, 8)
        tl.setSpacing(8)
        self.trunc_label = QLabel(t("truncated_badge"))
        self.trunc_label.setStyleSheet("color:#fff;background:#d6454b;border-radius:8px;padding:2px 8px;"
                                       "font-size:11px;font-weight:600;")
        self.trunc_label.setToolTip(t("truncated_tip"))
        tl.addWidget(self.trunc_label)
        self.trunc_btn = Button(t("truncated_bump"), "plus", "secondary", "sm")
        self.trunc_btn.setToolTip(t("truncated_tip"))
        self.trunc_btn.clicked.connect(self.bump_tokens_requested)
        tl.addWidget(self.trunc_btn)
        tl.addStretch(1)
        self.trunc.hide()
        pl.addWidget(self.trunc)
        self.skeleton = Skeleton(self.text.viewport())
        self.skeleton.hide()
        self.stack.addWidget(page)
        # --- история правок текущей сессии
        self.dialog = QTextBrowser()
        self.dialog.setMinimumHeight(160)
        self.dialog.setOpenLinks(False)
        self.dialog.setPlaceholderText(t("dialog_empty"))
        self.stack.addWidget(self.dialog)
        self._build_translation_page()
        self.group.idClicked.connect(self.stack.setCurrentIndex)
        root.addWidget(self.stack, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.copy_btn = Button(t("copy"), "copy", "secondary")
        self.save_btn = Button(t("save"), "download", "secondary")
        self.edit_btn = Button(t("edit"), "pencil", "secondary")
        for b in (self.copy_btn, self.save_btn, self.edit_btn):
            b.setStyleSheet("QPushButton { padding: 0 8px; }")
            row.addWidget(b, 1)
        root.addLayout(row)
        self.copy_btn.clicked.connect(self.copy)
        self.save_btn.clicked.connect(self.save_to_file)
        self.edit_btn.clicked.connect(self._toggle_edit)

        self._reasoning = ""
        self._reasoning_open = True
        self._tr_cache: dict[tuple[str, str], str] = {}
        self._tr_busy = False
        self._tr_src: tuple[str, str] = ("", "")
        self._streaming = False
        self._cursor_on = True
        self._base = ""
        self._editing = False
        self._original = ""
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._blink)
        self._update_buttons()

    # --- состояние --------------------------------------------------------------
    @property
    def prompt(self) -> str:
        return self._base if self._streaming else self.text.toPlainText()

    def has_prompt(self) -> bool:
        return bool(self.prompt.strip()) and not self._streaming

    def _update_buttons(self) -> None:
        has = self.has_prompt()
        for b in (self.copy_btn, self.save_btn):
            b.setEnabled(has)
        self.edit_btn.setEnabled(has or self._editing)
        self.translate_btn.setEnabled(has or self._tr_busy)

    # --- рассуждения модели ---------------------------------------------------------
    def _build_reasoning(self, pl: QVBoxLayout) -> None:
        self.reasoning_box = QFrame()
        self.reasoning_box.setObjectName("ReasoningBox")
        rb = QVBoxLayout(self.reasoning_box)
        rb.setContentsMargins(12, 8, 8, 0)
        rb.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("brain", "#8fa6ff", 16))
        head.addWidget(ic)
        self.reasoning_title = ElidedLabel(t("reasoning_title"), "Muted")
        head.addWidget(self.reasoning_title, 1)
        self.reasoning_toggle = icon_button("chevron-up", "", 26, 16)
        self.reasoning_toggle.clicked.connect(lambda: self._set_reasoning_open(not self._reasoning_open))
        head.addWidget(self.reasoning_toggle)
        rb.addLayout(head)
        self.reasoning_text = QPlainTextEdit()
        self.reasoning_text.setReadOnly(True)
        self.reasoning_text.setProperty("flat", "true")
        self.reasoning_text.setFixedHeight(130)
        self.reasoning_text.setStyleSheet(f"QPlainTextEdit{{color:{T['text_2']};font-size:12px;}}")
        rb.addWidget(self.reasoning_text)
        rb.addWidget(hline())
        self.reasoning_box.hide()
        pl.addWidget(self.reasoning_box)

    def _set_reasoning_open(self, open_: bool) -> None:
        self._reasoning_open = open_
        self.reasoning_text.setVisible(open_)
        self.reasoning_toggle.set_icon("chevron-up" if open_ else "chevron-down")

    def begin_reasoning(self) -> None:
        self._reasoning = ""
        self.reasoning_text.clear()
        self.reasoning_title.setText(t("reasoning_live"))
        self._set_reasoning_open(True)
        self.reasoning_box.show()

    def append_reasoning(self, chunk: str) -> None:
        if not self._streaming or not chunk:
            return
        if not self.reasoning_box.isVisible():
            self.begin_reasoning()
        self._reasoning += chunk
        cur = self.reasoning_text.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(chunk)
        sb = self.reasoning_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def hide_reasoning(self) -> None:
        self._reasoning = ""
        self.reasoning_text.clear()
        self.reasoning_box.hide()

    def finish_reasoning(self) -> None:
        if not self._reasoning.strip():
            self.hide_reasoning()
            return
        self.reasoning_title.setText(f"{t('reasoning_title')} · {t('chars', n=len(self._reasoning))}")
        self._set_reasoning_open(False)   # после ответа сворачиваем, чтобы был виден промт

    # --- перевод ----------------------------------------------------------------------
    def _build_translation_page(self) -> None:
        page = QFrame()
        page.setObjectName("Inset")
        tl = QVBoxLayout(page)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(10, 8, 10, 8)
        bar.setSpacing(8)
        self.lang_combo = QComboBox()
        for ui_name, model_name in LANGUAGES:
            self.lang_combo.addItem(ui_name, model_name)
        self.lang_combo.setToolTip(t("translate_lang"))
        self.lang_combo.setMinimumContentsLength(8)
        self.lang_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.lang_combo.currentIndexChanged.connect(self._on_language)
        self.translate_btn = Button(t("translate_btn"), "globe", "outline", "sm")
        self.translate_btn.clicked.connect(self._on_translate_clicked)
        self.tr_copy_btn = icon_button("copy", t("translate_copy"), 30, 16)
        self.tr_copy_btn.clicked.connect(self._copy_translation)
        bar.addWidget(self.lang_combo, 1)
        bar.addWidget(self.translate_btn)
        bar.addWidget(self.tr_copy_btn)
        tl.addLayout(bar)
        tl.addWidget(hline())
        self.tr_text = QPlainTextEdit()
        self.tr_text.setReadOnly(True)
        self.tr_text.setProperty("flat", "true")
        self.tr_text.setPlaceholderText(t("translate_ph"))
        self.tr_text.setMinimumHeight(160)
        self.tr_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tr_text.setStyleSheet("QPlainTextEdit{line-height:160%;}")
        tl.addWidget(self.tr_text, 1)
        self.tr_meta = label("", "Faint")
        self.tr_meta.setContentsMargins(14, 0, 14, 8)
        tl.addWidget(self.tr_meta)
        self.stack.addWidget(page)       # индекс 2

    def current_language(self) -> str:
        return self.lang_combo.currentData() or "Russian"

    def set_language(self, model_name: str) -> None:
        i = self.lang_combo.findData(model_name)
        if i >= 0:
            self.lang_combo.blockSignals(True)
            self.lang_combo.setCurrentIndex(i)
            self.lang_combo.blockSignals(False)
            self._sync_translation()

    def _on_language(self) -> None:
        self._sync_translation()
        self.language_changed.emit(self.current_language())

    def _on_translate_clicked(self) -> None:
        if self._tr_busy:
            self.translate_stop_requested.emit()
        else:
            self.translate_requested.emit(self.prompt if self.has_prompt() else "", self.current_language())

    def _sync_translation(self) -> None:
        """Показывает сохранённый перевод для текущего промта и языка (или очищает поле)."""
        if self._tr_busy:
            return
        text = self._tr_cache.get((self.prompt.strip(), self.current_language()), "")
        self.tr_text.setPlainText(text)
        self.tr_meta.setText(t("chars", n=len(text)) if text else "")

    def set_translate_busy(self, busy: bool) -> None:
        self._tr_busy = busy
        if busy:
            self.translate_btn.setText(t("translate_stop"))
            self.translate_btn.set_icon("stop")
            self.translate_btn.set_variant("danger")
        else:
            self.translate_btn.setText(t("translate_btn"))
            self.translate_btn.set_icon("globe")
            self.translate_btn.set_variant("outline")
        self.lang_combo.setEnabled(not busy)
        self._update_buttons()

    def begin_translation(self) -> None:
        self.tr_text.clear()
        self.tr_meta.setText(t("translating"))
        self._tr_src = (self.prompt.strip(), self.current_language())

    def append_translation(self, chunk: str) -> None:
        cur = self.tr_text.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(chunk)
        sb = self.tr_text.verticalScrollBar()
        sb.setValue(sb.maximum())
        self.tr_meta.setText(t("translating") + f" {len(self.tr_text.toPlainText())}")

    def end_translation(self, text: str, stopped: bool) -> None:
        text = text.strip()
        if text and not stopped:
            self._tr_cache[self._tr_src] = text
        self.tr_text.setPlainText(text)
        self.tr_meta.setText(t("chars", n=len(text)) if text else "")

    def fail_translation(self) -> None:
        self.tr_text.clear()
        self.tr_meta.setText("")

    def _copy_translation(self) -> None:
        from PySide6.QtGui import QGuiApplication

        txt = self.tr_text.toPlainText()
        if not txt:
            return
        QGuiApplication.clipboard().setText(txt)
        self.tr_copy_btn.set_icon("check")
        QTimer.singleShot(1400, lambda: self.tr_copy_btn.set_icon("copy"))

    def set_truncated(self, on: bool) -> None:
        self.trunc.setVisible(on)

    @property
    def reasoning_text_all(self) -> str:
        return self._reasoning

    def set_prompt(self, text: str, meta: str = "", keep_reasoning: bool = False) -> None:
        self.trunc.hide()
        if not keep_reasoning:
            self.hide_reasoning()
        self._leave_edit(commit=False)
        self._streaming = False
        self._timer.stop()
        self.skeleton.hide()
        self.text.setReadOnly(True)
        self.text.setPlainText(text)
        self.meta.setText(meta or (t("chars", n=len(text)) if text else ""))
        self._update_buttons()
        self._sync_translation()

    # --- стриминг ---------------------------------------------------------------
    def begin_stream(self) -> None:
        self._leave_edit(commit=False)
        self.hide_reasoning()
        if not self._tr_busy:
            self.tr_text.clear()
            self.tr_meta.setText("")
        self._streaming = True
        self._base = ""
        self.text.setReadOnly(True)
        self.text.clear()
        self.meta.setText(t("generating"))
        self.skeleton.setGeometry(0, 0, self.text.viewport().width(), self.text.viewport().height())
        self.skeleton.show()
        self.skeleton.raise_()
        self._update_buttons()

    def append(self, chunk: str) -> None:
        if not self._streaming:
            return
        if self.skeleton.isVisible():
            self.skeleton.hide()
            self._timer.start()
        self._base += chunk
        self._render_stream()
        self.meta.setText(t("generating_chars", n=len(self._base)))

    def _render_stream(self) -> None:
        cursor = "▍" if self._cursor_on else " "
        self.text.setPlainText(self._base + cursor)
        sb = self.text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _blink(self) -> None:
        self._cursor_on = not self._cursor_on
        if self._streaming:
            self._render_stream()

    def end_stream(self, final: str, meta: str = "") -> None:
        self.set_prompt(final, meta, keep_reasoning=True)
        self.finish_reasoning()

    def show_error(self, message: str) -> None:
        self._streaming = False
        self._timer.stop()
        self.skeleton.hide()
        self.text.setPlainText("")
        self.meta.setText("")
        self._update_buttons()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.skeleton.isVisible():
            self.skeleton.setGeometry(0, 0, self.text.viewport().width(), self.text.viewport().height())

    # --- действия -----------------------------------------------------------------
    def copy(self) -> None:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(self.prompt)
        self.copy_btn.setText(t("copied"))
        self.copy_btn.set_icon("check")
        QTimer.singleShot(1400, lambda: (self.copy_btn.setText(t("copy")), self.copy_btn.set_icon("copy")))

    def save_to_file(self) -> None:
        f, _ = QFileDialog.getSaveFileName(self.window(), t("save"), "prompt.txt", "Text (*.txt);;Markdown (*.md)")
        if f:
            Path(f).write_text(self.prompt, encoding="utf-8")

    def _toggle_edit(self) -> None:
        if self._editing:
            self._leave_edit(commit=True)
        else:
            self._editing = True
            self._original = self.text.toPlainText()
            self.text.setReadOnly(False)
            self.text.setFocus()
            self.edit_btn.setText(t("done"))
            self.edit_btn.set_icon("check")
            self.edit_btn.set_variant("outline")

    def _leave_edit(self, commit: bool) -> None:
        if not self._editing:
            return
        self._editing = False
        self.text.setReadOnly(True)
        self.edit_btn.setText(t("edit"))
        self.edit_btn.set_icon("pencil")
        self.edit_btn.set_variant("secondary")
        new = self.text.toPlainText()
        if commit and new.strip() and new != self._original:
            self.manual_edit_committed.emit(new)
        elif not commit:
            pass
        self._update_buttons()

    # --- диалог -------------------------------------------------------------------
    def set_dialog(self, messages: list[tuple[str, str]]) -> None:
        if not messages:
            self.dialog.setHtml(f'<p style="color:{T["text_3"]}">{html.escape(t("dialog_empty"))}</p>')
            return
        parts = []
        for role, text in messages:
            who = t("dialog_you") if role == "user" else t("dialog_ai")
            col = "#7ea6ff" if role == "user" else "#2dd4bf"
            body = html.escape(text if len(text) < 700 else text[:700] + "…").replace("\n", "<br>")
            parts.append(f'<p style="margin:0 0 4px 0;"><b style="color:{col}">{who}</b></p>'
                         f'<p style="margin:0 0 14px 0;color:{T["text"]};line-height:150%">{body}</p>')
        self.dialog.setHtml("".join(parts))
        sb = self.dialog.verticalScrollBar()
        sb.setValue(sb.maximum())


class EditPanel(Section):
    apply_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(t("edit_title"), None, False, True, "", parent)
        from app.ui.widgets import CountedEdit

        self.edit = CountedEdit(t("edit_ph"))
        self.edit.setFixedHeight(76)
        self.edit.submitted.connect(self._apply)
        self.body_lay.addWidget(self.edit)
        host = QWidget()
        fl = FlowLayout(host, 8)
        for key in ("quick_closer", "quick_atmosphere", "quick_no_rain", "quick_warmer", "quick_less_detail"):
            b = Button(t(key), None, "secondary")
            b.setProperty("chip", True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._fill(k))
            fl.addWidget(b)
        self.body_lay.addWidget(host)
        self.apply_btn = Button(t("apply_edit"), "send", "primary")
        self.apply_btn.clicked.connect(self._apply)
        self.body_lay.addWidget(self.apply_btn, 0, Qt.AlignLeft)
        self.apply_btn.setMinimumWidth(180)

    def _fill(self, key: str) -> None:
        text = t(key + "_text")
        cur = self.edit.toPlainText().strip()
        self.edit.setPlainText((cur + " " + text).strip() if cur and text not in cur else (cur or text))
        self.edit.setFocus()

    def _apply(self) -> None:
        text = self.edit.toPlainText().strip()
        if text and self.apply_btn.isEnabled():
            self.apply_requested.emit(text)

    def clear(self) -> None:
        self.edit.clear()

    def set_enabled_apply(self, on: bool) -> None:
        self.apply_btn.setEnabled(on)
