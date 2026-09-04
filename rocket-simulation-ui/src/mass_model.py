"""Where the mass actually sits, and what that does to stability.

A rocket was previously described by two numbers - a dry mass and a dry CG -
which you had to work out yourself and type in. That hides the thing you
usually want to change: *where* the weight is. Moving a pound of ballast from
the tail to the nose is the standard fix for a marginally stable rocket, and
with a single typed-in CG there was no way to try it.

So mass is now a list of components at stations along the airframe. From that
list three things fall out, none of which have to be typed:

    total dry mass      the sum
    CG                  the mass-weighted mean station, which MOVES as
                        propellant burns off
    pitch inertia       sum of m*r^2 about the CG, plus each component's own
                        inertia about itself

That last one matters more than it looks. The flight model needs pitch inertia
to work out how fast the vehicle can weathercock into a crosswind, and it was
previously estimated as mass*(L/3.5)^2 - a uniform rod. Real rockets are not
uniform rods: a heavy motor at the tail and a nose weight at the tip have far
more inertia than a rod of the same mass and length, and they turn more slowly
into the wind. With real components that estimate becomes a real calculation.

Positions are measured from the nose tip, in metres, like everything else.

Qt-free and importable on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# Component kinds, purely for labelling and sensible defaults in the UI.
COMPONENT_KINDS = {
    "Nose weight":    "Ballast in the nose. The strongest lever on stability - "
                      "it pulls CG forward with the longest arm.",
    "Nose cone":      "The cone itself.",
    "Avionics bay":   "Flight computer, batteries, sled.",
    "Recovery":       "Parachutes, harness and deployment hardware.",
    "Payload":        "Whatever the flight is actually for.",
    "Body tube":      "Airframe structure. Give it a length so it is treated "
                      "as distributed rather than as a point mass.",
    "Fins":           "Fin can and hardware, near the tail.",
    "Motor hardware": "Case, closures, injector, nozzle - the dry motor.",
    "Ballast":        "Trim weight, anywhere.",
    "Other":          "Anything else.",
}


@dataclass
class MassComponent:
    """One lump of mass at a station on the airframe."""
    name: str = "Component"
    mass_kg: float = 0.0
    position_m: float = 0.0      # CG of this component, from the nose tip
    length_m: float = 0.0        # 0 = point mass; >0 = uniform over its length
    kind: str = "Other"
    enabled: bool = True

    def own_inertia(self) -> float:
        """Pitch inertia about the component's own CG [kg m^2].

        A point mass has none. Anything with a length is treated as a uniform
        rod, mL^2/12 - which is what a body tube, a motor case or a payload
        bay actually is, and ignoring it under-reports total inertia.
        """
        if self.length_m <= 0:
            return 0.0
        return self.mass_kg * self.length_m ** 2 / 12.0

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        c = MassComponent()
        for k, v in (d or {}).items():
            if hasattr(c, k):
                setattr(c, k, v)
        return c


@dataclass
class MassBuildup:
    """The component list, and the mass properties it produces."""
    components: list = field(default_factory=list)

    def active(self):
        return [c for c in self.components if c.enabled and c.mass_kg > 0]

    def total_mass(self) -> float:
        return sum(c.mass_kg for c in self.active())

    def cg(self) -> float:
        """Mass-weighted station of the components [m from nose]."""
        items = self.active()
        total = sum(c.mass_kg for c in items)
        if total <= 0:
            return 0.0
        return sum(c.mass_kg * c.position_m for c in items) / total

    def inertia_about(self, pivot_m: float) -> float:
        """Pitch inertia about a station [kg m^2], parallel-axis included."""
        return sum(c.own_inertia() + c.mass_kg * (c.position_m - pivot_m) ** 2
                   for c in self.active())

    def inertia(self) -> float:
        """Pitch inertia about the buildup's own CG [kg m^2]."""
        return self.inertia_about(self.cg())

    # --- combining with propellant -----------------------------------------
    def with_propellant(self, prop_mass_kg: float, prop_position_m: float,
                        prop_length_m: float = 0.0):
        """(total mass, CG, pitch inertia about that CG) including propellant.

        Called every step of a flight with the propellant remaining, which is
        how CG migration and the changing inertia get into the trajectory
        rather than being fixed at ignition.
        """
        dry = self.total_mass()
        prop = max(0.0, prop_mass_kg)
        total = dry + prop
        if total <= 0:
            return 0.0, self.cg(), 0.0
        cg = (dry * self.cg() + prop * prop_position_m) / total
        inertia = self.inertia_about(cg)
        inertia += prop * (prop_position_m - cg) ** 2
        if prop_length_m > 0:
            inertia += prop * prop_length_m ** 2 / 12.0
        return total, cg, inertia

    # --- serialisation ------------------------------------------------------
    def to_list(self):
        return [c.to_dict() for c in self.components]

    @staticmethod
    def from_list(rows):
        return MassBuildup([MassComponent.from_dict(r) for r in (rows or [])])

    def describe(self) -> str:
        items = self.active()
        if not items:
            return "No mass components defined."
        cg = self.cg()
        return (f"{len(items)} components, {self.total_mass():.3f} kg, "
                f"CG {cg:.3f} m from nose, "
                f"pitch inertia {self.inertia():.3f} kg·m²")


def uniform_rod_inertia(mass_kg: float, length_m: float) -> float:
    """The old estimate, kept so the two can be compared.

    mass*(L/3.5)^2 is a uniform rod about its centre (radius of gyration
    L/sqrt(12) = L/3.464). It is what the flight model used before component
    masses existed, and it is still the fallback when no components are given.
    """
    return mass_kg * (length_m / 3.5) ** 2
