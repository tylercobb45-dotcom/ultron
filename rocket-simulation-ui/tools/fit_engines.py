#!/usr/bin/env python3
"""Fit Engine Lab configurations to published HyperTEK motor performance.

What is fitted and what is not
------------------------------
Anything HyperTEK publishes is LOCKED and never moves:

    tank internal volume   from the motor designation (440 / 835 / 1685 cc)
    tank diameter/length   from the volume and the vent-tube length
    injector orifice       from the designation (.172 / .125 inch), one hole
    case outer diameter    54 mm or 75 mm system

Only the internals HyperTEK does not publish are free:

    fuel grain length, outer diameter and initial port
    nozzle throat diameter and expansion ratio
    c*/nozzle efficiency
    tank fill fraction at ignition

The free parameters still decouple physically, so this is a proportional
correction loop followed by a bounded Nelder-Mead polish:

    fill fraction sets oxidizer load, and with the locked tank, propellant mass
    grain geometry sets fuel mass and burn area
    c*/nozzle efficiency sets Isp, and therefore total impulse
    throat area   sets chamber pressure

Chamber pressure is constrained to the vendor's own operating band rather than
a generic guess: HyperTEK states the self-pressurising N2O sits at 650-750 psi
and drives "initial chamber pressures of up to about 550 psi", so the fit is
held near 3.6 MPa and penalised outside 3.0-3.9 MPa.

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
from hybrid_sim.config import Fuel
import presets as preset_defs

TARGETS = preset_defs.ENGINE_REFERENCE
SEEDS = preset_defs.ENGINE_FITS
HARDWARE = preset_defs.ENGINE_HARDWARE

PC_TARGET = 3.6e6
PC_LO, PC_HI = 3.0e6, 3.9e6


def locked(name):
    """The published values for this motor, which the fit may not change."""
    return preset_defs.hardware_geometry(name)


# HyperTEK grains are a moulded proprietary thermoplastic, not HTPB, and the
# regression law of that material is not published. ABS is the closest
# published thermoplastic, so it supplies the density and the flux exponent,
# and the regression COEFFICIENT is fitted - one unpublished material property,
# shared by every motor in the line.
_BASE = FUELS["ABS"]


def make_fuel(a):
    return Fuel("HyperTEK thermoplastic", _BASE.rho, a, _BASE.n,
                _BASE.of_shift, _BASE.cstar_scale)


def measure(params):
    fuel = make_fuel(params.get("fuel_a", _BASE.a))
    eng = Engine(fill_frac=params.get("fill_frac", 0.85), T_tank_0=293,
                 n_holes=params.get("n_holes", 1), fuel=fuel,
                 **{k: v for k, v in params.items()
                    if k not in ("n_holes", "fill_frac", "fuel_a")})
    res = EngineModel(eng).run()
    m = hsm(res)
    return dict(impulse=m["total_impulse"], burn=m["burn_time"],
                peak=m["peak_thrust"], prop=m["prop_mass"],
                pc=m["peak_Pc"], isp=m["isp"],
                ptank=float(res["P_tank"][0]))


def _clamp(name, p):
    """Re-apply the published values and keep the free ones buildable."""
    p.update(locked(name))
    case_od = HARDWARE[name]["case_od"]
    # The grain is a moulded liner inside the case, so it cannot be wider than
    # the case, and needs a wall left to hold pressure.
    p["d_grain_outer"] = min(max(p["d_grain_outer"], 0.020), case_od - 0.004)
    p["d_port_0"] = min(max(p["d_port_0"], 0.008), p["d_grain_outer"] * 0.92)
    p["d_throat"] = min(max(p["d_throat"], 0.004), 0.030)
    # The grain has to fit in the motor the vendor sells. Overall length minus
    # the tank, minus an allowance for the injector bell and nozzle.
    ml = HARDWARE[name].get("motor_length")
    hi = 0.70 if not ml else max(0.08, ml - p["L_tank"] - 0.060)
    p["L_grain"] = min(max(p["L_grain"], 0.08), hi)
    p["eps_exp"] = min(max(p["eps_exp"], 2.0), 8.0)
    p["fill_frac"] = min(0.95, max(0.55, p.get("fill_frac", 0.85)))
    if "d_hole" not in locked(name):          # only the L550 floats here
        p["d_hole"] = min(max(p["d_hole"], 0.0015), 0.010)
    p["eta_cstar"] = min(0.97, max(0.70, p["eta_cstar"]))
    p["eta_nozzle"] = p["eta_cstar"]
    # Effective evaporative cooling. The 0.16 default was calibrated on an 8 L
    # tank; these are 0.44-1.7 L, with far more wall area per kg of liquid to
    # feed heat back in, so a lower value here is physics rather than fudge.
    p["cooling_coeff"] = min(0.30, max(0.02, p.get("cooling_coeff", 0.16)))
    # Injector DISCHARGE coefficient. The orifice *diameter* is published and
    # locked; how efficiently that hole actually flows is not. For N2O this is
    # not the textbook 0.7 of a water orifice: the liquid flashes to vapour as
    # it crosses the hole, and the resulting two-phase choking cuts the
    # effective discharge coefficient well below the single-phase value.
    p["Cd_inj"] = min(0.85, max(0.30, p.get("Cd_inj", 0.7)))
    p["fuel_a"] = min(4.0e-4, max(2.0e-5, p.get("fuel_a", _BASE.a)))
    return p


def polish(name, seed, evals=600):
    """Bounded Nelder-Mead over the unpublished internals only."""
    from scipy.optimize import minimize
    want = TARGETS[name]
    isp_want = want["impulse"] / (want["prop"] * 9.80665)
    keys = ["fill_frac", "L_grain", "d_port_0", "d_grain_outer",
            "d_throat", "eps_exp", "eta_cstar", "cooling_coeff", "Cd_inj",
            "fuel_a"]
    if "d_hole" not in locked(name):
        keys.append("d_hole")
    base = _clamp(name, dict(seed))
    x0 = [base[k] for k in keys]
    scale = [abs(v) if abs(v) > 1e-9 else 1e-3 for v in x0]

    def unpack(x):
        p = dict(base)
        for k, v, s in zip(keys, x, scale):
            p[k] = abs(v * s)
        return _clamp(name, p)

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
        pc = got["pc"]
        if pc < PC_LO:
            terms.append(6.0 * ((PC_LO - pc) / PC_LO) ** 2)
        elif pc > PC_HI:
            terms.append(6.0 * ((pc - PC_HI) / PC_HI) ** 2)
        else:
            terms.append(0.25 * ((pc - PC_TARGET) / PC_TARGET) ** 2)
        return sum(terms)

    r = minimize(cost, [1.0] * len(x0), method="Nelder-Mead",
                 options=dict(maxfev=evals, xatol=1e-6, fatol=1e-9))
    return unpack(r.x)


def fit(name, passes=16):
    want = TARGETS[name]
    p = dict(SEEDS[name])
    p.setdefault("cooling_coeff", 0.16)
    p.setdefault("Cd_inj", 0.7)
    p.setdefault("fuel_a", _BASE.a)
    p = _clamp(name, p)
    for _ in range(passes):
        got = measure(p)
        if got["prop"] <= 0 or got["burn"] <= 0:
            break
        # 1. propellant mass <- fill fraction and grain size (tank is locked)
        ratio = want["prop"] / got["prop"]
        p["fill_frac"] *= ratio ** 0.6
        p["L_grain"] *= ratio ** 0.3
        # 2. burn time <- injector discharge coefficient. The orifice
        #    diameter is published, so oxidiser flow is trimmed by how well
        #    that hole flows, not by redrilling it.
        p["Cd_inj"] *= (got["burn"] / want["burn"]) ** 0.5
        if "d_hole" not in locked(name):
            p["d_hole"] *= (got["burn"] / want["burn"]) ** 0.2
        # 3. Isp / impulse <- combustion and nozzle efficiency
        isp_want = want["impulse"] / (want["prop"] * 9.80665)
        if got["isp"] > 0:
            s = (isp_want / got["isp"]) ** 0.5
            p["eta_cstar"] = min(0.97, max(0.70, p["eta_cstar"] * s))
            p["eta_nozzle"] = p["eta_cstar"]
        # 4. throat <- hold chamber pressure in the vendor's stated band
        if got["pc"] > 0:
            p["d_throat"] *= (got["pc"] / PC_TARGET) ** 0.12
        # 5. curve shape <- tank cooling. Too much cooling decays the tank
        #    fast and makes the curve peaky: high peak thrust, low impulse.
        if got["peak"] > 0 and want["peak"] > 0:
            p["cooling_coeff"] *= (want["peak"] / got["peak"]) ** 0.5
        p = _clamp(name, p)
    p = polish(name, p)
    return p, measure(p)


def main():
    fitted = {}
    for name in TARGETS:
        p, got = fit(name)
        fitted[name] = p
        want = TARGETS[name]
        isp_want = want["impulse"] / (want["prop"] * 9.80665)
        print("\n%-16s %9s %9s %9s %9s %9s" %
              (name, "impulse", "burn", "peak", "prop", "Isp"))
        print("%-16s %9.0f %9.2f %9.0f %9.3f %9.1f   published"
              % ("", want["impulse"], want["burn"], want["peak"], want["prop"], isp_want))
        print("%-16s %9.0f %9.2f %9.0f %9.3f %9.1f   model"
              % ("", got["impulse"], got["burn"], got["peak"], got["prop"], got["isp"]))
        print("%-16s %8.1f%% %8.1f%% %8.1f%% %8.1f%% %8.1f%%   error"
              % ("",
                 (got["impulse"] - want["impulse"]) / want["impulse"] * 100,
                 (got["burn"] - want["burn"]) / want["burn"] * 100,
                 (got["peak"] - want["peak"]) / want["peak"] * 100,
                 (got["prop"] - want["prop"]) / want["prop"] * 100,
                 (got["isp"] - isp_want) / isp_want * 100))
        ml = HARDWARE[name].get("motor_length")
        pack = p["L_tank"] + p["L_grain"]
        print("%-16s packaging: tank %.0f + grain %.0f = %.0f mm%s"
              % ("", p["L_tank"]*1000, p["L_grain"]*1000, pack*1000,
                 ("  (vendor overall %.0f mm)" % (ml*1000)) if ml else ""))
        print("%-16s fuel regression a = %.3e (ABS %.3e)"
              % ("", p["fuel_a"], _BASE.a))
        print("%-16s peak Pc %.2f MPa (%.0f psi, vendor ceiling %.0f) | "
              "tank %.2f MPa (%.0f psi, vendor %.0f-%.0f)"
              % ("", got["pc"] / 1e6, got["pc"] / preset_defs.PSI, preset_defs.PC_MAX_PSI,
                 got["ptank"] / 1e6, got["ptank"] / preset_defs.PSI,
                 preset_defs.N2O_TANK_PSI[0], preset_defs.N2O_TANK_PSI[1]))

    print("\n\n# paste into presets.ENGINE_FITS")
    for name, p in fitted.items():
        print('    "%s": dict(' % name)
        print('        d_tank=%.5f, L_tank=%.5f, d_hole=%.5f, n_holes=%d,'
              % (p["d_tank"], p["L_tank"], p["d_hole"], p.get("n_holes", 1)))
        print('        d_throat=%.5f, eps_exp=%.3f, L_grain=%.4f,'
              % (p["d_throat"], p["eps_exp"], p["L_grain"]))
        print('        d_grain_outer=%.4f, d_port_0=%.4f,'
              % (p["d_grain_outer"], p["d_port_0"]))
        print('        eta_cstar=%.3f, eta_nozzle=%.3f, fill_frac=%.3f,'
              % (p["eta_cstar"], p["eta_nozzle"], p["fill_frac"]))
        print('        cooling_coeff=%.4f, Cd_inj=%.4f, fuel_a=%.3e),'
              % (p["cooling_coeff"], p["Cd_inj"], p["fuel_a"]))


if __name__ == "__main__":
    main()
