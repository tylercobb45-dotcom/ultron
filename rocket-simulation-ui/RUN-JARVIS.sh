#!/usr/bin/env bash
# ===================================================================
#  JARVIS - run from a flash drive on macOS or Linux
#
#  For the SOURCE download (the green "Code" button on GitHub). If you
#  downloaded a release zip instead you do not need this.
#
#  Needs Python on the computer but installs nothing into it: libraries
#  go into a folder ON THE DRIVE, keyed by platform and Python version
#  so one stick can serve several machines.
#
#  Deliberately not a virtual environment - a venv bakes in the
#  absolute path it was created at, which breaks as soon as the drive
#  mounts somewhere else. "pip --target" does not.
# ===================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "  JARVIS Rocket Simulation - running from the drive"
echo "  -------------------------------------------"

PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
    cat <<'EOF'

  No Python found on this computer.

  Install Python 3.11+ (macOS: brew install python, or python.org;
  Linux: your package manager), or download the ready-to-run release
  zip instead, which needs no Python:
    https://github.com/tylercobb45-dotcom/ultron/releases

EOF
    exit 1
fi

TAG=$("$PY" -c 'import sys,platform;print(f"{sys.platform}-{platform.machine().lower()}-py{sys.version_info.major}.{sys.version_info.minor}")')
LIBDIR="JARVIS-Data/lib/$TAG"

if [ ! -d "$LIBDIR" ]; then
    echo
    echo "  First run on this kind of computer - setting up libraries."
    echo "  Needs the internet once; after that it works offline."
    echo "  Installing into $LIBDIR"
    echo
    "$PY" -m pip install --upgrade pip --quiet || true
    if ! "$PY" -m pip install --target "$LIBDIR" -r requirements.txt; then
        echo
        echo "  Library install failed. Check the internet connection, or"
        echo "  use the release zip which needs no install."
        rm -rf "$LIBDIR"
        exit 1
    fi
fi

export PYTHONPATH="$PWD/$LIBDIR:${PYTHONPATH:-}"
echo "  Starting JARVIS..."
exec "$PY" src/main.py
