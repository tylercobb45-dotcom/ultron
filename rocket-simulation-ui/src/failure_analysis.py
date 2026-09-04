"""Failure-mode analysis over a completed simulation run.

Takes the per-timestep data the flight simulation already collects (and, when
the flight was flown on an Engine Lab motor, the engine's internal-ballistics
data too), and grades the design against a numbered list of physical failure
modes: does it reach the target altitude, does anything melt, does anything
buckle, does the motor run oxidizer-rich, do the fins flutter, does the
recovery system survive deployment.

Every check carries a status - OK (green), CAUTION (yellow), CRITICAL (red)
or NO DATA (grey) - a measured value, the limit it was measured against, and
where applicable the time in the flight it happened, so the UI can drop a
numbered marker on the graphs that points back at the table row.

This module is deliberately Qt-free and importable on its own so the analysis
can be run and tested headlessly.

Scope note: these are the failure modes that the simulated data can actually
speak to. Modes that need geometry this app does not model - static stability
margin (CP/CG), torsional divergence, shear-pin and coupler joint strength,
zipper on deployment, motor-mount bond line, ignition transients - are listed
in Report.not_evaluated rather than silently omitted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import materials as mat_lib

OK = "OK"
CAUTION = "CAUTION"
CRITICAL = "CRITICAL"
NO_DATA = "NO DATA"

_STATUS_ORDER = {CRITICAL: 0, CAUTION: 1, OK: 2, NO_DATA: 3}

G0 = 9.80665
FT_PER_M = 3.28084

# ISA constants
_P_SL, _T_SL, _LAPSE, _T_TROP, _R_AIR, _GAMMA = 101325.0, 288.15, 0.0065, 216.65, 287.058, 1.4
_RECOVERY_FACTOR = 0.9  # turbulent boundary layer recovery factor


def isa(h: float):
    """Standard atmosphere. Returns (T [K], P [Pa], rho [kg/m^3], a [m/s])."""
    h = max(0.0, h)
    T = max(_T_TROP, _T_SL - _LAPSE * h)
    if h <= 11000.0:
        P = _P_SL * (T / _T_SL) ** 5.2561
    else:
        P11 = _P_SL * (_T_TROP / _T_SL) ** 5.2561
        P = P11 * math.exp(-G0 * (h - 11000.0) / (_R_AIR * _T_TROP))
    rho = P / (_R_AIR * T)
    return T, P, rho, math.sqrt(_GAMMA * _R_AIR * T)


@dataclass
class Check:
    """One numbered failure-mode check."""
    code: str
    category: str
    name: str
    status: str
    value: str
    limit: str
    detail: str
    recommendation: str = ""
    t_event: float | None = None
    margin: float | None = None      # fractional margin; negative = exceeded
    number: int = 0                  # assigned when the report is assembled


@dataclass
class Event:
    """A notable moment in the flight, for the timeline and graph markers."""
    name: str
    t: float
    detail: str


@dataclass
class VehicleConfig:
    """Everything the failure analysis needs that the flight sim does not model."""
    # Airframe
    body_od_m: float = 0.140
    body_wall_m: float = 0.003
    body_length_m: float = 2.5
    airframe_material: str = "Fiberglass G10/FR4"
    nose_material: str = "Fiberglass G10/FR4"
    # Fins
    fin_count: int = 4
    fin_root_chord_m: float = 0.30
    fin_tip_chord_m: float = 0.15
    fin_span_m: float = 0.15
    fin_thickness_m: float = 0.005
    fin_material: str = "Fiberglass G10/FR4"
    # Motor hardware
    nozzle_material: str = "Graphite"
    chamber_material: str = "Aluminum 6061-T6"
    chamber_wall_m: float = 0.004
    tank_material: str = "Aluminum 6061-T6"
    tank_wall_m: float = 0.003
    # Operations
    rail_length_m: float = 5.18          # 17 ft rail
    harness_rating_n: float = 4000.0     # shock cord / harness rated load
    target_altitude_ft: float = 50000.0
    min_pressure_sf: float = 2.0         # required safety factor on pressure parts
    min_structure_sf: float = 1.5        # required safety factor on structure


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    target_ft: float = 50000.0
    apogee_ft: float = 0.0
    apogee_m: float = 0.0
    goal_met: bool = False
    max_mach: float = 0.0
    max_q_pa: float = 0.0
    max_g: float = 0.0
    burnout_t: float | None = None
    has_engine_data: bool = False
    not_evaluated: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict:
        c = {OK: 0, CAUTION: 0, CRITICAL: 0, NO_DATA: 0}
        for chk in self.checks:
            c[chk.status] = c.get(chk.status, 0) + 1
        return c

    @property
    def verdict(self) -> str:
        c = self.counts
        if c[CRITICAL]:
            return CRITICAL
        if c[CAUTION]:
            return CAUTION
        return OK

    def by_status(self, status) -> list[Check]:
        return [c for c in self.checks if c.status == status]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sf_status(sf: float, need: float) -> str:
    """Status from a safety factor against a required safety factor."""
    if sf < 1.0:
        return CRITICAL
    if sf < need:
        return CAUTION
    return OK


def _band_status(value: float, lo_crit, lo_warn, hi_warn, hi_crit) -> str:
    """Status for a value that should sit inside a band."""
    if (lo_crit is not None and value < lo_crit) or (hi_crit is not None and value > hi_crit):
        return CRITICAL
    if (lo_warn is not None and value < lo_warn) or (hi_warn is not None and value > hi_warn):
        return CAUTION
    return OK


def _series(flight, key, default=0.0):
    return [row.get(key, default) if row.get(key, default) is not None else default for row in flight]


def _arg_max(values):
    best_i, best_v = 0, float("-inf")
    for i, v in enumerate(values):
        if v > best_v:
            best_i, best_v = i, v
    return best_i, best_v


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def analyze(flight, vehicle: VehicleConfig | None = None,
            engine_result=None, engine=None) -> Report:
    """Grade a completed flight against every failure mode we can evaluate.

    flight        : list of per-timestep dicts from simulation.run_simulation
    vehicle       : VehicleConfig (materials, geometry, operational limits)
    engine_result : optional hybrid_sim EngineModel.run() output dict
    engine        : optional hybrid_sim Engine dataclass that produced it
    """
    v = vehicle or VehicleConfig()
    rep = Report(target_ft=v.target_altitude_ft,
                 has_engine_data=engine_result is not None and engine is not None)

    if not flight:
        rep.checks.append(Check("M-01", "Mission", "Target altitude", NO_DATA,
                                "-", f"{v.target_altitude_ft:,.0f} ft",
                                "No flight data was produced."))
        return _finalize(rep)

    t = _series(flight, "time")
    alt = _series(flight, "altitude")
    vel = _series(flight, "velocity")
    acc = _series(flight, "acceleration")
    thrust = _series(flight, "thrust")
    drag = _series(flight, "drag")
    mass = _series(flight, "mass")
    mach = _series(flight, "Mach")
    q = _series(flight, "q")
    chute = [bool(r.get("chute_deployed")) for r in flight]

    i_ap, apogee_m = _arg_max(alt)
    rep.apogee_m = apogee_m
    rep.apogee_ft = apogee_m * FT_PER_M
    rep.goal_met = rep.apogee_ft >= v.target_altitude_ft
    i_mach, rep.max_mach = _arg_max(mach)
    i_q, rep.max_q_pa = _arg_max(q)
    i_g, max_a = _arg_max(acc)
    rep.max_g = max_a / G0

    _mission_checks(rep, v, flight, t, alt, thrust, mass, i_ap)
    _events(rep, t, alt, vel, thrust, chute, i_ap, i_q, i_mach)
    _thermal_checks(rep, v, t, alt, mach)
    _structural_checks(rep, v, t, alt, vel, thrust, drag, acc, q, i_q, i_g)
    _flight_checks(rep, v, t, alt, vel, thrust, mass, chute, flight)
    _engine_checks(rep, v, engine_result, engine)

    _trajectory_checks(rep, v, flight, t, alt, vel)

    rep.not_evaluated = [
        "Fin torsional divergence, and flutter of a non-trapezoidal planform.",
        "Joint-level structure: shear pins, coupler bond lines, motor mount "
        "retention, deployment zippering.",
        "Ignition transient and chuffing at motor start.",
        "Gusts and wind direction changes - the wind model is a steady "
        "power-law profile, not turbulence.",
        "Roll, coning, and any motion out of the vertical plane.",
    ]
    return _finalize(rep)


def _trajectory_checks(rep, v, flight, t, alt, vel):
    """Checks that need the 2-DOF model's data. Skipped on a legacy run."""
    if not flight or 'stability_cal' not in flight[0]:
        return

    # --- W-01 static stability margin through the burn ---
    thrust = _series(flight, "thrust")
    peak = max(thrust) if thrust else 0.0
    boost = [r for r in flight if r.get("thrust", 0) > 0.05 * peak] if peak > 0 else []
    if boost:
        margins = [r["stability_cal"] for r in boost]
        worst = min(margins)
        i_worst = margins.index(worst)
        status = _band_status(worst, 1.0, 1.5, 4.0, 6.0)
        rep.checks.append(Check(
            "W-01", "Stability", "Static stability margin", status,
            f"{worst:,.2f} cal min ({max(margins):,.2f} max)",
            "1.5 - 4.0 calibers",
            f"Barrowman centre of pressure against the centre of gravity as "
            f"propellant burns off. Under 1 caliber the vehicle is unstable and "
            f"will tumble; over about 4 it is overstable and weathercocks hard "
            f"into wind, trading altitude for a trip downwind. Margin moves "
            f"during the burn because the CG walks as propellant leaves.",
            "Under 1 cal: move mass forward or grow the fins. Over 4 cal: the "
            "rocket is nose-heavy, which costs altitude in any wind."
            if status != OK else "Margin stays in the usual flyable band.",
            t_event=boost[i_worst]["time"], margin=worst / 1.5 - 1.0))

    # --- W-02 weathercocking / angle of attack ---
    if 'angle_of_attack_deg' in flight[0] and boost:
        # While the vehicle is on the rail it is physically constrained, so the
        # angle between its axis and the wind is not an angle of attack it is
        # actually flying at. Only free flight counts.
        aoa = [(abs(r.get("angle_of_attack_deg", 0.0)), r["time"])
               for r in boost if r["time"] > 0.3 and not r.get("on_rail")]
        if aoa:
            worst_aoa, t_aoa = max(aoa)
            tilt = max(abs(r.get("angle_from_vertical_deg", 0.0)) for r in boost)
            rep.checks.append(Check(
                "W-02", "Stability", "Weathercocking / angle of attack",
                _band_status(worst_aoa, None, None, 10.0, 20.0),
                f"{worst_aoa:,.1f} deg AoA, {tilt:,.1f} deg tilt",
                "under 10 deg",
                f"Peak angle of attack during boost, and how far off vertical the "
                f"vehicle ended up pointing. Fins only work at small angles - past "
                f"10-15 degrees they stall and the restoring moment collapses. The "
                f"tilt is where the altitude went if the flight came up short.",
                "Reduce the stability margin, launch in less wind, or tilt the "
                "rail into the wind to cancel the weathercock."
                if worst_aoa > 10 else "Vehicle tracks close to its flight path.",
                t_event=t_aoa))

    # --- W-03 downrange drift / recovery distance ---
    if 'downrange' in flight[0]:
        drift = abs(flight[-1].get("downrange", 0.0))
        rep.checks.append(Check(
            "W-03", "Recovery", "Downrange drift",
            _band_status(drift, None, None, 1500.0, 4000.0),
            f"{drift:,.0f} m ({drift*3.28084:,.0f} ft)",
            "under 1.5 km",
            f"Where the vehicle lands relative to the pad, from weathercocking on "
            f"the way up plus wind drift under canopy on the way down. High-altitude "
            f"dual deploy drifts kilometres; that is the whole reason to hold the "
            f"main until low altitude.",
            "Deploy the main lower, use a smaller drogue, or check the recovery "
            "area is big enough for this." if drift > 1500 else
            "Landing stays within a reasonable recovery walk.",
            t_event=t[-1]))

    # --- W-04 supersonic model validity, now that drag is Mach-aware ---
    if rep.max_mach > 5.0:
        rep.checks.append(Check(
            "W-04", "Fidelity", "Aerodynamic model range", CRITICAL,
            f"Mach {rep.max_mach:.2f}", "validated to Mach 5",
            "The drag buildup is built and checked for the subsonic through "
            "Mach 5 range. Above Mach 5 real air starts dissociating and this "
            "model does not represent it.",
            "Treat this trajectory as unreliable above Mach 5."))
    else:
        rep.checks.append(Check(
            "W-04", "Fidelity", "Aerodynamic model range", OK,
            f"Mach {rep.max_mach:.2f}", "validated to Mach 5",
            "Drag is recomputed every step from Reynolds and Mach with a "
            "component buildup (skin friction, base, wave, fins), so the "
            "transonic rise and supersonic falloff are represented rather than "
            "a single fixed coefficient. Supersonic wave drag is an engineering "
            "correlation - treat it as +/-20%, not CFD."))


def _finalize(rep: Report) -> Report:
    rep.checks.sort(key=lambda c: (_STATUS_ORDER.get(c.status, 9), c.code))
    for i, chk in enumerate(rep.checks, start=1):
        chk.number = i
    rep.events.sort(key=lambda e: e.t)
    return rep


# ---------------------------------------------------------------------------
# mission
# ---------------------------------------------------------------------------

def _mission_checks(rep, v, flight, t, alt, thrust, mass, i_ap):
    target_ft = v.target_altitude_ft
    shortfall_ft = rep.apogee_ft - target_ft
    pct = (shortfall_ft / target_ft * 100.0) if target_ft else 0.0
    if rep.goal_met:
        status = OK if pct >= 5 else CAUTION
        detail = (f"Apogee {rep.apogee_ft:,.0f} ft clears the {target_ft:,.0f} ft goal "
                  f"by {shortfall_ft:,.0f} ft ({pct:+.1f}%).")
        rec = ("Margin is under 5% - wind, a warm motor, or a heavier build could "
               "put this back under the goal." if status == CAUTION else
               "Goal met with margin.")
    else:
        status = CRITICAL
        detail = (f"Apogee {rep.apogee_ft:,.0f} ft falls {abs(shortfall_ft):,.0f} ft "
                  f"({pct:+.1f}%) short of the {target_ft:,.0f} ft goal.")
        rec = _shortfall_advice(flight, rep, target_ft)
    rep.checks.append(Check(
        "M-01", "Mission", "Target altitude", status,
        f"{rep.apogee_ft:,.0f} ft", f"{target_ft:,.0f} ft",
        detail, rec, t_event=t[i_ap],
        margin=(rep.apogee_ft - target_ft) / target_ft if target_ft else None))

    # Sizing sanity: ideal delta-v available vs. delta-v the goal needs.
    m0 = mass[0] if mass else 0.0
    mf = min(mass) if mass else 0.0
    peak = max(thrust) if thrust else 0.0
    burn_t = 0.0
    if peak > 0:
        burning = [tt for tt, F in zip(t, thrust) if F > 0.05 * peak]
        burn_t = (burning[-1] - burning[0]) if burning else 0.0
    impulse = _trapz(thrust, t)
    if m0 > 0 and mf > 0 and mf < m0 and impulse > 0:
        isp = impulse / ((m0 - mf) * G0)
        dv_ideal = isp * G0 * math.log(m0 / mf)
        dv_gravity_loss = G0 * burn_t
        dv_available = dv_ideal - dv_gravity_loss
        # Drag-free burnout speed needed to coast to the target altitude.
        dv_needed = math.sqrt(2 * G0 * (v.target_altitude_ft / FT_PER_M))
        ratio = dv_available / dv_needed if dv_needed else 0.0
        # Drag makes the real requirement higher than the drag-free number,
        # so anything under ~1.3x is already in trouble.
        status = _band_status(ratio, 1.0, 1.3, None, None)
        rep.checks.append(Check(
            "M-02", "Mission", "Delta-v budget vs goal", status,
            f"{dv_available:,.0f} m/s", f"{dv_needed:,.0f} m/s (drag-free)",
            f"Ideal delta-v {dv_ideal:,.0f} m/s minus {dv_gravity_loss:,.0f} m/s gravity "
            f"loss over a {burn_t:.1f} s burn leaves {dv_available:,.0f} m/s. A drag-free "
            f"coast to {v.target_altitude_ft:,.0f} ft needs {dv_needed:,.0f} m/s, and drag "
            f"raises that materially.",
            "Ratio under 1.3 means the motor is undersized for the goal even before "
            "drag is counted - add total impulse or take mass out."
            if ratio < 1.3 else "Delta-v budget has headroom over the drag-free requirement.",
            margin=ratio - 1.0))


def _shortfall_advice(flight, rep, target_ft):
    """Point at the dominant lever for closing an altitude shortfall."""
    need = target_ft / rep.apogee_ft if rep.apogee_ft > 0 else float("inf")
    bits = [f"Apogee scales roughly with burnout energy: closing this gap needs "
            f"on the order of {need:.1f}x the current altitude."]
    drag_imp = _trapz(_series(flight, "drag"), _series(flight, "time"))
    thr_imp = _trapz(_series(flight, "thrust"), _series(flight, "time"))
    if thr_imp > 0:
        frac = drag_imp / thr_imp
        if frac > 0.25:
            bits.append(f"Drag is eating {frac*100:.0f}% of total impulse - a smaller "
                        f"frontal area or lower Cd is the cheapest win before adding motor.")
        else:
            bits.append(f"Drag costs only {frac*100:.0f}% of total impulse, so this is an "
                        f"impulse/mass problem, not an aerodynamics one: more total "
                        f"impulse or less dry mass.")
    return " ".join(bits)


def _trapz(y, x):
    return sum((y[i] + y[i - 1]) / 2.0 * (x[i] - x[i - 1]) for i in range(1, len(x)))


def _touchdown_speed(alt, vel):
    """Descent rate on the last airborne sample (the sim zeroes v at touchdown)."""
    for i in range(len(alt) - 1, -1, -1):
        if alt[i] > 0:
            return abs(vel[i])
    return abs(vel[-1]) if vel else 0.0


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------

def _events(rep, t, alt, vel, thrust, chute, i_ap, i_q, i_mach):
    peak = max(thrust) if thrust else 0.0
    if peak > 0:
        burning = [i for i, F in enumerate(thrust) if F > 0.05 * peak]
        if burning:
            rep.events.append(Event("Ignition", t[burning[0]], "Thrust above 5% of peak."))
            rep.burnout_t = t[burning[-1]]
            rep.events.append(Event("Burnout", t[burning[-1]],
                                    f"Thrust tails off at {alt[burning[-1]]*FT_PER_M:,.0f} ft, "
                                    f"{vel[burning[-1]]:,.0f} m/s."))
    if rep.max_q_pa > 0:
        rep.events.append(Event("Max Q", t[i_q], f"{rep.max_q_pa/1000:,.1f} kPa dynamic pressure."))
    if rep.max_mach > 0:
        rep.events.append(Event("Max Mach", t[i_mach], f"Mach {rep.max_mach:.2f}."))
    rep.events.append(Event("Apogee", t[i_ap], f"{rep.apogee_ft:,.0f} ft."))
    for i, deployed in enumerate(chute):
        if deployed:
            rep.events.append(Event("Chute deploy", t[i],
                                    f"At {alt[i]*FT_PER_M:,.0f} ft, {abs(vel[i]):,.1f} m/s."))
            break
    rep.events.append(Event("Landing", t[-1],
                            f"Descent rate {_touchdown_speed(alt, vel):,.1f} m/s."))


# ---------------------------------------------------------------------------
# aero-thermal
# ---------------------------------------------------------------------------

def _thermal_checks(rep, v, t, alt, mach):
    skin = mat_lib.get(v.airframe_material)
    nose = mat_lib.get(v.nose_material)

    t_rec, i_hot = 0.0, 0
    for i, (h, M) in enumerate(zip(alt, mach)):
        T_amb, _, _, _ = isa(h)
        T = T_amb * (1 + _RECOVERY_FACTOR * (_GAMMA - 1) / 2 * M * M)
        if T > t_rec:
            t_rec, i_hot = T, i

    note = ("Recovery temperature is the upper bound the airflow can drive the skin to; "
            "a short flight in thin air will not fully soak to it, so treat this as "
            "conservative.")
    for code, name, m in (("T-01", "Airframe skin heating", skin),
                          ("T-02", "Nose cone tip heating", nose)):
        sf_service = m.max_service_k / t_rec if t_rec > 0 else float("inf")
        if t_rec >= m.melt_k:
            status = CRITICAL
        elif t_rec >= m.max_service_k:
            status = CAUTION
        else:
            status = OK
        rep.checks.append(Check(
            code, "Aero-thermal", name, status,
            f"{t_rec:,.0f} K ({t_rec-273.15:,.0f} C)",
            f"{m.max_service_k:,.0f} K service / {m.melt_k:,.0f} K failure",
            f"Peak recovery temperature at Mach {rep.max_mach:.2f}. {m.name}: "
            f"{m.max_service_k:,.0f} K continuous, {m.melt_k:,.0f} K outright failure. {note}",
            ("Above the material's failure temperature - this part does not survive. "
             "Move to a higher-temperature material or an ablative/cork tip."
             if status == CRITICAL else
             "Above continuous-service temperature: strength drops even though it will not "
             "melt. Consider a tip insert, ablative coat, or a higher-temp layup."
             if status == CAUTION else "Skin stays inside the material's service range."),
            t_event=t[i_hot], margin=sf_service - 1.0))


# ---------------------------------------------------------------------------
# structures
# ---------------------------------------------------------------------------

def _structural_checks(rep, v, t, alt, vel, thrust, drag, acc, q, i_q, i_g):
    skin = mat_lib.get(v.airframe_material)
    fin = mat_lib.get(v.fin_material)

    d_out = v.body_od_m
    d_in = max(1e-4, d_out - 2 * v.body_wall_m)
    area = math.pi / 4 * (d_out ** 2 - d_in ** 2)
    inertia = math.pi / 64 * (d_out ** 4 - d_in ** 4)

    # S-01 dynamic pressure (rule-of-thumb banding; the real limits are S-02/S-03)
    rep.checks.append(Check(
        "S-01", "Structures", "Maximum dynamic pressure",
        _band_status(rep.max_q_pa / 1000.0, None, None, 80.0, 150.0),
        f"{rep.max_q_pa/1000:,.1f} kPa", "80 kPa caution / 150 kPa critical",
        f"Peak dynamic pressure at t={t[i_q]:.1f} s, {alt[i_q]*FT_PER_M:,.0f} ft. "
        f"High q drives fin loads, body bending, and any airframe opening.",
        "These are class-of-vehicle rules of thumb; the load-bearing verdicts are the "
        "axial stress and buckling checks below.",
        t_event=t[i_q]))

    # S-02 axial compression
    loads = [F + D for F, D in zip(thrust, drag)]
    i_load, n_max = _arg_max(loads)
    sigma = n_max / area if area > 0 else 0.0
    sf = (skin.yield_pa / sigma) if sigma > 0 else float("inf")
    rep.checks.append(Check(
        "S-02", "Structures", "Airframe axial compressive stress",
        _sf_status(sf, v.min_structure_sf),
        f"{sigma/1e6:,.1f} MPa (SF {sf:,.1f})",
        f"{skin.yield_mpa:,.0f} MPa yield, SF>={v.min_structure_sf:.1f}",
        f"Peak compressive load {n_max:,.0f} N (thrust + drag) on a {d_out*1000:.0f} mm "
        f"tube with {v.body_wall_m*1000:.1f} mm wall ({area*1e6:,.0f} mm^2 section) "
        f"in {skin.name}.",
        ("Section is overstressed - thicken the wall or change material."
         if sf < 1.0 else
         "Safety factor is below the required margin for flight hardware."
         if sf < v.min_structure_sf else "Axial stress has adequate margin."),
        t_event=t[i_load], margin=sf - 1.0))

    # S-03 Euler buckling of the body tube
    p_cr = (math.pi ** 2 * skin.youngs_pa * inertia) / (v.body_length_m ** 2) if v.body_length_m > 0 else float("inf")
    sf_b = p_cr / n_max if n_max > 0 else float("inf")
    rep.checks.append(Check(
        "S-03", "Structures", "Body tube column buckling",
        _sf_status(sf_b, v.min_structure_sf),
        f"{n_max:,.0f} N applied (SF {sf_b:,.1f})", f"{p_cr:,.0f} N critical",
        f"Euler critical load for a {v.body_length_m:.2f} m {skin.name} tube "
        f"(pinned ends, conservative) against the same {n_max:,.0f} N peak compression.",
        ("Column buckling governs before material yield - shorten the unsupported "
         "span with couplers/bulkheads or stiffen the tube."
         if sf_b < v.min_structure_sf else "Buckling margin is adequate."),
        t_event=t[i_load], margin=sf_b - 1.0))

    # S-04 axial g-load on payload/avionics
    rep.checks.append(Check(
        "S-04", "Structures", "Peak axial acceleration",
        _band_status(rep.max_g, None, None, 15.0, 30.0),
        f"{rep.max_g:,.1f} g", "15 g caution / 30 g critical",
        f"Peak acceleration at t={t[i_g]:.1f} s. Commercial altimeters and batteries "
        f"are typically qualified in the 20-50 g range; potted assemblies do better.",
        "Check the altimeter, battery retention, and any epoxy fillets against this "
        "number - it is a mounting problem more often than an airframe one.",
        t_event=t[i_g]))

    # S-05 fin flutter
    _fin_flutter_check(rep, v, fin, t, alt, vel)


def _fin_flutter_check(rep, v, fin, t, alt, vel):
    cr, ct, b, th = v.fin_root_chord_m, v.fin_tip_chord_m, v.fin_span_m, v.fin_thickness_m
    if min(cr, b, th) <= 0:
        rep.checks.append(Check(
            "S-05", "Structures", "Fin flutter", NO_DATA, "-", "-",
            "Fin geometry is incomplete, so flutter speed cannot be computed."))
        return

    ar = 2 * b / (cr + ct) if (cr + ct) > 0 else 0.0
    lam = ct / cr
    tc = th / cr
    worst_ratio, worst_i, worst_vf = 0.0, 0, float("inf")
    for i, (h, vv) in enumerate(zip(alt, vel)):
        _, P, _, a = isa(h)
        denom = 2 * (ar + 2) * (tc ** 3)
        num = 1.337 * (ar ** 3) * P * (lam + 1)
        if num <= 0 or denom <= 0:
            continue
        vf = a * math.sqrt(fin.shear_pa / (num / denom))
        speed = abs(vv)
        if vf > 0 and speed / vf > worst_ratio:
            worst_ratio, worst_i, worst_vf = speed / vf, i, vf

    sf = 1.0 / worst_ratio if worst_ratio > 0 else float("inf")
    rep.checks.append(Check(
        "S-05", "Structures", "Fin flutter margin", _sf_status(sf, 1.5),
        f"{abs(vel[worst_i]):,.0f} m/s flight (SF {sf:,.2f})",
        f"{worst_vf:,.0f} m/s flutter speed",
        f"NACA TN-4197 flutter speed for a {th*1000:.1f} mm {fin.name} fin "
        f"(AR {ar:.2f}, taper {lam:.2f}, t/c {tc:.3f}) at "
        f"{alt[worst_i]*FT_PER_M:,.0f} ft. Flutter speed falls with ambient pressure, "
        f"so the worst point is usually low and fast, not high and fast.",
        ("Flight speed exceeds flutter speed - the fin will diverge and shed. Thicken "
         "the fin, shorten the root chord, or move to a stiffer material (shear "
         "modulus is what matters)."
         if sf < 1.0 else
         "Less than 50% margin on flutter; thicken the fin or stiffen the material."
         if sf < 1.5 else "Fin stays well below flutter speed."),
        t_event=t[worst_i], margin=sf - 1.0))


# ---------------------------------------------------------------------------
# flight / recovery
# ---------------------------------------------------------------------------

def _mach_aware_drag(flight) -> bool:
    """Did this flight rebuild Cd as Mach changed, or hold it fixed?

    A fixed Cd - the Aerodynamics tab's measured-Cd override, or the legacy
    vertical model - shows the same body Cd at every Mach number. The 2-DOF
    buildup does not. Decided from the data rather than from which code path
    we think ran, so an override is caught even inside the 2-DOF model.
    """
    if not flight:
        return False
    cds, machs = [], []
    for row in flight:
        cd = row.get("Cd_body_eff", row.get("cd_body_eff"))
        mach = row.get("Mach")
        if cd is None or mach is None or cd <= 0:
            continue
        cds.append(float(cd))
        machs.append(float(mach))
    if len(cds) < 3:
        return False
    if max(machs) - min(machs) < 0.05:
        return False        # nothing moved; can't tell, so don't claim it did
    return (max(cds) - min(cds)) > 1e-6 * max(cds)



def _flight_checks(rep, v, t, alt, vel, thrust, mass, chute, flight):
    # R-01 rail exit velocity
    rail_i = next((i for i, h in enumerate(alt) if h >= v.rail_length_m), None)
    if rail_i is None:
        rep.checks.append(Check(
            "R-01", "Flight", "Rail exit velocity", CRITICAL, "never cleared rail",
            f"{v.rail_length_m:.2f} m rail",
            "The vehicle never reached the end of the launch rail.",
            "Thrust-to-weight is too low to fly at all.", t_event=t[0], margin=-1.0))
    else:
        vr = vel[rail_i]
        rep.checks.append(Check(
            "R-01", "Flight", "Rail exit velocity", _band_status(vr, 15.0, 20.0, None, None),
            f"{vr:,.1f} m/s", ">= 20 m/s (15 m/s absolute floor)",
            f"Speed leaving a {v.rail_length_m:.2f} m rail at t={t[rail_i]:.2f} s. Below "
            f"roughly 15-20 m/s the fins do not have enough authority to hold attitude "
            f"and the vehicle weathercocks off the rail.",
            "Lengthen the rail or raise initial thrust-to-weight."
            if vr < 20 else "Adequate authority at rail departure.",
            t_event=t[rail_i], margin=vr / 20.0 - 1.0))

    # P-01 liftoff thrust-to-weight (flight-side, works without engine data)
    w0 = mass[0] * G0 if mass else 0.0
    early = [F for tt, F in zip(t, thrust) if tt <= t[0] + 0.5]
    f_early = sum(early) / len(early) if early else 0.0
    twr = f_early / w0 if w0 > 0 else 0.0
    rep.checks.append(Check(
        "P-01", "Propulsion", "Liftoff thrust-to-weight", _band_status(twr, 2.0, 5.0, None, None),
        f"{twr:,.2f}:1", ">= 5:1 (2:1 absolute floor)",
        f"Mean thrust over the first 0.5 s ({f_early:,.0f} N) against liftoff weight "
        f"({w0:,.0f} N). The 5:1 convention exists so the vehicle is moving fast enough "
        f"to be stable by the time it clears the rail.",
        "Marginal or negative thrust-to-weight: the vehicle lifts slowly and departs "
        "the rail unstable." if twr < 5 else "Healthy launch acceleration.",
        t_event=t[0], margin=twr / 5.0 - 1.0))

    # R-02 transonic drag fidelity.
    #
    # This has to know which drag model actually flew. The 2-DOF model rebuilds
    # Cd every step from Reynolds and Mach, and W-04 grades that. But the same
    # report is produced for a flight flown on a FIXED Cd - either the Vehicle
    # tab's measured-Cd override, or the legacy vertical model - and for those
    # the old warning is still exactly right. Asserting "fixed Cd" regardless
    # of what ran is what made R-02 contradict W-04 in the same report.
    mach_varying = _mach_aware_drag(flight)
    if mach_varying:
        rep.checks.append(Check(
            "R-02", "Fidelity", "Transonic drag model validity", OK,
            f"Mach {rep.max_mach:.2f}", "Cd rebuilt every step",
            "Drag was recomputed at each step from Reynolds and Mach rather "
            "than held fixed, so the transonic rise is represented. W-04 "
            "grades how far that model can be trusted.",
            ""))
    elif rep.max_mach >= 0.8:
        status = CAUTION if rep.max_mach < 1.2 else CRITICAL
        rep.checks.append(Check(
            "R-02", "Fidelity", "Transonic drag model validity", status,
            f"Mach {rep.max_mach:.2f}", "Mach 0.8",
            "This flight was flown on a FIXED drag coefficient - either a "
            "measured-Cd override or the legacy vertical model. Real Cd can "
            "rise by 50-100% through the transonic rise, so above Mach 0.8 the "
            "predicted apogee is optimistic.",
            "Clear the measured-Cd override on the Aerodynamics tab to use the "
            "Mach-aware drag buildup, or supply a Cd(M) table from "
            "CFD/RASAero.",
            t_event=None))
    else:
        rep.checks.append(Check(
            "R-02", "Fidelity", "Transonic drag model validity", OK,
            f"Mach {rep.max_mach:.2f}", "Mach 0.8",
            "Flight stays subsonic, where a fixed drag coefficient is a "
            "reasonable model.",
            ""))

    # R-03 deployment shock
    deploy_i = next((i for i, c in enumerate(chute) if c), None)
    if deploy_i is None:
        rep.checks.append(Check(
            "R-03", "Recovery", "Deployment shock load", NO_DATA, "-",
            f"{v.harness_rating_n:,.0f} N harness",
            "No parachute deployment occurred in this run."))
        rep.checks.append(Check(
            "R-04", "Recovery", "Landing descent rate", CAUTION,
            f"{abs(vel[-1]):,.1f} m/s", "<= 6 m/s",
            "No recovery deployment was modelled, so the vehicle arrived ballistic.",
            "Configure parachute deploy height and size on the Simulation tab."))
        return

    # Read the *uncapped* canopy load: the flight model applies a drag limiter
    # (default 400 N) that would otherwise hide the real snatch force.
    shock, capped = 0.0, False
    for row in flight[deploy_i:]:
        uncapped = row.get("chute_drag_signed_smoothed_uncapped")
        f = abs(uncapped if uncapped is not None
                else (row.get("chute_drag_signed_smoothed") or row.get("drag") or 0.0))
        shock = max(shock, f)
        if row.get("drag_cap_applied"):
            capped = True
    sf = v.harness_rating_n / shock if shock > 0 else float("inf")
    deploy_v = abs(vel[deploy_i])
    rep.checks.append(Check(
        "R-03", "Recovery", "Deployment shock load", _sf_status(sf, 1.5),
        f"{shock:,.0f} N (SF {sf:,.1f})", f"{v.harness_rating_n:,.0f} N harness rating",
        f"Peak canopy load after deployment at {deploy_v:,.1f} m/s and "
        f"{alt[deploy_i]*FT_PER_M:,.0f} ft. This is {shock/(mass[deploy_i]*G0):,.1f}x "
        f"vehicle weight through the harness and bulkheads."
        + (" Note: the flight model's drag limiter clipped the force it actually flew "
           "with, so the trajectory after deployment is softer than this load implies."
           if capped else ""),
        ("Snatch load exceeds the harness rating - deploy a drogue first, deploy "
         "lower/slower, or uprate the harness and bulkhead hardware."
         if sf < 1.5 else "Harness has margin over the modelled snatch load."),
        t_event=t[deploy_i], margin=sf - 1.0))

    # R-05 deployment speed
    rep.checks.append(Check(
        "R-05", "Recovery", "Velocity at deployment", _band_status(deploy_v, None, None, 30.0, 60.0),
        f"{deploy_v:,.1f} m/s", "30 m/s caution / 60 m/s critical",
        f"Main deployment at {deploy_v:,.1f} m/s. Deploying a large main much above "
        f"~30 m/s is how canopies get shredded and airframes get zippered.",
        "Deploy a drogue at apogee and hold the main to low altitude."
        if deploy_v > 30 else "Deployment speed is in a survivable range.",
        t_event=t[deploy_i]))

    # R-04 landing velocity. The simulator zeroes velocity on touchdown, so read
    # the last sample that was still airborne.
    v_land = _touchdown_speed(alt, vel)
    rep.checks.append(Check(
        "R-04", "Recovery", "Landing descent rate", _band_status(v_land, None, None, 6.0, 9.0),
        f"{v_land:,.1f} m/s", "6 m/s caution / 9 m/s critical",
        f"Touchdown at {v_land:,.1f} m/s ({v_land*FT_PER_M:,.1f} ft/s). Above about "
        f"7.6 m/s (25 ft/s) fibreglass fins and airframes start taking damage.",
        "Increase main canopy area for a softer landing." if v_land > 6 else
        "Landing speed is in the usual safe band.",
        t_event=t[-1]))


# ---------------------------------------------------------------------------
# engine internal ballistics (needs Engine Lab data)
# ---------------------------------------------------------------------------

_ENGINE_CODES = [
    ("P-02", "Chamber pressure vs case"),
    ("P-03", "Injector pressure drop"),
    ("P-04", "O/F ratio band"),
    ("P-05", "Oxidizer-rich burn tail"),
    ("P-06", "Fuel grain burn-through"),
    ("P-07", "Oxidizer mass flux"),
    ("P-08", "Port-to-throat area ratio"),
    ("P-09", "Flame temperature vs throat"),
    ("P-10", "Nozzle flow separation"),
    ("P-11", "Tank pressure vs tank wall"),
    ("P-12", "Tank thermal collapse"),
]


def _engine_checks(rep, v, res, eng):
    if res is None or eng is None:
        for code, name in _ENGINE_CODES:
            rep.checks.append(Check(
                code, "Propulsion", name, NO_DATA, "-", "-",
                "No engine data for this flight. Design a motor on the Engine Lab tab "
                "and send its thrust curve to the simulation to enable engine-side "
                "failure checks.", ""))
        return

    t = list(res["t"])
    pc = list(res["Pc"])
    ptank = list(res["P_tank"])
    of = list(res["OF"])
    mdot_ox = list(res["mdot_ox"])
    mdot_f = list(res["mdot_fuel"])
    r_port = list(res["r_port"])
    m_fuel = list(res["m_fuel"])
    m_ox = list(res["m_ox"])
    t_tank = list(res["T_tank"])

    chamber = mat_lib.get(v.chamber_material)
    tank = mat_lib.get(v.tank_material)
    throat = mat_lib.get(v.nozzle_material, "Graphite")

    # P-02 chamber pressure vs case hoop stress
    i_pc, pc_max = _arg_max(pc)
    r_c = eng.d_grain_outer / 2.0
    hoop_c = pc_max * r_c / v.chamber_wall_m if v.chamber_wall_m > 0 else float("inf")
    sf_c = chamber.yield_pa / hoop_c if hoop_c > 0 else float("inf")
    rep.checks.append(Check(
        "P-02", "Propulsion", "Chamber pressure vs case", _sf_status(sf_c, v.min_pressure_sf),
        f"{hoop_c/1e6:,.0f} MPa hoop (SF {sf_c:,.1f})",
        f"{chamber.yield_mpa:,.0f} MPa yield, SF>={v.min_pressure_sf:.1f}",
        f"Peak chamber pressure {pc_max/1e6:.2f} MPa ({pc_max*0.000145038:,.0f} psi) on a "
        f"{eng.d_grain_outer*1000:.0f} mm case with {v.chamber_wall_m*1000:.1f} mm "
        f"{chamber.name} wall.",
        "Pressure vessels want a factor of 2 on yield. Thicken the case, drop chamber "
        "pressure (larger throat), or move to a stronger alloy."
        if sf_c < v.min_pressure_sf else "Case has the conventional 2x margin.",
        t_event=None, margin=sf_c / v.min_pressure_sf - 1.0))

    # Established-burn window: ignore the ignition transient (chamber starting at
    # ambient) and the dying blowdown tail, neither of which the model resolves
    # well enough to grade a design on.
    burn = _burn_window(res)

    # P-03 injector stiffness (dP/Pc)
    ratios = [((ptank[i] - pc[i]) / pc[i], i) for i in burn if pc[i] > 1e5]
    if ratios:
        dp_ratio, i_dp = min(ratios)
    else:
        dp_ratio, i_dp = 0.0, 0
    rep.checks.append(Check(
        "P-03", "Propulsion", "Injector pressure drop", _band_status(dp_ratio, 0.10, 0.20, None, None),
        f"{dp_ratio*100:,.0f}% of Pc", ">= 20% of Pc",
        f"Lowest injector stiffness during the established burn (t={t[i_dp]:.1f} s). Below "
        f"roughly 20% the feed system and chamber couple and the motor chugs; below 10% "
        f"it can flash back into the tank. Measured over the window where thrust is "
        f"above 25% of peak - the tail of a blowdown always trends to zero stiffness.",
        "Shrink the injector orifices or run a higher tank pressure to stiffen the feed."
        if dp_ratio < 0.20 else "Injector stays stiff enough to decouple feed from chamber.",
        margin=dp_ratio / 0.20 - 1.0))

    # P-04 mixture ratio band
    live = [(tt, o) for tt, o in zip(t, of) if o > 0]
    if live:
        of_vals = [o for _, o in live]
        of_mean = sum(of_vals) / len(of_vals)
        of_max = max(of_vals)
        i_of = of_vals.index(of_max)
        rep.checks.append(Check(
            "P-04", "Propulsion", "O/F ratio band", _band_status(of_mean, 2.5, 4.0, 8.0, 10.0),
            f"{of_mean:,.2f} mean, {of_max:,.2f} peak", "4-8 efficient band",
            f"c* for N2O/HTPB peaks near O/F 6-6.5. Running lean of 4 wastes oxidizer "
            f"and runs hot and oxidizing; rich of 8 leaves unburned fuel and drops Isp. "
            f"This motor sweeps {min(of_vals):,.2f} to {of_max:,.2f} as the port opens up.",
            "Resize the port or injector to centre the burn nearer O/F 6."
            if not (4.0 <= of_mean <= 8.0) else "Mixture ratio tracks through the efficient band.",
            t_event=None, margin=None))
    else:
        rep.checks.append(Check("P-04", "Propulsion", "O/F ratio band", NO_DATA, "-", "4-8",
                                "No combustion occurred in the engine run."))

    # P-05 oxidizer-rich tail: fuel gone while oxidizer still flowing
    m_f0 = float(res.get("m_f0") or m_fuel[0])
    fuel_out = m_fuel[-1] <= 0.01 * m_f0
    ox_left = m_ox[-1] / max(m_ox[0], 1e-9)
    if fuel_out and ox_left > 0.05:
        status, margin = CRITICAL, -1.0
    elif fuel_out:
        status, margin = CAUTION, -0.2
    else:
        status, margin = OK, 1.0
    rep.checks.append(Check(
        "P-05", "Propulsion", "Oxidizer-rich burn tail", status,
        f"{m_fuel[-1]*1000:,.0f} g fuel left, {ox_left*100:,.0f}% ox left",
        "fuel must outlast oxidizer",
        "A hybrid must run out of oxidizer first. If the fuel grain is consumed while "
        "oxidizer is still flowing, the chamber becomes a pure oxidizing torch and the "
        "case, liner and nozzle go with it.",
        "Lengthen the grain or add web thickness so the fuel outlasts the oxidizer."
        if fuel_out else "Fuel outlasts the oxidizer, which is the correct shutdown order.",
        t_event=t[-1], margin=margin))

    # P-06 grain burn-through to the case wall
    web0 = eng.R_outer - eng.r_port_0
    web_left = eng.R_outer - r_port[-1]
    frac = web_left / web0 if web0 > 0 else 0.0
    rep.checks.append(Check(
        "P-06", "Propulsion", "Fuel grain burn-through", _band_status(frac, 0.05, 0.15, None, None),
        f"{web_left*1000:,.1f} mm web left ({frac*100:,.0f}%)",
        ">= 15% of initial web",
        f"Initial web {web0*1000:,.1f} mm, final port radius {r_port[-1]*1000:,.1f} mm "
        f"against a {eng.R_outer*1000:,.1f} mm case radius. When the port reaches the "
        f"case, combustion gas hits the liner directly.",
        "Thicken the grain web (smaller initial port or larger grain OD)."
        if frac < 0.15 else "Comfortable web margin at burnout.",
        t_event=t[-1], margin=frac / 0.15 - 1.0))

    # P-07 oxidizer mass flux
    # Flux is per unit of PORT area, and a multi-port grain has n_ports of
    # them. Dividing by a single port's area reports the flux n_ports times too
    # high and trips a false CRITICAL. Engine.A_port is what the solver itself
    # uses, so use it here too.
    def _port_area(r):
        if hasattr(eng, "A_port"):
            return eng.A_port(r)
        return math.pi * r ** 2

    flux = [(mdot_ox[i] / _port_area(r_port[i]), i)
            for i in burn if r_port[i] > 0 and _port_area(r_port[i]) > 0]
    if flux:
        g_max, i_g = max(flux)
    else:
        g_max, i_g = 0.0, 0
    rep.checks.append(Check(
        "P-07", "Propulsion", "Oxidizer mass flux", _band_status(g_max, None, None, 500.0, 700.0),
        f"{g_max:,.0f} kg/m^2s peak", "<= 500 (700 critical)",
        f"Peak oxidizer flux through the port during the established burn "
        f"(t={t[i_g]:.1f} s). Classical HTPB regression data runs to roughly "
        f"350-500 kg/m^2s; much above that the flame lifts off the fuel surface and the "
        f"motor can flood or blow out. The model's ignition transient is excluded - it "
        f"starts the chamber at ambient pressure and briefly overstates injection.",
        "Open up the initial port diameter to drop peak flux."
        if g_max > 500 else "Flux stays inside the well-characterised regression range.",
        margin=500.0 / g_max - 1.0 if g_max > 0 else None))

    # P-08 port-to-throat area ratio
    a_port0 = math.pi * eng.r_port_0 ** 2
    ratio = a_port0 / eng.A_throat if eng.A_throat > 0 else 0.0
    rep.checks.append(Check(
        "P-08", "Propulsion", "Port-to-throat area ratio", _band_status(ratio, 1.5, 2.0, None, None),
        f"{ratio:,.2f}:1 at ignition", ">= 2:1",
        f"Initial port area {a_port0*1e6:,.0f} mm^2 against throat area "
        f"{eng.A_throat*1e6:,.0f} mm^2. Below about 2:1 the port itself starts choking "
        f"the flow, which drives erosive burning and a hard pressure spike at ignition.",
        "Increase initial port diameter or shrink the throat."
        if ratio < 2.0 else "Port is comfortably larger than the throat at ignition.",
        margin=ratio / 2.0 - 1.0))

    # P-09 flame temperature against throat material
    tc_flame = _flame_temperature(res, eng)
    if tc_flame is None:
        rep.checks.append(Check(
            "P-09", "Propulsion", "Flame temperature vs throat", NO_DATA, "-",
            f"{throat.melt_k:,.0f} K", "Chamber c* data unavailable for this run."))
    else:
        if tc_flame >= throat.melt_k:
            status = CRITICAL
        elif tc_flame >= throat.max_service_k:
            status = CAUTION
        else:
            status = OK
        rep.checks.append(Check(
            "P-09", "Propulsion", "Flame temperature vs throat", status,
            f"{tc_flame:,.0f} K", f"{throat.max_service_k:,.0f} K service / {throat.melt_k:,.0f} K failure",
            f"Adiabatic flame temperature from the c* the motor actually ran at, against "
            f"{throat.name}. Bare metal throats do not survive hybrid flame temperatures "
            f"for more than a second or two; graphite and ablatives are the usual answer.",
            "Move to graphite, tungsten, or an ablative-lined throat."
            if status != OK else "Throat material is rated above the flame temperature.",
            margin=throat.melt_k / tc_flame - 1.0))

    # P-10 nozzle over-expansion / flow separation at sea level
    gam = eng.gamma
    me = _exit_mach(eng.eps_exp, gam)
    pe_over_pc = (1 + (gam - 1) / 2 * me * me) ** (-gam / (gam - 1))
    pe_sl = pc_max * pe_over_pc
    sep_ratio = pe_sl / _P_SL
    rep.checks.append(Check(
        "P-10", "Propulsion", "Nozzle flow separation", _band_status(sep_ratio, 0.3, 0.4, None, None),
        f"Pe/Pa {sep_ratio:,.2f} at sea level", ">= 0.4 (Summerfield)",
        f"Exit pressure {pe_sl/1000:,.0f} kPa at peak chamber pressure with an area ratio "
        f"of {eng.eps_exp:.1f} (exit Mach {me:.2f}). Below the Summerfield criterion of "
        f"0.4 the flow separates inside the bell, which costs thrust and can drive "
        f"side loads on the nozzle.",
        "Reduce the expansion ratio for a sea-level start, or accept the separation "
        "losses through the first seconds of flight."
        if sep_ratio < 0.4 else "Nozzle runs attached at sea level.",
        margin=sep_ratio / 0.4 - 1.0))

    # P-11 tank pressure vs tank wall
    i_pt, pt_max = _arg_max(ptank)
    hoop_t = pt_max * (eng.d_tank / 2.0) / v.tank_wall_m if v.tank_wall_m > 0 else float("inf")
    sf_t = tank.yield_pa / hoop_t if hoop_t > 0 else float("inf")
    rep.checks.append(Check(
        "P-11", "Propulsion", "Tank pressure vs tank wall", _sf_status(sf_t, v.min_pressure_sf),
        f"{hoop_t/1e6:,.0f} MPa hoop (SF {sf_t:,.1f})",
        f"{tank.yield_mpa:,.0f} MPa yield, SF>={v.min_pressure_sf:.1f}",
        f"Peak tank pressure {pt_max/1e6:.2f} MPa ({pt_max*0.000145038:,.0f} psi) - N2O "
        f"vapour pressure, so it tracks tank temperature and rises fast on a hot pad - "
        f"on a {eng.d_tank*1000:.0f} mm tank with {v.tank_wall_m*1000:.1f} mm "
        f"{tank.name} wall.",
        "This is the single most dangerous part of a hybrid on the ground. Size the "
        "tank for N2O vapour pressure at the hottest pad temperature you will ever see "
        "(over 7 MPa near the 36 C critical point), with a 2x factor and a burst disc."
        if sf_t < v.min_pressure_sf else
        "Tank has the conventional 2x margin at the simulated temperature - confirm it "
        "still holds at maximum expected pad temperature.",
        t_event=None, margin=sf_t / v.min_pressure_sf - 1.0))

    # P-12 tank thermal collapse over the blowdown
    t_drop = t_tank[0] - min(t_tank)
    t_min = min(t_tank)
    rep.checks.append(Check(
        "P-12", "Propulsion", "Tank thermal collapse", _band_status(t_min, 182.3, 250.0, None, None),
        f"{t_min:,.0f} K min ({t_drop:,.0f} K drop)", ">= 250 K (182 K freezes)",
        f"Self-pressurised N2O cools as it boils off: {t_tank[0]:,.0f} K to "
        f"{t_min:,.0f} K over the burn. Vapour pressure follows temperature down, which "
        f"is what produces the long thrust tail-off. At 182 K nitrous freezes.",
        "Deep cooling means the back half of the burn runs at much lower thrust. A "
        "larger ullage, a warmer fill, or a pressurant system flattens the curve."
        if t_min < 250 else "Tank stays warm enough to hold useful pressure through the burn.",
        margin=(t_min - 182.3) / 182.3))


def _burn_window(res):
    """Indices where the motor is in its established burn (thrust > 25% of peak)."""
    thrust = list(res["thrust"])
    peak = max(thrust) if thrust else 0.0
    if peak <= 0:
        return list(range(len(thrust)))
    idx = [i for i, F in enumerate(thrust) if F > 0.25 * peak]
    return idx or list(range(len(thrust)))


def _flame_temperature(res, eng):
    """Adiabatic flame temperature back-derived from the c* the motor ran at."""
    try:
        pc = list(res["Pc"])
        mdot = list(res["mdot_tot"])
        pairs = [(p, m) for p, m in zip(pc, mdot) if m > 1e-6 and p > 1e5]
        if not pairs:
            return None
        # c*_ideal = Pc * At / (mdot * eta_cstar)
        cstars = [p * eng.A_throat / (m * eng.eta_cstar) for p, m in pairs]
        cstar = max(cstars)
        g = eng.gamma
        gamma_fn = math.sqrt(g) * (2 / (g + 1)) ** ((g + 1) / (2 * (g - 1)))
        r_specific = 8314.46 / eng.MW
        return (cstar * gamma_fn) ** 2 / r_specific
    except Exception:
        return None


def _exit_mach(eps, g):
    """Supersonic solution of the area-Mach relation (bisection, no scipy needed)."""
    def ar(M):
        return (1 / M) * ((2 / (g + 1)) * (1 + (g - 1) / 2 * M * M)) ** ((g + 1) / (2 * (g - 1)))
    lo, hi = 1.0 + 1e-9, 50.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if ar(mid) - eps > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2
