"""Aerodynamics: airframe shape, mass and balance, launch site, and recovery.

Three things the simulator needs that had nowhere to live before:

  * the airframe's actual shape - nose cone profile and length, body tube,
    boat tail, fin planform - which is what the Mach-5 drag model and the
    Barrowman centre of pressure are computed from
  * where and when you are launching - field elevation, temperature, pressure,
    wind and its shear, rail length and tilt
  * the recovery train - single deploy, dual deploy, reefed, streamer - with
    each stage's canopy, size, trigger and inflation independently editable

Everything here feeds the flight model directly, so changing a nose cone or a
wind speed changes the trajectory rather than just a label.
"""
from __future__ import annotations

import math

from PyQt5 import QtWidgets, QtCore

import aero
import atmosphere as atmosphere_mod
import recovery as recovery_mod
import flight_model
import unit_fields
import mass_tab
import theme

_P = theme.PALETTE

# (label, attribute, display factor, decimals, tooltip)
_NOSE_FIELDS = [
    ("Nose length (mm)", "nose_length_m", 1000.0, 1,
     "Longer is finer, and a finer nose pays much less wave drag supersonic."),
]
_BODY_FIELDS = [
    ("Body diameter (mm)", "body_diameter_m", 1000.0, 1,
     "Sets the reference area every drag coefficient is measured against."),
    ("Body length, less nose (mm)", "body_length_m", 1000.0, 1, ""),
    ("Surface roughness (um)", "surface_roughness_um", 1.0, 1,
     "20 um is smooth painted glass; 100 um is a rough finish. Sets the "
     "skin-friction floor."),
    ("Measured Cd override", "cd_override", 1.0, 3,
     "0 = use the computed drag buildup. Set a number here to force a Cd you "
     "measured or got from CFD/RASAero - it replaces the estimate entirely."),
    ("Boat tail length (mm)", "boattail_length_m", 1000.0, 1,
     "Zero for a plain blunt aft end."),
    ("Boat tail exit dia (mm)", "boattail_exit_diameter_m", 1000.0, 1,
     "Smaller than body diameter; cuts base drag."),
]
_FIN_FIELDS = [
    ("Fin count", "fin_count", 1.0, 0, ""),
    ("Root chord (mm)", "fin_root_chord_m", 1000.0, 1, ""),
    ("Tip chord (mm)", "fin_tip_chord_m", 1000.0, 1, ""),
    ("Semi-span (mm)", "fin_span_m", 1000.0, 1, "Exposed span of one fin."),
    ("Sweep distance (mm)", "fin_sweep_m", 1000.0, 1,
     "How far back the leading edge travels from root to tip."),
    ("Thickness (mm)", "fin_thickness_m", 1000.0, 2,
     "Thin fins are low drag and flutter early. This is the trade."),
]
_MASS_FIELDS = [
    ("Dry mass (kg)", "dry_mass_kg", 1.0, 2, "Everything except propellant."),
    ("Propellant mass (kg)", "propellant_mass_kg", 1.0, 3,
     "Filled from the Engine Lab when you send a motor across."),
    ("Dry CG from nose (mm)", "dry_cg_m", 1000.0, 1,
     "Balance point with the tanks empty."),
    ("Propellant CG from nose (mm)", "propellant_cg_m", 1000.0, 1,
     "Where the propellant sits. As it burns off the CG walks toward the dry "
     "CG, which is why stability changes during the burn."),
]
_SITE_FIELDS = [
    ("Field elevation (m ASL)", "elevation_m", 1.0, 1, ""),
    ("Latitude (deg)", "latitude_deg", 1.0, 3, "Used for the gravity model."),
    ("Air temperature (C)", "temperature_c", 1.0, 1, ""),
    ("Station pressure (hPa)", "pressure_pa", 0.01, 1,
     "Pressure measured at the pad, not sea-level corrected."),
    ("Relative humidity (%)", "humidity_pct", 1.0, 0, ""),
    ("Wind speed (m/s)", "wind_speed_ms", 1.0, 1,
     "Steady wind at the reference height."),
    ("Wind direction (deg)", "wind_dir_deg", 1.0, 0,
     "The direction the wind blows TOWARD. Only the Stability Test tab used "
     "to hold this, so it never reached the flight."),
    ("Wind measured at (m)", "wind_ref_height_m", 1.0, 1, ""),
    ("Wind shear exponent", "wind_shear_exp", 1.0, 3,
     "0.143 is the open-terrain 1/7 power law. Higher over trees or buildings."),
    ("Rail length (m)", "rail_length_m", 1.0, 2, ""),
    ("Rail tilt from vertical (deg)", "rail_angle_deg", 1.0, 1,
     "Tilting away from the wind is the usual way to fight weathercocking."),
]

_RECOVERY_PRESETS = [
    "Dual deploy (drogue + main)",
    "Single deploy (main at apogee)",
    "Streamer drogue + main",
    "Reefed single deploy",
    "Custom",
]


class _EjectionChargeEditor(QtWidgets.QGroupBox):
    """Sizes the charge that has to open a bay, and the pins holding it shut.

    Every term in this is known - pin shear area, bay area, bay volume, the
    gas constant of black powder - so the panel works the sum rather than
    offering a rule of thumb. It answers it in both directions, because both
    get asked: how much powder shears the pins I have, and how many pins can
    the charge I can fit actually break.

    It does not replace a ground test and says so on the face of it.
    """

    def __init__(self, parent=None):
        super().__init__("Ejection charge", parent)
        form = QtWidgets.QFormLayout(self)

        self.charge_type = QtWidgets.QComboBox()
        self.charge_type.addItems([recovery_mod.CHARGE_BLACK_POWDER,
                                   recovery_mod.CHARGE_CO2])
        self.charge_type.setToolTip(
            "Black powder burns to a hot gas and is sized from its own gas "
            "constant. CO2 arrives cold out of a cartridge, so the same "
            "pressure takes several times the mass - and you buy it in fixed "
            "cartridge sizes.")
        form.addRow("Charge:", self.charge_type)

        self.bay_diameter = QtWidgets.QDoubleSpinBox()
        self.bay_diameter.setRange(10.0, 1000.0)
        self.bay_diameter.setDecimals(1)
        self.bay_diameter.setValue(101.6)
        self.bay_diameter.setToolTip(
            "The inside diameter of the bay being pressurised. This is the "
            "area the pressure pushes on, so it drives the whole sum.")
        form.addRow("Bay diameter (mm):", self.bay_diameter)

        self.bay_length = QtWidgets.QDoubleSpinBox()
        self.bay_length.setRange(10.0, 5000.0)
        self.bay_length.setDecimals(1)
        self.bay_length.setValue(254.0)
        form.addRow("Bay length (mm):", self.bay_length)

        self.free_volume = QtWidgets.QDoubleSpinBox()
        self.free_volume.setRange(0.0, 500000.0)
        self.free_volume.setDecimals(0)
        self.free_volume.setValue(0.0)
        self.free_volume.setToolTip(
            "Leave at 0 to use the bay's own cylinder volume. Set it to the "
            "volume the gas can actually reach once the chute, the harness "
            "and the av-bay are in there - which is smaller, and is what the "
            "pressure has to fill.")
        form.addRow("Free volume (cm3, 0 = geometry):", self.free_volume)

        self.pin_type = QtWidgets.QComboBox()
        self.pin_type.addItems(list(recovery_mod.SHEAR_PINS.keys()))
        form.addRow("Shear pin:", self.pin_type)

        self.pin_material = QtWidgets.QComboBox()
        self.pin_material.addItems(list(recovery_mod.SHEAR_PIN_MATERIALS.keys()))
        self.pin_material.setToolTip(
            "Nylon is the shear pin. Brass and steel are in the list so that "
            "choosing one shows up as an absurd charge instead of quietly "
            "sizing a charge that will never break them.")
        form.addRow("Pin material:", self.pin_material)

        self.pin_count = QtWidgets.QSpinBox()
        self.pin_count.setRange(0, 24)
        self.pin_count.setValue(2)
        form.addRow("Pins in the joint:", self.pin_count)

        self.friction = QtWidgets.QDoubleSpinBox()
        self.friction.setRange(0.0, 10000.0)
        self.friction.setDecimals(0)
        self.friction.setToolTip(
            "Whatever the coupler and nose cone fit add on top of the pins. "
            "Nobody can correlate this - it is a measurement, taken by "
            "pulling the joint apart - so it is an input and defaults to 0.")
        form.addRow("Friction allowance (N):", self.friction)

        self.safety = QtWidgets.QDoubleSpinBox()
        self.safety.setRange(1.0, 5.0)
        self.safety.setSingleStep(0.1)
        self.safety.setDecimals(2)
        self.safety.setValue(1.5)
        self.safety.setToolTip(
            "The published shear force for a given pin spans a range and the "
            "figures here sit at the bottom of it, so this factor is doing "
            "real work. 1.5 is the floor, not a starting point to trim.")
        form.addRow("Safety factor:", self.safety)

        # The other direction: how many pins will hold a given load.
        self.hold_force = QtWidgets.QDoubleSpinBox()
        self.hold_force.setRange(0.0, 100000.0)
        self.hold_force.setDecimals(0)
        self.hold_force.setToolTip(
            "The load the joint has to survive without shearing - drogue "
            "snatch, or the drag trying to pull the bay apart on the way "
            "down. The answer below is how many of the chosen pin that takes.")
        form.addRow("Load the pins must hold (N):", self.hold_force)

        self.pins_needed = QtWidgets.QLabel("-")
        form.addRow("Pins needed for that load:", self.pins_needed)

        self.brief = QtWidgets.QTextEdit()
        self.brief.setReadOnly(True)
        self.brief.setMinimumHeight(190)
        self.brief.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        self.brief.setFontFamily("monospace")
        form.addRow(self.brief)

        warn = QtWidgets.QLabel(
            "<b>Ground test every charge.</b> This sizes it. Only a ground "
            "test shows the bay actually opens, that the pins were in the "
            "holes you think they were, and that the gas did not just vent "
            "past a bulkhead that does not seal.")
        warn.setWordWrap(True)
        warn.setStyleSheet(
            f"QLabel {{ background:{_P['panel']}; color:{_P['text']}; "
            f"border:1px solid {_P['border']}; "
            f"border-left:3px solid {_P['critical']}; padding:8px; }}")
        form.addRow(warn)

        for w in (self.bay_diameter, self.bay_length, self.free_volume,
                  self.friction, self.safety, self.hold_force):
            w.valueChanged.connect(self._recompute)
        self.pin_count.valueChanged.connect(self._recompute)
        for w in (self.charge_type, self.pin_type, self.pin_material):
            w.currentTextChanged.connect(self._recompute)
        self._recompute()

    def charge(self) -> recovery_mod.EjectionCharge:
        return recovery_mod.EjectionCharge(
            name="Ejection bay",
            bay_diameter_m=self.bay_diameter.value() / 1000.0,
            bay_length_m=self.bay_length.value() / 1000.0,
            free_volume_m3=self.free_volume.value() * 1e-6,
            pin_type=self.pin_type.currentText(),
            pin_count=self.pin_count.value(),
            pin_material=self.pin_material.currentText(),
            friction_n=self.friction.value(),
            safety_factor=self.safety.value(),
            charge_type=self.charge_type.currentText(),
        )

    def apply_charge(self, charge: recovery_mod.EjectionCharge):
        """Put a saved charge back on the form.

        Signals are held down across the lot so the brief is rebuilt once at
        the end rather than once per field, which would otherwise show the
        reader six wrong answers on the way to the right one.
        """
        widgets = (self.bay_diameter, self.bay_length, self.free_volume,
                   self.pin_count, self.friction, self.safety,
                   self.charge_type, self.pin_type, self.pin_material)
        blocked = [w.blockSignals(True) for w in widgets]
        try:
            self.bay_diameter.setValue(charge.bay_diameter_m * 1000.0)
            self.bay_length.setValue(charge.bay_length_m * 1000.0)
            self.free_volume.setValue(charge.free_volume_m3 * 1e6)
            self.pin_count.setValue(int(charge.pin_count))
            self.friction.setValue(charge.friction_n)
            self.safety.setValue(charge.safety_factor)
            for combo, text in ((self.charge_type, charge.charge_type),
                                (self.pin_type, charge.pin_type),
                                (self.pin_material, charge.pin_material)):
                idx = combo.findText(text)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        finally:
            for w, was in zip(widgets, blocked):
                w.blockSignals(was)
        self._recompute()

    def set_bay_diameter_m(self, diameter_m: float):
        """Follow the airframe, so the bay is not sized for a different rocket."""
        if diameter_m and diameter_m > 0:
            mm = diameter_m * 1000.0
            if abs(mm - self.bay_diameter.value()) > 0.05:
                blocked = self.bay_diameter.blockSignals(True)
                self.bay_diameter.setValue(mm)
                self.bay_diameter.blockSignals(blocked)
                self._recompute()

    def _recompute(self):
        charge = self.charge()
        self.brief.setPlainText(charge.brief())
        pin = self.pin_type.currentText()
        material = self.pin_material.currentText()
        one = recovery_mod.pin_shear_force(pin, 1, material)
        need = recovery_mod.pins_for_force(self.hold_force.value(), pin,
                                           material)
        self.pins_needed.setText(
            "%d  (each %s holds %.0f N / %.0f lbf)"
            % (need, pin, one, one / 4.44822))


class _StageEditor(QtWidgets.QGroupBox):
    """Editor for one recovery stage."""

    changed = QtCore.pyqtSignal()

    def __init__(self, stage: recovery_mod.RecoveryStage, parent=None):
        super().__init__(stage.name, parent)
        self.stage = stage
        form = QtWidgets.QFormLayout(self)

        self.enabled = QtWidgets.QCheckBox("Stage armed")
        self.enabled.setChecked(stage.enabled)
        form.addRow(self.enabled)

        self.name_edit = QtWidgets.QLineEdit(stage.name)
        form.addRow("Name:", self.name_edit)

        self.canopy = QtWidgets.QComboBox()
        self.canopy.addItems(list(recovery_mod.CANOPY_TYPES.keys()))
        idx = self.canopy.findText(stage.canopy_type)
        if idx >= 0:
            self.canopy.setCurrentIndex(idx)
        self.canopy.currentTextChanged.connect(self._canopy_changed)
        form.addRow("Canopy type:", self.canopy)

        self.canopy_note = QtWidgets.QLabel()
        self.canopy_note.setWordWrap(True)
        self.canopy_note.setStyleSheet(f"color:{_P['text_dim']}; font-size:9pt;")
        form.addRow(self.canopy_note)

        self.diameter = QtWidgets.QLineEdit(f"{stage.diameter_m:.2f}")
        form.addRow("Diameter (m):", self.diameter)
        self.cd = QtWidgets.QLineEdit(f"{stage.cd:.2f}")
        form.addRow("Drag coefficient:", self.cd)

        self.trigger = QtWidgets.QComboBox()
        self.trigger.addItems(["At apogee", "Descending through altitude",
                               "At a set time", "Delay after previous stage"])
        self.trigger.setCurrentIndex(
            {recovery_mod.TRIGGER_APOGEE: 0, recovery_mod.TRIGGER_ALTITUDE: 1,
             recovery_mod.TRIGGER_TIME: 2, recovery_mod.TRIGGER_DELAY: 3}
            .get(stage.trigger, 0))
        form.addRow("Fires:", self.trigger)

        self.trigger_alt = QtWidgets.QLineEdit(f"{stage.trigger_altitude_m:.0f}")
        form.addRow("Altitude AGL (m):", self.trigger_alt)
        self.trigger_time = QtWidgets.QLineEdit(f"{stage.trigger_time_s:.1f}")
        form.addRow("Time / delay (s):", self.trigger_time)
        self.inflation = QtWidgets.QLineEdit(f"{stage.inflation_time_s:.2f}")
        form.addRow("Inflation time (s):", self.inflation)

        self.reefed = QtWidgets.QCheckBox("Reefed (opens partially, then disreefs)")
        self.reefed.setChecked(stage.reefed)
        self.reefed.toggled.connect(self._reef_toggled)
        form.addRow(self.reefed)
        self.reef_ratio = QtWidgets.QLineEdit(f"{stage.reef_ratio:.2f}")
        form.addRow("Reefed area fraction:", self.reef_ratio)
        self.reef_duration = QtWidgets.QLineEdit(f"{stage.reef_duration_s:.1f}")
        form.addRow("Reefed for (s):", self.reef_duration)
        self.disreef = QtWidgets.QLineEdit(f"{stage.disreef_time_s:.1f}")
        form.addRow("Disreef time (s):", self.disreef)

        # A canopy, its harness, bag and charges are real mass at a real
        # station, so they belong in the mass buildup like anything else.
        self.mass = QtWidgets.QLineEdit(f"{stage.mass_kg:.3f}")
        self.mass.setToolTip(
            "Canopy, shroud lines, harness, deployment bag and charges. "
            "Counted in the Mass & Ballast buildup.")
        form.addRow("Stage mass (kg):", self.mass)
        self.position = QtWidgets.QLineEdit(f"{stage.position_m*1000:.1f}")
        self.position.setToolTip(
            "Where it is packed, from the nose tip. Recovery normally sits "
            "forward of the motor, so it pulls the CG forward.")
        form.addRow("Packed station (mm):", self.position)

        # Show the canopy note, but do NOT overwrite the Cd we just loaded:
        # the stage carries its own value (a dual-deploy drogue is Cd 1.5, not
        # the drogue-slider default), and a saved custom Cd has to survive a
        # reload. The type only suggests a Cd when a person picks one.
        self._describe_canopy(self.canopy.currentText())
        self._reef_toggled(self.reefed.isChecked())

    def _describe_canopy(self, name):
        _cd, note = recovery_mod.CANOPY_TYPES.get(name, (1.5, ""))
        self.canopy_note.setText(note)

    def _canopy_changed(self, name):
        """A person picked a canopy type: suggest its typical Cd."""
        cd, _note = recovery_mod.CANOPY_TYPES.get(name, (1.5, ""))
        self._describe_canopy(name)
        self.cd.setText(f"{cd:.2f}")

    def _reef_toggled(self, on):
        for w in (self.reef_ratio, self.reef_duration, self.disreef):
            w.setEnabled(on)

    def to_stage(self) -> recovery_mod.RecoveryStage:
        s = self.stage
        s.enabled = self.enabled.isChecked()
        s.name = self.name_edit.text().strip() or s.name
        s.canopy_type = self.canopy.currentText()
        s.diameter_m = _f(self.diameter.text(), s.diameter_m)
        s.cd = _f(self.cd.text(), s.cd)
        s.mass_kg = _f(self.mass.text(), s.mass_kg)
        s.position_m = _f(self.position.text(), s.position_m * 1000.0) / 1000.0
        s.trigger = [recovery_mod.TRIGGER_APOGEE, recovery_mod.TRIGGER_ALTITUDE,
                     recovery_mod.TRIGGER_TIME, recovery_mod.TRIGGER_DELAY][
            self.trigger.currentIndex()]
        s.trigger_altitude_m = _f(self.trigger_alt.text(), s.trigger_altitude_m)
        s.trigger_time_s = _f(self.trigger_time.text(), s.trigger_time_s)
        s.inflation_time_s = _f(self.inflation.text(), s.inflation_time_s)
        s.reefed = self.reefed.isChecked()
        s.reef_ratio = _f(self.reef_ratio.text(), s.reef_ratio)
        s.reef_duration_s = _f(self.reef_duration.text(), s.reef_duration_s)
        s.disreef_time_s = _f(self.disreef.text(), s.disreef_time_s)
        return s


def _f(text, fallback=0.0):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return fallback


class VehicleTabWidget(QtWidgets.QWidget):
    """Airframe, mass properties, launch site and recovery configuration."""

    def __init__(self, on_changed=None, parent=None):
        super().__init__(parent)
        self._on_changed = on_changed
        self._fields = {}
        self._binder = unit_fields.FieldBinder(
            on_change=self._refresh_summary)
        self._stage_editors = []
        # Signals fire while the pages are still being assembled, so the
        # summary must stay quiet until every widget it reads actually exists.
        self._ready = False
        self._build_ui()
        self._ready = True
        self._set_recovery_preset(_RECOVERY_PRESETS[0])
        self._refresh_summary()

    # ---- UI ---------------------------------------------------------------
    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        # Kept as an attribute so the Aerodynamics section can add the
        # Cd-vs-Mach page alongside these rather than nesting another tab bar.
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._airframe_page(), "Airframe")
        self.tabs.addTab(self._site_page(), "Launch Site")
        self.tabs.addTab(self._recovery_page(), "Recovery")
        self.tabs.addTab(self._mass_page(), "Mass && Ballast")
        outer.addWidget(self.tabs, stretch=1)

        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(
            f"background:{_P['panel']}; border:1px solid {_P['border']}; "
            f"border-left:3px solid {_P['accent']}; padding:10px; "
            f"color:{_P['text']}; font-size:10pt;")
        outer.addWidget(self.summary)

        row = QtWidgets.QHBoxLayout()
        recalc = QtWidgets.QPushButton("Recalculate")
        recalc.clicked.connect(self._refresh_summary)
        row.addWidget(recalc)
        row.addStretch()
        outer.addLayout(row)

    def _scroll(self, inner):
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(inner)
        return area

    def _airframe_page(self):
        host = QtWidgets.QWidget()
        cols = QtWidgets.QHBoxLayout(host)

        left = QtWidgets.QVBoxLayout()
        nose_group = QtWidgets.QGroupBox("Nose Cone")
        nose_form = QtWidgets.QFormLayout(nose_group)
        self.nose_shape = QtWidgets.QComboBox()
        self.nose_shape.addItems(list(aero.NOSE_SHAPES.keys()))
        self.nose_shape.setCurrentIndex(
            list(aero.NOSE_SHAPES).index("Tangent Ogive"))
        self.nose_shape.currentTextChanged.connect(self._nose_changed)
        nose_form.addRow("Profile:", self.nose_shape)
        self.nose_note = QtWidgets.QLabel()
        self.nose_note.setWordWrap(True)
        self.nose_note.setStyleSheet(f"color:{_P['text_dim']}; font-size:9pt;")
        nose_form.addRow(self.nose_note)
        self._add_fields(nose_form, _NOSE_FIELDS, aero.Airframe())
        left.addWidget(nose_group)

        body_group = QtWidgets.QGroupBox("Body Tube")
        self._add_fields(QtWidgets.QFormLayout(body_group), _BODY_FIELDS, aero.Airframe())
        left.addWidget(body_group)
        left.addStretch()

        right = QtWidgets.QVBoxLayout()
        fin_group = QtWidgets.QGroupBox("Fins")
        fin_form = QtWidgets.QFormLayout(fin_group)
        self._add_fields(fin_form, _FIN_FIELDS, aero.Airframe())
        self.fin_profile = QtWidgets.QComboBox()
        self.fin_profile.addItems(list(aero.FIN_PROFILES.keys()))
        self.fin_profile.setCurrentIndex(
            list(aero.FIN_PROFILES).index("Rounded leading"))
        fin_form.addRow("Edge profile:", self.fin_profile)
        right.addWidget(fin_group)

        mass_group = QtWidgets.QGroupBox("Mass && Balance")
        self._add_fields(QtWidgets.QFormLayout(mass_group), _MASS_FIELDS,
                         flight_model.MassProperties())
        right.addWidget(mass_group)
        right.addStretch()

        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        self._nose_changed(self.nose_shape.currentText())
        return self._scroll(host)

    def _mass_page(self):
        """Where the weight sits, which is what actually sets stability."""
        self.mass_builder = mass_tab.MassBuildupWidget(
            on_changed=self._mass_changed,
            get_cp=lambda: self.airframe().center_of_pressure(0.3),
            get_diameter=lambda: self.airframe().body_diameter_m,
            get_propellant=self._propellant_for_mass)
        return self.mass_builder

    def _propellant_for_mass(self):
        """(mass, station, length) of the propellant, for the mass readout."""
        return (self._value("propellant_mass_kg", 0.0),
                self._value("propellant_cg_m", 0.0),
                0.0)

    def _mass_changed(self):
        if getattr(self, "_ready", False):
            self._refresh_summary()

    def _site_page(self):
        host = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(host)
        group = QtWidgets.QGroupBox("Conditions at the Pad")
        self._add_fields(QtWidgets.QFormLayout(group), _SITE_FIELDS,
                         atmosphere_mod.LaunchSite())
        v.addWidget(group)

        note = QtWidgets.QLabel(
            "Wind grows with height following the power-law shear profile, so "
            "the vehicle meets stronger wind as it climbs than the number "
            "measured at the pad. Field elevation and temperature shift the "
            "whole atmospheric column: a hot, high desert site gives noticeably "
            "less drag and a higher apogee than a cold sea-level one.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{_P['text_dim']}; font-size:9pt; padding:6px;")
        v.addWidget(note)
        v.addStretch()
        return self._scroll(host)

    def _recovery_page(self):
        host = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(host)

        row = QtWidgets.QFormLayout()
        self.recovery_preset = QtWidgets.QComboBox()
        self.recovery_preset.addItems(_RECOVERY_PRESETS)
        self.recovery_preset.currentTextChanged.connect(self._set_recovery_preset)
        row.addRow("Recovery mode:", self.recovery_preset)
        v.addLayout(row)

        self.stage_host = QtWidgets.QWidget()
        self.stage_layout = QtWidgets.QVBoxLayout(self.stage_host)
        self.stage_layout.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.stage_host)

        btns = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add stage")
        add.clicked.connect(self._add_stage)
        remove = QtWidgets.QPushButton("Remove last stage")
        remove.clicked.connect(self._remove_stage)
        btns.addWidget(add)
        btns.addWidget(remove)
        btns.addStretch()
        v.addLayout(btns)

        self.charge_editor = _EjectionChargeEditor()
        v.addWidget(self.charge_editor)

        v.addStretch()
        return self._scroll(host)

    def _add_fields(self, form, spec, defaults):
        for label, attr, factor, dec, tip in spec:
            edit = self._binder.make(attr, tip)
            value = getattr(defaults, attr, 0.0) or 0.0
            # Defaults are in the attribute's own storage unit, which is not
            # always SI - roughness is microns, site temperature is Celsius.
            self._binder.set(attr, value, dec)
            self._fields[attr] = (edit, factor, dec)
            form.addRow(self._binder.label_for(attr, label) + ":", edit)

    def _nose_changed(self, name):
        _cp, _wave, desc = aero.NOSE_SHAPES.get(name, (0, 0, ""))
        self.nose_note.setText(desc)
        self._refresh_summary()

    # ---- recovery presets --------------------------------------------------
    def _set_recovery_preset(self, name):
        if name == "Custom":
            return
        system = {
            "Dual deploy (drogue + main)": recovery_mod.RecoverySystem.dual_deploy(),
            "Single deploy (main at apogee)": recovery_mod.RecoverySystem.single_deploy(),
            "Streamer drogue + main": recovery_mod.RecoverySystem.streamer_drogue(),
            "Reefed single deploy": recovery_mod.RecoverySystem.reefed_main(),
        }.get(name)
        if system:
            self._load_stages(system.stages)

    def _load_stages(self, stages):
        for editor in self._stage_editors:
            editor.setParent(None)
        self._stage_editors = []
        for stage in stages:
            editor = _StageEditor(stage)
            self._stage_editors.append(editor)
            self.stage_layout.addWidget(editor)
        self._refresh_summary()

    def _add_stage(self):
        self._stage_editors.append(_StageEditor(recovery_mod.RecoveryStage(
            name=f"Stage {len(self._stage_editors) + 1}")))
        self.stage_layout.addWidget(self._stage_editors[-1])
        self.recovery_preset.setCurrentText("Custom")
        self._refresh_summary()

    def _remove_stage(self):
        if self._stage_editors:
            self._stage_editors.pop().setParent(None)
            self.recovery_preset.setCurrentText("Custom")
            self._refresh_summary()

    # ---- building the model objects ---------------------------------------
    def set_value(self, attr, value):
        """Write one field, in the unit the attribute is stored in.

        The counterpart to _value(). Launch conditions are shown in more than
        one place in the app - the launch site here, and the wind and rail
        angle again on the Stability Test tab, where you are setting up a
        launch. They are ONE value with several views, so the views need a
        way to write back to it rather than keeping copies that drift.
        """
        entry = self._fields.get(attr)
        if not entry:
            return False
        edit, factor, dec = entry
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        if isinstance(edit, unit_fields.UnitField):
            self._binder.set(attr, number, dec)
        else:
            edit.setText(f"{number * factor:.{dec}f}")
        return True

    def _value(self, attr, fallback=0.0):
        """Value in the unit this attribute is stored in."""
        entry = self._fields.get(attr)
        if not entry:
            return fallback
        edit, factor, _dec = entry
        if isinstance(edit, unit_fields.UnitField):
            return self._binder.get(attr, fallback)
        return _f(edit.text(), fallback * factor) / factor

    def cd_override(self):
        """A measured Cd to use instead of the buildup, or None."""
        value = self._value("cd_override", 0.0)
        return value if value and value > 0 else None

    def airframe(self) -> aero.Airframe:
        af = aero.Airframe()
        for attr in ("nose_length_m", "body_diameter_m", "body_length_m",
                     "surface_roughness_um", "boattail_length_m",
                     "boattail_exit_diameter_m", "fin_root_chord_m",
                     "fin_tip_chord_m", "fin_span_m", "fin_sweep_m",
                     "fin_thickness_m"):
            setattr(af, attr, self._value(attr, getattr(af, attr)))
        af.fin_count = int(round(self._value("fin_count", af.fin_count)))
        af.nose_shape = self.nose_shape.currentText()
        af.fin_profile = self.fin_profile.currentText()
        return af

    def mass_properties(self) -> flight_model.MassProperties:
        mp = flight_model.MassProperties()
        for attr in ("dry_mass_kg", "propellant_mass_kg", "dry_cg_m",
                     "propellant_cg_m"):
            setattr(mp, attr, self._value(attr, getattr(mp, attr)))
        # Components win when there are any: they know where the weight is,
        # not just where its average is, so CG, its migration through the burn
        # and the pitch inertia all come out of them rather than being typed.
        # The recovery train joins the same buildup - a main and its harness
        # are real mass at a real station.
        builder = getattr(self, "mass_builder", None)
        if builder is not None:
            buildup = builder.buildup()
            buildup.components = list(buildup.components)
            buildup.components += self.recovery_system().mass_components()
            if buildup.active():
                mp.buildup = buildup
        return mp

    def launch_site(self) -> atmosphere_mod.LaunchSite:
        site = atmosphere_mod.LaunchSite()
        for attr in ("elevation_m", "latitude_deg", "temperature_c",
                     "pressure_pa", "humidity_pct", "wind_speed_ms",
                     "wind_dir_deg",
                     "wind_ref_height_m", "wind_shear_exp", "rail_length_m",
                     "rail_angle_deg"):
            setattr(site, attr, self._value(attr, getattr(site, attr)))
        return site

    def recovery_system(self) -> recovery_mod.RecoverySystem:
        stages = [e.to_stage() for e in self._stage_editors]
        return recovery_mod.RecoverySystem(self.recovery_preset.currentText(), stages)

    def set_propellant_mass(self, kg: float):
        entry = self._fields.get("propellant_mass_kg")
        if entry:
            entry[0].setText(f"{kg:.3f}")
            self._refresh_summary()

    # ---- summary -----------------------------------------------------------
    def _refresh_summary(self):
        if not getattr(self, "_ready", False):
            return
        try:
            af = self.airframe()
            mp = self.mass_properties()
            site = self.launch_site()
            recovery_system = self.recovery_system()
        except Exception as exc:
            self.summary.setText(f"Configuration error: {exc}")
            return

        # effective_dry_mass(), not the typed field. CG and margin on this
        # same line already come from the components; taking mass from the
        # field mixed the two and put the landing speed ~29% out when they
        # disagreed.
        dry = mp.effective_dry_mass()
        wet = dry + mp.propellant_mass_kg
        cg_wet, cg_dry = mp.cg(mp.propellant_mass_kg), mp.cg(0.0)
        cp = af.center_of_pressure(0.3)
        d = max(1e-6, af.body_diameter_m)
        marg_wet, marg_dry = (cp - cg_wet) / d, (cp - cg_dry) / d

        _temp, _press, rho, a_snd, _visc = site.properties(0.0)
        cd_sub, _ = aero.drag_coefficient(0.3, 0.0, 0.3 * a_snd, af, site)
        cd_max, _ = aero.drag_coefficient(1.1, 3000.0, 1.1 * 320, af, site)
        cd_hi, _ = aero.drag_coefficient(3.0, 15000.0, 3.0 * 300, af, site)

        rate_all = recovery_system.descent_rate(dry, rho)
        stages = recovery_system.active_stages()
        first = (recovery_system.descent_rate_stage(0, dry, rho)
                 if stages else float('inf'))

        def cal(v):
            return f"{v:.2f} cal"

        warn = ""
        if marg_wet < 1.0:
            warn = (f"<br><span style='color:{_P['critical']}'><b>Unstable at "
                    f"liftoff</b> - stability margin under 1 caliber. Move mass "
                    f"forward or grow the fins.</span>")
        elif marg_wet > 4.0:
            warn = (f"<br><span style='color:{_P['caution']}'>Very overstable "
                    f"({cal(marg_wet)}) - it will weathercock hard into any "
                    f"wind and lose altitude.</span>")

        self.summary.setText(
            f"<b>{af.total_length:.2f} m</b> long, <b>{af.body_diameter_m*1000:.0f} mm</b> "
            f"across, {af.nose_shape} nose (fineness {af.nose_fineness:.1f}), "
            f"{af.fin_count} fins &nbsp;|&nbsp; wet <b>{wet:.1f} kg</b>, dry "
            f"{dry:.1f} kg<br>"
            f"CP {cp:.2f} m &nbsp; CG {cg_wet:.2f} m wet / {cg_dry:.2f} m dry "
            f"&nbsp; margin <b>{cal(marg_wet)}</b> wet, <b>{cal(marg_dry)}</b> burnout<br>"
            f"Cd: <b>{cd_sub:.2f}</b> subsonic, <b>{cd_max:.2f}</b> transonic peak, "
            f"<b>{cd_hi:.2f}</b> at Mach 3 &nbsp;|&nbsp; wetted area "
            f"{af.wetted_area():.2f} m²<br>"
            f"Recovery: <b>{recovery_system.mode}</b>, {len(stages)} stage(s) - "
            f"first stage {first:.1f} m/s, all open <b>{rate_all:.1f} m/s</b> "
            f"at this site's density{warn}")

        if self._on_changed:
            self._on_changed()

    # ---- profile save/load -------------------------------------------------
    def get_config(self) -> dict:
        return {
            # Stored in each attribute's own unit rather than as display
            # text, so a profile no longer depends on which unit the field
            # happened to be showing. "_units" marks the new format; without
            # it the values are read as the display text they used to be.
            "_units": "storage",
            "fields": {attr: (self._binder.get(attr)
                              if isinstance(edit, unit_fields.UnitField)
                              else edit.text())
                       for attr, (edit, _f, _d) in self._fields.items()},
            "nose_shape": self.nose_shape.currentText(),
            "fin_profile": self.fin_profile.currentText(),
            "recovery": self.recovery_system().to_dict(),
            "ejection_charge": (self.charge_editor.charge().to_dict()
                                if hasattr(self, "charge_editor") else {}),
            "mass_components": (self.mass_builder.get_config()
                                if hasattr(self, "mass_builder") else []),
        }

    def apply_config(self, cfg: dict):
        if not cfg:
            return
        storage_cfg = str(cfg.get("_units", "")) == "storage"
        for attr, value in (cfg.get("fields") or {}).items():
            entry = self._fields.get(attr)
            if not entry:
                continue
            edit, factor, dec = entry
            if isinstance(edit, unit_fields.UnitField):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if not storage_cfg:
                    # Legacy profile: the number is display text in the unit
                    # that used to be baked into this field's label.
                    number = number / factor
                self._binder.set(attr, number, dec)
            else:
                edit.setText(str(value))
        if hasattr(self, "mass_builder"):
            self.mass_builder.apply_config(cfg.get("mass_components") or [])
        for combo, key in ((self.nose_shape, "nose_shape"),
                           (self.fin_profile, "fin_profile")):
            idx = combo.findText(str(cfg.get(key, "")))
            if idx >= 0:
                combo.setCurrentIndex(idx)
        rec_cfg = cfg.get("recovery")
        if rec_cfg:
            system = recovery_mod.RecoverySystem.from_dict(rec_cfg)
            mode_idx = self.recovery_preset.findText(system.mode)
            self.recovery_preset.blockSignals(True)
            self.recovery_preset.setCurrentIndex(
                mode_idx if mode_idx >= 0
                else self.recovery_preset.findText("Custom"))
            self.recovery_preset.blockSignals(False)
            self._load_stages(system.stages)
        charge_cfg = cfg.get("ejection_charge")
        if charge_cfg and hasattr(self, "charge_editor"):
            self.charge_editor.apply_charge(
                recovery_mod.EjectionCharge.from_dict(charge_cfg))
        self._refresh_summary()
