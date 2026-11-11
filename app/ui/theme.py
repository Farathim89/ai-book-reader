"""Modern dark theme: colours, stylesheet and the Windows icon font."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

BG = "#131418"
SURFACE = "#1b1d23"
SURFACE2 = "#22252d"
SURFACE3 = "#2a2e38"
BORDER = "#2f333d"
TEXT = "#e7e9ee"
MUTED = "#9299a6"
FAINT = "#646b78"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#7ba3ff"
ACCENT_DIM = "#2b3f6e"
READER_BG = "#16171c"
READER_TEXT = "#d9dce2"
HIGHLIGHT = QColor(91, 140, 255, 64)
HIGHLIGHT_TEXT = QColor("#ffffff")
DANGER = "#ff6b6b"

ICON_FONT = "Segoe Fluent Icons"

# Segoe Fluent Icons / MDL2 code points
I = {
    "play": "", "pause": "", "prev": "", "next": "",
    "back": "", "left": "", "right": "", "add": "",
    "folder": "", "settings": "", "mic": "", "people": "",
    "save": "", "export": "", "library": "", "font_up": "",
    "font_down": "", "volume": "", "search": "", "rewind": "",
    "forward": "", "close": "", "delete": "", "headphones": "",
    "book": "", "refresh": "", "check": "", "more": "",
    "sparkle": "", "stop": "", "list": "",
}


def icon_font(size: int = 13) -> QFont:
    f = QFont(ICON_FONT)
    f.setPixelSize(size)
    return f


def use_icon_font(widget, size: int = 13, extra: str = "") -> None:
    """The app stylesheet sets a text font for every widget, which would win over setFont();
    a widget-level stylesheet makes the icon font stick."""
    widget.setFont(icon_font(size))
    widget.setStyleSheet(f'font-family: "{ICON_FONT}"; font-size: {size}px; {extra}')


STYLE = f"""
* {{ outline: none; }}
QWidget {{ background: {BG}; color: {TEXT}; font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {SURFACE3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; }}

#TopBar, #PlayerBar {{ background: {SURFACE}; }}
#TopBar {{ border-bottom: 1px solid {BORDER}; }}
#PlayerBar {{ border-top: 1px solid {BORDER}; }}
#AppTitle {{ font-size: 15pt; font-weight: 600; background: transparent; }}
#BookTitle {{ font-size: 11.5pt; font-weight: 600; background: transparent; }}
#Muted, QLabel[muted="true"] {{ color: {MUTED}; background: transparent; }}
QLabel {{ background: transparent; }}

QPushButton {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 7px; padding: 6px 14px; }}
QPushButton:hover {{ background: {SURFACE3}; border-color: #3a3f4b; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; }}
QPushButton#Accent {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Accent:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#Danger {{ color: {DANGER}; }}

QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 5px 8px; }}
QToolButton:hover {{ background: {SURFACE3}; }}
QToolButton:pressed, QToolButton:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT_DIM}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#PlayButton {{ background: {ACCENT}; border-radius: 21px; color: white; }}
QToolButton#PlayButton:hover {{ background: {ACCENT_HOVER}; }}
QToolButton#TextTool {{ font-family: "Segoe UI"; font-size: 10pt; padding: 6px 10px; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 7px; padding: 5px 9px;
    selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url(@ASSETS@/chevron-down.svg); width: 12px; height: 12px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DIM};
    outline: none; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QSlider::groove:horizontal {{ height: 4px; background: {SURFACE3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: white; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #383d48; border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: #4a505d; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #383d48; border-radius: 4px; min-width: 36px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}

QMenu {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; margin-left: 4px; }}

QProgressBar {{ background: {SURFACE3}; border: none; border-radius: 3px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QListView, QListWidget, QTableWidget, QTreeWidget {{ background: {BG}; border: none; }}
QListWidget::item {{ padding: 7px 8px; border-radius: 6px; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: {ACCENT_DIM}; color: {TEXT}; }}
QListWidget::item:hover {{ background: {SURFACE2}; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px 8px; }}
QTableWidget {{ gridline-color: {BORDER}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; background: {SURFACE}; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 16px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #4a505d; background: {SURFACE2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(@ASSETS@/check.svg); }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding: 12px 10px 10px 10px; background: {SURFACE}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}

QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar::item {{ border: none; }}
#JobPill {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 8px; }}
#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
QSplitter::handle {{ background: {BORDER}; }}
"""


def apply_theme(app: QApplication) -> None:
    global ICON_FONT
    families = set(QFontDatabase.families())
    if "Segoe Fluent Icons" not in families and "Segoe MDL2 Assets" in families:
        ICON_FONT = "Segoe MDL2 Assets"
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in [
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, SURFACE2),
        (QPalette.AlternateBase, SURFACE), (QPalette.Text, TEXT), (QPalette.Button, SURFACE2),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#ffffff"),
        (QPalette.ToolTipBase, SURFACE3), (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, FAINT),
        (QPalette.Link, ACCENT),
    ]:
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    from ..paths import resource_dir
    assets = (resource_dir() / "assets").as_posix()
    app.setStyleSheet(STYLE.replace("@ASSETS@", assets))
