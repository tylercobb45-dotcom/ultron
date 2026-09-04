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
    ("angle_from_vertical_deg", "Tilt",              "deg",     1.0,    3),
    ("angle_of_attack_deg",     "Angle of attack",   "deg",     1.0,    3),
    ("wind_speed",              "Wind speed",        "m/s",     1.0,    3),
    ("terminal_v_current",      "Terminal velocity", "m/s",     1.0,    3),
    ("ballistic_coeff_current", "Ballistic coeff",   "kg/m2",   1.0,    2),
    ("on_rail",                 "On rail",           "",        None,   0),
    ("chute_deployed",          "Chute out",         "",        None,   0),
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
]

PSI = 6894.757


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
    for i in range(n):
        row = {k: float(result[k][i]) for k in keys}
        row["Pc_psi"] = row.get("Pc", 0.0) / PSI
        row["P_tank_psi"] = row.get("P_tank", 0.0) / PSI
        row["T_tank_c"] = row.get("T_tank", 0.0) - 273.15
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
