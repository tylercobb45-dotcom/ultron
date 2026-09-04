"""Engine Lab: a PyQt5 tab for designing a custom N2O/HTPB-family hybrid rocket
engine and turning it into a thrust curve the rest of JARVIS can fly.

Physics comes entirely from the vendored ``hybrid_sim`` package (tank
blowdown, Dyer NHNE injector, Marxman fuel regression, CEA c* table,
isentropic nozzle with Summerfield separation) - nothing in that package is
modified here. This module only adds a PyQt5 front end around it:

    * a form for the engine's tank / injector / fuel grain / nozzle geometry
    * preset motors to start from (the Goddard baseline and the two
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
    """Where to save generated thrust curves.

    Running from source these sit with the project. Frozen, they must not go
    into the bundle: a onefile build unpacks to a temp directory that is
    deleted on exit, and an install location may not be writable - so use a
    per-user folder that survives and is always writable.
    """
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.expanduser('~'), 'JARVIS', 'generated_curves')
    return os.path.join(_HYBRID_SIM_ROOT, 'generated_curves')

from hybrid_sim import Engine, Rocket, EngineModel, FlightModel, FUELS, metrics as hs_metrics  # noqa: E402
from hybrid_sim.config import INJECTOR_TYPES  # noqa: E402

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

_ALL_FIELDS = (_TANK_FIELDS + _INJ_FIELDS + _GRAIN_FIELDS
               + _NOZZLE_FIELDS + _GAS_FIELDS)
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
    "Goddard baseline": dict(
        d_tank=0.100, L_tank=1.019, fill_frac=0.85, T_tank_0=293,
        n_holes=4, d_hole=0.00252, Cd_inj=0.7, fuel="HTPB",
        L_grain=0.30, d_grain_outer=0.076, d_port_0=0.036,
        d_throat=0.018, eps_exp=5.0, alpha_deg=15.0,
        eta_cstar=0.90, eta_nozzle=0.95, gamma=1.22, MW=26.0,
        rocket=dict(m_dry=20.0, Cd_body=1.625, d_body=0.14),
    ),
}
_PRESETS.update(_hypertek_presets())

# Inputs are styled by the application-wide theme; nothing local needed.
_INPUT_STYLE = ""


class EngineLabWidget(QtWidgets.QWidget):
    """Design a hybrid engine, run its internal-ballistics model, and (optionally)
    hand the resulting thrust curve off to the main Simulation tab."""

    def __init__(self, on_send_to_simulation=None, parent=None):
        super().__init__(parent)
        self._on_send_to_simulation = on_send_to_simulation
        self._last_result = None      # hybrid_sim EngineModel.run() output
        self._last_metrics = None
        self._last_engine = None      # the Engine dataclass that produced it
        self._fields = {}             # field name -> QLineEdit
        self._loading = False         # suppress "helpful" edits while loading
        self._build_ui()
        self._apply_preset("Goddard baseline")

    # ---- UI construction -------------------------------------------------
    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QtWidgets.QWidget()
        left.setMinimumWidth(330)
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
            ("Fuel Grain & Combustion Chamber", _GRAIN_FIELDS),
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
                edit = QtWidgets.QLineEdit()
                edit.setStyleSheet(_INPUT_STYLE)
                edit.setToolTip(tip)
                edit.editingFinished.connect(self._update_derived)
                self._fields[key] = edit
                row_label = QtWidgets.QLabel(label + ":")
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
            edit = QtWidgets.QLineEdit()
            edit.setStyleSheet(_INPUT_STYLE)
            self._fields[key] = edit
            rform.addRow(label + ":", edit)
        form_layout.addWidget(rocket_group)

        form_layout.addStretch()
        scroll.setWidget(form_host)
        left_layout.addWidget(scroll)

        self.run_button = QtWidgets.QPushButton("Run Engine Simulation")
        self.run_button.clicked.connect(self._run_engine)
        left_layout.addWidget(self.run_button)

        self.send_button = QtWidgets.QPushButton("Send Thrust Curve to Simulation")
        self.send_button.clicked.connect(self._send_to_simulation)
        self.send_button.setEnabled(False)
        left_layout.addWidget(self.send_button)

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
        self.canvas = FigureCanvas(self.figure)
        right_layout.addWidget(self.canvas)
        splitter.addWidget(right)

        # Roughly a third for the form, two thirds for the plots, and draggable.
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

    def get_config(self) -> dict:
        """The engine design as plain values, for saving into a rocket profile."""
        cfg = {key: edit.text() for key, edit in self._fields.items()}
        cfg["fuel"] = self.fuel_combo.currentText()
        cfg["inj_type"] = self.inj_combo.currentText()
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
                self._fields[key].setText(str(value))

    def get_last_run(self):
        """(engine, result, metrics) from the last successful engine run, or None.

        Used by the Flight Report tab so the failure analysis can grade the
        engine's internal ballistics, not just the trajectory.
        """
        if self._last_result is None or self._last_engine is None:
            return None
        return self._last_engine, self._last_result, self._last_metrics

    # ---- presets -----------------------------------------------------------
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
                self._fields[key].setText(f"{value * factor:.{dec}f}")
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
                self._fields[key].setText(f"{rocket[key] * factor:.{dec}f}")

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
            text = self._fields[key].text().strip()
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

    def _read_rocket(self) -> Rocket:
        m_dry = float(self._fields["m_dry"].text() or 20.0)
        Cd_body = float(self._fields["Cd_body"].text() or 1.6)
        d_body = float(self._fields["d_body"].text() or 140.0) / 1000.0
        return Rocket(m_dry=m_dry, Cd_body=Cd_body, d_body=d_body)

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
            self.send_button.setEnabled(False)
            self.error_label.setText(f"Engine simulation failed: {exc}")
            traceback.print_exc()
            return

        self._last_result = result
        self._last_metrics = m
        self._last_engine = engine
        self.send_button.setEnabled(True)
        self._plot(result)

        preview = ""
        try:
            rocket = self._read_rocket()
            fl = FlightModel(rocket, result).run()
            preview = (
                f"<br><b>Quick flight preview</b><br>"
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
            f"Propellant mass: {m['prop_mass']:.3f} kg"
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

        dry_mass = float(self._fields["m_dry"].text() or 20.0)
        if self._on_send_to_simulation:
            self._on_send_to_simulation(path, self._last_metrics["prop_mass"], dry_mass)

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
