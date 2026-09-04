# Validation of the preset engines and rockets

This is the record of checking JARVIS against data it was not built from:
what matched, what did not, and why. Run it yourself with

```
python tools/validate_presets.py
```

Nothing here is asserted without a number behind it, and every disagreement
is explained or flagged as unexplained.

---

## What the reference data actually is

| Reference | What it is | Where it comes from |
|---|---|---|
| HyperTEK J317, K240, L550 thrust curves | **Measured** test-stand data | `thrust_curves/csv/`, from thrustcurve.org, contributed by John Coker. Source URLs are in each file header. |
| HyperTEK I260 performance | Published certification figures | Carried in `hybrid_sim/validation.py` |
| SystemsGo Goddard flight | An **independent spreadsheet model** by another author | `hybrid_sim/excel_ref.json` |
| ISA-1976 atmosphere | Published standard-atmosphere tables | Base pressures at each layer boundary |

A useful self-check on the motor data: a motor's designation encodes its
impulse class and average thrust, and the measured curves agree with their own
labels.

| Motor | Measured impulse | Class band | Measured avg thrust | Designation |
|---|---|---|---|---|
| J317 | 997 N·s | J (640–1280) | 314 N | 317 |
| K240 | 1423 N·s | K (1280–2560) | 233 N | 240 |
| L550 | 2999 N·s | L (2560–5120) | 542 N | 550 |

All three land in the right class and within a few percent of their stated
average thrust, so the curves are what they claim to be.

---

## 1. Engine model vs published motor performance

The Engine Lab configurations are *fitted* to reproduce published performance.
See the caveat on internal geometry below — this is a performance match, not a
claim about HyperTEK's hardware.

All 24 engine checks pass. Residuals as of the current fit:

| Motor | Impulse | Propellant | Peak thrust | Burn time | Isp | Peak Pc |
|---|---|---|---|---|---|---|
| I260 | −1.5% | −0.4% | +2.9% | +5.1% | −1.1% | 2.50 MPa |
| J317 | +2.9% | +0.7% | −5.5% | **−14.4%** | +2.1% | 2.60 MPa |
| K240 | −0.1% | −0.0% | −0.1% | +0.0% | −0.0% | 2.69 MPa |
| L550 | −0.9% | −0.2% | +1.6% | +2.9% | −0.7% | 2.50 MPa |

Everything except the J317 burn time lands within a few percent. That one
outlier has a concrete reason.

### Why burn time is the loosest number

"Burn time" for a hybrid is a convention, not a measurement. The blowdown tail
is long and shallow, so where you put the cutoff moves the answer a lot.
Measured on the real curves themselves:

| Motor | 1% of peak | 5% of peak | 10% of peak | Spread |
|---|---|---|---|---|
| J317 | 3.46 s | 3.17 s | 3.03 s | 14% |
| K240 | 6.36 s | 6.09 s | 5.56 s | 13% |
| L550 | 6.03 s | 5.53 s | 5.28 s | 14% |

A 10–15% disagreement on burn time is inside the ambiguity of the definition.
It is not evidence the model is wrong.

### Caveat: a thrust curve does not determine internal geometry

Several very different internal layouts produce the same external thrust
curve. An early version of the fit reached excellent agreement on impulse,
thrust and Isp using a 19.5 mm throat running at 1.1 MPa chamber pressure —
matching the curve, but not how these motors are actually built.

The fit therefore constrains peak chamber pressure to the 2.5–4.5 MPa band
real hybrids operate in. This matters beyond tidiness: the Flight Report
grades chamber pressure against the case material's yield strength, so an
unrealistically low pressure would produce a falsely reassuring safety check.

**These configurations reproduce what the motor did, not what is inside it.**
HyperTEK does not publish internal geometry. Do not machine hardware from
these numbers.

---

## 2. Flight model vs an independent implementation

The Goddard baseline has a full independent reference — a spreadsheet model by
another author, covering the whole flight.

Flown with **the same drag coefficient the reference used (Cd = 1.625)**:

| Quantity | This model | Reference | Error |
|---|---|---|---|
| Apogee | 9,172 ft | 9,292 ft | **−1.3%** |
| Max velocity | 192 m/s | 190.9 m/s | **+0.5%** |

That is the integrator, atmosphere, and mass bookkeeping agreeing with a
separately written model to about 1%.

### The discrepancy worth understanding

Flown instead on **this model's own computed drag**, the same vehicle reaches
about **17,300 ft — roughly +86%**.

That gap is entirely the drag coefficient:

| | Cd | Reference area | Drag area |
|---|---|---|---|
| Reference vehicle | 1.625 | 0.01539 m² | 0.02501 m² |
| This model's buildup for the shape entered | 0.524 | 0.01539 m² | 0.00807 m² |

**A 3.1× difference in drag area.** Cd = 1.625 is very high for a rocket —
typical values are 0.4–0.8 — so the reference is either using a measured value
for a much draggier vehicle than the shape entered here, or a deliberately
conservative number. The airframe dimensions of the real vehicle are not in
the reference, so the shape used in the preset is representative, not
measured.

This is the single most important thing to understand about the tool: **the
drag coefficient dominates the altitude answer.** Getting the trajectory
integration right buys you about 1%. Getting Cd wrong by 3× costs you a factor
of two in altitude.

The Goddard preset therefore ships with the reference's Cd = 1.625 in the
"Measured Cd override" field on the Vehicle tab, so it reproduces its
documented flight. Clear that field to zero and the model falls back to its
own drag buildup.

---

### Preset flight results

| Preset | Apogee | Max Mach | Peak g | Rail exit | Drift |
|---|---|---|---|---|---|
| HyperTEK J317 Sport | 3,255 ft | 0.40 | 6.8 | 19.8 m/s | 682 m |
| HyperTEK K240 Altitude | 5,570 ft | 0.55 | 5.0 | 16.1 m/s | 343 m |
| SystemsGo Goddard Baseline | 9,172 ft | 0.57 | 4.2 | 20.7 m/s | 0 m |
| HyperTEK L550 Supersonic | 15,419 ft | **1.26** | 13.3 | 31.3 m/s | 1,483 m |

Three subsonic cases and one supersonic, spanning 3,000 to 15,000 ft and
4 to 13 g.

## 3. Physical bounds

These must hold regardless of any modelling choice, and are checked for every
preset:

- **Apogee below the drag-free energy ceiling.** A vehicle cannot coast higher
  than its burnout kinetic energy allows with drag switched off.
- **Landing speed equals the closed-form terminal velocity** under the fully
  open recovery system, `sqrt(2mg / (ρ·Cd·A))`.
- **Mass bookkeeping closes** — burnout mass equals dry mass plus remaining
  propellant.
- **Statically stable throughout boost** — the Barrowman margin never goes
  negative, or the flight being simulated is fiction.

---

## What is *not* validated

Stated plainly, because a safety tool that overstates its own confidence is
worse than one that admits its limits:

- **No preset has been flown against real flight data.** The motors are real
  and measured; the airframes are representative, and no measured apogee for
  those airframes exists to check against.
- **Supersonic drag is an engineering correlation, not CFD.** The transonic
  rise and supersonic falloff have the right shape and magnitude, but treat
  the coefficient as ±20%. For a serious altitude attempt, get a Cd(Mach)
  table from RASAero or CFD and put it in the Cd override field.
- **Weathercocking magnitudes are approximate.** The trends are right — zero
  wind gives zero drift, and an overstable rocket weathercocks worse than a
  marginally stable one — but the absolute angles have not been checked
  against flight data.
- **The atmosphere is a standard day plus your ground conditions.** No
  turbulence, no gusts, no wind direction changes, no jet stream.
