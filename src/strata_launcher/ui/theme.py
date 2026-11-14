"""Colours and the style sheet, for a light and a dark look."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette

LIGHT = {
    "window": "#f4f4f2", "card": "#ffffff", "text": "#1d1d1f", "dim": "#62626a", "border": "#dcdcd8", "field": "#ffffff",
    "accent": "#1c6fd6", "accent_text": "#ffffff", "danger": "#c01c28", "ok": "#1b8553", "warning": "#b56a00", "track": "#e4e4e0",
    # log lines
    "launcher": "#1c6fd6", "answer": "#1b8553", "warn": "#905300", "error": "#c01c28", "key": "#1f7a6c", "detail": "#77767b",
    "start": "#813d9c", "live": "#3a7d4f",
    # bar segments
    "model": "#3584e4", "slots": "#c061cb", "other": "#9a9996", "file": "#e5a50a", "free": "#e4e4e0", "bar": "#3584e4",
}
DARK = {
    "window": "#1f1f21", "card": "#2a2a2d", "text": "#f2f2f3", "dim": "#a3a3ab", "border": "#3d3d42", "field": "#343438",
    "accent": "#6aa7f0", "accent_text": "#10141a", "danger": "#ff7b72", "ok": "#73d49a", "warning": "#f0c05a", "track": "#3d3d42",
    "launcher": "#78aeed", "answer": "#8ff0a4", "warn": "#f8e45c", "error": "#ff7b63", "key": "#93ddc2", "detail": "#9a9996",
    "start": "#dc8add", "live": "#b5e3bd",
    "model": "#62a0ea", "slots": "#c061cb", "other": "#8a8a8f", "file": "#f6d32d", "free": "#3d3d42", "bar": "#62a0ea",
}


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:                                   # Qt before 6.5
        return QGuiApplication.palette().color(QPalette.Window).lightness() < 128


def colours(theme: str = "system") -> dict:
    dark = theme == "dark" or (theme == "system" and system_is_dark())
    return dict(DARK if dark else LIGHT, dark=dark)


def palette(c: dict) -> QPalette:
    p = QPalette()
    for role, key in ((QPalette.Window, "window"), (QPalette.Base, "field"), (QPalette.AlternateBase, "card"),
                      (QPalette.Button, "card"), (QPalette.ToolTipBase, "card"), (QPalette.WindowText, "text"),
                      (QPalette.Text, "text"), (QPalette.ButtonText, "text"), (QPalette.ToolTipText, "text"),
                      (QPalette.Highlight, "accent"), (QPalette.HighlightedText, "accent_text"), (QPalette.Link, "accent"),
                      (QPalette.PlaceholderText, "dim")):
        p.setColor(role, QColor(c[key]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(c["dim"]))
    return p


def style_sheet(c: dict) -> str:
    return f"""
QWidget {{ font-size: 10pt; }}
QToolTip {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
#card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px; }}
#tile {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px; }}
#heading {{ font-size: 11pt; font-weight: 600; }}
#rowTitle {{ font-weight: 600; }}
#dim, #help {{ color: {c['dim']}; }}
#help, #caption {{ font-size: 9pt; }}
#caption {{ color: {c['dim']}; }}
#statusTitle {{ font-size: 16pt; font-weight: 700; }}
#big {{ font-size: 19pt; font-weight: 700; }}
#tileTitle {{ font-size: 9pt; font-weight: 600; color: {c['dim']}; }}
#notice {{ background: {c['card']}; border: 1px solid {c['warning']}; border-radius: 8px; padding: 8px; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {c['border']}; }}
QTabBar::tab {{ padding: 8px 18px; margin: 0 2px; border: none; border-bottom: 3px solid transparent; color: {c['dim']}; font-weight: 600; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 3px solid {c['accent']}; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QPushButton {{ padding: 6px 14px; border: 1px solid {c['border']}; border-radius: 7px; background: {c['card']}; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['dim']}; background: {c['window']}; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border: 1px solid {c['accent']}; font-weight: 600; }}
QPushButton#primary:disabled {{ background: {c['track']}; color: {c['dim']}; border-color: {c['track']}; }}
QPushButton#danger {{ color: {c['danger']}; font-weight: 600; }}
QPushButton#danger:hover {{ border-color: {c['danger']}; }}
QPushButton#danger:disabled {{ color: {c['dim']}; }}
QPushButton#big {{ padding: 9px 14px; font-size: 10.5pt; }}
QPushButton#flat {{ border: none; background: transparent; color: {c['accent']}; padding: 4px 8px; }}
QLineEdit {{ padding: 5px 8px; border: 1px solid {c['border']}; border-radius: 7px; background: {c['field']}; min-height: 20px; }}
QLineEdit:focus {{ border-color: {c['accent']}; }}
QLineEdit:read-only {{ background: {c['window']}; }}
QComboBox, QSpinBox {{ min-height: 30px; }}
QComboBox QLineEdit, QSpinBox QLineEdit {{ border: none; background: transparent; padding: 0 4px; min-height: 0; }}
QPlainTextEdit {{ border: none; background: {c['card']}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QProgressBar {{ border: none; border-radius: 4px; background: {c['track']}; max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ border-radius: 4px; background: {c['accent']}; }}
QCheckBox, QRadioButton {{ spacing: 7px; }}
#line {{ background: {c['border']}; max-height: 1px; min-height: 1px; border: none; }}
"""


def apply(app, theme: str = "system") -> dict:
    c = colours(theme)
    app.setStyle("Fusion")
    app.setPalette(palette(c))
    app.setStyleSheet(style_sheet(c))
    return c
