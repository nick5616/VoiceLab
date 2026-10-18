"""Per-take panel of sliders that re-render the take with the voice shifted,
play it, and show what the shifted version measures."""
import numpy as np
import qtawesome as qta
import sounddevice as sd
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout

from analysis import SAMPLE_RATE, hz_to_note
from engine import AnalysisWorker
from shift import NEUTRAL, SHIFT_PARAMS, is_neutral, render_and_analyze

BUTTON_STYLE = "background-color: #3a3a3a; color: white; padding: 5px 10px; border-radius: 5px;"
RENDER_DELAY_MS = 400   # wait this long after the last slider move before rendering
STEPS = {"semitones": 0.5, "resonance": 1, "intonation": 5, "tempo": 5,
         "breathiness": 5, "brightness": 0.5}
HINTS = {
    "semitones": "Moves pitch, keeps resonance.",
    "resonance": "Scales formants: + brighter / smaller tract, − darker / larger.",
    "intonation": "Widens or flattens pitch movement around your median.",
    "tempo": "Speeds up or slows down without changing pitch.",
    "breathiness": "+ airier, − more pressed. Weight-like.",
    "brightness": "Spectral tilt: + edgier/heavier, − softer/lighter.",
}


class ShiftPanel(QFrame):
    def __init__(self, data, on_use_as_target):
        super().__init__()
        self.data = data
        self.on_use_as_target = on_use_as_target
        self.worker = None
        self.rendered = None
        self.pending_play = False
        self.setStyleSheet("ShiftPanel { background-color: #232323; border-radius: 6px; } "
                           "QLabel { color: #ddd; font-size: 11px; margin: 0px; background: transparent; }")
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        self.sliders, self.value_labels = {}, {}
        for row, (key, (label, lo, hi, neutral, unit)) in enumerate(SHIFT_PARAMS.items()):
            step = STEPS[key]
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(int(lo / step), int(hi / step))
            slider.setValue(int(neutral / step))
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
        self.original_btn.clicked.connect(self.play_original)
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
        self.render_timer.timeout.connect(lambda: self.render(play=True))
        self._update_labels()

    def params(self):
        return {key: slider.value() * STEPS[key] for key, slider in self.sliders.items()}

    def _update_labels(self):
        for key, value in self.params().items():
            unit = SHIFT_PARAMS[key][4]
            sign = "+" if key in ("semitones", "resonance", "breathiness", "brightness") and value > 0 else ""
            self.value_labels[key].setText(f"{sign}{value:g} {unit}".strip())
        neutral = is_neutral(self.params())
        self.shifted_btn.setEnabled(not neutral)
        self.target_btn.setEnabled(not neutral and self._is_current())
        if neutral:
            self.result_label.setText("Move a slider: the shifted version renders and plays when you let go.")
        elif not self._is_current() and self.worker is None:
            self.render_timer.start()

    def _is_current(self):
        return self.rendered is not None and self.rendered["params"] == self.params()

    def reset(self):
        for key, slider in self.sliders.items():
            slider.setValue(int(NEUTRAL[key] / STEPS[key]))

    def play_original(self):
        sd.play(self.data["audio"], SAMPLE_RATE)
        self.result_label.setText("Playing original. " + self._summary_text())

    def play_shifted(self):
        """Play the shifted version, rendering it first if the sliders moved."""
        if is_neutral(self.params()):
            self.play_original()
        elif self._is_current():
            sd.play(self.rendered["audio"], SAMPLE_RATE)
            self.result_label.setText("Playing shifted. " + self._summary_text())
        else:
            self.render(play=True)

    def render(self, play=False):
        params = self.params()
        if is_neutral(params):
            return
        if self.worker is not None:          # one render at a time; redo after
            self.pending_play = self.pending_play or play
            return
        self.pending_play = play
        self.result_label.setText(f"Rendering {self._params_text(params)}…")
        self.worker = AnalysisWorker(render_and_analyze, self.data["audio"], params)
        self.worker.finished_analysis.connect(self._rendered)
        self.worker.start()

    def _rendered(self, result):
        self.worker = None
        if "error" in result:
            self.result_label.setText(result["error"])
            return
        self.rendered = result
        if not self._is_current():           # sliders moved while rendering
            self.render(play=self.pending_play)
            return
        self._update_labels()
        if self.pending_play:
            sd.play(result["audio"], SAMPLE_RATE)
        self.result_label.setText(("Playing shifted. " if self.pending_play else "") + self._summary_text())

    def _summary_text(self):
        if not self._is_current():
            return ""
        analysis = self.rendered["analysis"]
        if "error" in analysis:
            return f"Shifted audio couldn't be measured: {analysis['error']}"
        return f"Shifted ({self._params_text(self.rendered['params'])}) measures: {self._compare(analysis)}"

    @staticmethod
    def _params_text(params):
        changed = [f"{SHIFT_PARAMS[k][0].lower()} {v:+g}{SHIFT_PARAMS[k][4] and ' ' + SHIFT_PARAMS[k][4]}"
                   if k not in ("intonation", "tempo") else f"{SHIFT_PARAMS[k][0].lower()} {v:g}%"
                   for k, v in params.items() if v != NEUTRAL[k]]
        return ", ".join(changed)

    def _compare(self, shifted):
        """Shifted values with the change from the original take."""
        original = self.data
        parts = []
        note, _ = hz_to_note(shifted["pitch"])
        parts.append(f"pitch {note} {shifted['pitch']:.0f} Hz "
                     f"({12 * np.log2(shifted['pitch'] / original['pitch']):+.1f} st)")
        if shifted["delta_f"] and original["delta_f"]:
            parts.append(f"ΔF {shifted['delta_f']:.0f} Hz ({shifted['delta_f'] - original['delta_f']:+.0f})")
        for key, name in (("h1h2", "fold contact"), ("cpp", "clarity"), ("ring", "ring")):
            if not np.isnan(shifted[key]) and not np.isnan(original[key]):
                parts.append(f"{name} {shifted[key]:.1f} dB ({shifted[key] - original[key]:+.1f})")
        if not np.isnan(shifted["pitch_range"]):
            parts.append(f"intonation {shifted['pitch_range']:.1f} st")
        return " · ".join(parts)

    def _use_as_target(self):
        if self.rendered:
            self.on_use_as_target(self.rendered["analysis"])
