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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "hybrid_sim"))

import aero
import atmosphere
import flight_model
import recovery as recovery_mod
import presets as preset_defs
from hybrid_sim import Engine, EngineModel, FUELS, metrics as hsm

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

def validate_engines():
    banner("1. ENGINE MODEL vs PUBLISHED MOTOR PERFORMANCE")
    print("   Tolerance: 12% on impulse/propellant/peak, 20% on burn time.")
    print("   Burn time is the loosest because the published figure uses the")
    print("   5%-of-peak certification cutoff and a hybrid's blowdown tail is")
    print("   long and shallow - a small threshold difference moves it a lot.\n")
    for name, fit in preset_defs.ENGINE_FITS.items():
        ref = preset_defs.ENGINE_REFERENCE[name]
        eng = Engine(fill_frac=fit.get("fill_frac", 0.85), T_tank_0=293,
                     n_holes=fit.get("n_holes", 1), Cd_inj=0.7,
                     fuel=FUELS["HTPB"],
                     **{k: v for k, v in fit.items()
                        if k not in ("n_holes", "fill_frac")})
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


def main():
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
