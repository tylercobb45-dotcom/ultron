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
├── thrust_curves/          # Sample motor thrust curves (CSV and RASP .eng)
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

## Features

- **Two themes** — a retro pixel-art look and an aerospace "mission control"
  look, switchable at runtime and persisted to `src/user_settings.json`.
- **Live telemetry dashboard** — real-time altitude, velocity, acceleration,
  G-force, thrust, drag, mass, and Mach number during a simulated flight,
  plus flight-phase tracking (liftoff, powered ascent, coast, descent,
  chute descent, landed).
- **Physics** — thrust-curve driven mass flow (Isp derived from propellant
  mass when provided), optional ISA atmosphere model, a simple Mach-drag
  bump near transonic speeds, and a smoothed/capped parachute deployment
  model.
- **Live code viewer** — inspect the simulation source from within the app.
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
- **Engine Lab** — design a custom hybrid rocket engine (N2O tank, injector,
  fuel grain, nozzle) on the "Engine Lab" tab and run a physics-based
  internal-ballistics simulation (tank blowdown, Dyer NHNE injector, Marxman
  fuel regression, CEA c* table, isentropic nozzle) instead of only loading a
  pre-made thrust curve. Pick a fuel (HTPB, paraffin, ABS, ...) and a starting
  preset (a baseline motor or one of two HyperTEK motors the physics is
  validated against), see the resulting thrust/pressure/O-F/mass-flow plots
  and performance metrics, then send the generated thrust curve straight into
  the Simulation tab. See [`hybrid_sim/README.md`](hybrid_sim/README.md) for
  the physics and its validation against published motor certification data.

## Packaging & Distribution

See [`PACKAGING_GUIDE.md`](PACKAGING_GUIDE.md) for building a standalone
executable with PyInstaller, and [`DISTRIBUTION_README.md`](DISTRIBUTION_README.md)
for how to share it. For running the executable on locked-down Windows
machines (AppLocker/WDAC/SmartScreen), see the root
[`DIGITAL_SIGNATURE_GUIDE.md`](../DIGITAL_SIGNATURE_GUIDE.md).

## Contributing

Contributions are welcome — open an issue or a pull request.
