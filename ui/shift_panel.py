"""Per-take panel of sliders that hear the take with the voice shifted (shift_session.ShiftSession)."""
import qtawesome as qta
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from shift_session import HINTS, SHIFT_PARAMS, ShiftSession, slider_range
from ui.meters import BUTTON_STYLE
from ui.widgets import PageSlider

RENDER_DELAY_MS = 400   # wait this long after the last slider move before rendering


class ShiftPanel(QFrame):
    def __init__(self, data, on_use_as_target, audio=None, runner=None, player=None):
        super().__init__()
        self.data = data
        self.on_use_as_target = on_use_as_target
        self.session = ShiftSession(data, audio, runner=runner, player=player, on_change=self._show)
        self.setStyleSheet("ShiftPanel { background-color: #232323; border-radius: 6px; } "
                           "QLabel { color: #ddd; font-size: 11px; margin: 0px; background: transparent; }")
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        self.sliders, self.value_labels = {}, {}
        for row, (key, (label, *_)) in enumerate(SHIFT_PARAMS.items()):
            lo, hi, neutral = slider_range(key)
            slider = PageSlider(Qt.Orientation.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(neutral)
            slider.valueChanged.connect(self._update_labels)
            name = QLabel(label)
            name.setToolTip(HINTS[key])
            value = QLabel()
            value.setFixedWidth(80)
            grid.addWidget(name, row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(value, row, 2)
            self.sliders[key], self.value_labels[key] = slider, value
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        self.original_btn = QPushButton(qta.icon("fa5s.play", color="white"), " Original")
        self.shifted_btn = QPushButton(qta.icon("fa5s.play", color="white"), " Shifted")
        self.reset_btn = QPushButton("Reset")
        self.target_btn = QPushButton("Use shifted as target")
        for btn in (self.original_btn, self.shifted_btn, self.reset_btn, self.target_btn):
            btn.setStyleSheet(BUTTON_STYLE)
            buttons.addWidget(btn)
        self.shifted_btn.setStyleSheet("background-color: #2980b9; color: white; padding: 5px 10px; border-radius: 5px;")
        self.original_btn.clicked.connect(self.session.play_original)
        self.shifted_btn.clicked.connect(lambda: self.play_shifted())
        self.reset_btn.clicked.connect(self.reset)
        self.target_btn.clicked.connect(self._use_as_target)
        layout.addLayout(buttons)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        # Re-render shortly after the sliders stop moving, then play it.
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(RENDER_DELAY_MS)
        self.render_timer.timeout.connect(lambda: self.session.render(play=True))
        self._update_labels()

    # The session's render, under the name the panel always had.
    rendered = property(lambda self: self.session.rendered,
                        lambda self, value: setattr(self.session, "rendered", value))

    def params(self):
        return self.session.params()

    def _is_current(self):
        return self.session.is_current()

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
