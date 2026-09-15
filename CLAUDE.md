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

### The one exception, and why

Keep the **Linux job in CI as a test**, not as a published download. It is
the only runner where the frozen app can actually be launched headless and
checked that it stays up; Windows and macOS runners cannot do that. Deleting
it would leave every build verified as *complete* but never as *running*.
It produces no release asset, so it costs the user nothing.

## Verification expectations

- Both suites must pass before any commit: `python tools/validate_presets.py`
  (97 checks) and `python hybrid_sim/verify.py` (27 checks).
- The app is PyQt5; test headless with `QT_QPA_PLATFORM=offscreen`.
- Do not trust a static read of UI code. Several bugs here were only found by
  rendering frames and looking at them.
