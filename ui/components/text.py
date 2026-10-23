"""Text components: Headline, Caption, HelpTip and ElidedLabel."""
from PyQt6.QtCore import QPoint, QRectF, QSize, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QSizePolicy, QToolTip

from ui import theme
from ui.components.base import Label, ThemedWidget, painter, rounded, tone_color

HEADLINE_ROLES = ("display", "title", "heading")
HELP_SIZE = 18              # px: the "?" circle; a 24 px hit area around it


class Headline(Label):
    """A big plain sentence ("Your pitch sits in the overlap zone"). Wraps; never elides."""

    def __init__(self, text="", level="title", tone="primary", parent=None):
        if level not in HEADLINE_ROLES:
            raise ValueError(f"level must be one of {HEADLINE_ROLES}")
        super().__init__(text, role=level, tone=tone, wrap=True, parent=parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)


class Caption(Label):
    """Small secondary text: units, sources, timestamps. Muted by default."""

    def __init__(self, text="", tone="muted", wrap=True, parent=None):
        super().__init__(text, role="caption", tone=tone, wrap=wrap, parent=parent)


class HelpTip(ThemedWidget):
    """A small "?" that explains something in plain words, on hover or click."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self.hover = False
        self.setToolTip(text)
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName("Help")
        self.setAccessibleDescription(text)

    def set_text(self, text):
        self.text = text
        self.setToolTip(text)
        self.setAccessibleDescription(text)

    def sizeHint(self):
        return QSize(24, 24)

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def mousePressEvent(self, event):
        QToolTip.showText(self.mapToGlobal(QPoint(0, self.height())), self.text, self)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.mousePressEvent(event)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        r = QRectF((self.width() - HELP_SIZE) / 2, (self.height() - HELP_SIZE) / 2, HELP_SIZE, HELP_SIZE)
        ink = t.q("text") if self.hover or self.hasFocus() else t.q("text_muted")
        rounded(p, r, HELP_SIZE / 2, fill=t.q("surface_raised") if self.hover else None, border=ink, width=1.2)
        p.setFont(theme.font("caption", size=12, weight=600))
        p.setPen(ink)
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), "?")


class ElidedLabel(Label):
    """One line that ends in "…" where it doesn't fit, instead of widening its parent. The full
    text is in the tooltip whenever it's cut."""

    def __init__(self, text="", role="body", tone="primary", parent=None):
        super().__init__(text, role=role, tone=tone, wrap=False, parent=parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)

    def elided(self):
        """The text as drawn at the current width."""
        return self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, max(0, self.width()))

    def setText(self, text):
        super().setText(text)
        self._sync_tooltip()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_tooltip()

    def _sync_tooltip(self):
        self.setToolTip(self.text() if self.elided() != self.text() else "")

    def paintEvent(self, event):
        p = painter(self)
        p.setFont(self.font())
        p.setPen(QColor(tone_color(self.tone)))
        p.drawText(self.rect(), int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), self.elided())
