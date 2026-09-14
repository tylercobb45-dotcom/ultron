"""Where JARVIS keeps everything the user creates.

The app is meant to run from a flash drive: copy the folder to a USB stick,
plug it into any computer, run it. That only works if the things you make -
saved rockets, engine designs, generated thrust curves, settings, exports -
live **on the drive**, not in the home folder of whichever computer you
happened to plug into. Otherwise you save a rocket in the lab on Monday and
it is not there on Tuesday in a different room.

So the data folder is chosen portable-first:

  1. ``JARVIS-Data`` beside the program (the .exe for a built copy, the
     repository root when running from source). On a flash drive this is on
     the flash drive, and it travels.
  2. Only if that location cannot actually be written - a read-only mount, a
     copy dropped in Program Files, a locked-down lab machine - fall back to
     the user's home folder, so the app still works rather than failing.

Writability is tested by writing a real file, not guessed from the path:
Windows can report a directory as existing and still refuse writes to it.
"""
from __future__ import annotations

import os
import sys

_DATA_FOLDER_NAME = "JARVIS-Data"
_cached_root: str | None = None


def program_dir() -> str:
    """The folder the program lives in.

    Frozen, that is the directory holding the executable. From source it is
    the repository root - the parent of ``src`` - so the data folder sits
    beside ``src`` and ``thrust_curves`` rather than inside the code.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_writable(path: str) -> bool:
    """Can we actually create a file here?

    os.access(W_OK) lies often enough on Windows to be useless, so this
    writes a probe file and removes it.
    """
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write-test")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def data_dir() -> str:
    """The writable folder for user data, portable if at all possible."""
    global _cached_root
    if _cached_root is not None:
        return _cached_root

    # An explicit override wins, so a team can point a shared install at a
    # network folder or a teacher can pin it somewhere specific.
    override = os.environ.get("JARVIS_DATA_DIR")
    candidates = []
    if override:
        candidates.append(os.path.abspath(override))
    candidates.append(os.path.join(program_dir(), _DATA_FOLDER_NAME))
    candidates.append(os.path.join(os.path.expanduser("~"), "JARVIS"))

    for candidate in candidates:
        if _is_writable(candidate):
            _cached_root = candidate
            break
    else:
        # Everything refused; use a temp folder so the app runs rather than
        # dying on startup. Nothing saved will survive, and is_portable()
        # reports False so the UI can say so.
        import tempfile
        _cached_root = os.path.join(tempfile.gettempdir(), "JARVIS")
        os.makedirs(_cached_root, exist_ok=True)
    return _cached_root


def is_portable() -> bool:
    """True when the data folder is beside the program (travels with it)."""
    return os.path.dirname(os.path.abspath(data_dir())) == program_dir()


def _sub(name: str) -> str:
    path = os.path.join(data_dir(), name)
    os.makedirs(path, exist_ok=True)
    return path


def settings_file() -> str:
    return os.path.join(data_dir(), "user_settings.json")


def profiles_dir() -> str:
    return _sub("profiles")


def generated_curves_dir() -> str:
    return _sub("generated_curves")


def exports_dir() -> str:
    """Default folder offered by every Save/Export dialog.

    Exports default to the drive too, so a CSV or a report written in one
    computer lab is still there in the next one.
    """
    return _sub("exports")


def bundled_dir(*parts: str) -> str:
    """A read-only resource that ships WITH the program.

    Thrust curves, the preset profiles and the theme assets are program
    content, not user data: they live inside the bundle and are only read.
    PyInstaller unpacks them beside the executable (onedir) or into
    ``sys._MEIPASS`` (onefile), so both are checked.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots.append(here)                       # src/
        roots.append(os.path.dirname(here))      # repository root
    for root in roots:
        candidate = os.path.join(root, *parts)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(roots[0], *parts)


def describe() -> str:
    """One line for the UI saying where things are being saved."""
    where = data_dir()
    if is_portable():
        return f"Portable: saving to {where} (travels with this copy)"
    return (f"Saving to {where} - this copy could not write beside the "
            f"program, so your work stays on this computer")
