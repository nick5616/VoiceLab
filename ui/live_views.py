"""The other live views: the spectrogram with a spectrum slice, and the pitch x resonance plane with your
takes on it. They draw what the live session gives them, in the theme's colours. (The pitch graph is
ui/live_graph.py.)"""
from collections import deque

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QGraphicsEllipseItem, QGraphicsRectItem

from display import note_ticks
from engine import UPDATE_INTERVAL
from live_session import HISTORY_LEN, HISTORY_SECONDS
from ui import theme
from ui.live_graph import style_plot

HISTORY_TIMES = np.linspace(-HISTORY_SECONDS, 0, HISTORY_LEN)
SPECTRUM_RANGE_DB = 70            # dynamic range shown in the spectrogram
PEAK_DECAY_DB = 0.05              # per frame; lets the display re-adapt after loud sounds
FORMANT_SERIES = ("f1", "f2", "f3", "pitch")    # theme series colours for F1-F4

PLANE_TRAIL_SECONDS = 3
PLANE_TRAIL_GAP_SECONDS = 0.5     # unmeasurable for this long starts a fresh trail
PLANE_PITCH_RANGE = (75, 400)     # Hz; resonance isn't judged above ~230 Hz anyway (analysis.F1_RELIABLE_MAX_F0)
PLANE_RESONANCE_RANGE = (900, 1350)
# Rough guides from typical adult speaking F0 and vocal tract lengths
# (about 16-18 cm vs 14-15.8 cm). Real voices vary and overlap widely, so these
# are orientation aids, not targets.
REFERENCE_REGIONS = [
    ("typically masculine", (960, 1090), (85, 155), "masculine"),
    ("typically feminine", (1110, 1260), (165, 255), "feminine"),
]


def _target_band(orientation):
    """A fixed, hidden target band; orientation 'horizontal' spans a y range."""
    band = pg.LinearRegionItem(orientation=orientation, movable=False)
    band.setZValue(-10)
    band.hide()
    return band


def _style_band(band):
    t = theme.current()
    band.setBrush(pg.mkBrush(t.q("on_target", 45)))
    for line in band.lines:
        line.setPen(pg.mkPen(t.q("on_target", 200), width=1.5))


class SpectrumView(pg.GraphicsLayoutWidget):
    """Spectrogram with formant tracks, plus the current spectrum turned on its
    side so harmonics can be lined up against resonances (formant tuning)."""

    def __init__(self, freqs):
        super().__init__()
        self.freqs = freqs
        self.max_freq = float(freqs[-1])
        self.spec = np.full((HISTORY_LEN, len(freqs)), -200.0)
        self.formant_history = np.full((HISTORY_LEN, 4), np.nan)
        self.peak_db = -60.0

        self.spec_plot = self.addPlot(row=0, col=0)
        self.slice_plot = self.addPlot(row=0, col=1)
        self.ci.layout.setColumnStretchFactor(0, 4)
        self.ci.layout.setColumnStretchFactor(1, 1)
        for plot in (self.spec_plot, self.slice_plot):
            plot.setYRange(0, self.max_freq, padding=0)
        self.slice_plot.setYLink(self.spec_plot)
        self.slice_plot.hideAxis("left")
        self.spec_plot.setXRange(-HISTORY_SECONDS, 0, padding=0)

        # Image axes: x = time (rows of self.spec), y = frequency.
        # setRect needs the image dimensions, so set an initial image first.
        self.image = pg.ImageItem(self.spec)
        self.image.setColorMap(pg.colormap.get("inferno"))
        self.image.setRect(QRectF(-HISTORY_SECONDS, 0, HISTORY_SECONDS, self.max_freq))
        self.spec_plot.addItem(self.image)
        self.formant_dots = [self.spec_plot.plot(pen=None, symbol="o", symbolSize=3, symbolPen=None,
                                                 name=f"F{i + 1}") for i in range(4)]
        self.slice_curve = self.slice_plot.plot()
        self.harmonic_dots = pg.ScatterPlotItem(size=6, pen=None)
        self.slice_plot.addItem(self.harmonic_dots)
        self.formant_lines = []
        for i in range(4):
            line = pg.InfiniteLine(angle=0, label=f"F{i + 1}", labelOpts={"position": 0.9})
            line.hide()
            self.slice_plot.addItem(line)
            self.formant_lines.append(line)
        theme.changed.connect(self.restyle)
        self.restyle()

    def restyle(self, *_):
        t = theme.current()
        self.setBackground(t["surface"])
        style_plot(self.spec_plot, left_width=56)
        style_plot(self.slice_plot)
        self.spec_plot.getAxis("left").setLabel("Hz", color=t["text_muted"])
        self.slice_plot.getAxis("bottom").setLabel("dB", color=t["text_muted"])
        for i, dots in enumerate(self.formant_dots):
            dots.setSymbolBrush(t.series[FORMANT_SERIES[i]])
        self.slice_curve.setPen(pg.mkPen(t["text_secondary"], width=1))
        self.harmonic_dots.setBrush(pg.mkBrush(t["accent"]))
        for i, line in enumerate(self.formant_lines):
            color = t.series[FORMANT_SERIES[i]]
            line.setPen(pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine))
            line.label.setColor(QColor(color))

    def update_frame(self, frame):
        spectrum = frame["spectrum"]
        self.spec = np.roll(self.spec, -1, axis=0)
        self.spec[-1] = spectrum
        self.peak_db = max(self.peak_db - PEAK_DECAY_DB, float(spectrum.max()))
        levels = (self.peak_db - SPECTRUM_RANGE_DB, self.peak_db)
        # Formant estimates are only drawn where they can be trusted.
        formants = frame["formants"] if frame["resonance_reliable"] else np.full(4, np.nan)
        self.formant_history = np.roll(self.formant_history, -1, axis=0)
        self.formant_history[-1] = formants
        if not self.isVisible():        # keep the history, skip the drawing
            return
        self.image.setImage(self.spec, autoLevels=False, levels=levels)
        for i, dots in enumerate(self.formant_dots):
            track = self.formant_history[:, i]
            ok = ~np.isnan(track)
            dots.setData(HISTORY_TIMES[ok], track[ok])

        self.slice_curve.setData(spectrum, self.freqs)
        self.slice_plot.setXRange(*levels, padding=0)
        for line, value in zip(self.formant_lines, formants):
            line.setVisible(not np.isnan(value))
            if not np.isnan(value):
                line.setValue(value)

        f0 = frame["f0"]
        if np.isnan(f0):
            self.harmonic_dots.setData([], [])
            return
        harmonics = np.arange(1, int(self.max_freq // f0) + 1) * f0
        bins = np.searchsorted(self.freqs, harmonics).clip(0, len(self.freqs) - 1)
        self.harmonic_dots.setData(spectrum[bins], harmonics)


class PitchResonancePlane(pg.PlotWidget):
    """Where your voice sits in pitch x resonance space, live and per take."""

    def __init__(self):
        super().__init__()
        lo, hi = PLANE_PITCH_RANGE
        self.setXRange(*PLANE_RESONANCE_RANGE, padding=0)
        self.setYRange(np.log2(lo), np.log2(hi), padding=0)
        self.getAxis("left").setTicks(note_ticks(lo, hi))
        self.regions = []
        for name, (x0, x1), (y0, y1), zone in REFERENCE_REGIONS:
            region = QGraphicsEllipseItem(x0, np.log2(y0), x1 - x0, np.log2(y1) - np.log2(y0))
            region.setPen(pg.mkPen(None))
            self.addItem(region)
            label = pg.TextItem(name, anchor=(0.5, 1))
            label.setPos((x0 + x1) / 2, np.log2(y1))
            self.addItem(label)
            self.regions.append((zone, region, label))

        trail_len = int(PLANE_TRAIL_SECONDS / UPDATE_INTERVAL)
        self.trail = deque(maxlen=trail_len)
        self.missing_frames = 0
        self.trail_curve = self.plot()
        self.current = self.plot(pen=None, symbol="o", symbolSize=14)
        self.takes = pg.ScatterPlotItem(size=10)
        self.addItem(self.takes)
        self.take_points = {}    # take number -> (x, y, label)

        # Target: a box when both pitch and resonance targets exist, else a band.
        self.target_box = QGraphicsRectItem()
        self.target_box.setZValue(-5)
        self.target_box.hide()
        self.addItem(self.target_box)
        self.pitch_band = _target_band("horizontal")
        self.resonance_band = _target_band("vertical")
        self.addItem(self.pitch_band)
        self.addItem(self.resonance_band)
        self.baseline_marker = pg.ScatterPlotItem(size=16, symbol="star")
        self.addItem(self.baseline_marker)
        self.baseline_label = pg.TextItem("baseline", anchor=(0, 0))
        self.baseline_label.hide()
        self.addItem(self.baseline_label)
        theme.changed.connect(self.restyle)
        self.restyle()

    def restyle(self, *_):
        t = theme.current()
        self.setBackground(t["surface"])
        style_plot(self.plotItem)
        self.showGrid(x=True, y=True, alpha=0.12)
        self.getAxis("bottom").setLabel("Resonance ΔF (Hz): darker ← → brighter", color=t["text_muted"])
        for zone, region, label in self.regions:
            fill = QColor(t.zone(zone)["edge"])
            fill.setAlpha(50)
            region.setBrush(QBrush(fill))
            label.setColor(QColor(t.zone(zone)["edge"]))
            label.setFont(theme.font("caption", weight=600))
        ink = t.series["pitch"]
        trail = QColor(ink)
        trail.setAlpha(110)
        self.trail_curve.setPen(pg.mkPen(trail, width=2))
        self.current.setSymbolBrush(t["accent"])
        self.current.setSymbolPen(pg.mkPen(t["surface"], width=2))
        self.takes.setPen(pg.mkPen(t["surface"], width=1))
        self.takes.setBrush(pg.mkBrush(t["text_secondary"]))
        self.target_box.setBrush(pg.mkBrush(t.q("on_target", 45)))
        self.target_box.setPen(pg.mkPen(t.q("on_target", 200), width=1.5))
        _style_band(self.pitch_band)
        _style_band(self.resonance_band)
        self.baseline_marker.setPen(pg.mkPen(t["surface"]))
        self.baseline_marker.setBrush(pg.mkBrush(t["text"]))
        self.baseline_label.setColor(QColor(t["text_secondary"]))
        for _, _, label in self.take_points.values():
            label.setColor(QColor(t["text_muted"]))

    def set_reference(self, baseline, pitch_target, resonance_target):
        """baseline: (f0, delta_f) or None; targets: [low, high] or None."""
        if baseline and all(baseline):
            f0, df = baseline
            self.baseline_marker.setData([df], [np.log2(f0)])
            self.baseline_label.setPos(df, np.log2(f0))
            self.baseline_label.show()
        else:
            self.baseline_marker.setData([], [])
            self.baseline_label.hide()

        both = bool(pitch_target and resonance_target)
        self.target_box.setVisible(both)
        self.pitch_band.setVisible(bool(pitch_target) and not both)
        self.resonance_band.setVisible(bool(resonance_target) and not both)
        if both:
            y0, y1 = np.log2(pitch_target)
            x0, x1 = resonance_target
            self.target_box.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))
        elif pitch_target:
            self.pitch_band.setRegion(np.log2(pitch_target))
        elif resonance_target:
            self.resonance_band.setRegion(resonance_target)

    def update_point(self, f0, delta_f):
        """f0 / delta_f are smoothed values, or None (or NaN) when not measurable."""
        f0 = np.nan if f0 is None else f0
        delta_f = np.nan if delta_f is None else delta_f
        if np.isnan(f0) or np.isnan(delta_f):
            self.current.setData([], [])
            self.missing_frames += 1
            if self.missing_frames * UPDATE_INTERVAL >= PLANE_TRAIL_GAP_SECONDS:
                self.trail.clear()
                self.trail_curve.setData([], [])
            return
        self.missing_frames = 0
        point = (delta_f, np.log2(f0))
        self.trail.append(point)
        xs, ys = zip(*self.trail)
        self.trail_curve.setData(xs, ys)
        self.current.setData([point[0]], [point[1]])

    def add_take(self, number, f0, delta_f):
        if number in self.take_points:
            self.remove_take(number)
        label = pg.TextItem(f"#{number}", color=theme.current()["text_muted"], anchor=(0, 1))
        label.setPos(delta_f, np.log2(f0))
        self.addItem(label)
        self.take_points[number] = (delta_f, np.log2(f0), label)
        self._draw_takes()

    def remove_take(self, number):
        point = self.take_points.pop(number, None)
        if point:
            self.removeItem(point[2])
            self._draw_takes()

    def clear_takes(self):
        for number in list(self.take_points):
            self.remove_take(number)

    def _draw_takes(self):
        points = list(self.take_points.values())
        self.takes.setData([p[0] for p in points], [p[1] for p in points])
