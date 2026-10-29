"""The player strip under a take's headline (audit A-019): Play, the time, and the pitch track as a thin
graph with the tips' moments marked on it. Click a marked moment to hear it, click elsewhere to start
from there; the playhead moves while it plays. The full graph with formants is in the card's details."""
import math

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout

import take_view
from ui import theme
from ui.components import Button, Label
from ui.components.base import ThemedWidget, painter, rounded

STRIP_HEIGHT = 64               # px: the pitch graph
MOMENT_ALPHA = {"dark": 70, "light": 90}    # the marked moments are a wash over the line
PAD_ST = 3                      # semitones of headroom above and below the pitch line


def _pen(color, width=1.5, style=Qt.PenStyle.SolidLine):
    return pg.mkPen(QColor(color), width=width, style=style)


def clock(seconds):
    """'0:07' for 7 s."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class PlayerStrip(ThemedWidget):
    """Play, the clock and the marked pitch track of one take. play_clicked() from the button;
    seek_requested(seconds) from a click on the graph; moment_clicked(start, end) from a click on a
    marked moment. set_playhead(seconds or None), set_playing(bool), set_overlay(tracks or None)."""

    play_clicked = pyqtSignal()
    seek_requested = pyqtSignal(float)
    moment_clicked = pyqtSignal(float, float)

    def __init__(self, take, moments=(), parent=None):
        super().__init__(parent)
        self.tracks = take["tracks"]
        self.moments = [(float(a), float(b)) for a, b in moments]
        self.playhead = None
        self.overlay_tracks = None
        self.times = np.asarray(self.tracks["times"], dtype=float)
        self.length = float(self.times[-1]) if len(self.times) else 0.0
        box = QVBoxLayout(self)
        box.setContentsMargins(theme.M, theme.S, theme.M, theme.S)
        box.setSpacing(theme.XS)
        top = QHBoxLayout()
        top.setSpacing(theme.M)
        self.play_btn = Button("Play", "secondary", icon="ph.play-fill")
        self.play_btn.setToolTip("Play this take. Click the graph first to start from that point.")
        self.play_btn.clicked.connect(self.play_clicked.emit)
        self.clock_label = Label(self._clock_text(None), role="numeric", tone="secondary")
        self.hint = Label(self._hint(), role="caption", tone="muted")
        top.addWidget(self.play_btn)
        top.addWidget(self.clock_label)
        top.addWidget(self.hint, 1)
        box.addLayout(top)
        self.plot = pg.PlotWidget()
        self.plot.setFixedHeight(STRIP_HEIGHT)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.getAxis("left").setStyle(showValues=False)
        self.plot.getAxis("left").setWidth(8)
        self.plot.scene().sigMouseClicked.connect(self._clicked)
        box.addWidget(self.plot)
        self.setAccessibleName("Player")
        self.draw()

    def _hint(self):
        if self.moments:
            return "The shaded moments are the tips' moments: click one to hear it."
        return "Click the graph to start from there."

    def _clock_text(self, seconds):
        return f"{clock(seconds or 0)} / {clock(self.length)}"

    def restyle(self):
        if hasattr(self, "plot"):
            self.draw()

    def paintEvent(self, event):
        t = theme.current()
        rounded(painter(self), self.rect(), theme.RADIUS["medium"], fill=t.q("surface_raised"), border=t.q("border"))

    # --- drawing ---
    def draw(self):
        t = theme.current()
        p = self.plot
        p.clear()
        p.setBackground(t["surface_raised"])
        for name in ("left", "bottom"):
            axis = p.getAxis(name)
            axis.setPen(_pen(t["border"], 1))
            axis.setTextPen(_pen(t["text_muted"], 1))
            axis.setStyle(tickFont=QFont(theme.font("caption")), tickTextOffset=4)
        p.showGrid(x=True, y=False, alpha=0.1)
        f0 = np.asarray(self.tracks["f0"], dtype=float)
        voiced = f0[~np.isnan(f0)]
        if len(voiced):
            lo, hi = np.percentile(voiced, [1, 99])
            y0, y1 = math.log2(lo) - PAD_ST / 12, math.log2(hi) + PAD_ST / 12
        else:
            y0, y1 = math.log2(100), math.log2(200)
        end = self.length or 1.0
        self.regions = []
        for a, b in self.moments:
            fill = QColor(t["accent"])
            fill.setAlpha(MOMENT_ALPHA[t.name])
            region = pg.LinearRegionItem((a, b), orientation="vertical", movable=False, brush=pg.mkBrush(fill),
                                         pen=_pen(t["accent"], 1))
            region.setZValue(-10)
            region.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            p.addItem(region, ignoreBounds=True)
            self.regions.append(region)
        with np.errstate(divide="ignore", invalid="ignore"):
            self.curve = p.plot(self.times, np.log2(f0), pen=_pen(t.series["pitch"], 2.0), connect="finite")
        self.overlay = None
        self.set_overlay(self.overlay_tracks)
        self.head = pg.InfiniteLine(angle=90, pen=_pen(t["accent"], 2))
        p.addItem(self.head, ignoreBounds=True)
        p.setYRange(y0, y1, padding=0)
        p.setXRange(0, end, padding=0.01)
        self.set_playhead(self.playhead)

    def set_overlay(self, tracks):
        """Another take's pitch as a dashed grey line (the compared take); None removes it."""
        if self.overlay is not None:
            self.plot.removeItem(self.overlay)
            self.overlay = None
        self.overlay_tracks = tracks
        if tracks is None:
            return
        times = np.asarray(tracks["times"], dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            self.overlay = self.plot.plot(times, np.log2(np.asarray(tracks["f0"], dtype=float)),
                                          pen=_pen(theme.current().series["compare"], 1.6, Qt.PenStyle.DashLine),
                                          connect="finite")

    def set_playhead(self, seconds):
        """Show the playhead at `seconds` (and the clock), or hide it (None)."""
        self.playhead = seconds
        self.head.setVisible(seconds is not None)
        if seconds is not None:
            self.head.setValue(seconds)
        self.clock_label.setText(self._clock_text(seconds))

    def set_playing(self, playing):
        self.play_btn.setText("Stop" if playing else "Play")
        self.play_btn.set_icon("ph.stop-fill" if playing else "ph.play-fill")

    def set_play_text(self, text, playing):
        self.play_btn.setText(text)
        self.play_btn.set_icon("ph.stop-fill" if playing else "ph.play-fill")

    # --- mouse ---
    def _clicked(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.scenePos()
        if not self.plot.getPlotItem().vb.sceneBoundingRect().contains(pos):
            return
        x = float(np.clip(self.plot.getPlotItem().vb.mapSceneToView(pos).x(), 0, self.length))
        hit = take_view.moment_at(self.moments, x)
        if hit is not None:
            self.moment_clicked.emit(*hit)
        else:
            self.seek_requested.emit(x)
