"""The live panel: mode toggle, pitch and spectrum views, meters and noise badges."""
import time
from collections import deque

import numpy as np
from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame, QTabWidget, QButtonGroup
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
import qtawesome as qta

from analysis import (IN_TUNE_CENTS,
    MIN_RELIABLE_SNR,
    VOICE_BAND_HZ,
    NoiseMonitor,
    hz_to_midi,
    hz_to_note,
    pitch_range_st,
    vibrato_stats)
from engine import UPDATE_INTERVAL
from exercise import midi_name
from live_views import PitchView, SpectrumView, PitchResonancePlane, pitch_label
from speech import ReadingPrompt
from widgets import ResponsiveGrid
from ui_common import (BOTH_MODES,
    BUTTON_STYLE,
    METER_MIN_WIDTH,
    MetricBar,
    OFF_TARGET_COLOR,
    ON_TARGET_COLOR,
    fix_text_height,
    hiss_note)

# Live meters in display order, with the modes they appear in.
LIVE_METERS = [
    ("tuning", {"sing"}), ("vibrato", {"sing"}), ("delta_f", {"speak"}), ("h1h2", BOTH_MODES),
    ("cpp", BOTH_MODES), ("ring", BOTH_MODES), ("pitch_range", {"speak"}), ("level", BOTH_MODES),
]
SMOOTHED_KEYS = ["delta_f", "h1h2", "cpp", "ring"]   # per-frame metrics, smoothed for display

INTONATION_WINDOW_SECONDS = 6
VIBRATO_WINDOW_SECONDS = 1.2
NOISE_WINDOW_SECONDS = 10

NOISE_BADGE_EVERY = 10          # live frames between SNR badge updates
LIVE_TEXT_SECONDS = 0.2         # live numbers change at most this often, so they can be read
NOTE_WINDOW_SECONDS = 0.25      # the note readout shows the median pitch over this
NOTE_HOLD_SECONDS = 0.5         # ...and keeps the last note this long through gaps
CLIP_BADGE_SECONDS = 1.0        # the clipping badge stays up this long after a clip

LIVE_VIEW_HEIGHT = 220

# Exponential smoothing for live meters (~200 ms time constant).
METER_SMOOTHING = 1 - np.exp(-UPDATE_INTERVAL / 0.2)


class LivePanel(QFrame):
    """Tabbed live views and meters fed by the LiveEngine, in Speak or Sing mode."""

    mode_changed = pyqtSignal(str)

    def __init__(self, spectrum_freqs, profile, on_edit_targets):
        super().__init__()
        self.profile = profile
        self.mode = None
        # Returns the exercise's current target note (MIDI) or None.
        self.target_provider = lambda: None
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
            btn.clicked.connect(lambda _, m=mode: self.set_mode(m, user=True))
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
        self.note_shown_at = 0.0
        self.last_voiced_at = 0.0
        self.snr_badge = QLabel("SNR —")
        self.snr_badge.setStyleSheet("color: #aaa; font-size: 11px;")
        self.snr_badge.setFixedWidth(80)
        self.snr_badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.snr_badge.setToolTip("Voice-to-noise ratio. Pause briefly so the room noise can be measured.")
        self.noise = NoiseMonitor(int(NOISE_WINDOW_SECONDS / UPDATE_INTERVAL))
        self.frames_seen = 0
        self.clip_badge = QLabel("CLIPPING")
        self.clip_badge.setStyleSheet("color: white; background-color: #e74c3c; font-size: 11px; "
                                      "font-weight: bold; padding: 1px 5px; border-radius: 3px;")
        self.clip_badge.setToolTip("The input is overloading. Lower the mic gain or move back a little: "
                                   "clipping adds harshness that inflates Ring and lowers Clarity.")
        policy = self.clip_badge.sizePolicy()
        policy.setRetainSizeWhenHidden(True)    # appearing doesn't shift the header
        self.clip_badge.setSizePolicy(policy)
        self.clip_badge.hide()
        self.clip_frames_left = 0
        header.addSpacing(8)
        header.addWidget(targets_btn)
        header.addWidget(self.read_btn)
        header.addStretch()
        header.addWidget(self.clip_badge)
        header.addWidget(self.snr_badge)
        header.addWidget(self.note_label)
        layout.addLayout(header)

        self.reading_prompt = ReadingPrompt()
        self.reading_prompt.hide()
        self.read_btn.toggled.connect(self.reading_prompt.setVisible)
        layout.addWidget(self.reading_prompt)

        self.pitch_view = PitchView()
        self.spectrum_view = SpectrumView(spectrum_freqs)
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
        self.meters["pitch_range"].label = f"Intonation Range (last {INTONATION_WINDOW_SECONDS} s)"
        self.meters["level"].label = "Input Level"
        # In-tune zone: ±IN_TUNE_CENTS on a ±50 cent bar.
        self.meters["tuning"].bar.band = (0.5 - IN_TUNE_CENTS / 100, 0.5 + IN_TUNE_CENTS / 100)
        self.smoothed = {key: np.nan for key in SMOOTHED_KEYS}
        self.smoothed_log_f0 = np.nan
        self.recent_f0 = deque(maxlen=int(INTONATION_WINDOW_SECONDS / UPDATE_INTERVAL))
        self.refresh_profile()

    def add_tab(self, widget, name, modes, default_for=None):
        self.tabs.addTab(widget, name)
        self.tab_modes.append((widget, modes))
        if default_for:
            self.default_tab[default_for] = widget
        if self.mode:
            self.tabs.setTabVisible(self.tabs.indexOf(widget), self.mode in modes)

    def set_mode(self, mode, user=False):
        self.mode = mode
        self.mode_buttons[mode].setChecked(True)
        for widget, modes in self.tab_modes:
            self.tabs.setTabVisible(self.tabs.indexOf(widget), mode in modes)
        for meter in self.meters.values():
            meter.setVisible(mode in meter.modes)
        self.meter_grid.relayout()
        self.read_btn.setVisible(mode == "speak")
        if mode != "speak":
            self.read_btn.setChecked(False)
        if mode in self.default_tab:
            self.tabs.setCurrentWidget(self.default_tab[mode])
        if user:
            self.mode_changed.emit(mode)

    def refresh_profile(self):
        p = self.profile
        for key, meter in self.meters.items():
            if key not in ("tuning", "vibrato"):   # fixed scales, no profile targets
                meter.set_reference(p)
        self.pitch_view.set_reference(p.baseline_value("pitch"), p.target("pitch"))
        baseline = (p.baseline_value("pitch"), p.baseline_value("delta_f")) if p.baseline else None
        self.plane.set_reference(baseline, p.target("pitch"), p.target("delta_f"))

    @staticmethod
    def _smooth(prev, value):
        return value if np.isnan(prev) else prev + METER_SMOOTHING * (value - prev)

    def update_frame(self, frame):
        f0 = frame["f0"]
        self.pitch_view.update_frame(frame)
        self.spectrum_view.update_frame(frame)
        self.meters["level"].set_value(frame["level"], self.profile)
        self.noise.add(frame)
        self._update_clip_badge(frame.get("clipped", False))
        self.frames_seen += 1
        if self.frames_seen % NOISE_BADGE_EVERY == 0:
            self._update_snr_badge()
        self.recent_f0.append(f0)
        self.meters["pitch_range"].set_value(pitch_range_st(self.recent_f0), self.profile)
        self._update_vibrato()
        self._update_note_label(f0)

        if np.isnan(f0):
            self.meters["tuning"].set_value(None, self.profile, note="—")
            self.plane.update_point(np.nan, np.nan)
            return
        self._update_tuning(f0)
        self.smoothed_log_f0 = self._smooth(self.smoothed_log_f0, np.log2(f0))
        for key in SMOOTHED_KEYS:
            meter, value = self.meters[key], frame[key]
            if not np.isnan(value):
                self.smoothed[key] = self._smooth(self.smoothed[key], value)
            if key == "delta_f" and not frame["resonance_reliable"]:
                meter.set_value(None, self.profile, note="pitch too high to measure")
            else:
                meter.set_value(self.smoothed[key], self.profile)

        resonance = self.smoothed["delta_f"] if frame["resonance_reliable"] else np.nan
        self.plane.update_point(2 ** self.smoothed_log_f0, resonance)

    def _update_note_label(self, f0):
        """The note readout: median pitch of the last ~quarter second, changing
        at most every LIVE_TEXT_SECONDS and held briefly through gaps, so it
        can be read rather than flickering."""
        now = time.monotonic()
        if not np.isnan(f0):
            self.last_voiced_at = now
        if now - self.note_shown_at < LIVE_TEXT_SECONDS:
            return
        recent = np.array(list(self.recent_f0)[-int(NOTE_WINDOW_SECONDS / UPDATE_INTERVAL):])
        recent = recent[~np.isnan(recent)]
        if len(recent):
            pitch = float(np.median(recent))
            on_target = self.profile.in_target("pitch", pitch)
            self.note_label.setText(pitch_label(pitch))
            self.note_label.setStyleSheet(f"color: {ON_TARGET_COLOR if on_target else 'white'};")
        elif now - self.last_voiced_at >= NOTE_HOLD_SECONDS:
            self.note_label.setText("—")
            self.note_label.setStyleSheet("color: white;")
        else:
            return
        self.note_shown_at = now

    def _update_clip_badge(self, clipped):
        if clipped:
            self.clip_frames_left = int(CLIP_BADGE_SECONDS / UPDATE_INTERVAL)
        elif self.clip_frames_left:
            self.clip_frames_left -= 1
        self.clip_badge.setVisible(self.clip_frames_left > 0)

    def _update_snr_badge(self):
        snr, snr_full = self.noise.snr()
        if snr is None:
            self.snr_badge.setText("SNR —")
            self.snr_badge.setStyleSheet("color: #aaa; font-size: 11px;")
            return
        ok = snr >= MIN_RELIABLE_SNR
        self.snr_badge.setText(f"SNR {snr:.0f} dB")      # orange when noisy; the tooltip says why
        self.snr_badge.setStyleSheet(f"color: {ON_TARGET_COLOR if ok else OFF_TARGET_COLOR}; "
                                     "font-size: 11px; font-weight: bold;")
        tip = [f"Voice-to-noise ratio in the voice band (0–{VOICE_BAND_HZ // 1000} kHz), where "
               "VoiceLab measures, over the last 10 s."]
        if hiss_note(snr, snr_full):
            tip.append(hiss_note(snr, snr_full))
        if not ok:
            tip.append(f"Below {MIN_RELIABLE_SNR} dB, Clarity, Fold Contact and Ring become unreliable; "
                       "pitch and resonance hold up better. Move closer to the mic or reduce room noise.")
        self.snr_badge.setToolTip(" ".join(tip))

    def _update_tuning(self, f0):
        """Cents from the exercise's target note, or from the nearest note."""
        target = self.target_provider()
        if target is not None:
            cents = 100 * (float(hz_to_midi(f0)) - target)
            cents -= 1200 * round(cents / 1200)      # any octave counts
            text = f"{cents:+.0f}¢ vs {midi_name(target)}"
        else:
            note, cents = hz_to_note(f0)
            text = f"{cents:+d}¢ from {note}"
        self.meters["tuning"].set_value(cents, self.profile, display=text,
                                        on_target=abs(cents) <= IN_TUNE_CENTS)

    def _update_vibrato(self):
        """Vibrato over the last ~1.2 s, only while a note is held."""
        n = int(VIBRATO_WINDOW_SECONDS / UPDATE_INTERVAL)
        recent = np.array(list(self.recent_f0)[-n:])
        meter = self.meters["vibrato"]
        if len(recent) < n or np.isnan(recent).any():
            meter.set_value(None, self.profile, note="hold a note")
            return
        cents = 100 * hz_to_midi(recent)
        if np.ptp(cents) > 300:                       # moving between notes
            meter.set_value(None, self.profile, note="hold a note")
            return
        vib = vibrato_stats(cents, 1 / UPDATE_INTERVAL)
        if vib["rate"] is None:
            meter.set_value(0, self.profile, display="straight tone")
        else:
            meter.set_value(vib["extent"], self.profile,
                            display=f"{vib['rate']:.1f} Hz · ±{vib['extent']:.0f}¢")
