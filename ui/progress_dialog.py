"""The Progress dialog: draws progress.view() — every series over weeks, with its filters."""
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

import progress
from progress import IMPORTED_TOOLTIP, MODES, PERIODS, POOR_TOOLTIP, SERIES, TASK_TOOLTIP
from ui.widgets import ResponsiveGrid

CHART_MIN_WIDTH = 400          # charts per row: as many as fit, up to three
DOT_COLOR = (52, 152, 219, 160)
MEDIAN_COLOR = "#f1c40f"
BASELINE_PEN = pg.mkPen((255, 255, 255, 170), width=1, style=Qt.PenStyle.DashLine)
TARGET_FILL, TARGET_EDGE = (46, 204, 113, 50), (46, 204, 113, 160)


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
        self.counts = []                # "n=3" labels over the weekly medians
        self.median = self.plot.plot(pen=pg.mkPen(MEDIAN_COLOR, width=2), symbol="o", symbolSize=6,
                                     symbolBrush=MEDIAN_COLOR, symbolPen=None)
        for item in (self.band, self.baseline, self.dots):
            self.plot.addItem(item)
        layout.addWidget(self.title)
        layout.addWidget(self.plot)

    def show_chart(self, chart):
        """Draw one progress.chart(): the takes, the weekly medians with their counts, baseline and target."""
        self.title.setText(f"<b>{chart['name']}</b> — {chart['change']}")
        self.dots.setData(chart["times"], chart["values"])
        # Each week's median is drawn mid-week, labelled with the takes behind it.
        self.median.setData(chart["median_times"], chart["medians"])
        for label in self.counts:
            self.plot.removeItem(label)
        self.counts = []
        for x, median, count in zip(chart["median_times"], chart["medians"], chart["counts"]):
            label = pg.TextItem(f"n={count}", color="#f1c40f", anchor=(0.5, 1.4))
            label.setPos(x, median)
            self.plot.addItem(label, ignoreBounds=True)
            self.counts.append(label)
        baseline, target = chart["baseline"], chart["target"]
        self.baseline.setVisible(baseline is not None)
        if baseline is not None:
            self.baseline.setValue(baseline)
        self.band.setVisible(target is not None)
        if target is not None:
            self.band.setRegion(target)
        self.plot.setYRange(*chart["y_range"], padding=0)
        self.plot.setXRange(*chart["x_range"], padding=0)


class ProgressDialog(QDialog):
    """Charts for every series that has data, with mode / imported / period / task filters."""

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
        self.task = QComboBox()
        self.task.setToolTip(TASK_TOOLTIP)
        self.task_choice = None         # the task picked here; None until you pick, then the most common
        self.task.activated.connect(self._task_picked)
        self.imported = QCheckBox("Include imported files")
        self.imported.setToolTip(IMPORTED_TOOLTIP)
        self.poor = QCheckBox("Include poor recordings")
        self.poor.setToolTip(POOR_TOOLTIP)
        for w in (self.mode, self.period):
            w.currentIndexChanged.connect(self.refresh)
            filters.addWidget(w)
        filters.addWidget(self.task)
        for box in (self.imported, self.poor):
            box.toggled.connect(self.refresh)
            filters.addWidget(box)
        filters.addStretch()
        layout.addLayout(filters)
        self.left_out = QLabel()
        self.left_out.setStyleSheet("color: #aaa; font-size: 11px;")
        self.left_out.hide()
        layout.addWidget(self.left_out)

        self.empty = QLabel(progress.EMPTY_TEXT)
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

    def _task_picked(self, index):
        self.task_choice = (self.task.itemData(index),)     # a tuple, so "All tasks" (None) is a choice
        self.refresh()

    def _fill_tasks(self, tasks, task):
        """List the tasks takes were made for, most common first, with `task` chosen."""
        self.task.blockSignals(True)
        self.task.clear()
        self.task.addItem("All tasks", None)
        for name, count in tasks:
            self.task.addItem(f"{name} ({count})", name)
        self.task.setCurrentIndex(max(0, self.task.findData(task)))
        self.task.blockSignals(False)

    def refresh(self, *_):
        shown = progress.view(self.entries_provider(), self.profile, mode=self.mode.currentData(),
                              days=self.period.currentData(), include_imported=self.imported.isChecked(),
                              include_poor=self.poor.isChecked(), task_choice=self.task_choice)
        self._fill_tasks(shown["tasks"], shown["task"])
        for key, chart in self.charts.items():
            data = shown["charts"][key]
            chart.setVisible(data is not None)
            if data is not None:
                chart.show_chart(data)
        self.grid.relayout()
        self.empty.setVisible(shown["empty"])
        self.left_out.setVisible(shown["left_out"] is not None)
        if shown["left_out"]:
            self.left_out.setText(shown["left_out"])
