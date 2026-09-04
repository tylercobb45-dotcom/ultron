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

# name -> Engine field, display factor (value shown = field value * factor), decimals
_TANK_FIELDS = [
    ("Tank diameter (mm)", "d_tank", 1000.0, 1),
    ("Tank length (mm)", "L_tank", 1000.0, 1),
    ("Fill fraction (%)", "fill_frac", 100.0, 0),
    ("Initial tank temp (K)", "T_tank_0", 1.0, 1),
]
_INJ_FIELDS = [
    ("Number of holes", "n_holes", 1.0, 0),
    ("Hole diameter (mm)", "d_hole", 1000.0, 3),
    ("Injector Cd", "Cd_inj", 1.0, 2),
]
_GRAIN_FIELDS = [
    ("Grain length (mm)", "L_grain", 1000.0, 1),
    ("Grain outer diameter (mm)", "d_grain_outer", 1000.0, 1),
    ("Initial port diameter (mm)", "d_port_0", 1000.0, 1),
]
_NOZZLE_FIELDS = [
    ("Throat diameter (mm)", "d_throat", 1000.0, 2),
    ("Expansion ratio (Ae/At)", "eps_exp", 1.0, 2),
    ("Half angle (deg)", "alpha_deg", 1.0, 1),
    ("c* efficiency", "eta_cstar", 1.0, 2),
    ("Nozzle efficiency", "eta_nozzle", 1.0, 2),
    ("Gas gamma (Cp/Cv)", "gamma", 1.0, 2),
    ("Molar mass (g/mol)", "MW", 1.0, 1),
]

_PRESETS = {
    "Goddard baseline": dict(
        d_tank=0.100, L_tank=1.019, fill_frac=0.85, T_tank_0=293,
        n_holes=4, d_hole=0.00252, Cd_inj=0.7, fuel="HTPB",
        L_grain=0.30, d_grain_outer=0.076, d_port_0=0.036,
        d_throat=0.018, eps_exp=5.0, alpha_deg=15.0,
        eta_cstar=0.90, eta_nozzle=0.95, gamma=1.22, MW=26.0,
        rocket=dict(m_dry=20.0, Cd_body=1.625, d_body=0.14),
    ),
    "HyperTEK I260": dict(
        d_tank=0.050, L_tank=0.224, fill_frac=0.85, T_tank_0=293,
        n_holes=1, d_hole=0.00437, Cd_inj=0.7, fuel="HTPB",
        L_grain=0.20, d_grain_outer=0.050, d_port_0=0.044,
        d_throat=0.0092, eps_exp=3.5, alpha_deg=15.0,
        eta_cstar=0.85, eta_nozzle=0.85, gamma=1.22, MW=26.0,
        rocket=dict(m_dry=20.0, Cd_body=1.625, d_body=0.14),
    ),
    "HyperTEK K240": dict(
        d_tank=0.058, L_tank=0.425, fill_frac=0.80, T_tank_0=293,
        n_holes=1, d_hole=0.003175, Cd_inj=0.7, fuel="HTPB",
        L_grain=0.20, d_grain_outer=0.050, d_port_0=0.041,
        d_throat=0.008, eps_exp=3.5, alpha_deg=15.0,
        eta_cstar=0.85, eta_nozzle=0.85, gamma=1.22, MW=26.0,
        rocket=dict(m_dry=20.0, Cd_body=1.625, d_body=0.14),
    ),
}

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

        for title, fields in (
            ("Oxidizer Tank (N2O)", _TANK_FIELDS),
            ("Injector", _INJ_FIELDS),
            ("Fuel Grain", _GRAIN_FIELDS),
            ("Nozzle / Combustion", _NOZZLE_FIELDS),
        ):
            group = QtWidgets.QGroupBox(title)
            gform = QtWidgets.QFormLayout(group)
            if fields is _GRAIN_FIELDS:
                gform.addRow("Fuel:", self.fuel_combo)
            for label, key, _factor, _dec in fields:
                edit = QtWidgets.QLineEdit()
                edit.setStyleSheet(_INPUT_STYLE)
                self._fields[key] = edit
                gform.addRow(label + ":", edit)
            form_layout.addWidget(group)

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
        return cfg

    def apply_config(self, cfg: dict):
        """Restore an engine design saved by get_config()."""
        if not cfg:
            return
        for key, value in cfg.items():
            if key == "fuel":
                idx = self.fuel_combo.findText(str(value))
                if idx >= 0:
                    self.fuel_combo.setCurrentIndex(idx)
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
        for _label, key, factor, dec in _TANK_FIELDS + _INJ_FIELDS + _GRAIN_FIELDS + _NOZZLE_FIELDS:
            if key in preset:
                self._fields[key].setText(f"{preset[key] * factor:.{dec}f}")
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

    # ---- build dataclasses from the form -----------------------------------
    def _read_engine(self) -> Engine:
        factors = {k: f for _l, k, f, _d in _TANK_FIELDS + _INJ_FIELDS + _GRAIN_FIELDS + _NOZZLE_FIELDS}
        kwargs = {}
        for key, factor in factors.items():
            text = self._fields[key].text().strip()
            if not text:
                raise ValueError(f"Missing value for '{key}'")
            value = float(text) / factor
            kwargs[key] = int(round(value)) if key == "n_holes" else value
        kwargs["fuel"] = FUELS[self.fuel_combo.currentText()]
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
