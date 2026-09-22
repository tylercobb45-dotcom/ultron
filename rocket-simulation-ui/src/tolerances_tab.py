"""The Tolerances tab: how far each engine component can be wrong.

A front end over ``tolerances``. It owns no physics - it collects the engine
and the flight context from the rest of the app, hands them to the search, and
shows what came back.

It is deliberately a dead end in the data flow. Nothing here writes to the
loaded rocket, the Engine tab or the Simulation tab: every trial flies a COPY
of the engine with one number moved. You can run it, read it, and the rocket
you had before is the rocket you still have.
"""
from __future__ import annotations

import traceback

from PyQt5 import QtWidgets, QtCore

import datasheet
import theme
import tolerances as tol


class TrialSheetDialog(QtWidgets.QDialog):
    """One trial's flight and motor data, in the app's own spreadsheet."""

    def __init__(self, title, summary, flight_rows, engine_rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1100, 700)
        layout = QtWidgets.QVBoxLayout(self)

        head = QtWidgets.QLabel(f"<b>{title}</b><br>{summary}")
        head.setWordWrap(True)
        head.setTextFormat(QtCore.Qt.RichText)
        layout.addWidget(head)

        tabs = QtWidgets.QTabWidget()
        # The app's own DataSheet, with the same column spec the Simulation
        # tab uses - so this reads like the sheets beside it and exports the
        # same way, rather than being a third kind of table.
        flight = datasheet.DataSheet(datasheet.FLIGHT_COLUMNS,
                                     title="tolerance_flight")
        flight.set_rows(flight_rows)
        tabs.addTab(flight, "Flight Data")

        engine = datasheet.DataSheet(datasheet.ENGINE_COLUMNS,
                                     title="tolerance_engine")
        engine.set_rows(engine_rows)
        tabs.addTab(engine, "Engine Data")
        layout.addWidget(tabs)

        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=QtCore.Qt.AlignRight)


class TolerancesTab(QtWidgets.QWidget):
    """Measure the design margin on each engine component."""

    def __init__(self, get_inputs=None, parent=None):
        super().__init__(parent)
        # Returns (engine, FlightContext) or raises with a message worth
        # showing. Supplied by the main window, which owns the tabs the
        # inputs come from.
        self._get_inputs = get_inputs
        self._cancel = False
        self._running = False
        self._last_run = None
        self._replay = None
        self._open_dialogs = []
        self._build_ui()

    # ---- construction ----------------------------------------------------
    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QtWidgets.QWidget()
        # Same lesson as the Engine tab: a panel narrower than its form clips
        # the right-hand side of every row.
        left.setMinimumWidth(430)
        lv = QtWidgets.QVBoxLayout(left)

        intro = QtWidgets.QLabel(
            "<b>Tolerances</b> &mdash; how far wrong each thing can be and "
            "still have this rocket meet its goals.<br><br>"
            "Two kinds of wrong. The <b>hardware</b> can be built off the "
            "print. The <b>model</b> can simply be mistaken: these equations "
            "are our interpretation of the physics, not the physics, so what "
            "they predict for chamber pressure or regression rate can be out "
            "by a fifth and nothing will tell you. Both are scaled across "
            "the whole burn and everything downstream follows.<br><br>"
            "Every step runs the <i>full</i> engine burn and the <i>full</i> "
            "trajectory, then grades it against this rocket's own goals "
            "&mdash; the same goals the Flight Report uses. The order is "
            "measured first, so the things this rocket actually cares about "
            "come out on top. Nothing here changes the loaded rocket.<br><br>"
            "<i>All ten takes a couple of minutes. Stop works at any "
            "point.</i>")
        intro.setWordWrap(True)
        lv.addWidget(intro)

        self._boxes = {}
        for kind, title, blurb in (
            (tol.HARDWARE, "What the hardware is",
             "Build tolerances: the part is not quite the size on the print."),
            (tol.MODEL, "What the model says",
             "Model error: the equation is our interpretation of the "
             "physics, and an interpretation can be wrong. Each of these is "
             "scaled across the WHOLE burn, and everything downstream "
             "follows."),
        ):
            group = QtWidgets.QGroupBox(title)
            cv = QtWidgets.QVBoxLayout(group)
            note = QtWidgets.QLabel(f"<span style='font-size:9pt'>{blurb}</span>")
            note.setWordWrap(True)
            cv.addWidget(note)
            for knob in tol.default_knobs():
                if knob.kind != kind:
                    continue
                box = QtWidgets.QCheckBox(
                    # Escaped rather than literal so this file stays pure
                    # ASCII on disk. Qt renders it the same either way.
                    "%s \u2014 %s" % (knob.component, knob.quantity))
                box.setChecked(True)
                box.setToolTip(knob.why)
                self._boxes[knob.key] = box
                cv.addWidget(box)
            lv.addWidget(group)

        self.run_button = QtWidgets.QPushButton("Find Tolerances")
        self.run_button.clicked.connect(self._run)
        lv.addWidget(self.run_button)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.clicked.connect(self._request_cancel)
        self.stop_button.setEnabled(False)
        lv.addWidget(self.stop_button)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setValue(0)
        lv.addWidget(self.progress)

        self.status = QtWidgets.QLabel(
            "Load a rocket, then press Find Tolerances.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.RichText)
        self.status.setStyleSheet(
            f"QLabel {{ background:{theme.PALETTE['panel']}; "
            f"border:1px solid {theme.PALETTE['border']}; "
            f"border-left:3px solid {theme.PALETTE['accent']}; padding:8px; "
            f"color:{theme.PALETTE['text']}; }}")
        lv.addWidget(self.status)
        lv.addStretch()
        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        right.setMinimumWidth(520)
        rv = QtWidgets.QVBoxLayout(right)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Component", "What was varied", "As modelled",
             "Lowest OK", "Highest OK", "Tolerance", "Impact"])
        # Short headers on purpose. The full phrases needed more width than
        # the values under them, so capping those columns to fit the numbers
        # clipped their own titles to "WEST THAT WOR".
        self.table.setToolTip(
            "Lowest OK / Highest OK: the furthest this quantity can be wrong "
            "in each direction and still have the rocket meet its goals.\n"
            "Impact: how much apogee moved when it was put 10% high - "
            "measured on this rocket, and what the rows are ordered by.")
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(180)
        rv.addWidget(self.table, stretch=1)

        self.note = QtWidgets.QLabel("")
        self.note.setWordWrap(True)
        self.note.setTextFormat(QtCore.Qt.RichText)
        rv.addWidget(self.note)

        log_group = QtWidgets.QGroupBox("Every flight it ran")
        lg = QtWidgets.QVBoxLayout(log_group)
        hint = QtWidgets.QLabel(
            "Each row is one complete engine burn and trajectory. "
            "<b>Double-click a row</b> to open its spreadsheet.")
        hint.setWordWrap(True)
        lg.addWidget(hint)
        self.log = QtWidgets.QTableWidget()
        self.log.setColumnCount(6)
        self.log.setHorizontalHeaderLabels(
            ["#", "What was varied", "Way", "Setting", "Result", "Apogee"])
        self.log.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.log.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows)
        self.log.verticalHeader().setVisible(False)
        self.log.itemDoubleClicked.connect(self._open_trial)
        # Let the description take the slack and pin the rest to their
        # contents, so the column saying WHAT was varied is not the one that
        # gets squeezed to "Throat ..." while empty space sits to its right.
        _hdr = self.log.horizontalHeader()
        _hdr.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        _hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        _hdr.setStretchLastSection(False)
        lg.addWidget(self.log)
        rv.addWidget(log_group, stretch=1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

    # ---- running ---------------------------------------------------------
    def selected_knobs(self):
        return [k for k in tol.default_knobs()
                if self._boxes[k.key].isChecked()]

    def _request_cancel(self):
        self._cancel = True
        self.status.setText("Stopping after this flight...")

    def _say(self, html):
        self.status.setText(html)
        QtWidgets.QApplication.processEvents()

    def _run(self):
        if self._running:
            return
        knobs = self.selected_knobs()
        if not knobs:
            self._say("Tick at least one component to measure.")
            return
        if self._get_inputs is None:
            self._say("This tab has not been connected to a rocket.")
            return
        try:
            engine, ctx, reference_ft = self._get_inputs()
        except Exception as exc:
            self._say(f"<b style='color:{theme.PALETTE['critical']}'>"
                      f"Could not read the rocket: {exc}</b>")
            traceback.print_exc()
            return

        self._cancel = False
        self._running = True
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.table.setRowCount(0)
        self.log.setRowCount(0)
        self.note.setText("")
        goals = tol.goal_list(ctx.vehicle)
        # Kept so a double-clicked row can be flown again exactly as it was.
        self._replay = {"engine": engine, "ctx": ctx, "goals": goals,
                        "knobs": {k.key: k for k in knobs}, "baselines": {}}
        # Roughly: one baseline, then a far-end probe plus a bisection each
        # way per component. Only a guide for the bar - a component that
        # survives its whole range finishes in one flight.
        est = 1 + len(knobs) * (1 + 2 * (2 + tol.MAX_TRIALS_PER_DIRECTION))
        self.progress.setMaximum(est)
        self.progress.setValue(0)
        self._say("Flying the engine as configured...")

        done = {"n": 0}

        def on_trial(knob, trial, downward):
            done["n"] += 1
            self.progress.setValue(min(done["n"], est))
            arrow = "down" if downward else "up"
            row = self.log.rowCount()
            self.log.insertRow(row)
            cells = [str(row + 1), knob.quantity, arrow,
                     f"x{trial.factor:.4f}",
                     "ok" if trial.passed else "MISS",
                     f"{trial.apogee_ft:,.0f} ft"]
            for col, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if trial.failed_goals:
                    item.setToolTip("Missed: " + "; ".join(trial.failed_goals))
                if col == 0:
                    # Everything needed to fly this one again lives on the
                    # row, so opening it later does not depend on the search
                    # still being in progress.
                    item.setData(QtCore.Qt.UserRole,
                                 (knob.key, float(trial.factor)))
                self.log.setItem(row, col, item)
            self.log.scrollToBottom()
            self._say(f"{knob.component}: {knob.quantity} &mdash; "
                      f"{arrow} at {trial.factor * 100:.1f}% of baseline "
                      f"({done['n']} flights so far)")

        def on_progress(_i, _n, message):
            QtWidgets.QApplication.processEvents()

        try:
            run = tol.find_tolerances(
                engine, ctx, knobs=knobs, on_progress=on_progress,
                on_trial=on_trial, should_cancel=lambda: self._cancel,
                reference_apogee_ft=reference_ft)
        except Exception as exc:
            self._say(f"<b style='color:{theme.PALETTE['critical']}'>"
                      f"The search failed: {exc}</b>")
            traceback.print_exc()
            return
        finally:
            self._running = False
            self.run_button.setEnabled(True)
            self.stop_button.setEnabled(False)

        self._last_run = run
        if self._replay is not None:
            self._replay["baselines"] = dict(run.baselines)
        self.progress.setValue(self.progress.maximum())
        self._show(run)

    # ---- opening one flight ----------------------------------------------
    def _open_trial(self, item):
        """Fly the double-clicked trial again, this time keeping every row.

        The sweep throws per-sample data away on purpose - that is most of
        why it is quick. So a flight somebody wants to look at is simply
        flown again, with capture on. It is one flight, it is deterministic,
        and it comes back identical to the one in the table.
        """
        if self._running:
            self._say("Wait for the search to finish before opening a flight.")
            return
        replay = self._replay
        if not replay:
            self._say("Run a search first - there are no flights to open.")
            return
        row = item.row()
        key_item = self.log.item(row, 0)
        payload = key_item.data(QtCore.Qt.UserRole) if key_item else None
        if not payload:
            return
        knob_key, factor = payload
        knob = replay["knobs"].get(knob_key)
        if knob is None:
            return
        baseline = replay["baselines"].get(knob_key)
        if not baseline:
            self._say("That flight's baseline is no longer available - "
                      "run the search again.")
            return

        self._say(f"Re-flying {knob.component} at "
                  f"{factor * 100:.2f}% to collect its data...")
        try:
            engine, scales = knob.apply(replay["engine"], baseline, factor)
            outcome, burn = tol.capture_trial(engine, replay["ctx"], scales)
        except Exception as exc:
            self._say(f"<b style='color:{theme.PALETTE['critical']}'>"
                      f"Could not re-fly that trial: {exc}</b>")
            traceback.print_exc()
            return
        if outcome is None:
            self._say("That trial does not produce a flight to tabulate - "
                      "the motor did not run.")
            return

        met, missed = tol.grade(outcome, replay["goals"])
        title = (f"{knob.component} \u2014 {knob.quantity} at "
                 f"{factor * 100:.2f}% of as-modelled "
                 f"({tol.display_value(knob, baseline * factor)})")
        summary = (f"Apogee {outcome.apogee_ft:,.0f} ft, max Mach "
                   f"{outcome.max_mach:.2f}, max {outcome.max_g:.1f} g, "
                   f"landing {outcome.landing_speed_ms:.1f} m/s. "
                   + ("Meets the goals." if met
                      else "MISSES: " + "; ".join(missed)))
        dialog = TrialSheetDialog(title, summary, outcome.rows, burn.rows,
                                  parent=self)
        self._open_dialogs.append(dialog)      # keep it alive, non-modal
        dialog.show()
        self._say(f"Opened the spreadsheet for {knob.quantity} at "
                  f"{factor * 100:.2f}%.")

    # ---- results ---------------------------------------------------------
    def _show(self, run):
        pal = theme.PALETTE
        good, bad = pal.get('good', '#3fb950'), pal['critical']
        if run.error:
            detail = ("Missed: " + "; ".join(run.baseline_missed)
                      if run.baseline_missed else "")
            self._say(f"<b style='color:{bad}'>{run.error}</b><br>"
                      f"The engine as configured reaches "
                      f"{run.baseline_apogee_ft:,.0f} ft. {detail}")
            return

        self.table.setRowCount(len(run.results))
        for row, r in enumerate(run.results):
            def put(col, text):
                self.table.setItem(row, col,
                                   QtWidgets.QTableWidgetItem(text))
            put(0, r.knob.component)
            put(1, r.knob.quantity)
            put(2, tol.display_value(r.knob, r.baseline))
            if r.error and r.error != "cancelled":
                put(3, "-")
                put(4, "-")
                put(5, r.error)
                put(6, "-")
                continue
            # A run stopped part way through still measured whatever it
            # finished. Showing the completed side and marking only the other
            # one unmeasured is more use than blanking the row - the search is
            # slow enough that a half answer is worth keeping.
            low_done = r.low_factor is not None
            high_done = r.high_factor is not None
            put(3, tol.display_value(r.knob, r.low_value())
                if low_done else "not measured")
            put(4, tol.display_value(r.knob, r.high_value())
                if high_done else "not measured")
            down = f"-{r.down_pct:.1f}%" if low_done else "?"
            up = f"+{r.up_pct:.1f}%" if high_done else "?"
            put(5, f"{down}  /  {up}")
            put(6, "-" if r.sensitivity_pct is None
                else f"{r.sensitivity_pct:+.1f}% apogee")
        self.table.resizeColumnsToContents()
        header = self.table.horizontalHeader()
        # Cap the value columns BEFORE stretching the description. Sizing
        # every column to its contents first gives the three number columns
        # whatever their widest row needs, and "What was varied" - the column
        # that says what the row is about - gets the crumbs and shows
        # "Pressure the mod...". Capping a column that is about to be
        # stretched, as this did, achieves nothing at all.
        for col, cap in ((2, 130), (3, 140), (4, 140), (5, 150), (6, 130)):
            if self.table.columnWidth(col) > cap:
                self.table.setColumnWidth(col, cap)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)

        notes = []
        for r in run.results:
            for side, text in (("downward", r.low_note), ("upward", r.high_note)):
                if text:
                    notes.append(f"<li><b>{r.knob.component}</b> "
                                 f"({side}): {text}</li>")
        self.note.setText(
            f"<b>Worth knowing</b><ul>{''.join(notes)}</ul>" if notes else "")

        headline = ("<b style='color:%s'>Stopped early.</b> " % bad
                    if run.cancelled else
                    "<b style='color:%s'>Done.</b> " % good)
        # The tightest margin among the things that matter is the one number
        # worth putting in front of someone. A 10% margin on the quantity the
        # rocket is most sensitive to is the whole result; the table is the
        # working.
        tightest = ""
        scored = [r for r in run.results
                  if r.down_pct is not None and not r.error]
        if scored:
            worst = min(scored, key=lambda r: r.down_pct)
            tightest = (
                f"<br><br>Tightest margin: <b>{worst.knob.component} "
                f"&mdash; {worst.knob.quantity}</b>, which only has "
                f"<b>-{worst.down_pct:.0f}%</b> before this rocket stops "
                f"making its goals.")
        agree = ""
        if run.agreement is not None:
            agree = (f"<br>Checked against the main simulation: "
                     f"<b>{run.agreement * 100:+.2f}%</b> on baseline apogee.")
        self._say(
            f"{headline}{run.trials} complete flights.{agree}<br>"
            f"The engine as configured reaches "
            f"<b>{run.baseline_apogee_ft:,.0f} ft</b> and meets its goals; "
            f"the table says how far each thing can be wrong before it "
            f"stops.{tightest}")
