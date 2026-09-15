# The downloadable JARVIS

This is the downloadable version: a ready-to-run copy of JARVIS that needs
no Python and no installer.

Copy it to a USB stick, plug the stick into any computer, run it. Nothing is
installed, no admin rights are needed, and your rockets travel with the drive.

## The short version

1. Go to the **[Releases page](https://github.com/tylercobb45-dotcom/ultron/releases)**
2. Download **`JARVIS-Downloadable-Windows.zip`**
3. Unzip it **onto the flash drive**
4. Open the folder and run `JARVIS_Rocket_Simulation`

That is all. No Python, no installer, no setup.

## Where your work is saved

Everything you create goes into the **`JARVIS-Data`** folder sitting next to
the program:

```
E:\JARVIS-Downloadable-Windows\
├── JARVIS_Rocket_Simulation.exe
├── _internal\                 (the program's own files - leave alone)
└── JARVIS-Data\               <- everything you make lives here
    ├── user_settings.json
    ├── profiles\              saved rockets
    ├── generated_curves\      motors sent from the Engine Lab
    └── exports\               CSVs, .eng files, saved graphs
```

Because that folder is **on the drive**, a rocket you save in one classroom is
still there in the next one. This is the whole point — the program used to
save into the home folder of whichever computer it happened to be running on,
so your work stayed behind on that machine.

If the drive is ever write-protected, JARVIS falls back to the computer's home
folder so it still runs. It tells you which it is using.

### Backing up

The `JARVIS-Data` folder is the only thing you need to copy. Drag it somewhere
safe now and then — flash drives do get lost.

## If your computer has Python instead

If you downloaded the **source** (the green *Code* button) rather than a
release, you need Python 3.11 or newer on the computer:

- **Windows** — double-click `Run JARVIS.bat`

The first run downloads the libraries into `JARVIS-Data/lib/` **on the drive**,
keyed by platform and Python version, so one stick can serve several machines
without them interfering. It needs the internet once; after that it works
offline. Nothing is installed into the computer itself.

## Troubleshooting

**Windows says "Windows protected your PC"** — SmartScreen does this for any
program it has not seen before, because the build is not code-signed. Click
*More info* → *Run anyway*. If you would rather not, run from source instead.

**Antivirus quarantines it** — PyInstaller programs get flagged fairly often as
a false positive. The zips are built in public by GitHub Actions from the
source in this repository; the build log shows exactly what went into them.

**It starts slowly the first time** — reading a few hundred megabytes off a USB
2.0 stick is simply slow. Later launches are quicker because the computer
caches it. A USB 3 stick makes a real difference.

**Nothing happens when I double-click** — run it from a Command Prompt to see
the error.

## Which download do I want?

| You have | Download | Needs |
|---|---|---|
| A school/lab computer, no admin rights | the release zip | nothing |
| Your own computer with Python | either | Python 3.11+ for source |
| You want to change the code | the source | Python 3.11+ |
