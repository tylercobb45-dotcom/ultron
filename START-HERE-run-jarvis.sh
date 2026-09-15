#!/usr/bin/env bash
# ===================================================================
#  JARVIS - start here.
#
#  You downloaded the source (the green "Code" button on GitHub) and
#  unzipped it onto a flash drive. Run this to start it:
#
#      bash "START-HERE-run-jarvis.sh"
#
#  ("bash NAME" rather than "./NAME" because unzipping often drops the
#  executable bit, and then ./ just says Permission denied.)
#
#  The app itself lives in rocket-simulation-ui/; this hands over to the
#  launcher in there so you do not have to go looking for it.
# ===================================================================
set -euo pipefail
cd "$(dirname "$0")/rocket-simulation-ui"
exec bash ./RUN-JARVIS.sh
