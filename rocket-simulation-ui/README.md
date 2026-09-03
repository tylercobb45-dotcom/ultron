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
│   ├── utils.py            # Small helpers (flight-phase classification)
│   ├── live_code_viewer.py # In-app viewer for the simulation source
│   ├── JARVIS.ico          # Window/app icon
│   ├── crash.jpg           # Image shown in the crash dialog on an unhandled exception
│   └── profiles/           # Saved rocket configuration presets (JSON)
├── thrust_curves/          # Sample motor thrust curves (CSV and RASP .eng)
├── requirements.txt
└── README.md
```

## Setup

1. Install the dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Run the app:
   ```
   python src/main.py
   ```

## Usage

1. Enter the rocket's mass, drag coefficient, cross-sectional area, and air
   density, or load a saved profile from `src/profiles/`.
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

## Packaging & Distribution

See [`PACKAGING_GUIDE.md`](PACKAGING_GUIDE.md) for building a standalone
executable with PyInstaller, and [`DISTRIBUTION_README.md`](DISTRIBUTION_README.md)
for how to share it. For running the executable on locked-down Windows
machines (AppLocker/WDAC/SmartScreen), see the root
[`DIGITAL_SIGNATURE_GUIDE.md`](../DIGITAL_SIGNATURE_GUIDE.md).

## Contributing

Contributions are welcome — open an issue or a pull request.
