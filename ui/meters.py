"""Meter widgets (RangeBar, ShareBar, MetricBar) and the colours they draw with. They draw readings the
core made (display.metric_reading); nothing here judges a value."""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QBrush, QPainter, QColor, QPen

from display import METRIC_DEFS
from ui.widgets import ElidedLabel

METER_MIN_WIDTH = 300           # meters and take bars go two-up when this fits twice

NEUTRAL_COLOR = "#7f93a8"       # where a value sits on its bar: no good or bad implied
ON_TARGET_COLOR = "#2ecc71"
OFF_TARGET_COLOR = "#e67e22"
DISABLED_COLOR = "#555"
TARGET_BAND_COLOR = QColor(46, 204, 113, 70)
MIX_BAND_COLOR = QColor(155, 89, 182, 70)
MIX_EDGE_COLOR = "#9b59b6"
# Register colours (chest = M1, head = M2), shared by the take card's bar and graph.
REGISTER_COLORS = {"chest": "#3498db", "mix": "#9b59b6", "head": "#e74c3c", "fry": "#95a5a6"}
BUTTON_STYLE = "background-color: #3a3a3a; color: white; padding: 5px 10px; border-radius: 5px;"
# Status levels of the core's "status" event, as text colours.
STATUS_COLORS = {"info": "#888", "success": "#2ecc71", "warning": "#e67e22", "error": "#e74c3c",
                 "busy": "#f1c40f", "recording": "#e74c3c"}


def status_style(level):
    """Style sheet of a status line at this level."""
    color = STATUS_COLORS.get(level, "#888")
    return f"color: {color}; font-weight: bold;" if level == "recording" else f"color: {color};"


def fix_text_height(label, sample):
    """Pin a label's height to what `sample` needs, so changing text never
    changes its size."""
    text = label.text()
    label.ensurePolished()
    label.setText(sample)
    label.setFixedHeight(label.sizeHint().height())
    label.setText(text)


def bar_color(position, on_target):
    """Green only for on target, amber only for off target, otherwise a neutral hue."""
    if on_target is not None:
        return ON_TARGET_COLOR if on_target else OFF_TARGET_COLOR
    return NEUTRAL_COLOR


def reading_color(reading):
    """The fill colour of a reading's bar: its verdict, else its zone (the register's), else neutral."""
    if reading["position"] is None:
        return DISABLED_COLOR
    if reading.get("zone") and reading["verdict"] is None:
        return REGISTER_COLORS[reading["zone"]]
    return bar_color(reading["position"], reading["verdict"])


class RangeBar(QWidget):
    """Track with a value fill, optional green target band and baseline tick.
    Positions are fractions 0-1 of the track. A centered bar fills from the
    middle, for deviations such as tuning."""

    def __init__(self, centered=False):
        super().__init__()
        self.setFixedHeight(12)
        self.centered = centered
        self.position = None
        self.color = DISABLED_COLOR
        self.band = None
        self.band_color = (TARGET_BAND_COLOR, ON_TARGET_COLOR)     # fill, edge
        self.baseline = None
        self.marker = None          # fraction 0-1: the raw reading, a thin line beside the smoothed fill
        self.hatched = False        # no trustworthy reading: the track is hatched

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track = QRectF(0, 2, w, h - 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#444"))
        p.drawRoundedRect(track, 4, 4)
        if self.hatched:
            p.setBrush(QBrush(QColor("#888"), Qt.BrushStyle.BDiagPattern))
            p.drawRoundedRect(track, 4, 4)
        if self.position is not None and self.centered:
            p.setBrush(QColor(self.color))
            x0, x1 = sorted((w * 0.5, w * self.position))
            p.drawRoundedRect(QRectF(x0, 2, max(4.0, x1 - x0), h - 4), 3, 3)
        elif self.position is not None:
            p.setBrush(QColor(self.color))
            p.drawRoundedRect(QRectF(0, 2, max(track.height(), w * self.position), h - 4), 4, 4)
        if self.band is not None:
            lo, hi = self.band
            p.setBrush(self.band_color[0])
            p.setPen(QPen(QColor(self.band_color[1]), 1))
            p.drawRect(QRectF(w * lo, 0, max(2.0, w * (hi - lo)), h - 1))
        if self.marker is not None:
            p.setPen(QPen(QColor(255, 255, 255, 200), 1))
            x = int(w * self.marker)
            p.drawLine(x, 1, x, h - 2)
        if self.baseline is not None:
            p.setPen(QPen(QColor("white"), 2))
            x = w * self.baseline
            p.drawLine(int(x), 0, int(x), h)


class ShareBar(QWidget):
    """A thin bar split into coloured segments, e.g. the share of a take in each register."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(12)
        self.segments = []              # (fraction, colour)

    def set_segments(self, segments):
        self.segments = segments
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#444"))
        p.drawRoundedRect(QRectF(0, 2, w, h - 4), 4, 4)
        x = 0.0
        for fraction, color in self.segments:
            p.setBrush(QColor(color))
            p.drawRect(QRectF(x, 2, w * fraction, h - 4))
            x += w * fraction


class MetricBar(QWidget):
    """Labelled RangeBar for one metric. show_reading() draws a reading from the core."""

    def __init__(self, key, centered=False, live=False):
        super().__init__()
        self.key = key
        self.label, _, _, low_label, high_label, _, _ = METRIC_DEFS[key]
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 4, 0, 4)

        self.title = ElidedLabel(self.label) if live else QLabel(self.label)
        self.title.setStyleSheet("font-size: 11px; color: #aaa;")
        if live:
            # One line that never changes the meter's size: live text changes
            # every frame, and resizing would shake the whole window. The height
            # is measured with the symbols used (≈ ✓ ¢ come from a taller
            # fallback font) so it doesn't change with the text either.
            self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            fix_text_height(self.title, "Hz ≈ ✓ ¢ ± — ·")
        else:
            self.title.setWordWrap(True)   # wraps rather than widening the card
        vbox.addWidget(self.title)

        bar_row = QHBoxLayout()
        self.bar = RangeBar(centered)
        low, high = QLabel(low_label), QLabel(high_label)
        for lbl in (low, high):
            lbl.setStyleSheet("color: #ddd; font-size: 11px;")
            lbl.setFixedWidth(72)
        high.setAlignment(Qt.AlignmentFlag.AlignRight)
        bar_row.addWidget(low)
        bar_row.addWidget(self.bar)
        bar_row.addWidget(high)
        vbox.addLayout(bar_row)

    def show_reading(self, reading):
        """Draw a reading (display.metric_reading): its title, bar position and colour, band and baseline."""
        self.label = reading["label"]
        if self.title.text() != reading["title"]:
            self.title.setText(reading["title"])
        bar = self.bar
        bar.position, bar.color = reading["position"], reading_color(reading)
        bar.hatched = reading["position"] is None and reading["unavailable"] is not None
        bar.band = reading["band"]
        bar.band_color = (MIX_BAND_COLOR, MIX_EDGE_COLOR) if reading.get("band_kind") == "mix" \
            else (TARGET_BAND_COLOR, ON_TARGET_COLOR)
        bar.baseline, bar.marker = reading["baseline"], reading.get("marker")
        bar.update()
