"""Warm cream and quiet night palettes for the desktop companion."""
from __future__ import annotations

_PALETTES = {
    "cream": {
        "background": "#F8F2E8",
        "surface": "#FFFCF7",
        "text": "#41372E",
        "muted": "#796B5D",
        "accent": "#825B37",
        "heading": "#5D412C",
        "border": "#DFD1BF",
        "selection": "#ECD0AA",
        "primary_text": "#FFFFFF",
        "user_bubble": "#F0DEC4",
        "assistant_bubble": "#F4EDE3",
        "error": "#B23745",
        "state_background": "#EEE0CA",
        "state_text": "#67482D",
        "hover": "#F0E4D2",
        "accent_hover": "#6B472B",
        "disabled_background": "#E9E0D5",
        "disabled_text": "#9A8B7B",
    },
    "night": {
        "background": "#1E2634",
        "surface": "#293345",
        "text": "#F1ECE3",
        "muted": "#BBC0CB",
        "accent": "#DEB786",
        "heading": "#F0CEA0",
        "border": "#4B5669",
        "selection": "#5F4C36",
        "primary_text": "#251D15",
        "user_bubble": "#514337",
        "assistant_bubble": "#303C50",
        "error": "#FFA0AA",
        "state_background": "#463D31",
        "state_text": "#F0CEA0",
        "hover": "#37445A",
        "accent_hover": "#ECC89A",
        "disabled_background": "#343F50",
        "disabled_text": "#919AA8",
    },
}


def palette(theme: str = "cream") -> dict[str, str]:
    """Return a private copy; invalid names fall back to the default palette."""
    return dict(_PALETTES.get(theme, _PALETTES["cream"]))


def stylesheet(theme: str = "cream") -> str:
    p = palette(theme)
    return f"""
QWidget {{
    font-family: 'Malgun Gothic'; font-size: 10pt; color: {p['text']};
}}
QDialog, QWidget#chat {{ background: {p['background']}; }}
QLabel#heading {{ color: {p['heading']}; font-size: 18pt; font-weight: bold; }}
QLabel#subtitle {{ color: {p['muted']}; font-size: 9pt; }}
QLabel#state, QLabel#stateChip, QLabel#stateBadge {{
    background: {p['state_background']}; color: {p['state_text']};
    border-radius: 9px; padding: 5px 10px; font-size: 9pt;
}}
QPushButton {{
    background: {p['surface']}; border: 1px solid {p['border']};
    border-radius: 8px; padding: 7px 11px;
}}
QPushButton:hover {{ background: {p['hover']}; border-color: {p['accent']}; }}
QPushButton:disabled {{
    color: {p['disabled_text']}; background: {p['disabled_background']};
}}
QPushButton#primary {{
    background: {p['accent']}; color: {p['primary_text']}; border-color: {p['accent']};
    font-weight: bold;
}}
QPushButton#primary:hover {{ background: {p['accent_hover']}; }}
QPushButton#primary:disabled {{
    background: {p['disabled_background']}; color: {p['disabled_text']};
    border-color: {p['border']};
}}
QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox {{
    background: {p['surface']}; color: {p['text']}; border: 1px solid {p['border']};
    border-radius: 8px; padding: 7px;
    selection-background-color: {p['selection']}; selection-color: {p['text']};
}}
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {p['accent']};
}}
QCheckBox {{ spacing: 7px; padding: 4px 0; }}
QTabWidget::pane {{
    border: 1px solid {p['border']}; background: {p['surface']};
    border-radius: 8px; top: -1px;
}}
QWidget#settingsPage {{ background: {p['surface']}; }}
QTabBar::tab {{
    background: {p['background']}; color: {p['muted']};
    border: 1px solid {p['border']}; padding: 8px 14px;
    border-top-left-radius: 7px; border-top-right-radius: 7px;
}}
QTabBar::tab:selected {{ background: {p['surface']}; color: {p['heading']}; }}
QMenu, QComboBox QAbstractItemView {{
    background: {p['surface']}; color: {p['text']}; border: 1px solid {p['border']};
    selection-background-color: {p['selection']};
}}
QMenu::item {{ padding: 7px 20px; }}
QMenu::item:selected {{ background: {p['selection']}; }}
QToolTip {{
    color: {p['text']}; background: {p['surface']};
    border: 1px solid {p['border']}; padding: 5px;
}}
"""
