"""Live visualizations: pitch trace, spectrogram + spectrum slice, and the
pitch x resonance plane."""
from collections import deque

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QGraphicsEllipseItem

from analysis import NOTE_NAMES, hz_to_note
from engine import UPDATE_INTERVAL

HISTORY_SECONDS = 8
HISTORY_LEN = int(HISTORY_SECONDS / UPDATE_INTERVAL)
HISTORY_TIMES = np.linspace(-HISTORY_SECONDS, 0, HISTORY_LEN)
MIN_PITCH, MAX_PITCH = 60, 1200
BACKGROUND = "#1e1e1e"

FORMANT_COLORS = ("#2ecc71", "#f1c40f", "#e74c3c", "#ecf0f1")  # F1-F4
SPECTRUM_RANGE_DB = 70            # dynamic range shown in the spectrogram
PEAK_DECAY_DB = 0.05              # per frame; lets the display re-adapt after loud sounds

PLANE_TRAIL_SECONDS = 3
PLANE_TRAIL_GAP_SECONDS = 0.5     # unmeasurable for this long starts a fresh trail
PLANE_PITCH_RANGE = (75, 400)     # Hz; resonance isn't measured above ~350 Hz anyway
PLANE_RESONANCE_RANGE = (900, 1350)
# Rough guides from typical adult speaking F0 and vocal tract lengths
# (about 16-18 cm vs 14-15.8 cm). Real voices vary and overlap widely, so these
# are orientation aids, not targets.
REFERENCE_REGIONS = [
    ("typical male-read", (960, 1090), (85, 155), "#3498db"),
    ("typical female-read", (1110, 1260), (165, 255), "#e74c3c"),
]


def pitch_label(f0):
    note, cents = hz_to_note(f0)
    return f"{note} {cents:+d}¢ · {f0:.0f} Hz"


def note_ticks(lo_hz, hi_hz):
    """Pitch-axis ticks (in log2 Hz) labelled with note names, thinned to fit."""
    lo_midi = int(np.floor(69 + 12 * np.log2(lo_hz / 440)))
    hi_midi = int(np.ceil(69 + 12 * np.log2(hi_hz / 440)))
    span = hi_midi - lo_midi
    step = 1 if span <= 12 else 2 if span <= 24 else 5
    major, minor = [], []
    for midi in range(lo_midi, hi_midi + 1):
        y = np.log2(440 * 2 ** ((midi - 69) / 12))
        if midi % step == 0:
            major.append((y, f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}  {2 ** y:.0f}"))
        else:
            minor.append((y, ""))
    return [major, minor]


def _static_plot(plot):
    plot.setMouseEnabled(x=False, y=False)
    plot.hideButtons()
    plot.setMenuEnabled(False)


class PitchView(pg.PlotWidget):
    """Scrolling pitch trace on a musical (log) axis that follows your range."""

    def __init__(self):
        super().__init__(background=BACKGROUND)
        self.history = deque([np.nan] * HISTORY_LEN, maxlen=HISTORY_LEN)
        # Pitch is plotted as log2(Hz) so equal musical intervals are equal heights.
        self.showGrid(x=False, y=True, alpha=0.2)
        _static_plot(self)
        self.setXRange(-HISTORY_SECONDS, 0, padding=0)
        self.getAxis("left").setWidth(70)
        self.getAxis("bottom").setLabel("seconds")
        self.curve = self.plot(pen=pg.mkPen("#3498db", width=2), connect="finite")

    def update_frame(self, frame):
        self.history.append(frame["f0"])
        self.curve.setData(HISTORY_TIMES, np.log2(np.array(self.history)))
        self._follow_pitch()

    def _follow_pitch(self):
        """Keep the recent pitch range in view with at least an octave of span."""
        recent = np.array(self.history)
        recent = recent[~np.isnan(recent)]
        if len(recent) == 0:
            return
        lo, hi = np.percentile(recent, [5, 95])
        center = np.sqrt(lo * hi)
        span = max(hi / lo, 2.0) * 1.3
        lo, hi = max(MIN_PITCH, center / np.sqrt(span)), min(MAX_PITCH, center * np.sqrt(span))
        self.setYRange(np.log2(lo), np.log2(hi), padding=0)
        self.getAxis("left").setTicks(note_ticks(lo, hi))


class SpectrumView(pg.GraphicsLayoutWidget):
    """Spectrogram with formant tracks, plus the current spectrum turned on its
    side so harmonics can be lined up against resonances (formant tuning)."""

    def __init__(self, freqs):
        super().__init__()
        self.setBackground(BACKGROUND)
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
            _static_plot(plot)
            plot.setYRange(0, self.max_freq, padding=0)
        self.slice_plot.setYLink(self.spec_plot)
        self.slice_plot.hideAxis("left")
        self.slice_plot.getAxis("bottom").setLabel("dB")
        self.spec_plot.getAxis("left").setLabel("Hz")
        self.spec_plot.getAxis("bottom").setLabel("seconds")
        self.spec_plot.setXRange(-HISTORY_SECONDS, 0, padding=0)

        # Image axes: x = time (rows of self.spec), y = frequency.
        # setRect needs the image dimensions, so set an initial image first.
        self.image = pg.ImageItem(self.spec)
        self.image.setColorMap(pg.colormap.get("inferno"))
        self.image.setRect(QRectF(-HISTORY_SECONDS, 0, HISTORY_SECONDS, self.max_freq))
        self.spec_plot.addItem(self.image)
        self.formant_dots = [
            self.spec_plot.plot(pen=None, symbol="o", symbolSize=3, symbolPen=None,
                                symbolBrush=color, name=f"F{i + 1}")
            for i, color in enumerate(FORMANT_COLORS)
        ]

        self.slice_curve = self.slice_plot.plot(pen=pg.mkPen("#bbbbbb", width=1))
        self.harmonic_dots = pg.ScatterPlotItem(size=6, pen=None, brush="#3498db")
        self.slice_plot.addItem(self.harmonic_dots)
        self.formant_lines = []
        for i, color in enumerate(FORMANT_COLORS):
            line = pg.InfiniteLine(angle=0, pen=pg.mkPen(color, width=1, style=pg.QtCore.Qt.PenStyle.DashLine),
                                   label=f"F{i + 1}", labelOpts={"color": color, "position": 0.9})
            line.hide()
            self.slice_plot.addItem(line)
            self.formant_lines.append(line)

    def update_frame(self, frame):
        spectrum = frame["spectrum"]
        self.spec = np.roll(self.spec, -1, axis=0)
        self.spec[-1] = spectrum
        self.peak_db = max(self.peak_db - PEAK_DECAY_DB, float(spectrum.max()))
        levels = (self.peak_db - SPECTRUM_RANGE_DB, self.peak_db)
        self.image.setImage(self.spec, autoLevels=False, levels=levels)

        # Formant estimates are only drawn where they can be trusted.
        formants = frame["formants"] if frame["resonance_reliable"] else np.full(4, np.nan)
        self.formant_history = np.roll(self.formant_history, -1, axis=0)
        self.formant_history[-1] = formants
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
        super().__init__(background=BACKGROUND)
        _static_plot(self)
        self.showGrid(x=True, y=True, alpha=0.15)
        lo, hi = PLANE_PITCH_RANGE
        self.setXRange(*PLANE_RESONANCE_RANGE, padding=0)
        self.setYRange(np.log2(lo), np.log2(hi), padding=0)
        self.getAxis("left").setTicks(note_ticks(lo, hi))
        self.getAxis("left").setWidth(70)
        self.getAxis("bottom").setLabel("Resonance ΔF (Hz): darker ← → brighter")

        for name, (x0, x1), (y0, y1), color in REFERENCE_REGIONS:
            region = QGraphicsEllipseItem(x0, np.log2(y0), x1 - x0, np.log2(y1) - np.log2(y0))
            fill = QColor(color)
            fill.setAlpha(45)
            region.setBrush(QBrush(fill))
            region.setPen(pg.mkPen(None))
            self.addItem(region)
            label = pg.TextItem(name, color=color, anchor=(0.5, 1))
            label.setPos((x0 + x1) / 2, np.log2(y1))
            self.addItem(label)

        trail_len = int(PLANE_TRAIL_SECONDS / UPDATE_INTERVAL)
        self.trail = deque(maxlen=trail_len)
        self.missing_frames = 0
        self.trail_curve = self.plot(pen=pg.mkPen((255, 255, 255, 90), width=2))
        self.current = self.plot(pen=None, symbol="o", symbolSize=14,
                                 symbolBrush="#f1c40f", symbolPen=pg.mkPen("w", width=1))
        self.takes = pg.ScatterPlotItem(size=10, pen=pg.mkPen("w"), brush="#9b59b6")
        self.addItem(self.takes)

    def update_point(self, f0, delta_f):
        """f0 / delta_f are smoothed values, or NaN when not measurable."""
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
        self.takes.addPoints([delta_f], [np.log2(f0)])
        label = pg.TextItem(f"#{number}", color="#ddd", anchor=(0, 1))
        label.setPos(delta_f, np.log2(f0))
        self.addItem(label)
