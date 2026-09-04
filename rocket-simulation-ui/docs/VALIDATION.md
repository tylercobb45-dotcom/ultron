# Validation of the preset engines and rockets

This is the record of checking JARVIS against data it was not built from:
what matched, what did not, and why. Run it yourself with

```
python tools/validate_presets.py
```

**97 / 97 checks pass.** Nothing here is asserted without a number behind it,
and every disagreement is explained or flagged as unexplained.

---

## What the reference data actually is

| Reference | What it is | Where it comes from |
|---|---|---|
| HyperTEK J317, K240, L550 thrust curves | **Measured** test-stand data | `thrust_curves/csv/`, from thrustcurve.org, contributed by John Coker. Source URLs are in each file header. |
| HyperTEK I260 performance | Published certification figures | Carried in `hybrid_sim/validation.py` |
| HyperTEK hardware dimensions | **Manufacturer-published** | Motor designations, vendor product listings and the HyperTEK manual introduction — see below |
| SystemsGo Goddard flight | An **independent spreadsheet model** by another author | `hybrid_sim/excel_ref.json` |
| ISA-1976 atmosphere | Published standard-atmosphere tables | Base pressures at each layer boundary |

---

## 0. The published hardware, and whether it is self-consistent

Previous versions of this document said HyperTEK does not publish internal
geometry. That was too pessimistic: a good deal of it is encoded in the motor
designation itself.

```
440CC172J-I260
^^^              oxidiser tank volume, cc
   ^^^           injector orifice, thousandths of an inch
      ^          fuel grain type
        ^^^^     impulse class and average thrust
```

The injector is a bell holding **one** field-interchangeable orifice insert —
swapping the insert is exactly what turns an 835 cc motor from a J317 into a
K240. So `n_holes = 1`, and the orifice diameter is published to the
thousandth of an inch.

Tank internal diameter follows from two published numbers: the 835 cc 54 mm
tank takes a **17.5 in vent tube** running its length, so L = 0.4445 m, and
835 cc at that length gives **ID 48.9 mm** — exactly right for a 54 mm tube
with a ~2.5 mm wall. The 440 cc tank shares the 54 mm system; the 75 mm L tank
is scaled by the same OD/ID ratio.

| Motor | Designation | Tank | Tank ID × L | Orifice | Case | Overall length |
|---|---|---|---|---|---|---|
| I260 | 440CC172J | 440 cc | 48.9 × 234.3 mm | 0.172 in | 54 mm | 21 in |
| J317 | 835CC172J | 835 cc | 48.9 × 444.6 mm | 0.172 in | 54 mm | 30 in |
| K240 | 835CC125J | 835 cc | 48.9 × 444.6 mm | 0.125 in | 54 mm | 30 in |
| L550 | 1685CCRGL | 1685 cc | 67.9 × 465.1 mm | not published | 75 mm | — |

### The strongest check in this document

The J317 and K240 are **the same motor**. Same 835 cc tank, same case, same
grain — the only difference is the injector orifice, 0.172 in versus 0.125 in.

If those numbers really are orifice diameters, and oxidiser flow really scales
with injector area, then burn time must scale as the inverse area ratio.
Both sides of this are published; nothing is fitted:

| | Value |
|---|---|
| Injector area ratio (0.172 / 0.125)² | 1.8934 |
| Measured burn-time ratio, 6.09 s / 3.17 s | 1.9211 |
| **Agreement** | **1.47%** |

Two independently published quantities, from different documents, agreeing to
1.5% on a relationship the model assumes. That is what makes the locked
geometry trustworthy rather than merely plausible.

### Vendor-stated pressures

HyperTEK states the self-pressurising N2O sits at **650–750 psi** and drives
**initial chamber pressures of up to about 550 psi**. Neither was used as a
fit target:

- N2O saturation pressure at 293 K, from the model's own property fits:
  **730 psi**. Inside the stated band.
- Fitted peak chamber pressures: **434–537 psi**. Under the stated ceiling.

### Propellant mass fits the tank

Published propellant mass, against what a tank of the published volume can
physically hold as liquid N2O at 293 K — the ratio should be a little over 1,
since propellant is oxidiser *plus* fuel:

| Motor | Published propellant | Full-tank oxidiser | Ratio |
|---|---|---|---|
| I260 | 0.383 kg | 0.347 kg | 1.11 |
| J317 | 0.712 kg | 0.658 kg | 1.08 |
| K240 | 0.789 kg | 0.658 kg | 1.20 |
| L550 | 1.552 kg | 1.327 kg | 1.17 |

All four sit just above 1, as they must.

---

## 1. Engine model vs published motor performance

### What is locked and what is fitted

Everything in the table above is **locked** and was not adjusted to improve
the match. The grain is additionally bounded so that tank + grain fits inside
the vendor's stated overall motor length.

What was fitted is the physics HyperTEK does *not* publish:

| Fitted | Why it is legitimately free |
|---|---|
| Injector discharge coefficient | Not the textbook 0.7 of a water orifice. N2O flashes to vapour crossing the hole and the two-phase choking that follows cuts the effective Cd a long way — the fits land at 0.32–0.80. |
| Tank cooling coefficient | How much boil-off latent heat comes out of the liquid rather than the tank walls. Sets how steeply the blowdown decays. Fits land at 0.129–0.150. |
| Fuel regression coefficient | HyperTEK grains are a moulded proprietary thermoplastic, not HTPB. ABS supplies the density and flux exponent; the coefficient is fitted. |
| Throat, expansion ratio, grain port, efficiencies | Not published. |

### Residuals

| Motor | Impulse | Propellant | Peak thrust | Burn time | Isp | Peak Pc |
|---|---|---|---|---|---|---|
| I260 | −0.1% | −0.0% | +0.0% | +0.0% | −0.1% | 522 psi |
| J317 | −0.0% | +0.0% | −0.0% | −0.0% | −0.0% | 522 psi |
| K240 | **−4.3%** | **−5.8%** | −0.0% | +1.8% | +1.5% | 434 psi |
| L550 | **−6.1%** | **−4.3%** | +0.3% | +0.5% | −1.9% | 537 psi |

The I260 and J317 reproduce published performance essentially exactly while
running on the manufacturer's own tank and orifice. The K240 and L550 fall
4–6% short on impulse and propellant, and that shortfall is **the honest cost
of locking real hardware**: the model cannot burn more propellant than a tank
of the published volume holds, and it treats 5% of the liquid as unusable
residual. The K240's fitted grain sits exactly on the packaging bound — it
wants to be longer than the motor is.

An earlier fit that let tank volume and orifice float reached ~1% on all four.
That version matched the curves better and described no real motor. This one
is worse on paper and means something.

### What this does and does not tell you

**These configurations reproduce what the motor did, using the tank and
injector it really has.** The unpublished internals — throat, grain port,
efficiencies — are one self-consistent set among several that would produce
the same curve. Do not machine hardware from those numbers.

### Why burn time is a soft number anyway

"Burn time" for a hybrid is a convention, not a measurement. The blowdown tail
is long and shallow, so the cutoff threshold moves the answer:

| Motor | 1% of peak | 5% of peak | 10% of peak | Spread |
|---|---|---|---|---|
| J317 | 3.46 s | 3.17 s | 3.03 s | 14% |
| K240 | 6.36 s | 6.09 s | 5.56 s | 13% |
| L550 | 6.03 s | 5.53 s | 5.28 s | 14% |

---

## 2. Flight model vs an independent implementation

The Goddard baseline has a full independent reference — a spreadsheet model by
another author, covering the whole flight.

Flown with **the same drag coefficient the reference used (Cd = 1.625)**:

| Quantity | This model | Reference | Error |
|---|---|---|---|
| Apogee | 9,172 ft | 9,292 ft | **−1.3%** |
| Max velocity | 192.0 m/s | 190.9 m/s | **+0.6%** |
| Max Mach | 0.57 | 0.57 | +0.5% |
| Time to apogee | 25.6 s | 26.1 s | −1.9% |

That is the integrator, atmosphere, and mass bookkeeping agreeing with a
separately written model to about 1%.

### The discrepancy worth understanding

Flown instead on **this model's own computed drag**, the same vehicle reaches
about **17,965 ft — roughly +96%**.

That gap is entirely the drag coefficient:

| | Cd | Reference area | Drag area |
|---|---|---|---|
| Reference vehicle | 1.625 | 0.01539 m² | 0.02501 m² |
| This model's buildup for the shape entered | 0.470 | 0.01539 m² | 0.00724 m² |

**A 3.5× difference in drag area.** Cd = 1.625 is very high for a rocket —
typical values are 0.4–0.8 — so the reference is either using a measured value
for a much draggier vehicle than the shape entered here, or a deliberately
conservative number. The airframe dimensions of the real vehicle are not in
the reference, so the shape used in the preset is representative, not measured.

This is the single most important thing to understand about the tool: **the
drag coefficient dominates the altitude answer.** Getting the trajectory
integration right buys you about 1%. Getting Cd wrong by 3× costs you a factor
of two in altitude.

The Goddard preset therefore ships with the reference's Cd = 1.625 in the
"Measured Cd override" field on the Vehicle tab, so it reproduces its
documented flight. Clear that field to zero and the model falls back to its
own drag buildup.

---

## 3. Preset flight results

| Preset | Apogee | Max Mach | Peak g | Rail exit | Drift |
|---|---|---|---|---|---|
| HyperTEK J317 Sport | 3,346 ft | 0.40 | 6.8 | 19.8 m/s | 702 m |
| HyperTEK K240 Altitude | 5,805 ft | 0.56 | 5.0 | 16.1 m/s | 340 m |
| SystemsGo Goddard Baseline | 9,172 ft | 0.57 | 4.2 | 20.7 m/s | 0 m |
| HyperTEK L550 Supersonic | 15,953 ft | **1.27** | 13.3 | 31.3 m/s | 1,520 m |

Three subsonic cases and one supersonic, spanning 3,000 to 16,000 ft and
4 to 13 g.

These rose 3-4% against the previous release: skin friction on the fins was
being charged twice, once in the body's wetted area and again in the fin term,
inflating the friction component by about 17%. The Goddard figure is unchanged
because it flies on a measured-Cd override rather than the buildup, which is
exactly the behaviour you would expect from that fix.

## 4. Physical bounds

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


## 5. The Cd(Mach) curve

The Aero Analysis tab sweeps the drag buildup across Mach and can export the
result, or fly a curve imported from RASAero, CFD or a wind tunnel instead.

There is no external drag curve for these airframes to check against, so the
sweep is validated on **shape** rather than absolute value. Every published
rocket drag curve has these properties, and a model that violates them is
wrong whatever its numbers say:

| Property | Result |
|---|---|
| Transonic peak sits just past Mach 1 | peak at **Mach 1.20** |
| Rise over subsonic is the published 30–120% | **+51%** (0.493 → 0.746) |
| Cd falls monotonically above Mach 2 | 0.528 at Mach 2 → 0.251 at Mach 5 |
| Power-on Cd never exceeds power-off | holds — the plume can only *remove* base drag |

The table machinery is checked end to end: a curve round-trips through CSV to
5 × 10⁻⁶, holds flat outside its Mach range instead of extrapolating, and a
table of a single repeated value flies **bit-for-bit identically** to that
value as a scalar override (15,421.3 ft both ways). A real curve then moves
the answer — the L550 preset reaches 15,421 ft on a constant Cd = 0.55 and
14,784 ft on the swept curve, **+4.3%** of optimism from ignoring the
transonic rise.

### A limitation of tables, worth knowing

A Cd(Mach) table is a function of Mach alone. The component buildup is a
function of Mach *and* altitude, because skin friction is Reynolds-dependent —
the same vehicle at the same Mach has less friction drag at 30,000 ft than on
the pad. So a table swept at one altitude and then flown over a whole
trajectory will disagree with the buildup, and the difference is real physics
rather than an error in either.

Sweep at an altitude representative of where the vehicle spends its fast,
high-drag seconds — for most flights that is the first few thousand feet, not
apogee.


## 6. Is the changing Cd actually right?

Section 5 checks that the assembled curve has the right *shape*. This checks
that each piece is the correlation it claims to be, evaluated against the
closed form by hand. Between them: correct components, assembled into a curve
with the right shape.

**Skin friction** — Blasius laminar and Prandtl-Schlichting turbulent, matched
to 10⁻⁹:

| Check | Result |
|---|---|
| `Cf = 1.328/√Re` at Re = 10⁵ | 4.199505e-03 vs 4.199505e-03 |
| `Cf = 0.455/(log₁₀Re)^2.58` at Re = 10⁷ | 3.003713e-03 vs 3.003713e-03 |
| Roughness floor `Cf = 0.032(Rs/L)^0.2` binds at high Re | 3.200000e-03 vs 3.200000e-03 |
| A rougher airframe never has *less* friction than a smooth one | holds |
| Turbulent Cf falls monotonically with Re | 4.47e-03 → 1.57e-03 over Re 10⁶–10⁹ |
| Compressibility reduces friction monotonically with Mach | 3.00e-03 → 1.22e-03 over Mach 0–5 |

**Base drag** — the standard `0.12 + 0.13M²` / `0.25/M` correlation:

| Check | Result |
|---|---|
| Subsonic base Cd at M=0 is 0.12 | exact |
| Agrees with Hoerner `0.029/√Cf` within 20% | 0.1200 vs 0.1297 at Cf = 0.05 |
| Subsonic and supersonic branches meet at Mach 1 | 0.250000 both sides — continuous, no step for the integrator |
| The plume reduces base drag while thrusting | 0.023 vs 0.153 at Mach 0.5 |

**Nose wave drag** — an engineering correlation, so checked on behaviour:
zero below drag divergence, peaks at Mach 1.10, a hemispherical nose pays
0.301 against a Von Karman's 0.116 at Mach 1.2, and a fineness-7.9 nose pays
0.083 against a fineness-2.0 nose's 0.438.

**Assembled totals** land where real high-power rockets live — subsonic Cd
0.423 (J317 Sport), 0.439 (K240 Altitude), 0.457 (L550 Supersonic), against
the published 0.3–0.9 band.

### What this still does not prove

No absolute Cd has been compared against a *measured* drag curve for these
airframes, because none exists here. The components are provably the
correlations they claim to be, and the curve provably has the right shape —
but the correlations themselves carry the ±20% supersonic uncertainty stated
below. That is exactly why the Aerodynamics tab imports external Cd(Mach)
tables.


## 7. Mass components, CG migration and stability

Mass is now a list of components at stations along the airframe, and dry mass,
CG and pitch inertia are derived from it rather than typed in.

**Closed-form checks**, hand-computable and independent of the simulation:

| Check | Result |
|---|---|
| Two equal point masses balance at their midpoint | CG 1.000000 m |
| Their inertia about that CG is Σmr² | 4.000000 vs 4.0 kg·m² |
| A rod discretised into 400 point masses converges on mL²/12 | 0.999994 vs 1.000000 (0.0006%) |
| A single length-aware component equals that rod exactly | 1.000000 vs 1.000000 |

**CG migration** through the burn, on the L550 airframe: CG walks forward from
1,233 mm to 1,137 mm, and the static margin rises 2.47 → 5.03 cal. That is the
right direction — an aft-mounted motor makes a rocket more stable as it burns.

**The coupled trade.** Nose ballast has to do four things at once, and any one
of them moving alone would mean the mass model was not really reaching the
trajectory:

| Nose ballast | Apogee | Margin | Pitch inertia | Drift |
|---|---|---|---|---|
| 0.0 kg | 15,950 ft | 2.47 cal | 1.151 kg·m² | 1,512 m |
| 0.5 kg | 15,708 ft | 3.70 cal | 1.796 kg·m² | 1,322 m |
| 1.5 kg | 15,102 ft | 5.65 cal | 2.823 kg·m² | 984 m |

Ballast costs altitude, buys margin, raises inertia, and — because a
higher-inertia vehicle turns into a crosswind more slowly — cuts drift by a
third. All four checked as monotonic.

Recovery stages contribute to the same buildup (2 components, 0.900 kg on a
dual-deploy train), and a profile with no components flies **exactly** as it
did before: 15,952.7 ft against the recorded 15,952.7.

### One bug this found

Flying a component buildup for the first time gave 2,453 ft instead of 15,950.
The trajectory was reading `dry_mass_kg` — the typed-in field — rather than
the component sum, so every vehicle flew as the 20 kg default and adding
ballast changed nothing. Fixed, and the "dry mass flown is the sum of the
components" check exists so it cannot come back silently.

---

## What is *not* validated

Stated plainly, because a safety tool that overstates its own confidence is
worse than one that admits its limits:

- **No preset has been flown against real flight data.** The motors are real
  and measured; the airframes are representative, and no measured apogee for
  those airframes exists to check against.
- **The fitted fuel regression coefficient is not one consistent number.**
  Fitting it per motor gives 1.34, 1.17, 0.90 and 0.69 × 10⁻⁴ — a 2× spread
  across four motors that share a fuel. Something in the regression law is
  not capturing how these motors differ, most likely the flux exponent or the
  port geometry. The per-motor values reproduce each motor; they are not a
  material property you should reuse.
- **Airframe dimensions for the preset rockets are representative.** Only the
  *motors* carry manufacturer-published geometry.
- **Supersonic drag is an engineering correlation, not CFD.** The transonic
  rise and supersonic falloff have the right shape and magnitude - checked
  above - but no absolute value has been compared against a measured drag
  curve for these shapes, so treat the coefficient as ±20%. For a serious
  altitude attempt, get a Cd(Mach) table from RASAero or CFD and import it on
  the Aero Analysis tab, which is exactly what that importer is for.
- **Throat erosion, tank venting and multi-port grains are modelled but
  unvalidated.** They default to off. The trends are right — an eroding throat
  drops chamber pressure, a vent costs Isp — but no measured case has been
  checked.
- **Weathercocking magnitudes are approximate.** The trends are right, and
  pitch inertia is now a real sum of m·r² rather than a uniform-rod estimate,
  but the absolute angles have not been checked against flight data.
- **Component masses are yours to get right.** The buildup computes CG and
  inertia correctly from what you enter; it has no way to know whether the
  masses and stations you entered match the hardware.
- **The atmosphere is a standard day plus your ground conditions.** No
  turbulence, no gusts, no wind direction changes, no jet stream.
