"""The mic check dialog: drives mic_check.MicCheckSession with timers and shows what it found."""
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout

from mic_check import INTRO, LEAD_SECONDS, PHASE_SECONDS, MicCheckSession
from ui import theme
from ui.components import Button, Headline, Label, ProgressStrip


class MicCheckDialog(QDialog):
    """Guides a 3 s silence + 3 s "ah" recording and shows what it found. `result` holds the dict
    afterwards and finished_check carries it. Nothing here opens an audio device."""

    finished_check = pyqtSignal(dict)

    def __init__(self, engine, parent=None, seconds=PHASE_SECONDS, lead_seconds=LEAD_SECONDS):
        super().__init__(parent)
        self.session = MicCheckSession(engine, seconds, lead_seconds)
        self.setWindowTitle("Mic check")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.XL, theme.XL, theme.XL, theme.L)
        layout.setSpacing(theme.M)
        layout.addWidget(Headline("Mic check", level="title"))
        self.instruction = Label(INTRO, role="body", tone="secondary", wrap=True)
        self.status = Label("", role="heading")
        self.strip = ProgressStrip()
        self.strip.hide()
        self.report = Label("", role="body", wrap=True)
        self.report.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.instruction)
        layout.addWidget(self.status)
        layout.addWidget(self.strip)
        layout.addWidget(self.report)
        row = QHBoxLayout()
        self.start_btn = Button("Start", variant="primary", icon="ph.microphone")
        self.close_btn = Button("Close", variant="ghost")
        self.start_btn.clicked.connect(self.start)
        self.close_btn.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(self.close_btn)
        row.addWidget(self.start_btn)
        layout.addLayout(row)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next)
        self._tick = QTimer(self)
        self._tick.setInterval(100)
        self._tick.timeout.connect(self._show_countdown)
        self._total = 2 * seconds + lead_seconds
        self._done = 0.0
        self._phase_seconds = 0.0

    # The session's settings and state, under the names the dialog always had.
    seconds = property(lambda self: self.session.seconds, lambda self, v: setattr(self.session, "seconds", v))
    lead_seconds = property(lambda self: self.session.lead_seconds,
                            lambda self, v: setattr(self.session, "lead_seconds", v))
    phase = property(lambda self: self.session.phase)
    result = property(lambda self: self.session.result)

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), theme.color("surface"))

    def start(self):
        """Begin the check (refused while a take is being recorded)."""
        seconds = self.session.start()
        if seconds is None:
            self.status.setText(self.session.status_text())
            return
        self.report.setText("")
        self.start_btn.setEnabled(False)
        self._total = 2 * self.session.seconds + self.session.lead_seconds
        self._done = 0.0
        self.strip.show()
        self.strip.set_progress(0, self._total)
        self._wait(seconds)

    def _wait(self, seconds):
        self._phase_seconds = seconds
        self._timer.start(int(seconds * 1000))
        self._tick.start()
        self._show_countdown()

    def _show_countdown(self):
        self.status.setText(self.session.status_text())
        left = max(0.0, self.session.ends_at - self.session.clock())
        self.strip.set_progress(self._done + self._phase_seconds - left, self._total)

    def _next(self):
        self._done += self._phase_seconds
        seconds = self.session.next()
        if seconds is not None:
            self._wait(seconds)
            return
        self._tick.stop()
        self._finish()

    def _finish(self):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Run again")
        self.strip.hide()
        report = self.session.report()
        color = theme.current()["on_target" if report["ok"] else "off_target"]
        self.status.setText(self.session.status_text())
        text = "<br>".join(report["lines"])
        text += f"<p style='color:{color}'>" + "<br><br>".join(report["advice"]) + "</p>"
        self.report.setText(text)
        self.finished_check.emit(self.session.result)

    def reject(self):
        """Closing mid-check stops the recording and throws it away."""
        self._timer.stop()
        self._tick.stop()
        self.session.cancel()
        super().reject()
