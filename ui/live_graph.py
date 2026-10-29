"""The live pitch graph: a scrolling pitch line over a FIXED axis. In speak mode the axis is 70-400 Hz
(log) with the report's zones shaded behind it, so a line's height means the same thing every time
(Voice Tools' lesson); in sing mode it is a note axis sized from your range that only moves when the
voice leaves it, and then glides. The note readout and the graph draw the same snapshot data
(snapshot["note"]["hz"]), and a pitch off the axis gets an arrow marker at the edge instead of vanishing.
"""
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsRectItem

import report
from display import note_name, note_ticks, zone_word
from live_session import HISTORY_LEN, HISTORY_SECONDS, pitch_axis
from ui import anim, theme

HISTORY_TIMES = np.linspace(-HISTORY_SECONDS, 0, HISTORY_LEN)
SPEAK_TICKS_HZ = (80, 100, 125, 150, 180, 220, 260, 300, 350)   # Hz; inside the 70-400 axis, so none is clipped
SPEAK_TICKS_SHORT_HZ = (100, 150, 220, 300)     # Hz; on a graph too short for nine labels
SHORT_GRAPH_PX = 220            # px: a graph shorter than this gets the short tick set
ZONE_ALPHA = {"dark": 78, "light": 120}    # zone washes behind the line (0-255): the line stays on top
AXIS_WIDTH = 92                 # px for "100 Hz  G2"
LINE_WIDTH = 2.6                # px
DOT_SIZE = 11                   # px: the readout's pitch at "now"
GLIDE_MS = 420                  # the sing axis glides to a new range this long
X_RANGE = (-HISTORY_SECONDS - 0.5, 0.6)  # s: a little room either side, so the "8" and "now" ticks and the dot are not cut
TIME_TICKS = [(-8, "8"), (-6, "6"), (-4, "4"), (-2, "2"), (0, "now")]   # the time axis: seconds ago, then "now"
TIME_AXIS_LABEL = "seconds ago"
BAND_ALPHA = {False: 12, True: 56}      # the target band's wash (0-255): brighter while the voice is in range
BAND_EDGE_WIDTH = {False: 1.5, True: 2.6}   # px


def hz_ticks(values, lo_hz, hi_hz):
    """Speak-mode ticks: Hz with the note name, inside the axis."""
    return [[(np.log2(v), f"{v} Hz  {note_name(v)}") for v in values if lo_hz <= v <= hi_hz], []]


def style_plot(plot_item, left_width=AXIS_WIDTH):
    """Theme a pyqtgraph PlotItem's axes: muted ink, caption font, no mouse interaction."""
    t = theme.current()
    plot_item.setMouseEnabled(x=False, y=False)
    plot_item.hideButtons()
    plot_item.setMenuEnabled(False)
    font = theme.font("caption")
    for name in ("left", "bottom", "right", "top"):
        axis = plot_item.getAxis(name)
        axis.setPen(pg.mkPen(t["border"]))
        axis.setTextPen(pg.mkPen(t["text_muted"]))
        axis.setTickFont(font)
        axis.setStyle(tickLength=-4)
    plot_item.getAxis("left").setWidth(left_width)


class LivePitchGraph(pg.PlotWidget):
    """The live pitch line on a fixed, zone-shaded axis. show(snapshot) draws a live snapshot; set_mode()
    switches axis; set_reference() draws the target band and baseline; freeze()/set_playhead() show a
    replay over the history as it was."""

    def __init__(self, mode="speak", voice_range=None, compact=False, parent=None):
        super().__init__(parent=parent)
        self.mode = None
        self.compact = compact
        self.voice_range = voice_range      # (MIDI lo, hi) for the sing axis, or None
        self.axis_hz = None                 # (lo, hi) the axis is heading to
        self.shown_hz = None                # (lo, hi) as drawn (gliding)
        self.history = [np.nan] * HISTORY_LEN
        self.frozen = False
        self.off_scale = None               # "above" / "below" when the readout's pitch is off the axis
        self.readout_hz = None
        self.zone_items = []
        self.setXRange(*X_RANGE, padding=0)
        self.plotItem.getAxis("bottom").setTicks([TIME_TICKS, []])
        self.plotItem.getAxis("bottom").setLabel(TIME_AXIS_LABEL)
        self.in_range = False               # the voice is inside its target right now (the band glows)
        self.short_ticks = None             # the speak axis uses the short tick set (a short graph)
        self.plotItem.showGrid(x=False, y=False)
        self.target_band = pg.LinearRegionItem(orientation="horizontal", movable=False)
        self.target_band.setZValue(-5)
        self.target_band.hide()
        self.addItem(self.target_band)
        self.target_label = pg.TextItem("target", anchor=(1, 1))
        self.target_label.hide()
        self.addItem(self.target_label, ignoreBounds=True)
        self.baseline_line = pg.InfiniteLine(angle=0, label="baseline", labelOpts={"position": 0.97})
        self.baseline_line.hide()
        self.addItem(self.baseline_line)
        self.curve = self.plot(connect="finite")
        self.curve.setZValue(10)
        self.dot = pg.ScatterPlotItem(size=DOT_SIZE)
        self.dot.setZValue(11)
        self.addItem(self.dot)
        self.marker = pg.TextItem("", anchor=(1, 0))
        self.marker.setZValue(12)
        self.marker.hide()
        self.addItem(self.marker, ignoreBounds=True)
        self.playhead = pg.InfiniteLine(angle=90)
        self.playhead.setZValue(13)
        self.playhead.hide()
        self.addItem(self.playhead)
        self.reference = (None, None)
        theme.changed.connect(self.restyle)
        self.set_mode(mode)
        self.restyle()

    # --- axis ---

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "mode", None) == "speak" and self.shown_hz and (self.height() < SHORT_GRAPH_PX) != self.short_ticks:
            self._set_axis(self.shown_hz)

    def y_range(self):
        """(low Hz, high Hz) as drawn now."""
        return self.shown_hz

    def set_mode(self, mode, voice_range=None):
        """Speak: the fixed 70-400 Hz axis with zones; sing: a note axis from the voice range."""
        if voice_range is not None:
            self.voice_range = voice_range
        self.mode = mode
        self.axis_hz = pitch_axis(mode, None, (), self.voice_range)
        self._set_axis(self.axis_hz)
        self._draw_zones()
        self._draw_reference()

    def _set_axis(self, hz):
        self.shown_hz = tuple(hz)
        lo, hi = np.log2(hz[0]), np.log2(hz[1])
        self.setYRange(lo, hi, padding=0)
        left = self.getAxis("left")
        if self.mode == "speak":
            short = self.height() < SHORT_GRAPH_PX
            self.short_ticks = short
            left.setTicks(hz_ticks(SPEAK_TICKS_SHORT_HZ if short else SPEAK_TICKS_HZ, *hz))
        else:
            left.setTicks(note_ticks(*hz))
        self._place_zone_labels()

    def _glide_axis(self, target):
        """Move the sing axis to `target` (Hz) smoothly."""
        start = self.shown_hz
        self.axis_hz = target

        def step(f):
            lo = np.exp(np.log(start[0]) + f * (np.log(target[0]) - np.log(start[0])))
            hi = np.exp(np.log(start[1]) + f * (np.log(target[1]) - np.log(start[1])))
            self._set_axis((lo, hi))
        anim.tween(step, 0.0, 1.0, GLIDE_MS, owner=self, name="axis")

    # --- zones, target and baseline ---

    def _draw_zones(self):
        for item, label in self.zone_items:
            self.removeItem(item)
            self.removeItem(label)
        self.zone_items = []
        spec = report.live_zones(self.mode)
        if not spec:
            return
        lo_hz, hi_hz = self.axis_hz
        for z in spec["zones"]:
            lo = lo_hz if z is spec["zones"][0] else max(lo_hz, z["lo"])
            hi = hi_hz if z is spec["zones"][-1] else min(hi_hz, z["hi"])
            if hi <= lo:
                continue
            rect = QGraphicsRectItem(QRectF(X_RANGE[0], np.log2(lo), X_RANGE[1] - X_RANGE[0], np.log2(hi) - np.log2(lo)))
            rect.setPen(pg.mkPen(None))
            rect.setZValue(-20)
            rect.zone = z
            self.addItem(rect, ignoreBounds=True)
            label = pg.TextItem(zone_word(z["id"], short=True) or z["label"], anchor=(0, 0.5))
            label.setZValue(-10)
            label.zone = z
            label.span = (lo, hi)
            self.addItem(label, ignoreBounds=True)
            self.zone_items.append((rect, label))
        self._color_zones()
        self._place_zone_labels()

    def zone_labels(self):
        """The zone labels drawn, low to high (for tests)."""
        return [label.toPlainText() for _, label in self.zone_items]

    def _place_zone_labels(self):
        for _, label in self.zone_items:
            lo, hi = label.span
            label.setPos(X_RANGE[0] + 0.12, (np.log2(lo) + np.log2(hi)) / 2)

    def _color_zones(self):
        t = theme.current()
        for rect, label in self.zone_items:
            colors = t.zone(rect.zone["id"])
            fill = QColor(colors["fill"])
            fill.setAlpha(ZONE_ALPHA[t.name])
            rect.setBrush(fill)
            label.setColor(QColor(colors["text"] if not t.dark else colors["edge"]))
            label.setFont(theme.font("caption", size=11 if self.compact else 12, weight=600))

    def set_reference(self, baseline_hz, target_hz):
        """The baseline pitch and the target pitch range (Hz); None hides them."""
        self.reference = (baseline_hz, target_hz)
        self._draw_reference()

    def _draw_reference(self):
        baseline_hz, target_hz = self.reference
        self.baseline_line.setVisible(bool(baseline_hz))
        if baseline_hz:
            self.baseline_line.setValue(np.log2(baseline_hz))
        self.target_band.setVisible(bool(target_hz))
        self.target_label.setVisible(bool(target_hz))
        if target_hz:
            self.target_band.setRegion([np.log2(v) for v in target_hz])
            self.target_label.setPos(X_RANGE[1] - 0.05, np.log2(max(target_hz)))

    # --- drawing a snapshot ---

    def show_snapshot(self, snapshot):
        """Draw a live snapshot: the pitch history and the readout's pitch."""
        if self.frozen:
            return
        self.history = list(snapshot["pitch_history"])
        hz = np.array(self.history, dtype=float)
        if self.mode == "sing":
            target = pitch_axis("sing", self.axis_hz, self.history, self.voice_range)
            if target != self.axis_hz:
                self._glide_axis(target)
        self.curve.setData(HISTORY_TIMES[-len(hz):], np.log2(hz))
        self.show_readout(snapshot["note"].get("hz"))

    def show_readout(self, hz):
        """The readout's pitch as a dot at "now", or an arrow at the edge when it is off the axis."""
        self.readout_hz = hz
        if hz is None:
            self.dot.setData([], [])
            self.marker.hide()
            self.off_scale = None
            return
        lo, hi = self.axis_hz
        if lo <= hz <= hi:
            self.off_scale = None
            self.marker.hide()
            self.dot.setData([0.0], [np.log2(hz)])
            return
        self.off_scale = "above" if hz > hi else "below"
        self.dot.setData([], [])
        self.marker.setText(("▲ " if hz > hi else "▼ ") + f"{hz:.0f} Hz")
        self.marker.setAnchor((1, 0) if hz > hi else (1, 1))
        self.marker.setPos(-0.05, np.log2(hi if hz > hi else lo))
        self.marker.show()

    def set_in_range(self, on):
        """Make the target band glow while the voice is inside the target."""
        on = bool(on)
        if on != self.in_range:
            self.in_range = on
            self._style_band()

    def in_view(self, hz):
        """True if `hz` sits inside the axis as drawn."""
        return self.shown_hz[0] <= hz <= self.shown_hz[1]

    # --- replay ---

    def freeze(self, on):
        """Hold the line still (while the last seconds are replayed); off resumes following the voice."""
        self.frozen = bool(on)
        if not on:
            self.playhead.hide()

    def set_playhead(self, seconds_ago):
        """A vertical line `seconds_ago` before the frozen "now"; None hides it."""
        if seconds_ago is None or seconds_ago > HISTORY_SECONDS:
            self.playhead.hide()
            return
        self.playhead.setValue(-seconds_ago)
        self.playhead.show()

    # --- theme ---

    def _style_band(self):
        green = QColor(theme.current()["on_target"])
        fill = QColor(green)
        fill.setAlpha(BAND_ALPHA[self.in_range])
        edge = QColor(green)
        edge.setAlpha(255 if self.in_range else 200)
        self.target_band.setBrush(pg.mkBrush(fill))
        for line in self.target_band.lines:
            line.setPen(pg.mkPen(edge, width=BAND_EDGE_WIDTH[self.in_range]))

    def restyle(self, *_):
        t = theme.current()
        self.setBackground(t["surface"])
        style_plot(self.plotItem)
        self.getAxis("bottom").setStyle(showValues=True)
        self.curve.setPen(pg.mkPen(t.series["pitch"], width=LINE_WIDTH))
        self.dot.setBrush(pg.mkBrush(t["accent"]))
        self.dot.setPen(pg.mkPen(t["surface"], width=2))
        self._style_band()
        self.baseline_line.setPen(pg.mkPen(t["text_secondary"], width=1.2, style=Qt.PenStyle.DashLine))
        self.baseline_line.label.setColor(QColor(t["text_secondary"]))
        self.baseline_line.label.setFont(theme.font("caption"))
        self.playhead.setPen(pg.mkPen(t["accent"], width=2))
        self.target_label.setColor(QColor(t["on_target"]))
        self.target_label.setFont(theme.font("caption", weight=600))
        self.marker.setColor(QColor(t["warning"]))
        self.marker.setFont(theme.font("label"))
        self._color_zones()
