"""The vowel check (speech.VowelCheckSession draws here): the word to say, Start / Skip / Save, progress
through the eight words, and the vowel chart."""
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from speech import VOWEL_PROMPT, VOWEL_WORDS
from ui import theme
from ui.components import Button, Caption, Headline, Label, ProgressStrip
from ui.live_graph import style_plot
from ui.screens.listen import listen


class VowelChart(pg.PlotWidget):
    """Classic vowel space: F2 (right to left) against F1 (top to bottom)."""

    def __init__(self):
        super().__init__()
        self.invertX(True)
        self.invertY(True)
        self.setXRange(700, 3000, padding=0)
        self.setYRange(250, 1050, padding=0)
        # Bottom-left (front, open) is the emptiest corner of a vowel chart.
        self.legend = self.addLegend(offset=(10, -10))
        self.data = (None, None)
        theme.changed.connect(self.restyle)
        self.restyle()

    def restyle(self, *_):
        t = theme.current()
        self.setBackground(t["surface"])
        style_plot(self.plotItem, left_width=56)
        self.showGrid(x=True, y=True, alpha=0.12)
        self.getAxis("bottom").setLabel("F2 Hz (front ← → back)", color=t["text_muted"])
        self.getAxis("left").setLabel("F1 Hz", color=t["text_muted"])
        self.legend.setLabelTextColor(t["text_secondary"])
        self.show_vowels(*self.data)

    def show_vowels(self, current, baseline):
        """current / baseline: {word: {"f1":..., "f2":...}} or None."""
        self.data = (current, baseline)
        t = theme.current()
        self.clear()
        self.legend.clear()
        for vowels, color, name, style in ((baseline, t["text_muted"], "baseline", Qt.PenStyle.DashLine),
                                           (current, t["accent"], "this check", Qt.PenStyle.SolidLine)):
            if not vowels:
                continue
            words = [w for w in VOWEL_WORDS if vowels.get(w) and vowels[w].get("f1")]
            xs = [vowels[w]["f2"] for w in words]
            ys = [vowels[w]["f1"] for w in words]
            # Words are ordered around the vowel space, so this draws its outline.
            self.plot(xs + xs[:1], ys + ys[:1], pen=pg.mkPen(color, width=2, style=style),
                      symbol="o", symbolSize=8, symbolBrush=color, symbolPen=None, name=name)
            if vowels is current:
                for word, x, y in zip(words, xs, ys):
                    label = pg.TextItem(word, color=QColor(t["text_secondary"]), anchor=(0, 1))
                    label.setFont(theme.font("caption"))
                    label.setPos(x, y)
                    self.addItem(label)


class VowelCheck(QWidget):
    """The hands-free vowel drill (speech.VowelCheckSession): prompts, buttons, progress and the chart."""

    def __init__(self, core):
        super().__init__()
        self.session = core.vowels
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.XL)
        left = QVBoxLayout()
        left.setSpacing(theme.M)
        self.overline = Label("Say this word, then pause", role="label", tone="muted")
        self.prompt = Headline("", level="display")
        self.progress = ProgressStrip()
        self.progress.set_progress(0, len(VOWEL_WORDS))
        self.status = Label("", role="body", tone="secondary", wrap=True)
        self.start_btn = Button("Start", variant="primary", icon="ph.play-fill")
        self.skip_btn = Button("Skip word", variant="secondary", icon="ph.skip-forward")
        self.baseline_btn = Button("Save as vowel baseline", variant="secondary", icon="ph.push-pin")
        self.start_btn.clicked.connect(self.session.toggle)
        self.skip_btn.clicked.connect(self.session.skip)
        self.baseline_btn.clicked.connect(self.session.save_baseline)
        for w in (self.overline, self.prompt, self.progress, self.status):
            left.addWidget(w)
        buttons = QHBoxLayout()
        buttons.setSpacing(theme.S)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.skip_btn)
        buttons.addStretch(1)
        left.addLayout(buttons)
        left.addWidget(self.baseline_btn, 0, Qt.AlignmentFlag.AlignLeft)
        left.addStretch(1)
        left.addWidget(Caption("The vowel check measures resonance across eight vowels, the fairest reading "
                               "there is, and sets the formant ceiling for your voice."))
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(340)
        layout.addWidget(left_widget)

        self.chart = VowelChart()
        layout.addWidget(self.chart, 1)
        self._chart_data = None
        listen(self, core.events, ("vowels_changed", self.show_state))
        self.show_state(self.session.state())

    @property
    def running(self):
        return self.session.running

    @property
    def result(self):
        return self.session.result

    def show_state(self, state):
        """Draw the session's state: texts, buttons, progress and the chart."""
        if state is None:
            return
        self.prompt.setText(state["prompt"])
        self.prompt.setVisible(state["prompt"] != VOWEL_PROMPT)       # the page already says "Vowel check"
        self.status.setText(state["status"])
        self.start_btn.setText("Stop" if state["running"] else "Start")
        self.start_btn.set_icon("ph.stop-fill" if state["running"] else "ph.play-fill")
        self.start_btn.set_variant("secondary" if state["running"] else "primary")
        self.skip_btn.setEnabled(state["running"])
        self.baseline_btn.setEnabled(state["can_save_baseline"])
        self.overline.setVisible(state["running"])
        done = self.session.index if state["running"] else (len(VOWEL_WORDS) if self.session.result else 0)
        self.progress.set_progress(done, len(VOWEL_WORDS))
        chart = (state["current"], state["baseline"])
        if chart != self._chart_data:
            self._chart_data = chart
            self.chart.show_vowels(*chart)
