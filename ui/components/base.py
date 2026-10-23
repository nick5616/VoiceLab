"""Plumbing shared by the components: theme-following widgets and labels, and painting helpers."""
import qtawesome as qta
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QLabel, QWidget

from ui import theme

TONES = {"primary": "text", "secondary": "text_secondary", "muted": "text_muted"}   # text tone -> token
KINDS = ("info", "success", "warning", "error")                                      # status kinds
KIND_ICONS = {"info": "ph.info", "success": "ph.check-circle", "warning": "ph.warning",
              "error": "ph.x-circle", "neutral": "ph.info"}


def tone_color(tone):
    """A text tone ('primary', 'secondary', 'muted') or any colour token, as a hex string."""
    return theme.current()[TONES.get(tone, tone)]


def mix(a, b, t):
    """Blend QColor a toward b by t (0-1); opaque result."""
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def wash(key, alpha=40):
    """A token at low alpha, for chip and banner backgrounds."""
    return theme.color(key, alpha)


def rgba(color):
    """A QColor as a stylesheet rgba() string."""
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


def icon(name, color=None):
    """A qtawesome icon in a theme colour (token key or hex); defaults to the text colour."""
    hexcolor = theme.current().colors.get(color, color) if color else theme.current()["text"]
    return qta.icon(name, color=hexcolor)


def painter(widget):
    """An antialiased QPainter on the widget."""
    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    return p


def rounded(p, rect, radius, fill=None, border=None, width=1.0):
    """Fill and/or stroke a rounded rectangle; the stroke sits inside the rect."""
    rect = QRectF(rect)
    if border is not None:
        rect = rect.adjusted(width / 2, width / 2, -width / 2, -width / 2)
    p.setPen(QPen(border, width) if border is not None else Qt.PenStyle.NoPen)
    p.setBrush(fill if fill is not None else Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(rect, radius, radius)


def rounded_path(rect, radius):
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), radius, radius)
    return path


def text_width(font, text):
    return QFontMetricsF(font).horizontalAdvance(text)


def draw_icon(p, name, rect, color=None):
    """Paint a qtawesome icon into a rect (QRectF)."""
    icon(name, color).paint(p, QRectF(rect).toAlignedRect())


class ThemedWidget(QWidget):
    """A QWidget that calls restyle() now and again on every theme change."""

    def __init__(self, parent=None):
        super().__init__(parent)
        theme.changed.connect(self._theme_changed)

    def _theme_changed(self, *_):
        self.restyle()
        self.update()

    def restyle(self):
        """Re-read theme tokens; painted widgets read them in paintEvent and need nothing here."""


class Label(QLabel):
    """A plain-text label in a type role and text tone that follows the theme."""

    def __init__(self, text="", role="body", tone="primary", wrap=False, parent=None):
        super().__init__(text, parent)
        self.role, self.tone = role, tone
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(wrap)
        theme.changed.connect(self.restyle)
        self.restyle()

    def set_tone(self, tone):
        self.tone = tone
        self.restyle()

    def set_role(self, role):
        self.role = role
        self.restyle()

    def restyle(self, *_):
        self.setFont(theme.font(self.role))
        self.setStyleSheet(f"color: {tone_color(self.tone)}; background: transparent;")
