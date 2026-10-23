"""The mic check dialog: drives mic_check.MicCheckSession with timers and shows what it found."""
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from mic_check import INTRO, LEAD_SECONDS, PHASE_SECONDS, MicCheckSession
from ui.meters import BUTTON_STYLE, OFF_TARGET_COLOR, ON_TARGET_COLOR


class MicCheckDialog(QDialog):
    """Guides a 3 s silence + 3 s "ah" recording and shows what it found. `result` holds the dict
    afterwards and finished_check carries it. Nothing here opens an audio device."""

    finished_check = pyqtSignal(dict)

    def __init__(self, engine, parent=None, seconds=PHASE_SECONDS, lead_seconds=LEAD_SECONDS):
        super().__init__(parent)
        self.session = MicCheckSession(engine, seconds, lead_seconds)
        self.setWindowTitle("Mic check")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        self.instruction = QLabel(INTRO)
        self.instruction.setWordWrap(True)
        self.status = QLabel("")
        self.status.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.report = QLabel("")
        self.report.setWordWrap(True)
        layout.addWidget(self.instruction)
        layout.addWidget(self.status)
        layout.addWidget(self.report)
        row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.close_btn = QPushButton("Close")
        for btn in (self.start_btn, self.close_btn):
            btn.setStyleSheet(BUTTON_STYLE)
        self.start_btn.clicked.connect(self.start)
        self.close_btn.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(self.start_btn)
        row.addWidget(self.close_btn)
        layout.addLayout(row)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next)
        self._tick = QTimer(self)
        self._tick.setInterval(100)
        self._tick.timeout.connect(self._show_countdown)

    # The session's settings and state, under the names the dialog always had.
    seconds = property(lambda self: self.session.seconds, lambda self, v: setattr(self.session, "seconds", v))
    lead_seconds = property(lambda self: self.session.lead_seconds,
                            lambda self, v: setattr(self.session, "lead_seconds", v))
    phase = property(lambda self: self.session.phase)
    result = property(lambda self: self.session.result)

    def start(self):
        """Begin the check (refused while a take is being recorded)."""
        seconds = self.session.start()
        if seconds is None:
            self.status.setText(self.session.status_text())
            return
        self.report.setText("")
        self.start_btn.setEnabled(False)
        self._wait(seconds)

    def _wait(self, seconds):
        self._timer.start(int(seconds * 1000))
        self._tick.start()
        self._show_countdown()

    def _show_countdown(self):
        self.status.setText(self.session.status_text())

    def _next(self):
        seconds = self.session.next()
        if seconds is not None:
            self._wait(seconds)
            return
        self._tick.stop()
        self._finish()

    def _finish(self):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Run again")
        report = self.session.report()
        color = ON_TARGET_COLOR if report["ok"] else OFF_TARGET_COLOR
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
