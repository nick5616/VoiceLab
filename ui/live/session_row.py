"""The session row under the live graph: how long the voice has held in range, the share of time in range
(last minute, whole session, against the first minutes), and a strip of the last few minutes. With no target
set it says so and offers to set one. live_session decides every word and number (snapshot["session"])."""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from ui import theme
from ui.components import Button, Caption, Label, Sparkline
from ui.components.controls import SMALL_HEIGHT

STRIP_WIDTH = 150               # px: the trend strip
STRIP_HEIGHT = 28               # px
STRIP_HEIGHT_COMPACT = 20       # px: on a short window
TREND_TIPS = {"in_range": "Each point is 15 seconds: the share of your voiced time inside your target.",
              "pitch": "Each point is 15 seconds: your median pitch. Set a pitch target to see time in range instead."}
TREND_LABELS = {"in_range": "in range, last 5 min", "pitch": "pitch, last 5 min"}


class SessionRow(QWidget):
    """One thin row: the line, the strip and (without a target) a "Set target" button."""

    targets_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(STRIP_HEIGHT)        # the same with or without the strip, so nothing below it jumps
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.M)
        self.line = Label("", role="body", tone="secondary")
        self.line.setToolTip("Time in range counts only the moments you are speaking, against your pitch and "
                             "resonance targets.")
        self.set_btn = Button("Set target", variant="ghost", small=True)
        self.set_btn.clicked.connect(self.targets_requested.emit)
        self.trend_label = Caption("", tone="muted")
        self.spark = Sparkline(height=STRIP_HEIGHT)
        self.spark.setFixedWidth(STRIP_WIDTH)
        row.addWidget(self.line)
        row.addWidget(self.set_btn)
        row.addStretch(1)
        row.addWidget(self.trend_label)
        row.addWidget(self.spark)
        self.shown = None
        self.compact = False
        self.has_target = True

    def _fit(self):
        """The row's height: flatter on a short window, but tall enough for the button when it shows."""
        height = STRIP_HEIGHT_COMPACT if self.compact else STRIP_HEIGHT
        self.spark.setFixedHeight(height - 2)
        self.setFixedHeight(height if self.has_target else max(height, SMALL_HEIGHT))

    def set_compact(self, compact):
        """The short-window row: the same content, a little flatter."""
        self.compact = bool(compact)
        self._fit()

    def show_session(self, session):
        """Draw snapshot["session"]; nothing is repainted while it has not changed."""
        trend = session["trend"]
        key = (session["line"], session["has_target"], trend["kind"], tuple(trend["values"]))
        if key == self.shown:
            return
        self.shown = key
        self.line.setText(session["line"])
        self.set_btn.setVisible(not session["has_target"])
        self.has_target = session["has_target"]
        self._fit()
        kind = trend["kind"]
        visible = kind is not None and any(v is not None for v in trend["values"])
        self.spark.setVisible(visible)
        self.trend_label.setVisible(visible)
        if visible:
            self.spark.set_values(trend["values"])
            self.spark.setToolTip(TREND_TIPS[kind])
            self.trend_label.setText(TREND_LABELS[kind])
