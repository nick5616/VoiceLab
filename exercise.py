"""Singing exercises: built-in patterns or a MIDI melody, a guide tone,
a scrolling piano roll, and per-note scoring of the take."""
import time
from collections import defaultdict

import numpy as np
import pyqtgraph as pg
import sounddevice as sd
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                             QPushButton, QSpinBox, QVBoxLayout, QWidget)

from analysis import (CLOSE_CENTS, IN_TUNE_CENTS, NOTE_NAMES, PERCEIVED_SECONDS, SAMPLE_RATE,
                      analyze_take, hz_to_midi, midi_to_hz, perceived_cents, score_exercise)
from engine import AnalysisWorker

# Semitone patterns from the start note: (name, intervals, seconds per note).
PATTERNS = [
    ("Five-note scale", [0, 2, 4, 5, 7, 5, 4, 2, 0], 0.6),
    ("Major arpeggio", [0, 4, 7, 12, 7, 4, 0], 0.7),
    ("Minor arpeggio", [0, 3, 7, 12, 7, 3, 0], 0.7),
    ("Octave scale", [0, 2, 4, 5, 7, 9, 11, 12, 11, 9, 7, 5, 4, 2, 0], 0.5),
    ("Long tone (vibrato & sustain)", [0], 5.0),
]
COUNT_IN_BEATS, BEAT_SECONDS = 4, 0.5
LEAD_IN = COUNT_IN_BEATS * BEAT_SECONDS
TAIL_SECONDS = 0.6                # keep recording briefly after the last note
ROLL_BEFORE, ROLL_AFTER = 3, 5    # piano roll window around "now" (s)
NOTE_RANGE = range(36, 85)        # C2-C6 start notes
IN_TUNE_COLOR, CLOSE_COLOR, OFF_COLOR = "#2ecc71", "#f1c40f", "#e67e22"   # within 25 / 50 cents / beyond
BAR_COLORS = {IN_TUNE_COLOR: (46, 204, 113, 200), CLOSE_COLOR: (241, 196, 15, 200), OFF_COLOR: (230, 126, 34, 200)}
DEFAULT_BAR_COLOR = (52, 152, 219, 200)
BUTTON_STYLE = "background-color: #3a3a3a; color: white; padding: 5px 10px; border-radius: 5px;"


def midi_name(midi):
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def pattern_targets(intervals, note_seconds, start_midi):
    """[(start_s, duration_s, midi)] after the count-in."""
    return [(LEAD_IN + i * note_seconds, note_seconds, start_midi + step)
            for i, step in enumerate(intervals)]


def load_midi_melody(path, transpose=0):
    """Monophonic melody from a MIDI file: the busiest non-drum channel, keeping
    the highest note when notes start together and cutting each note off where
    the next begins. Times are shifted to start after the count-in."""
    import mido
    active, by_channel, now = {}, defaultdict(list), 0.0
    for msg in mido.MidiFile(path):          # iterating gives times in seconds
        now += msg.time
        if msg.type not in ("note_on", "note_off"):
            continue
        key = (msg.channel, msg.note)
        if msg.type == "note_on" and msg.velocity > 0:
            active[key] = now
        elif key in active:
            start = active.pop(key)
            by_channel[msg.channel].append((start, now - start, msg.note))
    channels = [c for c in by_channel if c != 9]  # channel 10 is drums
    if not channels:
        raise ValueError("No melodic notes found in this MIDI file.")
    notes = sorted(by_channel[max(channels, key=lambda c: len(by_channel[c]))],
                   key=lambda n: (round(n[0], 2), -n[2]))
    melody = []
    for start, duration, note in notes:
        if melody and round(start, 2) == round(melody[-1][0], 2):
            continue                              # chord: keep the top note
        if melody and start < melody[-1][0] + melody[-1][1]:
            prev = melody[-1]
            melody[-1] = (prev[0], start - prev[0], prev[2])
        melody.append((start, duration, note))
    offset = LEAD_IN - melody[0][0]
    return [(s + offset, d, n + transpose) for s, d, n in melody if d > 0.05]


def guide_audio(targets, sample_rate=SAMPLE_RATE):
    """Count-in clicks plus a soft tone for each target note."""
    end = max(s + d for s, d, _ in targets) + TAIL_SECONDS
    out = np.zeros(int(end * sample_rate), dtype=np.float32)
    click_len = int(0.02 * sample_rate)
    click = np.sin(2 * np.pi * 1500 * np.arange(click_len) / sample_rate) * np.hanning(click_len)
    for beat in range(COUNT_IN_BEATS):
        i = int(beat * BEAT_SECONDS * sample_rate)
        out[i:i + click_len] += 0.3 * click
    for start, duration, midi in targets:
        n = int(duration * sample_rate)
        t = np.arange(n) / sample_rate
        f = float(midi_to_hz(midi))
        tone = sum(a * np.sin(2 * np.pi * k * f * t) for k, a in ((1, 1.0), (2, 0.4), (3, 0.15)))
        env = np.minimum(1, np.minimum(t / 0.03, (duration - t) / 0.06)).clip(0)
        i = int(start * sample_rate)
        out[i:i + n] += 0.15 * (tone * env)[:len(out) - i]
    return out


def tuning_color(cents):
    """Colour for a perceived error in cents: green within 25, yellow within 50, else orange; None if unknown."""
    if cents is None or np.isnan(cents):
        return None
    if abs(cents) <= IN_TUNE_CENTS:
        return IN_TUNE_COLOR
    return CLOSE_COLOR if abs(cents) <= CLOSE_CENTS else OFF_COLOR


def output_latency():
    """Seconds between sd.play and the guide being heard, from the output stream (0 if unknown)."""
    try:
        latency = sd.get_stream().latency
        return float(latency[1] if np.ndim(latency) else latency)
    except Exception:
        return 0.0


def analyze_exercise_take(audio, targets, name, latency=0.0):
    """analyze_take plus the exercise score. `latency` is the guide's output latency: the guide
    sounded that much after the recording started, so the singer is scored against the targets
    shifted by it (the reported timing is then reaction time, not device lag)."""
    result = analyze_take(audio)
    if "error" not in result:
        times, f0 = result["tracks"]["times"], result["tracks"]["f0"]
        heard = [(s + latency, d, m) for s, d, m in targets]
        result["exercise"] = score_exercise(times, f0, heard) | {"name": name, "targets": targets,
                                                                 "latency": latency}
    return result


class PianoRoll(pg.PlotWidget):
    """Target notes as bars scrolling past a playhead, with your pitch on top."""

    def __init__(self):
        super().__init__(background="#1e1e1e")
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.setMenuEnabled(False)
        self.showGrid(x=False, y=True, alpha=0.15)
        self.getAxis("left").setWidth(45)
        self.getAxis("bottom").setLabel("seconds")
        self.bars = self.plot(pen=pg.mkPen((150, 150, 150, 140), width=12), connect="pairs")
        self.current_bar = self.plot(pen=pg.mkPen(DEFAULT_BAR_COLOR, width=12))
        self.trace = self.plot(pen=pg.mkPen("#f1c40f", width=2), connect="finite")
        self.playhead = pg.InfiniteLine(angle=90, pen=pg.mkPen("w", width=1))
        self.addItem(self.playhead)
        self.targets = []
        self.trace_t, self.trace_m = [], []

    def set_targets(self, targets):
        self.targets = targets
        self.trace_t, self.trace_m = [], []
        xs, ys = [], []
        for start, duration, midi in targets:
            xs += [start, start + duration - 0.02]
            ys += [midi, midi]
        self.bars.setData(xs, ys)
        self.trace.setData([], [])
        self.current_bar.setData([], [])
        midis = [m for _, _, m in targets]
        lo, hi = min(midis) - 3, max(midis) + 3
        self.setYRange(lo, hi, padding=0)
        self.getAxis("left").setTicks([[(m, midi_name(m)) for m in range(lo, hi + 1)
                                        if m in midis or m % 12 == 0]])
        self.update_view(0.0, np.nan)

    def update_view(self, now, f0, color=None):
        """Move the roll to `now` with the latest f0; `color` (a tuning_color) tints the current bar."""
        if not np.isnan(f0):
            midi = float(hz_to_midi(f0))
            # Fold onto the target's octave so an octave-down singer stays on the bars.
            target = current_target(self.targets, now)
            if target is not None:
                midi -= 12 * np.round((midi - target) / 12)
            self.trace_t.append(now)
            self.trace_m.append(midi)
        else:
            self.trace_t.append(now)
            self.trace_m.append(np.nan)
        self.trace.setData(self.trace_t, self.trace_m)
        self.playhead.setValue(now)
        self.setXRange(now - ROLL_BEFORE, now + ROLL_AFTER, padding=0)
        self.current_bar.setPen(pg.mkPen(BAR_COLORS.get(color, DEFAULT_BAR_COLOR), width=12))
        for start, duration, midi in self.targets:
            if start <= now < start + duration:
                self.current_bar.setData([start, start + duration - 0.02], [midi, midi])
                break
        else:
            self.current_bar.setData([], [])


def current_target(targets, now):
    for start, duration, midi in targets:
        if start <= now < start + duration:
            return midi
    return None


class ExerciseView(QWidget):
    """Pick an exercise, sing it against the piano roll, get a scored take."""

    running_changed = pyqtSignal(bool)
    take_ready = pyqtSignal(dict)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.running = False
        self.targets, self.midi_targets, self.midi_name = [], None, None
        self.t0 = None
        self.latency = 0.0               # guide output latency, s
        self.recent = []                 # (time, cents from target) over the last PERCEIVED_SECONDS
        self.worker = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        self.cents_label = QLabel("—")
        self.cents_label.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        self.cents_label.setStyleSheet("color: white;")
        self.choice = QComboBox()
        for name, _, _ in PATTERNS:
            self.choice.addItem(name)
        self.choice.addItem("Load MIDI melody…")
        self.choice.activated.connect(self._choice_changed)
        self.start_note = QComboBox()
        for m in NOTE_RANGE:
            self.start_note.addItem(midi_name(m), m)
        self.start_note.setCurrentIndex(NOTE_RANGE.index(48))  # C3
        self.transpose = QSpinBox()
        self.transpose.setRange(-24, 24)
        self.transpose.setPrefix("Transpose ")
        self.transpose.hide()
        self.guide = QCheckBox("Guide tone")
        self.guide.setToolTip("Use headphones, or the mic will hear the guide instead of you.")
        self.guide.setChecked(True)
        self.start_btn = QPushButton("Start")
        self.start_btn.setStyleSheet(BUTTON_STYLE)
        self.start_btn.clicked.connect(self.toggle)
        self.status = QLabel("Sing along with the bars. Singing an octave down is fine.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #aaa; font-size: 11px;")
        style = "QComboBox, QSpinBox { background: #1e1e1e; color: white; } QCheckBox { color: #ddd; }"
        for w in (self.choice, self.start_note, self.transpose, self.guide):
            w.setStyleSheet(style)
        for w in (self.cents_label, self.choice, self.start_note, self.transpose,
                  self.guide, self.start_btn, self.status):
            left.addWidget(w)
        left.addStretch()
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(230)
        layout.addWidget(left_widget)

        self.roll = PianoRoll()
        layout.addWidget(self.roll)
        self.start_note.currentIndexChanged.connect(self._refresh_targets)
        self.transpose.valueChanged.connect(self._refresh_targets)
        self._refresh_targets()
        engine.frame_ready.connect(self.on_frame)

    # --- CHOOSING ---

    def _is_midi(self):
        return self.choice.currentIndex() == len(PATTERNS)

    def _choice_changed(self):
        if self._is_midi():
            path, _ = QFileDialog.getOpenFileName(self, "Load MIDI melody", "", "MIDI files (*.mid *.midi)")
            if path:
                try:
                    self.midi_targets = load_midi_melody(path)
                    self.midi_name = path.replace("\\", "/").split("/")[-1]
                except Exception as e:
                    self.status.setText(f"Couldn't load MIDI: {e}")
                    self.midi_targets = None
            if not self.midi_targets:
                self.choice.setCurrentIndex(0)
        self._refresh_targets()

    def _refresh_targets(self):
        midi = self._is_midi()
        self.start_note.setVisible(not midi)
        self.transpose.setVisible(midi)
        self.roll.set_targets(self.build_targets())

    def build_targets(self):
        if self._is_midi() and self.midi_targets:
            return [(s, d, n + self.transpose.value()) for s, d, n in self.midi_targets]
        name, intervals, seconds = PATTERNS[min(self.choice.currentIndex(), len(PATTERNS) - 1)]
        return pattern_targets(intervals, seconds, self.start_note.currentData())

    def exercise_name(self):
        if self._is_midi() and self.midi_name:
            return self.midi_name
        return f"{self.choice.currentText()} from {self.start_note.currentText()}"

    # --- RUNNING ---

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.engine.is_capturing():
            self.status.setText("Stop the current recording first.")
            return
        self.targets = self.build_targets()
        self.roll.set_targets(self.targets)
        self.engine.start_take()
        self.latency = 0.0
        self.recent = []
        if self.guide.isChecked():
            sd.play(guide_audio(self.targets), SAMPLE_RATE)
            self.latency = output_latency()
        self.t0 = time.perf_counter()
        self._set_running(True)
        self.status.setText("Count-in…")

    def stop(self, finished=False):
        sd.stop()
        audio = self.engine.stop_take() if self.engine.is_capturing() else np.zeros(0)
        self._set_running(False)
        if not finished:
            self.status.setText("Stopped.")
            return
        self.status.setText("Scoring…")
        self.worker = AnalysisWorker(analyze_exercise_take, audio, self.targets, self.exercise_name(),
                                     self.latency)
        self.worker.finished_analysis.connect(self._scored)
        self.worker.start()

    def _set_running(self, running):
        self.running = running
        self.start_btn.setText("Stop" if running else "Start")
        for w in (self.choice, self.start_note, self.transpose):
            w.setEnabled(not running)
        self.running_changed.emit(running)

    def now(self):
        return time.perf_counter() - self.t0 if self.running else None

    def current_target(self):
        """MIDI note the singer should be on right now, or None."""
        return current_target(self.targets, self.now()) if self.running else None

    def perceived_error(self, now, f0, target):
        """Perceived cents from `target` (octave-folded) over the last PERCEIVED_SECONDS, or NaN."""
        cents = np.nan
        if target is not None and not np.isnan(f0):
            cents = 100 * (float(hz_to_midi(f0)) - target)
            cents -= 1200 * np.round(cents / 1200)
        self.recent = [(t, c) for t, c in self.recent if now - t <= PERCEIVED_SECONDS] + [(now, cents)]
        return perceived_cents([c for _, c in self.recent])

    def on_frame(self, frame):
        if not self.running:
            return
        now = self.now()
        f0 = frame["f0"]
        # The singer follows what they hear, which the output device delays.
        target = current_target(self.targets, now - self.latency)
        cents = self.perceived_error(now, f0, target)
        color = tuning_color(cents)
        self.roll.update_view(now, f0, color)
        if target is not None and color is not None:
            self.cents_label.setText(f"{midi_name(target)} {cents:+.0f}¢")
            self.cents_label.setStyleSheet(f"color: {color};")
        elif target is not None:
            self.cents_label.setText(midi_name(target))
            self.cents_label.setStyleSheet("color: white;")
        if now < LEAD_IN:
            self.status.setText(f"Count-in… {COUNT_IN_BEATS - int(now / BEAT_SECONDS)}")
        else:
            self.status.setText("Sing!")
        if now > max(s + d for s, d, _ in self.targets) + TAIL_SECONDS:
            self.stop(finished=True)

    def _scored(self, result):
        if "error" in result:
            self.status.setText(result["error"])
            return
        score = result["exercise"]
        if score["notes_sung"]:
            self.status.setText(f"{score['notes_in_tune']}/{score['notes_total']} notes in tune "
                                f"(±{IN_TUNE_CENTS}¢), {score['notes_close']} close (±{CLOSE_CENTS}¢), "
                                f"average error {score['mean_abs_cents']:.0f}¢ · {score['timing']}.")
        else:
            self.status.setText("No sung notes detected.")
        self.cents_label.setText("—")
        self.cents_label.setStyleSheet("color: white;")
        self.take_ready.emit(result)
