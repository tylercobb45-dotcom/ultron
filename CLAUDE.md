# JARVIS — working notes

## Windows is the only thing that ships

The rule is about the DELIVERABLE, not about where work gets done:

- **Only Windows reaches the user.** `JARVIS-Downloadable-Windows.zip` is the
  one release asset. No macOS build, no Linux download, nothing else on the
  Releases page or in the docs.
- **Report Windows.** Don't lead with or dwell on other platforms. If another
  platform fails and Windows is fine, one line and move on.
- **Testing anywhere is fine.** Checking something on Linux along the way is
  explicitly allowed - it just must not turn into something the user
  downloads. The Linux CI job exists on exactly that basis: it publishes no
  release asset, and it is the only runner that can launch the frozen app
  headless and confirm it stays up, which a Windows runner cannot do. Without
  it a build would be verified as complete but never as running.
- **Windows-specific bugs are top priority.** This repo has already shipped
  two defects that bit on Windows and nowhere else: `python -m pyinstaller`
  in lowercase (the package is `PyInstaller`, which only resolves on a
  case-insensitive filesystem), and emoji in `build_simple.py` that a cp1252
  console cannot encode. Neither was reachable from a Linux test. Read the
  Windows paths adversarially - encoding, path separators, CRLF, `cmd.exe`
  quoting - because this environment cannot execute them.

## Verification expectations

- Both suites must pass before any commit: `python tools/validate_presets.py`
  (125 checks) and `python hybrid_sim/verify.py` (31 checks).
- The app is PyQt5; test headless with `QT_QPA_PLATFORM=offscreen`.
- Do not trust a static read of UI code. Several bugs here were only found by
  rendering frames and looking at them.
