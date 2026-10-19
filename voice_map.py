"""Voice map: a pitch x level grid accumulated from live frames and coloured by
a voice-quality metric, in the spirit of FonaDyn (Ternström & Pabon). It shows
your range and where in it your voice is clearest, brightest or lightest."""
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from analysis import NOTE_NAMES, hz_to_midi

MIDI_LO, MIDI_HI = 36, 96            # C2-C7
LEVEL_LO, LEVEL_HI, LEVEL_STEP = -60, 0, 2   # dBFS
MIN_FRAMES = 3                       # frames before a cell is shown
REDRAW_EVERY = 10                    # live frames between redraws
# key: (label, colour range, unit)
MAP_METRICS = {
    "cpp": ("Clarity (CPP)", (8, 30), " dB"),
    "ring": ("Ring (2–4 kHz)", (-30, 0), " dB"),
    "h1h2": ("Fold Contact (H1*–H2*)", (-5, 15), " dB"),
    "cq": ("Contact quotient (EGG)", (0.2, 0.7), ""),
}
EGG_METRICS = {"cq"}                 # only offered once an EGG has filled some cells
BUTTON_STYLE = "background-color: #3a3a3a; color: white; padding: 5px 10px; border-radius: 5px;"


class VoiceMapData:
    def __init__(self):
        shape = (MIDI_HI - MIDI_LO + 1, (LEVEL_HI - LEVEL_LO) // LEVEL_STEP)
        self.counts = np.zeros(shape)
        self.sums = {k: np.zeros(shape) for k in MAP_METRICS}
        self.metric_counts = {k: np.zeros(shape) for k in MAP_METRICS}

    def add(self, frame):
        f0, level = frame["f0"], frame["level"]
        if np.isnan(f0) or not LEVEL_LO <= level < LEVEL_HI:
            return False
        col = int(round(float(hz_to_midi(f0)))) - MIDI_LO
        if not 0 <= col < self.counts.shape[0]:
            return False
        row = int((level - LEVEL_LO) // LEVEL_STEP)
        self.counts[col, row] += 1
        for key in MAP_METRICS:
            value = frame.get(key, np.nan)
            if not np.isnan(value):
                self.sums[key][col, row] += value
                self.metric_counts[key][col, row] += 1
        return True

    def mean(self, key):
        n = self.metric_counts[key]
        out = np.full(n.shape, np.nan)
        shown = self.counts >= MIN_FRAMES
        out[shown & (n > 0)] = self.sums[key][shown & (n > 0)] / n[shown & (n > 0)]
        return out

    def has_data(self, key):
        return bool(self.metric_counts[key].sum() > 0)

    def range_midi(self):
        cols = np.flatnonzero((self.counts >= MIN_FRAMES).any(axis=1))
        return (cols[0] + MIDI_LO, cols[-1] + MIDI_LO) if len(cols) else None

    def save(self, path):
        np.savez(path, counts=self.counts,
                 **{f"sum_{k}": v for k, v in self.sums.items()},
                 **{f"n_{k}": v for k, v in self.metric_counts.items()})

    @classmethod
    def load(cls, path):
        data = cls()
        try:
            with np.load(path) as f:
                if f["counts"].shape == data.counts.shape:
                    data.counts = f["counts"]
                    for k in MAP_METRICS:       # maps saved before a metric existed lack it
                        if f"sum_{k}" in f:
                            data.sums[k] = f[f"sum_{k}"]
                            data.metric_counts[k] = f[f"n_{k}"]
        except (OSError, KeyError, ValueError):
            pass
        return data


def midi_name(midi):
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


class VoiceMapView(QWidget):
    def __init__(self, engine, path):
        super().__init__()
        self.path = path
        self.data = VoiceMapData.load(path) if path.exists() else VoiceMapData()
        self.pending = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        self.metric = QComboBox()
        self.update_metric_choices()
        self.metric.setStyleSheet("QComboBox { background: #1e1e1e; color: white; }")
        self.metric.currentIndexChanged.connect(self.redraw)
        self.range_label = QLabel()
        self.range_label.setWordWrap(True)
        self.range_label.setStyleSheet("color: white; font-size: 13px;")
        hint = QLabel("Builds up as you sing or speak, across sessions. Level is uncalibrated: "
                      "keep mic gain and distance the same.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaa; font-size: 11px;")
        clear_btn = QPushButton("Clear map")
        clear_btn.setStyleSheet(BUTTON_STYLE)
        clear_btn.clicked.connect(self.clear)
        self.scale_label = QLabel()
        self.scale_label.setWordWrap(True)
        self.scale_label.setStyleSheet("color: #ddd; font-size: 11px;")
        for w in (self.metric, self.scale_label, self.range_label, hint):
            left.addWidget(w)
        left.addStretch()
        left.addWidget(clear_btn)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(230)
        layout.addWidget(left_widget)

        self.plot = pg.PlotWidget(background="#1e1e1e")
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.getAxis("left").setLabel("Level dBFS")
        self.plot.getAxis("bottom").setTicks([[(m + 0.5, midi_name(m))
                                               for m in range(MIDI_LO, MIDI_HI + 1, 12)]])
        self.image = pg.ImageItem(np.zeros(self.data.counts.shape))
        self.image.setRect(QRectF(MIDI_LO, LEVEL_LO, MIDI_HI - MIDI_LO + 1, LEVEL_HI - LEVEL_LO))
        lut = pg.colormap.get("viridis").getLookupTable(nPts=256, alpha=True)
        lut[0] = (0, 0, 0, 0)   # index 0 = empty cell, drawn transparent
        self.image.setLookupTable(lut)
        self.plot.addItem(self.image)
        layout.addWidget(self.plot)
        self.redraw()
        engine.frame_ready.connect(self.on_frame)

    def metric_keys(self):
        """Metrics the picker offers: EGG ones only while the map holds EGG data."""
        return [k for k in MAP_METRICS if k not in EGG_METRICS or self.data.has_data(k)]

    def update_metric_choices(self):
        """Refill the picker if the offered metrics changed, keeping the selection."""
        keys = self.metric_keys()
        if keys == [self.metric.itemData(i) for i in range(self.metric.count())]:
            return
        current = self.metric.currentData()
        self.metric.blockSignals(True)
        self.metric.clear()
        for key in keys:
            self.metric.addItem(MAP_METRICS[key][0], key)
        self.metric.setCurrentIndex(max(0, self.metric.findData(current)))
        self.metric.blockSignals(False)
        if self.metric.currentData() != current and hasattr(self, "image"):
            self.redraw()

    def on_frame(self, frame):
        if self.data.add(frame):
            self.update_metric_choices()
            self.pending += 1
            if self.pending >= REDRAW_EVERY and self.isVisible():
                self.redraw()

    def redraw(self):
        self.pending = 0
        self.update_metric_choices()
        key = self.metric.currentData()
        lo, hi = MAP_METRICS[key][1]
        values = self.data.mean(key)
        # Map real values to lut indices 1-255 and empty cells below them to 0.
        step = (hi - lo) / 254
        self.image.setImage(np.where(np.isnan(values), lo - 10 * step, values),
                            levels=(lo - step, hi), autoLevels=False)
        self.scale_label.setText(f"Colour: purple {lo:g} → yellow {hi:g}{MAP_METRICS[key][2]}")

        rng = self.data.range_midi()
        if rng:
            lo_m, hi_m = rng
            self.range_label.setText(f"Range: {midi_name(lo_m)}–{midi_name(hi_m)} "
                                     f"({hi_m - lo_m} semitones)")
            self.plot.setXRange(max(MIDI_LO, lo_m - 4), min(MIDI_HI + 1, hi_m + 5), padding=0)
        else:
            self.range_label.setText("Nothing mapped yet.")
            self.plot.setXRange(MIDI_LO + 6, MIDI_LO + 42, padding=0)
        self.plot.setYRange(LEVEL_LO, LEVEL_HI, padding=0)

    def showEvent(self, event):
        self.redraw()
        super().showEvent(event)

    def clear(self):
        self.data = VoiceMapData()
        self.save()
        self.redraw()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data.save(self.path)
