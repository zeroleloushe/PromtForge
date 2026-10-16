"""Левая колонка: категории, поиск, список стилей с переключателями, импорт/экспорт."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QVBoxLayout, QWidget)

from app.core.style_manager import Style, StyleManager
from app.ui import icons
from app.ui.dialogs import StyleEditorDialog, confirm, info
from app.ui.theme import T
from app.ui import anim
from app.ui.widgets import (Button, ElidedLabel, IconTile, Toggle, VScroll, clear_layout, icon_button, label, repolish, hline)
from app.utils.i18n import t
from app.utils.log import get_logger

log = get_logger("styles_ui")


class _ClickableRow(QFrame):
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        hit = e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint())
        super().mouseReleaseEvent(e)
        if hit:
            self.clicked.emit()     # после базовой обработки: обработчик может удалить эту строку


class StylesPanel(QFrame):
    styles_changed = Signal()   # включён/выключен/создан/удалён — перерисовать выбранные стили

    ALL = "__all__"

    def __init__(self, manager: StyleManager, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.manager = manager
        self.category = self.ALL
        self.query = ""
        self.setAcceptDrops(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        tile = IconTile("brush", "violet", 30, solid=False)
        head.addWidget(tile)
        head.addWidget(label(t("styles"), "H2"))
        head.addStretch(1)
        self.create_btn = Button(t("style_create"), "plus", "outline", "sm")
        self.create_btn.clicked.connect(self.create_style)
        head.addWidget(self.create_btn)
        root.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText(t("style_search"))
        self.search.addAction(icons.icon("search", T["text_3"], 16), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        root.addWidget(self.search)

        self.scroll = VScroll()
        self.scroll.lay.setSpacing(6)
        self.cat_caption = ElidedLabel(t("style_categories").upper())
        self.cat_caption.setStyleSheet(f"color:{T['text_3']};font-size:11px;font-weight:600;padding-left:4px;")
        self.scroll.lay.addWidget(self.cat_caption)
        self.cat_box = QVBoxLayout()
        self.cat_box.setSpacing(3)
        self.scroll.lay.addLayout(self.cat_box)
        self.scroll.lay.addSpacing(6)
        self.list_head = QHBoxLayout()
        self.list_title = ElidedLabel(t("my_styles"), "H2")
        self.list_head.addWidget(self.list_title, 1)
        self.count_lb = label("", "Faint")
        self.count_lb.setToolTip(t("style_count_tip"))
        self.list_head.addWidget(self.count_lb)
        self.scroll.lay.addLayout(self.list_head)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(2)
        self.scroll.lay.addLayout(self.rows_box)
        self.empty = label(t("style_none_found"), "Muted", wrap=True)
        self.scroll.lay.addWidget(self.empty)
        self.scroll.lay.addStretch(1)
        root.addWidget(self.scroll, 1)

        self.io_btn = Button(t("style_import_export"), "upload", "secondary")
        self.io_btn.setMinimumHeight(40)
        menu = QMenu(self.io_btn)
        menu.addAction(icons.icon("download", T["text_2"], 16), t("style_import"), self.import_styles)
        menu.addAction(icons.icon("upload", T["text_2"], 16), t("style_export_all"), self.export_all)
        self.io_btn.setMenu(menu)
        root.addWidget(self.io_btn)
        self.refresh()

    # --- построение ---------------------------------------------------------
    def refresh(self, animate: bool = False) -> None:
        styles = self.manager.all()
        self.scroll.body.setUpdatesEnabled(False)      # без промежуточной отрисовки старых/новых строк
        clear_layout(self.cat_box)
        total = len(styles)
        if self.category != self.ALL and self.category not in dict(self.manager.categories()):
            self.category = self.ALL
        self.cat_box.addWidget(self._cat_row(self.ALL, t("all_styles"), "grid", "blue", total))
        for name, count in self.manager.categories():
            sample = next(s for s in styles if s.category == name)
            self.cat_box.addWidget(self._cat_row(name, name, sample.icon, sample.tone, count))
        self._fill_rows(animate)
        self.scroll.body.setUpdatesEnabled(True)

    def _cat_row(self, key: str, title: str, icon_name: str, tone_name: str, count: int) -> QFrame:
        row = _ClickableRow()
        row.setObjectName("CatRow")
        row.setCursor(Qt.PointingHandCursor)
        row.setProperty("active", self.category == key)
        row.setFixedHeight(38)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 0, 12, 0)
        lay.setSpacing(10)
        lay.addWidget(IconTile(icon_name, tone_name, 26, solid=self.category == key))
        lb = ElidedLabel(title)
        lb.setStyleSheet("font-weight:500;")
        lb.setToolTip(title)
        lay.addWidget(lb, 1)
        cnt = QLabel(str(count))
        cnt.setStyleSheet(f"color:{T['text_3']};font-size:12px;")
        if self.category == key:
            cnt.setStyleSheet("color:#fff;font-size:12px;")
        lay.addWidget(cnt)
        row.clicked.connect(lambda k=key: self._select_category(k))
        return row

    def _select_category(self, key: str) -> None:
        self.category = key
        # перестраиваем после выхода из обработчика клика: строка категории, по которой кликнули, удаляется
        QTimer.singleShot(0, lambda: self.refresh(animate=True))

    def _on_search(self, text: str) -> None:
        self.query = text.strip().lower()
        self._fill_rows()

    def _visible_styles(self) -> list[Style]:
        out = []
        for s in self.manager.all():
            if self.category != self.ALL and s.category != self.category:
                continue
            if self.query and self.query not in (s.name + " " + s.description + " " + s.category).lower():
                continue
            out.append(s)
        return out

    def _fill_rows(self, animate: bool = False) -> None:
        clear_layout(self.rows_box)
        vis = self._visible_styles()
        self.list_title.setText(t("my_styles") if self.category == self.ALL else t("styles_in_category", name=self.category))
        self.count_lb.setText(f"{sum(1 for s in self.manager.all() if s.enabled)}/{len(self.manager.all())} {t('active_lc')}")
        rows = []
        for s in vis:
            row = self._style_row(s)
            self.rows_box.addWidget(row)
            rows.append(row)
        self.empty.setVisible(not vis)
        if animate:
            # только прозрачность, без сдвига: строки только что добавлены и ещё не разложены (pos = 0,0) —
            # анимация pos «прибивала» их к верху списка, и они рисовались поверх категорий
            anim.stagger(rows, 22, 200, 12, slide=0)

    def _style_row(self, s: Style) -> QFrame:
        row = QFrame()
        row.setObjectName("StyleRow")
        row.setFixedHeight(56)
        row.setMinimumWidth(0)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 0, 4, 0)
        lay.setSpacing(8)
        lay.addWidget(IconTile(s.icon, s.tone, 36))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addStretch(1)
        name = ElidedLabel(s.name)
        name.setStyleSheet("font-weight:500;")
        col.addWidget(name)
        sub = QHBoxLayout()
        sub.setSpacing(6)
        if s.custom:
            b = QLabel(t("badge_custom"))
            b.setObjectName("Badge")
            sub.addWidget(b)
        desc = ElidedLabel(s.description, "Faint")
        sub.addWidget(desc, 1)
        col.addLayout(sub)
        col.addStretch(1)
        lay.addLayout(col, 1)
        row.setToolTip(f"{s.name}\n{s.description}\n" + "\n".join(f"• {i}" for i in s.instructions))
        more = icon_button("more", t("style_actions"), 22, 16)
        menu = QMenu(more)
        menu.addAction(icons.icon("pencil", T["text_2"], 16), t("edit"), lambda sid=s.id: self.edit_style(sid))
        menu.addAction(icons.icon("copy", T["text_2"], 16), t("duplicate"), lambda sid=s.id: self.duplicate_style(sid))
        menu.addAction(icons.icon("upload", T["text_2"], 16), t("style_export"), lambda sid=s.id: self.export_one(sid))
        menu.addSeparator()
        menu.addAction(icons.icon("trash", "#ff8a8d", 16), t("delete"), lambda sid=s.id: self.delete_style(sid))
        more.setMenu(menu)
        lay.addWidget(more)
        tg = Toggle(s.enabled)
        tg.setToolTip(t("style_toggle_tip"))
        tg.toggled.connect(lambda on, sid=s.id: self._toggle(sid, on))
        lay.addWidget(tg)
        return row

    def _toggle(self, style_id: str, on: bool) -> None:
        self.manager.set_enabled(style_id, on)
        self.count_lb.setText(f"{sum(1 for s in self.manager.all() if s.enabled)}/{len(self.manager.all())} {t('active_lc')}")
        self.styles_changed.emit()

    # --- CRUD ---------------------------------------------------------------------
    def _categories(self) -> list[str]:
        return [c for c, _ in self.manager.categories()]

    def create_style(self) -> None:
        dlg = StyleEditorDialog(self.window(), None, self._categories())
        if dlg.exec():
            self.manager.create({**dlg.data(), "enabled": True})
            self.refresh()
            self.styles_changed.emit()

    def edit_style(self, style_id: str) -> None:
        s = self.manager.get(style_id)
        if not s:
            return
        dlg = StyleEditorDialog(self.window(), s, self._categories())
        if dlg.exec():
            d = dlg.data()
            s.name, s.description, s.category = d["name"], d["description"], d["category"]
            s.icon, s.tone, s.priority, s.instructions = d["icon"], d["tone"], d["priority"], d["instructions"]
            self.manager.save(s)
            self.refresh()
            self.styles_changed.emit()

    def duplicate_style(self, style_id: str) -> None:
        if self.manager.duplicate(style_id):
            self.refresh()
            self.styles_changed.emit()

    def delete_style(self, style_id: str) -> None:
        s = self.manager.get(style_id)
        if s and confirm(self.window(), t("delete"), t("style_delete_q", name=s.name), t("delete"), True):
            self.manager.delete(style_id)
            self.refresh()
            self.styles_changed.emit()

    # --- импорт / экспорт --------------------------------------------------------------
    def import_styles(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self.window(), t("style_import"), "", "JSON (*.json)")
        self.import_files(files)

    def import_files(self, files: list[str]) -> None:
        total = 0
        for f in files:
            try:
                total += len(self.manager.import_file(Path(f)))
            except Exception as exc:
                log.error("Импорт стиля не удался (%s): %s", f, exc)
                info(self.window(), t("style_import"), t("style_import_err", name=Path(f).name, detail=str(exc)))
        if total:
            self.refresh()
            self.styles_changed.emit()
            self.window().toast(t("style_imported", n=total), "success") if hasattr(self.window(), "toast") else None

    def export_one(self, style_id: str) -> None:
        s = self.manager.get(style_id)
        if not s:
            return
        f, _ = QFileDialog.getSaveFileName(self.window(), t("style_export"), f"{s.id}.json", "JSON (*.json)")
        if f:
            self.manager.export_one(style_id, Path(f))

    def export_all(self) -> None:
        f, _ = QFileDialog.getSaveFileName(self.window(), t("style_export_all"), "promptforge_styles.json", "JSON (*.json)")
        if f:
            self.manager.export_all(Path(f))

    # drag & drop JSON-стилей
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(".json") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.import_files([u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile().lower().endswith(".json")])
