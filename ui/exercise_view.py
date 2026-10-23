"""The singing exercise view: choices, the scrolling piano roll and the readout. exercise.ExerciseSession
runs the exercise; this draws it."""
import numpy as np
import pyqtgraph as pg
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                             QPushButton, QSpinBox, QVBoxLayout, QWidget)

from display import midi_name
from exercise import CLOSE_COLOR, IN_TUNE_COLOR, MIDI_CHOICE, NOTE_RANGE, OFF_COLOR, ROLL_AFTER, ROLL_BEFORE
from ui.meters import BUTTON_STYLE

BAR_COLORS = {IN_TUNE_COLOR: (46, 204, 113, 200), CLOSE_COLOR: (241, 196, 15, 200), OFF_COLOR: (230, 126, 34, 200)}
DEFAULT_BAR_COLOR = (52, 152, 219, 200)


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
        self.show_view(0.0, np.nan, None, None)

    def show_view(self, now, midi, color, current):
        """Move the roll to `now` with the voice at `midi` (NaN unvoiced); `color` tints the current bar,
        `current` is (start, duration, midi) of the bar under the playhead or None."""
        self.trace_t.append(now)
        self.trace_m.append(midi)
        self.trace.setData(self.trace_t, self.trace_m)
        self.playhead.setValue(now)
        self.setXRange(now - ROLL_BEFORE, now + ROLL_AFTER, padding=0)
        self.current_bar.setPen(pg.mkPen(BAR_COLORS.get(color, DEFAULT_BAR_COLOR), width=12))
        if current is not None:
            start, duration, note = current
            self.current_bar.setData([start, start + duration - 0.02], [note, note])
        else:
            self.current_bar.setData([], [])


class ExerciseView(QWidget):
    """Pick an exercise, sing it against the piano roll, get a scored take (the core's ExerciseSession)."""

    def __init__(self, core):
        super().__init__()
        self.session = core.exercise
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        self.cents_label = QLabel("—")
        self.cents_label.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        self.cents_label.setStyleSheet("color: white;")
        self.choice = QComboBox()
        for name in self.session.choice_names():
            self.choice.addItem(name)
        self.choice.activated.connect(self._choice_changed)
        self.start_note = QComboBox()
        for midi, name in self.session.start_notes():
            self.start_note.addItem(name, midi)
        self.start_note.setCurrentIndex(NOTE_RANGE.index(self.session.start_midi))
        self.transpose = QSpinBox()
        self.transpose.setRange(-24, 24)
        self.transpose.setPrefix("Transpose ")
        self.transpose.hide()
        self.guide = QCheckBox("Guide tone")
        self.guide.setToolTip("Use headphones, or the mic will hear the guide instead of you.")
        self.guide.setChecked(self.session.guide)
        self.guide.toggled.connect(lambda on: setattr(self.session, "guide", on))
        self.start_btn = QPushButton("Start")
        self.start_btn.setStyleSheet(BUTTON_STYLE)
        self.start_btn.clicked.connect(self.toggle)
        self.status = QLabel(self.session.status)
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
        core.events.subscribe("exercise_changed", self.show_state)

    @property
    def running(self):
        return self.session.running

    def build_targets(self):
        return self.session.build_targets()

    def current_target(self):
        return self.session.current_target()

    def start(self):
        if self.session.start():
            self.roll.set_targets(self.session.targets)

    def stop(self, finished=False):
        self.session.stop(finished)

    def toggle(self):
        if self.session.running:
            self.stop()
        else:
            self.start()

    def _choice_changed(self, index):
        path = None
        if index == MIDI_CHOICE:
            path, _ = QFileDialog.getOpenFileName(self, "Load MIDI melody", "", "MIDI files (*.mid *.midi)")
        self.choice.setCurrentIndex(self.session.choose(index, path or None))
        self._refresh_targets()

    def _refresh_targets(self, *_):
        self.session.start_midi = self.start_note.currentData()
        self.session.transpose = self.transpose.value()
        midi = self.session.is_midi()
        self.start_note.setVisible(not midi)
        self.transpose.setVisible(midi)
        self.roll.set_targets(self.session.build_targets())

    def show_state(self, state):
        """Draw the session's texts and lock the choices while it runs."""
        self.start_btn.setText("Stop" if state["running"] else "Start")
        for w in (self.choice, self.start_note, self.transpose):
            w.setEnabled(not state["running"])
        self.status.setText(state["status"])
        self.cents_label.setText(state["cents_text"])
        self.cents_label.setStyleSheet(f"color: {state['cents_color'] or 'white'};")

    def show_frame(self, view):
        """Draw one live frame of the running exercise (ExerciseSession.on_frame's result)."""
        if view is None:
            return
        self.roll.show_view(view["now"], view["midi"], view["color"], view["current"])
        if not view["finished"]:
            self.show_state(view)
