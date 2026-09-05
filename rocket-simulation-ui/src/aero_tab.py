"""Aero Analysis: drag coefficient against Mach, the way RASAero presents it.

A single drag coefficient cannot describe a rocket that goes transonic. Cd
climbs steeply approaching Mach 1, peaks a little past it, then falls away -
and since drag dominates the altitude answer, that curve matters more than
almost anything else in the model.

This tab does three things:

  1. Sweeps this model's own component buildup across Mach at a chosen
     altitude, power-off and power-on, and shows what each component
     contributes.
  2. Reports centre of pressure and static margin across the same range,
     because both move transonically and a rocket can be stable on the pad
     and marginal at Mach 1.
  3. Imports and exports Cd(Mach) tables, so a curve from RASAero, CFD or a
     wind tunnel can be flown instead of the estimate.

Power-on differs from power-off only in base drag: the exhaust plume fills the
base region while the motor burns, which is why the two curves converge at
burnout.
"""
from __future__ import annotations

import os

from PyQt5 import QtWidgets, QtCore
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar

import aero as aero_mod
import theme
import datasheet

_P = theme.PALETTE

SWEEP_COLUMNS = [
    ("mach",            "Mach",              "-",      1.0,  3),
    ("speed_ms",        "True airspeed",     "m/s",    1.0,  1),
    ("cd_power_off",    "Cd power-off",      "-",      1.0,  5),
    ("cd_power_on",     "Cd power-on",       "-",      1.0,  5),
    ("cd_friction",     "Cd friction",       "-",      1.0,  5),
    ("cd_base",         "Cd base",           "-",      1.0,  5),
    ("cd_wave",         "Cd wave (nose)",    "-",      1.0,  5),
    ("cd_fins",         "Cd fins",           "-",      1.0,  5),
    ("cd_interference", "Cd interference",   "-",      1.0,  5),
    ("cda_power_off",   "Cd·A power-off",    "m2",     1.0,  6),
    ("cda_power_on",    "Cd·A power-on",     "m2",     1.0,  6),
    ("reynolds",        "Reynolds",          "-",      1.0,  0),
    ("cp_m",            "CP from nose",      "m",      1.0,  4),
    ("cg_m",            "CG from nose",      "m",      1.0,  4),
    ("stability_cal",   "Static margin",     "cal",    1.0,  3),
    ("altitude_m",      "Altitude",          "m",      1.0,  1),
]


class AeroAnalysisWidget(QtWidgets.QWidget):
    """Cd(Mach) sweep, plot, table, and table import/export."""

    def __init__(self, get_airframe=None, get_cg=None, on_table_changed=None,
                 get_site=None, parent=None):
        super().__init__(parent)
        self._get_airframe = get_airframe
        self._get_cg = get_cg
        # The launch site sets air density and viscosity, and friction drag is
        # Reynolds-dependent, so a sweep run against a default sea-level ISA
        # is not the vehicle's drag at the field it is flying from.
        self._get_site = get_site
        self._on_table_changed = on_table_changed
        self._rows = []
        self._imported = None          # aero.CdMachTable or None
        self._build_ui()

    # ---- UI ---------------------------------------------------------------
    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QtWidgets.QWidget()
        left.setMinimumWidth(340)
        lv = QtWidgets.QVBoxLayout(left)
        lv.setSpacing(6)

        intro = QtWidgets.QLabel(
            "<b>Aero Analysis</b><br>"
            "<span style='font-size:9pt'>Drag coefficient against Mach for the "
            "airframe on this tab, the way RASAero presents it: "
            "power-off and power-on curves, the component breakdown behind "
            "them, and stability across the same range.</span>")
        intro.setWordWrap(True)
        lv.addWidget(intro)

        sweep = QtWidgets.QGroupBox("Sweep")
        form = QtWidgets.QFormLayout(sweep)
        self.alt_input = QtWidgets.QLineEdit("0")
        self.alt_input.setToolTip(
            "Altitude to evaluate at. Skin friction is Reynolds-dependent, so "
            "the same vehicle at the same Mach has less friction drag high up "
            "than on the pad. Sweep at the altitude you care about.")
        form.addRow("Altitude (m):", self.alt_input)
        self.mach_min = QtWidgets.QLineEdit("0.05")
        form.addRow("Mach from:", self.mach_min)
        self.mach_max = QtWidgets.QLineEdit("5.0")
        self.mach_max.setToolTip(
            "The drag buildup is built and checked to Mach 5. Above that, air "
            "starts dissociating and this model does not represent it.")
        form.addRow("Mach to:", self.mach_max)
        self.mach_step = QtWidgets.QLineEdit("0.05")
        form.addRow("Mach step:", self.mach_step)
        self.cg_input = QtWidgets.QLineEdit("")
        self.cg_input.setPlaceholderText("blank = take from Airframe page")
        self.cg_input.setToolTip(
            "CG from the nose tip, for the static margin column. Blank uses "
            "the dry CG from the Airframe page.")
        form.addRow("CG from nose (m):", self.cg_input)
        lv.addWidget(sweep)

        self.run_button = QtWidgets.QPushButton("Run Sweep")
        self.run_button.clicked.connect(self.run_sweep)
        lv.addWidget(self.run_button)

        self.summary = QtWidgets.QLabel(
            "Run a sweep to see the drag curve for the current airframe.")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(
            f"color:{_P['text_dim']}; font-size:9pt; "
            f"border:1px solid {_P['border']}; padding:6px;")
        lv.addWidget(self.summary)

        table_group = QtWidgets.QGroupBox("Cd(Mach) table")
        tg = QtWidgets.QVBoxLayout(table_group)
        note = QtWidgets.QLabel(
            "A measured or CFD-derived curve beats this model's estimate. "
            "Import a two-column Mach,Cd file (RASAero export, RockSim, or "
            "your own spreadsheet) and the flight model flies that curve "
            "instead of the buildup.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{_P['text_dim']}; font-size:9pt;")
        tg.addWidget(note)

        row = QtWidgets.QHBoxLayout()
        self.import_button = QtWidgets.QPushButton("Import Cd(Mach)...")
        self.import_button.clicked.connect(self.import_table)
        row.addWidget(self.import_button)
        self.clear_button = QtWidgets.QPushButton("Use model")
        self.clear_button.setToolTip(
            "Stop using the imported table and go back to the computed "
            "component buildup.")
        self.clear_button.clicked.connect(self.clear_table)
        self.clear_button.setEnabled(False)
        row.addWidget(self.clear_button)
        tg.addLayout(row)

        row2 = QtWidgets.QHBoxLayout()
        self.export_button = QtWidgets.QPushButton("Export this sweep as Cd(Mach)")
        self.export_button.setToolTip(
            "Write the swept curve as a two-column Mach,Cd file.")
        self.export_button.clicked.connect(self.export_table)
        self.export_button.setEnabled(False)
        row2.addWidget(self.export_button)
        tg.addLayout(row2)

        self.table_status = QtWidgets.QLabel(
            "Flying the computed drag buildup.")
        self.table_status.setWordWrap(True)
        self.table_status.setStyleSheet(f"font-size:9pt; color:{_P['text_dim']};")
        tg.addWidget(self.table_status)
        lv.addWidget(table_group)

        lv.addStretch()
        self.error_label = QtWidgets.QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color:{_P['critical']};")
        lv.addWidget(self.error_label)
        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.tabs = QtWidgets.QTabWidget()

        plot_host = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(plot_host)
        pv.setContentsMargins(0, 0, 0, 0)
        self.figure = plt.Figure()
        self.canvas = FigureCanvas(self.figure)
        pv.addWidget(self.canvas)
        pv.addWidget(NavigationToolbar(self.canvas, self))
        self.tabs.addTab(plot_host, "Drag Curve")

        self.sheet = datasheet.DataSheet(SWEEP_COLUMNS, title="cd_vs_mach")
        self.tabs.addTab(self.sheet, "Sweep Data")
        rv.addWidget(self.tabs)
        splitter.addWidget(right)

        splitter.setSizes([360, 1100])
        layout.addWidget(splitter)

    # ---- helpers ----------------------------------------------------------
    def _f(self, edit, default):
        try:
            text = edit.text().strip()
            return float(text) if text else default
        except ValueError:
            return default

    def current_table(self):
        """The imported Cd(Mach) curve, or None to use the buildup."""
        return self._imported

    # ---- actions ----------------------------------------------------------
    def run_sweep(self):
        self.error_label.setText("")
        if not self._get_airframe:
            self.error_label.setText("No airframe source connected.")
            return
        try:
            airframe = self._get_airframe()
            site = None
            if self._get_site:
                try:
                    site = self._get_site()
                except Exception:
                    site = None
            if site is None:
                import atmosphere as atmos_mod
                site = atmos_mod.LaunchSite()
            cg = self._f(self.cg_input, None) if self.cg_input.text().strip() else None
            if cg is None and self._get_cg:
                try:
                    cg = self._get_cg()
                except Exception:
                    cg = None
            rows = aero_mod.drag_sweep(
                airframe, site,
                altitude_m=self._f(self.alt_input, 0.0),
                mach_min=max(0.01, self._f(self.mach_min, 0.05)),
                mach_max=self._f(self.mach_max, 5.0),
                mach_step=max(0.005, self._f(self.mach_step, 0.05)),
                cg_m=cg)
        except Exception as exc:
            self.error_label.setText(f"Sweep failed: {exc}")
            return
        if not rows:
            self.error_label.setText("Sweep produced no points - check the Mach range.")
            return

        self._rows = rows
        self.sheet.set_rows(rows)
        self.export_button.setEnabled(True)
        self._plot(rows)
        self._summarize(rows, airframe)

    def _summarize(self, rows, airframe):
        cds = [r["cd_power_off"] for r in rows]
        peak = max(rows, key=lambda r: r["cd_power_off"])
        sub = [r for r in rows if r["mach"] <= 0.3]
        sub_cd = sum(r["cd_power_off"] for r in sub) / len(sub) if sub else cds[0]
        text = (
            f"<b>{len(rows)} points, Mach {rows[0]['mach']:.2f}–{rows[-1]['mach']:.2f}</b>"
            f" at {rows[0]['altitude_m']:,.0f} m<br>"
            f"Low-subsonic Cd: <b>{sub_cd:.3f}</b><br>"
            f"Peak Cd: <b>{peak['cd_power_off']:.3f}</b> at Mach "
            f"<b>{peak['mach']:.2f}</b> "
            f"({peak['cd_power_off']/sub_cd - 1:+.0%} over subsonic)<br>"
            f"Cd at Mach 5 end: <b>{cds[-1]:.3f}</b><br>"
            f"Reference area: {airframe.reference_area*1e4:,.1f} cm²")
        stab = [r["stability_cal"] for r in rows if "stability_cal" in r]
        if stab:
            text += (f"<br>Static margin: <b>{min(stab):.2f}–{max(stab):.2f} cal</b>"
                     + ("  <span style='color:%s'>(goes below 1 cal)</span>" % _P['caution']
                        if min(stab) < 1.0 else ""))
        self.summary.setText(text)

    def _plot(self, rows):
        self.figure.clear()
        self.figure.patch.set_facecolor(_P["plot_bg"])
        mach = [r["mach"] for r in rows]

        ax = self.figure.add_subplot(211)
        ax2 = self.figure.add_subplot(212, sharex=ax)
        for a in (ax, ax2):
            a.set_facecolor(_P["plot_bg"])
            a.tick_params(colors=_P["text_dim"], labelsize=8)
            for sp in a.spines.values():
                sp.set_color(_P["plot_axes"])
            a.grid(True, color=_P["grid"], linewidth=0.5, alpha=0.6)

        ax.plot(mach, [r["cd_power_off"] for r in rows],
                color=_P["accent"], linewidth=2.0, label="Cd power-off")
        ax.plot(mach, [r["cd_power_on"] for r in rows],
                color=_P["text"], linewidth=1.4, linestyle="--",
                label="Cd power-on")
        if self._imported is not None:
            lo, hi = self._imported.mach_range
            inside = [m for m in mach if lo <= m <= hi]
            if inside:
                ax.plot(inside, [self._imported(m) for m in inside],
                        color=_P["ok"], linewidth=2.0, linestyle=":",
                        label=f"imported: {self._imported.name}")
        ax.set_ylabel("Cd", color=_P["text"], fontsize=9)
        ax.set_title("Drag coefficient vs Mach", color=_P["text"], fontsize=10)
        leg = ax.legend(fontsize=8, facecolor=_P["panel"], edgecolor=_P["border"])
        for txt in leg.get_texts():
            txt.set_color(_P["text"])

        # Component stack: what actually makes up the power-off curve.
        parts = [("cd_friction", "friction"), ("cd_base", "base"),
                 ("cd_wave", "wave (nose)"), ("cd_fins", "fins"),
                 ("cd_interference", "interference")]
        ax2.stackplot(mach, *[[r[k] for r in rows] for k, _l in parts],
                      labels=[l for _k, l in parts], alpha=0.85)
        ax2.set_xlabel("Mach", color=_P["text"], fontsize=9)
        ax2.set_ylabel("Cd contribution", color=_P["text"], fontsize=9)
        leg2 = ax2.legend(fontsize=8, loc="upper right",
                          facecolor=_P["panel"], edgecolor=_P["border"])
        for txt in leg2.get_texts():
            txt.set_color(_P["text"])

        self.figure.tight_layout()
        self.canvas.draw_idle()

    def import_table(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Cd(Mach) table", "",
            "Cd tables (*.csv *.txt);;All files (*)")
        if not path:
            return
        try:
            table = aero_mod.CdMachTable.from_csv(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self, "Could not read that file",
                f"{exc}\n\nExpected two numeric columns: Mach first, Cd "
                f"second. Header lines are skipped automatically.")
            return
        self._imported = table
        self.clear_button.setEnabled(True)
        self.table_status.setText(
            f"<b>Flying the imported curve.</b><br>{table.summary()}<br>"
            f"<span style='font-size:8pt'>Outside that Mach range the end "
            f"values are held flat rather than extrapolated.</span>")
        if self._rows:
            self._plot(self._rows)
        if self._on_table_changed:
            self._on_table_changed(table)

    def clear_table(self):
        self._imported = None
        self.clear_button.setEnabled(False)
        self.table_status.setText("Flying the computed drag buildup.")
        if self._rows:
            self._plot(self._rows)
        if self._on_table_changed:
            self._on_table_changed(None)

    def export_table(self):
        if not self._rows:
            return
        default = os.path.join(os.path.expanduser("~"), "cd_vs_mach.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Cd(Mach) table", default, "CSV Files (*.csv)")
        if not path:
            return
        try:
            table = aero_mod.CdMachTable(
                [(r["mach"], r["cd_power_off"]) for r in self._rows],
                name=os.path.basename(path))
            table.to_csv(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))
            return
        QtWidgets.QMessageBox.information(
            self, "Exported",
            f"Wrote {len(self._rows)} Mach,Cd rows to\n{path}")
