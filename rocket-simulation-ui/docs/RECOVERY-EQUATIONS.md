# Recovery equations — parachutes, streamers, reefing

Every equation JARVIS actually uses for recovery, with the file and symbol
names so you can check any of it against the source. Written to be copied and
pasted into a notebook, a report or a spreadsheet.

Source files: `src/recovery.py` (the model), `src/flight_model.py` (how the
2-DOF integrator uses it), `src/simulation.py` (the legacy 1-D model on the
Simulation tab), `src/failure_analysis.py` (the recovery checks).

---

## 1. Symbols

```
d        canopy nominal diameter                   m
A        canopy area                               m^2
Cd       canopy drag coefficient                   -
CdA      drag area (the quantity that matters)     m^2
rho      air density at altitude                   kg/m^3
m        vehicle mass                              kg
g        gravity                                   9.80665 m/s^2
v        speed through the air                     m/s
q        dynamic pressure                          Pa
t_d      time since that stage fired               s
```

---

## 2. Canopy area and drag area

Area is computed from the **nominal diameter**, as a flat circle — not the
inflated projected diameter.

```
A   = pi * (d / 2)^2

CdA = Cd * A
```

`recovery.py` → `RecoveryStage.area`, `RecoveryStage.full_drag_area`

**Drag area is the output of this whole module.** The flight integrator only
ever asks for `CdA`; it never sees `Cd` and `A` separately. That matters
because it means a 2 m canopy at Cd 1.5 and a 1.73 m canopy at Cd 2.0 are the
same rocket to the simulator.

---

## 3. Canopy drag coefficients

```
Canopy type              Cd      Notes
---------------------------------------------------------------------------
Round parachute         1.50     Classic hemispherical. High drag, high
                                 snatch load, drifts a long way.
Elliptical parachute    1.60     Slightly more efficient than round for the
                                 same fabric.
Toroidal / annular      2.20     Highest drag per square metre. Common for
                                 high-power mains.
Cruciform (X-form)      0.85     Very stable, low drag, easy to sew.
Streamer                0.35     A ribbon, not a canopy. Drogue duty only.
Drogue slider           1.10     Small stabilising drogue.
```

`recovery.py` → `CANOPY_TYPES`

> **Trap worth knowing.** Picking a canopy type in the UI *suggests* the Cd
> above — it does not lock it. The `cd` field on the stage is what the
> simulation uses. The built-in dual-deploy preset, for example, sets
> `canopy_type="Drogue slider"` (table says 1.10) but `cd=1.5`, and **1.5 is
> what flies**. If you change the type in a saved profile, check the Cd field
> followed it.

---

## 4. Inflation — the opening ramp

Canopies open slowly, then snap. The model uses a smoothstep S-curve rather
than a straight line, which keeps the load from spiking on the first step:

```
f      = clamp(t_d / t_inflation, 0, 1)

S(f)   = f^2 * (3 - 2*f)

CdA(t) = Cd * A * S(f)
```

`recovery.py` → `_inflation_curve`, `RecoveryStage.drag_area_at`

Values:

```
f      0.00   0.25   0.50   0.75   1.00
S(f)   0.000  0.156  0.500  0.844  1.000
```

Default inflation times in the shipped presets:

```
Drogue at apogee      1.0 s
Main                  2.0 s
Streamer              0.5 s
Reefed main           1.0 s (to the reefed area)
```

---

## 5. Reefing — two-stage opening

A reefed canopy opens to a fraction of full area, holds, then disreefs. This
is how you survive a fast main deployment.

Let `CdA_full = Cd * A` and `CdA_reef = CdA_full * reef_ratio`.

```
Phase 1  inflate to reefed area      0 <= t_d < t_inf
         CdA = CdA_reef * S(t_d / t_inf)

Phase 2  hold reefed                 t_inf <= t_d < t_inf + t_hold
         CdA = CdA_reef

Phase 3  disreef to full             t_d >= t_inf + t_hold
         u   = (t_d - t_inf - t_hold) / t_disreef
         CdA = CdA_reef + (CdA_full - CdA_reef) * S(clamp(u, 0, 1))
```

```
Fully open after:  t_inf + t_hold + t_disreef
```

`recovery.py` → `RecoveryStage.drag_area_at`, `fully_open_after`

**Worked example** — the built-in reefed main (d = 3.0 m, Cd = 1.5,
reef_ratio = 0.2, t_inf = 1.0 s, t_hold = 4.0 s, t_disreef = 1.5 s):

```
CdA_full = 1.5 * pi * 1.5^2 = 10.603 m^2
CdA_reef = 10.603 * 0.2     =  2.121 m^2

t_d (s)   0.0    0.5    1.0    2.0    5.0    5.5    6.0    6.5
CdA       0.000  1.060  2.121  2.121  2.121  4.320  8.404  10.603
                                              |<-- disreef -->|
fully open after 6.5 s
```

---

## 6. Total drag area of the train

Every stage that has fired contributes, so a drogue still flying under an
open main is still counted:

```
CdA_total(t) = SUM over fired stages of  CdA_stage(t - t_fired)
```

`recovery.py` → `RecoverySystem.drag_area`

---

## 7. Descent rate (terminal velocity)

Steady state, drag = weight:

```
0.5 * rho * CdA * v^2 = m * g

    =>   v = sqrt( 2*m*g / (rho * CdA) )
```

`recovery.py` → `descent_rate` (everything open),
`descent_rate_stage` (first N stages only, e.g. drogue phase)

Defaults used for the tab readout: `rho = 1.225 kg/m^3`, `g = 9.80665 m/s^2`,
and **dry mass** (propellant is gone by then).

**Worked example** — Goddard baseline, dry 56.808 kg, drogue 1.2 m and main
4.8 m both at Cd 1.5:

```
Drogue   A = pi*0.6^2  =  1.131 m^2   CdA =  1.696 m^2
Main     A = pi*2.4^2  = 18.096 m^2   CdA = 27.143 m^2

drogue only:  v = sqrt(2*56.808*9.80665 / (1.225*1.696))  = 23.15 m/s
both open:    v = sqrt(2*56.808*9.80665 / (1.225*28.839)) =  5.62 m/s
```

---

## 8. Sizing a canopy for a target descent rate

Rearranging §7. This is the form you want when you know the landing speed you
are allowed and need the canopy:

```
CdA_required = 2*m*g / (rho * v_target^2)

A_required   = 2*m*g / (rho * Cd * v_target^2)

d_required   = 2 * sqrt( A_required / pi )
             = sqrt( 8*m*g / (pi * rho * Cd * v_target^2) )
```

The legacy model does exactly this when you give it a target rate instead of
an area (`simulation.py`, `chute_target_descent_rate`):

```
target_A = (2 * m * g) / (rho_local * target_Cd * v_target^2)
```

**Worked example** — 56.8 kg onto 5.5 m/s at sea level under Cd 1.5:

```
A = 2*56.8*9.80665 / (1.225 * 1.5 * 5.5^2) = 20.0 m^2
d = 2 * sqrt(20.0 / pi)                    = 5.05 m
```

---

## 9. How the 2-DOF flight model uses it

`flight_model.py` → `run_flight`

```
CdA_body     = Cd_body * A_ref              (from the drag buildup)
CdA_recovery = recovery_system.drag_area(t)

if CdA_recovery > 0:
    CdA_body = 0.8 * A_ref                  # see note

CdA_total = CdA_body + CdA_recovery

q         = 0.5 * rho * v_rel^2
D         = q * CdA_total

Dx = -D * vx_rel / v_rel
Dz = -D * vz_rel / v_rel
```

**The 0.8 substitution.** Once a canopy is out, the airframe is no longer
flying nose-first — it is tumbling on the end of a harness. Its streamlined
drag coefficient is meaningless there, so the model swaps in `0.8 * A_ref`,
which is broadly attitude-independent. The airframe's own drag is small next
to the canopy anyway.

Note `v_rel` is speed **relative to the air**, so wind is already in it.

---

## 10. Deployment triggers

`recovery.py` → `RecoverySystem.update`

```
apogee      fires once past apogee
altitude    fires when past apogee AND altitude_AGL <= trigger_altitude
time        fires when launched AND t > 0 AND t >= trigger_time
delay       fires at (previous stage's fire time) + trigger_time
```

Two guards worth knowing:

- Altitude triggers require `past_apogee`, so a main set to 300 m cannot fire
  on the way up.
- Time triggers require `launched` and `t > 0`, because `trigger_time_s`
  defaults to 0.0 — without the guard, switching a stage to "at a set time"
  and not editing the time would fire it on the rail.

---

## 11. Legacy 1-D model (Simulation tab)

The older vertical-only model in `simulation.py` is still what the Simulation
tab's `chute_size` / `chute_height` / `chute_cd` fields drive. It differs from
the recovery train above and it is worth knowing how:

```
Deploy test:     velocity < -0.5 m/s  AND  altitude < chute_height
                 (default chute_height = 300 m)

Fill:            frac = clamp((t - t_deploy) / t_duration, 0, 1)
                 chute_fill = frac^2                 <-- PARABOLIC, not S-curve
                 (default t_duration = 3.0 s)

Chute drag:      F_chute = -0.5 * rho * Cd_chute * (A_chute * chute_fill)
                            * v * |v|
Body drag:       F_body  = -0.5 * rho * Cd_body * A * v * |v|
```

Then two things the newer model does not do:

```
Exponential smoothing (alpha = 0.30), applied to body and chute separately:
    F_smoothed = (1 - alpha) * F_previous + alpha * F_raw

Drag limiter, applied to the CHUTE COMPONENT ONLY (default 400 N, soft mode,
threshold 0.85):
    thr = 0.85 * cap
    if |F| > thr:
        r   = (|F| - thr) / (cap - thr)
        |F| = thr + (cap - thr) * (3r^2 - 2r^3)     clamped to cap
```

The limiter exists to keep the legacy integrator stable, but it means the
**flown** trajectory is softer than the real snatch load. That is why the
shock check reads the uncapped value (§12).

> Use `v * |v|` rather than `v^2` — it keeps the sign, so drag opposes motion
> in both directions. Squaring alone gets the descent wrong.

---

## 12. The recovery checks

`failure_analysis.py`

**R-03 deployment shock load**

```
shock = max |F_chute_uncapped| after deployment
SF    = harness_rating_N / shock
```

Graded against a safety factor of 1.5. Reported as a multiple of vehicle
weight: `shock / (m * g)`. Reads the *uncapped* force so the limiter in §11
cannot hide the real load.

**R-04 landing descent rate**

```
touchdown speed, from the last sample before the simulator zeroes it
```

```
<= 6 m/s      OK
6 - 9 m/s     CAUTION
> 9 m/s       CRITICAL
```

Above about 7.6 m/s (25 ft/s) fibreglass fins and airframes start taking
damage.

**R-05 velocity at deployment** — graded on the **main**, not the first thing
out. On a dual deploy the first event is the drogue at apogee where speed is
near zero by definition, and grading that made the check unfailable.

```
<= 30 m/s     OK
30 - 60 m/s   CAUTION
> 60 m/s      CRITICAL
```

---

## 13. Streamers specifically

A streamer is modelled as a canopy with a small Cd, using the same area
equation. It is **not** a separate physics path:

```
Cd = 0.35      (vs 1.50 for a round canopy)
A  = pi * (d/2)^2       <-- still the disc formula, from the nominal size
```

So a streamer produces about **23%** of the drag of a round canopy of the same
nominal dimension. That is the whole point — far less drag, far less drift,
and the vehicle comes down fast until the main opens.

**Worked example** — the built-in streamer-drogue preset, 0.5 m streamer on a
20 kg vehicle:

```
A   = pi * 0.25^2 = 0.1963 m^2
CdA = 0.35 * 0.1963 = 0.0687 m^2
v   = sqrt(2*20*9.80665 / (1.225*0.0687)) = 68.3 m/s under streamer alone
```

Compare the 2.6 m main in the same preset: `CdA = 7.964 m^2`, which is **116x**
the streamer's drag area.

**Honest limitation.** Real streamer drag depends on length-to-width ratio and
material, and comes from flutter rather than a trapped air bubble. Sizing one
by an equivalent disc diameter is a modelling convenience, not physics. If you
are relying on a streamer for a specific descent rate, measure it — this model
will give you a number, and that number is a rough one.

---

## 14. Air density and wind (for descent and drift)

Descent rate is a function of density, so it changes all the way down.
`atmosphere.py` supplies ISA-1976 density at altitude; the tab readouts use
sea-level 1.225 kg/m^3.

Wind during descent uses the standard power-law shear profile:

```
v_wind(h) = v_ref * (h / h_ref)^alpha

  h_ref = 10 m       (height the pad wind was measured at)
  alpha = 0.143      (1/7, open terrain)
```

`atmosphere.py` → `LaunchSite.wind_at`

Drift is then an integration, not a formula — the vehicle is advected by the
wind at every step while it descends. There is no closed form in the code. The
rough hand estimate, if you want one:

```
drift ~ v_wind_mean * (descent height / descent rate)
```

which is why halving the main's deployment altitude roughly halves the drift
under it.

---

## 15. Quick reference card

```
A            = pi*(d/2)^2
CdA          = Cd*A
S(f)         = f^2*(3-2f)                       inflation curve
CdA(t)       = Cd*A*S(clamp(t_d/t_inf,0,1))
v_descent    = sqrt(2*m*g/(rho*CdA))
CdA_required = 2*m*g/(rho*v_target^2)
d_required   = sqrt(8*m*g/(pi*rho*Cd*v_target^2))
q            = 0.5*rho*v^2
D            = q*CdA_total
F_chute      = -0.5*rho*Cd*A*chute_fill*v*|v|   (legacy, signed)
```

Constants: `g = 9.80665 m/s^2`, `rho_sea_level = 1.225 kg/m^3`.
