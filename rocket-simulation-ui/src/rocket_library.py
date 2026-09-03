"""Rockets tab: the saved-rocket library.

One place where every rocket you have built is stored, so you can switch
between them and have the whole app repopulate itself - flight inputs on the
Simulation tab, the motor on the Engine Lab tab, and the materials and
geometry on the Flight Report tab.

Rockets are plain JSON files in the profiles directory, the same files the
Settings tab's profile dropdown reads, so the two stay in sync.
"""
from __future__ import annotations

import json
import os
import shutil

from PyQt5 import QtWidgets, QtCore

_INPUT_STYLE = """
    QLineEdit, QComboBox {
        padding: 5px 8px; font-size: 12px;
        border: 2px solid #BCA16A; border-radius: 6px;
        background-color: #FDF6E3; color: #3C2F1E; min-height: 22px;
    }
"""

# Fields worth surfacing in the summary pane, per section.
_SUMMARY = [
    ("Flight", "rocket_parameters", [
        ("mass", "Liftoff mass"), ("prop_mass", "Propellant mass"),
        ("cd", "Drag coefficient"), ("area", "Cross-section area"),
        ("body_diameter", "Body diameter"), ("fin_count", "Fin count"),
        ("chute_height", "Chute deploy height"), ("chute_size", "Chute size"),
    ]),
    ("Engine", "engine", [
        ("fuel", "Fuel"), ("d_tank", "Tank diameter (mm)"),
        ("L_tank", "Tank length (mm)"), ("d_throat", "Throat diameter (mm)"),
        ("eps_exp", "Expansion ratio"), ("d_grain_outer", "Grain OD (mm)"),
        ("d_port_0", "Initial port (mm)"), ("L_grain", "Grain length (mm)"),
    ]),
    ("Materials & structure", "vehicle", [
        ("airframe_material", "Airframe"), ("fin_material", "Fins"),
        ("nozzle_material", "Nozzle throat"), ("chamber_material", "Chamber"),
        ("tank_material", "Tank"), ("target_altitude_ft", "Target altitude (ft)"),
        ("body_wall_m", "Body wall (mm)"), ("rail_length_m", "Rail length (m)"),
    ]),
]


class RocketLibraryWidget(QtWidgets.QWidget):
    """Browse, switch between, and manage saved rockets."""

    def __init__(self, capture_config, apply_config, writable_dir,
                 search_dirs, on_changed=None, on_loaded=None, parent=None):
        """
        capture_config : () -> dict   snapshot of the app's current setup
        apply_config   : (dict) -> bool  push a saved setup back into the app
        writable_dir   : () -> str    where new rockets are saved
        search_dirs    : () -> list[str]  every directory to read rockets from
        on_changed     : optional callback after the library is modified
        on_loaded      : optional callback after a rocket is loaded
        """
        super().__init__(parent)
        self._capture = capture_config
        self._apply = apply_config
        self._writable_dir = writable_dir
        self._search_dirs = search_dirs
        self._on_changed = on_changed
        self._on_loaded = on_loaded
        self._paths = {}       # display name -> file path
        self._build_ui()
        self.refresh()

    # ---- UI ---------------------------------------------------------------
    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # --- left: the list of rockets ---
        left = QtWidgets.QWidget()
        left.setMaximumWidth(380)
        lv = QtWidgets.QVBoxLayout(left)
        lv.addWidget(QtWidgets.QLabel(
            "<b>Saved Rockets</b><br><span style='font-size:11px'>Pick one and load "
            "it to repopulate the whole app.</span>"))

        self.list = QtWidgets.QListWidget()
        self.list.setStyleSheet(
            "QListWidget { background:#FDF6E3; border:2px solid #BCA16A; "
            "border-radius:6px; font-size:13px; } "
            "QListWidget::item { padding:7px; } "
            "QListWidget::item:selected { background:#E94F37; color:white; }")
        self.list.currentItemChanged.connect(self._show_details)
        self.list.itemDoubleClicked.connect(self._load_selected)
        lv.addWidget(self.list, stretch=1)

        self.load_button = QtWidgets.QPushButton("Load This Rocket")
        self.load_button.setToolTip("Populate the Simulation, Engine Lab and "
                                    "Flight Report tabs from this rocket")
        self.load_button.clicked.connect(self._load_selected)
        lv.addWidget(self.load_button)

        self.save_button = QtWidgets.QPushButton("Save Current Setup as New Rocket")
        self.save_button.clicked.connect(self._save_new)
        lv.addWidget(self.save_button)

        row = QtWidgets.QHBoxLayout()
        for label, slot, tip in (
            ("Update", self._update_selected, "Overwrite this rocket with the current setup"),
            ("Duplicate", self._duplicate_selected, "Copy this rocket under a new name"),
            ("Delete", self._delete_selected, "Delete this rocket"),
        ):
            b = QtWidgets.QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        lv.addLayout(row)

        row2 = QtWidgets.QHBoxLayout()
        for label, slot in (("Import...", self._import), ("Export...", self._export),
                            ("Refresh", self.refresh)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            row2.addWidget(b)
        lv.addLayout(row2)
        layout.addWidget(left)

        # --- right: what is stored in the selected rocket ---
        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        self.title = QtWidgets.QLabel("Select a rocket")
        self.title.setStyleSheet("font-size:16px; font-weight:bold; color:#3C2F1E;")
        rv.addWidget(self.title)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#1E6B2E; font-weight:bold;")
        rv.addWidget(self.status)

        self.details = QtWidgets.QTextEdit()
        self.details.setReadOnly(True)
        self.details.setStyleSheet(
            "background:#FDF6E3; border:2px solid #BCA16A; border-radius:8px; "
            "padding:10px; color:#3C2F1E; font-size:12px;")
        rv.addWidget(self.details, stretch=1)
        layout.addWidget(right, stretch=1)

    # ---- library management -------------------------------------------------
    def refresh(self):
        """Rescan every profile directory."""
        current = self.list.currentItem().text() if self.list.currentItem() else None
        self.list.clear()
        self._paths.clear()
        seen = set()
        for directory in self._search_dirs():
            if not os.path.isdir(directory):
                continue
            try:
                names = sorted(os.listdir(directory))
            except OSError:
                continue
            for filename in names:
                if not filename.endswith(".json"):
                    continue
                name = filename[:-5]
                if name in seen:
                    continue          # a user copy shadows a bundled example
                seen.add(name)
                self._paths[name] = os.path.join(directory, filename)
                self.list.addItem(name)
        if current:
            found = self.list.findItems(current, QtCore.Qt.MatchExactly)
            if found:
                self.list.setCurrentItem(found[0])
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)

    def _selected(self):
        item = self.list.currentItem()
        if not item:
            return None, None
        name = item.text()
        return name, self._paths.get(name)

    def _read(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self, "Could not read rocket", str(exc))
            return None

    def _write(self, path, config):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            return True
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Could not save rocket", str(exc))
            return False

    # ---- actions ------------------------------------------------------------
    def _load_selected(self):
        name, path = self._selected()
        if not path:
            return
        config = self._read(path)
        if config is None:
            return
        if self._apply(config):
            self.status.setText(f"Loaded '{name}' - the Simulation, Engine Lab and "
                                f"Flight Report tabs now use this rocket.")
            if self._on_loaded:
                self._on_loaded(name, config)

    def _save_new(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save Rocket", "Name for this rocket:",
            QtWidgets.QLineEdit.Normal, "My Rocket")
        if not ok or not name.strip():
            return
        name = _safe_name(name)
        path = os.path.join(self._writable_dir(), f"{name}.json")
        if os.path.exists(path) and not self._confirm(
                "Overwrite rocket?", f"'{name}' already exists. Replace it?"):
            return
        description, _ = QtWidgets.QInputDialog.getText(
            self, "Description", "Short description (optional):")
        config = self._capture()
        config["name"] = name
        config["description"] = description or ""
        if self._write(path, config):
            self.refresh()
            self._select(name)
            self.status.setText(f"Saved '{name}'.")
            if self._on_changed:
                self._on_changed()

    def _update_selected(self):
        name, path = self._selected()
        if not path:
            return
        if not self._confirm("Update rocket?",
                             f"Overwrite '{name}' with the current setup?"):
            return
        existing = self._read(path) or {}
        config = self._capture()
        config["name"] = name
        config["description"] = existing.get("description", "")
        # A bundled example is read-only; write the edit to the user directory.
        target = os.path.join(self._writable_dir(), f"{name}.json")
        if self._write(target, config):
            self.refresh()
            self._select(name)
            self.status.setText(f"Updated '{name}'.")
            if self._on_changed:
                self._on_changed()

    def _duplicate_selected(self):
        name, path = self._selected()
        if not path:
            return
        new_name, ok = QtWidgets.QInputDialog.getText(
            self, "Duplicate Rocket", "Name for the copy:",
            QtWidgets.QLineEdit.Normal, f"{name} copy")
        if not ok or not new_name.strip():
            return
        new_name = _safe_name(new_name)
        config = self._read(path)
        if config is None:
            return
        config["name"] = new_name
        if self._write(os.path.join(self._writable_dir(), f"{new_name}.json"), config):
            self.refresh()
            self._select(new_name)
            self.status.setText(f"Duplicated '{name}' as '{new_name}'.")
            if self._on_changed:
                self._on_changed()

    def _delete_selected(self):
        name, path = self._selected()
        if not path:
            return
        if not self._confirm("Delete rocket?",
                             f"Permanently delete '{name}'?\n\n{path}"):
            return
        try:
            os.remove(path)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Could not delete", str(exc))
            return
        self.refresh()
        self.status.setText(f"Deleted '{name}'.")
        if self._on_changed:
            self._on_changed()

    def _import(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Rocket", "", "Rocket files (*.json);;All files (*)")
        if not path:
            return
        config = self._read(path)
        if config is None:
            return
        name = _safe_name(config.get("name") or os.path.basename(path)[:-5])
        target = os.path.join(self._writable_dir(), f"{name}.json")
        if os.path.exists(target) and not self._confirm(
                "Overwrite rocket?", f"'{name}' already exists. Replace it?"):
            return
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(path, target)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Could not import", str(exc))
            return
        self.refresh()
        self._select(name)
        self.status.setText(f"Imported '{name}'.")
        if self._on_changed:
            self._on_changed()

    def _export(self):
        name, path = self._selected()
        if not path:
            return
        target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Rocket", f"{name}.json", "Rocket files (*.json)")
        if not target:
            return
        try:
            shutil.copyfile(path, target)
            self.status.setText(f"Exported '{name}'.")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Could not export", str(exc))

    def _select(self, name):
        found = self.list.findItems(name, QtCore.Qt.MatchExactly)
        if found:
            self.list.setCurrentItem(found[0])

    def _confirm(self, title, text):
        return QtWidgets.QMessageBox.question(
            self, title, text,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes

    # ---- details pane -------------------------------------------------------
    def _show_details(self):
        name, path = self._selected()
        if not path:
            self.title.setText("Select a rocket")
            self.details.setPlainText("")
            return
        self.title.setText(name)
        self.status.setText("")
        config = self._read(path)
        if config is None:
            return

        lines = []
        if config.get("description"):
            lines.append(f"<i>{config['description']}</i><br>")
        if config.get("created"):
            lines.append(f"<span style='color:#6A6154'>Saved {config['created']}</span><br>")

        for heading, section, fields in _SUMMARY:
            data = config.get(section) or {}
            if not data:
                lines.append(f"<br><b>{heading}</b><br>"
                             f"<span style='color:#8A6100'>not stored in this rocket "
                             f"- it was saved before this section existed, and loading "
                             f"it will leave those tabs untouched.</span><br>")
                continue
            rows = [f"&nbsp;&nbsp;{label}: <b>{data[key]}</b>"
                    for key, label in fields if data.get(key) not in (None, "")]
            lines.append(f"<br><b>{heading}</b><br>" + "<br>".join(rows) + "<br>")

        curve = config.get("thrust_curve_path")
        if curve:
            exists = os.path.exists(curve)
            lines.append(
                f"<br><b>Thrust curve</b><br>&nbsp;&nbsp;{os.path.basename(curve)}"
                + ("" if exists else " <span style='color:#8E1B10'>(file is missing - "
                                    "regenerate it in the Engine Lab)</span>") + "<br>")
        self.details.setHtml("".join(lines))


def _safe_name(name: str) -> str:
    """Strip characters that are not legal in a Windows filename."""
    cleaned = "".join(c for c in name.strip() if c not in '<>:"/\\|?*').strip()
    return cleaned or "Rocket"
