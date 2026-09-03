# JARVIS

A PyQt5 desktop app for simulating model-rocket flight, plus the tooling
used to package and distribute it as a signed Windows executable.

The application itself lives in [`rocket-simulation-ui/`](rocket-simulation-ui/README.md) —
see that README for the project structure, setup, and usage instructions.

## Repository Layout

```
.
├── rocket-simulation-ui/       # The application (see its own README)
├── PressStart2P-Regular.ttf    # Retro-theme font asset
├── Sign_JARVIS.ps1             # Self-signs the packaged .exe
├── Get_JARVIS_Info.ps1         # Prints file hash/info for IT whitelist requests
├── JARVIS_Certificate.cer      # Self-signed code-signing certificate
├── DIGITAL_SIGNATURE_GUIDE.md  # How to sign the .exe / get it trusted on locked-down PCs
└── GIT_LFS_SETUP.md            # How large build artifacts (e.g. the .exe) are tracked
```

## Building & Distributing

1. Build the executable — see [`rocket-simulation-ui/PACKAGING_GUIDE.md`](rocket-simulation-ui/PACKAGING_GUIDE.md).
2. Sign it for use on managed Windows machines — see
   [`DIGITAL_SIGNATURE_GUIDE.md`](DIGITAL_SIGNATURE_GUIDE.md).
3. Package it for sharing — see
   [`rocket-simulation-ui/DISTRIBUTION_README.md`](rocket-simulation-ui/DISTRIBUTION_README.md).

Large binaries (the built `.exe`) are tracked with Git LFS — see
[`GIT_LFS_SETUP.md`](GIT_LFS_SETUP.md).

## Contributing

Contributions are welcome — open an issue or a pull request.
