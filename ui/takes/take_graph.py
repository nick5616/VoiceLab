"""A take's graphs in the design system's colours: the pitch track over the zone bands (on the live graph's
fixed axis), register dots, smoothed formants below, a playhead, click-to-seek and a comparison overlay;
and the exercise's per-note tuning chart. take_view decides the axis, the formant lines and the register
of each frame; this module only draws them."""
import math

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

import take_view
from display import formants_label, pitch_label
from ui import theme

PITCH_STRETCH, FORMANT_STRETCH = 3, 2   # row heights: the pitch track leads
FORMANT_AXIS_HZ = (0, 4000)             # fixed, so takes compare at a glance (F3 of adult speech < 3.5 kHz;
#                                         Hillenbrand et al. 1995 vowel means, unverified)
ZONE_ALPHA = {"dark": 80, "light": 120}    # the zone bands are a background: softer than on a ZoneBar
REGISTER_DOT = 7                        # px; soft dots under the pitch line: a coloured glow along it
REGISTER_ALPHA = 150
TARGET_ALPHA = 40


def _pen(color, width=1.5, style=Qt.PenStyle.SolidLine, alpha=None):
    c = QColor(color)
    if alpha is not None:
        c.setAlpha(alpha)
    return pg.mkPen(c, width=width, style=style)


def _style_axis(axis, t):
    axis.setPen(_pen(t["border"], 1))
    axis.setTextPen(_pen(t["text_muted"], 1))
    font = QFont(theme.font("caption"))
    axis.setStyle(tickFont=font, tickTextOffset=6)


class TakeGraph(pg.GraphicsLayoutWidget):
    """The pitch track (log Hz) over the take's zones and the formants below. seek_requested(seconds)
    on a click; set_playhead(seconds or None); set_overlay(other tracks or None); set_raw(bool) shows
    the formants as measured instead of smoothed and voiced-only."""

    seek_requested = pyqtSignal(float)

    def __init__(self, take, register=True, parent=None):
        super().__init__(parent=parent)
        self.take = take
        self.tracks = take["tracks"]
        self.show_register = register
        self.raw = False
        self.overlay_tracks = None
        self.playhead = None
        self.target = None              # (lo, hi) Hz of the pitch target, drawn dashed
        self.target_band = None
        self.axis = take_view.pitch_axis(take)
        self.ax_pitch = self.addPlot(row=0, col=0)
        self.formants = take_view.has_formants(self.tracks)
        self.ax_res = self.addPlot(row=1, col=0)
        self.ci.layout.setRowStretchFactor(0, PITCH_STRETCH)
        self.ci.layout.setRowStretchFactor(1, FORMANT_STRETCH if self.formants else 0)
        if not self.formants:           # no formant readings (most sung takes): the pitch track gets the room
            self.ci.removeItem(self.ax_res)
            self.ax_res.hide()
        self.ci.setContentsMargins(4, 6, 10, 4)
        self.ax_res.setXLink(self.ax_pitch)
        for ax in (self.ax_pitch, self.ax_res):
            ax.setMouseEnabled(x=False, y=False)
            ax.hideButtons()
            ax.setMenuEnabled(False)
            ax.getAxis("left").setWidth(78)
        if self.formants:
            self.ax_pitch.getAxis("bottom").setStyle(showValues=False)
            self.ax_res.setLabel("bottom", "seconds")
        else:
            self.ax_pitch.setLabel("bottom", "seconds")
        self.scene().sigMouseClicked.connect(self._clicked)
        self.scene().sigMouseMoved.connect(self._hover)
        theme.changed.connect(self._theme_changed)
        self.draw()

    def duration(self):
        times = np.asarray(self.tracks["times"], dtype=float)
        return float(times[-1]) if len(times) else 0.0

    def _theme_changed(self, *_):
        self.draw()

    # --- drawing ---
    def draw(self):
        """(Re)draw everything in the current theme."""
        t = theme.current()
        self.setBackground(t["surface"])
        for ax in (self.ax_pitch, self.ax_res):
            ax.clear()
            for name in ("left", "bottom"):
                _style_axis(ax.getAxis(name), t)
            ax.showGrid(x=True, y=False, alpha=0.12)
        times = np.asarray(self.tracks["times"], dtype=float)
        lo, hi = self.axis["lo"], self.axis["hi"]
        self.ax_pitch.setYRange(math.log2(lo), math.log2(hi), padding=0)
        self.ax_pitch.getAxis("left").setTicks([[(math.log2(hz), text) for hz, text in self.axis["ticks"]], []])
        end = self.duration() or 1.0
        self.ax_pitch.setXRange(0, end, padding=0.01)
        self._draw_zones(t, end)
        f0 = np.asarray(self.tracks["f0"], dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            y = np.log2(f0)
        self.pitch_curve = self.ax_pitch.plot(times, y, pen=_pen(t.series["pitch"], 2.2), connect="finite")
        self.pitch_curve.setZValue(10)
        self.register_dots = pg.ScatterPlotItem(size=REGISTER_DOT, pen=None)
        self.register_dots.setZValue(5)
        self.ax_pitch.addItem(self.register_dots)
        self.set_register(self.show_register)

        self.ax_res.setYRange(*FORMANT_AXIS_HZ, padding=0)
        self.ax_res.getAxis("left").setTicks([[(v, f"{v} Hz" if v else "0") for v in (0, 1000, 2000, 3000, 4000)], []])
        self.formant_curves = {}
        for name, values in take_view.formant_lines(self.tracks, raw=self.raw).items():
            self.formant_curves[name] = self.ax_res.plot(times, values, pen=_pen(t.series[name], 1.8),
                                                         connect="finite")
        self.playheads = [pg.InfiniteLine(angle=90, pen=_pen(t["accent"], 2)) for _ in range(2)]
        for ax, line in zip((self.ax_pitch, self.ax_res), self.playheads):
            ax.addItem(line, ignoreBounds=True)
        self.readout = pg.TextItem(color=t["text_secondary"], anchor=(0, 0))
        self.readout.setFont(QFont(theme.font("caption", weight=600)))
        self.ax_pitch.addItem(self.readout, ignoreBounds=True)
        self.overlay = []
        self.set_overlay(self.overlay_tracks)
        self.set_playhead(self.playhead)
        self.target_band = None
        self.set_target(self.target)

    def _draw_zones(self, t, end):
        zones = self.axis.get("zones")
        self.zone_items = []
        if not zones:
            return
        for z in zones["zones"]:
            lo, hi = max(z["lo"], self.axis["lo"]), min(z["hi"], self.axis["hi"])
            colors = t.zone(z["id"])
            fill = QColor(colors["fill"])
            fill.setAlpha(ZONE_ALPHA[t.name])
            band = pg.LinearRegionItem((math.log2(lo), math.log2(hi)), orientation="horizontal", movable=False,
                                       brush=pg.mkBrush(fill), pen=pg.mkPen(None))
            band.setZValue(-20)
            self.ax_pitch.addItem(band, ignoreBounds=True)
            label = pg.TextItem(z["label"], color=colors["text"], anchor=(1, 0.5))
            label.setFont(QFont(theme.font("caption", weight=600)))
            label.setPos(end, (math.log2(lo) + math.log2(hi)) / 2)
            label.setZValue(-10)
            self.ax_pitch.addItem(label, ignoreBounds=True)
            self.zone_items.append((band, label))

    def set_target(self, target):
        """A pitch target (lo, hi) in Hz drawn as a dashed outline on the pitch axis; None removes it."""
        t = theme.current()
        self.target = tuple(target) if target and self.axis["mode"] == "speak" else None
        target = self.target
        if self.target_band is not None:
            self.ax_pitch.removeItem(self.target_band)
            self.target_band = None
        if target:
            band = pg.LinearRegionItem((math.log2(target[0]), math.log2(target[1])), orientation="horizontal",
                                       movable=False, brush=pg.mkBrush(QColor(0, 0, 0, 0)),
                                       pen=_pen(t["on_target"], 1.2, Qt.PenStyle.DashLine))
            band.setZValue(-5)
            self.ax_pitch.addItem(band, ignoreBounds=True)
            self.target_band = band

    def set_register(self, show=True):
        """Dot the pitch track in its register colours (take_view.register_zones); off for a register
        the recording can't support."""
        self.show_register = show
        zones = take_view.register_zones(self.tracks) if show else None
        if not zones:
            self.register_dots.clear()
            return
        t = theme.current()
        f0 = np.asarray(self.tracks["f0"], dtype=float)
        times = np.asarray(self.tracks["times"], dtype=float)
        keep = [i for i, z in enumerate(zones) if z and not np.isnan(f0[i])]
        brushes = {}
        for z in take_view.REGISTER_ZONES:
            c = QColor(t.register[z])
            c.setAlpha(REGISTER_ALPHA)
            brushes[z] = pg.mkBrush(c)
        self.register_dots.setData(x=times[keep], y=np.log2(f0[keep]), brush=[brushes[zones[i]] for i in keep])

    def set_raw(self, raw):
        self.raw = bool(raw)
        times = np.asarray(self.tracks["times"], dtype=float)
        for name, values in take_view.formant_lines(self.tracks, raw=self.raw).items():
            if name in self.formant_curves:
                self.formant_curves[name].setData(times, values, connect="finite")

    def set_playhead(self, seconds):
        """Show the playhead at `seconds`, or hide it (None)."""
        self.playhead = seconds
        for line in self.playheads:
            line.setVisible(seconds is not None)
            if seconds is not None:
                line.setValue(seconds)

    def set_overlay(self, tracks):
        """Draw another take's pitch and formants as dashed grey lines; None removes them."""
        for ax, item in self.overlay:
            ax.removeItem(item)
        self.overlay = []
        self.overlay_tracks = tracks
        if tracks is None:
            return
        pen = _pen(theme.current().series["compare"], 1.6, Qt.PenStyle.DashLine)
        times = np.asarray(tracks["times"], dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            item = self.ax_pitch.plot(times, np.log2(np.asarray(tracks["f0"], dtype=float)), pen=pen, connect="finite")
        self.overlay.append((self.ax_pitch, item))
        for name, values in take_view.formant_lines(tracks).items():
            self.overlay.append((self.ax_res, self.ax_res.plot(times, values, pen=pen, connect="finite")))

    # --- mouse ---
    def _x_at(self, pos):
        for ax in (self.ax_pitch, self.ax_res):
            if ax.sceneBoundingRect().contains(pos):
                return ax.vb.mapSceneToView(pos).x()
        return None

    def _clicked(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = self._x_at(event.scenePos())
        if x is not None:
            self.seek_requested.emit(float(np.clip(x, 0, self.duration())))

    def _hover(self, pos):
        x = self._x_at(pos)
        if x is None:
            self.readout.setText("")
            return
        times = np.asarray(self.tracks["times"], dtype=float)
        i = int(np.abs(times - x).argmin())
        f0 = self.tracks["f0"][i]
        parts = [f"{times[i]:.1f} s", pitch_label(f0) if not np.isnan(f0) else "unvoiced"]
        f1, f2, f3 = (self.tracks.get(k, [np.nan] * len(times))[i] for k in ("f1", "f2", "f3"))
        if not np.isnan(f0) and not np.isnan(f1):
            parts.append(formants_label(f1, f2, f3))
        self.readout.setText("   ".join(parts))
        (x0, _), (_, y1) = self.ax_pitch.vb.viewRange()
        self.readout.setPos(x0, y1)


class NoteScoreChart(pg.PlotWidget):
    """Per-note tuning error of an exercise (take_view.exercise_notes), with the in-tune zone shaded."""

    def __init__(self, notes, in_tune_cents, parent=None):
        super().__init__(parent=parent)
        self.notes, self.in_tune = notes, in_tune_cents
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.setMenuEnabled(False)
        self.setFixedHeight(150)
        theme.changed.connect(lambda *_: self.draw())
        self.draw()

    def draw(self):
        t = theme.current()
        self.setBackground(t["surface"])
        self.clear()
        for name in ("left", "bottom"):
            _style_axis(self.getAxis(name), t)
        self.getAxis("left").setLabel("cents off")
        band = QColor(t["on_target"])
        band.setAlpha(TARGET_ALPHA)
        self.addItem(pg.LinearRegionItem((-self.in_tune, self.in_tune), orientation="horizontal", movable=False,
                                         brush=pg.mkBrush(band), pen=pg.mkPen(None)))
        colors = {"in_tune": t["on_target"], "off": t["off_target"], None: t["text_muted"]}
        xs = np.arange(len(self.notes))
        heights = [n["height"] for n in self.notes]
        brushes = [colors[n["zone"]] for n in self.notes]
        self.addItem(pg.BarGraphItem(x=xs, height=heights, width=0.6, brushes=brushes, pen=None))
        sung = [i for i, n in enumerate(self.notes) if n["cents"] is not None]
        self.addItem(pg.ScatterPlotItem([xs[i] for i in sung], [heights[i] for i in sung], size=8, pen=None,
                                        brush=[brushes[i] for i in sung]))
        missed = [i for i, n in enumerate(self.notes) if n["cents"] is None]
        if missed:
            self.plot(missed, [0] * len(missed), pen=None, symbol="x", symbolBrush=t["text_muted"],
                      symbolPen=t["text_muted"])
        self.getAxis("bottom").setTicks([[(i, n["label"]) for i, n in enumerate(self.notes)]])
        limit = take_view.NOTE_CHART_CENTS
        self.setYRange(-limit, limit, padding=0)
        self.setXRange(-0.6, len(self.notes) - 0.4, padding=0)
