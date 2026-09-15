"""
===============================================================================
 FLIGHT EQUATIONS - every calculation that decides what the airframe does
===============================================================================

The companion to engine_equations.py. That file answers "what does the motor
do"; this one answers "what does the rocket do with it". Together they are the
whole of the flight physics, and both exist so you can edit an equation in one
place and have the entire program follow.

WHAT IS IN HERE
    Only equations. Pure functions: numbers in, numbers out. No classes holding
    state, no files, no user interface. Anything here can be called on its own
    from a Python prompt with nothing set up first.

        - the atmosphere the rocket flies through
        - drag, in the pieces it is actually made of
        - stability: where the pressure acts, where the mass is
        - recovery: canopies, inflation, descent
        - the equations of motion themselves

WHAT IS NOT IN HERE
    Anything structural. Whether the fins flutter, whether the airframe buckles
    under thrust, whether the tube survives its own hoop stress, what a material
    is rated for - all of that lives with the materials and the failure checks.
    This file is about forces and motion, not strength.

    The integrator is also elsewhere: flight_model marches these through time.

UNITS
    SI throughout: metres, kilograms, seconds, kelvin, pascals, newtons,
    radians inside the trigonometry and degrees only where a function says so.
    Every argument and return states its unit. Keep edits in SI - the rest of
    the program converts for display and trusts what it gets from here.

REFERENCE AREA
    Every drag coefficient in this file is referenced to the BODY
    CROSS-SECTIONAL AREA, pi*(d/2)^2, including the fin and base terms. That is
    the usual convention and it is what makes the pieces add up into one Cd.
    If you change it, change it everywhere at once or the drag buildup stops
    being a sum.

HOW A FLIGHT GOES, IN ORDER
        1. atmosphere - altitude gives density, pressure, speed of sound  (F1)
        2. drag       - speed and air give the force resisting it          (F2)
        3. stability  - where CP sits against CG decides if it flies       (F3)
        4. mass       - propellant burns off, CG and inertia move          (F4)
        5. recovery   - canopies, inflation, descent rate                  (F5)
        6. motion     - the forces are summed and integrated               (F6)
===============================================================================
"""
from __future__ import annotations

import math

# =============================================================================
#  CONSTANTS
# =============================================================================

#: Standard gravity [m/s^2] - the defined constant, used for g-loads and Isp.
#: Local gravity is computed from latitude and altitude; see local_gravity.
G0 = 9.80665

#: Specific gas constant for dry air [J/(kg*K)] and its ratio of specific heats.
#: NOTE the exact value. atmosphere.py has always used 287.05287, and every
#: validated apogee in docs/VALIDATION.md was produced with it. The fuller
#: CODATA figure (287.052874) shifts air density by about 1.4e-8 relative,
#: which is harmless physically but would move the recorded numbers. If you
#: want the more precise constant, change it HERE and re-record the baselines
#: - that is exactly the kind of edit this file exists to make possible.
R_AIR = 287.05287

#: Feet per metre. Exact by definition: 1 ft = 0.3048 m. Stated once here and
#: imported, because the app had two spellings of it (3.28084 and
#: 3.280839895) in different files, so two panels could print the same apogee
#: as two slightly different numbers.
FT_PER_M = 1.0 / 0.3048

GAMMA_AIR = 1.4

#: Mean Earth radius [m], for geopotential altitude and the gravity model.
R_EARTH = 6_356_766.0

#: Sea-level standard conditions.
P_SEA_LEVEL = 101325.0
T_SEA_LEVEL = 288.15

#: Sutherland's law constants for air viscosity.
_MU_REF = 1.716e-5
_T_MU_REF = 273.15
_S_SUTHERLAND = 110.4

#: Boundary-layer transition Reynolds number. Below this the flow over the
#: airframe is laminar, above it turbulent, and the friction law changes.
RE_TRANSITION = 5.0e5

#: Recovery factor for a turbulent boundary layer [-]. Sets how much of the
#: stagnation temperature rise the skin actually feels.
RECOVERY_FACTOR = 0.9


# =============================================================================
#  F1. THE ATMOSPHERE - what the rocket is flying through
#
#  The U.S. Standard Atmosphere 1976, built as a stack of layers each with its
#  own temperature lapse rate. Valid to 86 km, which is far past anything a
#  Goddard-class vehicle will see.
# =============================================================================

def geopotential_altitude(geometric_altitude_m: float) -> float:
    """Geometric altitude -> geopotential altitude [m].

        h = R_earth * z / (R_earth + z)

    Gravity weakens with height, so a metre up high is "worth" slightly less
    than a metre at the ground as far as the pressure equation is concerned.
    Geopotential altitude absorbs that, which lets the barometric formulae be
    written as though gravity were constant.
    """
    return R_EARTH * geometric_altitude_m / (R_EARTH + geometric_altitude_m)


def air_viscosity(temperature_k: float) -> float:
    """Dynamic viscosity of air [Pa*s] - Sutherland's law.

        mu = mu_ref * (T/T_ref)^1.5 * (T_ref + S) / (T + S)

    Only needed for the Reynolds number, which decides whether the boundary
    layer is laminar or turbulent and therefore how much skin friction there is.
    """
    return (_MU_REF * (temperature_k / _T_MU_REF) ** 1.5
            * (_T_MU_REF + _S_SUTHERLAND) / (temperature_k + _S_SUTHERLAND))


def speed_of_sound(temperature_k: float) -> float:
    """Speed of sound in air [m/s]:  a = sqrt(gamma * R * T).

    Depends ONLY on temperature, not on pressure or density. This is why Mach
    number rises as a rocket climbs into colder air even at constant speed.
    """
    return math.sqrt(GAMMA_AIR * R_AIR * temperature_k)


def air_density(pressure_pa: float, temperature_k: float) -> float:
    """Air density [kg/m^3] from the ideal gas law:  rho = P / (R * T)."""
    return pressure_pa / (R_AIR * temperature_k)


def humid_air_density(pressure_pa: float, temperature_k: float,
                      relative_humidity_pct: float) -> float:
    """Air density [kg/m^3] corrected for water vapour.

    Humid air is LIGHTER than dry air at the same pressure, because a water
    molecule weighs less than the nitrogen or oxygen it displaces. The effect
    is small - a few tenths of a percent - but it is free to include, and it
    pushes apogee the opposite way to most people's intuition.

    Saturation vapour pressure uses the Tetens/Magnus form.
    """
    if relative_humidity_pct <= 0:
        return air_density(pressure_pa, temperature_k)
    t_c = temperature_k - 273.15
    p_sat = 610.78 * math.exp(17.27 * t_c / (t_c + 237.3))
    p_vapour = (relative_humidity_pct / 100.0) * p_sat
    p_dry = pressure_pa - p_vapour
    # 461.495 J/(kg K) is the gas constant for water vapour.
    return p_dry / (R_AIR * temperature_k) + p_vapour / (461.495 * temperature_k)


def local_gravity(latitude_deg: float, altitude_m: float,
                  site_elevation_m: float = 0.0) -> float:
    """Gravity at this latitude and height [m/s^2].

        g_surface = 9.780327 * (1 + 0.0053024 sin^2(lat) - 0.0000058 sin^2(2 lat))
        g(z)      = g_surface * (R_earth / (R_earth + elevation + z))^2

    The latitude term is the Earth's oblateness and rotation; the altitude term
    is the plain inverse square. Worth about 0.5% between the equator and the
    poles, and about 0.3% at 10 km. Small, but it is the difference between a
    model that closes and one that is quietly 0.5% out.
    """
    phi = math.radians(latitude_deg)
    g_surface = (9.780327 * (1.0 + 0.0053024 * math.sin(phi) ** 2
                             - 0.0000058 * math.sin(2.0 * phi) ** 2))
    radius = R_EARTH + site_elevation_m + max(0.0, altitude_m)
    return g_surface * (R_EARTH / radius) ** 2


def wind_at_altitude(reference_wind_ms: float, altitude_agl_m: float,
                     reference_height_m: float = 10.0,
                     shear_exponent: float = 0.143) -> float:
    """Wind speed at height, from a measurement near the ground [m/s].

        v(z) = v_ref * (z / z_ref)^alpha

    The power-law wind profile. alpha = 1/7 (0.143) is open flat terrain;
    rougher ground - trees, buildings - shears more strongly and takes a larger
    exponent. This matters because the rocket leaves the rail into the
    near-ground wind but spends its climb in a faster one.
    """
    if altitude_agl_m <= 0 or reference_height_m <= 0:
        return reference_wind_ms
    return reference_wind_ms * (altitude_agl_m / reference_height_m) ** shear_exponent


# =============================================================================
#  F2. DRAG - built up from the pieces it is actually made of
#
#  Rather than one fudged coefficient, drag is assembled from skin friction,
#  base pressure, nose wave drag and fin drag. That way a change to the
#  airframe moves the right term, and the report can say WHERE the drag is.
# =============================================================================

def reynolds_number(density: float, speed_ms: float, length_m: float,
                    viscosity_pa_s: float) -> float:
    """Reynolds number [-]:  Re = rho * v * L / mu.

    The ratio of inertial to viscous forces. It decides whether the boundary
    layer is laminar or turbulent, which changes skin friction by a factor of
    several.
    """
    if viscosity_pa_s <= 0:
        return 0.0
    return density * abs(speed_ms) * length_m / viscosity_pa_s


def skin_friction_coefficient(reynolds: float, mach: float,
                              roughness_m: float, length_m: float) -> float:
    """Flat-plate skin friction coefficient [-], compressibility corrected.

        laminar   (Re < 5e5):  Cf = 1.328 / sqrt(Re)        Blasius
        turbulent (Re > 5e5):  Cf = 0.455 / (log10 Re)^2.58  Prandtl-Schlichting
        roughness floor:       Cf = 0.032 * (k/L)^0.2
        compressible:          Cf / (1 + 0.15 M^2)^0.58

    The roughness floor is the important practical term: below a critical
    Reynolds number the surface finish governs and friction STOPS falling as
    the rocket goes faster. It is why a well-sanded airframe is worth real
    altitude, and it is why a rough one cannot be made to look smooth by
    flying it faster.
    """
    re = max(1.0, reynolds)
    if re < RE_TRANSITION:
        cf = 1.328 / math.sqrt(re)
    else:
        cf = 0.455 / (math.log10(re) ** 2.58)
    if roughness_m > 0 and length_m > 0:
        cf = max(cf, 0.032 * (roughness_m / length_m) ** 0.2)
    if mach > 0.1:
        cf /= (1.0 + 0.15 * mach * mach) ** 0.58
    return cf


def base_drag_coefficient(mach: float, thrusting: bool) -> float:
    """Base (aft end) pressure drag [-], referenced to the BASE area.

        subsonic:    Cd = 0.12 + 0.13 * M^2
        supersonic:  Cd = 0.25 / M
        thrusting:   x 0.15

    The blunt aft end leaves a low-pressure wake that pulls backwards. While
    the motor burns, the exhaust plume fills and pressurises that region and
    the term nearly vanishes - which is why measured drag JUMPS at burnout
    even though nothing about the rocket's shape changed. Forgetting this is
    one of the most common ways a simulation disagrees with a real flight.
    """
    if mach < 1.0:
        cd = 0.12 + 0.13 * mach * mach
    else:
        cd = 0.25 / mach
    return cd * 0.15 if thrusting else cd


def wave_drag_coefficient(mach: float, nose_fineness: float,
                          shape_factor: float) -> float:
    """Transonic and supersonic pressure (wave) drag of the nose [-].

    Zero below the drag-divergence Mach number, rising steeply to a peak just
    above Mach 1, then decaying supersonically:

        M_div = min(0.95, 0.72 + 0.06 * fineness)
        peak  = shape_factor * 0.90 / fineness^1.20      (capped at 1.2)
        rise  = peak * ((M - M_div)/(1.10 - M_div))^1.8
        decay = peak * min(1, 1.10 / beta^0.85),  beta = sqrt(M^2 - 1)

    A longer, finer nose spreads the compression over more length, so it both
    holds off the divergence and pays a smaller peak. This is an engineering
    correlation fitted to the usual data, not CFD - treat it as a good estimate
    rather than a measurement.

    ``shape_factor`` comes from the nose profile (Von Karman is lowest, a
    hemisphere is worst); ``nose_fineness`` is nose length over body diameter.
    """
    fineness = max(0.5, nose_fineness)
    m_div = min(0.95, 0.72 + 0.06 * min(fineness, 4.0))
    if mach <= m_div:
        return 0.0
    peak = min(shape_factor * 0.90 / (fineness ** 1.20), 1.2)
    m_peak = 1.10
    if mach < m_peak:
        return peak * ((mach - m_div) / (m_peak - m_div)) ** 1.8
    beta = math.sqrt(max(0.05, mach * mach - 1.0))
    return peak * min(1.0, 1.10 / beta ** 0.85)


def dynamic_pressure(density: float, speed_ms: float) -> float:
    """Dynamic pressure q [Pa]:  q = 0.5 * rho * v^2.

    The scale of every aerodynamic force. Max-q - where this peaks - is the
    moment of greatest aerodynamic load, and it is what the airframe has to be
    built to survive.
    """
    return 0.5 * density * speed_ms * speed_ms


def drag_force(dynamic_pressure_pa: float, drag_coefficient: float,
               reference_area_m2: float) -> float:
    """Drag force [N]:  D = q * Cd * A_ref.

    Always opposes the direction of travel through the air, which is not
    necessarily the direction the rocket is pointing.
    """
    return dynamic_pressure_pa * drag_coefficient * reference_area_m2


def drag_area(drag_coefficient: float, reference_area_m2: float) -> float:
    """Cd*A [m^2] - the only combination that actually matters for drag.

    Two rockets with the same Cd*A decelerate identically regardless of how
    that splits between a coefficient and an area, which is why this is the
    right number to quote and to compare.
    """
    return drag_coefficient * reference_area_m2


def ballistic_coefficient(mass_kg: float, drag_area_m2: float) -> float:
    """Ballistic coefficient [kg/m^2]:  beta = m / (Cd*A).

    How much a vehicle resists being slowed by air. High is a dart, low is a
    shuttlecock. It sets both the coast after burnout and the descent rate.
    """
    if drag_area_m2 <= 0:
        return 0.0
    return mass_kg / drag_area_m2


def terminal_velocity(mass_kg: float, gravity: float, density: float,
                      drag_area_m2: float) -> float:
    """Steady descent speed [m/s] where drag exactly balances weight.

        v_terminal = sqrt( 2*m*g / (rho * Cd*A) )

    Setting drag equal to weight and solving. Under a canopy the rocket reaches
    this within a second or two, so it is effectively the landing speed.
    """
    if density <= 0 or drag_area_m2 <= 0:
        return 0.0
    return math.sqrt(2.0 * mass_kg * gravity / (density * drag_area_m2))


# =============================================================================
#  F3. STABILITY - whether it flies straight or tumbles
#
#  A rocket is a weathervane. If the aerodynamic force acts BEHIND the centre
#  of mass, a disturbance is corrected; if in front, it is amplified and the
#  rocket tumbles. Everything here is about measuring that distance.
# =============================================================================

def fin_normal_force_slope(fin_count: int, fin_span_m: float,
                           body_radius_m: float, body_diameter_m: float,
                           root_chord_m: float, tip_chord_m: float,
                           mid_chord_sweep_m: float) -> float:
    """Fin set normal force slope CN_alpha [per radian] - Barrowman.

        K_fb  = 1 + r / (s + r)                          body interference
        CN_a  = K_fb * 4*n*(s/d)^2
                / (1 + sqrt(1 + (2*Lm/(cr+ct))^2))

    How much sideways force the fins make per radian of angle of attack. The
    (s/d)^2 says span matters enormously - doubling the span roughly quadruples
    the restoring force. K_fb is the body carrying some of the fins' load.
    """
    if fin_count <= 0 or fin_span_m <= 0 or (root_chord_m + tip_chord_m) <= 0:
        return 0.0
    k_fb = (1.0 + body_radius_m / (fin_span_m + body_radius_m)
            if (fin_span_m + body_radius_m) > 0 else 1.0)
    denominator = 1.0 + math.sqrt(
        1.0 + (2.0 * mid_chord_sweep_m / (root_chord_m + tip_chord_m)) ** 2)
    return k_fb * (4.0 * fin_count * (fin_span_m / body_diameter_m) ** 2) / denominator


def fin_center_of_pressure(fin_root_position_m: float, sweep_m: float,
                           root_chord_m: float, tip_chord_m: float) -> float:
    """Where the fins' force acts, from the nose [m] - Barrowman.

        x = x_root + sweep*(cr + 2ct)/(3(cr+ct))
                   + (1/6)*((cr+ct) - cr*ct/(cr+ct))

    The first term places the fin, the second is the sweep's contribution and
    the third the chord distribution's.
    """
    if (root_chord_m + tip_chord_m) <= 0:
        return fin_root_position_m
    return (fin_root_position_m
            + (sweep_m * (root_chord_m + 2.0 * tip_chord_m))
            / (3.0 * (root_chord_m + tip_chord_m))
            + (1.0 / 6.0) * ((root_chord_m + tip_chord_m)
                             - (root_chord_m * tip_chord_m)
                             / (root_chord_m + tip_chord_m)))


def center_of_pressure(nose_cn: float, nose_cp_m: float,
                       fin_cn: float, fin_cp_m: float,
                       total_length_m: float, mach: float = 0.3) -> float:
    """Combined centre of pressure from the nose [m].

        CP = (CN_nose * x_nose + CN_fins * x_fins) / (CN_nose + CN_fins)

    A force-weighted average of where each part's lift acts. The nose always
    contributes CN = 2 in Barrowman's method.

    TRANSONIC SHIFT: above Mach 0.8 the CP migrates rearward as the flow goes
    supersonic, which makes a rocket MORE stable through the transonic region -
    up to 8% of body length is applied here. That is a first-order correction,
    not a substitute for real supersonic data.
    """
    cn_total = nose_cn + fin_cn
    if cn_total <= 0:
        return 0.5 * total_length_m
    cp = (nose_cn * nose_cp_m + fin_cn * fin_cp_m) / cn_total
    if mach > 0.8:
        cp += min(0.08, 0.08 * (min(mach, 1.5) - 0.8) / 0.7) * total_length_m
    return cp


def static_margin(center_of_pressure_m: float, center_of_gravity_m: float,
                  body_diameter_m: float) -> float:
    """Static margin in CALIBERS [-]:  (CP - CG) / body diameter.

    THE stability number. Positive means the CP is behind the CG and the rocket
    weathervanes into the wind - stable. Negative means it tumbles.

    The usual target is 1.0 to 2.0 calibers. Under about 1 it is marginal; over
    about 4 it is over-stable, which sounds safe but is not: an over-stable
    rocket turns hard into a crosswind and flies away downrange instead of up.

    Measured in body diameters rather than metres so the number means the same
    thing on a 38 mm rocket and a 6-inch one.
    """
    if body_diameter_m <= 0:
        return 0.0
    return (center_of_pressure_m - center_of_gravity_m) / body_diameter_m


def restoring_moment(dynamic_pressure_pa: float, reference_area_m2: float,
                     normal_force_slope: float, angle_of_attack_rad: float,
                     static_margin_m: float) -> float:
    """Aerodynamic moment straightening the rocket out [N*m].

        M = q * A_ref * CN_alpha * alpha * (CP - CG)

    Proportional to dynamic pressure, so a rocket is barely stable at low speed
    off the rail and strongly stable at max-q. That is exactly why rail exit
    velocity matters: leave too slowly and there is not enough q to correct
    anything before the wind has tipped you over.
    """
    return (dynamic_pressure_pa * reference_area_m2 * normal_force_slope
            * angle_of_attack_rad * static_margin_m)


# =============================================================================
#  F4. MASS - the rocket gets lighter, and its balance moves
# =============================================================================

def current_mass(dry_mass_kg: float, propellant_remaining_kg: float) -> float:
    """Vehicle mass right now [kg]:  dry + what propellant is left."""
    return dry_mass_kg + max(0.0, propellant_remaining_kg)


def propellant_burn_rate(thrust_n: float, specific_impulse_s: float) -> float:
    """How fast propellant is leaving [kg/s]:  mdot = F / (Isp * g0).

    The rocket equation in differential form. Isp is what ties thrust to the
    mass being spent to make it.
    """
    if specific_impulse_s <= 0:
        return 0.0
    return thrust_n / (specific_impulse_s * G0)


def center_of_gravity(dry_mass_kg: float, dry_cg_m: float,
                      propellant_kg: float, propellant_cg_m: float) -> float:
    """Combined CG from the nose [m] - a mass-weighted average.

        CG = (m_dry*x_dry + m_prop*x_prop) / (m_dry + m_prop)

    As propellant burns off, the CG walks toward the dry CG. Since the CP
    barely moves, the static margin CHANGES THROUGH THE BURN - usually becoming
    more stable, because the propellant is normally aft of the dry CG. Check
    stability at both ends of the burn, not just at liftoff.
    """
    total = dry_mass_kg + max(0.0, propellant_kg)
    if total <= 0:
        return dry_cg_m
    return (dry_mass_kg * dry_cg_m + max(0.0, propellant_kg) * propellant_cg_m) / total


def pitch_inertia_point_masses(components) -> float:
    """Pitch inertia about the CG [kg*m^2] from placed masses:  I = SUM m*r^2.

    ``components`` is an iterable of (mass_kg, distance_from_cg_m).

    Inertia is what sets how FAST a rocket responds to a disturbance. Two
    rockets with the same static margin but different inertias behave quite
    differently: the heavy-ended one turns slowly into a crosswind and drifts
    less, which is the real reason nose ballast reduces drift.
    """
    return sum(mass * distance * distance for mass, distance in components)


def pitch_inertia_uniform_rod(mass_kg: float, length_m: float) -> float:
    """Pitch inertia of a uniform rod about its centre [kg*m^2].

        I = m * (L/3.5)^2

    The fallback when the masses have not been placed individually. The 3.5 is
    an empirical divisor that lands between a true uniform rod (sqrt(12) = 3.46)
    and the slightly end-heavy reality of a rocket with a motor in the tail.
    Use placed components instead wherever possible - this is an estimate.
    """
    return mass_kg * (length_m / 3.5) ** 2


# =============================================================================
#  F5. RECOVERY - getting it back in one piece
# =============================================================================

def canopy_drag_area(diameter_m: float, drag_coefficient: float) -> float:
    """Canopy Cd*A [m^2]:  Cd * pi * (d/2)^2.

    Round parachutes run about Cd = 1.5 on their nominal diameter; a flat sheet
    is nearer 0.8 and a ribbon drogue lower still. As with the airframe, only
    the product matters.
    """
    return drag_coefficient * math.pi * (diameter_m / 2.0) ** 2


def inflation_factor(time_since_deploy_s: float, inflation_time_s: float) -> float:
    """How far open the canopy is [-], 0 to 1.

    A parachute does not appear instantly. Modelled as the AREA growing
    linearly over the inflation time; because drag goes with area, this is the
    fraction to multiply Cd*A by. Deployment shock depends entirely on how fast
    this happens, which is why a drogue and a slow-opening main are what keep
    an airframe intact.
    """
    if inflation_time_s <= 0:
        return 1.0
    return min(1.0, max(0.0, time_since_deploy_s / inflation_time_s))


def reefed_area_factor(time_since_deploy_s: float, reef_ratio: float,
                       reef_duration_s: float, disreef_time_s: float) -> float:
    """Area fraction for a REEFED canopy [-], 0 to 1.

    Reefing ties the canopy down to a fraction of its area, lets the vehicle
    slow, then releases. It is how you survive deploying at speed: opening a
    full main at 100 m/s would tear it off. Three stages - held reefed, then
    the disreef ramp, then fully open.
    """
    if time_since_deploy_s < reef_duration_s:
        return reef_ratio
    if disreef_time_s <= 0:
        return 1.0
    progress = (time_since_deploy_s - reef_duration_s) / disreef_time_s
    return min(1.0, reef_ratio + (1.0 - reef_ratio) * max(0.0, progress))


def deployment_shock_force(dynamic_pressure_pa: float,
                           canopy_drag_area_m2: float,
                           inflation_factor_value: float = 1.0) -> float:
    """Peak load on the harness at deployment [N]:  F = q * Cd*A * fill.

    Compare it against the harness rating. Deploying at high q is what breaks
    shock cords and rips canopies - and q goes as speed SQUARED, so deploying
    at twice the speed is four times the load.
    """
    return dynamic_pressure_pa * canopy_drag_area_m2 * inflation_factor_value


# =============================================================================
#  F6. EQUATIONS OF MOTION - summing the forces and moving the rocket
#
#  Two degrees of freedom: vertical and downrange. The rocket is treated as a
#  point mass that points along a heading, which is enough for altitude, drift
#  and loads, and is not enough for roll or coning.
# =============================================================================

def net_vertical_force(thrust_n: float, drag_n: float, weight_n: float,
                       velocity_ms: float) -> float:
    """Net upward force [N]:  F = T - D*sign(v) - W.

    Drag opposes MOTION, so its sign flips at apogee: on the way up it adds to
    gravity, on the way down it fights it. Getting that sign wrong is the
    classic simulation bug - the rocket accelerates downward forever.
    """
    drag_signed = drag_n if velocity_ms >= 0 else -drag_n
    return thrust_n - drag_signed - weight_n


def acceleration(net_force_n: float, mass_kg: float) -> float:
    """Acceleration [m/s^2]:  a = F / m. Newton's second law, and that is all."""
    if mass_kg <= 0:
        return 0.0
    return net_force_n / mass_kg


def rail_constrained_acceleration(force_along_rail_n: float, mass_kg: float,
                                  still_on_rail: bool) -> float:
    """Acceleration while the rail still holds the rocket [m/s^2].

    On the rail only the along-rail component can accelerate the vehicle - the
    rail takes everything else. This is why a rocket does not weathervane until
    it leaves, and why rail exit velocity is the number that decides whether a
    windy launch is safe.
    """
    if not still_on_rail:
        return acceleration(force_along_rail_n, mass_kg)
    return acceleration(max(0.0, force_along_rail_n), mass_kg)


def angle_of_attack(velocity_vertical_ms: float, velocity_horizontal_ms: float,
                    body_angle_rad: float) -> float:
    """Angle between where the rocket POINTS and where it is GOING [rad].

    Zero when flying straight along its own axis. Any non-zero value makes the
    fins produce a restoring force - and also costs drag, which is why a
    weathercocking rocket loses altitude twice over: once to the turn, once to
    the extra drag of flying sideways.
    """
    if abs(velocity_vertical_ms) < 1e-9 and abs(velocity_horizontal_ms) < 1e-9:
        return 0.0
    flight_path = math.atan2(velocity_horizontal_ms, velocity_vertical_ms)
    return flight_path - body_angle_rad


def weathercock_rate(target_angle_rad: float, current_angle_rad: float,
                     time_constant_s: float, dt_s: float) -> float:
    """New body angle after weathercocking for dt [rad].

    A stable rocket turns to point into the relative wind, but not instantly -
    its inertia sets a time constant. Modelled as first-order relaxation
    toward the flight path:

        theta_new = theta + (theta_target - theta) * min(1, dt/tau)

    A larger pitch inertia means a longer tau, a slower turn, and less drift.
    """
    if time_constant_s <= 0:
        return target_angle_rad
    return current_angle_rad + (target_angle_rad - current_angle_rad) * min(
        1.0, dt_s / time_constant_s)


def integrate_step(position_m: float, velocity_ms: float,
                   acceleration_ms2: float, dt_s: float) -> tuple:
    """Advance one timestep [m, m/s] - explicit (semi-implicit) Euler.

        v_new = v + a*dt
        x_new = x + v_new*dt

    Updating velocity FIRST and using the new value for position is what makes
    this semi-implicit, and it is noticeably better behaved than plain Euler
    for this kind of problem. Accuracy comes from a small dt - 0.01 s is what
    the app uses, which is about 30,000 steps for a Goddard flight.

    Returns (position_new, velocity_new).
    """
    velocity_new = velocity_ms + acceleration_ms2 * dt_s
    return position_m + velocity_new * dt_s, velocity_new


def stagnation_temperature(ambient_temperature_k: float, mach: float,
                           recovery_factor: float = RECOVERY_FACTOR) -> float:
    """Temperature the skin actually reaches [K].

        T_skin = T_ambient * (1 + r * (gamma-1)/2 * M^2)

    Air brought to rest against the airframe gives up its kinetic energy as
    heat. The recovery factor (about 0.9 turbulent) is the fraction of the full
    stagnation rise the surface really feels. This is what decides whether a
    fibreglass airframe survives a supersonic flight - G10 softens around 410 K,
    which Mach 2 will exceed at low altitude.
    """
    return ambient_temperature_k * (
        1.0 + recovery_factor * (GAMMA_AIR - 1.0) / 2.0 * mach * mach)


def mach_number(speed_ms: float, speed_of_sound_ms: float) -> float:
    """Mach number [-]:  M = v / a."""
    if speed_of_sound_ms <= 0:
        return 0.0
    return abs(speed_ms) / speed_of_sound_ms


def delta_v_gravity_loss(gravity: float, angle_from_vertical_rad: float,
                         dt_s: float) -> float:
    """Delta-v lost to gravity over a timestep [m/s]:  g*cos(theta)*dt.

    Every second spent climbing costs about 9.8 m/s of velocity that gravity
    takes back. Tilting reduces it - which is why real launch vehicles pitch
    over - but for a sounding rocket going straight up, this is usually the
    single largest loss in the budget.
    """
    return gravity * math.cos(angle_from_vertical_rad) * dt_s


def delta_v_drag_loss(drag_n: float, mass_kg: float, dt_s: float) -> float:
    """Delta-v lost to drag over a timestep [m/s]:  (D/m)*dt.

    Unlike gravity loss this is not recoverable in any sense: the energy is
    simply heat in the air. On a high-drag amateur vehicle it frequently
    exceeds the gravity loss.
    """
    if mass_kg <= 0:
        return 0.0
    return drag_n / mass_kg * dt_s
