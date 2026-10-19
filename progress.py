"""Progress over weeks: every take and vowel check charted over time, with the
weekly median, your baseline and target, and the change since your first week."""
from datetime import datetime, timedelta

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from profile_store import summarize
from widgets import ResponsiveGrid

DAY = 86400
WEEK = 7 * DAY
PERIODS = [("All time", None), ("Last 90 days", 90), ("Last 30 days", 30)]
MODES = [("Speak & Sing", None), ("Speak", "speak"), ("Sing", "sing")]
CHART_MIN_WIDTH = 400          # charts per row: as many as fit, up to three
DOT_COLOR = (52, 152, 219, 160)
MEDIAN_COLOR = "#f1c40f"
BASELINE_PEN = pg.mkPen((255, 255, 255, 170), width=1, style=Qt.PenStyle.DashLine)
TARGET_FILL, TARGET_EDGE = (46, 204, 113, 50), (46, 204, 113, 160)


def _get(*path):
    """Getter for a nested value, None when any step is missing."""
    def get(entry):
        for key in path:
            entry = entry.get(key) if isinstance(entry, dict) else None
        return entry
    return get


def _share_in_tune(entry):
    ex = entry.get("exercise") or {}
    if not ex.get("notes_total"):
        return None
    return 100 * ex["notes_in_tune"] / ex["notes_total"]


# key: (title, unit, kind of entry, getter, profile key for baseline/target).
# Pitch changes are reported in semitones, the rest in their own units.
SERIES = {
    "pitch": ("Pitch", "Hz", "take", _get("pitch"), "pitch"),
    "delta_f": ("Resonance (ΔF)", "Hz", "take", _get("delta_f"), "delta_f"),
    "vowel_delta_f": ("Vowel-balanced ΔF (vowel check)", "Hz", "vowels", _get("delta_f"), None),
    "h1h2": ("Fold Contact (H1*–H2*)", "dB", "take", _get("h1h2"), "h1h2"),
    "cpp": ("Clarity (CPP)", "dB", "take", _get("cpp"), "cpp"),
    "ring": ("Ring (2–4 kHz)", "dB", "take", _get("ring"), "ring"),
    "pitch_range": ("Intonation Range", "st", "take", _get("pitch_range"), "pitch_range"),
    "in_tune": ("Exercise notes in tune", "%", "take", _share_in_tune, None),
    "exercise_error": ("Exercise average error", "¢", "take", _get("exercise", "mean_abs_cents"), None),
    "vibrato_rate": ("Vibrato rate", "Hz", "take", _get("singing", "vibrato_rate"), None),
    "vibrato_extent": ("Vibrato extent", "¢", "take", _get("singing", "vibrato_extent"), None),
    "head_share": ("Head share (register estimate)", "%", "take", _get("register"), "register"),
    "chest_share": ("Chest share (register estimate)", "%", "take", _get("chest_share"), None),
    "register_breaks": ("Register breaks per minute", "/min", "take", _get("breaks_per_min"), None),
    "cq": ("Contact quotient (EGG)", "", "take", _get("cq"), None),
}
SING_ONLY = {"register_breaks"}        # series that only mean something when singing


def collect_entries(history, folder_takes=()):
    """History log entries plus the takes in the takes folder, one per take."""
    entries, seen = [], set()
    for take in folder_takes:
        entries.append(summarize(take) | {"kind": "take", "time": take["time"]})
        if take.get("uid"):
            seen.add(take["uid"])
    # A take in both (logged, then saved when a folder was chosen) counts once.
    entries += [e for e in history if not (e.get("kind") == "take" and e.get("uid") in seen)]
    return entries


def timestamp(entry):
    try:
        return datetime.fromisoformat(entry["time"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def select(entries, key, mode=None, include_imported=False, days=None, now=None):
    """(times, values) of one series, oldest first, after the filters."""
    _, _, kind, getter, _ = SERIES[key]
    cutoff = ((now or datetime.now()) - timedelta(days=days)).timestamp() if days else None
    points = []
    for e in entries:
        if e.get("kind", "take") != kind:
            continue
        if key in SING_ONLY and e.get("mode") != "sing":
            continue
        if kind == "take" and ((mode and e.get("mode") != mode) or
                               (e.get("source") and not include_imported)):
            continue
        if kind == "vowels" and mode == "sing":
            continue
        t, v = timestamp(e), getter(e)
        if t is None or v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        if cutoff is None or t >= cutoff:
            points.append((t, float(v)))
    points.sort()
    return np.array([p[0] for p in points]), np.array([p[1] for p in points])


def week_start(t):
    """Timestamp of local midnight on the Monday of t's week."""
    day = datetime.fromtimestamp(t).replace(hour=0, minute=0, second=0, microsecond=0)
    return (day - timedelta(days=day.weekday())).timestamp()


def weekly_medians(times, values):
    """(week start times, median per week) for weeks with data."""
    weeks = {}
    for t, v in zip(times, values):
        weeks.setdefault(week_start(t), []).append(v)
    starts = sorted(weeks)
    return np.array(starts), np.array([float(np.median(weeks[w])) for w in starts])


def change_text(key, times, values):
    """e.g. 'this week 1130 Hz · +45 Hz since week of Oct 6', or the latest
    value when there is only one week."""
    if len(values) == 0:
        return "no data yet"
    unit = SERIES[key][1]
    starts, medians = weekly_medians(times, values)
    current = "this week" if starts[-1] == week_start(datetime.now().timestamp()) else "latest week"
    latest = f"{current} {_format(medians[-1], unit)}"
    if len(starts) < 2:
        return f"{latest} · {len(values)} take{'s' if len(values) != 1 else ''}"
    first = datetime.fromtimestamp(starts[0]).strftime("%b %d").replace(" 0", " ")
    if key == "pitch":
        delta = f"{12 * np.log2(medians[-1] / medians[0]):+.1f} st"
    else:
        delta = _format(medians[-1] - medians[0], unit, sign=True, scale=medians[0])
    return f"{latest} · {delta} since week of {first}"


def _format(value, unit, sign=False, scale=None):
    """Whole numbers for large-valued metrics (Hz), one decimal otherwise."""
    decimals = 0 if abs(value if scale is None else scale) >= 100 else 2 if unit == "" else 1
    space = "" if unit in ("%", "¢", "") else " "
    return f"{value:{'+' if sign else ''}.{decimals}f}{space}{unit}"


class ProgressChart(QWidget):
    def __init__(self, key):
        super().__init__()
        self.key = key
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        self.title = QLabel()
        self.title.setStyleSheet("color: #ddd; font-size: 12px;")
        self.title.setWordWrap(True)
        self.plot = pg.PlotWidget(background="#1e1e1e", axisItems={"bottom": pg.DateAxisItem()})
        self.plot.setFixedHeight(160)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.getAxis("left").setLabel(SERIES[key][1])
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.band = pg.LinearRegionItem(orientation="horizontal", movable=False,
                                        brush=pg.mkBrush(TARGET_FILL), pen=pg.mkPen(TARGET_EDGE))
        self.band.setZValue(-10)
        self.baseline = pg.InfiniteLine(angle=0, pen=BASELINE_PEN, label="baseline",
                                        labelOpts={"color": "#bbb", "position": 0.9})
        self.dots = pg.ScatterPlotItem(size=7, pen=None, brush=pg.mkBrush(DOT_COLOR))
        self.median = self.plot.plot(pen=pg.mkPen(MEDIAN_COLOR, width=2), symbol="o", symbolSize=6,
                                     symbolBrush=MEDIAN_COLOR, symbolPen=None)
        for item in (self.band, self.baseline, self.dots):
            self.plot.addItem(item)
        layout.addWidget(self.title)
        layout.addWidget(self.plot)

    def show_data(self, times, values, baseline=None, target=None):
        self.title.setText(f"<b>{SERIES[self.key][0]}</b> — {change_text(self.key, times, values)}")
        self.dots.setData(times, values)
        starts, medians = weekly_medians(times, values)
        # Each week's median is drawn mid-week.
        self.median.setData(starts + WEEK / 2, medians)
        self.baseline.setVisible(baseline is not None)
        if baseline is not None:
            self.baseline.setValue(baseline)
        self.band.setVisible(target is not None)
        if target is not None:
            self.band.setRegion(target)
        span = [*values, *([baseline] if baseline is not None else []), *(target or [])]
        lo, hi = min(span), max(span)
        pad = max((hi - lo) * 0.15, 1e-3 * max(abs(hi), 1), 0.05 if SERIES[self.key][1] == "" else 0.5)
        self.plot.setYRange(lo - pad, hi + pad, padding=0)
        t0, t1 = min(times[0], starts[0]), max(times[-1], starts[-1] + WEEK)
        self.plot.setXRange(t0 - DAY / 2, t1 + DAY / 2, padding=0)


class ProgressDialog(QDialog):
    """Charts for every series that has data, with mode / imported / period filters."""

    def __init__(self, profile, entries_provider, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.entries_provider = entries_provider
        self.setWindowTitle("Progress")
        self.resize(900, 760)
        self.setStyleSheet("QDialog { background-color: #2b2b2b; } QLabel, QCheckBox { color: white; } "
                           "QComboBox { background: #1e1e1e; color: white; }")
        layout = QVBoxLayout(self)

        filters = QHBoxLayout()
        self.mode = QComboBox()
        for label, mode in MODES:
            self.mode.addItem(label, mode)
        self.period = QComboBox()
        for label, days in PERIODS:
            self.period.addItem(label, days)
        self.imported = QCheckBox("Include imported files")
        self.imported.setToolTip("Imported files may be someone else's voice, so they're left out by default.")
        for w in (self.mode, self.period):
            w.currentIndexChanged.connect(self.refresh)
            filters.addWidget(w)
        self.imported.toggled.connect(self.refresh)
        filters.addWidget(self.imported)
        filters.addStretch()
        layout.addLayout(filters)

        self.empty = QLabel("Nothing to chart yet. Record some takes over a few days, and they'll "
                            "show up here with a weekly median (yellow) against your baseline and targets.")
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color: #aaa;")
        layout.addWidget(self.empty)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        self.grid = ResponsiveGrid(CHART_MIN_WIDTH, max_columns=3)
        self.charts = {}
        for key in SERIES:
            chart = ProgressChart(key)
            self.grid.addWidget(chart)
            self.charts[key] = chart
        content_layout.addWidget(self.grid)
        content_layout.addStretch()                     # charts stay compact at the top
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setStyleSheet("border: none;")
        layout.addWidget(scroll)
        self.refresh()

    def _reference(self, key):
        """(baseline, target) on the chart's axis, either may be None."""
        if key == "vowel_delta_f":
            vb = self.profile.vowel_baseline
            return (vb.get("delta_f") if vb else None), self.profile.target("delta_f")
        profile_key = SERIES[key][4]
        if not profile_key:
            return None, None
        return self.profile.baseline_value(profile_key), self.profile.target(profile_key)

    def refresh(self, *_):
        entries = self.entries_provider()
        shown = 0
        for key, chart in self.charts.items():
            times, values = select(entries, key, mode=self.mode.currentData(),
                                   include_imported=self.imported.isChecked(),
                                   days=self.period.currentData())
            chart.setVisible(len(values) > 0)
            if len(values):
                chart.show_data(times, values, *self._reference(key))
                shown += 1
        self.grid.relayout()
        self.empty.setVisible(shown == 0)
