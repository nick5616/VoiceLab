"""Take cards and the take analysis graphs."""
import time
from datetime import datetime

import numpy as np
import pyqtgraph as pg
import sounddevice as sd
from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame, QLineEdit, QMenu,
                             QSizePolicy, QWidget)
from PyQt6.QtGui import QFont, QAction
import qtawesome as qta

import coach
import register
from analysis import CLOSE_CENTS, IN_TUNE_CENTS, SAMPLE_RATE
from exercise import midi_name
from shift_panel import ShiftPanel
from live_views import pitch_label
from widgets import ResponsiveGrid
from take_store import default_mode
from ui_common import (BUTTON_STYLE, METER_MIN_WIDTH, METRIC_DEFS, REGISTER_COLORS, MetricBar, OFF_TARGET_COLOR,
                       ON_TARGET_COLOR, ShareBar, hiss_note)

TAKE_BAR_KEYS = ["pitch", "delta_f", "h1h2", "cpp", "ring", "pitch_range", "level"]
REGISTER_DOT_SIZE = 5           # px, the register-coloured dots on the pitch track
# Take bars whose reading coach.take_quality can mark unreliable (same names in its "trust").
TRUSTED_BARS = {"pitch", "delta_f", "h1h2", "cpp", "ring", "pitch_range"}
TIER_COLORS = {"good": ON_TARGET_COLOR, "fair": "#f1c40f", "poor": "#e74c3c"}
TIER_TEXT = {"good": "Good recording", "fair": "Fair recording", "poor": "Poor recording"}
PLAY_TICK_MS = 30               # playhead refresh while a take plays
COMPARE_GAP_SECONDS = 0.5       # silence between the take and the one it is compared with
PLAY_STYLE = "background-color: #4CAF50; color: white; padding: 5px 10px; border-radius: 5px;"
STOP_STYLE = "background-color: #c0392b; color: white; padding: 5px 10px; border-radius: 5px;"
COMPARE_PEN = pg.mkPen((220, 220, 220, 150), width=1.5, style=Qt.PenStyle.DashLine)
_playing_card = None            # the one card that is playing, so starting another stops it


def take_date(data):
    try:
        return datetime.fromisoformat(data["time"]).strftime("%a %b %d · %H:%M").replace(" 0", " ")
    except (KeyError, TypeError, ValueError):
        return ""


def register_colors(tracks):
    """Per-frame colour of the register track (fry grey), or None if the take has none."""
    scores = tracks.get("register")
    if scores is None:
        return None
    fry = tracks.get("fry")
    colors = []
    for i, score in enumerate(scores):
        if fry is not None and fry[i] > 0.5:
            colors.append(REGISTER_COLORS["fry"])
        elif np.isnan(score):
            colors.append(None)
        else:
            colors.append(REGISTER_COLORS["chest" if score < register.MIX_BAND[0] else
                                          "head" if score > register.MIX_BAND[1] else "mix"])
    return colors


class AnalysisGraph(pg.GraphicsLayoutWidget):
    """Pitch track on top, F1-F3 below, with a hover readout. The pitch track is
    dotted in the register's colour (chest blue, mix purple, head red, fry grey).
    A click asks to seek (seek_requested, seconds); a playhead follows playback; a second take
    can be overlaid for comparison."""

    seek_requested = pyqtSignal(float)

    def __init__(self, tracks):
        super().__init__()
        self.setBackground("#2b2b2b")
        self.tracks = tracks
        t = tracks["times"]

        self.ax_pitch = self.addPlot(row=0, col=0)
        self.ax_res = self.addPlot(row=1, col=0)
        self.ci.layout.setRowStretchFactor(0, 1)
        self.ci.layout.setRowStretchFactor(1, 2)
        self.ax_res.setXLink(self.ax_pitch)
        self.ax_pitch.setLabel("left", "Hz")
        self.ax_res.setLabel("left", "Formants Hz")
        for ax in (self.ax_pitch, self.ax_res):
            ax.showGrid(x=True, y=True, alpha=0.2)
            ax.setMouseEnabled(x=False, y=False)
            ax.hideButtons()

        self.ax_pitch.plot(t, tracks["f0"], pen=pg.mkPen("#3498db", width=2), connect="finite")
        self.register_dots = pg.ScatterPlotItem(size=REGISTER_DOT_SIZE, pen=None)
        self.ax_pitch.addItem(self.register_dots)
        self.set_register(tracks)
        # At least ~4 semitones of span so a steady note doesn't zoom into noise.
        lo, hi = np.nanmin(tracks["f0"]), np.nanmax(tracks["f0"])
        center = np.sqrt(lo * hi)
        self.ax_pitch.setYRange(min(lo, center / 1.12), max(hi, center * 1.12))
        self.ax_res.addLegend(offset=(-10, 10))
        for key, color in (("f3", "#e74c3c"), ("f2", "#f1c40f"), ("f1", "#2ecc71")):
            self.ax_res.plot(t, tracks[key], pen=pg.mkPen(color, width=1.5),
                             connect="finite", name=key.upper())

        self.cursors = [pg.InfiniteLine(angle=90, pen=pg.mkPen("w", width=1)) for _ in range(2)]
        self.ax_pitch.addItem(self.cursors[0])
        self.ax_res.addItem(self.cursors[1])
        self.text_p = pg.TextItem(color="w", anchor=(0, 0))
        self.text_r = pg.TextItem(color="w", anchor=(0, 0))
        self.ax_pitch.addItem(self.text_p, ignoreBounds=True)
        self.ax_res.addItem(self.text_r, ignoreBounds=True)
        self.scene().sigMouseMoved.connect(self.on_hover)
        self.scene().sigMouseClicked.connect(self.on_click)
        self.playheads = [pg.InfiniteLine(angle=90, pen=pg.mkPen("#f1c40f", width=2)) for _ in range(2)]
        for ax, line in zip((self.ax_pitch, self.ax_res), self.playheads):
            ax.addItem(line, ignoreBounds=True)
            line.hide()
        self.overlay = []

    def on_click(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        for ax in (self.ax_pitch, self.ax_res):
            if ax.sceneBoundingRect().contains(event.scenePos()):
                x = ax.vb.mapSceneToView(event.scenePos()).x()
                self.seek_requested.emit(float(np.clip(x, 0, self.duration())))
                return

    def duration(self):
        return float(np.nanmax(self.tracks["times"]))

    def set_playhead(self, seconds):
        """Show the playhead at `seconds`, or hide it (None)."""
        for line in self.playheads:
            line.setVisible(seconds is not None)
            if seconds is not None:
                line.setValue(seconds)

    def set_overlay(self, tracks):
        """Draw another take's pitch and formants as dashed grey lines; None removes them."""
        for ax, item in self.overlay:
            ax.removeItem(item)
        self.overlay = []
        if tracks is None:
            return
        t = np.asarray(tracks["times"], dtype=float)
        for ax, keys in ((self.ax_pitch, ("f0",)), (self.ax_res, ("f1", "f2", "f3"))):
            for key in keys:
                if key in tracks:
                    item = ax.plot(t, np.asarray(tracks[key], dtype=float), pen=COMPARE_PEN, connect="finite")
                    self.overlay.append((ax, item))

    def set_register(self, tracks, show=True):
        """(Re)colour the pitch track by register; plain blue when the take has no register
        (or `show` is False, for a register the recording can't support)."""
        self.tracks = tracks
        colors = register_colors(tracks) if show else None
        if colors is None:
            self.register_dots.clear()
            return
        f0 = tracks["f0"]
        keep = [i for i, c in enumerate(colors) if c is not None and not np.isnan(f0[i])]
        self.register_dots.setData(x=tracks["times"][keep], y=f0[keep],
                                   brush=[pg.mkBrush(colors[i]) for i in keep])

    def on_hover(self, pos):
        for ax in (self.ax_pitch, self.ax_res):
            if ax.sceneBoundingRect().contains(pos):
                x = ax.vb.mapSceneToView(pos).x()
                break
        else:
            return
        for cursor in self.cursors:
            cursor.setValue(x)
        idx = int(np.abs(self.tracks["times"] - x).argmin())
        p_val = self.tracks["f0"][idx]
        f1, f2, f3 = (self.tracks[k][idx] for k in ("f1", "f2", "f3"))

        self.text_p.setText(pitch_label(p_val) if not np.isnan(p_val) else "Unvoiced")
        self.text_p.setPos(self.ax_pitch.vb.viewRange()[0][0], self.ax_pitch.vb.viewRange()[1][1])
        if not np.isnan(f1):
            self.text_r.setText(f"F1:{f1:.0f} F2:{f2:.0f} F3:{f3:.0f}")
            self.text_r.setPos(self.ax_res.vb.viewRange()[0][0], self.ax_res.vb.viewRange()[1][1])


class NoteScoreChart(pg.PlotWidget):
    """Per-note tuning error of an exercise, with the in-tune zone shaded."""

    def __init__(self, notes):
        super().__init__(background="#2b2b2b")
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.setMenuEnabled(False)
        self.setFixedHeight(120)
        self.getAxis("left").setLabel("¢")
        zone = pg.LinearRegionItem((-IN_TUNE_CENTS, IN_TUNE_CENTS), orientation="horizontal",
                                   movable=False, brush=pg.mkBrush(46, 204, 113, 40), pen=pg.mkPen(None))
        self.addItem(zone)
        xs = np.arange(len(notes))
        heights = [np.clip(n["cents"], -100, 100) if n["cents"] is not None else 0 for n in notes]
        brushes = ["#555" if n["cents"] is None else
                   ON_TARGET_COLOR if abs(n["cents"]) <= IN_TUNE_CENTS else OFF_TARGET_COLOR
                   for n in notes]
        self.addItem(pg.BarGraphItem(x=xs, height=heights, width=0.7, brushes=brushes, pen=None))
        # Dots keep spot-on notes (zero-height bars) visible.
        sung = [i for i, n in enumerate(notes) if n["cents"] is not None]
        self.addItem(pg.ScatterPlotItem([xs[i] for i in sung], [heights[i] for i in sung], size=8,
                                        pen=None, brush=[brushes[i] for i in sung]))
        missed = [i for i, n in enumerate(notes) if n["cents"] is None]
        if missed:
            self.plot(missed, [0] * len(missed), pen=None, symbol="x", symbolBrush="#888", symbolPen="#888")
        self.getAxis("bottom").setTicks([[(i, midi_name(n["midi"])) for i, n in enumerate(notes)]])
        self.setYRange(-100, 100, padding=0)
        self.setXRange(-0.6, len(notes) - 0.4, padding=0)


def exercise_summary(exercise):
    """One line on an exercise: notes in tune and close, time in tune, and timing against the guide."""
    text = f"{exercise['name']}: {exercise['notes_in_tune']}/{exercise['notes_total']} notes in tune (±{IN_TUNE_CENTS}¢)"
    if exercise.get("notes_close") is not None:
        text += f", {exercise['notes_close']} within ±{CLOSE_CENTS}¢"
    if exercise.get("mean_abs_cents") is not None:
        text += f" · average error {exercise['mean_abs_cents']:.0f}¢ · {exercise['time_in_tune']:.0%} of note time in tune"
        if exercise.get("time_close") is not None:
            text += f", {exercise['time_close']:.0%} close"
    if exercise.get("timing"):
        text += f" · timing: {exercise['timing']}"
    return text


def singing_summary(singing):
    """One line on held notes, vibrato, drift and tuning (spread about your own centre, centre vs
    A440, interval error), or None."""
    if not singing or not singing["held_notes"]:
        return None
    parts = [f"Held notes: {singing['held_notes']}"]
    if singing["vibrato_notes"]:
        regularity = singing["vibrato_regularity"]
        parts.append(f"vibrato on {singing['vibrato_notes']}: {singing['vibrato_rate']:.1f} Hz "
                     f"±{singing['vibrato_extent']:.0f}¢"
                     + (f", cycle variation {regularity:.0%}" if regularity is not None else ""))
    else:
        parts.append("straight tone")
    if singing.get("tremolo_notes"):
        parts.append(f"loudness wobble on {singing['tremolo_notes']}")
    if singing["drift"] is not None:
        parts.append(f"drift ±{singing['drift']:.0f}¢")
    spread = singing.get("tuning_spread")
    if spread is not None:
        parts.append(f"notes sit ±{spread:.0f}¢ from your own centre")
    if singing.get("interval_error") is not None:
        parts.append(f"steps between notes off by {singing['interval_error']:.0f}¢")
    tonic = singing.get("tonic_offset")
    if tonic is not None:
        parts.append(f"your centre is {abs(tonic):.0f}¢ {'above' if tonic > 0 else 'below'} A440")
    return " · ".join(parts)


class UntrustedMetric(QWidget):
    """Stands in for a take bar whose reading the recording can't support: a dash and the reason, no bar."""

    def __init__(self, key, reason):
        super().__init__()
        self.key = key
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 4, 0, 4)
        self.title = QLabel(f"{METRIC_DEFS[key][0]}: — ({reason})")
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size: 11px; color: #888;")
        box.addWidget(self.title)

    def set_reference(self, profile):
        pass

    def set_value(self, *args, **kwargs):
        pass


class TakeCard(QFrame):
    """One take's report. `audio` returns its samples (loaded on demand for
    saved takes); `on_rename(card, title)` and `on_delete(card)` manage it.
    `compare_options(card)` lists [(label, take)] to compare with, `compare_audio(take)` loads one."""

    def __init__(self, data, count, profile, on_set_baseline, on_set_target,
                 on_rename=None, on_delete=None, audio=None, on_label=None,
                 compare_options=None, compare_audio=None):
        super().__init__()
        self.data, self.profile, self.number = data, profile, count
        self.on_label, self.on_rename, self.on_delete = on_label, on_rename, on_delete
        self.on_set_baseline, self.on_set_target = on_set_baseline, on_set_target
        self.compare_options, self.compare_audio = compare_options, compare_audio
        self.audio = audio or (lambda: data["audio"])
        self.compare = None             # the take played and drawn alongside this one
        self.playing = False
        self.start_at = 0.0             # where Play starts (s): set by clicking the graph
        self.pieces = []                # what is playing: (offset in its take or None for the gap, seconds)
        self.started = 0.0
        self.clock = time.monotonic
        self.timer = QTimer(self)
        self.timer.setInterval(PLAY_TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        # Scoped to the card: a bare QFrame rule would also hit every QLabel inside.
        self.setStyleSheet("""
            TakeCard { background-color: #2b2b2b; border-radius: 10px; margin-bottom: 10px; }
            QLabel { color: #ffffff; background: transparent; }
            QLineEdit { color: white; background: transparent; border: 1px solid transparent;
                        border-radius: 4px; padding: 1px 3px; }
            QLineEdit:hover, QLineEdit:focus { border: 1px solid #555; background: #1e1e1e; }
        """)
        self.outer = QVBoxLayout(self)
        self.body = None
        self._build()

    def reload(self):
        """Show the take again from its (re-analysed) data."""
        self._build()

    def _build(self):
        if self.body is not None:
            self.stop_playback()
            self.outer.removeWidget(self.body)
            self.body.setParent(None)
            self.body.deleteLater()
        self.body = QWidget()
        self.outer.addWidget(self.body)
        data, count = self.data, self.number
        self.quality = coach.take_quality(data, data.get("noise"))
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header
        header = QHBoxLayout()
        self.title = QLineEdit(data.get("title") or "")
        self.title.setPlaceholderText(f"Take #{count}")
        self.title.setToolTip("Click to name this take" + (" (renames its files)" if data.get("file") else ""))
        self.title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.title.setMinimumWidth(110)
        self.title.setCursorPosition(0)       # long names show their start, not their end
        self.title.editingFinished.connect(self._title_edited)
        self.date = QLabel()
        self.date.setStyleSheet("color: #888; font-size: 11px;")
        self.update_saved_state()
        baseline_btn = QPushButton(qta.icon("fa5s.flag", color="white"), " Baseline")
        baseline_btn.setToolTip("Set as baseline")
        baseline_btn.clicked.connect(lambda: self.on_set_baseline(data))
        target_btn = QPushButton(qta.icon("fa5s.bullseye", color="white"), " Target")
        target_btn.setToolTip("Use as target")
        target_btn.clicked.connect(lambda: self.on_set_target(data))
        self.shift_btn = QPushButton(qta.icon("fa5s.sliders-h", color="white"), " Shift")
        self.shift_btn.setToolTip("Hear this take with the voice shifted")
        self.shift_btn.setCheckable(True)
        for btn in (baseline_btn, target_btn):
            btn.setStyleSheet(BUTTON_STYLE)
        self.shift_btn.setStyleSheet(f"QPushButton {{ {BUTTON_STYLE} }} "
                                     "QPushButton:checked { background-color: #3498db; }")
        delete_btn = QPushButton(qta.icon("fa5s.trash-alt", color="#ccc"), "")
        delete_btn.setToolTip("Delete this take")
        delete_btn.setStyleSheet(BUTTON_STYLE)
        delete_btn.clicked.connect(lambda: self.on_delete and self.on_delete(self))
        header.addWidget(self.title, 1)
        header.addWidget(self.date)
        header.addWidget(baseline_btn)
        header.addWidget(target_btn)
        header.addWidget(self.shift_btn)
        header.addWidget(delete_btn)
        layout.addLayout(header)

        if data.get("source"):
            source = QLabel(data["source"])
            source.setStyleSheet("color: #aaa; font-size: 11px;")
            layout.addWidget(source)

        # --- QUALITY AND ADVICE ---
        tier = self.quality["tier"]
        self.quality_chip = QLabel(f"● {TIER_TEXT[tier]}")
        self.quality_chip.setStyleSheet(f"color: {TIER_COLORS[tier]}; font-size: 12px; font-weight: bold;")
        self.quality_chip.setToolTip("How far this recording can be trusted. Readings it can't support "
                                     "are shown as — instead of a number.")
        layout.addWidget(self.quality_chip)
        if self.quality["reasons"]:
            self.quality_reasons = QLabel(" · ".join(self.quality["reasons"]))
            self.quality_reasons.setWordWrap(True)
            self.quality_reasons.setStyleSheet("color: #bbb; font-size: 11px;")
            layout.addWidget(self.quality_reasons)
        note = hiss_note(data.get("snr"), data.get("snr_full"))
        if note:
            info = QLabel(note)
            info.setWordWrap(True)
            info.setStyleSheet("color: #aaa; font-size: 11px;")
            layout.addWidget(info)
        self.advice_box = QVBoxLayout()
        layout.addLayout(self.advice_box)

        self.shift_panel = ShiftPanel(data, self.on_set_target, audio=self.audio)
        self.shift_panel.hide()
        self.shift_btn.toggled.connect(self.shift_panel.setVisible)
        # While shifting, the card's Play button plays the shifted version.
        self.shift_btn.toggled.connect(lambda _: self._update_play_text())
        layout.addWidget(self.shift_panel)

        # --- GRAPH AND PLAYBACK ---
        self.graph = AnalysisGraph(data['tracks'])
        self.graph.setFixedHeight(200)
        self.graph.seek_requested.connect(self.seek)
        layout.addWidget(self.graph)
        self.play_btn = QPushButton(qta.icon('fa5s.play', color="white"), " Play")
        self.play_btn.setStyleSheet(PLAY_STYLE)
        self.play_btn.setToolTip("Play this take. Click the graph first to start from that point.")
        self.play_btn.clicked.connect(self.play_audio)
        self.compare_btn = QPushButton(qta.icon("fa5s.exchange-alt", color="white"), " Compare with…")
        self.compare_btn.setStyleSheet(BUTTON_STYLE + " QPushButton::menu-indicator { image: none; }")
        self.compare_btn.setToolTip("Play this take, then another (your baseline take or one you choose), "
                                    "and draw the other on the graph.")
        menu = QMenu(self.compare_btn)
        menu.setStyleSheet("QMenu { background: #2b2b2b; color: white; } "
                           "QMenu::item:selected { background: #3498db; }")
        menu.aboutToShow.connect(lambda: self._fill_compare_menu(menu))
        self.compare_btn.setMenu(menu)
        self.compare_label = QLabel()
        self.compare_label.setStyleSheet("color: #aaa; font-size: 11px;")
        self.compare_label.setWordWrap(True)
        play_row = QHBoxLayout()
        for w in (self.play_btn, self.compare_btn):
            play_row.addWidget(w)
        play_row.addWidget(self.compare_label, 1)
        layout.addLayout(play_row)

        self.time_in_target = QLabel()
        self.time_in_target.setWordWrap(True)
        self.time_in_target.setStyleSheet("color: #2ecc71; font-size: 12px;")
        layout.addWidget(self.time_in_target)

        # --- SINGING ---
        trust = self.quality["trust"]
        why_singing = self.quality["why_untrusted"].get("singing", "")
        exercise = data.get("exercise")
        if exercise:
            label = QLabel(exercise_summary(exercise) if trust["singing"] else
                           f"{exercise['name']}: — ({why_singing})")
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 12px;")
            layout.addWidget(label)
            if trust["singing"]:
                layout.addWidget(NoteScoreChart(exercise["notes"]))
        summary = singing_summary(data.get("singing")) if trust["singing"] else None
        if data.get("singing") and not trust["singing"]:
            summary = f"Singing details: — ({why_singing})"
        if summary:
            label = QLabel(summary)
            label.setWordWrap(True)
            label.setStyleSheet("color: #ddd; font-size: 12px;")
            layout.addWidget(label)

        # --- REGISTER ---
        self.register_box = self._build_register_row()
        layout.addWidget(self.register_box)
        egg = data.get("egg")
        if egg and not egg["signal_ok"]:
            warning = QLabel("⚠ EGG: " + (egg.get("note") or "no usable signal"))
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {OFF_TARGET_COLOR}; font-size: 12px;")
            layout.addWidget(warning)

        # --- METRIC BARS ---
        has_cq = egg is not None and egg["signal_ok"] and data.get("cq") is not None
        self.bars = {}
        for key in TAKE_BAR_KEYS + (["cq"] if has_cq else []):
            if key in TRUSTED_BARS and not trust[key]:
                self.bars[key] = UntrustedMetric(key, self.quality["why_untrusted"][key])
            else:
                self.bars[key] = MetricBar(key)
        bar_grid = ResponsiveGrid(METER_MIN_WIDTH)
        for bar in self.bars.values():
            bar_grid.addWidget(bar)
        layout.addWidget(bar_grid)
        self.set_compare(self.compare)
        self.refresh_profile()

    def _build_register_row(self):
        box = QFrame()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 2, 0, 2)
        left = QVBoxLayout()
        self.register_title = QLabel("Register")
        self.register_title.setWordWrap(True)
        self.register_title.setStyleSheet("font-size: 11px; color: #aaa;")
        self.register_bar = ShareBar()
        self.register_text = QLabel()
        self.register_text.setWordWrap(True)
        self.register_text.setStyleSheet("font-size: 12px;")
        for w in (self.register_title, self.register_bar, self.register_text):
            left.addWidget(w)
        row.addLayout(left, 1)
        label_btn = QPushButton(qta.icon("fa5s.tag", color="white"), " Label register…")
        label_btn.setStyleSheet(BUTTON_STYLE + " QPushButton::menu-indicator { image: none; }")
        label_btn.setToolTip("Tell VoiceLab this take is chest or head voice. Once enough takes "
                             "are labelled, the register estimate is calibrated to your voice.")
        menu = QMenu(label_btn)
        menu.setStyleSheet("QMenu { background: #2b2b2b; color: white; } "
                           "QMenu::item:selected { background: #3498db; }")
        self.label_actions = {}
        for key, text in (("chest", "Chest"), ("head", "Head"), (None, "Clear label")):
            action = QAction(text, menu)
            action.triggered.connect(lambda _, k=key: self.on_label and self.on_label(self, k))
            menu.addAction(action)
            self.label_actions[key] = action
        label_btn.setMenu(menu)
        row.addWidget(label_btn, 0)
        return box

    def refresh_register(self):
        """Show the register row and colour the graph. A take without a register result
        (an older take, scored in the background) keeps the row's space, so the card doesn't jump."""
        reg = self.data.get("register")
        if not self.quality["trust"]["register"]:
            self.graph.set_register(self.data["tracks"], show=False)
            self.register_title.setText(f"Register — — ({self.quality['why_untrusted']['register']})")
            self.register_bar.set_segments([])
            self.register_text.setText('<span style="color:#888">—</span>')
            return
        self.graph.set_register(self.data["tracks"])
        if not reg or "chest_share" not in reg:
            self.register_title.setText("Register — not scored yet")
            self.register_bar.set_segments([])
            self.register_text.setText('<span style="color:#888">estimating…</span>')
            return
        shares = [(reg[f"{name}_share"], name) for name in ("chest", "mix", "head", "fry")]
        self.register_bar.set_segments([(share, REGISTER_COLORS[name]) for share, name in shares])
        self.register_text.setText(" · ".join(
            f'<span style="color:{REGISTER_COLORS[name]}">{name} {share:.0%}</span>'
            for share, name in shares if share > 0 or name != "fry"))
        breaks = len(reg.get("breaks") or [])
        notes = [f"{breaks} break{'s' if breaks != 1 else ''}",
                 "calibrated to your voice" if reg.get("calibrated") else "estimate"]
        if self.data.get("register_label"):
            notes.append(f"you labelled it {self.data['register_label']}")
        on_target = self.profile.in_target("register", 100 * reg["head_share"])
        if on_target is not None:
            notes.append("head share ✓ on target" if on_target else "head share off target")
        self.register_title.setText("Register — " + " · ".join(notes))

    def mode(self):
        return default_mode(self.data)

    def refresh_advice(self):
        """Up to three next steps from the take and the targets, at the top of the card."""
        while self.advice_box.count():
            item = self.advice_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.advice = coach.advice(self.data, self.profile.targets, self.profile.baseline,
                                   mode=self.mode(), quality=self.quality)
        for message in self.advice:
            label = QLabel("→ " + message["text"])
            label.setWordWrap(True)
            color = "#ddd" if message["severity"] in ("info", "tentative") else "#fff"
            label.setStyleSheet(f"color: {color}; font-size: 12px; padding: 2px 0;"
                                + (" font-style: italic;" if message["severity"] == "tentative" else ""))
            self.advice_box.addWidget(label)

    def refresh_profile(self):
        # Share of voiced time inside each target: holding a voice across a whole
        # passage matters more than its median.
        self.refresh_register()
        self.refresh_advice()
        tracks, trust = self.data["tracks"], self.quality["trust"]
        parts = []
        for name, key in (("pitch", "pitch"), ("resonance", "delta_f")):
            share = self.profile.time_in_target(key, tracks[key if key == "delta_f" else "f0"]) \
                if trust[key] else None
            if share is not None:
                parts.append(f"{name} {share:.0%}")
        if trust["pitch"] and trust["delta_f"] and self.profile.target("pitch") and self.profile.target("delta_f"):
            both = self.profile.time_in_both(tracks["f0"], tracks["delta_f"])
            if both is not None:
                parts.append(f"pitch + resonance together {both:.0%}")
        self.time_in_target.setText(f"Time in target: {' · '.join(parts)}" if parts else "")
        self.time_in_target.setVisible(bool(parts))
        for key, bar in self.bars.items():
            bar.set_reference(self.profile)
            if key == "delta_f" and self.data["delta_f"] is None:
                bar.set_value(None, self.profile, note=self.data["resonance_note"])
            else:
                bar.set_value(self.data[key], self.profile)

    def _title_edited(self):
        title = self.title.text().strip()
        if self.on_rename and title != (self.data.get("title") or ""):
            self.on_rename(self, title)

    def update_saved_state(self):
        """Date, and where the take is stored."""
        self.date.setText(take_date(self.data) + ("" if self.data.get("file") else " · not saved"))
        self.date.setToolTip(self.data.get("file") or "Choose a takes folder to keep takes between sessions.")

    # --- PLAYBACK ---

    def _update_play_text(self):
        if self.playing:
            self.play_btn.setText(" Stop")
            self.play_btn.setIcon(qta.icon("fa5s.stop", color="white"))
            self.play_btn.setStyleSheet(STOP_STYLE)
            return
        self.play_btn.setIcon(qta.icon("fa5s.play", color="white"))
        self.play_btn.setStyleSheet(PLAY_STYLE)
        self.play_btn.setText(" Play shifted" if self.shift_btn.isChecked() else
                              " Play, then compare" if self.compare else " Play")

    def play_audio(self):
        """Play from the start (or the point clicked on the graph); while playing, stop."""
        if self.playing:
            self.stop_playback()
        else:
            self.start_playback(self.start_at)

    def start_playback(self, start=0.0):
        global _playing_card
        if _playing_card is not None and _playing_card is not self:
            _playing_card.stop_playback()
        samples = np.asarray(self.audio(), dtype=float)
        duration = len(samples) / SAMPLE_RATE
        start = float(np.clip(start, 0, max(duration - 0.05, 0)))
        if self.shift_panel.isVisible():
            self.shift_panel.play_shifted()
            self.pieces = [(start, duration - start)]
        else:
            buffer = samples[int(start * SAMPLE_RATE):]
            self.pieces = [(start, len(buffer) / SAMPLE_RATE)]
            if self.compare is not None and self.compare_audio:
                other = np.asarray(self.compare_audio(self.compare), dtype=float)
                gap = np.zeros(int(COMPARE_GAP_SECONDS * SAMPLE_RATE))
                self.pieces += [(None, COMPARE_GAP_SECONDS), (0.0, len(other) / SAMPLE_RATE)]
                buffer = np.concatenate([buffer, gap, other])
            sd.play(buffer, SAMPLE_RATE)
        self.playing, _playing_card = True, self
        self.started = self.clock()
        self.timer.start()
        self._update_play_text()
        self._tick()

    def _tick(self):
        """Move the playhead to where playback has got to; stop at the end."""
        elapsed = self.clock() - self.started
        for index, (offset, length) in enumerate(self.pieces):
            if elapsed < length:
                self.graph.set_playhead(None if offset is None else offset + elapsed)
                if self.compare is not None:
                    self.compare_label.setText("Playing this take" if index == 0 else
                                               "…" if offset is None else f"Playing {self._compare_name()}")
                return
            elapsed -= length
        self.stop_playback()

    def stop_playback(self):
        global _playing_card
        was_playing, self.playing = self.playing, False
        self.timer.stop()
        if was_playing:
            sd.stop()
        if _playing_card is self:
            _playing_card = None
        self.start_at = 0.0
        self.graph.set_playhead(None)
        self.compare_label.setText(self._compare_text())
        self._update_play_text()

    def seek(self, seconds):
        """A click on the graph: play from there, or if stopped, start there next time."""
        if self.playing:
            self.start_playback(seconds)
            return
        self.start_at = seconds
        self.graph.set_playhead(seconds)

    # --- COMPARISON ---

    def _compare_name(self):
        data = self.compare
        return data.get("title") or take_date(data) or "the other take"

    def _compare_text(self, now=None):
        if self.compare is None:
            return ""
        return now or f"Comparing with {self._compare_name()} (dashed). Play runs this take, then that one."

    def _fill_compare_menu(self, menu):
        menu.clear()
        options = self.compare_options(self) if self.compare_options else []
        for label, take in options:
            action = QAction(label, menu)
            action.triggered.connect(lambda _, t=take: self.set_compare(t))
            menu.addAction(action)
        if not options:
            menu.addAction(QAction("No other takes to compare with", menu)).setEnabled(False)
        if self.compare is not None:
            menu.addSeparator()
            clear = QAction("Remove comparison", menu)
            clear.triggered.connect(lambda: self.set_compare(None))
            menu.addAction(clear)

    def set_compare(self, take):
        """Compare with another take (None clears it): its tracks are drawn dashed on the graph."""
        if self.playing:
            self.stop_playback()
        self.compare = take
        self.graph.set_overlay(None if take is None else take["tracks"])
        self.compare_label.setText(self._compare_text())
        self._update_play_text()
