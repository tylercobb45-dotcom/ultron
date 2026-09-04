"""Airframe geometry and an aerodynamic model valid from subsonic to Mach 5.

What this replaces: the simulator used a single user-entered drag coefficient
with a +15% bump between Mach 0.8 and 1.2 and nothing above it, so every
supersonic flight was flown with subsonic drag. For a 50,000 ft attempt -
which is supersonic for most of the boost - that is the single largest error
in the whole model.

What it does instead is a component buildup, recomputed at every timestep from
the actual Reynolds and Mach number:

    Cd = Cd_friction + Cd_base + Cd_wave(nose) + Cd_fins + Cd_interference

with the nose cone shape, fineness ratio, body length, and fin planform all
feeding in. Base drag is reduced while the motor is burning, because the
exhaust plume fills the base region.

It also provides Barrowman centre of pressure, so static stability margin can
be tracked against the moving centre of gravity as propellant burns off.

Accuracy note: skin friction and Barrowman CP are standard, well-established
methods. The transonic and supersonic wave-drag terms are engineering
correlations of the kind amateur tools use - shape- and fineness-dependent,
anchored to published Cd curves. They are far better than a constant Cd, and
they are not CFD. Treat supersonic Cd as +/-20%, not gospel.

Qt-free and importable on its own.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# --- nose cone shapes -----------------------------------------------------
# name -> (CP position as a fraction of nose length [Barrowman],
#          wave-drag shape factor, human description)
# The CP fractions are the standard Barrowman values; the wave-drag factors
# scale the transonic/supersonic pressure drag, with a slender Von Karman
# taken as the 1.0 reference and blunter shapes penalised.
NOSE_SHAPES = {
    "Von Karman (LV-Haack)": (0.500, 1.00,
                              "Minimum wave drag for a given length and base. "
                              "The right default for a supersonic shot."),
    "Tangent Ogive":         (0.466, 1.10,
                              "The classic hobby shape. Slightly more wave drag "
                              "than Von Karman, much easier to make."),
    "Secant Ogive":          (0.466, 1.12,
                              "Similar to tangent ogive; sharper tip options."),
    "Elliptical":            (0.500, 1.35,
                              "Good subsonic, poor supersonic - blunt shoulder "
                              "drives a strong bow shock."),
    "Parabolic":             (0.500, 1.20,
                              "Between ogive and elliptical."),
    "Power series (n=0.75)": (0.500, 1.15,
                              "Blunted tip, decent all-round supersonic shape."),
    "Conical":               (0.667, 1.30,
                              "Simplest to build, highest wave drag of the "
                              "practical shapes."),
    "Hemispherical":         (0.500, 2.60,
                              "Very high drag. Subsonic/recovery use only."),
}

FIN_PROFILES = {
    "Square edged":    1.00,
    "Rounded leading": 0.72,
    "Airfoil":         0.45,
    "Double wedge":    0.55,
}


@dataclass
class Airframe:
    """Everything the aerodynamic model needs about the vehicle's shape."""
    # Nose
    nose_shape: str = "Tangent Ogive"
    nose_length_m: float = 0.60
    # Body
    body_diameter_m: float = 0.140
    body_length_m: float = 2.50          # excluding the nose
    surface_roughness_um: float = 20.0   # 20 um ~ smooth painted glass
    # Boat tail (optional): exit diameter equal to body means none
    boattail_length_m: float = 0.0
    boattail_exit_diameter_m: float = 0.0
    # Fins
    fin_count: int = 4
    fin_root_chord_m: float = 0.30
    fin_tip_chord_m: float = 0.15
    fin_span_m: float = 0.15             # semi-span, one fin, exposed
    fin_sweep_m: float = 0.10            # leading-edge sweep distance
    fin_thickness_m: float = 0.005
    fin_profile: str = "Rounded leading"
    fin_root_position_m: float = 0.0     # from nose tip; 0 = auto (aft end)

    # --- basic geometry ----------------------------------------------------
    @property
    def radius(self) -> float:
        return self.body_diameter_m / 2.0

    @property
    def reference_area(self) -> float:
        return math.pi * self.radius ** 2

    @property
    def total_length(self) -> float:
        return self.nose_length_m + self.body_length_m + self.boattail_length_m

    @property
    def nose_fineness(self) -> float:
        return self.nose_length_m / self.body_diameter_m if self.body_diameter_m else 0.0

    @property
    def body_fineness(self) -> float:
        return self.total_length / self.body_diameter_m if self.body_diameter_m else 0.0

    @property
    def fin_planform_area(self) -> float:
        """Exposed planform area of one fin."""
        return 0.5 * (self.fin_root_chord_m + self.fin_tip_chord_m) * self.fin_span_m

    @property
    def fin_mid_chord_sweep(self) -> float:
        """Sweep length measured at mid-chord, used by Barrowman."""
        dx = self.fin_sweep_m + 0.5 * (self.fin_tip_chord_m - self.fin_root_chord_m)
        return math.hypot(dx, self.fin_span_m)

    def body_wetted_area(self) -> float:
        """Wetted area of the body alone: nose + body tube + boat tail.

        Fins are deliberately excluded. Their friction is computed in
        fin_drag_coefficient, with its own thickness form factor, so counting
        them here as well charges for the same surface twice.
        """
        d, r = self.body_diameter_m, self.radius
        slant = math.hypot(self.nose_length_m, r)
        fullness = 1.0 if self.nose_shape == "Conical" else 1.06
        nose = math.pi * r * slant * fullness
        body = math.pi * d * self.body_length_m
        boat = 0.0
        if self.boattail_length_m > 0 and self.boattail_exit_diameter_m > 0:
            r2 = self.boattail_exit_diameter_m / 2.0
            boat = math.pi * (r + r2) * math.hypot(self.boattail_length_m, r - r2)
        return nose + body + boat

    def fin_wetted_area(self) -> float:
        """Wetted area of both faces of every fin."""
        return 2.0 * self.fin_count * self.fin_planform_area

    def wetted_area(self) -> float:
        """Total wetted area: nose + body tube + boat tail + both fin faces.

        Reporting only - the drag buildup uses the body and fin parts
        separately so neither gets counted twice.
        """
        return self.body_wetted_area() + self.fin_wetted_area()

    def base_area(self) -> float:
        """Area of the blunt aft end, after any boat tail."""
        if self.boattail_length_m > 0 and self.boattail_exit_diameter_m > 0:
            return math.pi * (self.boattail_exit_diameter_m / 2.0) ** 2
        return self.reference_area

    # --- Barrowman centre of pressure --------------------------------------
    def center_of_pressure(self, mach: float = 0.3) -> float:
        """Barrowman CP, measured from the nose tip [m].

        Standard subsonic Barrowman, with a mild aft shift applied through the
        transonic region, which is the usual first-order correction.
        """
        cp_frac, _wave, _desc = NOSE_SHAPES.get(
            self.nose_shape, NOSE_SHAPES["Tangent Ogive"])
        cn_nose = 2.0
        x_nose = cp_frac * self.nose_length_m

        cn_fins, x_fins = 0.0, 0.0
        if self.fin_count > 0 and self.fin_span_m > 0:
            r = self.radius
            s = self.fin_span_m
            cr, ct = self.fin_root_chord_m, self.fin_tip_chord_m
            lm = self.fin_mid_chord_sweep
            if (cr + ct) > 0:
                # Fin normal force slope, with body interference factor
                kfb = 1.0 + r / (s + r) if (s + r) > 0 else 1.0
                denom = 1.0 + math.sqrt(1.0 + (2.0 * lm / (cr + ct)) ** 2)
                cn_fins = kfb * (4.0 * self.fin_count * (s / self.body_diameter_m) ** 2) / denom
                x_root = (self.fin_root_position_m
                          if self.fin_root_position_m > 0
                          else self.total_length - cr)
                x_fins = (x_root
                          + (self.fin_sweep_m * (cr + 2 * ct)) / (3 * (cr + ct))
                          + (1.0 / 6.0) * ((cr + ct) - (cr * ct) / (cr + ct)))

        cn_total = cn_nose + cn_fins
        if cn_total <= 0:
            return 0.5 * self.total_length
        cp = (cn_nose * x_nose + cn_fins * x_fins) / cn_total
        # Transonic aft shift: CP migrates rearward as the flow goes supersonic.
        if mach > 0.8:
            shift = min(0.08, 0.08 * (min(mach, 1.5) - 0.8) / 0.7)
            cp += shift * self.total_length
        return cp

    def normal_force_slope(self) -> float:
        """Total CN_alpha [per radian], for weathercocking estimates."""
        cn = 2.0
        if self.fin_count > 0 and self.fin_span_m > 0:
            r, s = self.radius, self.fin_span_m
            cr, ct = self.fin_root_chord_m, self.fin_tip_chord_m
            if (cr + ct) > 0:
                kfb = 1.0 + r / (s + r) if (s + r) > 0 else 1.0
                denom = 1.0 + math.sqrt(1.0 + (2.0 * self.fin_mid_chord_sweep / (cr + ct)) ** 2)
                cn += kfb * (4.0 * self.fin_count * (s / self.body_diameter_m) ** 2) / denom
        return cn


# ---------------------------------------------------------------------------
# drag model
# ---------------------------------------------------------------------------

def skin_friction_coefficient(reynolds: float, mach: float,
                              roughness_m: float, length_m: float) -> float:
    """Flat-plate skin friction, compressibility corrected.

    Turbulent Prandtl-Schlichting above transition, laminar Blasius below,
    with a roughness floor so a rough airframe cannot beat a smooth one.
    """
    re = max(1.0, reynolds)
    if re < 5.0e5:
        cf = 1.328 / math.sqrt(re)
    else:
        cf = 0.455 / (math.log10(re) ** 2.58)
    # Roughness-limited friction: below a critical Reynolds the surface finish
    # governs and Cf stops falling with Re.
    if roughness_m > 0 and length_m > 0:
        cf_rough = 0.032 * (roughness_m / length_m) ** 0.2
        cf = max(cf, cf_rough)
    # Compressibility: friction drops as Mach rises
    if mach > 0.1:
        cf /= (1.0 + 0.15 * mach * mach) ** 0.58
    return cf


def base_drag_coefficient(mach: float, thrusting: bool) -> float:
    """Base (aft end) pressure drag, referenced to the base area.

    While the motor burns, the exhaust plume pressurises the base region and
    this term largely disappears - which is why a rocket's drag jumps at
    burnout even though nothing about its shape changed.
    """
    if mach < 1.0:
        cd = 0.12 + 0.13 * mach * mach
    else:
        cd = 0.25 / mach
    if thrusting:
        cd *= 0.15
    return cd


def wave_drag_coefficient(mach: float, airframe: Airframe) -> float:
    """Transonic and supersonic pressure (wave) drag of the nose and body.

    Zero below the drag-divergence Mach number, rising through the transonic
    peak just above Mach 1, then falling off supersonically. The magnitude
    scales with nose shape and inversely with nose fineness ratio - a longer,
    finer nose spreads the compression over more length and pays less.

    This is an engineering correlation, not CFD.
    """
    fineness = max(0.5, airframe.nose_fineness)
    _cp, shape_factor, _desc = NOSE_SHAPES.get(
        airframe.nose_shape, NOSE_SHAPES["Tangent Ogive"])

    # Drag divergence: finer noses hold off the transonic rise longer.
    m_div = min(0.95, 0.72 + 0.06 * min(fineness, 4.0))
    if mach <= m_div:
        return 0.0

    # Peak transonic wave drag, referenced to frontal area.
    peak = shape_factor * 0.90 / (fineness ** 1.20)
    peak = min(peak, 1.2)
    m_peak = 1.10

    if mach < m_peak:
        # Rise from divergence to the transonic peak
        frac = (mach - m_div) / (m_peak - m_div)
        return peak * frac ** 1.8
    # Supersonic decay. Slender-body wave drag falls roughly with the
    # Prandtl-Glauert factor; clamp so it stays physical out to Mach 5+.
    beta = math.sqrt(max(0.05, mach * mach - 1.0))
    decay = min(1.0, 1.10 / beta ** 0.85)
    return peak * decay


def fin_drag_coefficient(mach: float, airframe: Airframe, cf: float) -> float:
    """Fin drag: friction on the wetted faces plus leading/trailing edge
    pressure drag, referenced to the body cross-section."""
    if airframe.fin_count <= 0 or airframe.fin_planform_area <= 0:
        return 0.0
    a_ref = airframe.reference_area
    if a_ref <= 0:
        return 0.0
    profile = FIN_PROFILES.get(airframe.fin_profile, 1.0)
    mean_chord = max(1e-4, 0.5 * (airframe.fin_root_chord_m + airframe.fin_tip_chord_m))
    tc = airframe.fin_thickness_m / mean_chord

    # Friction on both faces, with a thickness form factor. This is the only
    # place fin friction is charged; the body term uses body_wetted_area().
    cd = cf * (1.0 + 2.0 * tc) * airframe.fin_wetted_area() / a_ref

    # Edge pressure drag, strongly profile- and Mach-dependent. Blended through
    # the transonic band: a hard switch at Mach 1 puts a step discontinuity in
    # the drag the integrator then has to chew through.
    frontal = airframe.fin_count * airframe.fin_thickness_m * airframe.fin_span_m
    edge_sub = 0.12 * profile
    beta = math.sqrt(max(0.05, mach * mach - 1.0))
    edge_sup = profile * min(1.0, 0.90 / beta ** 0.7)
    if mach <= 0.9:
        edge = edge_sub
    elif mach >= 1.2:
        edge = edge_sup
    else:
        w = (mach - 0.9) / 0.3
        edge = edge_sub * (1 - w) + edge_sup * w
    cd += edge * frontal / a_ref
    return cd


def drag_coefficient(mach: float, altitude_m: float, speed_ms: float,
                     airframe: Airframe, atmos, thrusting: bool = False):
    """Total drag coefficient, referenced to body cross-sectional area.

    atmos must expose properties(altitude) -> (T, P, rho, a, mu).
    Returns (Cd_total, breakdown dict).
    """
    a_ref = airframe.reference_area
    if a_ref <= 0 or speed_ms <= 0:
        return 0.0, {"friction": 0.0, "base": 0.0, "wave": 0.0,
                     "fins": 0.0, "interference": 0.0, "reynolds": 0.0}

    _T, _P, rho, _a, mu = atmos.properties(altitude_m)
    length = max(1e-3, airframe.total_length)
    reynolds = rho * speed_ms * length / max(mu, 1e-12)

    cf = skin_friction_coefficient(reynolds, mach,
                                   airframe.surface_roughness_um * 1e-6, length)

    # Body friction with a fineness form factor (slender bodies pay less)
    fr = max(1.0, airframe.body_fineness)
    form = 1.0 + 60.0 / (fr ** 3) + 0.0025 * fr
    cd_friction = cf * form * airframe.body_wetted_area() / a_ref

    cd_base = base_drag_coefficient(mach, thrusting) * airframe.base_area() / a_ref
    cd_wave = wave_drag_coefficient(mach, airframe)
    cd_fins = fin_drag_coefficient(mach, airframe, cf)
    cd_interference = 0.05 * cd_fins       # fin-body junction

    total = cd_friction + cd_base + cd_wave + cd_fins + cd_interference
    return total, {
        "friction": cd_friction, "base": cd_base, "wave": cd_wave,
        "fins": cd_fins, "interference": cd_interference, "reynolds": reynolds,
    }


# ---------------------------------------------------------------------------
# Cd(Mach) sweeps and tables
#
# RASAero's central output is not a single drag coefficient, it is a curve:
# Cd against Mach, power-on and power-off, broken into the components that
# make it up. That curve is what actually decides an altitude prediction -
# getting the trajectory integration right is worth about 1%, getting Cd wrong
# by 3x costs a factor of two in apogee.
#
# Two things live here: sweeping this model to produce that curve, and a
# lookup table so a curve from anywhere else (RASAero, CFD, wind tunnel) can
# be flown instead.
# ---------------------------------------------------------------------------

def drag_sweep(airframe: "Airframe", atmos, altitude_m: float = 0.0,
               mach_min: float = 0.05, mach_max: float = 5.0,
               mach_step: float = 0.05, cg_m: float | None = None):
    """Cd against Mach at one altitude, power-off and power-on.

    Returns a list of row dicts, one per Mach number, carrying the total in
    both power states and the component breakdown behind the power-off value,
    plus centre of pressure and - when a CG is given - static margin.

    Altitude matters because skin friction is Reynolds-dependent: the same
    vehicle at the same Mach number has measurably less friction drag at
    30,000 ft than on the pad, which is why the sweep is per-altitude rather
    than a single universal curve.
    """
    rows = []
    if mach_step <= 0:
        mach_step = 0.05
    _T, _P, _rho, a_sound, _mu = atmos.properties(altitude_m)
    d = airframe.body_diameter_m
    a_ref = airframe.reference_area

    n = int(round((mach_max - mach_min) / mach_step)) + 1
    for i in range(max(1, n)):
        mach = mach_min + i * mach_step
        if mach > mach_max + 1e-9:
            break
        speed = mach * a_sound
        cd_off, parts = drag_coefficient(mach, altitude_m, speed, airframe,
                                         atmos, thrusting=False)
        cd_on, _ = drag_coefficient(mach, altitude_m, speed, airframe,
                                    atmos, thrusting=True)
        cp = airframe.center_of_pressure(mach)
        row = {
            "mach": mach,
            "speed_ms": speed,
            "altitude_m": altitude_m,
            "cd_power_off": cd_off,
            "cd_power_on": cd_on,
            "cd_friction": parts["friction"],
            "cd_base": parts["base"],
            "cd_wave": parts["wave"],
            "cd_fins": parts["fins"],
            "cd_interference": parts["interference"],
            "reynolds": parts["reynolds"],
            "cda_power_off": cd_off * a_ref,
            "cda_power_on": cd_on * a_ref,
            "cp_m": cp,
        }
        if cg_m is not None and d > 0:
            row["cg_m"] = cg_m
            row["stability_cal"] = (cp - cg_m) / d
        rows.append(row)
    return rows


class CdMachTable:
    """A Cd(Mach) curve to fly instead of the buildup.

    This is the shape RASAero, CFD and wind-tunnel data all come in, and the
    thing docs/VALIDATION.md tells you to go and get for a serious altitude
    attempt. Linear interpolation between points; flat outside the range,
    because extrapolating a drag curve past its last point is how you get a
    confident wrong answer.
    """

    def __init__(self, points, name: str = "Cd(Mach) table"):
        pts = sorted((float(m), float(c)) for m, c in points if c is not None)
        self.points = [(m, c) for m, c in pts if c >= 0]
        self.name = name
        if not self.points:
            raise ValueError("A Cd(Mach) table needs at least one point.")

    @property
    def mach_range(self):
        return self.points[0][0], self.points[-1][0]

    def __call__(self, mach: float) -> float:
        pts = self.points
        if mach <= pts[0][0]:
            return pts[0][1]
        if mach >= pts[-1][0]:
            return pts[-1][1]
        lo, hi = 0, len(pts) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if pts[mid][0] <= mach:
                lo = mid
            else:
                hi = mid
        m0, c0 = pts[lo]
        m1, c1 = pts[hi]
        if m1 == m0:
            return c0
        return c0 + (c1 - c0) * (mach - m0) / (m1 - m0)

    def to_rows(self):
        return [{"mach": m, "cd": c} for m, c in self.points]

    @staticmethod
    def from_csv(path: str) -> "CdMachTable":
        """Read a two-column Mach,Cd file.

        Deliberately forgiving about the header, because every tool writes it
        differently: RASAero, RockSim and a hand-typed spreadsheet all land
        here. Any line whose first two fields parse as numbers is a data row.
        """
        import csv as _csv
        import os as _os
        points = []
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            delim = ";" if sample.count(";") > sample.count(",") else ","
            if sample.count("\t") > sample.count(delim):
                delim = "\t"
            for raw in _csv.reader(fh, delimiter=delim):
                if len(raw) < 2:
                    continue
                try:
                    mach = float(str(raw[0]).strip())
                    cd = float(str(raw[1]).strip())
                except (TypeError, ValueError):
                    continue          # header or blurb line
                if mach < 0 or cd < 0:
                    continue
                points.append((mach, cd))
        if not points:
            raise ValueError(
                "No Mach,Cd rows found. Expected two numeric columns, "
                "Mach first and Cd second.")
        return CdMachTable(points, name=_os.path.basename(path))

    def to_csv(self, path: str):
        import csv as _csv
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            w.writerow(["Mach", "Cd"])
            for m, c in self.points:
                w.writerow([f"{m:.4f}", f"{c:.5f}"])

    def summary(self) -> str:
        lo, hi = self.mach_range
        cds = [c for _m, c in self.points]
        return (f"{self.name}: {len(self.points)} points, Mach {lo:.2f}-{hi:.2f}, "
                f"Cd {min(cds):.3f}-{max(cds):.3f}")
