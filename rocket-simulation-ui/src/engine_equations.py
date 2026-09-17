"""
===============================================================================
 ENGINE EQUATIONS - every calculation that decides what the motor does
===============================================================================

This file exists so the physics of the motor lives in ONE place you can edit.
Change an equation here and every part of the app that reports on the engine -
the Engine Lab, the thrust curve it generates, the Flight Report's propulsion
checks, the engine data sheet - changes with it. Nothing here is duplicated
anywhere else in the program.

WHAT IS IN HERE
    Only equations. Pure functions: numbers in, numbers out. No classes that
    hold state, no files, no user interface, no plotting. Every function can be
    called on its own, from a script or a Python prompt, with nothing set up.

WHAT IS NOT IN HERE
    Structural and material questions - whether the case can take the pressure,
    whether the nozzle survives the heat, how strong the fuel grain is. Those
    live with the materials and the failure checks. This file answers "what
    does the motor do", not "does it survive doing it".

    The integrator is also elsewhere. These are the equations; hybrid_sim's
    EngineModel is the thing that marches them through time.

WHERE THE EQUATIONS COME FROM
    The motor model follows Humble, Henry & Larson, "Space Propulsion
    Analysis and Design", Chapter 7 "Hybrid Rocket Propulsion Systems",
    Section 7.5 "Performance Estimate" (pp. 421-423). Table 7.9 there sets
    out the simulation algorithm step by step, and the sections below are
    labelled with the step each one serves, so this file can be read against
    the book. Equation numbers in the comments - (7.101), (7.102), (1.6),
    (3.112) and so on - are Humble's.

    Where this file departs from him it says so at the point of departure.
    The N2O property fits, the Dyer non-homogeneous-equilibrium injector
    model and the self-pressurising tank blowdown are not in Chapter 7 at
    all; Humble's oxidiser feed is a pressure-fed liquid, and a
    self-pressurising nitrous system needs its own saturation physics. Those
    carry their own sources.

UNITS
    SI throughout, with no exceptions: metres, kilograms, seconds, kelvin,
    pascals, newtons. Every argument and every return value says its unit. If
    you edit an equation, keep it in SI - the rest of the program converts for
    display and assumes what it gets from here is SI.

HOW THE MOTOR WORKS, IN ORDER
    A hybrid burns a solid fuel grain with a liquid oxidiser sprayed down the
    middle of it. Nitrous oxide is SELF-PRESSURISING: it sits on its own
    saturation curve, so tank pressure is set by tank temperature, and the tank
    cools as liquid boils off to replace what the injector draws. There is no
    regulator and no pressurant gas. That is the whole reason to fly N2O, and
    it is why the tank temperature is a state variable rather than a constant.

        1. tank        - temperature gives saturation pressure          (S1)
        2. injector    - tank-to-chamber pressure drop gives flow       (S2)
        3. grain       - oxidiser flux over the port gives regression   (S3)
        4. combustion  - the O/F ratio gives c*, which gives Pc         (S4)
        5. nozzle      - Pc and area ratio give thrust coefficient      (S5)
        6. performance - Cf x Pc x A_throat gives thrust                (S6)

    Each section below is numbered to match.
===============================================================================
"""
from __future__ import annotations

import math

# =============================================================================
#  CONSTANTS
# =============================================================================

#: Standard gravity [m/s^2]. Used to turn an exhaust velocity into an Isp in
#: seconds. This is a defined constant, not a local gravity - Isp is quoted
#: against it everywhere in the world.
G0 = 9.80665

#: Universal gas constant [J/(mol*K)].
R_UNIVERSAL = 8.314462618

#: Sea-level pressure [Pa], the ambient the nozzle is sized against by default.
P_SEA_LEVEL = 101325.0

# --- nitrous oxide, as a substance -------------------------------------------
#: Critical pressure [Pa], critical temperature [K], critical density [kg/m^3]
#: and specific gas constant [J/(kg*K)] of N2O.
N2O_P_CRIT = 7.245e6
N2O_T_CRIT = 309.57
N2O_RHO_CRIT = 452.0
N2O_R_GAS = 188.91

#: Below this the saturation fits stop being trustworthy, so temperature is
#: clamped into [200 K, T_crit]. A tank that cold has stopped flowing anyway.
N2O_T_FLOOR = 200.0

#: Ratio of specific heats for N2O VAPOUR. Only used for choked flow out of a
#: vent orifice - the combustion products have their own gamma, which is an
#: engine parameter rather than a constant.
N2O_GAMMA_VAPOUR = 1.27


# =============================================================================
#  S1. THE TANK - nitrous oxide saturation properties
#      [Humble Table 7.9 step 1: initialise, and the oxidiser history]
#
#  N2O in a tank sits as liquid with its own vapour above it, both on the
#  saturation curve. Everything about the tank follows from its temperature.
#  All five fits are closed-form in temperature, valid 200 K to the critical
#  point, and all clamp their input rather than returning nonsense outside it.
# =============================================================================

def _clamp_tank_temperature(T_k: float) -> float:
    """Hold temperature inside the range the saturation fits are valid over."""
    return min(max(float(T_k), N2O_T_FLOOR), N2O_T_CRIT)


# Wagner equation coefficients and exponents for saturation pressure, and the
# Wagner-form coefficients for saturated liquid density.
# Source: Ferreira & Lobo (2009).
_WAGNER_P_COEFFS = (-6.8657, 1.9373, -2.6440, 0.0387)
_WAGNER_P_POWERS = (1.0, 1.5, 2.5, 5.0)
_WAGNER_RHO_COEFFS = (1.72328, -0.8395, 0.5106, -0.10412)
_WAGNER_RHO_POWERS = (1.0 / 3.0, 2.0 / 3.0, 1.0, 4.0 / 3.0)

# Cubic fit to the NIST saturated-liquid heat capacity, in c3*T^3 + ... order.
_CP_LIQUID_COEFFS = (0.004311, -3.2501, 819.213, -66944.4)


def n2o_saturation_pressure(T_k: float) -> float:
    """Tank pressure from tank temperature [Pa].

        P_sat = P_crit * exp( (T_crit/T) * SUM a_i * tau^p_i ),  tau = 1 - T/T_crit

    This one equation is why an N2O hybrid needs no pressurant: warm the tank
    and it pressurises itself. It is also why performance falls away as the
    burn cools the tank - see n2o_tank_cooling_rate.
    """
    T = _clamp_tank_temperature(T_k)
    tau = 1.0 - T / N2O_T_CRIT
    series = sum(a * tau ** p
                 for a, p in zip(_WAGNER_P_COEFFS, _WAGNER_P_POWERS))
    return N2O_P_CRIT * math.exp((N2O_T_CRIT / T) * series)


def n2o_liquid_density(T_k: float) -> float:
    """Density of the saturated LIQUID [kg/m^3].

        rho_l = rho_crit * exp( SUM b_i * tau^q_i ),  tau = 1 - T/T_crit

    Sets how much oxidiser a given tank actually holds, so it decides the
    propellant load as much as the tank volume does.
    """
    T = _clamp_tank_temperature(T_k)
    tau = 1.0 - T / N2O_T_CRIT
    return N2O_RHO_CRIT * math.exp(
        sum(b * tau ** q
            for b, q in zip(_WAGNER_RHO_COEFFS, _WAGNER_RHO_POWERS)))


def n2o_vapour_density(T_k: float) -> float:
    """Density of the saturated VAPOUR [kg/m^3], as an ideal gas.

        rho_v = P_sat(T) / (R_N2O * T)

    Ideal gas is a simplification near the critical point, where the vapour is
    far from ideal. It matters little because the vapour is a small part of the
    load until the liquid is nearly gone.
    """
    T = _clamp_tank_temperature(T_k)
    return n2o_saturation_pressure(T) / (N2O_R_GAS * T)


def n2o_heat_of_vaporisation(T_k: float) -> float:
    """Latent heat of vaporisation [J/kg], Watson correlation.

        h_v = 140.01 * (T_crit - T)^0.2041  [kJ/kg]

    This is the energy price of every kilogram that leaves the tank as vapour,
    and it is paid out of the liquid's own heat - which is what cools the tank.
    """
    T = _clamp_tank_temperature(T_k)
    return 140.01 * (N2O_T_CRIT - T) ** 0.2041 * 1000.0


def n2o_liquid_heat_capacity(T_k: float) -> float:
    """Specific heat capacity of the saturated liquid [J/(kg*K)].

    Cubic fit to NIST data. Together with the latent heat it sets how fast the
    tank cools as it empties: a lot of heat capacity per kilogram means the
    temperature - and therefore the pressure - holds up longer.
    """
    T = _clamp_tank_temperature(T_k)
    c3, c2, c1, c0 = _CP_LIQUID_COEFFS
    return c3 * T ** 3 + c2 * T ** 2 + c1 * T + c0


def tank_volume(diameter_m: float, length_m: float) -> float:
    """Internal volume of a cylindrical tank [m^3]:  V = pi * (d/2)^2 * L."""
    return math.pi * (diameter_m / 2.0) ** 2 * length_m


def tank_liquid_mass(total_ox_mass_kg: float, T_k: float,
                     tank_volume_m3: float) -> float:
    """How much of the oxidiser still in the tank is LIQUID [kg].

    The tank is full of liquid and vapour, both saturated. Two facts pin the
    split: the masses add to what is in the tank, and the volumes add to the
    tank's volume. Solving the pair,

        m_liquid = (V_tank - m_total/rho_v) / (1/rho_l - 1/rho_v)

    This matters because the injector can only draw liquid. When it reaches
    zero the motor is in vapour blowdown and thrust collapses.
    """
    rho_l = n2o_liquid_density(T_k)
    rho_v = n2o_vapour_density(T_k)
    denominator = 1.0 / rho_l - 1.0 / rho_v
    if abs(denominator) < 1e-12:      # at the critical point the phases merge
        return 0.0
    liquid = (tank_volume_m3 - total_ox_mass_kg / rho_v) / denominator
    return min(max(liquid, 0.0), total_ox_mass_kg)


def tank_fill_fraction(liquid_mass_kg: float, T_k: float,
                       tank_volume_m3: float) -> float:
    """Fraction of the tank's volume filled with liquid [-], 0 to 1."""
    if tank_volume_m3 <= 0:
        return 0.0
    return liquid_mass_kg / n2o_liquid_density(T_k) / tank_volume_m3


def n2o_tank_cooling_rate(liquid_mass_kg: float, T_k: float,
                          mass_leaving_kg_s: float,
                          cooling_coefficient: float = 1.0) -> float:
    """How fast the tank is cooling [K/s] - a negative number.

        dT/dt = -k * mdot_out * h_v(T) / (m_liquid * cp_l(T))

    Every kilogram that leaves as vapour has to be boiled first, and the latent
    heat comes out of the liquid left behind. That is self-pressurisation
    working in reverse: the tank cools, saturation pressure falls, the injector
    sees less drop, and thrust tails off through the burn. It is the single
    biggest reason a real N2O hybrid's thrust curve droops.

    ``mass_leaving_kg_s`` is everything going out - injector draw plus anything
    vented. ``cooling_coefficient`` is a tuning factor for how well the tank
    tracks equilibrium; 1.0 is perfect equilibrium, lower is a tank that lags.
    """
    if liquid_mass_kg <= 0:
        return 0.0
    heat_capacity = liquid_mass_kg * n2o_liquid_heat_capacity(T_k)
    if heat_capacity <= 0:
        return 0.0
    return -(cooling_coefficient * mass_leaving_kg_s
             * n2o_heat_of_vaporisation(T_k) / heat_capacity)


# =============================================================================
#  S2. THE INJECTOR - how much oxidiser reaches the chamber
#      [feeds Humble Table 7.9 step 2's m_dot_ox]
#
#  A saturated liquid crossing an orifice into a much lower pressure starts
#  boiling inside the orifice. Neither of the two simple models describes that
#  well on its own, so the Dyer model blends them.
# =============================================================================

def injector_area(n_holes: int, hole_diameter_m: float) -> float:
    """Total geometric injector area [m^2]:  n * pi * (d/2)^2."""
    return n_holes * math.pi * (hole_diameter_m / 2.0) ** 2


def injector_flow_spi(cd_area_m2: float, liquid_density: float,
                      delta_p_pa: float) -> float:
    """Single-Phase Incompressible flow [kg/s] - the plain orifice equation.

        mdot = Cd*A * sqrt(2 * rho_liquid * dP)

    Treats the nitrous as an ordinary liquid that does not boil. Right when the
    pressure drop is large; OVER-predicts when the drop is small, because it
    ignores the vapour forming in the orifice and choking it.
    """
    return cd_area_m2 * math.sqrt(max(0.0, 2.0 * liquid_density * delta_p_pa))


def injector_flow_hem(cd_area_m2: float, vapour_density: float,
                      heat_of_vaporisation: float,
                      chamber_pressure_pa: float,
                      tank_pressure_pa: float) -> float:
    """Homogeneous Equilibrium Model flow [kg/s].

        mdot = Cd*A * rho_vapour * sqrt( 2 * h_v * (1 - Pc/Pt) )

    The opposite assumption: the flow reaches equilibrium inside the orifice
    and is fully flashed to vapour. Right when the pressure drop is small;
    UNDER-predicts when it is large.
    """
    if tank_pressure_pa <= 0:
        return 0.0
    ratio = max(0.0, 1.0 - chamber_pressure_pa / tank_pressure_pa)
    return cd_area_m2 * vapour_density * math.sqrt(
        2.0 * heat_of_vaporisation * ratio)


def injector_mass_flow(cd_area_m2: float, T_k: float,
                       chamber_pressure_pa: float,
                       kappa: float = 1.0,
                       max_pc_over_pt: float = 0.95) -> tuple:
    """Oxidiser flow through the injector [kg/s], and tank pressure [Pa].

    The Dyer non-homogeneous non-equilibrium (NHNE) model: a weighted blend of
    the two limits above,

        w    = 1 / (1 + kappa)
        mdot = (1 - w) * mdot_SPI + w * mdot_HEM

    kappa is the ratio of the bubble growth time to the residence time in the
    orifice. kappa = 1 weights them equally and is the usual starting point.

    ``max_pc_over_pt`` stops the chamber from being treated as though it were
    pushing back harder than the tank can push - above about 0.95 the injector
    has lost authority and the motor is on the edge of feed-system coupled
    instability ("chugging"), which this quasi-steady model cannot represent.

    Returns (mdot_kg_s, tank_pressure_pa).
    """
    tank_pressure = n2o_saturation_pressure(T_k)
    effective_pc = min(chamber_pressure_pa, tank_pressure * max_pc_over_pt)
    delta_p = max(0.0, tank_pressure - effective_pc)

    spi = injector_flow_spi(cd_area_m2, n2o_liquid_density(T_k), delta_p)
    hem = injector_flow_hem(cd_area_m2, n2o_vapour_density(T_k),
                            n2o_heat_of_vaporisation(T_k),
                            effective_pc, tank_pressure)
    weight = 1.0 / (1.0 + kappa)
    return (1.0 - weight) * spi + weight * hem, tank_pressure


def injector_stiffness(tank_pressure_pa: float,
                       chamber_pressure_pa: float) -> float:
    """Injector pressure drop as a fraction of chamber pressure [-].

        stiffness = (Pt - Pc) / Pc

    The classic feed-system stability number. Below about 0.20 the chamber can
    push back on the feed and the motor chugs; 0.20 to 0.30 is the usual target.
    """
    if chamber_pressure_pa <= 1e4:
        return 0.0
    return max(0.0, tank_pressure_pa - chamber_pressure_pa) / chamber_pressure_pa


def vent_mass_flow(vent_cd: float, vent_area_m2: float,
                   tank_pressure_pa: float, T_k: float) -> float:
    """Vapour bled overboard through a vent orifice [kg/s].

        mdot = Cd*A*Pt * sqrt(gamma) * (2/(gamma+1))^((gamma+1)/(2(gamma-1)))
                 / sqrt(R*T)

    Choked flow, which any vent to atmosphere is at N2O tank pressures. This is
    oxidiser you filled and did not burn, so it comes straight off total
    impulse. Zero unless a vent has been fitted.
    """
    if vent_area_m2 <= 0:
        return 0.0
    g = N2O_GAMMA_VAPOUR
    choked = math.sqrt(g) * (2.0 / (g + 1.0)) ** ((g + 1.0) / (2.0 * (g - 1.0)))
    return vent_cd * vent_area_m2 * tank_pressure_pa * choked / math.sqrt(
        N2O_R_GAS * _clamp_tank_temperature(T_k))


# =============================================================================
#  S3. THE FUEL GRAIN - how fast the fuel burns back
#      [Humble Table 7.9, steps 2 and 3: regression rate, then fuel flow]
#
#  A hybrid's fuel does not burn at a rate set by pressure the way a solid
#  does. It burns at a rate set by how hard the oxidiser is blowing down the
#  port, which is why the O/F ratio wanders through the burn instead of staying
#  where it was designed.
# =============================================================================

def port_area(n_ports: int, port_radius_m: float) -> float:
    """Total flow area down the port(s) [m^2]:  n * pi * r^2."""
    return n_ports * math.pi * port_radius_m * port_radius_m


def burn_area(n_ports: int, port_radius_m: float, grain_length_m: float) -> float:
    """Total burning surface [m^2]:  n * 2*pi*r*L.

    The inner wall of every port. It GROWS as the port opens up, which is why a
    hybrid's fuel flow tends to rise through the burn even as the oxidiser flow
    falls away with tank pressure.
    """
    return n_ports * 2.0 * math.pi * port_radius_m * grain_length_m


def oxidiser_flux(mdot_ox_kg_s: float, port_area_m2: float) -> float:
    """Oxidiser mass flux down the port [kg/(m^2*s)].

        G_ox = mdot_ox / A_port

    The number the whole grain design turns on. Too low and combustion is
    sluggish and unstable; too high (above roughly 700 kg/m^2s) and the flame
    blows away from the fuel surface and it stops burning properly.
    """
    if port_area_m2 <= 0:
        return 0.0
    return mdot_ox_kg_s / port_area_m2


def regression_rate(oxidiser_flux_kg_m2_s: float, a: float, n: float) -> float:
    """How fast the fuel surface moves back [m/s] - the Marxman law.

        rdot = a * G_ox^n

    ``a`` and ``n`` are measured for a fuel, not derived. For HTPB, n is
    usually near 0.5, which means doubling the oxidiser flux only raises the
    regression rate by about 40% - so the O/F ratio climbs as a burn goes on.
    This single line is the heart of hybrid motor design; change it and the
    whole character of the motor changes.
    """
    if oxidiser_flux_kg_m2_s <= 0:
        return 0.0
    return a * oxidiser_flux_kg_m2_s ** n


def fuel_mass_flow(fuel_density: float, burn_area_m2: float,
                   regression_rate_m_s: float) -> float:
    """Fuel entering the chamber [kg/s]:  mdot_f = rho_fuel * A_burn * rdot."""
    return fuel_density * burn_area_m2 * regression_rate_m_s


def initial_fuel_mass(fuel_density: float, outer_radius_m: float,
                      port_radius_m: float, grain_length_m: float,
                      n_ports: int = 1) -> float:
    """Loaded fuel mass [kg] - the solid left once the ports are bored out.

    For a single port this is a plain annulus. For N ports the casing area is
    shared between them, so each port is bored from its own share of the
    cross-section.
    """
    if n_ports > 1:
        return (fuel_density * n_ports
                * math.pi * (outer_radius_m ** 2 / n_ports - port_radius_m ** 2)
                * grain_length_m)
    return (fuel_density * math.pi
            * (outer_radius_m ** 2 - port_radius_m ** 2) * grain_length_m)


def web_remaining(outer_radius_m: float, port_radius_m: float) -> float:
    """Fuel wall left to burn [m]. Reaching zero is a burn-through."""
    return max(0.0, outer_radius_m - port_radius_m)


def chamber_volume(n_ports: int, port_radius_m: float, grain_length_m: float,
                   outer_radius_m: float, pre_chamber_m: float,
                   post_chamber_m: float) -> float:
    """Gas volume in the chamber [m^3] - the ports plus the end chambers."""
    case_area = math.pi * outer_radius_m ** 2
    return (port_area(n_ports, port_radius_m) * grain_length_m
            + case_area * (pre_chamber_m + post_chamber_m))


def characteristic_length(chamber_volume_m3: float,
                          throat_area_m2: float) -> float:
    """L* = V_chamber / A_throat [m].

    How much chamber there is per unit of throat. Too small and the propellant
    leaves before it has finished burning; too large and the motor is heavier
    than it needs to be.
    """
    if throat_area_m2 <= 0:
        return 0.0
    return chamber_volume_m3 / throat_area_m2


def gas_residence_time(characteristic_length_m: float,
                       cstar_m_s: float) -> float:
    """Roughly how long the gas stays in the chamber [s]:  L* / c*.

    Compare it against how long the propellant needs to finish reacting. Too
    short and the motor throws unburnt propellant out of the nozzle.
    """
    return characteristic_length_m / max(1.0, cstar_m_s)


# =============================================================================
#  S4. COMBUSTION - what the propellant is worth, and the chamber pressure
#      [Humble Table 7.9 step 4: thermochemistry, O/F -> c*]
# =============================================================================

def mixture_ratio(mdot_ox_kg_s: float, mdot_fuel_kg_s: float) -> float:
    """O/F - oxidiser mass flow over fuel mass flow [-].

    A hybrid does not hold this steady. It is set by whatever the injector and
    the regression law happen to be doing at that instant, and it typically
    climbs through the burn as the port opens up.
    """
    if mdot_fuel_kg_s <= 1e-9:
        return 0.0
    return mdot_ox_kg_s / mdot_fuel_kg_s


def characteristic_velocity(of_ratio: float, of_table, cstar_table,
                            of_shift: float = 0.0,
                            cstar_scale: float = 1.0) -> float:
    """c* - how much chamber pressure the propellant is worth [m/s].

    Interpolated from a table computed by an equilibrium code (CEA) for this
    propellant pair. c* depends almost entirely on the mixture ratio, and peaks
    somewhere near stoichiometric: run richer or leaner than the peak and you
    get less pressure for the same mass flow.

    ``of_shift`` and ``cstar_scale`` let a particular fuel formulation be
    nudged off the table without rebuilding it.

    Outside the table the value is HELD at the end point rather than
    extrapolated - a polynomial run past its data does not degrade gracefully.
    """
    of = of_ratio + of_shift
    of = min(max(of, of_table[0]), of_table[-1])
    # Straight linear interpolation, kept explicit so it is easy to swap for a
    # spline or a different table without touching anything else.
    for i in range(len(of_table) - 1):
        lo, hi = of_table[i], of_table[i + 1]
        if lo <= of <= hi:
            span = hi - lo
            frac = 0.0 if span == 0 else (of - lo) / span
            value = cstar_table[i] + frac * (cstar_table[i + 1] - cstar_table[i])
            return cstar_scale * value
    return cstar_scale * cstar_table[-1]


def chamber_pressure_target(mdot_total_kg_s: float, cstar_m_s: float,
                            throat_area_m2: float,
                            cstar_efficiency: float = 1.0,
                            tank_pressure_pa: float = None,
                            max_pc_over_pt: float = 0.95) -> float:
    """The chamber pressure this mass flow would settle at [Pa].

        Pc = eta_cstar * mdot_total * c* / A_throat

    Straight from the definition of c*. The chamber is a box with propellant
    coming in and gas leaving through a choked throat; this is the pressure at
    which those balance.

    ``cstar_efficiency`` is how much of the theoretical c* combustion actually
    delivers - hybrids are typically 0.85 to 0.95 because mixing is imperfect.

    The result is capped against tank pressure: the chamber cannot push back
    harder than the tank is pushing.
    """
    if mdot_total_kg_s <= 0 or throat_area_m2 <= 0:
        return 0.0
    pc = cstar_efficiency * mdot_total_kg_s * cstar_m_s / throat_area_m2
    if tank_pressure_pa is not None:
        pc = min(pc, tank_pressure_pa * max_pc_over_pt)
    return pc


def chamber_pressure_lag(target_pa: float, current_pa: float,
                         tau_s: float) -> float:
    """Rate of change of chamber pressure [Pa/s] - a first-order lag.

        dPc/dt = (Pc_target - Pc) / tau

    The chamber has volume, so it cannot change pressure instantly: gas has to
    accumulate or drain. Without this the pressure would jump discontinuously
    at ignition and at burnout, and the integrator would struggle.
    """
    if tau_s <= 0:
        return 0.0
    return (target_pa - current_pa) / tau_s


# =============================================================================
#  S5. THE NOZZLE - turning chamber pressure into thrust
#      [Humble Table 7.9 step 5: chamber, nozzle, performance]
# =============================================================================

def throat_area(throat_diameter_m: float) -> float:
    """Throat area [m^2]:  pi * (d/2)^2. The motor's most important dimension."""
    return math.pi * (throat_diameter_m / 2.0) ** 2


def eroded_throat_diameter(initial_diameter_m: float, erosion_rate_m_s: float,
                           time_s: float) -> float:
    """Throat diameter after erosion [m]:  d0 + 2 * rate * t.

    The rate is a RADIUS growth, hence the factor of two. Graphite and phenolic
    throats really do open up during a burn, and since chamber pressure goes as
    1/A_throat, even a small amount bleeds off pressure and thrust.
    """
    if erosion_rate_m_s <= 0:
        return initial_diameter_m
    return initial_diameter_m + 2.0 * erosion_rate_m_s * max(0.0, time_s)


def expansion_ratio(exit_area_m2: float, throat_area_m2: float) -> float:
    """Area ratio Ae/At [-]. With a fixed exit, erosion LOWERS this."""
    if throat_area_m2 <= 0:
        return 1.0
    return max(1.0001, exit_area_m2 / throat_area_m2)


def area_ratio_from_mach(mach: float, gamma: float) -> float:
    """Area ratio that produces this exit Mach [-] - isentropic area relation.

        A/At = (1/M) * ( (2/(g+1)) * (1 + (g-1)/2 * M^2) )^((g+1)/(2(g-1)))

    Solve it backwards (see exit_mach) to get the Mach from a nozzle you have.
    """
    return ((1.0 / mach)
            * ((2.0 / (gamma + 1.0)) * (1.0 + (gamma - 1.0) / 2.0 * mach * mach))
            ** ((gamma + 1.0) / (2.0 * (gamma - 1.0))))


def exit_mach(area_ratio: float, gamma: float,
              tolerance: float = 1e-10, max_iterations: int = 200) -> float:
    """Supersonic exit Mach for an area ratio [-].

    The area relation cannot be inverted in closed form, so this brackets the
    supersonic root between M just above 1 and M = 50 and bisects. Bisection
    rather than Newton because the function is steep near M = 1 and a Newton
    step can leap out of the supersonic branch entirely.
    """
    target = max(1.0001, area_ratio)
    low, high = 1.0 + 1e-9, 50.0
    f_low = area_ratio_from_mach(low, gamma) - target
    for _ in range(max_iterations):
        mid = 0.5 * (low + high)
        f_mid = area_ratio_from_mach(mid, gamma) - target
        if abs(f_mid) < tolerance or (high - low) < tolerance:
            return mid
        if (f_mid < 0) == (f_low < 0):
            low, f_low = mid, f_mid
        else:
            high = mid
    return 0.5 * (low + high)


def exit_pressure(chamber_pressure_pa: float, exit_mach_number: float,
                  gamma: float) -> float:
    """Static pressure at the nozzle exit [Pa] - isentropic relation.

        Pe = Pc * (1 + (g-1)/2 * Me^2)^(-g/(g-1))
    """
    return chamber_pressure_pa * (
        1.0 + (gamma - 1.0) / 2.0 * exit_mach_number ** 2) ** (
        -gamma / (gamma - 1.0))


def vandenkerckhove(gamma: float) -> float:
    """The Vandenkerckhove function Gamma(gamma) [-].

        Gamma = sqrt(g) * (2/(g+1))^((g+1)/(2(g-1)))

    A tidy grouping of gammas that turns up in every choked-flow expression.
    """
    return math.sqrt(gamma) * (2.0 / (gamma + 1.0)) ** (
        (gamma + 1.0) / (2.0 * (gamma - 1.0)))


def thrust_coefficient(chamber_pressure_pa: float, ambient_pressure_pa: float,
                       area_ratio: float, exit_mach_number: float,
                       gamma: float,
                       separation_criterion: float = 0.4) -> float:
    """Thrust coefficient Cf [-] - how much the nozzle multiplies Pc*At.

        Cf = Gamma * sqrt( 2g/(g-1) * (1 - (Pe/Pc)^((g-1)/g)) )   momentum
             + (Pe - Pa)/Pc * eps                                  pressure

    The first term is momentum the nozzle extracted; the second is the pressure
    imbalance across the exit plane, which is negative when over-expanded.

    FLOW SEPARATION: below roughly Pe = 0.4 * Pa the exhaust cannot stay
    attached to the wall and tears away, and the nozzle stops behaving like the
    area ratio says. Summerfield's criterion. Past that point the effective
    exit pressure is held at the separation value rather than continuing down,
    because the flow has separated and the rest of the bell is doing nothing.
    """
    if chamber_pressure_pa <= 1e4:
        return 0.0
    pe = exit_pressure(chamber_pressure_pa, exit_mach_number, gamma)
    pe_effective = max(pe, separation_criterion * ambient_pressure_pa)
    momentum = vandenkerckhove(gamma) * math.sqrt(max(
        0.0, 2.0 * gamma / (gamma - 1.0)
        * (1.0 - (pe / chamber_pressure_pa) ** ((gamma - 1.0) / gamma))))
    pressure = (pe_effective - ambient_pressure_pa) / chamber_pressure_pa * area_ratio
    return momentum + pressure


def divergence_loss(half_angle_deg: float) -> float:
    """Divergence (cosine) efficiency of a conical nozzle [-].

        lambda = (1 + cos(alpha)) / 2

    A cone throws exhaust sideways as well as backwards, and the sideways part
    is wasted. 15 degrees costs about 1.7%; opening up to 20 costs 3%.
    """
    return (1.0 + math.cos(math.radians(half_angle_deg))) / 2.0


def gas_constant(molar_mass_g_mol: float) -> float:
    """Specific gas constant of the exhaust [J/(kg*K)]:  R_universal / M."""
    return R_UNIVERSAL / (molar_mass_g_mol / 1000.0)


# =============================================================================
#  S6. PERFORMANCE - what comes out of the back
#      [Humble Table 7.9 steps 5 and 7: F, Isp, and what is stored]
# =============================================================================

def thrust(thrust_coefficient_value: float, chamber_pressure_pa: float,
           throat_area_m2: float, divergence_efficiency: float = 1.0,
           nozzle_efficiency: float = 1.0) -> float:
    """Thrust [N] - the equation the whole file has been building towards.

        F = lambda * eta_nozzle * Cf * Pc * A_throat

    Chamber pressure times throat area is the raw scale of the motor; Cf is
    how much the nozzle multiplies it; the two efficiencies are what the real
    hardware gives back.
    """
    return max(0.0, divergence_efficiency * nozzle_efficiency
               * thrust_coefficient_value * chamber_pressure_pa * throat_area_m2)


def specific_impulse(thrust_n: float, mdot_total_kg_s: float) -> float:
    """Instantaneous Isp [s]:  F / (mdot * g0).

    How many seconds a kilogram of propellant can produce a kilogram of thrust.
    The single number for how good the propellant and nozzle are together.
    """
    if mdot_total_kg_s <= 1e-9:
        return 0.0
    return thrust_n / (mdot_total_kg_s * G0)


def total_impulse(times_s, thrusts_n) -> float:
    """Total impulse [N*s] - the area under the thrust curve, trapezoidally.

    This decides the motor's letter class, and with the propellant mass it
    gives the delivered Isp. If the curve does not start at t=0, the first
    point's thrust is held back to zero - a motor whose first sample is at
    0.12 s did not fire from nothing at that instant.
    """
    if not times_s or len(times_s) < 1:
        return 0.0
    impulse = times_s[0] * thrusts_n[0] if times_s[0] > 0 else 0.0
    for i in range(1, len(times_s)):
        dt = times_s[i] - times_s[i - 1]
        impulse += 0.5 * dt * (thrusts_n[i] + thrusts_n[i - 1])
    return impulse


def effective_exhaust_velocity(cstar_m_s: float,
                               thrust_coefficient_value: float) -> float:
    """Effective exhaust velocity [m/s]:  c* * Cf.

    The same thing Isp measures, in velocity rather than seconds. Splitting it
    this way separates the two halves of the problem: c* is the propellant and
    the combustion, Cf is the nozzle.
    """
    return cstar_m_s * thrust_coefficient_value


def port_to_throat_ratio(port_area_m2: float, throat_area_m2: float) -> float:
    """A_port / A_throat [-].

    Below about 2 the port starts choking before the nozzle does, and the grain
    becomes a second throat - which is not what anything was designed around.
    """
    if throat_area_m2 <= 0:
        return 0.0
    return port_area_m2 / throat_area_m2


def throat_mass_flux(mdot_total_kg_s: float, throat_area_m2: float) -> float:
    """Mass flux through the throat [kg/(m^2*s)] - what drives throat erosion."""
    if throat_area_m2 <= 0:
        return 0.0
    return mdot_total_kg_s / throat_area_m2


# =============================================================================
#  HUMBLE Table 7.9 - the simulation algorithm, written out
#
#  Humble, Henry & Larson, "Space Propulsion Analysis and Design",
#  Chapter 7 "Hybrid Rocket Propulsion Systems", Section 7.5 "Performance
#  Estimate", pp. 421-423. Table 7.9 gives the step order an engine
#  simulation should follow; the functions below are the steps that were
#  missing from this file, and the sections above are labelled with the step
#  each one serves.
#
#      1. Initialise            grain, oxidiser history, nozzle, regression
#                               parameters, throat erosion, ambient history,
#                               time step, thermochemistry
#      2. Fuel regression rate  m_dot_ox, A_p, rho, N  ->  G_ox, G, rdot
#      3. Total mass flow       rdot, S_p, rho, N, m_dot_ox
#                               ->  m_dot_fuel = rdot*rho*S_p*N
#                                   m_dot_prop = m_dot_fuel + m_dot_ox
#      4. Thermochemistry       O/F = m_dot_ox/m_dot_fuel -> T_c, gamma, M, c*
#      5. Chamber and nozzle    p_c = m_dot_prop*c*/A_t, M_e, p_e, v_e, A_e,
#                               F = lambda[m_dot_prop*v_e + (p_e-p_a)A_e],
#                               Isp = F/(m_dot_prop*g0)
#      6. Update geometry       w = w - rdot*dt, r_t = r_t + edot*dt,
#                               new A_p and S_p, t = t + dt
#      7. Store the outputs
#      8. Repeat from 2 until the burn is done
# =============================================================================


def regression_rate_humble(total_flux_kg_m2_s: float, a: float, n: float,
                           port_length_m: float = 1.0, m: float = 0.0) -> float:
    """Humble's full regression law (Table 7.9 step 2, and Eq. 7.101).

        rdot = a * G^n * L_p^m

    Three differences from the short form used elsewhere in this file, all of
    them Humble's:

    * the flux is the TOTAL mass flux G, oxidiser plus the fuel already
      picked up, not the oxidiser flux alone. Humble notes on p.423 that this
      is the harder choice - it is circular, because you need the fuel flow to
      get the flux and the flux to get the fuel flow - and gives the iteration
      that resolves it. See ``converge_total_flux`` below.
    * the port-length term L_p^m. Regression is not uniform down a port: the
      boundary layer thickens along it, so the fuel further down sees a
      different rate. m is measured with a; for many fuels it is reported as
      zero or near it, which collapses this to the familiar form.
    * ``a`` is defined against g0 in Humble's formulation, so it carries the
      units that make the product come out in m/s.

    With m = 0 and G = G_ox this is exactly ``regression_rate`` above, which
    is why both exist: the short one is what the vendored integrator calls,
    and this is the general form to reach for when a fuel's data includes m
    or when the total-flux law is wanted.
    """
    if total_flux_kg_m2_s <= 0 or a <= 0:
        return 0.0
    length_term = (port_length_m ** m) if m else 1.0
    return a * (total_flux_kg_m2_s ** n) * length_term


def converge_total_flux(mdot_ox_kg_s: float, port_area_m2: float,
                        burn_area_m2: float, fuel_density: float,
                        a: float, n: float,
                        port_length_m: float = 1.0, m: float = 0.0,
                        tolerance: float = 1e-4,
                        max_iterations: int = 50):
    """Humble's total-mass-flux iteration, p.423.

    The problem in his words: "We need to know fuel-mass flow to determine
    fuel regression, which is required to estimate fuel flow - a circular
    problem." His fix, verbatim in structure:

        1. Start by assuming the total flux is just the oxidiser flux
        2. Determine fuel regression and flow rate based on this flux
        3. Add the fuel flow to oxidiser flow to get a new port mass flux
        4. If the old guess is (nearly) the new guess, stop
        5. If not, iterate

    He calls it "very robust", and it is - the fuel flow is a fraction of the
    oxidiser flow, so each pass moves the answer by less than the last and it
    settles in six to eight passes at the fluxes a hybrid this size runs.

    THE COEFFICIENT IS NOT INTERCHANGEABLE. ``a`` and ``n`` measured against
    OXIDISER flux are not the same numbers as ``a`` and ``n`` measured against
    TOTAL flux, and swapping the law while keeping the old coefficient is a
    quiet way to build a motor that does not exist. Converged total flux runs
    40-100% above oxidiser flux alone in this range, so an oxidiser-flux
    coefficient used here inflates the regression rate and drives the O/F
    ratio far off where the fuel was characterised. Humble flags the choice
    on p.423 - "if we have chosen the regression-rate expression based only
    on oxidizer flow, flux is a given and there is no problem" - and the
    coefficient has to come from whichever expression is being used.

    Returns (total_flux, regression_rate, fuel_mass_flow, iterations).
    """
    if port_area_m2 <= 0 or mdot_ox_kg_s <= 0:
        return 0.0, 0.0, 0.0, 0

    flux = mdot_ox_kg_s / port_area_m2          # step 1: oxidiser flux alone
    rdot = 0.0
    mdot_fuel = 0.0
    for iteration in range(1, max_iterations + 1):
        # step 2
        rdot = regression_rate_humble(flux, a, n, port_length_m, m)
        mdot_fuel = fuel_density * burn_area_m2 * rdot
        # step 3
        new_flux = (mdot_ox_kg_s + mdot_fuel) / port_area_m2
        # step 4
        if abs(new_flux - flux) <= tolerance * max(new_flux, 1e-12):
            return new_flux, rdot, mdot_fuel, iteration
        flux = new_flux                          # step 5
    return flux, rdot, mdot_fuel, max_iterations


def port_diameter_after(initial_diameter_m: float, mdot_ox_kg_s: float,
                        elapsed_s: float, a: float, n: float,
                        port_length_m: float = 1.0, m: float = 0.0) -> float:
    """Port diameter at time t, Humble Eq. (7.102), p.421.

        D_p = [ a(4n+2)(4*m_dot_ox/pi)^n * L_p^m * t + D_pi^(2n+1) ]^(1/(2n+1))

    This is the closed-form integral of the regression law down a circular
    port, which Humble derives at Eq. (7.101) by separating D and t. It holds
    for single- and multi-port grains as long as the port is circular, and
    assumes the oxidiser mass flow and the port length are constant over the
    interval - so it is an analytic cross-check on a stepped integration
    rather than a replacement for one.

    ``mdot_ox_kg_s`` is the flow through ONE port, as in the book.
    """
    if elapsed_s <= 0 or a <= 0:
        return max(0.0, initial_diameter_m)
    exponent = 2.0 * n + 1.0
    length_term = (port_length_m ** m) if m else 1.0
    growth = (a * (4.0 * n + 2.0)
              * (4.0 * mdot_ox_kg_s / math.pi) ** n
              * length_term * elapsed_s)
    return (growth + initial_diameter_m ** exponent) ** (1.0 / exponent)


def chamber_pressure_humble(mdot_total_kg_s: float, cstar_m_s: float,
                            throat_area_m2: float) -> float:
    """Chamber pressure, Humble Table 7.9 step 5.

        p_c = m_dot_prop * c* / A_t

    The same relation as ``chamber_pressure_target`` above, written the way
    the book writes it. Both are here because this file is meant to be read
    against the source as well as run.
    """
    if throat_area_m2 <= 0:
        return 0.0
    return mdot_total_kg_s * cstar_m_s / throat_area_m2


def thrust_humble(mdot_total_kg_s: float, exit_velocity_m_s: float,
                  exit_pressure_pa: float, ambient_pressure_pa: float,
                  exit_area_m2: float, nozzle_efficiency: float = 1.0) -> float:
    """Thrust in Humble's momentum-plus-pressure form, Eq. (1.6) via Table 7.9.

        F = lambda * [ m_dot_prop * v_e + (p_e - p_a) * A_e ]

    The same number as the thrust-coefficient form used above - Cf is defined
    so that it comes out that way - but written with the two physical terms
    visible: the momentum the exhaust carries away, and the pressure
    difference acting over the exit plane. The second term is why a nozzle
    that is right at sea level is wrong at altitude.

    ``lambda`` is the nozzle efficiency, the lumped factor for divergence,
    friction and the rest of the losses between the ideal and the real
    nozzle. Humble carries it as an input to the step rather than folding it
    into Cf.
    """
    momentum = mdot_total_kg_s * exit_velocity_m_s
    pressure = (exit_pressure_pa - ambient_pressure_pa) * exit_area_m2
    return max(0.0, nozzle_efficiency * (momentum + pressure))


def exit_velocity(exit_mach_number: float, gamma: float,
                  chamber_temperature_k: float,
                  molar_mass_g_mol: float) -> float:
    """Nozzle exit velocity [m/s], Humble Table 7.9 step 5 (his Eq. 3.112).

        v_e = M_e * sqrt( gamma * R * T_e ),
        T_e = T_c / (1 + (gamma-1)/2 * M_e^2)

    The exit static temperature follows from the chamber temperature by the
    isentropic relation, and the speed of sound is evaluated at THAT
    temperature - not the chamber's. Using the chamber value here is a common
    slip and overstates the exhaust speed by a third or more at the area
    ratios a hybrid runs.
    """
    if exit_mach_number <= 0 or chamber_temperature_k <= 0:
        return 0.0
    r_specific = gas_constant(molar_mass_g_mol)
    t_exit = chamber_temperature_k / (1.0 + 0.5 * (gamma - 1.0)
                                      * exit_mach_number ** 2)
    return exit_mach_number * math.sqrt(gamma * r_specific * t_exit)


def specific_impulse_humble(thrust_n: float, mdot_total_kg_s: float) -> float:
    """Isp [s], Humble Table 7.9 step 5:  Isp = F / (m_dot_prop * g0)."""
    if mdot_total_kg_s <= 0:
        return 0.0
    return thrust_n / (mdot_total_kg_s * G0)


def update_web_and_throat(web_m: float, throat_radius_m: float,
                          regression_rate_m_s: float,
                          throat_erosion_rate_m_s: float,
                          dt_s: float):
    """Humble Table 7.9 step 6 - march the geometry forward one step.

        w   = w   - rdot * dt        the fuel web gets thinner
        r_t = r_t + edot * dt        the throat opens up

    Humble flags in the text that the web is the easy half: relating it back
    to the port cross-section needs the port's own geometry, which is why
    step 6 asks for "new port geometry (A_p, S_p)" rather than a formula.
    For a circular port that is just the area and surface of the new radius,
    which ``port_area`` and ``burn_area`` above already give.

    Throat erosion matters for an ablative nozzle: as the throat opens, the
    chamber pressure and the thrust both fall away through the burn even with
    the oxidiser flow held steady.

    Returns (web, throat_radius), both floored at zero.
    """
    return (max(0.0, web_m - regression_rate_m_s * dt_s),
            max(0.0, throat_radius_m + throat_erosion_rate_m_s * dt_s))

