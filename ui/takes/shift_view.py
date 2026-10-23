"""Hear a take with the voice shifted: sliders on shift_session.ShiftSession, in the design system. The
shifted version renders in the core's runner (so it shows in the activity centre) and plays through the
core's player."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QGridLayout, QSlider, QVBoxLayout, QWidget

from shift_session import HINTS, SHIFT_PARAMS, ShiftSession, slider_range
from ui import theme
from ui.components import Button, Caption, Label, WrapRow

RENDER_DELAY_MS = 400   # wait this long after the last slider move before rendering


class FocusSlider(QSlider):
    """A slider that takes the wheel only once clicked, so scrolling past it moves the page."""

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ShiftView(QWidget):
    """Sliders for pitch, resonance, intonation, tempo, breathiness and brightness; Play original,
    Play shifted, Reset, and "Use shifted as target" (on_use_as_target(analysis))."""

    def __init__(self, take, audio, runner, player, on_use_as_target, parent=None):
        super().__init__(parent)
        self.on_use_as_target = on_use_as_target
        self.session = ShiftSession(take, audio, runner=runner, player=player, on_change=self._show)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.M)
        box.addWidget(Caption("Move a slider to hear this take as it would sound with that change: a way to "
                              "find a direction before you practise it.", tone="secondary"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.M)
        grid.setVerticalSpacing(theme.S)
        self.sliders, self.value_labels = {}, {}
        for row, (key, (label, *_)) in enumerate(SHIFT_PARAMS.items()):
            lo, hi, neutral = slider_range(key)
            slider = FocusSlider()
            slider.setRange(lo, hi)
            slider.setValue(neutral)
            slider.setToolTip(HINTS[key])
            slider.valueChanged.connect(self._update_labels)
            name = Label(label, role="body")
            name.setToolTip(HINTS[key])
            value = Label("", role="numeric", tone="secondary")
            value.setMinimumWidth(80)
            grid.addWidget(name, row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(value, row, 2)
            self.sliders[key], self.value_labels[key] = slider, value
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        self.original_btn = Button("Play original", "secondary", icon="ph.play-fill", small=True)
        self.shifted_btn = Button("Play shifted", "primary", icon="ph.play-fill", small=True)
        self.reset_btn = Button("Reset", "ghost", small=True)
        self.target_btn = Button("Use shifted as target", "secondary", icon="ph.target", small=True)
        self.original_btn.clicked.connect(self.session.play_original)
        self.shifted_btn.clicked.connect(self.play_shifted)
        self.reset_btn.clicked.connect(self.reset)
        self.target_btn.clicked.connect(self._use_as_target)
        box.addWidget(WrapRow([self.shifted_btn, self.original_btn, self.reset_btn, self.target_btn]))
        self.result_label = Label("", role="caption", tone="secondary", wrap=True)
        box.addWidget(self.result_label)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(RENDER_DELAY_MS)
        self.render_timer.timeout.connect(lambda: self.session.render(play=True))
        self._update_labels()

    def params(self):
        return self.session.params()

    def _update_labels(self, *_):
        if self.session.set_steps({key: slider.value() for key, slider in self.sliders.items()}):
            self.render_timer.start()
        self._show()

    def _show(self):
        session = self.session
        for key, text in session.value_labels().items():
            self.value_labels[key].setText(text)
        self.shifted_btn.setEnabled(not session.neutral())
        self.target_btn.setEnabled(session.can_target())
        self.result_label.setText(session.message)

    def reset(self):
        for key, position in self.session.reset_steps().items():
            self.sliders[key].setValue(position)

    def play_shifted(self):
        self.session.play_shifted()

    def _use_as_target(self):
        analysis = self.session.target_analysis()
        if analysis is not None:
            self.on_use_as_target(analysis)
