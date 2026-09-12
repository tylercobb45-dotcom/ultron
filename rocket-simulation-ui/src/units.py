"""Dimension-aware units: one conversion table, SI everywhere internally.

The app used to carry conversion factors inline wherever a unit combo
happened to be, and bake units into label text everywhere else ("Body
diameter (mm)"). That made dimensional analysis a matter of trusting each
call site, and it meant most of the app could not change units at all.

The rules this module enforces:

  * Every stored value is SI. Conversion happens only at the edge, when a
    number is shown to or read from a person. Nothing downstream ever sees a
    pound or an inch, so the physics cannot pick up a unit bug.
  * A quantity declares its DIMENSION, not its unit. The unit is a display
    choice, and each field names the one it prefers in metric and in
    imperial - so a body tube offers millimetres and inches, and a launch
    rail offers metres and feet, rather than both offering the same list and
    inviting "8 mm" or "0.0762 m" readings of the same tube.
  * Dimensionless quantities - drag coefficient, Mach, O/F, expansion ratio,
    efficiencies, stability in calibers - have NO unit and no selector.
    Giving them one implies a conversion that does not exist.

Conversion factors are exact where the definition is exact (the inch, the
pound, the foot are all defined exactly in SI).
"""
from __future__ import annotations

from dataclasses import dataclass

METRIC = "metric"
IMPERIAL = "imperial"


@dataclass(frozen=True)
class Unit:
    """One unit of some dimension.

    ``si = value * factor + offset`` converts a displayed number to SI. The
    offset exists for temperature, which is the only dimension here whose
    scales do not share a zero.
    """
    symbol: str
    factor: float
    offset: float = 0.0

    def to_si(self, value: float) -> float:
        return value * self.factor + self.offset

    def from_si(self, si: float) -> float:
        return (si - self.offset) / self.factor


@dataclass(frozen=True)
class Dimension:
    """A physical dimension and the units it may be displayed in."""
    name: str
    units: tuple[Unit, ...]

    @property
    def si_symbol(self) -> str:
        return self.units[0].symbol

    def unit(self, symbol: str) -> Unit:
        for u in self.units:
            if u.symbol == symbol:
                return u
        raise KeyError(f"{self.name} has no unit {symbol!r}; "
                       f"have {[u.symbol for u in self.units]}")

    def symbols(self) -> list[str]:
        return [u.symbol for u in self.units]


def _d(name, *units) -> Dimension:
    return Dimension(name, tuple(units))


# The SI unit is always first in each list, so Dimension.si_symbol works and
# a factor of 1.0 leads every table.
#
# Exact by definition: in = 0.0254 m, ft = 0.3048 m, lb = 0.45359237 kg,
# mph = 0.44704 m/s. The rest are the CODATA/NIST values.
DIMENSIONS: dict[str, Dimension] = {
    "length": _d(
        "length",
        Unit("m", 1.0), Unit("cm", 0.01), Unit("mm", 0.001),
        Unit("um", 1e-6), Unit("km", 1000.0),
        Unit("in", 0.0254), Unit("ft", 0.3048), Unit("yd", 0.9144),
        Unit("mi", 1609.344)),
    "mass": _d(
        "mass",
        Unit("kg", 1.0), Unit("g", 0.001),
        Unit("lb", 0.45359237), Unit("oz", 0.028349523125)),
    "time": _d(
        "time",
        Unit("s", 1.0), Unit("ms", 0.001), Unit("min", 60.0)),
    "temperature": _d(
        "temperature",
        Unit("K", 1.0),
        Unit("C", 1.0, 273.15),
        # Fahrenheit: K = (F + 459.67) * 5/9, i.e. factor 5/9 and an offset
        # of 459.67 * 5/9 once the factor is applied.
        Unit("F", 5.0 / 9.0, 459.67 * 5.0 / 9.0)),
    "angle": _d(
        "angle",
        Unit("deg", 1.0), Unit("rad", 57.29577951308232)),
    "area": _d(
        "area",
        Unit("m2", 1.0), Unit("cm2", 1e-4), Unit("mm2", 1e-6),
        Unit("in2", 0.00064516), Unit("ft2", 0.09290304)),
    "volume": _d(
        "volume",
        Unit("m3", 1.0), Unit("L", 0.001), Unit("cc", 1e-6),
        Unit("in3", 1.6387064e-5), Unit("ft3", 0.028316846592)),
    "velocity": _d(
        "velocity",
        Unit("m/s", 1.0), Unit("km/h", 1.0 / 3.6),
        Unit("ft/s", 0.3048), Unit("mph", 0.44704)),
    "acceleration": _d(
        "acceleration",
        Unit("m/s2", 1.0), Unit("ft/s2", 0.3048), Unit("g", 9.80665)),
    "force": _d(
        "force",
        Unit("N", 1.0), Unit("kN", 1000.0), Unit("lbf", 4.4482216152605)),
    "pressure": _d(
        "pressure",
        Unit("Pa", 1.0), Unit("kPa", 1000.0), Unit("MPa", 1e6),
        Unit("bar", 1e5), Unit("psi", 6894.757293168361),
        Unit("atm", 101325.0)),
    "density": _d(
        "density",
        Unit("kg/m3", 1.0), Unit("g/cm3", 1000.0),
        Unit("lb/ft3", 16.018463373960143),
        Unit("lb/in3", 27679.904710203122)),
    "mass_flow": _d(
        "mass_flow",
        Unit("kg/s", 1.0), Unit("g/s", 0.001), Unit("lb/s", 0.45359237)),
    "impulse": _d(
        "impulse",
        Unit("N.s", 1.0), Unit("kN.s", 1000.0),
        Unit("lbf.s", 4.4482216152605)),
    "energy": _d(
        "energy",
        Unit("J", 1.0), Unit("kJ", 1000.0), Unit("MJ", 1e6),
        Unit("ft.lbf", 1.3558179483314004), Unit("BTU", 1055.05585262)),
    "power": _d(
        "power",
        Unit("W", 1.0), Unit("kW", 1000.0), Unit("hp", 745.6998715822702)),
    "speed_rate": _d(
        # Regression and erosion rates: a velocity dimension, but nobody
        # writes a fuel regression rate in m/s.
        "speed_rate",
        Unit("m/s", 1.0), Unit("mm/s", 0.001), Unit("in/s", 0.0254)),
    "inertia": _d(
        "inertia",
        Unit("kg.m2", 1.0), Unit("lb.ft2", 0.042140110093804)),
    "mass_flux": _d(
        "mass_flux",
        Unit("kg/m2s", 1.0), Unit("lb/in2s", 703.0695796464)),
}


def convert(value: float, from_symbol: str, to_symbol: str,
            dimension: str) -> float:
    """Convert between two units of the same dimension."""
    dim = DIMENSIONS[dimension]
    return dim.unit(to_symbol).from_si(dim.unit(from_symbol).to_si(value))


@dataclass(frozen=True)
class Quantity:
    """What one input field measures, and how to show it.

    ``dimension`` of None means dimensionless - a ratio, a coefficient, a
    Mach number. Those get no unit and no selector, which is the whole point
    of carrying the distinction.
    """
    key: str
    label: str
    dimension: str | None = None
    metric: str | None = None       # preferred unit in metric
    imperial: str | None = None     # preferred unit in imperial
    decimals: int = 3
    help: str = ""

    def __post_init__(self):
        if self.dimension is None:
            if self.metric or self.imperial:
                raise ValueError(
                    f"{self.key}: dimensionless quantities cannot have units")
            return
        dim = DIMENSIONS[self.dimension]
        for attr in ("metric", "imperial"):
            symbol = getattr(self, attr)
            if symbol is None:
                raise ValueError(f"{self.key}: {attr} unit not set")
            dim.unit(symbol)        # raises if the symbol is not valid here

    @property
    def dimensionless(self) -> bool:
        return self.dimension is None

    def preferred(self, system: str) -> str | None:
        """The unit this field shows under the given unit system."""
        if self.dimensionless:
            return None
        return self.imperial if system == IMPERIAL else self.metric

    def choices(self) -> list[str]:
        """Units offered for this field.

        Deliberately the full list for the dimension: someone may genuinely
        want a tube in inches while working in metric. The preferred unit is
        what the field SELECTS, which is what keeps readings sensible.
        """
        if self.dimensionless:
            return []
        return DIMENSIONS[self.dimension].symbols()

    def to_si(self, value: float, symbol: str | None) -> float:
        if self.dimensionless or symbol is None:
            return value
        return DIMENSIONS[self.dimension].unit(symbol).to_si(value)

    def from_si(self, si: float, symbol: str | None) -> float:
        if self.dimensionless or symbol is None:
            return si
        return DIMENSIONS[self.dimension].unit(symbol).from_si(si)


def format_si(si: float, quantity: Quantity, system: str) -> str:
    """Render an SI value in the unit this field prefers, with its symbol."""
    symbol = quantity.preferred(system)
    value = quantity.from_si(si, symbol)
    text = f"{value:,.{quantity.decimals}f}"
    return text if symbol is None else f"{text} {symbol}"
