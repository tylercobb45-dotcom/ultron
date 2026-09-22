#!/usr/bin/env python3
"""Check that a freshly built downloadable folder actually contains the app.

PyInstaller does not fail when it quietly leaves a module out. It follows
imports statically, so anything reached through a runtime ``sys.path`` insert
- which is how the app loads ``hybrid_sim``, and how ``hybrid_sim`` loads
``engine_equations`` - is invisible to it. The build succeeds, the zip looks
right, and the app dies on the user's machine the first time they open the
tab that needed the missing piece.

So after every build we open the bundle and look. Two things are checked:

  1. every module in src/ that the app imports is inside the PYZ archive
  2. every data folder the app reads at runtime was unpacked beside it

Run from the project root, after build_simple.py:

    python tools/check_bundle.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUNDLE = os.path.join(ROOT, "dist", "JARVIS_Rocket_Simulation")

# Modules that must be in the bundle. Not every file in src/ - the standalone
# tools and the scratch scripts are not part of the app - but everything the
# running program imports, with the two physics files first because they are
# the ones PyInstaller cannot see for itself.
REQUIRED_MODULES = [
    "engine_equations", "flight_equations", "motor_designer",
    "tolerances", "tolerances_tab",
    "aero", "aero_tab", "atmosphere", "component_library", "datasheet",
    "engine_lab", "failure_analysis", "flight_model", "graphs_tab",
    "live_code_viewer", "mass_model", "mass_tab", "materials",
    "portable_paths", "presets", "rasp", "recovery", "report_tab",
    "rocket_library", "sections", "simulation", "theme", "unit_fields",
    "units", "utils", "vehicle_tab",
    # The vendored engine package, shipped as data AND imported.
    "hybrid_sim", "hybrid_sim.config", "hybrid_sim.engine",
    "hybrid_sim.flight", "hybrid_sim.metrics", "hybrid_sim.n2o",
]

# Read-only content unpacked beside the program.
REQUIRED_DATA = [
    "thrust_curves",   # the measured motor curves the presets point at
    "profiles",        # the preset rockets
    "assets",          # drop-down arrows the stylesheet references
    "hybrid_sim",      # the engine package, loaded from disk at runtime
]

REQUIRED_FILES = ["JARVIS.ico", "jarvis.gif", "Rocket.png", "crash.jpg"]


def fail(msg):
    print(f"  [FAIL] {msg}")
    return 1


def main():
    if not os.path.isdir(BUNDLE):
        print(f"No bundle at {BUNDLE} - run build_simple.py first.")
        return 1

    exe = os.path.join(BUNDLE, "JARVIS_Rocket_Simulation"
                       + (".exe" if os.name == "nt" else ""))
    if not os.path.isfile(exe):
        return fail(f"no executable at {exe}")

    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    bad = 0
    print("== modules in the bundle ==")
    archive = CArchiveReader(exe)
    pyz_name = next((n for n in archive.toc if n.lower().endswith(".pyz")), None)
    if pyz_name is None:
        return fail("the executable has no PYZ archive")

    # The PYZ is embedded in the executable; ZlibArchiveReader needs a file.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "app.pyz")
        with open(path, "wb") as fh:
            fh.write(archive.extract(pyz_name))
        present = set(ZlibArchiveReader(path).toc)

    missing = [m for m in REQUIRED_MODULES if m not in present]
    if missing:
        bad += fail(f"{len(missing)} module(s) never made it in: "
                    + ", ".join(missing))
    else:
        print(f"  [PASS] all {len(REQUIRED_MODULES)} modules present")

    # In onedir the data lands in _internal/ next to the exe; older layouts
    # put it beside the exe. Accept either.
    roots = [os.path.join(BUNDLE, "_internal"), BUNDLE]

    def find(name):
        return next((os.path.join(r, name) for r in roots
                     if os.path.exists(os.path.join(r, name))), None)

    print("== data unpacked beside the program ==")
    for name in REQUIRED_DATA:
        found = find(name)
        if found and os.path.isdir(found):
            print(f"  [PASS] {name}/")
        else:
            bad += fail(f"{name}/ is not in the bundle")

    for name in REQUIRED_FILES:
        if find(name):
            print(f"  [PASS] {name}")
        else:
            bad += fail(f"{name} is not in the bundle")

    # Every motor curve a preset rocket points at has to be in there. A count
    # would not catch the case that matters: the bundle has curves, just not
    # the one the preset names, so that rocket loads with no motor.
    print("== motor curves the presets point at ==")
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import presets
    wanted = sorted({r["thrust_curve"] for r in presets.PRESET_ROCKETS
                     if r.get("thrust_curve")})
    for rel in wanted:
        if find(rel.replace("/", os.sep)):
            print(f"  [PASS] {rel}")
        else:
            bad += fail(f"{rel} is named by a preset but is not in the bundle")

    print()
    if bad:
        print(f"{bad} problem(s) - this bundle would fail on a user's machine.")
        return 1
    print("Bundle looks complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
