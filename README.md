# JARVIS

A desktop app for simulating model-rocket flight, built for a SystemsGo
Goddard-level vehicle. It runs from a flash drive so your work follows you
from one computer to the next.

## Running it from a USB stick

**Two ways. Pick by whether the computers you use have Python.**

### 1. The downloadable version — nothing needed on the computer

Go to the **[Releases page](https://github.com/tylercobb45-dotcom/ultron/releases)**,
download the zip for your computer, unzip it onto the stick, and run
`JARVIS_Rocket_Simulation`. No Python, no installer, no admin rights. This is
the one for a school or lab machine you cannot install anything on. Details
in [`DOWNLOADABLE-README.md`](rocket-simulation-ui/DOWNLOADABLE-README.md).

### 2. This source download — if the computer has Python

Download this branch with the green **Code** button → *Download ZIP*, unzip
it onto the stick, then:

- **Windows** — double-click **`START HERE - Run JARVIS.bat`**
- **macOS / Linux** — in a terminal, `bash "START-HERE-run-jarvis.sh"`

The first run fetches the libraries **onto the stick** (about 600 MB, in
`rocket-simulation-ui/JARVIS-Data/lib/`), keyed by platform and Python
version so one stick can serve a Windows lab and a Mac at home. It needs the
internet that once; every run after that is offline and starts immediately.
Nothing is installed into the computer.

Needs Python 3.11 or newer. If the computer has no Python, use option 1.

### Either way, your work lives on the stick

Saved rockets, motors, settings and exports all go into a **`JARVIS-Data`**
folder beside the program, so they travel with the drive. Copy that one
folder somewhere safe now and then — flash drives get lost.

## Repository Layout

```
.
├── START HERE - Run JARVIS.bat  # Windows: double-click this to run from source
├── START-HERE-run-jarvis.sh     # macOS/Linux: bash this to run from source
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
