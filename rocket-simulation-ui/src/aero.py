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

    def wetted_area(self) -> float:
        """Total wetted area: nose + body tube + boat tail + both fin faces."""
        d, r = self.body_diameter_m, self.radius
        # Nose wetted area, approximated as a cone-equivalent lateral surface
        # scaled by shape (ogive/Von Karman are slightly fuller than a cone).
        slant = math.hypot(self.nose_length_m, r)
        fullness = 1.0 if self.nose_shape == "Conical" else 1.06
        nose = math.pi * r * slant * fullness
        body = math.pi * d * self.body_length_m
        boat = 0.0
        if self.boattail_length_m > 0 and self.boattail_exit_diameter_m > 0:
            r2 = self.boattail_exit_diameter_m / 2.0
            boat = math.pi * (r + r2) * math.hypot(self.boattail_length_m, r - r2)
        fins = 2.0 * self.fin_count * self.fin_planform_area
        return nose + body + boat + fins

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

    # Friction on both faces, with a thickness form factor
    wetted = 2.0 * airframe.fin_count * airframe.fin_planform_area
    cd = cf * (1.0 + 2.0 * tc) * wetted / a_ref

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
    cd_friction = cf * form * airframe.wetted_area() / a_ref

    cd_base = base_drag_coefficient(mach, thrusting) * airframe.base_area() / a_ref
    cd_wave = wave_drag_coefficient(mach, airframe)
    cd_fins = fin_drag_coefficient(mach, airframe, cf)
    cd_interference = 0.05 * cd_fins       # fin-body junction

    total = cd_friction + cd_base + cd_wave + cd_fins + cd_interference
    return total, {
        "friction": cd_friction, "base": cd_base, "wave": cd_wave,
        "fins": cd_fins, "interference": cd_interference, "reynolds": reynolds,
    }
