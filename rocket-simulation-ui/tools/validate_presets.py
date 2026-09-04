#!/usr/bin/env python3
"""Validate the preset engines and rockets against reference data.

Three layers, each checked against something the model was not built from:

  1. ENGINES - the Engine Lab configurations against published HyperTEK
     performance (total impulse, burn time, peak thrust, propellant mass,
     Isp). The reference is certification data for the I260 and the measured
     thrustcurve.org curves for the J317, K240 and L550.

  2. FLIGHT vs INDEPENDENT MODEL - the Goddard baseline against the
     spreadsheet reference carried in hybrid_sim/excel_ref.json, which is a
     separate implementation by a different author.

  3. PHYSICAL BOUNDS - every preset flight against limits that must hold
     regardless of modelling choices: apogee below the drag-free energy
     ceiling, landing speed equal to the closed-form terminal velocity,
     impulse consistent with the motor's designation class.

Run:  python tools/validate_presets.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "hybrid_sim"))

import aero
import atmosphere
import flight_model
import recovery as recovery_mod
import presets as preset_defs
from hybrid_sim import Engine, EngineModel, FUELS, metrics as hsm, n2o

G0 = 9.80665
PASS, FAIL = "PASS", "FAIL"
_results = []


def check(label, ok, detail):
    _results.append((label, ok, detail))
    print(f"  [{PASS if ok else FAIL}] {label:<44} {detail}")
    return ok


def banner(text):
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78)


# ---------------------------------------------------------------------------
# 1. engines vs published performance
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 0. published hardware self-consistency
# ---------------------------------------------------------------------------

def build_engine(fit):
    """An Engine from a preset fit dict, honouring its fuel choice.

    Presets name their fuel; anything not naming one predates the field and
    gets HTPB, which is what it was fitted with.
    """
    skip = ("n_holes", "fill_frac", "fuel")
    return Engine(fill_frac=fit.get("fill_frac", 0.85), T_tank_0=293,
                  n_holes=fit.get("n_holes", 1),
                  fuel=FUELS[fit.get("fuel", "HTPB")],
                  **{k: v for k, v in fit.items() if k not in skip})



def validate_hardware():
    """Check the published numbers against each other, before any modelling.

    None of this involves the simulation. It asks whether the vendor's own
    figures - tank volumes, orifice sizes, propellant masses, burn times -
    tell a consistent story. If they do, the geometry we locked into the fit
    is the real geometry; if they do not, the fit is built on sand.
    """
    banner("0. PUBLISHED HARDWARE SELF-CONSISTENCY (no modelling involved)")

    print("  Tank geometry reproduces the volume in the motor designation:\n")
    for name, hw in preset_defs.ENGINE_HARDWARE.items():
        g = preset_defs.hardware_geometry(name)
        V_cc = math.pi * (g["d_tank"] / 2) ** 2 * g["L_tank"] * 1e6
        err = (V_cc - hw["tank_cc"]) / hw["tank_cc"] * 100
        check(f"    {name}: {V_cc:.0f} cc vs {hw['tank_cc']:.0f} cc "
              f"({hw['designation']})", abs(err) < 0.5, f"{err:+.2f}%")

        # The tank has to physically fit inside the case it ships in.
        check(f"    {name}: tank fits the {hw['case_od']*1000:.0f} mm case",
              g["d_tank"] < hw["case_od"],
              f"tank ID {g['d_tank']*1000:.1f} mm inside "
              f"{hw['case_od']*1000:.0f} mm OD")

    print("\n  Same hardware, different orifice - the strongest check available.")
    print("  The J317 and K240 are the SAME 835 cc motor; only the injector")
    print("  orifice differs (.172 in vs .125 in). If those numbers really are")
    print("  the orifice diameters, and oxidiser flow really scales with")
    print("  injector area, then burn time must scale as the inverse area")
    print("  ratio. Nothing here is fitted - both sides are published.\n")
    j, k = preset_defs.ENGINE_HARDWARE["HyperTEK J317"], preset_defs.ENGINE_HARDWARE["HyperTEK K240"]
    area_ratio = (j["orifice_in"] / k["orifice_in"]) ** 2
    burn_ratio = (preset_defs.ENGINE_REFERENCE["HyperTEK K240"]["burn"]
                  / preset_defs.ENGINE_REFERENCE["HyperTEK J317"]["burn"])
    err = (burn_ratio - area_ratio) / area_ratio * 100
    check(f"    injector area ratio {area_ratio:.4f} vs measured burn-time "
          f"ratio {burn_ratio:.4f}", abs(err) < 5.0, f"{err:+.2f}%")

    print("\n  Vendor-stated pressures:\n")
    lo, hi = preset_defs.N2O_TANK_PSI
    p_tank = float(n2o.psat(293.0)) / preset_defs.PSI
    check(f"    N2O at 293 K self-pressurizes to {p_tank:.0f} psi",
          lo <= p_tank <= hi, f"vendor states {lo:.0f}-{hi:.0f} psi")

    print("\n  Published propellant mass fits the published tank:\n")
    for name, hw in preset_defs.ENGINE_HARDWARE.items():
        ref = preset_defs.ENGINE_REFERENCE[name]
        V = hw["tank_cc"] * 1e-6
        rho = float(n2o.rho_l(293.0))
        # Oxidiser alone, at a plausible fill, must not exceed the total
        # propellant the motor is certified to burn - and must be most of it.
        ox_full = V * rho
        frac = ref["prop"] / ox_full
        check(f"    {name}: {ref['prop']:.3f} kg propellant vs "
              f"{ox_full:.3f} kg tank capacity",
              0.55 <= frac <= 1.35,
              f"ratio {frac:.2f} (ox is most of the propellant, plus fuel)")
    print()


def validate_engines():
    banner("1. ENGINE MODEL vs PUBLISHED MOTOR PERFORMANCE")
    print("   Tolerance: 12% on impulse/propellant/peak, 20% on burn time.")
    print("   Burn time is the loosest because the published figure uses the")
    print("   5%-of-peak certification cutoff and a hybrid's blowdown tail is")
    print("   long and shallow - a small threshold difference moves it a lot.\n")
    for name, fit in preset_defs.ENGINE_FITS.items():
        ref = preset_defs.ENGINE_REFERENCE[name]
        eng = build_engine(fit)
        m = hsm(EngineModel(eng).run())
        got = dict(impulse=m["total_impulse"], burn=m["burn_time"],
                   peak=m["peak_thrust"], prop=m["prop_mass"])
        pc_mpa = m["peak_Pc"] / 1e6
        print(f"  {name}   (reference: {ref['source']})")
        for key, tol in (("impulse", 12), ("prop", 12), ("peak", 12), ("burn", 20)):
            err = (got[key] - ref[key]) / ref[key] * 100
            check(f"    {key}: {got[key]:.2f} vs {ref[key]:.2f} published",
                  abs(err) <= tol, f"{err:+.1f}%  (tol {tol}%)")
        isp_ref = ref["impulse"] / (ref["prop"] * G0)
        isp_got = got["impulse"] / (got["prop"] * G0)
        err = (isp_got - isp_ref) / isp_ref * 100
        check(f"    Isp: {isp_got:.1f} s vs {isp_ref:.1f} s published",
              abs(err) <= 15, f"{err:+.1f}%  (tol 15%)")
        check(f"    peak chamber pressure buildable",
              2.0 <= pc_mpa <= 5.0,
              f"{pc_mpa:.2f} MPa ({pc_mpa*145.038:.0f} psi), band 2.0-5.0 MPa")
        print()


# ---------------------------------------------------------------------------
# 2. flight vs the independent spreadsheet model
# ---------------------------------------------------------------------------

def load_profile(name):
    path = os.path.join(ROOT, "src", "profiles", f"{name}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fly_preset(preset):
    """Run a preset through the same flight model the app uses."""
    curve_path = os.path.join(ROOT, preset["thrust_curve"])
    points, _prop = flight_model.load_thrust_curve(curve_path)
    if not points:
        raise SystemExit(f"no thrust curve at {curve_path}")
    a = preset["airframe"]
    airframe = aero.Airframe(
        nose_shape=a["nose_shape"], nose_length_m=a["nose_length_m"],
        body_diameter_m=a["body_diameter_m"], body_length_m=a["body_length_m"],
        surface_roughness_um=a["surface_roughness_um"],
        fin_count=a["fin_count"], fin_root_chord_m=a["fin_root_chord_m"],
        fin_tip_chord_m=a["fin_tip_chord_m"], fin_span_m=a["fin_span_m"],
        fin_sweep_m=a["fin_sweep_m"], fin_thickness_m=a["fin_thickness_m"])
    site = atmosphere.LaunchSite(
        elevation_m=a["elevation_m"], temperature_c=a["temperature_c"],
        wind_speed_ms=a["wind_speed_ms"], rail_length_m=a["rail_length_m"],
        rail_angle_deg=a["rail_angle_deg"])
    mass = flight_model.MassProperties(
        dry_mass_kg=a["dry_mass_kg"], propellant_mass_kg=a["propellant_mass_kg"],
        dry_cg_m=a["dry_cg_m"], propellant_cg_m=a["propellant_cg_m"])
    spec = preset["recovery_spec"]
    if spec.get("kind") == "single":
        system = recovery_mod.RecoverySystem.single_deploy(diameter_m=spec["main_d"])
    else:
        system = recovery_mod.RecoverySystem.dual_deploy(
            drogue_d=spec["drogue_d"], main_d=spec["main_d"],
            main_altitude_m=spec["main_alt"])
    cd = a.get("cd_override") or None
    rows, summary = flight_model.run_flight(points, airframe, site, system, mass,
                                            cd_override=cd)
    return rows, summary, airframe, site, mass, system, points


def validate_goddard(rows, summary, preset):
    banner("2. GODDARD FLIGHT vs INDEPENDENT SPREADSHEET REFERENCE")
    with open(os.path.join(ROOT, "hybrid_sim", "excel_ref.json"), encoding="utf-8") as f:
        ref = json.load(f)["summary"]
    max_mach = max(r["Mach"] for r in rows)
    v_max = max(r["velocity"] for r in rows)
    print("   The spreadsheet is a separate implementation by another author,")
    print("   flown with a measured drag coefficient of 1.625. Matching it")
    print("   tests this model's integrator, atmosphere and mass bookkeeping.\n")
    for label, got, want, tol in (
        ("apogee (ft)", summary["apogee_ft"], ref["Apogee (ft)"], 5),
        ("max velocity (m/s)", v_max, ref["Max velocity"], 5),
        ("max Mach", max_mach, ref["Max Mach"], 5),
        ("time to apogee (s)", summary_time_to_apogee(rows), ref["Time to apogee"], 8),
    ):
        err = (got - want) / want * 100
        check(f"  {label}: {got:.2f} vs {want:.2f}", abs(err) <= tol,
              f"{err:+.1f}% (tol {tol}%)")

    # And the same vehicle flown on this model's own drag estimate, to show
    # how much of the answer the drag coefficient alone is worth.
    rows2, summary2, *_ = fly_preset(dict(preset, airframe=dict(
        preset["airframe"], cd_override=0.0)))
    import aero as _aero
    af = _aero.Airframe(nose_shape=preset["airframe"]["nose_shape"],
                        nose_length_m=preset["airframe"]["nose_length_m"],
                        body_diameter_m=preset["airframe"]["body_diameter_m"],
                        body_length_m=preset["airframe"]["body_length_m"])
    import atmosphere as _atm
    cd_est, _ = _aero.drag_coefficient(0.5, 500, 170, af, _atm.LaunchSite())
    print()
    print("   Flown instead on this model's computed drag (Cd ~ %.2f rather than" % cd_est)
    print("   the reference's measured 1.625), the same vehicle reaches")
    print("   %.0f ft - %+.0f%%. That gap is the drag coefficient, not the" % (
        summary2["apogee_ft"],
        (summary2["apogee_ft"] - summary["apogee_ft"]) / summary["apogee_ft"] * 100))
    print("   flight model: a 3.1x difference in drag area moves apogee that far.")


def summary_time_to_apogee(rows):
    best = max(rows, key=lambda r: r["altitude"])
    return best["time"]


# ---------------------------------------------------------------------------
# 3. physical bounds that must hold for every preset
# ---------------------------------------------------------------------------

def validate_bounds(name, rows, summary, airframe, site, mass, system, points):
    v_max = max(r["velocity"] for r in rows)
    burnout = max((r for r in rows if r["thrust"] > 0.05 *
                   max(p[1] for p in points)), key=lambda r: r["time"], default=rows[0])

    # Drag-free ceiling: a rocket cannot coast higher than its burnout kinetic
    # energy allows, ignoring drag entirely.
    ceiling = burnout["altitude"] + v_max ** 2 / (2 * G0)
    check(f"  {name}: apogee below drag-free ceiling",
          summary["apogee_m"] <= ceiling * 1.02,
          f"{summary['apogee_m']:.0f} m <= {ceiling:.0f} m")

    # Landing speed must match the closed-form terminal velocity under the
    # fully open recovery system.
    airborne = [r for r in rows if r["altitude"] > 0]
    v_land = abs(airborne[-1]["velocity"]) if airborne else 0.0
    _t, _p, rho, _a, _mu = site.properties(0.0)
    cda = sum(s.full_drag_area for s in system.active_stages())
    v_term = math.sqrt(2 * mass.dry_mass_kg * G0 / (rho * cda)) if cda > 0 else 0.0
    err = (v_land - v_term) / v_term * 100 if v_term else 0.0
    check(f"  {name}: landing speed matches terminal velocity",
          abs(err) <= 12, f"{v_land:.2f} vs {v_term:.2f} m/s ({err:+.1f}%)")

    # Mass must be conserved: burnout mass equals dry mass.
    err_m = abs(burnout["mass"] - (mass.dry_mass_kg + burnout["propellant_remaining"]))
    check(f"  {name}: mass bookkeeping closes", err_m < 1e-6,
          f"residual {err_m:.2e} kg")

    # Stability must never go negative during boost or the flight is fiction.
    boost = [r for r in rows if r["thrust"] > 0]
    worst = min((r["stability_cal"] for r in boost), default=0.0)
    check(f"  {name}: statically stable throughout boost", worst > 0,
          f"minimum margin {worst:.2f} cal")


# ---------------------------------------------------------------------------
# 5. Cd(Mach) sweep and table
# ---------------------------------------------------------------------------

def validate_aero_sweep():
    """The drag curve and the table that can replace it.

    The sweep is checked for shape rather than against an external number:
    a transonic peak in the right place, of roughly the right size, falling
    away supersonically. Those are properties every published rocket drag
    curve has, so a model that violates them is wrong regardless of what its
    absolute values are.
    """
    banner("5. Cd(Mach) SWEEP AND TABLE")

    preset = next(p for p in preset_defs.PRESET_ROCKETS if "L550" in p["name"])
    _rows, _summary, airframe, site, mass, system, points = fly_preset(preset)

    sweep = aero.drag_sweep(airframe, site, altitude_m=0.0,
                            mach_min=0.05, mach_max=5.0, mach_step=0.05,
                            cg_m=mass.dry_cg_m)
    check("  sweep produces a full curve", len(sweep) > 90,
          f"{len(sweep)} points, Mach {sweep[0]['mach']:.2f}-{sweep[-1]['mach']:.2f}")

    sub = [r["cd_power_off"] for r in sweep if r["mach"] <= 0.3]
    cd_sub = sum(sub) / len(sub)
    peak = max(sweep, key=lambda r: r["cd_power_off"])
    check("  transonic peak sits just past Mach 1",
          0.9 <= peak["mach"] <= 1.4,
          f"peak at Mach {peak['mach']:.2f}")
    rise = peak["cd_power_off"] / cd_sub - 1.0
    check("  transonic rise is the published 30-120% of subsonic Cd",
          0.30 <= rise <= 1.20,
          f"{rise*100:.0f}% (subsonic {cd_sub:.3f} -> peak {peak['cd_power_off']:.3f})")

    suparr = [r for r in sweep if r["mach"] >= 2.0]
    falling = all(suparr[i]["cd_power_off"] >= suparr[i + 1]["cd_power_off"] - 1e-9
                  for i in range(len(suparr) - 1))
    check("  Cd falls away monotonically above Mach 2", falling,
          f"{suparr[0]['cd_power_off']:.3f} at Mach 2 -> "
          f"{suparr[-1]['cd_power_off']:.3f} at Mach {suparr[-1]['mach']:.1f}")

    on_le_off = all(r["cd_power_on"] <= r["cd_power_off"] + 1e-9 for r in sweep)
    check("  power-on Cd never exceeds power-off", on_le_off,
          "the exhaust plume fills the base, it cannot add base drag")

    # Table round-trip and interpolation
    table = aero.CdMachTable([(r["mach"], r["cd_power_off"]) for r in sweep],
                             "sweep")
    tmp = os.path.join(tempfile.gettempdir(), "jarvis_cd_check.csv")
    table.to_csv(tmp)
    back = aero.CdMachTable.from_csv(tmp)
    os.remove(tmp)
    worst = max(abs(back(r["mach"]) - r["cd_power_off"]) for r in sweep)
    check("  Cd(Mach) table round-trips through CSV", worst < 1e-4,
          f"worst node error {worst:.2e}")

    lo, hi = table.mach_range
    flat_ok = (abs(table(lo - 1.0) - table(lo)) < 1e-12
               and abs(table(hi + 3.0) - table(hi)) < 1e-12)
    check("  table holds flat outside its range rather than extrapolating",
          flat_ok, "extrapolating a drag curve invents confident wrong answers")

    # End to end: a table of one constant must fly identically to that constant.
    flat = aero.CdMachTable([(0.0, 0.55), (5.0, 0.55)], "flat 0.55")
    system.reset()
    _r1, s1 = flight_model.run_flight(points, airframe, site, system, mass,
                                      output_dt=0.05, cd_override=0.55)
    system.reset()
    _r2, s2 = flight_model.run_flight(points, airframe, site, system, mass,
                                      output_dt=0.05, cd_override=flat)
    err = abs(s2["apogee_ft"] - s1["apogee_ft"]) / max(1.0, s1["apogee_ft"])
    check("  a flat table flies identically to the same constant Cd",
          err < 1e-9,
          f"{s1['apogee_ft']:,.1f} ft vs {s2['apogee_ft']:,.1f} ft")

    # And a real curve must differ from a constant, or the feature does nothing.
    system.reset()
    _r3, s3 = flight_model.run_flight(points, airframe, site, system, mass,
                                      output_dt=0.05, cd_override=table)
    diff = (s1["apogee_ft"] - s3["apogee_ft"]) / s3["apogee_ft"]
    check("  the curve changes the answer against a constant Cd",
          abs(diff) > 0.02,
          f"constant 0.55 gives {s1['apogee_ft']:,.0f} ft, the curve "
          f"{s3['apogee_ft']:,.0f} ft ({diff*100:+.1f}%)")
    print()


# ---------------------------------------------------------------------------
# 6. drag model internals vs the correlations they claim to implement
# ---------------------------------------------------------------------------

def validate_drag_internals():
    """Check each drag component against the published form it is built on.

    Section 5 checks that the assembled curve has the right SHAPE. This checks
    that each piece is the correlation it says it is, evaluated against the
    closed form by hand. Between them they cover "is the changing Cd right":
    correct pieces, assembled into a curve with the right shape.

    What this cannot do is compare an absolute Cd against a measured drag
    curve for these airframes, because no such measurement exists here. That
    limit is stated in docs/VALIDATION.md and is why the Aero tab imports
    external tables.
    """
    banner("6. DRAG COMPONENTS vs THEIR SOURCE CORRELATIONS")

    # --- skin friction ---
    print("  Skin friction: Blasius laminar, Prandtl-Schlichting turbulent.\n")
    re = 1.0e5
    want = 1.328 / math.sqrt(re)
    got = aero.skin_friction_coefficient(re, 0.0, 0.0, 1.0)
    check("    laminar Cf = 1.328/sqrt(Re) at Re=1e5",
          abs(got - want) < 1e-9, f"{got:.6e} vs {want:.6e}")

    re = 1.0e7
    want = 0.455 / (math.log10(re) ** 2.58)
    got = aero.skin_friction_coefficient(re, 0.0, 0.0, 1.0)
    check("    turbulent Cf = 0.455/(log10 Re)^2.58 at Re=1e7",
          abs(got - want) < 1e-9, f"{got:.6e} vs {want:.6e}")

    # Roughness floor: Cf = 0.032 (Rs/L)^0.2, the standard Barrowman form.
    rough, length = 20e-6, 2.0
    want = 0.032 * (rough / length) ** 0.2
    got = aero.skin_friction_coefficient(1.0e12, 0.0, rough, length)
    check("    roughness floor Cf = 0.032 (Rs/L)^0.2 binds at high Re",
          abs(got - want) < 1e-9, f"{got:.6e} vs {want:.6e}")

    smooth = aero.skin_friction_coefficient(1.0e7, 0.0, 1e-9, 2.0)
    rougher = aero.skin_friction_coefficient(1.0e7, 0.0, 60e-6, 2.0)
    check("    a rougher airframe never has less friction than a smooth one",
          rougher >= smooth, f"{rougher:.6e} >= {smooth:.6e}")

    cfs = [aero.skin_friction_coefficient(r, 0.0, 0.0, 1.0)
           for r in (1e6, 1e7, 1e8, 1e9)]
    check("    turbulent Cf falls monotonically with Reynolds",
          all(cfs[i] > cfs[i + 1] for i in range(len(cfs) - 1)),
          " -> ".join(f"{c:.2e}" for c in cfs))

    comp = [aero.skin_friction_coefficient(1e7, m, 0.0, 1.0)
            for m in (0.0, 1.0, 3.0, 5.0)]
    check("    compressibility reduces friction monotonically with Mach",
          all(comp[i] > comp[i + 1] for i in range(len(comp) - 1)),
          " -> ".join(f"{c:.2e}" for c in comp))

    # --- base drag ---
    print("\n  Base drag: the standard 0.12 + 0.13 M^2 / 0.25 M correlation.\n")
    b0 = aero.base_drag_coefficient(0.0, False)
    check("    subsonic base Cd at M=0 is 0.12",
          abs(b0 - 0.12) < 1e-12, f"{b0:.4f}")
    # Cross-check against Hoerner's Cd_base = 0.029/sqrt(Cd_friction), with a
    # representative airframe friction coefficient.
    hoerner = 0.029 / math.sqrt(0.05)
    check("    and agrees with Hoerner 0.029/sqrt(Cf) to within 20%",
          abs(b0 - hoerner) / hoerner < 0.20,
          f"{b0:.4f} vs {hoerner:.4f} at Cf=0.05")

    lo = aero.base_drag_coefficient(0.999999, False)
    hi = aero.base_drag_coefficient(1.000001, False)
    check("    subsonic and supersonic branches meet at Mach 1",
          abs(lo - hi) < 1e-5, f"{lo:.6f} vs {hi:.6f}")

    on = aero.base_drag_coefficient(0.5, True)
    off = aero.base_drag_coefficient(0.5, False)
    check("    the plume reduces base drag while thrusting", on < off,
          f"{on:.4f} thrusting vs {off:.4f} coasting")

    # --- wave drag ---
    print("\n  Nose wave drag: zero below drag divergence, peak just past M=1.\n")
    af = aero.Airframe(nose_shape="Von Karman (LV-Haack)", nose_length_m=0.42,
                       body_diameter_m=0.076)
    check("    zero below drag divergence",
          aero.wave_drag_coefficient(0.5, af) == 0.0, "Cd_wave = 0 at Mach 0.5")
    peak_m = max((m / 100.0 for m in range(80, 300)),
                 key=lambda m: aero.wave_drag_coefficient(m, af))
    check("    peaks in the transonic band", 1.0 <= peak_m <= 1.3,
          f"peak at Mach {peak_m:.2f}")

    blunt = aero.Airframe(nose_shape="Hemispherical", nose_length_m=0.42,
                          body_diameter_m=0.076)
    check("    a blunt nose pays more wave drag than a Von Karman",
          aero.wave_drag_coefficient(1.2, blunt) > aero.wave_drag_coefficient(1.2, af),
          f"{aero.wave_drag_coefficient(1.2, blunt):.4f} vs "
          f"{aero.wave_drag_coefficient(1.2, af):.4f} at Mach 1.2")

    short = aero.Airframe(nose_shape="Tangent Ogive", nose_length_m=0.15,
                          body_diameter_m=0.076)
    long_ = aero.Airframe(nose_shape="Tangent Ogive", nose_length_m=0.60,
                          body_diameter_m=0.076)
    check("    a finer nose pays less wave drag",
          aero.wave_drag_coefficient(1.2, long_) < aero.wave_drag_coefficient(1.2, short),
          f"fineness 7.9 -> {aero.wave_drag_coefficient(1.2, long_):.4f}, "
          f"fineness 2.0 -> {aero.wave_drag_coefficient(1.2, short):.4f}")

    # --- assembled totals land where hobby rockets actually live ---
    print("\n  Assembled subsonic Cd against the range real rockets occupy.\n")
    for preset in preset_defs.PRESET_ROCKETS:
        if preset.get("airframe", {}).get("cd_override"):
            continue          # flown on a measured Cd, not the buildup
        af = _airframe_from_preset(preset)
        site = atmosphere.LaunchSite()
        cd, _parts = aero.drag_coefficient(0.3, 0.0, 0.3 * 340.0, af, site)
        check(f"    {preset['name']}: subsonic Cd in the published 0.3-0.9 band",
              0.30 <= cd <= 0.90, f"Cd = {cd:.3f} at Mach 0.3")
    print()


def _airframe_from_preset(preset):
    af = aero.Airframe()
    for key, value in preset.get("airframe", {}).items():
        if hasattr(af, key):
            setattr(af, key, value)
    return af


def main():
    validate_hardware()
    validate_engines()

    banner("3. PRESET FLIGHTS")
    print("   %-30s %10s %8s %8s %9s %8s" %
          ("preset", "apogee ft", "maxMach", "max g", "rail m/s", "drift m"))
    flights = {}
    for preset in preset_defs.PRESET_ROCKETS:
        rows, summary, airframe, site, mass, system, points = fly_preset(preset)
        flights[preset["name"]] = (rows, summary, airframe, site, mass, system, points)
        print("   %-30s %10.0f %8.2f %8.1f %9.1f %8.0f" % (
            preset["name"][:30], summary["apogee_ft"],
            max(r["Mach"] for r in rows),
            max(r["accel_total"] for r in rows) / G0,
            summary["rail_exit_speed"] or 0.0, abs(summary["drift_m"])))

    goddard = flights.get("SystemsGo Goddard Baseline")
    if goddard:
        validate_goddard(goddard[0], goddard[1],
                         next(p for p in preset_defs.PRESET_ROCKETS
                              if p["name"] == "SystemsGo Goddard Baseline"))

    banner("4. PHYSICAL BOUNDS (must hold regardless of modelling choices)")
    for name, data in flights.items():
        validate_bounds(name, *data)
        print()

    validate_aero_sweep()
    validate_drag_internals()

    banner("SUMMARY")
    passed = sum(1 for _l, ok, _d in _results if ok)
    total = len(_results)
    print(f"  {passed} / {total} checks passed")
    for label, ok, detail in _results:
        if not ok:
            print(f"    FAIL {label.strip()} - {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
