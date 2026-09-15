# JARVIS — working notes

## Windows is the only platform that matters

The user builds and flies on Windows. Treat Windows as the only target that
counts:

- **Report Windows only.** Do not lead with, or dwell on, macOS or Linux
  results. If another platform fails and Windows is fine, say so in a line
  and move on.
- **Release Windows only.** `JARVIS-Downloadable-Windows.zip` is the
  deliverable. Other platform zips are not wanted on the Releases page.
- **Windows-specific bugs are top priority.** This repo has already shipped
  two defects that only bit on Windows and nowhere else: `python -m
  pyinstaller` in lowercase (the package is `PyInstaller`, which only
  resolves on a case-insensitive filesystem), and emoji in `build_simple.py`
  that a cp1252 console cannot encode. Neither was reachable from a Linux
  test. Read Windows paths adversarially — encoding, path separators, CRLF,
  `cmd.exe` quoting — because this environment cannot execute them.

### No other platforms

Do not build, test, release or report macOS or Linux. The user has said so
explicitly. CI is Windows-only; there is no smoke-test job on another
platform and none should be added back.

The cost of that, so it is not rediscovered as a surprise: nothing in CI ever
launches the built app. Windows runners cannot start a GUI headlessly, so a
build is verified as COMPLETE - every module and data file present, both
physics suites passing - but never as RUNNING. A defect that only shows at
startup reaches the user. Compensate by reading the Windows-specific paths
carefully rather than assuming a green build means a working one, and say so
plainly when handing over a release.

## Verification expectations

- Both suites must pass before any commit: `python tools/validate_presets.py`
  (97 checks) and `python hybrid_sim/verify.py` (27 checks).
- The app is PyQt5; test headless with `QT_QPA_PLATFORM=offscreen`.
- Do not trust a static read of UI code. Several bugs here were only found by
  rendering frames and looking at them.
