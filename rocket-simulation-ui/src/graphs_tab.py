"""Every graph the flight and the motor can produce, in one place.

The report used to carry a single four-panel summary figure. This is the
whole set - trajectory, aerodynamics, mass and balance, energy, the delta-v
budget, atmosphere, recovery, structures and the motor internals - grouped by
which part of the rocket they describe.

Graphs are drawn lazily. Building thirty-odd matplotlib figures up front
takes seconds and freezes the tab; only the selected one is rendered, and it
is re-rendered on demand rather than cached, so a new run never shows a stale
plot.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavToolbar
from PyQt5 import QtWidgets, QtCore

import theme

FT = 3.280839895


def _series(rows, key, scale=1.0):
    return [(r.get(key) or 0.0) * scale for r in rows]


def _has(rows, key):
    """Is this column present and ever non-zero?

    A graph of a column that is flat zero for the whole flight is noise, so
    those are dropped from the list rather than drawn empty.
    """
    if not rows or key not in rows[0]:
        return False
    return any(abs(r.get(key) or 0.0) > 0 for r in rows)


# ---------------------------------------------------------------------------
# Plot definitions. Each takes (axes, flight rows, engine rows) and draws.
# ---------------------------------------------------------------------------
def _line(ax, x, ys, xlabel, ylabel, title, labels=None, colors=None):
    colors = colors or theme.SERIES
    for i, y in enumerate(ys):
        ax.plot(x, y, lw=1.6, color=colors[i % len(colors)],
                label=(labels[i] if labels else None))
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if labels:
        ax.legend(fontsize=8)


def _trajectory(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "altitude_ft")],
          "Time (s)", "Altitude (ft)", "Altitude vs time")


def _ground_track(ax, f, e):
    _line(ax, _series(f, "downrange"), [_series(f, "altitude")],
          "Downrange (m)", "Altitude (m)", "Ground track")


def _velocity(ax, f, e):
    _line(ax, _series(f, "time"),
          [_series(f, "velocity"), _series(f, "horizontal_velocity"),
           _series(f, "airspeed")],
          "Time (s)", "Speed (m/s)", "Velocity components",
          ["vertical", "horizontal", "airspeed"])


def _mach(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "Mach")],
          "Time (s)", "Mach", "Mach number")


def _accel(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "accel_g")],
          "Time (s)", "Acceleration (g)", "G-load")


def _fpa(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "flight_path_angle_deg"),
                                   _series(f, "angle_from_vertical_deg")],
          "Time (s)", "Angle (deg)", "Flight path angle vs body tilt",
          ["flight path", "body tilt"])


def _drag(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "drag"), _series(f, "thrust")],
          "Time (s)", "Force (N)", "Thrust and drag", ["drag", "thrust"])


def _cd_parts(ax, f, e):
    _line(ax, _series(f, "time"),
          [_series(f, "cd_friction"), _series(f, "cd_base"),
           _series(f, "cd_wave"), _series(f, "cd_fins"),
           _series(f, "Cd_eff")],
          "Time (s)", "Cd", "Drag coefficient buildup",
          ["friction", "base", "wave", "fins", "effective"])


def _cd_mach(ax, f, e):
    asc = [r for r in f if (r.get("thrust") or 0) > 0 or (r.get("velocity") or 0) > 0]
    ax.plot(_series(asc, "Mach"), _series(asc, "Cd_body_eff"),
            lw=1.6, color=theme.SERIES[0])
    ax.set(xlabel="Mach", ylabel="Cd (body)", title="Cd vs Mach (ascent)")


def _q(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "q", 1e-3)],
          "Time (s)", "q (kPa)", "Dynamic pressure")


def _q_alpha(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "q_alpha")],
          "Time (s)", "q-alpha (Pa.deg)", "Aerodynamic bending load")


def _reynolds(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "reynolds")],
          "Time (s)", "Re", "Reynolds number")


def _drag_power(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "drag_power", 1e-3)],
          "Time (s)", "Power (kW)", "Power dissipated by drag")


def _mass(ax, f, e):
    _line(ax, _series(f, "time"),
          [_series(f, "mass"), _series(f, "propellant_remaining")],
          "Time (s)", "Mass (kg)", "Mass and propellant",
          ["total", "propellant left"])


def _cg_cp(ax, f, e):
    _line(ax, _series(f, "time"),
          [_series(f, "cg_m", 1000), _series(f, "cp_m", 1000)],
          "Time (s)", "Station from nose (mm)", "CG and CP migration",
          ["CG", "CP"])


def _stability(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "stability_cal")],
          "Time (s)", "Static margin (calibers)", "Stability margin")
    ax.axhspan(1.5, 4.0, color=theme.PALETTE["ok"], alpha=0.12)


def _inertia(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "pitch_inertia")],
          "Time (s)", "Pitch inertia (kg.m2)", "Pitch inertia")


def _thrust_weight(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "thrust_to_weight")],
          "Time (s)", "T/W", "Thrust-to-weight")
    ax.axhline(1.0, color=theme.PALETTE["critical"], ls="--", lw=1)


def _impulse(ax, f, e):
    _line(ax, _series(f, "time"),
          [_series(f, "impulse_ns"), _series(f, "drag_impulse_ns")],
          "Time (s)", "Impulse (N.s)", "Cumulative impulse",
          ["thrust", "drag"])


def _dv_budget(ax, f, e):
    _line(ax, _series(f, "time"),
          [_series(f, "dv_ideal"), _series(f, "dv_drag_loss"),
           _series(f, "dv_gravity_loss"), _series(f, "dv_net")],
          "Time (s)", "Delta-v (m/s)", "Delta-v budget",
          ["ideal", "drag loss", "gravity loss", "net"])


def _energy(ax, f, e):
    _line(ax, _series(f, "time"),
          [_series(f, "kinetic_energy", 1e-3),
           _series(f, "potential_energy", 1e-3),
           _series(f, "total_energy", 1e-3)],
          "Time (s)", "Energy (kJ)", "Energy", ["kinetic", "potential", "total"])


def _atmosphere(ax, f, e):
    ax.plot(_series(f, "rho_local"), _series(f, "altitude"),
            lw=1.6, color=theme.SERIES[0])
    ax.set(xlabel="Air density (kg/m3)", ylabel="Altitude (m)",
           title="Density vs altitude")


def _air_state(ax, f, e):
    _line(ax, _series(f, "time"),
          [_series(f, "temperature_k"), _series(f, "stagnation_temp_k")],
          "Time (s)", "Temperature (K)", "Ambient and recovery temperature",
          ["ambient", "recovery (skin)"])


def _sonic(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "speed_of_sound")],
          "Time (s)", "a (m/s)", "Local speed of sound")


def _descent(ax, f, e):
    ap = max(range(len(f)), key=lambda i: f[i].get("altitude") or 0.0)
    d = f[ap:]
    _line(ax, _series(d, "time"), [[-v for v in _series(d, "velocity")]],
          "Time (s)", "Descent rate (m/s)", "Descent rate after apogee")


def _canopy(ax, f, e):
    _line(ax, _series(f, "time"),
          [_series(f, "chute_fill"), _series(f, "cda_recovery")],
          "Time (s)", "Fill / Cd.A", "Canopy inflation and drag area",
          ["fill fraction", "Cd.A (m2)"])


def _terminal(ax, f, e):
    _line(ax, _series(f, "time"), [_series(f, "terminal_v_current")],
          "Time (s)", "Terminal velocity (m/s)", "Instantaneous terminal velocity")


# --- engine internals ------------------------------------------------------
def _e_pressure(ax, f, e):
    _line(ax, _series(e, "t"),
          [_series(e, "Pc", 1e-6), _series(e, "P_tank", 1e-6),
           _series(e, "P_exit", 1e-6)],
          "Time (s)", "Pressure (MPa)", "Chamber, tank and exit pressure",
          ["chamber", "tank", "exit"])


def _e_thrust(ax, f, e):
    _line(ax, _series(e, "t"), [_series(e, "thrust")],
          "Time (s)", "Thrust (N)", "Motor thrust")


def _e_of(ax, f, e):
    _line(ax, _series(e, "t"), [_series(e, "OF")],
          "Time (s)", "O/F", "Mixture ratio")


def _e_mdot(ax, f, e):
    _line(ax, _series(e, "t"),
          [_series(e, "mdot_ox"), _series(e, "mdot_fuel"),
           _series(e, "mdot_tot")],
          "Time (s)", "Mass flow (kg/s)", "Propellant flow",
          ["oxidiser", "fuel", "total"])


def _e_regression(ax, f, e):
    _line(ax, _series(e, "t"),
          [_series(e, "rdot", 1000), _series(e, "G_ox")],
          "Time (s)", "rdot (mm/s) / G_ox (kg/m2s)", "Regression and flux",
          ["regression rate", "oxidiser flux"])


def _e_port(ax, f, e):
    _line(ax, _series(e, "t"),
          [_series(e, "r_port", 1000), _series(e, "web_left", 1000)],
          "Time (s)", "mm", "Port radius and web remaining",
          ["port radius", "web left"])


def _e_perf(ax, f, e):
    _line(ax, _series(e, "t"),
          [_series(e, "cstar"), _series(e, "c_star_eff")],
          "Time (s)", "c* (m/s)", "Characteristic velocity",
          ["ideal", "delivered"])


def _e_isp(ax, f, e):
    _line(ax, _series(e, "t"),
          [_series(e, "Isp_inst"), _series(e, "Isp_cumulative")],
          "Time (s)", "Isp (s)", "Specific impulse",
          ["instantaneous", "cumulative"])


def _e_cf(ax, f, e):
    _line(ax, _series(e, "t"), [_series(e, "cf")],
          "Time (s)", "Cf", "Thrust coefficient")


def _e_tank(ax, f, e):
    _line(ax, _series(e, "t"),
          [_series(e, "m_liquid"), _series(e, "m_vapor"), _series(e, "m_fuel")],
          "Time (s)", "Mass (kg)", "Propellant remaining",
          ["liquid N2O", "vapour N2O", "fuel"])


def _e_tank_temp(ax, f, e):
    _line(ax, _series(e, "t"), [_series(e, "T_tank")],
          "Time (s)", "Tank temperature (K)", "Self-pressurising tank cooling")


def _e_injector(ax, f, e):
    _line(ax, _series(e, "t"),
          [_series(e, "inj_dP", 1e-6), _series(e, "inj_stiffness")],
          "Time (s)", "dP (MPa) / stiffness", "Injector drop and stiffness",
          ["injector dP", "dP/Pc"])


def _e_throat(ax, f, e):
    _line(ax, _series(e, "t"),
          [_series(e, "d_throat", 1000), _series(e, "throat_erosion", 1000)],
          "Time (s)", "mm", "Throat diameter and erosion",
          ["diameter", "erosion"])


def _e_progress(ax, f, e):
    _line(ax, _series(e, "t"),
          [_series(e, "burn_progress"), _series(e, "fill_frac")],
          "Time (s)", "Fraction", "Burn progress and tank fill",
          ["web burned", "tank fill"])


def _e_impulse(ax, f, e):
    _line(ax, _series(e, "t"), [_series(e, "impulse_ns")],
          "Time (s)", "Impulse (N.s)", "Cumulative motor impulse")


def _e_ratios(ax, f, e):
    _line(ax, _series(e, "t"),
          [_series(e, "port_to_throat"), _series(e, "Pe_over_Pamb")],
          "Time (s)", "Ratio", "Port/throat and exit pressure ratio",
          ["port/throat", "Pe/Pamb"])


# (category, title, draw fn, source) - source "flight" or "engine"
CATALOG = [
    ("Trajectory", "Altitude vs time", _trajectory, "flight"),
    ("Trajectory", "Ground track", _ground_track, "flight"),
    ("Trajectory", "Velocity components", _velocity, "flight"),
    ("Trajectory", "Mach number", _mach, "flight"),
    ("Trajectory", "G-load", _accel, "flight"),
    ("Trajectory", "Flight path angle", _fpa, "flight"),
    ("Aerodynamics", "Thrust and drag", _drag, "flight"),
    ("Aerodynamics", "Cd buildup", _cd_parts, "flight"),
    ("Aerodynamics", "Cd vs Mach", _cd_mach, "flight"),
    ("Aerodynamics", "Dynamic pressure", _q, "flight"),
    ("Aerodynamics", "q-alpha bending load", _q_alpha, "flight"),
    ("Aerodynamics", "Reynolds number", _reynolds, "flight"),
    ("Aerodynamics", "Drag power", _drag_power, "flight"),
    ("Mass & balance", "Mass and propellant", _mass, "flight"),
    ("Mass & balance", "CG and CP migration", _cg_cp, "flight"),
    ("Mass & balance", "Static margin", _stability, "flight"),
    ("Mass & balance", "Pitch inertia", _inertia, "flight"),
    ("Performance", "Thrust-to-weight", _thrust_weight, "flight"),
    ("Performance", "Cumulative impulse", _impulse, "flight"),
    ("Performance", "Delta-v budget", _dv_budget, "flight"),
    ("Performance", "Energy", _energy, "flight"),
    ("Atmosphere", "Density vs altitude", _atmosphere, "flight"),
    ("Atmosphere", "Ambient and recovery temperature", _air_state, "flight"),
    ("Atmosphere", "Speed of sound", _sonic, "flight"),
    ("Recovery", "Descent rate", _descent, "flight"),
    ("Recovery", "Canopy inflation", _canopy, "flight"),
    ("Recovery", "Terminal velocity", _terminal, "flight"),
    ("Engine", "Pressures", _e_pressure, "engine"),
    ("Engine", "Thrust", _e_thrust, "engine"),
    ("Engine", "Mixture ratio", _e_of, "engine"),
    ("Engine", "Propellant flow", _e_mdot, "engine"),
    ("Engine", "Regression and flux", _e_regression, "engine"),
    ("Engine", "Port and web", _e_port, "engine"),
    ("Engine", "Characteristic velocity", _e_perf, "engine"),
    ("Engine", "Specific impulse", _e_isp, "engine"),
    ("Engine", "Thrust coefficient", _e_cf, "engine"),
    ("Engine", "Propellant remaining", _e_tank, "engine"),
    ("Engine", "Tank temperature", _e_tank_temp, "engine"),
    ("Engine", "Injector", _e_injector, "engine"),
    ("Engine", "Throat and erosion", _e_throat, "engine"),
    ("Engine", "Burn progress", _e_progress, "engine"),
    ("Engine", "Cumulative impulse", _e_impulse, "engine"),
    ("Engine", "Port/throat and exit ratio", _e_ratios, "engine"),
]


class GraphGallery(QtWidgets.QWidget):
    """Pick a graph on the left, see it full size on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flight: list = []
        self._engine: list = []

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QtWidgets.QLabel("<b>Graphs</b>"))
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(lambda *_: self._draw())
        lv.addWidget(self.tree, 1)
        self.save_all = QtWidgets.QPushButton("Save All Graphs...")
        self.save_all.clicked.connect(self._save_all)
        self.save_all.setEnabled(False)
        lv.addWidget(self.save_all)
        left.setMinimumWidth(230)
        split.addWidget(left)

        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.figure = plt.Figure(figsize=(8, 6))
        theme.placeholder_figure(
            self.figure, "Run a simulation to draw the graphs.")
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumHeight(260)
        rv.addWidget(self.canvas, 1)
        rv.addWidget(NavToolbar(self.canvas, self))
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 950])

        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(4, 4, 4, 4)
        box.addWidget(split)

    # -- data ---------------------------------------------------------------
    def set_data(self, flight_rows, engine_rows):
        self._flight = list(flight_rows or [])
        self._engine = list(engine_rows or [])
        self._rebuild_tree()
        self.save_all.setEnabled(bool(self._flight or self._engine))
        self._draw()

    def _available(self):
        """Only offer graphs whose source data is actually present."""
        out = []
        for cat, title, fn, source in CATALOG:
            rows = self._flight if source == "flight" else self._engine
            if rows:
                out.append((cat, title, fn, source))
        return out

    def _rebuild_tree(self):
        self.tree.clear()
        groups: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for cat, title, fn, source in self._available():
            if cat not in groups:
                node = QtWidgets.QTreeWidgetItem([cat])
                node.setFlags(QtCore.Qt.ItemIsEnabled)
                self.tree.addTopLevelItem(node)
                node.setExpanded(True)
                groups[cat] = node
            leaf = QtWidgets.QTreeWidgetItem([title])
            leaf.setData(0, QtCore.Qt.UserRole, (title, fn, source))
            groups[cat].addChild(leaf)
        first = self.tree.topLevelItem(0)
        if first and first.childCount():
            self.tree.setCurrentItem(first.child(0))

    # -- drawing ------------------------------------------------------------
    def _selected(self):
        item = self.tree.currentItem()
        return item.data(0, QtCore.Qt.UserRole) if item else None

    def _draw(self):
        spec = self._selected()
        if not spec:
            return
        title, fn, source = spec
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        try:
            fn(ax, self._flight, self._engine)
        except Exception as exc:      # a bad column must not kill the tab
            self.figure.clear()
            theme.placeholder_figure(self.figure, f"{title}: {exc}")
            self.canvas.draw()
            return
        theme.style_axes(ax)
        theme.style_figure(self.figure)
        try:
            self.figure.tight_layout()
        except Exception:
            pass
        self.canvas.draw()

    def _save_all(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Save every graph as PNG")
        if not directory:
            return
        import os
        fig = plt.Figure(figsize=(8, 6))
        canvas = FigureCanvas(fig)
        written = 0
        for cat, title, fn, source in self._available():
            fig.clear()
            ax = fig.add_subplot(111)
            try:
                fn(ax, self._flight, self._engine)
            except Exception:
                continue
            theme.style_axes(ax)
            theme.style_figure(fig)
            try:
                fig.tight_layout()
            except Exception:
                pass
            safe = f"{cat}_{title}".replace(" ", "_").replace("/", "-")
            fig.savefig(os.path.join(directory, f"{safe}.png"), dpi=150,
                        facecolor=fig.get_facecolor())
            written += 1
        QtWidgets.QMessageBox.information(
            self, "Graphs saved", f"Wrote {written} graphs to {directory}.")
