"""The mass-and-balance page: put weight where it actually is.

A rocket used to be described here by a dry mass and a dry CG, both of which
you had to work out yourself. This page takes the components instead - nose
weight, avionics bay, recovery, motor hardware, ballast - and derives dry
mass, CG and pitch inertia from where they sit.

That turns the thing you usually want to try into a one-line edit. Marginally
stable? Add 200 g at the nose and watch the margin move. The old two-number
form is still there and still works; components override it when present,
because they carry strictly more information.
"""
from __future__ import annotations

from PyQt5 import QtWidgets, QtCore

import theme
import mass_model

_P = theme.PALETTE

_COLS = ["Use", "Name", "Kind", "Mass (kg)", "Station (mm)", "Length (mm)"]

_STARTERS = [
    ("Nose weight", "Nose weight", 0.0, 0.05, 0.0),
    ("Nose cone", "Nose cone", 0.5, 0.28, 0.42),
    ("Avionics bay", "Avionics bay", 0.6, 0.90, 0.25),
    ("Recovery", "Recovery", 0.7, 1.10, 0.30),
    ("Body tube", "Body tube", 1.2, 1.05, 1.40),
    ("Fin can", "Fins", 0.5, 1.70, 0.25),
    ("Motor hardware", "Motor hardware", 1.4, 1.60, 0.50),
]


class MassBuildupWidget(QtWidgets.QWidget):
    """An editable component table plus the mass properties it produces."""

    def __init__(self, on_changed=None, get_cp=None, get_diameter=None,
                 get_propellant=None, parent=None):
        super().__init__(parent)
        self._on_changed = on_changed
        self._get_cp = get_cp
        self._get_diameter = get_diameter
        self._get_propellant = get_propellant
        self._build_ui()
        self.refresh_summary()

    def _build_ui(self):
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)

        intro = QtWidgets.QLabel(
            "Weight, and where it sits. Dry mass, CG and pitch inertia are "
            "computed from this list — you do not type a CG, you place the "
            "parts and the CG follows. Stations are measured from the nose "
            "tip. Give a part a length and it is treated as spread over that "
            "length rather than as a point; leave it 0 for a point mass.<br>"
            "<b>Leave the table empty to keep using the Dry mass / Dry CG "
            "fields on the Airframe page instead.</b>")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{_P['text_dim']}; font-size:9pt;")
        v.addWidget(intro)

        self.table = QtWidgets.QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        header = self.table.horizontalHeader()
        # Stretch the NAME column and size the rest to their content. Stretching
        # the last section instead left "Length" absurdly wide and clipped the
        # "Station" header down to "tation".
        header.setStretchLastSection(False)
        modes = [QtWidgets.QHeaderView.ResizeToContents,   # Use
                 QtWidgets.QHeaderView.Stretch,            # Name
                 QtWidgets.QHeaderView.ResizeToContents,   # Kind
                 QtWidgets.QHeaderView.ResizeToContents,   # Mass
                 QtWidgets.QHeaderView.ResizeToContents,   # Station
                 QtWidgets.QHeaderView.ResizeToContents]   # Length
        for i, mode in enumerate(modes):
            header.setSectionResizeMode(i, mode)
        header.setMinimumSectionSize(70)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(lambda _i: self.refresh_summary())
        v.addWidget(self.table, stretch=1)

        row = QtWidgets.QHBoxLayout()
        for text, slot, tip in (
            ("Add component", self.add_row, "Add an empty row"),
            ("Add starter set", self.add_starters,
             "Fill in a typical high-power layout to edit from"),
            ("Remove selected", self.remove_selected, "Delete the selected rows"),
            ("Clear", self.clear,
             "Empty the table and go back to the Dry mass / Dry CG fields"),
        ):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)

        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(
            f"background:{_P['panel']}; border:1px solid {_P['border']}; "
            f"border-left:3px solid {_P['accent']}; padding:8px; "
            f"color:{_P['text']};")
        v.addWidget(self.summary)

    # ---- table plumbing ---------------------------------------------------
    def _set_row(self, r, name, kind, mass, station_m, length_m, enabled=True):
        self.table.blockSignals(True)
        use = QtWidgets.QTableWidgetItem()
        use.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled
                     | QtCore.Qt.ItemIsSelectable)
        use.setCheckState(QtCore.Qt.Checked if enabled else QtCore.Qt.Unchecked)
        self.table.setItem(r, 0, use)
        self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(str(name)))
        combo = QtWidgets.QComboBox()
        combo.addItems(list(mass_model.COMPONENT_KINDS.keys()))
        idx = combo.findText(kind)
        combo.setCurrentIndex(idx if idx >= 0 else combo.count() - 1)
        combo.setToolTip(mass_model.COMPONENT_KINDS.get(kind, ""))
        combo.currentTextChanged.connect(lambda _t: self.refresh_summary())
        self.table.setCellWidget(r, 2, combo)
        self.table.setItem(r, 3, QtWidgets.QTableWidgetItem(f"{mass:.3f}"))
        self.table.setItem(r, 4, QtWidgets.QTableWidgetItem(f"{station_m*1000:.1f}"))
        self.table.setItem(r, 5, QtWidgets.QTableWidgetItem(f"{length_m*1000:.1f}"))
        self.table.blockSignals(False)

    def add_row(self):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self._set_row(r, "Component", "Ballast", 0.0, 0.0, 0.0)
        self.refresh_summary()

    def add_starters(self):
        for name, kind, mass, station, length in _STARTERS:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self._set_row(r, name, kind, mass, station, length)
        self.refresh_summary()

    def remove_selected(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()},
                        reverse=True):
            self.table.removeRow(r)
        self.refresh_summary()

    def clear(self):
        self.table.setRowCount(0)
        self.refresh_summary()

    @staticmethod
    def _f(item, default=0.0):
        try:
            return float(item.text())
        except (AttributeError, ValueError):
            return default

    # ---- data in / out ----------------------------------------------------
    def buildup(self) -> mass_model.MassBuildup:
        comps = []
        for r in range(self.table.rowCount()):
            use = self.table.item(r, 0)
            name = self.table.item(r, 1)
            combo = self.table.cellWidget(r, 2)
            comps.append(mass_model.MassComponent(
                name=name.text() if name else f"Row {r+1}",
                mass_kg=self._f(self.table.item(r, 3)),
                position_m=self._f(self.table.item(r, 4)) / 1000.0,
                length_m=self._f(self.table.item(r, 5)) / 1000.0,
                kind=combo.currentText() if combo else "Other",
                enabled=(use.checkState() == QtCore.Qt.Checked) if use else True))
        return mass_model.MassBuildup(comps)

    def set_buildup(self, rows):
        self.table.setRowCount(0)
        for d in rows or []:
            c = mass_model.MassComponent.from_dict(d)
            r = self.table.rowCount()
            self.table.insertRow(r)
            self._set_row(r, c.name, c.kind, c.mass_kg, c.position_m,
                          c.length_m, c.enabled)
        self.refresh_summary()

    def get_config(self):
        return self.buildup().to_list()

    def apply_config(self, rows):
        self.set_buildup(rows)

    # ---- live readout -----------------------------------------------------
    def refresh_summary(self):
        b = self.buildup()
        if not b.active():
            self.summary.setText(
                "<b>No components.</b> Dry mass and CG come from the Airframe "
                "page's Mass &amp; Balance fields, and pitch inertia falls "
                "back to a uniform-rod estimate.")
            if self._on_changed:
                self._on_changed()
            return

        prop, prop_cg, prop_len = 0.0, 0.0, 0.0
        if self._get_propellant:
            try:
                prop, prop_cg, prop_len = self._get_propellant()
            except Exception:
                pass
        m_wet, cg_wet, i_wet = b.with_propellant(prop, prop_cg, prop_len)
        m_dry, cg_dry, i_dry = b.with_propellant(0.0, prop_cg, prop_len)

        text = (f"<b>{len(b.active())} components</b><br>"
                f"Dry: <b>{m_dry:.3f} kg</b>, CG <b>{cg_dry*1000:,.0f} mm</b>, "
                f"pitch inertia <b>{i_dry:.3f} kg·m²</b>")
        if prop > 0:
            text += (f"<br>Loaded ({prop:.3f} kg propellant): "
                     f"<b>{m_wet:.3f} kg</b>, CG <b>{cg_wet*1000:,.0f} mm</b>, "
                     f"pitch inertia <b>{i_wet:.3f} kg·m²</b>"
                     f"<br>CG moves <b>{(cg_dry-cg_wet)*1000:+,.0f} mm</b> "
                     f"through the burn")

        cp = diameter = None
        if self._get_cp:
            try:
                cp = self._get_cp()
            except Exception:
                cp = None
        if self._get_diameter:
            try:
                diameter = self._get_diameter()
            except Exception:
                diameter = None
        if cp and diameter and diameter > 0:
            m_lift = (cp - cg_wet) / diameter
            m_burn = (cp - cg_dry) / diameter
            worst = min(m_lift, m_burn)
            colour = (_P['critical'] if worst < 1.0
                      else _P['caution'] if worst < 1.5 else _P['ok'])
            text += (f"<br>CP <b>{cp*1000:,.0f} mm</b> — static margin "
                     f"<span style='color:{colour}'><b>{m_lift:.2f} cal at "
                     f"liftoff, {m_burn:.2f} cal at burnout</b></span>")
            if worst < 1.0:
                text += ("<br><span style='color:%s'>Under 1 caliber. Move "
                         "weight forward or ballast the nose.</span>" % _P['critical'])
        self.summary.setText(text)
        if self._on_changed:
            self._on_changed()
