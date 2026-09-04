# JARVIS Rocket Simulation UI

A PyQt5 desktop app for simulating model-rocket flight. Give it a rocket's
mass, drag coefficient, cross-sectional area, and a motor thrust curve, and
it integrates the flight — thrust, drag, parachute deployment, atmosphere —
step by step, then plots altitude, velocity, acceleration, and mass over
time.

## Project Structure

```
rocket-simulation-ui
├── src
│   ├── main.py             # App entry point, PyQt5 UI, themes, telemetry dashboard
│   ├── simulation.py       # Flight physics: thrust curve parsing, drag, parachute, atmosphere
│   ├── engine_lab.py       # Engine Lab tab: design a hybrid engine, generate a thrust curve
│   ├── atmosphere.py       # ISA-1976 to 86 km + launch-site conditions and wind shear
│   ├── aero.py             # Nose/body/fin geometry, Mach-5 drag buildup, Barrowman CP
│   ├── recovery.py         # Recovery trains: single, dual, reefed, streamer
│   ├── flight_model.py     # 2-DOF flight: wind drift, moving CG, staged recovery
│   ├── vehicle_tab.py      # Vehicle tab: airframe, mass/balance, launch site, recovery
│   ├── theme.py            # Dark futuristic theme (black/red/white, sharp edges)
│   ├── presets.py          # Preset engines and rockets, with their reference data
│   ├── failure_analysis.py # Failure-mode checks run against a completed flight (Qt-free)
│   ├── materials.py        # Material library: melting points, service temps, strength
│   ├── report_tab.py       # Flight Report tab: verdict, colour-coded checks, graphs, export
│   ├── rocket_library.py   # Rockets tab: saved rockets, switch between complete setups
│   ├── utils.py            # Small helpers (flight-phase classification)
│   ├── live_code_viewer.py # In-app viewer for the simulation source
│   ├── JARVIS.ico          # Window/app icon
│   ├── crash.jpg           # Image shown in the crash dialog on an unhandled exception
│   └── profiles/           # Saved rocket configuration presets (JSON)
├── hybrid_sim/              # Vendored hybrid (N2O/fuel-grain) engine physics package
│   ├── hybrid_sim/          # Tank blowdown, injector, regression, nozzle, 1-DOF flight - see its README
│   ├── run.py, verify.py    # Standalone CLI + 27-check verification suite for the physics package
│   └── README.md            # Physics documentation and validation results
├── tools/
│   ├── fit_engines.py       # Fits Engine Lab configs to published motor performance
│   ├── build_presets.py     # Regenerates the preset rocket profiles
│   └── validate_presets.py  # Checks presets against published/reference data
├── thrust_curves/          # Real measured motor curves (thrustcurve.org) + RASP .eng
├── requirements.txt
└── README.md
```

## Setup

**Windows:** double-click **`Run JARVIS.bat`**. It finds Python, installs the
dependencies the first time, and launches the app. If Python is missing it
tells you where to get it — install it from
[python.org](https://www.python.org/downloads/) and tick *"Add python.exe to
PATH"* in the installer.

**Any platform, by hand:**

1. Install the dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Run the app:
   ```
   python src/main.py
   ```

Needs Python 3.12 or newer.

## Usage

1. Pick a rocket on the **Rockets** tab and load it — the Simulation, Engine
   Lab, and Flight Report tabs all repopulate from it. Or start from scratch
   and enter the rocket's mass, drag coefficient, cross-sectional area, and
   air density by hand.
2. Optionally load a motor thrust curve from `thrust_curves/` (or your own
   CSV / RASP `.eng` file).
3. Configure parachute deployment (height, size or target descent rate, drag
   coefficient) if simulating a recovery phase.
4. Run the simulation and review the live telemetry dashboard and the
   altitude/velocity/acceleration/mass plots.

## How it is organised

A rocket is an assembly of two independently designed halves, and the tabs are
split the same way:

| Tab | What lives there |
|---|---|
| **Engine** | The motor: oxidizer tank, injector, fuel grain and combustion chamber, nozzle, combustion gas. Resize any of it - bigger tank, different orifice, longer grain - run it, and get a thrust curve. Saves named **engine designs**. |
| **Aerodynamics** | The airframe: nose cone, body tube, boat tail, fins, surface finish, launch site, the full **recovery train**, a **Mass & Ballast** buildup, and the **Cd vs Mach** sweep that geometry produces. Saves named **aerodynamics designs**. |
| **Simulation** | Where the two get paired. Pick an engine and an aerodynamics design at the top, set the masses, and fly it. **That pairing is the rocket** - it is what the Rockets tab saves and what gets run. |

They are separate because they change on different schedules: one motor gets
flown in several airframes while you tune the airframe, and one airframe gets
tried with several motors while you chase an altitude. Changing one does not
disturb the other. A saved rocket records which pair it was built from, and
also stores both full configurations, so it still loads correctly if a
component design is later renamed or deleted.

Designs are plain JSON under `profiles/engines/` and `profiles/airframes/`, so
they can be copied between machines, diffed and checked into version control.

Geometry is owned by the Aerodynamics tab. The Simulation tab still carries
some legacy fields for the basic vertical model; if one of them disagrees with
the airframe, the run says so rather than silently picking a side.

## Features

- **Dark futuristic UI** — black ground, red accent, white text, square edges,
  applied as one application-wide stylesheet. High-DPI aware, so it stays sharp
  on scaled Windows displays.
- **Live telemetry dashboard** — real-time altitude, velocity, acceleration,
  G-force, thrust, drag, mass, and Mach number during a simulated flight,
  plus flight-phase tracking (liftoff, powered ascent, coast, descent,
  chute descent, landed).
- **Flight physics to Mach 5, at any altitude** — the U.S. Standard Atmosphere
  to 86 km (the old model froze the temperature above 11 km and was wrong
  through the stratosphere), and a drag coefficient rebuilt every timestep
  from Reynolds and Mach: skin friction, base drag, nose wave drag and fin
  drag, so the transonic rise and supersonic falloff are actually represented
  instead of a single fixed number. Base drag drops while the motor burns,
  because the exhaust plume fills the base.
- **Wind and launch site** — field elevation, temperature, pressure, humidity,
  and a wind profile that shears with height. The vehicle flies against
  air-relative velocity, weathercocks into the wind with real pitch inertia
  and fin restoring moment, and the model reports downrange drift and where it
  lands.
- **Airframe you can shape** — eight nose cone profiles (Von Karman through
  conical, each with its own wave-drag penalty), body tube, boat tail, and fin
  planform, all feeding the drag buildup and the Barrowman centre of pressure.
- **Mass and balance, by component** — instead of typing a dry mass and a CG
  you worked out yourself, place the parts: nose weight, nose cone, avionics
  bay, recovery, body tube, fin can, motor hardware, ballast — each with a
  mass, a station from the nose tip, and optionally a length so it counts as
  distributed rather than as a point. Dry mass, CG and pitch inertia all fall
  out of that list, and the recovery train joins the same buildup, because a
  main and its harness are real mass at a real station.

  Three things follow, live, as you edit: **CG**, how far it **migrates**
  through the burn, and the **static margin at liftoff and at burnout** —
  colour-coded, with a warning under 1 caliber. Adding 400 g at the nose moves
  the margin visibly, which is the whole point.

  Pitch inertia is the part that used to be missing. The flight model needs it
  to work out how fast the vehicle weathercocks into a crosswind, and it was
  estimated as `mass·(L/3.5)²` — a uniform rod. Real rockets are not uniform
  rods, and a proper `Σmr²` changes the answer: on the L550 preset, 1.5 kg of
  nose ballast costs 850 ft of altitude, buys 3.2 calibers of margin, raises
  inertia from 1.15 to 2.82 kg·m², and cuts drift from 1,512 m to 984 m. All
  four move together, because they are the same trade.

  The old two-number form still works — leave the component table empty and
  nothing changes.
- **Recovery systems** — single deploy, dual deploy, reefed (opens partially,
  then disreefs), and streamer drogues, with per-stage canopy type, size,
  trigger, and inflation time all editable.
- **Live code viewer** — inspect the simulation source from within the app.
- **Preset rockets and engines** — four rockets and five engines ship with the
  app, spanning subsonic to supersonic. Each preset rocket flies a *real
  measured* thrust curve from thrustcurve.org rather than a modelled one, and
  the engine presets are Engine Lab configurations fitted to reproduce
  published HyperTEK motor performance. `python tools/validate_presets.py`
  checks all of it against the reference data and prints the residuals, so the
  agreement (and every disagreement) is visible rather than asserted. See
  `docs/VALIDATION.md` for what matched, what did not, and why.
- **Rocket library** — the "Rockets" tab stores every rocket you have built
  and switches between them: loading one repopulates the flight inputs, the
  engine design, and the materials/structure in a single step, so you can keep
  several vehicles side by side and compare them. Rockets are plain JSON files
  in the profiles directory (shared with the Settings tab's profile dropdown),
  so they can be exported, imported, and checked into version control. Profiles
  saved before the engine and materials sections existed still load; the tabs
  they do not cover are left untouched and the library says so.
- **Flight Report** — every simulation run is automatically graded against a
  numbered list of physical failure modes and a target altitude (50,000 ft by
  default). Each check reports green (inside limits), yellow (thin margin or a
  shaky modelling assumption), or red (fails as simulated), with the measured
  value, the limit, and the moment in flight it happened; the numbers are
  plotted as markers on the altitude trace so a red dot on the graph points
  straight at its table row. Build the vehicle out of real materials — the
  library carries melting points, continuous-service temperatures, yield
  strength and shear modulus — and the checks grade against what you picked:
  skin and nose heating vs the airframe's service and failure temperatures,
  axial stress and column buckling, fin flutter (shear modulus driven), rail
  exit velocity, deployment shock and landing speed. When the flight was flown
  on an Engine Lab motor it also grades the motor internals: chamber and tank
  hoop stress, injector stiffness, O/F band, oxidizer-rich burn tail, grain
  burn-through, oxidizer mass flux, port-to-throat ratio, flame temperature vs
  the throat material, nozzle separation, and tank thermal collapse. Exports a
  self-contained HTML report with the graphs embedded.
- **Engine Lab** — design a custom hybrid rocket engine on the "Engine Lab"
  tab and run a physics-based internal-ballistics simulation (tank blowdown,
  Dyer NHNE injector, Marxman fuel regression, CEA c* table, isentropic
  nozzle) instead of only loading a pre-made thrust curve. Every component is
  editable, grouped the way the hardware is actually built, and each field
  carries a tooltip explaining what it does to the motor:

  - **Oxidizer tank (self-pressurizing N2O)** — internal diameter and length,
    fill fraction, initial temperature, vent orifice and its discharge
    coefficient, and the tank cooling coefficient. Nitrous supplies its own
    pressure: there is no pressurant and no regulator, so tank pressure *is*
    the saturation pressure at tank temperature (293 K ≈ 5.0 MPa / 730 psi),
    and it falls as the liquid boils off and chills the tank. The cooling
    coefficient sets how steeply that decay runs, which is what makes a
    blowdown curve peaky or flat.
  - **Injector** — type (single orifice, showerhead, pintle, swirl,
    impinging — picking one suggests its typical discharge coefficient), hole
    count, hole diameter, and Cd.
  - **Fuel grain and combustion chamber** — fuel material, grain length, outer
    diameter, initial port, number of ports for multi-port grains, and pre-
    and post-combustion chamber lengths.
  - **Nozzle** — throat diameter, expansion ratio, divergence and convergence
    half angles, and a throat erosion rate, because graphite and phenolic
    throats really do open up during a burn and bleed off chamber pressure.
  - **Combustion gas** — ratio of specific heats and molar mass.

  A live "derived geometry" panel shows what falls out of those numbers as you
  type — tank volume, liquid N2O loaded, tank pressure at the chosen
  temperature, fuel mass and web thickness, injector and throat areas, exit
  diameter, and L\* — since those are the quantities you actually size
  hardware against and none of them are typed in directly. Then send the
  generated thrust curve straight into the Simulation tab. See
  [`hybrid_sim/README.md`](hybrid_sim/README.md) for the physics.
- **Aero Analysis (Cd vs Mach)** — the drag curve, presented the way RASAero
  presents it, because a single drag coefficient cannot describe a rocket that
  goes transonic. Sweep the airframe across Mach at a chosen altitude and get
  power-off and power-on Cd curves, a stacked breakdown of what each component
  contributes (skin friction, base, nose wave, fins, interference), Reynolds
  number, centre of pressure and static margin across the same range — plotted,
  tabulated, and exportable. Power-on differs from power-off only in base drag,
  since the exhaust plume fills the base while the motor burns.

  The sweep is per-altitude on purpose: skin friction is Reynolds-dependent, so
  the same vehicle at the same Mach number has measurably less friction drag at
  30,000 ft than on the pad.

  It also **imports and flies external Cd(Mach) tables**. Point it at a
  two-column Mach,Cd file — a RASAero export, RockSim output, CFD results or
  your own spreadsheet — and the flight model flies that curve instead of the
  estimate. The reader skips header lines and copes with comma, semicolon or
  tab separators. Outside the table's Mach range the end values are held flat
  rather than extrapolated, because extrapolating a drag curve past its last
  point is how you get a confident wrong answer. Drag precedence is by how much
  the source actually knows: an imported curve beats the Vehicle tab's single
  measured Cd, which beats the computed buildup.
- **Two spreadsheets per run** — the Simulation tab's right-hand panel carries
  a **Flight Data** sheet (one row per trajectory sample: position, velocity,
  Mach, acceleration in m/s² and g, thrust, drag and its component buildup,
  mass, dynamic pressure, atmospheric state, CG/CP and stability margin, tilt
  and angle of attack, recovery drag area and canopy fill — 58 columns) and a
  separate **Engine Data** sheet holding the motor internals on their own time
  base (chamber and tank pressure in MPa and psi, tank temperature, injector
  ΔP and stiffness, oxidizer and fuel mass flow, O/F, c* ideal and delivered,
  thrust coefficient, instantaneous Isp, oxidizer flux, regression rate, port
  radius and area, web remaining, throat diameter, expansion ratio, L\*, gas
  residence time, and the liquid/vapour split in the tank — 36 columns). Both
  have an **Export CSV** button that writes *every* sample, not just the
  strided rows drawn on screen.

## Packaging & Distribution

See [`PACKAGING_GUIDE.md`](PACKAGING_GUIDE.md) for building a standalone
executable with PyInstaller, and [`DISTRIBUTION_README.md`](DISTRIBUTION_README.md)
for how to share it. For running the executable on locked-down Windows
machines (AppLocker/WDAC/SmartScreen), see the root
[`DIGITAL_SIGNATURE_GUIDE.md`](../DIGITAL_SIGNATURE_GUIDE.md).

## Contributing

Contributions are welcome — open an issue or a pull request.
