"""Dark futuristic theme: black ground, red accent, white text, sharp edges.

Applied as a single application-wide stylesheet rather than the per-widget
styling the app used before, so every widget - including ones added later -
picks it up without being individually painted.

Everything is square: no rounded corners anywhere, thin hard borders, and a
red accent that only appears on things that are interactive or important.
"""
from __future__ import annotations

PALETTE = {
    # grounds, darkest to lightest
    "bg":          "#08080A",
    "panel":       "#101014",
    "raised":      "#17171C",
    "input":       "#0C0C10",
    "border":      "#2A2A33",
    "border_soft": "#1E1E25",
    # accent
    "accent":      "#E01E37",
    "accent_dim":  "#8E1424",
    "accent_glow": "#FF3B52",
    # text
    "text":        "#FFFFFF",
    "text_dim":    "#A8A8B4",
    "text_faint":  "#6E6E7A",
    # status
    "ok":          "#3DD68C",
    "ok_bg":       "#0E2A1E",
    "caution":     "#F5A623",
    "caution_bg":  "#2E2310",
    "critical":    "#FF3B30",
    "critical_bg": "#2E1113",
    "nodata":      "#7A7A86",
    "nodata_bg":   "#16161B",
    # plots
    "plot_bg":     "#0C0C10",
    "plot_axes":   "#101016",
    "grid":        "#26262F",
}

# Matplotlib series colours that read on a black ground
SERIES = ["#E01E37", "#4FC3F7", "#3DD68C", "#F5A623",
          "#B388FF", "#FF8A65", "#FFFFFF", "#7A7A86"]


def stylesheet() -> str:
    p = PALETTE
    return f"""
* {{
    outline: 0;
}}
QWidget {{
    background-color: {p['bg']};
    color: {p['text']};
    font-family: "Segoe UI", "Inter", "DejaVu Sans", sans-serif;
    font-size: 10pt;
}}
QMainWindow, QDialog {{ background-color: {p['bg']}; }}

/* ---- tabs ---- */
QTabWidget::pane {{
    background: {p['panel']};
    border: 1px solid {p['border']};
    top: -1px;
}}
QTabBar::tab {{
    background: {p['bg']};
    color: {p['text_dim']};
    border: 1px solid {p['border_soft']};
    border-bottom: none;
    /* Generous horizontal padding and a minimum width: with letter-spacing
       applied, Qt sizes the tab from the unspaced text and then clips the
       first character off every label. */
    padding: 8px 20px;
    min-width: 70px;
    margin-right: 2px;
    font-weight: 600;
    font-size: 9pt;
}}
QTabBar::tab:selected {{
    background: {p['panel']};
    color: {p['text']};
    border-top: 2px solid {p['accent']};
}}
QTabBar::tab:hover:!selected {{ color: {p['text']}; background: {p['raised']}; }}

/* ---- group boxes ---- */
QGroupBox {{
    background: {p['panel']};
    border: 1px solid {p['border']};
    margin-top: 16px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 2px 8px;
    color: {p['accent']};
    background: {p['bg']};
    text-transform: uppercase;
    font-size: 9pt;
    letter-spacing: 0.8px;
}}

/* ---- inputs ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background: {p['input']};
    color: {p['text']};
    border: 1px solid {p['border']};
    padding: 6px 9px;
    min-height: 20px;
    selection-background-color: {p['accent']};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {p['accent']};
    background: {p['raised']};
}}
QLineEdit:hover, QComboBox:hover {{ border: 1px solid {p['accent_dim']}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {p['text_faint']}; background: {p['bg']}; }}
QComboBox::drop-down {{
    border: none;
    width: 22px;
    background: {p['raised']};
}}
QComboBox::down-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {p['accent']};
}}
QComboBox QAbstractItemView {{
    background: {p['raised']};
    color: {p['text']};
    border: 1px solid {p['accent_dim']};
    selection-background-color: {p['accent']};
    selection-color: #FFFFFF;
    outline: 0;
}}

/* ---- buttons ---- */
QPushButton {{
    background: {p['raised']};
    color: {p['text']};
    border: 1px solid {p['border']};
    padding: 8px 16px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    font-size: 9pt;
}}
QPushButton:hover {{
    background: {p['accent_dim']};
    border: 1px solid {p['accent']};
    color: #FFFFFF;
}}
QPushButton:pressed {{ background: {p['accent']}; }}
QPushButton:disabled {{
    background: {p['bg']};
    color: {p['text_faint']};
    border: 1px solid {p['border_soft']};
}}
QPushButton:default {{ border: 1px solid {p['accent']}; }}

/* ---- tables ---- */
QTableWidget, QTableView {{
    background: {p['panel']};
    alternate-background-color: {p['raised']};
    color: {p['text']};
    gridline-color: {p['border_soft']};
    border: 1px solid {p['border']};
    selection-background-color: {p['accent_dim']};
    selection-color: #FFFFFF;
}}
QHeaderView::section {{
    background: {p['bg']};
    color: {p['accent']};
    border: none;
    border-right: 1px solid {p['border_soft']};
    border-bottom: 1px solid {p['border']};
    padding: 7px 8px;
    font-weight: 700;
    text-transform: uppercase;
    font-size: 8.5pt;
    letter-spacing: 0.6px;
}}
QTableCornerButton::section {{ background: {p['bg']}; border: none; }}

/* ---- lists ---- */
QListWidget {{
    background: {p['panel']};
    color: {p['text']};
    border: 1px solid {p['border']};
    outline: 0;
}}
QListWidget::item {{ padding: 9px 10px; border-bottom: 1px solid {p['border_soft']}; }}
QListWidget::item:selected {{
    background: {p['accent_dim']};
    color: #FFFFFF;
    border-left: 3px solid {p['accent']};
}}
QListWidget::item:hover:!selected {{ background: {p['raised']}; }}

/* ---- text views ---- */
QTextEdit {{
    background: {p['panel']};
    color: {p['text']};
    border: 1px solid {p['border']};
    padding: 8px;
    selection-background-color: {p['accent']};
}}

/* ---- scrollbars ---- */
QScrollBar:vertical {{
    background: {p['bg']}; width: 12px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p['border']}; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['accent_dim']}; }}
QScrollBar:horizontal {{ background: {p['bg']}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {p['border']}; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {p['accent_dim']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- misc ---- */
QScrollArea {{ border: none; background: transparent; }}
QSplitter::handle {{ background: {p['border_soft']}; }}
QSplitter::handle:hover {{ background: {p['accent']}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QLabel {{ background: transparent; color: {p['text']}; }}
QCheckBox, QRadioButton {{ color: {p['text']}; spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {p['border']};
    background: {p['input']};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p['accent']};
    border: 1px solid {p['accent']};
}}
QProgressBar {{
    background: {p['input']};
    border: 1px solid {p['border']};
    text-align: center;
    color: {p['text']};
}}
QProgressBar::chunk {{ background: {p['accent']}; }}
QToolTip {{
    background: {p['raised']};
    color: {p['text']};
    border: 1px solid {p['accent']};
    padding: 5px;
}}
QMenuBar {{ background: {p['bg']}; color: {p['text']}; }}
QMenuBar::item:selected {{ background: {p['accent_dim']}; }}
QMenu {{ background: {p['raised']}; color: {p['text']}; border: 1px solid {p['border']}; }}
QMenu::item:selected {{ background: {p['accent_dim']}; }}
"""


def style_figure(figure):
    """Paint a matplotlib figure for the dark theme."""
    p = PALETTE
    figure.patch.set_facecolor(p["plot_bg"])
    for ax in figure.get_axes():
        style_axes(ax)


def style_axes(ax):
    p = PALETTE
    ax.set_facecolor(p["plot_axes"])
    ax.grid(True, color=p["grid"], alpha=0.7, linewidth=0.6)
    ax.tick_params(colors=p["text_dim"], labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(p["border"])
    ax.xaxis.label.set_color(p["text_dim"])
    ax.yaxis.label.set_color(p["text_dim"])
    ax.title.set_color(p["text"])
    legend = ax.get_legend()
    if legend is not None:
        legend.get_frame().set_facecolor(p["raised"])
        legend.get_frame().set_edgecolor(p["border"])
        for text in legend.get_texts():
            text.set_color(p["text"])


def status_colors(status_key: str):
    """(background, foreground, accent) for OK / CAUTION / CRITICAL / NO DATA."""
    p = PALETTE
    return {
        "OK":       (p["ok_bg"], p["ok"], p["ok"]),
        "CAUTION":  (p["caution_bg"], p["caution"], p["caution"]),
        "CRITICAL": (p["critical_bg"], p["critical"], p["critical"]),
        "NO DATA":  (p["nodata_bg"], p["nodata"], p["nodata"]),
    }.get(status_key, (p["panel"], p["text"], p["text"]))
