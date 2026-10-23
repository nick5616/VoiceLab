"""The live panel: mode toggle, pitch and spectrum views, meters and input badges. It draws the live
session's snapshots (live_session.LiveSession); every reading, verdict and text comes from there."""
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame, QTabWidget,
                             QButtonGroup)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
import qtawesome as qta

from display import BOTH_MODES
from live_session import COACH_IDLE, LIVE_METERS
from ui.live_views import PitchView, SpectrumView, PitchResonancePlane
from ui.meters import BUTTON_STYLE, METER_MIN_WIDTH, MetricBar, OFF_TARGET_COLOR, ON_TARGET_COLOR, fix_text_height
from ui.mic_check_dialog import MicCheckDialog
from ui.vowel_check import ReadingPrompt
from ui.widgets import ElidedLabel, ResponsiveGrid

NOISE_BADGE_WIDTH = 150
NOISE_BADGE_STYLE = ("QPushButton {{ color: {color}; font-size: 11px; font-weight: bold; border: none; "
                     "text-align: right; padding: 0px; }}")
SEVERITY_COLORS = {"ok": ON_TARGET_COLOR, "warn": OFF_TARGET_COLOR, "error": OFF_TARGET_COLOR, "unknown": "#aaa"}
LIVE_VIEW_HEIGHT = 220


class LivePanel(QFrame):
    """Tabbed live views and meters for the core's live session, in Speak or Sing mode."""

    mic_check_done = pyqtSignal(dict)       # the mic check's result dict (mic_check.analyze_check)

    def __init__(self, core, on_edit_targets):
        super().__init__()
        self.core = core
        self.session = core.live
        self.profile = core.profile
        self.mode = None
        self._engine = None
        self.setStyleSheet("QFrame { background-color: #2b2b2b; border-radius: 10px; } "
                           "QLabel { color: #ffffff; }")
        layout = QVBoxLayout(self)

        toggle_style = (f"QPushButton {{ {BUTTON_STYLE} }} "
                        "QPushButton:checked { background-color: #3498db; }")
        header = QHBoxLayout()
        self.mode_buttons = {}
        mode_group = QButtonGroup(self)
        for mode, label, icon in (("speak", "Speak", "fa5s.comment"), ("sing", "Sing", "fa5s.music")):
            btn = QPushButton(qta.icon(icon, color="white"), f" {label}")
            btn.setCheckable(True)
            btn.setStyleSheet(toggle_style)
            btn.clicked.connect(lambda _, m=mode: self.core.set_mode(m))
            mode_group.addButton(btn)
            self.mode_buttons[mode] = btn
            header.addWidget(btn)
        targets_btn = QPushButton(qta.icon("fa5s.bullseye", color="white"), " Targets")
        targets_btn.setStyleSheet(BUTTON_STYLE)
        targets_btn.clicked.connect(on_edit_targets)
        self.read_btn = QPushButton(qta.icon("fa5s.book-open", color="white"), " Read aloud")
        self.read_btn.setCheckable(True)
        self.read_btn.setStyleSheet(toggle_style)
        # Header readouts have fixed widths: text changing size would resize the
        # panel, and with it everything beside it.
        self.note_label = QLabel("—")
        self.note_label.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        self.note_label.setFixedWidth(QFontMetrics(self.note_label.font()).horizontalAdvance(
            "C#5 -50¢ · 1200 Hz") + 6)
        self.note_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        fix_text_height(self.note_label, "C#5 -50¢ · 1200 Hz —")
        # The noise badge: the main input problem found in the last few seconds, or the SNR.
        # Clicking it opens the mic check.
        self.noise_badge = QPushButton("SNR —")
        self.noise_badge.setFlat(True)
        self.noise_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.noise_badge.setFixedWidth(NOISE_BADGE_WIDTH)
        self.noise_badge.setStyleSheet(NOISE_BADGE_STYLE.format(color="#aaa"))   # styled from the start: no resize
        self.noise_badge.clicked.connect(self.show_mic_check)
        self.path_badge = QLabel("slow audio path")
        self.path_badge.setStyleSheet(f"color: {OFF_TARGET_COLOR}; font-size: 11px; font-weight: bold;")
        self.path_badge.setToolTip(self.session.input["slow_path_tooltip"])
        self.path_badge.hide()
        self.mic_dialog = None
        self.last_mic_check = None
        self.clip_badge = QLabel("CLIPPING")
        self.clip_badge.setStyleSheet("color: white; background-color: #e74c3c; font-size: 11px; "
                                      "font-weight: bold; padding: 1px 5px; border-radius: 3px;")
        self.clip_badge.setToolTip(self.session.input["clip_tooltip"])
        policy = self.clip_badge.sizePolicy()
        policy.setRetainSizeWhenHidden(True)    # appearing doesn't shift the header
        self.clip_badge.setSizePolicy(policy)
        self.clip_badge.hide()
        self.egg_badge = QLabel("EGG ●")
        self.egg_badge.setFixedWidth(52)
        self.egg_badge.hide()
        header.addSpacing(8)
        header.addWidget(targets_btn)
        header.addWidget(self.read_btn)
        header.addStretch()
        header.addWidget(self.clip_badge)
        header.addWidget(self.egg_badge)
        header.addWidget(self.note_label)
        layout.addLayout(header)

        # One live coaching line (coach.advice on the last few seconds), and the input badges.
        status = QHBoxLayout()
        self.coach_label = ElidedLabel(COACH_IDLE)
        self.coach_label.setStyleSheet("color: #ddd; font-size: 12px;")
        fix_text_height(self.coach_label, "Hz ≈ ✓ ¢ ± — ·")
        status.addWidget(self.coach_label, 1)
        status.addWidget(self.path_badge)
        status.addWidget(self.noise_badge)
        self.noise_badge.setFixedHeight(self.coach_label.height())
        layout.addLayout(status)

        self.reading_prompt = ReadingPrompt(on_passage=self._passage_changed)
        self.reading_prompt.hide()
        self.read_btn.toggled.connect(self._reading_toggled)
        layout.addWidget(self.reading_prompt)

        self.pitch_view = PitchView()
        engine = core.engine
        self.spectrum_view = SpectrumView(engine.analyzer.spectrum_freqs)
        self.plane = PitchResonancePlane()
        self.tabs = QTabWidget()
        # Grows when the window gives the live panel more room (side-by-side layout).
        self.tabs.setMinimumHeight(LIVE_VIEW_HEIGHT + 30)
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: none; }
            QTabBar::tab { background: #333; color: #aaa; padding: 5px 14px; border-radius: 4px; margin-right: 4px; }
            QTabBar::tab:selected { background: #3498db; color: white; }
        """)
        self.tab_modes = []
        self.default_tab = {}
        self.add_tab(self.pitch_view, "Pitch", BOTH_MODES, default_for="speak")
        self.add_tab(self.spectrum_view, "Spectrum", BOTH_MODES)
        self.add_tab(self.plane, "Pitch × Resonance", {"speak"})
        layout.addWidget(self.tabs, 1)

        self.meters = {}
        self.meter_grid = ResponsiveGrid(METER_MIN_WIDTH)
        for key, modes in LIVE_METERS:
            meter = MetricBar(key, centered=(key == "tuning"), live=True)
            meter.modes = modes
            self.meters[key] = meter
            self.meter_grid.addWidget(meter)
        layout.addWidget(self.meter_grid)
        for event, handler in (("mode_changed", self.apply_mode), ("profile_changed", self.refresh_profile),
                               ("mic_changed", lambda info: self.apply_mode(self.session.mode))):
            core.events.subscribe(event, handler)
        self.refresh_profile()

    @property
    def engine(self):
        """The engine the mic check records through: the core's, unless one is set here."""
        return self._engine if self._engine is not None else self.core.engine

    @engine.setter
    def engine(self, engine):
        self._engine = engine

    def add_tab(self, widget, name, modes, default_for=None):
        self.tabs.addTab(widget, name)
        self.tab_modes.append((widget, modes))
        if default_for:
            self.default_tab[default_for] = widget
        if self.mode:
            self.tabs.setTabVisible(self.tabs.indexOf(widget), self.mode in modes)

    def apply_mode(self, mode):
        """Show the views and meters of `mode` (the core already switched its live session)."""
        self.mode = mode
        self.mode_buttons[mode].setChecked(True)
        for widget, modes in self.tab_modes:
            self.tabs.setTabVisible(self.tabs.indexOf(widget), mode in modes)
        for key, meter in self.meters.items():
            meter.setVisible(self.session.visible(key))
        self.pitch_view.set_mode(mode)
        self.egg_badge.setVisible(self.session.egg_on)
        self._show_egg(self.session.egg_state())
        self.refresh_profile()
        self.meter_grid.relayout()
        self.read_btn.setVisible(mode == "speak")
        if mode != "speak":
            self.read_btn.setChecked(False)
        if mode in self.default_tab:
            self.tabs.setCurrentWidget(self.default_tab[mode])

    def refresh_profile(self):
        """Redraw what the profile sets: meter bands and baselines, the pitch view's and plane's references."""
        p = self.profile
        for key, meter in self.meters.items():
            meter.show_reading(self.session.metrics[key])
        self.pitch_view.set_reference(p.baseline_value("pitch"), p.target("pitch"))
        baseline = (p.baseline_value("pitch"), p.baseline_value("delta_f")) if p.baseline else None
        self.plane.set_reference(baseline, p.target("pitch"), p.target("delta_f"))

    # --- READING ALOUD ---

    def _reading_toggled(self, on):
        self.reading_prompt.setVisible(on)
        self.core.set_reading(self.reading_prompt.index if on else None)

    def _passage_changed(self, index):
        if self.read_btn.isChecked():
            self.core.set_reading(index)

    # --- FRAMES ---

    def show_frame(self, frame, snapshot):
        """Draw one live frame's snapshot (and the frame's spectrum)."""
        self.pitch_view.show_history(snapshot["pitch_history"])
        self.spectrum_view.update_frame(frame)
        for key, meter in self.meters.items():
            meter.show_reading(snapshot["metrics"][key])
        note = snapshot["note"]
        if self.note_label.text() != note["text"]:
            self.note_label.setText(note["text"])
        style = f"color: {ON_TARGET_COLOR if note['on_target'] else 'white'};"
        if self.note_label.styleSheet() != style:
            self.note_label.setStyleSheet(style)
        coach = snapshot["coach"]
        if self.coach_label.text() != coach["line"]:
            self.coach_label.setText(coach["line"])
        if self.coach_label.toolTip() != coach["full"]:
            self.coach_label.setToolTip(coach["full"])
        self._show_input(snapshot["input"])
        if snapshot["egg"]["on"]:
            self._show_egg(snapshot["egg"])
        self.plane.update_point(snapshot["plane"]["f0"], snapshot["plane"]["delta_f"])

    def _show_input(self, info):
        self.clip_badge.setVisible(info["clipping"])
        if self.noise_badge.text() != info["badge"]:
            self.noise_badge.setText(info["badge"])
        style = NOISE_BADGE_STYLE.format(color=SEVERITY_COLORS.get(info["severity"], "#aaa"))
        if self.noise_badge.styleSheet() != style:
            self.noise_badge.setStyleSheet(style)
        self.noise_badge.setToolTip(info["tooltip"])
        self.path_badge.setVisible(info["slow_path"])
        self.path_badge.setToolTip(info["slow_path_tooltip"])

    def _show_egg(self, egg):
        style = f"color: {ON_TARGET_COLOR if egg['signal'] else '#777'}; font-size: 11px; font-weight: bold;"
        if self.egg_badge.styleSheet() != style:
            self.egg_badge.setStyleSheet(style)
        self.egg_badge.setToolTip(egg["tooltip"])

    # --- MIC CHECK ---

    def show_mic_check(self):
        """Open the mic check dialog (non-modal); its result is stored in last_mic_check and sent
        through mic_check_done. Does nothing without an engine."""
        engine = self.engine
        if engine is None:
            return None
        self.mic_dialog = MicCheckDialog(engine, self)
        self.mic_dialog.finished_check.connect(self._mic_checked)
        self.mic_dialog.show()
        return self.mic_dialog

    def _mic_checked(self, result):
        self.last_mic_check = result
        self.mic_check_done.emit(result)
