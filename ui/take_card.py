"""Take cards and the take analysis graphs. A card draws take_view's data for its take and plays it
through the core's Player; the actions (rename, label, delete, targets) go to the core."""
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame, QLineEdit, QMenu,
                             QSizePolicy, QWidget)
from PyQt6.QtGui import QFont, QAction
import qtawesome as qta

import take_view
from display import formants_label, pitch_label
from playback import Player
from take_store import default_mode
from ui.meters import (BUTTON_STYLE, METER_MIN_WIDTH, REGISTER_COLORS, MetricBar, OFF_TARGET_COLOR, ON_TARGET_COLOR,
                       ShareBar)
from ui.shift_panel import ShiftPanel
from ui.widgets import ResponsiveGrid

REGISTER_DOT_SIZE = 5           # px, the register-coloured dots on the pitch track
TIER_COLORS = {"good": ON_TARGET_COLOR, "fair": "#f1c40f", "poor": "#e74c3c"}
NOTE_COLORS = {"in_tune": ON_TARGET_COLOR, "off": OFF_TARGET_COLOR, None: "#555"}
PLAY_TICK_MS = 30               # playhead refresh while a take plays
PLAY_STYLE = "background-color: #4CAF50; color: white; padding: 5px 10px; border-radius: 5px;"
STOP_STYLE = "background-color: #c0392b; color: white; padding: 5px 10px; border-radius: 5px;"
COMPARE_PEN = pg.mkPen((220, 220, 220, 150), width=1.5, style=Qt.PenStyle.DashLine)
_player = Player()              # one playback at a time, for cards made without a core


def register_colors(tracks):
    """Per-frame colour of the register track (fry grey), or None if the take has none."""
    zones = take_view.register_zones(tracks)
    return None if zones is None else [REGISTER_COLORS[z] if z else None for z in zones]


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
            self.text_r.setText(formants_label(f1, f2, f3))
            self.text_r.setPos(self.ax_res.vb.viewRange()[0][0], self.ax_res.vb.viewRange()[1][1])


class NoteScoreChart(pg.PlotWidget):
    """Per-note tuning error of an exercise (take_view.exercise_notes), with the in-tune zone shaded."""

    def __init__(self, notes, in_tune_cents):
        super().__init__(background="#2b2b2b")
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.setMenuEnabled(False)
        self.setFixedHeight(120)
        self.getAxis("left").setLabel("¢")
        zone = pg.LinearRegionItem((-in_tune_cents, in_tune_cents), orientation="horizontal",
                                   movable=False, brush=pg.mkBrush(46, 204, 113, 40), pen=pg.mkPen(None))
        self.addItem(zone)
        xs = np.arange(len(notes))
        heights = [n["height"] for n in notes]
        brushes = [NOTE_COLORS[n["zone"]] for n in notes]
        self.addItem(pg.BarGraphItem(x=xs, height=heights, width=0.7, brushes=brushes, pen=None))
        # Dots keep spot-on notes (zero-height bars) visible.
        sung = [i for i, n in enumerate(notes) if n["cents"] is not None]
        self.addItem(pg.ScatterPlotItem([xs[i] for i in sung], [heights[i] for i in sung], size=8,
                                        pen=None, brush=[brushes[i] for i in sung]))
        missed = [i for i, n in enumerate(notes) if n["cents"] is None]
        if missed:
            self.plot(missed, [0] * len(missed), pen=None, symbol="x", symbolBrush="#888", symbolPen="#888")
        self.getAxis("bottom").setTicks([[(i, n["label"]) for i, n in enumerate(notes)]])
        limit = take_view.NOTE_CHART_CENTS
        self.setYRange(-limit, limit, padding=0)
        self.setXRange(-0.6, len(notes) - 0.4, padding=0)


class UntrustedMetric(QWidget):
    """Stands in for a take bar whose reading the recording can't support: a dash and the reason, no bar."""

    def __init__(self, key, reason):
        super().__init__()
        self.key = key
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 4, 0, 4)
        self.title = QLabel(take_view.untrusted_title(key, reason))
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size: 11px; color: #888;")
        box.addWidget(self.title)

    def show_reading(self, reading):
        pass


class TakeCard(QFrame):
    """One take's report. `audio` returns its samples (loaded on demand for saved takes);
    `on_rename(card, title)` and `on_delete(card)` manage it. `compare_options(card)` lists
    [(label, take)] to compare with, `compare_audio(take)` loads one. `player` plays it (one card at a time),
    `runner` renders shifts."""

    def __init__(self, data, count, profile, on_set_baseline, on_set_target,
                 on_rename=None, on_delete=None, audio=None, on_label=None,
                 compare_options=None, compare_audio=None, player=None, runner=None):
        super().__init__()
        self.data, self.profile, self.number = data, profile, count
        self.on_label, self.on_rename, self.on_delete = on_label, on_rename, on_delete
        self.on_set_baseline, self.on_set_target = on_set_baseline, on_set_target
        self.compare_options, self.compare_audio = compare_options, compare_audio
        self.audio = audio or (lambda: data["audio"])
        self.player = player or _player
        self.runner = runner
        self._unsubscribe = self.player.on_finished(self._finished)
        self.compare = None             # the take played and drawn alongside this one
        self.start_at = 0.0             # where Play starts (s): set by clicking the graph
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
        self._tracks = None
        self._build()

    def dispose(self):
        """Before the card goes: stop its playback and stop listening to the player."""
        self.stop_playback()
        self._unsubscribe()

    @property
    def playing(self):
        return self.player.playing and self.player.owner is self

    @property
    def clock(self):
        return self.player.clock

    @clock.setter
    def clock(self, clock):
        self.player.clock = clock

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
        self._tracks = data["tracks"]
        view = take_view.card(data, self.profile, count)
        self.quality = view["quality"]
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header
        header = QHBoxLayout()
        placeholder, tooltip = take_view.title_hint(data, count)
        self.title = QLineEdit(data.get("title") or "")
        self.title.setPlaceholderText(placeholder)
        self.title.setToolTip(tooltip)
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

        if view["source"]:
            source = QLabel(view["source"])
            source.setStyleSheet("color: #aaa; font-size: 11px;")
            layout.addWidget(source)

        # --- QUALITY AND ADVICE ---
        tier = view["tier"]
        self.quality_chip = QLabel(f"● {view['tier_text']}")
        self.quality_chip.setStyleSheet(f"color: {TIER_COLORS[tier]}; font-size: 12px; font-weight: bold;")
        self.quality_chip.setToolTip(view["tier_tooltip"])
        layout.addWidget(self.quality_chip)
        if view["reasons"]:
            self.quality_reasons = QLabel(view["reasons"])
            self.quality_reasons.setWordWrap(True)
            self.quality_reasons.setStyleSheet("color: #bbb; font-size: 11px;")
            layout.addWidget(self.quality_reasons)
        if view["hiss"]:
            info = QLabel(view["hiss"])
            info.setWordWrap(True)
            info.setStyleSheet("color: #aaa; font-size: 11px;")
            layout.addWidget(info)
        self.advice_box = QVBoxLayout()
        layout.addLayout(self.advice_box)

        self.shift_panel = ShiftPanel(data, self.on_set_target, audio=self.audio, runner=self.runner,
                                      player=self.player)
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
        if view["exercise"]:
            label = QLabel(view["exercise"])
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 12px;")
            layout.addWidget(label)
            if view["exercise_notes"] is not None:
                layout.addWidget(NoteScoreChart(view["exercise_notes"], take_view.IN_TUNE_CENTS))
        if view["singing"]:
            label = QLabel(view["singing"])
            label.setWordWrap(True)
            label.setStyleSheet("color: #ddd; font-size: 12px;")
            layout.addWidget(label)

        # --- REGISTER ---
        self.register_box = self._build_register_row()
        layout.addWidget(self.register_box)
        if view["egg_warning"]:
            warning = QLabel(view["egg_warning"])
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {OFF_TARGET_COLOR}; font-size: 12px;")
            layout.addWidget(warning)

        # --- METRIC BARS ---
        trust, why = self.quality["trust"], self.quality["why_untrusted"]
        self.bars = {}
        for key in view["bar_keys"]:
            if key in take_view.TRUSTED_BARS and not trust[key]:
                self.bars[key] = UntrustedMetric(key, why[key])
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
        """Show the register row and colour the graph (take_view.register_row)."""
        row = take_view.register_row(self.data, self.profile, self.quality)
        self.graph.set_register(self.data["tracks"], show=row["show_track"])
        self.register_title.setText(row["title"])
        self.register_bar.set_segments([(share, REGISTER_COLORS[zone]) for share, zone in row["segments"]])
        if row["placeholder"] is not None:
            self.register_text.setText(f'<span style="color:#888">{row["placeholder"]}</span>')
        else:
            self.register_text.setText(" · ".join(f'<span style="color:{REGISTER_COLORS[zone]}">{text}</span>'
                                                  for zone, text in row["parts"]))

    def mode(self):
        return default_mode(self.data)

    def refresh_advice(self):
        """Up to three next steps from the take and the targets, at the top of the card."""
        while self.advice_box.count():
            item = self.advice_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.advice = take_view.advice(self.data, self.profile, self.quality)
        for message in self.advice:
            label = QLabel("→ " + message["text"])
            label.setWordWrap(True)
            color = "#ddd" if message["severity"] in ("info", "tentative") else "#fff"
            label.setStyleSheet(f"color: {color}; font-size: 12px; padding: 2px 0;"
                                + (" font-style: italic;" if message["severity"] == "tentative" else ""))
            self.advice_box.addWidget(label)

    def refresh_profile(self):
        self.refresh_register()
        self.refresh_advice()
        text = take_view.time_in_target(self.data, self.profile, self.quality)
        self.time_in_target.setText(text)
        self.time_in_target.setVisible(bool(text))
        readings = take_view.bars(self.data, self.profile, self.quality)
        for key, bar in self.bars.items():
            bar.show_reading(readings[key])

    def take_updated(self):
        """The core changed the take: its title, label, register scores or (re-analysis) everything."""
        if self.data["tracks"] is not self._tracks:
            self.reload()
            return
        self.title.setText(self.data.get("title") or "")
        self.update_saved_state()
        self.refresh_register()

    def _title_edited(self):
        title = self.title.text().strip()
        if self.on_rename and title != (self.data.get("title") or ""):
            self.on_rename(self, title)

    def update_saved_state(self):
        """Date, and where the take is stored."""
        text, tooltip = take_view.saved_state(self.data)
        self.date.setText(text)
        self.date.setToolTip(tooltip)

    # --- PLAYBACK ---

    def _update_play_text(self):
        self.play_btn.setText(take_view.play_label(self.playing, self.shift_btn.isChecked(), self.compare is not None))
        if self.playing:
            self.play_btn.setIcon(qta.icon("fa5s.stop", color="white"))
            self.play_btn.setStyleSheet(STOP_STYLE)
        else:
            self.play_btn.setIcon(qta.icon("fa5s.play", color="white"))
            self.play_btn.setStyleSheet(PLAY_STYLE)

    def play_audio(self):
        """Play from the start (or the point clicked on the graph); while playing, stop."""
        if self.playing:
            self.stop_playback()
        else:
            self.start_playback(self.start_at)

    def start_playback(self, start=0.0):
        if self.shift_panel.isVisible():
            self.player.track_take(np.asarray(self.audio(), dtype=float), start, owner=self)
            self.shift_panel.play_shifted()
        else:
            other = None
            if self.compare is not None and self.compare_audio:
                other = self.compare_audio(self.compare)
            self.player.play_take(self.audio(), start, then=other, owner=self)
        self.timer.start()
        self._update_play_text()
        self._tick()

    def _tick(self):
        """Move the playhead to where playback has got to; stop at the end."""
        if self.player.owner is not self:
            self.stop_playback()
            return
        position = self.player.position()
        if not position["playing"]:
            return                          # the player stopped: _finished resets the card
        self.graph.set_playhead(position["seconds"])
        if self.compare is not None:
            self.compare_label.setText(take_view.compare_text(self.compare, position["piece"]))

    def _finished(self, owner):
        if owner is self:
            self._reset_playback()

    def stop_playback(self):
        if self.playing:
            self.player.stop()              # calls _finished
        else:
            self._reset_playback()

    def _reset_playback(self):
        self.timer.stop()
        self.start_at = 0.0
        self.graph.set_playhead(None)
        self.compare_label.setText(take_view.compare_text(self.compare))
        self._update_play_text()

    def seek(self, seconds):
        """A click on the graph: play from there, or if stopped, start there next time."""
        if self.playing:
            self.start_playback(seconds)
            return
        self.start_at = seconds
        self.graph.set_playhead(seconds)

    # --- COMPARISON ---

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
        self.compare_label.setText(take_view.compare_text(take))
        self._update_play_text()
