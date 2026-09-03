"""Material property library for structural / thermal failure analysis.

Values are representative engineering figures for the common amateur and
high-power rocketry material set. They are good enough to rank designs and
flag margins, but they are NOT a substitute for a datasheet from the
specific supplier and layup you actually fly.

Fields
------
density              kg/m^3
melt_k               melting / sublimation / decomposition temperature [K]
                     (for composites and plastics this is where the matrix
                     fails outright, not a true melting point)
max_service_k        continuous-use temperature limit [K] - above this the
                     material softens and loses most of its rated strength
yield_mpa            yield (or ultimate, for brittle/composite materials)
                     strength in the load direction that matters [MPa]
youngs_gpa           Young's modulus [GPa]
shear_gpa            shear modulus [GPa] - drives fin flutter
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    name: str
    density: float
    melt_k: float
    max_service_k: float
    yield_mpa: float
    youngs_gpa: float
    shear_gpa: float
    note: str = ""

    @property
    def yield_pa(self) -> float:
        return self.yield_mpa * 1e6

    @property
    def youngs_pa(self) -> float:
        return self.youngs_gpa * 1e9

    @property
    def shear_pa(self) -> float:
        return self.shear_gpa * 1e9


# --- Airframe / fin / structural materials -------------------------------
STRUCTURAL = {
    "Aluminum 6061-T6": Material(
        "Aluminum 6061-T6", 2700, 925, 475, 276, 68.9, 26.0,
        "Common machined airframe/coupler alloy. Anneals badly above ~475 K."),
    "Aluminum 7075-T6": Material(
        "Aluminum 7075-T6", 2810, 908, 420, 503, 71.7, 26.9,
        "Stronger than 6061 but loses temper at a lower temperature."),
    "Carbon Fiber / Epoxy": Material(
        "Carbon Fiber / Epoxy", 1600, 600, 400, 600, 70.0, 5.0,
        "Strength is layup-dependent; the epoxy Tg (~400 K) is the real limit."),
    "Fiberglass G10/FR4": Material(
        "Fiberglass G10/FR4", 1850, 590, 410, 280, 18.0, 7.0,
        "Tough, forgiving, and the usual choice for fins on fast flights."),
    "Phenolic (kraft)": Material(
        "Phenolic (kraft)", 1250, 570, 400, 40, 4.0, 1.5,
        "Cheap tubing. Weak and flutter-prone unless reinforced."),
    "Blue Tube": Material(
        "Blue Tube", 1250, 520, 380, 55, 3.5, 1.3,
        "Vulcanized fiber. Tougher than phenolic, still low modulus."),
    "Stainless Steel 304": Material(
        "Stainless Steel 304", 8000, 1700, 1100, 215, 193.0, 77.0,
        "Heavy; used for pressure parts rather than airframes."),
    "Titanium Ti-6Al-4V": Material(
        "Titanium Ti-6Al-4V", 4430, 1900, 600, 880, 114.0, 44.0,
        "Excellent strength-to-weight, expensive and hard to machine."),
    "PVC": Material(
        "PVC", 1400, 460, 340, 52, 3.0, 1.1,
        "Softens in sunlight on the pad. Not advisable for fast flights."),
    "3D-printed PLA": Material(
        "3D-printed PLA", 1240, 450, 330, 50, 3.5, 1.3,
        "Layer adhesion governs; softens near 330 K. Prototype use only."),
    "3D-printed ABS": Material(
        "3D-printed ABS", 1040, 490, 360, 40, 2.3, 0.8,
        "Higher service temp than PLA, lower stiffness."),
}

# --- Nozzle / throat / chamber-liner materials ----------------------------
HOT_SECTION = {
    "Graphite": Material(
        "Graphite", 1800, 3900, 2800, 70, 10.0, 4.0,
        "Sublimes rather than melts; standard hybrid throat material."),
    "Phenolic Ablative": Material(
        "Phenolic Ablative", 1400, 800, 700, 60, 8.0, 3.0,
        "Designed to char and erode. Sized by burn duration, not temperature."),
    "Copper C101": Material(
        "Copper C101", 8960, 1358, 800, 70, 117.0, 44.0,
        "Great conductor, but melts well below hybrid flame temperature."),
    "Stainless Steel 304": Material(
        "Stainless Steel 304", 8000, 1700, 1100, 215, 193.0, 77.0,
        "Will erode fast at a bare throat without ablative protection."),
    "Tungsten": Material(
        "Tungsten", 19300, 3695, 2800, 550, 411.0, 161.0,
        "Near-graphite temperature capability with real strength; very heavy."),
    "Zirconia Ceramic": Material(
        "Zirconia Ceramic", 5680, 2988, 2400, 200, 200.0, 80.0,
        "Thermal-shock sensitive; usually a coating rather than a whole part."),
}

# --- Pressure vessel materials (tank / combustion chamber) ----------------
PRESSURE = {
    "Aluminum 6061-T6": STRUCTURAL["Aluminum 6061-T6"],
    "Aluminum 7075-T6": STRUCTURAL["Aluminum 7075-T6"],
    "Stainless Steel 304": STRUCTURAL["Stainless Steel 304"],
    "Titanium Ti-6Al-4V": STRUCTURAL["Titanium Ti-6Al-4V"],
    "Carbon Fiber / Epoxy": STRUCTURAL["Carbon Fiber / Epoxy"],
}

ALL = {}
for _group in (STRUCTURAL, HOT_SECTION, PRESSURE):
    ALL.update(_group)


def get(name: str, default: str = "Fiberglass G10/FR4") -> Material:
    """Look a material up by name, falling back to a sane default."""
    return ALL.get(name) or ALL[default]
