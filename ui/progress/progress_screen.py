"""Progress: one chart per report dimension over the weeks (weekly medians with how many takes each
rests on, the zone bands behind, your target and baseline), with task, poor-take and time filters; and
ProgressMini, the dashboard's sparklines. progress.report_view() and progress.mini() decide what shows."""
import math

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QGridLayout, QScrollArea, QVBoxLayout, QWidget

import progress
from ui import theme
from ui.components import (Caption, Card, Chip, Collapsible, EmptyState, FlowGrid, Headline, Label, SegmentedControl,
                           Select, Sparkline, Toggle, WrapRow)
from ui.takes import listen
from ui.takes.take_card import clear_layout

CHART_HEIGHT = 200              # px
CHART_COLUMN = 480              # px: the narrowest chart column
ZONE_ALPHA = {"dark": 70, "light": 110}     # the bands are a background, as on the take graph
PERIOD_KEYS = [("all", "All time", None), ("90", "90 days", 90), ("30", "30 days", 30)]
PITCH_TICKS = (80, 100, 125, 145, 180, 220, 300, 400)   # Hz, as the take graph's speak axis
EMPTY_TEXT = "Nothing to chart yet"
EMPTY_DETAIL = ("Record some takes over a few days. Each week's median shows here on the same zones as "
                "your reports, with your target and baseline.")
MINI_EMPTY = "Record takes over a few days to see a trend here."


def _pen(color, width=1.5, style=Qt.PenStyle.SolidLine, alpha=None):
    c = QColor(color)
    if alpha is not None:
        c.setAlpha(alpha)
    return pg.mkPen(c, width=width, style=style)


class ProgressChart(Card):
    """One dimension over time: its takes (faint dots), the weekly medians (labelled n=), the zone bands,
    the target (dashed green) and the baseline (dashed line). The subtitle is progress.change_text."""

    def __init__(self, data, parent=None):
        super().__init__(parent=parent)
        self.data = data
        self.zone_chip = Chip("")
        self.add_action(self.zone_chip)
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.plot.setFixedHeight(CHART_HEIGHT)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.explanation = Caption("", tone="secondary")
        self.add(self.plot)
        self.add(self.explanation)
        self.counts = []
        theme.changed.connect(self._theme_changed)
        self.show_chart(data)

    def _theme_changed(self, *_):
        self.show_chart(self.data)

    def _y(self, values):
        values = np.asarray(values, dtype=float)
        return np.log2(values) if self.data.get("log") else values

    def show_chart(self, data):
        self.data = data
        t = theme.current()
        self.set_title(data["name"])
        self.set_subtitle(data["change"])
        self.explanation.setText(data.get("explanation") or "")
        self.explanation.setVisible(bool(data.get("explanation")))
        zone = data.get("latest_zone")
        self.zone_chip.set_text(theme.ZONE_SHORT.get(zone, "") if zone else "", kind=zone or "neutral")
        self.zone_chip.setVisible(bool(zone))
        self.zone_chip.setToolTip("Where the latest week's median sits")
        p = self.plot
        p.clear()
        p.setBackground(t["surface"])
        for name in ("left", "bottom"):
            axis = p.getAxis(name)
            axis.setPen(_pen(t["border"], 1))
            axis.setTextPen(_pen(t["text_muted"], 1))
            axis.setStyle(tickFont=QFont(theme.font("caption")))
        p.getAxis("left").setWidth(76)
        p.showGrid(x=True, y=False, alpha=0.1)
        lo, hi = data["y_range"]
        y0, y1 = (math.log2(lo), math.log2(hi)) if data.get("log") else (lo, hi)
        spec = data.get("zones")
        if spec:
            for z in spec["zones"]:
                a, b = max(z["lo"], lo), min(z["hi"], hi)
                if b <= a:
                    continue
                fill = QColor(t.zone(z["id"])["fill"])
                fill.setAlpha(ZONE_ALPHA[t.name])
                band = pg.LinearRegionItem(tuple(self._y([a, b])), orientation="horizontal", movable=False,
                                           brush=pg.mkBrush(fill), pen=pg.mkPen(None))
                band.setZValue(-20)
                p.addItem(band, ignoreBounds=True)
        if data.get("log"):
            p.getAxis("left").setTicks([[(math.log2(v), f"{v}") for v in PITCH_TICKS if lo <= v <= hi], []])
        elif data["key"] == progress.OVERALL_KEY and spec:
            p.getAxis("left").setTicks([[((z["lo"] + z["hi"]) / 2, theme.ZONE_SHORT[z["id"]])
                                         for z in spec["zones"]], []])
        else:
            p.getAxis("left").setTicks(None)
        if data.get("target"):
            band = pg.LinearRegionItem(tuple(self._y(data["target"])), orientation="horizontal", movable=False,
                                       brush=pg.mkBrush(QColor(0, 0, 0, 0)),
                                       pen=_pen(t["on_target"], 1.4, Qt.PenStyle.DashLine))
            p.addItem(band, ignoreBounds=True)
        if data.get("baseline") is not None:
            line = pg.InfiniteLine(pos=float(self._y([data["baseline"]])[0]), angle=0,
                                   pen=_pen(t["text_secondary"], 1.2, Qt.PenStyle.DashLine), label="baseline",
                                   labelOpts={"color": t["text_secondary"], "position": 0.06})
            p.addItem(line, ignoreBounds=True)
        p.addItem(pg.ScatterPlotItem(data["times"], self._y(data["values"]), size=6, pen=None,
                                     brush=pg.mkBrush(QColor(t["text_muted"]))))
        p.plot(data["median_times"], self._y(data["medians"]), pen=_pen(t["accent"], 2.4), symbol="o",
               symbolSize=8, symbolBrush=t["accent"], symbolPen=_pen(t["surface"], 1.5))
        self.counts = []
        for x, median, count in zip(data["median_times"], self._y(data["medians"]), data["counts"]):
            label = pg.TextItem(f"n={count}", color=t["text_secondary"], anchor=(0.5, 1.5))
            label.setFont(QFont(theme.font("caption")))
            label.setPos(x, median)
            p.addItem(label, ignoreBounds=True)
            self.counts.append(label)
        p.setYRange(y0, y1, padding=0.04)
        p.setXRange(*data["x_range"], padding=0)


class ProgressScreen(QWidget):
    """Every report dimension over the weeks, like with like: filters for the task, poor takes (left out
    by default) and the time range; the other series under "More"."""

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.task_choice = None         # None until a task is picked: then a 1-tuple (None = all tasks)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.M)
        outer.addWidget(Headline("Progress", level="title"))
        outer.addWidget(Caption("Weekly medians of your takes on the same zones as your reports. A trend is "
                                "only named once there are enough takes and it is bigger than your usual "
                                "take-to-take variation.", tone="secondary"))
        self.task = Select([(None, "All tasks")])
        self.task.setToolTip(progress.TASK_TOOLTIP)
        self.task.changed.connect(self._task_picked)
        self.period = SegmentedControl([(k, label) for k, label, _ in PERIOD_KEYS], current="all")
        self.period.changed.connect(self.refresh)
        self.poor = Toggle(False, "Include poor recordings")
        self.poor.setToolTip(progress.POOR_TOOLTIP)
        self.poor.toggled.connect(self.refresh)
        outer.addWidget(WrapRow([self.task, self.period, self.poor], spacing=theme.L))
        self.left_out = Caption("", tone="secondary")
        outer.addWidget(self.left_out)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(0, 0, theme.S, theme.L)
        self.body.setSpacing(theme.L)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        self.charts = {}
        self.more_charts = {}
        listen(self, core, {"progress_changed": self.refresh, "profile_changed": self.refresh,
                            "takes_loaded": self.refresh})
        self.refresh()

    def _task_picked(self, key):
        self.task_choice = (key,)
        self.refresh()

    def days(self):
        return next(d for k, _, d in PERIOD_KEYS if k == self.period.current())

    def view(self):
        return progress.report_view(self.core.progress_entries(), self.core.profile, task_choice=self.task_choice,
                                    include_poor=self.poor.isChecked(), days=self.days())

    def refresh(self, *_):
        shown = self.view()
        self.shown = shown
        self.task.set_options([(None, "All tasks")] + [(name, f"{name} ({n})") for name, n in shown["tasks"]],
                              shown["task"])
        self.left_out.setText(shown["left_out"] or "")
        self.left_out.setVisible(bool(shown["left_out"]))
        clear_layout(self.body)
        self.charts, self.more_charts = {}, {}
        if shown["empty"]:
            self.empty = EmptyState("ph.chart-line-up", EMPTY_TEXT, detail=EMPTY_DETAIL)
            self.body.addWidget(self.empty)
            self.body.addStretch(1)
            return
        grid = FlowGrid(min_column_width=CHART_COLUMN, spacing=theme.L, max_column_width=900)
        for key, data in shown["charts"]:
            if data is None:
                continue
            self.charts[key] = grid.add(ProgressChart(data))
        self.body.addWidget(grid)
        if shown["more"]:
            more = Collapsible(f"More series ({len(shown['more'])})", key="progress.more", role="label")
            more_grid = FlowGrid(min_column_width=CHART_COLUMN, spacing=theme.L, max_column_width=900)
            for key, data in shown["more"].items():
                self.more_charts[key] = more_grid.add(ProgressChart(data))
            more.add(more_grid)
            self.body.addWidget(more)
        self.body.addStretch(1)


class ProgressMini(Card):
    """The dashboard's progress: sparklines of pitch, resonance and the overall placement over the last
    weeks (progress.mini), each with its latest value and zone."""

    def __init__(self, core, parent=None):
        super().__init__("Progress", parent=parent)
        self.core = core
        self.list = QWidget()
        self.grid = QGridLayout(self.list)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(theme.M)
        self.grid.setVerticalSpacing(theme.S)
        self.add(self.list)
        self.sparks = {}
        listen(self, core, {"progress_changed": self.refresh, "profile_changed": self.refresh,
                            "takes_loaded": self.refresh})
        self.refresh()

    def refresh(self, *_):
        data = progress.mini(self.core.progress_entries(), self.core.profile)
        self.data = data
        clear_layout(self.grid)
        self.sparks = {}
        if not any(s["latest"] for s in data["series"]):
            self.set_subtitle("")
            self.grid.addWidget(Caption(MINI_EMPTY, tone="secondary"), 0, 0)
            return
        self.set_subtitle(f"{(data['task'] or 'All tasks').capitalize()} · weekly medians, last {data['weeks']} weeks")
        for row, s in enumerate(data["series"]):
            name = Label("Overall" if s["key"] == progress.OVERALL_KEY else s["name"], role="caption",
                         tone="secondary")
            name.setFixedWidth(80)
            spark = Sparkline(s["values"], height=32)
            if s["target"]:
                spark.setToolTip(f"Your target: {s['target'][0]:.0f}–{s['target'][1]:.0f}")
            latest = Label(s["latest"] or "—", role="numeric")
            if s["key"] == progress.OVERALL_KEY:
                latest = Chip(theme.ZONE_SHORT.get(s["zone"], "—") if s["zone"] else "—", kind=s["zone"] or "neutral",
                              dot=bool(s["zone"]))
            self.grid.addWidget(name, row, 0)
            self.grid.addWidget(spark, row, 1)
            self.grid.addWidget(latest, row, 2, Qt.AlignmentFlag.AlignRight)
            self.sparks[s["key"]] = spark
        self.grid.setColumnStretch(1, 1)
