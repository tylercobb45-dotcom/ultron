"""Size a complete hybrid motor from a requirements brief.

You say what the motor has to DO - total impulse, average thrust, burn time,
a minimum Isp - and how much room it has to do it in - maximum outside
diameter and maximum overall length. This module returns a whole motor: tank
bore and length, the nitrous mass that fits in it, injector hole pattern,
fuel grain, chamber, nozzle, and the materials and wall thicknesses those
pressures need. Every number it produces lands in the ordinary Engine Lab
fields, so nothing here is a black box you cannot then edit by hand.

HOW IT WORKS

Two stages, because neither alone is any good:

1.  An ANALYTIC SEED from the Humble relations in ``engine_equations`` - the
    same module the rest of the app runs on. Propellant mass from the impulse
    and Isp, the oxidiser/fuel split from the c* peak, tank volume from the
    nitrous density at the fill temperature, port diameter from a sensible
    oxidiser flux, grain length from the Marxman regression law at the target
    O/F, throat area from mass flow and chamber pressure, expansion ratio from
    the pressure the nozzle should expand to, injector area from the Dyer
    model inverted at the design point.

2.  REFINEMENT AGAINST THE REAL SIMULATOR. The seed is a steady-state
    approximation and the motor is not steady: the tank blows down, it cools,
    the port opens up and the O/F walks across the burn. So the seed is then
    run through ``EngineModel`` - the very same solver the Run button uses -
    and corrected on what actually comes out. A design is never reported as
    meeting a requirement because the closed form said so; it is reported as
    meeting it because the simulation did.

The levers are chosen so they mostly do not fight each other:

    throat diameter   -> chamber pressure
    injector area     -> oxidiser flow, and so thrust
    grain length      -> fuel flow, and so O/F
    tank length       -> oxidiser mass, and so burn time and total impulse
    expansion ratio   -> Isp

WHEN IT CANNOT WIN

The envelope is a hard wall, not a preference. If the required propellant
simply does not fit in the tube you have, no amount of iterating invents
volume. In that case the designer returns the best motor that DOES fit and
says plainly which requirement it had to give up and by how much. A sizing
tool that quietly relaxes the brief and reports success is worse than useless
on a vehicle someone is going to stand next to.

Nothing in here imports Qt, so it can be exercised headlessly.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field

import portable_paths

# hybrid_sim is a sibling package, imported through a path insert so it can be
# dropped in or updated on its own. bundled_dir already knows where it lands
# running from source and inside a frozen build, so use it rather than a
# second copy of that logic.
_HYBRID_SIM_ROOT = portable_paths.bundled_dir('hybrid_sim')
if os.path.isdir(_HYBRID_SIM_ROOT) and _HYBRID_SIM_ROOT not in sys.path:
    sys.path.insert(0, _HYBRID_SIM_ROOT)

import engine_equations as eq          # noqa: E402
import materials as materials_mod      # noqa: E402
from hybrid_sim.config import (        # noqa: E402
    Engine, FUELS, CSTAR_OF, CSTAR_VAL, INJECTOR_TYPES)
from hybrid_sim.engine import EngineModel          # noqa: E402
from hybrid_sim.metrics import metrics as hs_metrics  # noqa: E402

G0 = 9.80665


# --- what you are asking the motor for -----------------------------------

@dataclass
class Requirements:
    """The brief. Zero means "no requirement", not "requires zero".

    That distinction matters: a blank thrust box has to mean "size it for
    whatever the impulse and burn time imply", never "design a motor that
    produces no thrust".
    """
    total_impulse_ns: float = 0.0
    avg_thrust_n: float = 0.0
    burn_time_s: float = 0.0
    min_isp_s: float = 0.0
    max_peak_thrust_n: float = 0.0
    max_chamber_pressure_pa: float = 0.0
    max_diameter_m: float = 0.0
    max_length_m: float = 0.0
    fuel: str = ""                 # "" = let the designer pick
    injector: str = ""             # "" = let the designer pick
    tank_temp_k: float = 293.0
    ambient_pa: float = 101325.0
    structural_sf: float = 2.0     # on pressure-vessel yield


@dataclass
class Compliance:
    """One line of the requirement-vs-delivered table."""
    label: str
    required: str
    achieved: str
    met: bool
    note: str = ""


@dataclass
class DesignResult:
    engine_fields: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    compliance: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    materials: dict = field(default_factory=dict)
    envelope: dict = field(default_factory=dict)
    fuel_name: str = "HTPB"
    injector_name: str = "Showerhead"
    runs: int = 0

    @property
    def met_all(self) -> bool:
        return all(c.met for c in self.compliance)


# --- small helpers --------------------------------------------------------

def _dia(area_m2: float) -> float:
    return math.sqrt(max(0.0, 4.0 * area_m2 / math.pi))


def _area(diameter_m: float) -> float:
    return math.pi * (diameter_m * 0.5) ** 2


def peak_cstar_of(fuel) -> float:
    """The O/F this fuel makes the most chamber pressure at.

    Scanned off the same CEA table the solver interpolates, rather than
    hard-coded, so a fuel with an of_shift gets its own answer instead of
    HTPB's. Running at the c* peak is the standard starting point: it is the
    mixture that turns the most mass flow into the most pressure.
    """
    best_of, best_c = 6.0, -1.0
    of = 1.0
    while of <= 12.0001:
        c = eq.characteristic_velocity(of, CSTAR_OF, CSTAR_VAL,
                                       fuel.of_shift, fuel.cstar_scale)
        if c > best_c:
            best_of, best_c = of, c
        of += 0.05
    return best_of


def _cstar_at(fuel, of_ratio: float) -> float:
    return eq.characteristic_velocity(of_ratio, CSTAR_OF, CSTAR_VAL,
                                      fuel.of_shift, fuel.cstar_scale)


def _expansion_for_exit_pressure(pc_pa: float, pe_pa: float,
                                 gamma: float) -> float:
    """Area ratio that expands pc down to pe.

    Isentropic exit Mach from the pressure ratio, then the area ratio that
    Mach needs. Kept in terms of pressure because that is what decides whether
    the bell separates, which is the failure that actually bites at sea level.
    """
    if pe_pa <= 0 or pc_pa <= pe_pa:
        return 1.0001
    g = gamma
    m2 = (2.0 / (g - 1.0)) * ((pc_pa / pe_pa) ** ((g - 1.0) / g) - 1.0)
    mach = math.sqrt(max(0.0, m2))
    if mach <= 1.0:
        return 1.0001
    return eq.area_ratio_from_mach(mach, g)


# --- pressure parts: what they are made of, and how thick -----------------

# Below this a wall is not really manufacturable in a hobby/university shop -
# you cannot reliably machine or weld it, and the hoop-stress number stops
# meaning anything because a scratch is a large fraction of the section.
MIN_WALL_M = 0.0010


def wall_thickness(pressure_pa: float, inner_radius_m: float,
                   material, safety_factor: float) -> float:
    """Thin-wall hoop-stress thickness, floored at what can be made.

        t = P * r / sigma_allow,   sigma_allow = yield / SF

    Thin-wall (Barlow) rather than Lame: at these radii and pressures t/r
    stays under about a tenth, where the thin-wall answer is within a few
    percent and is the form every tank drawing is dimensioned from anyway.
    """
    allow = material.yield_pa / max(1.0, safety_factor)
    if allow <= 0:
        return MIN_WALL_M
    return max(MIN_WALL_M, pressure_pa * inner_radius_m / allow)


# Pressure-vessel materials the designer will actually recommend. Deliberately
# metals only, although materials.PRESSURE also lists carbon/epoxy: on specific
# strength a composite wins every time, and at these pressures it wins by
# landing on the minimum-gauge floor, which is not really a design at all. A
# filament-wound N2O tank is a genuine option, but it is a COPV - it needs a
# liner, a wind schedule and proof testing, and recommending one to a team who
# asked for a motor would be handing them a vessel they cannot verify. The
# note offers it as an upgrade instead.
TANK_MATERIALS = ("Aluminum 6061-T6", "Aluminum 7075-T6",
                  "Stainless Steel 304", "Titanium Ti-6Al-4V")

# Sizing temperature for the oxidiser tank, regardless of the fill temperature
# asked for. N2O is self-pressurising, so tank pressure IS its saturation
# pressure, and a tank that sat in the sun before the countdown is hotter than
# the one that was filled. 308 K (35 C) is an ordinary summer pad temperature
# and puts the tank near 7 MPa instead of the 5 MPa a 293 K fill suggests. The
# flight pressure is not the design pressure.
HOT_PAD_K = 308.15


def tank_design_pressure(fill_temp_k: float) -> float:
    """What the tank wall has to hold [Pa].

    Used by the bore calculation and by the material report, from one place on
    purpose: sizing the bore off the fill pressure and the wall off the hot-pad
    pressure would quietly grow the assembled motor past the envelope it was
    just checked against.
    """
    return eq.n2o_saturation_pressure(max(fill_temp_k, HOT_PAD_K))


def pressure_material_options(pressure_pa: float, inner_radius_m: float,
                              safety_factor: float, candidates=None) -> list:
    """Every candidate material for this duty, lightest wall first.

    Lightest by the MASS of the wall it needs, not by density: a dense alloy
    with four times the strength carries the same pressure in a quarter of the
    wall and wins. Mass per unit length goes as rho * t, with the
    minimum-gauge floor applied first - below it the extra strength buys
    nothing, because the thickness cannot shrink any further.

    The whole list is returned, not just the winner. On specific strength the
    answer is usually titanium or 7075, and at these pressures it is often
    sitting on the minimum-gauge floor where the ranking stops meaning much -
    while the material a team can actually cut, weld and inspect may be two
    rows down. That is the user's call to make, so they get to see the rows.
    """
    if candidates is None:
        candidates = {n: materials_mod.PRESSURE[n] for n in TANK_MATERIALS
                      if n in materials_mod.PRESSURE}
    pool = candidates or materials_mod.PRESSURE
    out = []
    for name, mat in pool.items():
        t = wall_thickness(pressure_pa, inner_radius_m, mat, safety_factor)
        out.append({"name": name, "wall_m": t,
                    "mass_per_m": mat.density * t * 2.0 * math.pi
                    * (inner_radius_m + t / 2.0),
                    "at_min_gauge": t <= MIN_WALL_M * 1.0001,
                    "note": mat.note})
    out.sort(key=lambda o: o["mass_per_m"])
    return out


def choose_pressure_material(pressure_pa: float, inner_radius_m: float,
                             safety_factor: float, candidates=None):
    """The lightest pressure-vessel material for this duty, as (name, mat, t)."""
    best = pressure_material_options(
        pressure_pa, inner_radius_m, safety_factor, candidates)[0]
    return best["name"], materials_mod.get(best["name"]), best["wall_m"]


def select_materials(chamber_pa: float, tank_pa: float,
                     chamber_r: float, tank_r: float,
                     burn_time_s: float, safety_factor: float,
                     fill_temp_k: float = 293.0) -> dict:
    """Tank, chamber and throat materials with the walls they imply."""
    # The tank is sized for the hottest it will plausibly get, not for the
    # temperature it was filled at - see HOT_PAD_K.
    tank_design_pa = max(tank_pa, tank_design_pressure(fill_temp_k))
    tank_opts = pressure_material_options(tank_design_pa, tank_r, safety_factor)
    cham_opts = pressure_material_options(chamber_pa, chamber_r, safety_factor)
    tank_name, tank_mat, tank_t = (tank_opts[0]["name"],
                                   materials_mod.get(tank_opts[0]["name"]),
                                   tank_opts[0]["wall_m"])
    cham_name, cham_mat, cham_t = (cham_opts[0]["name"],
                                   materials_mod.get(cham_opts[0]["name"]),
                                   cham_opts[0]["wall_m"])
    # The throat is a temperature problem, not a pressure one. An N2O/HTPB
    # flame runs around 3000 K, which is above the melting point of every
    # metal in the table - so the choice is between something that survives it
    # (graphite, which sublimes rather than melts) and something designed to
    # be eaten (phenolic ablative, sized by burn duration). Long burns eat a
    # lot of ablative, so graphite is the default and the note says why.
    throat_name = "Graphite"
    throat_note = ("Graphite: sublimes rather than melts, so it survives a "
                   "~3000 K flame. Expect some throat erosion - set the "
                   "erosion rate field if you have measured it.")
    if burn_time_s <= 6.0:
        throat_note += (" A phenolic ablative throat is also viable at this "
                        "burn time and is cheaper to make.")
    return {
        "tank": {"name": tank_name, "wall_m": tank_t,
                 "pressure_pa": tank_design_pa,
                 "options": tank_opts,
                 "note": f"{tank_mat.note} Wall sized for "
                         f"{tank_design_pa/1e6:.2f} MPa at SF "
                         f"{safety_factor:.1f} - the saturation pressure on a "
                         f"{max(fill_temp_k, HOT_PAD_K) - 273.15:.0f} C pad, "
                         f"not the {tank_pa/1e6:.2f} MPa of the fill "
                         f"temperature. A filament-wound composite tank is "
                         f"lighter still, but it is a COPV and needs a liner "
                         f"and proof testing."},
        "chamber": {"name": cham_name, "wall_m": cham_t,
                    "pressure_pa": chamber_pa, "options": cham_opts,
                    "note": f"{cham_mat.note} Wall sized for "
                            f"{chamber_pa/1e6:.2f} MPa at SF "
                            f"{safety_factor:.1f} - PRESSURE ONLY. A hybrid "
                            f"chamber also has to survive the flame, so this "
                            f"wall assumes a liner (phenolic or a cast "
                            f"insulator) between it and the gas. Bare metal "
                            f"at this thickness will not last the burn."},
        "nozzle": {"name": throat_name, "wall_m": 0.0,
                   "pressure_pa": chamber_pa, "note": throat_note},
    }


# --- how long the finished motor actually is ------------------------------

# Hardware that is not tank, grain or bell but still eats length. Expressed in
# calibres of the part it bolts to, because that is how it scales: a bigger
# motor needs a bigger dome and a thicker bulkhead, not a fixed 40 mm.
FWD_HARDWARE_CAL = 0.60      # tank dome, fill/vent plumbing, forward closure
BULKHEAD_CAL = 0.40          # injector bulkhead between tank and chamber


def motor_length(eng: Engine, case_id_m: float) -> dict:
    """Overall length of the assembled motor, itemised.

    Itemised rather than a single number because when a design does not fit,
    the useful thing to be told is WHICH part is eating the tube.
    """
    conv = math.radians(max(1.0, eng.beta_conv_deg))
    div = math.radians(max(1.0, eng.alpha_deg))
    l_conv = max(0.0, (case_id_m - eng.d_throat) / (2.0 * math.tan(conv)))
    l_div = max(0.0, (eng.d_exit - eng.d_throat) / (2.0 * math.tan(div)))
    parts = {
        "forward hardware": FWD_HARDWARE_CAL * eng.d_tank,
        "oxidiser tank": eng.L_tank,
        "injector bulkhead": BULKHEAD_CAL * case_id_m,
        "pre-combustion chamber": eng.L_pre,
        "fuel grain": eng.L_grain,
        "post-combustion chamber": eng.L_post,
        "nozzle": l_conv + l_div,
    }
    parts["TOTAL"] = sum(parts.values())
    return parts


# --- the analytic seed ----------------------------------------------------

# Oxidiser mass flux through the port at ignition, kg/m^2/s. The usual hybrid
# design window is roughly 150-700: below it the grain runs fuel-lean and the
# flame is lazy, above it the boundary layer blows off the wall and the motor
# both erodes and goes unstable. Mid-window is where a first cut belongs.
DESIGN_G_OX = 350.0

# Chamber pressure as a fraction of the tank's own saturation pressure. N2O
# has no pump and no regulator: whatever the tank is at is all the pressure
# there will ever be, so the chamber has to sit far enough below it that the
# injector keeps authority. 0.70 leaves about 43% injector stiffness at
# ignition, which decays as the tank blows down and cools - a design that
# starts at the usual 20-30% target is already chugging by mid-burn.
PC_OVER_PTANK = 0.70

# Exit pressure as a fraction of ambient. Slightly over-expanded: the motor
# spends most of its impulse above the pad, so expanding a little past sea
# level buys Isp up high. Not below 0.4 - that is where Summerfield says the
# flow tears off the wall and the extra bell stops working.
PE_OVER_PA = 0.60

# Summerfield's separation criterion: below Pe = 0.4*Pa the flow leaves the
# wall. The sizing target above (0.60) sits above it on purpose - this is the
# hard floor the search is not allowed to expand past, not the design point.
SEPARATION_MARGIN = 0.40

# Pre- and post-combustion chamber lengths, in calibres of the grain OD.
# The post chamber is the larger of the two on purpose: hybrids leave
# fuel-rich streaks coming off the grain and that volume is where they finish
# burning. Skimping on it is a direct c* efficiency loss.
L_PRE_CAL = 0.50
L_POST_CAL = 0.75


def resolve_duty(req: Requirements) -> tuple:
    """Total impulse, average thrust and burn time, made self-consistent.

    Any two of the three fix the third, since I = F * t. Which two you were
    given decides what gets derived; giving all three and having them disagree
    is a contradiction the caller has to hear about rather than have silently
    resolved in its favour.
    """
    notes = []
    it, fa, tb = req.total_impulse_ns, req.avg_thrust_n, req.burn_time_s
    if it > 0 and fa > 0 and tb > 0:
        implied = fa * tb
        if abs(implied - it) / it > 0.02:
            notes.append(
                f"Impulse, thrust and burn time disagree: {fa:.0f} N for "
                f"{tb:.2f} s is {implied:.0f} N.s, not {it:.0f} N.s. Total "
                f"impulse and thrust were kept; burn time became "
                f"{it / fa:.2f} s.")
        tb = it / fa
    elif it > 0 and fa > 0:
        tb = it / fa
    elif it > 0 and tb > 0:
        fa = it / tb
    elif fa > 0 and tb > 0:
        it = fa * tb
    elif it > 0:
        # Only an impulse. Burn time has to come from somewhere, and bigger
        # motors do burn longer, so scale with the square root of impulse and
        # keep it inside what a hybrid of this size plausibly does. Stated in
        # the notes because it is an assumption, not a requirement.
        tb = min(25.0, max(3.0, 2.5 * math.sqrt(it / 1000.0)))
        fa = it / tb
        notes.append(
            f"No burn time or thrust given, so the motor was sized for "
            f"{tb:.1f} s at {fa:.0f} N average. Set either one to change it.")
    else:
        raise ValueError(
            "Give at least a total impulse, or an average thrust and a burn "
            "time - there is nothing to size the motor against otherwise.")
    return it, fa, tb, notes


def seed_engine(req: Requirements, fuel_name: str, injector_name: str,
                duty: tuple) -> tuple:
    """A first complete motor, from the closed-form relations.

    Good enough to be worth simulating, not good enough to be trusted: it
    assumes a steady tank and a fixed O/F, and the real motor has neither.
    Returns (Engine, working dict of the intermediate numbers, notes).
    """
    it, fa, tb, _ = duty
    notes = []
    fuel = FUELS[fuel_name]
    cd_inj = INJECTOR_TYPES.get(injector_name, (0.7, ""))[0]

    gamma, mw = 1.22, 26.0
    eta_cstar, eta_nozzle = 0.90, 0.95
    alpha_deg, beta_deg = 15.0, 30.0

    # Pressures. The tank sets the ceiling and everything else hangs off it.
    p_tank = eq.n2o_saturation_pressure(req.tank_temp_k)
    pc = PC_OVER_PTANK * p_tank
    if req.max_chamber_pressure_pa > 0:
        pc = min(pc, req.max_chamber_pressure_pa)

    # Mixture. Sitting on the c* peak is the standard first cut.
    of = peak_cstar_of(fuel)
    cstar = _cstar_at(fuel, of)

    # Nozzle, so there is an Isp to size the propellant load against.
    eps = _expansion_for_exit_pressure(pc, PE_OVER_PA * req.ambient_pa, gamma)
    me = eq.exit_mach(eps, gamma)
    cf = eq.thrust_coefficient(pc, req.ambient_pa, eps, me, gamma)
    lam = eq.divergence_loss(alpha_deg)
    isp_est = max(80.0, eta_cstar * cstar * cf * lam * eta_nozzle / G0)

    # Propellant, and the split between the two tanks it lives in.
    m_prop = it / (G0 * isp_est)
    m_ox = m_prop * of / (1.0 + of)
    m_fuel = m_prop - m_ox
    mdot_ox = m_ox / tb
    mdot_tot = m_prop / tb

    # Pressure parts, so the envelope is measured on real bores rather than
    # on the outside diameter as though the walls were free.
    sf = req.structural_sf
    od = req.max_diameter_m if req.max_diameter_m > 0 else 0.0
    if od > 0:
        # Thickness depends on the bore and the bore depends on thickness, so
        # take one pass off the outside diameter and settle it.
        p_tank_design = tank_design_pressure(req.tank_temp_k)
        r_guess = od / 2.0
        for _ in range(4):
            _, _, t_tank = choose_pressure_material(p_tank_design, r_guess, sf)
            r_guess = od / 2.0 - t_tank
            if r_guess <= MIN_WALL_M:
                break
        d_tank = max(4 * MIN_WALL_M, 2.0 * r_guess)
        r_guess = od / 2.0
        for _ in range(4):
            _, _, t_case = choose_pressure_material(pc, r_guess, sf)
            r_guess = od / 2.0 - t_case
            if r_guess <= MIN_WALL_M:
                break
        case_id = max(4 * MIN_WALL_M, 2.0 * r_guess)
    else:
        # No envelope given: let the tank take a slenderness typical of a
        # flight motor rather than growing without limit.
        v_needed = m_ox / max(1e-6, _ox_density(req.tank_temp_k, 0.85))
        d_tank = (v_needed * 4.0 / (math.pi * 6.0)) ** (1.0 / 3.0)
        case_id = d_tank
        notes.append("No maximum diameter given, so the tank was given a "
                     "6:1 length-to-diameter ratio.")

    # Tank: volume for the oxidiser, length for the volume.
    fill = 0.85
    v_tank = m_ox / max(1e-9, _ox_density(req.tank_temp_k, fill))
    l_tank = v_tank / max(1e-9, _area(d_tank))

    # Port: start it where the oxidiser flux is in the good window.
    a_port = mdot_ox / DESIGN_G_OX
    d_port = _dia(a_port)

    # Grain length, from the Marxman law at the design point. Fuel flow is
    # rho * A_burn * rdot, and A_burn is what the length buys, so:
    #     L = mdot_fuel / (rho * 2*pi*r_port * rdot)
    g_ox = mdot_ox / max(1e-9, a_port)
    rdot = eq.regression_rate(g_ox, fuel.a, fuel.n)
    mdot_fuel = mdot_ox / of
    l_grain = mdot_fuel / max(1e-12, fuel.rho * 2.0 * math.pi
                              * (d_port / 2.0) * rdot)

    # Web: enough fuel wall to last the burn, with margin, but never more
    # than the case can hold. Burning through the web ends the burn early no
    # matter what the tank still has in it.
    web = rdot * tb * 1.25
    d_outer = d_port + 2.0 * web
    if case_id > 0 and d_outer > case_id:
        d_outer = case_id
        notes.append(
            f"Grain outside diameter was capped at the case bore "
            f"({case_id * 1000:.1f} mm).")

    # Throat, from the flow it has to pass at the chamber pressure it has to
    # hold. This is the most sensitive dimension in the motor.
    a_throat = mdot_tot * cstar * eta_cstar / max(1e3, pc)
    d_throat = _dia(a_throat)

    # Cap the bell at the airframe. An exit cone wider than the tube is not a
    # design, it is a drawing error.
    if od > 0:
        eps_max = (od / max(1e-9, d_throat)) ** 2
        if eps > eps_max:
            eps = max(1.0001, eps_max)
            notes.append(
                f"Expansion ratio was capped at {eps:.2f} to keep the exit "
                f"cone inside {od * 1000:.0f} mm.")

    # Injector. Flow is linear in Cd*A for both limbs of the Dyer blend, so
    # one call at unit area gives the constant and the area falls straight
    # out - no search needed.
    per_area, _ = eq.injector_mass_flow(1.0, req.tank_temp_k, pc)
    cda = mdot_ox / max(1e-9, per_area)
    a_inj = cda / max(0.05, cd_inj)
    n_holes, d_hole = _hole_pattern(a_inj, injector_name)

    eng = Engine(
        d_tank=d_tank, L_tank=l_tank, fill_frac=fill, T_tank_0=req.tank_temp_k,
        d_vent=0.0, Cd_vent=0.65, cooling_coeff=0.0,
        inj_type=injector_name, n_holes=n_holes, d_hole=d_hole, Cd_inj=cd_inj,
        fuel=fuel, L_grain=l_grain, d_grain_outer=d_outer, d_port_0=d_port,
        n_ports=1, L_pre=L_PRE_CAL * d_outer, L_post=L_POST_CAL * d_outer,
        d_throat=d_throat, eps_exp=eps, alpha_deg=alpha_deg,
        beta_conv_deg=beta_deg, erosion_rate=0.0,
        eta_cstar=eta_cstar, eta_nozzle=eta_nozzle, gamma=gamma, MW=mw)

    work = dict(pc=pc, p_tank=p_tank, of=of, cstar=cstar, isp_est=isp_est,
                m_prop=m_prop, m_ox=m_ox, m_fuel=m_fuel, case_id=case_id,
                mdot_ox=mdot_ox, mdot_tot=mdot_tot, cd_inj=cd_inj)
    return eng, work, notes


def _ox_density(temp_k: float, fill_frac: float) -> float:
    """Mass of nitrous per unit of TANK volume at this fill [kg/m^3].

    Not the liquid density: the ullage is full of saturated vapour, which at
    293 K is about 160 kg/m^3 and is emphatically not nothing. Sizing a tank
    on liquid density alone overfills it.
    """
    return (fill_frac * eq.n2o_liquid_density(temp_k)
            + (1.0 - fill_frac) * eq.n2o_vapour_density(temp_k))


def _hole_pattern(area_m2: float, injector_name: str) -> tuple:
    """Hole count and diameter for a required total injector area.

    Holes want to land between about 1 and 3 mm: under a millimetre they
    block on a speck of debris and drill wander is a large fraction of the
    diameter, over three the spray is coarse and mixing suffers. A single
    orifice injector gets one hole whatever that implies, because that is
    what the hardware is.
    """
    if injector_name == "Single orifice":
        return 1, _dia(area_m2)
    for n in range(1, 200):
        d = _dia(area_m2 / n)
        if d <= 0.003:
            # Do not go below a millimetre chasing the upper bound.
            if d < 0.001 and n > 1:
                n -= 1
                d = _dia(area_m2 / n)
            return n, d
    return 199, _dia(area_m2 / 199)


# --- refinement against the real simulator --------------------------------

# How hard to push each correction. A full-strength multiplicative step
# overshoots, because the levers are only approximately independent - opening
# the injector raises chamber pressure as well as thrust. Under-relaxing
# trades a few more runs for not ringing.
DAMPING = 0.65

# Fractional error at or under which a requirement counts as met. Tighter than
# this is meaningless and worse than useless: the c* table is interpolated
# from a couple of dozen CEA points, the regression coefficients are
# literature averages for a whole fuel family, and the c* efficiency is an
# assumption. Reporting a 2% miss on a number carried to 3 significant figures
# invites someone to trust the third one.
TOLERANCE = 0.03

MAX_SWEEPS = 22


def _clone(eng: Engine, **changes) -> Engine:
    """A copy of an engine with some dimensions changed.

    dataclasses.replace would do it, but Engine carries a Fuel object and a
    handful of derived properties, and being explicit about which fields the
    designer is allowed to touch keeps the search from quietly editing
    something like the gas properties.
    """
    keys = ("d_tank", "L_tank", "fill_frac", "T_tank_0", "d_vent", "Cd_vent",
            "cooling_coeff", "inj_type", "n_holes", "d_hole", "Cd_inj",
            "fuel", "fuel_a", "fuel_n", "L_grain", "d_grain_outer",
            "d_port_0", "n_ports", "L_pre", "L_post", "d_throat", "eps_exp",
            "alpha_deg", "beta_conv_deg", "erosion_rate", "eta_cstar",
            "eta_nozzle", "gamma", "MW")
    kwargs = {k: getattr(eng, k) for k in keys}
    kwargs.update(changes)
    return Engine(**kwargs)


def _evaluate(eng: Engine, ambient_pa: float):
    """Run the motor. None if it will not run at all."""
    try:
        m = hs_metrics(EngineModel(eng, Pa=ambient_pa).run())
    except Exception:
        return None
    if not m or m.get("peak_thrust", 0) <= 0 or m.get("burn_time", 0) <= 0:
        return None
    return m


def _targets(m: dict, req: Requirements, duty: tuple) -> list:
    """Fractional miss on each requirement that was actually given.

    Only the boxes the user filled in: a design must not be rewarded for
    nailing a requirement nobody stated.
    """
    it, fa, tb, _ = duty
    errs = []
    if req.total_impulse_ns > 0:
        errs.append(abs(m["total_impulse"] - it) / it)
    if req.avg_thrust_n > 0:
        errs.append(abs(m["avg_thrust"] - fa) / fa)
    if req.burn_time_s > 0:
        errs.append(abs(m["burn_time"] - tb) / tb)
    if req.min_isp_s > 0 and m["isp"] < req.min_isp_s:
        errs.append((req.min_isp_s - m["isp"]) / req.min_isp_s)
    return errs


def _violation(m: dict, req: Requirements) -> float:
    """How far this motor is OVER its ceilings, as a fraction. 0 = legal.

    Ceilings are kept apart from targets because they are not the same kind of
    thing. Missing a target by 5% is a worse motor; exceeding a structural
    limit by 5% is a motor that breaks something. A design that busts a
    ceiling must never win on a better average, however good the rest of it
    looks - so feasibility is checked first and the score only separates
    designs that are already legal.
    """
    worst = 0.0
    if req.max_peak_thrust_n > 0 and m["peak_thrust"] > req.max_peak_thrust_n:
        worst = max(worst, (m["peak_thrust"] - req.max_peak_thrust_n)
                    / req.max_peak_thrust_n)
    if req.max_chamber_pressure_pa > 0 \
            and m["peak_Pc"] > req.max_chamber_pressure_pa:
        worst = max(worst, (m["peak_Pc"] - req.max_chamber_pressure_pa)
                    / req.max_chamber_pressure_pa)
    return worst


def _score(m: dict, req: Requirements, duty: tuple) -> float:
    """How far this motor is from the brief - lower is better.

    The WORST single miss, not the average of them. An average lets a design
    bury one badly missed requirement under three it happened to nail, and
    then the loop stops because the mean looks converged while the compliance
    table - which grades each requirement on its own - still says MISS. Same
    measure in both places, so "converged" and "meets the brief" cannot
    disagree.
    """
    errs = _targets(m, req, duty)
    return max(errs) if errs else 0.0


def _fit_envelope(eng: Engine, req: Requirements, work: dict) -> tuple:
    """Pull a design back inside the tube it has to live in.

    Length is taken out of the tank, because the tank is the only part whose
    length is pure volume - shortening the grain changes the mixture ratio and
    shortening the bell throws away Isp. Returns the engine and what had to
    give, if anything.
    """
    gave = []
    if req.max_length_m <= 0:
        return eng, gave
    parts = motor_length(eng, work["case_id"])
    over = parts["TOTAL"] - req.max_length_m
    if over <= 0:
        return eng, gave
    new_tank = eng.L_tank - over
    if new_tank < 0.05:
        # Even a zero-length tank does not fit: the fixed hardware, grain and
        # bell alone are longer than the envelope.
        new_tank = 0.05
        gave.append(
            f"The grain, chamber and nozzle alone are longer than the "
            f"{req.max_length_m * 1000:.0f} mm allowed, so the tank was cut "
            f"to its minimum and the motor still does not fit.")
    else:
        gave.append(
            f"Tank shortened by {over * 1000:.0f} mm to fit "
            f"{req.max_length_m * 1000:.0f} mm, which costs oxidiser.")
    return _clone(eng, L_tank=new_tank), gave


def refine(eng: Engine, req: Requirements, duty: tuple, work: dict,
           progress=None) -> tuple:
    """Walk the seed onto the brief using the real engine solver.

    Returns (best engine, its metrics, run count, notes).
    """
    it, fa, tb, _ = duty
    of_target = work["of"]
    pc_target = work["pc"]
    if req.max_chamber_pressure_pa > 0:
        pc_target = min(pc_target, req.max_chamber_pressure_pa)

    notes = []
    best_eng, best_m, best_key = None, None, None
    runs = 0
    eps_capped = False
    stalled = 0

    for sweep in range(MAX_SWEEPS):
        if progress is not None:
            progress(sweep / float(MAX_SWEEPS),
                     f"Simulating candidate {sweep + 1}...")
        m = _evaluate(eng, req.ambient_pa)
        runs += 1
        if m is None:
            # The step went somewhere the motor will not light. Fall back to
            # the best design so far rather than carrying on from a corpse.
            if best_eng is None:
                break
            eng = best_eng
            continue

        # Feasibility first, then closeness. Sorting on the pair means a
        # design that breaks a ceiling can only ever win against another that
        # breaks it worse, never against a legal one.
        s = _score(m, req, duty)
        key = (_violation(m, req), s)
        if best_key is None or key < best_key:
            best_eng, best_m, best_key = eng, m, key
            stalled = 0
        else:
            stalled += 1
        if key[0] <= 0.0 and s <= TOLERANCE:
            break
        # Nothing has improved for several sweeps. Either the brief cannot be
        # met or the steps are ringing around the answer; in both cases more
        # sweeps cost the user seconds and return the same motor. The
        # compliance table will say which requirement was missed.
        if stalled >= 4:
            break

        # --- work out this sweep's corrections --------------------------
        # Injector area sets oxidiser flow, and thrust follows it almost
        # linearly. This is the lever that moves thrust.
        k_inj = 1.0
        if req.avg_thrust_n > 0:
            k_inj = (fa / max(1e-6, m["avg_thrust"])) ** DAMPING
        elif req.burn_time_s > 0:
            # No thrust requirement, but a burn time: more area empties the
            # tank faster, so the ratio goes the other way up.
            k_inj = (m["burn_time"] / max(1e-6, tb)) ** DAMPING

        # A peak-thrust ceiling overrides wanting more thrust: the structure
        # is not negotiable and a motor that meets its impulse by breaking
        # the airframe has not met anything.
        if req.max_peak_thrust_n > 0 and m["peak_thrust"] > req.max_peak_thrust_n:
            k_inj = min(k_inj, (req.max_peak_thrust_n
                                / m["peak_thrust"]) ** DAMPING)

        # Chamber pressure goes as mdot/At, so the throat has to follow the
        # injector just to stand still. Fold that in rather than discovering
        # it next sweep.
        k_at = k_inj * (m["peak_Pc"] / max(1e3, pc_target)) ** DAMPING

        # Fuel flow has to track oxidiser flow to hold the mixture, and grain
        # length is what buys burn area.
        k_grain = k_inj * (m["avg_OF"] / max(1e-6, of_target)) ** DAMPING

        # Total impulse is propellant mass times Isp, and tank length is the
        # only thing here that is pure propellant volume.
        k_tank = 1.0
        if req.total_impulse_ns > 0:
            k_tank = (it / max(1e-6, m["total_impulse"])) ** DAMPING
        elif req.burn_time_s > 0:
            # No impulse requirement, but a burn time. Burn time is oxidiser
            # mass over oxidiser flow, and the injector is already committed
            # to holding thrust - so the tank is what is left to set it. With
            # no lever here at all, a thrust-plus-burn-time brief could only
            # ever satisfy the thrust.
            k_tank = (tb / max(1e-6, m["burn_time"])) ** DAMPING
            # Opening the injector to chase thrust empties the tank faster, so
            # the tank has to grow by that much again just to hold the clock.
            k_tank *= k_inj

        a_inj_new = eng.A_inj * k_inj
        n_holes, d_hole = _hole_pattern(a_inj_new, eng.inj_type)
        d_throat_new = eng.d_throat * math.sqrt(max(0.05, min(20.0, k_at)))
        l_grain_new = eng.L_grain * max(0.3, min(3.0, k_grain))
        l_tank_new = eng.L_tank * max(0.3, min(3.0, k_tank))

        eps_new = eng.eps_exp
        if req.min_isp_s > 0 and m["isp"] < req.min_isp_s and not eps_capped:
            # Isp is bought with expansion, up to where the bell runs out of
            # airframe or the flow separates off the wall.
            eps_new = eng.eps_exp * 1.15

        cand = _clone(eng, n_holes=n_holes, d_hole=d_hole,
                      d_throat=d_throat_new, L_grain=l_grain_new,
                      L_tank=l_tank_new, eps_exp=eps_new)
        cand = _apply_limits(cand, req, work)
        # If the nozzle asked to grow and the airframe refused, say so once
        # and stop asking. Otherwise every remaining sweep is spent requesting
        # an expansion ratio that gets clipped straight back off.
        if eps_new > eng.eps_exp and cand.eps_exp < eps_new * 0.999:
            eps_capped = True
            notes.append(
                f"Isp is short of the {req.min_isp_s:.0f} s asked for and the "
                f"expansion ratio is already as large as "
                f"{req.max_diameter_m * 1000:.0f} mm allows "
                f"(eps {cand.eps_exp:.2f}). N2O with a hydrocarbon grain "
                f"delivers about 180-200 s at sea level - more expansion is "
                f"the only lever here and it has run out.")
        cand, gave = _fit_envelope(cand, req, work)
        for g in gave:
            if g not in notes:
                notes.append(g)
        eng = cand

    if best_eng is None:
        raise ValueError(
            "No runnable motor came out of these requirements - check that "
            "the diameter and length leave room for a tank.")
    return best_eng, best_m, runs, notes


def _apply_limits(eng: Engine, req: Requirements, work: dict) -> Engine:
    """Keep a candidate physically buildable and inside its diameter.

    Applied after every step rather than once at the end: a search allowed to
    wander through impossible geometry spends its runs there and comes back
    with a motor whose port is wider than its case.
    """
    case_id = work["case_id"]
    changes = {}

    # The grain has to fit the case, and the port has to fit the grain with
    # some web left to burn.
    d_outer = min(eng.d_grain_outer, case_id) if case_id > 0 \
        else eng.d_grain_outer
    d_port = min(eng.d_port_0, d_outer - 2.0 * 0.003)
    d_port = max(d_port, 0.006)
    if d_outer <= d_port:
        d_outer = d_port + 2.0 * 0.003
    changes["d_grain_outer"] = d_outer
    changes["d_port_0"] = d_port
    changes["L_pre"] = L_PRE_CAL * d_outer
    changes["L_post"] = L_POST_CAL * d_outer

    # Two separate ceilings on the bell, and the tighter one wins.
    #
    # Geometry: an exit cone wider than the airframe is a drawing error.
    #
    # Physics: below about Pe = 0.4*Pa the exhaust cannot stay attached to the
    # wall and tears away (Summerfield), and past that point the extra bell is
    # dead weight that buys no thrust. Without this cap the search will happily
    # chase a shortfall in Isp all the way to an area ratio in the forties -
    # the solver models separation, so the Isp stops improving, but nothing
    # stopped the geometry growing. A motor with a separated nozzle also
    # side-loads the bell, which is how nozzles get torn off.
    eps_max = eng.eps_exp
    if req.max_diameter_m > 0:
        eps_max = min(eps_max, (req.max_diameter_m
                                / max(1e-9, eng.d_throat)) ** 2)
    eps_sep = _expansion_for_exit_pressure(
        work["pc"], SEPARATION_MARGIN * req.ambient_pa, eng.gamma)
    changes["eps_exp"] = max(1.0001, min(eps_max, eps_sep))

    # A throat wider than the port is not a nozzle, it is an open pipe.
    changes["d_throat"] = max(0.002, min(eng.d_throat, d_port * 0.95))

    changes["L_grain"] = max(0.02, eng.L_grain)
    changes["L_tank"] = max(0.05, eng.L_tank)
    return _clone(eng, **changes)


# --- the whole job --------------------------------------------------------

# Fuels tried when the brief does not name one. Not the full table: these four
# are the ones with real published regression data behind them and the ones a
# university team can actually cast or buy. Offering the designer a fuel whose
# coefficients are a guess would put a guess in the answer.
AUTO_FUELS = ("HTPB", "Paraffin", "ABS", "HDPE")


def engine_to_fields(eng: Engine, fuel_name: str) -> dict:
    """The design as the Engine Lab form stores it.

    Same shape ``EngineLabWidget.get_config`` produces, so the result can be
    handed straight to ``apply_config`` and every value lands in a box the
    user can then edit. The ``_units`` marker says these numbers are SI, which
    is what the form's unit-aware fields expect.
    """
    return {
        "d_tank": eng.d_tank, "L_tank": eng.L_tank,
        "fill_frac": eng.fill_frac, "T_tank_0": eng.T_tank_0,
        "d_vent": eng.d_vent, "Cd_vent": eng.Cd_vent,
        "cooling_coeff": eng.cooling_coeff,
        "n_holes": int(eng.n_holes), "d_hole": eng.d_hole,
        "Cd_inj": eng.Cd_inj,
        "L_grain": eng.L_grain, "d_grain_outer": eng.d_grain_outer,
        "d_port_0": eng.d_port_0, "fuel_a": eng.fuel_a, "fuel_n": eng.fuel_n,
        "n_ports": int(eng.n_ports), "L_pre": eng.L_pre, "L_post": eng.L_post,
        "d_throat": eng.d_throat, "eps_exp": eng.eps_exp,
        "alpha_deg": eng.alpha_deg, "beta_conv_deg": eng.beta_conv_deg,
        "erosion_rate": eng.erosion_rate,
        "eta_cstar": eng.eta_cstar, "eta_nozzle": eng.eta_nozzle,
        "gamma": eng.gamma, "MW": eng.MW,
        "fuel": fuel_name, "inj_type": eng.inj_type,
        "_units": "si",
    }


def assembled_diameter(eng: Engine, case_id_m: float, mats: dict) -> float:
    """The widest the finished motor actually is [m].

    Checking a design against its diameter limit using the limit itself
    proves nothing. This is the real number: bores plus the walls the
    pressures need, against the exit cone, whichever is largest.
    """
    tank_od = eng.d_tank + 2.0 * mats["tank"]["wall_m"]
    case_od = case_id_m + 2.0 * mats["chamber"]["wall_m"]
    return max(tank_od, case_od, eng.d_exit)


def _compliance(m: dict, req: Requirements, duty: tuple,
                length_parts: dict) -> list:
    """Requirement against delivered, one row each, only for what was asked."""
    it, fa, tb, _ = duty
    rows = []

    def add(label, required, achieved, met, note=""):
        rows.append(Compliance(label, required, achieved, met, note))

    if req.total_impulse_ns > 0:
        got = m["total_impulse"]
        add("Total impulse", f"{it:,.0f} N.s", f"{got:,.0f} N.s",
            abs(got - it) / it <= TOLERANCE,
            f"{(got - it) / it * 100:+.1f}%")
    if req.avg_thrust_n > 0:
        got = m["avg_thrust"]
        add("Average thrust", f"{fa:,.0f} N", f"{got:,.0f} N",
            abs(got - fa) / fa <= TOLERANCE,
            f"{(got - fa) / fa * 100:+.1f}%")
    if req.burn_time_s > 0:
        got = m["burn_time"]
        add("Burn time", f"{tb:.2f} s", f"{got:.2f} s",
            abs(got - tb) / tb <= TOLERANCE,
            f"{(got - tb) / tb * 100:+.1f}%")
    if req.min_isp_s > 0:
        got = m["isp"]
        add("Specific impulse", f"{req.min_isp_s:.1f} s minimum",
            f"{got:.1f} s", got >= req.min_isp_s * (1.0 - TOLERANCE),
            "" if got >= req.min_isp_s else
            "Isp is set by the propellant and the expansion ratio; a bigger "
            "bell is the only lever, and the airframe caps it.")
    if req.max_peak_thrust_n > 0:
        got = m["peak_thrust"]
        add("Peak thrust", f"{req.max_peak_thrust_n:,.0f} N maximum",
            f"{got:,.0f} N", got <= req.max_peak_thrust_n * (1.0 + TOLERANCE))
    if req.max_chamber_pressure_pa > 0:
        got = m["peak_Pc"]
        add("Chamber pressure",
            f"{req.max_chamber_pressure_pa / 1e6:.2f} MPa maximum",
            f"{got / 1e6:.2f} MPa",
            got <= req.max_chamber_pressure_pa * (1.0 + TOLERANCE))
    if req.max_length_m > 0:
        got = length_parts["TOTAL"]
        add("Overall length", f"{req.max_length_m * 1000:.0f} mm maximum",
            f"{got * 1000:.0f} mm", got <= req.max_length_m * 1.001)
    if req.max_diameter_m > 0:
        got = length_parts.get("_outside_diameter", 0.0)
        add("Outside diameter", f"{req.max_diameter_m * 1000:.0f} mm maximum",
            f"{got * 1000:.1f} mm", got <= req.max_diameter_m * 1.001,
            "Widest of the tank, the case and the nozzle exit, walls "
            "included.")
    return rows


def design_motor(req: Requirements, progress=None) -> DesignResult:
    """Size a whole motor to a brief. The entry point.

    ``progress(fraction, message)`` is called as the search runs, so a UI can
    say what it is doing instead of freezing.
    """
    duty = resolve_duty(req)
    it, fa, tb, duty_notes = duty

    fuels = [req.fuel] if req.fuel else list(AUTO_FUELS)
    fuels = [f for f in fuels if f in FUELS] or ["HTPB"]
    injector = req.injector if req.injector in INJECTOR_TYPES else "Showerhead"

    best = None
    total_runs = 0
    for i, fuel_name in enumerate(fuels):
        if progress is not None:
            progress(i / float(len(fuels)), f"Trying {fuel_name}...")
        try:
            seed, work, seed_notes = seed_engine(req, fuel_name, injector, duty)
            eng, m, runs, ref_notes = refine(
                seed, req, duty, work,
                progress=(lambda f, msg, _i=i: progress(
                    (_i + f) / float(len(fuels)), msg))
                if progress is not None else None)
        except Exception as exc:                      # this fuel cannot work
            total_runs += 1
            if best is None:
                best = ("__error__", exc, None, None, None, None)
            continue
        total_runs += runs
        key = (_violation(m, req), _score(m, req, duty))
        if best is None or best[0] == "__error__" or key < best[0]:
            best = (key, fuel_name, eng, m, work, seed_notes + ref_notes)
        # A fuel that meets the whole brief ends the search. Trying the other
        # three to see whether one meets it by a slightly smaller margin costs
        # seconds of the user's time and cannot change the answer.
        if key[0] <= 0.0 and key[1] <= TOLERANCE:
            break

    if best is None or best[0] == "__error__":
        raise ValueError(
            f"Could not size a motor for these requirements: "
            f"{best[1] if best else 'no candidate fuels'}")

    _key, fuel_name, eng, m, work, notes = best
    notes = list(duty_notes) + list(notes)

    parts = motor_length(eng, work["case_id"])
    mats = select_materials(
        chamber_pa=m["peak_Pc"], tank_pa=work["p_tank"],
        chamber_r=work["case_id"] / 2.0, tank_r=eng.d_tank / 2.0,
        burn_time_s=m["burn_time"], safety_factor=req.structural_sf,
        fill_temp_k=req.tank_temp_k)
    # Carried alongside the length breakdown so the compliance table can check
    # the diameter against what the motor measures, not against the limit.
    parts["_outside_diameter"] = assembled_diameter(
        eng, work["case_id"], mats)

    # A peak-thrust ceiling and an average-thrust requirement can simply be
    # incompatible, and when they are, the average is what gives - the
    # structural limit is not negotiable. Say which one bound and why, or the
    # table just shows a thrust miss with no explanation.
    if req.max_peak_thrust_n > 0 and req.avg_thrust_n > 0:
        shape = m["peak_thrust"] / max(1e-6, m["avg_thrust"])
        implied_avg = req.max_peak_thrust_n / shape
        if implied_avg < req.avg_thrust_n * (1.0 - TOLERANCE):
            notes.append(
                f"The {req.max_peak_thrust_n:,.0f} N peak limit is what is "
                f"holding thrust down. A blowdown hybrid starts hard and "
                f"decays, and this one peaks at {shape:.2f} times its "
                f"average, so a {req.max_peak_thrust_n:,.0f} N peak allows "
                f"about {implied_avg:,.0f} N average - not the "
                f"{req.avg_thrust_n:,.0f} N asked for. Raise the peak limit, "
                f"or accept the lower average. A vent orifice or a regulated "
                f"feed would flatten the curve, which this model can only "
                f"partly represent.")

    # Things worth saying about the motor that no requirement asked for but
    # that decide whether it is safe to fire.
    stiffness = eq.injector_stiffness(work["p_tank"], m["peak_Pc"])
    if stiffness < 0.20:
        notes.append(
            f"Injector stiffness is only {stiffness * 100:.0f}% at peak "
            f"chamber pressure. Below about 20% the chamber can push back on "
            f"the feed and the motor chugs. Open the throat or cool the tank.")
    port_to_throat = eq.port_to_throat_ratio(eng.A_port_0, eng.A_throat)
    if port_to_throat < 1.5:
        notes.append(
            f"Port area is only {port_to_throat:.1f} times the throat. Under "
            f"about 1.5 the port itself starts to choke the flow.")
    if eng.web_0 <= 0.004:
        notes.append(
            f"Fuel web is {eng.web_0 * 1000:.1f} mm. There is very little "
            f"wall to burn - the grain may burn through before the tank "
            f"empties.")
    l_star = eng.L_star()
    if l_star < 0.5:
        notes.append(
            f"L* is {l_star:.2f} m, which is a short chamber for a hybrid. "
            f"Expect the c* efficiency to fall short of the assumed "
            f"{eng.eta_cstar:.2f}.")

    return DesignResult(
        engine_fields=engine_to_fields(eng, fuel_name),
        metrics=dict(m),
        compliance=_compliance(m, req, duty, parts),
        notes=notes,
        materials=mats,
        envelope=parts,
        fuel_name=fuel_name,
        injector_name=eng.inj_type,
        runs=total_runs,
    )
