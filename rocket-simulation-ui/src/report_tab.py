"""Flight Report tab: the failure-mode report for the last simulation run.

Left side configures the physical vehicle the analysis grades - materials
(with their melting points and service limits), wall thicknesses, fin
geometry, and operational limits. Right side is the report itself: a
pass/fail verdict against the altitude goal, a numbered and colour-coded
table of every failure mode that was checked, a flight timeline, and graphs
with numbered markers that point back at the table rows.

Green = inside limits. Yellow = margin is thin or an assumption is shaky.
Red = the design fails this check as simulated.
"""
from __future__ import annotations

import base64
import html
import io
import os
import webbrowser
from pathlib import Path

from PyQt5 import QtWidgets, QtGui, QtCore
import theme
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

import materials as mat_lib
import failure_analysis as fa

# status -> (table row background, text colour, banner colour)
_COLORS = {
    fa.OK:       theme.status_colors("OK"),
    fa.CAUTION:  theme.status_colors("CAUTION"),
    fa.CRITICAL: theme.status_colors("CRITICAL"),
    fa.NO_DATA:  theme.status_colors("NO DATA"),
}

_INPUT_STYLE = ""

# (label, VehicleConfig attribute, display factor, decimals)
_AIRFRAME_FIELDS = [
    ("Body outer diameter (mm)", "body_od_m", 1000.0, 1),
    ("Body wall thickness (mm)", "body_wall_m", 1000.0, 2),
    ("Body length (m)", "body_length_m", 1.0, 2),
]
_FIN_FIELDS = [
    ("Fin count", "fin_count", 1.0, 0),
    ("Root chord (mm)", "fin_root_chord_m", 1000.0, 1),
    ("Tip chord (mm)", "fin_tip_chord_m", 1000.0, 1),
    ("Span (mm)", "fin_span_m", 1000.0, 1),
    ("Thickness (mm)", "fin_thickness_m", 1000.0, 2),
]
_MOTOR_FIELDS = [
    ("Chamber wall (mm)", "chamber_wall_m", 1000.0, 2),
    ("Tank wall (mm)", "tank_wall_m", 1000.0, 2),
]
_OPS_FIELDS = [
    ("Target altitude (ft)", "target_altitude_ft", 1.0, 0),
    ("Launch rail length (m)", "rail_length_m", 1.0, 2),
    ("Harness rating (N)", "harness_rating_n", 1.0, 0),
    ("Pressure safety factor", "min_pressure_sf", 1.0, 1),
    ("Structure safety factor", "min_structure_sf", 1.0, 1),
]
_MATERIAL_FIELDS = [
    ("Airframe", "airframe_material", mat_lib.STRUCTURAL),
    ("Nose cone", "nose_material", mat_lib.STRUCTURAL),
    ("Fins", "fin_material", mat_lib.STRUCTURAL),
    ("Nozzle throat", "nozzle_material", mat_lib.HOT_SECTION),
    ("Combustion chamber", "chamber_material", mat_lib.PRESSURE),
    ("Oxidizer tank", "tank_material", mat_lib.PRESSURE),
]


class FlightReportWidget(QtWidgets.QWidget):
    """Failure-mode report generated from the last simulation run."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flight = None
        self._engine_result = None
        self._engine = None
        self._report = None
        self._fields = {}
        self._material_combos = {}
        self._user_edited = set()   # fields a person has typed into
        self._build_ui()

    # ---- UI ---------------------------------------------------------------
    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_config_panel())
        splitter.addWidget(self._build_report_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 1100])
        layout.addWidget(splitter)

    def _build_config_panel(self):
        panel = QtWidgets.QWidget()
        panel.setMinimumWidth(380)
        outer = QtWidgets.QVBoxLayout(panel)
        outer.setSpacing(6)
        intro = QtWidgets.QLabel(
            "<b>Vehicle &amp; Materials</b><br>"
            "<span style='font-size:9pt'>What the failure checks are graded "
            "against.</span>")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        host = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(host)

        defaults = fa.VehicleConfig()

        mat_group = QtWidgets.QGroupBox("Materials")
        mat_form = QtWidgets.QFormLayout(mat_group)
        for label, attr, library in _MATERIAL_FIELDS:
            combo = QtWidgets.QComboBox()
            combo.addItems(list(library.keys()))
            combo.setStyleSheet(_INPUT_STYLE)
            current = getattr(defaults, attr)
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentTextChanged.connect(self._update_material_note)
            self._material_combos[attr] = combo
            mat_form.addRow(label + ":", combo)
        self.material_note = QtWidgets.QLabel()
        self.material_note.setWordWrap(True)
        self.material_note.setStyleSheet(
            f"font-size:9pt; color:{theme.PALETTE['text_dim']}; "
            f"background:{theme.PALETTE['panel']}; "
            f"border:1px solid {theme.PALETTE['border']}; padding:6px;")
        mat_form.addRow(self.material_note)
        vbox.addWidget(mat_group)

        for title, fields in (("Airframe", _AIRFRAME_FIELDS),
                              ("Fins", _FIN_FIELDS),
                              ("Motor Hardware", _MOTOR_FIELDS),
                              ("Mission & Operations", _OPS_FIELDS)):
            group = QtWidgets.QGroupBox(title)
            form = QtWidgets.QFormLayout(group)
            for label, attr, factor, dec in fields:
                edit = QtWidgets.QLineEdit()
                edit.setStyleSheet(_INPUT_STYLE)
                edit.setText(f"{getattr(defaults, attr) * factor:.{dec}f}")
                # textEdited fires only for typing, never for setText, so this
                # marks the fields a person has actually taken over.
                edit.textEdited.connect(
                    lambda _t, a=attr: self._user_edited.add(a))
                self._fields[attr] = (edit, factor, dec)
                form.addRow(label + ":", edit)
            vbox.addWidget(group)

        vbox.addStretch()
        scroll.setWidget(host)
        outer.addWidget(scroll)

        self.analyze_button = QtWidgets.QPushButton("Re-run Analysis")
        self.analyze_button.clicked.connect(self._reanalyze)
        outer.addWidget(self.analyze_button)

        self.export_button = QtWidgets.QPushButton("Export HTML Report")
        self.export_button.clicked.connect(self._export)
        self.export_button.setEnabled(False)
        outer.addWidget(self.export_button)

        self._update_material_note()
        return panel

    def _build_report_panel(self):
        panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(panel)
        vbox.setSpacing(6)

        self.banner = QtWidgets.QLabel("Run a simulation to generate a failure report.")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            f"background:{theme.PALETTE['panel']}; color:{theme.PALETTE['text']}; "
            f"padding:12px; font-size:11pt; border-left:4px solid {theme.PALETTE['border']};")
        vbox.addWidget(self.banner)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)

        table_host = QtWidgets.QWidget()
        table_layout = QtWidgets.QVBoxLayout(table_host)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["#", "Code", "Category", "Check", "Status", "Measured", "Limit", "Time"])
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(240)
        self.table.itemSelectionChanged.connect(self._show_detail)
        table_layout.addWidget(self.table, stretch=1)

        self.detail = QtWidgets.QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setHtml("Select a row for the full explanation.")
        self.detail.setMinimumHeight(90)
        self.detail.setMaximumHeight(170)
        self.detail.setStyleSheet(
            f"background:{theme.PALETTE['panel']}; "
            f"border:1px solid {theme.PALETTE['border']}; padding:10px; "
            f"color:{theme.PALETTE['text']}; font-size:10pt;")
        table_layout.addWidget(self.detail)
        splitter.addWidget(table_host)

        self.figure = plt.Figure(figsize=(9, 6))
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumHeight(340)
        splitter.addWidget(self.canvas)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([440, 460])
        vbox.addWidget(splitter, stretch=1)
        return panel

    def _update_material_note(self):
        lines = []
        for label, attr, _lib in _MATERIAL_FIELDS:
            m = mat_lib.get(self._material_combos[attr].currentText())
            lines.append(
                f"<b>{html.escape(label)}</b>: {html.escape(m.name)} &mdash; "
                f"service {m.max_service_k:,.0f} K, fails {m.melt_k:,.0f} K, "
                f"yield {m.yield_mpa:,.0f} MPa")
        self.material_note.setText("<br>".join(lines))

    # ---- config ------------------------------------------------------------
    def vehicle_config(self) -> fa.VehicleConfig:
        cfg = fa.VehicleConfig()
        for attr, (edit, factor, _dec) in self._fields.items():
            text = edit.text().strip()
            if not text:
                continue
            try:
                value = float(text) / factor
            except ValueError:
                continue
            setattr(cfg, attr, int(round(value)) if attr == "fin_count" else value)
        for _label, attr, _lib in _MATERIAL_FIELDS:
            setattr(cfg, attr, self._material_combos[attr].currentText())
        return cfg

    def get_config(self) -> dict:
        """Vehicle geometry, materials and limits as plain values, for saving
        into a rocket profile."""
        cfg = {attr: edit.text() for attr, (edit, _f, _d) in self._fields.items()}
        for _label, attr, _lib in _MATERIAL_FIELDS:
            cfg[attr] = self._material_combos[attr].currentText()
        return cfg

    def apply_config(self, cfg: dict):
        """Restore a vehicle configuration saved by get_config()."""
        if not cfg:
            return
        for attr, value in cfg.items():
            if attr in self._material_combos:
                idx = self._material_combos[attr].findText(str(value))
                if idx >= 0:
                    self._material_combos[attr].setCurrentIndex(idx)
            elif attr in self._fields:
                self._fields[attr][0].setText(str(value))
        self._update_material_note()
        if self._flight:
            self._reanalyze()

    def apply_geometry_hints(self, hints: dict):
        """Fill geometry the Simulation tab already knows.

        Every field here is pre-filled with a default at build time, so
        "empty means untouched" was never true and the hints were always
        dropped - the checks then graded a 140 mm four-fin tube no matter what
        the user had actually flown. A field counts as the user's only once
        they have typed in it; anything still showing a default or a previous
        hint gets updated from the run.
        """
        for attr, value in hints.items():
            entry = self._fields.get(attr)
            if not entry or value in (None, 0):
                continue
            if attr in self._user_edited:
                continue
            edit, factor, dec = entry
            edit.setText(f"{value * factor:.{dec}f}")

    # ---- data in -----------------------------------------------------------
    def update_from_simulation(self, flight, engine_result=None, engine=None,
                               geometry_hints=None, cd_source=None,
                               mass_props=None):
        """Called after a simulation run completes."""
        if geometry_hints:
            self.apply_geometry_hints(geometry_hints)
        self._flight = flight
        self._engine_result = engine_result
        self._engine = engine
        # What drove drag, and how mass was described. Both change what the
        # report can honestly say, so both are graded rather than assumed.
        self._cd_source = cd_source
        self._mass_props = mass_props
        self._reanalyze()

    def _reanalyze(self):
        if not self._flight:
            self.banner.setText("Run a simulation first - the report is built from its data.")
            return
        self._report = fa.analyze(self._flight, self.vehicle_config(),
                                  self._engine_result, self._engine,
                                  cd_source=getattr(self, "_cd_source", None),
                                  mass_props=getattr(self, "_mass_props", None))
        self._render(self._report)
        self.export_button.setEnabled(True)

    # ---- rendering ---------------------------------------------------------
    def _render(self, rep: fa.Report):
        counts = rep.counts
        goal_bg = _COLORS[fa.OK][2] if rep.goal_met else _COLORS[fa.CRITICAL][2]
        goal_text = ("GOAL MET" if rep.goal_met else "GOAL NOT MET")
        delta = rep.apogee_ft - rep.target_ft
        self.banner.setStyleSheet(
            f"background:{theme.PALETTE['panel']}; color:{theme.PALETTE['text']}; "
            f"border-left:5px solid {goal_bg}; padding:12px; font-size:11pt;")
        engine_note = ("" if rep.has_engine_data else
                       " &nbsp;|&nbsp; <i>no engine data - send an Engine Lab motor to the "
                       "simulation to enable the 11 propulsion checks</i>")
        self.banner.setText(
            f"<b style='font-size:13pt'>{goal_text}</b> &nbsp;&mdash;&nbsp; apogee "
            f"<b>{rep.apogee_ft:,.0f} ft</b> vs {rep.target_ft:,.0f} ft target "
            f"({delta:+,.0f} ft)<br>"
            f"<b>{counts[fa.CRITICAL]}</b> critical &nbsp; "
            f"<b>{counts[fa.CAUTION]}</b> caution &nbsp; "
            f"<b>{counts[fa.OK]}</b> nominal &nbsp; "
            f"<b>{counts[fa.NO_DATA]}</b> not evaluated &nbsp;|&nbsp; "
            f"Max Mach {rep.max_mach:.2f}, max q {rep.max_q_pa/1000:,.0f} kPa, "
            f"{rep.max_g:,.1f} g{engine_note}")

        self.table.setRowCount(len(rep.checks))
        for row, chk in enumerate(rep.checks):
            bg, fg, _ = _COLORS[chk.status]
            cells = [str(chk.number), chk.code, chk.category, chk.name, chk.status,
                     chk.value, chk.limit,
                     f"{chk.t_event:.1f} s" if chk.t_event is not None else "-"]
            for col, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                item.setBackground(QtGui.QColor(bg))
                item.setForeground(QtGui.QColor(fg))
                if col == 4:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        if rep.checks:
            self.table.selectRow(0)
        self._plot(rep)

    def _show_detail(self):
        if not self._report:
            return
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return
        chk = self._report.checks[min(rows)]
        _bg, fg, _banner = _COLORS[chk.status]
        rec = (f"<br><br><b>What to do:</b> {html.escape(chk.recommendation)}"
               if chk.recommendation else "")
        self.detail.setHtml(
            f"<b>#{chk.number} &nbsp; {html.escape(chk.code)} &nbsp; "
            f"{html.escape(chk.name)}</b> &nbsp; "
            f"<span style='color:{fg}'><b>{chk.status}</b></span><br>"
            f"Measured <b>{html.escape(chk.value)}</b> against "
            f"<b>{html.escape(chk.limit)}</b><br><br>"
            f"{html.escape(chk.detail)}{rec}")

    def _plot(self, rep: fa.Report):
        self.figure.clear()
        # Constrained layout keeps titles off the axis labels of the row above
        # when the canvas is short; tight_layout lets them collide.
        try:
            self.figure.set_layout_engine('constrained')
        except AttributeError:
            pass
        flight = self._flight
        t = [r["time"] for r in flight]
        alt_ft = [r["altitude"] * fa.FT_PER_M for r in flight]
        vel = [r["velocity"] for r in flight]
        mach = [r.get("Mach", 0.0) for r in flight]
        q_kpa = [r.get("q", 0.0) / 1000.0 for r in flight]
        g = [r.get("acceleration", 0.0) / fa.G0 for r in flight]

        ax = self.figure.subplots(2, 2)

        # --- altitude with the numbered failure markers ---
        a0 = ax[0, 0]
        a0.plot(t, alt_ft, color="#8e44ad", lw=1.8)
        a0.axhline(rep.target_ft, color=_COLORS[fa.CRITICAL][2], ls="--", lw=1.2)
        a0.set_ylim(top=max(rep.target_ft, max(alt_ft) if alt_ft else 0) * 1.12)
        a0.annotate(f"{rep.target_ft:,.0f} ft goal",
                    xy=(t[0], rep.target_ft), xytext=(0, 3),
                    textcoords="offset points", fontsize=7,
                    color=_COLORS[fa.CRITICAL][2], va="bottom")
        for ev in rep.events:
            a0.axvline(ev.t, color="#7f8c8d", ls=":", lw=0.8)
            a0.annotate(ev.name, xy=(ev.t, 0), rotation=90, fontsize=6,
                        color="#5A5A5A", va="bottom", ha="right")
        self._annotate_checks(a0, rep, t, alt_ft)
        a0.set(xlabel="Time (s)", ylabel="Altitude (ft)")
        a0.set_title("Altitude - numbered markers match the table", fontsize=10)

        a1 = ax[0, 1]
        a1.plot(t, vel, color="#27ae60", lw=1.6, label="velocity (m/s)")
        a1.axhline(0, color="k", lw=0.6)
        a1.set(xlabel="Time (s)", ylabel="Velocity (m/s)")
        a1.set_title("Velocity & Mach", fontsize=10)
        a1b = a1.twinx()
        a1b.plot(t, mach, color="#d35400", lw=1.2, ls="--", label="Mach")
        a1b.axhline(0.8, color="#d35400", lw=0.8, ls=":")
        a1b.set_ylabel("Mach")
        a1.legend(loc="upper right", fontsize=7)

        a2 = ax[1, 0]
        a2.plot(t, q_kpa, color="#2980b9", lw=1.6)
        a2.set(xlabel="Time (s)", ylabel="q (kPa)")
        a2.set_title("Dynamic pressure & acceleration", fontsize=10)
        a2b = a2.twinx()
        a2b.plot(t, g, color="#c0392b", lw=1.2, ls="--")
        a2b.set_ylabel("Acceleration (g)")

        # --- thermal margin against the chosen material ---
        a3 = ax[1, 1]
        skin = mat_lib.get(self.vehicle_config().airframe_material)
        temps = []
        for r in flight:
            T_amb, _, _, _ = fa.isa(r["altitude"])
            M = r.get("Mach", 0.0)
            temps.append(T_amb * (1 + 0.9 * 0.2 * M * M))
        a3.plot(t, temps, color="#e67e22", lw=1.6, label="recovery temp")
        a3.axhline(skin.max_service_k, color=_COLORS[fa.CAUTION][2], ls="--", lw=1.2,
                   label=f"{skin.name} service")
        a3.axhline(skin.melt_k, color=_COLORS[fa.CRITICAL][2], ls="--", lw=1.2,
                   label=f"{skin.name} failure")
        a3.set(xlabel="Time (s)", ylabel="Temperature (K)")
        a3.set_title("Skin heating vs material", fontsize=10)
        a3.legend(fontsize=8)

        theme.style_figure(self.figure)
        for a in ax.flat:
            a.tick_params(labelsize=8)
            a.xaxis.label.set_size(9)
            a.yaxis.label.set_size(9)
        if self.figure.get_layout_engine() is None:
            self.figure.tight_layout()
        self.canvas.draw()

    @staticmethod
    def _annotate_checks(axis, rep, t, alt_ft):
        """Drop numbered, status-coloured markers on the altitude trace."""
        for chk in rep.checks:
            if chk.t_event is None or chk.status == fa.OK:
                continue
            i = min(range(len(t)), key=lambda k: abs(t[k] - chk.t_event))
            color = _COLORS[chk.status][2]
            axis.plot([t[i]], [alt_ft[i]], "o", color=color, ms=9, zorder=5)
            axis.annotate(str(chk.number), xy=(t[i], alt_ft[i]), color="#FFFFFF",
                          fontsize=6, fontweight="bold", ha="center", va="center",
                          zorder=6)

    # ---- export ------------------------------------------------------------
    def _export(self):
        if not self._report:
            return
        default = os.path.join(os.path.expanduser("~"), "jarvis_flight_report.html")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Flight Report", default, "HTML Report (*.html)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._html(self._report))
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Export failed", str(exc))
            return
        try:
            # Path.as_uri() builds a valid file: URL on every platform. Formatting
            # "file://" + a Windows path by hand produces file://C:\... which no
            # browser will open.
            webbrowser.open(Path(path).resolve().as_uri())
        except Exception:
            pass

    def _html(self, rep: fa.Report) -> str:
        png = io.BytesIO()
        self.figure.savefig(png, format="png", dpi=130, bbox_inches="tight")
        b64 = base64.b64encode(png.getvalue()).decode("ascii")

        rows = []
        for chk in rep.checks:
            bg, fg, _ = _COLORS[chk.status]
            rec = (f"<br><i>{html.escape(chk.recommendation)}</i>"
                   if chk.recommendation else "")
            rows.append(
                f"<tr style='background:{bg}; color:{fg}'>"
                f"<td class='num'>{chk.number}</td><td>{html.escape(chk.code)}</td>"
                f"<td>{html.escape(chk.category)}</td><td>{html.escape(chk.name)}</td>"
                f"<td><b>{chk.status}</b></td><td>{html.escape(chk.value)}</td>"
                f"<td>{html.escape(chk.limit)}</td>"
                f"<td>{'%.1f s' % chk.t_event if chk.t_event is not None else '-'}</td>"
                f"<td class='detail'>{html.escape(chk.detail)}{rec}</td></tr>")

        events = "".join(
            f"<tr><td>{e.t:.1f} s</td><td>{html.escape(e.name)}</td>"
            f"<td>{html.escape(e.detail)}</td></tr>" for e in rep.events)
        not_eval = "".join(f"<li>{html.escape(x)}</li>" for x in rep.not_evaluated)
        counts = rep.counts
        banner_color = _COLORS[fa.OK][2] if rep.goal_met else _COLORS[fa.CRITICAL][2]
        cfg = self.vehicle_config()
        mats = "".join(
            f"<tr><td>{html.escape(label)}</td><td>{html.escape(mat_lib.get(getattr(cfg, attr)).name)}</td>"
            f"<td>{mat_lib.get(getattr(cfg, attr)).max_service_k:,.0f} K</td>"
            f"<td>{mat_lib.get(getattr(cfg, attr)).melt_k:,.0f} K</td>"
            f"<td>{mat_lib.get(getattr(cfg, attr)).yield_mpa:,.0f} MPa</td></tr>"
            for label, attr, _lib in _MATERIAL_FIELDS)

        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>JARVIS Flight Report</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px;
        color: #23201B; background: #FBF8F0; }}
 h1 {{ margin-bottom: 4px; }} h2 {{ margin-top: 28px; }}
 .banner {{ background: {banner_color}; color: #fff; padding: 16px 18px;
            border-radius: 10px; font-size: 13pt; }}
 table {{ border-collapse: collapse; width: 100%; margin-top: 10px; font-size: 11pt; }}
 th, td {{ border: 1px solid #D8CFBA; padding: 6px 8px; text-align: left;
           vertical-align: top; }}
 th {{ background: #EFE7D4; }}
 td.num {{ font-weight: bold; text-align: center; }}
 td.detail {{ font-size: 10pt; max-width: 460px; }}
 img {{ max-width: 100%; margin-top: 12px; border: 1px solid #D8CFBA; border-radius: 8px; }}
 .foot {{ margin-top: 30px; font-size: 10pt; color: #6A6154; }}
</style></head><body>
<h1>JARVIS Flight Report</h1>
<div class="banner">
  <b>{'GOAL MET' if rep.goal_met else 'GOAL NOT MET'}</b> &mdash;
  apogee <b>{rep.apogee_ft:,.0f} ft</b> against a {rep.target_ft:,.0f} ft target
  ({rep.apogee_ft - rep.target_ft:+,.0f} ft)<br>
  {counts[fa.CRITICAL]} critical &nbsp; {counts[fa.CAUTION]} caution &nbsp;
  {counts[fa.OK]} nominal &nbsp; {counts[fa.NO_DATA]} not evaluated<br>
  Max Mach {rep.max_mach:.2f} &nbsp;|&nbsp; max q {rep.max_q_pa/1000:,.0f} kPa
  &nbsp;|&nbsp; peak {rep.max_g:,.1f} g
</div>

<h2>Failure checks</h2>
<table><tr><th>#</th><th>Code</th><th>Category</th><th>Check</th><th>Status</th>
<th>Measured</th><th>Limit</th><th>Time</th><th>Detail</th></tr>{''.join(rows)}</table>

<h2>Graphs</h2>
<p>Numbered markers on the altitude trace correspond to the check numbers above.</p>
<img src="data:image/png;base64,{b64}" alt="flight graphs">

<h2>Flight timeline</h2>
<table><tr><th>Time</th><th>Event</th><th>Detail</th></tr>{events}</table>

<h2>Materials as configured</h2>
<table><tr><th>Part</th><th>Material</th><th>Service limit</th><th>Failure temp</th>
<th>Yield</th></tr>{mats}</table>

<h2>Not evaluated</h2>
<ul>{not_eval}</ul>

<p class="foot">Generated by JARVIS. These checks grade the simulated flight against
representative material and rule-of-thumb limits; they are a design screen, not a
substitute for your own structural analysis, a proof test, or your range safety
officer's review.</p>
</body></html>"""
