"""Moments: MomentChip, a time ("4.2 s" or "3.1–4.0 s") in a take that plays when clicked."""
from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QSizePolicy

from ui import theme
from ui.components.base import ThemedWidget, draw_icon, painter, rounded, text_width

MOMENT_HEIGHT = 26          # px


def moment_text(start, end=None):
    """'4.2 s' for a point, '3.1–4.0 s' for a stretch."""
    if end is None or abs(end - start) < 0.05:
        return f"{start:.1f} s"
    return f"{start:.1f}–{end:.1f} s"


class MomentChip(ThemedWidget):
    """A moment in a take for a report tip; click to play it. Emits play(start, end) (end = start
    for a point). set_playing(True) shows it as playing."""

    play = pyqtSignal(float, float)

    def __init__(self, start, end=None, parent=None):
        super().__init__(parent)
        self.start, self.end = float(start), float(end if end is not None else start)
        self.text = moment_text(self.start, end)
        self.playing = False
        self.hover = False
        self.setFixedHeight(MOMENT_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(f"Play {self.text}")
        self.setAccessibleName(f"Play {self.text}")
        self.restyle()

    def restyle(self):
        self.setFixedWidth(int(text_width(theme.font("numeric", size=12, weight=600), self.text) + 14 + 2 * theme.S + 4))

    def sizeHint(self):
        return QSize(self.width(), MOMENT_HEIGHT)

    def set_playing(self, playing):
        self.playing = bool(playing)
        self.update()

    def mouseReleaseEvent(self, event):
        if self.rect().contains(event.position().toPoint()):
            self.play.emit(self.start, self.end)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.play.emit(self.start, self.end)
        else:
            super().keyPressEvent(event)

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        if self.playing:
            fill, border, ink = t.q("accent"), None, t.q("accent_text")
        else:
            fill = t.q("accent_soft") if self.hover else t.q("surface_raised")
            border, ink = t.q("accent") if self.hasFocus() else t.q("border"), t.q("text")
        rounded(p, self.rect(), MOMENT_HEIGHT / 2, fill=fill, border=border)
        draw_icon(p, "ph.pause-fill" if self.playing else "ph.play-fill",
                  QRectF(theme.S, (MOMENT_HEIGHT - 12) / 2, 12, 12), ink.name() if self.playing else t["accent"])
        p.setFont(theme.font("numeric", size=12, weight=600))
        p.setPen(ink)
        p.drawText(QRectF(theme.S + 16, 0, self.width(), MOMENT_HEIGHT), int(Qt.AlignmentFlag.AlignVCenter), self.text)
