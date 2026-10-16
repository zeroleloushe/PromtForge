"""Design tokens и QSS. Несколько тем; T обновляется через apply_theme()."""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# общие радиусы для всех тем
_RADII = {"r_xs": 6, "r_sm": 8, "r_md": 11, "r_lg": 14, "r_xl": 18}

THEMES: dict[str, dict] = {
    "dark": {
        "name_key": "set_theme_dark",
        "bg_app": "#050814", "bg_panel": "#0a1024", "bg_card": "#0d1430", "bg_input": "#080e20",
        "bg_hover": "rgba(90,130,255,0.11)", "bg_active": "rgba(47,107,255,0.18)",
        "border_subtle": "rgba(110,140,255,0.09)", "border": "rgba(110,145,255,0.16)",
        "border_strong": "rgba(120,155,255,0.32)",
        "primary": "#3a74ff", "primary_hover": "#5588ff", "primary_top": "#4a82ff", "primary_bottom": "#285ef0",
        "violet": "#7c5cff", "success": "#22c55e", "danger": "#e5383b", "warning": "#f5a524", "teal": "#2dd4bf",
        "text": "#eaf0ff", "text_2": "#a8b4db", "text_3": "#6a779f",
        "outline": "#8eb4ff", "btn_hover": "rgba(70,110,255,0.14)", "btn_pressed": "rgba(70,110,255,0.22)",
        "panel_top": "rgba(16,24,52,0.96)", "panel_bottom": "rgba(9,14,32,0.98)",
        "dock_top": "rgba(18,28,58,0.98)", "dock_bottom": "rgba(10,16,36,0.99)",
        "cat_a": "#3a74ff", "cat_b": "#2f63ee", "scroll": "rgba(120,150,255,0.22)", "scroll_hover": "rgba(120,150,255,0.42)",
        "nebula": ((0.16, -0.10, 0.78, (58, 116, 255, 38)), (1.02, -0.02, 0.58, (124, 92, 255, 28)),
                   (0.55, 1.12, 0.72, (45, 120, 255, 18)), (0.82, 0.55, 0.35, (45, 212, 191, 10))),
        "logo_a": "#3b78ff", "logo_b": "#7c5cff", "is_light": False,
    },
    "midnight": {
        "name_key": "set_theme_midnight",
        "bg_app": "#000000", "bg_panel": "#0a0a0c", "bg_card": "#121216", "bg_input": "#08080a",
        "bg_hover": "rgba(120,120,140,0.12)", "bg_active": "rgba(99,102,241,0.20)",
        "border_subtle": "rgba(255,255,255,0.06)", "border": "rgba(255,255,255,0.10)",
        "border_strong": "rgba(255,255,255,0.18)",
        "primary": "#6366f1", "primary_hover": "#818cf8", "primary_top": "#7c83f7", "primary_bottom": "#4f46e5",
        "violet": "#a78bfa", "success": "#34d399", "danger": "#f87171", "warning": "#fbbf24", "teal": "#2dd4bf",
        "text": "#f4f4f5", "text_2": "#a1a1aa", "text_3": "#71717a",
        "outline": "#a5b4fc", "btn_hover": "rgba(99,102,241,0.16)", "btn_pressed": "rgba(99,102,241,0.26)",
        "panel_top": "rgba(18,18,22,0.98)", "panel_bottom": "rgba(8,8,10,0.99)",
        "dock_top": "rgba(20,20,26,0.98)", "dock_bottom": "rgba(10,10,12,0.99)",
        "cat_a": "#6366f1", "cat_b": "#4f46e5", "scroll": "rgba(160,160,180,0.22)", "scroll_hover": "rgba(160,160,180,0.40)",
        "nebula": ((0.2, -0.05, 0.6, (99, 102, 241, 28)), (0.95, 0.1, 0.5, (167, 139, 250, 18)),
                   (0.5, 1.1, 0.65, (45, 212, 191, 12))),
        "logo_a": "#6366f1", "logo_b": "#a78bfa", "is_light": False,
    },
    "graphite": {
        "name_key": "set_theme_graphite",
        "bg_app": "#12141a", "bg_panel": "#1a1d26", "bg_card": "#222632", "bg_input": "#161922",
        "bg_hover": "rgba(148,163,184,0.10)", "bg_active": "rgba(56,189,248,0.16)",
        "border_subtle": "rgba(148,163,184,0.10)", "border": "rgba(148,163,184,0.18)",
        "border_strong": "rgba(148,163,184,0.32)",
        "primary": "#38bdf8", "primary_hover": "#7dd3fc", "primary_top": "#4fc3f7", "primary_bottom": "#0ea5e9",
        "violet": "#94a3b8", "success": "#4ade80", "danger": "#f87171", "warning": "#fbbf24", "teal": "#2dd4bf",
        "text": "#f1f5f9", "text_2": "#94a3b8", "text_3": "#64748b",
        "outline": "#7dd3fc", "btn_hover": "rgba(56,189,248,0.12)", "btn_pressed": "rgba(56,189,248,0.22)",
        "panel_top": "rgba(30,34,44,0.98)", "panel_bottom": "rgba(18,21,28,0.99)",
        "dock_top": "rgba(32,36,48,0.98)", "dock_bottom": "rgba(20,23,30,0.99)",
        "cat_a": "#38bdf8", "cat_b": "#0ea5e9", "scroll": "rgba(148,163,184,0.25)", "scroll_hover": "rgba(148,163,184,0.45)",
        "nebula": ((0.15, -0.08, 0.7, (56, 189, 248, 22)), (1.0, 0.0, 0.5, (100, 116, 139, 20)),
                   (0.5, 1.15, 0.7, (56, 189, 248, 12))),
        "logo_a": "#38bdf8", "logo_b": "#0ea5e9", "is_light": False,
    },
    "ember": {
        "name_key": "set_theme_ember",
        "bg_app": "#0c0806", "bg_panel": "#16100c", "bg_card": "#1e1610", "bg_input": "#120e0a",
        "bg_hover": "rgba(251,146,60,0.10)", "bg_active": "rgba(249,115,22,0.16)",
        "border_subtle": "rgba(251,146,60,0.10)", "border": "rgba(251,146,60,0.18)",
        "border_strong": "rgba(251,146,60,0.34)",
        "primary": "#f97316", "primary_hover": "#fb923c", "primary_top": "#fb923c", "primary_bottom": "#ea580c",
        "violet": "#f59e0b", "success": "#84cc16", "danger": "#ef4444", "warning": "#fbbf24", "teal": "#2dd4bf",
        "text": "#fff7ed", "text_2": "#d6b89a", "text_3": "#8a6f58",
        "outline": "#fdba74", "btn_hover": "rgba(249,115,22,0.14)", "btn_pressed": "rgba(249,115,22,0.24)",
        "panel_top": "rgba(34,24,16,0.98)", "panel_bottom": "rgba(16,12,8,0.99)",
        "dock_top": "rgba(38,26,16,0.98)", "dock_bottom": "rgba(18,12,8,0.99)",
        "cat_a": "#f97316", "cat_b": "#ea580c", "scroll": "rgba(251,146,60,0.25)", "scroll_hover": "rgba(251,146,60,0.45)",
        "nebula": ((0.18, -0.08, 0.72, (249, 115, 22, 32)), (0.95, 0.05, 0.5, (245, 158, 11, 22)),
                   (0.5, 1.1, 0.65, (234, 88, 12, 14))),
        "logo_a": "#fb923c", "logo_b": "#ea580c", "is_light": False,
    },
    "light": {
        "name_key": "set_theme_light",
        "bg_app": "#e8edf7", "bg_panel": "#f7f9fc", "bg_card": "#ffffff", "bg_input": "#eef2f8",
        "bg_hover": "rgba(47,107,255,0.08)", "bg_active": "rgba(47,107,255,0.12)",
        "border_subtle": "rgba(40,60,120,0.08)", "border": "rgba(40,60,120,0.14)",
        "border_strong": "rgba(40,70,140,0.28)",
        "primary": "#2f6bff", "primary_hover": "#4a80ff", "primary_top": "#4a82ff", "primary_bottom": "#2158ee",
        "violet": "#6d5cff", "success": "#16a34a", "danger": "#dc2626", "warning": "#d97706", "teal": "#0d9488",
        "text": "#152033", "text_2": "#4b5a78", "text_3": "#7a879e",
        "outline": "#2f6bff", "btn_hover": "rgba(47,107,255,0.10)", "btn_pressed": "rgba(47,107,255,0.18)",
        "panel_top": "rgba(255,255,255,0.96)", "panel_bottom": "rgba(244,247,252,0.98)",
        "dock_top": "rgba(255,255,255,0.98)", "dock_bottom": "rgba(240,244,250,0.99)",
        "cat_a": "#2f6bff", "cat_b": "#2158ee", "scroll": "rgba(80,100,150,0.22)", "scroll_hover": "rgba(80,100,150,0.40)",
        "nebula": ((0.2, -0.05, 0.7, (47, 107, 255, 28)), (0.95, 0.0, 0.5, (124, 92, 255, 18)),
                   (0.5, 1.1, 0.65, (47, 107, 255, 14))),
        "logo_a": "#3b78ff", "logo_b": "#6d5cff", "is_light": True,
    },
}

THEME_ORDER = ("dark", "midnight", "graphite", "ember", "light")

T: dict = {}
_current = "dark"

TONES = {
    "blue": "#3b82f6", "cyan": "#22b8e8", "teal": "#14b8a6", "amber": "#f59e0b", "pink": "#ec4899",
    "violet": "#8b5cf6", "orange": "#f97316", "green": "#22c55e", "slate": "#64748b", "red": "#ef4444",
}

FONT_STACK = '"Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", Arial, sans-serif'
APP_NAME = "PromptForge"
APP_NAME_LEFT = "Prompt"
APP_NAME_RIGHT = "Forge"


def tone(name: str) -> str:
    return TONES.get(name, TONES["blue"])


def rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def current_theme() -> str:
    return _current


def theme_ids() -> list[str]:
    return list(THEME_ORDER)


def apply_theme(name: str, app: QApplication | None = None) -> str:
    """Подставляет токены темы в T и при необходимости обновляет палитру/QSS приложения."""
    global _current
    key = name if name in THEMES else "dark"
    _current = key
    data = copy.deepcopy(THEMES[key])
    data.update(_RADII)
    nebula = data.pop("nebula", ())
    is_light = data.pop("is_light", False)
    name_key = data.pop("name_key", "set_theme_dark")
    T.clear()
    T.update(data)
    T["nebula"] = nebula
    T["is_light"] = is_light
    T["name_key"] = name_key
    if app is not None:
        apply_palette(app)
        app.setStyleSheet(build_qss())
    return key


def _asset_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "promptforge_assets"
    d.mkdir(exist_ok=True)
    return d


def _asset(name: str, color: str) -> str:
    from app.ui.icons import pixmap

    path = _asset_dir() / f"{name}_{color.lstrip('#')}.png"
    if not path.exists():
        pixmap(name, color, 14).save(str(path), "PNG")
    return str(path).replace("\\", "/")


def apply_palette(app) -> None:
    pal = QPalette()
    roles = {
        QPalette.Window: T["bg_panel"], QPalette.WindowText: T["text"], QPalette.Base: T["bg_input"],
        QPalette.AlternateBase: T["bg_card"], QPalette.Text: T["text"], QPalette.Button: T["bg_card"],
        QPalette.ButtonText: T["text"], QPalette.Highlight: T["primary"], QPalette.HighlightedText: "#ffffff",
        QPalette.ToolTipBase: T["bg_card"], QPalette.ToolTipText: T["text"], QPalette.PlaceholderText: T["text_3"],
        QPalette.Link: T["primary_hover"],
    }
    for role, col in roles.items():
        pal.setColor(role, QColor(col))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        pal.setColor(QPalette.Disabled, role, QColor(T["text_3"]))
    app.setPalette(pal)
    f = QFont()
    f.setFamilies(["Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Arial"])
    f.setPixelSize(13)
    app.setFont(f)


def build_qss() -> str:
    c = T
    chevron = _asset("chevron-down", c["text_2"])
    handle = "#ffffff" if not c.get("is_light") else c["primary"]
    handle_border = c["primary"] if not c.get("is_light") else "#ffffff"
    return f"""
* {{ outline: 0; }}
QWidget {{ background: transparent; color: {c['text']}; font-family: {FONT_STACK}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {c['bg_app']}; }}
#Root {{ background: {c['bg_app']}; }}
#Panel {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {c['panel_top']}, stop:1 {c['panel_bottom']});
    border: 1px solid {c['border_subtle']};
    border-radius: {c['r_xl']}px;
}}
#Card {{
    background: {c['bg_card']};
    border: 1px solid {c['border_subtle']};
    border-radius: {c['r_lg']}px;
}}
#CardFlat {{ background: {c['bg_card']}; border-radius: {c['r_lg']}px; }}
#Inset {{
    background: {c['bg_input']};
    border: 1px solid {c['border']};
    border-radius: {c['r_lg']}px;
}}
#Inset[dragover="true"] {{ border: 1px solid {c['primary']}; background: {c['bg_active']}; }}
#RequestDock {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {c['dock_top']}, stop:1 {c['dock_bottom']});
    border: 1px solid {c['border']};
    border-radius: {c['r_lg']}px;
}}
#SideRail {{
    background: {c['bg_card']};
    border: 1px solid {c['border_subtle']};
    border-radius: {c['r_lg']}px;
}}
#StatusBar {{ background: transparent; }}
#StatusBar QLabel {{ color: {c['text_3']}; font-size: 11px; }}
#Sep {{ background: {c['border']}; max-width: 1px; min-width: 1px; }}
#HLine {{ background: {c['border_subtle']}; max-height: 1px; min-height: 1px; }}

QLabel#H1 {{ font-size: 18px; font-weight: 700; letter-spacing: -0.3px; }}
QLabel#BrandLeft {{ font-size: 18px; font-weight: 700; letter-spacing: -0.4px; color: {c['text']}; }}
QLabel#BrandRight {{ font-size: 18px; font-weight: 700; letter-spacing: -0.4px; color: {c['primary']}; }}
QLabel#H2 {{ font-size: 14px; font-weight: 600; }}
QLabel#Muted {{ color: {c['text_2']}; font-size: 12px; }}
QLabel#Faint {{ color: {c['text_3']}; font-size: 11px; }}
QLabel#Fine {{ color: {c['text_3']}; font-size: 11px; }}
QLabel#Success {{ color: {c['teal']}; font-size: 12px; font-weight: 500; }}
QLabel#Warn {{ color: {c['warning']}; font-size: 12px; }}

QPlainTextEdit, QTextEdit, QTextBrowser, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {c['bg_input']}; border: 1px solid {c['border']}; border-radius: {c['r_md']}px;
    padding: 8px 12px; selection-background-color: {c['bg_active']}; selection-color: {c['text']};
}}
QPlainTextEdit, QTextEdit, QTextBrowser {{ padding: 10px 14px; }}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {c['primary']};
}}
QPlainTextEdit[flat="true"], QTextBrowser[flat="true"] {{ border: none; background: transparent; }}
QPlainTextEdit#ChatInput {{ padding: 10px 12px; }}
QLineEdit {{ min-height: 22px; }}
QLineEdit#CompactInput, QSpinBox#CompactInput, QDoubleSpinBox#CompactInput {{
    min-height: 16px; padding: 3px 8px;
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QComboBox {{ background: {c['bg_input']}; border: 1px solid {c['border']}; border-radius: {c['r_md']}px;
    padding: 8px 12px; min-height: 22px; }}
QComboBox:hover {{ border-color: {c['border_strong']}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({chevron}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {c['bg_card']}; border: 1px solid {c['border_strong']}; border-radius: {c['r_md']}px;
    padding: 4px; selection-background-color: {c['bg_active']}; selection-color: {c['text']}; outline: 0; }}

QPushButton {{ background: {c['bg_card']}; border: 1px solid {c['border']}; border-radius: {c['r_md']}px;
    padding: 0 14px; min-height: 36px; color: {c['text']}; font-weight: 500; }}
QPushButton:hover {{ background: {c['btn_hover']}; border-color: {c['border_strong']}; }}
QPushButton:pressed {{ background: {c['btn_pressed']}; }}
QPushButton:focus-visible {{ border: 1px solid {c['primary']}; }}
QPushButton:disabled {{ color: {c['text_3']}; background: {c['bg_input']}; border-color: {c['border_subtle']}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QPushButton[size="sm"] {{ min-height: 30px; padding: 0 12px; font-size: 12px; border-radius: {c['r_sm']}px; }}
QPushButton[size="lg"] {{ min-height: 44px; border-radius: {c['r_lg']}px; font-size: 14px; }}
QPushButton[variant="primary"] {{ border: 1px solid {c['border_strong']}; color: #fff; font-weight: 600;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {c['primary_top']}, stop:1 {c['primary_bottom']}); }}
QPushButton[variant="primary"]:hover {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {c['primary_hover']}, stop:1 {c['primary']}); }}
QPushButton[variant="primary"]:pressed {{ padding-top: 1px; background: {c['primary_bottom']}; }}
QPushButton[variant="primary"]:disabled {{ color: rgba(255,255,255,0.55); border-color: transparent;
    background: {c['bg_input']}; }}
QPushButton[variant="outline"] {{ background: transparent; border: 1px solid {c['border_strong']}; color: {c['outline']}; }}
QPushButton[variant="outline"]:hover {{ background: {c['bg_active']}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {c['text_2']}; }}
QPushButton[variant="ghost"]:hover {{ background: {c['bg_hover']}; color: {c['text']}; }}
QPushButton[variant="danger"] {{ color: #ffd9da; font-weight: 600; border: 1px solid rgba(229,56,59,0.75);
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #5b1219, stop:1 #3a0c11); }}
QPushButton[variant="danger"]:hover {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #7a1822, stop:1 #4d0f16); }}
QPushButton[variant="danger"]:disabled {{ color: #8a5a5c; border-color: rgba(229,56,59,0.25); background: #1c0a0d; }}
QPushButton[seg="left"] {{ border-top-right-radius: 0; border-bottom-right-radius: 0; border-right: none; }}
QPushButton[seg="right"] {{ border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 1px solid rgba(255,255,255,0.28);
    padding: 0 12px; }}
QPushButton[win="true"] {{ background: transparent; border: none; border-radius: 6px; min-height: 28px; padding: 0; }}
QPushButton[win="true"]:hover {{ background: {c['bg_hover']}; }}
QPushButton[win="close"] {{ background: transparent; border: none; border-radius: 6px; min-height: 28px; padding: 0; }}
QPushButton[win="close"]:hover {{ background: {c['danger']}; }}
QPushButton[tab="model"] {{ min-height: 42px; padding: 0 15px; border-radius: {c['r_lg']}px; background: {c['bg_card']};
    border: 1px solid {c['border_subtle']}; color: {c['text_2']}; font-weight: 500; font-size: 13px; }}
QPushButton[tab="model"]:hover {{ border-color: {c['border_strong']}; color: {c['text']}; background: {c['bg_hover']}; }}
QPushButton[tab="model"]:checked {{ background: {c['bg_active']}; border: 1px solid {c['primary']}; color: {c['text']}; }}
QPushButton[tab="pill"] {{ min-height: 30px; max-height: 30px; padding: 0 11px; border-radius: {c['r_md']}px; background: {c['bg_card']};
    border: 1px solid {c['border_subtle']}; color: {c['text_2']}; font-size: 12px; }}
QPushButton[tab="pill"]:hover {{ color: {c['text']}; border-color: {c['border_strong']}; }}
QPushButton[tab="pill"]:checked {{ background: {c['bg_active']}; border: 1px solid {c['primary']}; color: {c['text']}; }}
QPushButton[off="true"] {{ color: {c['text_3']}; opacity: 0.75; }}
QPushButton[chip="true"] {{ min-height: 26px; padding: 0 12px; border-radius: 13px; font-size: 12px; font-weight: 400;
    background: {c['bg_card']}; border: 1px solid {c['border']}; color: {c['text_2']}; }}
QPushButton[chip="true"]:hover {{ color: {c['text']}; border-color: {c['primary']}; background: {c['bg_active']}; }}
QPushButton[chat="true"] {{ min-height: 36px; padding: 0 14px; }}
QPushButton[chat="true"]:checked {{
    background: {c['bg_active']}; border: 1px solid {c['primary']}; color: {c['text']};
}}

#CatRow, #StyleRow, #VersionRow {{ border-radius: {c['r_md']}px; border: 1px solid transparent; }}
#CatRow:hover, #StyleRow:hover {{ background: {c['bg_hover']}; }}
#CatRow[active="true"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {c['cat_a']}, stop:1 {c['cat_b']}); }}
#CatRow[active="true"] QLabel {{ color: #fff; }}
#VersionRow {{ background: {c['bg_input']}; border: 1px solid {c['border_subtle']}; border-radius: {c['r_lg']}px; }}
#VersionRow[current="true"] {{ background: {c['bg_active']}; border: 1px solid {c['primary']}; }}
#VersionBadge {{ background: {c['bg_card']}; border: 1px solid {c['border']}; border-radius: {c['r_md']}px; font-weight: 700; font-size: 15px; color: {c['text_2']}; }}
#VersionBadge[current="true"] {{ color: #fff; border-color: {c['primary']}; background: {c['bg_active']}; }}
#Chip {{ background: {c['bg_card']}; border: 1px solid {c['border']}; border-radius: 18px; }}
#Badge {{ background: {c['bg_hover']}; border-radius: 8px; color: {c['text_3']}; font-size: 10px; padding: 1px 8px; }}
#StatusChip {{ background: rgba(34,197,94,0.10); border: 1px solid rgba(34,197,94,0.30); border-radius: 12px; }}
#StatusChip QLabel {{ color: #6ee7a0; font-size: 12px; }}
#StatusChip[off="true"] {{ background: {c['bg_hover']}; border-color: {c['border']}; }}
#StatusChip[off="true"] QLabel {{ color: {c['text_3']}; }}

QRadioButton, QCheckBox {{ spacing: 8px; color: {c['text_2']}; font-size: 12px; }}
QRadioButton:hover, QCheckBox:hover {{ color: {c['text']}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; border: 1.5px solid {c['border_strong']}; background: {c['bg_input']}; }}
QRadioButton::indicator:checked {{ border: 1.5px solid {c['primary']};
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {c['primary']}, stop:0.45 {c['primary']}, stop:0.55 {c['bg_input']}, stop:1 {c['bg_input']}); }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1.5px solid {c['border_strong']}; background: {c['bg_input']}; }}
QCheckBox::indicator:checked {{ background: {c['primary']}; border-color: {c['primary']}; }}

QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['primary']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {handle}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
    border: 2px solid {handle_border}; }}
QSlider::handle:horizontal:hover {{ border-color: {c['primary_hover']}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['scroll']}; border-radius: 3px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c['scroll_hover']}; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['scroll']}; border-radius: 3px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {c['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{ background: {c['bg_card']}; border: 1px solid {c['border_strong']}; border-radius: {c['r_md']}px; padding: 6px; }}
QMenu::item {{ padding: 8px 14px 8px 12px; border-radius: 6px; color: {c['text']}; }}
QMenu::item:selected {{ background: {c['bg_active']}; }}
QMenu::item:disabled {{ color: {c['text_3']}; }}
QMenu::separator {{ height: 1px; background: {c['border_subtle']}; margin: 5px 6px; }}
QMenu::indicator {{ width: 14px; height: 14px; margin-left: 4px; }}
QToolTip {{ background: {c['bg_card']}; color: {c['text']}; border: 1px solid {c['border_strong']}; padding: 6px 8px; border-radius: 6px; }}
QListWidget {{ background: {c['bg_input']}; border: 1px solid {c['border']}; border-radius: {c['r_md']}px; padding: 4px; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {c['bg_active']}; color: {c['text']}; }}
QListWidget::item:hover {{ background: {c['bg_hover']}; }}
QTabWidget::pane {{ border: none; }}
#Toast {{ background: {c['bg_card']}; border: 1px solid {c['border_strong']}; border-radius: {c['r_lg']}px; }}
#Toast[kind="success"] {{ border-color: rgba(34,197,94,0.6); }}
#Toast[kind="warning"] {{ border-color: rgba(245,165,36,0.6); }}
#Toast[kind="error"] {{ border-color: rgba(229,56,59,0.7); }}
#TitleBar {{ background: transparent; }}
#ReasoningBox {{ background: {c['bg_hover']}; }}
"""


# стартовые токены (до apply_theme из main)
apply_theme("dark")
