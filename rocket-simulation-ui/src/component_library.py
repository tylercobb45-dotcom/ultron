"""Named design libraries for the two halves of a rocket.

A rocket in JARVIS is now an assembly of two independently designed things:

    ENGINE       tank, injector, grain, nozzle - what makes the thrust
    AERODYNAMICS nose, body, fins, recovery, drag - what the thrust pushes

They are designed on their own tabs and saved on their own, because in
practice they change on different schedules: you fly one motor in several
airframes while you tune the airframe, or try several motors in one airframe
while you chase an altitude. Keeping them separate means changing one does not
disturb the other.

The Simulation tab then pairs an engine with an airframe, and *that pairing*,
with its masses, is what gets saved as a rocket and flown.

This module is the shared save/load/delete bar both design tabs use. Designs
are plain JSON in a subdirectory of the user's profiles folder, so they can be
copied between machines, diffed and checked into version control.
"""
from __future__ import annotations

import json
import os
import re

from PyQt5 import QtWidgets, QtCore

import theme

_P = theme.PALETTE


def safe_filename(name: str) -> str:
    """A filename that keeps the design's name readable but cannot escape."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")
    return cleaned or "unnamed"


class ComponentLibraryBar(QtWidgets.QWidget):
    """Save, load and delete named designs of one kind."""

    def __init__(self, kind: str, label: str, get_config, apply_config,
                 directory_fn, on_loaded=None, parent=None):
        super().__init__(parent)
        self.kind = kind                  # subdirectory name, e.g. "engines"
        self.label = label                # human word, e.g. "engine"
        self._get_config = get_config
        self._apply_config = apply_config
        self._directory_fn = directory_fn
        self._on_loaded = on_loaded
        self._current = None
        self._build_ui()
        self.refresh()

    # ---- ui ---------------------------------------------------------------
    def _build_ui(self):
        box = QtWidgets.QGroupBox(f"Saved {self.label} designs")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)
        v = QtWidgets.QVBoxLayout(box)
        v.setSpacing(4)

        row = QtWidgets.QHBoxLayout()
        self.combo = QtWidgets.QComboBox()
        self.combo.setToolTip(f"Saved {self.label} designs. Choosing one loads it.")
        self.combo.activated.connect(self._load_selected)
        row.addWidget(self.combo, 1)
        v.addLayout(row)

        buttons = QtWidgets.QHBoxLayout()
        for text, slot, tip in (
            ("Save", self.save_current,
             f"Overwrite the selected {self.label} design with what is on "
             f"this tab now"),
            ("Save As...", self.save_as,
             f"Store what is on this tab as a new {self.label} design"),
            ("Delete", self.delete_selected, f"Delete this {self.label} design"),
            ("Refresh", self.refresh, "Re-read the folder"),
        ):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        v.addLayout(buttons)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{_P['text_dim']}; font-size:9pt;")
        v.addWidget(self.status)

    # ---- storage ----------------------------------------------------------
    def directory(self) -> str:
        base = self._directory_fn()
        path = os.path.join(base, self.kind)
        os.makedirs(path, exist_ok=True)
        return path

    def names(self):
        try:
            return sorted(
                os.path.splitext(f)[0] for f in os.listdir(self.directory())
                if f.lower().endswith(".json"))
        except OSError:
            return []

    def _path_for(self, name: str) -> str:
        return os.path.join(self.directory(), f"{safe_filename(name)}.json")

    def refresh(self):
        current = self.combo.currentText()
        self.combo.blockSignals(True)
        self.combo.clear()
        names = self.names()
        self.combo.addItems(names)
        target = self._current or current
        if target and target in names:
            self.combo.setCurrentIndex(names.index(target))
        self.combo.blockSignals(False)
        if not names:
            self.status.setText(
                f"No saved {self.label} designs yet. Build one on this tab and "
                f"press Save As.")

    # ---- actions ----------------------------------------------------------
    def _load_selected(self):
        name = self.combo.currentText()
        if name:
            self.load(name)

    def load(self, name: str) -> bool:
        path = self._path_for(name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            self.status.setText(f"Could not read '{name}': {exc}")
            return False
        try:
            self._apply_config(data.get("config", data))
        except Exception as exc:
            self.status.setText(f"Could not apply '{name}': {exc}")
            return False
        self._current = name
        self.status.setText(f"Loaded {self.label} design '{name}'.")
        if self._on_loaded:
            self._on_loaded(name)
        return True

    def save_current(self):
        name = self.combo.currentText().strip()
        if not name:
            self.save_as()
            return
        self._write(name)

    def save_as(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, f"Save {self.label} design",
            f"Name for this {self.label} design:",
            text=self.combo.currentText() or f"My {self.label}")
        if not ok or not name.strip():
            return
        self._write(name.strip())

    def _write(self, name: str):
        try:
            config = self._get_config()
        except Exception as exc:
            self.status.setText(f"Could not read this tab: {exc}")
            return
        payload = {"kind": self.kind, "name": name, "config": config}
        try:
            with open(self._path_for(name), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self, "Could not save", f"Writing '{name}' failed:\n{exc}")
            return
        self._current = name
        self.refresh()
        self.status.setText(f"Saved {self.label} design '{name}'.")

    def delete_selected(self):
        name = self.combo.currentText().strip()
        if not name:
            return
        if QtWidgets.QMessageBox.question(
                self, f"Delete {self.label} design?",
                f"Permanently delete the {self.label} design '{name}'?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        try:
            os.remove(self._path_for(name))
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Could not delete", str(exc))
            return
        if self._current == name:
            self._current = None
        self.refresh()
        self.status.setText(f"Deleted '{name}'.")

    # ---- for the assembly view -------------------------------------------
    def current_name(self):
        return self._current

    def config_for(self, name: str):
        """The stored config for a design, without loading it into the tab."""
        try:
            with open(self._path_for(name), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return None
        return data.get("config", data)
