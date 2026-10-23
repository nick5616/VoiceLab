"""The singing exercise view: choices, Start / Stop, progress, the scrolling piano roll and the tuning
readout. exercise.ExerciseSession runs the exercise; this draws it."""
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QSpinBox, QVBoxLayout, QWidget

from display import midi_name
from exercise import CLOSE_COLOR, IN_TUNE_COLOR, MIDI_CHOICE, OFF_COLOR, ROLL_AFTER, ROLL_BEFORE, TAIL_SECONDS
from ui import theme
from ui.components import Button, Caption, Label, ProgressStrip, Select, Toggle
from ui.live_graph import style_plot
from ui.screens.listen import listen

# The session's tuning colours -> theme tokens: in tune, close, off.
TUNING_TOKENS = {IN_TUNE_COLOR: "on_target", CLOSE_COLOR: "warning", OFF_COLOR: "off_target"}


class PianoRoll(pg.PlotWidget):
    """Target notes as bars scrolling past a playhead, with your pitch on top."""

    def __init__(self):
        super().__init__()
        self.bars = self.plot(connect="pairs")
        self.current_bar = self.plot()
        self.trace = self.plot(connect="finite")
        self.playhead = pg.InfiniteLine(angle=90)
        self.addItem(self.playhead)
        self.targets = []
        self.trace_t, self.trace_m = [], []
        self.color = None
        theme.changed.connect(self.restyle)
        self.restyle()

    def restyle(self, *_):
        t = theme.current()
        self.setBackground(t["surface"])
        style_plot(self.plotItem, left_width=48)
        self.showGrid(x=False, y=True, alpha=0.12)
        self.bars.setPen(pg.mkPen(t.q("text_muted", 150), width=12))
        self.trace.setPen(pg.mkPen(t.series["pitch"], width=2.5))
        self.playhead.setPen(pg.mkPen(t["accent"], width=2))
        self._color_bar()

    def _color_bar(self):
        t = theme.current()
        self.current_bar.setPen(pg.mkPen(t[TUNING_TOKENS.get(self.color, "accent")], width=12))

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
        if color != self.color:
            self.color = color
            self._color_bar()
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
        layout.setSpacing(theme.XL)
        left = QVBoxLayout()
        left.setSpacing(theme.M)
        self.cents_label = Label("—", role="figure")
        self.cents_label.setFont(theme.font("figure", size=40))
        self.choice = Select([(str(i), name) for i, name in enumerate(self.session.choice_names())])
        self.choice.activated.connect(self._choice_changed)
        self.start_note = Select([(str(m), name) for m, name in self.session.start_notes()],
                                 current=str(self.session.start_midi))
        self.transpose = QSpinBox()
        self.transpose.setRange(-24, 24)
        self.transpose.setPrefix("Transpose ")
        self.transpose.hide()
        self.guide = Toggle(self.session.guide, "Guide tone")
        self.guide.setToolTip("Use headphones, or the mic will hear the guide instead of you.")
        self.guide.toggled.connect(lambda on: setattr(self.session, "guide", on))
        self.start_btn = Button("Start", variant="primary", icon="ph.play-fill")
        self.start_btn.clicked.connect(self.toggle)
        self.progress = ProgressStrip()
        self.progress.set_progress(0, 1)
        self.status = Label(self.session.status, role="body", tone="secondary", wrap=True)
        left.addWidget(self.cents_label)
        left.addWidget(Label("Exercise", role="label", tone="muted"))
        left.addWidget(self.choice)
        left.addWidget(Label("Start note", role="label", tone="muted"))
        left.addWidget(self.start_note)
        left.addWidget(self.transpose)
        left.addWidget(self.guide)
        left.addWidget(self.start_btn, 0, Qt.AlignmentFlag.AlignLeft)
        left.addWidget(self.progress)
        left.addWidget(self.status)
        left.addStretch(1)
        left.addWidget(Caption("Each finished exercise becomes a scored take: notes in tune, timing and "
                               "steadiness."))
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(320)
        layout.addWidget(left_widget)

        self.roll = PianoRoll()
        layout.addWidget(self.roll, 1)
        self.start_note.changed.connect(self._refresh_targets)
        self.transpose.valueChanged.connect(self._refresh_targets)
        self._refresh_targets()
        self.show_state(self.session.state())
        listen(self, core.events, ("exercise_changed", self.show_state))

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
        self.session.start_midi = int(self.start_note.current_key())
        self.session.transpose = self.transpose.value()
        midi = self.session.is_midi()
        self.start_note.setVisible(not midi)
        self.transpose.setVisible(midi)
        self.roll.set_targets(self.session.build_targets())

    def show_state(self, state):
        """Draw the session's texts and lock the choices while it runs."""
        running = state["running"]
        self.start_btn.setText("Stop" if running else "Start")
        self.start_btn.set_icon("ph.stop-fill" if running else "ph.play-fill")
        self.start_btn.set_variant("secondary" if running else "primary")
        for w in (self.choice, self.start_note, self.transpose):
            w.setEnabled(not running)
        self.status.setText(state["status"])
        idle = state["cents_text"] == "—"
        self.cents_label.setText("Ready" if idle else state["cents_text"])
        self.cents_label.set_tone("muted" if idle else TUNING_TOKENS.get(state["cents_color"], "primary"))

    def show_frame(self, view):
        """Draw one live frame of the running exercise (ExerciseSession.on_frame's result)."""
        if view is None:
            return
        self.roll.show_view(view["now"], view["midi"], view["color"], view["current"])
        end = max(s + d for s, d, _ in view["targets"]) + TAIL_SECONDS if view["targets"] else 1.0
        self.progress.set_progress(min(view["now"], end), end)
        if not view["finished"]:
            self.show_state(view)
