"""Speaking practice: reading passages and the guided vowel check."""
import numpy as np
import pyqtgraph as pg
import qtawesome as qta
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from analysis import VOWEL_WORDS, analyze_vowels, vocal_tract_length_cm
from engine import AnalysisWorker, UPDATE_INTERVAL

# Original passages, written to cover different speaking situations. Reading the
# same passage each session makes takes comparable over time.
READING_PASSAGES = [
    ("Vowel tour",
     "We see three green leaves. Is it his big fish? Ten red hens fed well. "
     "The black cat sat back. Father wants hot soup. The sun comes up. "
     "Look, the good book stood. Who knew the blue moon would move so soon?"),
    ("Questions and answers",
     "Are you coming tonight? Yes, I'd love to! What time does it start? "
     "Around eight, I think. Should I bring anything? Just yourself, "
     "and maybe a few snacks if you're feeling generous."),
    ("Everyday story",
     "This morning I walked to the little café on the corner. The barista remembered "
     "my name and my usual order. While I waited, I watched the rain roll across the "
     "windows, and I realized how much I enjoy these small, quiet routines."),
    ("Phone call",
     "Hi, it's me! I'm just calling to check in. Do you have a minute? Great. "
     "So, about Saturday: are we still meeting at noon, or would a bit later "
     "work better for you? Either way is fine with me."),
    ("Counting and days",
     "One, two, three, four, five, six, seven, eight, nine, ten. "
     "Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday."),
]

BUTTON_STYLE = "background-color: #3a3a3a; color: white; padding: 5px 10px; border-radius: 5px;"
# Vowel check capture: this much voicing counts as hearing the word, and this
# much silence afterwards means it has ended.
HEARD_SECONDS = 0.2
END_SILENCE_SECONDS = 0.4
VOWEL_COLOR = "#f1c40f"
BASELINE_COLOR = "#95a5a6"


class ReadingPrompt(QFrame):
    """A passage to read aloud, with previous/next controls."""

    def __init__(self):
        super().__init__()
        self.index = 0
        self.setStyleSheet("QFrame { background-color: #1e1e1e; border-radius: 6px; } "
                           "QLabel { color: #eee; }")
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("color: #aaa; font-size: 11px;")
        prev_btn = QPushButton(qta.icon("fa5s.chevron-left", color="white"), "")
        next_btn = QPushButton(qta.icon("fa5s.chevron-right", color="white"), "")
        for btn, step in ((prev_btn, -1), (next_btn, 1)):
            btn.setStyleSheet(BUTTON_STYLE)
            btn.setFixedWidth(34)
            btn.clicked.connect(lambda _, s=step: self.show_passage(self.index + s))
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(prev_btn)
        header.addWidget(next_btn)
        layout.addLayout(header)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setFont(QFont("Georgia", 13))
        layout.addWidget(self.text)
        self.show_passage(0)

    def show_passage(self, index):
        self.index = index % len(READING_PASSAGES)
        name, text = READING_PASSAGES[self.index]
        self.title.setText(f"Read aloud · {self.index + 1}/{len(READING_PASSAGES)} · {name}")
        self.text.setText(text)


class VowelChart(pg.PlotWidget):
    """Classic vowel space: F2 (right to left) against F1 (top to bottom)."""

    def __init__(self):
        super().__init__(background="#1e1e1e")
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.setMenuEnabled(False)
        self.invertX(True)
        self.invertY(True)
        self.setXRange(700, 3000, padding=0)
        self.setYRange(250, 1050, padding=0)
        self.showGrid(x=True, y=True, alpha=0.15)
        self.getAxis("bottom").setLabel("F2 Hz (front ← → back)")
        self.getAxis("left").setLabel("F1 Hz")
        # Bottom-left (front, open) is the emptiest corner of a vowel chart.
        self.addLegend(offset=(10, -10))

    def show_vowels(self, current, baseline):
        """current / baseline: {word: {"f1":..., "f2":...}} or None."""
        self.clear()
        for vowels, color, name, style in ((baseline, BASELINE_COLOR, "baseline", Qt.PenStyle.DashLine),
                                           (current, VOWEL_COLOR, "this check", Qt.PenStyle.SolidLine)):
            if not vowels:
                continue
            words = [w for w in VOWEL_WORDS if vowels.get(w) and vowels[w].get("f1")]
            xs = [vowels[w]["f2"] for w in words]
            ys = [vowels[w]["f1"] for w in words]
            # Words are ordered around the vowel space, so this draws its outline.
            self.plot(xs + xs[:1], ys + ys[:1], pen=pg.mkPen(color, width=1.5, style=style),
                      symbol="o", symbolSize=7, symbolBrush=color, symbolPen=None, name=name)
            if vowels is current:
                for word, x, y in zip(words, xs, ys):
                    label = pg.TextItem(word, color="#ddd", anchor=(0, 1))
                    label.setPos(x, y)
                    self.addItem(label)


class VowelCheck(QWidget):
    """Hands-free drill: prompts each hVd word, captures it when you finish
    saying it, then charts your vowel space and vowel-balanced resonance."""

    running_changed = pyqtSignal(bool)
    completed = pyqtSignal(dict)

    def __init__(self, engine, profile):
        super().__init__()
        self.engine, self.profile = engine, profile
        self.running = False
        self.result = None
        self.worker = None
        self.tokens, self.index = {}, 0
        self.voiced_frames = self.silent_frames = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        self.prompt = QLabel("Vowel check")
        self.prompt.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        self.prompt.setStyleSheet("color: white;")
        self.status = QLabel("Say 8 short words, one at a time. Each is captured when you "
                             "pause. Use your practice voice; keep pitch under ~350 Hz.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #aaa; font-size: 11px;")
        self.start_btn = QPushButton("Start")
        self.skip_btn = QPushButton("Skip word")
        self.baseline_btn = QPushButton("Save as vowel baseline")
        for btn in (self.start_btn, self.skip_btn, self.baseline_btn):
            btn.setStyleSheet(BUTTON_STYLE)
        self.start_btn.clicked.connect(self.toggle)
        self.skip_btn.clicked.connect(self.skip)
        self.baseline_btn.clicked.connect(self.save_baseline)
        self.skip_btn.setEnabled(False)
        self.baseline_btn.setEnabled(False)
        left.addWidget(self.prompt)
        left.addWidget(self.status)
        left.addStretch()
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.skip_btn)
        left.addLayout(buttons)
        left.addWidget(self.baseline_btn)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(230)
        layout.addWidget(left_widget)

        self.chart = VowelChart()
        layout.addWidget(self.chart)
        baseline = profile.vowel_baseline
        self.chart.show_vowels(None, baseline["vowels"] if baseline else None)
        engine.frame_ready.connect(self.on_frame)

    # --- DRILL FLOW ---

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.engine.is_capturing():
            self.status.setText("Stop the current recording first.")
            return
        self.tokens, self.index = {}, 0
        self._set_running(True)
        self._prompt_word()

    def stop(self):
        if self.engine.is_capturing():
            self.engine.stop_take()
        self._set_running(False)
        self.prompt.setText("Vowel check")
        self.status.setText("Stopped.")

    def skip(self):
        if self.running:
            self.engine.stop_take()
            self._next_word()

    def _set_running(self, running):
        self.running = running
        self.start_btn.setText("Stop" if running else "Start")
        self.skip_btn.setEnabled(running)
        self.running_changed.emit(running)

    def _prompt_word(self):
        word = VOWEL_WORDS[self.index]
        self.prompt.setText(f"“{word}”")
        self.status.setText(f"Word {self.index + 1} of {len(VOWEL_WORDS)}: say it once, then pause.")
        self.voiced_frames = self.silent_frames = 0
        self.engine.start_take()

    def on_frame(self, frame):
        if not self.running:
            return
        if not np.isnan(frame["f0"]):
            self.voiced_frames += 1
            self.silent_frames = 0
        else:
            self.silent_frames += 1
        heard = self.voiced_frames * UPDATE_INTERVAL >= HEARD_SECONDS
        if heard and self.silent_frames * UPDATE_INTERVAL >= END_SILENCE_SECONDS:
            self.tokens[VOWEL_WORDS[self.index]] = self.engine.stop_take()
            self._next_word()

    def _next_word(self):
        self.index += 1
        if self.index < len(VOWEL_WORDS):
            self._prompt_word()
            return
        self._set_running(False)
        self.prompt.setText("Analyzing…")
        self.status.setText("")
        self.worker = AnalysisWorker(analyze_vowels, self.tokens)
        self.worker.finished_analysis.connect(self._show_result)
        self.worker.start()

    # --- RESULTS ---

    def _show_result(self, result):
        if "error" in result or not result["vowels"]:
            self.prompt.setText("Vowel check")
            self.status.setText(result.get("error", "No vowels could be measured. Try a bit "
                                                    "louder, and keep your pitch under ~350 Hz."))
            return
        self.result = result
        self.profile.log_vowel_check(result)
        self.baseline_btn.setEnabled(True)
        self.refresh_profile()
        self.completed.emit(result)

    def refresh_profile(self):
        baseline = self.profile.vowel_baseline
        self.chart.show_vowels(self.result["vowels"] if self.result else None,
                               baseline["vowels"] if baseline else None)
        if not self.result:
            return
        df = self.result["delta_f"]
        self.prompt.setText(f"ΔF {df:.0f} Hz")
        parts = [f"Vowel-balanced resonance, ≈{vocal_tract_length_cm(df):.1f} cm tract."]
        if baseline and baseline.get("delta_f"):
            parts.append(f"{df - baseline['delta_f']:+.0f} Hz vs vowel baseline "
                         f"({baseline.get('recorded', '?')}).")
        if self.result["skipped"]:
            parts.append(f"Not measured: {', '.join(self.result['skipped'])}.")
        self.status.setText(" ".join(parts))

    def save_baseline(self):
        if self.result:
            self.profile.set_vowel_baseline(self.result)
            self.refresh_profile()
            self.status.setText(self.status.text() + " Saved as vowel baseline.")
