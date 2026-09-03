# hybrid_sim v2.0

N2O/HTPB hybrid rocket engine + 1-DOF flight simulation (Goddard level,
SystemsGo). From-scratch rebuild, verified layer-by-layer against independent
references.

## Run

```bash
python run.py              # baseline engine + flight + plots
python run.py --validate   # vs published HyperTEK cert data
python verify.py           # 27-check verification suite
```

## Verification approach (verify.py)

Every layer is checked against something it was NOT built from:

1. **N2O properties** vs handbook anchors: Psat(25 C)=5.66 MPa, NIST
   rho_l(0 C)=907.4, exact critical-point limits, Clausius-Clapeyron sign.
2. **Nozzle**: exit Mach re-inserted into the area-Mach relation must recover
   the area ratio to 1e-9; Cf inside physical bounds and below vacuum ceiling.
3. **Injector**: zero-flow limits, monotonicity in dP, order-of-magnitude.
4. **Regression**: HTPB rdot lands in the published 0.5-4 mm/s band at
   baseline flux.
5. **Conservation**: integral of mdot equals state-variable drop (<1%) for
   both oxidizer and fuel; Pc never exceeds tank pressure; no post-peak
   thrust oscillation; tank cooling matches the Excel reference within 1 K.
6. **Excel reference**: peak/impulse/Isp/burn within 3-5%, mid-burn curve
   RMS < 5%.
7. **Flight**: apogee/v_max vs Excel, plus a no-drag energy-ceiling bound.

Result: 27/27 pass.

## External validation (published cert data)

| motor | peak | Isp | total impulse |
|---|---|---|---|
| HyperTEK I260 | -3% | -2% | -15% |
| HyperTEK K240 | -2% | +1% | -8% |

Impulse/burn-time spread reflects the vapor-blowdown tail vs the 5% cert
threshold convention.

## Known model characteristics

* The Watson H_v fit reads high vs handbook (~248 vs ~170 kJ/kg at 293 K).
  The tank cooling coefficient (0.16) was calibrated *with* this H_v, so the
  pair is self-consistent: predicted tank cooling matches the validated
  reference within 1 K. Do not change one without re-calibrating the other.
* Tank temperature drops ~80 K over a deep blowdown. That is intentional and
  matches the reference (N2O tanks frost over in reality).
* Excel "Max G = 14.4" was the parachute snatch load; ascent G is ~4-5.

## Baseline (Goddard design)

```
Peak thrust 1315 N   Impulse 11601 N*s   Burn 16.1 s
Peak Pc 535 psi      Isp 196 s           Apogee 9147 ft
v_max 191 m/s        Mach 0.57           Ascent G 4.2
```

## Modules

n2o.py (properties) | config.py (dataclasses, fuel library, c* table) |
engine.py (ODE internal ballistics) | metrics.py | flight.py (ISA, recovery) |
plotting.py | validation.py | verify.py | run.py
