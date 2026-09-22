"""How far each engine component can drift before the rocket misses its goals.

A motor is built to drawings, and drawings have tolerances. A drilled
injector orifice is not exactly the diameter on the print, a cast grain does
not regress at exactly the coefficient in the literature, a tank is not
filled to exactly the fraction on the checklist. The question this answers is
the one that decides whether those errors matter: **how far wrong can each one
be and still have the rocket do what it was built to do?**

HOW IT WORKS

Each component has one knob - the quantity that actually moves its behaviour.
The knob is scaled by a factor, the whole motor is re-simulated, the whole
flight is re-flown, and the result is graded against the rocket's own goals.
Nothing is estimated: every point on the curve below is a full engine burn and
a full trajectory.

The search is a bisection, exactly as you would do it by hand:

    baseline (factor 1.00) must meet the goals, or there is nothing to measure
    try 0.50          - half the value
      still meets?    - try 0.25, and keep halving
      now misses?     - go back up halfway, to 0.375
    ... until the pass/fail boundary is pinned to two decimal places

then the same thing upward, from 1.00 toward 2.00.

A component that survives being halved has a 50% downward tolerance. One that
fails at 0.80 has a 20% downward tolerance. That is the number on the report.

WHAT IT DOES NOT DO

It varies ONE knob at a time from the baseline. Real builds miss several
tolerances at once and the combination can be worse than any single one; this
does not search combinations. It also does not feed anything back into the
Simulation tab - it flies its own copies and leaves the loaded rocket alone.

No Qt in here, so the whole search can be run headlessly.
"""
from __future__ import annotations

import dataclasses
import math
import os
import sys
from dataclasses import dataclass, field

import portable_paths

_HYBRID_SIM_ROOT = portable_paths.bundled_dir('hybrid_sim')
if os.path.isdir(_HYBRID_SIM_ROOT) and _HYBRID_SIM_ROOT not in sys.path:
    sys.path.insert(0, _HYBRID_SIM_ROOT)

import failure_analysis as fa        # noqa: E402
import flight_model                  # noqa: E402
from hybrid_sim.config import Engine  # noqa: E402
from hybrid_sim.engine import EngineModel            # noqa: E402
from hybrid_sim.metrics import metrics as hs_metrics  # noqa: E402


# Bisection stops when the bracket is this wide. 0.005 is half of the last
# digit reported, so the two-decimal answer cannot move once it is printed.
RESOLUTION = 0.005

# How far up to look. The downward search has a natural floor - a factor of
# zero is the component doing nothing at all - and the upward one does not, so
# it gets a stated limit rather than an invented one. 2.0 mirrors the floor:
# "twice as much" against "none at all".
MAX_FACTOR = 2.0

# Guard rail on runtime. A bisection to RESOLUTION needs about eight trials,
# and anything much past that means the boundary is not behaving like a
# boundary (a knob whose goal outcome is not monotonic, usually).
MAX_TRIALS_PER_DIRECTION = 12


# --- the knobs ------------------------------------------------------------

@dataclass
class Knob:
    """One component, and the quantity whose tolerance is being measured.

    ``read`` pulls the baseline out of an Engine. ``write`` puts a scaled
    value back, returning the Engine to fly. ``limit`` is the physical bound
    the quantity cannot pass regardless of what the goals say - a fill
    fraction cannot exceed a full tank however forgiving the mission is, and
    reporting an unreachable tolerance would be worse than reporting none.
    """
    key: str
    component: str
    quantity: str
    unit: str
    decimals: int
    read: object
    write: object
    why: str
    lower_limit: float = 0.0
    upper_limit: float = float("inf")

    def format(self, value: float) -> str:
        return f"{value:,.{self.decimals}f}{(' ' + self.unit) if self.unit else ''}"


def _set(eng: Engine, **changes) -> Engine:
    return dataclasses.replace(eng, **changes)


def _injector_area(eng: Engine) -> float:
    return eng.A_inj


def _set_injector_area(eng: Engine, area: float) -> Engine:
    """Put a total injector area back as a per-hole diameter.

    The hole count is left alone: a pattern is drilled with the number of
    holes on the print, and what varies is the size of them. Area goes as the
    square of the diameter, so the diameter moves as its square root.
    """
    n = max(1, int(eng.n_holes))
    d = math.sqrt(max(1e-12, area) * 4.0 / (math.pi * n))
    return _set(eng, d_hole=d)


def _regression_a(eng: Engine) -> float:
    """The regression coefficient actually in force.

    Engine.fuel_a is an override where 0 means "use the value that belongs to
    the named fuel", so the baseline has to resolve that - scaling a zero
    gives zero and would have reported every fuel as infinitely tolerant.
    """
    return eng.fuel_a if eng.fuel_a > 0 else eng.fuel_eff.a


def default_knobs() -> list:
    """The components this can measure, in the order they are searched."""
    return [
        Knob(
            key="tank", component="Oxidiser tank",
            quantity="Fill fraction (liquid vs vapour)", unit="", decimals=4,
            read=lambda e: e.fill_frac,
            write=lambda e, v: _set(e, fill_frac=v),
            why="How much of the tank is liquid at ignition rather than "
                "ullage vapour. Sets how much nitrous is aboard, and it is "
                "the number a fill is least able to hit exactly.",
            # A tank cannot be more than full, and a little ullage is needed
            # for thermal expansion - filling to the brim is a burst risk,
            # not a tolerance.
            lower_limit=0.01, upper_limit=0.98),
        Knob(
            key="injector", component="Injector",
            quantity="Flow area (Cd x A)", unit="mm^2", decimals=3,
            read=_injector_area,
            write=_set_injector_area,
            why="Total orifice area, which is what sets oxidiser flow and so "
                "thrust and burn time. Drill wander and edge break move it.",
            lower_limit=1e-9),
        Knob(
            key="grain", component="Fuel grain",
            quantity="Regression coefficient a", unit="", decimals=8,
            read=_regression_a,
            write=lambda e, v: _set(e, fuel_a=v),
            why="Fuel regression rate, which sets fuel flow and therefore the "
                "O/F the motor actually runs at. The least known number in a "
                "hybrid: it depends on the formulation and the casting, and "
                "it is not published for proprietary fuels.",
            lower_limit=1e-12),
        Knob(
            key="throat", component="Nozzle throat",
            quantity="Throat diameter", unit="mm", decimals=3,
            read=lambda e: e.d_throat,
            write=lambda e, v: _set(e, d_throat=v),
            why="The most sensitive dimension in the motor - chamber pressure "
                "goes roughly as one over throat area. Machining tolerance "
                "and erosion both move it.",
            lower_limit=1e-4),
    ]


# Display scaling, so a report reads in the unit a person would measure in.
DISPLAY_SCALE = {"injector": 1e6, "throat": 1e3}


def display_value(knob: Knob, si_value: float) -> str:
    return knob.format(si_value * DISPLAY_SCALE.get(knob.key, 1.0))


# --- flying one candidate -------------------------------------------------

@dataclass
class FlightContext:
    """Everything except the engine, held fixed across the whole search.

    Captured once, before the first trial. The point of a tolerance is that
    one thing moved and nothing else did, so the airframe, the site, the
    recovery train and the dry mass have to be the same objects every time.
    """
    airframe: object
    site: object
    recovery: object
    mass_props: object
    vehicle: object                 # failure_analysis.VehicleConfig, for goals
    cd_override: object = None
    output_dt: float = 0.05

    def fresh_mass(self):
        """A copy of the mass properties, because the flight mutates them.

        run_flight burns propellant off the object it is given. Handing it the
        same one twice would fly the second trial with the first trial's
        leftovers, and every later trial would look worse than it is.
        """
        return dataclasses.replace(self.mass_props)


@dataclass
class Trial:
    factor: float
    passed: bool
    apogee_ft: float = 0.0
    detail: str = ""
    failed_goals: tuple = ()


def fly(engine: Engine, ctx: FlightContext):
    """Run the engine, fly it, grade it. Returns (report, engine metrics).

    None if the motor will not run or the rocket will not leave the pad -
    which is a legitimate outcome of winding a knob far enough, and has to be
    a FAIL rather than an exception that stops the search.
    """
    try:
        res = EngineModel(engine).run()
        em = hs_metrics(res)
        if em.get("peak_thrust", 0.0) <= 0 or em.get("burn_time", 0.0) <= 0:
            return None, None
        points = [(float(t), max(0.0, float(f)))
                  for t, f in zip(res["t"], res["thrust"])]
        if len(points) < 2:
            return None, None

        mass_props = ctx.fresh_mass()
        # The propellant that flies is the propellant this motor actually
        # expends. Holding it at the baseline would fly a half-flow injector
        # with a full load of nitrous, and the tolerance would come back far
        # too generous.
        prop = float(em.get("prop_mass") or 0.0)
        if prop > 0:
            mass_props.propellant_mass_kg = prop

        rows, summary = flight_model.run_flight(
            points, ctx.airframe, ctx.site, ctx.recovery, mass_props,
            output_dt=ctx.output_dt, cd_override=ctx.cd_override)
        if not rows:
            return None, None
        rep = fa.analyze(rows, ctx.vehicle, engine_result=res, engine=engine,
                         cd_source=ctx.cd_override, mass_props=mass_props,
                         summary=summary)
        return rep, em
    except Exception:
        return None, None


def goal_list(vehicle) -> list:
    """The goals a flight is graded on, matching the Flight Report exactly.

    Same fallback: a rocket carrying no goals of its own is graded against
    its target altitude. Duplicating that rule rather than sharing it would
    let the two drift, and a tolerance measured against a different bar from
    the one on the report is worse than no tolerance at all.
    """
    goals = [g if isinstance(g, fa.Goal) else fa.Goal.from_dict(g)
             for g in (getattr(vehicle, "goals", None) or [])]
    if not goals:
        goals = [fa.Goal(metric="apogee", comparison=fa.AT_LEAST,
                         value=getattr(vehicle, "target_altitude_ft", 0.0))]
    return goals


def grade(rep, goals) -> tuple:
    """(met_all, tuple of the labels that were missed)."""
    if rep is None:
        return False, ("the motor or the flight did not run",)
    missed = []
    for goal in goals:
        status, _achieved, _detail = goal.evaluate(rep)
        if status == fa.CRITICAL:
            missed.append(goal.label())
    return (not missed), tuple(missed)


# --- the search -----------------------------------------------------------

@dataclass
class ToleranceResult:
    knob: Knob
    baseline: float = 0.0
    baseline_apogee_ft: float = 0.0
    low_factor: float | None = None      # smallest factor that still passes
    high_factor: float | None = None     # largest factor that still passes
    low_note: str = ""
    high_note: str = ""
    trials: list = field(default_factory=list)
    error: str = ""

    @property
    def down_pct(self):
        return None if self.low_factor is None else (1.0 - self.low_factor) * 100.0

    @property
    def up_pct(self):
        return None if self.high_factor is None else (self.high_factor - 1.0) * 100.0

    def low_value(self):
        return None if self.low_factor is None else self.baseline * self.low_factor

    def high_value(self):
        return None if self.high_factor is None else self.baseline * self.high_factor


def _bisect(knob, base_engine, ctx, goals, baseline, downward,
            on_trial=None, should_cancel=None):
    """Find the pass/fail boundary in one direction.

    Returns (boundary_factor, note, trials). The boundary is the last factor
    that still MEETS the goals, so the number reported is always one the
    rocket survives - never one it does not.
    """
    trials = []
    limit = knob.lower_limit if downward else knob.upper_limit
    limit_factor = (limit / baseline) if baseline else None
    if limit_factor is not None and not math.isfinite(limit_factor):
        limit_factor = None

    # The far end of the bracket: downward, the component doing nothing;
    # upward, the stated search cap. Physics pulls it in if it gets there
    # first.
    far = 0.0 if downward else MAX_FACTOR
    if limit_factor is not None:
        far = max(far, limit_factor) if downward else min(far, limit_factor)

    def run(factor):
        if should_cancel is not None and should_cancel():
            return None
        eng = knob.write(base_engine, baseline * factor)
        rep, _em = fly(eng, ctx)
        met, missed = grade(rep, goals)
        t = Trial(factor=factor, passed=met,
                  apogee_ft=(rep.apogee_ft if rep else 0.0),
                  failed_goals=missed)
        trials.append(t)
        if on_trial is not None:
            on_trial(knob, t, downward)
        return t

    # No headroom at all: the value is already at or past its physical bound,
    # so there is nothing to search and nothing to claim.
    if (downward and far >= 1.0) or ((not downward) and far <= 1.0):
        return 1.0, "already at its physical limit - no room to move", trials

    # Probe the far end first. If the knob can be taken to the end of its
    # range and the rocket still makes its goals, there is no boundary to
    # find. Bisecting toward one that does not exist would hand back the edge
    # of the bracket dressed up as a measurement.
    edge = run(far)
    if edge is None:
        return None, "cancelled", trials
    if edge.passed:
        # WHY it stopped matters as much as where. "Still fine at +15%" reads
        # as a thin margin; "still fine with the tank as full as it can safely
        # be filled" is the same number meaning the opposite - the goals never
        # bit at all, the hardware ran out first. Reporting the search cap's
        # wording for a physical limit understates the real tolerance.
        at_physical_limit = (limit_factor is not None
                             and abs(far - limit_factor) <= 1e-9)
        if downward:
            note = ("still meets the goals with this as low as it can "
                    "physically go" if at_physical_limit else
                    "still meets the goals with the component at zero")
        elif at_physical_limit:
            note = (f"still meets the goals at +{(far - 1.0) * 100:.0f}%, "
                    f"which is as high as this can physically go - the goals "
                    f"never became the limit")
        else:
            note = (f"still meets the goals at +{(far - 1.0) * 100:.0f}%, "
                    f"the furthest this searches - the real limit is higher")
        return far, note, trials

    # Invariant: lo fails, hi passes (downward) / lo passes, hi fails (up).
    lo, hi = (far, 1.0) if downward else (1.0, far)
    for _ in range(MAX_TRIALS_PER_DIRECTION):
        if abs(hi - lo) <= RESOLUTION:
            break
        mid = 0.5 * (lo + hi)
        t = run(mid)
        if t is None:
            return None, "cancelled", trials
        if t.passed == downward:
            # Downward a pass tightens the top of the bracket; upward a fail
            # tightens the top. Both narrow toward the boundary from above.
            hi = mid
        else:
            lo = mid

    # The last known-good end: the high side going down, the low side going up.
    passing = hi if downward else lo
    note = ""
    if limit_factor is not None and abs(passing - limit_factor) <= RESOLUTION:
        note = "stopped by the physical limit, not by the goals"
    return passing, note, trials


def measure(knob, base_engine, ctx, goals, baseline_apogee_ft,
            on_trial=None, should_cancel=None) -> ToleranceResult:
    """Both directions for one component."""
    baseline = float(knob.read(base_engine))
    result = ToleranceResult(knob=knob, baseline=baseline,
                             baseline_apogee_ft=baseline_apogee_ft)
    if not baseline or baseline <= 0:
        result.error = ("This component's value is zero on the loaded engine, "
                        "so there is no baseline to measure a tolerance "
                        "against.")
        return result

    low, low_note, t1 = _bisect(knob, base_engine, ctx, goals, baseline,
                                downward=True, on_trial=on_trial,
                                should_cancel=should_cancel)
    result.trials.extend(t1)
    if low is None:
        result.error = "cancelled"
        return result
    result.low_factor, result.low_note = low, low_note

    high, high_note, t2 = _bisect(knob, base_engine, ctx, goals, baseline,
                                  downward=False, on_trial=on_trial,
                                  should_cancel=should_cancel)
    result.trials.extend(t2)
    if high is None:
        result.error = "cancelled"
        return result
    result.high_factor, result.high_note = high, high_note
    return result


@dataclass
class ToleranceRun:
    baseline_ok: bool = False
    baseline_apogee_ft: float = 0.0
    baseline_missed: tuple = ()
    goals: list = field(default_factory=list)
    results: list = field(default_factory=list)
    cancelled: bool = False
    error: str = ""
    trials: int = 0


def find_tolerances(base_engine: Engine, ctx: FlightContext, knobs=None,
                    on_progress=None, on_trial=None,
                    should_cancel=None) -> ToleranceRun:
    """Measure every selected component. The entry point.

    ``on_progress(done, total, message)`` is called as it goes so a UI can
    show what it is doing; ``should_cancel()`` is polled before every trial.
    """
    knobs = list(knobs if knobs is not None else default_knobs())
    run = ToleranceRun(goals=goal_list(ctx.vehicle))

    counter = {"n": 0}

    def counted_trial(knob, trial, downward):
        counter["n"] += 1
        if on_trial is not None:
            on_trial(knob, trial, downward)

    # The baseline has to meet the goals or the question is meaningless: you
    # cannot measure how far a rocket can drift from meeting its goals if it
    # is not meeting them to begin with.
    if on_progress is not None:
        on_progress(0, len(knobs) + 1, "Flying the engine as configured...")
    rep, _em = fly(base_engine, ctx)
    counter["n"] += 1
    met, missed = grade(rep, run.goals)
    run.baseline_apogee_ft = rep.apogee_ft if rep else 0.0
    run.baseline_ok, run.baseline_missed = met, missed
    if not met:
        run.error = (
            "The engine as configured does not meet this rocket's goals, so "
            "there is no tolerance to measure - every result would be zero. "
            "Fix the design or the goals first.")
        run.trials = counter["n"]
        return run

    for i, knob in enumerate(knobs, start=1):
        if should_cancel is not None and should_cancel():
            run.cancelled = True
            break
        if on_progress is not None:
            on_progress(i, len(knobs) + 1,
                        f"{knob.component}: {knob.quantity}")
        result = measure(knob, base_engine, ctx, run.goals,
                         run.baseline_apogee_ft, on_trial=counted_trial,
                         should_cancel=should_cancel)
        run.results.append(result)
        if result.error == "cancelled":
            run.cancelled = True
            break

    run.trials = counter["n"]
    if on_progress is not None:
        on_progress(len(knobs) + 1, len(knobs) + 1, "Done.")
    return run
