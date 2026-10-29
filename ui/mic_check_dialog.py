"""The mic setup: pick the mic, watch its level while you say "ah", then a plain checklist of what to fix and a
test-again that says what changed. Drives mic_check.MicCheckSession with timers."""
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout

import mic_check
import shell_state
from mic_check import INTRO, LEAD_SECONDS, PHASE_SECONDS, MicCheckSession
from ui import theme
from ui.components import Button, Caption, Headline, Label, LevelMeter, ProgressStrip, Select

METER_MS = 120              # ms between looks at the live level


class MicCheckDialog(QDialog):
    """Guides a 3 s silence + 3 s "ah" recording and shows what it found. `result` holds the dict
    afterwards and finished_check carries it. With `core` it also offers the mic to pick. Nothing here opens
    an audio device."""

    finished_check = pyqtSignal(dict)

    def __init__(self, engine, parent=None, seconds=PHASE_SECONDS, lead_seconds=LEAD_SECONDS, core=None):
        super().__init__(parent)
        self.engine = engine
        self.core = core
        self.session = MicCheckSession(engine, seconds, lead_seconds)
        self.previous = core.profile.mic_check if core is not None else None     # what a retest compares with
        self.setWindowTitle("Mic check")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.XL, theme.XL, theme.XL, theme.L)
        layout.setSpacing(theme.M)
        layout.addWidget(Headline("Mic check", level="title"))
        self.mic_choice = None
        if core is not None:
            row = QHBoxLayout()
            row.setSpacing(theme.M)
            row.addWidget(Label("Microphone", role="label", tone="secondary"))
            self.mic_choice = Select([(name or "", label) for label, name in core.input_devices()])
            self.mic_choice.setCurrentIndex(max(0, self.mic_choice.findData(core.profile.input_device or "")))
            self.mic_choice.currentIndexChanged.connect(self._mic_picked)
            row.addWidget(self.mic_choice, 1)
            layout.addLayout(row)
        self.instruction = Label(INTRO, role="body", tone="secondary", wrap=True)
        self.meter = LevelMeter()
        self.status = Label("", role="heading")
        self.strip = ProgressStrip()
        self.strip.hide()
        self.report = Label("", role="body", wrap=True)
        self.report.setTextFormat(Qt.TextFormat.RichText)
        self.changes = Label("", role="body", tone="secondary", wrap=True)
        self.capture = Caption("")
        layout.addWidget(self.instruction)
        layout.addWidget(self.meter)
        layout.addWidget(self.status)
        layout.addWidget(self.strip)
        layout.addWidget(self.report)
        layout.addWidget(self.changes)
        layout.addWidget(self.capture)
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
        self._level = QTimer(self)
        self._level.setInterval(METER_MS)
        self._level.timeout.connect(self._show_level)
        self._level.start()
        self._total = 2 * seconds + lead_seconds
        self._done = 0.0
        self._phase_seconds = 0.0
        self._show_capture()

    # The session's settings and state, under the names the dialog always had.
    seconds = property(lambda self: self.session.seconds, lambda self, v: setattr(self.session, "seconds", v))
    lead_seconds = property(lambda self: self.session.lead_seconds,
                            lambda self, v: setattr(self.session, "lead_seconds", v))
    phase = property(lambda self: self.session.phase)
    result = property(lambda self: self.session.result)

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), theme.color("surface"))

    def _mic_picked(self, *_):
        """Another mic: open it, so the level and the check are for that one."""
        self.core.open_mic(self.mic_choice.currentData() or None, self.core.profile.egg_channel)
        self._show_capture()

    def _show_capture(self):
        note = mic_check.capture_note(getattr(self.engine, "capture_info", None))
        self.capture.setText(note or "")
        self.capture.setVisible(bool(note))

    def _show_level(self):
        """The live level of the mic, so you can see it hears you before the check starts."""
        recent = getattr(self.engine, "recent_audio", None)
        if recent is None:
            return
        db = mic_check.level_dbfs(recent(mic_check.LEVEL_WINDOW_SECONDS))
        self.meter.set_level(shell_state.mic_level_fraction(db))

    def start(self):
        """Begin the check (refused while a take is being recorded)."""
        seconds = self.session.start()
        if seconds is None:
            self.status.setText(self.session.status_text())
            return
        self.report.setText("")
        self.changes.setText("")
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
        found = mic_check.findings(self.session.result)
        if found:
            text += "<p><b>What to fix</b></p>" + "".join(
                f"<p style='color:{color}'><b>{item['issue']}</b><br>{item['fix']}</p>" for item in found)
        else:
            text += f"<p style='color:{color}'>" + "<br><br>".join(report["advice"]) + "</p>"
        self.report.setText(text)
        lines = mic_check.compare(self.previous, self.session.result)
        self.changes.setText("Compared with your last test:\n" + "\n".join(lines) if lines else "")
        self.previous = self.session.result
        self.finished_check.emit(self.session.result)

    def reject(self):
        """Closing mid-check stops the recording and throws it away."""
        self._timer.stop()
        self._tick.stop()
        self._level.stop()
        self.session.cancel()
        super().reject()
