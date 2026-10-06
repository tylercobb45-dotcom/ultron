"""The Engine Designer tab: say what the engine has to do, get an engine.

A front end over ``motor_designer``, which owns all the physics. You give it
the brief - total impulse, thrust, burn time, a minimum Isp, the ceilings it
must stay under, how long and how heavy it is allowed to be, and the fuel -
and it hands back a complete hybrid: tank, injector, grain, chamber and
nozzle, with the thrust curve the real engine solver produces for it.

It is a separate tab from the Engine tab on purpose. The Engine tab is for
working on ONE motor, field by field; this one is for asking "what motor do I
need?" before there is one. Nothing here touches the loaded rocket until you
press Send to Engine tab, which hands the design over through the Engine
tab's own loader, so a motor made here is in exactly the state one made there
would be.
"""
from __future__ import annotations

import csv
import math
import traceback

from PyQt5 import QtWidgets, QtCore
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

import engine_lab
import motor_designer
import theme
import unit_fields
from hybrid_sim.config import FUELS, INJECTOR_TYPES


# What the engine has to DO. (label, Requirements attribute, unit-field key,
# tooltip). The same shape as the Engine tab's requirements form, and the
# tooltips for the fields the two share are the same text, so the two forms
# cannot explain one requirement two different ways.
_PERFORMANCE_FIELDS = [
    f for f in engine_lab._REQ_FIELDS
    if f[1] in ("total_impulse_ns", "avg_thrust_n", "burn_time_s",
                "min_isp_s", "max_peak_thrust_n", "max_chamber_pressure_pa")]

# How much room, and how much mass, it is allowed.
_ENVELOPE_FIELDS = [
    f for f in engine_lab._REQ_FIELDS
    if f[1] in ("max_diameter_m", "max_length_m")] + [
    ("Minimum length", "min_length_m", "req_min_length",
     "Shortest the assembled motor may be - for a motor bay it has to fill, "
     "or mounts it has to reach. Met by making the motor narrower: the same "
     "oxidiser in a slimmer tank is a longer tank, and a lighter one."),
    ("Maximum mass", "max_motor_mass_kg", "req_max_motor_mass",
     "Heaviest the motor may be as it sits on the pad: hardware plus "
     "propellant. The hardware is an estimate from the walls the pressures "
     "need, plus 10% for fittings."),
]

# Display order inside the envelope group: diameter, then the length range,
# then mass.
_ENVELOPE_ORDER = ("max_diameter_m", "min_length_m", "max_length_m",
                   "max_motor_mass_kg")


class EngineDesignerTab(QtWidgets.QWidget):
    """Generate a complete hybrid engine, and its thrust curve, from a brief."""

    def __init__(self, on_send_to_engine=None, parent=None):
        super().__init__(parent)
        # Called with the DesignResult when the user sends a design on.
        # Supplied by the main window, which owns the Engine tab.
        self._on_send = on_send_to_engine
        self._fields = {}
        self._design = None
        self._build_ui()

    # ---- construction ----------------------------------------------------
    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        left_inner = QtWidgets.QWidget()
        left_inner.setMinimumWidth(430)
        lv = QtWidgets.QVBoxLayout(left_inner)

        intro = QtWidgets.QLabel(
            "<b>Engine Designer</b> &mdash; say what the engine has to do "
            "and how much room and mass it has, and get a complete hybrid "
            "engine back: tank, injector, fuel grain, chamber and nozzle, "
            "with its thrust curve.<br><br>"
            "Every box is optional except some idea of size: a total "
            "impulse, or a thrust and a burn time. A blank box is not a "
            "requirement. Every design is checked by running the real engine "
            "solver, not the estimate it started from, and anything it could "
            "not meet is reported, not hidden.")
        intro.setWordWrap(True)
        lv.addWidget(intro)

        lv.addWidget(self._field_group(
            "What the engine has to do", _PERFORMANCE_FIELDS))
        by_key = {f[1]: f for f in _ENVELOPE_FIELDS}
        lv.addWidget(self._field_group(
            "Size and mass limits", [by_key[k] for k in _ENVELOPE_ORDER]))

        build = QtWidgets.QGroupBox("Propellant and build")
        form = QtWidgets.QFormLayout(build)
        self.fuel_combo = QtWidgets.QComboBox()
        self.fuel_combo.addItem("Choose the best fuel")
        self.fuel_combo.addItems(list(FUELS.keys()))
        self.fuel_combo.setToolTip(
            "Left on 'choose', the designer sizes an engine with each of the "
            "well-characterised fuels (%s) and keeps whichever meets the "
            "brief best." % ", ".join(motor_designer.AUTO_FUELS))
        form.addRow("Fuel:", self.fuel_combo)

        self.injector_combo = QtWidgets.QComboBox()
        self.injector_combo.addItem("Choose for me")
        self.injector_combo.addItems(list(INJECTOR_TYPES.keys()))
        self.injector_combo.setToolTip(
            "Injector style. It sets the discharge coefficient and how many "
            "holes the pattern is allowed to have.")
        form.addRow("Injector:", self.injector_combo)

        self.tank_temp = unit_fields.UnitField(unit_fields.FIELDS["tank_temp"])
        # 20.0 C exactly. 293.0 K reads back as "19.9" in a one-decimal box.
        self.tank_temp.set_value_si(293.15)
        self.tank_temp.setToolTip(
            "Nitrous temperature at ignition. N2O is self-pressurising, so "
            "this sets the tank pressure, and with it everything else.")
        form.addRow("Tank fill temperature:", self.tank_temp)

        self.sf_edit = QtWidgets.QLineEdit("2.0")
        self.sf_edit.setToolTip(
            "Safety factor on the yield strength of the tank and chamber. It "
            "sizes the walls, and so decides both how much bore is left "
            "inside the diameter allowed and how much the engine weighs.")
        form.addRow("Pressure safety factor:", self.sf_edit)
        lv.addWidget(build)
        lv.addStretch()

        # Scrollable for the same reason as every other form-heavy tab: at
        # 768 px tall a QVBoxLayout with nowhere to put the overflow crushes
        # its children instead of scrolling them.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left_inner)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        # The FORM scrolls; the actions do not. Everything below used to sit
        # inside the scroll area with the form, and at 1366x768 the brief is
        # taller than the viewport - so Generate Engine, the progress bar and
        # both export buttons rendered nothing at all. You could fill the
        # whole brief in and never find the control that acts on it, which
        # reads as the tab being broken. The Engine tab had the identical
        # defect and was fixed the same way; section 12 now measures both.
        left = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(left)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.addWidget(scroll, 1)

        self.generate_button = QtWidgets.QPushButton("Generate Engine")
        self.generate_button.clicked.connect(self._generate)
        outer.addWidget(self.generate_button)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setValue(0)
        outer.addWidget(self.progress)

        self.send_button = QtWidgets.QPushButton("Send to Engine tab")
        self.send_button.setToolTip(
            "Load this engine into the Engine tab, where every dimension can "
            "be edited, saved with the rocket, and flown.")
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self._send)
        outer.addWidget(self.send_button)

        self.export_button = QtWidgets.QPushButton("Export thrust curve (CSV)")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._export_curve)
        outer.addWidget(self.export_button)

        self.status = QtWidgets.QLabel("No engine generated yet.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.RichText)
        self.status.setStyleSheet(
            f"QLabel {{ background:{theme.PALETTE['panel']}; "
            f"border:1px solid {theme.PALETTE['border']}; "
            f"border-left:3px solid {theme.PALETTE['accent']}; padding:8px; "
            f"color:{theme.PALETTE['text']}; }}")
        outer.addWidget(self.status)

        left.setMinimumWidth(455)
        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        right.setMinimumWidth(480)
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.right_tabs = QtWidgets.QTabWidget()

        self.figure = plt.Figure(figsize=(8, 6))
        theme.placeholder_figure(
            self.figure, "Generate an engine to plot its thrust curve.")
        theme.style_figure(self.figure)
        self.canvas = FigureCanvas(self.figure)
        self.right_tabs.addTab(self.canvas, "Thrust Curve")

        self.spec = QtWidgets.QTableWidget()
        self.spec.setColumnCount(3)
        self.spec.setHorizontalHeaderLabels(["Part", "Dimension", "Value"])
        self.spec.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.spec.verticalHeader().setVisible(False)
        hdr = self.spec.horizontalHeader()
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        hdr.setStretchLastSection(True)
        self.right_tabs.addTab(self.spec, "Engine")

        self.report = QtWidgets.QLabel("")
        self.report.setWordWrap(True)
        self.report.setTextFormat(QtCore.Qt.RichText)
        self.report.setAlignment(QtCore.Qt.AlignTop)
        report_area = QtWidgets.QScrollArea()
        report_area.setWidgetResizable(True)
        report_area.setWidget(self.report)
        self.right_tabs.addTab(report_area, "Design Report")

        rv.addWidget(self.right_tabs)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

    def _field_group(self, title, fields):
        group = QtWidgets.QGroupBox(title)
        form = QtWidgets.QFormLayout(group)
        for label, key, qkey, tip in fields:
            widget = unit_fields.UnitField(unit_fields.FIELDS[qkey])
            widget.setToolTip(tip)
            widget.clear()
            self._fields[key] = widget
            row_label = QtWidgets.QLabel(label + ":")
            row_label.setToolTip(tip)
            form.addRow(row_label, widget)
        return group

    # ---- the brief -------------------------------------------------------
    def requirements(self) -> motor_designer.Requirements:
        """The brief as the designer wants it, in SI.

        Read by the same function the Engine tab uses, so one brief cannot
        size two different motors on the two tabs.
        """
        return engine_lab.read_requirements(
            self._fields, self.fuel_combo.currentText(),
            self.injector_combo.currentText(),
            (self.tank_temp.value_si() if not self.tank_temp.is_blank()
             else 293.15),
            self.sf_edit.text())

    def set_requirements(self, **values):
        """Fill boxes from SI values, by Requirements attribute name."""
        for key, value in values.items():
            if key in self._fields:
                self._fields[key].set_value_si(value)
            elif key == "fuel":
                index = self.fuel_combo.findText(value)
                self.fuel_combo.setCurrentIndex(max(0, index))
            elif key == "tank_temp_k":
                self.tank_temp.set_value_si(value)

    # ---- generating ------------------------------------------------------
    def _say(self, html):
        self.status.setText(html)
        QtWidgets.QApplication.processEvents()

    def _generate(self):
        pal = theme.PALETTE
        try:
            req = self.requirements()
        except Exception as exc:
            self._say(f"<b style='color:{pal['critical']}'>Could not read "
                      f"the requirements: {exc}</b>")
            return

        self.generate_button.setEnabled(False)
        self.progress.setValue(0)
        self._say("Sizing...")

        def progress(frac, message):
            self.progress.setValue(int(round(100 * min(1.0, max(0.0, frac)))))
            self._say(f"Sizing... {message}")

        try:
            design = motor_designer.design_motor(req, progress=progress)
        except Exception as exc:
            self._say(f"<b style='color:{pal['critical']}'>Could not size an "
                      f"engine: {exc}</b>")
            traceback.print_exc()
            return
        finally:
            self.generate_button.setEnabled(True)

        self.progress.setValue(100)
        self.show_design(design)

    def show_design(self, design):
        """Display a DesignResult: curve, spec sheet and report."""
        pal = theme.PALETTE
        self._design = design
        self.send_button.setEnabled(self._on_send is not None)
        self.export_button.setEnabled(bool(design.curve))
        self._plot(design)
        self._fill_spec(design)
        self.report.setText(engine_lab.design_report_html(design))
        m = design.metrics
        summary = (f"{design.fuel_name}, {m['total_impulse']:,.0f} N.s, "
                   f"{m['avg_thrust']:,.0f} N average for "
                   f"{m['burn_time']:.2f} s, {design.masses.get('LOADED', 0):.2f}"
                   f" kg loaded.")
        if design.met_all:
            self._say(f"<b style='color:{pal['ok']}'>Engine generated "
                      f"&mdash; it meets every requirement.</b><br>{summary}")
        else:
            missed = ", ".join(c.label for c in design.compliance if not c.met)
            self._say(f"<b style='color:{pal['critical']}'>Engine generated, "
                      f"but it misses: {missed}.</b> The Design Report says "
                      f"by how much and why.<br>{summary}")

    def _plot(self, design):
        c = design.curve
        self.figure.clear()
        if not c:
            theme.placeholder_figure(self.figure, "No thrust curve available.")
            theme.style_figure(self.figure)
            self.canvas.draw()
            return
        try:
            self.figure.set_layout_engine('constrained')
        except AttributeError:
            pass
        t = c["t"]
        ax_f, ax_p = self.figure.subplots(2, 1, sharex=True)
        ax_f.plot(t, c["thrust"], color=theme.SERIES[0], lw=1.8)
        avg = design.metrics.get("avg_thrust", 0.0)
        if avg > 0:
            ax_f.axhline(avg, color=theme.SERIES[6], lw=0.8, ls=":",
                         label=f"average {avg:,.0f} N")
            ax_f.legend(fontsize=8, loc="upper right")
        ax_f.set(ylabel="Thrust (N)",
                 title=f"Thrust curve - {design.fuel_name}, "
                       f"{design.metrics['total_impulse']:,.0f} N.s")
        ax_p.plot(t, [p / 1e6 for p in c["Pc"]], color=theme.SERIES[1],
                  lw=1.6, label="chamber")
        ax_p.plot(t, [p / 1e6 for p in c["P_tank"]], color=theme.SERIES[3],
                  lw=1.2, ls="--", label="tank")
        ax_p.set(xlabel="Time (s)", ylabel="Pressure (MPa)")
        ax_p.legend(fontsize=8, loc="upper right")
        theme.style_figure(self.figure)
        if self.figure.get_layout_engine() is None:
            self.figure.tight_layout()
        self.canvas.draw()

    def _fill_spec(self, design):
        f = design.engine_fields
        m = design.metrics
        mass = design.masses
        env = design.envelope
        d_exit = f["d_throat"] * math.sqrt(max(1.0, f["eps_exp"]))
        rows = [
            ("Oxidiser tank", "Inside diameter", f"{f['d_tank']*1000:.1f} mm"),
            ("", "Length", f"{f['L_tank']*1000:.0f} mm"),
            ("", "Liquid fill", f"{f['fill_frac']*100:.0f} %"),
            ("", "Material, wall",
             f"{design.materials['tank']['name']}, "
             f"{design.materials['tank']['wall_m']*1000:.2f} mm"),
            ("Injector", "Type", design.injector_name),
            ("", "Holes", f"{f['n_holes']} x {f['d_hole']*1000:.2f} mm"),
            ("", "Discharge coefficient", f"{f['Cd_inj']:.2f}"),
            ("Fuel grain", "Fuel", design.fuel_name),
            ("", "Length", f"{f['L_grain']*1000:.0f} mm"),
            ("", "Outside diameter", f"{f['d_grain_outer']*1000:.1f} mm"),
            ("", "Starting port", f"{f['d_port_0']*1000:.1f} mm"),
            ("Chamber", "Pre / post chamber",
             f"{f['L_pre']*1000:.0f} / {f['L_post']*1000:.0f} mm"),
            ("", "Material, wall",
             f"{design.materials['chamber']['name']}, "
             f"{design.materials['chamber']['wall_m']*1000:.2f} mm"),
            ("Nozzle", "Throat", f"{f['d_throat']*1000:.2f} mm"),
            ("", "Exit", f"{d_exit*1000:.1f} mm"),
            ("", "Expansion ratio", f"{f['eps_exp']:.2f}"),
            ("", "Divergence half angle", f"{f['alpha_deg']:.1f} deg"),
            ("Performance", "Total impulse", f"{m['total_impulse']:,.0f} N.s"),
            ("", "Average thrust", f"{m['avg_thrust']:,.0f} N"),
            ("", "Peak thrust", f"{m['peak_thrust']:,.0f} N"),
            ("", "Burn time", f"{m['burn_time']:.2f} s"),
            ("", "Specific impulse", f"{m['isp']:.1f} s"),
            ("", "Peak chamber pressure", f"{m['peak_Pc']/1e6:.2f} MPa"),
            ("", "Average O/F", f"{m['avg_OF']:.2f}"),
            ("Size", "Overall length", f"{env['TOTAL']*1000:.0f} mm"),
            ("", "Outside diameter",
             f"{env.get('_outside_diameter', 0)*1000:.1f} mm"),
            ("Mass (estimate)", "Dry", f"{mass.get('DRY', 0):.2f} kg"),
            ("", "Propellant loaded",
             f"{mass.get('oxidiser propellant', 0) + mass.get('fuel propellant', 0):.2f} kg"),
            ("", "Loaded", f"{mass.get('LOADED', 0):.2f} kg"),
        ]
        self.spec.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for col, text in enumerate(values):
                item = QtWidgets.QTableWidgetItem(text)
                if col == 0 and text:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.spec.setItem(r, col, item)

    # ---- handing it on ---------------------------------------------------
    def _send(self):
        if self._design is None or self._on_send is None:
            return
        try:
            self._on_send(self._design)
        except Exception as exc:
            traceback.print_exc()
            self._say(f"<b style='color:{theme.PALETTE['critical']}'>Could "
                      f"not send the engine: {exc}</b>")

    def _export_curve(self):
        if self._design is None or not self._design.curve:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export thrust curve", "engine_designer_thrust.csv",
            "CSV files (*.csv)")
        if not path:
            return
        try:
            write_curve_csv(path, self._design)
        except OSError as exc:
            self._say(f"<b style='color:{theme.PALETTE['critical']}'>Could "
                      f"not write {path}: {exc}</b>")
            return
        self._say(f"Thrust curve written to {path}")


def write_curve_csv(path, design):
    """The thrust curve as time,thrust rows, the format the Simulation tab
    reads, with chamber and tank pressure alongside."""
    c = design.curve
    prop = design.metrics.get("prop_mass", 0.0)
    # newline='' so the csv module's own CRLF is not doubled on Windows.
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        # Metadata above the header, which the Simulation tab's reader picks
        # the propellant mass out of - the same layout as the shipped curves.
        writer.writerow(["motor:", f"Engine Designer {design.fuel_name} "
                                   f"hybrid"])
        writer.writerow(["propellant mass:", f"{prop:.4f} kg"])
        writer.writerow(["time_s", "thrust_n", "chamber_pressure_pa",
                         "tank_pressure_pa"])
        for row in zip(c["t"], c["thrust"], c["Pc"], c["P_tank"]):
            writer.writerow([f"{v:.6g}" for v in row])
