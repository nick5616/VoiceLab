"""Speaking practice views: the reading prompt and the vowel check (speech.VowelCheckSession draws here)."""
import pyqtgraph as pg
import qtawesome as qta
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from speech import VOWEL_WORDS, passage
from ui.meters import BUTTON_STYLE

VOWEL_COLOR = "#f1c40f"
BASELINE_COLOR = "#95a5a6"


class ReadingPrompt(QFrame):
    """A passage to read aloud, with previous/next controls. on_passage(index) hears about a change."""

    def __init__(self, on_passage=None):
        super().__init__()
        self.index = 0
        self.on_passage = on_passage or (lambda index: None)
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
        shown = passage(index)
        self.index = shown["index"]
        self.title.setText(shown["title"])
        self.text.setText(shown["text"])
        self.on_passage(self.index)


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
    """The hands-free vowel drill (speech.VowelCheckSession): prompts, buttons and the vowel chart."""

    def __init__(self, core):
        super().__init__()
        self.session = core.vowels
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        self.prompt = QLabel()
        self.prompt.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        self.prompt.setStyleSheet("color: white;")
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #aaa; font-size: 11px;")
        self.start_btn = QPushButton("Start")
        self.skip_btn = QPushButton("Skip word")
        self.baseline_btn = QPushButton("Save as vowel baseline")
        for btn in (self.start_btn, self.skip_btn, self.baseline_btn):
            btn.setStyleSheet(BUTTON_STYLE)
        self.start_btn.clicked.connect(self.session.toggle)
        self.skip_btn.clicked.connect(self.session.skip)
        self.baseline_btn.clicked.connect(self.session.save_baseline)
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
        self._chart_data = None
        core.events.subscribe("vowels_changed", self.show_state)
        self.show_state(self.session.state())

    @property
    def running(self):
        return self.session.running

    @property
    def result(self):
        return self.session.result

    def show_state(self, state):
        """Draw the session's state: texts, buttons and the chart."""
        if state is None:
            return
        self.prompt.setText(state["prompt"])
        self.status.setText(state["status"])
        self.start_btn.setText("Stop" if state["running"] else "Start")
        self.skip_btn.setEnabled(state["running"])
        self.baseline_btn.setEnabled(state["can_save_baseline"])
        chart = (state["current"], state["baseline"])
        if chart != self._chart_data:
            self._chart_data = chart
            self.chart.show_vowels(*chart)
