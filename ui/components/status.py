"""State indicators for the frame (phase 11 / 05): MicIndicator (the mic's name, state words and a level
meter), RecordingPill ("REC 0:07"), RecordingLine (a red line along the top of the window) and FitLabel (a
status line that shows a whole phrase or none, never a cut one)."""
import math
import time

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ui import theme
from ui.components.base import Label, ThemedWidget, painter, rounded, text_width
from ui.components.chips import STATUS, StatusDot

METER_WIDTH = 40            # px: the mic level meter
METER_HEIGHT = 6            # px
METER_BARS = 8              # segments in the meter
NAME_MAX = 240              # px: a very long mic name is cut here (the tooltip has the whole name)
GAP = 8                     # px between the dot, the name, the words and the meter
LINE_HEIGHT = 3             # px: the red recording line
PILL_HEIGHT = 24            # px


class MicIndicator(StatusDot):
    """The top bar's mic block: a state dot, the short device name, the state in words ("Hearing you") and a
    small level meter that moves with the voice. Colour is never alone: the words say it too. `state` is a
    StatusDot state (ok, warn, error, idle, recording); `key` the shell_state.MIC_STATES key."""

    def __init__(self, parent=None):
        self.name, self.words, self.key, self.level = "", "", "listening", 0.0
        super().__init__("ok", "Mic", parent)
        self.setFixedHeight(28)

    def set_mic(self, name, words, state, key, tooltip=None, full_name=None):
        """Show a device name and a state; the tooltip says what it means and which device it is."""
        self.name, self.words, self.key = name, words, key
        if key in ("unavailable", "lost"):
            self.level = 0.0
        self.setToolTip(tooltip or "")
        self.setAccessibleName(f"Microphone {full_name or name}: {words}")
        self.set_state(state, f"{name} · {words}")

    def set_level(self, fraction):
        """The meter's fill, 0-1 (0 in silence or without a mic)."""
        fraction = 0.0 if self.key in ("unavailable", "lost") else max(0.0, min(1.0, float(fraction)))
        if abs(fraction - self.level) > 0.02:
            self.level = fraction
            self.update()

    def _name_width(self):
        return min(NAME_MAX, math.ceil(text_width(theme.font("label"), self.name)) + 3)

    def set_state(self, state, label=None):
        if state not in STATUS:
            raise ValueError(f"state must be one of {tuple(STATUS)}")
        self.state = state
        if label is not None:
            self.label = label
        self.setFixedWidth(self._width())
        self.update()

    def _width(self):
        words = text_width(theme.font("label", weight=600), self.words)
        return int(14 + GAP + self._name_width() + GAP + words + GAP + METER_WIDTH + 4)

    def showEvent(self, event):
        pass

    def restyle(self):
        self.setFixedWidth(self._width())

    def sizeHint(self):
        return QSize(self._width(), 28)

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        colour = t.q(STATUS[self.state])
        cy = self.height() / 2
        # the dot; a ring around it while the voice is heard, so "hearing" differs from "listening" without colour
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawEllipse(QRectF(3, cy - 4, 8, 8))
        if self.key in ("hearing", "recording"):
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(colour, 1.5))
            p.drawEllipse(QRectF(1, cy - 6, 12, 12))
        elif self.key in ("lost", "unavailable"):
            p.setPen(QPen(QColor(t["surface"]), 1.8))
            p.drawLine(int(5.5), int(cy + 2.5), int(8.5), int(cy - 2.5))      # a slash through the dot: no signal
        x = 14 + GAP
        font = theme.font("label")
        p.setFont(font)
        p.setPen(t.q("text"))
        room = math.ceil(self._name_width()) + 3
        name = p.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, room)
        p.drawText(QRectF(x, 0, room, self.height()), int(Qt.AlignmentFlag.AlignVCenter), name)
        x += self._name_width() + GAP
        p.setFont(theme.font("label", weight=600))
        p.setPen(t.q("text_secondary" if self.key == "listening" else "text"))
        words_w = text_width(theme.font("label", weight=600), self.words)
        p.drawText(QRectF(x, 0, words_w + 2, self.height()), int(Qt.AlignmentFlag.AlignVCenter), self.words)
        x += words_w + GAP
        self._paint_meter(p, t, x, cy, colour)

    def _paint_meter(self, p, t, x, cy, colour):
        """METER_BARS rising segments; lit ones follow the level, the rest stay faint so silence is visible."""
        lit = round(self.level * METER_BARS)
        step = METER_WIDTH / METER_BARS
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(METER_BARS):
            h = METER_HEIGHT + 8 * (i + 1) / METER_BARS
            on = i < lit
            p.setBrush(colour if on else t.q("border_strong", 140))
            p.drawRoundedRect(QRectF(x + i * step, cy + 7 - h, step - 2, h), 1, 1)


class RecordingPill(ThemedWidget):
    """A red pill "REC 0:07" for the top bar while a take is recorded. Shown only then."""

    def __init__(self, clock=time.monotonic, parent=None):
        super().__init__(parent)
        self.clock = clock
        self.started = None
        self.elapsed = 0.0
        self.setFixedHeight(PILL_HEIGHT)
        self.setFixedWidth(84)
        self.setAccessibleName("Recording")
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.hide()

    def start(self, elapsed=0.0):
        self.started = self.clock() - elapsed
        self.elapsed = elapsed
        self.timer.start()
        self.setAccessibleName("Recording " + self.text())
        self.show()
        self.update()

    def stop(self):
        self.timer.stop()
        self.started = None
        self.hide()

    def _tick(self):
        if self.started is not None:
            self.elapsed = self.clock() - self.started
            self.update()

    def text(self):
        s = int(self.elapsed)
        return f"REC {s // 60}:{s % 60:02d}"

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        rounded(p, QRectF(self.rect()), PILL_HEIGHT / 2, fill=t.q("recording"))
        p.setFont(theme.font("numeric", size=12, weight=700))
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(self.rect()), int(Qt.AlignmentFlag.AlignCenter), self.text())


class RecordingLine(QWidget):
    """A strip along the top edge of the window that is red while recording: seen from across the room. It
    always takes its few pixels in the layout (clear when idle), so recording never moves anything."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.on = False
        self.setFixedHeight(LINE_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Recording indicator")

    def set_on(self, on):
        self.on = bool(on)
        self.update()

    def paintEvent(self, event):
        if self.on:
            painter(self).fillRect(self.rect(), theme.current().q("recording"))


class FitLabel(Label):
    """A line of status text that shows the first of its alternatives that fits, or nothing: never half a
    sentence. The full text is in the tooltip when a shorter one (or none) is shown."""

    def __init__(self, text="", role="caption", tone="muted", parent=None):
        super().__init__(text, role=role, tone=tone, wrap=False, parent=parent)
        self.alternatives = [text] if text else []
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def set_alternatives(self, *texts):
        """Full text first, then shorter ones."""
        self.alternatives = [t for t in texts if t]
        self._fit()

    def setText(self, text):
        self.alternatives = [text] if text else []
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        full = self.alternatives[0] if self.alternatives else ""
        room = max(0, self.width())
        shown = next((t for t in self.alternatives if self.fontMetrics().horizontalAdvance(t) <= room), "")
        if room <= 1 and self.alternatives:
            shown = self.alternatives[-1]     # not laid out yet
        super().setText(shown)
        self.setToolTip(full if shown != full else "")


class LevelMeter(ThemedWidget):
    """A wide level meter for the mic check: rising segments that follow the voice, with the word "quiet" at
    the left end and "loud" at the right, so the bar reads without numbers."""

    SEGMENTS = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.level = 0.0
        self.setFixedHeight(30)
        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Input level")

    def set_level(self, fraction):
        fraction = max(0.0, min(1.0, float(fraction)))
        if abs(fraction - self.level) > 0.01:
            self.level = fraction
            self.setAccessibleDescription(f"{round(100 * fraction)} percent")
            self.update()

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        lit = round(self.level * self.SEGMENTS)
        step = self.width() / self.SEGMENTS
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.SEGMENTS):
            if i < lit:
                p.setBrush(t.q("success" if i < self.SEGMENTS * 0.8 else "warning"))
            else:
                p.setBrush(t.q("border_strong", 140))
            p.drawRoundedRect(QRectF(i * step + 1, 2, step - 3, self.height() - 4), 2, 2)
