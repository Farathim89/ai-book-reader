"""Audiobookshelf-style themes: colours, stylesheet and the Windows icon font.

The colour names below are module globals that set_theme() swaps; widgets read them when they
paint, so a theme change applies live."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# name: (appbar, sidebar, bg, surface, surface2, surface3, border, text, muted, faint, accent, play, play_hover)
THEMES: dict[str, tuple] = {
    "Audiobookshelf": ("#232323", "#2b2b2b", "#2f2f2f", "#262626", "#373737", "#444444", "#3d3d3d",
                       "#f5f5f5", "#b8b8b8", "#8a8a8a", "#fbbf24", "#3b8a3e", "#4caf50"),
    "Midnight": ("#0e1524", "#121b2d", "#151f36", "#111a2b", "#1d2943", "#2a3857", "#26324c",
                 "#e8edf7", "#a3b0c8", "#6f7d99", "#60a5fa", "#2563eb", "#3b82f6"),
    "OLED Black": ("#000000", "#050505", "#000000", "#080808", "#151515", "#242424", "#1d1d1d",
                   "#f2f2f2", "#a6a6a6", "#6e6e6e", "#fbbf24", "#2e7d32", "#43a047"),
    "Forest": ("#15201a", "#1a271f", "#1e2c23", "#17231c", "#26382d", "#33493b", "#2b3e32",
               "#eef5ef", "#a9bdaf", "#73897a", "#86efac", "#2f855a", "#38a169"),
    "Mocha": ("#231b16", "#2a211b", "#2f251e", "#261e18", "#392d24", "#4a3b30", "#42342a",
              "#f4ece4", "#c4b3a3", "#8f7d6d", "#f59e0b", "#8b5e34", "#a0703f"),
    "Amethyst": ("#1b1425", "#21192d", "#261d34", "#1e172a", "#312749", "#40345c", "#392e51",
                 "#f3eefb", "#b9aed0", "#837799", "#c084fc", "#7c3aed", "#8b5cf6"),
}
CURRENT_THEME = "Audiobookshelf"

# ABS palette (defaults; set_theme() replaces them)
APPBAR = "#232323"        # top bar
SIDEBAR = "#2b2b2b"       # left navigation
BG = "#2f2f2f"            # pages / dialogs
SURFACE = "#262626"       # toolbars, player
SURFACE2 = "#373737"      # inputs, cards
SURFACE3 = "#444444"      # hover
BORDER = "#3d3d3d"
TEXT = "#f5f5f5"
MUTED = "#b8b8b8"
FAINT = "#8a8a8a"
YELLOW = "#fbbf24"        # selected item / highlights
ACCENT = YELLOW
ACCENT_HOVER = "#fcd34d"
ACCENT_DIM = "#5a4a1e"
GREEN = "#3b8a3e"         # Play / primary buttons
GREEN_HOVER = "#4caf50"
ORANGE = "#fb8c00"        # progress
READER_BG = "#262626"
READER_TEXT = "#e6e6e6"
HIGHLIGHT = QColor(251, 191, 36, 70)
HIGHLIGHT_TEXT = QColor("#ffffff")
DANGER = "#ff5252"

ICON_FONT = "Segoe Fluent Icons"

# Segoe Fluent Icons / MDL2 code points
I = {
    "play": "", "pause": "", "prev": "", "next": "",
    "back": "", "left": "", "right": "", "add": "",
    "folder": "", "settings": "", "mic": "", "people": "",
    "save": "", "export": "", "library": "", "volume": "",
    "search": "", "rewind": "", "forward": "", "close": "",
    "delete": "", "headphones": "", "book": "", "refresh": "",
    "check": "", "more": "", "sparkle": "", "stop": "",
    "list": "", "home": "", "series": "", "author": "",
    "moon": "", "upload": "", "read": "", "minus": "",
    "filter": "", "sort": "", "edit": "", "up": "", "down": "",
}


def icon_font(size: int = 13) -> QFont:
    f = QFont(ICON_FONT)
    f.setPixelSize(size)
    return f


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t), int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def set_theme(name: str) -> None:
    """Swap the colour globals to another theme."""
    global CURRENT_THEME, APPBAR, SIDEBAR, BG, SURFACE, SURFACE2, SURFACE3, BORDER, TEXT, MUTED, FAINT
    global YELLOW, ACCENT, ACCENT_HOVER, ACCENT_DIM, GREEN, GREEN_HOVER, READER_BG, READER_TEXT, HIGHLIGHT
    if name not in THEMES:
        name = "Audiobookshelf"
    (APPBAR, SIDEBAR, BG, SURFACE, SURFACE2, SURFACE3, BORDER, TEXT, MUTED, FAINT,
     accent, GREEN, GREEN_HOVER) = THEMES[name]
    CURRENT_THEME = name
    YELLOW = ACCENT = accent
    ACCENT_HOVER = _mix(accent, "#ffffff", 0.25)
    ACCENT_DIM = _mix(accent, BG, 0.68)
    READER_BG = SURFACE
    READER_TEXT = _mix(TEXT, BG, 0.08)
    c = QColor(accent)
    HIGHLIGHT = QColor(c.red(), c.green(), c.blue(), 70)


def use_icon_font(widget, size: int = 13, extra: str = "") -> None:
    """The app stylesheet sets a text font for every widget, which would win over setFont();
    a widget-level stylesheet makes the icon font stick."""
    widget.setFont(icon_font(size))
    widget.setStyleSheet(f'font-family: "{ICON_FONT}"; font-size: {size}px; {extra}')


def build_style() -> str:
    return f"""
* {{ outline: none; }}
QWidget {{ background: {BG}; color: {TEXT}; font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {APPBAR}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; }}
QLabel {{ background: transparent; }}

#AppBar {{ background: {APPBAR}; }}
#SideBar {{ background: {SIDEBAR}; }}
#ToolBar {{ background: {SURFACE}; border-bottom: 1px solid #1c1c1c; }}
#TopBar {{ background: {SURFACE}; border-bottom: 1px solid #1c1c1c; }}
#PlayerBar {{ background: {APPBAR}; border-top: 1px solid #111111; }}
#AppTitle {{ font-size: 17pt; font-weight: 400; background: transparent; letter-spacing: 0.3px; }}
#BookTitle {{ font-size: 11.5pt; font-weight: 600; background: transparent; }}
#PageTitle {{ font-size: 12.5pt; font-weight: 500; background: transparent; }}
#Muted, QLabel[muted="true"] {{ color: {MUTED}; background: transparent; }}

#NavButton {{ background: transparent; border: none; border-left: 4px solid transparent; color: {TEXT};
              padding: 12px 0 10px 0; font-size: 10.5pt; }}
#NavButton:hover {{ background: #333333; }}
#NavButton:checked {{ background: #383838; border-left: 4px solid {YELLOW}; color: {YELLOW}; }}

QPushButton {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 7px 16px; }}
QPushButton:hover {{ background: {SURFACE3}; }}
QPushButton:pressed {{ background: #505050; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; }}
QPushButton#Accent, QPushButton#Play {{ background: {GREEN}; border: 1px solid {GREEN}; color: white; font-weight: 600; }}
QPushButton#Accent:hover, QPushButton#Play:hover {{ background: {GREEN_HOVER}; border-color: {GREEN_HOVER}; }}
QPushButton#Play {{ font-size: 11.5pt; padding: 8px 26px; }}
QPushButton#Danger {{ color: {DANGER}; }}
QPushButton#Link {{ background: transparent; border: none; padding: 0; color: {TEXT}; text-align: left; }}
QPushButton#Link:hover {{ color: {YELLOW}; text-decoration: underline; }}

QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 5px 8px; }}
QToolButton:hover {{ background: {SURFACE3}; }}
QToolButton:pressed, QToolButton:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT_DIM}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#PlayButton {{ background: {TEXT}; border-radius: 22px; color: #1a1a1a; }}
QToolButton#PlayButton:hover {{ background: white; }}
QToolButton#TextTool {{ font-family: "Segoe UI"; font-size: 10pt; padding: 6px 10px; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 9px;
    selection-background-color: {ACCENT_DIM}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{ border-color: #6f6f6f; }}
QLineEdit#Search {{ background: #2f2f2f; border: 1px solid #3a3a3a; padding: 7px 12px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url(@ASSETS@/chevron-down.svg); width: 12px; height: 12px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; selection-background-color: {SURFACE3};
    outline: none; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QSlider::groove:horizontal {{ height: 4px; background: #4a4a4a; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {TEXT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.22); border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.35); }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: rgba(255,255,255,0.22); border-radius: 4px; min-width: 36px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}

QMenu {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {SURFACE3}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; margin-left: 4px; }}

QProgressBar {{ background: #4a4a4a; border: none; border-radius: 3px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ORANGE}; border-radius: 3px; }}

QListView, QListWidget, QTableWidget, QTreeWidget {{ background: {BG}; border: none; }}
QListWidget::item {{ padding: 7px 8px; border-radius: 6px; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: {SURFACE3}; color: {TEXT}; }}
QListWidget::item:hover {{ background: {SURFACE2}; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px 8px; }}
QTableWidget {{ gridline-color: {BORDER}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; background: {SURFACE}; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 16px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {YELLOW}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #6a6a6a; background: {SURFACE2}; }}
QCheckBox::indicator:checked {{ background: {GREEN}; border-color: {GREEN}; image: url(@ASSETS@/check.svg); }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding: 12px 10px 10px 10px; background: {SURFACE}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}

QStatusBar {{ background: {APPBAR}; border-top: 1px solid #111111; color: {MUTED}; }}
QStatusBar::item {{ border: none; }}
#JobPill {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 8px; }}
#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
#ZoomPill {{ background: rgba(35,35,35,0.92); border: 1px solid #4a4a4a; border-radius: 16px; }}
#ShelfLabel {{ background: rgba(20,20,20,0.80); color: {TEXT}; border-radius: 4px; padding: 3px 12px; font-size: 11pt; }}
QSplitter::handle {{ background: {BORDER}; }}
"""


def apply_theme(app: QApplication, name: str | None = None) -> None:
    global ICON_FONT
    set_theme(name or CURRENT_THEME)
    families = set(QFontDatabase.families())
    if "Segoe Fluent Icons" not in families and "Segoe MDL2 Assets" in families:
        ICON_FONT = "Segoe MDL2 Assets"
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in [
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, SURFACE2),
        (QPalette.AlternateBase, SURFACE), (QPalette.Text, TEXT), (QPalette.Button, SURFACE2),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT_DIM), (QPalette.HighlightedText, "#ffffff"),
        (QPalette.ToolTipBase, APPBAR), (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, FAINT),
        (QPalette.Link, YELLOW),
    ]:
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    from ..paths import resource_dir
    assets = (resource_dir() / "assets").as_posix()
    app.setStyleSheet(build_style().replace("@ASSETS@", assets))
