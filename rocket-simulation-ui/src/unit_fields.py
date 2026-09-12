"""Unit-aware input field, and the catalogue of what every field measures.

A ``UnitField`` is a line edit plus a unit selector that keeps its value in
SI internally. Reading it never returns a pound or an inch, so the physics
cannot pick up a unit bug from the UI, and switching the unit re-renders the
same physical value rather than reinterpreting the number.

Dimensionless fields (drag coefficient, Mach, O/F, efficiencies) render as a
bare line edit with no selector at all - see units.Quantity.
"""
from __future__ import annotations

from PyQt5 import QtWidgets, QtCore

import units as U


# ---------------------------------------------------------------------------
# What each field measures.
#
# Preferred units are chosen so the number a person reads is a sensible size:
# cross-sections, chords and wall thicknesses in mm/in because that is how
# they are machined and measured, but overall lengths (body, rail) in m/ft
# because a 3,000 mm rocket is a 3 m rocket in every conversation about it.
# The app previously had it both ways - "Body length, less nose (mm)" on one
# tab and "Body length (m)" on another, for the same quantity.
# ---------------------------------------------------------------------------
def _q(key, label, dimension=None, metric=None, imperial=None,
       decimals=3, help=""):
    return U.Quantity(key, label, dimension, metric, imperial, decimals, help)


_LEN_SMALL = dict(dimension="length", metric="mm", imperial="in", decimals=2)
_LEN_LONG = dict(dimension="length", metric="m", imperial="ft", decimals=3)

FIELDS: dict[str, U.Quantity] = {q.key: q for q in [
    # --- airframe geometry -------------------------------------------------
    _q("body_diameter", "Body diameter", **_LEN_SMALL),
    _q("body_length", "Body length, less nose", **_LEN_LONG),
    _q("body_wall", "Body wall thickness", **_LEN_SMALL),
    _q("nose_length", "Nose length", **_LEN_SMALL),
    _q("boattail_length", "Boat tail length", **_LEN_SMALL),
    _q("boattail_exit", "Boat tail exit diameter", **_LEN_SMALL),
    _q("surface_roughness", "Surface roughness",
       dimension="length", metric="um", imperial="um", decimals=1,
       help="Surface finish is quoted in microns in both systems."),
    # --- fins --------------------------------------------------------------
    _q("fin_root_chord", "Root chord", **_LEN_SMALL),
    _q("fin_tip_chord", "Tip chord", **_LEN_SMALL),
    _q("fin_span", "Semi-span", **_LEN_SMALL),
    _q("fin_sweep", "Sweep distance", **_LEN_SMALL),
    _q("fin_thickness", "Fin thickness", **_LEN_SMALL),
    _q("fin_count", "Fin count"),                      # dimensionless count
    # --- mass and balance --------------------------------------------------
    _q("dry_mass", "Dry mass", dimension="mass", metric="kg",
       imperial="lb", decimals=3),
    _q("propellant_mass", "Propellant mass", dimension="mass", metric="kg",
       imperial="lb", decimals=3),
    _q("liftoff_mass", "Liftoff mass", dimension="mass", metric="kg",
       imperial="lb", decimals=3),
    _q("dry_cg", "Dry CG from nose", **_LEN_SMALL),
    _q("propellant_cg", "Propellant CG from nose", **_LEN_SMALL),
    _q("pitch_inertia", "Pitch inertia", dimension="inertia",
       metric="kg.m2", imperial="lb.ft2", decimals=4),
    # --- launch site -------------------------------------------------------
    _q("elevation", "Site elevation", **_LEN_LONG),
    _q("rail_length", "Launch rail length", **_LEN_LONG),
    _q("rail_angle", "Rail tilt from vertical", dimension="angle",
       metric="deg", imperial="deg", decimals=2),
    _q("latitude", "Latitude", dimension="angle", metric="deg",
       imperial="deg", decimals=4),
    _q("temperature", "Air temperature", dimension="temperature",
       metric="C", imperial="F", decimals=1),
    _q("wind_speed", "Wind speed", dimension="velocity", metric="m/s",
       imperial="mph", decimals=2),
    _q("wind_ref_height", "Wind measured at", **_LEN_LONG),
    _q("humidity", "Relative humidity"),               # percent, dimensionless
    _q("air_density", "Air density", dimension="density", metric="kg/m3",
       imperial="lb/ft3", decimals=4),
    _q("station_pressure", "Station pressure", dimension="pressure",
       metric="kPa", imperial="psi", decimals=2,
       help="Measured at the pad, not sea-level corrected."),
    # --- recovery ----------------------------------------------------------
    _q("chute_diameter", "Canopy diameter", **_LEN_LONG),
    _q("chute_area", "Canopy area", dimension="area", metric="m2",
       imperial="ft2", decimals=3),
    _q("deploy_altitude", "Deploy altitude", **_LEN_LONG),
    _q("harness_rating", "Harness rating", dimension="force", metric="N",
       imperial="lbf", decimals=0),
    # --- engine: tank ------------------------------------------------------
    _q("tank_diameter", "Tank diameter", **_LEN_SMALL),
    _q("tank_length", "Tank length", **_LEN_SMALL),
    _q("tank_wall", "Tank wall thickness", **_LEN_SMALL),
    _q("tank_volume", "Tank volume", dimension="volume", metric="cc",
       imperial="in3", decimals=1),
    _q("tank_temp", "Initial tank temperature", dimension="temperature",
       metric="C", imperial="F", decimals=1),
    _q("fill_fraction", "Fill fraction"),              # dimensionless
    # --- engine: injector --------------------------------------------------
    _q("hole_diameter", "Injector hole diameter", **_LEN_SMALL),
    _q("n_holes", "Number of holes"),                  # dimensionless count
    _q("cd_injector", "Injector discharge coefficient"),
    _q("vent_diameter", "Vent orifice diameter", **_LEN_SMALL),
    _q("cd_vent", "Vent discharge coefficient"),
    # --- engine: grain -----------------------------------------------------
    _q("grain_length", "Grain length", **_LEN_SMALL),
    _q("grain_outer", "Grain outer diameter", **_LEN_SMALL),
    _q("port_diameter", "Initial port diameter", **_LEN_SMALL),
    _q("n_ports", "Number of ports"),                  # dimensionless count
    _q("pre_chamber", "Pre-combustion chamber", **_LEN_SMALL),
    _q("post_chamber", "Post-combustion chamber", **_LEN_SMALL),
    # --- engine: nozzle ----------------------------------------------------
    _q("throat_diameter", "Throat diameter", **_LEN_SMALL),
    _q("chamber_wall", "Chamber wall thickness", **_LEN_SMALL),
    _q("expansion_ratio", "Expansion ratio (Ae/At)"),  # dimensionless
    _q("div_angle", "Divergence half angle", dimension="angle",
       metric="deg", imperial="deg", decimals=1),
    _q("conv_angle", "Convergence half angle", dimension="angle",
       metric="deg", imperial="deg", decimals=1),
    _q("erosion_rate", "Throat erosion rate", dimension="speed_rate",
       metric="mm/s", imperial="in/s", decimals=4),
    _q("eta_cstar", "c* efficiency"),                  # dimensionless
    _q("eta_nozzle", "Nozzle efficiency"),             # dimensionless
    # --- gas properties ----------------------------------------------------
    _q("gamma", "Gas gamma (Cp/Cv)"),                  # dimensionless
    _q("molar_mass", "Molar mass",
       dimension="mass", metric="g", imperial="g", decimals=2,
       help="Per mole; grams per mole is universal in both systems."),
    # --- simulation --------------------------------------------------------
    _q("timestep", "Time step", dimension="time", metric="s",
       imperial="s", decimals=4),
    _q("target_altitude", "Target altitude", **_LEN_LONG),
    _q("cd_override", "Measured Cd override"),         # dimensionless
    _q("body_cd", "Body Cd"),                          # dimensionless
    _q("reference_area", "Reference area", dimension="area", metric="m2",
       imperial="in2", decimals=6),
]}


class UnitField(QtWidgets.QWidget):
    """Line edit + unit selector over one Quantity, holding SI internally."""

    valueChanged = QtCore.pyqtSignal()

    def __init__(self, quantity: U.Quantity, system: str = U.METRIC,
                 parent=None):
        super().__init__(parent)
        self.quantity = quantity
        self._system = system
        self._si = 0.0
        self._updating = False
        self._rendered_text = None

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.edit = QtWidgets.QLineEdit()
        self.edit.setMinimumWidth(96)
        self.edit.editingFinished.connect(self._read_edit)
        row.addWidget(self.edit, 1)

        self.combo = None
        if not quantity.dimensionless:
            self.combo = QtWidgets.QComboBox()
            self.combo.addItems(quantity.choices())
            self.combo.setCurrentText(quantity.preferred(system))
            self.combo.currentIndexChanged.connect(self._unit_changed)
            self.combo.setSizePolicy(QtWidgets.QSizePolicy.Fixed,
                                     QtWidgets.QSizePolicy.Fixed)
            row.addWidget(self.combo)
        if quantity.help:
            self.setToolTip(quantity.help)

    # -- value -------------------------------------------------------------
    def unit(self) -> str | None:
        return self.combo.currentText() if self.combo else None

    def value_si(self) -> float:
        """The field's value in SI, whatever unit is on screen."""
        self._read_edit()
        return self._si

    def set_value_si(self, si: float):
        self._si = float(si)
        self._render()

    def _render(self):
        self._updating = True
        shown = self.quantity.from_si(self._si, self.unit())
        text = f"{shown:,.{self.quantity.decimals}f}".replace(",", "")
        self.edit.setText(text)
        # Remember exactly what we drew. The displayed number is a ROUNDED
        # view of a more precise SI value, so reading it back would quietly
        # degrade the value on every unit change: 76 mm rendered as 2.99 in
        # parses back to 75.946 mm. _read_edit uses this to tell "the user
        # typed something" from "this is still our own rendering".
        self._rendered_text = text
        self._updating = False

    def _read_edit(self):
        if self._updating:
            return
        text = self.edit.text().replace(",", "").strip()
        if text == getattr(self, "_rendered_text", None):
            return          # untouched rendering: keep the full-precision SI
        try:
            typed = float(text)
        except ValueError:
            return          # leave the last good value; _render will restore
        si = self.quantity.to_si(typed, self.unit())
        if si != self._si:
            self._si = si
            self._rendered_text = text
            self.valueChanged.emit()

    def _unit_changed(self):
        """Re-render the SAME physical value in the newly chosen unit.

        The old ad-hoc converter reinterpreted the typed number instead,
        which is what turned a 76 mm tube into 1.93 mm when a profile load
        happened to move a combo.
        """
        if self._updating:
            return
        self._render()

    # -- unit system -------------------------------------------------------
    def set_system(self, system: str):
        """Switch to this system's preferred unit, keeping the value."""
        self._system = system
        if self.combo is None:
            return
        preferred = self.quantity.preferred(system)
        if preferred and preferred != self.combo.currentText():
            self._updating = True
            self.combo.setCurrentText(preferred)
            self._updating = False
        self._render()

    def label(self) -> str:
        """Field label. No unit in the text - the selector carries that."""
        return self.quantity.label + ":"


# ---------------------------------------------------------------------------
# Binding form fields to the attributes behind them.
#
# The dataclass attribute a field edits is NOT always in SI: surface roughness
# is stored in microns, launch-site temperature in Celsius, and the report's
# target altitude in feet. The binder converts between the field's SI value
# and whatever unit the attribute is actually kept in, so neither side has to
# know about the other.
#
# attribute name -> (catalogue key, unit the attribute is stored in)
# An entry of None means dimensionless: a plain line edit, no selector.
# ---------------------------------------------------------------------------
BINDINGS: dict[str, tuple[str, str] | None] = {
    # --- airframe (vehicle_tab + report_tab) -------------------------------
    "nose_length_m": ("nose_length", "m"),
    "body_diameter_m": ("body_diameter", "m"),
    "body_od_m": ("body_diameter", "m"),
    "body_length_m": ("body_length", "m"),
    "body_wall_m": ("body_wall", "m"),
    "surface_roughness_um": ("surface_roughness", "um"),
    "boattail_length_m": ("boattail_length", "m"),
    "boattail_exit_diameter_m": ("boattail_exit", "m"),
    "cd_override": None,
    # --- fins --------------------------------------------------------------
    "fin_count": None,
    "fin_root_chord_m": ("fin_root_chord", "m"),
    "fin_tip_chord_m": ("fin_tip_chord", "m"),
    "fin_span_m": ("fin_span", "m"),
    "fin_sweep_m": ("fin_sweep", "m"),
    "fin_thickness_m": ("fin_thickness", "m"),
    # --- mass and balance --------------------------------------------------
    "dry_mass_kg": ("dry_mass", "kg"),
    "propellant_mass_kg": ("propellant_mass", "kg"),
    "dry_cg_m": ("dry_cg", "m"),
    "propellant_cg_m": ("propellant_cg", "m"),
    # --- launch site -------------------------------------------------------
    "elevation_m": ("elevation", "m"),
    "latitude_deg": ("latitude", "deg"),
    "temperature_c": ("temperature", "C"),
    "pressure_pa": ("station_pressure", "Pa"),
    "humidity_pct": None,
    "wind_speed_ms": ("wind_speed", "m/s"),
    "wind_ref_height_m": ("wind_ref_height", "m"),
    "wind_shear_exp": None,
    "rail_length_m": ("rail_length", "m"),
    "rail_angle_deg": ("rail_angle", "deg"),
    # --- motor hardware / mission (report_tab) -----------------------------
    "chamber_wall_m": ("chamber_wall", "m"),
    "tank_wall_m": ("tank_wall", "m"),
    "target_altitude_ft": ("target_altitude", "ft"),
    "harness_rating_n": ("harness_rating", "N"),
    "min_pressure_sf": None,
    "min_structure_sf": None,
}


class FieldBinder:
    """Builds and reads unit-aware fields for one tab's attributes."""

    def __init__(self, system: str = U.METRIC, on_change=None):
        self.system = system
        self._on_change = on_change
        self.widgets: dict[str, QtWidgets.QWidget] = {}

    def make(self, attr: str, tip: str = "") -> QtWidgets.QWidget:
        binding = BINDINGS.get(attr, None)
        if binding:
            widget = UnitField(FIELDS[binding[0]], self.system)
            if self._on_change:
                widget.edit.editingFinished.connect(self._on_change)
        else:
            widget = QtWidgets.QLineEdit()
            if self._on_change:
                widget.editingFinished.connect(self._on_change)
        if tip:
            widget.setToolTip(tip)
        self.widgets[attr] = widget
        return widget

    @staticmethod
    def label_for(attr: str, label: str) -> str:
        """Drop the unit from a label that now carries a selector."""
        if BINDINGS.get(attr) and "(" in label:
            label = label[:label.index("(")].strip()
        return label

    def get(self, attr: str, default: float = 0.0) -> float:
        """Value in the unit the underlying attribute is stored in."""
        widget = self.widgets.get(attr)
        if widget is None:
            return default
        binding = BINDINGS.get(attr)
        if binding and isinstance(widget, UnitField):
            quantity, storage = FIELDS[binding[0]], binding[1]
            return quantity.from_si(widget.value_si(), storage)
        text = widget.text().strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            return default

    def set(self, attr: str, stored: float, decimals: int = 3):
        """Write a value that is in the attribute's own storage unit."""
        widget = self.widgets.get(attr)
        if widget is None:
            return
        binding = BINDINGS.get(attr)
        if binding and isinstance(widget, UnitField):
            quantity, storage = FIELDS[binding[0]], binding[1]
            widget.set_value_si(quantity.to_si(float(stored), storage))
        else:
            widget.setText(f"{stored:.{decimals}f}")

    def set_system(self, system: str):
        self.system = system
        for widget in self.widgets.values():
            if isinstance(widget, UnitField):
                widget.set_system(system)
