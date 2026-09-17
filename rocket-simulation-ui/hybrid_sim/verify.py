#!/usr/bin/env python3
"""
verify.py -- independent double-checks of every physics layer.

Each check tests the model against something it was NOT built from:
analytic identities, conservation laws, handbook anchor values, and the
row-by-row Excel reference curve.
"""
import sys, json, math, os
# Everything here is located relative to THIS file, not to wherever it was
# run from, so CI (which runs "python hybrid_sim/verify.py" from the project
# root) finds the package and the Excel reference just as a local run does.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REF_JSON = os.path.join(_HERE, "excel_ref.json")
sys.path.insert(0, _HERE)
import numpy as np
from hybrid_sim import Engine, EngineModel, Rocket, FlightModel, FUELS, n2o, metrics
from hybrid_sim.config import G0

PASS = 0; FAIL = 0
def check(name, ok, detail=""):
    global PASS, FAIL
    tag = "PASS" if ok else "FAIL"
    if ok: PASS += 1
    else: FAIL += 1
    print(f"  [{tag}] {name}  {detail}")

print("== 1. N2O properties vs handbook anchors ==")
check("Psat(298.15K) = 5.66 MPa +-1%", abs(float(n2o.psat(298.15))/5.66e6 - 1) < 0.01,
      f"got {float(n2o.psat(298.15))/1e6:.3f}")
check("Psat(Tc) = 7.245 MPa exact", abs(float(n2o.psat(309.57)) - 7.245e6) < 1e3)
check("rho_l(273.15K) = 907.4 (NIST) +-1%", abs(float(n2o.rho_l(273.15))/907.4 - 1) < 0.01,
      f"got {float(n2o.rho_l(273.15)):.1f}")
check("rho_l(Tc) -> 452 (critical)", abs(float(n2o.rho_l(309.57)) - 452) < 1)
check("H_v = 0 exactly at Tc, monotone decreasing",
      float(n2o.h_v(309.57)) == 0.0 and
      float(n2o.h_v(280)) > float(n2o.h_v(295)) > float(n2o.h_v(305)))
check("Clausius-Clapeyron sign: dPsat/dT > 0",
      float(n2o.psat(300)) > float(n2o.psat(290)) > float(n2o.psat(280)))

print("\n== 2. Nozzle: analytic identities ==")
eng = Engine(d_tank=0.100, L_tank=1.019)
em = EngineModel(eng)
g, Me, eps = eng.gamma, em._Me, eng.eps_exp
# invert: plug Me back into area-Mach relation, must recover eps
ar = (1/Me)*((2/(g+1))*(1+(g-1)/2*Me*Me))**((g+1)/(2*(g-1)))
check(f"area-Mach inversion (eps=5): Me={Me:.4f}", abs(ar - eps) < 1e-9, f"A/A*={ar:.6f}")
# Cf bounds: for eps=5, matched-pressure Cf should be ~1.4-1.7 for gamma 1.22
cf = em._cf(3.6e6)
check("Cf in physical range 1.3-1.8 at Pc=3.6MPa", 1.3 < cf < 1.8, f"Cf={cf:.3f}")
# vacuum ceiling: Cf < Gamma*sqrt(2g/(g-1)) + eps*Pe/Pc always
cf_max = eng.Gamma_gam*math.sqrt(2*g/(g-1)) + 0.1
check("Cf below vacuum ceiling", cf < cf_max, f"ceiling~{cf_max:.2f}")

print("\n== 3. Injector: limiting behavior ==")
# SPI limit: at dP=0 flow must be 0; flow must rise with dP
m1, _ = em._mdot_ox(1.0, 293.0, 4.9e6)
m2, _ = em._mdot_ox(1.0, 293.0, 3.0e6)
m3, _ = em._mdot_ox(1.0, 293.0, 1.0e6)
check("mdot monotone in dP", m1 < m2 < m3, f"{m1:.3f} < {m2:.3f} < {m3:.3f}")
check("mdot = 0 with no liquid", em._mdot_ox(0.0, 293.0, 1e5)[0] == 0.0)
# order of magnitude: 4x2.52mm holes, Cd .7, dP~1.4MPa, rho~788 -> ~0.5-0.7 kg/s
check("baseline mdot order ~0.5-0.7 kg/s", 0.4 < m2 < 0.8, f"{m2:.3f}")

print("\n== 4. Regression: Marxman sanity ==")
# G_ox = 0.55/(pi*0.018^2) ~ 540 kg/m2s -> rdot = 4.5e-5 * 540^0.681 ~ 3.3e-3 m/s? 
G = 0.55/(math.pi*0.018**2)
rdot = FUELS["HTPB"].a*G**FUELS["HTPB"].n
check("HTPB rdot ~ 0.5-4 mm/s at baseline flux", 0.5e-3 < rdot < 4e-3,
      f"G={G:.0f} kg/m2s, rdot={rdot*1000:.2f} mm/s")

print("\n== 5. Full burn: conservation & consistency ==")
res = EngineModel(eng).run(n_out=2000)
m = metrics(res)
# mass conservation: integral of mdot_ox == m_ox drop
used_int = float(np.trapezoid(res["mdot_ox"], res["t"]))
used_state = res["m_l0"]+res["m_v0"]-res["m_ox"][-1]
check("oxidizer mass conservation <1%", abs(used_int/used_state - 1) < 0.01,
      f"integral {used_int:.3f} vs state {used_state:.3f} kg")
used_f_int = float(np.trapezoid(res["mdot_fuel"], res["t"]))
used_f_state = res["m_f0"]-res["m_fuel"][-1]
check("fuel mass conservation <1%", abs(used_f_int/used_f_state - 1) < 0.01,
      f"integral {used_f_int:.3f} vs state {used_f_state:.3f} kg")
# Isp cross-check from first principles: Isp ~ eta_n*lambda*Cf*c*_eff/g0
i_pk = int(np.argmax(res["thrust"]))
cstar_eff = eng.eta_cstar*em._cstar(res["OF"][i_pk])
isp_theory = eng.lambda_div*eng.eta_nozzle*res["cf"][i_pk]*cstar_eff/G0
check("Isp vs c*.Cf theory +-8%", abs(m["isp"]/isp_theory - 1) < 0.08,
      f"sim {m['isp']:.0f}s vs theory {isp_theory:.0f}s")
# Pc never exceeds tank pressure
check("Pc <= P_tank always", bool(np.all(res["Pc"] <= res["P_tank"]*0.995 + 1e5)))
# no chuffing: thrust monotone-decreasing after peak (tolerance 1N)
F = res["thrust"]; pk = F.argmax()
check("no post-peak oscillation", int(np.sum(np.diff(F[pk:]) > 1.0)) == 0)
# tank cools but stays physical
dT_py = res["T_tank"][0]-res["T_tank"][-1]
_c = json.load(open(_REF_JSON))["curve"]
_Tt = [p[3] for p in _c if p[3] is not None]
dT_xl = _Tt[0]-min(_Tt)
check("tank cooling matches Excel +-5K", abs(dT_py-dT_xl) < 5,
      f"python {dT_py:.1f}K vs excel {dT_xl:.1f}K")

print("\n== 6. Excel reference cross-check (row-by-row) ==")
ref = json.load(open(_REF_JSON))
s = ref["summary"]
check("peak thrust +-3% of Excel", abs(m["peak_thrust"]/s["Peak thrust"]-1) < 0.03,
      f"{m['peak_thrust']:.0f} vs {s['Peak thrust']:.0f}")
check("total impulse +-3%", abs(m["total_impulse"]/s["Total impulse"]-1) < 0.03,
      f"{m['total_impulse']:.0f} vs {s['Total impulse']:.0f}")
check("Isp +-3%", abs(m["isp"]/s["Avg Isp"]-1) < 0.03, f"{m['isp']:.1f} vs {s['Avg Isp']:.1f}")
check("burn time +-5%", abs(m["burn_time"]/s["Burn time"]-1) < 0.05,
      f"{m['burn_time']:.1f} vs {s['Burn time']:.1f}")
# mid-burn curve agreement (t=2..12s, where both models are in quasi-steady decay)
tt = np.array([p[0] for p in ref["curve"]]); FF = np.array([p[1] for p in ref["curve"]])
sel = (tt >= 2) & (tt <= 12)
Fi = np.interp(tt[sel], res["t"], res["thrust"])
rms = float(np.sqrt(np.mean((Fi/FF[sel]-1)**2)))
check("mid-burn curve RMS deviation <5%", rms < 0.05, f"RMS {rms*100:.1f}%")

print("\n== 7. Flight cross-check ==")
fl = FlightModel(Rocket(m_dry=20.0), res).run()
check("apogee +-3% of Excel", abs(fl["apogee_ft"]/s["Apogee (ft)"]-1) < 0.03,
      f"{fl['apogee_ft']:.0f} vs {s['Apogee (ft)']:.0f} ft")
check("v_max +-2%", abs(fl["v_max"]/s["Max velocity"]-1) < 0.02,
      f"{fl['v_max']:.1f} vs {s['Max velocity']:.1f}")
# energy sanity: apogee < v_max^2/2g + burn-phase altitude (drag only removes energy)
h_ceiling = fl["v_max"]**2/(2*G0) + 0.5*fl["v_max"]*res["t"][-1]
check("apogee below no-drag energy ceiling", fl["apogee_m"] < h_ceiling,
      f"{fl['apogee_m']:.0f} < {h_ceiling:.0f} m")

print("\n== 8. Humble Table 7.9 forms agree with the ones already validated ==")
import math as _m
import engine_equations as _eq

# Humble's regression law reduces to the short form at m = 0.
_ok = all(abs(_eq.regression_rate(G, a, n)
              - _eq.regression_rate_humble(G, a, n, 1.0, 0.0)) < 1e-15
          for G in (50, 200, 540, 900) for a, n in ((1.5e-4, 0.5), (2.3e-5, 0.62)))
check("Humble rdot = a G^n L^m reduces to a G^n at m=0", _ok)

# Eq. (7.102) closed form against stepping the same law.
_a, _n, _D0, _mox, _Lp = 1.5e-4, 0.5, 0.05, 0.30, 0.8
_worst = 0.0
for _T in (1.0, 5.0, 16.0):
    _closed = _eq.port_diameter_after(_D0, _mox, _T, _a, _n, _Lp, 0.0)
    _D, _dt = _D0, 1e-4
    for _ in range(int(_T / _dt)):
        _D += 2.0 * _eq.regression_rate_humble(_mox / (_m.pi * (_D / 2) ** 2),
                                               _a, _n, _Lp, 0.0) * _dt
    _worst = max(_worst, abs(_closed - _D))
check("Humble Eq 7.102 closed form matches a stepped burn", _worst < 1e-6,
      f"worst {_worst*1e6:.3f} um over 16 s")

# F = lambda[mdot ve + (pe-pa)Ae] against F = Cf Pc At, same gas.
_worst = 0.0
for _g, _Tc, _M in ((1.22, 3000.0, 25.0), (1.15, 3300.0, 22.0), (1.25, 2800.0, 28.0)):
    _R = _eq.gas_constant(_M)
    _cs = _m.sqrt(_R * _Tc) / _eq.vandenkerckhove(_g)
    _At = _m.pi * (0.03 / 2) ** 2
    for _eps in (3.0, 6.0, 10.0):
        _Me = _eq.exit_mach(_eps, _g)
        _Pe = _eq.exit_pressure(3.0e6, _Me, _g)
        _ve = _eq.exit_velocity(_Me, _g, _Tc, _M)
        _mdot = 3.0e6 * _At / _cs
        _Fh = _eq.thrust_humble(_mdot, _ve, _Pe, 0.0, _eps * _At, 1.0)
        _Fc = _eq.thrust(_eq.thrust_coefficient(3.0e6, 0.0, _eps, _Me, _g),
                         3.0e6, _At)
        _worst = max(_worst, abs(_Fh - _Fc) / _Fc)
check("Humble thrust form equals the Cf form", _worst < 1e-9,
      f"worst {_worst*100:.2e}%")

# The total-flux iteration converges.
_G, _rd, _mf, _it = _eq.converge_total_flux(0.5, _eq.port_area(1, 0.030),
                                            _eq.burn_area(1, 0.030, 0.8),
                                            920.0, 1.5e-4, 0.5, 0.8, 0.0)
check("Humble total-flux iteration converges", 0 < _it < 20 and _G > 0,
      f"{_it} iterations, G={_G:.0f} kg/m2s")

print(f"\n{'='*46}\n  {PASS} passed, {FAIL} failed\n{'='*46}")
sys.exit(1 if FAIL else 0)
