"""Референсы (фото, видео, аудио): превью, подписи, добавление, удаление, замена, drag & drop."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLineEdit, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from app.core.reference_manager import AUDIO_EXT, IMAGE_EXT, VIDEO_EXT, Reference, ReferenceManager
from app.ui import icons
from app.ui.theme import T
from app.ui.widgets import Section, label, repolish
from app.utils.i18n import t
from app.utils.log import get_logger

log = get_logger("refs_ui")
THUMB_W, THUMB_H = 115, 130
CAP_H = 40                       # полоса подписи внизу плитки (поверх картинки)
TILE_H = THUMB_H                 # все плитки одного размера
ALL_EXT = IMAGE_EXT | VIDEO_EXT | AUDIO_EXT
_cache: dict[str, QPixmap] = {}


def _cover(path: str, w: int, h: int) -> QPixmap | None:
    key = f"{path}|{w}x{h}"
    if key in _cache:
        return _cache[key]
    pm = QPixmap(path)
    if pm.isNull():
        return None
    sc = 2
    pm = pm.scaled(w * sc, h * sc, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x, y = (pm.width() - w * sc) // 2, (pm.height() - h * sc) // 2
    pm = pm.copy(x, y, w * sc, h * sc)
    pm.setDevicePixelRatio(sc)
    _cache[key] = pm
    return pm


class RefThumb(QWidget):
    remove_requested = Signal(int)
    open_requested = Signal(int)
    replace_requested = Signal(int)
    caption_requested = Signal(int)

    def __init__(self, ref: Reference, index: int, badge: str = "", parent=None):
        super().__init__(parent)
        self.ref, self.index = ref, index
        self.badge = badge or str(index + 1)
        self.setFixedSize(THUMB_W, TILE_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._hover = False
        self._missing = not ref.exists()
        self._pm = _cover(ref.path, THUMB_W, THUMB_H) if ref.type == "image" and not self._missing else None
        tip = ref.name + (("\n" + ref.description) if ref.description else "")
        self.setToolTip(tip + ("\n" + t("ref_missing") if self._missing else ""))
        self.close_btn = QPushButton(self)
        self.close_btn.setIcon(icons.icon("x", "#ffffff", 12))
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setToolTip(t("ref_remove"))
        self.close_btn.setAccessibleName(t("ref_remove"))
        self.close_btn.setStyleSheet("QPushButton{background:rgba(6,10,24,0.72);border:1px solid rgba(255,255,255,0.18);"
                                     "border-radius:11px;min-height:0;padding:0;}QPushButton:hover{background:rgba(229,56,59,0.9);}")
        self.close_btn.move(THUMB_W - 28, 6)
        self.close_btn.clicked.connect(lambda: self.remove_requested.emit(self.index))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            if e.position().y() >= THUMB_H - CAP_H:
                self.caption_requested.emit(self.index)      # клик по полосе подписи — редактировать название/описание
            else:
                self.open_requested.emit(self.index)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction(t("ref_open"), lambda: self.open_requested.emit(self.index))
        m.addAction(t("ref_caption"), lambda: self.caption_requested.emit(self.index))
        m.addAction(t("ref_replace"), lambda: self.replace_requested.emit(self.index))
        m.addSeparator()
        m.addAction(t("ref_remove"), lambda: self.remove_requested.emit(self.index))
        m.exec(e.globalPos())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0.5, 0.5, THUMB_W - 1, THUMB_H - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        p.setClipPath(path)
        if self._pm:
            p.drawPixmap(0, 0, self._pm)
        else:
            g = QLinearGradient(0, 0, 0, THUMB_H)
            g.setColorAt(0, QColor("#16224a"))
            g.setColorAt(1, QColor("#0b1128"))
            p.fillRect(self.rect(), g)
            ic = "file-missing" if self._missing else {"video": "play", "audio": "music"}.get(self.ref.type, "image")
            p.drawPixmap(THUMB_W // 2 - 14, 22, icons.pixmap(ic, "#8fa6ff" if not self._missing else "#ff8a8d", 28))
            p.setPen(QColor(T["text_2"]))
            f = p.font()
            f.setPixelSize(10)
            p.setFont(f)
            p.drawText(QRectF(6, 56, THUMB_W - 12, 30), Qt.AlignHCenter | Qt.TextWordWrap,
                       t("ref_missing_short") if self._missing else self.ref.name)
        # полоса подписи внизу плитки: описание, а если его нет — приглашение (только при наведении)
        has = bool(self.ref.description)
        if has or self._hover:
            g = QLinearGradient(0, THUMB_H - CAP_H - 8, 0, THUMB_H)
            g.setColorAt(0, QColor(6, 10, 24, 0))
            g.setColorAt(0.35, QColor(6, 10, 24, 200))
            g.setColorAt(1, QColor(6, 10, 24, 235))
            p.fillRect(QRectF(0, THUMB_H - CAP_H - 8, THUMB_W, CAP_H + 8), g)
            f = p.font()
            f.setPixelSize(10)
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor("#e6ecff") if has else QColor("#8fa6ff"))
            cap = QRectF(7, THUMB_H - CAP_H + 2, THUMB_W - 14, CAP_H - 6)
            text = self.ref.description.replace("\n", " ") if has else "+ " + t("ref_add_caption")
            p.setClipRect(cap)
            p.drawText(cap, Qt.AlignLeft | Qt.AlignBottom | Qt.TextWordWrap,
                       p.fontMetrics().elidedText(text, Qt.ElideRight, int((THUMB_W - 14) * 2 - 24)))
            p.setClipping(False)
        # индекс (нумерация совпадает с «Image N / Video N / Audio N» в запросе к модели) — сверху слева
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(6, 10, 24, 200))
        bw = 20 if len(self.badge) < 2 else 26
        p.drawRoundedRect(QRectF(7, 7, bw, 18), 9, 9)
        p.setPen(QColor("#ffffff"))
        f = p.font()
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(7, 7, bw, 18), Qt.AlignCenter, self.badge)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(T["primary"]) if self._hover else QColor(100, 130, 255, 60), 1.2))
        p.drawRoundedRect(rect, 10, 10)


class AddTile(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(THUMB_W, THUMB_H)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False
        self.setToolTip(t("ref_add"))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(T["primary"]) if self._hover else QColor(120, 150, 255, 90)
        pen = QPen(col, 1.4, Qt.DashLine)
        p.setPen(pen)
        if self._hover:
            p.setBrush(QColor(47, 107, 255, 28))
        p.drawRoundedRect(QRectF(0.7, 0.7, self.width() - 1.4, self.height() - 1.4), 10, 10)
        cx = self.width() / 2
        p.drawPixmap(int(cx - 10), 16, icons.pixmap("plus", "#8fa6ff", 20, 2))
        p.setPen(QColor(T["text_2"]))
        f = p.font()
        f.setPixelSize(11)
        p.setFont(f)
        p.drawText(QRectF(8, 44, self.width() - 16, 30), Qt.AlignHCenter | Qt.TextWordWrap, t("ref_add"))
        p.setPen(QColor(T["text_3"]))
        f.setPixelSize(10)
        p.setFont(f)
        p.drawText(QRectF(8, 76, self.width() - 16, 28), Qt.AlignHCenter | Qt.TextWordWrap, t("ref_add_kinds"))
        p.drawPixmap(int(cx - 7), THUMB_H - 22, icons.pixmap("chevron-down", T["text_3"], 14))


class ReferencesPanel(Section):
    changed = Signal()

    def __init__(self, manager: ReferenceManager, parent=None):
        super().__init__(t("references"), "image", collapsible=True, info=t("ref_info"), parent=parent)
        self.manager = manager
        self.setAcceptDrops(True)
        self.zone = QFrame()
        self.zone.setObjectName("Inset")
        zl = QVBoxLayout(self.zone)
        zl.setContentsMargins(10, 10, 10, 10)
        zl.setSpacing(6)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(TILE_H + 16)
        self.scroll.viewport().setAutoFillBackground(False)
        self.row_host = QWidget()
        self.row = QHBoxLayout(self.row_host)
        self.row.setContentsMargins(0, 0, 0, 6)
        self.row.setSpacing(10)
        self.scroll.setWidget(self.row_host)
        zl.addWidget(self.scroll)
        self.warn = label("", "Warn", wrap=True)
        self.warn.hide()
        zl.addWidget(self.warn)
        self.body_lay.addWidget(self.zone)
        self.supports_vision = True
        self.rebuild()

    # --- построение ----------------------------------------------------------------
    def rebuild(self) -> None:
        while self.row.count():
            it = self.row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        counts = {"image": 0, "video": 0, "audio": 0}
        prefix = {"image": "", "video": "V", "audio": "A"}     # те же номера, что «Image 1 / Video 1 / Audio 1» в запросе к модели
        for i, ref in enumerate(self.manager.items()):
            counts[ref.type] = counts.get(ref.type, 0) + 1
            th = RefThumb(ref, i, f"{prefix.get(ref.type, '')}{counts[ref.type]}")
            th.remove_requested.connect(self._remove)
            th.open_requested.connect(self._open_ref)
            th.replace_requested.connect(self._replace)
            th.caption_requested.connect(self._edit_caption)
            self.row.addWidget(th, 0, Qt.AlignTop)
        add = AddTile()
        add.clicked.connect(self._add_menu)
        self.row.addWidget(add)
        self.row.addStretch(1)
        self._update_warn()

    def set_vision(self, supports: bool) -> None:
        self.supports_vision = supports
        self._update_warn()

    def _update_warn(self) -> None:
        items = self.manager.items()
        msgs = []
        if any(r.type == "image" for r in items) and not self.supports_vision:
            msgs.append(t("warn_no_vision"))
        if any(r.type == "video" for r in items):
            msgs.append(t("warn_video_names"))
        if any(r.type == "audio" for r in items):
            msgs.append(t("warn_audio_names"))
        if any(not r.exists() for r in items):
            msgs.append(t("warn_ref_missing"))
        self.warn.setText("  ".join(msgs))
        self.warn.setVisible(bool(msgs))

    # --- действия --------------------------------------------------------------------
    KINDS = {"image": IMAGE_EXT, "video": VIDEO_EXT, "audio": AUDIO_EXT}

    def _add_menu(self) -> None:
        m = QMenu(self)
        m.addAction(icons.icon("image", T["text_2"], 16), t("ref_add_image"), lambda: self.pick_files("image"))
        m.addAction(icons.icon("film", T["text_2"], 16), t("ref_add_video"), lambda: self.pick_files("video"))
        m.addAction(icons.icon("music", T["text_2"], 16), t("ref_add_audio"), lambda: self.pick_files("audio"))
        m.addSeparator()
        m.addAction(t("ref_add_any"), lambda: self.pick_files("all"))
        m.exec(QCursor.pos())

    def pick_files(self, kind: str = "all") -> None:
        exts_set = ALL_EXT if kind == "all" else self.KINDS[kind]
        exts = " ".join(f"*{e}" for e in sorted(exts_set))
        title = {"image": t("ref_filter_image"), "video": t("ref_filter_video"), "audio": t("ref_filter_audio")}.get(kind, t("ref_filter"))
        files, _ = QFileDialog.getOpenFileNames(self.window(), t("ref_add"), "", f"{title} ({exts})")
        self.add_files(files)

    def add_files(self, files: list[str]) -> None:
        added = False
        new_audio: list[int] = []
        for f in files:
            try:
                ref = self.manager.add(f)
                added = True
                if ref.type == "audio":
                    new_audio.append(len(self.manager.items()) - 1)
            except ValueError:
                self._toast(t("ref_unsupported", name=Path(f).name), "warning")
            except Exception as exc:
                log.error("Не удалось добавить референс %s: %s", f, exc)
                self._toast(t("ref_add_failed", name=Path(f).name), "error")
        if added:
            self.rebuild()
            self.changed.emit()
            for idx in new_audio:        # аудио сразу просим подписать: что на нём
                self._edit_caption(idx)

    def _edit_caption(self, index: int) -> None:
        """Диалог «Название и описание» — то, что модель узнает о референсе."""
        from app.ui.dialogs import BaseDialog

        items = self.manager.items()
        if not 0 <= index < len(items):
            return
        ref = items[index]
        dlg = BaseDialog(self.window(), t("ref_caption_title"), 520)
        name = QLineEdit(ref.name)
        desc = QPlainTextEdit(ref.description)
        desc.setFixedHeight(120)
        desc.setPlaceholderText(t({"audio": "ref_desc_ph_audio", "video": "ref_desc_ph_video"}.get(ref.type, "ref_desc_ph_image")))
        dlg.body.addWidget(label(t("ref_caption_name"), "Muted"))
        dlg.body.addWidget(name)
        dlg.body.addWidget(label(t("ref_caption_desc"), "Muted"))
        dlg.body.addWidget(desc)
        dlg.body.addWidget(label(t("ref_caption_hint"), "Faint", wrap=True))
        dlg.add_buttons(t("save"), t("cancel"))
        desc.setFocus()
        if dlg.exec():
            self.manager.set_caption(index, name.text(), desc.toPlainText())
            self.rebuild()
            self.changed.emit()

    def _remove(self, index: int) -> None:
        self.manager.remove(index)
        self.rebuild()
        self.changed.emit()

    def _open_ref(self, index: int) -> None:
        items = self.manager.items()
        if 0 <= index < len(items):
            if items[index].exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(items[index].path))
            else:
                self._toast(t("ref_missing"), "warning")

    def _replace(self, index: int) -> None:
        exts = " ".join(f"*{e}" for e in sorted(ALL_EXT))
        f, _ = QFileDialog.getOpenFileName(self.window(), t("ref_replace"), "", f"{t('ref_filter')} ({exts})")
        if f:
            try:
                self.manager.replace(index, f)
                self.rebuild()
                self.changed.emit()
            except Exception as exc:
                log.error("Замена референса не удалась: %s", exc)
                self._toast(t("ref_add_failed", name=Path(f).name), "error")

    def _toast(self, text: str, kind: str) -> None:
        w = self.window()
        if hasattr(w, "toast"):
            w.toast(text, kind)

    # --- drag & drop ------------------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(Path(u.toLocalFile()).suffix.lower() in ALL_EXT
                                          for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self.zone.setProperty("dragover", True)
            repolish(self.zone)

    def dragLeaveEvent(self, e):
        self.zone.setProperty("dragover", False)
        repolish(self.zone)

    def dropEvent(self, e):
        self.zone.setProperty("dragover", False)
        repolish(self.zone)
        self.add_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
