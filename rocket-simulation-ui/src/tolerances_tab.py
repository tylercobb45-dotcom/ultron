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

import theme
import tolerances as tol


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
            "<b>Tolerances</b> &mdash; how far wrong each part of the motor "
            "can be built and still have this rocket meet its goals.<br><br>"
            "Each component is scaled up and down from the engine as it is "
            "configured now. Every step runs the <i>full</i> engine burn and "
            "the <i>full</i> trajectory, then grades the flight against this "
            "rocket's own goals &mdash; the same goals the Flight Report "
            "uses. Nothing here changes the loaded rocket.")
        intro.setWordWrap(True)
        lv.addWidget(intro)

        comp_group = QtWidgets.QGroupBox("Components to measure")
        cv = QtWidgets.QVBoxLayout(comp_group)
        self._boxes = {}
        for knob in tol.default_knobs():
            box = QtWidgets.QCheckBox(
                # Escaped rather than literal so this file stays pure
                # ASCII on disk. Qt renders it the same either way.
                "%s \u2014 %s" % (knob.component, knob.quantity))
            box.setChecked(True)
            box.setToolTip(knob.why)
            self._boxes[knob.key] = box
            cv.addWidget(box)
        lv.addWidget(comp_group)

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
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["Component", "What was varied", "As configured",
             "Lowest that works", "Highest that works", "Tolerance"])
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
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setPlaceholderText(
            "Each line is one complete engine burn and trajectory.")
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
            engine, ctx = self._get_inputs()
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
        self.log.clear()
        self.note.setText("")
        goals = tol.goal_list(ctx.vehicle)
        self.log.appendPlainText(
            "Graded against: " + "; ".join(g.label() for g in goals))
        # Roughly: one baseline, then a far-end probe plus a bisection each
        # way per component. Only a guide for the bar - a component that
        # survives its whole range finishes in one flight.
        est = 1 + len(knobs) * 2 * (2 + tol.MAX_TRIALS_PER_DIRECTION)
        self.progress.setMaximum(est)
        self.progress.setValue(0)
        self._say("Flying the engine as configured...")

        done = {"n": 0}

        def on_trial(knob, trial, downward):
            done["n"] += 1
            self.progress.setValue(min(done["n"], est))
            arrow = "down" if downward else "up"
            self.log.appendPlainText(
                "  %-14s %-4s x%.4f  %-4s  apogee %8.0f ft%s"
                % (knob.key, arrow, trial.factor,
                   "ok" if trial.passed else "MISS", trial.apogee_ft,
                   ("   missed: " + "; ".join(trial.failed_goals))
                   if trial.failed_goals else ""))
            self._say(f"{knob.component}: {knob.quantity} &mdash; "
                      f"{arrow} at {trial.factor * 100:.1f}% of baseline "
                      f"({done['n']} flights so far)")

        def on_progress(_i, _n, message):
            self.log.appendPlainText(message)
            QtWidgets.QApplication.processEvents()

        try:
            run = tol.find_tolerances(
                engine, ctx, knobs=knobs, on_progress=on_progress,
                on_trial=on_trial, should_cancel=lambda: self._cancel)
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
        self.progress.setValue(self.progress.maximum())
        self._show(run)

    # ---- results ---------------------------------------------------------
    def _show(self, run):
        pal = theme.PALETTE
        good, bad = pal.get('good', '#3fb950'), pal['critical']
        if run.error:
            self._say(f"<b style='color:{bad}'>{run.error}</b><br>"
                      f"The engine as configured reaches "
                      f"{run.baseline_apogee_ft:,.0f} ft. Missed: "
                      + "; ".join(run.baseline_missed))
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
        self.table.resizeColumnsToContents()
        header = self.table.horizontalHeader()
        for col, cap in ((1, 230),):
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
        self._say(
            f"{headline}{run.trials} complete flights.<br>"
            f"The engine as configured reaches "
            f"<b>{run.baseline_apogee_ft:,.0f} ft</b> and meets its goals; "
            f"the table says how far each part can drift from that before it "
            f"stops.")
