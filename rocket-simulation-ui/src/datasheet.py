"""Spreadsheet views for simulation output.

Two datasets get tabulated, and they are different animals:

  * the FLIGHT sheet - one row per trajectory sample, every state and
    derived quantity the 2-DOF model tracks
  * the ENGINE sheet - one row per internal-ballistics sample, the numbers
    that live inside the motor and never appear in a trajectory

Both are the same widget: a table with readable column headers and units, a
row-count note, and a CSV export that writes the FULL dataset rather than the
strided view on screen. Rendering every sample of a long flight is tens of
thousands of cells and locks the window up, so the table is strided; the
export is not.
"""
from __future__ import annotations

import csv
import math
import os

from PyQt5 import QtWidgets, QtCore

# key -> (heading, unit, scale applied to the stored value, decimals)
# Anything not listed still shows up, just with its raw key as the heading, so
# adding a new field to the model never silently drops it from the sheet.
FLIGHT_COLUMNS = [
    ("time",                    "Time",              "s",       1.0,    3),
    ("altitude",                "Altitude",          "m",       1.0,    2),
    ("altitude_ft",             "Altitude",          "ft",      1.0,    1),
    ("downrange",               "Downrange",         "m",       1.0,    2),
    ("velocity",                "Vertical vel",      "m/s",     1.0,    3),
    ("horizontal_velocity",     "Horizontal vel",    "m/s",     1.0,    3),
    ("ground_speed",            "Ground speed",      "m/s",     1.0,    3),
    ("airspeed",                "Airspeed",          "m/s",     1.0,    3),
    ("Mach",                    "Mach",              "-",       1.0,    4),
    ("acceleration",            "Acceleration",      "m/s2",    1.0,    3),
    ("accel_total",             "Accel (total)",     "m/s2",    1.0,    3),
    ("accel_g",                 "Accel",             "g",       1.0,    3),
    ("thrust",                  "Thrust",            "N",       1.0,    2),
    ("drag",                    "Drag",              "N",       1.0,    3),
    ("mass",                    "Mass",              "kg",      1.0,    4),
    ("propellant_remaining",    "Propellant left",   "kg",      1.0,    4),
    ("mdot",                    "Mass flow",         "kg/s",    1.0,    5),
    ("q",                       "Dynamic pressure",  "Pa",      1.0,    2),
    ("rho_local",               "Air density",       "kg/m3",   1.0,    5),
    ("temperature_k",           "Air temperature",   "K",       1.0,    2),
    ("pressure_pa",             "Air pressure",      "Pa",      1.0,    1),
    ("Cd_eff",                  "Cd (effective)",    "-",       1.0,    4),
    ("Cd_body_eff",             "Cd (body)",         "-",       1.0,    4),
    ("cd_friction",             "Cd friction",       "-",       1.0,    5),
    ("cd_base",                 "Cd base",           "-",       1.0,    5),
    ("cd_wave",                 "Cd wave",           "-",       1.0,    5),
    ("cd_fins",                 "Cd fins",           "-",       1.0,    5),
    ("reynolds",                "Reynolds",          "-",       1.0,    0),
    ("A_eff",                   "Reference area",    "m2",      1.0,    5),
    ("cda_recovery",            "Recovery Cd.A",     "m2",      1.0,    4),
    ("chute_fill",              "Canopy fill",       "-",       1.0,    3),
    ("recovery_deployed",       "Deployed",          "",        None,   0),
    ("cg_m",                    "CG from nose",      "m",       1.0,    4),
    ("cp_m",                    "CP from nose",      "m",       1.0,    4),
    ("stability_cal",           "Stability",         "cal",     1.0,    3),
    ("pitch_inertia",           "Pitch inertia",     "kg.m2",   1.0,    4),
    ("angle_from_vertical_deg", "Tilt",              "deg",     1.0,    3),
    ("angle_of_attack_deg",     "Angle of attack",   "deg",     1.0,    3),
    ("wind_speed",              "Wind speed",        "m/s",     1.0,    3),
    ("terminal_v_current",      "Terminal velocity", "m/s",     1.0,    3),
    ("ballistic_coeff_current", "Ballistic coeff",   "kg/m2",   1.0,    2),
    ("on_rail",                 "On rail",           "",        None,   0),
    ("chute_deployed",          "Chute out",         "",        None,   0),

    # --- derived in flight_rows(): reporting quantities, not model state ---
    ("downrange_ft",            "Downrange",         "ft",      1.0,    1),
    ("airspeed_fps",            "Airspeed",          "ft/s",    1.0,    2),
    ("speed_of_sound",          "Speed of sound",    "m/s",     1.0,    2),
    ("mach_angle_deg",          "Mach angle",        "deg",     1.0,    2),
    ("flight_path_angle_deg",   "Flight path angle", "deg",     1.0,    3),
    ("gravity",                 "Local gravity",     "m/s2",    1.0,    5),
    ("thrust_to_weight",        "Thrust/weight",     "-",       1.0,    3),
    ("mass_ratio",              "Mass ratio",        "-",       1.0,    4),
    ("q_psf",                   "Dynamic pressure",  "lb/ft2",  1.0,    2),
    ("q_alpha",                 "q-alpha",           "Pa.deg",  1.0,    1),
    ("stagnation_temp_k",       "Recovery temp",     "K",       1.0,    2),
    ("kinetic_energy",          "Kinetic energy",    "J",       1.0,    1),
    ("potential_energy",        "Potential energy",  "J",       1.0,    1),
    ("total_energy",            "Total energy",      "J",       1.0,    1),
    ("specific_energy",         "Specific energy",   "J/kg",    1.0,    2),
    ("drag_power",              "Drag power",        "W",       1.0,    1),
    ("impulse_ns",              "Impulse to date",   "N.s",     1.0,    2),
    ("drag_impulse_ns",         "Drag impulse",      "N.s",     1.0,    2),
    ("dv_ideal",                "Delta-v (ideal)",   "m/s",     1.0,    2),
    ("dv_drag_loss",            "Delta-v drag loss", "m/s",     1.0,    2),
    ("dv_gravity_loss",         "Delta-v grav loss", "m/s",     1.0,    2),
    ("dv_net",                  "Delta-v (net)",     "m/s",     1.0,    2),

    # --- diagnostics: the drag limiter's working, kept last ---
    ("drag_signed",             "Drag (signed)",     "N",       1.0,    3),
    ("drag_raw_signed",         "Drag raw",          "N",       1.0,    3),
    ("drag_signed_uncapped",    "Drag uncapped",     "N",       1.0,    3),
    ("drag_cap_applied",        "Drag cap hit",      "",        None,   0),
    ("drag_cap_method",         "Drag cap method",   "",        None,   0),
    ("rocket_drag_signed_raw",  "Body drag raw",     "N",       1.0,    3),
    ("rocket_drag_signed_smoothed", "Body drag",     "N",       1.0,    3),
    ("chute_drag_signed_raw",   "Canopy drag raw",   "N",       1.0,    3),
    ("chute_drag_signed_smoothed", "Canopy drag",    "N",       1.0,    3),
    ("chute_drag_signed_smoothed_uncapped", "Canopy drag uncapped", "N", 1.0, 3),
    ("terminal_v_body",         "Terminal v (body)", "m/s",     1.0,    3),
    ("ballistic_coeff_body",    "Ballistic coeff (body)", "kg/m2", 1.0, 2),
    ("dry_mass",                "Dry mass",          "kg",      1.0,    4),
    ("initial_mass",            "Liftoff mass",      "kg",      1.0,    4),
    ("propellant_mass",         "Propellant loaded", "kg",      1.0,    4),
    ("sim_version",             "Sim version",       "",        None,   0),
]

ENGINE_COLUMNS = [
    ("t",              "Time",                "s",        1.0,    4),
    ("thrust",         "Thrust",              "N",        1.0,    2),
    ("Pc",             "Chamber pressure",    "MPa",      1e-6,   4),
    ("Pc_psi",         "Chamber pressure",    "psi",      1.0,    1),
    ("P_tank",         "Tank pressure",       "MPa",      1e-6,   4),
    ("P_tank_psi",     "Tank pressure",       "psi",      1.0,    1),
    ("P_exit",         "Nozzle exit pressure","MPa",      1e-6,   5),
    ("T_tank",         "Tank temperature",    "K",        1.0,    2),
    ("T_tank_c",       "Tank temperature",    "C",        1.0,    2),
    ("inj_dP",         "Injector dP",         "MPa",      1e-6,   4),
    ("inj_stiffness",  "Injector stiffness",  "dP/Pc",    1.0,    4),
    ("Pc_over_Pt",     "Pc / Ptank",          "-",        1.0,    4),
    ("mdot_ox",        "Oxidiser flow",       "kg/s",     1.0,    5),
    ("mdot_fuel",      "Fuel flow",           "kg/s",     1.0,    5),
    ("mdot_tot",       "Total flow",          "kg/s",     1.0,    5),
    ("mdot_vent",      "Vent flow",           "kg/s",     1.0,    6),
    ("OF",             "O/F ratio",           "-",        1.0,    3),
    ("cstar",          "c* (ideal)",          "m/s",      1.0,    1),
    ("c_star_eff",     "c* (delivered)",      "m/s",      1.0,    1),
    ("cf",             "Thrust coefficient",  "-",        1.0,    4),
    ("Isp_inst",       "Isp (instantaneous)", "s",        1.0,    2),
    ("G_ox",           "Oxidiser flux",       "kg/m2s",   1.0,    2),
    ("rdot",           "Regression rate",     "mm/s",     1000.0, 4),
    ("r_port",         "Port radius",         "mm",       1000.0, 3),
    ("A_port",         "Port area",           "mm2",      1e6,    2),
    ("web_left",       "Web remaining",       "mm",       1000.0, 3),
    ("d_throat",       "Throat diameter",     "mm",       1000.0, 4),
    ("eps",            "Expansion ratio",     "-",        1.0,    3),
    ("L_star",         "L* (V_c/At)",         "m",        1.0,    4),
    ("t_residence",    "Gas residence time",  "ms",       1000.0, 4),
    ("m_ox",           "Oxidiser remaining",  "kg",       1.0,    5),
    ("m_liquid",       "Liquid N2O",          "kg",       1.0,    5),
    ("m_vapor",        "Vapour N2O",          "kg",       1.0,    5),
    ("fill_frac",      "Tank fill",           "-",        1.0,    4),
    ("m_fuel",         "Fuel remaining",      "kg",       1.0,    5),

    # --- derived in engine_rows() ---
    ("A_throat",       "Throat area",         "mm2",      1e6,    3),
    ("A_exit",         "Exit area",           "mm2",      1e6,    2),
    ("port_to_throat", "Port / throat area",  "-",        1.0,    3),
    ("throat_erosion", "Throat erosion",      "mm",       1000.0, 4),
    ("G_throat",       "Throat mass flux",    "kg/m2s",   1.0,    1),
    ("Pc_over_Pe",     "Pc / Pexit",          "-",        1.0,    3),
    ("Pe_over_Pamb",   "Pexit / Pamb (SL)",   "-",        1.0,    4),
    ("port_LD",        "Port L/D",            "-",        1.0,    3),
    ("port_growth",    "Port radius growth",  "mm",       1000.0, 4),
    ("web_burned",     "Web burned",          "mm",       1000.0, 3),
    ("burn_progress",  "Burn progress",       "-",        1.0,    4),
    ("ox_consumed",    "Oxidiser used",       "kg",       1.0,    5),
    ("fuel_consumed",  "Fuel used",           "kg",       1.0,    5),
    ("prop_consumed",  "Propellant used",     "kg",       1.0,    5),
    ("ullage_frac",    "Tank ullage",         "-",        1.0,    4),
    ("cstar_efficiency", "c* efficiency",     "-",        1.0,    4),
    ("v_exhaust_eff",  "Effective exhaust v", "m/s",      1.0,    1),
    ("impulse_ns",     "Impulse to date",     "N.s",      1.0,    2),
    ("Isp_cumulative", "Isp (cumulative)",    "s",        1.0,    2),
    ("expansion_ratio", "Expansion ratio",    "-",        1.0,    3),
]

PSI = 6894.757
G0 = 9.80665
FT_PER_M = 3.280839895
PSF_PER_PA = 0.020885434        # Pa -> lb/ft^2
GAMMA_AIR, R_AIR = 1.4, 287.058
P_AMB_SL = 101325.0


def flight_rows(rows):
    """Augment trajectory rows with the derived quantities the sheet reports.

    The 2-DOF model carries the state it needs to integrate; everything a
    person actually reads a flight sheet FOR - energy, impulse, the delta-v
    the motor produced and where it went, structural load indicators - is a
    function of that state and is derived here. Keeping it out of the
    integrator means adding a reported quantity never risks the trajectory.

    Cumulative columns are trapezoidal integrals over the sample times, so
    they are only as fine as the output step.
    """
    out = []
    impulse = drag_impulse = dv_ideal = dv_drag = dv_grav = 0.0
    prev = None
    for r in rows:
        row = dict(r)
        t = row.get("time", 0.0)
        mass = row.get("mass", 0.0) or 0.0
        g = row.get("gravity", G0)
        alt = row.get("altitude", 0.0)
        thrust = row.get("thrust", 0.0)
        drag = row.get("drag", 0.0)
        mach = row.get("Mach", 0.0)
        # Kinetic energy uses speed over the ground, not airspeed: it is the
        # vehicle's energy in the launch frame, and wind must not add to it.
        speed = row.get("ground_speed") or math.hypot(
            row.get("velocity", 0.0), row.get("horizontal_velocity", 0.0))

        row["altitude_ft"] = alt * FT_PER_M
        row["downrange_ft"] = row.get("downrange", 0.0) * FT_PER_M
        row["accel_g"] = row.get(
            "accel_total", row.get("acceleration", 0.0)) / G0
        row["airspeed_fps"] = row.get("airspeed", 0.0) * FT_PER_M
        row["gravity"] = g

        temp = row.get("temperature_k", 0.0)
        a_sound = math.sqrt(GAMMA_AIR * R_AIR * temp) if temp > 0 else 0.0
        row["speed_of_sound"] = a_sound
        # Mach angle only exists supersonically; below Mach 1 there is no cone.
        row["mach_angle_deg"] = (math.degrees(math.asin(1.0 / mach))
                                 if mach > 1.0 else 0.0)
        # Recovery temperature at the skin, same 0.9 turbulent factor the
        # thermal checks (T-01/T-02) grade against.
        row["stagnation_temp_k"] = (
            temp * (1 + 0.9 * (GAMMA_AIR - 1) / 2 * mach * mach)
            if temp > 0 else 0.0)

        # Flight path angle of the velocity vector: +90 straight up, 0
        # horizontal, -90 straight down. Distinct from body tilt, which is
        # where the airframe points. Measured against the MAGNITUDE of the
        # horizontal component - signing it folds the direction of travel into
        # the climb angle, which read -179 deg at apogee (level flight going
        # the other way) instead of the ~0 a flight path angle should show.
        row["flight_path_angle_deg"] = math.degrees(math.atan2(
            row.get("velocity", 0.0),
            abs(row.get("horizontal_velocity", 0.0))))

        weight = mass * g
        row["thrust_to_weight"] = thrust / weight if weight > 0 else 0.0
        row["mass_ratio"] = (mass / row["initial_mass"]
                             if row.get("initial_mass") else 0.0)

        ke = 0.5 * mass * speed * speed
        pe = mass * g * alt
        row["kinetic_energy"] = ke
        row["potential_energy"] = pe
        row["total_energy"] = ke + pe
        row["specific_energy"] = (ke + pe) / mass if mass > 0 else 0.0
        row["drag_power"] = drag * row.get("airspeed", 0.0)

        q = row.get("q", 0.0)
        row["q_psf"] = q * PSF_PER_PA
        # q-alpha: dynamic pressure times angle of attack is the standard
        # proxy for aerodynamic bending load on the airframe.
        row["q_alpha"] = q * abs(row.get("angle_of_attack_deg", 0.0))

        if prev is not None:
            dt = t - prev["time"]
            if dt > 0:
                impulse += 0.5 * dt * (thrust + prev["thrust"])
                drag_impulse += 0.5 * dt * (drag + prev["drag"])
                dv_ideal += 0.5 * dt * (prev["a_thrust"] + (
                    thrust / mass if mass > 0 else 0.0))
                dv_drag += 0.5 * dt * (prev["a_drag"] + (
                    drag / mass if mass > 0 else 0.0))
                dv_grav += 0.5 * dt * (prev["a_grav"] + g * math.cos(
                    math.radians(row.get("angle_from_vertical_deg", 0.0))))
        row["impulse_ns"] = impulse
        row["drag_impulse_ns"] = drag_impulse
        # Where the motor's delta-v went. dv_ideal is what the thrust would
        # have bought in vacuum with no gravity; the two losses are what the
        # atmosphere and the climb took back.
        row["dv_ideal"] = dv_ideal
        row["dv_drag_loss"] = dv_drag
        row["dv_gravity_loss"] = dv_grav
        row["dv_net"] = dv_ideal - dv_drag - dv_grav

        prev = {"time": t, "thrust": thrust, "drag": drag,
                "a_thrust": thrust / mass if mass > 0 else 0.0,
                "a_drag": drag / mass if mass > 0 else 0.0,
                "a_grav": g * math.cos(math.radians(
                    row.get("angle_from_vertical_deg", 0.0)))}
        out.append(row)
    return out


def engine_rows(result):
    """Turn an EngineModel result dict into a list of row dicts.

    The model returns parallel numpy arrays; the sheet wants rows. A few
    convenience columns (psi, Celsius) are derived here rather than being
    carried around in the physics.
    """
    if not result or "t" not in result:
        return []
    n = len(result["t"])
    keys = [k for k, v in result.items()
            if hasattr(v, "__len__") and len(v) == n]
    rows = []
    # Initial loads are scalars on the result, not per-sample arrays; they are
    # what "consumed so far" and "burn progress" are measured against.
    m_ox0 = float(result.get("m_l0", 0.0)) + float(result.get("m_v0", 0.0))
    m_f0 = float(result.get("m_f0", 0.0))
    d_throat_0 = float(result["d_throat"][0]) if "d_throat" in result else 0.0
    r_port_0 = float(result["r_port"][0]) if "r_port" in result else 0.0
    web_0 = float(result["web_left"][0]) if "web_left" in result else 0.0
    # Scalar, not an array: used to derive port_LD below rather
    # than repeated down a column of identical values.
    L_grain = float(result.get("L_grain", 0.0))

    impulse = 0.0
    prev = None
    for i in range(n):
        row = {k: float(result[k][i]) for k in keys}
        row["Pc_psi"] = row.get("Pc", 0.0) / PSI
        row["P_tank_psi"] = row.get("P_tank", 0.0) / PSI
        row["T_tank_c"] = row.get("T_tank", 0.0) - 273.15

        # Areas. The model carries diameters and radii; the ratios that get
        # designed against are areas.
        d_th = row.get("d_throat", 0.0)
        a_throat = math.pi * (d_th / 2.0) ** 2
        a_port = row.get("A_port", 0.0)
        row["A_throat"] = a_throat
        row["A_exit"] = a_throat * row.get("eps", row.get("expansion_ratio", 0.0))
        # Port-to-throat drives whether the port chokes before the nozzle
        # does; below about 2 the grain starts behaving like a second throat.
        row["port_to_throat"] = a_port / a_throat if a_throat > 0 else 0.0
        row["throat_erosion"] = d_th - d_throat_0
        # Mass flux through the throat, the number that sets erosion rate.
        row["G_throat"] = (row.get("mdot_tot", 0.0) / a_throat
                           if a_throat > 0 else 0.0)

        # Nozzle pressure ratios. Pe/Pamb below ~0.4 is where Summerfield
        # says the flow separates off the wall (P-10 grades this).
        p_exit = row.get("P_exit", 0.0)
        pc = row.get("Pc", 0.0)
        row["Pc_over_Pe"] = pc / p_exit if p_exit > 0 else 0.0
        row["Pe_over_Pamb"] = p_exit / P_AMB_SL

        # Propellant consumed, and how far through the web the burn is.
        row["ox_consumed"] = max(0.0, m_ox0 - row.get("m_ox", 0.0))
        row["fuel_consumed"] = max(0.0, m_f0 - row.get("m_fuel", 0.0))
        row["prop_consumed"] = row["ox_consumed"] + row["fuel_consumed"]
        row["web_burned"] = max(0.0, web_0 - row.get("web_left", 0.0))
        row["burn_progress"] = (row["web_burned"] / web_0) if web_0 > 0 else 0.0
        row["port_growth"] = row.get("r_port", 0.0) - r_port_0
        # Port length-to-diameter: long thin ports run oxidiser-rich at the
        # head end and fuel-rich at the aft.
        d_port = 2.0 * row.get("r_port", 0.0)
        row["port_LD"] = (L_grain / d_port) if d_port > 0 else 0.0

        row["ullage_frac"] = 1.0 - row.get("fill_frac", 0.0)
        # Delivered vs theoretical c*: the combustion efficiency actually
        # achieved at this instant.
        cstar = row.get("cstar", 0.0)
        row["cstar_efficiency"] = (row.get("c_star_eff", 0.0) / cstar
                                   if cstar > 0 else 0.0)
        # Effective exhaust velocity, the c*-Cf product that Isp restates.
        row["v_exhaust_eff"] = row.get("c_star_eff", 0.0) * row.get("cf", 0.0)

        thrust = row.get("thrust", 0.0)
        if prev is not None:
            dt = row.get("t", 0.0) - prev[0]
            if dt > 0:
                impulse += 0.5 * dt * (thrust + prev[1])
        row["impulse_ns"] = impulse
        # Isp integrated to this point, which is what the motor has actually
        # delivered so far rather than its instantaneous value.
        used = row["prop_consumed"]
        row["Isp_cumulative"] = impulse / (used * G0) if used > 0 else 0.0
        prev = (row.get("t", 0.0), thrust)

        rows.append(row)
    return rows


def _columns_for(rows, spec):
    """Ordered (key, heading, unit, scale, decimals) for the keys present.

    Anything in the data but not in the spec is appended with its raw key, so
    a new model output shows up in the sheet without being wired in by hand.
    """
    if not rows:
        return []
    present = set(rows[0].keys())
    cols = [c for c in spec if c[0] in present]
    known = {c[0] for c in spec}
    for key in rows[0].keys():
        if key not in known:
            cols.append((key, key.replace("_", " ").capitalize(), "", 1.0, 4))
    return cols


class DataSheet(QtWidgets.QWidget):
    """A table view over a list of row dicts, with full-dataset CSV export."""

    MAX_ROWS = 600

    def __init__(self, spec, title="data", parent=None):
        super().__init__(parent)
        self._spec = spec
        self._title = title
        self._rows = []
        self._cols = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        bar = QtWidgets.QHBoxLayout()
        self.info = QtWidgets.QLabel("No data yet - run a simulation.")
        self.info.setWordWrap(True)
        bar.addWidget(self.info, 1)
        self.export_button = QtWidgets.QPushButton("Export CSV")
        self.export_button.setToolTip(
            "Write every sample to a CSV file, not just the rows shown here.")
        self.export_button.clicked.connect(self.export_csv)
        self.export_button.setEnabled(False)
        bar.addWidget(self.export_button)
        layout.addLayout(bar)

        self.table = QtWidgets.QTableWidget()
        self.table.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                 QtWidgets.QSizePolicy.Expanding)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)

    # -- data ---------------------------------------------------------------
    def set_rows(self, rows):
        self._rows = rows or []
        self._cols = _columns_for(self._rows, self._spec)
        self.export_button.setEnabled(bool(self._rows))
        if not self._rows:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.info.setText("No data yet - run a simulation.")
            return

        stride = max(1, len(self._rows) // self.MAX_ROWS)
        shown = self._rows[::stride]
        self.table.setColumnCount(len(self._cols))
        self.table.setRowCount(len(shown))
        self.table.setHorizontalHeaderLabels(
            ["%s\n(%s)" % (h, u) if u else h for _, h, u, _, _ in self._cols])
        for r, row in enumerate(shown):
            for c, (key, _, _, scale, dec) in enumerate(self._cols):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(
                    _fmt(row.get(key), scale, dec)))
        self.table.resizeColumnsToContents()
        note = ("%d samples, %d columns. " % (len(self._rows), len(self._cols)))
        if stride > 1:
            note += ("Showing every %dth row to keep the table responsive - "
                     "Export CSV writes all of them." % stride)
        else:
            note += "All rows shown."
        self.info.setText(note)

    def export_csv(self):
        if not self._rows:
            return
        default = os.path.join(os.path.expanduser("~"),
                               "jarvis_%s.csv" % self._title)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export %s" % self._title, default, "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["%s (%s)" % (h, u) if u else h
                            for _, h, u, _, _ in self._cols])
                w.writerow([k for k, _, _, _, _ in self._cols])
                for row in self._rows:
                    w.writerow([_fmt(row.get(k), s, d)
                                for k, _, _, s, d in self._cols])
            QtWidgets.QMessageBox.information(
                self, "Export complete",
                "Wrote %d rows and %d columns to\n%s"
                % (len(self._rows), len(self._cols), path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Export failed", "Could not write the CSV:\n%s" % exc)


def _fmt(value, scale, decimals):
    if value is None:
        return ""
    if scale is None or isinstance(value, (str, bool)):
        return str(value)
    try:
        return "%.*f" % (decimals, float(value) * scale)
    except (TypeError, ValueError):
        return str(value)
