"""The two halves a rocket is built from, and the assembly that pairs them.

JARVIS used to have one big pile of settings. It is now split the way the
hardware is:

    ENGINE          the motor - tank, injector, grain, nozzle. What makes
                    thrust, and what you resize when you want more of it.
    AERODYNAMICS    the airframe - nose, body, fins, recovery, and the drag
                    that comes out of them. What the thrust has to push.

Each is designed on its own tab and saved on its own, because they change on
different schedules: one motor gets flown in several airframes, and one
airframe gets tried with several motors.

The Simulation tab then pairs one of each. That pairing, plus its masses, is
the rocket - it is what gets saved to the rocket library and what gets flown.
"""
from __future__ import annotations

from PyQt5 import QtWidgets

import theme
from component_library import ComponentLibraryBar

_P = theme.PALETTE


def _header(title: str, blurb: str) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(f"<b>{title}</b><br>"
                           f"<span style='font-size:9pt'>{blurb}</span>")
    lab.setWordWrap(True)
    lab.setStyleSheet(
        f"background:{_P['panel']}; border-left:3px solid {_P['accent']}; "
        f"padding:8px; color:{_P['text']};")
    return lab


class EngineSection(QtWidgets.QWidget):
    """Everything about the motor, plus its own design library."""

    def __init__(self, engine_lab, directory_fn, parent=None):
        super().__init__(parent)
        self.engine_lab = engine_lab
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        layout.addWidget(_header(
            "Engine",
            "Design the motor: oxidizer tank, injector, fuel grain and "
            "combustion chamber, nozzle. Resize any of it here — a bigger "
            "tank, a different orifice, a longer grain — and run it to get a "
            "thrust curve. Pair it with an airframe on the Simulation tab to "
            "make a rocket."))

        self.library = ComponentLibraryBar(
            kind="engines", label="engine",
            get_config=engine_lab.get_config,
            apply_config=engine_lab.apply_config,
            directory_fn=directory_fn)
        layout.addWidget(self.library)
        layout.addWidget(engine_lab, stretch=1)

    def get_config(self):
        return self.engine_lab.get_config()

    def apply_config(self, cfg):
        self.engine_lab.apply_config(cfg)


class AerodynamicsSection(QtWidgets.QWidget):
    """Everything about the airframe and its drag, plus its design library."""

    def __init__(self, vehicle_tab, aero_tab, directory_fn, parent=None):
        super().__init__(parent)
        self.vehicle_tab = vehicle_tab
        self.aero_tab = aero_tab

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        layout.addWidget(_header(
            "Aerodynamics",
            "Shape the airframe: nose cone, body tube, boat tail, fins, "
            "surface finish, and the recovery train. The Cd vs Mach page "
            "sweeps the drag that geometry produces, and is where an external "
            "drag curve gets imported. Pair it with an engine on the "
            "Simulation tab to make a rocket."))

        self.library = ComponentLibraryBar(
            kind="airframes", label="aerodynamics",
            get_config=vehicle_tab.get_config,
            apply_config=vehicle_tab.apply_config,
            directory_fn=directory_fn)
        layout.addWidget(self.library)

        # The drag sweep belongs beside the geometry that produces it, not in
        # a tab bar of its own - change a fin and re-sweep without navigating.
        vehicle_tab.tabs.addTab(aero_tab, "Cd vs Mach")
        layout.addWidget(vehicle_tab, stretch=1)

    def get_config(self):
        return self.vehicle_tab.get_config()

    def apply_config(self, cfg):
        self.vehicle_tab.apply_config(cfg)


class AssemblyPanel(QtWidgets.QGroupBox):
    """Pick one engine and one aerodynamics design: that pairing is the rocket.

    Selecting here loads the stored design into its tab, so the assembly and
    the tabs never disagree about what is about to be flown.
    """

    def __init__(self, engine_section, aero_section, on_changed=None, parent=None):
        super().__init__("Rocket assembly — engine + aerodynamics", parent)
        self.engine_section = engine_section
        self.aero_section = aero_section
        self._on_changed = on_changed

        form = QtWidgets.QFormLayout(self)
        form.setSpacing(4)

        self.engine_combo = QtWidgets.QComboBox()
        self.engine_combo.setToolTip(
            "Saved engine designs, from the Engine tab. Choosing one loads it.")
        self.engine_combo.activated.connect(self._engine_picked)
        form.addRow("Engine:", self.engine_combo)

        self.aero_combo = QtWidgets.QComboBox()
        self.aero_combo.setToolTip(
            "Saved aerodynamics designs, from the Aerodynamics tab. "
            "Choosing one loads it.")
        self.aero_combo.activated.connect(self._aero_picked)
        form.addRow("Aerodynamics:", self.aero_combo)

        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color:{_P['text_dim']}; font-size:9pt;")
        form.addRow(self.note)

        refresh = QtWidgets.QPushButton("Refresh lists")
        refresh.clicked.connect(self.refresh)
        form.addRow(refresh)

        self.refresh()

    _UNSAVED = "(what is on the tab now)"

    def refresh(self):
        for combo, section in ((self.engine_combo, self.engine_section),
                               (self.aero_combo, self.aero_section)):
            keep = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(self._UNSAVED)
            combo.addItems(section.library.names())
            idx = combo.findText(keep)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        self._describe()

    def _engine_picked(self):
        name = self.engine_combo.currentText()
        if name and name != self._UNSAVED:
            self.engine_section.library.load(name)
        self._describe()
        if self._on_changed:
            self._on_changed()

    def _aero_picked(self):
        name = self.aero_combo.currentText()
        if name and name != self._UNSAVED:
            self.aero_section.library.load(name)
        self._describe()
        if self._on_changed:
            self._on_changed()

    def selection(self):
        """(engine name or None, aerodynamics name or None)."""
        e = self.engine_combo.currentText()
        a = self.aero_combo.currentText()
        return (None if e == self._UNSAVED else e,
                None if a == self._UNSAVED else a)

    def set_selection(self, engine_name, aero_name):
        for combo, name in ((self.engine_combo, engine_name),
                            (self.aero_combo, aero_name)):
            if not name:
                continue
            idx = combo.findText(name)
            if idx >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)
        self._describe()

    def _describe(self):
        engine, aero = self.selection()
        self.note.setText(
            f"Flying <b>{engine or 'the Engine tab as it stands'}</b> in "
            f"<b>{aero or 'the Aerodynamics tab as it stands'}</b>. "
            f"Save the pair on the Rockets tab to keep it.")
