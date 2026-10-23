"""Small indicators: Chip, Badge, ConfidenceChip and StatusDot."""
from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QSizePolicy

from ui import anim, theme
from ui.components.base import KINDS, ThemedWidget, draw_icon, painter, rounded, text_width

CHIP_HEIGHT = 24            # px
BADGE_HEIGHT = 18           # px
CONFIDENCE = {              # level: (bars lit of 3, label, colour token)
    "high": (3, "High confidence", "success"),
    "medium": (2, "Medium confidence", "info"),
    "low": (1, "Low confidence", "warning"),
    "none": (0, "Can't judge", "text_muted"),
}
STATUS = {"idle": "text_muted", "ok": "success", "warn": "warning", "error": "error",
          "busy": "accent", "recording": "recording"}      # StatusDot state -> colour token


def _kind_colors(kind):
    """(background, dot, text) QColors for a chip kind: neutral, accent, a status kind or a zone id."""
    t = theme.current()
    if kind in t.zones:
        z = t.zone(kind)
        return QColor(z["fill"]), QColor(z["edge"]), QColor(z["text"])
    if kind == "accent":
        return t.q("accent_soft"), t.q("accent"), t.q("text")
    if kind in KINDS or kind in ("on_target", "off_target", "recording"):
        return t.q(kind, 36 if t.dark else 30), t.q(kind), t.q("text")
    return t.q("surface_raised"), t.q("text_muted"), t.q("text_secondary")


class Chip(ThemedWidget):
    """A small pill of text: a tag, a zone name or a state. `kind` is neutral, accent, info,
    success, warning, error, on_target, off_target, or a zone id. With `dot`, a coloured dot leads
    (so the meaning never rests on the wash alone); `icon` is a qtawesome name."""

    def __init__(self, text="", kind="neutral", dot=False, icon=None, parent=None):
        super().__init__(parent)
        self.text, self.kind, self.dot, self.icon = text, kind, dot, icon
        self.setFixedHeight(CHIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._resize()

    def set_text(self, text, kind=None):
        self.text = text
        if kind is not None:
            self.kind = kind
        self._resize()
        self.update()

    def set_kind(self, kind):
        self.kind = kind
        self.update()

    def _lead(self):
        return 14 if (self.dot or self.icon) else 0

    def _resize(self):
        self.setFixedWidth(int(text_width(theme.font("caption", weight=600), self.text) + 2 * theme.S + self._lead() + 2))
        self.updateGeometry()

    def restyle(self):
        self._resize()

    def sizeHint(self):
        return QSize(self.width(), CHIP_HEIGHT)

    def paintEvent(self, event):
        bg, dot, ink = _kind_colors(self.kind)
        p = painter(self)
        rounded(p, self.rect(), CHIP_HEIGHT / 2, fill=bg)
        x = theme.S
        if self.icon:
            draw_icon(p, self.icon, QRectF(x - 1, (CHIP_HEIGHT - 14) / 2, 14, 14), dot.name())
            x += self._lead()
        elif self.dot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot)
            p.drawEllipse(QRectF(x, CHIP_HEIGHT / 2 - 4, 8, 8))
            x += self._lead()
        p.setFont(theme.font("caption", weight=600))
        p.setPen(ink)
        p.drawText(QRectF(x, 0, self.width() - x, CHIP_HEIGHT), int(Qt.AlignmentFlag.AlignVCenter), self.text)


class Badge(ThemedWidget):
    """A count or a short word on a small solid pill (nav items, tabs). Empty text hides it."""

    def __init__(self, text="", kind="accent", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedHeight(BADGE_HEIGHT)
        self.set_text(text)

    def set_text(self, text):
        self.text = "" if text is None else str(text)
        self.setVisible(bool(self.text))
        w = text_width(theme.font("caption", size=11, weight=700), self.text) + 10
        self.setFixedWidth(int(max(BADGE_HEIGHT, w)))
        self.update()

    def paintEvent(self, event):
        paint_badge(painter(self), QRectF(self.rect()), self.text, self.kind)


def paint_badge(p, rect, text, kind="accent"):
    """Draw a badge pill into rect (shared with NavRail and Tabs)."""
    t = theme.current()
    fill = t.q("accent") if kind == "accent" else t.q(kind) if kind in KINDS else t.q("text_secondary")
    ink = t.q("accent_text") if kind == "accent" else t.q("bg" if t.dark else "surface_raised")   # >= 4.5:1
    rounded(p, rect, rect.height() / 2, fill=fill)
    p.setFont(theme.font("caption", size=11, weight=700))
    p.setPen(ink)
    p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)


class ConfidenceChip(ThemedWidget):
    """How far a reading can be trusted: high / medium / low / none. Shown as 0-3 lit bars plus a
    word, so it never relies on colour; the reason is in the tooltip."""

    def __init__(self, level="none", reason=None, compact=False, parent=None):
        super().__init__(parent)
        self.compact = compact
        self.setFixedHeight(CHIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_level(level, reason)

    def set_level(self, level, reason=None):
        if level not in CONFIDENCE:
            raise ValueError(f"confidence must be one of {tuple(CONFIDENCE)}")
        self.level, self.reason = level, reason
        label = CONFIDENCE[level][1]
        self.label = label.split()[0] if self.compact and level != "none" else label
        self.setToolTip(f"{label}. {reason}" if reason else label)
        self.setAccessibleName(label)
        self.setFixedWidth(int(text_width(theme.font("caption", weight=600), self.label) + 2 * theme.S + 20))
        self.update()

    def paintEvent(self, event):
        t = theme.current()
        lit, _, key = CONFIDENCE[self.level]
        p = painter(self)
        rounded(p, self.rect(), CHIP_HEIGHT / 2, fill=t.q("surface_raised"), border=t.q("border"))
        x = theme.S + 1
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):                  # signal-strength bars, rising
            h = 5 + 3 * i
            p.setBrush(t.q(key) if i < lit else t.q("border_strong", 110))
            p.drawRoundedRect(QRectF(x + i * 4.5, CHIP_HEIGHT / 2 + 6 - h, 3, h), 1, 1)
        p.setFont(theme.font("caption", weight=600))
        p.setPen(t.q("text_secondary") if lit else t.q("text_muted"))
        p.drawText(QRectF(x + 18, 0, self.width() - x - 18, CHIP_HEIGHT), int(Qt.AlignmentFlag.AlignVCenter),
                   self.label)


class StatusDot(ThemedWidget):
    """A small coloured dot with an optional label: idle, ok, warn, error, busy (breathes) or recording."""

    def __init__(self, state="idle", label=None, parent=None):
        super().__init__(parent)
        self.pulse_phase = 0.0
        self.label = label
        self.setFixedHeight(20)
        self.set_state(state, label)

    def set_state(self, state, label=None):
        if state not in STATUS:
            raise ValueError(f"state must be one of {tuple(STATUS)}")
        self.state = state
        if label is not None:
            self.label = label
        w = 14 + (text_width(theme.font("caption"), self.label) + theme.XS if self.label else 0)
        self.setFixedWidth(int(w) + 2)
        if state in ("busy", "recording") and self.isVisible():
            anim.pulse(self)
        else:
            anim.stop(self, "pulse_phase")
            self.pulse_phase = 0.0
        self.update()

    def showEvent(self, event):
        if self.state in ("busy", "recording"):
            anim.pulse(self)

    def hideEvent(self, event):
        anim.stop(self, "pulse_phase")

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        c = t.q(STATUS[self.state])
        cy = self.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        if self.pulse_phase:
            halo = QColor(c)
            halo.setAlpha(int(110 * (1 - self.pulse_phase)))
            p.setBrush(halo)
            r = 4 + 3 * self.pulse_phase
            p.drawEllipse(QRectF(7 - r, cy - r, 2 * r, 2 * r))
        p.setBrush(c)
        p.drawEllipse(QRectF(3, cy - 4, 8, 8))
        if self.label:
            p.setFont(theme.font("caption"))
            p.setPen(t.q("text_secondary"))
            p.drawText(QRectF(14 + theme.XS, 0, self.width(), self.height()), int(Qt.AlignmentFlag.AlignVCenter),
                       self.label)
