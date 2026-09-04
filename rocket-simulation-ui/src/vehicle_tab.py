"""Vehicle tab: airframe shape, mass and balance, launch site, and recovery.

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

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._airframe_page(), "Airframe")
        tabs.addTab(self._site_page(), "Launch Site")
        tabs.addTab(self._recovery_page(), "Recovery")
        outer.addWidget(tabs, stretch=1)

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
        v.addStretch()
        return self._scroll(host)

    def _add_fields(self, form, spec, defaults):
        for label, attr, factor, dec, tip in spec:
            edit = QtWidgets.QLineEdit()
            value = getattr(defaults, attr, 0.0) or 0.0
            edit.setText(f"{value * factor:.{dec}f}")
            if tip:
                edit.setToolTip(tip)
            edit.editingFinished.connect(self._refresh_summary)
            self._fields[attr] = (edit, factor, dec)
            form.addRow(label + ":", edit)

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
    def _value(self, attr, fallback=0.0):
        entry = self._fields.get(attr)
        if not entry:
            return fallback
        edit, factor, _dec = entry
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
        return mp

    def launch_site(self) -> atmosphere_mod.LaunchSite:
        site = atmosphere_mod.LaunchSite()
        for attr in ("elevation_m", "latitude_deg", "temperature_c",
                     "pressure_pa", "humidity_pct", "wind_speed_ms",
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

        wet = mp.dry_mass_kg + mp.propellant_mass_kg
        cg_wet, cg_dry = mp.cg(mp.propellant_mass_kg), mp.cg(0.0)
        cp = af.center_of_pressure(0.3)
        d = max(1e-6, af.body_diameter_m)
        marg_wet, marg_dry = (cp - cg_wet) / d, (cp - cg_dry) / d

        _temp, _press, rho, a_snd, _visc = site.properties(0.0)
        cd_sub, _ = aero.drag_coefficient(0.3, 0.0, 0.3 * a_snd, af, site)
        cd_max, _ = aero.drag_coefficient(1.1, 3000.0, 1.1 * 320, af, site)
        cd_hi, _ = aero.drag_coefficient(3.0, 15000.0, 3.0 * 300, af, site)

        rate_all = recovery_system.descent_rate(mp.dry_mass_kg, rho)
        stages = recovery_system.active_stages()
        first = (recovery_system.descent_rate_stage(0, mp.dry_mass_kg, rho)
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
            f"{mp.dry_mass_kg:.1f} kg<br>"
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
            "fields": {attr: edit.text() for attr, (edit, _f, _d) in self._fields.items()},
            "nose_shape": self.nose_shape.currentText(),
            "fin_profile": self.fin_profile.currentText(),
            "recovery": self.recovery_system().to_dict(),
        }

    def apply_config(self, cfg: dict):
        if not cfg:
            return
        for attr, value in (cfg.get("fields") or {}).items():
            entry = self._fields.get(attr)
            if entry:
                entry[0].setText(str(value))
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
        self._refresh_summary()
