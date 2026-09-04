#!/usr/bin/env python3
"""Fit Engine Lab configurations to published HyperTEK motor performance.

The parameters decouple physically, so this is a short proportional
correction loop rather than a blind optimiser:

    tank volume   sets propellant mass
    injector area sets oxidizer flow, and therefore burn time
    c*/nozzle efficiency sets Isp, and therefore total impulse
    throat area   sets chamber pressure (and only weakly the thrust level,
                  since F = Cf * eta * mdot * c* is driven by mass flow)

Each pass measures the model, corrects each parameter toward its target, and
repeats. Converges in a handful of engine runs per motor.

Run:  python tools/fit_engines.py
"""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "hybrid_sim"))

from hybrid_sim import Engine, EngineModel, FUELS, metrics as hsm
import presets as preset_defs

TARGETS = preset_defs.ENGINE_REFERENCE
SEEDS = preset_defs.ENGINE_FITS


def measure(params):
    eng = Engine(fill_frac=params.get("fill_frac", 0.85), T_tank_0=293,
                 n_holes=params.get("n_holes", 1), Cd_inj=0.7, fuel=FUELS["HTPB"],
                 **{k: v for k, v in params.items()
                    if k not in ("n_holes", "fill_frac")})
    m = hsm(EngineModel(eng).run())
    return dict(impulse=m["total_impulse"], burn=m["burn_time"],
                peak=m["peak_thrust"], prop=m["prop_mass"],
                pc=m["peak_Pc"], isp=m["isp"])


def _clamp_geometry(p):
    """Keep the fit inside buildable hardware.

    Without this the correction loop happily grows a 58 mm tank to 2.4 m to
    chase propellant mass, and a tank that large barely blows down - which
    flattens the thrust curve and destroys the peak-to-average ratio.
    """
    d = p["d_tank"]
    p["L_tank"] = min(max(p["L_tank"], 2.0 * d), 10.0 * d)
    p["d_port_0"] = min(p["d_port_0"], p["d_grain_outer"] * 0.92)
    p["d_throat"] = min(max(p["d_throat"], 0.004), 0.030)
    p["d_hole"] = min(max(p["d_hole"], 0.0015), 0.010)
    return p


def polish(name, seed, evals=420):
    """Bounded Nelder-Mead over the shape-driving parameters, including peak
    thrust, which the proportional loop cannot control on its own."""
    from scipy.optimize import minimize
    want = TARGETS[name]
    isp_want = want["impulse"] / (want["prop"] * 9.80665)
    keys = ["L_tank", "d_hole", "d_throat", "eta_cstar", "L_grain", "fill_frac"]
    base = dict(seed)
    base.setdefault("fill_frac", 0.85)
    x0 = [base[k] for k in keys]

    def unpack(x):
        p = dict(base)
        for k, v in zip(keys, x):
            p[k] = abs(v)
        p["fill_frac"] = min(0.95, max(0.60, p["fill_frac"]))
        p["eta_cstar"] = min(0.97, max(0.70, p["eta_cstar"]))
        p["eta_nozzle"] = p["eta_cstar"]
        p["L_grain"] = min(0.60, max(0.10, p["L_grain"]))
        return _clamp_geometry(p)

    def cost(x):
        try:
            got = measure(unpack(x))
        except Exception:
            return 1e6
        if got["prop"] <= 0 or got["burn"] <= 0:
            return 1e6
        terms = [3.0 * ((got["impulse"] - want["impulse"]) / want["impulse"]) ** 2,
                 3.0 * ((got["prop"] - want["prop"]) / want["prop"]) ** 2,
                 2.0 * ((got["peak"] - want["peak"]) / want["peak"]) ** 2,
                 1.0 * ((got["burn"] - want["burn"]) / want["burn"]) ** 2,
                 1.0 * ((got["isp"] - isp_want) / isp_want) ** 2]
        # A thrust curve alone does not determine internal geometry: the fit
        # will happily reach the right thrust with a 20 mm throat running at
        # 1 MPa, which is not how these motors are built and would make the
        # report's chamber-pressure safety check meaningless. Penalise
        # chamber pressures outside the band real hybrids operate in.
        pc = got["pc"] / 1e6
        if pc < 2.5:
            terms.append(4.0 * ((2.5 - pc) / 2.5) ** 2)
        elif pc > 4.5:
            terms.append(4.0 * ((pc - 4.5) / 4.5) ** 2)
        return sum(terms)

    r = minimize(cost, x0, method="Nelder-Mead",
                 options=dict(maxfev=evals, xatol=1e-5, fatol=1e-8))
    return unpack(r.x)


def fit(name, passes=14):
    want = TARGETS[name]
    p = dict(SEEDS[name])
    for _ in range(passes):
        got = measure(p)
        if got["prop"] <= 0 or got["burn"] <= 0:
            break
        # 1. propellant mass <- tank volume (damped, fuel also contributes)
        p["L_tank"] *= (want["prop"] / got["prop"]) ** 0.75
        # 2. burn time <- injector area (mdot ~ A_inj); area ~ d^2
        p["d_hole"] *= (got["burn"] / want["burn"]) ** 0.4
        # 3. Isp / impulse <- combustion and nozzle efficiency
        isp_want = want["impulse"] / (want["prop"] * 9.80665)
        if got["isp"] > 0:
            scale = (isp_want / got["isp"]) ** 0.5
            p["eta_cstar"] = min(0.97, max(0.70, p["eta_cstar"] * scale))
            p["eta_nozzle"] = min(0.97, max(0.70, p["eta_nozzle"] * scale))
        # 4. throat <- keep peak chamber pressure in a buildable band
        target_pc = 3.2e6
        if got["pc"] > 0:
            p["d_throat"] *= (got["pc"] / target_pc) ** 0.12
        p = _clamp_geometry(p)
    p = polish(name, p)
    return p, measure(p)


def main():
    print("%-16s %9s %9s %9s %9s %9s" %
          ("motor", "impulse", "burn", "peak", "prop", "Isp"))
    fitted = {}
    for name in TARGETS:
        p, got = fit(name)
        fitted[name] = p
        want = TARGETS[name]
        isp_want = want["impulse"] / (want["prop"] * 9.80665)
        print("\n%-16s %9.0f %9.2f %9.0f %9.3f %9.1f   published"
              % (name, want["impulse"], want["burn"], want["peak"], want["prop"], isp_want))
        print("%-16s %9.0f %9.2f %9.0f %9.3f %9.1f   model"
              % ("", got["impulse"], got["burn"], got["peak"], got["prop"], got["isp"]))
        print("%-16s %8.1f%% %8.1f%% %8.1f%% %8.1f%% %8.1f%%   error"
              % ("",
                 (got["impulse"] - want["impulse"]) / want["impulse"] * 100,
                 (got["burn"] - want["burn"]) / want["burn"] * 100,
                 (got["peak"] - want["peak"]) / want["peak"] * 100,
                 (got["prop"] - want["prop"]) / want["prop"] * 100,
                 (got["isp"] - isp_want) / isp_want * 100))
        print("%-16s peak Pc %.2f MPa" % ("", got["pc"] / 1e6))

    print("\n\n# paste into presets.ENGINE_FITS")
    for name, p in fitted.items():
        print('    "%s": dict(' % name)
        print('        d_tank=%.4f, L_tank=%.4f, d_hole=%.5f, d_throat=%.5f,'
              % (p["d_tank"], p["L_tank"], p["d_hole"], p["d_throat"]))
        print('        L_grain=%.4f, d_grain_outer=%.4f, d_port_0=%.4f,'
              % (p["L_grain"], p["d_grain_outer"], p["d_port_0"]))
        print('        eps_exp=%.2f, eta_cstar=%.3f, eta_nozzle=%.3f, fill_frac=%.3f%s),'
              % (p["eps_exp"], p["eta_cstar"], p["eta_nozzle"], p.get("fill_frac", 0.85),
                 ", n_holes=%d" % p["n_holes"] if "n_holes" in p else ""))


if __name__ == "__main__":
    main()
