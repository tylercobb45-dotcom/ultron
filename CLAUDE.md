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
  three defects that bit on Windows and nowhere else: `python -m pyinstaller`
  in lowercase (the package is `PyInstaller`, which only resolves on a
  case-insensitive filesystem), emoji in `build_simple.py` that a cp1252
  console cannot encode, and the table layouts in v1.5.0, which were tuned
  against the Linux font and overflowed in Segoe UI. None was reachable from
  a Linux test. Read the Windows paths adversarially - encoding, path
  separators, CRLF, `cmd.exe` quoting, and **font metrics**, since every
  width in a Qt layout is one - because this environment cannot execute them.
- **A Windows step needs `shell: bash`.** PowerShell is the default shell on
  a Windows runner and GitHub propagates only the LAST line's exit code out
  of a `run: |` block. v1.5.0 was built, released and published with three
  failing checks under a green tick, because the test step ran two suites on
  two lines and the second one passed. Section 16 of the preset suite now
  reads the workflow back and fails if any multi-line Windows step loses its
  shell.
- **Measure the UI in a wider font than this one.** Section 11b re-renders
  the tables at 125%, which is what Windows display scaling does on a great
  many laptops, and is the only reason the v1.5.0 defects are catchable from
  here.

## Verification expectations

- Both suites must pass before any commit: `python tools/validate_presets.py`
  (216 checks) and `python hybrid_sim/verify.py` (31 checks).
- The app is PyQt5; test headless with `QT_QPA_PLATFORM=offscreen`.
- Do not trust a static read of UI code. Several bugs here were only found by
  rendering frames and looking at them.
