"""Lay a table's columns out so they fit the width the table is given.

Qt has no resize mode that does this. ResizeToContents sizes every column to
its longest row and lets the total run past the viewport, so the columns on
the right go off the edge where nobody scrolls to find them. Stretch divides
the viewport up and squeezes the narrow columns until their own headings
clip. Both modes were in use here and both shipped a table that did not fit.

The widths involved are all font metrics, so a layout tuned by hand against
one platform's font is not a layout at all. The Tolerances results table was
sized until it fitted in 721 px with the Linux default font; on Windows, in
Segoe UI, the same table asked for 888 px in 803 and hid the Tolerance and
Impact columns - the two the tab exists to report. The Flight Report's
Category heading clipped there, and its Check column fell to 114 px against
a 150 px floor. Nothing about any of that was visible from Linux.

So nothing here is a tuned constant. Every width is derived from the font in
use, and the one hard guarantee is the useful one: no column is ever narrower
than its own heading, and the columns never add up to more than the room
there is.
"""

from PyQt5 import QtCore, QtGui, QtWidgets

# Qt draws a header's text inset by a few pixels on each side and the sort
# indicator and grid line take a little more. Measuring the string alone
# reports a column as fitting when its last letter is already cut off.
PAD = 14


def header_need(table, col, pad=PAD):
    """The width `col`'s heading needs to be read in full.

    Measured per LINE: a heading wrapped onto two lines needs its longest
    word, not the whole string laid out flat. The stylesheet draws headings
    uppercase (`text-transform: uppercase`), and capitals are wider, so the
    uppercase form is what gets measured.
    """
    item = table.horizontalHeaderItem(col)
    if item is None:
        return 0
    metrics = QtGui.QFontMetrics(table.horizontalHeader().font())
    return max(metrics.horizontalAdvance(line.upper())
               for line in item.text().split("\n")) + pad


def headings_width(table, extra=28):
    """The narrowest the table can be and still show every heading.

    `extra` covers the frame, the grid and a vertical scrollbar, which sit
    outside the viewport the columns are measured against.
    """
    return sum(header_need(table, col)
               for col in range(table.columnCount())) + extra


def fit_columns(table, stretch_col=None, caps=None, min_widths=None):
    """Size every column of `table` to fit inside the table's viewport.

    `stretch_col` is the column that absorbs whatever room is left over - the
    description, normally, since it is the one that can always use more. It
    is left in Qt's Stretch mode so it keeps following the window as it is
    resized; every other column is given an explicit width here.

    `caps` is an optional {column: px} ceiling for columns whose rows are long
    free text, and `min_widths` an optional {column: px} floor. A column is
    never taken below its own heading whatever those say.

    When the columns cannot all fit, the ones with the most room to spare
    give it up first, proportionally, down to their headings. The total only
    exceeds the viewport if even the headings do not fit, which no font
    reaches at these sizes.
    """
    caps = caps or {}
    min_widths = min_widths or {}
    header = table.horizontalHeader()
    count = table.columnCount()
    if count == 0:
        return

    # Read the room FIRST. Changing the resize modes and sizing to contents
    # before this was a trap: a table fitted before it had been laid out
    # returned here with its modes already switched to Interactive, no widths
    # written and the stretch column never put into Stretch, so it sat with
    # contents-width columns running off the edge and nothing stretching
    # until some later resize happened to arrive.
    room = table.viewport().width()
    if room <= 0:
        return

    # Start from the contents, then clamp each column into its own range.
    # Interactive first: a column still in ResizeToContents ignores
    # setColumnWidth, which is why capping a stretched column achieved
    # nothing the last time this was attempted.
    for col in range(count):
        if col != stretch_col:
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.Interactive)
    table.resizeColumnsToContents()

    # The header's own minimum has to be part of every floor. Qt raises any
    # narrower width back up to it without saying so, and a width that comes
    # back bigger than the one asked for puts the total over the budget - six
    # pixels of it, in the table that found this, which was exactly enough to
    # starve the stretch column below its own heading and clip it.
    min_section = header.minimumSectionSize()
    floor = {}
    want = {}
    for col in range(count):
        floor[col] = max(header_need(table, col), min_widths.get(col, 0),
                         min_section)
        width = max(table.columnWidth(col), floor[col])
        if col in caps:
            width = max(floor[col], min(width, caps[col]))
        want[col] = width

    # The stretch column is normally not sized here - Qt hands it the
    # leftovers - but its floor has to be reserved, or the leftovers come out
    # smaller than its heading and Qt clips it instead.
    others = [c for c in range(count) if c != stretch_col]
    reserved = floor.get(stretch_col, 0) if stretch_col is not None else 0
    budget = room - reserved

    def shrink_to(target):
        """Take `target` pixels off the columns with the most room to spare."""
        slack = {c: want[c] - floor[c] for c in others}
        total_slack = sum(slack.values())
        if total_slack <= 0:
            return
        taken = 0
        order = sorted(others, key=lambda c: slack[c], reverse=True)
        for i, col in enumerate(order):
            if i == len(order) - 1:
                give = target - taken              # last absorbs the rounding
            else:
                give = int(round(target * slack[col] / total_slack))
            give = max(0, min(give, slack[col]))
            want[col] -= give
            taken += give

    excess = sum(want[c] for c in others) - budget
    if excess > 0:
        shrink_to(excess)

    for col in others:
        table.setColumnWidth(col, want[col])

    # Then check, rather than assume. A column can come back wider than it
    # was set to - a minimum enforced somewhere else in the style, a section
    # that refused the width - and the only thing that matters is what the
    # columns actually measure now.
    for _ in range(3):
        actual = sum(table.columnWidth(c) for c in others)
        if actual <= budget:
            break
        for col in others:
            want[col] = table.columnWidth(col)
        shrink_to(actual - budget)
        for col in others:
            table.setColumnWidth(col, want[col])

    if stretch_col is None:
        return

    # The floors are the one thing that does not bend. Where they all fit,
    # the stretch column takes the leftovers and keeps following the window.
    # Where they do not - a window dragged down to its minimum, a font the
    # layout was never going to survive - Stretch would quietly hand the
    # stretch column less than its heading and clip it: the Flight Report's
    # Check column, which says WHAT was tested, measured 47 px against its
    # own 150 px floor at the app's minimum width, heading included. A
    # horizontal scrollbar is the honest answer there, so the column is
    # pinned at its floor and the table is allowed to scroll. Nothing is
    # readable at a width it does not have, and a heading nobody can read is
    # worse than a scrollbar.
    if sum(table.columnWidth(c) for c in others) + reserved > room:
        header.setSectionResizeMode(stretch_col,
                                    QtWidgets.QHeaderView.Interactive)
        table.setColumnWidth(stretch_col, floor[stretch_col])
    else:
        header.setSectionResizeMode(stretch_col,
                                    QtWidgets.QHeaderView.Stretch)


class _Refitter(QtCore.QObject):
    """Re-runs the fit whenever the table changes width."""

    def __init__(self, table, kwargs):
        super().__init__(table)
        self._table = table
        self._kwargs = kwargs
        self._busy = False
        self._font_key = self._current_font_key()

    def _current_font_key(self):
        return self._table.horizontalHeader().font().key()

    #: Resize is the obvious one. FontChange and StyleChange matter because
    #: these columns are Interactive now: Qt recomputes ResizeToContents and
    #: Stretch sections itself when the font changes, but an Interactive
    #: section keeps whatever width was last written to it. Switching theme
    #: swaps the font without touching the viewport width, so without these
    #: every column would keep its old font's width and the headings would
    #: clip with no way back short of resizing the window.
    _TRIGGERS = (QtCore.QEvent.Resize,
                 QtCore.QEvent.FontChange,
                 QtCore.QEvent.StyleChange)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            # Only when the font REALLY changed. Writing column widths makes
            # Qt emit StyleChange on the header, so refitting on the event
            # itself re-entered through the queue the moment _busy cleared
            # and span at 98% CPU for as long as anything kept pumping
            # events. Comparing the font key ends it: a width written by the
            # fitter does not change the font, so it does not come back.
            key = self._current_font_key()
            if key == self._font_key:
                return False
            self._font_key = key
        if kind in self._TRIGGERS and not self._busy:
            # Setting column widths can show or hide the scrollbar, which
            # resizes the viewport and calls straight back in here.
            self._busy = True
            try:
                fit_columns(self._table, **self._kwargs)
            finally:
                self._busy = False
                self._font_key = self._current_font_key()
        return False


def keep_fitted(table, stretch_col=None, caps=None, min_widths=None):
    """Fit `table` now, and again every time it is given a new width.

    Needed because the fit depends on the room available: widths worked out
    while the window is maximised do not fit once it is restored to 1366x768,
    and the first thing anyone does with a window is resize it.
    """
    kwargs = dict(stretch_col=stretch_col, caps=caps, min_widths=min_widths)
    existing = table.property("_table_fit_refitter")
    if existing is None:
        refitter = _Refitter(table, kwargs)
        # The viewport gets the resizes; the table and its header get the
        # font and style changes.
        table.viewport().installEventFilter(refitter)
        table.installEventFilter(refitter)
        table.horizontalHeader().installEventFilter(refitter)
        table.setProperty("_table_fit_refitter", refitter)
    else:
        existing._kwargs = kwargs
    fit_columns(table, **kwargs)
