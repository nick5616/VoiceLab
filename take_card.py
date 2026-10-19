"""Take cards and the take analysis graphs."""
from datetime import datetime

import numpy as np
import pyqtgraph as pg
import sounddevice as sd
from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame, QLineEdit
from PyQt6.QtGui import QFont
import qtawesome as qta

from analysis import IN_TUNE_CENTS, MIN_RELIABLE_SNR, SAMPLE_RATE
from exercise import midi_name
from shift_panel import ShiftPanel
from live_views import pitch_label
from widgets import ResponsiveGrid
from ui_common import BUTTON_STYLE, METER_MIN_WIDTH, MetricBar, OFF_TARGET_COLOR, ON_TARGET_COLOR, hiss_note

TAKE_BAR_KEYS = ["pitch", "delta_f", "h1h2", "cpp", "ring", "pitch_range", "level"]


def take_date(data):
    try:
        return datetime.fromisoformat(data["time"]).strftime("%a %b %d · %H:%M").replace(" 0", " ")
    except (KeyError, TypeError, ValueError):
        return ""


class AnalysisGraph(pg.GraphicsLayoutWidget):
    """Pitch track on top, F1-F3 below, with a hover readout."""

    def __init__(self, tracks):
        super().__init__()
        self.setBackground("#2b2b2b")
        self.tracks = tracks
        t = tracks["times"]

        self.ax_pitch = self.addPlot(row=0, col=0)
        self.ax_res = self.addPlot(row=1, col=0)
        self.ci.layout.setRowStretchFactor(0, 1)
        self.ci.layout.setRowStretchFactor(1, 2)
        self.ax_res.setXLink(self.ax_pitch)
        self.ax_pitch.setLabel("left", "Hz")
        self.ax_res.setLabel("left", "Formants Hz")
        for ax in (self.ax_pitch, self.ax_res):
            ax.showGrid(x=True, y=True, alpha=0.2)
            ax.setMouseEnabled(x=False, y=False)
            ax.hideButtons()

        self.ax_pitch.plot(t, tracks["f0"], pen=pg.mkPen("#3498db", width=2), connect="finite")
        # At least ~4 semitones of span so a steady note doesn't zoom into noise.
        lo, hi = np.nanmin(tracks["f0"]), np.nanmax(tracks["f0"])
        center = np.sqrt(lo * hi)
        self.ax_pitch.setYRange(min(lo, center / 1.12), max(hi, center * 1.12))
        self.ax_res.addLegend(offset=(-10, 10))
        for key, color in (("f3", "#e74c3c"), ("f2", "#f1c40f"), ("f1", "#2ecc71")):
            self.ax_res.plot(t, tracks[key], pen=pg.mkPen(color, width=1.5),
                             connect="finite", name=key.upper())

        self.cursors = [pg.InfiniteLine(angle=90, pen=pg.mkPen("w", width=1)) for _ in range(2)]
        self.ax_pitch.addItem(self.cursors[0])
        self.ax_res.addItem(self.cursors[1])
        self.text_p = pg.TextItem(color="w", anchor=(0, 0))
        self.text_r = pg.TextItem(color="w", anchor=(0, 0))
        self.ax_pitch.addItem(self.text_p, ignoreBounds=True)
        self.ax_res.addItem(self.text_r, ignoreBounds=True)
        self.scene().sigMouseMoved.connect(self.on_hover)

    def on_hover(self, pos):
        for ax in (self.ax_pitch, self.ax_res):
            if ax.sceneBoundingRect().contains(pos):
                x = ax.vb.mapSceneToView(pos).x()
                break
        else:
            return
        for cursor in self.cursors:
            cursor.setValue(x)
        idx = int(np.abs(self.tracks["times"] - x).argmin())
        p_val = self.tracks["f0"][idx]
        f1, f2, f3 = (self.tracks[k][idx] for k in ("f1", "f2", "f3"))

        self.text_p.setText(pitch_label(p_val) if not np.isnan(p_val) else "Unvoiced")
        self.text_p.setPos(self.ax_pitch.vb.viewRange()[0][0], self.ax_pitch.vb.viewRange()[1][1])
        if not np.isnan(f1):
            self.text_r.setText(f"F1:{f1:.0f} F2:{f2:.0f} F3:{f3:.0f}")
            self.text_r.setPos(self.ax_res.vb.viewRange()[0][0], self.ax_res.vb.viewRange()[1][1])


class NoteScoreChart(pg.PlotWidget):
    """Per-note tuning error of an exercise, with the in-tune zone shaded."""

    def __init__(self, notes):
        super().__init__(background="#2b2b2b")
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.setMenuEnabled(False)
        self.setFixedHeight(120)
        self.getAxis("left").setLabel("¢")
        zone = pg.LinearRegionItem((-IN_TUNE_CENTS, IN_TUNE_CENTS), orientation="horizontal",
                                   movable=False, brush=pg.mkBrush(46, 204, 113, 40), pen=pg.mkPen(None))
        self.addItem(zone)
        xs = np.arange(len(notes))
        heights = [np.clip(n["cents"], -100, 100) if n["cents"] is not None else 0 for n in notes]
        brushes = ["#555" if n["cents"] is None else
                   ON_TARGET_COLOR if abs(n["cents"]) <= IN_TUNE_CENTS else OFF_TARGET_COLOR
                   for n in notes]
        self.addItem(pg.BarGraphItem(x=xs, height=heights, width=0.7, brushes=brushes, pen=None))
        # Dots keep spot-on notes (zero-height bars) visible.
        sung = [i for i, n in enumerate(notes) if n["cents"] is not None]
        self.addItem(pg.ScatterPlotItem([xs[i] for i in sung], [heights[i] for i in sung], size=8,
                                        pen=None, brush=[brushes[i] for i in sung]))
        missed = [i for i, n in enumerate(notes) if n["cents"] is None]
        if missed:
            self.plot(missed, [0] * len(missed), pen=None, symbol="x", symbolBrush="#888", symbolPen="#888")
        self.getAxis("bottom").setTicks([[(i, midi_name(n["midi"])) for i, n in enumerate(notes)]])
        self.setYRange(-100, 100, padding=0)
        self.setXRange(-0.6, len(notes) - 0.4, padding=0)


def singing_summary(singing):
    """One line on held notes, vibrato, drift and tuning, or None."""
    if not singing or not singing["held_notes"]:
        return None
    parts = [f"Held notes: {singing['held_notes']}"]
    if singing["vibrato_notes"]:
        regularity = singing["vibrato_regularity"]
        parts.append(f"vibrato on {singing['vibrato_notes']}: {singing['vibrato_rate']:.1f} Hz "
                     f"±{singing['vibrato_extent']:.0f}¢"
                     + (f", cycle variation {regularity:.0%}" if regularity is not None else ""))
    else:
        parts.append("straight tone")
    if singing["drift"] is not None:
        parts.append(f"drift ±{singing['drift']:.0f}¢")
    parts.append(f"{singing['tuning_offset']:.0f}¢ from A440 notes")
    return " · ".join(parts)


class TakeCard(QFrame):
    """One take's report. `audio` returns its samples (loaded on demand for
    saved takes); `on_rename(card, title)` and `on_delete(card)` manage it."""

    def __init__(self, data, count, profile, on_set_baseline, on_set_target,
                 on_rename=None, on_delete=None, audio=None):
        super().__init__()
        self.data, self.profile, self.number = data, profile, count
        self.audio = audio or (lambda: data["audio"])
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        # Scoped to the card: a bare QFrame rule would also hit every QLabel inside.
        self.setStyleSheet("""
            TakeCard { background-color: #2b2b2b; border-radius: 10px; margin-bottom: 10px; }
            QLabel { color: #ffffff; background: transparent; }
            QLineEdit { color: white; background: transparent; border: 1px solid transparent;
                        border-radius: 4px; padding: 1px 3px; }
            QLineEdit:hover, QLineEdit:focus { border: 1px solid #555; background: #1e1e1e; }
        """)
        layout = QVBoxLayout()

        # Header
        header = QHBoxLayout()
        self.title = QLineEdit(data.get("title") or "")
        self.title.setPlaceholderText(f"Take #{count}")
        self.title.setToolTip("Click to name this take" + (" (renames its files)" if data.get("file") else ""))
        self.title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.title.setMinimumWidth(110)
        self.title.setCursorPosition(0)       # long names show their start, not their end
        self.on_rename = on_rename
        self.title.editingFinished.connect(self._title_edited)
        self.date = QLabel()
        self.date.setStyleSheet("color: #888; font-size: 11px;")
        self.update_saved_state()
        baseline_btn = QPushButton(qta.icon("fa5s.flag", color="white"), " Baseline")
        baseline_btn.setToolTip("Set as baseline")
        baseline_btn.clicked.connect(lambda: on_set_baseline(data))
        target_btn = QPushButton(qta.icon("fa5s.bullseye", color="white"), " Target")
        target_btn.setToolTip("Use as target")
        target_btn.clicked.connect(lambda: on_set_target(data))
        shift_btn = QPushButton(qta.icon("fa5s.sliders-h", color="white"), " Shift")
        shift_btn.setToolTip("Hear this take with the voice shifted")
        shift_btn.setCheckable(True)
        play_btn = QPushButton(qta.icon('fa5s.play', color="white"), " Play")
        play_btn.clicked.connect(self.play_audio)
        for btn in (baseline_btn, target_btn):
            btn.setStyleSheet(BUTTON_STYLE)
        shift_btn.setStyleSheet(f"QPushButton {{ {BUTTON_STYLE} }} "
                                "QPushButton:checked { background-color: #3498db; }")
        play_btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 5px 10px; border-radius: 5px;")
        delete_btn = QPushButton(qta.icon("fa5s.trash-alt", color="#ccc"), "")
        delete_btn.setToolTip("Delete this take")
        delete_btn.setStyleSheet(BUTTON_STYLE)
        delete_btn.clicked.connect(lambda: on_delete and on_delete(self))
        header.addWidget(self.title, 1)
        header.addWidget(self.date)
        header.addWidget(baseline_btn)
        header.addWidget(target_btn)
        header.addWidget(shift_btn)
        header.addWidget(play_btn)
        header.addWidget(delete_btn)
        layout.addLayout(header)

        if data.get("source"):
            source = QLabel(data["source"])
            source.setStyleSheet("color: #aaa; font-size: 11px;")
            layout.addWidget(source)
        snr = data.get("snr")
        if snr is not None and snr < MIN_RELIABLE_SNR:
            warning = QLabel(f"⚠ Background noise: voice-band SNR {snr:.0f} dB. Clarity, Fold Contact "
                             "and Ring may be unreliable; pitch and resonance hold up better.")
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {OFF_TARGET_COLOR}; font-size: 12px;")
            layout.addWidget(warning)
        note = hiss_note(snr, data.get("snr_full"))
        if note:
            info = QLabel(note)
            info.setWordWrap(True)
            info.setStyleSheet("color: #aaa; font-size: 11px;")
            layout.addWidget(info)
        clips = data.get("clip_events") or 0
        if clips:
            warning = QLabel(f"⚠ Clipping: the input overloaded {clips} time{'s' if clips > 1 else ''}. "
                             "Lower the mic gain; clipping inflates Ring and lowers Clarity.")
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {OFF_TARGET_COLOR}; font-size: 12px;")
            layout.addWidget(warning)

        self.shift_panel = ShiftPanel(data, on_set_target, audio=self.audio)
        self.shift_panel.hide()
        shift_btn.toggled.connect(self.shift_panel.setVisible)
        # While shifting, the card's Play button plays the shifted version.
        shift_btn.toggled.connect(lambda on: play_btn.setText(" Play shifted" if on else " Play"))
        layout.addWidget(self.shift_panel)

        # --- GRAPH ---
        graph = AnalysisGraph(data['tracks'])
        graph.setFixedHeight(200)
        layout.addWidget(graph)

        self.time_in_target = QLabel()
        self.time_in_target.setStyleSheet("color: #2ecc71; font-size: 12px;")
        layout.addWidget(self.time_in_target)

        # --- SINGING ---
        exercise = data.get("exercise")
        if exercise:
            text = f"{exercise['name']}: {exercise['notes_in_tune']}/{exercise['notes_total']} notes in tune"
            if exercise["mean_abs_cents"] is not None:
                text += (f" (±{IN_TUNE_CENTS}¢) · average error {exercise['mean_abs_cents']:.0f}¢ · "
                         f"{exercise['time_in_tune']:.0%} of note time in tune")
            label = QLabel(text)
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 12px;")
            layout.addWidget(label)
            layout.addWidget(NoteScoreChart(exercise["notes"]))
        summary = singing_summary(data.get("singing"))
        if summary:
            label = QLabel(summary)
            label.setWordWrap(True)
            label.setStyleSheet("color: #ddd; font-size: 12px;")
            layout.addWidget(label)

        # --- METRIC BARS ---
        self.bars = {key: MetricBar(key) for key in TAKE_BAR_KEYS}
        bar_grid = ResponsiveGrid(METER_MIN_WIDTH)
        for bar in self.bars.values():
            bar_grid.addWidget(bar)
        layout.addWidget(bar_grid)
        self.setLayout(layout)
        self.refresh_profile()

    def refresh_profile(self):
        # Share of voiced time inside each target: holding a voice across a whole
        # passage matters more than its median.
        tracks = self.data["tracks"]
        shares = [(name, self.profile.time_in_target(key, tracks[track]))
                  for name, key, track in (("pitch", "pitch", "f0"), ("resonance", "delta_f", "delta_f"))]
        parts = [f"{name} {share:.0%}" for name, share in shares if share is not None]
        self.time_in_target.setText(f"Time in target: {' · '.join(parts)}" if parts else "")
        self.time_in_target.setVisible(bool(parts))
        for key, bar in self.bars.items():
            bar.set_reference(self.profile)
            if key == "delta_f" and self.data["delta_f"] is None:
                bar.set_value(None, self.profile, note=self.data["resonance_note"])
            else:
                bar.set_value(self.data[key], self.profile)

    def _title_edited(self):
        title = self.title.text().strip()
        if self.on_rename and title != (self.data.get("title") or ""):
            self.on_rename(self, title)

    def update_saved_state(self):
        """Date, and where the take is stored."""
        self.date.setText(take_date(self.data) + ("" if self.data.get("file") else " · not saved"))
        self.date.setToolTip(self.data.get("file") or "Choose a takes folder to keep takes between sessions.")

    def play_audio(self):
        if self.shift_panel.isVisible():
            self.shift_panel.play_shifted()
        else:
            sd.play(self.audio(), SAMPLE_RATE)
