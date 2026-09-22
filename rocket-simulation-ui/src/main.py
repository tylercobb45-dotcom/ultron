from PyQt5 import QtWidgets, QtGui, QtCore
import sys
import math
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from simulation import run_simulation
import os
import re
import json
import copy
import numpy as np
import random
import traceback
import csv
from scipy.interpolate import interp1d
import matplotlib.patches as mpatches
from live_code_viewer import LiveCodeViewer  # Import our live code viewer
from engine_lab import EngineLabWidget  # Hybrid engine design tab
from report_tab import FlightReportWidget  # Failure-mode report tab
from rocket_library import RocketLibraryWidget  # Saved-rocket library tab
from vehicle_tab import VehicleTabWidget  # Airframe / launch site / recovery tab
from tolerances_tab import TolerancesTab  # Build-tolerance search (optional tab)
import tolerances  # the search itself, Qt-free
import recovery as recovery_mod
import failure_analysis as fa
from aero_tab import AeroAnalysisWidget  # Cd vs Mach analysis tab
from sections import EngineSection, AerodynamicsSection, AssemblyPanel
import theme as app_theme
import units as app_units
import unit_fields
import flight_model
import aero
import atmosphere as atmosphere_mod
import datasheet  # Flight + engine spreadsheet views
import portable_paths
import flight_equations


def user_settings_path():
    """Location of user_settings.json - see portable_paths.

    This used to write into the home folder of whatever computer the app was
    running on, which loses the point of a flash-drive copy: settings and
    saved rockets stayed behind on each machine instead of travelling with
    the drive.
    """
    return portable_paths.settings_file()

# === FULL RETRO PIXEL STYLE ===
# NOTE: Removed use of a global app stylesheet to avoid forcing retro styles over other themes.
# Theme application is now handled per-widget via apply_theme().
# === END RETRO STYLE ===

def _wind_dir_text(degrees):
    """Label for the wind dial: bearing plus the compass point it means.

    The bearing is the direction the wind blows TOWARD, which is what
    LaunchSite.wind_at resolves - at 0 degrees it returns pure north, at 90
    pure east. The dial used to be labelled "0 (East)", which is the wrong
    quarter of the compass, and it only named a direction at all before the
    dial was first moved; after that it showed a bare number. Getting this
    backwards points the drift the wrong way, and drift is a range-safety
    number.
    """
    points = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    idx = int((float(degrees) % 360.0) / 22.5 + 0.5) % 16
    return f"Wind Direction: {int(degrees)}\u00b0 (toward {points[idx]})"


class CrashImageDialog(QtWidgets.QDialog):
    def __init__(self, image_path, error_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Program Crashed!')
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel('The program has crashed!')
        label.setStyleSheet('font-size: 18px; color: #E94F37; font-weight: bold;')
        layout.addWidget(label)
        pixmap = QtGui.QPixmap(image_path)
        img_label = QtWidgets.QLabel()
        img_label.setPixmap(pixmap.scaledToWidth(400, QtCore.Qt.SmoothTransformation))
        layout.addWidget(img_label)
        error_box = QtWidgets.QTextEdit()
        error_box.setReadOnly(True)
        error_box.setText(error_text)
        error_box.setStyleSheet('color: #E94F37; background: #F8F5E3;')
        layout.addWidget(error_box)
        self.setLayout(layout)


class _WheelGuard(QtCore.QObject):
    """Event filter: ignore wheel events on controls that do not have focus.

    Passing the event on lets the enclosing scroll area handle it, so the page
    scrolls instead of the value changing.
    """

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return super().eventFilter(obj, event)


def _parse_mass_kg(text):
    """Parse a CSV metadata mass into kilograms, or None.

    These rows carry units inconsistently - JARVIS's own Engine Lab export
    writes "6.0493 kg", other tools write a bare number or grams. A bare
    number is read as kilograms, matching the .eng format the rest of the
    app now reports in.
    """
    match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(text))
    if not match:
        return None
    value = float(match.group())
    unit = str(text)[match.end():].strip().lower()
    if unit.startswith("kg"):
        return value
    if unit.startswith("g"):
        return value / 1000.0
    if unit.startswith("lb"):
        return value * 0.45359237
    return value          # bare number: kilograms


def _lock_unit_combo(combo):
    """Stop a unit combo from being squeezed out of existence.

    The theme reserves 22px of every combo for the drop-down arrow, so a combo
    that loses its width fight with a neighbouring Expanding QLineEdit renders
    as an arrow and nothing else. Measure the widest entry it actually holds,
    add room for the arrow and padding, and pin the combo there so the line
    edit absorbs the panel's width changes instead.
    """
    fm = combo.fontMetrics()
    widest = max((fm.horizontalAdvance(combo.itemText(i))
                  for i in range(combo.count())), default=0)
    combo.setMinimumWidth(widest + 46)      # 22px arrow + padding + border
    combo.setSizePolicy(QtWidgets.QSizePolicy.Fixed,
                        QtWidgets.QSizePolicy.Fixed)
    return combo


class StabilityLengthField(QtWidgets.QWidget):
    """A length with a unit picker, that follows the loaded rocket by default.

    Two problems this solves at once.

    The Stability Test tab's rocket length, centre of mass and centre of
    pressure were plain metre boxes that nothing ever filled in. Load a
    rocket and they kept whatever was typed last, so the pad margin shown
    here could describe a different vehicle from the one being flown - the
    same failure the Engine tab's preview had.

    And they were metres only, on a page where everything else can be set in
    whichever unit the numbers came in.

    So the value is held in SI internally and displayed in whichever unit is
    picked, and it tracks the vehicle until someone deliberately overrides it.
    """

    UNITS = (("m", 1.0), ("cm", 0.01), ("mm", 0.001),
             ("in", 0.0254), ("ft", 0.3048))

    def __init__(self, si_value=0.0, maximum_m=100.0, parent=None):
        super().__init__(parent)
        self._si = float(si_value)
        self._unit_index = 0
        self._loading = False

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setDecimals(3)
        self.spin.setRange(0.0, maximum_m / self.UNITS[0][1])
        self.spin.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                QtWidgets.QSizePolicy.Fixed)
        self.spin.valueChanged.connect(self._on_value_changed)
        row.addWidget(self.spin, 1)

        self.unit = QtWidgets.QComboBox()
        for name, _factor in self.UNITS:
            self.unit.addItem(name)
        self.unit.currentIndexChanged.connect(self._on_unit_changed)
        row.addWidget(self.unit, 0)

        self._maximum_m = maximum_m
        self._refresh_display()

    # -- value in SI, whatever is on screen -------------------------------
    # value()/setValue() keep the QDoubleSpinBox spelling these replaced, so
    # every existing caller keeps working and keeps getting metres.
    def value(self):
        return self._si

    def setValue(self, v):
        self.set_si_value(v)

    def si_value(self):
        return self._si

    def set_si_value(self, value):
        self._si = float(value or 0.0)
        self._refresh_display()

    def factor(self):
        return self.UNITS[self._unit_index][1]

    def _refresh_display(self):
        self._loading = True
        try:
            factor = self.factor()
            self.spin.setRange(0.0, self._maximum_m / factor)
            # Enough decimals that a millimetre still shows in any unit.
            self.spin.setDecimals(1 if factor <= 0.001 else 3)
            self.spin.setValue(self._si / factor)
        finally:
            self._loading = False

    def _on_value_changed(self, shown):
        if self._loading:
            return
        self._si = float(shown) * self.factor()
        self.valueChanged.emit(self._si)

    def _on_unit_changed(self, index):
        # Convert the value that is already there rather than reinterpreting
        # it: 1.5 m picked as inches is 1.5 m shown as 59.055 in, not 1.5 in.
        self._unit_index = index
        self._refresh_display()

    def set_editable(self, editable):
        self.spin.setReadOnly(not editable)
        self.spin.setButtonSymbols(
            QtWidgets.QAbstractSpinBox.UpDownArrows if editable
            else QtWidgets.QAbstractSpinBox.NoButtons)
        self.spin.setEnabled(True)      # stays readable either way
        self.spin.setToolTip("" if editable else
                             "Taken from the loaded rocket. Tick Override to "
                             "set it by hand.")

    valueChanged = QtCore.pyqtSignal(float)


def _scrollable(page):
    """Wrap a tab page in a vertical scroll area.

    Qt propagates every child's minimum height up, so a tab holding a long
    form makes the whole window refuse to be shorter than that form. Four of
    these tabs pushed the window's minimum height to 1190px - taller than a
    1080p desktop, and far past a 1366x768 laptop, which left the bottom of
    those tabs permanently unreachable with no way to scroll to it. Wrapping
    each page lets the window shrink to fit the screen and scroll instead.
    """
    area = QtWidgets.QScrollArea()
    area.setWidget(page)
    area.setWidgetResizable(True)
    area.setFrameShape(QtWidgets.QFrame.NoFrame)
    # Horizontal room is what these forms are short of; let them keep their
    # natural width and only ever scroll vertically.
    area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    return area


class RocketSimulationUI(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self._rocket_img_cache = {}
        
        # Initialize theme system
        self.current_theme = "futuristic"  # Dark futuristic is the default now
        self.themes = self.setup_themes()
        
        # Set user settings file path
        self.user_settings_file = user_settings_path()
        
        # Load saved theme preference
        self.current_theme = self.load_theme_preference()
        
        # Initialize live code viewer (but don't show it yet)
        self.live_code_viewer = None
        
        # Set window and taskbar icon (use .ico for best Windows compatibility)
        self.setWindowIcon(QtGui.QIcon(os.path.join(os.path.dirname(__file__), 'JARVIS.ico')))
        self._unit_combos = []       # re-measured once the theme is applied
        self.init_ui()
        self.load_inputs()  # Load inputs on startup
        self.apply_theme(self.current_theme)  # Apply initial theme
        # Tab bars measure with their own font but paint at the stylesheet's
        # weight; line the two up so multi-word tab labels stop losing their
        # last character. Runs after the whole tree exists.
        app_theme.apply_tab_fonts(self)
        # Unit combos were measured during init_ui(), before apply_theme()
        # installed the stylesheet that sets font-size: 10pt. On a platform
        # whose default font is smaller (Windows ships 9pt) they were sized
        # from the wrong metrics and clipped anyway - the exact defect
        # _lock_unit_combo exists to prevent. Re-measure now that the font
        # they are painted with is the font they are measured with.
        for combo in self._unit_combos:
            _lock_unit_combo(combo)
        self._install_wheel_guard()
        self.showMaximized()

    def _install_wheel_guard(self):
        """Stop the mouse wheel from silently editing combos and spin boxes.

        Five tab pages are now inside scroll areas. Rolling the wheel to reach
        a field lower down sends the event to whatever widget is under the
        cursor, so passing over a unit combo stepped it - "m" to "ft", "kg" to
        "lb" - which fires currentIndexChanged and reinterprets the number
        beside it, all without the page scrolling. Wheel now only edits a
        control the user has actually focused.
        """
        self._wheel_guard = _WheelGuard(self)
        for widget in self.findChildren((QtWidgets.QComboBox,
                                         QtWidgets.QSpinBox,
                                         QtWidgets.QDoubleSpinBox)):
            widget.setFocusPolicy(QtCore.Qt.StrongFocus)
            widget.installEventFilter(self._wheel_guard)

    # ---- THEME-AWARE STYLING HELPERS ----
    def get_plot_style(self):
        """Return a dict of matplotlib styling values based on the current theme."""
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            return {
                'fig_face': theme['plot_bg'],            # #F8F5E3
                'ax_face': theme['colors']['secondary_bg'],  # #FDF6E3
                'grid_color': theme['colors']['accent'],     # #BCA16A
                'grid_alpha': 0.3,
                'spine_color': theme['colors']['button_bg'], # #E94F37
                'spine_lw': 2.0,
                'tick_color': theme['colors']['primary_text'],
                'label_color': theme['colors']['primary_text'],
                'title_color': theme['colors']['primary_text'],
                'legend_face': theme['colors']['secondary_bg'],
                'legend_edge': theme['colors']['accent']
            }
        else:  # professional
            return {
                'fig_face': theme['plot_bg'],             # #1A252F
                'ax_face': theme['colors']['secondary_bg'],  # #34495E
                'grid_color': theme['colors']['primary_bg'], # #2C3E50
                'grid_alpha': 0.35,
                'spine_color': theme['colors']['accent'],    # #00D4FF
                'spine_lw': 1.2,
                'tick_color': theme['colors']['primary_text'], # #ECF0F1
                'label_color': theme['colors']['primary_text'],
                'title_color': theme['colors']['primary_text'],
                'legend_face': '#2C3E50',
                'legend_edge': theme['colors']['accent']
            }

    def style_axes(self, ax):
        """Apply theme-aware styling to a matplotlib Axes."""
        style = self.get_plot_style()
        # Figure and axes backgrounds
        if ax.figure is not None:
            ax.figure.patch.set_facecolor(style['fig_face'])
        ax.set_facecolor(style['ax_face'])
        # Grid
        ax.grid(True, alpha=style['grid_alpha'], color=style['grid_color'], linestyle=':')
        # Spines
        for spine in ax.spines.values():
            spine.set_color(style['spine_color'])
            spine.set_linewidth(style['spine_lw'])
        # Ticks and labels
        ax.tick_params(axis='both', colors=style['tick_color'])
        ax.xaxis.label.set_color(style['label_color'])
        ax.yaxis.label.set_color(style['label_color'])
        ax.title.set_color(style['title_color'])
        # Legend (if exists)
        leg = ax.get_legend()
        if leg is not None:
            leg.get_frame().set_facecolor(style['legend_face'])
            leg.get_frame().set_edgecolor(style['legend_edge'])
            for text in leg.get_texts():
                text.set_color(style['tick_color'])

    def style_toolbar(self):
        """Apply theme-aware styling to the matplotlib navigation toolbar."""
        if not hasattr(self, 'toolbar'):
            return
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            ss = f"""
                QWidget {{
                    background-color: {theme['colors']['primary_bg']};
                    border: 2px solid {theme['colors']['accent']};
                    color: {theme['colors']['primary_text']};
                    font-family: 'Press Start 2P', monospace;
                    font-size: 12px;
                }}
            """
        else:
            # Subtle, dark toolbar with accent border
            ss = f"""
                QWidget {{
                    background-color: {theme['colors']['primary_bg']};
                    border: 1px solid {theme['colors']['accent']};
                    color: {theme['colors']['primary_text']};
                    font-family: 'Consolas', 'Monaco', monospace;
                    font-size: 12px;
                }}
            """
        # Apply the stylesheet to the toolbar
        self.toolbar.setStyleSheet(ss)

    # ---- Launch angle recommendation helpers ----
    def compute_recommended_launch_angle_deg(self):
        """Recommend a small tilt (±0–5°) into the wind. 0° when calm.
        Simple heuristic: angle = clamp(k * wind_speed, 0..5), sign into-wind.
        We interpret positive angle as tilting toward wind direction's coming-from azimuth to reduce drift.
        """
        try:
            ws = float(self.wind_speed_input.value())  # m/s
            wd = float(self.wind_direction_input.value())  # degrees, 0..359
        except Exception:
            return 0.0
        # Map speed to degrees with a gentle slope. Example: 0 deg at 0 m/s, 5 deg at >= 10 m/s
        k = 5.0 / 10.0
        mag = max(0.0, min(5.0, k * ws))
        # Sign convention: positive angle means tilt to the right in our XY view. We can map wind direction to a left/right component.
        # For simplicity in 2D, use cosine to decide sign: wind from 90° (from +X) -> tilt negative (left), from 270° -> tilt positive (right)
        # Compute sign = -cos(wd) so that wind from +X (90°) gives negative tilt; from -X (270°) gives positive.
        sign = -math.cos(math.radians(wd))
        # Normalize sign to -1, 0, 1 threshold to avoid tiny noise
        s = 1.0 if sign > 0.2 else (-1.0 if sign < -0.2 else 0.0)
        return mag * s

    def update_recommended_launch_angle_label(self):
        rec = self.compute_recommended_launch_angle_deg()
        ws = self.wind_speed_input.value()
        if abs(rec) < 0.05:
            txt = f"Recommended: 0.0° (calm)"
        else:
            txt = f"Recommended: {rec:+.1f}° (wind {ws:.1f} m/s)"
        if hasattr(self, 'recommended_angle_label'):
            self.recommended_angle_label.setText(txt)

    def apply_recommended_launch_angle(self):
        rec = self.compute_recommended_launch_angle_deg()
        # Bound to control range just in case
        rec = max(self.launch_angle_input.minimum(), min(self.launch_angle_input.maximum(), rec))
        self.launch_angle_input.setValue(rec)

    def restyle_all_plots(self):
        """Restyle any existing figures/axes and redraw canvases."""
        # Main results figure
        if hasattr(self, 'figure') and hasattr(self, 'canvas'):
            for ax in self.figure.axes:
                self.style_axes(ax)
            self.canvas.draw_idle()
        # Launch/animation figure
        if hasattr(self, 'launch_fig') and hasattr(self, 'launch_canvas'):
            for ax in self.launch_fig.axes:
                self.style_axes(ax)
            self.launch_canvas.draw_idle()

    def setup_themes(self):
        """Define all available themes for the application"""
        themes = {
            "retro": {
                "name": "JARVIS Retro",
                "description": "Classic 8BitDo retro gaming aesthetic",
                "colors": {
                    "primary_bg": "#F8F5E3",
                    "secondary_bg": "#FDF6E3", 
                    "accent": "#BCA16A",
                    "primary_text": "#3C2F1E",
                    "button_bg": "#E94F37",
                    "button_hover": "#FFD447",
                    "button_text": "#F8F5E3",
                    "success": "#2E8B57",
                    "warning": "#FF6347",
                    "info": "#4169E1"
                },
                "telemetry": {
                    "bg": "#F8F5E3",
                    "border": "#BCA16A",
                    "header_bg": "#3C2F1E",
                    "header_text": "#FFD447",
                    "gauge_bg": "#FFFFFF",
                    "gauge_text": "#3C2F1E",
                    "status_active": "#32CD32",
                    "status_inactive": "#C0C0C0"
                },
                "plot_bg": "#F8F5E3"
            },
            "professional": {
                "name": "Aerospace Professional",
                "description": "Mission control center dark theme",
                "colors": {
                    "primary_bg": "#2C3E50",
                    "secondary_bg": "#34495E",
                    "accent": "#00D4FF",
                    "primary_text": "#ECF0F1",
                    "button_bg": "#E94F37",
                    "button_hover": "#FF6B35",
                    "button_text": "#FFFFFF",
                    "success": "#00FF41",
                    "warning": "#FF6B35",
                    "info": "#0099FF"
                },
                "telemetry": {
                    "bg": "qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #2C3E50, stop: 1 #34495E)",
                    "border": "#1A252F",
                    "header_bg": "qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #1A252F, stop: 1 #0F1419)",
                    "header_text": "#00D4FF",
                    "gauge_bg": "qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 rgba(26, 37, 47, 0.9), stop: 1 rgba(15, 20, 25, 0.9))",
                    "gauge_text": "#ECF0F1",
                    "status_active": "#00FF41",
                    "status_inactive": "#555555"
                },
                "plot_bg": "#1A252F"
            }
        }
        # Build the dark theme from an existing one so it is guaranteed to
        # carry every key the widget-styling code reads, then recolour it.
        # Missing a single key here raises deep inside init_ui, and the app's
        # own crash handler turns that into a modal dialog that just hangs.
        pal = app_theme.PALETTE
        futuristic = copy.deepcopy(themes["professional"])
        futuristic["name"] = "Dark Futuristic"
        futuristic["description"] = "Black ground, red accent, white text, sharp edges"
        futuristic["plot_bg"] = pal["plot_bg"]

        def _recolour(key, value):
            k = key.lower()
            if "gradient" in str(value):
                return pal["panel"]
            if "status_active" in k:
                return pal["ok"]
            if "status_inactive" in k:
                return pal["text_faint"]
            if "accent" in k:
                return pal["accent"]
            if "border" in k:
                return pal["border"]
            if "button_bg" in k or "gauge_bg" in k or "header_bg" in k:
                return pal["raised"]
            # primary_bg has to be tested BEFORE the generic _bg catch-all,
            # or the base ground silently takes the panel colour.
            if "primary_bg" in k:
                return pal["bg"]
            if "secondary_bg" in k or k.endswith("_bg") or k == "bg":
                return pal["panel"]
            if "text" in k:
                return pal["text"]
            return value

        for section in ("colors", "telemetry"):
            block = futuristic.get(section)
            if isinstance(block, dict):
                for key in list(block):
                    block[key] = _recolour(key, block[key])
        themes["futuristic"] = futuristic
        return themes


    def apply_theme(self, theme_name):
        """Apply the selected theme to the entire application"""
        if theme_name not in self.themes:
            return
            
        self.current_theme = theme_name
        theme = self.themes[theme_name]
        
        # Apply base application style
        if theme_name == "futuristic":
            self.apply_futuristic_theme(theme)
        elif theme_name == "retro":
            self.apply_retro_theme(theme)
        elif theme_name == "professional":
            self.apply_professional_theme(theme)
            
        # Update telemetry dashboard if it exists
        if hasattr(self, 'altitude_display'):
            self.update_telemetry_theme()
            
        # Update plot backgrounds
        if hasattr(self, 'canvas'):
            self.canvas.figure.patch.set_facecolor(theme["plot_bg"])
            self.restyle_all_plots()
        if hasattr(self, 'launch_canvas'):
            self.launch_canvas.figure.patch.set_facecolor(theme["plot_bg"])
            self.restyle_all_plots()

        # Update toolbar styling
        self.style_toolbar()
        
        # Update force diagram styling
        if hasattr(self, 'force_widget'):
            self.setup_force_widget_styling()
            self.setup_force_header()
        if hasattr(self, 'force_canvas'):
            self.force_canvas.figure.patch.set_facecolor(theme["plot_bg"])
            self.update_force_diagram()
            
        # Save theme preference
        self.save_theme_preference()

    def apply_futuristic_theme(self, theme):
        """Dark futuristic theme.

        Unlike the other two, this one does not paint widgets individually -
        the whole look comes from the application-wide stylesheet in theme.py.
        All this has to do is strip the per-widget sheets the other themes
        leave behind, otherwise their cream backgrounds sit on top of it
        (a widget stylesheet always beats the application one).
        """
        self.setStyleSheet("")
        pal = app_theme.PALETTE
        for name, extra in (('result_label', f"border-left:3px solid {pal['accent']};"),
                            ('error_label', f"color:{pal['critical']}; font-weight:bold;")):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setStyleSheet(
                    f"background:{pal['panel']}; color:{pal['text']}; "
                    f"border:1px solid {pal['border']}; padding:10px; {extra}")
        # The application-wide sheet is installed once at startup; re-applying
        # it here forces a full restyle of the entire widget tree on every
        # theme switch, which is slow and unnecessary.
        app = QtWidgets.QApplication.instance()
        if app is not None and not app.styleSheet():
            app.setStyleSheet(app_theme.stylesheet())

    def apply_retro_theme(self, theme):
        """Apply the retro JARVIS theme"""
        retro_style = f"""
            QWidget {{
                background-color: {theme['colors']['primary_bg']};
                font-family: 'Press Start 2P', monospace;
                font-size: 12px;
                color: {theme['colors']['primary_text']};
            }}

            QLineEdit {{
                background-color: {theme['colors']['secondary_bg']};
                border: 2px solid {theme['colors']['accent']};
                border-radius: 4px;
                padding: 4px;
                color: {theme['colors']['primary_text']};
            }}

            QPushButton {{
                background-color: {theme['colors']['button_bg']};
                border: 2px solid {theme['colors']['primary_text']};
                border-radius: 6px;
                padding: 6px;
                font-weight: bold;
                color: {theme['colors']['button_text']};
            }}

            QPushButton:hover {{
                background-color: {theme['colors']['button_hover']};
                border: 2px solid {theme['colors']['button_bg']};
                color: {theme['colors']['primary_text']};
            }}

            QLabel {{
                font-weight: bold;
                color: {theme['colors']['primary_text']};
            }}

            QSplitter::handle {{
                background-color: {theme['colors']['accent']};
            }}
            
            QTabWidget::pane {{
                border: 2px solid {theme['colors']['accent']};
                border-radius: 4px;
            }}
            
            QTabBar::tab {{
                background-color: {theme['colors']['secondary_bg']};
                border: 2px solid {theme['colors']['accent']};
                padding: 8px 16px;
                margin: 2px;
            }}
            
            QTabBar::tab:selected {{
                background-color: {theme['colors']['button_bg']};
                color: {theme['colors']['button_text']};
            }}
            
            QGroupBox {{
                font-weight: bold;
                border: 2px solid {theme['colors']['accent']};
                border-radius: 6px;
                margin-top: 6px;
                padding-top: 6px;
            }}
            
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px 0 4px;
                background-color: {theme['colors']['primary_bg']};
            }}
            
            QComboBox {{
                background-color: {theme['colors']['secondary_bg']};
                border: 2px solid {theme['colors']['accent']};
                border-radius: 4px;
                padding: 4px;
            }}
        """
        self.setStyleSheet(retro_style)

    def apply_professional_theme(self, theme):
        """Apply the professional aerospace theme"""
        professional_style = f"""
            QWidget {{
                background: {theme['telemetry']['bg']};
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 12px;
                color: {theme['colors']['primary_text']};
            }}

            QLineEdit {{
                background-color: {theme['colors']['secondary_bg']};
                border: 1px solid {theme['colors']['accent']};
                border-radius: 4px;
                padding: 6px;
                color: {theme['colors']['primary_text']};
            }}

            QPushButton {{
                background-color: {theme['colors']['button_bg']};
                border: 1px solid {theme['colors']['accent']};
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
                color: {theme['colors']['button_text']};
            }}

            QPushButton:hover {{
                background-color: {theme['colors']['button_hover']};
                border: 1px solid {theme['colors']['button_bg']};
            }}

            QLabel {{
                color: {theme['colors']['primary_text']};
                font-weight: normal;
            }}

            QSplitter::handle {{
                background-color: {theme['colors']['accent']};
            }}
            
            QTabWidget::pane {{
                border: 1px solid {theme['colors']['accent']};
                border-radius: 4px;
                background: {theme['colors']['primary_bg']};
            }}
            
            QTabBar::tab {{
                background: {theme['colors']['secondary_bg']};
                border: 1px solid {theme['colors']['accent']};
                padding: 8px 16px;
                margin: 1px;
                color: {theme['colors']['primary_text']};
            }}
            
            QTabBar::tab:selected {{
                background-color: {theme['colors']['accent']};
                color: {theme['colors']['primary_bg']};
            }}
            
            QGroupBox {{
                font-weight: bold;
                color: {theme['colors']['accent']};
                border: 1px solid {theme['colors']['accent']};
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 8px;
                background: rgba(26, 37, 47, 0.8);
            }}
            
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px 0 6px;
                background-color: rgba(26, 37, 47, 1);
                border: 1px solid {theme['colors']['accent']};
                border-radius: 2px;
            }}
            
            QComboBox {{
                background-color: {theme['colors']['secondary_bg']};
                border: 1px solid {theme['colors']['accent']};
                border-radius: 4px;
                padding: 6px;
                color: {theme['colors']['primary_text']};
            }}
            
            QComboBox::drop-down {{
                border: none;
            }}
            
            QComboBox::down-arrow {{
                border: none;
                color: {theme['colors']['accent']};
            }}
        """
        self.setStyleSheet(professional_style)



    def update_theme_preview(self):
        """Update the theme preview display"""
        if not hasattr(self, 'theme_preview'):
            return
        
        # Ensure we have a valid theme
        if self.current_theme not in self.themes:
            return
            
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            preview_style = f"""
                QLabel {{
                    background-color: {theme['colors']['primary_bg']};
                    border: 2px solid {theme['colors']['accent']};
                    border-radius: 4px;
                    color: {theme['colors']['primary_text']};
                    font-weight: bold;
                    padding: 8px;
                    qproperty-alignment: AlignCenter;
                }}
            """
            self.theme_preview.setText("🚀 RETRO JARVIS THEME")
        else:
            preview_style = f"""
                QLabel {{
                    background: {theme['telemetry']['bg']};
                    border: 1px solid {theme['telemetry']['border']};
                    border-radius: 4px;
                    color: {theme['colors']['accent']};
                    font-weight: bold;
                    padding: 8px;
                    qproperty-alignment: AlignCenter;
                    font-family: 'Consolas', 'Monaco', monospace;
                }}
            """
            self.theme_preview.setText("🛰️ AEROSPACE PROFESSIONAL")
            
        self.theme_preview.setStyleSheet(preview_style)

    def save_theme_preference(self):
        """Save the current theme preference to user settings"""
        if hasattr(self, 'user_settings_file'):
            try:
                with open(self.user_settings_file, 'r') as f:
                    settings = json.load(f)
            except:
                settings = {}
            
            settings['theme'] = self.current_theme
            
            try:
                with open(self.user_settings_file, 'w') as f:
                    json.dump(settings, f, indent=2)
            except:
                pass

    def load_theme_preference(self):
        """Load the saved theme preference"""
        if hasattr(self, 'user_settings_file'):
            try:
                with open(self.user_settings_file, 'r') as f:
                    settings = json.load(f)
                    return settings.get('theme', 'futuristic')
            except:
                pass
        return 'futuristic'

    def update_telemetry_theme(self):
        """Update telemetry dashboard styling based on current theme"""
        if not hasattr(self, 'telemetry_widget'):
            return
        # Stop previous timer if any
        try:
            if hasattr(self, 'telemetry_timer') and self.telemetry_timer is not None:
                self.telemetry_timer.stop()
                self.telemetry_timer.deleteLater()
        except Exception:
            pass
        self.telemetry_timer = None

        # Clear telemetry widget layout and rebuild with current theme
        layout = self.telemetry_widget.layout()
        if layout is None:
            layout = QtWidgets.QVBoxLayout(self.telemetry_widget)

        def clear_layout(lay):
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
                else:
                    sub = item.layout()
                    if sub is not None:
                        clear_layout(sub)
        clear_layout(layout)

        # Reapply container/header styling then recreate dashboard
        self.setup_telemetry_widget_styling()
        self.setup_telemetry_header()
        self.create_telemetry_dashboard(layout)
        self.setup_status_display_styling()

    def setup_telemetry_widget_styling(self):
        """Apply theme-aware styling to telemetry widget"""
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            style = f"""
                QWidget {{
                    background-color: {theme['colors']['primary_bg']};
                    border: 2px solid {theme['colors']['accent']};
                    border-radius: 8px;
                    margin: 2px;
                }}
            """
        else:
            style = f"""
                QWidget {{
                    background: {theme['telemetry']['bg']};
                    border: 1px solid {theme['telemetry']['border']};
                    border-radius: 8px;
                    margin: 2px;
                }}
            """
        self.telemetry_widget.setStyleSheet(style)

    def setup_telemetry_header(self):
        """Apply theme-aware styling to telemetry header"""
        if not hasattr(self, 'telemetry_header') or self.telemetry_header is None:
            return
            
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            self.telemetry_header.setText("")  # Remove header text
            style = "QLabel { background: transparent; margin: 0px; padding: 0px; }"
        else:
            self.telemetry_header.setText("")  # Remove header text
            style = "QLabel { background: transparent; margin: 0px; padding: 0px; }"
        
        try:
            self.telemetry_header.setStyleSheet(style)
            self.telemetry_header.setMaximumHeight(0)  # Hide completely
        except RuntimeError:
            # Widget has been deleted, skip styling
            pass

    def setup_force_widget_styling(self):
        """Setup theme-aware styling for force widget"""
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            style = f"""
                QWidget {{
                    background-color: {theme['colors']['primary_bg']};
                    border: 2px solid {theme['colors']['accent']};
                    border-radius: 8px;
                    margin: 2px;
                }}
            """
        else:
            style = f"""
                QWidget {{
                    background: {theme['telemetry']['bg']};
                    border: 1px solid {theme['telemetry']['border']};
                    border-radius: 4px;
                    margin: 2px;
                }}
            """
        self.force_widget.setStyleSheet(style)

    def setup_force_header(self):
        """Setup theme-aware force diagram header"""
        if not hasattr(self, 'force_header') or self.force_header is None:
            return
            
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            self.force_header.setText("")  # Remove header text
            style = "QLabel { background: transparent; margin: 0px; padding: 0px; }"
        else:
            self.force_header.setText("")  # Remove header text
            style = "QLabel { background: transparent; margin: 0px; padding: 0px; }"
        
        try:
            self.force_header.setStyleSheet(style)
            self.force_header.setMaximumHeight(0)  # Hide completely
        except RuntimeError:
            # Widget has been deleted, skip styling
            pass

    def setup_trajectory_widget_styling(self):
        """Apply theme-aware styling to trajectory widget"""
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            style = f"""
                QWidget {{
                    background-color: {theme['colors']['primary_bg']};
                    border: 2px solid {theme['colors']['accent']};
                    border-radius: 8px;
                    margin: 2px;
                }}
            """
        else:
            style = f"""
                QWidget {{
                    background: {theme['telemetry']['bg']};
                    border: 1px solid {theme['telemetry']['border']};
                    border-radius: 8px;
                    margin: 2px;
                }}
            """
        self.trajectory_widget.setStyleSheet(style)

    def setup_trajectory_header(self):
        """Apply theme-aware styling to trajectory header"""
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            self.trajectory_header.setText("📈 TRAJECTORY VISUALIZATION")
            self.trajectory_header.setAlignment(QtCore.Qt.AlignCenter)
            style = f"""
                QLabel {{
                    background-color: {theme['colors']['primary_text']};
                    color: {theme['colors']['button_hover']};
                    font-size: 16px;
                    font-weight: bold;
                    padding: 8px;
                    border: 2px solid {theme['colors']['accent']};
                    border-radius: 6px;
                    margin-bottom: 8px;
                }}
            """
        else:
            self.trajectory_header.setText("FLIGHT PATH VISUALIZATION")
            self.trajectory_header.setAlignment(QtCore.Qt.AlignCenter)
            style = f"""
                QLabel {{
                    background: {theme['telemetry']['header_bg']};
                    color: {theme['telemetry']['header_text']};
                    font-size: 14px;
                    font-weight: bold;
                    font-family: 'Consolas', 'Monaco', monospace;
                    padding: 12px;
                    border: 1px solid {theme['colors']['secondary_bg']};
                    border-radius: 4px;
                    margin-bottom: 8px;
                    letter-spacing: 2px;
                }}
            """
        self.trajectory_header.setStyleSheet(style)

    def setup_plot_background(self):
        """Set plot background based on current theme"""
        theme = self.themes[self.current_theme]
        self.launch_fig.patch.set_facecolor(theme["plot_bg"])

    def setup_canvas_styling(self):
        """Apply theme-aware styling to canvas"""
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            style = f"""
                QWidget {{
                    border: 1px solid {theme['colors']['accent']};
                    border-radius: 4px;
                }}
            """
        else:
            style = f"""
                QWidget {{
                    border: 1px solid {theme['colors']['secondary_bg']};
                    border-radius: 4px;
                    background-color: {theme['plot_bg']};
                }}
            """
        self.launch_canvas.setStyleSheet(style)

    def setup_group_styling(self, group):
        """Apply theme-aware styling to group boxes"""
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            group.setStyleSheet(f"""
                QGroupBox {{
                    font-size: 12px;
                    font-weight: bold;
                    color: {theme['colors']['primary_text']};
                    border: 2px solid {theme['colors']['accent']};
                    border-radius: 6px;
                    margin-top: 6px;
                    padding-top: 6px;
                }}
                QGroupBox::title {{
                    subcontrol-origin: margin;
                    left: 8px;
                    padding: 0 4px 0 4px;
                    background-color: {theme['colors']['primary_bg']};
                }}
            """)
        else:
            group.setStyleSheet(f"""
                QGroupBox {{
                    font-size: 11px;
                    font-weight: bold;
                    font-family: 'Consolas', 'Monaco', monospace;
                    color: {theme['colors']['accent']};
                    border: 1px solid {theme['colors']['secondary_bg']};
                    border-radius: 4px;
                    margin-top: 8px;
                    padding-top: 8px;
                    background: rgba(26, 37, 47, 0.8);
                }}
                QGroupBox::title {{
                    subcontrol-origin: margin;
                    left: 12px;
                    padding: 0 6px 0 6px;
                    background-color: rgba(26, 37, 47, 1);
                    border: 1px solid {theme['colors']['secondary_bg']};
                    border-radius: 2px;
                }}
            """)

    def setup_status_display_styling(self):
        """Apply theme-aware styling to status displays"""
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            phase_style = f"""
                QLabel {{
                    background-color: {theme['colors']['primary_text']};
                    color: {theme['colors']['button_hover']};
                    font-size: 14px;
                    font-weight: bold;
                    padding: 8px 12px;
                    border: 2px solid {theme['colors']['accent']};
                    border-radius: 6px;
                }}
            """
            time_style = f"""
                QLabel {{
                    background-color: {theme['colors']['success']};
                    color: {theme['colors']['primary_bg']};
                    font-size: 14px;
                    font-weight: bold;
                    padding: 8px 12px;
                    border: 2px solid {theme['colors']['accent']};
                    border-radius: 6px;
                }}
            """
        else:
            phase_style = f"""
                QLabel {{
                    background: {theme['telemetry']['header_bg']};
                    color: {theme['colors']['success']};
                    font-size: 13px;
                    font-weight: bold;
                    font-family: 'Consolas', 'Monaco', monospace;
                    padding: 10px 16px;
                    border: 1px solid {theme['colors']['secondary_bg']};
                    border-radius: 3px;
                    letter-spacing: 1px;
                }}
            """
            time_style = f"""
                QLabel {{
                    background: {theme['telemetry']['header_bg']};
                    color: {theme['colors']['accent']};
                    font-size: 13px;
                    font-weight: bold;
                    font-family: 'Consolas', 'Monaco', monospace;
                    padding: 10px 16px;
                    border: 1px solid {theme['colors']['secondary_bg']};
                    border-radius: 3px;
                    letter-spacing: 1px;
                }}
            """
        
        try:
            if hasattr(self, 'phase_display') and self.phase_display is not None:
                self.phase_display.setStyleSheet(phase_style)
            if hasattr(self, 'time_display') and self.time_display is not None:
                self.time_display.setStyleSheet(time_style)
        except RuntimeError:
            # Widgets have been deleted, skip styling
            pass

    def create_retro_gauge(self, label, value, unit, color):
        """Create a retro-themed gauge display"""
        widget = QtWidgets.QFrame()
        widget.setFrameStyle(QtWidgets.QFrame.StyledPanel)
        theme = self.themes["retro"]
        widget.setStyleSheet(f"""
            QFrame {{
                background-color: {theme['colors']['secondary_bg']};
                border: 2px solid {color};
                border-radius: 8px;
                margin: 2px;
            }}
            QLabel {{
                background-color: transparent;
                color: {theme['colors']['primary_text']};
            }}
        """)
        
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        
        # Label
        label_widget = QtWidgets.QLabel(label)
        label_widget.setAlignment(QtCore.Qt.AlignCenter)
        label_widget.setStyleSheet(f"""
            font-size: 10px; 
            color: {color}; 
            font-weight: bold;
            text-transform: uppercase;
        """)
        
        # Value
        value_text = f"{value} {unit}" if unit else value
        value_widget = QtWidgets.QLabel(value_text)
        value_widget.setAlignment(QtCore.Qt.AlignCenter)
        value_widget.setStyleSheet(f"""
            font-size: 16px; 
            font-weight: bold; 
            color: {theme['colors']['primary_text']};
            padding: 4px;
        """)
        
        layout.addWidget(label_widget)
        layout.addWidget(value_widget)
        
        # Store references
        widget.value_label = value_widget
        widget.unit = unit
        widget.gauge_color = color
        
        return widget

    def create_retro_indicator(self, label, icon):
        """Create a retro-themed status indicator"""
        widget = QtWidgets.QFrame()
        theme = self.themes["retro"]
        widget.setStyleSheet(f"""
            QFrame {{
                background-color: {theme['colors']['secondary_bg']};
                border: 1px solid {theme['colors']['accent']};
                border-radius: 6px;
                padding: 4px;
            }}
        """)
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        
        # Icon and status
        status_layout = QtWidgets.QHBoxLayout()
        
        icon_label = QtWidgets.QLabel(icon)
        icon_label.setAlignment(QtCore.Qt.AlignCenter)
        icon_label.setStyleSheet("font-size: 14px;")
        
        circle = QtWidgets.QLabel("●")
        circle.setAlignment(QtCore.Qt.AlignCenter)
        circle.setStyleSheet(f"color: {theme['telemetry']['status_inactive']}; font-size: 12px;")
        
        status_layout.addWidget(icon_label)
        status_layout.addWidget(circle)
        
        # Label
        text = QtWidgets.QLabel(label)
        text.setAlignment(QtCore.Qt.AlignCenter)
        text.setStyleSheet(f"""
            font-size: 9px; 
            color: {theme['colors']['primary_text']}; 
            font-weight: bold;
        """)
        
        layout.addLayout(status_layout)
        layout.addWidget(text)
        
        # Store references
        widget.status_circle = circle
        widget.is_active = False
        
        return widget


    def setup_menu_bar(self):
        """Setup menu bar with presentation features"""
        # Since QWidget doesn't have a menu bar, we'll create a main window wrapper
        # For now, let's add a button in the interface instead
        pass

    def show_live_code_viewer(self):
        """Show or bring to front the live code viewer window"""
        print("🔴 Live Code Viewer button clicked!")  # Debug output
        
        # Show a message box to confirm button click
        QtWidgets.QMessageBox.information(self, "Live Code Viewer", "Opening Live Code Viewer window...")
        
        if self.live_code_viewer is None:
            print("Creating new Live Code Viewer...")  # Debug output
            self.live_code_viewer = LiveCodeViewer(self)
            
            # Connect to main app events for better integration
            self.live_code_viewer.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        
        print("Positioning and showing window...")  # Debug output
        
        # Position the window optimally for presentations
        screen = QtWidgets.QApplication.desktop().screenGeometry()
        main_rect = self.geometry()
        
        # If there's room to the right, place it there
        if main_rect.right() + 1000 < screen.width():
            viewer_x = main_rect.right() + 10
            viewer_y = main_rect.top()
        else:
            # Otherwise, tile vertically
            viewer_x = main_rect.left()
            viewer_y = main_rect.bottom() + 10
        
        print(f"Positioning at: {viewer_x}, {viewer_y}")  # Debug output
        
        self.live_code_viewer.move(viewer_x, viewer_y)
        self.live_code_viewer.show()
        self.live_code_viewer.raise_()
        self.live_code_viewer.activateWindow()
        
        print("Window should be visible now")  # Debug output
        
        # Update button text to indicate viewer is open
        self.live_code_button.setText('🟢 Code Viewer Open')
        
        # Auto-start demo mode for presentations (disabled for now to avoid issues)
        # QtCore.QTimer.singleShot(1000, self.live_code_viewer.run_demo)

    def init_ui(self):
        self.setWindowTitle('JARVIS')
        
        # Add menu bar for presentation features
        self.setup_menu_bar()
        
        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)  # No margins on main window
        main_layout.setSpacing(0)  # No spacing
        self.tabs = QtWidgets.QTabWidget()
        main_panel = self.main_panel = QtWidgets.QWidget()
        main_panel_layout = QtWidgets.QHBoxLayout(main_panel)
        main_panel_layout.setContentsMargins(0, 0, 0, 0)  # No margins on main panel

        # Left panel: Inputs
        left_widget = QtWidgets.QWidget()
        left_widget.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        left_widget.setMinimumWidth(330)  # Splitter governs the width from here
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        # The assembly panel goes in at the top once both design sections
        # exist; a rocket is the pairing they describe.
        self._sim_left_layout = left_layout
        left_layout.setContentsMargins(8, 8, 8, 8)  # More comfortable margins
        left_layout.setSpacing(8)  # Better spacing between elements

        form_layout = QtWidgets.QFormLayout()
        form_layout.setVerticalSpacing(8)  # More comfortable vertical spacing
        form_layout.setHorizontalSpacing(8)  # Better horizontal spacing
        form_layout.setContentsMargins(8, 8, 8, 8)  # Some margins for better look
        # Input fields
        self.mass_input = QtWidgets.QLineEdit()
        self.mass_input.setPlaceholderText("e.g., 5.0")
        self.mass_unit = QtWidgets.QComboBox(); self.mass_unit.addItems(["kg", "g", "lb"])
        mass_row = QtWidgets.QHBoxLayout(); mass_row.addWidget(self.mass_input); mass_row.addWidget(self.mass_unit)

        # New: Propellant mass (subtracted from start mass during burn)
        self.prop_mass_input = QtWidgets.QLineEdit()
        self.prop_mass_input.setPlaceholderText("e.g., 1.2")
        self.prop_mass_unit = QtWidgets.QComboBox(); self.prop_mass_unit.addItems(["kg", "g", "lb"])
        prop_mass_row = QtWidgets.QHBoxLayout(); prop_mass_row.addWidget(self.prop_mass_input); prop_mass_row.addWidget(self.prop_mass_unit)

        self.cd_input = QtWidgets.QLineEdit()
        self.cd_input.setPlaceholderText("e.g., 0.7")

        self.area_input = QtWidgets.QLineEdit()
        self.area_input.setPlaceholderText("e.g., 0.0045")
        self.area_unit = QtWidgets.QComboBox(); self.area_unit.addItems(["m²", "cm²", "ft²"])
        area_row = QtWidgets.QHBoxLayout(); area_row.addWidget(self.area_input); area_row.addWidget(self.area_unit)

        self.rho_input = QtWidgets.QLineEdit()
        self.rho_input.setPlaceholderText("e.g., 1.225")
        self.rho_unit = QtWidgets.QComboBox(); self.rho_unit.addItems(["kg/m³", "g/cm³", "lb/ft³"])
        rho_row = QtWidgets.QHBoxLayout(); rho_row.addWidget(self.rho_input); rho_row.addWidget(self.rho_unit)

        self.timestep_input = QtWidgets.QLineEdit()
        self.timestep_input.setPlaceholderText("e.g., 0.1")
        self.timestep_input.setText("0.1")
        self.timestep_unit = QtWidgets.QComboBox(); self.timestep_unit.addItems(["s", "ms"])
        timestep_row = QtWidgets.QHBoxLayout(); timestep_row.addWidget(self.timestep_input); timestep_row.addWidget(self.timestep_unit)

        self.fin_count_input = QtWidgets.QLineEdit()
        self.fin_count_input.setPlaceholderText("e.g., 3")
        
        self.fin_thickness_input = QtWidgets.QLineEdit()
        self.fin_thickness_input.setPlaceholderText("e.g., 0.003")
        self.fin_thickness_unit = QtWidgets.QComboBox(); self.fin_thickness_unit.addItems(["m", "mm", "in"])
        fin_thickness_row = QtWidgets.QHBoxLayout(); fin_thickness_row.addWidget(self.fin_thickness_input); fin_thickness_row.addWidget(self.fin_thickness_unit)

        self.fin_length_input = QtWidgets.QLineEdit()
        self.fin_length_input.setPlaceholderText("e.g., 0.1")
        self.fin_length_unit = QtWidgets.QComboBox(); self.fin_length_unit.addItems(["m", "mm", "in"])
        fin_length_row = QtWidgets.QHBoxLayout(); fin_length_row.addWidget(self.fin_length_input); fin_length_row.addWidget(self.fin_length_unit)

        self.body_diameter_input = QtWidgets.QLineEdit()
        self.body_diameter_input.setPlaceholderText("e.g., 0.06")
        self.body_diameter_unit = QtWidgets.QComboBox(); self.body_diameter_unit.addItems(["m", "mm", "in"])
        body_diameter_row = QtWidgets.QHBoxLayout(); body_diameter_row.addWidget(self.body_diameter_input); body_diameter_row.addWidget(self.body_diameter_unit)

        self.chute_height_input = QtWidgets.QLineEdit()
        self.chute_height_input.setPlaceholderText("e.g., 500")
        self.chute_height_unit = QtWidgets.QComboBox(); self.chute_height_unit.addItems(["m", "ft"])
        chute_height_row = QtWidgets.QHBoxLayout(); chute_height_row.addWidget(self.chute_height_input); chute_height_row.addWidget(self.chute_height_unit)

        self.chute_size_input = QtWidgets.QLineEdit()
        self.chute_size_input.setPlaceholderText("e.g., 0.5")
        self.chute_size_unit = QtWidgets.QComboBox(); self.chute_size_unit.addItems(["m²", "ft²"])
        chute_size_row = QtWidgets.QHBoxLayout(); chute_size_row.addWidget(self.chute_size_input); chute_size_row.addWidget(self.chute_size_unit)

        # Inputs are styled by the application-wide theme (theme.py).
        # Themes that want their own look re-apply it in apply_*_theme().
        input_style = ""
        
        for widget in [self.mass_input, self.prop_mass_input, self.cd_input, self.area_input, self.rho_input,
                       self.timestep_input,
                       self.fin_count_input, self.fin_thickness_input, self.fin_length_input, self.body_diameter_input,
                       self.chute_height_input, self.chute_size_input]:
            widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            widget.setStyleSheet(input_style)
            
        combo_style = ""

        # Unit combos sit next to an Expanding QLineEdit in a QHBoxLayout. With
        # no width floor of their own they lose every pixel to the line edit as
        # the panel narrows, and because the theme reserves 22px for the
        # drop-down arrow they collapse to an unreadable red square - the unit
        # is then neither legible nor selectable. Size each to its own widest
        # entry ("kg/m3", "lb/ft3") and hold it there.
        for combo in [self.mass_unit, self.prop_mass_unit, self.area_unit, self.rho_unit, self.timestep_unit,
                       self.fin_thickness_unit, self.fin_length_unit, self.body_diameter_unit,
                       self.chute_height_unit, self.chute_size_unit]:
            combo.setStyleSheet(combo_style)
            _lock_unit_combo(combo)
            self._unit_combos.append(combo)
            
        for widget in [self.mass_input, self.prop_mass_input, self.cd_input, self.area_input, self.rho_input,
                       self.timestep_input,
                       self.fin_count_input, self.fin_thickness_input, self.fin_length_input, self.body_diameter_input,
                       self.chute_height_input, self.chute_size_input]:
            widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            widget.setStyleSheet(input_style)
            # A cross-sectional area reads "0.004560" and an air density
            # "1.225": eight characters plus padding. Below that the field
            # scrolls and shows the tail of the number ("04560"), which looks
            # like a different, wrong value rather than a truncated one.
            widget.setMinimumWidth(96)

        # Add rows to form layout
        form_layout.addRow("Liftoff Mass:", mass_row)
        form_layout.addRow("Propellant Mass:", prop_mass_row)
        form_layout.addRow("Drag Coefficient (Cd):", self.cd_input)
        form_layout.addRow("Cross-sectional Area:", area_row)
        form_layout.addRow("Air Density:", rho_row)
        form_layout.addRow("Time Step:", timestep_row)
        form_layout.addRow("Fin Count:", self.fin_count_input)
        form_layout.addRow("Fin Thickness:", fin_thickness_row)
        form_layout.addRow("Fin Length:", fin_length_row)
        form_layout.addRow("Body Tube Diameter:", body_diameter_row)
        form_layout.addRow("Parachute Deploy Height:", chute_height_row)
        form_layout.addRow("Parachute Size:", chute_size_row)
        # New row for parachute drag coefficient
        self.chute_cd_input = QtWidgets.QLineEdit()
        self.chute_cd_input.setPlaceholderText("e.g., 1.5")
        self.chute_cd_input.setText("1.5")
        self.chute_cd_input.setStyleSheet(input_style)  # Apply same styling
        self.chute_cd_input.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        form_layout.addRow("Parachute Drag Coefficient (Cd):", self.chute_cd_input)

        left_layout.addLayout(form_layout)

        # (Scoreboard progress bar removed per user request)

        # Buttons
        self.thrust_curve_path = None
        self.select_thrust_button = QtWidgets.QPushButton('Select Thrust Curve File')
        self.select_thrust_button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.select_thrust_button.clicked.connect(self.select_thrust_curve)
        left_layout.addWidget(self.select_thrust_button)

        self.start_button = QtWidgets.QPushButton('Start Simulation')
        self.start_button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.start_button.clicked.connect(self.start_simulation)
        left_layout.addWidget(self.start_button)

        # Live Code Viewer button for presentations
        self.live_code_button = QtWidgets.QPushButton('🔴 Live Code Viewer')
        self.live_code_button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.live_code_button.setToolTip('Show live code execution for presentations')
        self.live_code_button.clicked.connect(self.show_live_code_viewer)
        left_layout.addWidget(self.live_code_button)

    # Removed Az/5 kill button per user request

        # Results label
        self.result_label = QtWidgets.QLabel('Results will be displayed here.')
        self.result_label.setWordWrap(True)
        self.result_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.result_label.setTextFormat(QtCore.Qt.RichText)
        self.result_label.setStyleSheet('''
            QLabel {
                background-color: #FDF6E3;
                border: 2px solid #E94F37;
                border-radius: 10px;
                padding: 15px;
                color: #3C2F1E;
                font-family: 'Arial', sans-serif;
                font-size: 12px;
                line-height: 1.4;
                min-height: 120px;
            }
        ''')
        left_layout.addWidget(self.result_label)

        # Error/warning label
        self.error_label = QtWidgets.QLabel("")
        self.error_label.setStyleSheet("color: red; font-weight: bold;")
        left_layout.addWidget(self.error_label)

        # Which model actually flew, and which inputs on this tab it ignored.
        # The 2-DOF model takes drag and recovery from the Aerodynamics tab, so
        # fields here can be live and unused at the same time - saying so is
        # better than letting someone tune a number that does nothing.
        self.model_note_label = QtWidgets.QLabel("")
        self.model_note_label.setWordWrap(True)
        self.model_note_label.setStyleSheet(
            f"color:{app_theme.PALETTE['text_dim']}; font-size:9pt; "
            f"border:1px solid {app_theme.PALETTE['border']}; padding:5px;")
        left_layout.addWidget(self.model_note_label)

        left_layout.addStretch()

        # Right panel: Graph only (remove wireframe)
        right_widget = QtWidgets.QWidget()
        right_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        right_layout = QtWidgets.QVBoxLayout(right_widget)
        right_layout.setContentsMargins(2, 2, 2, 2)  # Minimal margins
        right_layout.setSpacing(2)  # Tight spacing

        # --- Graph/Spreadsheet Tabs ---
        self.graph_tab_widget = QtWidgets.QTabWidget()
        # Graph tab
        graph_tab = QtWidgets.QWidget()
        graph_layout = QtWidgets.QVBoxLayout(graph_tab)
        graph_layout.setContentsMargins(0, 0, 0, 0)  # No margins for max space
        graph_layout.setSpacing(0)  # No spacing
        self.figure = plt.Figure()
        # Without this the Graph tab opens as a bare rectangle - white under a
        # stock matplotlib figure - with nothing saying why it is empty.
        app_theme.placeholder_figure(
            self.figure,
            "No flight yet. Set the inputs on the left, pick a thrust curve,\n"
            "then press Start Simulation.")
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        graph_layout.addWidget(self.canvas)
        self.toolbar = NavigationToolbar(self.canvas, self)
        self.style_toolbar()
        graph_layout.addWidget(self.toolbar)
        self.graph_tab_widget.addTab(graph_tab, "Graph")

        # Spreadsheet tabs: the trajectory and the motor internals are
        # different datasets on different time bases, so they get a sheet each.
        self.flight_sheet = datasheet.DataSheet(
            datasheet.FLIGHT_COLUMNS, title="flight_data")
        self.graph_tab_widget.addTab(self.flight_sheet, "Flight Data")

        self.engine_sheet = datasheet.DataSheet(
            datasheet.ENGINE_COLUMNS, title="engine_data")
        self.engine_sheet.info.setText(
            "No engine data. Design a motor on the Engine Lab tab and press "
            "\"Send to Simulation\" - the internal ballistics of that run "
            "appear here alongside the trajectory.")
        self.graph_tab_widget.addTab(self.engine_sheet, "Engine Data")

        right_layout.addWidget(self.graph_tab_widget)

    # Removed animation controls (pause/speed) per user request; Simulation tab now pure graph display.

        right_widget.setLayout(right_layout)

        # Splitter for resizable panels. The left panel is a long form whose
        # rows are label + value + unit; at the 300px it used to be given, the
        # unit combos collapsed to their drop-down arrows and the values
        # clipped mid-number, while the graph beside it sat empty at 1200px.
        # It now opens wide enough to read and keeps that width as the window
        # grows (stretch 0), so the extra space goes to the plot instead.
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        left_scroll = _scrollable(left_widget)
        # QFormLayout gives every row the same label column - as wide as the
        # longest label ("Parachute Drag Coefficient (Cd):"). The widest row is
        # therefore that column plus a value field plus the roomiest unit combo
        # ("kg/m3"), which is what this width is measured against; below it the
        # air-density combo loses its arrow off the right edge.
        left_scroll.setMinimumWidth(380)
        splitter.addWidget(left_scroll)
        splitter.addWidget(right_widget)
        splitter.setSizes([490, 1010])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)

        main_panel_layout.addWidget(splitter)
        main_panel_layout.setContentsMargins(2, 2, 2, 2)  # Minimal margins
        self.tabs.addTab(main_panel, "Simulation")

        # A rocket is an assembly of two independently designed halves, so
        # they get a section each rather than being scattered across tabs.
        #
        #   ENGINE        what makes the thrust
        #   AERODYNAMICS  what the thrust has to push
        #
        # The Simulation tab pairs one of each, and that pairing is the rocket.

        # --- Engine: the motor, and the library of saved motor designs ---
        self.engine_lab = EngineLabWidget(
            on_send_to_simulation=self.use_engine_lab_thrust_curve,
            # So the Engine tab's flight preview describes the rocket the app
            # is actually configured for, instead of its own three boxes that
            # nothing updates when a vehicle is loaded.
            get_vehicle=self._engine_preview_vehicle,
            # A generated motor knows what its tank and chamber have to be
            # made of and how thick. Those fields live on the Flight Report,
            # which is where they get graded, so hand them over instead of
            # printing a recommendation the user has to retype.
            on_materials=self._apply_motor_materials)
        self.engine_section = EngineSection(self.engine_lab,
                                            self.get_profiles_dir)
        self.tabs.addTab(_scrollable(self.engine_section), "Engine")

        # --- Aerodynamics: airframe shape, recovery, and the drag it makes ---
        # on_changed keeps the Stability Test tab's view of the launch
        # conditions in step when they are edited on this one.
        self.vehicle_tab = VehicleTabWidget(
            on_changed=self.refresh_shared_launch_views)
        self.aero_tab = AeroAnalysisWidget(
            get_airframe=lambda: self.vehicle_tab.airframe(),
            # effective_dry_cg(), not the typed field: when mass components
            # exist they are what the flight uses, so the sweep's static
            # margin has to be computed against the same CG.
            get_cg=lambda: self.vehicle_tab.mass_properties().effective_dry_cg(),
            get_site=lambda: self.vehicle_tab.launch_site(),
            on_table_changed=self._cd_table_changed)
        self.aero_section = AerodynamicsSection(
            self.vehicle_tab, self.aero_tab, self.get_profiles_dir)
        self.tabs.addTab(_scrollable(self.aero_section), "Aerodynamics")

        # The Simulation tab is where the two halves get combined. Put the
        # pairing at the very top of it, above the numbers it governs.
        self.assembly = AssemblyPanel(self.engine_section, self.aero_section,
                                      on_changed=self._assembly_changed)
        self._sim_left_layout.insertWidget(0, self.assembly)

        # --- Flight Report: failure-mode analysis of the last simulation ---
        self.flight_report = FlightReportWidget()
        self.tabs.addTab(self.flight_report, "Flight Report")

        # What these three tabs hold before any rocket is loaded. A profile
        # that omits a section - or carries one only partly filled, as the
        # v1.0 presets do with a vehicle section holding nothing but
        # target_altitude_ft - is completed from this, so loading a rocket
        # second gives the same machine as loading it first.
        self._pristine_sections = {
            'engine': self.engine_lab.get_config(),
            'vehicle': self.flight_report.get_config(),
            'airframe': self.vehicle_tab.get_config(),
        }

        # --- Tolerances: an extension, not part of the flight path ---
        #
        # It reads the rocket and flies copies of it; nothing it does reaches
        # the Simulation tab or the loaded configuration. That is also why it
        # can be switched off entirely from Settings without anything else
        # noticing it has gone.
        self.tolerances_tab = TolerancesTab(
            get_inputs=self._tolerance_inputs)
        self.tabs.addTab(self.tolerances_tab, "Tolerances")
        # A tab switched off last time should not flash up now. This runs
        # here, after the tab exists - called any earlier it silently did
        # nothing, because there was no tab yet to remove.
        if not self.load_extension_enabled():
            self.set_tolerances_enabled(False)

        # --- Rockets: the saved-rocket library. First tab, because picking a
        # rocket is where a session starts. ---
        self.rocket_library = RocketLibraryWidget(
            capture_config=self.get_current_configuration,
            apply_config=self.apply_configuration,
            writable_dir=self.get_profiles_dir,
            search_dirs=self.get_profile_search_dirs,
            on_changed=self.refresh_profile_dropdown,
            on_loaded=self.on_rocket_loaded)
        self.tabs.insertTab(0, self.rocket_library, "Rockets")

        # Settings tab for units and theme
        settings_widget = QtWidgets.QWidget()
        settings_layout = QtWidgets.QVBoxLayout(settings_widget)
        
        # Theme selection section
        theme_group = QtWidgets.QGroupBox("Interface Theme")
        theme_layout = QtWidgets.QVBoxLayout(theme_group)
        
        self.theme_select = QtWidgets.QComboBox()
        for theme_key, theme_data in self.themes.items():
            self.theme_select.addItem(f"{theme_data['name']} - {theme_data['description']}", theme_key)
        
        # Set current theme as selected
        for i in range(self.theme_select.count()):
            if self.theme_select.itemData(i) == self.current_theme:
                self.theme_select.setCurrentIndex(i)
                break
        
        # Connect to theme change handler using lambda to pass the theme name
        self.theme_select.currentIndexChanged.connect(lambda index: self.on_theme_changed(self.theme_select.itemData(index)))
        
        theme_layout.addWidget(QtWidgets.QLabel("Select Interface Theme:"))
        theme_layout.addWidget(self.theme_select)
        
        # Theme preview
        self.theme_preview = QtWidgets.QLabel()
        self.theme_preview.setFixedHeight(60)
        self.theme_preview.setStyleSheet("border: 1px solid #ccc; border-radius: 4px;")
        self.update_theme_preview()
        theme_layout.addWidget(QtWidgets.QLabel("Preview:"))
        theme_layout.addWidget(self.theme_preview)
        
        settings_layout.addWidget(theme_group)
        
        # Units selection section
        units_group = QtWidgets.QGroupBox("Measurement Units")
        units_layout = QtWidgets.QVBoxLayout(units_group)
        
        self.unit_select = QtWidgets.QComboBox()
        self.unit_select.addItems(["Metric (m, kg)", "Imperial (ft, lb)"])
        # This combo existed but was connected to nothing, so choosing
        # Imperial did not change a single field. It now switches every
        # unit-aware field in the app to that system's preferred unit,
        # converting the displayed number rather than reinterpreting it.
        self.unit_select.currentIndexChanged.connect(self.on_unit_system_changed)
        units_layout.addWidget(QtWidgets.QLabel("Select Unit System:"))
        units_layout.addWidget(self.unit_select)
        units_note = QtWidgets.QLabel(
            "<span style='font-size:9pt'>Every field keeps its value in SI "
            "internally, so switching systems never changes the rocket - only "
            "how it is written. Individual fields can still be set to any unit "
            "of their own dimension. Dimensionless values (Cd, Mach, O/F, "
            "efficiencies) have no unit and are unaffected.</span>")
        # Without this the label is one unbreakable line, and a QLabel's
        # minimum width is whatever its longest line needs - which pushed the
        # whole Settings tab wider than the window and cut the last profile
        # button off the right-hand edge.
        units_note.setWordWrap(True)
        units_layout.addWidget(units_note)

        settings_layout.addWidget(units_group)

        # --- optional extensions ------------------------------------------
        ext_group = QtWidgets.QGroupBox("Extensions")
        ext_layout = QtWidgets.QVBoxLayout(ext_group)
        self.tolerances_enabled = QtWidgets.QCheckBox(
            "Tolerances tab - how far each engine component can be wrong")
        self.tolerances_enabled.setChecked(self.load_extension_enabled())
        self.tolerances_enabled.setToolTip(
            "The tolerance search flies the rocket dozens of times, which "
            "takes a minute or two. Turning it off removes the tab entirely; "
            "nothing else in the app depends on it.")
        self.tolerances_enabled.toggled.connect(self.set_tolerances_enabled)
        ext_layout.addWidget(self.tolerances_enabled)
        ext_note = QtWidgets.QLabel(
            "<span style='font-size:9pt'>Extensions are self-contained: they "
            "read the loaded rocket and never write to it, so switching one "
            "off changes nothing about how the rocket flies.</span>")
        ext_note.setWordWrap(True)
        ext_layout.addWidget(ext_note)
        settings_layout.addWidget(ext_group)
        
        # Rocket Configuration Profiles section
        profiles_group = QtWidgets.QGroupBox("Rocket Configuration Profiles")
        profiles_layout = QtWidgets.QVBoxLayout(profiles_group)
        
        # Profile selection and management
        profile_selection_layout = QtWidgets.QHBoxLayout()
        profile_selection_layout.addWidget(QtWidgets.QLabel("Current Profile:"))
        
        self.profile_select = QtWidgets.QComboBox()
        self.profile_select.setEditable(False)
        self.load_available_profiles()
        self.profile_select.currentTextChanged.connect(self.load_profile)
        profile_selection_layout.addWidget(self.profile_select)
        
        profiles_layout.addLayout(profile_selection_layout)
        
        # Profile management buttons
        profile_buttons_layout = QtWidgets.QHBoxLayout()
        
        self.save_profile_button = QtWidgets.QPushButton("Save Current Settings")
        self.save_profile_button.setToolTip("Save current rocket parameters as a new profile")
        self.save_profile_button.clicked.connect(self.save_current_profile)
        profile_buttons_layout.addWidget(self.save_profile_button)
        
        self.delete_profile_button = QtWidgets.QPushButton("Delete Profile")
        self.delete_profile_button.setToolTip("Delete the selected profile")
        self.delete_profile_button.clicked.connect(self.delete_current_profile)
        profile_buttons_layout.addWidget(self.delete_profile_button)
        
        self.export_profile_button = QtWidgets.QPushButton("Export")
        self.export_profile_button.setToolTip("Export profile to file")
        self.export_profile_button.clicked.connect(self.export_profile)
        profile_buttons_layout.addWidget(self.export_profile_button)
        
        self.import_profile_button = QtWidgets.QPushButton("Import")
        self.import_profile_button.setToolTip("Import profile from file")
        self.import_profile_button.clicked.connect(self.import_profile)
        profile_buttons_layout.addWidget(self.import_profile_button)
        
        profiles_layout.addLayout(profile_buttons_layout)
        
        # Profile description
        self.profile_description = QtWidgets.QLabel("Profiles store all rocket parameters, thrust curves, and launch conditions for quick switching between different rocket configurations.")
        self.profile_description.setWordWrap(True)
        self.profile_description.setStyleSheet("color: #666; font-style: italic; font-size: 10px;")
        profiles_layout.addWidget(self.profile_description)
        
        settings_layout.addWidget(profiles_group)
        settings_layout.addStretch()
        self.tabs.addTab(_scrollable(settings_widget), "Settings")

        # The standalone "Launch Conditions" tab is gone.
        #
        # Field elevation, temperature and humidity were set there, AGAIN on
        # the Aerodynamics tab's launch site, and the wind and rail angle a
        # third time on the Stability Test tab. Three places for one set of
        # conditions, and only the launch site reached the flight model - so
        # the other two could say something different from what flew.
        #
        # The launch site is now the single copy. These three widgets stay
        # alive because the air-density readout and the profile format use
        # them, but they are views onto the launch site rather than a second
        # set of values: writing one writes through, and they follow when the
        # site changes.
        self.start_altitude_input = QtWidgets.QLineEdit("0")
        self.temperature_input = QtWidgets.QLineEdit("15")
        self.humidity_input = QtWidgets.QLineEdit("50")
        for widget in (self.start_altitude_input, self.temperature_input,
                       self.humidity_input):
            widget.setVisible(False)
        self.start_altitude_input.textChanged.connect(self.update_air_density)
        self.temperature_input.textChanged.connect(self.update_air_density)
        self.humidity_input.textChanged.connect(self.update_air_density)

        # Add the Stability Test tab last (to the right)
        launch_anim_tab = QtWidgets.QWidget()
        launch_anim_layout = QtWidgets.QVBoxLayout(launch_anim_tab)

        wind_group = QtWidgets.QGroupBox(
            "Launch conditions - shared with the Aerodynamics tab")
        wind_layout = QtWidgets.QFormLayout(wind_group)
        shared_note = QtWidgets.QLabel(
            "One set of conditions, shown here and on the Aerodynamics tab's "
            "launch site. Change either and both follow, and it is what flies.")
        shared_note.setWordWrap(True)
        shared_note.setStyleSheet(
            f"color:{app_theme.PALETTE['text_dim']}; font-size:9pt;")
        wind_layout.addRow(shared_note)

        self.wind_speed_input = QtWidgets.QDoubleSpinBox()
        self.wind_speed_input.setRange(0, 100)
        self.wind_speed_input.setValue(0)
        self.wind_speed_input.setSuffix(" m/s")
        wind_layout.addRow("Wind Speed:", self.wind_speed_input)

        self.wind_direction_input = QtWidgets.QDial()
        self.wind_direction_input.setMinimum(0)
        self.wind_direction_input.setMaximum(359)
        self.wind_direction_input.setNotchesVisible(True)
        self.wind_direction_input.setToolTip(
            "The direction the wind blows TOWARD, as a compass bearing: 0 is "
            "north, 90 is east. This is what sets which way the rocket "
            "weathercocks and which way it drifts under canopy.")
        wind_dir_label = QtWidgets.QLabel(_wind_dir_text(0))
        wind_dir_label.setAlignment(QtCore.Qt.AlignCenter)
        self.wind_direction_input.valueChanged.connect(
            lambda v: wind_dir_label.setText(_wind_dir_text(v)))
        wind_layout.addRow(wind_dir_label, self.wind_direction_input)

        launch_anim_layout.addWidget(wind_group)
        # Stability controls
        stability_group = QtWidgets.QGroupBox("Rocket Stability")
        stability_layout = QtWidgets.QFormLayout(stability_group)

        # These follow the loaded rocket unless the override is ticked, and
        # each carries its own unit picker. Left to be typed by hand they went
        # stale the moment a different vehicle was loaded, so the pad margin
        # shown here could belong to a rocket that was no longer on screen.
        self.stability_override = QtWidgets.QCheckBox(
            "Override - set these by hand instead of from the rocket")
        self.stability_override.setToolTip(
            "Off: length, CG and CP are read from the loaded rocket and keep "
            "up with it.\nOn: type your own, and they stop tracking.")
        self.stability_override.toggled.connect(self._on_stability_override)
        stability_layout.addRow(self.stability_override)

        self.rocket_length_input = StabilityLengthField(1.0, maximum_m=30.0)
        stability_layout.addRow("Rocket Length:", self.rocket_length_input)

        self.center_of_mass_input = StabilityLengthField(0.5, maximum_m=30.0)
        stability_layout.addRow("Center of Mass (from nose):",
                                self.center_of_mass_input)

        self.center_of_pressure_input = StabilityLengthField(0.7, maximum_m=30.0)
        stability_layout.addRow("Center of Pressure (from nose):",
                                self.center_of_pressure_input)

        # These combos go through the same re-measure as every other unit
        # picker. Windows ships a 9pt default font, so a combo sized before
        # the stylesheet's 10pt lands gets clipped to a bare arrow - the
        # defect _lock_unit_combo exists to prevent, and the one platform
        # that cannot be tested from here.
        for field in (self.rocket_length_input, self.center_of_mass_input,
                      self.center_of_pressure_input):
            self._unit_combos.append(field.unit)

        self.stability_source_label = QtWidgets.QLabel()
        self.stability_source_label.setWordWrap(True)
        self.stability_source_label.setStyleSheet(
            f"color:{app_theme.PALETTE['text_dim']}; font-size:9pt;")
        stability_layout.addRow(self.stability_source_label)

        self.launch_angle_input = QtWidgets.QDoubleSpinBox()
        self.launch_angle_input.setRange(-45.0, 45.0)
        self.launch_angle_input.setSingleStep(0.5)
        self.launch_angle_input.setValue(0.0)
        self.launch_angle_input.setSuffix("°")
        self.launch_angle_input.setToolTip("Launch angle as deviation from vertical (0° = vertical). Safety recommendation: keep within ±5° unless site RSO allows more.")
        stability_layout.addRow("Launch Angle (± from vertical):", self.launch_angle_input)

        # Recommended launch angle (±0–5°) based on wind — tilt slightly into the wind
        rec_row = QtWidgets.QHBoxLayout()
        self.recommended_angle_label = QtWidgets.QLabel("Recommended: 0.0° (calm)")
        self.recommended_angle_label.setToolTip("Computed from wind speed and direction; bounded to ±5°")
        self.apply_recommended_btn = QtWidgets.QPushButton("Apply")
        self.apply_recommended_btn.setToolTip("Set launch angle to the recommended value")
        self.apply_recommended_btn.clicked.connect(self.apply_recommended_launch_angle)
        rec_row.addWidget(self.recommended_angle_label)
        rec_row.addStretch(1)
        rec_row.addWidget(self.apply_recommended_btn)
        stability_layout.addRow("Recommendation:", rec_row)

        self.stability_status_label = QtWidgets.QLabel("Stability Margin: 0.2 m (Stable)")
        self.stability_status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.stability_status_label.setStyleSheet("font-size:14px;color:#2E8B57;font-weight:bold;")
        stability_layout.addRow(self.stability_status_label)

        # Update stability margin when inputs change.
        #
        # Before the tab has been flown this is all there is: the margin as
        # the rocket stands on the pad, from the two figures entered above.
        # Once it HAS been flown, the flight's own verdict replaces it and
        # must not be overwritten by a pad number - the whole point is that
        # the margin does not stay at its pad value once the motor lights.
        update_stability = self.refresh_stability_margin
        self.rocket_length_input.valueChanged.connect(update_stability)
        self.center_of_mass_input.valueChanged.connect(update_stability)
        self.center_of_pressure_input.valueChanged.connect(update_stability)
        # Start tracking the rocket, not editable, which is the default.
        self._on_stability_override(False)
        QtCore.QTimer.singleShot(0, self.sync_stability_from_vehicle)

        # Update recommended angle label initially and when wind/angle changes
        def _update_rec_label():
            self.update_recommended_launch_angle_label()
        self.wind_speed_input.valueChanged.connect(_update_rec_label)
        self.wind_direction_input.valueChanged.connect(_update_rec_label)
        self.launch_angle_input.valueChanged.connect(_update_rec_label)
        # Initialize recommendation display
        QtCore.QTimer.singleShot(0, self.update_recommended_launch_angle_label)

        # New layout: left half trajectory visualizer, right half split vertically
        main_launch_split = QtWidgets.QHBoxLayout()

        # --- Left: Trajectory / Launch Visualizer (takes ~50% width, full height) ---
        trajectory_widget = QtWidgets.QWidget()
        self.trajectory_widget = trajectory_widget
        self.setup_trajectory_widget_styling()
        traj_layout = QtWidgets.QVBoxLayout(trajectory_widget)
        traj_layout.setContentsMargins(16, 16, 16, 16)
        traj_layout.setSpacing(12)
        traj_header = QtWidgets.QLabel()
        self.trajectory_header = traj_header
        self.setup_trajectory_header()
        traj_layout.addWidget(traj_header)
        self.launch_fig = plt.Figure(figsize=(6, 5))
        app_theme.placeholder_figure(
            self.launch_fig, "Run a simulation to animate the launch.")
        self.setup_plot_background()
        self.launch_canvas = FigureCanvas(self.launch_fig)
        self.launch_canvas.setMinimumSize(300, 250)
        self.setup_canvas_styling()
        traj_layout.addWidget(self.launch_canvas, 1)
        main_launch_split.addWidget(trajectory_widget, 1)

        # --- Right: Top controls (wind + stability) and bottom Force Diagram ---
        right_panel = QtWidgets.QWidget()
        right_panel_layout = QtWidgets.QVBoxLayout(right_panel)
        right_panel_layout.setContentsMargins(8, 8, 8, 8)
        right_panel_layout.setSpacing(10)

        controls_container = QtWidgets.QWidget()
        controls_layout = QtWidgets.QVBoxLayout(controls_container)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        controls_layout.addWidget(stability_group)
        controls_layout.insertWidget(0, wind_group)
        right_panel_layout.addWidget(controls_container, 1)

        force_widget = QtWidgets.QWidget()
        self.force_widget = force_widget
        self.setup_force_widget_styling()
        force_layout = QtWidgets.QVBoxLayout(force_widget)
        force_layout.setContentsMargins(8, 8, 8, 8)
        force_layout.setSpacing(6)
        force_header = QtWidgets.QLabel()
        self.force_header = force_header
        self.setup_force_header()
        force_layout.addWidget(force_header)
        self.create_force_diagram(force_layout)
        right_panel_layout.addWidget(force_widget, 2)

        main_launch_split.addWidget(right_panel, 1)
        launch_anim_layout.addLayout(main_launch_split, 1)

        # Force diagram idle refresh timer (updates when not launching)
        # Ensures Live Forces panel stays current (e.g., weight changes if mass input changes)
        if not hasattr(self, 'force_update_timer'):
            self.force_update_timer = QtCore.QTimer()
            def _idle_force_update():
                try:
                    if not self.is_launching:
                        self.update_force_diagram(
                            getattr(self, '_force_scrub_time', None))
                except Exception:
                    pass
            self.force_update_timer.timeout.connect(_idle_force_update)
            self.force_update_timer.start(150)  # ~6-7 fps while idle for smoother updates

        # Retro styled Launch/Stop buttons
        buttons_row = QtWidgets.QHBoxLayout()
        self.launch_button = QtWidgets.QPushButton('Launch!')
        self.launch_button.setStyleSheet('''
            QPushButton {
                background-color: #E94F37;
                border: 2px solid #3C2F1E;
                border-radius: 10px;
                padding: 12px 32px;
                font-weight: bold;
                font-size: 20px;
                color: #F8F5E3;
                margin-top: 16px;
            }
            QPushButton:hover {
                background-color: #FFD447;
                color: #3C2F1E;
                border: 2px solid #E94F37;
            }
        ''')
        self.launch_button.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.launch_button.clicked.connect(self.start_launch_animation)

        self.stop_button = QtWidgets.QPushButton('Stop')
        self.stop_button.setStyleSheet('''
            QPushButton {
                background-color: #3C2F1E;
                border: 2px solid #BCA16A;
                border-radius: 10px;
                padding: 12px 24px;
                font-weight: bold;
                font-size: 18px;
                color: #FFD447;
                margin-top: 16px;
            }
            QPushButton:hover {
                background-color: #BCA16A;
                color: #3C2F1E;
                border: 2px solid #3C2F1E;
            }
        ''')
        self.stop_button.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.stop_button.clicked.connect(self.stop_launch_animation)

        buttons_row.addWidget(self.launch_button)
        buttons_row.addWidget(self.stop_button)
        launch_anim_layout.addLayout(buttons_row)

        # Animation variables
        self.launch_time = 0.0
        self.is_launching = False
        self.launch_timer = QtCore.QTimer()
        self.launch_timer.timeout.connect(self.update_launch_frame)
        
        # Debounced input update timer for force diagram
        self.input_debounce_timer = QtCore.QTimer()
        self.input_debounce_timer.setSingleShot(True)
        self.input_debounce_timer.timeout.connect(lambda: self.update_force_diagram() if not self.is_launching else None)
        
        # Connect key inputs to debounced force updates (500ms delay to avoid excessive redraws while typing)
        def _debounced_force_update():
            if hasattr(self, 'input_debounce_timer'):
                self.input_debounce_timer.start(500)
        
        # Mass affects weight force
        self.mass_input.textChanged.connect(_debounced_force_update)
        # Wind affects stability calculations  
        self.wind_speed_input.valueChanged.connect(_debounced_force_update)
        self.wind_direction_input.valueChanged.connect(_debounced_force_update)
        # Stability parameters affect force calculations
        self.center_of_mass_input.valueChanged.connect(_debounced_force_update)
        self.center_of_pressure_input.valueChanged.connect(_debounced_force_update)

        def update_launch_animation():
            """Update static preview of launch trajectory using real simulation parameters"""
            if self.is_launching:
                return  # Don't update static view during animation
                
            ax = self.launch_fig.gca()
            ax.clear()
            
            # Get rocket parameters from Simulation tab
            try:
                m, Cd, A, rho, time_step, fin_thickness, fin_length, body_diameter, chute_height, chute_size, chute_deploy_time, chute_cd = self.get_inputs_for_simulation()
                if m <= 0 or Cd <= 0 or A <= 0 or rho <= 0:
                    raise ValueError("Invalid simulation parameters")
            except:
                # Fallback to default values if simulation inputs are invalid
                m, Cd, A, rho = 5.0, 0.7, 0.004560, 1.225
            
            # Get wind and stability parameters
            wind_speed = self.wind_speed_input.value()
            wind_dir_deg = self.wind_direction_input.value()
            margin = self.center_of_pressure_input.value() - self.center_of_mass_input.value()
            stable = margin > 0.05
            color = '#2E8B57' if stable else '#E94F37'
            
            # Load thrust curve data using shared method
            times_thrust, thrusts, thrust_func, burn_time = self.load_thrust_curve_data()
            
            # Simulate trajectory preview using real physics
            g = 9.81
            dt = 0.1  # Time step for preview
            max_time = 15.0  # Preview up to 15 seconds
            
            # Initialize
            velocity = 0.0
            altitude = 0.0
            x_pos = 0.0
            mass = m
            
            times = []
            x_traj = []
            y_traj = []
            
            # Wind drift
            drift_factor = wind_speed * 0.01
            angle_rad = wind_dir_deg * math.pi / 180
            x_drift_per_sec = drift_factor * math.cos(angle_rad)
            
            t = 0
            while t < max_time and altitude >= 0:
                times.append(t)
                x_traj.append(x_pos)
                y_traj.append(altitude)
                
                # Get thrust at current time
                current_thrust = float(thrust_func(t)) if t <= burn_time else 0.0
                
                # Calculate drag force
                drag_force = 0.5 * rho * (velocity ** 2) * Cd * A if velocity > 0 else 0
                
                # Net force and acceleration
                net_force = current_thrust - drag_force - (mass * g)
                acceleration = net_force / mass
                
                # Add instability
                if not stable:
                    wobble = 0.3 * math.sin(t * 8) * (1 + t * 0.05)
                    acceleration += wobble
                
                # Update motion
                velocity += acceleration * dt
                altitude += velocity * dt
                x_pos += x_drift_per_sec * dt
                
                if altitude < 0:
                    altitude = 0
                    break
                    
                t += dt
            
            # Plot trajectory
            ax.plot(x_traj, y_traj, '--', color=color, alpha=0.7, linewidth=2, label='Predicted Path')
            
            # Draw rocket at launch pad
            ax.plot([0], [0], color=color, marker='^', markersize=15, label=f'Rocket ({"Stable" if stable else "Unstable"})')
            
            # Draw wind arrow if there's wind
            if wind_speed > 0:
                ax.arrow(-0.5, 0.2, x_drift_per_sec * 50, 0, 
                        head_width=0.15, head_length=0.15, 
                        fc='#4682B4', ec='#4682B4', linewidth=3, 
                        label=f'Wind: {wind_speed:.1f} m/s')
            
            # Auto-scale based on trajectory
            if x_traj and y_traj:
                max_x = max(max(x_traj), abs(min(x_traj)))
                max_y = max(y_traj)
                ax.set_xlim(-max(2, max_x * 1.2), max(2, max_x * 1.2))
                ax.set_ylim(0, max(3, max_y * 1.1))
            else:
                ax.set_xlim(-2, 2)
                ax.set_ylim(0, 3)
            
            ax.set_xlabel('Drift (m)')
            ax.set_ylabel('Altitude (m)')
            # Apply theme-aware styling
            self.style_axes(ax)
            ax.legend(loc='upper left')
            
            # Add stability warning text
            if not stable:
                ax.text(0, 2.5, 'UNSTABLE ROCKET!', 
                       ha='center', va='center', fontsize=16, 
                       color='red', fontweight='bold',
                       bbox=dict(boxstyle='round', facecolor='yellow', alpha=0.8))
            
            # Force canvas to update
            self.launch_canvas.draw()

        # Connect all relevant inputs to update animation
        self.wind_speed_input.valueChanged.connect(update_launch_animation)
        self.wind_direction_input.valueChanged.connect(update_launch_animation)
        self.center_of_mass_input.valueChanged.connect(update_launch_animation)
        self.center_of_pressure_input.valueChanged.connect(update_launch_animation)
        self.rocket_length_input.valueChanged.connect(update_launch_animation)
        self.launch_angle_input.valueChanged.connect(update_launch_animation)
        
        # Initial animation update
        update_launch_animation()

        # This tab is the stability check: rail angle, CG against CP,
        # static margin and the wind that pushes the vehicle off it.
        # "Launch" said nothing about that and sat confusingly next to
        # "Launch Conditions", which is the atmosphere and the pad.
        self.tabs.addTab(_scrollable(launch_anim_tab), "Stability Test")

        # Wire the shared launch conditions together. Everything below is one
        # value with two views; nothing here keeps its own copy.
        self._bind_shared_launch_conditions()

        main_layout.addWidget(self.tabs)
        self.setLayout(main_layout)

        # Connect fin count, thickness, and body diameter inputs to area update
        self.fin_count_input.textChanged.connect(self.update_area)
        self.fin_thickness_input.textChanged.connect(self.update_area)
        self.body_diameter_input.textChanged.connect(self.update_area)
        self.body_diameter_unit.currentIndexChanged.connect(self.update_area)
        self.fin_length_input.textChanged.connect(self.update_area)
        self.fin_length_unit.currentIndexChanged.connect(self.update_area)
        self.fin_thickness_input.textChanged.connect(self.update_area)
        self.fin_thickness_unit.currentIndexChanged.connect(self.update_area)

    # Removed redundant single-variable graph selection dropdown per user request.

        # Multi-variable graphing checkboxes (compact grid)
        self.graph_vars = {
            "Altitude": QtWidgets.QCheckBox("Altitude"),
            "Velocity": QtWidgets.QCheckBox("Velocity"),
            "Mass": QtWidgets.QCheckBox("Mass"),
            "Acceleration": QtWidgets.QCheckBox("Acceleration"),
            "Thrust": QtWidgets.QCheckBox("Thrust"),
            "Drag": QtWidgets.QCheckBox("Drag"),
            "G-Load": QtWidgets.QCheckBox("G-Load"),
            "Net Force": QtWidgets.QCheckBox("Net Force")
        }
        graph_var_widget = QtWidgets.QWidget()
        graph_var_container = QtWidgets.QVBoxLayout(graph_var_widget)
        graph_var_container.setContentsMargins(0, 0, 0, 0)
        graph_var_container.setSpacing(4)
        title_lbl = QtWidgets.QLabel("Variables to graph:")
        title_lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
        graph_var_container.addWidget(title_lbl)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        grid.setContentsMargins(0, 0, 0, 0)
        cols = 4
        names = list(self.graph_vars.keys())
        for idx, name in enumerate(names):
            cb = self.graph_vars[name]
            # Default to showing Altitude only; others off until selected
            cb.setChecked(name == "Altitude")
            cb.stateChanged.connect(self.update_graph)
            cb.setStyleSheet("QCheckBox { font-size: 11px; spacing: 6px; }")
            r, c = divmod(idx, cols)
            grid.addWidget(cb, r, c)
        graph_var_container.addLayout(grid)
        left_layout.addWidget(graph_var_widget)

        # Connect unit dropdowns to conversion update
        self.mass_unit.currentIndexChanged.connect(lambda: self.update_conversions('mass'))
        # Propellant mass had a unit combo but was never connected, so
        # switching it from kg to lb left the number alone and it was
        # reinterpreted - a 2.2x change in propellant load, silently.
        self.prop_mass_unit.currentIndexChanged.connect(lambda: self.update_conversions('prop_mass'))
        self.area_unit.currentIndexChanged.connect(lambda: self.update_conversions('area'))
        self.rho_unit.currentIndexChanged.connect(lambda: self.update_conversions('rho'))
        self.timestep_unit.currentIndexChanged.connect(lambda: self.update_conversions('timestep'))
        self.fin_thickness_unit.currentIndexChanged.connect(lambda: self.update_conversions('fin_thickness'))
        self.fin_length_unit.currentIndexChanged.connect(lambda: self.update_conversions('fin_length'))
        self.body_diameter_unit.currentIndexChanged.connect(lambda: self.update_conversions('body_diameter'))
        self.chute_height_unit.currentIndexChanged.connect(lambda: self.update_conversions('chute_height'))
        self.chute_size_unit.currentIndexChanged.connect(lambda: self.update_conversions('chute_size'))
        # Seed the previous-index map now the combos exist. Without it
        # update_conversions defaults a missing entry to the NEW index,
        # so the FIRST change to any unit combo in a session
        # reinterpreted the number instead of converting it.
        self._sync_unit_indices()

    def load_thrust_curve_data(self):
        """Load thrust curve data from file or use default. Returns (times, thrusts, thrust_func, burn_time)"""
        thrust_data = []
        self.current_motor_info = {}  # Reset motor info
        
        if hasattr(self, 'thrust_curve_path') and self.thrust_curve_path:
            path = self.thrust_curve_path
            _, ext = os.path.splitext(path.lower())
            try:
                if ext == '.csv':
                    thrust_data = self.parse_csv_thrust(path)
                elif ext in ('.eng', '.rasp'):
                    thrust_data = self.parse_rasp_eng_thrust(path)
                else:
                    # Try CSV as a fallback
                    thrust_data = self.parse_csv_thrust(path)
            except Exception:
                thrust_data = []
        
        if not thrust_data or len(thrust_data) < 2:
            # Default thrust curve with motor info
            self.current_motor_info = {
                'name': 'Default Motor',
                'propellant_mass': 0.050,  # kg
                'total_mass': 0.150        # kg
            }
            thrust_data = [
                (0.124, 816.849), (0.375, 796.043), (0.626, 781.861), (0.877, 767.440),
                (1.129, 759.627), (1.380, 735.948), (1.631, 714.454), (1.883, 701.582),
                (2.134, 674.667), (2.385, 656.493), (2.637, 636.076), (2.889, 612.409),
                (3.140, 587.801), (3.391, 567.170), (3.642, 559.971), (3.894, 534.157),
                (4.145, 444.562), (4.396, 280.510), (4.648, 216.702), (4.899, 163.136),
                (5.150, 120.571), (5.402, 86.544), (5.653, 59.990), (5.904, 39.527),
                (6.156, 25.914), (6.408, 0.000)
            ]
        
        # Deduplicate exact or near-equal times to ensure strictly increasing for interp1d
        deduped = []
        last_t = None
        for t, y in sorted(thrust_data, key=lambda p: p[0]):
            if last_t is None or t > last_t + 1e-9:
                deduped.append((t, y))
                last_t = t
            else:
                # Replace previous with latest value at same timestamp
                deduped[-1] = (t, y)
                last_t = t
        times_thrust, thrusts = zip(*deduped)
        thrust_func = interp1d(times_thrust, thrusts, bounds_error=False, fill_value=0.0)
        burn_time = times_thrust[-1]
        
        # Display motor info if available
        self.display_motor_info()
        
        return times_thrust, thrusts, thrust_func, burn_time

    def display_motor_info(self):
        """Show the loaded motor's specs above the simulation results."""
        info = getattr(self, 'current_motor_info', None)
        if not info:
            # A curve with no metadata (a bare two-column CSV) used to leave
            # this function early, so the PREVIOUS motor's name and masses
            # stayed on screen while the simulation ran the new file. Clear
            # the stale block instead of returning.
            self._set_motor_info_html("")
            return

        rows = [("Name", 'name', "{}"),
                ("Propellant Mass", 'propellant_mass', "{:.3f} kg"),
                ("Total Mass", 'total_mass', "{:.3f} kg"),
                ("Burn Time", 'burn_time', "{:.1f}s"),
                ("Diameter", 'diameter', "{:.0f}mm"),
                ("Length", 'length', "{:.0f}mm"),
                ("Manufacturer", 'manufacturer', "{}")]
        info_text = "<b>Motor Information:</b><br>" + "".join(
            f"• {label}: {fmt.format(info[key])}<br>"
            for label, key, fmt in rows if key in info)
        self._set_motor_info_html(info_text)

    def _set_motor_info_html(self, info_text):
        """Replace the motor block on the results label, keeping the results.

        This used to bail out whenever a motor block was already present, so
        loading a second motor left the FIRST motor's name and masses on
        screen while the simulation ran the new one. Strip whatever block was
        put here last time and prepend the fresh one.
        """
        current_text = self.result_label.text()
        previous = getattr(self, '_motor_info_html', None)
        remainder = current_text
        if previous and current_text.startswith(previous):
            remainder = current_text[len(previous):]
            if remainder.startswith("<br>"):
                remainder = remainder[4:]
        placeholder = "Results will be displayed here."
        if not remainder.strip() or remainder.strip() == placeholder:
            self.result_label.setText(info_text or placeholder)
        elif info_text:
            self.result_label.setText(info_text + "<br>" + remainder)
        else:
            self.result_label.setText(remainder)
        self._motor_info_html = info_text

    def parse_csv_thrust(self, path):
        data = []
        motor_info = {}
        
        with open(path, 'r', newline='') as f:
            reader = csv.reader(f)
            rows = list(reader)
        
        # Look for mass values in rows immediately above the time/thrust header
        time_header_row = None
        for i, row in enumerate(rows):
            if len(row) >= 2:
                # Check if this looks like the time/thrust header row
                if (row[0].strip().lower().replace('(', '').replace(')', '').replace('"', '') in ['time', 'time s', 'time(s)', 'times'] and
                    'thrust' in row[1].strip().lower()):
                    time_header_row = i
                    
                    # Check the row immediately above for mass values
                    if i > 0:
                        mass_row = rows[i-1]
                        if len(mass_row) >= 2:
                            # Total mass above time column (first value)
                            try:
                                total_mass_str = mass_row[0].strip().replace('"', '').replace('g', '').replace('grams', '').strip()
                                if total_mass_str and total_mass_str.replace('.', '').replace('-', '').isdigit():
                                    # The suffix stripped just above is 'g',
                                    # so this row is grams. The panel now
                                    # renders kilograms (the .eng format's
                                    # unit), so convert rather than leaving
                                    # the two file types on different units.
                                    motor_info['total_mass'] = float(total_mass_str) / 1000.0
                            except (ValueError, IndexError):
                                pass
                            
                            # Propellant mass above thrust column (second value)
                            try:
                                prop_mass_str = mass_row[1].strip().replace('"', '').replace('g', '').replace('grams', '').strip()
                                if prop_mass_str and prop_mass_str.replace('.', '').replace('-', '').isdigit():
                                    motor_info['propellant_mass'] = float(prop_mass_str) / 1000.0
                            except (ValueError, IndexError):
                                pass
                    break
        
        # Also look for motor info in metadata rows (existing functionality)
        for row in rows:
            if not row:
                continue
                
            # Extract motor metadata from header rows
            if len(row) >= 2:
                key = row[0].strip().lower().replace(':', '').replace('"', '')
                value = row[1].strip().replace('"', '')
                
                # Look for propellant mass in various formats
                if 'propellant' in key and 'mass' in key:
                    try:
                        parsed = _parse_mass_kg(value)
                        if parsed is not None:
                            motor_info['propellant_mass'] = parsed
                    except ValueError:
                        pass
                elif 'prop' in key and ('mass' in key or 'weight' in key):
                    try:
                        parsed = _parse_mass_kg(value)
                        if parsed is not None:
                            motor_info['propellant_mass'] = parsed
                    except ValueError:
                        pass
                elif key in ['propellant_mass', 'prop_mass', 'fuel_mass']:
                    try:
                        parsed = _parse_mass_kg(value)
                        if parsed is not None:
                            motor_info['propellant_mass'] = parsed
                    except ValueError:
                        pass
                # Store other motor info
                elif key in ['motor', 'name', 'designation']:
                    motor_info['name'] = value
                elif key in ['total_mass', 'loaded_mass']:
                    try:
                        parsed = _parse_mass_kg(value)
                        if parsed is not None:
                            motor_info['total_mass'] = parsed
                    except ValueError:
                        pass
                elif key in ['burn_time', 'burntime']:
                    try:
                        motor_info['burn_time'] = float(value)
                    except ValueError:
                        pass
        
        # Parse thrust data (skip metadata and header rows)
        for row in rows:
            if not row:
                continue
                
            first = row[0].strip()
            if not first:
                continue
            if first.startswith('#') or first.startswith(';'):
                continue
                
            # Skip header rows with text labels
            if first.lower().replace('(', '').replace(')', '').replace('"', '') in ['time', 'time s', 'time(s)', 'times'] or 'time' in first.lower():
                continue
                
            try:
                t = float(first)
                thrust = float(row[1]) if len(row) > 1 else None
                if thrust is None:
                    continue
                data.append((t, thrust))
            except Exception:
                # Non-numeric row -> skip
                continue
                    
        # Store motor info for later use
        if hasattr(self, 'current_motor_info'):
            self.current_motor_info.update(motor_info)
        else:
            self.current_motor_info = motor_info
            
        # ensure sorted and unique by time (keep last occurrence on duplicates)
        data.sort(key=lambda x: x[0])
        deduped = []
        last_t = None
        for t, y in data:
            if last_t is None or t > last_t + 1e-9:
                deduped.append((t, y))
                last_t = t
            else:
                deduped[-1] = (t, y)
                last_t = t
        return deduped

    def parse_rasp_eng_thrust(self, path):
        """Parse a RASP/ENG motor file. Extract motor specs from header and time/thrust pairs.
        RASP format: name dia(mm) len(mm) delays prop_mass(kg) total_mass(kg) manufacturer
        Lines starting with ';' or '#' are comments."""
        data = []
        motor_info = {}
        
        with open(path, 'r') as f:
            lines = f.readlines()
        # strip whitespace
        lines = [ln.strip() for ln in lines]
        # skip initial comments
        i = 0
        while i < len(lines) and (not lines[i] or lines[i].startswith(';') or lines[i].startswith('#')):
            i += 1
        if i >= len(lines):
            return data
            
        # Parse header line (RASP format)
        header_line = lines[i]
        try:
            parts = header_line.split()
            if len(parts) >= 6:
                motor_info['name'] = parts[0]
                motor_info['diameter'] = float(parts[1])  # mm
                motor_info['length'] = float(parts[2])    # mm
                # Skip delays (parts[3])
                # RASP .eng specifies both mass fields in KILOGRAMS. They
                # were read as grams, so every real motor file downloaded
                # from ThrustCurve.org reported its masses 1000x too small
                # ("Propellant Mass: 1.6g" for a 1.6 kg reload).
                motor_info['propellant_mass'] = float(parts[4])  # kg
                motor_info['total_mass'] = float(parts[5])       # kg
                if len(parts) >= 7:
                    # Field 7 is the MANUFACTURER, not Isp. Reading it as a
                    # float raised on every real file ("AeroTech"), was
                    # swallowed by the except below, and on the odd file that
                    # does put a number there printed it as a specific
                    # impulse. rasp.write_eng documents the same layout.
                    motor_info['manufacturer'] = parts[6]
        except (ValueError, IndexError):
            # If header parsing fails, continue with just thrust data
            pass
            
        # Store motor info for later use
        if hasattr(self, 'current_motor_info'):
            self.current_motor_info.update(motor_info)
        else:
            self.current_motor_info = motor_info
            
        i += 1  # Skip header line
        
        # read time/thrust pairs
        while i < len(lines):
            ln = lines[i]
            if not ln or ln.startswith(';') or ln.startswith('#'):
                # stop at blank/comment -> end of first motor block
                break
            parts = ln.split()
            if len(parts) < 2:
                break
            try:
                t = float(parts[0])
                thrust = float(parts[1])
                data.append((t, thrust))
            except Exception:
                # stop on parse failure for safety
                break
            i += 1
        data.sort(key=lambda x: x[0])
        # dedup
        deduped = []
        last_t = None
        for t, y in data:
            if last_t is None or t > last_t + 1e-9:
                deduped.append((t, y))
                last_t = t
            else:
                deduped[-1] = (t, y)
                last_t = t
        return deduped

    def create_telemetry_dashboard(self, layout):
        """Create a professional mission control-style telemetry dashboard"""
        
        # Initialize telemetry data storage
        self.telemetry_data = {
            'altitude': 0.0,
            'velocity': 0.0,
            'acceleration': 0.0,
            'thrust': 0.0,
            'mass': 0.0,
            'g_force': 0.0,
            'drag_force': 0.0,
            'mach_number': 0.0,
            'flight_phase': 'Standby',
            'time': 0.0
        }
        
        theme = self.themes[self.current_theme]
        
        # Removed scoreboard (telemetry metrics, phase/time, indicators) per user request; retaining only core graph functionality.
        
        # Initialize telemetry update timer
        self.telemetry_timer = QtCore.QTimer()
        self.telemetry_timer.timeout.connect(self.update_telemetry_displays)
        self.telemetry_timer.timeout.connect(self.update_force_diagram)
        self.telemetry_timer.start(100)  # Update every 100ms for smooth display

    def create_professional_metric(self, label, value, unit, color):
        """Create a professional mission control-style metric display"""
        widget = QtWidgets.QFrame()
        widget.setStyleSheet(f"""
            QFrame {{
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 rgba(52, 73, 94, 0.8), 
                    stop: 1 rgba(44, 62, 80, 0.9));
                border: 1px solid {color};
                border-radius: 6px;
                margin: 2px;
            }}
        """)
        
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        
        # Label
        label_widget = QtWidgets.QLabel(label)
        label_widget.setAlignment(QtCore.Qt.AlignCenter)
        label_widget.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 8px;
                font-weight: bold;
                letter-spacing: 1px;
                margin-bottom: 2px;
            }}
        """)
        layout.addWidget(label_widget)
        
        # Value with unit
        value_widget = QtWidgets.QLabel(f"{value} {unit}")
        value_widget.setAlignment(QtCore.Qt.AlignCenter)
        value_widget.setStyleSheet("""
            QLabel {
                color: #FFFFFF;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 12px;
                font-weight: bold;
                background: rgba(0, 0, 0, 0.3);
                padding: 4px;
                border-radius: 3px;
            }
        """)
        layout.addWidget(value_widget)
        
        # Store reference for updates
        widget.value_label = value_widget
        return widget

    def create_force_diagram(self, layout):
        """Create the real-time force vector diagram display"""
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.figure import Figure
        import matplotlib.patches as patches
        
        # Create matplotlib figure for force diagram
        self.force_fig = Figure(figsize=(4, 6), dpi=80)
        self.force_fig.patch.set_facecolor(self.themes[self.current_theme]["plot_bg"])
        self.force_canvas = FigureCanvas(self.force_fig)
        self.force_canvas.setMinimumSize(200, 300)
        
        # Force diagram visualization
        self.force_ax = self.force_fig.add_subplot(111)
        self.force_ax.set_xlim(-2, 2)
        self.force_ax.set_ylim(-1, 4)
        self.force_ax.set_aspect('equal')
        self.force_ax.axis('off')
        
        # Initialize force vectors
        self.thrust_vector = None
        self.drag_vector = None  
        self.weight_vector = None
        self.net_vector = None
        
        layout.addWidget(self.force_canvas)

        # Scrub through the flight. Without this the diagram only ever showed
        # whatever instant the launch animation happened to be at, so the
        # forces were unreadable in motion and frozen at zero the rest of the
        # time. Dragging this walks the real simulated flight.
        scrub_row = QtWidgets.QHBoxLayout()
        self.force_scrubber = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.force_scrubber.setRange(0, 1000)
        self.force_scrubber.setEnabled(False)
        self.force_scrubber.valueChanged.connect(self._on_force_scrub)
        self.force_time_label = QtWidgets.QLabel("Run a simulation to scrub")
        self.force_time_label.setStyleSheet(
            f"color:{app_theme.PALETTE['text_dim']}; font-size:9pt;")
        self.force_time_label.setMinimumWidth(150)
        scrub_row.addWidget(self.force_scrubber, 1)
        scrub_row.addWidget(self.force_time_label)
        layout.addLayout(scrub_row)

        # Force magnitude displays
        forces_grid = QtWidgets.QGridLayout()
        forces_grid.setSpacing(8)
        
        # Create force value displays with theme-aware styling
        self.thrust_force_display = self.create_force_display("THRUST", "0.0", "N", "#00FF00")
        self.drag_force_display = self.create_force_display("DRAG", "0.0", "N", "#FF4444") 
        self.chute_drag_display = self.create_force_display("CHUTE", "0.0", "N", "#FF00FF")
        self.weight_force_display = self.create_force_display("WEIGHT", "0.0", "N", "#FFAA00")
        self.net_force_display = self.create_force_display("NET", "0.0", "N", "#00D4FF")
        self.velocity_display = self.create_force_display("VELOCITY", "0.0", "m/s", "#002FFF")
        
        forces_grid.addWidget(self.thrust_force_display, 0, 0)
        forces_grid.addWidget(self.drag_force_display, 0, 1)
        forces_grid.addWidget(self.chute_drag_display, 1, 0)
        forces_grid.addWidget(self.weight_force_display, 1, 1)
        forces_grid.addWidget(self.net_force_display, 2, 0)
        forces_grid.addWidget(self.velocity_display, 2, 1)
        
        layout.addLayout(forces_grid)
        
        # Initialize force diagram update
        self.setup_initial_force_diagram()

    def create_force_display(self, label, value, unit, color):
        """Create a compact force value display"""
        widget = QtWidgets.QFrame()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        
        # Theme-aware styling
        theme = self.themes[self.current_theme]
        if self.current_theme == "retro":
            widget.setStyleSheet(f"""
                QFrame {{
                    background-color: {theme['colors']['secondary_bg']};
                    border: 1px solid {theme['colors']['accent']};
                    border-radius: 4px;
                }}
                QLabel {{
                    background-color: transparent;
                    color: {theme['colors']['primary_text']};
                }}
            """)
        else:
            widget.setStyleSheet(f"""
                QFrame {{
                    background: {theme['telemetry']['bg']};
                    border: 1px solid {theme['telemetry']['border']};
                    border-radius: 3px;
                }}
                QLabel {{
                    background-color: transparent;
                    color: {theme['telemetry']['gauge_text']};
                    font-family: 'Consolas', 'Monaco', monospace;
                }}
            """)
        
        # Label
        label_widget = QtWidgets.QLabel(label)
        label_widget.setAlignment(QtCore.Qt.AlignCenter)
        label_widget.setStyleSheet(f"font-size: 9px; font-weight: bold; color: {color};")
        layout.addWidget(label_widget)
        
        # Value
        value_widget = QtWidgets.QLabel(f"{value} {unit}")
        value_widget.setAlignment(QtCore.Qt.AlignCenter)
        value_widget.setStyleSheet("font-size: 11px; font-weight: bold;")
        layout.addWidget(value_widget)
        
        # Store references for updates
        widget.label = label_widget
        widget.value = value_widget
        widget.unit = unit
        
        return widget

    def setup_initial_force_diagram(self):
        """Setup the initial force diagram visualization"""
        import matplotlib.patches as patches
        
        self.force_ax.clear()
        self.force_ax.set_xlim(-2, 2)
        self.force_ax.set_ylim(-1, 4)
        self.force_ax.set_aspect('equal')
        self.force_ax.axis('off')
        
        self.draw_rocket_body()
        self.force_canvas.draw()

    # -- the free-body diagram -------------------------------------------
    #
    # Drawn to the SHAPE OF THE REAL ROCKET, and every arrow starts at the
    # point on the airframe where that force actually acts: thrust at the
    # nozzle, weight at the centre of gravity, body drag at the centre of
    # pressure, parachute drag at the nose. That is the whole point of a
    # stability test - it is the gap between CG and CP, and which side of it
    # drag pulls on, that decides whether the rocket flies straight.
    #
    # The rocket is always drawn the same height on screen whatever its real
    # length; only the proportions change. Stations are quoted in metres from
    # the nose tip, matching how cg_m and cp_m come out of the flight model.

    NOSE_Y = 2.55           # where the nose tip sits in axis units
    TAIL_Y = 0.35           # where the tail sits
    LANE = 0.62             # sideways spacing between arrow columns
    PIVOT_Y = 1.45          # the page point the airframe rotates about

    def _rocket_geometry(self):
        """Screen geometry for the current airframe, and station -> y.

        Falls back to a generic slender rocket when no airframe has been set
        up yet, so the panel draws something honest rather than nothing.
        """
        length = diameter = nose_len = boattail = 0.0
        fin_root = fin_span = 0.0
        try:
            af = self.vehicle_tab.airframe()
            length = af.total_length or 0.0
            diameter = af.body_diameter_m or 0.0
            nose_len = af.nose_length_m or 0.0
            boattail = af.boattail_length_m or 0.0
            fin_root = af.fin_root_chord_m or 0.0
            fin_span = af.fin_span_m or 0.0
        except Exception:
            pass
        if length <= 0:
            length, diameter, nose_len = 2.0, 0.10, 0.45
            fin_root, fin_span = 0.22, 0.11

        height = self.NOSE_Y - self.TAIL_Y
        # Half-width, deliberately exaggerated. A real high-power airframe is
        # 20 to 30 calibers long, so drawn true to scale it is a hairline with
        # no visible nose, fins or nozzle and nothing for the arrows to point
        # at. Stations stay exact - it is only the width that is stretched, so
        # where CG and CP sit along the body is still honest.
        half_w = max(0.085, min(0.16, 0.5 * (diameter / length) * height))

        def station_y(station_m):
            """Metres from the nose tip -> y in axis units."""
            frac = min(1.0, max(0.0, station_m / length))
            return self.NOSE_Y - frac * height

        return {
            'length': length, 'diameter': diameter, 'half_w': half_w,
            'nose_len': nose_len, 'boattail': boattail,
            'fin_root': fin_root, 'fin_span': fin_span,
            'station_y': station_y, 'height': height,
        }

    def draw_rocket_body(self, geom=None, tilt_deg=0.0):
        """Draw the airframe in proportion, rotated to its real attitude.

        ``tilt_deg`` is the angle of the nose from vertical, the same number
        the flight model reports, so the diagram shows the vehicle the way the
        tracker beside it does - including upside down, once it has pitched
        right over.
        """
        import matplotlib.patches as patches

        g = geom or self._rocket_geometry()
        sy, w = g['station_y'], g['half_w']
        y_nose_base = sy(g['nose_len'])
        y_body_end = sy(g['length'] - g['boattail'])
        y_tail = sy(g['length'])
        rot = self._rotator(tilt_deg)

        body = patches.Polygon(
            [rot(-w, y_body_end), rot(w, y_body_end),
             rot(w, y_nose_base), rot(-w, y_nose_base)],
            closed=True, facecolor='#C0C0C0', edgecolor='#2B2B2B',
            linewidth=1.4, zorder=3)
        self.force_ax.add_patch(body)

        nose = patches.Polygon(
            [rot(-w, y_nose_base), rot(w, y_nose_base), rot(0, self.NOSE_Y)],
            closed=True, facecolor='#A8A8A8', edgecolor='#2B2B2B',
            linewidth=1.4, zorder=3)
        self.force_ax.add_patch(nose)

        if g['boattail'] > 0 and y_body_end > y_tail:
            boat = patches.Polygon(
                [rot(-w, y_body_end), rot(w, y_body_end),
                 rot(w * 0.6, y_tail), rot(-w * 0.6, y_tail)],
                closed=True, facecolor='#9A9A9A', edgecolor='#2B2B2B',
                linewidth=1.2, zorder=3)
            self.force_ax.add_patch(boat)

        if g['fin_root'] > 0 and g['fin_span'] > 0:
            root_h = (g['fin_root'] / g['length']) * g['height']
            span_w = min(0.42, (g['fin_span'] / g['length']) * g['height'] * 2.2)
            y_fin_root = sy(g['length'] - g['boattail'])
            for side in (-1, 1):
                fin = patches.Polygon(
                    [rot(side * w, y_fin_root + root_h),
                     rot(side * w, y_fin_root),
                     rot(side * (w + span_w), y_fin_root)],
                    closed=True, facecolor='#8A8A8A', edgecolor='#2B2B2B',
                    linewidth=1.1, zorder=2)
                self.force_ax.add_patch(fin)

        nozzle = patches.Polygon(
            [rot(-w * 0.5, y_tail), rot(w * 0.5, y_tail),
             rot(w * 0.5, y_tail - 0.07), rot(-w * 0.5, y_tail - 0.07)],
            closed=True, facecolor='#404040', edgecolor='#2B2B2B',
            linewidth=1, zorder=3)
        self.force_ax.add_patch(nozzle)
        return g

    def _rotator(self, tilt_deg):
        """Map body coordinates to the page, rotating about the centre.

        Body coordinates are the upright drawing: x across, y along the axis
        with the nose up. Positive tilt leans the nose to the right, matching
        the sign the flight model uses for the angle from vertical.
        """
        ang = math.radians(tilt_deg or 0.0)
        ca, sa = math.cos(ang), math.sin(ang)
        px, py = 0.0, self.PIVOT_Y

        def rot(bx, by):
            dx, dy = bx - px, by - py
            return (px + dx * ca + dy * sa, py - dx * sa + dy * ca)
        return rot

    def _draw_cg_cp(self, geom, state):
        """Mark the centre of gravity and centre of pressure on the airframe.

        The standard symbols: CG is the quartered circle, CP the open one.
        Their separation IS the static margin, so it is drawn to scale and
        labelled - on a stability test that is the number that matters.
        """
        import matplotlib.patches as patches

        sy = geom['station_y']
        half_w_label = geom['half_w'] + 0.03
        rot = self._rotator(state.get('tilt_deg') or 0.0)
        drawn = {}
        for key, colour, filled, tag in (
                ('cg_m', '#FFAA00', True, 'CG'),
                ('cp_m', '#FF4444', False, 'CP')):
            station = state.get(key)
            if not station or station <= 0:
                continue
            y = sy(station)
            drawn[key] = y
            cx, cy = rot(0, y)
            self.force_ax.add_patch(patches.Circle(
                (cx, cy), 0.062, facecolor=(colour if filled else 'none'),
                edgecolor=colour, linewidth=1.6, zorder=6, alpha=0.95))
            if filled:
                # The quartered look, so CG reads as CG at a glance.
                a1, a2 = rot(-0.062, y), rot(0.062, y)
                b1, b2 = rot(0, y - 0.062), rot(0, y + 0.062)
                self.force_ax.plot([a1[0], a2[0]], [a1[1], a2[1]],
                                   color='#1A1A1A', linewidth=0.9, zorder=7)
                self.force_ax.plot([b1[0], b2[0]], [b1[1], b2[1]],
                                   color='#1A1A1A', linewidth=0.9, zorder=7)
            # CG and CP can be within a caliber of each other on a marginally
            # stable rocket - exactly the case worth looking at - so nudge the
            # labels apart rather than letting them print on top of each other.
            y_label = y
            if 'cg_m' in drawn and 'cp_m' in drawn and key == 'cp_m':
                if abs(drawn['cg_m'] - y) < 0.15:
                    y_label = y - 0.09 if y < drawn['cg_m'] else y + 0.09
            lx, ly = rot(half_w_label, y_label)
            self.force_ax.text(lx, ly, tag, fontsize=7,
                               color=colour, fontweight='bold', va='center',
                               ha='left', zorder=7)

        margin = state.get('stability_cal')
        if margin and 'cg_m' in drawn and 'cp_m' in drawn:
            # A bracket spanning the gap, labelled in calibers.
            y_hi, y_lo = sorted((drawn['cg_m'], drawn['cp_m']), reverse=True)
            x = -geom['half_w'] - 0.14
            p_lo, p_hi = rot(x, y_lo), rot(x, y_hi)
            self.force_ax.plot([p_lo[0], p_hi[0]], [p_lo[1], p_hi[1]],
                               color='#7FDBFF', linewidth=1.2, zorder=6)
            for y in (y_lo, y_hi):
                a, b = rot(x, y), rot(x + 0.06, y)
                self.force_ax.plot([a[0], b[0]], [a[1], b[1]],
                                   color='#7FDBFF', linewidth=1.2, zorder=6)
            tx, ty = rot(x - 0.10, (y_lo + y_hi) / 2)
            # Keep the text the right way up. Following the body all the way
            # round puts it upside down once the vehicle passes horizontal,
            # so flip it back through 180 rather than let it read mirrored.
            rotation = 90 - (state.get('tilt_deg') or 0.0)
            rotation = (rotation + 180) % 360 - 180
            if rotation > 90 or rotation < -90:
                rotation += 180
            self.force_ax.text(tx, ty, f"{margin:.2f} cal", fontsize=7,
                               color='#7FDBFF', fontweight='bold',
                               rotation=rotation,
                               va='center', ha='center', zorder=7)

    def _draw_vector(self, origin, direction, length, colour, label,
                     value_text, dashed=False, approach=False, zorder=5,
                     offset=0.0):
        """One arrow, pointing wherever the force actually points.

        ``direction`` is a unit (dx, dy) on the page, so a force is drawn
        along its real line of action rather than being forced onto the
        vertical. ``approach`` puts the head at the application point and the
        tail out beyond it - how thrust has to be drawn, since an arrow
        growing forward out of the nozzle would run up through the airframe.

        ``offset`` slides the whole arrow sideways, perpendicular to its own
        direction, and leaves a dotted leader back to the point it really acts
        on. Thrust and drag are both along the body axis, and weight and the
        resultant both start at the centre of gravity, so drawn exactly on
        their lines of action they land on top of each other and neither the
        arrows nor their labels can be read. Sliding them apart keeps every
        arrow's DIRECTION honest, which is the part that carries the physics,
        and only moves where it is drawn.
        """
        ax = self.force_ax
        dx, dy = direction
        norm = math.hypot(dx, dy)
        if norm < 1e-9 or length <= 0:
            return
        dx, dy = dx / norm, dy / norm
        px, py = -dy, dx                       # unit perpendicular
        ox, oy = origin
        ax_, ay = ox + px * offset, oy + py * offset
        if abs(offset) > 1e-9:
            ax.plot([ox, ax_], [oy, ay], color=colour, linewidth=0.8,
                    linestyle=':', alpha=0.5, zorder=4)
        if approach:
            sx, sy_ = ax_ - dx * length, ay - dy * length
            tx, ty = sx - dx * 0.13, sy_ - dy * 0.13
        else:
            sx, sy_ = ax_, ay
            tx, ty = ax_ + dx * (length + 0.13), ay + dy * (length + 0.13)
        ax.arrow(sx, sy_, dx * length, dy * length,
                 head_width=0.095, head_length=0.085,
                 fc=colour, ec=colour, linewidth=2.2, zorder=zorder,
                 length_includes_head=True, alpha=0.95,
                 linestyle=('dashed' if dashed else 'solid'))
        ax.text(tx, ty, f"{label}\n{value_text}",
                ha='center', va='center', fontsize=7, color=colour,
                fontweight='bold', linespacing=0.95, zorder=7)

    def _pad_rail_angle(self):
        """Rail angle, for drawing the vehicle on the pad as it really sits."""
        try:
            return float(self.launch_angle_input.value())
        except Exception:
            return 0.0

    def force_state(self, t=None):
        """The forces acting on the rocket at time t, from the real flight.

        This used to recompute its own approximations - a fixed 1.5 chute Cd,
        sea-level density, g as 9.81 - and only while the launch animation was
        running, so the panel read zero at every other moment. The simulation
        already produces thrust, drag, mass, local gravity and canopy drag at
        every timestep, so the diagram shows those instead of a second, worse
        estimate that could disagree with the flight it claims to describe.

        Returns a dict of real numbers, or the standing-on-the-pad state when
        no flight has been run yet.
        """
        rows = self.stability_rows()
        if rows:
            if t is None:
                t = getattr(self, 'launch_time', 0.0)
            # Nearest sample. The output step is small enough that
            # interpolating would not change what the arrows look like.
            row = min(rows, key=lambda r: abs((r.get('time') or 0.0) - t))
            mass = row.get('mass') or 0.0
            gravity = row.get('gravity') or 9.80665
            thrust = max(0.0, row.get('thrust') or 0.0)
            velocity = row.get('velocity') or 0.0
            # The model reports total drag; split the canopy back out so the
            # panel can show them separately the way it always claimed to.
            drag_total = abs(row.get('drag') or 0.0)
            cda_chute = row.get('cda_recovery') or 0.0
            cda_body = max(0.0, (row.get('Cd_eff') or 0.0) * (row.get('A_eff') or 0.0))
            share = cda_chute / (cda_chute + cda_body) if (cda_chute + cda_body) > 0 else 0.0
            chute_drag = drag_total * share
            body_drag = drag_total - chute_drag
            weight = mass * gravity
            return {
                'time': row.get('time') or 0.0,
                'thrust': thrust, 'body_drag': body_drag,
                'chute_drag': chute_drag, 'weight': weight,
                'net': thrust - drag_total * (1 if velocity >= 0 else -1) - weight,
                'velocity': velocity, 'mass': mass,
                'on_pad': False, 'has_flight': True,
                'chute_deployed': bool(row.get('chute_deployed')),
                # Where along the airframe things act, so the arrows can be
                # anchored to the real stations rather than floating.
                'cg_m': row.get('cg_m'), 'cp_m': row.get('cp_m'),
                'stability_cal': row.get('stability_cal'),
                # Attitude and the wind, so the diagram can be drawn in the
                # vehicle's real orientation instead of pinned to one axis.
                'tilt_deg': row.get('angle_from_vertical_deg') or 0.0,
                'aoa_deg': row.get('angle_of_attack_deg') or 0.0,
                'v_horizontal': row.get('horizontal_velocity') or 0.0,
                'wind_speed': row.get('wind_speed') or 0.0,
                # Scale for the velocity arrow. It is not a force, so it
                # cannot share the force scale; against the fastest moment of
                # this flight it grows and shrinks meaningfully.
                'v_max': max((abs(r.get('velocity') or 0.0) for r in rows),
                             default=0.0),
            }

        # No flight yet: the rocket is sitting on the rail. Weight is real and
        # the rail pushes back exactly as hard, which is worth drawing - it is
        # a true free-body diagram, not a blank panel.
        try:
            mass = self.get_inputs_for_simulation()[0] or 0.0
        except Exception:
            mass = 0.0
        weight = mass * 9.80665
        # The CG and CP are known before any flight is run - they are set right
        # here on this tab - so mark them on the pad drawing too. Reading the
        # spin boxes means the diagram answers as they are dragged, which is
        # what a stability test is for.
        cg = cp = margin = None
        try:
            cg = self.center_of_mass_input.value()
            cp = self.center_of_pressure_input.value()
            diameter = self.vehicle_tab.airframe().body_diameter_m
            if diameter and diameter > 0:
                margin = (cp - cg) / diameter
        except Exception:
            pass
        return {'time': 0.0, 'thrust': 0.0, 'body_drag': 0.0, 'chute_drag': 0.0,
                'weight': weight, 'net': 0.0, 'velocity': 0.0, 'mass': mass,
                'on_pad': True, 'has_flight': False, 'chute_deployed': False,
                'cg_m': cg, 'cp_m': cp, 'stability_cal': margin, 'v_max': 0.0,
                'tilt_deg': self._pad_rail_angle(), 'aoa_deg': 0.0,
                'v_horizontal': 0.0,
                'wind_speed': (self.wind_speed_input.value()
                               if hasattr(self, 'wind_speed_input') else 0.0)}

    def update_force_diagram(self, t=None):
        """Draw the free-body diagram for the current moment of flight."""
        if not hasattr(self, 'force_ax') or not hasattr(self, 'force_canvas'):
            return

        if t is None:
            # The idle refresh timer calls this with no time. Without this it
            # fell back to launch_time (0.0) and redrew the pad every 150ms,
            # wiping out whatever moment the user had scrubbed to a fraction
            # of a second after they got there.
            t = getattr(self, '_force_scrub_time', None)
        state = self.force_state(t)
        ax = self.force_ax
        ax.clear()
        ax.set_xlim(-2.0, 2.0)
        ax.set_ylim(-1.45, 3.35)
        ax.set_aspect('equal')
        ax.axis('off')

        tilt = state.get('tilt_deg') or 0.0
        geom = self.draw_rocket_body(tilt_deg=tilt)
        sy = geom['station_y']
        rot = self._rotator(tilt)
        self._draw_cg_cp(geom, state)

        # Where each force acts, on the rotated airframe.
        mid = sy(geom['length'] * 0.5)
        cg_pt = rot(0, sy(state['cg_m']) if state.get('cg_m') else mid)
        cp_pt = rot(0, sy(state['cp_m']) if state.get('cp_m') else mid)
        tail_pt = rot(0, sy(geom['length']))
        nose_pt = rot(0, self.NOSE_Y)

        # Directions on the page. Up the page is up; the body axis leans with
        # the vehicle, so thrust leans with it too. Weight does not: it is
        # always straight down however the rocket is lying.
        ang = math.radians(tilt)
        axis_dir = (math.sin(ang), math.cos(ang))       # nose-forward
        down_dir = (0.0, -1.0)

        # Drag opposes the AIR-RELATIVE motion, which is the velocity the
        # rocket has through the air minus the wind - not its ground track.
        # That difference is the whole reason a crosswind turns a rocket.
        v_up = state.get('velocity') or 0.0
        v_side = state.get('v_horizontal') or 0.0
        wind = state.get('wind_speed') or 0.0
        rel_x, rel_y = v_side - wind, v_up
        rel_speed = math.hypot(rel_x, rel_y)
        if rel_speed > 1e-6:
            flow_dir = (rel_x / rel_speed, rel_y / rel_speed)
            drag_dir = (-flow_dir[0], -flow_dir[1])
        else:
            flow_dir = (0.0, 1.0)
            drag_dir = down_dir

        v_ground = math.hypot(v_side, v_up)
        vel_dir = ((v_side / v_ground, v_up / v_ground)
                   if v_ground > 1e-6 else (0.0, 1.0))

        span = 0.95
        stub = 0.11

        def arrow_len(magnitude, reference):
            """Proportional length, floored so small forces still read."""
            if not reference:
                return stub
            return max(stub, (abs(magnitude) / reference) * span)

        L = self.LANE
        forces = [
            # magnitude, origin, direction, colour, label, approach, offset
            (state['thrust'], tail_pt, axis_dir, '#00FF88', 'Thrust', True, 0.0),
            (state['body_drag'], cp_pt, drag_dir, '#FF4444', 'Drag', False, -L),
            (state['chute_drag'], nose_pt, drag_dir, '#FF00FF', 'Chute', False, L),
            (state['weight'], cg_pt, down_dir, '#FFAA00', 'Weight', False, L),
        ]
        if state['on_pad'] and state['weight'] > 0:
            forces.append((state['weight'], tail_pt, axis_dir, '#7FDBFF',
                           'Rail', False, 2 * L))

        biggest = max([abs(f[0]) for f in forces] + [abs(state['net']), 1.0])
        floor = max(0.5, biggest * 0.005)
        for magnitude, origin, direction, colour, label, approach, off in forces:
            if magnitude < floor:
                continue
            self._draw_vector(origin, direction, arrow_len(magnitude, biggest),
                              colour, label, f"{magnitude:,.0f} N",
                              approach=approach, offset=off)

        # Net force, as the real resultant of the arrows above rather than a
        # single up-or-down number. Drawn from the centre of gravity, which is
        # the point the whole vehicle accelerates about.
        net_x = (state['thrust'] * axis_dir[0]
                 + (state['body_drag'] + state['chute_drag']) * drag_dir[0])
        net_y = (state['thrust'] * axis_dir[1]
                 + (state['body_drag'] + state['chute_drag']) * drag_dir[1]
                 - state['weight'])
        if state['on_pad']:
            net_x = net_y = 0.0
        net_mag = math.hypot(net_x, net_y)
        if net_mag > floor:
            self._draw_vector(cg_pt, (net_x, net_y),
                              arrow_len(net_mag, biggest), '#00D4FF', 'Net',
                              f"{net_mag:,.0f} N", zorder=6, offset=-L)

        # Velocity is not a force, so it gets its own scale and a dashed
        # shaft. Drawn from the CG along the direction of travel.
        v_max = state.get('v_max') or 0.0
        if v_ground > 0.5 and v_max > 0:
            self._draw_vector(cg_pt, vel_dir, arrow_len(v_ground, v_max),
                              '#B388FF', 'Velocity', f"{v_ground:,.1f} m/s",
                              dashed=True, offset=-2 * L)

        # Wind. Not a force on the rocket by itself - it acts by changing the
        # air-relative flow, which is what the drag arrow already shows - so
        # it is drawn to one side as the condition it is, in the direction it
        # blows, not as another arrow on the airframe.
        if abs(wind) > 0.1:
            wx = -1.55 if wind > 0 else 1.55
            wy = 2.95
            wlen = min(0.55, 0.05 * abs(wind) + 0.12)
            ax.arrow(wx, wy, math.copysign(wlen, wind), 0,
                     head_width=0.08, head_length=0.07, fc='#7FDBFF',
                     ec='#7FDBFF', linewidth=1.8, length_includes_head=True,
                     alpha=0.9, zorder=5)
            ax.text(wx + math.copysign(wlen / 2, wind), wy + 0.13,
                    f"Wind {abs(wind):.0f} m/s", ha='center', va='bottom',
                    fontsize=7, color='#7FDBFF', fontweight='bold')

        # Angle of attack: the gap between where the nose points and where the
        # air is coming from. It is the number that says whether the vehicle
        # is flying or tumbling, so it is worth stating outright.
        aoa = state.get('aoa_deg') or 0.0
        if not state['on_pad']:
            ax.text(1.95, -1.15, f"tilt {tilt:+.0f}\u00b0   AoA {aoa:.0f}\u00b0",
                    ha='right', va='center', fontsize=7.5,
                    color=('#FF4444' if abs(aoa) > 15 else '#7A7A86'),
                    fontweight='bold')

        headline = ('On the pad' if state['on_pad'] else
                    f"t = {state['time']:.2f} s")
        ax.set_title(f"Forces on the rocket - {headline}", fontsize=9,
                     fontweight='bold', color='#FFFFFF', pad=6)
        if not state['has_flight']:
            note = "Run a simulation to scrub through the flight"
        elif state['on_pad'] and net_mag <= floor:
            note = "Net 0 N - balanced on the rail"
        else:
            note = None
        if note:
            ax.text(0, -1.30, note, ha='center', va='center',
                    fontsize=7.5, color='#7A7A86')

        self._update_force_readouts(state)
        self.force_canvas.draw_idle()

    def _update_force_readouts(self, state):
        """Push the same numbers to the gauges under the diagram."""
        if not hasattr(self, 'thrust_force_display'):
            return
        for widget, value in (
                (self.thrust_force_display, state['thrust']),
                (self.drag_force_display, state['body_drag']),
                (self.chute_drag_display, state['chute_drag']),
                (self.weight_force_display, state['weight']),
                (self.net_force_display, state['net'])):
            widget.value.setText(f"{value:,.0f} {widget.unit}")
        self.velocity_display.value.setText(
            f"{state['velocity']:.1f} {self.velocity_display.unit}")
        live = "#FF00FF" if state['chute_deployed'] else "#808080"
        self.chute_drag_display.label.setStyleSheet(
            f"font-size: 9px; font-weight: bold; color: {live};")

    def _on_force_scrub(self, value):
        """Time slider under the force diagram."""
        rows = self.stability_rows()
        if not rows:
            return
        span = (rows[-1].get('time') or 0.0)
        t = span * (value / 1000.0)
        self._force_scrub_time = t
        self.force_time_label.setText(f"t = {t:6.2f} s  of {span:.1f} s")
        self.update_force_diagram(t)

    def enable_force_scrubber(self):
        """Switch the scrubber on once there is a flight to scrub."""
        if not hasattr(self, 'force_scrubber'):
            return
        rows = self.stability_rows()
        has = bool(rows)
        self.force_scrubber.setEnabled(has)
        self._force_scrub_time = 0.0 if has else None
        if has:
            self.force_scrubber.setValue(0)
            self._on_force_scrub(0)

    # (Removed temporary stub definition of update_launch_frame; real implementation appears later.)

    def update_retro_indicator(self, widget, active):
        """Update retro-style status indicator"""
        if widget.is_active != active:
            widget.is_active = active
            theme = self.themes["retro"]
            color = theme['telemetry']['status_active'] if active else theme['telemetry']['status_inactive']
            widget.status_circle.setStyleSheet(f"color: {color}; font-size: 12px;")

    def update_professional_indicator(self, widget, active, text=None):
        """Update professional-style status indicator"""
        if widget.is_active != active or (text and hasattr(widget, 'status_text') and widget.status_text.text() != text):
            widget.is_active = active
            theme = self.themes["professional"]
            
            # Update indicator light
            color = theme['telemetry']['status_active'] if active else theme['telemetry']['status_inactive']
            widget.led.setStyleSheet(f"""
                QLabel {{
                    color: {color};
                    font-size: 14px;
                    font-weight: bold;
                    background: rgba(0, 255, 65, 0.2);
                    border-radius: 8px;
                }}
            """)
            
            # Update status text if provided
            if text and hasattr(widget, 'status_text'):
                widget.status_text.setText(text)
                text_color = theme['telemetry']['status_active'] if active else theme['telemetry']['gauge_text']
                widget.status_text.setStyleSheet(f"""
                    QLabel {{
                        color: {text_color};
                        font-size: 9px;
                        font-family: 'Courier New';
                        font-weight: bold;
                    }}
                """)

    def unit_system(self):
        """'metric' or 'imperial', per the Settings tab."""
        return (app_units.IMPERIAL if self.unit_select.currentIndex() == 1
                else app_units.METRIC)

    def on_unit_system_changed(self, _index=None):
        """Switch every unit-aware field to the chosen system."""
        system = self.unit_system()
        for field in self.findChildren(unit_fields.UnitField):
            field.set_system(system)
        # The Simulation tab keeps its own combos rather than UnitFields,
        # because a saved profile stores the selected index.
        self._apply_sim_unit_system(system)
        # The results summary is written in whatever system was current when
        # the flight ran, so it has to be rebuilt too - otherwise it is the
        # only panel left in the old units.
        results = getattr(self, '_last_display_results', None)
        if results:
            try:
                self.display_results(results)
            except Exception:
                traceback.print_exc()

    def on_theme_changed(self, theme_name):
        """Handle theme selection change"""
        if theme_name and theme_name in self.themes and theme_name != self.current_theme:
            self.current_theme = theme_name
            self.apply_theme(theme_name)
            self.update_theme_preview()
            
            # Save theme preference
            self.save_theme_preference()

    def toggle_fbd_animation(self):
        # Animation removed
        return

    # -- the stability test's own flight ---------------------------------
    #
    # This tab asks one question: is the rocket stable for the WHOLE launch?
    # A single "CP minus CG" cannot answer it, because neither point stays
    # put. Propellant leaves from a particular place, so the centre of
    # gravity walks along the airframe as the motor burns, and the centre of
    # pressure moves with Mach number. The margin is a curve over the flight,
    # and what matters is its worst moment - which is usually just after
    # burnout, when the CG has moved as far as it is going to and the rocket
    # is still fast.
    #
    # So this tab flies the real model itself rather than showing whatever
    # the Simulation tab last happened to run, and everything on the page -
    # the animation, the force vectors, the CG and CP marks, the verdict -
    # comes from that one flight.

    # Calibers of static margin that count as stable. Under the low figure
    # the fins cannot correct a disturbance; over the high one the rocket
    # weathercocks hard into the wind and loses altitude to it.
    STABLE_MIN_CAL = 1.5
    STABLE_MAX_CAL = 4.0

    def stability_rows(self):
        """The stability test's own flight.

        Falls back to the Simulation tab's last run so the panel still has
        something real to draw before this tab has been flown.
        """
        return (getattr(self, '_stability_results', None)
                or getattr(self, '_last_results', None))

    def run_stability_test(self):
        """Fly the rocket for this tab, with the same model the app flies.

        Returns the rows, or None with the reason already on screen.
        """
        try:
            self.save_inputs()
            (m, Cd, A, rho, time_step, _fin_t, _fin_l, body_diameter,
             chute_height, chute_size, chute_deploy_time, chute_cd,
             prop_m) = self.get_inputs_for_simulation()
        except Exception:
            self._set_stability_note(
                "Enter the rocket's mass, drag and area on the Simulation tab "
                "before running the stability test.", ok=False)
            return None

        if not all((m > 0, Cd > 0, A > 0, rho > 0)):
            self._set_stability_note(
                "Mass, drag coefficient, area and air density all have to be "
                "positive before this can fly.", ok=False)
            return None

        sim_kwargs = {
            'thrust_curve_path': self.thrust_curve_path,
            'chute_height': chute_height,
            'chute_size': chute_size,
            'time_step': time_step,
            'chute_deploy_start': chute_deploy_time,
            'chute_cd': chute_cd,
            'propellant_mass': prop_m,
        }
        try:
            rows, summary = self.run_flight_simulation(
                m, Cd, A, rho, sim_kwargs, time_step,
                body_diameter=body_diameter,
                site_override=self._stability_launch_site(),
                **self._stability_geometry_overrides())
        except Exception:
            traceback.print_exc()
            self._set_stability_note(
                "The flight model failed - see the console.", ok=False)
            return None

        if not isinstance(rows, list) or not rows:
            self._set_stability_note(
                "The flight model returned nothing to test.", ok=False)
            return None

        self._stability_results = rows
        self._stability_summary = summary
        self._build_playback(rows)
        self._stability_envelope = self.stability_envelope(rows)
        self._report_stability_envelope()
        self.enable_force_scrubber()
        return rows

    def _on_stability_override(self, checked):
        """Switch the stability figures between tracking and typed."""
        for field in (self.rocket_length_input, self.center_of_mass_input,
                      self.center_of_pressure_input):
            field.set_editable(checked)
        if not checked:
            # Going back to tracking re-reads the rocket, so nothing the user
            # typed is left behind masquerading as the vehicle's own numbers.
            self.sync_stability_from_vehicle(force=True)
        else:
            self.stability_source_label.setText(
                "Overridden - these are your numbers, not the rocket's.")
        self.refresh_stability_margin()

    def sync_stability_from_vehicle(self, force=False):
        """Pull length, CG and CP off the loaded rocket.

        Does nothing while the override is ticked, unless forced - that is how
        turning the override back off restores the vehicle's own figures.

        The centre of gravity taken here is the LOADED one, with propellant
        aboard, because that is the margin the rocket leaves the rail with and
        the one this page's pad reading is about. It migrates during the burn,
        and the flight itself reports that; this is the starting point.
        """
        if getattr(self, 'stability_override', None) is None:
            return
        if self.stability_override.isChecked() and not force:
            return
        try:
            airframe = self.vehicle_tab.airframe()
            mass_props = self.vehicle_tab.mass_properties()
        except Exception:
            self.stability_source_label.setText(
                "No rocket configured yet - tick Override to enter these by hand.")
            return

        length = airframe.total_length or 0.0
        if length <= 0:
            self.stability_source_label.setText(
                "The loaded rocket has no length set - tick Override to enter "
                "these by hand.")
            return
        cg = mass_props.cg(max(0.0, mass_props.propellant_mass_kg))
        try:
            cp = airframe.center_of_pressure(0.3)
        except Exception:
            cp = 0.0

        self.rocket_length_input.set_si_value(length)
        self.center_of_mass_input.set_si_value(cg)
        self.center_of_pressure_input.set_si_value(cp)
        diameter = airframe.body_diameter_m or 0.0
        if diameter > 0:
            self.stability_source_label.setText(
                f"From the loaded rocket: {length:.3f} m long, "
                f"{diameter * 1000:.0f} mm across, loaded CG at {cg:.3f} m, "
                f"CP at {cp:.3f} m (subsonic).")
        else:
            self.stability_source_label.setText("From the loaded rocket.")
        self.refresh_stability_margin()

    def refresh_stability_margin(self):
        """Re-state the pad margin, or the flight verdict if one exists."""
        if getattr(self, '_stability_envelope', None):
            self._report_stability_envelope()
            return
        if not hasattr(self, 'stability_status_label'):
            return
        cp = self.center_of_pressure_input.value()
        cg = self.center_of_mass_input.value()
        margin_m = cp - cg
        try:
            diameter = self.vehicle_tab.airframe().body_diameter_m or 0.0
        except Exception:
            diameter = 0.0
        if diameter > 0:
            cal = margin_m / diameter
            ok = self.STABLE_MIN_CAL <= cal <= self.STABLE_MAX_CAL
            text = (f"On the pad: {cal:.2f} cal ({margin_m:.3f} m) - "
                    f"press Launch to test it through the burn")
        else:
            ok = margin_m > 0.05
            text = (f"On the pad: {margin_m:.3f} m - press Launch to test it "
                    f"through the burn")
        self._set_stability_note(text, ok=ok)

    # Frames the playback is allowed, and how they are shared between the
    # climb and the descent. A Goddard flight is 200 s long; at one frame per
    # 0.05 s that is 4,000 frames, and about six minutes of watching, nearly
    # all of it a parachute drifting down. Budgeting frames instead keeps
    # every flight roughly the same length to watch, whether it is a 30 s
    # sport flight or a 40-minute descent from 30 km.
    PLAYBACK_FRAMES = 420
    PLAYBACK_ASCENT_SHARE = 0.62      # most of them on the part that matters

    def _build_playback(self, rows):
        """The flight times to show, one per frame.

        Dense through the boost and coast, where the attitude and the forces
        are actually changing, and coarse under the canopy, where they are
        not. The animation steps through this list rather than advancing a
        fixed slice of flight time, so a long descent costs a few frames
        instead of thousands.
        """
        if not rows:
            self._playback_times = []
            return
        end = rows[-1].get('time') or 0.0
        apogee_i = max(range(len(rows)),
                       key=lambda i: rows[i].get('altitude') or 0.0)
        t_apogee = rows[apogee_i].get('time') or 0.0

        n_up = max(2, int(self.PLAYBACK_FRAMES * self.PLAYBACK_ASCENT_SHARE))
        n_down = max(2, self.PLAYBACK_FRAMES - n_up)
        times = [t_apogee * i / (n_up - 1) for i in range(n_up)]
        if end > t_apogee:
            times += [t_apogee + (end - t_apogee) * (i + 1) / n_down
                      for i in range(n_down)]
        self._playback_times = times
        self._playback_index = 0
        # Cached once per run instead of rescanned every frame.
        self._playback_apogee = (rows[apogee_i].get('downrange') or 0.0,
                                 rows[apogee_i].get('altitude') or 0.0)
        self._playback_apogee_t = t_apogee
        self._playback_max_thrust = max(
            (r.get('thrust') or 0.0) for r in rows) or 1.0

    def _stability_geometry_overrides(self):
        """CG and CP for the flight, when they have been set by hand here.

        With the override off this is empty and the rocket's own geometry
        flies, which is the normal case. With it on, the typed figures are
        what fly - otherwise ticking Override would change the number printed
        beside the diagram while the rocket carried on flying its real CG,
        which is worse than not offering the override at all.
        """
        if not getattr(self, 'stability_override', None):
            return {}
        if not self.stability_override.isChecked():
            return {}
        return {'cg_override': self.center_of_mass_input.value(),
                'cp_override': self.center_of_pressure_input.value()}

    def _stability_launch_site(self):
        """The launch site the stability test flies.

        This used to copy the Stability Test tab's wind and rail angle over
        the launch site, because those boxes were a separate set of values
        that otherwise changed nothing. They are now VIEWS of the launch site
        rather than copies of it, so there is nothing left to override - the
        site already carries whatever was typed on either tab.
        """
        try:
            return self.vehicle_tab.launch_site()
        except Exception:
            return None

    def stability_envelope(self, rows):
        """How the static margin behaved across the ascent.

        Only the part of the flight where the fins can actually do anything
        is judged: from the moment there is enough airspeed for them to bite,
        up to apogee. Below that speed the margin is a number with no force
        behind it, and on the way down under a parachute it means nothing at
        all - counting either would fail rockets that are perfectly sound.
        """
        judged = []
        seen_flying = False
        for row in rows:
            margin = row.get('stability_cal')
            speed = abs(row.get('airspeed') or row.get('velocity') or 0.0)
            if (row.get('velocity') or 0.0) <= 0 and seen_flying:
                break                      # past apogee
            if speed >= 15.0:              # fins have authority from here
                seen_flying = True
            if seen_flying and margin is not None:
                judged.append((row.get('time') or 0.0, margin))
        if not judged:
            return None

        worst_t, worst = min(judged, key=lambda p: p[1])
        best_t, best = max(judged, key=lambda p: p[1])
        return {
            'min_cal': worst, 'min_time': worst_t,
            'max_cal': best, 'max_time': best_t,
            'start_cal': judged[0][1], 'end_cal': judged[-1][1],
            'under': worst < self.STABLE_MIN_CAL,
            'over': best > self.STABLE_MAX_CAL,
        }

    def _report_stability_envelope(self):
        """Put the whole-flight verdict on the tab, not a one-instant number."""
        env = getattr(self, '_stability_envelope', None)
        if not env:
            self._set_stability_note(
                "Flew, but the model reported no static margin to judge.",
                ok=False)
            return
        if env['under']:
            text = (f"UNSTABLE at T+{env['min_time']:.1f} s - margin falls to "
                    f"{env['min_cal']:.2f} cal (needs {self.STABLE_MIN_CAL:.1f})")
            ok = False
        elif env['over']:
            text = (f"Over-stable - {env['max_cal']:.2f} cal at "
                    f"T+{env['max_time']:.1f} s will weathercock into wind")
            ok = False
        else:
            text = (f"Stable all the way up - margin stays between "
                    f"{env['min_cal']:.2f} and {env['max_cal']:.2f} cal")
            ok = True
        self._set_stability_note(text, ok=ok)

    def _set_stability_note(self, text, ok=True):
        if not hasattr(self, 'stability_status_label'):
            return
        colour = "#2E8B57" if ok else "#E94F37"
        self.stability_status_label.setText(text)
        self.stability_status_label.setStyleSheet(
            f"font-size:13px;font-weight:bold;color:{colour};")

    def _stability_row_at(self, t):
        """The flown state at time t, for the animation and the vectors."""
        rows = self.stability_rows()
        if not rows:
            return None
        return min(rows, key=lambda r: abs((r.get('time') or 0.0) - t))

    def start_launch_animation(self):
        """Start the rocket launch animation"""
        if self.is_launching:
            return
            
        # Reset physics variables for new launch
        self.launch_velocity = 0.0
        self.launch_altitude = 0.0
        self.launch_x_pos = 0.0
        self.launch_x_vel = 0.0
        # Angular state for full rotation - start with launch guide angle
        launch_guide_angle_deg = self.launch_angle_input.value()
        self.launch_angle = math.radians(launch_guide_angle_deg)  # Convert to radians
        self.launch_angular_velocity = 0.0   # radians/sec
        # Parachute and apogee state
        self.chute_deployed = False
        self.chute_open_factor = 0.0  # 0..1 gradually opens when deployed
        self.apogee_marked = False
        self.apogee_pos = None
        self.apogee_flash_frames = 0
        self.launch_prev_velocity = 0.0
        self.position_history = []  # Reset trail
        self.prev_acceleration = 0.0  # Reset acceleration smoothing
        
        # Reset camera smoothing
        self.smooth_center_x = 0.0
        self.smooth_center_y = 1.5
        self.smooth_zoom = 1.0
        self.smooth_flame_intensity = 0.0
        # Fly it first. Everything this tab shows - the animation, the force
        # vectors, the CG and CP marks, the verdict - is read back out of this
        # one run, so if it cannot fly there is nothing honest to animate.
        #
        # (The mass used to be fetched here by unpacking twelve values from a
        # thirteen-value call inside a bare except, so it raised every single
        # time and silently animated a hardcoded 5 kg rocket no matter what
        # was entered. The flight now carries the mass, and its own migrating
        # CG with it.)
        if self.run_stability_test() is None:
            return
        self.launch_mass = (self._stability_results[0].get('mass')
                            or 0.0) or 5.0

        self.is_launching = True
        self.launch_time = 0.0
        self.launch_button.setText('Launching...')
        self.launch_button.setEnabled(False)
        
        # Pause idle force updates during launch for efficiency
        if hasattr(self, 'force_update_timer'):
            self.force_update_timer.stop()
        
        # 40 ms, not 33. A frame of this panel plus the force diagram costs
        # more than 33 ms on ordinary hardware, so asking for 30 fps only
        # built a backlog. With the playback schedule above the whole flight
        # still plays in about twenty seconds.
        self.launch_timer.start(40)

    def stop_launch_animation(self):
        """Stop the rocket launch animation and reset UI state."""
        try:
            if hasattr(self, 'launch_timer'):
                self.launch_timer.stop()
            self.is_launching = False
            self.launch_button.setText('Launch!')
            self.launch_button.setEnabled(True)
            
            # Resume idle force updates
            if hasattr(self, 'force_update_timer'):
                self.force_update_timer.start(150)
            
            # Optional: clear trail and reset camera smoothing to defaults
            self.position_history = []
            self.smooth_center_x = 0.0
            self.smooth_center_y = 1.5
            self.smooth_zoom = 1.0
            self.smooth_flame_intensity = 0.0
        except Exception:
            pass

    def _ensure_animation_state(self):
        """Seed the drawing's own state so a frame can be drawn at any time.

        The integration this replaced seeded these on its first pass. Without
        it, anything that draws a frame without going through the Launch
        button - a theme change, a redraw, a test - dies on a missing
        attribute part way through the drawing, with half a figure on screen.
        """
        defaults = {
            'launch_velocity': 0.0, 'launch_altitude': 0.0,
            'launch_x_pos': 0.0, 'launch_x_vel': 0.0,
            'launch_mass': 0.0, 'launch_angle': 0.0,
            'launch_angular_velocity': 0.0, 'launch_prev_velocity': 0.0,
            'prev_acceleration': 0.0,
            'chute_deployed': False, 'chute_open_factor': 0.0,
            'apogee_marked': False, 'apogee_pos': None,
            'apogee_flash_frames': 0,
            'position_history': None,
            'smooth_center_x': 0.0, 'smooth_center_y': 1.5,
            'smooth_zoom': 1.0, 'smooth_flame_intensity': 0.0,
        }
        for name, value in defaults.items():
            if not hasattr(self, name):
                setattr(self, name, [] if value is None and
                        name == 'position_history' else value)

    def update_launch_frame(self):
        """Update each frame of the launch animation using real simulation parameters.

        Drops frames rather than queueing them. A frame costs more to draw
        than the timer's interval, so without this guard Qt keeps firing while
        the previous frame is still rendering, the event queue backs up, and
        the whole window - not just this panel - stops responding. That
        backlog is what the lag actually was. Skipping a frame we are too slow
        to draw keeps the UI answering and only costs smoothness.
        """
        if getattr(self, '_frame_busy', False):
            return
        self._frame_busy = True
        try:
            self._update_launch_frame_inner()
        finally:
            self._frame_busy = False

    def _update_launch_frame_inner(self):
        ax = self.launch_fig.gca()
        ax.clear()
        
        # Play back the stability test's own flight.
        #
        # This used to integrate its own physics beside the real model: a
        # constant mass, a constant air density, gravity pinned at 9.81, wind
        # drift as "wind_speed * 0.01  # Scale for demo", and a restoring
        # torque built from an arbitrary stiffness over a "crude rotational
        # inertia proxy". It decided stable or unstable ONCE, from two spin
        # boxes, and held that for the whole flight.
        #
        # That is precisely the thing a stability test must not do. The
        # centre of gravity moves as propellant leaves, so a rocket can start
        # the burn with plenty of margin and end it without any. The real
        # model already tracks the moving CG, the CP against Mach, the
        # atmosphere and the wind, so the animation now shows THAT flight
        # instead of a second, worse one that could disagree with it.
        self._ensure_animation_state()
        # The "run a simulation" placeholder is a FIGURE-level text, so
        # ax.clear() never touched it and it sat behind every frame of the
        # animation forever. Clear it once there is a real frame to draw.
        if self.launch_fig.texts:
            self.launch_fig.texts.clear()
        rows = self.stability_rows()
        t = self.launch_time
        wind_speed = self.wind_speed_input.value()

        if not rows:
            ax.text(0.5, 0.5, "Press Launch to fly the stability test",
                    transform=ax.transAxes, ha='center', va='center',
                    fontsize=12, color='#7A7A86')
            if hasattr(self, 'launch_canvas'):
                self.launch_canvas.draw_idle()
            return

        row = self._stability_row_at(t)
        # Scanned once when the flight was built, not once per frame.
        max_thrust = getattr(self, '_playback_max_thrust', None)
        if not max_thrust:
            max_thrust = max((r.get('thrust') or 0.0) for r in rows) or 1.0
        current_thrust = max(0.0, row.get('thrust') or 0.0)

        self.launch_altitude = max(0.0, row.get('altitude') or 0.0)
        self.launch_x_pos = row.get('downrange') or 0.0
        self.launch_velocity = row.get('velocity') or 0.0
        self.launch_x_vel = row.get('horizontal_velocity') or 0.0
        self.launch_mass = row.get('mass') or self.launch_mass
        # The model reports tilt in degrees from vertical; the drawing wants
        # radians, same sign convention.
        self.launch_angle = math.radians(row.get('angle_from_vertical_deg') or 0.0)
        self.chute_deployed = bool(row.get('chute_deployed'))
        self.chute_open_factor = float(row.get('chute_fill') or
                                       (1.0 if self.chute_deployed else 0.0))
        # Speed through the air, for the drag arrow and the canopy's heading.
        # It used to leak out of the integration loop into the drawing below;
        # with the loop gone it has to be stated.
        v_total = math.hypot(self.launch_x_vel, self.launch_velocity)

        # Stability is now a property of this instant of the flight, read off
        # the margin the model computed for it, not a verdict fixed before
        # the rocket left the pad.
        margin_cal = row.get('stability_cal')
        if margin_cal is None:
            stable = True
        else:
            stable = self.STABLE_MIN_CAL <= margin_cal <= self.STABLE_MAX_CAL
        color = '#2E8B57' if stable else '#E94F37'

        # Apogee, taken from the flight rather than watched for frame by frame.
        if not getattr(self, 'apogee_marked', False):
            # Located once per run; this used to rescan every row every frame.
            t_apogee = getattr(self, '_playback_apogee_t', None)
            if t_apogee is None:
                peak = max(range(len(rows)),
                           key=lambda i: rows[i].get('altitude') or 0.0)
                t_apogee = rows[peak].get('time') or 0.0
                self._playback_apogee = (rows[peak].get('downrange') or 0.0,
                                         rows[peak].get('altitude') or 0.0)
            if t_apogee <= t:
                self.apogee_marked = True
                self.apogee_pos = getattr(self, '_playback_apogee', (0.0, 0.0))
                self.apogee_flash_frames = 30

        x_pos = self.launch_x_pos
        y_pos = max(0.0, self.launch_altitude)

        # The wind arrow used to be drawn from a "scale for demo" constant.
        # The flight carries the real wind at this altitude, so draw that.
        wind_here = row.get('wind_speed')
        if wind_here is None:
            wind_here = wind_speed
        wind_arrow_dx = math.copysign(
            min(2.0, 0.15 * abs(wind_here)), wind_here or 1.0)
        
        # Calculate camera following parameters with smoothing
        # Smooth camera movement using exponential moving average
        if not hasattr(self, 'smooth_center_x'):
            self.smooth_center_x = x_pos
            self.smooth_center_y = y_pos
            self.smooth_zoom = 1.0

        # Camera smoothing parameters
        camera_smoothing = 0.60  # Lower = smoother, higher = more responsive
        zoom_smoothing = 0.8    # Much faster zoom response for a snappier feel

        # Smooth camera position
        # Follow vertically; horizontally: partial follow when unstable so drift is visible but stays in frame
        if not stable:
            # Widen camera follow for unstable rockets
            target_center_x = 0.8 * x_pos
            follow_alpha_x = 0.2
        else:
            target_center_x = x_pos
            follow_alpha_x = camera_smoothing
        self.smooth_center_x += (target_center_x - self.smooth_center_x) * follow_alpha_x
        target_center_y = max(1.5, y_pos)
        self.smooth_center_y += (target_center_y - self.smooth_center_y) * camera_smoothing

        # Smooth zoom with altitude and velocity; keep rocket big on ascent
        base_zoom = max(1.0, y_pos * 0.25 + abs(self.launch_velocity) * 0.02)
    # Remove all zoom capping: camera always zooms out as much as needed
        # Keep rocket visually large: blend between min zoom and physics zoom
        min_visual_zoom = 1.2
        if self.launch_velocity > 0:
            # On ascent, blend so rocket stays big
            visual_alpha = max(0.0, min(1.0, y_pos / 30.0))  # 0 at pad, 1 at 30m+
            target_zoom = min_visual_zoom * (1 - visual_alpha) + base_zoom * visual_alpha
        else:
            target_zoom = base_zoom
        self.smooth_zoom += (target_zoom - self.smooth_zoom) * zoom_smoothing

        # Apply smoothed values
        center_x = self.smooth_center_x
        center_y = self.smooth_center_y
        # Make x zoom much slower for readability
        x_zoom_factor = 0.35  # Lower = slower expansion, more readable
        view_width = 2 * (self.smooth_zoom * x_zoom_factor + self.smooth_zoom * (1 - x_zoom_factor))
        view_height = 2 * self.smooth_zoom
        
        # Draw trajectory trail using stored positions
        if not hasattr(self, 'position_history'):
            self.position_history = []
        
        # Store current position
        self.position_history.append((x_pos, y_pos))
        
        # Keep only last 20 positions for trail
        if len(self.position_history) > 20:
            self.position_history = self.position_history[-20:]
        
        # The trail as ONE line, not one per segment. Nineteen separate
        # ax.plot calls meant nineteen Line2D artists created, styled and
        # drawn every frame for a twenty-point trail. The fade is gone with
        # them; a single translucent line reads the same at this size.
        if len(self.position_history) > 1:
            trail_x = [p[0] for p in self.position_history]
            trail_y = [p[1] for p in self.position_history]
            ax.plot(trail_x, trail_y, color=color, alpha=0.55, linewidth=2,
                    solid_capstyle='round')
        
        # Draw current rocket position (rotated polygon) and flame aligned with body
        if y_pos > 0:
            # Rocket size, scaled to the view rather than fixed in metres.
            # It was a flat 0.6 m, while the camera zooms out with altitude -
            # so the rocket shrank to nothing the moment it climbed, and the
            # tracker showed an empty sky. Tying it to the view keeps it the
            # same size on screen the whole way up. Visual only; it is a
            # marker for where the rocket is, not a scale drawing.
            L_draw = max(0.6, view_height * 0.075)
            W_draw = L_draw * 0.3
            # Body frame vertices: (x_right, y_forward)
            nose = (0.0, L_draw/2)
            left_tail = (-W_draw/2, -L_draw/2)
            right_tail = (W_draw/2, -L_draw/2)
            # Forward along body axis and right perpendicular
            u_forward = (math.sin(self.launch_angle), math.cos(self.launch_angle))
            u_right = (math.cos(self.launch_angle), -math.sin(self.launch_angle))
            def to_world(pt):
                x_r, y_f = pt
                return (x_pos + x_r * u_right[0] + y_f * u_forward[0],
                        y_pos + x_r * u_right[1] + y_f * u_forward[1])
            verts = [to_world(nose), to_world(right_tail), to_world(left_tail)]
            rocket_poly = mpatches.Polygon(verts, closed=True, facecolor=color, edgecolor='black', linewidth=2, zorder=6)
            ax.add_patch(rocket_poly)

            # Add thrust flame based on actual thrust with smoothing
            if not hasattr(self, 'smooth_flame_intensity'):
                self.smooth_flame_intensity = 0.0
            target_flame_intensity = current_thrust / max_thrust if max_thrust > 0 else 0
            # Smooth flame intensity changes
            flame_smoothing = 0.2
            self.smooth_flame_intensity += (target_flame_intensity - self.smooth_flame_intensity) * flame_smoothing
            flame_length = self.smooth_flame_intensity * 0.5  # Scale flame length
            if flame_length > 0.02:  # Only show flame if significant thrust
                # Add slight flame flicker for realism
                flicker = 1.0 + 0.1 * math.sin(t * 25) * self.smooth_flame_intensity
                actual_flame_length = flame_length * flicker
                # Tail center in world coords (0, -L/2 in body frame)
                tail_world = to_world((0.0, -L_draw/2))
                # Flame direction opposite forward
                flame_dir = (-u_forward[0], -u_forward[1])
                end1 = (tail_world[0] + flame_dir[0] * actual_flame_length,
                        tail_world[1] + flame_dir[1] * actual_flame_length)
                end2 = (tail_world[0] + flame_dir[0] * actual_flame_length * 0.7,
                        tail_world[1] + flame_dir[1] * actual_flame_length * 0.7)
                ax.plot([tail_world[0], end1[0]], [tail_world[1], end1[1]],
                        color='orange', linewidth=max(1, int(8 * self.smooth_flame_intensity)), alpha=0.8)
                ax.plot([tail_world[0], end2[0]], [tail_world[1], end2[1]],
                        color='yellow', linewidth=max(1, int(4 * self.smooth_flame_intensity)), alpha=0.9)

            # Draw parachute if deployed
            if self.chute_deployed and self.chute_open_factor > 0.05:
                # Direction opposite velocity
                if v_total < 1e-3:
                    para_dir = (0.0, 1.0)
                else:
                    para_dir = (-self.launch_x_vel / v_total, -self.launch_velocity / v_total)
                # Canopy center a bit behind rocket along para_dir
                canopy_offset = 1.1 * L_draw
                canopy_center = (x_pos + para_dir[0] * canopy_offset,
                                 y_pos + para_dir[1] * canopy_offset)
                canopy_radius = L_draw * 0.6 * self.chute_open_factor
                canopy = mpatches.Circle(canopy_center, canopy_radius, facecolor='#A7C7E7', edgecolor='#3C2F1E', linewidth=2, alpha=0.85, zorder=5)
                ax.add_patch(canopy)
                # Lines (shrouds) to tail
                tail_anchor = to_world((0.0, -L_draw/2))
                ax.plot([tail_anchor[0], canopy_center[0]], [tail_anchor[1], canopy_center[1]], color='#3C2F1E', linewidth=1, alpha=0.8, zorder=5)

            # FBD arrows (thrust, drag, gravity) near rocket
            # Thrust
            arrow_unit = max(0.4, view_height * 0.05)
            t_scale = arrow_unit * (current_thrust / max_thrust) if max_thrust > 0 else 0
            ax.arrow(x_pos, y_pos, u_forward[0]*t_scale, u_forward[1]*t_scale, head_width=arrow_unit*0.15, head_length=arrow_unit*0.2, fc='green', ec='green', alpha=0.8, zorder=7)
            # Drag opposite velocity
            if v_total > 1e-3:
                d_dir = (-self.launch_x_vel / v_total, -self.launch_velocity / v_total)
                d_scale = arrow_unit * min(1.0, v_total / 50.0)
                ax.arrow(x_pos, y_pos, d_dir[0]*d_scale, d_dir[1]*d_scale, head_width=arrow_unit*0.15, head_length=arrow_unit*0.2, fc='red', ec='red', alpha=0.8, zorder=7)
            # Gravity
            g_len = arrow_unit * 0.75
            ax.arrow(x_pos, y_pos, 0, -g_len, head_width=arrow_unit*0.15, head_length=arrow_unit*0.2, fc='blue', ec='blue', alpha=0.8, zorder=7)

        # Apogee flash marker
        if self.apogee_flash_frames and self.apogee_pos:
            fx, fy = self.apogee_pos
            flash_alpha = max(0.0, self.apogee_flash_frames / 30.0)
            ax.scatter([fx], [fy], s=180, c='#FFD447', edgecolors='#E94F37', linewidths=2, alpha=flash_alpha, zorder=8, marker='*')
            self.apogee_flash_frames = max(0, self.apogee_flash_frames - 1)
        
        # Draw wind arrow
        if wind_speed > 0:
            ax.arrow(-0.5, y_pos + 0.2, wind_arrow_dx, 0, 
                    head_width=0.15, head_length=0.15, 
                    fc='#4682B4', ec='#4682B4', linewidth=3, alpha=0.7)
        
        # Draw ground line and launch pad
        ground_x = [center_x - view_width, center_x + view_width]
        ground_y = [0, 0]
        ax.plot(ground_x, ground_y, color='#8B4513', linewidth=4, label='Ground')
        
        # Launch pad at origin
        ax.plot([0], [0], 's', color='gray', markersize=15, label='Launch Pad')
        
        # Styling with dynamic camera following
        ax.set_xlim(center_x - view_width, center_x + view_width)
        ax.set_ylim(center_y - view_height*0.3, center_y + view_height*0.7)
        ax.set_title(f'Rocket Launch - T+{t:.1f}s - Alt: {y_pos:.1f}m', fontsize=14, fontweight='bold')
        ax.set_xlabel('Drift (m)')
        ax.set_ylabel('Altitude (m)')
        # Cap the tick count. The camera changes the limits every frame, so
        # matplotlib rebuilds the whole tick set every frame - it was about a
        # third of the cost of drawing one, and the default locator can ask
        # for a dozen ticks per axis at these ranges. Five a side reads the
        # same on a panel this size for a fraction of the work.
        ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=4))
        ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=4))
        # Theme-aware styling
        self.style_axes(ax)
        
        # Add status text.
        #
        # Too much margin and too little are different failures and must not
        # carry the same word: a rocket at 4.6 calibers is not going to tumble,
        # it is going to turn hard into the wind and lose altitude doing it.
        # Calling that "UNSTABLE!" sends you adding nose weight, which is the
        # wrong way.
        if not stable and y_pos > 0 and margin_cal is not None:
            if margin_cal < self.STABLE_MIN_CAL:
                banner, face = f'UNSTABLE  {margin_cal:.1f} cal', 'yellow'
            else:
                banner, face = f'OVER-STABLE  {margin_cal:.1f} cal', '#FFD47F'
            ax.text(x_pos, y_pos + L_draw * 0.9, banner, ha='center', va='bottom',
                   fontsize=11, color='#7A1B1B', fontweight='bold',
                   bbox=dict(boxstyle='round', facecolor=face, alpha=0.85))
        
        if y_pos <= 0 and t > 1:
            ax.text(x_pos, view_height * 0.05, 'IMPACT!', ha='center', va='center', 
                   fontsize=14, color='red', fontweight='bold',
                   bbox=dict(boxstyle='round', facecolor='orange', alpha=0.9))
        
        self.launch_canvas.draw()
        
        # Update time
        # Advance along the playback schedule. Under the canopy that is a
        # large slice of flight time per frame; through the boost it is a
        # small one.
        times = getattr(self, '_playback_times', None)
        if times:
            self._playback_index = getattr(self, '_playback_index', 0) + 1
            if self._playback_index < len(times):
                self.launch_time = times[self._playback_index]
            else:
                self.launch_time = times[-1] + 1.0    # past the end, stop below
        else:
            self.launch_time += 0.05

        # The force diagram is half the cost of a frame and its arrows change
        # far more slowly than 30 times a second, so it is redrawn every few
        # frames rather than every one. The scrubber and the end of playback
        # still redraw it immediately, so it is never left stale where anyone
        # is looking at it.
        self._force_frame_skip = getattr(self, '_force_frame_skip', 0) + 1
        if self._force_frame_skip >= 3:
            self._force_frame_skip = 0
            try:
                self.update_force_diagram(self.launch_time)
            except Exception:
                pass
        
        # The playback ends when the flight does. The old cut-offs - ground
        # contact, or a 60 s safety stop - belonged to a model that made its
        # own trajectory up; this one runs to the end of the flown rows, so a
        # descent under canopy that really takes three minutes is not cut off
        # at one.
        flight_end = (rows[-1].get('time') or 0.0) if rows else 0.0
        if self.launch_time > flight_end:
            # Land the diagram on the last real moment of the flight rather
            # than wherever the throttle happened to leave it.
            try:
                self.update_force_diagram(flight_end)
            except Exception:
                pass
            self.launch_timer.stop()
            self.is_launching = False
            self.launch_button.setText('Launch Again!')
            self.launch_button.setEnabled(True)
            
            # Resume idle force updates
            if hasattr(self, 'force_update_timer'):
                self.force_update_timer.start(150)

    def set_fbd_anim_speed(self):
        # Animation removed
        return

    def _sync_unit_indices(self):
        """Record the current unit selections without converting anything.

        update_conversions decides whether to convert by comparing against the
        last index it saw; after a programmatic load those have to be brought
        up to date, or the next genuine user change converts from a unit that
        was never actually displayed.
        """
        if not hasattr(self, '_last_unit_indices'):
            self._last_unit_indices = {}
        for field, combo in (
            ('mass', getattr(self, 'mass_unit', None)),
            ('area', getattr(self, 'area_unit', None)),
            ('rho', getattr(self, 'rho_unit', None)),
            ('timestep', getattr(self, 'timestep_unit', None)),
            ('fin_thickness', getattr(self, 'fin_thickness_unit', None)),
            ('fin_length', getattr(self, 'fin_length_unit', None)),
            ('body_diameter', getattr(self, 'body_diameter_unit', None)),
            ('chute_height', getattr(self, 'chute_height_unit', None)),
            ('chute_size', getattr(self, 'chute_size_unit', None)),
        ):
            if combo is not None:
                self._last_unit_indices[field] = combo.currentIndex()

    # Simulation-tab unit selectors. These predate the units module and keep
    # their own combos (a profile stores the selected INDEX, so the lists and
    # their order are part of the saved format), but the conversion factors
    # now come from the one table in units.py rather than being retyped here -
    # the old inline copy had lb at 0.453592 and lb/ft3 at 16.0185, both
    # rounded, so a value round-tripped through them drifted.
    #
    # field -> (input attr, combo attr, dimension, [(combo text, unit symbol)],
    #           metric preference, imperial preference)
    _SIM_UNITS = {
        'mass': ('mass_input', 'mass_unit', 'mass',
                 [('kg', 'kg'), ('g', 'g'), ('lb', 'lb')], 'kg', 'lb'),
        'prop_mass': ('prop_mass_input', 'prop_mass_unit', 'mass',
                      [('kg', 'kg'), ('g', 'g'), ('lb', 'lb')], 'kg', 'lb'),
        'area': ('area_input', 'area_unit', 'area',
                 [('m\u00b2', 'm2'), ('cm\u00b2', 'cm2'), ('ft\u00b2', 'ft2')],
                 'm2', 'ft2'),
        'rho': ('rho_input', 'rho_unit', 'density',
                [('kg/m\u00b3', 'kg/m3'), ('g/cm\u00b3', 'g/cm3'),
                 ('lb/ft\u00b3', 'lb/ft3')], 'kg/m3', 'lb/ft3'),
        'timestep': ('timestep_input', 'timestep_unit', 'time',
                     [('s', 's'), ('ms', 'ms')], 's', 's'),
        'fin_thickness': ('fin_thickness_input', 'fin_thickness_unit', 'length',
                          [('m', 'm'), ('mm', 'mm'), ('in', 'in')], 'mm', 'in'),
        'fin_length': ('fin_length_input', 'fin_length_unit', 'length',
                       [('m', 'm'), ('mm', 'mm'), ('in', 'in')], 'mm', 'in'),
        'body_diameter': ('body_diameter_input', 'body_diameter_unit', 'length',
                          [('m', 'm'), ('mm', 'mm'), ('in', 'in')], 'mm', 'in'),
        'chute_height': ('chute_height_input', 'chute_height_unit', 'length',
                         [('m', 'm'), ('ft', 'ft')], 'm', 'ft'),
        'chute_size': ('chute_size_input', 'chute_size_unit', 'area',
                       [('m\u00b2', 'm2'), ('ft\u00b2', 'ft2')], 'm2', 'ft2'),
    }

    def _sim_unit_defs(self):
        """(input, combo, labels, factors) per field, factors from units.py."""
        out = {}
        for field, (inp, combo, dimension, pairs, _m, _i) in self._SIM_UNITS.items():
            widget, selector = getattr(self, inp, None), getattr(self, combo, None)
            if widget is None or selector is None:
                continue
            dim = app_units.DIMENSIONS[dimension]
            out[field] = (widget, selector, [p[0] for p in pairs],
                          [dim.unit(p[1]).factor for p in pairs])
        return out

    def _apply_sim_unit_system(self, system):
        """Point the Simulation tab's combos at the chosen system's units.

        The conversion is done here rather than left to each combo's own
        signal. Relying on the signal made the result depend on whether that
        particular combo happened to be connected and on what the
        previous-index bookkeeping held - propellant mass, whose combo was
        never connected at all, changed its unit without converting its
        number. Converting explicitly makes every field behave the same.
        """
        for field, (inp, combo, dimension, pairs, metric, imperial) in \
                self._SIM_UNITS.items():
            widget, selector = getattr(self, inp, None), getattr(self, combo, None)
            if widget is None or selector is None:
                continue
            want = imperial if system == app_units.IMPERIAL else metric
            target = next((i for i, (_t, sym) in enumerate(pairs)
                           if sym == want), None)
            current = selector.currentIndex()
            if target is None or target == current:
                continue
            dim = app_units.DIMENSIONS[dimension]
            try:
                value = float(widget.text().replace(",", "").strip())
            except ValueError:
                value = None
            selector.blockSignals(True)
            selector.setCurrentIndex(target)
            selector.blockSignals(False)
            if value is not None:
                si = dim.unit(pairs[current][1]).to_si(value)
                widget.setText(f"{dim.unit(pairs[target][1]).from_si(si):.12g}")
            self._last_unit_indices[field] = target

    def update_conversions(self, field):
        # Only convert value if the user changes the unit, not on load.
        #
        # Loading a profile sets the unit combo too, and this slot then
        # "converted" a number that was already stored in the new unit. On the
        # shipped L550 profile that turned 76 mm into 1.93 mm, 4 mm fin
        # thickness into 101.6 mm and a 0.0045 m2 area into 0.000003 m2, and
        # the corrupted geometry flowed straight into the Flight Report.
        if getattr(self, '_loading_profile', False):
            self._sync_unit_indices()
            return
        if not hasattr(self, '_last_unit_indices'):
            self._last_unit_indices = {}
        unit_defs = self._sim_unit_defs()
        if field in unit_defs:
            input_widget, unit_widget, units, factors = unit_defs[field]
            idx = unit_widget.currentIndex()
            last_idx = self._last_unit_indices.get(field, idx)
            if idx != last_idx:
                try:
                    val = float(input_widget.text())
                except Exception:
                    self._last_unit_indices[field] = idx
                    return
                # Convert from last unit to new unit
                val_base = val * factors[last_idx]
                val_new = val_base / factors[idx]
                input_widget.setText(str(val_new))
            self._last_unit_indices[field] = idx

    # Unit selection change
    # self.unit_select.currentIndexChanged.connect(self.update_units)

    def select_thrust_curve(self):
        options = QtWidgets.QFileDialog.Options()
        # Default to the bundled thrust_curves folder. bundled_dir, not a walk
        # up from __file__: in the downloadable build the curves are unpacked
        # beside the program, not two folders above this module.
        default_dir = portable_paths.bundled_dir('thrust_curves')
        if not os.path.isdir(default_dir):
            default_dir = ''
        fileName, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Thrust Curve",
            default_dir,
            "Thrust Curves (*.csv *.eng *.rasp);;CSV Files (*.csv);;RASP/ENG Files (*.eng *.rasp);;All Files (*)",
            options=options
        )
        if fileName:
            self.thrust_curve_path = fileName
            self.result_label.setText(f"Selected thrust curve: {fileName}")
            # Try to auto-fill propellant mass from the selected CSV
            try:
                self.try_set_propellant_mass_from_file(fileName)
            except Exception:
                pass

    def use_engine_lab_thrust_curve(self, csv_path, propellant_mass_kg, dry_mass_kg):
        """Callback for the Engine Lab tab: wire its generated thrust curve into
        the Simulation tab's inputs and switch to it."""
        self.thrust_curve_path = csv_path
        # Remember that this curve came from the Engine Lab, so the Flight Report
        # only attributes engine-side data to a flight actually flown on it.
        self.engine_lab_curve_path = csv_path
        self.prop_mass_unit.setCurrentIndex(0)  # kg
        self.prop_mass_input.setText(f"{propellant_mass_kg:.3f}")
        self.mass_unit.setCurrentIndex(0)  # kg
        self.mass_input.setText(f"{dry_mass_kg + propellant_mass_kg:.3f}")
        if hasattr(self, 'vehicle_tab'):
            self.vehicle_tab.set_propellant_mass(propellant_mass_kg)
        self.result_label.setText(
            f"Loaded thrust curve from Engine Lab: {os.path.basename(csv_path)}<br>"
            f"Liftoff mass set to {dry_mass_kg + propellant_mass_kg:.3f} kg "
            f"(dry {dry_mass_kg:.3f} kg + propellant {propellant_mass_kg:.3f} kg)."
        )
        self.tabs.setCurrentWidget(self.main_panel)

    def try_set_propellant_mass_from_file(self, path):
        import os, csv, re
        _, ext = os.path.splitext(path.lower())
        if ext != '.csv':
            return
        # First pass: robust scan to find header with 'thrust' and parse metadata lines above it
        with open(path, newline='') as f:
            r = csv.reader(f)
            rows = []
            for row in r:
                if not row:
                    continue
                cleaned = [(c or '').strip() for c in row]
                if any(cleaned):
                    rows.append(cleaned)
        header_idx = None
        for i, row in enumerate(rows):
            lc = [c.lower() for c in row]
            if any('thrust' in c for c in lc):
                header_idx = i
                break
        # Try to extract prop mass from lines above header
        if header_idx is not None:
            for i in range(header_idx):
                line = ','.join(rows[i]).lower()
                if ('propellant' in line) or ('prop mass' in line) or ('propellant mass' in line):
                    nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", line)
                    if nums:
                        try:
                            val = float(nums[-1])
                            if ' g' in line and 'kg' not in line:
                                val = val / 1000.0
                            if val > 0:
                                self.prop_mass_unit.setCurrentIndex(0)  # kg
                                self.prop_mass_input.setText(f"{val:.3f}")
                                return
                        except Exception:
                            pass
        # Fallback: try headered columns using DictReader and known field names
        try:
            with open(path, newline='') as f:
                sample = f.read(2048)
                f.seek(0)
                has_header = True
                try:
                    has_header = csv.Sniffer().has_header(sample)
                except Exception:
                    pass
                if not has_header:
                    return
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    return
                pm_keys_kg = ['propellant_mass','prop_mass','m_propellant','propellant','propellant_mass_kg','prop_weight_kg']
                pm_keys_g = ['propellant_mass_g','prop_mass_g','prop_g','prop_weight_g']
                row = next(reader, None)
                if not row:
                    return
                val = None
                # Normalize keys to lower
                lower_row = { (k or '').strip().lower(): v for k, v in row.items() }
                for k in pm_keys_kg:
                    if k in lower_row and lower_row[k] not in (None, ''):
                        try:
                            val = float(lower_row[k])
                            break
                        except Exception:
                            pass
                if val is None:
                    for k in pm_keys_g:
                        if k in lower_row and lower_row[k] not in (None, ''):
                            try:
                                val = float(lower_row[k]) / 1000.0
                                break
                            except Exception:
                                pass
                if val is not None and val > 0:
                    self.prop_mass_unit.setCurrentIndex(0)  # kg
                    self.prop_mass_input.setText(f"{val:.3f}")
        except Exception:
            return

    def get_value_in_base_unit(self, value, unit_idx, factors):
        try:
            return float(value) * factors[unit_idx]
        except Exception:
            return 0.0

    def _bind_shared_launch_conditions(self):
        """Make every view of the launch conditions the same value.

        The wind, the wind direction and the rail angle are set on the
        Stability Test tab, where you are setting up a launch, and on the
        Aerodynamics tab's launch site, which is what the flight model reads.
        Field elevation, temperature and humidity used to have a third home
        of their own. They are all one set of conditions, so each pair is
        bound both ways here: editing either view writes the launch site, and
        the other view follows.

        Guarded against the obvious loop - a write that came from the binding
        does not bounce back.
        """
        self._binding_shared = False

        def bind(widget, attr, to_widget=None, from_widget=None):
            signal = (widget.valueChanged if hasattr(widget, "valueChanged")
                      else widget.textChanged)

            def push(_value=None):
                if self._binding_shared:
                    return
                self._binding_shared = True
                try:
                    raw = (widget.value() if hasattr(widget, "value")
                           else widget.text())
                    value = from_widget(raw) if from_widget else raw
                    self.vehicle_tab.set_value(attr, value)
                except Exception:
                    pass
                finally:
                    self._binding_shared = False

            signal.connect(push)
            self._shared_views.append((widget, attr, to_widget))

        self._shared_views = []
        bind(self.wind_speed_input, "wind_speed_ms")
        bind(self.wind_direction_input, "wind_dir_deg")
        bind(self.launch_angle_input, "rail_angle_deg")
        bind(self.start_altitude_input, "elevation_m",
             from_widget=lambda t: float(t or 0))
        bind(self.temperature_input, "temperature_c",
             from_widget=lambda t: float(t or 0))
        bind(self.humidity_input, "humidity_pct",
             from_widget=lambda t: float(t or 0))
        # And pull the other way whenever the vehicle tab changes.
        try:
            self.vehicle_tab.changed.connect(self.refresh_shared_launch_views)
        except Exception:
            pass
        self.refresh_shared_launch_views()

    def refresh_shared_launch_views(self):
        """Push the launch site back out to the views that show it."""
        if getattr(self, "_binding_shared", False):
            return
        site = None
        try:
            site = self.vehicle_tab.launch_site()
        except Exception:
            return
        self._binding_shared = True
        try:
            for widget, attr, _to in getattr(self, "_shared_views", []):
                value = getattr(site, attr, None)
                if value is None:
                    continue
                if hasattr(widget, "setValue"):
                    lo = widget.minimum() if hasattr(widget, "minimum") else None
                    hi = widget.maximum() if hasattr(widget, "maximum") else None
                    v = float(value)
                    if lo is not None:
                        v = max(lo, min(hi, v))
                    widget.setValue(type(widget.value())(v))
                else:
                    widget.setText(f"{float(value):g}")
        except Exception:
            pass
        finally:
            self._binding_shared = False

    def _apply_motor_materials(self, mats):
        """Put a generated motor's pressure parts onto the Flight Report.

        set_values, not apply_config: apply_config reads a missing "goals"
        key as "this rocket has no goals" and would wipe the ones the user
        set, which is right for a whole profile load and wrong for dropping
        in three materials.
        """
        if not mats or not hasattr(self, 'flight_report'):
            return
        values = {}
        for part, attr, wall_attr in (
                ("tank", "tank_material", "tank_wall_m"),
                ("chamber", "chamber_material", "chamber_wall_m"),
                ("nozzle", "nozzle_material", None)):
            info = mats.get(part) or {}
            if info.get("name"):
                values[attr] = info["name"]
            if wall_attr and info.get("wall_m"):
                values[wall_attr] = float(info["wall_m"])
        if values:
            self.flight_report.set_values(values)
            self.invalidate_flight_results(
                "Motor materials changed - run a simulation to re-grade it.")

    # ---- optional extensions ---------------------------------------------
    def load_extension_enabled(self, name="tolerances", default=True):
        """Whether an optional tab is switched on, from the settings file."""
        try:
            with open(getattr(self, 'user_settings_file', None)
                      or user_settings_path(), 'r') as f:
                return bool(json.load(f).get(f"{name}_tab_enabled", default))
        except Exception:
            return default

    def set_tolerances_enabled(self, enabled):
        """Show or hide the Tolerances tab, and remember the choice.

        Removing the tab rather than disabling it: a greyed-out tab still
        invites a click and still has to explain itself. The widget is kept
        alive so switching back does not lose whatever was measured.
        """
        enabled = bool(enabled)
        tab = getattr(self, 'tolerances_tab', None)
        if tab is not None:
            index = self.tabs.indexOf(tab)
            if enabled and index < 0:
                # Back where it was: after the Flight Report, before Settings.
                before = self.tabs.indexOf(self.flight_report)
                at = before + 1 if before >= 0 else self.tabs.count()
                self.tabs.insertTab(at, tab, "Tolerances")
            elif not enabled and index >= 0:
                self.tabs.removeTab(index)
                tab.setParent(None)
        try:
            path = (getattr(self, 'user_settings_file', None)
                    or user_settings_path())
            try:
                with open(path, 'r') as f:
                    existing = json.load(f)
            except Exception:
                existing = {}
            existing["tolerances_tab_enabled"] = enabled
            with open(path, 'w') as f:
                json.dump(existing, f, indent=2)
        except Exception:
            pass

    def _tolerance_inputs(self):
        """(engine, FlightContext) for the Tolerances tab.

        Deliberately the same sources the Simulation tab flies from, so the
        baseline the tolerance search reports is the flight the rest of the
        app would give you. A margin measured against a different rocket from
        the one on screen is worse than no margin.
        """
        if not hasattr(self, 'engine_lab') or not hasattr(self, 'vehicle_tab'):
            raise RuntimeError("the Engine and Aerodynamics tabs are not ready")
        engine = self.engine_lab._read_engine()
        ctx = tolerances.FlightContext(
            airframe=self.vehicle_tab.airframe(),
            site=self.vehicle_tab.launch_site(),
            recovery=self.vehicle_tab.recovery_system(),
            mass_props=self.vehicle_tab.mass_properties(),
            vehicle=self.flight_report.vehicle_config(),
            cd_override=self.cd_source())
        return engine, ctx

    def _engine_preview_vehicle(self):
        """The loaded rocket, for the Engine tab's quick flight preview.

        Returns (dry mass kg, body Cd, body diameter m), or None before there
        is a vehicle to describe. Dry mass, not liftoff: the preview's model
        adds the propellant from the motor it just simulated, so handing it a
        wet mass would count the propellant twice.
        """
        try:
            airframe = self.vehicle_tab.airframe()
            mass_props = self.vehicle_tab.mass_properties()
        except Exception:
            return None
        dry = mass_props.effective_dry_mass()
        diameter = airframe.body_diameter_m
        if not dry or dry <= 0 or not diameter or diameter <= 0:
            return None
        # The same drag the flight model would use: an imported Cd(Mach)
        # curve sampled low-subsonic, an override, or the airframe buildup.
        cd = None
        try:
            source = self.cd_source()
            if callable(source):
                cd = float(source(0.3))
            elif source:
                cd = float(source)
        except Exception:
            cd = None
        if not cd or cd <= 0:
            # Fall back to the airframe buildup at a representative subsonic
            # point, the same function the flight model calls each step.
            try:
                site = self.vehicle_tab.launch_site()
                speed = 0.3 * site.properties(site.elevation_m)[3]
                cd, _breakdown = aero.drag_coefficient(
                    0.3, site.elevation_m, speed, airframe, site)
            except Exception:
                cd = None
        return (dry, cd, diameter)

    def get_inputs_for_simulation(self):
        # Convert all values to base units for simulation
        m = self.get_value_in_base_unit(self.mass_input.text(), self.mass_unit.currentIndex(), [1, 0.001, 0.453592])
        Cd = float(self.cd_input.text()) if self.cd_input.text() else 0.0
        A = self.get_value_in_base_unit(self.area_input.text(), self.area_unit.currentIndex(), [1, 0.0001, 0.092903])
        rho = self.get_value_in_base_unit(self.rho_input.text(), self.rho_unit.currentIndex(), [1, 1000, 16.0185])
        time_step = self.get_value_in_base_unit(self.timestep_input.text(), self.timestep_unit.currentIndex(), [1, 0.001])
        fin_thickness = self.get_value_in_base_unit(self.fin_thickness_input.text(), self.fin_thickness_unit.currentIndex(), [1, 0.001, 0.0254])
        fin_length = self.get_value_in_base_unit(self.fin_length_input.text(), self.fin_length_unit.currentIndex(), [1, 0.001, 0.0254])
        body_diameter = self.get_value_in_base_unit(self.body_diameter_input.text(), self.body_diameter_unit.currentIndex(), [1, 0.001, 0.0254])
        chute_height = self.get_value_in_base_unit(self.chute_height_input.text(), self.chute_height_unit.currentIndex(), [1, 0.3048])
        chute_size = self.get_value_in_base_unit(self.chute_size_input.text(), self.chute_size_unit.currentIndex(), [1, 0.092903])
        try:
            chute_cd = float(self.chute_cd_input.text())
        except Exception:
            chute_cd = 1.5
        # No random time-based deployment by default; we deploy based on descent and height
        chute_deploy_time = None
        # Convert propellant mass
        prop_m = self.get_value_in_base_unit(self.prop_mass_input.text(), self.prop_mass_unit.currentIndex(), [1, 0.001, 0.453592]) if self.prop_mass_input.text() else None
        return m, Cd, A, rho, time_step, fin_thickness, fin_length, body_diameter, chute_height, chute_size, chute_deploy_time, chute_cd, prop_m

    def start_simulation(self):
        try:
            self.save_inputs()
            m, Cd, A, rho, time_step, fin_thickness, fin_length, body_diameter, chute_height, chute_size, chute_deploy_time, chute_cd, prop_m = self.get_inputs_for_simulation()
            
            # Input validation
            errors = []
            if m <= 0:
                errors.append("Mass must be positive.")
            if Cd <= 0:
                errors.append("Drag coefficient must be positive.")
            if A <= 0:
                errors.append("Area must be positive.")
            if rho <= 0:
                errors.append("Air density must be positive.")
            if prop_m is not None and prop_m >= m:
                errors.append("Propellant mass must be less than liftoff mass.")
            if errors:
                self.error_label.setText("; ".join(errors))
                # Whatever flew last is not this rocket. Leaving it on screen
                # is how the app ends up reporting two different apogees.
                self.invalidate_flight_results("; ".join(errors))
                return
            else:
                self.error_label.setText("")

            # Advanced parachute modeling: get both parachute settings
            try:
                chute_height = float(self.chute_height_input.text()) if self.chute_height_input.text() else None
                chute_size = float(self.chute_size_input.text()) if self.chute_size_input.text() else None
            except Exception:
                chute_height = chute_size = None
            # Get time step from UI
            try:
                time_step = float(self.timestep_input.text())
            except Exception:
                time_step = 0.1
            sim_kwargs = {
                'thrust_curve_path': self.thrust_curve_path,
                'chute_height': chute_height,
                'chute_size': chute_size,
                'time_step': time_step,
                'chute_deploy_start': chute_deploy_time,
                'chute_cd': chute_cd,
                'propellant_mass': prop_m
            }
            results, self.last_flight_summary = self.run_flight_simulation(
                m, Cd, A, rho, sim_kwargs, time_step,
                body_diameter=body_diameter)
            self._warn_if_truncated(self.last_flight_summary)
            # Error handling for simulation results
            if isinstance(results, dict) and 'error' in results:
                self.error_label.setText(results['error'])
                self.invalidate_flight_results(results['error'])
                return
            if not isinstance(results, list) or not results:
                self.error_label.setText("Simulation returned unexpected data.")
                self.invalidate_flight_results(
                    "Simulation returned unexpected data.")
                return
            self.display_results(results)
            self.plot_results(results)
            self.update_flight_report(results, body_diameter)

        except ValueError:
            self.error_label.setText("Please enter valid numbers.")
            self.invalidate_flight_results("Please enter valid numbers.")

    def _assembly_changed(self):
        """A different engine or airframe was paired on the Simulation tab."""
        try:
            if hasattr(self, 'vehicle_tab'):
                self.vehicle_tab._refresh_summary()
        except Exception:
            pass
        if hasattr(self, 'model_note_label'):
            engine, aero = self.assembly.selection()
            self.model_note_label.setText(
                f"Assembly changed: engine <b>{engine or 'from the Engine tab'}"
                f"</b>, aerodynamics <b>{aero or 'from the Aerodynamics tab'}"
                f"</b>. Run a simulation to fly it.")

    def cd_source(self):
        """What drag the flight should use: a curve, a constant, or None.

        Precedence is by how much the source actually knows. An imported
        Cd(Mach) table describes the transonic rise; the Aerodynamics tab's
        measured Cd does not but is still a measurement; with neither, the
        component buildup estimates it.
        """
        table = None
        if hasattr(self, 'aero_tab'):
            table = self.aero_tab.current_table()
        if table is not None:
            return table
        return self.vehicle_tab.cd_override() if hasattr(self, 'vehicle_tab') else None

    def _cd_table_changed(self, table):
        """The Aero tab loaded or cleared an external drag curve."""
        if not hasattr(self, 'error_label'):
            return
        if table is None:
            self.error_label.setText("")
            return
        lo, hi = table.mach_range
        self.error_label.setText("")
        if hasattr(self, 'model_note_label'):
            self.model_note_label.setText(
                f"Drag will come from the imported curve <b>{table.name}</b> "
                f"(Mach {lo:.2f}-{hi:.2f}) instead of the computed buildup. "
                f"Run a simulation to fly it.")

    def _warn_if_truncated(self, summary):
        """Say so when the run ended with the vehicle still in the air.

        A big canopy deployed high can take longer to come down than the
        integrator is allowed to run. Everything up to and including apogee is
        still valid; the descent simply stops early, and nothing downstream
        should be read as a landing.
        """
        if not summary or not summary.get('truncated'):
            return
        alt = summary.get('final_altitude_m', 0.0)
        dur = summary.get('duration_s', 0.0)
        self.error_label.setText(
            f"Simulation hit its time limit after {dur:,.0f} s with the "
            f"vehicle still {alt:,.0f} m ({alt*flight_equations.FT_PER_M:,.0f} ft) up. Apogee "
            f"and the ascent are valid; the descent is cut short, so landing "
            f"speed and drift are not final. Deploy the main lower or use a "
            f"smaller canopy.")

    def run_flight_simulation(self, m, Cd, A, rho, sim_kwargs, time_step,
                              body_diameter=None, site_override=None,
                              cg_override=None, cp_override=None):
        """Fly the vehicle.

        Uses the 2-DOF model (wind, full atmosphere, Mach-5 drag buildup,
        staged recovery, moving CG) whenever there is a thrust curve and a
        Vehicle configuration to fly, and falls back to the original vertical
        model otherwise.

        Which inputs win, and why:

        * Masses and body diameter come from the Simulation tab when entered
          there - they are the same physical quantities the Aerodynamics tab
          and letting the two disagree silently is how a 98 mm rocket ends up
          flown as the Aerodynamics tab's 140 mm default.
        * Drag comes from the Aerodynamics tab: the 2-DOF model rebuilds Cd
          step from the airframe shape, which is the entire point of it. The
          Simulation tab's single Cd and reference area are used only by the
          fallback model. Same for its parachute fields, which the Vehicle
          tab's staged recovery supersedes.

        Anything the 2-DOF model ignored is reported through
        self.model_note_label rather than dropped in silence.
        """
        curve_path = sim_kwargs.get('thrust_curve_path')
        if hasattr(self, 'vehicle_tab') and curve_path:
            try:
                points, curve_prop = flight_model.load_thrust_curve(curve_path)
                if points:
                    airframe = self.vehicle_tab.airframe()
                    # The Stability Test tab flies its own wind and rail
                    # angle, set on that tab, rather than the Aerodynamics
                    # tab's - otherwise its controls change nothing.
                    site = site_override or self.vehicle_tab.launch_site()
                    recovery_system = self.vehicle_tab.recovery_system()
                    mass_props = self.vehicle_tab.mass_properties()
                    # The Simulation tab's masses win if they were entered:
                    # liftoff mass there is dry + propellant. But when mass
                    # components exist THEY are the dry mass and
                    # effective_dry_mass() ignores dry_mass_kg entirely, so
                    # setting it here was a no-op that let a 26 kg liftoff mass
                    # fly as 4.9 kg. Say so rather than quietly disagreeing.
                    prop = sim_kwargs.get('propellant_mass') or curve_prop or 0.0
                    if prop > 0:
                        mass_props.propellant_mass_kg = prop
                    self._mass_conflict = None
                    if m > prop > 0:
                        wanted_dry = m - prop
                        if mass_props._has_components():
                            summed = mass_props.effective_dry_mass()
                            if abs(summed - wanted_dry) > 0.05 * max(wanted_dry, 1e-9):
                                self._mass_conflict = (m, wanted_dry, summed)
                        else:
                            mass_props.dry_mass_kg = wanted_dry
                    # An imported Cd(Mach) curve beats a single number,
                    # which in turn beats the estimate.
                    cd_over = self.cd_source()
                    # A CG set by hand on the Stability Test tab is applied
                    # by sliding the whole vehicle's mass distribution so its
                    # LOADED centre of gravity lands where it was typed. The
                    # propellant still burns off and the CG still migrates -
                    # what moves is where it starts.
                    if cg_override is not None:
                        loaded = mass_props.cg(
                            max(0.0, mass_props.propellant_mass_kg))
                        shift = float(cg_override) - loaded
                        if abs(shift) > 1e-9:
                            # Slide the components along rather than deleting
                            # them. Dropping the buildup took the component
                            # MASS with it - dry mass fell back to the typed
                            # field, 5 kg became 20 kg - and took the real
                            # pitch inertia too, so the vehicle both weighed
                            # the wrong amount and turned at the wrong rate.
                            # The override is about where the CG sits, not
                            # about how much the rocket weighs.
                            buildup = mass_props.buildup
                            if buildup is not None and mass_props._has_components():
                                for component in getattr(buildup, "components",
                                                         None) or []:
                                    component.position_m = (
                                        component.position_m + shift)
                            else:
                                mass_props.dry_cg_m = (
                                    mass_props.effective_dry_cg() + shift)
                            mass_props.propellant_cg_m += shift
                    results, summary = flight_model.run_flight(
                        points, airframe, site, recovery_system, mass_props,
                        output_dt=max(0.02, float(time_step or 0.05)),
                        cd_override=cd_over, cp_override=cp_override)
                    if results:
                        self._note_model_used(results, airframe,
                                              recovery_system, cd_over, Cd, A,
                                              body_diameter)
                        return results, summary
            except Exception:
                traceback.print_exc()
                self.error_label.setText(
                    "Advanced flight model failed; fell back to the basic "
                    "vertical model. See the console for details.")
        self._note_fallback_model()
        return run_simulation(m, Cd, A, rho, **sim_kwargs), None

    def _note_model_used(self, results, airframe, recovery_system,
                         cd_override, sim_cd, sim_area, sim_diameter=None):
        """Say which model flew and which Simulation-tab inputs it ignored."""
        if not hasattr(self, 'model_note_label'):
            return
        cds = [r.get('Cd_body_eff', 0.0) for r in results if r.get('Cd_body_eff')]
        if callable(cd_override):
            lo, hi = cd_override.mach_range
            drag = (f"imported Cd(Mach) curve '{cd_override.name}' "
                    f"(Mach {lo:.2f}-{hi:.2f})")
        elif cd_override:
            drag = (f"fixed Cd {cd_override:.3f} from the Aerodynamics tab's "
                    f"measured-Cd override")
        elif cds:
            drag = (f"Cd rebuilt every step from the airframe shape "
                    f"({min(cds):.3f}-{max(cds):.3f} over the flight)")
        else:
            drag = "Cd from the airframe buildup"
        stages = ", ".join(s.name for s in recovery_system.active_stages()) or "none"
        ignored = []
        if sim_cd:
            ignored.append(f"drag coefficient ({sim_cd:g})")
        if sim_area:
            ignored.append(f"reference area ({sim_area:g} m²)")
        ignored.append("parachute fields")
        text = (
            f"Flown with the 2-DOF model: "
            f"{airframe.body_diameter_m*1000:.0f} mm airframe from the "
            f"Aerodynamics tab, {drag}, recovery from the Aerodynamics tab "
            f"({stages}).<br><i>Not used by this model:</i> the Simulation "
            f"tab's " + ", ".join(ignored) +
            " — those drive the basic vertical model only.")
        # Geometry is owned by the Aerodynamics section, but the same quantity
        # exists on this tab. If the two disagree, say so rather than picking
        # one silently - that is how a 76 mm rocket gets flown as 2 mm.
        if (sim_diameter and sim_diameter > 0 and airframe.body_diameter_m > 0
                and abs(sim_diameter - airframe.body_diameter_m)
                > 0.02 * airframe.body_diameter_m):
            text += (f"<br><b>Body diameter disagrees between tabs:</b> "
                     f"{sim_diameter*1000:.1f} mm here vs "
                     f"{airframe.body_diameter_m*1000:.1f} mm on the "
                     f"Aerodynamics tab. The Aerodynamics value was flown. "
                     f"Check the unit selector on this tab.")
        conflict = getattr(self, '_mass_conflict', None)
        if conflict:
            liftoff, wanted_dry, summed = conflict
            text += (f"<br><b>Liftoff mass on this tab was not used:</b> "
                     f"{liftoff:,.2f} kg implies {wanted_dry:,.2f} kg dry, but "
                     f"the Mass &amp; Ballast components sum to "
                     f"{summed:,.2f} kg and components take precedence. The "
                     f"flight used {summed:,.2f} kg.")
        self.model_note_label.setText(text)

    def _note_fallback_model(self):
        if not hasattr(self, 'model_note_label'):
            return
        self.model_note_label.setText(
            "Flown with the basic vertical model, using the Simulation tab's "
            "drag coefficient, reference area and parachute settings. Load a "
            "thrust curve to use the 2-DOF model.")

    def update_flight_report(self, results, body_diameter=None):
        """Hand the finished run to the Flight Report tab for failure analysis."""
        if not hasattr(self, 'flight_report'):
            return
        try:
            # The engine internals are always computed, so the thirteen
            # propulsion checks always have real numbers to grade. They used
            # to be supplied only when the flight had been flown on the
            # Engine Lab's own generated curve, which meant every flight on
            # an imported thrust curve - most of them - reported NO DATA
            # across the whole propulsion section.
            #
            # flown_engine says which case this is: True when the curve that
            # flew came from this motor, False when the motor is modelled
            # from the Engine tab while a different curve flew. The report
            # says so rather than implying the two match.
            flown_engine = bool(
                getattr(self, 'engine_lab_curve_path', None)
                and self.thrust_curve_path == self.engine_lab_curve_path)
            engine_run = (self.engine_lab.get_last_run() if flown_engine
                          else self.engine_lab.current_engine_run())
            engine, engine_result = ((engine_run[0], engine_run[1])
                                     if engine_run else (None, None))

            # Everything the report grades geometry against should be the
            # geometry that actually flew. Passing only diameter, fin count and
            # fin thickness left flutter, buckling and rail-exit graded against
            # VehicleConfig defaults - a 5.18 m rail and a 300 mm root chord -
            # regardless of the airframe on the Aerodynamics tab.
            hints = {}
            try:
                af = self.vehicle_tab.airframe()
                hints.update({
                    'body_od_m': af.body_diameter_m,
                    'body_length_m': af.total_length,
                    'fin_count': int(af.fin_count),
                    'fin_root_chord_m': af.fin_root_chord_m,
                    'fin_tip_chord_m': af.fin_tip_chord_m,
                    'fin_span_m': af.fin_span_m,
                    'fin_thickness_m': af.fin_thickness_m,
                })
                hints['rail_length_m'] = self.vehicle_tab.launch_site().rail_length_m
            except Exception:
                traceback.print_exc()
            # The Simulation tab's own body diameter still wins if it was typed
            # and the Aerodynamics tab has nothing useful.
            if body_diameter and not hints.get('body_od_m'):
                hints['body_od_m'] = body_diameter
            hints = {k: v for k, v in hints.items() if v}

            self.flight_report.update_from_simulation(
                results, engine_result=engine_result, engine=engine,
                engine_is_flown=flown_engine,
                geometry_hints=hints,
                cd_source=self.cd_source(),
                mass_props=(self.vehicle_tab.mass_properties()
                            if hasattr(self, 'vehicle_tab') else None),
                summary=getattr(self, 'last_flight_summary', None))
        except Exception:
            traceback.print_exc()

    def get_local_speed_of_sound(self):
        try:
            temp_c = float(self.temperature_input.text())
            humidity = float(self.humidity_input.text())
            altitude = float(self.start_altitude_input.text())
            # Calculate speed of sound (approximate, dry air)
            temp_k = temp_c + 273.15
            # Use formula: a = sqrt(gamma * R * T)
            gamma = 1.4
            R = 287.05
            a = (gamma * R * temp_k) ** 0.5
            return a
        except Exception:
            return 343.0

    def populate_datasheets(self, results):
        """Fill the Flight Data and Engine Data sheets from the latest run."""
        try:
            # Derivation lives in datasheet.flight_rows so the reported
            # quantities sit next to the column spec that names them, and so
            # the engine and flight sheets are built the same way.
            self.flight_sheet.set_rows(datasheet.flight_rows(results))
        except Exception:
            traceback.print_exc()

        # Motor internals are computed for every run now, the same as the
        # Flight Report's propulsion checks. When the flown curve came from
        # somewhere else the sheet says so rather than showing nothing - the
        # numbers still describe a real motor, just not necessarily the one
        # that flew.
        try:
            flown = bool(getattr(self, 'engine_lab_curve_path', None)
                         and self.thrust_curve_path == self.engine_lab_curve_path)
            engine_run = (self.engine_lab.get_last_run() if flown
                          else self.engine_lab.current_engine_run())
            if engine_run:
                self.engine_sheet.set_rows(datasheet.engine_rows(engine_run[1]))
                self.engine_sheet.info.setText(
                    "Motor internals at every integration sample."
                    if flown else
                    "This flight used an imported thrust curve. These are the "
                    "internals of the motor described on the Engine tab, which "
                    "is not necessarily the motor that flew.")
            else:
                self.engine_sheet.set_rows([])
                self.engine_sheet.info.setText(
                    "The Engine tab does not yet describe a motor that can be "
                    "modelled. Fill in the tank, injector, grain and nozzle "
                    "to get chamber pressure, O/F, regression rate and the rest.")
        except Exception:
            traceback.print_exc()

    def invalidate_flight_results(self, reason=""):
        """Drop every number that came from the last flight.

        A run's results outlive the rocket that produced them unless something
        throws them away. Loading a different rocket, or a run that fails,
        used to leave the previous flight on screen: the Simulation tab's
        summary, the Flight Report's verdict, the raw sheets, the graphs, the
        force diagram and the stability verdict all kept showing it, beside
        the new vehicle's configuration. That is what makes the app report one
        rocket at two different apogees depending which panel you read.

        So they are cleared together, and the reason is put where the numbers
        used to be rather than leaving a blank panel.
        """
        self._last_results = None
        self.last_flight_summary = None
        self._stability_results = None
        self._stability_summary = None
        self._stability_envelope = None
        self._force_scrub_time = None

        for sheet in ('flight_sheet', 'engine_sheet'):
            widget = getattr(self, sheet, None)
            if widget is not None:
                try:
                    widget.set_rows([])
                except Exception:
                    pass
        try:
            self.flight_report.clear_flight(reason)
        except Exception:
            pass
        if hasattr(self, 'force_scrubber'):
            self.force_scrubber.setEnabled(False)
            self.force_time_label.setText("Run a simulation to scrub")
        # Back to the pad: the diagram has no flight to read any more.
        try:
            self.update_force_diagram(None)
        except Exception:
            pass
        # The stability verdict came from the flight that just went away.
        if hasattr(self, 'stability_status_label'):
            self._set_stability_note(
                reason or "Press Launch to test this rocket through the burn",
                ok=True)
        try:
            self.clear_plots()
        except Exception:
            pass

    def clear_plots(self):
        """Blank the Simulation tab's plots, with a line saying why."""
        for name in ('figure', 'fig'):
            figure = getattr(self, name, None)
            if figure is None:
                continue
            try:
                app_theme.placeholder_figure(
                    figure, "No flight - run a simulation.")
                canvas = getattr(self, 'canvas', None)
                if canvas is not None:
                    canvas.draw_idle()
            except Exception:
                pass
            break

    def display_results(self, results):
        # Kept so the summary can be redrawn when the unit system changes.
        # It is built from the flight inline, so without this the panel stayed
        # in whichever system was current when the simulation ran: every other
        # number on screen switched to feet and the summary went on saying
        # 2795.85 m, which is the one place a units mix-up is least obvious.
        self._last_display_results = results
        if results:
            self.populate_datasheets(results)
            # Find max values and their times
            max_alt = max(r['altitude'] for r in results)
            max_alt_idx = next(i for i, r in enumerate(results) if r['altitude'] == max_alt)
            max_alt_time = results[max_alt_idx]['time']

            max_vel = max(r['velocity'] for r in results)
            max_vel_idx = next(i for i, r in enumerate(results) if r['velocity'] == max_vel)
            max_vel_time = results[max_vel_idx]['time']

            max_thrust = max(r['thrust'] for r in results)
            max_thrust_idx = next(i for i, r in enumerate(results) if r['thrust'] == max_thrust)
            max_thrust_time = results[max_thrust_idx]['time']

            max_drag = max(r['drag'] for r in results)
            max_drag_idx = next(i for i, r in enumerate(results) if r['drag'] == max_drag)
            max_drag_time = results[max_drag_idx]['time']

            # Final mass (not max) is more meaningful after burn
            final_mass = results[-1]['mass']
            final_mass_time = results[-1]['time']

            # Mach comes from the flight itself.
            #
            # This used to divide every velocity by ONE speed of sound - the
            # one at the launch site - while the flight model recorded Mach
            # against the local speed of sound at each altitude. The air is
            # colder and slower up there, so the two disagreed by about 2%:
            # the summary said Mach 1.245 where the graphs and the raw sheet
            # said 1.271, for the same flight. Use the recorded column when it
            # is there, and only fall back to the fixed figure for the basic
            # model, which does not produce one.
            machs = [r.get('Mach') for r in results if r.get('Mach') is not None]
            if len(machs) == len(results) and machs:
                machs = [r['Mach'] for r in results]
            else:
                local_a = self.get_local_speed_of_sound()
                machs = [r['velocity']/local_a if local_a else 0 for r in results]
            max_mach = max(machs)
            max_mach_idx = machs.index(max_mach)
            max_mach_time = results[max_mach_idx]['time']

            vel_unit = 'm/s'
            alt_unit = 'm'
            thrust_unit = 'N'
            drag_unit = 'N'
            mass_unit = 'kg'
            if self.unit_select.currentIndex() == 1:
                max_vel_disp = max_vel * 3.28084
                vel_unit = 'ft/s'
                max_alt_disp = max_alt * 3.28084
                alt_unit = 'ft'
                max_thrust_disp = max_thrust * 0.224809
                thrust_unit = 'lbf'
                max_drag_disp = max_drag * 0.224809
                drag_unit = 'lbf'
                final_mass_disp = final_mass * 2.20462
                mass_unit = 'lb'
            else:
                max_vel_disp = max_vel
                max_alt_disp = max_alt
                max_thrust_disp = max_thrust
                max_drag_disp = max_drag
                final_mass_disp = final_mass

            # Dragstrip-style stat: show all max times in a single line
            dragstrip = (
                f"[Max Times] Apogee: {max_alt_time:.2f}s | Velocity: {max_vel_time:.2f}s | Mach: {max_mach_time:.2f}s | Thrust: {max_thrust_time:.2f}s | Drag: {max_drag_time:.2f}s | Final Mass @ {final_mass_time:.2f}s"
            )

            # Nicely formatted compact HTML summary table (final stats)
            summary_html = f"""
<div style='font-family:Consolas, Courier New, monospace; font-size:12px; line-height:1.25;'>
    <table style='border-collapse:collapse;'>
        <tr><th style='text-align:left;padding:2px 8px;border-bottom:1px solid #BCA16A;'>Metric</th>
                <th style='text-align:right;padding:2px 8px;border-bottom:1px solid #BCA16A;'>Value</th></tr>
        <tr><td style='padding:2px 8px;'>Apogee</td><td style='padding:2px 8px;text-align:right;'>{max_alt_disp:.2f} {alt_unit}</td></tr>
        <tr><td style='padding:2px 8px;'>Max Velocity</td><td style='padding:2px 8px;text-align:right;'>{max_vel_disp:.2f} {vel_unit}</td></tr>
        <tr><td style='padding:2px 8px;'>Max Mach</td><td style='padding:2px 8px;text-align:right;'>{max_mach:.2f}</td></tr>
        <tr><td style='padding:2px 8px;'>Max Thrust</td><td style='padding:2px 8px;text-align:right;'>{max_thrust_disp:.2f} {thrust_unit}</td></tr>
        <tr><td style='padding:2px 8px;'>Max Drag</td><td style='padding:2px 8px;text-align:right;'>{max_drag_disp:.2f} {drag_unit}</td></tr>
    <tr><td style='padding:2px 8px;'>Final Mass</td><td style='padding:2px 8px;text-align:right;'>{final_mass_disp:.2f} {mass_unit}</td></tr>
    </table>
</div>
"""
            self.result_label.setText(summary_html)
        else:
            self.result_label.setText("No results to display.")

    def plot_results(self, results):
        self._last_results = results
        # The force diagram can now be scrubbed across the real flight.
        try:
            self.enable_force_scrubber()
        except Exception:
            traceback.print_exc()
        self.figure.clear()
        if not results:
            return

        ax = self.figure.add_subplot(111)
        # Apply theme-aware styling
        self.style_axes(ax)

        times = [r['time'] for r in results]
        # Derived series
        g0 = 9.81
        acc = [r.get('acceleration', 0.0) for r in results]
        masses = [r.get('mass', 0.0) for r in results]
        thrusts = [r.get('thrust', 0.0) for r in results]
        drag_signed = [r.get('drag_signed', -abs(r.get('drag', 0.0))) for r in results]
        net_force = [thrusts[i] + drag_signed[i] - masses[i]*g0 for i in range(len(results))]
        g_load = [a/g0 if g0 else 0.0 for a in acc]

        # Pick series colors based on theme (extended palette)
        if self.current_theme == "retro":
            series_colors = [
                "#E94F37", "#1C77C3", "#FFD447", "#3C2F1E", "#A7C7E7", "#A259F7", "#2E8B57", "#FF6B35"
            ]
        else:
            series_colors = [
                "#00D4FF", "#00FF41", "#FF6B35", "#ECF0F1", "#FFA500", "#FF69B4", "#32CD32", "#FF1493"
            ]

        labels = [
            "Altitude", "Velocity", "Mass", "Acceleration", "Thrust", "Drag", "G-Load", "Net Force"
        ]
        values_map = {
            "Altitude": [r.get('altitude', 0.0) for r in results],
            "Velocity": [r.get('velocity', 0.0) for r in results],
            "Mass": masses,
            "Acceleration": acc,
            "Thrust": thrusts,
            "Drag": [r.get('drag', 0.0) for r in results],
            "G-Load": g_load,
            "Net Force": net_force,
        }
        # Convert to the unit system on screen, and SAY which it is.
        #
        # This plotted raw SI and labelled the axis "Value". So the L550's
        # apogee was drawn as 4,853 - metres - beside a Flight Report reading
        # 15,921 ft for the same flight. Two views of one number, neither
        # carrying a unit, and the graph looked like the rocket only reached
        # about 5,000 ft. Every series now carries its unit in the legend, and
        # the axis carries it too when everything plotted shares one.
        imperial = self.unit_select.currentIndex() == 1
        FT = flight_equations.FT_PER_M
        unit_map = {
            #  label          metric                imperial
            "Altitude":     (("m", 1.0),         ("ft", FT)),
            "Velocity":     (("m/s", 1.0),       ("ft/s", FT)),
            "Mass":         (("kg", 1.0),        ("lb", 1.0 / 0.45359237)),
            "Acceleration": (("m/s2", 1.0),      ("ft/s2", FT)),
            "Thrust":       (("N", 1.0),         ("lbf", 0.224808943)),
            "Drag":         (("N", 1.0),         ("lbf", 0.224808943)),
            "G-Load":       (("g", 1.0),         ("g", 1.0)),
            "Net Force":    (("N", 1.0),         ("lbf", 0.224808943)),
        }

        def converted(label):
            unit, factor = unit_map[label][1 if imperial else 0]
            return [v * factor for v in values_map[label]], unit

        plotted = False
        units_shown = []
        tooltip_label = 'Altitude'
        tooltip_values, tooltip_unit = converted('Altitude')
        for i, label in enumerate(labels):
            if self.graph_vars.get(label) and self.graph_vars[label].isChecked():
                values, unit = converted(label)
                ax.plot(times, values, label=f"{label} ({unit})",
                        color=series_colors[i % len(series_colors)])
                units_shown.append(unit)
                if not plotted:
                    tooltip_label, tooltip_values, tooltip_unit = label, values, unit
                plotted = True
        if not plotted:
            # Default to altitude if nothing selected
            ax.plot(times, tooltip_values, label=f"{tooltip_label} ({tooltip_unit})",
                    color=series_colors[0])
            units_shown.append(tooltip_unit)
        ax.set_xlabel('Time (s)')
        # One unit on the axis when everything shares it; otherwise say plainly
        # that the axis is mixed rather than pretend a single scale means
        # something across metres, newtons and kilograms at once.
        distinct = sorted(set(units_shown))
        ax.set_ylabel(distinct[0] if len(distinct) == 1
                      else "mixed units - see legend")
        ax.legend(ncol=2, loc='best')
        self._plot_altitude_factor = FT if imperial else 1.0
        ax.figure.tight_layout()
        self.canvas.draw()

        # === FBD Animation Overlay ===
        from matplotlib.lines import Line2D

        # Remove previous FBD artists if any
        if hasattr(self, '_fbd_artists'):
            for artist in self._fbd_artists:
                try:
                    artist.remove()
                except Exception:
                    pass
        self._fbd_artists = []

        # Get time and altitude arrays
        times = [r['time'] for r in results]
        # Same scale as the plotted curve, or the marker floats off it.
        _alt_factor = getattr(self, '_plot_altitude_factor', 1.0)
        altitudes = [r['altitude'] * _alt_factor for r in results]

        # The marker and its arrows are drawn in DATA units, so they have to
        # be sized against the altitude axis rather than pinned to metres.
        # Ten metres is a visible nudge on a 4,850 m axis and an invisible one
        # on the same flight in feet, where the axis runs to 15,900.
        rocket_width = 2.0  # seconds (x-axis units)
        rocket_height = max(1e-6,
                            max(altitudes) - min(altitudes)) * 0.002

        # Initial position (first point on curve)
        x_pos = times[0]
        y_pos = altitudes[0]

        import matplotlib.image as mpimg
        from matplotlib.offsetbox import OffsetImage, AnnotationBbox
        rocket_img = mpimg.imread(os.path.join(os.path.dirname(__file__), 'Rocket.png'))
        self._rocket_img = rocket_img  # Store for animation
        self._rocket_zoom = 0.08
        imagebox = OffsetImage(rocket_img, zoom=self._rocket_zoom)  # Adjust zoom for desired size
        rocket_artist = AnnotationBbox(
            imagebox,
            (x_pos, y_pos),
            frameon=False,
            pad=0,
            zorder=15
        )
        ax.add_artist(rocket_artist)
        self._rocket_artist = rocket_artist

        # Offset for force arrows (to the right of the rocket)
        arrow_x_offset = rocket_width * 1.5

        # Force arrows (Line2D)
        thrust_line, = ax.plot([x_pos + arrow_x_offset, x_pos + arrow_x_offset], [y_pos, y_pos], color='g', linewidth=3, marker='^', markersize=10, label='Thrust', zorder=12)
        drag_line, = ax.plot([x_pos + arrow_x_offset, x_pos + arrow_x_offset], [y_pos + rocket_height, y_pos + rocket_height], color='r', linewidth=3, marker='v', markersize=10, label='Drag', zorder=12)
        gravity_line, = ax.plot([x_pos + arrow_x_offset, x_pos + arrow_x_offset], [y_pos + rocket_height/2, y_pos + rocket_height/2 - rocket_height*0.08], color='b', linewidth=3, marker='v', markersize=10, label='Gravity', zorder=12)

        self._fbd_artists = [rocket_artist, thrust_line, drag_line, gravity_line]

        # Precompute max values for normalization
        max_thrust = max((r['thrust'] for r in results if r['thrust'] > 0), default=1)
        max_drag = max((r['drag'] for r in results if r['drag'] > 0), default=1)

        # Animation state
        self._fbd_frame = 0
        self._fbd_results = results

        from utils import get_flight_phase
        def fbd_anim_step():
            # Interpolate between data points for smooth animation
            frame = self._fbd_frame
            n_frames = len(self._fbd_results)
            subframe = getattr(self, '_fbd_subframe', 0.0)
            subframes_per_frame = 5
            if frame >= n_frames - 1:
                self._fbd_timer.stop()
                return
            result_a = self._fbd_results[frame]
            result_b = self._fbd_results[min(frame+1, n_frames-1)]
            t_a = result_a['time']
            t_b = result_b['time']
            scale = getattr(self, '_plot_altitude_factor', 1.0)
            alt_a = result_a['altitude'] * scale
            alt_b = result_b['altitude'] * scale
            frac = subframe / subframes_per_frame
            x_pos = t_a + (t_b - t_a) * frac
            y_pos = alt_a + (alt_b - alt_a) * frac
            # Interpolate velocity vector for angle
            if frame > 0:
                prev_a = self._fbd_results[frame-1]
                dx_a = t_a - prev_a['time']
                dy_a = alt_a - prev_a['altitude'] * scale
                dx_b = t_b - t_a
                dy_b = alt_b - alt_a
                dx = dx_a + (dx_b - dx_a) * frac
                dy = dy_a + (dy_b - dy_a) * frac
                angle = np.degrees(np.arctan2(dy, dx))
            else:
                angle = 90.0
            # Advance subframe
            subframe += 1
            if subframe >= subframes_per_frame:
                subframe = 0
                self._fbd_frame += 1
            self._fbd_subframe = subframe

            # Track apogee and chute deployment
            altitudes = [r['altitude'] for r in self._fbd_results]
            apogee_frame = np.argmax(altitudes)
            chute_frame = next((i for i, r in enumerate(self._fbd_results) if r.get('chute_deployed')), None)

            # Flip rocket at apogee (point down)
            if frame >= apogee_frame and (chute_frame is None or frame < chute_frame):
                angle += 180.0
            # Flip back when chute deploys
            if chute_frame is not None and frame >= chute_frame:
                angle -= 180.0

            # Adjust for image orientation (e.g., subtract 90° if rocket.png points right)
            angle -= 90.0

            # Cache rotated images for performance (round to nearest 5°)
            cache_angle = int(round(angle / 5.0) * 5)
            if cache_angle not in self._rocket_img_cache:
                import scipy.ndimage
                self._rocket_img_cache[cache_angle] = scipy.ndimage.rotate(self._rocket_img, cache_angle, reshape=False, mode='nearest')
            rotated_img = self._rocket_img_cache[cache_angle]
            # Clip image data to valid range for imshow
            if rotated_img.dtype == float:
                rotated_img = np.clip(rotated_img, 0, 1)
            else:
                rotated_img = np.clip(rotated_img, 0, 255)
            rotated_imagebox = OffsetImage(rotated_img, zoom=self._rocket_zoom)
            # Remove previous rocket image
            try:
                self._rocket_artist.remove()
            except Exception:
                pass
            new_rocket_artist = AnnotationBbox(
                rotated_imagebox,
                (x_pos, y_pos),
                frameon=False,
                pad=0,
                zorder=15
            )
            ax.add_artist(new_rocket_artist)
            self._rocket_artist = new_rocket_artist
            # Normalize arrow lengths
            thrust_a = result_a['thrust']
            thrust_b = result_b['thrust']
            drag_a = result_a['drag']
            drag_b = result_b['drag']
            thrust_val = (thrust_a + (thrust_b - thrust_a) * frac) / max_thrust if max_thrust else 0
            drag_val = (drag_a + (drag_b - drag_a) * frac) / max_drag if max_drag else 0
            # Update thrust arrow (upwards from rocket base)
            thrust_line.set_data([x_pos + arrow_x_offset, x_pos + arrow_x_offset], [y_pos, y_pos + thrust_val * rocket_height * 2.0])
            # Update drag arrow (downwards from rocket top)
            drag_line.set_data([x_pos + arrow_x_offset, x_pos + arrow_x_offset], [y_pos + rocket_height,
                                  y_pos + rocket_height - drag_val * rocket_height * 2.0])
            # Gravity arrow (fixed length, always down from rocket center)
            gravity_line.set_data([x_pos + arrow_x_offset, x_pos + arrow_x_offset], [y_pos + rocket_height/2, y_pos + rocket_height/2 - rocket_height*0.08])

            # Redraw canvas so rocket and arrows move together
            self.canvas.draw_idle()

            # --- Live statistics update ---
            # Units
            vel_a = result_a['velocity']
            vel_b = result_b['velocity']
            mass_a = result_a['mass']
            mass_b = result_b['mass']
            if self.unit_select.currentIndex() == 1:  # Imperial
                # y_pos rides the plotted curve, which is already in feet when
                # the display is Imperial - so it is NOT converted again here.
                # Velocity, mass and the forces are still raw SI and are.
                alt_disp = y_pos
                alt_unit = 'ft'
                vel_disp = (vel_a + (vel_b - vel_a) * frac) * 3.28084
                vel_unit = 'ft/s'
                mass_disp = (mass_a + (mass_b - mass_a) * frac) * 2.20462
                mass_unit = 'lb'
                thrust_disp = (thrust_a + (thrust_b - thrust_a) * frac) * 0.224809
                thrust_unit = 'lbf'
                drag_disp = (drag_a + (drag_b - drag_a) * frac) * 0.224809
                drag_unit = 'lbf'
            else:
                alt_disp = y_pos
                alt_unit = 'm'
                vel_disp = vel_a + (vel_b - vel_a) * frac
                vel_unit = 'm/s'
                mass_disp = mass_a + (mass_b - mass_a) * frac
                mass_unit = 'kg'
                thrust_disp = thrust_a + (thrust_b - thrust_a) * frac
                thrust_unit = 'N'
                drag_disp = drag_a + (drag_b - drag_a) * frac
                drag_unit = 'N'
            # Mach number
            try:
                mach = (vel_a + (vel_b - vel_a) * frac) / self.get_local_speed_of_sound()
            except Exception:
                mach = 0.0
            # --- Dragstrip-style max time stat (live) ---
            # Compute max times up to current frame
            results_so_far = self._fbd_results[:frame+1]
            def get_max_time(key, arr=results_so_far):
                max_val = max(r[key] for r in arr)
                idx = next(i for i, r in enumerate(arr) if r[key] == max_val)
                return arr[idx]['time']
            local_a = self.get_local_speed_of_sound()
            machs = [r['velocity']/local_a if local_a else 0 for r in results_so_far]
            max_mach = max(machs)
            max_mach_idx = machs.index(max_mach)
            max_mach_time = results_so_far[max_mach_idx]['time']
            dragstrip = (
                f"[Max Times] Apogee: {get_max_time('altitude'):.2f}s | Velocity: {get_max_time('velocity'):.2f}s | Mach: {max_mach_time:.2f}s | Thrust: {get_max_time('thrust'):.2f}s | Drag: {get_max_time('drag'):.2f}s | Mass: {get_max_time('mass'):.2f}s"
            )
            # Live compact table for current frame values
            live_html = f"""
<div style='font-family:Consolas, Courier New, monospace; font-size:11px;'>
    <table style='border-collapse:collapse;'>
        <tr>
            <th style='text-align:left;padding:1px 6px;border-bottom:1px solid #BCA16A;'>t (s)</th>
            <th style='text-align:right;padding:1px 6px;border-bottom:1px solid #BCA16A;'>{x_pos:.2f}</th>
            <th style='text-align:left;padding:1px 6px;border-bottom:1px solid #BCA16A;'>Alt ({alt_unit})</th>
            <th style='text-align:right;padding:1px 6px;border-bottom:1px solid #BCA16A;'>{alt_disp:.2f}</th>
            <th style='text-align:left;padding:1px 6px;border-bottom:1px solid #BCA16A;'>Vel ({vel_unit})</th>
            <th style='text-align:right;padding:1px 6px;border-bottom:1px solid #BCA16A;'>{vel_disp:.2f}</th>
        </tr>
        <tr>
            <td style='padding:1px 6px;'>Mach</td><td style='padding:1px 6px;text-align:right;'>{mach:.2f}</td>
            <td style='padding:1px 6px;'>Thrust ({thrust_unit})</td><td style='padding:1px 6px;text-align:right;'>{thrust_disp:.2f}</td>
            <td style='padding:1px 6px;'>Drag ({drag_unit})</td><td style='padding:1px 6px;text-align:right;'>{drag_disp:.2f}</td>
        </tr>
        <tr>
            <td style='padding:1px 6px;'>Mass ({mass_unit})</td><td style='padding:1px 6px;text-align:right;'>{mass_disp:.2f}</td>
            <td style='padding:1px 6px;'>Max Alt (t)</td><td style='padding:1px 6px;text-align:right;'>{get_max_time('altitude'):.2f}</td>
            <td style='padding:1px 6px;'>Max Vel (t)</td><td style='padding:1px 6px;text-align:right;'>{get_max_time('velocity'):.2f}</td>
        </tr>
    </table>
</div>
"""
            self.result_label.setText(live_html)

            # (Progress bar / phase display removed.)

            self.canvas.draw_idle()
            self._fbd_frame += 1

        # Animation removed: no QTimer started

        # Add popup tooltip on hover (default to altitude, or first selected variable)
        if not hasattr(self, 'tooltip'):
            self.tooltip = QtWidgets.QToolTip
        def on_motion(event):
            if event.inaxes == ax:
                xdata = event.xdata
                ydata = event.ydata
                if xdata is None or ydata is None:
                    self.canvas.setToolTip("")
                    return
                idx = min(range(len(times)), key=lambda i: abs(times[i] - xdata))
                xval = times[idx]
                yval = tooltip_values[idx]
                # tooltip_values are ALREADY in the displayed unit - the plot
                # converted them before drawing. Converting again here showed
                # altitude at 10.76x in Imperial, feet turned into feet a
                # second time. Take the series and its unit as they are.
                unit_label = f"{tooltip_label} ({tooltip_unit})" if tooltip_unit \
                    else tooltip_label
                unit_value = yval
                unit_time = xval
                tooltip_text = f"Time: {unit_time:.2f} s\n{unit_label}: {unit_value:.2f}"
                QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), tooltip_text, self.canvas)
            else:
                QtWidgets.QToolTip.hideText()
        self.canvas.mpl_connect('motion_notify_event', on_motion)

    def save_inputs(self):
        # Save values as entered, in their selected units
        data = {
            'mass': self.mass_input.text(),
            'mass_unit': self.mass_unit.currentIndex(),
            'prop_mass': self.prop_mass_input.text(),
            'prop_mass_unit': self.prop_mass_unit.currentIndex(),
            'cd': self.cd_input.text(),
            'area': self.area_input.text(),
            'area_unit': self.area_unit.currentIndex(),
            'rho': self.rho_input.text(),
            'rho_unit': self.rho_unit.currentIndex(),
            'timestep': self.timestep_input.text(),
            'timestep_unit': self.timestep_unit.currentIndex(),
            'fin_count': self.fin_count_input.text(),
            'fin_thickness': self.fin_thickness_input.text(),
            'fin_thickness_unit': self.fin_thickness_unit.currentIndex(),
            'fin_length': self.fin_length_input.text(),
            'fin_length_unit': self.fin_length_unit.currentIndex(),
            'body_diameter': self.body_diameter_input.text(),
            'body_diameter_unit': self.body_diameter_unit.currentIndex(),
            'chute_height': self.chute_height_input.text(),
            'chute_height_unit': self.chute_height_unit.currentIndex(),
            'chute_size': self.chute_size_input.text(),
            'chute_size_unit': self.chute_size_unit.currentIndex(),
            # graph_select removed (single-variable dropdown deleted), multi-variable checkboxes persist
            'chute_cd': self.chute_cd_input.text(),
            'start_altitude': self.start_altitude_input.text(),
            'temperature': self.temperature_input.text(),
            'humidity': self.humidity_input.text(),
        }
        try:
            # Merge into whatever is already saved - this file also holds the
            # theme preference, which a blind overwrite would wipe.
            settings_path = getattr(self, 'user_settings_file', None) or user_settings_path()
            try:
                with open(settings_path, 'r') as f:
                    existing = json.load(f)
            except Exception:
                existing = {}
            existing.update(data)
            with open(settings_path, 'w') as f:
                json.dump(existing, f, indent=2)
        except Exception:
            pass

    def load_inputs(self):
        try:
            settings_path = getattr(self, 'user_settings_file', None) or user_settings_path()
            with open(settings_path, 'r') as f:
                data = json.load(f)
            self.mass_input.setText(str(data.get('mass', '')))
            self.mass_unit.setCurrentIndex(data.get('mass_unit', 0))
            self.prop_mass_input.setText(str(data.get('prop_mass', '')))
            self.prop_mass_unit.setCurrentIndex(data.get('prop_mass_unit', 0))
            self.cd_input.setText(str(data.get('cd', '')))
            self.area_input.setText(str(data.get('area', '')))
            self.area_unit.setCurrentIndex(data.get('area_unit', 0))
            self.rho_input.setText(str(data.get('rho', '')))
            self.rho_unit.setCurrentIndex(data.get('rho_unit', 0))
            self.timestep_input.setText(str(data.get('timestep', '')))
            self.timestep_unit.setCurrentIndex(data.get('timestep_unit', 0))
            self.fin_count_input.setText(str(data.get('fin_count', '')))
            self.fin_thickness_input.setText(str(data.get('fin_thickness', '')))
            self.fin_thickness_unit.setCurrentIndex(data.get('fin_thickness_unit', 0))
            self.fin_length_input.setText(str(data.get('fin_length', '')))
            self.fin_length_unit.setCurrentIndex(data.get('fin_length_unit', 0))
            self.body_diameter_input.setText(str(data.get('body_diameter', '')))
            self.body_diameter_unit.setCurrentIndex(data.get('body_diameter_unit', 0))
            self.chute_height_input.setText(str(data.get('chute_height', '')))
            self.chute_height_unit.setCurrentIndex(data.get('chute_height_unit', 0))
            self.chute_size_input.setText(str(data.get('chute_size', '')))
            self.chute_size_unit.setCurrentIndex(data.get('chute_size_unit', 0))
            self.chute_cd_input.setText(str(data.get('chute_cd', '1.5')))
            self.start_altitude_input.setText(str(data.get('start_altitude', '0')))
            self.temperature_input.setText(str(data.get('temperature', '15')))
            self.humidity_input.setText(str(data.get('humidity', '50')))
            # graph_select state no longer loaded (dropdown removed)
        except Exception:
            pass

    def update_graph(self, *args):
        if hasattr(self, '_last_results') and self._last_results:
            self.plot_results(self._last_results)

    def update_area(self):
        try:
            # Convert all inputs to meters first
            # Factors from the one table in units.py. The inline copies here
            # were rounded (ft2 as 0.092903 against the exact 0.09290304), so
            # every recompute nudged the area by 0.01% - and this runs on any
            # unit change, so switching the whole app to imperial walked the
            # value each time.
            dia_units = self._SIM_UNITS['body_diameter']
            body_diameter_m = self.get_value_in_base_unit(
                self.body_diameter_input.text(),
                self.body_diameter_unit.currentIndex(),
                [app_units.DIMENSIONS['length'].unit(sym).factor
                 for _t, sym in dia_units[3]]
            )
            
            # Area for drag = body tube cross-sectional area
            body_radius_m = body_diameter_m / 2
            body_area = np.pi * body_radius_m**2
            
            total_area = body_area

            # Set the main area input field (in the current unit). Twelve
            # significant figures rather than six decimals: at six, a small
            # area in ft2 lost most of its precision on every rewrite.
            area_units = self._SIM_UNITS['area']
            symbol = area_units[3][self.area_unit.currentIndex()][1]
            area_in_current_unit = app_units.DIMENSIONS['area'].unit(
                symbol).from_si(total_area)
            self.area_input.setText(f"{area_in_current_unit:.12g}")
            # Show the START of the number. setText leaves the cursor at the
            # end, so a value too long for the box scrolls and the user sees
            # its TAIL: 0.0153938040026 m2 displayed as "153938040026", which
            # reads as an area of 1.5e11 m2. The value was always right; what
            # was on screen was not.
            self.area_input.setCursorPosition(0)
        except (ValueError, ZeroDivisionError):
            self.area_input.setText("0")

    def update_air_density(self):
        try:
            altitude = float(self.start_altitude_input.text())
            temp_c = float(self.temperature_input.text())
            humidity = float(self.humidity_input.text())
            # Calculate pressure at altitude (barometric formula, simplified)
            P0 = 101325  # Pa at sea level
            T0 = 288.15  # K at sea level
            L = 0.0065   # K/m
            R = 287.05   # J/(kg·K)
            g = 9.80665  # m/s²
            temp_k = temp_c + 273.15
            # Pressure at altitude
            P = P0 * (1 - L * altitude / T0) ** (g / (R * L))
            # Saturation vapor pressure (Tetens formula)
            Es = 6.1078 * 10 ** ((7.5 * temp_c) / (237.3 + temp_c))
            # Actual vapor pressure
            E = Es * humidity / 100.0
            # Calculate air density
            rho = (P - E) / (R * temp_k) + (E / (461.495 * temp_k))
            self.rho_input.setText(f"{rho:.3f}")
        except Exception:
            pass

    # Profile Management Methods
    def _legacy_airframe_config(self, config):
        """An airframe for a profile saved before airframes existed.

        Those profiles predate the Aerodynamics tab but they do carry the
        shape: body diameter and fin geometry on the Simulation tab, overall
        length on the Stability tab. Building the airframe from those makes
        the rocket fly as ITSELF. The alternative - leaving the previous
        rocket's airframe in place - silently flies one rocket's motor on
        another rocket's body.

        What cannot be recovered is given a plain, stated default: a nose one
        and a half calibers long and the rest body, which is an ordinary
        slender layout rather than a claim about this particular vehicle.
        """
        rp = (config or {}).get('rocket_parameters') or {}
        st = (config or {}).get('stability_settings') or {}

        def si(key, unit_key, table, fallback):
            """A legacy field plus its stored unit index, in SI."""
            try:
                value = float(rp.get(key))
            except (TypeError, ValueError):
                return fallback
            idx = rp.get(unit_key, 0)
            try:
                factor = table[int(idx)]
            except (TypeError, ValueError, IndexError):
                factor = table[0]
            return value * factor

        # These index the Simulation tab's own combos, so they must match them
        # exactly: ["m", "mm", "in"] and ["kg", "g", "lb"]. This said cm for
        # index 1 where the combo says mm, so every legacy profile storing a
        # millimetre loaded ten times too big - a 76.2 mm body came in as
        # 762 mm. Read the combo, do not assume the order.
        LEN = [1.0, 0.001, 0.0254]         # m, mm, in - matches the combo
        diameter = si('body_diameter', 'body_diameter_unit', LEN, 0.10)
        fin_len = si('fin_length', 'fin_length_unit', LEN, diameter)
        fin_thick = si('fin_thickness', 'fin_thickness_unit', LEN, 0.003)
        try:
            total_length = float(st.get('rocket_length') or 0.0)
        except (TypeError, ValueError):
            total_length = 0.0
        if total_length <= 0:
            total_length = max(10.0 * diameter, 1.0)
        nose_length = min(1.5 * diameter, 0.45 * total_length)
        body_length = max(0.1, total_length - nose_length)
        try:
            fin_count = int(float(rp.get('fin_count') or 3))
        except (TypeError, ValueError):
            fin_count = 3

        # The profile's OWN drag coefficient wins over the buildup. The
        # geometry above is partly invented - nothing in a legacy profile says
        # how long the nose is - so letting a guessed shape set the drag would
        # replace the one number the profile actually states about it. This
        # rocket says Cd 0.75; flying the shape instead put it at a fifth of
        # the altitude its own figures give.
        try:
            cd = float(rp.get('cd') or 0.0)
        except (TypeError, ValueError):
            cd = 0.0

        lc = (config or {}).get('launch_conditions') or {}
        ws = (config or {}).get('wind_settings') or {}
        st_angle = st.get('launch_angle', 0.0)

        def number(value, fallback):
            try:
                return float(value)
            except (TypeError, ValueError):
                return fallback

        MASS = [1.0, 0.001, 0.45359237]    # kg, g, lb - matches the combo
        liftoff = si('mass', 'mass_unit', MASS, 0.0)
        prop_mass = si('prop_mass', 'prop_mass_unit', MASS, 0.0)
        if prop_mass >= liftoff > 0:
            prop_mass = 0.0                    # nonsense; treat as unstated
        dry_mass = max(0.001, liftoff - prop_mass) if liftoff > 0 else 1.0
        try:
            cg_m = float(st.get('center_of_mass') or 0.0)
        except (TypeError, ValueError):
            cg_m = 0.0

        # Launch conditions come from the profile too, so the site does not
        # stay set to the previous rocket's field and wind.

        # Start from a COMPLETE set of defaults, then overlay what the
        # profile gives. Listing only the fields that can be derived leaves
        # the rest inheriting from the previous rocket - surface roughness,
        # boattail, latitude, wind shear - and an exhaustive load-order test
        # caught the flight moving by 0.7 ft depending on what had been loaded
        # before it. Small, but it is the same leak in miniature, and the
        # point of this change is that a rocket gives one answer.
        fields = {}
        for source in (aero.Airframe(), atmosphere_mod.LaunchSite()):
            for attr in dir(source):
                if attr.startswith('_'):
                    continue
                value = getattr(source, attr, None)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    fields[attr] = value
        fields.update({"fin_count": 3, "cd_override": 0.0,
                       "dry_mass_kg": 1.0, "propellant_mass_kg": 0.0,
                       "dry_cg_m": 0.0, "propellant_cg_m": 0.0})
        # Wind direction reads off the profile like the speed does. It is not
        # a LaunchSite float that the defaults sweep picks up automatically,
        # so leaving it out silently reset a legacy profile's 270 deg to 0.
        fields["wind_dir_deg"] = number(ws.get('wind_direction'), 0.0)
        fields.update({
                "nose_length_m": nose_length,
                "body_diameter_m": diameter,
                "body_length_m": body_length,
                "fin_count": fin_count,
                "fin_root_chord_m": max(fin_len, 1e-3),
                "fin_tip_chord_m": max(fin_len * 0.5, 1e-3),
                "fin_span_m": max(fin_len * 0.5, 1e-3),
                "fin_sweep_m": max(fin_len * 0.5, 0.0),
                "fin_thickness_m": max(fin_thick, 1e-4),
                "cd_override": cd if cd > 0 else 0.0,
                # Mass, from the profile's own figures. Without these the
                # vehicle keeps whatever dry mass was there before - the
                # Simulation tab only pushes its mass down when a propellant
                # mass is given too, so a legacy profile with no propellant
                # figure flew at the default 20 kg. That put a 5 kg rocket at
                # 1,975 ft on a motor that takes it past 15,000.
                "dry_mass_kg": max(0.001, dry_mass),
                "propellant_mass_kg": max(0.0, prop_mass),
                "dry_cg_m": cg_m if cg_m > 0 else total_length * 0.55,
                "propellant_cg_m": (total_length * 0.80
                                    if cg_m <= 0 else min(total_length,
                                                          cg_m + 0.25 * total_length)),
                "elevation_m": number(lc.get('start_altitude'), 0.0),
                "temperature_c": number(lc.get('temperature'), 15.0),
                "humidity_pct": number(lc.get('humidity'), 0.0),
                "wind_speed_ms": number(ws.get('wind_speed'), 0.0),
                "rail_angle_deg": number(st_angle, 0.0),
        })
        return {
            "_units": "storage",
            "fields": fields,
            # No components: the legacy mass is a single typed number, and a
            # stale buildup from the previous rocket would override it.
            "mass_components": [],
            # Recovery from the profile's own parachute fields. Without this
            # the PREVIOUS rocket's canopies stayed rigged, so a legacy rocket
            # came down under someone else's main.
            "recovery": self._legacy_recovery_config(config),
        }

    def _legacy_recovery_config(self, config):
        """A recovery system from a legacy profile's parachute fields."""
        rp = (config or {}).get('rocket_parameters') or {}

        def number(value, fallback=0.0):
            try:
                return float(value)
            except (TypeError, ValueError):
                return fallback

        # chute_size is stored as an AREA in the legacy format; the recovery
        # model wants a diameter.
        area = number(rp.get('chute_size'))
        diameter = math.sqrt(4.0 * area / math.pi) if area > 0 else 0.0
        deploy_alt = number(rp.get('chute_height'))
        cd = number(rp.get('chute_cd'), 1.5) or 1.5
        if diameter <= 0:
            return recovery_mod.RecoverySystem().to_dict()
        return recovery_mod.RecoverySystem.single_deploy(
            diameter_m=diameter, cd=cd, altitude_m=deploy_alt).to_dict()

    def _apply_section(self, tab, name, stored, legacy=None):
        """Load one section so nothing of the previous rocket survives it.

        Two passes, because a section that is PRESENT is not a section that is
        COMPLETE. The v1.0 presets carry a vehicle section holding one key,
        target_altitude_ft; applying only that left body length, wall
        thicknesses, materials and safety factors at the previous rocket's
        value. So:

          1. a complete base - what the tab held before any rocket was
             loaded, with whatever this profile's older fields imply laid
             over it - which puts every field the tab owns at a known value;
          2. the section as actually stored, over the top.

        The two are applied separately rather than merged into one dict
        because they do not share a unit convention. `_units` is the marker
        that says which convention a section's numbers are written in, so it
        belongs to the layer its numbers came from. Merging it across layers
        hands a profile's numbers to the wrong reader: dropping an SI marker
        onto the stored v2.0 sections, which carry none, read every one of
        their metres as a display figure and loaded a 98 mm body as 98 m.
        """
        base = dict((getattr(self, '_pristine_sections', None) or {}).get(name)
                    or {})
        if legacy:
            base.update(legacy)
        if base:
            tab.apply_config(base)
        if stored:
            tab.apply_config(stored)

    def _legacy_vehicle_config(self, config):
        """A goal for a profile that carries none.

        The target altitude is what the Flight Report grades against, and it
        was surviving every rocket load - so a rocket was marked as falling
        short of a goal that belonged to whatever was loaded before it. A
        profile with no goal of its own gets the app default rather than
        inheriting one.
        """
        # Every vehicle field, at its default, not just the target. The old
        # version asked for a default_config() that does not exist, so the
        # hasattr was always False and this returned a dict carrying ONLY the
        # target altitude - every other vehicle field (materials, wall
        # thicknesses, rail length, safety factors) went on leaking from the
        # previous rocket, which is exactly the leak it was written to close.
        defaults = fa.VehicleConfig()
        cfg = {}
        for name in dir(defaults):
            if name.startswith('_'):
                continue
            value = getattr(defaults, name, None)
            if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                cfg[name] = value
        cfg['target_altitude_ft'] = 50000.0
        cfg['goals'] = []
        return cfg

    def get_profiles_dir(self):
        """The writable directory new rocket profiles are saved to."""
        return portable_paths.profiles_dir()

    def get_profile_search_dirs(self):
        """Every directory to read rockets from.

        The writable one first, then any examples shipped alongside the code -
        in a packaged build those live in the bundle, which is not writable.
        """
        dirs = [self.get_profiles_dir()]
        # Shipped presets travel inside the program. __file__ does not point
        # into the bundle in a packaged build, so ask portable_paths, which
        # knows where PyInstaller put them.
        for bundled in (portable_paths.bundled_dir('profiles'),
                        portable_paths.bundled_dir('src', 'profiles')):
            if os.path.isdir(bundled) and bundled not in dirs:
                dirs.append(bundled)
        return dirs

    def refresh_profile_dropdown(self):
        """Keep the Settings tab's profile dropdown in step with the library.

        Signals are blocked so repopulating the list does not fire a profile
        load and overwrite what the user just saved.
        """
        if not hasattr(self, 'profile_select'):
            return
        current = self.profile_select.currentText()
        self.profile_select.blockSignals(True)
        try:
            self.load_available_profiles()
            idx = self.profile_select.findText(current)
            if idx >= 0:
                self.profile_select.setCurrentIndex(idx)
        finally:
            self.profile_select.blockSignals(False)

    def on_rocket_loaded(self, name, config):
        """A rocket was loaded from the library: show the result on Simulation."""
        self.refresh_profile_dropdown()
        summary = []
        if config.get('engine'):
            summary.append("engine")
        if config.get('vehicle'):
            summary.append("materials")
        extra = (" (including " + " and ".join(summary) + ")") if summary else ""
        self.result_label.setText(
            f"Now running <b>{name}</b>{extra}. Inputs below are populated from "
            f"this rocket - press Start Simulation to fly it.")
        self.tabs.setCurrentWidget(self.main_panel)

    def resolve_thrust_curve(self, path):
        """Turn a stored thrust-curve path into one that exists on this machine.

        Presets that ship with the app record a path relative to the project so
        they survive being unzipped anywhere; a path the user chose is already
        absolute.
        """
        if not path:
            return None
        if os.path.isabs(path) and os.path.exists(path):
            return path
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for base in (project_root, os.path.dirname(os.path.abspath(__file__))):
            candidate = os.path.normpath(os.path.join(base, path))
            if os.path.exists(candidate):
                return candidate
        return path if os.path.exists(path) else None

    def find_profile_path(self, filename):
        """Locate a profile file across every search directory."""
        for directory in self.get_profile_search_dirs():
            candidate = os.path.join(directory, filename)
            if os.path.isfile(candidate):
                return candidate
        return None

    def get_current_configuration(self):
        """Get current rocket configuration as a dictionary"""
        config = {
            'version': '2.0',   # 2.0 adds the engine and vehicle sections
            'name': 'Unnamed Profile',
            'description': '',
            'created': QtCore.QDateTime.currentDateTime().toString(),
            'rocket_parameters': {
                'mass': self.mass_input.text(),
                'mass_unit': self.mass_unit.currentIndex(),
                'prop_mass': self.prop_mass_input.text(),
                'prop_mass_unit': self.prop_mass_unit.currentIndex(),
                'cd': self.cd_input.text(),
                'area': self.area_input.text(),
                'area_unit': self.area_unit.currentIndex(),
                'rho': self.rho_input.text(),
                'rho_unit': self.rho_unit.currentIndex(),
                'timestep': self.timestep_input.text(),
                'timestep_unit': self.timestep_unit.currentIndex(),
                'fin_count': self.fin_count_input.text(),
                'fin_thickness': self.fin_thickness_input.text(),
                'fin_thickness_unit': self.fin_thickness_unit.currentIndex(),
                'fin_length': self.fin_length_input.text(),
                'fin_length_unit': self.fin_length_unit.currentIndex(),
                'body_diameter': self.body_diameter_input.text(),
                'body_diameter_unit': self.body_diameter_unit.currentIndex(),
                'chute_height': self.chute_height_input.text(),
                'chute_height_unit': self.chute_height_unit.currentIndex(),
                'chute_size': self.chute_size_input.text(),
                'chute_size_unit': self.chute_size_unit.currentIndex(),
                'chute_cd': self.chute_cd_input.text(),
            },
            'launch_conditions': {
                'start_altitude': self.start_altitude_input.text(),
                'temperature': self.temperature_input.text(),
                'humidity': self.humidity_input.text(),
            },
            'stability_settings': {
                'rocket_length': self.rocket_length_input.value(),
                'center_of_mass': self.center_of_mass_input.value(),
                'center_of_pressure': self.center_of_pressure_input.value(),
                'launch_angle': self.launch_angle_input.value(),
            },
            'wind_settings': {
                'wind_speed': self.wind_speed_input.value(),
                'wind_direction': self.wind_direction_input.value(),
            },
            'thrust_curve_path': getattr(self, 'thrust_curve_path', None)
        }
        # The engine design and the materials/structure the failure analysis
        # grades against are part of the rocket too, not just the flight inputs.
        if hasattr(self, 'engine_lab'):
            config['engine'] = self.engine_lab.get_config()
        if hasattr(self, 'flight_report'):
            config['vehicle'] = self.flight_report.get_config()
        if hasattr(self, 'vehicle_tab'):
            config['airframe'] = self.vehicle_tab.get_config()
        # Whether the stability figures were typed rather than read from the
        # rocket, so a deliberate override is not silently lost on reload.
        config['stability_override'] = {
            'enabled': bool(self.stability_override.isChecked()),
            'rocket_length_m': self.rocket_length_input.value(),
            'center_of_mass_m': self.center_of_mass_input.value(),
            'center_of_pressure_m': self.center_of_pressure_input.value(),
        }
        # A rocket is a pairing. Record which named designs it was assembled
        # from, so the Simulation tab can show the pairing again on reload.
        # The full configs above are still stored, so a rocket keeps working
        # even if one of its component designs is later renamed or deleted.
        if hasattr(self, 'assembly'):
            engine_name, aero_name = self.assembly.selection()
            config['assembly'] = {'engine': engine_name,
                                  'aerodynamics': aero_name}
        return config

    def apply_configuration(self, config):
        """Apply a configuration to the current inputs"""
        try:
            # The flight on screen belongs to whatever was loaded before. It
            # is not this rocket, so it goes before anything else changes -
            # otherwise the new vehicle's configuration sits beside the old
            # vehicle's apogee and the two disagree.
            name = (config or {}).get('name') or 'this rocket'
            self.invalidate_flight_results(
                f"Loaded {name} - run a simulation to see its flight.")
            # The Stability Test tab's length, CG and CP belong to the rocket,
            # so they are re-read once this configuration is in. Deferred to
            # the end of the call, below, when the vehicle tab has been filled.
            self._resync_stability_after_load = True
            asm = config.get('assembly') or {}
            if asm and hasattr(self, 'assembly'):
                # Show the pairing this rocket was built from. The stored
                # engine/airframe configs below still win, so a rocket loads
                # correctly whether or not those named designs still exist.
                self.assembly.refresh()
                self.assembly.set_selection(asm.get('engine'),
                                            asm.get('aerodynamics'))
            # Rocket parameters. The unit goes in FIRST and the value second,
            # with the conversion slot held off for the whole load: the stored
            # number is already expressed in the stored unit, so there is
            # nothing to convert.
            rp = config.get('rocket_parameters', {})
            self._loading_profile = True
            try:
                for edit, combo, key, unit_key in (
                    (self.mass_input, self.mass_unit, 'mass', 'mass_unit'),
                    (self.prop_mass_input, self.prop_mass_unit,
                     'prop_mass', 'prop_mass_unit'),
                    (self.area_input, self.area_unit, 'area', 'area_unit'),
                    (self.rho_input, self.rho_unit, 'rho', 'rho_unit'),
                    (self.timestep_input, self.timestep_unit,
                     'timestep', 'timestep_unit'),
                    (self.fin_thickness_input, self.fin_thickness_unit,
                     'fin_thickness', 'fin_thickness_unit'),
                    (self.fin_length_input, self.fin_length_unit,
                     'fin_length', 'fin_length_unit'),
                    (self.body_diameter_input, self.body_diameter_unit,
                     'body_diameter', 'body_diameter_unit'),
                    (self.chute_height_input, self.chute_height_unit,
                     'chute_height', 'chute_height_unit'),
                    (self.chute_size_input, self.chute_size_unit,
                     'chute_size', 'chute_size_unit'),
                ):
                    if combo is not None:
                        combo.setCurrentIndex(rp.get(unit_key, 0))
                    edit.setText(str(rp.get(key, '')))
                self.cd_input.setText(rp.get('cd', ''))
                self.fin_count_input.setText(rp.get('fin_count', ''))
                self.chute_cd_input.setText(rp.get('chute_cd', ''))
            finally:
                self._loading_profile = False
                self._sync_unit_indices()

            # Launch conditions
            lc = config.get('launch_conditions', {})
            self.start_altitude_input.setText(lc.get('start_altitude', ''))
            self.temperature_input.setText(lc.get('temperature', ''))
            self.humidity_input.setText(lc.get('humidity', ''))

            # Stability settings
            ss = config.get('stability_settings', {})
            self.rocket_length_input.setValue(ss.get('rocket_length', 1.0))
            self.center_of_mass_input.setValue(ss.get('center_of_mass', 0.5))
            self.center_of_pressure_input.setValue(ss.get('center_of_pressure', 0.7))
            self.launch_angle_input.setValue(ss.get('launch_angle', 0.0))

            # Wind settings
            ws = config.get('wind_settings', {})
            self.wind_speed_input.setValue(ws.get('wind_speed', 0.0))
            self.wind_direction_input.setValue(ws.get('wind_direction', 0))

            # Engine design, goal and airframe.
            #
            # These used to be skipped when a profile did not carry them, on
            # the reasoning that an older profile predates the sections and
            # leaving those tabs alone is harmless. It is not: the PREVIOUS
            # rocket's airframe stays loaded and the new rocket's motor is
            # flown on it. Load a 76 mm supersonic airframe, then load a
            # different rocket that has no airframe section, and you get its
            # motor on the other rocket's body - 9,349 ft for a vehicle that
            # flies 5,198 ft as itself, with a goal inherited from a third
            # state. That is how one rocket reports three different apogees.
            #
            # And a section that is PRESENT is not one that is COMPLETE: the
            # v1.0 presets carry a vehicle section holding a single key,
            # target_altitude_ft, so applying only what is stored left body
            # length, wall thicknesses, materials and safety factors at the
            # previous rocket's value - a 2.5 m rocket kept a 3.1 m body from
            # whatever was loaded before it. A profile with no engine section
            # at all kept the previous motor outright, grain and tank and
            # injector: the same failure by another route.
            #
            # So every section is laid over a complete base rather than
            # trusted whole. See _apply_section.
            if hasattr(self, 'engine_lab'):
                self._apply_section(self.engine_lab, 'engine',
                                    config.get('engine'))

            if hasattr(self, 'flight_report'):
                self._apply_section(self.flight_report, 'vehicle',
                                    config.get('vehicle'),
                                    self._legacy_vehicle_config(config))

            if hasattr(self, 'vehicle_tab'):
                self._apply_section(self.vehicle_tab, 'airframe',
                                    config.get('airframe'),
                                    self._legacy_airframe_config(config))

            # Thrust curve. Shipped presets store a project-relative path so
            # they work on any machine; anything the user picked themselves is
            # absolute and resolves directly.
            # An imported drag curve belongs to the rocket it was imported
            # for. It was surviving every rocket load and is saved with none
            # of them, so a stale flat Cd=1.90 table silently dropped the L550
            # preset from 15,955 ft to 7,097 ft.
            if hasattr(self, 'aero_tab') and self.aero_tab.current_table():
                self.aero_tab.clear_table()

            stored_curve = config.get('thrust_curve_path')
            thrust_path = self.resolve_thrust_curve(stored_curve)
            if thrust_path and os.path.exists(thrust_path):
                self.thrust_curve_path = thrust_path
                self.result_label.setText(f"Loaded profile thrust curve: {os.path.basename(thrust_path)}")
            else:
                # Clear it. Leaving the previous rocket's motor loaded meant a
                # profile with no curve - or one whose file has moved - flew
                # somebody else's engine and reported success.
                self.thrust_curve_path = None
                self.engine_lab_curve_path = None
                if stored_curve:
                    self.error_label.setText(
                        f"This rocket's thrust curve is missing "
                        f"({os.path.basename(str(stored_curve))}). No motor is "
                        f"loaded - pick a curve before running.")
                else:
                    self.result_label.setText(
                        "This rocket has no thrust curve saved. Load one, or "
                        "send a motor across from the Engine tab.")

            # The vehicle is in place now, so the Stability Test tab can read
            # its length, CG and CP off it.
            if getattr(self, '_resync_stability_after_load', False):
                self._resync_stability_after_load = False
                try:
                    saved = (config or {}).get('stability_override') or {}
                    if saved.get('enabled'):
                        # A deliberate override is restored as typed, rather
                        # than being overwritten by the rocket's own figures.
                        self.stability_override.setChecked(True)
                        for field, key in (
                                (self.rocket_length_input, 'rocket_length_m'),
                                (self.center_of_mass_input, 'center_of_mass_m'),
                                (self.center_of_pressure_input,
                                 'center_of_pressure_m')):
                            if saved.get(key):
                                field.set_si_value(saved[key])
                        self.refresh_stability_margin()
                    else:
                        self.stability_override.setChecked(False)
                        self.sync_stability_from_vehicle(force=True)
                except Exception:
                    traceback.print_exc()

            return True
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Profile Load Error", f"Error applying profile configuration: {str(e)}")
            return False

    def load_available_profiles(self):
        """Load available profiles into the dropdown"""
        self.profile_select.clear()
        self.profile_select.addItem("Default", None)

        seen = set()
        for profiles_dir in self.get_profile_search_dirs():
            try:
                filenames = sorted(os.listdir(profiles_dir))
            except OSError:
                continue
            for filename in filenames:
                if filename.endswith('.json') and filename not in seen:
                    seen.add(filename)
                    profile_name = filename[:-5]  # Remove .json extension
                    self.profile_select.addItem(profile_name, filename)

    def save_current_profile(self):
        """Save current configuration as a new profile"""
        profile_name, ok = QtWidgets.QInputDialog.getText(
            self, 'Save Profile', 
            'Enter a name for this rocket configuration profile:',
            QtWidgets.QLineEdit.Normal, 
            'My Rocket'
        )
        
        if ok and profile_name.strip():
            profile_name = profile_name.strip()
            
            # Get profile description
            description, ok2 = QtWidgets.QInputDialog.getText(
                self, 'Profile Description', 
                'Enter a description (optional):',
                QtWidgets.QLineEdit.Normal, 
                ''
            )
            
            config = self.get_current_configuration()
            config['name'] = profile_name
            config['description'] = description if ok2 else ''
            
            # Save to file
            profiles_dir = self.get_profiles_dir()
            # Sanitize filename
            safe_name = "".join(c for c in profile_name if c.isalnum() or c in (' ', '-', '_')).strip()
            filename = f"{safe_name}.json"
            filepath = os.path.join(profiles_dir, filename)
            
            try:
                with open(filepath, 'w') as f:
                    json.dump(config, f, indent=2)
                
                # Reload dropdown and select new profile
                self.load_available_profiles()
                index = self.profile_select.findText(profile_name)
                if index >= 0:
                    self.profile_select.setCurrentIndex(index)
                
                QtWidgets.QMessageBox.information(self, "Profile Saved", f"Profile '{profile_name}' saved successfully!")
                
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Save Error", f"Failed to save profile: {str(e)}")

    def load_profile(self, profile_name):
        """Load a profile by name"""
        if not profile_name or profile_name == "Default":
            return
            
        # Find the corresponding filename
        filename = None
        for i in range(self.profile_select.count()):
            if self.profile_select.itemText(i) == profile_name:
                filename = self.profile_select.itemData(i)
                break
        
        if not filename:
            return
            
        filepath = self.find_profile_path(filename)
        if not filepath:
            return

        try:
            with open(filepath, 'r') as f:
                config = json.load(f)

            if self.apply_configuration(config):
                self.result_label.setText(f"Loaded profile: {profile_name}")
                
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load Error", f"Failed to load profile '{profile_name}': {str(e)}")

    def delete_current_profile(self):
        """Delete the currently selected profile"""
        current_profile = self.profile_select.currentText()
        if current_profile == "Default":
            QtWidgets.QMessageBox.information(self, "Cannot Delete", "Cannot delete the default profile.")
            return
            
        reply = QtWidgets.QMessageBox.question(
            self, 'Delete Profile', 
            f"Are you sure you want to delete the profile '{current_profile}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        
        if reply == QtWidgets.QMessageBox.Yes:
            filename = self.profile_select.currentData()
            if filename:
                # The dropdown lists every search directory, but only the
                # user's own is writable. Resolving against get_profiles_dir()
                # alone raised FileNotFoundError on a bundled preset in a
                # frozen build instead of refusing cleanly.
                writable = self.get_profiles_dir()
                filepath = os.path.join(writable, filename)
                if not os.path.exists(filepath):
                    found = next(
                        (os.path.join(d, filename)
                         for d in self.get_profile_search_dirs()
                         if os.path.exists(os.path.join(d, filename))), None)
                    if found:
                        QtWidgets.QMessageBox.information(
                            self, "Bundled profile",
                            f"'{current_profile}' ships with JARVIS and cannot "
                            f"be deleted. Save a copy under your own name if "
                            f"you want a version to change or remove.")
                        return
                    QtWidgets.QMessageBox.warning(
                        self, "Not found",
                        f"'{current_profile}' is no longer on disk.")
                    self.load_available_profiles()
                    return

                try:
                    os.remove(filepath)
                    self.load_available_profiles()
                    self.profile_select.setCurrentIndex(0)  # Select Default
                    QtWidgets.QMessageBox.information(self, "Profile Deleted", f"Profile '{current_profile}' deleted successfully!")
                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, "Delete Error", f"Failed to delete profile: {str(e)}")

    def export_profile(self):
        """Export current profile to a file"""
        profile_name = self.profile_select.currentText()
        if profile_name == "Default":
            profile_name = "rocket_config"
            
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Profile", 
            f"{profile_name}.json",
            "JSON Files (*.json);;All Files (*)"
        )
        
        if filename:
            config = self.get_current_configuration()
            try:
                with open(filename, 'w') as f:
                    json.dump(config, f, indent=2)
                QtWidgets.QMessageBox.information(self, "Export Success", f"Profile exported to {filename}")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Export Error", f"Failed to export profile: {str(e)}")

    def import_profile(self):
        """Import a profile from a file"""
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Profile", 
            "",
            "JSON Files (*.json);;All Files (*)"
        )
        
        if filename:
            try:
                with open(filename, 'r') as f:
                    config = json.load(f)
                
                # Ask for new profile name
                import_name = os.path.splitext(os.path.basename(filename))[0]
                profile_name, ok = QtWidgets.QInputDialog.getText(
                    self, 'Import Profile', 
                    'Enter name for imported profile:',
                    QtWidgets.QLineEdit.Normal, 
                    import_name
                )
                
                if ok and profile_name.strip():
                    config['name'] = profile_name.strip()
                    
                    # Save to profiles directory
                    profiles_dir = self.get_profiles_dir()
                    safe_name = "".join(c for c in profile_name if c.isalnum() or c in (' ', '-', '_')).strip()
                    new_filename = f"{safe_name}.json"
                    new_filepath = os.path.join(profiles_dir, new_filename)
                    
                    with open(new_filepath, 'w') as f:
                        json.dump(config, f, indent=2)
                    
                    # Reload dropdown and apply
                    self.load_available_profiles()
                    index = self.profile_select.findText(profile_name)
                    if index >= 0:
                        self.profile_select.setCurrentIndex(index)
                        self.apply_configuration(config)
                    
                    QtWidgets.QMessageBox.information(self, "Import Success", f"Profile '{profile_name}' imported successfully!")
                    
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Import Error", f"Failed to import profile: {str(e)}")


if __name__ == "__main__":
    # High-DPI support. Without this a Qt app renders at 100% and Windows
    # bitmap-stretches it to the display scaling (125%/150% on most laptops and
    # any 4K monitor), which is what makes the text look small and blurry.
    # These must be set before the QApplication is constructed.
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    try:
        # Fractional scaling (125%, 150%) needs pass-through rounding, or Qt
        # snaps to whole integers and everything is off by 25%.
        QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except AttributeError:
        pass  # Qt < 5.14

    app = QtWidgets.QApplication(sys.argv)
    # Global stylesheet removed; theming is applied per-widget via apply_theme

    # Base UI font in points, so it follows the system DPI instead of being
    # locked to a pixel height.
    base_font = app.font()
    base_font.setPointSizeF(max(base_font.pointSizeF(), 10.0))
    app.setFont(base_font)

    # Dark futuristic theme, applied once for every widget in the app.
    app.setStyleSheet(app_theme.stylesheet())

    # Splash screen with GIF animation
    gif_path = os.path.join(os.path.dirname(__file__), 'jarvis.gif')
    splash_movie = QtGui.QMovie(gif_path)
    
    if splash_movie.isValid():
        splash_label = QtWidgets.QLabel()
        splash_label.setMovie(splash_movie)
        splash_label.setWindowFlags(QtCore.Qt.SplashScreen | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
        splash_label.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        splash_label.setAlignment(QtCore.Qt.AlignCenter)
        
        # Center the splash screen
        screen = app.primaryScreen().geometry()
        splash_label.resize(400, 300)  # Set appropriate size
        splash_label.move((screen.width() - 400) // 2, (screen.height() - 300) // 2)
        
        # Start animation and show
        splash_movie.start()
        splash_label.movie = splash_movie  # Prevent garbage collection
        splash_label.show()
        app.processEvents()
        
        # Show splash for 4 seconds, then show main window
        def show_main():
            splash_label.close()
            splash_movie.stop()
            window.show()
        
        window = RocketSimulationUI()
        QtCore.QTimer.singleShot(4000, show_main)
    else:
        print(f"Could not load splash GIF: {gif_path}")
        # Show main window immediately if GIF fails to load
        window = RocketSimulationUI()
        window.show()

    sys.exit(app.exec_())

def excepthook(type_, value, tb):
    error_text = ''.join(traceback.format_exception(type_, value, tb))
    crash_image_path = os.path.join(os.path.dirname(__file__), 'crash.jpg')
    dlg = CrashImageDialog(crash_image_path, error_text)
    dlg.exec_()
    sys.exit(1)

sys.excepthook = excepthook


