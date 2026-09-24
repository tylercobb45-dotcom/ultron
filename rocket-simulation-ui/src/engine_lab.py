"""Engine Lab: a PyQt5 tab for designing a custom N2O/HTPB-family hybrid rocket
engine and turning it into a thrust curve the rest of JARVIS can fly.

Physics comes entirely from the vendored ``hybrid_sim`` package (tank
blowdown, Dyer NHNE injector, Marxman fuel regression, CEA c* table,
isentropic nozzle with Summerfield separation) - nothing in that package is
modified here. This module only adds a PyQt5 front end around it:

    * a form for the engine's tank / injector / fuel grain / nozzle geometry
    * preset motors to start from (the Goddard baseline, the hybrid_sim
      reference case, and the two
      HyperTEK motors hybrid_sim validates against)
    * an embedded thrust/pressure/O-F/mdot plot and a metrics readout
    * an optional quick apogee/velocity estimate (hybrid_sim's own 1-DOF
      flight model) so you get feedback before leaving this tab
    * "Send to Simulation", which writes the generated thrust curve to a
      CSV in the format the existing Simulation tab already knows how to
      load, and hands it back to the caller via a callback.
"""
from __future__ import annotations

import os
import sys
import time
import traceback

from PyQt5 import QtWidgets, QtCore
import theme
import rasp
import unit_fields
import portable_paths
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

# The hybrid_sim package lives in a sibling folder (rocket-simulation-ui/hybrid_sim)
# so it can be dropped in / updated independently of the app. Make it importable.
def _hybrid_sim_root():
    """Where the hybrid_sim package lives, running from source or frozen.

    Under PyInstaller the source tree is gone; the bundle unpacks to _MEIPASS
    (onefile) or sits beside the executable (onedir). The build ships the
    package as data under a 'hybrid_sim' folder in both cases.
    """
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', None) or os.path.dirname(sys.executable)
        return os.path.join(base, 'hybrid_sim')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hybrid_sim')


_HYBRID_SIM_ROOT = _hybrid_sim_root()
if os.path.isdir(_HYBRID_SIM_ROOT) and _HYBRID_SIM_ROOT not in sys.path:
    sys.path.insert(0, _HYBRID_SIM_ROOT)


def _generated_curves_dir():
    """Where to save generated thrust curves - beside the program, so a
    motor designed on one computer is still there on the next."""
    return portable_paths.generated_curves_dir()

from hybrid_sim import Engine, Rocket, EngineModel, FlightModel, FUELS, metrics as hs_metrics  # noqa: E402
from hybrid_sim.config import INJECTOR_TYPES  # noqa: E402
import motor_designer  # noqa: E402  (after the hybrid_sim path insert)

# (label, Engine field, display factor [shown = field * factor], decimals, tooltip)
# The form is generated from these lists, so adding a field to the physics and
# a line here is all it takes to make it editable.
_TANK_FIELDS = [
    ("Tank diameter (mm)", "d_tank", 1000.0, 2,
     "Internal diameter of the oxidizer tank."),
    ("Tank length (mm)", "L_tank", 1000.0, 2,
     "Internal length. With the diameter this sets tank volume, which sets "
     "how much N2O you are carrying."),
    ("Fill fraction (%)", "fill_frac", 100.0, 1,
     "Fraction of tank volume filled with LIQUID at ignition. The rest is "
     "ullage vapour. Above ~95% there is no room for thermal expansion."),
    ("Initial tank temp (K)", "T_tank_0", 1.0, 1,
     "N2O is self-pressurizing: tank pressure IS its saturation pressure at "
     "this temperature. 293 K gives about 5.0 MPa (730 psi). A hot pad day "
     "raises pressure and thrust; a cold one drops both."),
    ("Vent orifice diameter (mm)", "d_vent", 1000.0, 2,
     "Vent bleeding vapour overboard during the burn. 0 = vent closed. "
     "Venting costs oxidizer and Isp but keeps tank pressure in check."),
    ("Vent Cd", "Cd_vent", 1.0, 2,
     "Discharge coefficient of the vent orifice."),
    ("Tank cooling coefficient", "cooling_coeff", 1.0, 4,
     "How much of the boil-off latent heat comes out of the LIQUID rather "
     "than being fed back by the tank walls and the air around them. It sets "
     "how fast the tank cools, and so how steeply thrust decays: high values "
     "give a peaky curve, low values a flat one. Small tanks have more wall "
     "area per kg of liquid and hold pressure up better, so they want a "
     "lower number. 0 = use the solver default (0.16)."),
]
_INJ_FIELDS = [
    ("Number of holes", "n_holes", 1.0, 0,
     "Orifices in the injector. HyperTEK-style injector bells use ONE "
     "interchangeable orifice; showerhead plates use many."),
    ("Hole diameter (mm)", "d_hole", 1000.0, 3,
     "Diameter of each orifice. Total injector area is what sets oxidizer "
     "flow, and therefore burn time."),
    ("Injector Cd", "Cd_inj", 1.0, 4,
     "Discharge coefficient. Sharp-edged drilled holes ~0.6-0.7, "
     "well-rounded ~0.8, swirl injectors much lower."),
]
_GRAIN_FIELDS = [
    ("Grain length (mm)", "L_grain", 1000.0, 1,
     "Length of the fuel grain. Sets burn area, and with it fuel flow."),
    ("Grain outer diameter (mm)", "d_grain_outer", 1000.0, 1,
     "Outer diameter of the fuel. The burn ends when the port reaches this."),
    ("Initial port diameter (mm)", "d_port_0", 1000.0, 1,
     "Starting bore. Small ports give high oxidizer flux and fast regression "
     "(low O/F); large ports start fuel-lean."),
    ("Regression coeff a (0 = fuel default)", "fuel_a", 1.0, 8,
     "Fuel regression law: rdot = a * G_ox^n, SI units. The tabulated value "
     "for a named fuel is a literature average; a real grain's coefficient "
     "depends on the formulation, binder, additives and how it was made, and "
     "manufacturers do not publish it for proprietary fuels. 0 = use the "
     "selected fuel's own value."),
    ("Regression exponent n (0 = fuel default)", "fuel_n", 1.0, 4,
     "Flux exponent in rdot = a * G_ox^n. Typically 0.5-0.7. Higher means "
     "the grain is more sensitive to oxidizer flux, so regression falls off "
     "faster as the port opens up."),
    ("Number of ports", "n_ports", 1.0, 0,
     "Ports burning in parallel. Multi-port grains buy burn area in a short "
     "package, at the cost of lower flux per port and leftover slivers."),
    ("Pre-combustion chamber (mm)", "L_pre", 1000.0, 1,
     "Empty volume ahead of the grain. Lets the spray break up and burn "
     "before it reaches the fuel wall."),
    ("Post-combustion chamber (mm)", "L_post", 1000.0, 1,
     "Mixing volume aft of the grain. Hybrids run fuel-rich streaks; this is "
     "where they finish burning. Skimping on it costs c* efficiency."),
]
_NOZZLE_FIELDS = [
    ("Throat diameter (mm)", "d_throat", 1000.0, 3,
     "The single most sensitive dimension in the motor. Chamber pressure "
     "scales roughly as 1/A_throat."),
    ("Expansion ratio (Ae/At)", "eps_exp", 1.0, 2,
     "Exit area over throat area. Higher is better high up and worse at sea "
     "level; too high and the flow separates in the bell."),
    ("Divergence half angle (deg)", "alpha_deg", 1.0, 1,
     "Cone half angle of the diverging section. 15 deg is the classic "
     "compromise; the model applies the matching divergence loss."),
    ("Convergence half angle (deg)", "beta_conv_deg", 1.0, 1,
     "Half angle of the converging section. Geometry and packaging only - it "
     "does not change performance in this model."),
    ("Throat erosion rate (mm/s)", "erosion_rate", 1000.0, 4,
     "Radial erosion of the throat during the burn. 0 = none. Graphite and "
     "phenolic throats DO erode, which bleeds off chamber pressure and "
     "thrust as the burn goes on."),
    ("c* efficiency", "eta_cstar", 1.0, 3,
     "How much of the theoretical characteristic velocity combustion "
     "actually delivers. Hybrids typically 0.85-0.95; poor mixing is why."),
    ("Nozzle efficiency", "eta_nozzle", 1.0, 3,
     "Losses in the nozzle beyond the divergence angle."),
]
_GAS_FIELDS = [
    ("Gas gamma (Cp/Cv)", "gamma", 1.0, 3,
     "Ratio of specific heats of the combustion products."),
    ("Molar mass (g/mol)", "MW", 1.0, 1,
     "Mean molar mass of the exhaust."),
]


# The requirements brief: what the motor has to DO, as opposed to what it is
# made of. Same (label, key, quantity, tooltip) shape as the geometry tables
# above, so the form is generated the same way and every box gets a unit
# selector. An empty box means "no requirement", never "requires zero" - see
# motor_designer.Requirements.
_REQ_FIELDS = [
    ("Total impulse", "total_impulse_ns", "req_total_impulse",
     "The size of the motor, and the single most useful requirement. Area "
     "under the whole thrust curve. Leave the rest blank and a motor will "
     "still be sized around this."),
    ("Average thrust", "avg_thrust_n", "req_avg_thrust",
     "Mean thrust over the burn. With the total impulse this fixes the burn "
     "time, since impulse is thrust times time."),
    ("Burn time", "burn_time_s", "req_burn_time",
     "How long it burns. Any two of impulse, thrust and burn time fix the "
     "third - give two and leave the other blank."),
    ("Minimum Isp", "min_isp_s", "req_min_isp",
     "Specific impulse floor, in seconds. N2O with a hydrocarbon grain "
     "realistically delivers 180-200 s at sea level; asking for much more "
     "than that cannot be met by geometry alone."),
    ("Peak thrust limit", "max_peak_thrust_n", "req_peak_thrust",
     "Structural ceiling on the thrust spike. A blowdown hybrid starts hard "
     "and decays, so the peak runs well above the average - this is what "
     "protects the airframe, and it wins over the thrust requirement."),
    ("Chamber pressure limit", "max_chamber_pressure_pa", "req_max_pc",
     "Ceiling on chamber pressure, which is what sizes the case wall. "
     "Leave blank to let the designer sit it below tank pressure."),
    ("Maximum diameter", "max_diameter_m", "req_max_diameter",
     "Outside diameter of the finished motor, walls and nozzle exit "
     "included. Normally the inside of the airframe."),
    ("Maximum length", "max_length_m", "req_max_length",
     "Overall length of the assembled motor: forward hardware, tank, "
     "bulkhead, chamber, grain and bell."),
]


# Which catalogue quantity each form key measures. Keys absent here are
# dimensionless (efficiencies, discharge coefficients, gamma, counts, the
# percentage fill fraction) and stay plain line edits with no unit selector -
# offering one would imply a conversion that does not exist.
_QUANTITY_FOR = {
    "d_tank": "tank_diameter", "L_tank": "tank_length",
    "T_tank_0": "tank_temp", "d_vent": "vent_diameter",
    "d_hole": "hole_diameter", "L_grain": "grain_length",
    "d_grain_outer": "grain_outer", "d_port_0": "port_diameter",
    "L_pre": "pre_chamber", "L_post": "post_chamber",
    "d_throat": "throat_diameter", "alpha_deg": "div_angle",
    "beta_conv_deg": "conv_angle", "erosion_rate": "erosion_rate",
    "MW": "molar_mass", "m_dry": "dry_mass", "d_body": "body_diameter",
}


# Display-unit factors as they were before unit selectors existed, used to
# read profiles written back then. The Quick Flight Preview fields live in
# their own list rather than _ALL_FIELDS, and looking them up only in
# _ALL_FIELDS left body diameter unconverted - a 140 mm tube loading as a
# 140 m one.
_LEGACY_PREVIEW_FACTORS = {"m_dry": 1.0, "Cd_body": 1.0, "d_body": 1000.0}

_ALL_FIELDS = (_TANK_FIELDS + _INJ_FIELDS + _GRAIN_FIELDS
               + _NOZZLE_FIELDS + _GAS_FIELDS)
_LEGACY_DISPLAY_FACTOR = {
    **{f[1]: f[2] for f in _ALL_FIELDS}, **_LEGACY_PREVIEW_FACTORS}

_INT_FIELDS = {"n_holes", "n_ports"}

# Defaults for the fields added after the original four-group form. Old saved
# profiles and presets predate them, so a missing value means "use this"
# rather than an error - and every one of these reproduces the previous
# behaviour exactly.
_ENGINE_DEFAULTS = {
    "d_vent": 0.0, "Cd_vent": 0.65, "n_ports": 1, "cooling_coeff": 0.0,
    "fuel_a": 0.0, "fuel_n": 0.0,
    "L_pre": 0.0, "L_post": 0.0, "beta_conv_deg": 30.0, "erosion_rate": 0.0,
}


def _hypertek_presets():
    """Engine Lab entries for the real HyperTEK motors.

    Built from presets.ENGINE_FITS so the values the UI offers are exactly the
    ones tools/validate_presets.py checks against published performance.
    """
    try:
        import presets as preset_defs
    except Exception:
        return {}
    out = {}
    for name, fit in preset_defs.ENGINE_FITS.items():
        entry = dict(
            fill_frac=fit.get("fill_frac", 0.85), T_tank_0=293,
            n_holes=fit.get("n_holes", 1), Cd_inj=0.7, fuel="HTPB",
            alpha_deg=15.0, gamma=1.22, MW=26.0,
            rocket=dict(m_dry=6.0, Cd_body=0.55, d_body=0.098))
        entry.update({k: v for k, v in fit.items()
                      if k not in ("fill_frac", "n_holes")})
        out[name] = entry
    return out


_PRESETS = {
    # The configuration behind the spreadsheet reference. Not a Goddard-level
    # vehicle - it reaches 9,292 ft - so it is named for what it actually is.
    "hybrid_sim reference": dict(
        d_tank=0.100, L_tank=1.019, fill_frac=0.85, T_tank_0=293,
        n_holes=4, d_hole=0.00252, Cd_inj=0.7, fuel="HTPB",
        L_grain=0.30, d_grain_outer=0.076, d_port_0=0.036,
        d_throat=0.018, eps_exp=5.0, alpha_deg=15.0,
        eta_cstar=0.90, eta_nozzle=0.95, gamma=1.22, MW=26.0,
        rocket=dict(m_dry=20.0, Cd_body=1.625, d_body=0.14),
    ),
    # Sized here against the SystemsGo Goddard brief - a payload to 50,000 ft.
    # 78.6 kN.s, 21.4 s, Isp 191 s; the vehicle it flies reaches 53,459 ft.
    "Goddard baseline (50k)": dict(
        d_tank=0.1708, L_tank=2.60, fill_frac=0.85, T_tank_0=298,
        n_holes=12, d_hole=0.0026, Cd_inj=0.75, fuel="HTPB",
        L_grain=0.60, d_grain_outer=0.1708, d_port_0=0.090,
        d_throat=0.042, eps_exp=6.5, alpha_deg=15.0,
        eta_cstar=0.90, eta_nozzle=0.95, gamma=1.22, MW=26.0,
        rocket=dict(m_dry=56.808, Cd_body=0.55, d_body=0.184),
    ),
}
_PRESETS.update(_hypertek_presets())

# What the Engine tab opens on. Named rather than positional: the tab used to
# rely on this being the first key of _PRESETS, so adding an entry above it
# silently changed which motor the app started with.
DEFAULT_PRESET = "Goddard baseline (50k)"

# Inputs are styled by the application-wide theme; nothing local needed.
_INPUT_STYLE = ""


class _EngExportDialog(QtWidgets.QDialog):
    """Asks for the .eng header facts the burn simulation cannot supply.

    Motor envelope and hardware mass are properties of the built motor, not of
    its internal ballistics, so they are collected here rather than guessed.
    Diameter and length are pre-filled from the tank and grain geometry, but
    those are INTERNAL dimensions - the case wall sits outside them - so the
    prefill is a starting point the user is told to correct, not an answer.
    """

    def __init__(self, parent, designation, diameter_mm, length_mm):
        super().__init__(parent)
        self.setWindowTitle("Export motor as .eng")
        form = QtWidgets.QFormLayout(self)

        self.designation = QtWidgets.QLineEdit(designation)
        self.diameter = QtWidgets.QLineEdit(f"{diameter_mm:.1f}")
        self.length = QtWidgets.QLineEdit(f"{length_mm:.1f}")
        self.hardware = QtWidgets.QLineEdit()
        # No default: a hardware mass of zero declares a motor whose loaded
        # mass equals its propellant mass, which reads downstream as a motor
        # with no case, tank, injector or nozzle - and flies kilograms light.
        self.hardware.setPlaceholderText("required - case + tank + nozzle")
        self.manufacturer = QtWidgets.QLineEdit("JARVIS")

        form.addRow("Designation:", self.designation)
        form.addRow("Motor diameter (mm):", self.diameter)
        form.addRow("Motor length (mm):", self.length)
        form.addRow("Hardware mass (kg):", self.hardware)
        form.addRow("Manufacturer:", self.manufacturer)

        note = QtWidgets.QLabel(
            "Diameter and length are pre-filled from the oxidiser tank and "
            "fuel grain, which are internal dimensions - measure the case "
            "outside diameter and overall assembled length and correct them. "
            "Hardware mass is everything that flies but does not burn; it is "
            "added to the propellant to give the loaded motor mass.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{theme.PALETTE['text_dim']}; font-size:9pt;")
        form.addRow(note)

        self.problem = QtWidgets.QLabel("")
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet(
            f"color:{theme.PALETTE['critical']}; font-weight:bold;")
        form.addRow(self.problem)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._try_accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)

    @staticmethod
    def _num(widget):
        """Parse a field, or None when it is blank or not a number.

        Returning None rather than 0.0 is what lets _try_accept tell "the user
        typed nothing" apart from "the user meant zero" - a silent 0.0 wrote
        an .eng with a zero-diameter, zero-hardware motor.
        """
        try:
            return float(widget.text().strip())
        except ValueError:
            return None

    def _try_accept(self):
        for label, widget in (("Motor diameter", self.diameter),
                              ("Motor length", self.length),
                              ("Hardware mass", self.hardware)):
            value = self._num(widget)
            if value is None:
                self.problem.setText(f"{label} must be a number.")
                widget.setFocus()
                return
            if value <= 0:
                self.problem.setText(f"{label} must be greater than zero.")
                widget.setFocus()
                return
        self.accept()

    def values(self):
        return {
            "designation": self.designation.text().strip() or "UNNAMED",
            "diameter_mm": self._num(self.diameter),
            "length_mm": self._num(self.length),
            "hardware_kg": self._num(self.hardware),
            "manufacturer": self.manufacturer.text().strip() or "JARVIS",
        }


class EngineLabWidget(QtWidgets.QWidget):
    """Design a hybrid engine, run its internal-ballistics model, and (optionally)
    hand the resulting thrust curve off to the main Simulation tab."""

    def __init__(self, on_send_to_simulation=None, get_vehicle=None,
                 on_materials=None, parent=None):
        super().__init__(parent)
        self._on_send_to_simulation = on_send_to_simulation
        # Returns the rocket the rest of the app is configured for, as
        # (dry mass kg, body Cd, body diameter m), or None. Without it the
        # flight preview below describes whatever is typed in this tab's own
        # three boxes, which is not necessarily the rocket being designed.
        self._get_vehicle = get_vehicle
        self._last_result = None      # hybrid_sim EngineModel.run() output
        self._last_metrics = None
        self._last_engine = None      # the Engine dataclass that produced it
        self._fields = {}             # field name -> QLineEdit
        self._req_fields = {}         # requirement name -> UnitField
        self._loading = False         # suppress "helpful" edits while loading
        self._on_materials = on_materials
        self._last_design = None      # last motor_designer.DesignResult
        self._build_ui()
        self._select_preset(DEFAULT_PRESET)

    # ---- UI construction -------------------------------------------------
    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QtWidgets.QWidget()
        # Wide enough that the unit selector beside each field is actually on
        # screen. At 330 the form fitted the label and the number and clipped
        # the dropdown clean off the right edge, so every dimension in the
        # motor was showing without the unit it was in - and the requirements
        # panel, whose labels are longer, made it worse. This is the form's
        # own minimum (measured, not guessed) plus the scrollbar and margins;
        # below it the panel grows a horizontal scrollbar and hides the units
        # again.
        left.setMinimumWidth(560)
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setSpacing(6)

        intro = QtWidgets.QLabel(
            "<b>Engine Lab</b> — design a hybrid (N2O/fuel-grain) engine and "
            "generate a physically simulated thrust curve.")
        intro.setWordWrap(True)
        left_layout.addWidget(intro)

        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.addItems(list(_PRESETS.keys()))
        self.preset_combo.setStyleSheet(_INPUT_STYLE)
        self.preset_combo.currentTextChanged.connect(self._apply_preset)
        preset_row = QtWidgets.QFormLayout()
        preset_row.addRow("Start from preset:", self.preset_combo)
        left_layout.addLayout(preset_row)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        form_host = QtWidgets.QWidget()
        form_layout = QtWidgets.QVBoxLayout(form_host)

        # Requirements first: this is the top-down way in, where you say what
        # the motor has to do and the geometry below is filled in for you.
        # Everything it writes lands in the ordinary fields, so the form stays
        # the thing you edit afterwards.
        form_layout.addWidget(self._build_requirements())

        self.fuel_combo = QtWidgets.QComboBox()
        self.fuel_combo.addItems(list(FUELS.keys()))
        self.fuel_combo.setStyleSheet(_INPUT_STYLE)

        self.inj_combo = QtWidgets.QComboBox()
        self.inj_combo.addItems(list(INJECTOR_TYPES.keys()))
        self.inj_combo.setStyleSheet(_INPUT_STYLE)
        self.inj_combo.setToolTip("\n".join(
            "%s - %s" % (k, v[1]) for k, v in INJECTOR_TYPES.items()))
        self.inj_combo.currentTextChanged.connect(self._injector_type_changed)

        for title, fields in (
            ("Oxidizer Tank (self-pressurizing N2O)", _TANK_FIELDS),
            ("Injector", _INJ_FIELDS),
            ("Fuel Grain && Combustion Chamber", _GRAIN_FIELDS),
            ("Nozzle", _NOZZLE_FIELDS),
            ("Combustion Gas", _GAS_FIELDS),
        ):
            group = QtWidgets.QGroupBox(title)
            gform = QtWidgets.QFormLayout(group)
            if fields is _TANK_FIELDS:
                note = QtWidgets.QLabel(
                    "N2O supplies its own pressure - no pressurant, no "
                    "regulator. Tank pressure follows the saturation curve, "
                    "so it falls as the tank cools during the burn.")
                note.setWordWrap(True)
                gform.addRow(note)
            if fields is _INJ_FIELDS:
                gform.addRow("Injector type:", self.inj_combo)
            if fields is _GRAIN_FIELDS:
                gform.addRow("Fuel:", self.fuel_combo)
            for spec in fields:
                label, key, _factor, _dec = spec[0], spec[1], spec[2], spec[3]
                tip = spec[4] if len(spec) > 4 else ""
                edit = self._make_field(key, tip)
                row_label = QtWidgets.QLabel(self._field_label(key, label))
                row_label.setToolTip(tip)
                gform.addRow(row_label, edit)
            form_layout.addWidget(group)

        self.derived_label = QtWidgets.QLabel()
        self.derived_label.setWordWrap(True)
        derived_group = QtWidgets.QGroupBox("Derived geometry")
        dlayout = QtWidgets.QVBoxLayout(derived_group)
        dlayout.addWidget(self.derived_label)
        form_layout.addWidget(derived_group)

        rocket_group = QtWidgets.QGroupBox("Quick Flight Preview (optional)")
        rform = QtWidgets.QFormLayout(rocket_group)
        for label, key, _factor, _dec in [
            ("Dry mass (kg)", "m_dry", 1.0, 2),
            ("Body Cd", "Cd_body", 1.0, 2),
            ("Body diameter (mm)", "d_body", 1000.0, 1),
        ]:
            edit = self._make_field(key, "")
            rform.addRow(self._field_label(key, label), edit)
        form_layout.addWidget(rocket_group)

        form_layout.addStretch()
        scroll.setWidget(form_host)
        # Stretch 1 so the form - the thing being edited - keeps the spare
        # height. Without it the design report, which is also expanding,
        # takes an equal share and squeezes the form down to a few rows.
        left_layout.addWidget(scroll, 1)

        self.run_button = QtWidgets.QPushButton("Run Engine Simulation")
        self.run_button.clicked.connect(self._run_engine)
        left_layout.addWidget(self.run_button)

        self.send_button = QtWidgets.QPushButton("Send Thrust Curve to Simulation")
        self.send_button.clicked.connect(self._send_to_simulation)
        self.send_button.setEnabled(False)
        left_layout.addWidget(self.send_button)

        # A motor is only useful to the rest of the team if it can leave the
        # app. .eng is what OpenRocket, RASAero and ThrustCurve.org read, so
        # this is the format a flight-readiness package wants.
        self.export_eng_button = QtWidgets.QPushButton("Export Motor as .eng File")
        self.export_eng_button.clicked.connect(self._export_eng)
        self.export_eng_button.setEnabled(False)
        left_layout.addWidget(self.export_eng_button)

        self.results_label = QtWidgets.QLabel("Run the engine to see performance metrics.")
        self.results_label.setWordWrap(True)
        self.results_label.setStyleSheet(
            f"QLabel {{ background:{theme.PALETTE['panel']}; "
            f"border:1px solid {theme.PALETTE['border']}; "
            f"border-left:3px solid {theme.PALETTE['accent']}; padding:10px; "
            f"color:{theme.PALETTE['text']}; font-size:10pt; }}")
        left_layout.addWidget(self.results_label)

        self.error_label = QtWidgets.QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color:{theme.PALETTE['critical']}; font-weight:bold;")
        left_layout.addWidget(self.error_label)

        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        right.setMinimumWidth(420)
        right_layout = QtWidgets.QVBoxLayout(right)
        self.figure = plt.Figure(figsize=(8, 6))
        theme.placeholder_figure(
            self.figure, "Run the engine to plot thrust, pressure and O/F.")
        # Theme it immediately: an unthemed canvas is a white slab in a black
        # window until the first run, which reads as a broken panel.
        theme.style_figure(self.figure)
        self.canvas = FigureCanvas(self.figure)

        # The plots and the design report share this side rather than the
        # report living in the left column. It is a wide table - requirement,
        # asked, delivered, verdict, margin - and in a 560 px column every row
        # wrapped onto four lines while squeezing the form it was meant to
        # explain down to three visible fields. Here it gets the full width,
        # and the curve it was measured from is one tab away.
        self.right_tabs = QtWidgets.QTabWidget()
        self.right_tabs.addTab(self.canvas, "Thrust Curve")
        self.right_tabs.addTab(self._build_design_report(), "Design Report")
        self.right_tabs.setTabEnabled(1, False)
        right_layout.addWidget(self.right_tabs)
        splitter.addWidget(right)

        # Roughly a third for the form, two thirds for the plots, and draggable.
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

    # ---- requirements-driven design --------------------------------------
    def _build_requirements(self):
        """The 'say what you need, get a motor' panel."""
        group = QtWidgets.QGroupBox("Design a Motor to Requirements")
        outer = QtWidgets.QVBoxLayout(group)

        blurb = QtWidgets.QLabel(
            "Fill in what the motor has to do and how much room it has. "
            "Every box is optional except giving it some idea of size - a "
            "total impulse, or a thrust and a burn time. What you leave "
            "blank is not a requirement and will not be treated as one. "
            "The generated motor lands in the fields below, where you can "
            "change anything you like.")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        form = QtWidgets.QFormLayout()
        for label, key, qkey, tip in _REQ_FIELDS:
            widget = unit_fields.UnitField(unit_fields.FIELDS[qkey])
            widget.setToolTip(tip)
            widget.clear()
            self._req_fields[key] = widget
            row_label = QtWidgets.QLabel(label + ":")
            row_label.setToolTip(tip)
            form.addRow(row_label, widget)

        # "Choose for me" is the first entry on both, and the default. A team
        # that has already settled on a fuel says so; one that has not should
        # not have to guess, which is the whole point of the panel.
        self.req_fuel_combo = QtWidgets.QComboBox()
        self.req_fuel_combo.addItem("Choose the best fuel")
        self.req_fuel_combo.addItems(list(FUELS.keys()))
        self.req_fuel_combo.setToolTip(
            "Left on 'choose', the designer sizes a motor with each of the "
            "well-characterised fuels and keeps whichever meets the brief.")
        form.addRow("Fuel:", self.req_fuel_combo)

        self.req_inj_combo = QtWidgets.QComboBox()
        self.req_inj_combo.addItem("Choose for me")
        self.req_inj_combo.addItems(list(INJECTOR_TYPES.keys()))
        self.req_inj_combo.setToolTip(
            "Injector style. It sets the discharge coefficient and how many "
            "holes the pattern is allowed to have.")
        form.addRow("Injector:", self.req_inj_combo)

        self.req_sf_edit = QtWidgets.QLineEdit("2.0")
        self.req_sf_edit.setToolTip(
            "Safety factor on the yield strength of the tank and chamber. "
            "This sizes the walls and so decides how much bore is left "
            "inside the diameter you allowed.")
        form.addRow("Pressure safety factor:", self.req_sf_edit)
        outer.addLayout(form)

        self.generate_button = QtWidgets.QPushButton("Generate Motor")
        self.generate_button.setToolTip(
            "Size a complete motor against these requirements and fill in "
            "every field below.")
        self.generate_button.clicked.connect(self._generate_motor)
        outer.addWidget(self.generate_button)

        self.req_status = QtWidgets.QLabel("No motor generated yet.")
        self.req_status.setWordWrap(True)
        self.req_status.setTextFormat(QtCore.Qt.RichText)
        outer.addWidget(self.req_status)
        return group

    def _build_design_report(self):
        """Where the compliance table lives: visible, and out of the scroll.

        It started inside the requirements group, which put the one thing the
        user actually needs to read - did this motor meet the brief? - below
        the fold of a scrolling form, behind the button they had just pressed.
        Out here it is always on screen, and it gets its own scrollbar so a
        long list of caveats cannot squash the buttons above it.
        """
        self.design_group = QtWidgets.QGroupBox("Motor design report")
        box = QtWidgets.QVBoxLayout(self.design_group)
        box.setContentsMargins(6, 6, 6, 6)
        self.design_report = QtWidgets.QLabel()
        self.design_report.setWordWrap(True)
        self.design_report.setTextFormat(QtCore.Qt.RichText)
        self.design_report.setAlignment(QtCore.Qt.AlignTop)
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.design_report)
        box.addWidget(area)
        return self.design_group

    def _si_to_form(self, cfg: dict) -> dict:
        """An all-SI engine dict in the convention this form actually stores.

        The form is not uniformly SI and cannot be made so without breaking
        every profile already saved. The unit-aware fields hold SI; the plain
        line edits hold DISPLAY text, and one of them - fill fraction - is a
        percentage. Handing that field an SI 0.85 puts "0.85" in a box that
        means percent, and _read_engine then divides by 100 and fills the
        tank to 0.85%: a motor sized for 11,600 N.s fired 1,084 N.s and
        reported itself a J instead of an L.

        So the conversion happens here, at the one boundary where an outside
        SI dict meets the form, driven by the same display-factor table the
        form is generated from - a new plain field with a factor cannot
        reintroduce this by being forgotten.
        """
        out = dict(cfg)
        decimals = {spec[1]: spec[3] for spec in _ALL_FIELDS}
        for key, value in cfg.items():
            widget = self._fields.get(key)
            if widget is None or isinstance(widget, unit_fields.UnitField):
                continue                    # unit fields genuinely take SI
            factor = _LEGACY_DISPLAY_FACTOR.get(key, 1.0)
            try:
                number = float(value) * factor
            except (TypeError, ValueError):
                continue
            if key in _INT_FIELDS:
                out[key] = str(int(round(number)))
            else:
                # Format even when the factor is 1. These boxes declare a
                # number of decimals and every other route into them honours
                # it; passing a raw float through showed a generated motor's
                # expansion ratio as 7.574912532440119 where the same engine
                # loaded from a profile showed 7.57.
                out[key] = f"{number:.{decimals.get(key, 3)}f}"
        return out

    def _read_requirements(self):
        """The brief as the designer wants it, in SI."""
        req = motor_designer.Requirements()
        for _label, key, _qkey, _tip in _REQ_FIELDS:
            widget = self._req_fields.get(key)
            if widget is None:
                continue
            # An empty box is "no requirement". UnitField reads blank as 0.0,
            # which is the same number the dataclass uses for "not asked for",
            # so the two agree - but be explicit rather than relying on it.
            value = 0.0 if widget.is_blank() else widget.value_si()
            setattr(req, key, max(0.0, float(value)))

        fuel = self.req_fuel_combo.currentText()
        req.fuel = fuel if fuel in FUELS else ""
        inj = self.req_inj_combo.currentText()
        req.injector = inj if inj in INJECTOR_TYPES else ""

        # Tank fill temperature comes from the form rather than a box of its
        # own: it is already an engine field, and having it in two places is
        # how the two drift apart.
        req.tank_temp_k = self._field_si("T_tank_0", 293.0) or 293.0
        try:
            req.structural_sf = max(1.0, float(self.req_sf_edit.text()))
        except (TypeError, ValueError):
            req.structural_sf = 2.0
        return req

    def _generate_motor(self):
        self.error_label.setText("")
        try:
            req = self._read_requirements()
        except Exception as exc:
            self.req_status.setText(
                f"<b style='color:{theme.PALETTE['critical']}'>"
                f"Could not read the requirements: {exc}</b>")
            return

        self.generate_button.setEnabled(False)
        self.req_status.setText("Sizing...")
        QtWidgets.QApplication.processEvents()

        def progress(frac, message):
            self.req_status.setText(
                f"Sizing... {message} ({frac * 100:.0f}%)")
            QtWidgets.QApplication.processEvents()

        try:
            design = motor_designer.design_motor(req, progress=progress)
        except Exception as exc:
            self.req_status.setText(
                f"<b style='color:{theme.PALETTE['critical']}'>"
                f"Could not size a motor: {exc}</b>")
            traceback.print_exc()
            return
        finally:
            self.generate_button.setEnabled(True)

        self._last_design = design
        # Into the ordinary form, through the ordinary loader - so a generated
        # motor is in exactly the state a loaded one would be, and saving the
        # rocket saves it like any other.
        self.apply_config(self._si_to_form(design.engine_fields))
        pal = theme.PALETTE
        self.req_status.setText(
            f"<b style='color:{pal.get('good', '#3fb950')}'>Motor generated "
            f"and filled in below &mdash; it meets every requirement.</b> "
            f"See the design report."
            if design.met_all else
            f"<b style='color:{pal['critical']}'>Motor generated, but it "
            f"does not meet every requirement.</b> The design report says "
            f"which, and by how much.")
        self.design_report.setText(self._design_report(design))
        self.right_tabs.setTabEnabled(1, True)
        # Show the answer to what was asked, not the curve. The curve is the
        # evidence and it is one tab away; the verdict is the point.
        self.right_tabs.setCurrentIndex(1)

        # Hand the pressure-vessel choices to whatever owns the vehicle's
        # material fields, if anything does. The motor knows what it is made
        # of; the Flight Report is where that gets graded.
        if self._on_materials is not None:
            try:
                self._on_materials(design.materials)
            except Exception:
                traceback.print_exc()

        # Run it straight away. A generated motor with an empty plot beside it
        # looks like it has not been checked, and it has - this just shows the
        # curve the compliance table was measured from.
        self._run_engine()

    def _design_report(self, design) -> str:
        """The compliance table, the length budget and the materials."""
        pal = theme.PALETTE
        ok_col, bad_col = pal.get('good', '#3fb950'), pal['critical']
        rows = []
        for c in design.compliance:
            colour = ok_col if c.met else bad_col
            mark = "meets" if c.met else "MISSES"
            rows.append(
                f"<tr><td>{c.label}</td><td>{c.required}</td>"
                f"<td><b>{c.achieved}</b></td>"
                f"<td style='color:{colour}'>{mark}</td>"
                f"<td><i>{c.note}</i></td></tr>")
        table = ("<table cellspacing='0' cellpadding='3'>"
                 "<tr><th align='left'>Requirement</th><th align='left'>Asked"
                 "</th><th align='left'>Delivered</th><th align='left'></th>"
                 "<th align='left'></th></tr>" + "".join(rows) + "</table>")

        headline = ("<b>Motor meets every requirement.</b>"
                    if design.met_all else
                    f"<b style='color:{bad_col}'>Motor does not meet every "
                    f"requirement.</b> The closest fit is below; the rows "
                    f"marked MISSES say by how much.")

        parts = "".join(
            f"<tr><td>{k}</td><td align='right'>{v * 1000:.0f} mm</td></tr>"
            for k, v in design.envelope.items() if not k.startswith("_"))
        length = ("<table cellspacing='0' cellpadding='2'>" + parts
                  + "</table>")

        mats = []
        for part in ("tank", "chamber", "nozzle"):
            info = design.materials.get(part) or {}
            wall = (f" &mdash; {info['wall_m'] * 1000:.2f} mm wall"
                    if info.get("wall_m") else "")
            mats.append(
                f"<li><b>{part.title()}:</b> {info.get('name', '?')}{wall}"
                f"<br><i>{info.get('note', '')}</i></li>")

        notes = "".join(f"<li>{n}</li>" for n in design.notes)
        notes_html = f"<br><b>Worth knowing</b><ul>{notes}</ul>" if notes else ""

        return (
            f"{headline}<br><br>{table}"
            f"<br><b>Chosen fuel:</b> {design.fuel_name} &nbsp; "
            f"<b>Injector:</b> {design.injector_name} &nbsp; "
            f"<i>({design.runs} simulations)</i>"
            f"<br><br><b>Length budget</b>{length}"
            f"<br><b>Pressure parts</b><ul>{''.join(mats)}</ul>"
            f"{notes_html}")

    def get_config(self) -> dict:
        """The engine design as plain values, for saving into a rocket profile."""
        cfg = {}
        for key, widget in self._fields.items():
            if isinstance(widget, unit_fields.UnitField):
                cfg[key] = widget.value_si()
            else:
                cfg[key] = widget.text()
        cfg["fuel"] = self.fuel_combo.currentText()
        cfg["inj_type"] = self.inj_combo.currentText()
        # Marks the dimensioned values above as SI. Profiles written before
        # unit selectors existed stored the display number in whatever unit
        # was baked into the label (tank diameter in mm, throat in mm), so
        # without this flag they would be read as metres. _apply_config_values
        # falls back to the old per-field factor when it is absent.
        cfg["_units"] = "si"
        return cfg

    def apply_config(self, cfg: dict):
        """Restore an engine design saved by get_config()."""
        if not cfg:
            return
        self._loading = True
        try:
            self._apply_config_values(cfg)
        finally:
            self._loading = False
        self._update_derived()

    def _apply_config_values(self, cfg: dict):
        si_config = str(cfg.get("_units", "")) == "si"
        # Reset anything the config does not mention back to its default first.
        # Leaving absent fields untouched meant loading a preset motor kept the
        # user's previous multi-port grain, eroding throat or open vent, so the
        # "validated" fit was not what actually ran.
        for key, default in _ENGINE_DEFAULTS.items():
            if key in cfg or key not in self._fields:
                continue
            spec = next((f for f in _ALL_FIELDS if f[1] == key), None)
            if spec:
                # Defaults are stored in SI, like everything else.
                if isinstance(self._fields[key], unit_fields.UnitField):
                    self._set_field_si(key, default, spec[3])
                else:
                    self._fields[key].setText(
                        f"{default * spec[2]:.{spec[3]}f}")
        for key, value in cfg.items():
            if key == "fuel":
                idx = self.fuel_combo.findText(str(value))
                if idx >= 0:
                    self.fuel_combo.setCurrentIndex(idx)
            elif key == "inj_type":
                idx = self.inj_combo.findText(str(value))
                if idx >= 0:
                    self.inj_combo.setCurrentIndex(idx)
            elif key in self._fields:
                widget = self._fields[key]
                if isinstance(widget, unit_fields.UnitField):
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        continue
                    if not si_config:
                        # Legacy profile: the number is in the unit that used
                        # to be baked into this field's label.
                        factor = _LEGACY_DISPLAY_FACTOR.get(key)
                        if factor:
                            number = number / factor
                    widget.set_value_si(number)
                else:
                    widget.setText(str(value))

    # ---- unit-aware field plumbing ---------------------------------------
    def _make_field(self, key, tip):
        """A UnitField for a dimensioned key, a plain line edit otherwise."""
        qkey = _QUANTITY_FOR.get(key)
        if qkey:
            widget = unit_fields.UnitField(unit_fields.FIELDS[qkey])
            widget.setToolTip(tip)
            widget.edit.editingFinished.connect(self._update_derived)
        else:
            widget = QtWidgets.QLineEdit()
            widget.setStyleSheet(_INPUT_STYLE)
            widget.setToolTip(tip)
            widget.editingFinished.connect(self._update_derived)
        self._fields[key] = widget
        return widget

    @staticmethod
    def _field_label(key, label):
        """Strip the baked-in unit from a label that now has a selector."""
        if key in _QUANTITY_FOR and "(" in label:
            label = label[:label.index("(")].strip()
        return label + ":"

    def _field_si(self, key, default=None):
        """Field value in SI, whichever unit is showing."""
        widget = self._fields.get(key)
        if widget is None:
            return default
        if isinstance(widget, unit_fields.UnitField):
            return widget.value_si()
        text = widget.text().strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            return default

    def _set_field_si(self, key, si, decimals=3):
        widget = self._fields.get(key)
        if widget is None:
            return
        if isinstance(widget, unit_fields.UnitField):
            widget.set_value_si(si)
        else:
            widget.setText(f"{si:.{decimals}f}")

    def get_last_run(self):
        """(engine, result, metrics) from the last successful engine run, or None.

        Used by the Flight Report tab so the failure analysis can grade the
        engine's internal ballistics, not just the trajectory.
        """
        if self._last_result is None or self._last_engine is None:
            return None
        return self._last_engine, self._last_result, self._last_metrics

    def current_engine_run(self):
        """(engine, result, metrics) for the motor as the form stands now.

        get_last_run() only answers once the user has pressed Run, and the
        Flight Report only accepted it when the flight had been flown on this
        tab's own generated curve. Between them, thirteen propulsion checks
        reported "NO DATA" on every flight that used an imported thrust curve
        - which is most of them.

        The internal ballistics of the motor described on this tab are
        computable whether or not that motor is the one that flew, so this
        computes them on demand. The caller is responsible for saying which
        case it has; what it must not do is report nothing.

        Returns None only if the form cannot describe a motor at all.
        """
        if self._last_result is not None and self._last_engine is not None:
            return self._last_engine, self._last_result, self._last_metrics
        try:
            engine = self._read_engine()
            result = EngineModel(engine).run()
            return engine, result, hs_metrics(result)
        except Exception:
            # A half-filled form is not an error worth surfacing here; the
            # report simply says the engine could not be modelled.
            return None

    # ---- presets -----------------------------------------------------------
    def _select_preset(self, name):
        """Show a preset in the combo AND apply it.

        Applying without moving the combo leaves the dropdown naming one motor
        while the fields hold another - which is what happened when the first
        key of _PRESETS stopped being the default. Setting the combo is what
        the user would do, and the currentTextChanged connection applies it.
        """
        idx = self.preset_combo.findText(name)
        if idx < 0:
            self._apply_preset(name)
            return
        if self.preset_combo.currentIndex() == idx:
            self._apply_preset(name)      # already showing it; still apply
        else:
            self.preset_combo.setCurrentIndex(idx)

    def _apply_preset(self, name):
        preset = _PRESETS.get(name)
        if not preset:
            return
        self._loading = True
        try:
            self._apply_preset_values(preset)
        finally:
            self._loading = False
        self._update_derived()

    def _apply_preset_values(self, preset):
        for spec in _ALL_FIELDS:
            key, factor, dec = spec[1], spec[2], spec[3]
            value = preset.get(key, _ENGINE_DEFAULTS.get(key))
            if value is not None:
                self._set_field_si(key, value, dec) \
                    if isinstance(self._fields[key], unit_fields.UnitField) \
                    else self._fields[key].setText(f"{value * factor:.{dec}f}")
        idx = self.inj_combo.findText(preset.get("inj_type", "Showerhead"))
        if idx >= 0:
            self.inj_combo.setCurrentIndex(idx)
        idx = self.fuel_combo.findText(preset.get("fuel", "HTPB"))
        if idx >= 0:
            self.fuel_combo.setCurrentIndex(idx)
        rocket = preset.get("rocket", {})
        for label, key, factor, dec in [
            ("Dry mass (kg)", "m_dry", 1.0, 2),
            ("Body Cd", "Cd_body", 1.0, 2),
            ("Body diameter (mm)", "d_body", 1000.0, 1),
        ]:
            if key in rocket:
                self._set_field_si(key, rocket[key], dec) \
                    if isinstance(self._fields[key], unit_fields.UnitField) \
                    else self._fields[key].setText(f"{rocket[key] * factor:.{dec}f}")

    def _injector_type_changed(self, name):
        """Suggest the matching discharge coefficient when the type changes.

        Only when a person picks the type. Loading a preset or a saved profile
        also moves this combo, and those carry their own fitted Cd that must
        not be clobbered by a generic suggestion.
        """
        info = INJECTOR_TYPES.get(name)
        if not info or getattr(self, "_loading", False):
            return
        edit = self._fields.get("Cd_inj")
        if edit is not None:
            edit.setText(f"{info[0]:.2f}")
        self._update_derived()

    def _update_derived(self):
        """Show the numbers that fall out of the geometry as it is typed.

        These are the quantities you actually size hardware against - tank
        volume, injector area, L*, exit diameter - and none of them are things
        you type in directly, so they are easy to get wrong silently.
        """
        if not hasattr(self, "derived_label"):
            return
        try:
            eng = self._read_engine()
        except Exception:
            self.derived_label.setText(
                "<i>Fill in the geometry to see derived values.</i>")
            return
        try:
            import hybrid_sim.n2o as _n2o
            p_tank = float(_n2o.psat(eng.T_tank_0))
            rho_l = float(_n2o.rho_l(eng.T_tank_0))
        except Exception:
            p_tank, rho_l = 0.0, 0.0
        m_ox = eng.fill_frac * eng.V_tank * rho_l
        self.derived_label.setText(
            f"Tank volume: <b>{eng.V_tank_cc:.0f} cc</b> "
            f"({eng.V_tank * 1000:.2f} L)<br>"
            f"Liquid N2O at ignition: <b>{m_ox:.3f} kg</b><br>"
            f"Tank pressure at {eng.T_tank_0:.0f} K: "
            f"<b>{p_tank / 1e6:.2f} MPa ({p_tank * 0.000145038:.0f} psi)</b><br>"
            f"Fuel loaded: <b>{eng.m_fuel_0():.3f} kg</b><br>"
            f"Fuel web to burn: <b>{eng.web_0 * 1000:.1f} mm</b><br>"
            f"Injector area: <b>{eng.A_inj * 1e6:.2f} mm²</b> "
            f"(Cd·A {eng.CdA_inj * 1e6:.2f} mm²)<br>"
            f"Initial port area: <b>{eng.A_port_0 * 1e6:.1f} mm²</b><br>"
            f"Throat area: <b>{eng.A_throat * 1e6:.2f} mm²</b><br>"
            f"Exit diameter: <b>{eng.d_exit * 1000:.1f} mm</b><br>"
            f"L* at ignition: <b>{eng.L_star():.2f} m</b>")

    # ---- build dataclasses from the form -----------------------------------
    def _read_engine(self) -> Engine:
        kwargs = {}
        for spec in _ALL_FIELDS:
            key, factor = spec[1], spec[2]
            widget = self._fields[key]
            if isinstance(widget, unit_fields.UnitField):
                # Already SI; the spec's factor described the old baked-in
                # display unit and no longer applies.
                value = widget.value_si()
            else:
                text = widget.text().strip()
                if not text:
                    # A blank optional field means "leave it at the default"
                    # rather than an error, so old saved profiles still load.
                    if key in _ENGINE_DEFAULTS:
                        kwargs[key] = _ENGINE_DEFAULTS[key]
                        continue
                    raise ValueError(f"Missing value for '{key}'")
                value = float(text) / factor
            kwargs[key] = int(round(value)) if key in _INT_FIELDS else value
        kwargs["fuel"] = FUELS[self.fuel_combo.currentText()]
        kwargs["inj_type"] = self.inj_combo.currentText()
        return Engine(**kwargs)

    def _read_rocket(self):
        """The airframe the flight preview should fly, and where it came from.

        This tab has its own dry mass, Cd and diameter boxes, and the preview
        used to fly those and nothing else. They are not updated when a rocket
        is loaded, so the preview reported the same apogee for every vehicle -
        the value for a 20 kg, 140 mm airframe, which is right for exactly one
        of the presets and out by a factor of six for another.

        The rocket configured on the Aerodynamics and Simulation tabs wins
        when there is one. The boxes here are the fallback, for designing a
        motor before there is an airframe to put it in.
        """
        source = "this tab"
        m_dry = self._field_si("m_dry", 20.0)
        Cd_body = self._field_si("Cd_body", 1.6)
        d_body = self._field_si("d_body", 0.140)
        if self._get_vehicle is not None:
            try:
                vehicle = self._get_vehicle()
            except Exception:
                vehicle = None
            if vehicle:
                v_mass, v_cd, v_dia = vehicle
                if v_mass and v_mass > 0:
                    m_dry = float(v_mass)
                if v_cd and v_cd > 0:
                    Cd_body = float(v_cd)
                if v_dia and v_dia > 0:
                    d_body = float(v_dia)
                source = "the loaded rocket"
        return Rocket(m_dry=m_dry, Cd_body=Cd_body, d_body=d_body), source

    # ---- actions ------------------------------------------------------------
    def _run_engine(self):
        self.error_label.setText("")
        try:
            engine = self._read_engine()
            result = EngineModel(engine).run()
            m = hs_metrics(result)
            if m["peak_thrust"] <= 0:
                raise ValueError("No thrust produced - check tank/injector/grain geometry.")
        except Exception as exc:
            self._last_result = None
            self._last_metrics = None
            self.export_eng_button.setEnabled(False)
            self.send_button.setEnabled(False)
            self.error_label.setText(f"Engine simulation failed: {exc}")
            traceback.print_exc()
            return

        self._last_result = result
        self._last_metrics = m
        self._last_engine = engine
        self.send_button.setEnabled(True)
        self.export_eng_button.setEnabled(True)
        self._plot(result)

        preview = ""
        try:
            rocket, source = self._read_rocket()
            fl = FlightModel(rocket, result).run()
            preview = (
                f"<br><b>Quick flight preview</b><br>"
                f"<i>1-DOF estimate for {rocket.m_dry:.1f} kg dry, "
                f"{rocket.d_body*1000:.0f} mm, Cd {rocket.Cd_body:.2f} "
                f"(from {source}). The Simulation tab flies the full "
                f"2-DOF model and is the number to trust.</i><br>"
                f"Apogee: {fl['apogee_ft']:.0f} ft ({fl['apogee_m']:.0f} m)<br>"
                f"Max velocity: {fl['v_max']:.1f} m/s (Mach {fl['mach_max']:.2f})<br>"
                f"Max G (ascent): {fl['g_max_ascent']:.1f}"
            )
        except Exception as exc:
            preview = f"<br><i>Flight preview unavailable: {exc}</i>"

        self.results_label.setText(
            f"<b>Engine performance</b><br>"
            f"Peak thrust: {m['peak_thrust']:.0f} N<br>"
            f"Avg thrust: {m['avg_thrust']:.0f} N<br>"
            f"Total impulse: {m['total_impulse']:.0f} N·s<br>"
            f"Burn time: {m['burn_time']:.2f} s<br>"
            f"Peak Pc: {m['peak_Pc']/1e6:.2f} MPa ({m['peak_Pc']*0.000145038:.0f} psi)<br>"
            f"Isp: {m['isp']:.1f} s<br>"
            f"Avg O/F: {m['avg_OF']:.2f}<br>"
            f"Propellant mass: {m['prop_mass']:.3f} kg<br>"
            f"Designation: <b>{rasp.designation(m['total_impulse'], m['avg_thrust'])}</b> "
            f"({rasp.class_fraction(m['total_impulse'])*100:.0f}% up the "
            f"{rasp.impulse_class(m['total_impulse']) or 'sub-A'} class)"
            + preview
        )

    def _plot(self, res):
        self.figure.clear()
        try:
            self.figure.set_layout_engine('constrained')
        except AttributeError:
            pass
        t = res["t"]
        ax = self.figure.subplots(2, 2)
        ax[0, 0].plot(t, res["thrust"], color="#c0392b", lw=1.8)
        ax[0, 0].set(xlabel="Time (s)", ylabel="Thrust (N)", title="Thrust")
        ax[0, 1].plot(t, res["Pc"] / 1e6, color="#2c3e50", lw=1.8, label="chamber")
        ax[0, 1].plot(t, res["P_tank"] / 1e6, "--", color="#7f8c8d", lw=1.2, label="tank")
        ax[0, 1].set(xlabel="Time (s)", ylabel="Pressure (MPa)", title="Pressure")
        ax[0, 1].legend(fontsize=9)
        ax[1, 0].plot(t, res["OF"], color="#2980b9", lw=1.8)
        ax[1, 0].set(xlabel="Time (s)", ylabel="O/F", title="Mixture ratio")
        ax[1, 1].plot(t, res["mdot_ox"], color="#16a085", lw=1.5, label="oxidizer")
        ax[1, 1].plot(t, res["mdot_fuel"], color="#e67e22", lw=1.5, label="fuel")
        ax[1, 1].set(xlabel="Time (s)", ylabel="mdot (kg/s)", title="Mass flow")
        ax[1, 1].legend(fontsize=9)
        for a in ax.flat:
            a.grid(alpha=0.3)
        theme.style_figure(self.figure)
        if self.figure.get_layout_engine() is None:
            self.figure.tight_layout()
        self.canvas.draw()

    def _send_to_simulation(self):
        if self._last_result is None or self._last_metrics is None:
            return
        out_dir = _generated_curves_dir()
        path = os.path.join(out_dir, f"engine_lab_{int(time.time())}.csv")
        try:
            os.makedirs(out_dir, exist_ok=True)
            self._export_csv(path, self._last_result, self._last_metrics)
        except OSError as exc:
            self.error_label.setText(f"Could not save thrust curve to {out_dir}: {exc}")
            return

        dry_mass = self._field_si("m_dry", 20.0)
        if self._on_send_to_simulation:
            self._on_send_to_simulation(path, self._last_metrics["prop_mass"], dry_mass)

    def _export_eng(self):
        """Write the current motor out as a RASP .eng file.

        The .eng header carries facts the burn simulation does not know - the
        motor's overall envelope and the mass of the hardware that flies with
        the propellant - so it asks, pre-filling what it can derive from the
        geometry fields.
        """
        if self._last_result is None or self._last_metrics is None:
            return
        m = self._last_metrics
        name = rasp.designation(m["total_impulse"], m["avg_thrust"])

        # Fields on this form are millimetres. A hybrid's envelope is the tank
        # plus the combustion chamber stack, and its diameter the wider of the
        # two tubes.
        dia_mm = max(self._float_field("d_tank"),
                     self._float_field("d_grain_outer"))
        len_mm = sum(self._float_field(k) for k in
                     ("L_tank", "L_pre", "L_grain", "L_post"))

        dialog = _EngExportDialog(self, name, dia_mm, len_mm)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        cfg = dialog.values()

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export motor as .eng", f"{cfg['designation']}.eng",
            "RASP Motor Files (*.eng);;All Files (*)")
        if not path:
            return
        if not path.lower().endswith(".eng"):
            path += ".eng"

        try:
            rasp.write_eng(
                path, self._last_result["t"], self._last_result["thrust"],
                designation_str=cfg["designation"],
                diameter_m=cfg["diameter_mm"] / 1000.0,
                length_m=cfg["length_mm"] / 1000.0,
                propellant_mass_kg=m["prop_mass"],
                total_mass_kg=m["prop_mass"] + cfg["hardware_kg"],
                manufacturer=cfg["manufacturer"])
        except (ValueError, OSError) as exc:
            self.error_label.setText(f"Could not export .eng: {exc}")
            return
        self.error_label.setText("")
        self.results_label.setText(
            self.results_label.text()
            + f"<br><br><b>Exported</b> {cfg['designation']} to {path}")

    def _float_field(self, key, default=0.0):
        """Read one of the form fields as a float, tolerating blanks."""
        widget = self._fields.get(key)
        if widget is None:
            return default
        try:
            return float(widget.text())
        except (ValueError, AttributeError, TypeError):
            return default

    @staticmethod
    def _export_csv(path, res, m):
        import csv
        t, F = res["t"], res["thrust"]
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["motor:", "JARVIS Engine Lab custom hybrid engine"])
            w.writerow(["propellant mass:", f"{m['prop_mass']:.4f} kg"])
            w.writerow([])
            w.writerow(["Time (s)", "Thrust (N)"])
            for ti, Fi in zip(t, F):
                w.writerow([f"{ti:.4f}", f"{max(0.0, Fi):.3f}"])
