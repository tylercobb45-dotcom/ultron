"""A simulator built for the tolerance sweep, and for nothing else.

The tolerance search runs the rocket a hundred and fifty times. The main
simulation is not built for that - it is built to produce one flight in full
detail, with sixty columns of output per sample, and it earns every bit of
that when you are looking at a flight. Asking it for a hundred and fifty is
asking it to do a great deal of work whose answer is thrown away.

So this is a separate simulator. It is NOT a patch on the main one and it
does not reach into it: the main simulation is read for its NUMBERS - the
engine, the airframe, the site, the masses, the recovery train - and then
this integrates the flight itself.

WHAT MAKES IT FAST

Not a cruder model. The saving comes from what does not change:

  * The airframe, the site and the recovery train are FIXED for the whole
    sweep. Only the engine moves. So the drag coefficient and the atmosphere
    are tabulated once and interpolated afterwards, instead of being rebuilt
    from the component buildup twenty thousand times per flight. Cd is an
    exact function of (altitude, Mach, thrusting) once the airframe is
    fixed - Mach and altitude between them fix the speed, and so the
    Reynolds number - so the table is the same answer, not an approximation
    of it.
  * The descent under canopy is most of the elapsed time and almost none of
    the interest: it is a slow steady drift. It gets a coarse step; the
    ascent, where apogee is decided, keeps a fine one.
  * Only the numbers the goals are graded on come out. No per-sample rows.

WHERE THE PHYSICS COMES FROM

The equations are the shared library - ``engine_equations`` for the motor,
``aero`` for the drag buildup, ``atmosphere`` for the air, ``recovery`` for
the canopies. Those are single-sourced on purpose. A second private copy of
the physics is how two parts of a program start quietly disagreeing, and
this repo has paid for that lesson more than once. What is private here is
the INTEGRATION, not the physics.

The baseline this produces is checked against the main simulation every time
a search runs, and the search refuses to report anything if the two have
drifted apart - see agreement().
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field

import portable_paths

_HYBRID_SIM_ROOT = portable_paths.bundled_dir('hybrid_sim')
if os.path.isdir(_HYBRID_SIM_ROOT) and _HYBRID_SIM_ROOT not in sys.path:
    sys.path.insert(0, _HYBRID_SIM_ROOT)

from scipy.integrate import solve_ivp  # noqa: E402
import aero as aero_mod              # noqa: E402
import engine_equations as eq        # noqa: E402
from hybrid_sim.config import (      # noqa: E402
    CSTAR_OF, CSTAR_VAL, SimConfig, P_SL)

G0 = 9.80665
FT_PER_M = 3.280839895013123


# --- the numbers that do not move ----------------------------------------

@dataclass
class Tables:
    """Atmosphere and drag, evaluated once and interpolated after.

    Both are functions of things the sweep holds fixed, so tabulating them is
    bookkeeping rather than modelling. The grids are sized from the baseline
    flight so a rocket that goes twice as high still lands inside them.
    """
    z0: float
    dz: float
    alt: list                       # [(T, P, rho, a, mu)] by altitude index
    mach0: float
    dmach: float
    cd: list                        # cd[thrusting][iz][imach]
    n_z: int
    n_m: int

    def air(self, z: float):
        """(T, P, rho, a, mu) at altitude z, linearly interpolated."""
        f = (z - self.z0) / self.dz
        i = int(f)
        if i < 0:
            return self.alt[0]
        if i >= self.n_z - 1:
            return self.alt[self.n_z - 1]
        frac = f - i
        lo, hi = self.alt[i], self.alt[i + 1]
        return tuple(a + (b - a) * frac for a, b in zip(lo, hi))

    def drag_cd(self, z: float, mach: float, thrusting: bool) -> float:
        grid = self.cd[1 if thrusting else 0]
        fz = (z - self.z0) / self.dz
        iz = min(max(int(fz), 0), self.n_z - 2)
        tz = min(max(fz - iz, 0.0), 1.0)
        fm = (mach - self.mach0) / self.dmach
        im = min(max(int(fm), 0), self.n_m - 2)
        tm = min(max(fm - im, 0.0), 1.0)
        c00, c01 = grid[iz][im], grid[iz][im + 1]
        c10, c11 = grid[iz + 1][im], grid[iz + 1][im + 1]
        return ((c00 * (1 - tm) + c01 * tm) * (1 - tz)
                + (c10 * (1 - tm) + c11 * tm) * tz)


def build_tables(airframe, site, max_altitude_m: float,
                 max_mach: float = 4.0, n_z: int = 61,
                 n_m: int = 81) -> Tables:
    """Tabulate the air and the drag over the box this rocket will fly in."""
    top = max(1000.0, max_altitude_m)
    dz = top / (n_z - 1)
    alt = [site.properties(i * dz) for i in range(n_z)]
    dmach = max_mach / (n_m - 1)

    cd = []
    for thrusting in (False, True):
        grid = []
        for i in range(n_z):
            z = i * dz
            a_sound = alt[i][3]
            row = []
            for j in range(n_m):
                mach = j * dmach
                # Mach and altitude fix the speed, and so the Reynolds
                # number: this is the same Cd the main model computes, not a
                # stand-in for it.
                speed = mach * a_sound
                value, _breakdown = aero_mod.drag_coefficient(
                    mach, z, speed, airframe, site, thrusting=thrusting)
                row.append(float(value))
            grid.append(row)
        cd.append(grid)
    return Tables(z0=0.0, dz=dz, alt=alt, mach0=0.0, dmach=dmach, cd=cd,
                  n_z=n_z, n_m=n_m)


@dataclass
class Conditions:
    """Everything the sweep holds still, ready to fly against."""
    airframe: object
    site: object
    recovery: object
    mass_props: object
    tables: Tables
    cd_override: object = None      # a number, a callable of Mach, or None
    cp_override: float = None
    max_time: float = 5400.0

    @property
    def a_ref(self):
        return self.airframe.reference_area


# --- the motor ------------------------------------------------------------

#: The modelled quantities a tolerance run can be wrong about. These are
#: PARAMETERS of this simulator, deliberately - the previous version reached
#: into the main solver and overrode its methods, which made the tolerance
#: answers depend on the internal shape of a package this file does not own.
SCALES = ("mdot_ox", "pc", "cstar", "cf", "regression", "fuel_density", "of")


@dataclass
class Burn:
    t: list = field(default_factory=list)
    thrust: list = field(default_factory=list)
    prop_mass: float = 0.0
    peak_pc: float = 0.0
    mean_cstar: float = 0.0
    mean_cf: float = 0.0
    mean_rdot: float = 0.0
    peak_mdot_ox: float = 0.0
    mean_of: float = 0.0
    total_impulse: float = 0.0
    burn_time: float = 0.0
    #: Per-sample motor internals, only when the caller asked for them.
    #: Throwing these away is most of why this module is worth having, so
    #: nothing collects them unless somebody is about to look at them.
    rows: list = field(default_factory=list)

    def ok(self):
        return len(self.t) > 1 and self.total_impulse > 0.0


def burn_engine(eng, scales=None, dt: float = 0.01,
                cfg: SimConfig | None = None, ambient_pa: float = P_SL,
                capture: bool = False) -> Burn:
    """Integrate the motor on this module's own right-hand side.

    ``dt`` is the interval the finished burn is SAMPLED at for the thrust
    curve, not the integration step - the step is adaptive, see below.

    ``scales`` multiply the modelled quantities named in SCALES, at the point
    each one is computed, for every step of the burn, so everything
    downstream follows. They are parameters of this function. Nothing in the
    main solver is subclassed, patched or overridden to apply them.
    """
    c = cfg or SimConfig()
    s = dict(scales or {})
    k_mdot = s.get("mdot_ox", 1.0)
    k_pc = s.get("pc", 1.0)
    k_cstar = s.get("cstar", 1.0)
    k_cf = s.get("cf", 1.0)
    k_reg = s.get("regression", 1.0)
    k_rho = s.get("fuel_density", 1.0)
    k_of = s.get("of", 1.0)

    fuel = eng.fuel_eff
    a_reg = fuel.a * k_reg
    n_reg = fuel.n
    rho_f = fuel.rho * k_rho
    cool = eng.cooling_coeff if eng.cooling_coeff > 0 else c.cooling_coeff

    def cstar_of(of_ratio):
        return eq.characteristic_velocity(
            of_ratio * k_of, CSTAR_OF, CSTAR_VAL,
            fuel.of_shift, fuel.cstar_scale) * k_cstar

    def derivatives(t, y):
        """[m_ox, T, r_port, m_fuel, Pc] -> their rates, plus what we measure."""
        m_ox, T, r_port, m_f, Pc = y
        at = eq.throat_area(eng.d_throat_at(t))
        m_l = eq.tank_liquid_mass(max(0.0, m_ox), T, eng.V_tank)
        if m_l <= c.eps_mass:
            mdot_ox, p_tank = 0.0, eq.n2o_saturation_pressure(T)
        else:
            mdot_ox, p_tank = eq.injector_mass_flow(
                eng.Cd_inj * eng.A_inj, T, Pc, c.kappa, c.Pc_to_Ptank_max)
            mdot_ox *= k_mdot
        mdot_vent = eq.vent_mass_flow(eng.Cd_vent, eng.A_vent, p_tank, T)

        if r_port < eng.R_outer and m_f > c.eps_mass and mdot_ox > 0:
            flux = eq.oxidiser_flux(mdot_ox, eng.A_port(r_port))
            rdot = eq.regression_rate(flux, a_reg, n_reg)
            mdot_f = eq.fuel_mass_flow(rho_f, eng.A_burn(r_port), rdot)
        else:
            flux = rdot = mdot_f = 0.0

        mdot_tot = mdot_ox + mdot_f
        of = mdot_ox / mdot_f if mdot_f > 1e-9 else 0.0
        cstar = cstar_of(of)
        target = eq.chamber_pressure_target(
            mdot_tot, cstar, at, eng.eta_cstar, p_tank,
            c.Pc_to_Ptank_max) * k_pc
        dpc = eq.chamber_pressure_lag(target, Pc, c.tau_chamber)
        dT = (eq.n2o_tank_cooling_rate(m_l, T, mdot_ox + mdot_vent, cool)
              if m_l > c.eps_mass else 0.0)
        rates = [-(mdot_ox + mdot_vent), dT, rdot, -mdot_f, dpc]
        return rates, (at, mdot_ox, mdot_f, of, cstar, rdot, Pc)

    m_l0 = eng.fill_frac * eng.V_tank * eq.n2o_liquid_density(eng.T_tank_0)
    m_v0 = (1.0 - eng.fill_frac) * eng.V_tank * eq.n2o_vapour_density(
        eng.T_tank_0)
    y = [m_l0 + m_v0, eng.T_tank_0, eng.r_port_0, eng.m_fuel_0(), P_SL]
    # Dead liquid is a FRACTION of what was loaded, not an absolute mass.
    # Reading eps_liq_dead as 0.05 kg rather than 5% of the initial 5.07 kg
    # let the burn run 3.4 s past where the solver ends it, on a dribble of
    # thrust - which put burnout nearly a thousand feet too high.
    dead_liquid = c.eps_liq_dead * m_l0

    # Adaptive, not a fixed step of my own.
    #
    # A fixed-step midpoint rule was tried here and it is not good enough. It
    # agreed with the main solver to a twentieth of a percent at the baseline
    # and then drifted monotonically as the throat was squeezed - 2.8% at
    # 0.75x, 8.4% at 0.63x, 15.4% at 0.45x - because a small throat drives
    # chamber pressure up against tank pressure, the injector pressure drop
    # collapses, and the system turns stiff exactly where the limiter bites.
    # Refining the step around the end events did not touch it; the error is
    # in the stiff stretch, not at the boundary.
    #
    # That is the worst possible place to be wrong. A tolerance search spends
    # all its time at perturbed operating points, so an error invisible at
    # the baseline and growing with the perturbation would pass every check
    # that looked at the baseline and be wrong in every number reported.
    #
    # The saving that made this module worth writing was never here: the
    # trajectory is seven eighths of the cost of a trial and it still has its
    # own integrator. This is one call to a well-tested adaptive solver, on
    # THIS module's own right-hand side, with the scales as ordinary
    # parameters of it. Nothing is overridden.
    def rhs(t_now, state):
        return derivatives(t_now, state)[0]

    def ev_liquid(t_now, state):
        return eq.tank_liquid_mass(max(0.0, state[0]), state[1],
                                   eng.V_tank) - dead_liquid

    def ev_fuel(t_now, state):
        return state[3] - c.eps_mass

    def ev_wall(t_now, state):
        return eng.R_outer - state[2]

    for _event in (ev_liquid, ev_fuel, ev_wall):
        _event.terminal, _event.direction = True, -1

    y0 = list(y)
    sol = solve_ivp(rhs, (0.0, c.t_max), y0, method="RK45",
                    events=(ev_liquid, ev_fuel, ev_wall),
                    rtol=c.rtol, atol=c.atol, max_step=c.max_step,
                    dense_output=True)
    t_end = float(sol.t[-1])
    out = Burn()
    if t_end <= 0:
        return out
    series = {"cstar": [], "cf": [], "rdot": [], "of": [], "pc": [],
              "mdot_ox": []}
    eps_exp = eng.eps_exp
    me = eq.exit_mach(eps_exp, eng.gamma)
    n_out = max(2, int(t_end / dt) + 1)
    for i in range(n_out):
        t = t_end * i / (n_out - 1)
        state = sol.sol(t)
        _rates, obs = derivatives(t, state)
        at, mdot_ox, mdot_f, of, cstar, rdot, pc = obs
        cf = eq.thrust_coefficient(pc, ambient_pa, eps_exp, me, eng.gamma,
                                   c.sep_criterion) * k_cf
        out.t.append(t)
        out.thrust.append(
            max(0.0, cf * pc * at * eng.eta_nozzle * eng.lambda_div))
        for key, value in (("cstar", cstar), ("cf", cf), ("rdot", rdot),
                           ("of", of), ("pc", pc), ("mdot_ox", mdot_ox)):
            series[key].append(value)
        if capture:
            # Keys chosen to match datasheet.ENGINE_COLUMNS, so this sheet
            # carries the same headings and units as the Engine Data sheet
            # the rest of the app shows rather than a parallel vocabulary.
            p_tank = eq.n2o_saturation_pressure(state[1])
            out.rows.append({
                "t": t, "thrust": out.thrust[-1],
                "Pc": pc, "P_tank": p_tank, "T_tank": state[1],
                "inj_dP": max(0.0, p_tank - pc),
                "inj_stiffness": eq.injector_stiffness(p_tank, pc),
                "Pc_over_Pt": (pc / p_tank) if p_tank > 0 else 0.0,
                "mdot_ox": mdot_ox, "mdot_fuel": mdot_f,
                "mdot_tot": mdot_ox + mdot_f,
                "OF": of, "cstar": cstar, "c_star_eff": cstar * eng.eta_cstar,
                "cf": cf,
                "G_ox": eq.oxidiser_flux(mdot_ox, eng.A_port(state[2])),
                "rdot": rdot, "r_port": state[2],
                "web_left": max(0.0, eng.R_outer - state[2]),
                "m_ox": state[0], "m_fuel": state[3],
            })
    y_end = list(sol.y[:, -1])
    m_start = y0[0] + y0[3]
    y = y_end

    # Measured the way the main solver measures, to the letter. These are
    # conventions, not physics, and the two disagreeing by a third on burn
    # time - which is what happened while this used "thrust above a newton" -
    # reads as the simulator being wrong when it is only counting
    # differently.
    out.total_impulse = sum(
        (out.thrust[i] + out.thrust[i - 1]) * 0.5 * (out.t[i] - out.t[i - 1])
        for i in range(1, len(out.t)))
    peak = max(out.thrust) if out.thrust else 0.0
    # The 5%-of-peak certification convention.
    live = [i for i, f in enumerate(out.thrust) if f > 0.05 * peak]
    if len(live) > 1:
        out.burn_time = out.t[live[-1]] - out.t[live[0]]
    if live:
        def mean_over(key, positive_only=False):
            vals = [series[key][i] for i in live
                    if (not positive_only or series[key][i] > 0)]
            return sum(vals) / len(vals) if vals else 0.0
        out.mean_cstar = mean_over("cstar")
        out.mean_cf = mean_over("cf")
        out.mean_rdot = mean_over("rdot")
        out.mean_of = mean_over("of", positive_only=True)
        out.peak_pc = max(series["pc"][i] for i in live)
        out.peak_mdot_ox = max(series["mdot_ox"][i] for i in live)
    # Propellant is what LEFT the tank and the grain, not the integral of the
    # flow rates - the integral picks up the step error of every one of those
    # rates, and the mass difference picks up none of it.
    out.prop_mass = max(0.0, m_start - (y[0] + y[3]))
    return out


# --- the flight -----------------------------------------------------------

@dataclass
class Outcome:
    """Only what the goals are graded on. Nothing else comes out."""
    apogee_ft: float = 0.0
    apogee_m: float = 0.0
    max_speed_ms: float = 0.0
    max_mach: float = 0.0
    max_g: float = 0.0
    landing_speed_ms: float = 0.0
    drift_m: float = 0.0
    time_to_apogee_s: float = 0.0
    burnout_alt_ft: float = 0.0
    max_q_pa: float = 0.0
    flew: bool = False
    landed: bool = False
    #: Per-sample trajectory, only when the caller asked for it.
    rows: list = field(default_factory=list)


# Step sizes by phase. The ascent decides apogee and every speed the goals
# care about, so it keeps the main model's step. The descent under canopy is
# a slow steady drift whose only contributions are landing speed and drift,
# and it is eight times the elapsed time of the ascent - stepping it as
# finely as the boost is most of the cost of a flight for almost none of its
# information.
DT_ASCENT = 0.01
DT_DESCENT = 0.05
DT_CANOPY = 0.2



def fly(cond: Conditions, burn: Burn, capture: bool = False,
        output_dt: float = 0.05) -> Outcome:
    """Integrate one trajectory. Same physics as the main model, own stepper.

    ``capture`` fills Outcome.rows with per-sample trajectory data in the
    shape datasheet.FLIGHT_COLUMNS reads. It is off for the sweep - a hundred
    and fifty flights' worth of rows nobody asked to see is most of the cost
    this module exists to avoid - and on when one particular flight is opened
    to be looked at.
    """
    out = Outcome()
    if not burn.ok():
        return out
    next_sample = 0.0

    times, thrusts = burn.t, burn.thrust
    n_pts = len(times)
    t_burn_end = times[-1]
    prop_mass = max(0.0, burn.prop_mass)
    isp = (burn.total_impulse / (prop_mass * G0)) if prop_mass > 0 else 0.0

    site, airframe, rec = cond.site, cond.airframe, cond.recovery
    mass_props, tables = cond.mass_props, cond.tables
    rec.reset()

    rail_angle = math.radians(site.rail_angle_deg)
    rail_dx, rail_dz = math.sin(rail_angle), math.cos(rail_angle)
    rail_len = max(0.0, site.rail_length_m)
    a_ref = cond.a_ref
    diameter = max(1e-6, airframe.body_diameter_m)
    length = airframe.total_length
    cn_alpha = airframe.normal_force_slope()
    dry_mass = mass_props.effective_dry_mass()

    cd_lookup = cond.cd_override if callable(cond.cd_override) else None
    cd_constant = (float(cond.cd_override)
                   if cond.cd_override is not None and cd_lookup is None
                   else None)

    # Thrust interpolation over the burn's own samples. A local cursor, not a
    # search: t only moves forward.
    cursor = [0]

    def thrust_at(t):
        if t <= 0.0 or t >= t_burn_end:
            return 0.0
        i = cursor[0]
        while i + 1 < n_pts and times[i + 1] < t:
            i += 1
        cursor[0] = i
        if i + 1 >= n_pts:
            return 0.0
        t0, t1 = times[i], times[i + 1]
        if t1 <= t0:
            return thrusts[i]
        f = (t - t0) / (t1 - t0)
        return thrusts[i] + (thrusts[i + 1] - thrusts[i]) * f

    t = 0.0
    x = z = vx = vz = 0.0
    prop_left = prop_mass
    on_rail = True
    launched = False
    past_apogee = False
    theta = rail_angle
    omega = 0.0
    rail_travel = 0.0
    apogee_z = 0.0
    t_apogee = 0.0
    burnout_alt = 0.0
    prev_speed = 0.0

    while t < cond.max_time:
        thrust = thrust_at(t)
        thrusting = thrust > 0.01
        mass = dry_mass + prop_left

        _T, _P, rho, a_sound, _mu = tables.air(z)
        wind = site.wind_speed_at(z)
        rvx, rvz = vx - wind, vz
        speed_rel = math.hypot(rvx, rvz)
        mach = speed_rel / a_sound if a_sound > 0 else 0.0
        q = 0.5 * rho * speed_rel * speed_rel

        # Attitude from the moments acting on the vehicle, exactly as the main
        # model does it: the normal force acts at the CP, so its moment about
        # the CG restores when the CP is behind and diverges when it is not.
        if on_rail:
            theta, omega = rail_angle, 0.0
        elif speed_rel > 1e-6:
            theta_rel = math.atan2(rvx, rvz if abs(rvz) > 1e-9 else 1e-9)
            cg = mass_props.cg(prop_left)
            cp = (cond.cp_override if cond.cp_override is not None
                  else airframe.center_of_pressure(mach))
            inertia = max(1e-6, mass_props.inertia(prop_left, length))
            alpha = math.atan2(math.sin(theta - theta_rel),
                               math.cos(theta - theta_rel))
            arm = cp - cg
            moment = -q * a_ref * cn_alpha * math.sin(alpha) * arm
            if speed_rel > 1.0:
                moment -= (q * a_ref * cn_alpha * arm * arm
                           / speed_rel) * omega
            omega += (moment / inertia) * _dt_for(thrusting, past_apogee, rec)
            theta += omega * _dt_for(thrusting, past_apogee, rec)
            theta = math.atan2(math.sin(theta), math.cos(theta))
        dir_x, dir_z = math.sin(theta), math.cos(theta)

        if cd_lookup is not None:
            cd_body = float(cd_lookup(mach))
        elif cd_constant is not None:
            cd_body = cd_constant
        else:
            cd_body = tables.drag_cd(z, mach, thrusting)
        cda_body = cd_body * a_ref
        cda_recovery = rec.drag_area(t)
        if cda_recovery > 0:
            # Once a canopy is out the airframe is not flying nose-first any
            # more, so its own drag is the blunt-body figure.
            cda_body = 0.8 * a_ref
        cda_total = cda_body + cda_recovery

        drag_mag = q * cda_total
        if speed_rel > 1e-9:
            drag_x = -drag_mag * rvx / speed_rel
            drag_z = -drag_mag * rvz / speed_rel
        else:
            drag_x = drag_z = 0.0

        g = site.gravity(z)
        fx = thrust * dir_x + drag_x
        fz = thrust * dir_z + drag_z - mass * g
        if on_rail:
            along = fx * rail_dx + fz * rail_dz
            if along < 0 and rail_travel <= 0.0:
                along = 0.0
            fx, fz = along * rail_dx, along * rail_dz

        ax, az = fx / mass, fz / mass

        speed_ground = math.hypot(vx, vz)
        out.max_speed_ms = max(out.max_speed_ms, speed_ground)
        out.max_mach = max(out.max_mach, mach)
        out.max_q_pa = max(out.max_q_pa, q)
        out.max_g = max(out.max_g, math.hypot(ax, az) / G0)

        if capture and t >= next_sample - 1e-12:
            next_sample = t + output_dt
            out.rows.append({
                "time": t, "altitude": z, "altitude_ft": z * FT_PER_M,
                "downrange": x, "velocity": vz, "horizontal_velocity": vx,
                "ground_speed": speed_ground, "airspeed": speed_rel,
                "Mach": mach, "acceleration": az,
                "accel_total": math.hypot(ax, az),
                "accel_g": math.hypot(ax, az) / G0,
                "thrust": thrust, "drag": drag_mag, "mass": mass,
                "propellant_remaining": prop_left,
                "q": q, "rho_local": rho, "temperature_k": _T,
                "pressure_pa": _P,
                "Cd_eff": (cda_total / a_ref) if a_ref > 0 else 0.0,
                "Cd_body_eff": cd_body, "A_eff": a_ref,
                "cda_recovery": cda_recovery,
                "chute_deployed": cda_recovery > 0,
                "tilt_deg": math.degrees(theta),
            })

        dt = _dt_for(thrusting, past_apogee, rec)
        vx += ax * dt
        vz += az * dt
        x += vx * dt
        z += vz * dt
        t += dt
        if prop_left > 0 and isp > 0 and thrusting:
            prop_left = max(0.0, prop_left - thrust / (isp * G0) * dt)
        if on_rail:
            rail_travel += math.hypot(vx, vz) * dt
            if rail_travel >= rail_len:
                on_rail = False
        if z > 0.01:
            launched = True
        if z > apogee_z:
            apogee_z, t_apogee = z, t
        elif launched and vz < 0:
            past_apogee = True
        if thrusting:
            burnout_alt = z
        rec.update(t, z, vz, past_apogee, launched=launched)

        if launched and z <= 0.0:
            out.landed = True
            out.landing_speed_ms = abs(prev_speed)
            break
        prev_speed = vz

    out.flew = launched
    out.apogee_m = apogee_z
    out.apogee_ft = apogee_z * FT_PER_M
    out.time_to_apogee_s = t_apogee
    out.burnout_alt_ft = burnout_alt * FT_PER_M
    out.drift_m = abs(x)
    if not out.landed:
        out.landing_speed_ms = abs(vz)
    return out


def _dt_for(thrusting, past_apogee, rec):
    if rec.any_deployed():
        return DT_CANOPY
    if thrusting or not past_apogee:
        return DT_ASCENT
    return DT_DESCENT


def simulate(eng, cond: Conditions, scales=None, burn_dt: float = 0.01,
             capture: bool = False):
    """The whole thing: burn the motor, fly it, hand back the metrics."""
    burn = burn_engine(eng, scales=scales, dt=burn_dt,
                       ambient_pa=cond.site.pressure_pa, capture=capture)
    return fly(cond, burn, capture=capture), burn
