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
import tolerance_sim as sim          # noqa: E402
from hybrid_sim.config import Engine  # noqa: E402

#: Re-exported so callers do not need to know which module owns the list.
SCALES = sim.SCALES


# Bisection stops when the bracket is this wide. 0.005 is half of the last
# digit reported, so the two-decimal answer cannot move once it is printed.
RESOLUTION = 0.005

# How far up to look. The downward search has a natural floor - a factor of
# zero is the quantity being nothing at all - and the upward one does not, so
# it gets a stated limit rather than an invented one. Three times the modelled
# value: far enough that a motor which survives it is not meaningfully bounded
# above, and the report says so in those words rather than implying the limit
# was found.
MAX_FACTOR = 3.0

# Guard rail on runtime. A bisection to RESOLUTION needs about eight trials,
# and anything much past that means the boundary is not behaving like a
# boundary (a knob whose goal outcome is not monotonic, usually).
MAX_TRIALS_PER_DIRECTION = 12


# --- the knobs ------------------------------------------------------------

HARDWARE = "hardware"
MODEL = "model"


@dataclass
class Knob:
    """One thing that can be wrong, and how to make it wrong.

    ``apply`` takes the engine and a factor and returns the pair the trial
    needs: the Engine to run, and the model scales to run it under. A
    hardware knob changes the Engine and scales nothing; a model knob leaves
    the Engine alone and scales the solver. Both end up in the same search.

    ``observe`` pulls a representative baseline number out of the unperturbed
    run, purely so the report can say what 100% actually was.
    """
    key: str
    component: str
    quantity: str
    unit: str
    decimals: int
    kind: str
    apply: object
    observe: object
    why: str
    lower_limit: float = 0.0
    upper_limit: float = float("inf")

    def format(self, value: float) -> str:
        return (f"{value:,.{self.decimals}f}"
                f"{(' ' + self.unit) if self.unit else ''}")


def _set(eng: Engine, **changes) -> Engine:
    return dataclasses.replace(eng, **changes)


def _hardware(setter):
    """Wrap a hardware setter into the (engine, scales) shape."""
    def apply(engine, baseline, factor):
        return setter(engine, baseline * factor), {}
    return apply


def _model(name):
    """A model knob: the engine is untouched, the solver is scaled."""
    def apply(engine, _baseline, factor):
        return engine, {name: factor}
    return apply


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
    gives zero and would report every fuel as infinitely tolerant.
    """
    return eng.fuel_a if eng.fuel_a > 0 else eng.fuel_eff.a


def default_knobs() -> list:
    """Everything this can be wrong about.

    Hardware first because they are the build tolerances a team can act on,
    then the model doubts. The ORDER the search actually uses is measured,
    not this list - see rank_by_sensitivity.
    """
    return [
        # --- what the hardware is -------------------------------------
        Knob(key="tank", component="Oxidiser tank", kind=HARDWARE,
             quantity="Fill fraction (liquid vs vapour)", unit="", decimals=4,
             apply=_hardware(lambda e, v: _set(e, fill_frac=v)),
             observe=lambda burn, e: e.fill_frac,
             why="How much of the tank is liquid at ignition rather than "
                 "ullage vapour. Sets how much nitrous is aboard, and it is "
                 "the number a fill is least able to hit exactly.",
             lower_limit=0.01, upper_limit=0.98),
        Knob(key="injector", component="Injector", kind=HARDWARE,
             quantity="Flow area (Cd x A)", unit="mm^2", decimals=3,
             apply=_hardware(_set_injector_area),
             observe=lambda burn, e: e.A_inj,
             why="Total orifice area, which is what sets oxidiser flow and "
                 "so thrust and burn time. Drill wander and edge break move "
                 "it.",
             lower_limit=1e-9),
        Knob(key="throat", component="Nozzle throat", kind=HARDWARE,
             quantity="Throat diameter", unit="mm", decimals=3,
             apply=_hardware(lambda e, v: _set(e, d_throat=v)),
             observe=lambda burn, e: e.d_throat,
             why="The most sensitive dimension in the motor - chamber "
                 "pressure goes roughly as one over throat area. Machining "
                 "tolerance and erosion both move it.",
             lower_limit=1e-4),

        # --- what the model says --------------------------------------
        Knob(key="pc", component="Chamber pressure", kind=MODEL,
             quantity="Pressure the model predicts", unit="MPa", decimals=3,
             apply=_model("pc"),
             observe=lambda burn, e: burn.peak_pc / 1e6,
             why="Everything the nozzle does starts here. If the chamber "
                 "really runs above or below what the model says, thrust and "
                 "impulse move with it - this is usually the largest single "
                 "doubt in the whole engine."),
        Knob(key="cstar", component="Combustion", kind=MODEL,
             quantity="c* (characteristic velocity)", unit="m/s", decimals=1,
             apply=_model("cstar"),
             observe=lambda burn, e: burn.mean_cstar,
             why="How much chamber pressure the propellant is worth. Comes "
                 "off a CEA table computed for a propellant pair that is "
                 "close to yours rather than exactly yours."),
        Knob(key="cf", component="Nozzle", kind=MODEL,
             quantity="Thrust coefficient Cf", unit="", decimals=4,
             apply=_model("cf"),
             observe=lambda burn, e: burn.mean_cf,
             why="How much the bell multiplies chamber pressure times throat "
                 "area. An isentropic ideal with a divergence correction - "
                 "real nozzles lose more, and by an amount nobody knows "
                 "without firing it."),
        Knob(key="regression", component="Fuel grain", kind=MODEL,
             quantity="Regression rate", unit="mm/s", decimals=4,
             apply=_model("regression"),
             observe=lambda burn, e: burn.mean_rdot * 1000.0,
             why="How fast the fuel wall burns back, and so the fuel flow. "
                 "The coefficient behind it is a literature average for a "
                 "whole fuel family - the least known number in a hybrid, "
                 "and not published at all for proprietary fuels."),
        Knob(key="of", component="Mixture ratio", kind=MODEL,
             quantity="O/F the combustion sees", unit="", decimals=3,
             apply=_model("of"),
             observe=lambda burn, e: burn.mean_of,
             why="The ratio the chemistry behaves as though it were running "
                 "at. Hybrids burn in a boundary layer and mix unevenly, so "
                 "the effective mixture is not simply the two flow rates "
                 "divided."),
        Knob(key="mdot_ox", component="Oxidiser flow", kind=MODEL,
             quantity="Oxidiser mass flow", unit="kg/s", decimals=4,
             apply=_model("mdot_ox"),
             observe=lambda burn, e: burn.peak_mdot_ox,
             why="What the injector model says comes through for a given "
                 "pressure drop. A blend of two limiting cases with a "
                 "weighting that is a rule of thumb."),
        Knob(key="fuel_density", component="Fuel grain", kind=MODEL,
             quantity="Fuel density", unit="kg/m3", decimals=1,
             apply=_model("fuel_density"),
             observe=lambda burn, e: e.fuel_eff.rho,
             why="How much mass comes off for a given regression. A cast "
                 "grain with voids, or one packed denser than the datasheet, "
                 "moves this without changing how fast the wall recedes."),
    ]


# Display scaling, so a report reads in the unit a person would measure in.
DISPLAY_SCALE = {"injector": 1e6, "throat": 1e3}


def display_value(knob: Knob, si_value: float) -> str:
    if si_value is None:
        return "-"
    return knob.format(si_value * DISPLAY_SCALE.get(knob.key, 1.0))


# --- flying one candidate -------------------------------------------------

@dataclass
class FlightContext:
    """Everything except the engine, held fixed across the whole search.

    Captured once, before the first trial. The point of a tolerance is that
    one thing moved and nothing else did, so the airframe, the site, the
    recovery train and the dry mass have to be the same for every trial.

    The tolerance simulator's lookup tables hang off this too, because they
    are the same kind of thing: derived from what does not move, and built
    once.
    """
    airframe: object
    site: object
    recovery: object
    mass_props: object
    vehicle: object                 # failure_analysis.VehicleConfig, for goals
    cd_override: object = None
    conditions: object = None       # tolerance_sim.Conditions, built on demand

    def prepare(self, expected_apogee_m: float = 3000.0):
        """Build the simulator's fixed tables. Call once, before the search."""
        tables = sim.build_tables(
            self.airframe, self.site,
            # Room above the baseline for the trials that fly higher, so a
            # perturbed motor is not quietly clamped to the top of the table.
            max_altitude_m=max(1000.0, expected_apogee_m * 3.0))
        self.conditions = sim.Conditions(
            airframe=self.airframe, site=self.site, recovery=self.recovery,
            mass_props=dataclasses.replace(self.mass_props), tables=tables,
            cd_override=self.cd_override)
        return self.conditions


@dataclass
class Trial:
    factor: float
    passed: bool
    apogee_ft: float = 0.0
    detail: str = ""
    failed_goals: tuple = ()


def fly(engine: Engine, ctx: FlightContext, scales=None):
    """Burn the motor, fly it, hand back (outcome, burn).

    Goes through the tolerance simulator, not the main one. ``scales`` are
    model-error multipliers, passed straight in as parameters - the search
    does not reach into anybody's solver to apply them.

    None if the motor will not run or the rocket will not leave the pad -
    which is a legitimate outcome of winding a knob far enough, and has to be
    a FAIL rather than an exception that stops the search.
    """
    cond = ctx.conditions or ctx.prepare()
    try:
        outcome, burn = sim.simulate(engine, cond, scales=scales)
    except Exception:
        return None, None
    if not outcome.flew or outcome.apogee_m <= 0:
        return None, None
    return outcome, burn


def agreement(engine: Engine, ctx: FlightContext, reference_apogee_ft: float):
    """How far the tolerance simulator is from the flight the app flies.

    The whole point of a separate simulator is speed, and the whole risk of
    one is that it quietly stops describing the same rocket. So the baseline
    is compared against the main simulation's own answer every time a search
    runs, and the number is reported rather than assumed. Returns the
    fractional difference in apogee, or None if there is nothing to compare.
    """
    if not reference_apogee_ft:
        return None
    outcome, _burn = fly(engine, ctx)
    if outcome is None:
        return None
    return (outcome.apogee_ft - reference_apogee_ft) / reference_apogee_ft


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
    #: Apogee change, in percent, for the SENSITIVITY_STEP probe. What the
    #: ordering is built from, and worth showing: a knob the rocket barely
    #: notices and one that moves it 20% both deserve a tolerance, but not
    #: the same amount of attention.
    sensitivity_pct: float | None = None

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
    if knob.kind == MODEL:
        # A model scale has no hardware bound to run into: the question is
        # how wrong the number can be, and "wrong by all of it" is a
        # perfectly askable question. The limits below belong to parts.
        limit_factor = None
    else:
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
        eng, scales = knob.apply(base_engine, baseline, factor)
        rep, _em = fly(eng, ctx, scales)
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


def measure(knob, base_engine, ctx, goals, baseline_apogee_ft, baseline,
            on_trial=None, should_cancel=None) -> ToleranceResult:
    """Both directions for one component."""
    baseline = float(baseline or 0.0)
    result = ToleranceResult(knob=knob, baseline=baseline,
                             baseline_apogee_ft=baseline_apogee_ft)
    if baseline <= 0:
        result.error = ("This came out as zero on the loaded engine, so "
                        "there is no baseline to measure against.")
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
    ranking: list = field(default_factory=list)   # [(knob, apogee % change)]
    #: Fractional apogee difference between this simulator and the main one
    #: at the baseline. None if there was nothing to compare against.
    agreement: float | None = None


# One probe per knob decides the order. Small enough to stay in the linear
# part of the response, large enough to rise clear of solver noise.
SENSITIVITY_STEP = 0.10


def rank_by_sensitivity(base_engine, ctx, knobs, baselines,
                        baseline_apogee_ft, on_probe=None,
                        should_cancel=None):
    """Order the knobs by how much the rocket actually cares about each.

    Measured, not asserted. Which quantity matters most is a property of THIS
    rocket, not of hybrids in general - an oxidiser-flow error barely moves a
    tank-limited motor because the same nitrous comes out either way, while
    the same error on a chamber-pressure-limited one is the whole ball game.
    One flight per knob buys an order worth having, and the number is worth
    showing on its own.

    Returns [(knob, percent change in apogee)], largest first.
    """
    scored = []
    for knob in knobs:
        if should_cancel is not None and should_cancel():
            break
        base = baselines.get(knob.key) or 0.0
        if base <= 0:
            scored.append((knob, 0.0))
            continue
        eng, scales = knob.apply(base_engine, base, 1.0 + SENSITIVITY_STEP)
        rep, _em = fly(eng, ctx, scales)
        if rep is None or baseline_apogee_ft <= 0:
            pct = float("inf")        # it broke the motor: maximum attention
        else:
            pct = (rep.apogee_ft - baseline_apogee_ft) / baseline_apogee_ft * 100.0
        scored.append((knob, pct))
        if on_probe is not None:
            on_probe(knob, pct)
    scored.sort(key=lambda kv: -abs(kv[1]))
    return scored


#: How far the tolerance simulator may sit from the main one at the baseline
#: before the answers stop being about the same rocket. The two integrate the
#: same physics with different steppers, so they will not agree to the last
#: digit and should not be expected to; a percent is comfortably inside the
#: uncertainty in the c* table, and well outside anything a real disagreement
#: would hide in.
AGREEMENT_LIMIT = 0.01


def find_tolerances(base_engine: Engine, ctx: FlightContext, knobs=None,
                    on_progress=None, on_trial=None, should_cancel=None,
                    reference_apogee_ft: float = 0.0) -> ToleranceRun:
    """Measure every selected component. The entry point.

    ``on_progress(done, total, message)`` is called as it goes so a UI can
    show what it is doing; ``should_cancel()`` is polled before every trial.
    """
    knobs = list(knobs if knobs is not None else default_knobs())
    run = ToleranceRun(goals=goal_list(ctx.vehicle))
    if ctx.conditions is None:
        ctx.prepare()

    counter = {"n": 0}

    def counted_trial(knob, trial, downward):
        counter["n"] += 1
        if on_trial is not None:
            on_trial(knob, trial, downward)

    # The baseline has to meet the goals or the question is meaningless: you
    # cannot measure how far a rocket can drift from meeting its goals if it
    # is not meeting them to begin with.
    if on_progress is not None:
        on_progress(0, len(knobs) + 2, "Flying the engine as configured...")
    rep, burn = fly(base_engine, ctx)
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

    # Is this still the same rocket the rest of the app flies? Asked every
    # run, against the main simulation's own answer, because a fast simulator
    # that has drifted is worse than a slow one - it gives confident margins
    # for a vehicle nobody owns.
    if reference_apogee_ft:
        run.agreement = ((run.baseline_apogee_ft - reference_apogee_ft)
                         / reference_apogee_ft)
        if abs(run.agreement) > AGREEMENT_LIMIT:
            run.error = (
                f"The tolerance simulator and the main simulation disagree "
                f"about this rocket by {run.agreement * 100:+.1f}% on apogee "
                f"({run.baseline_apogee_ft:,.0f} ft against "
                f"{reference_apogee_ft:,.0f} ft). Margins measured against a "
                f"different flight from the one on the Simulation tab would "
                f"be misleading, so nothing is reported.")
            run.trials = counter["n"]
            return run

    # What 100% is, for each knob, read off that one unperturbed run.
    baselines = {}
    for knob in knobs:
        try:
            baselines[knob.key] = float(knob.observe(burn, base_engine))
        except Exception:
            baselines[knob.key] = 0.0

    if on_progress is not None:
        on_progress(1, len(knobs) + 2,
                    "Finding which of these the rocket cares about most...")
    ranked = rank_by_sensitivity(
        base_engine, ctx, knobs, baselines, run.baseline_apogee_ft,
        on_probe=(lambda k, pct: on_progress(
            1, len(knobs) + 2,
            f"{k.component}: {k.quantity} moves apogee {pct:+.1f}%")
            if on_progress is not None else None),
        should_cancel=should_cancel)
    counter["n"] += len(ranked)
    run.ranking = list(ranked)

    for i, (knob, pct) in enumerate(ranked, start=2):
        if should_cancel is not None and should_cancel():
            run.cancelled = True
            break
        if on_progress is not None:
            on_progress(i, len(knobs) + 2,
                        f"{knob.component}: {knob.quantity}")
        result = measure(knob, base_engine, ctx, run.goals,
                         run.baseline_apogee_ft, baselines.get(knob.key),
                         on_trial=counted_trial, should_cancel=should_cancel)
        result.sensitivity_pct = pct
        run.results.append(result)
        if result.error == "cancelled":
            run.cancelled = True
            break

    run.trials = counter["n"]
    if on_progress is not None:
        on_progress(len(knobs) + 2, len(knobs) + 2, "Done.")
    return run
