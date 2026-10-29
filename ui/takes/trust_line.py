"""The take's one statement of trust (take_view.trust_line): a dot for the recording's grade, one or two
sentences (the verdict, the readings it touches with the cause, what to try) and, when the mic or the room
is the cause, a Mic check button. It replaces the badge, the confidence chips and the banner that used to
disagree. A reading's own chip (reading_chip) only shows when that reading isn't reliable."""
from PyQt6.QtCore import QRectF, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy

import take_view
from ui import theme
from ui.components import Button, Chip, Label, StatusDot
from ui.components.base import ThemedWidget, draw_icon, painter, rounded

TRUST_MIN_HEIGHT = 40           # px


class TrustLine(ThemedWidget):
    """One line of trust for a take. mic_check() from the action. `view` is take_view.trust_line; compact
    says only what is wrong, with no fix and no action (the full report has them)."""

    mic_check = pyqtSignal()

    def __init__(self, view, compact=False, parent=None):
        super().__init__(parent)
        self.view = view
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.M, theme.S, theme.S, theme.S)
        row.setSpacing(theme.M)
        self.dot = StatusDot(take_view.TIER_DOT[view["tier"]])
        self.dot.setToolTip(view["verdict"])
        row.addWidget(self.dot)
        self.label = Label(view.get("short", view["text"]) if compact else view["text"], role="body", tone="secondary" if view["clean"] else "primary", wrap=True)
        row.addWidget(self.label, 1)
        self.action = None
        if view["action"] and not compact:
            self.action = Button(view["action"], "secondary", small=True)
            self.action.clicked.connect(self.mic_check.emit)
            row.addWidget(self.action)
        self.setAccessibleName(view["text"])
        self.setMinimumHeight(TRUST_MIN_HEIGHT)

    def resizeEvent(self, event):
        """Keep the height its wrapped text needs, whatever column it sits in."""
        super().resizeEvent(event)
        need = max(self.heightForWidth(self.width()), TRUST_MIN_HEIGHT)
        if need != self.minimumHeight():
            self.setMinimumHeight(need)

    def paintEvent(self, event):
        t = theme.current()
        rounded(painter(self), self.rect(), theme.RADIUS["medium"], fill=t.q("surface_raised"), border=t.q("border"))


def reading_chip(dim):
    """The chip for one reading's trust (Rough, Rough guide, Can't judge), or None for a reliable one.
    The reason is its tooltip."""
    view = take_view.reading_trust(dim)
    if not view["show"]:
        return None
    chip = Chip(view["word"], kind=view["kind"], dot=True)
    chip.setToolTip(view["reason"] or view["word"])
    chip.setAccessibleName(f"{view['word']}: {view['reason'] or ''}".strip(": "))
    return chip


class NoteLine(ThemedWidget):
    """A quiet line with an icon, for what a card is doing or just did ("Updating this take…",
    "Updated…"). Not a banner: nothing to act on."""

    def __init__(self, text, icon="ph.arrows-clockwise", kind="accent", parent=None):
        super().__init__(parent)
        self.icon_name, self.kind = icon, kind
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.M + 26, theme.S, theme.M, theme.S)
        self.label = Label(text, role="body", tone="secondary", wrap=True)
        row.addWidget(self.label, 1)
        self.setAccessibleName(text)

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        rounded(p, self.rect(), theme.RADIUS["medium"], fill=t.q(self.kind, 26), border=t.q(self.kind, 80))
        draw_icon(p, self.icon_name, QRectF(theme.M, (self.height() - 16) / 2, 16, 16), self.kind)
