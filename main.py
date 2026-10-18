import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import sounddevice as sd
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QScrollArea,
                             QFrame, QSizePolicy, QTabWidget, QDialog, QGridLayout,
                             QCheckBox, QDoubleSpinBox, QDialogButtonBox, QButtonGroup,
                             QFileDialog, QMessageBox, QLineEdit, QComboBox, QSplitter)
from PyQt6.QtCore import Qt, QSize, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics, QPainter, QColor, QPen
import qtawesome as qta

from analysis import (IN_TUNE_CENTS, MIN_RELIABLE_SNR, SAMPLE_RATE, VOICE_BAND_HZ, NoiseMonitor, analyze_take,
                      hz_to_midi, hz_to_note, pitch_range_st, vibrato_stats, vocal_tract_length_cm)
from engine import (AnalysisWorker, LiveEngine, device_index, input_devices, take_analysis_worker,
                    UPDATE_INTERVAL)
from exercise import ExerciseView, midi_name
import isolation
from progress import ProgressDialog, collect_entries
from shift_panel import ShiftPanel
from live_views import PitchView, SpectrumView, PitchResonancePlane, pitch_label
from profile_store import Profile, TARGET_KEYS, target_from_value
from speech import ReadingPrompt, VowelCheck
from take_store import IMPORT_MAX_SECONDS, TakeStore, load_audio, new_uid
from voice_map import VoiceMapView
from widgets import ResponsiveGrid

# --- METRIC DISPLAY ---
# key: (label, min, max, low label, high label, value format, scale)
# `scale` maps values onto the bar; pitch uses log2 so octaves are equal widths.
METRIC_DEFS = {
    "pitch": ("Pitch", 60, 1200, "Low", "High", "{:.0f} Hz", np.log2),
    "delta_f": ("Resonance (ΔF)", 900, 1300, "Dark", "Bright", "{:.0f} Hz", None),
    "h1h2": ("Fold Contact (H1*–H2*)", -5, 15, "Heavy", "Light", "{:+.1f} dB", None),
    "cpp": ("Clarity (CPP)", 8, 30, "Breathy", "Clear", "{:.1f} dB", None),
    "ring": ("Ring (2–4 kHz)", -30, 0, "Muffled", "Ringing", "{:+.1f} dB", None),
    "pitch_range": ("Intonation Range", 0, 12, "Flat", "Expressive", "{:.1f} st", None),
    "level": ("Level", -60, 0, "Quiet", "Loud", "{:.0f} dBFS, uncalibrated", None),
    "tuning": ("Tuning", -50, 50, "Flat", "Sharp", "{:+.0f}¢", None),
    "vibrato": ("Vibrato", 0, 100, "Straight", "Wide", "±{:.0f}¢", None),
}
BOTH_MODES = {"speak", "sing"}
# Live meters in display order, with the modes they appear in.
LIVE_METERS = [
    ("tuning", {"sing"}), ("vibrato", {"sing"}), ("delta_f", {"speak"}), ("h1h2", BOTH_MODES),
    ("cpp", BOTH_MODES), ("ring", BOTH_MODES), ("pitch_range", {"speak"}), ("level", BOTH_MODES),
]
SMOOTHED_KEYS = ["delta_f", "h1h2", "cpp", "ring"]   # per-frame metrics, smoothed for display
TAKE_BAR_KEYS = ["pitch", "delta_f", "h1h2", "cpp", "ring", "pitch_range", "level"]
INTONATION_WINDOW_SECONDS = 6
VIBRATO_WINDOW_SECONDS = 1.2
NOISE_WINDOW_SECONDS = 10
HISS_GAP_DB = 6                 # band SNR this much above full-band SNR = noise is mostly hiss
NOISE_BADGE_EVERY = 10          # live frames between SNR badge updates
LIVE_TEXT_SECONDS = 0.2         # live numbers change at most this often, so they can be read
NOTE_WINDOW_SECONDS = 0.25      # the note readout shows the median pitch over this
NOTE_HOLD_SECONDS = 0.5         # ...and keeps the last note this long through gaps
CLIP_BADGE_SECONDS = 1.0        # the clipping badge stays up this long after a clip
TAKES_PAGE = 20                 # take cards shown at first; older ones on request
AUDIO_FILE_FILTER = "Audio files (*.wav *.flac *.ogg *.mp3 *.aiff *.aif)"
# Targets dialog: key -> (spinbox min, max, decimals, unit)
TARGET_EDIT = {
    "pitch": (50, 1200, 0, "Hz"),
    "delta_f": (800, 1500, 0, "Hz"),
    "h1h2": (-20, 30, 1, "dB"),
    "cpp": (0, 50, 1, "dB"),
    "ring": (-50, 10, 1, "dB"),
    "pitch_range": (0, 24, 1, "st"),
}
LIVE_VIEW_HEIGHT = 220
METER_MIN_WIDTH = 300           # meters and take bars go two-up when this fits twice
WIDE_LAYOUT_WIDTH = 1100        # from this window width, live and takes sit side by side...
TAKES_COLUMN_WIDTH = 480        # ...if the takes still get about this much
# Exponential smoothing for live meters (~200 ms time constant).
METER_SMOOTHING = 1 - np.exp(-UPDATE_INTERVAL / 0.2)

BAR_COLORS = ("#3498db", "#9b59b6", "#e74c3c")
ON_TARGET_COLOR = "#2ecc71"
OFF_TARGET_COLOR = "#e67e22"
DISABLED_COLOR = "#555"
TARGET_BAND_COLOR = QColor(46, 204, 113, 70)
BUTTON_STYLE = "background-color: #3a3a3a; color: white; padding: 5px 10px; border-radius: 5px;"


def import_and_analyze(path, isolate):
    audio, trimmed = load_audio(path)
    if isolate:
        audio = isolation.isolate_vocals(audio, SAMPLE_RATE)
    result = analyze_take(audio)
    if "error" not in result:
        name = path.replace("\\", "/").split("/")[-1]
        notes = [name] + (["vocals isolated with Demucs"] if isolate else []) + \
                ([f"first {IMPORT_MAX_SECONDS} s"] if trimmed else [])
        result["source"] = " · ".join(notes)
    return result


def hiss_note(snr, snr_full):
    """Explains when most of the noise sits above the voice band, or None."""
    if snr is None or snr_full is None or snr - snr_full < HISS_GAP_DB:
        return None
    return (f"Most of the noise is high-pitched hiss above {VOICE_BAND_HZ // 1000} kHz "
            f"(full-band SNR {snr_full:.0f} dB). Measurements ignore that range.")


def load_folder(store):
    takes, problems = store.load_all()
    return {"takes": takes, "problems": problems}


def take_date(data):
    try:
        return datetime.fromisoformat(data["time"]).strftime("%a %b %d · %H:%M").replace(" 0", " ")
    except (KeyError, TypeError, ValueError):
        return ""


def fix_text_height(label, sample):
    """Pin a label's height to what `sample` needs, so changing text never
    changes its size."""
    text = label.text()
    label.ensurePolished()
    label.setText(sample)
    label.setFixedHeight(label.sizeHint().height())
    label.setText(text)


def bar_color(position, on_target):
    if on_target is not None:
        return ON_TARGET_COLOR if on_target else OFF_TARGET_COLOR
    return BAR_COLORS[0] if position < 0.33 else BAR_COLORS[1] if position < 0.66 else BAR_COLORS[2]


def format_value(key, value):
    if key == "pitch":
        note, cents = hz_to_note(value)
        return f"{note} {cents:+d}¢ · {value:.0f} Hz"
    if key == "delta_f":
        return f"{value:.0f} Hz, ≈{vocal_tract_length_cm(value):.1f} cm tract"
    return METRIC_DEFS[key][5].format(value)


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
        self.baseline = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track = QRectF(0, 2, w, h - 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#444"))
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
            p.setBrush(TARGET_BAND_COLOR)
            p.setPen(QPen(QColor(ON_TARGET_COLOR), 1))
            p.drawRect(QRectF(w * lo, 0, max(2.0, w * (hi - lo)), h - 1))
        if self.baseline is not None:
            p.setPen(QPen(QColor("white"), 2))
            x = w * self.baseline
            p.drawLine(int(x), 0, int(x), h)


class MetricBar(QWidget):
    """Labelled RangeBar for one metric, aware of baseline and target."""

    def __init__(self, key, centered=False, live=False):
        super().__init__()
        self.key = key
        self.modes = BOTH_MODES
        self.label, lo, hi, low_label, high_label, self.fmt, scale = METRIC_DEFS[key]
        self.scale = scale or (lambda v: v)
        self.lo, self.hi = self.scale(lo), self.scale(hi)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 4, 0, 4)

        self.title = QLabel(self.label)
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
        self.text_interval = LIVE_TEXT_SECONDS if live else 0
        self._text_shown_at = 0.0
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

    def _fraction(self, value):
        return float(np.clip((self.scale(value) - self.lo) / (self.hi - self.lo), 0, 1))

    def set_reference(self, profile):
        base = profile.baseline_value(self.key)
        target = profile.target(self.key)
        self.bar.baseline = self._fraction(base) if base is not None else None
        self.bar.band = tuple(self._fraction(v) for v in target) if target else None
        self.bar.update()

    def _set_title(self, text):
        """Live meters change their text at most every LIVE_TEXT_SECONDS, so
        numbers stay readable; the bar itself still moves every frame."""
        now = time.monotonic()
        if self.text_interval and now - self._text_shown_at < self.text_interval:
            return
        self._text_shown_at = now
        self.title.setText(text)

    def set_value(self, value, profile, note=None, display=None, on_target=None):
        """`note` marks the reading as unavailable and explains why; `display`
        overrides the value text; `on_target` overrides the profile's verdict."""
        if note or value is None or np.isnan(value):
            self._set_title(f"{self.label} — {note or 'no reading'}")
            self.bar.position, self.bar.color = None, DISABLED_COLOR
            self.bar.update()
            return
        parts = [display or format_value(self.key, value)]
        change = profile.change_text(self.key, value)
        if change:
            parts.append(change)
        if on_target is None:
            on_target = profile.in_target(self.key, value)
        title = f"{self.label} ({' · '.join(parts)})"
        if on_target is not None:
            title += "  ✓ on target" if on_target else "  off target"
        self._set_title(title)
        self.bar.position = self._fraction(value)
        self.bar.color = bar_color(self.bar.position, on_target)
        self.bar.update()


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


class TargetsDialog(QDialog):
    """Edit target ranges and manage the baseline."""

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.setWindowTitle("Targets & Baseline")
        self.setStyleSheet("""
            QDialog { background-color: #2b2b2b; } QLabel, QCheckBox { color: white; }
            QDoubleSpinBox { background: #1e1e1e; color: white; border: 1px solid #555; padding: 2px; }
        """)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Targets</b> — tick a metric to give it a target range."))
        grid = QGridLayout()
        self.rows = {}
        for row, key in enumerate(TARGET_KEYS):
            lo_lim, hi_lim, decimals, unit = TARGET_EDIT[key]
            check = QCheckBox(METRIC_DEFS[key][0])
            spins = []
            for _ in range(2):
                spin = QDoubleSpinBox()
                spin.setRange(lo_lim, hi_lim)
                spin.setDecimals(decimals)
                spin.setSingleStep(10 ** -decimals if decimals else (5 if unit == "Hz" else 1))
                spins.append(spin)
            hint = QLabel()
            hint.setStyleSheet("color: #aaa;")
            current = profile.target(key) or self._default_range(key)
            check.setChecked(profile.target(key) is not None)
            spins[0].setValue(current[0])
            spins[1].setValue(current[1])
            if key == "pitch":
                for spin in spins:
                    spin.valueChanged.connect(self._update_pitch_hint)
            grid.addWidget(check, row, 0)
            grid.addWidget(spins[0], row, 1)
            grid.addWidget(QLabel("to"), row, 2)
            grid.addWidget(spins[1], row, 3)
            grid.addWidget(QLabel(unit), row, 4)
            grid.addWidget(hint, row, 5)
            self.rows[key] = (check, spins, hint)
        layout.addLayout(grid)
        self._update_pitch_hint()

        layout.addSpacing(10)
        layout.addWidget(QLabel("<b>Baseline</b>"))
        self.baseline_label = QLabel()
        self.baseline_label.setWordWrap(True)
        layout.addWidget(self.baseline_label)
        clear_btn = QPushButton("Clear baseline")
        clear_btn.setStyleSheet(BUTTON_STYLE)
        clear_btn.clicked.connect(self._clear_baseline)
        layout.addWidget(clear_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self._show_baseline()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.setStyleSheet(f"QPushButton {{ {BUTTON_STYLE} }}")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _default_range(self, key):
        """Starting range for an unset target: around the baseline if there is one."""
        base = self.profile.baseline_value(key)
        if base is not None:
            return target_from_value(key, base)
        lo, hi = METRIC_DEFS[key][1:3]
        mid = np.sqrt(lo * hi) if key == "pitch" else (lo + hi) / 2
        return target_from_value(key, mid)

    def _update_pitch_hint(self):
        _, spins, hint = self.rows["pitch"]
        hint.setText(" – ".join(hz_to_note(s.value())[0] for s in spins))

    def _show_baseline(self):
        b = self.profile.baseline
        if not b:
            self.baseline_label.setText("No baseline set. Use “Set as baseline” on a take of your "
                                        "everyday or starting voice.")
            return
        parts = [f"Recorded {b.get('recorded', '?')}"]
        if b.get("pitch"):
            parts.append(format_value("pitch", b["pitch"]))
        if b.get("delta_f"):
            parts.append(f"ΔF {b['delta_f']:.0f} Hz")
        self.baseline_label.setText(" · ".join(parts))

    def _clear_baseline(self):
        self.profile.clear_baseline()
        self._show_baseline()

    def accept(self):
        for key, (check, spins, _) in self.rows.items():
            self.profile.set_target(key, (spins[0].value(), spins[1].value()) if check.isChecked() else None)
        super().accept()


class AnalysisGraph(pg.GraphicsLayoutWidget):
    """Pitch track on top, F1-F3 below, with a hover readout."""

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


def singing_summary(singing):
    """One line on held notes, vibrato, drift and tuning, or None."""
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
    if singing["drift"] is not None:
        parts.append(f"drift ±{singing['drift']:.0f}¢")
    parts.append(f"{singing['tuning_offset']:.0f}¢ from A440 notes")
    return " · ".join(parts)


class TakeCard(QFrame):
    """One take's report. `audio` returns its samples (loaded on demand for
    saved takes); `on_rename(card, title)` and `on_delete(card)` manage it."""

    def __init__(self, data, count, profile, on_set_baseline, on_set_target,
                 on_rename=None, on_delete=None, audio=None):
        super().__init__()
        self.data, self.profile, self.number = data, profile, count
        self.audio = audio or (lambda: data["audio"])
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        # Scoped to the card: a bare QFrame rule would also hit every QLabel inside.
        self.setStyleSheet("""
            TakeCard { background-color: #2b2b2b; border-radius: 10px; margin-bottom: 10px; }
            QLabel { color: #ffffff; background: transparent; }
            QLineEdit { color: white; background: transparent; border: 1px solid transparent;
                        border-radius: 4px; padding: 1px 3px; }
            QLineEdit:hover, QLineEdit:focus { border: 1px solid #555; background: #1e1e1e; }
        """)
        layout = QVBoxLayout()

        # Header
        header = QHBoxLayout()
        self.title = QLineEdit(data.get("title") or "")
        self.title.setPlaceholderText(f"Take #{count}")
        self.title.setToolTip("Click to name this take" + (" (renames its files)" if data.get("file") else ""))
        self.title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.title.setMinimumWidth(110)
        self.title.setCursorPosition(0)       # long names show their start, not their end
        self.on_rename = on_rename
        self.title.editingFinished.connect(self._title_edited)
        self.date = QLabel()
        self.date.setStyleSheet("color: #888; font-size: 11px;")
        self.update_saved_state()
        baseline_btn = QPushButton(qta.icon("fa5s.flag", color="white"), " Baseline")
        baseline_btn.setToolTip("Set as baseline")
        baseline_btn.clicked.connect(lambda: on_set_baseline(data))
        target_btn = QPushButton(qta.icon("fa5s.bullseye", color="white"), " Target")
        target_btn.setToolTip("Use as target")
        target_btn.clicked.connect(lambda: on_set_target(data))
        shift_btn = QPushButton(qta.icon("fa5s.sliders-h", color="white"), " Shift")
        shift_btn.setToolTip("Hear this take with the voice shifted")
        shift_btn.setCheckable(True)
        play_btn = QPushButton(qta.icon('fa5s.play', color="white"), " Play")
        play_btn.clicked.connect(self.play_audio)
        for btn in (baseline_btn, target_btn):
            btn.setStyleSheet(BUTTON_STYLE)
        shift_btn.setStyleSheet(f"QPushButton {{ {BUTTON_STYLE} }} "
                                "QPushButton:checked { background-color: #3498db; }")
        play_btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 5px 10px; border-radius: 5px;")
        delete_btn = QPushButton(qta.icon("fa5s.trash-alt", color="#ccc"), "")
        delete_btn.setToolTip("Delete this take")
        delete_btn.setStyleSheet(BUTTON_STYLE)
        delete_btn.clicked.connect(lambda: on_delete and on_delete(self))
        header.addWidget(self.title, 1)
        header.addWidget(self.date)
        header.addWidget(baseline_btn)
        header.addWidget(target_btn)
        header.addWidget(shift_btn)
        header.addWidget(play_btn)
        header.addWidget(delete_btn)
        layout.addLayout(header)

        if data.get("source"):
            source = QLabel(data["source"])
            source.setStyleSheet("color: #aaa; font-size: 11px;")
            layout.addWidget(source)
        snr = data.get("snr")
        if snr is not None and snr < MIN_RELIABLE_SNR:
            warning = QLabel(f"⚠ Background noise: voice-band SNR {snr:.0f} dB. Clarity, Fold Contact "
                             "and Ring may be unreliable; pitch and resonance hold up better.")
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {OFF_TARGET_COLOR}; font-size: 12px;")
            layout.addWidget(warning)
        note = hiss_note(snr, data.get("snr_full"))
        if note:
            info = QLabel(note)
            info.setWordWrap(True)
            info.setStyleSheet("color: #aaa; font-size: 11px;")
            layout.addWidget(info)
        clips = data.get("clip_events") or 0
        if clips:
            warning = QLabel(f"⚠ Clipping: the input overloaded {clips} time{'s' if clips > 1 else ''}. "
                             "Lower the mic gain; clipping inflates Ring and lowers Clarity.")
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {OFF_TARGET_COLOR}; font-size: 12px;")
            layout.addWidget(warning)

        self.shift_panel = ShiftPanel(data, on_set_target, audio=self.audio)
        self.shift_panel.hide()
        shift_btn.toggled.connect(self.shift_panel.setVisible)
        # While shifting, the card's Play button plays the shifted version.
        shift_btn.toggled.connect(lambda on: play_btn.setText(" Play shifted" if on else " Play"))
        layout.addWidget(self.shift_panel)

        # --- GRAPH ---
        graph = AnalysisGraph(data['tracks'])
        graph.setFixedHeight(200)
        layout.addWidget(graph)

        self.time_in_target = QLabel()
        self.time_in_target.setStyleSheet("color: #2ecc71; font-size: 12px;")
        layout.addWidget(self.time_in_target)

        # --- SINGING ---
        exercise = data.get("exercise")
        if exercise:
            text = f"{exercise['name']}: {exercise['notes_in_tune']}/{exercise['notes_total']} notes in tune"
            if exercise["mean_abs_cents"] is not None:
                text += (f" (±{IN_TUNE_CENTS}¢) · average error {exercise['mean_abs_cents']:.0f}¢ · "
                         f"{exercise['time_in_tune']:.0%} of note time in tune")
            label = QLabel(text)
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 12px;")
            layout.addWidget(label)
            layout.addWidget(NoteScoreChart(exercise["notes"]))
        summary = singing_summary(data.get("singing"))
        if summary:
            label = QLabel(summary)
            label.setWordWrap(True)
            label.setStyleSheet("color: #ddd; font-size: 12px;")
            layout.addWidget(label)

        # --- METRIC BARS ---
        self.bars = {key: MetricBar(key) for key in TAKE_BAR_KEYS}
        bar_grid = ResponsiveGrid(METER_MIN_WIDTH)
        for bar in self.bars.values():
            bar_grid.addWidget(bar)
        layout.addWidget(bar_grid)
        self.setLayout(layout)
        self.refresh_profile()

    def refresh_profile(self):
        # Share of voiced time inside each target: holding a voice across a whole
        # passage matters more than its median.
        tracks = self.data["tracks"]
        shares = [(name, self.profile.time_in_target(key, tracks[track]))
                  for name, key, track in (("pitch", "pitch", "f0"), ("resonance", "delta_f", "delta_f"))]
        parts = [f"{name} {share:.0%}" for name, share in shares if share is not None]
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

    def play_audio(self):
        if self.shift_panel.isVisible():
            self.shift_panel.play_shifted()
        else:
            sd.play(self.audio(), SAMPLE_RATE)


class MainWindow(QMainWindow):
    def __init__(self, engine, profile):
        super().__init__()
        self.setWindowTitle("Voice Lab")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1400, int(screen.width() * 0.9)), min(950, int(screen.height() * 0.9)))
        self.setStyleSheet("background-color: #1e1e1e;")

        self.take_count = 0
        self.is_recording = False
        self.analyzing = False
        self.engine = engine
        self.profile = profile
        self.analysis_worker = None
        self.load_worker = None
        self.cards = []
        self.older_takes = []       # loaded takes not yet shown as cards, oldest first
        self.progress_dialog = None
        self.wide_layout = None
        self.store =TakeStore(profile.takes_dir) if profile.takes_dir else None
        if profile.formant_ceiling:
            engine.analyzer.formant_ceiling = profile.formant_ceiling

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.live_panel = LivePanel(engine.analyzer.spectrum_freqs, profile, self.edit_targets)
        self.engine.frame_ready.connect(self.live_panel.update_frame)
        self.vowel_check = VowelCheck(engine, profile)
        self.vowel_check.running_changed.connect(self.update_record_enabled)
        self.exercise_view = ExerciseView(engine)
        self.exercise_view.running_changed.connect(self.update_record_enabled)
        self.exercise_view.take_ready.connect(self.process_results)
        self.vowel_check.completed.connect(self.refresh_progress)
        self.voice_map = VoiceMapView(engine, profile.voice_map_path)
        self.live_panel.add_tab(self.vowel_check, "Vowels", {"speak"})
        self.live_panel.add_tab(self.exercise_view, "Exercise", {"sing"}, default_for="sing")
        self.live_panel.add_tab(self.voice_map, "Voice Map", {"sing"})
        self.live_panel.target_provider = self.exercise_view.current_target
        self.live_panel.mode_changed.connect(profile.set_mode)
        self.live_panel.set_mode(profile.mode)

        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.more_btn = QPushButton()
        self.more_btn.setStyleSheet(BUTTON_STYLE)
        self.more_btn.clicked.connect(self.show_older_takes)
        self.more_btn.hide()
        self.scroll_layout.addWidget(self.more_btn)
        self.scroll_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.scroll_content)
        scroll.setStyleSheet("border: none;")

        controls = QFrame()
        controls.setStyleSheet("background-color: #252525; border-top: 1px solid #333;")
        controls_layout = QVBoxLayout(controls)

        self.status_label = QLabel("Ready to Record")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #888; font-size: 14px;")

        self.record_btn = QPushButton()
        self.record_btn.setFixedSize(64, 64)
        self.record_btn.setIconSize(QSize(30, 30))
        self.update_record_button_style(recording=False)
        self.record_btn.clicked.connect(self.toggle_recording)

        self.import_btn = QPushButton(qta.icon("fa5s.file-audio", color="white"), " Import audio…")
        self.import_btn.setStyleSheet(BUTTON_STYLE)
        self.import_btn.setToolTip("Analyse a recording: your own, or a reference singer or speaker.")
        self.import_btn.clicked.connect(self.import_audio)
        progress_btn = QPushButton(qta.icon("fa5s.chart-line", color="white"), " Progress")
        progress_btn.setStyleSheet(BUTTON_STYLE)
        progress_btn.setToolTip("Your measurements over weeks.")
        progress_btn.clicked.connect(self.show_progress)
        self.folder_btn = QPushButton(qta.icon("fa5s.folder-open", color="white"), "")
        self.folder_btn.setStyleSheet(BUTTON_STYLE)
        self.folder_btn.clicked.connect(self.choose_takes_folder)
        self.reload_btn = QPushButton(qta.icon("fa5s.sync-alt", color="white"), "")
        self.reload_btn.setStyleSheet(BUTTON_STYLE)
        self.reload_btn.setToolTip("Reload the takes folder, picking up files added, renamed or deleted there.")
        self.reload_btn.clicked.connect(self.load_takes)
        self.mic_choice = QComboBox()
        self.mic_choice.setStyleSheet("QComboBox { background: #1e1e1e; color: white; padding: 3px; }")
        self.mic_choice.setMaximumWidth(260)
        self.mic_choice.addItem("System default mic", None)
        for _, name in input_devices():
            self.mic_choice.addItem(name, name)
        self.mic_choice.setCurrentIndex(max(0, self.mic_choice.findData(profile.input_device)))
        self.mic_choice.currentIndexChanged.connect(self.change_mic)
        mic_label = QLabel("Mic")
        mic_label.setStyleSheet("color: #aaa; border: none;")
        self.update_folder_button()

        # One row: files on the left, record in the middle, progress and mic on the right.
        left, right = QHBoxLayout(), QHBoxLayout()
        for w in (self.import_btn, self.folder_btn, self.reload_btn):
            left.addWidget(w)
        left.addStretch()
        right.addStretch()
        for w in (progress_btn, mic_label, self.mic_choice):
            right.addWidget(w)
        button_row = QHBoxLayout()
        button_row.addLayout(left, 1)
        button_row.addWidget(self.record_btn)
        button_row.addLayout(right, 1)
        controls_layout.setContentsMargins(10, 4, 10, 8)
        controls_layout.setSpacing(4)
        controls_layout.addWidget(self.status_label)
        controls_layout.addLayout(button_row)

        # Live panel and takes: side by side on a wide window, stacked on a
        # narrow one. The divider can be dragged either way.
        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        self.splitter.addWidget(self.live_panel)
        self.splitter.addWidget(scroll)
        self.takes_scroll = scroll
        scroll.setMinimumSize(320, 160)
        self.update_layout()
        main_layout.addWidget(self.splitter, 1)
        main_layout.addWidget(controls)
        if self.store:
            self.load_takes()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "splitter"):
            self.update_layout()

    def wheelEvent(self, event):
        """A wheel turn nothing else used (over the live panel or the bottom
        bar) scrolls the takes, so it works wherever the pointer is."""
        QApplication.sendEvent(self.takes_scroll.verticalScrollBar(), event)

    def update_layout(self):
        # Side by side only when the takes still get a comfortable column; this
        # also keeps the window able to shrink back into the stacked layout.
        wide_from = max(WIDE_LAYOUT_WIDTH, self.live_panel.minimumSizeHint().width() + TAKES_COLUMN_WIDTH)
        wide = self.width() >= wide_from
        if wide == self.wide_layout:
            return
        self.wide_layout = wide
        self.splitter.setOrientation(Qt.Orientation.Horizontal if wide else Qt.Orientation.Vertical)
        if wide:     # even split
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 1)
            self.splitter.setSizes([self.width() // 2] * 2)
        else:        # live panel as compact as it goes, takes get the rest
            self.splitter.setStretchFactor(0, 0)
            self.splitter.setStretchFactor(1, 1)
            live = self.live_panel.minimumSizeHint().height()
            self.splitter.setSizes([live, max(160, self.height() - live)])

    def update_record_button_style(self, recording):
        if recording:
            self.record_btn.setIcon(qta.icon('fa5s.stop', color='white'))
            self.record_btn.setStyleSheet("QPushButton { background-color: #333; border-radius: 32px; border: 2px solid #555; }")
        else:
            self.record_btn.setIcon(qta.icon('fa5s.microphone', color='white'))
            self.record_btn.setStyleSheet("QPushButton { background-color: #e74c3c; border-radius: 32px; border: 2px solid #c0392b; }")

    def toggle_recording(self):
        if not self.is_recording:
            self.is_recording = True
            self.status_label.setText("Recording...")
            self.status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.update_record_button_style(recording=True)
            self.engine.start_take()
            self.update_record_enabled()
        else:
            self.is_recording = False
            self.status_label.setText("Processing...")
            self.status_label.setStyleSheet("color: #f1c40f;")
            self.update_record_button_style(recording=False)
            self.analyzing = True
            self.record_btn.setEnabled(False)
            self.analysis_worker = take_analysis_worker(self.engine.stop_take())
            self.analysis_worker.finished_analysis.connect(self.process_results)
            self.analysis_worker.start()

    def update_record_enabled(self, *_):
        """The record button is unavailable while a drill or exercise records."""
        busy = self.vowel_check.running or self.exercise_view.running or self.analyzing
        self.record_btn.setEnabled(not busy)
        self.import_btn.setEnabled(not busy and not self.is_recording)

    def import_audio(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import audio", "", AUDIO_FILE_FILTER)
        if not path:
            return
        isolate = False
        if isolation.available():
            answer = QMessageBox.question(
                self, "Isolate vocals?",
                "Is there music behind the voice? Demucs can isolate the vocals first.\n"
                "This is slower, and the first use downloads its model (~80 MB).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Cancel:
                return
            isolate = answer == QMessageBox.StandardButton.Yes
        self.analyzing = True
        self.update_record_enabled()
        self.status_label.setText("Isolating vocals and analyzing…" if isolate else "Analyzing file…")
        self.status_label.setStyleSheet("color: #f1c40f;")
        self.analysis_worker = AnalysisWorker(import_and_analyze, path, isolate)
        self.analysis_worker.finished_analysis.connect(self.process_results)
        self.analysis_worker.start()

    def process_results(self, results):
        # Exercise takes arrive here too; only our own worker finishing frees
        # the record button, or a second take could replace a running worker.
        if self.sender() is self.analysis_worker:
            self.analyzing = False
            self.update_record_enabled()
        if "error" in results:
            self.status_label.setText(results["error"])
            self.status_label.setStyleSheet("color: #e74c3c;")
            return
        # Calibrate the live formant tracker to the ceiling that fit this take,
        # unless it's an imported file, which may be someone else's voice.
        if not results.get("source"):
            self.engine.analyzer.formant_ceiling = results["formant_ceiling"]
            self.profile.set_formant_ceiling(results["formant_ceiling"])
        results.update(uid=new_uid(), time=datetime.now().isoformat(timespec="seconds"),
                       mode=self.profile.mode)
        message = "Done"
        # With a takes folder, the folder is the record of every take; without
        # one, a summary goes to the history log so progress still builds up.
        if self.store:
            try:
                self.store.save(results)
                message = f"Saved {results['file']}"
            except OSError as e:
                message = f"Couldn't save the take: {e}"
                self.profile.log_take(results)
        else:
            self.profile.log_take(results)
        self.add_card(results)
        self.set_status(message)
        self.refresh_progress()

    # --- TAKE CARDS ---

    def set_status(self, text, color="#888"):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")

    def take_audio(self, data):
        return self.store.audio(data) if data.get("file") and self.store else data["audio"]

    def add_card(self, data, number=None, at_bottom=False):
        if number is None:
            self.take_count += 1
            number = self.take_count
        if data["delta_f"] is not None:
            self.live_panel.plane.add_take(number, data["pitch"], data["delta_f"])
        card = TakeCard(data, number, self.profile, self.set_baseline, self.set_target,
                        on_rename=self.rename_take, on_delete=self.delete_take,
                        audio=lambda: self.take_audio(data))
        self.cards.append(card)
        self.scroll_layout.insertWidget(self.scroll_layout.indexOf(self.more_btn) if at_bottom else 0, card)
        return card

    def remove_card(self, card):
        self.cards.remove(card)
        self.live_panel.plane.remove_take(card.number)
        card.setParent(None)
        card.deleteLater()

    def show_older_takes(self):
        """Add the next page of older takes below the shown ones."""
        page, self.older_takes = self.older_takes[-TAKES_PAGE:], self.older_takes[:-TAKES_PAGE]
        for number, data in reversed(page):
            self.add_card(data, number, at_bottom=True)
        self.update_more_button()

    def update_more_button(self):
        self.more_btn.setText(f"Show older takes ({len(self.older_takes)} more)")
        self.more_btn.setVisible(bool(self.older_takes))

    # --- TAKES FOLDER ---

    def update_folder_button(self):
        if self.store:
            self.folder_btn.setText(f" {self.store.folder.name}")
            self.folder_btn.setToolTip(f"Takes are saved in {self.store.folder}. Click to change.")
        else:
            self.folder_btn.setText(" Choose takes folder…")
            self.folder_btn.setToolTip("Pick a folder to save takes in and load them from, "
                                       "so they're kept between sessions.")
        self.reload_btn.setVisible(self.store is not None)

    def ask_for_takes_folder(self):
        """First launch: offer to pick a takes folder, once."""
        if self.store or self.profile.takes_dir_asked:
            return
        answer = QMessageBox.question(
            self, "Keep your takes?",
            "Choose a folder to save takes in? Each take is saved there as a WAV file with its "
            "analysis beside it, and loads again next time.\n\n"
            "You can choose or change the folder any time with the folder button at the bottom.")
        if answer == QMessageBox.StandardButton.Yes:
            self.choose_takes_folder()
        else:
            self.profile.set_takes_dir(None)

    def choose_takes_folder(self):
        start = str(self.store.folder) if self.store else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Takes folder", start)
        if folder:
            self.set_takes_folder(folder)

    def set_takes_folder(self, folder):
        """Use `folder` for takes. This session's unsaved takes are saved into it."""
        self.profile.set_takes_dir(folder)
        self.store = TakeStore(folder)
        unsaved = [c.data for c in self.cards if not c.data.get("file")]
        for data in sorted(unsaved, key=lambda d: d["time"]):
            try:
                self.store.save(data)
            except OSError as e:
                self.set_status(f"Couldn't save a take: {e}", "#e74c3c")
        # They're in the folder now, so the history log shouldn't count them again.
        self.profile.forget_takes(d["uid"] for d in unsaved if d.get("file"))
        self.update_folder_button()
        self.load_takes()

    def load_takes(self):
        """(Re)load every take in the folder, in the background."""
        if not self.store or (self.load_worker and self.load_worker.isRunning()):
            return
        self.set_status(f"Loading takes from {self.store.folder}…", "#f1c40f")
        self.reload_btn.setEnabled(False)
        self.load_worker = AnalysisWorker(load_folder, self.store)
        self.load_worker.finished_analysis.connect(self._takes_loaded)
        self.load_worker.start()

    def _takes_loaded(self, result):
        self.reload_btn.setEnabled(True)
        if "error" in result:
            self.set_status(result["error"], "#e74c3c")
            return
        # Keep takes that aren't in the folder, and any saved while it loaded;
        # takes whose files were deleted outside VoiceLab go.
        loaded = {d["uid"] for d in result["takes"]}
        kept = [c.data for c in self.cards if c.data.get("uid") not in loaded and
                (not c.data.get("file") or self.store.path(c.data).exists())]
        for card in list(self.cards):
            self.remove_card(card)
        takes = sorted(result["takes"] + kept, key=lambda d: d["time"])
        numbered = list(enumerate(takes, start=1))
        self.take_count = len(takes)
        self.older_takes, shown = numbered[:-TAKES_PAGE], numbered[-TAKES_PAGE:]
        for number, data in shown:
            self.add_card(data, number)
        self.update_more_button()
        message = f"Loaded {len(result['takes'])} take{'s' if len(result['takes']) != 1 else ''} " \
                  f"from {self.store.folder.name}"
        if result["problems"]:
            message += f" · {len(result['problems'])} file(s) couldn't be read"
            self.status_label.setToolTip("\n".join(result["problems"]))
        self.set_status(message, "#e67e22" if result["problems"] else "#888")
        self.refresh_progress()

    def all_takes(self):
        return [c.data for c in self.cards] + [d for _, d in self.older_takes]

    def rename_take(self, card, title):
        data = card.data
        if data.get("file") and self.store:
            try:
                self.store.rename(data, title)
            except OSError as e:
                card.title.setText(data.get("title") or "")
                self.set_status(f"Couldn't rename: {e}", "#e74c3c")
                return
            card.title.setText(data["title"])
            self.set_status(f"Renamed to {data['file']}")
        else:
            data["title"] = title
        card.update_saved_state()

    def delete_take(self, card):
        data = card.data
        name = data.get("title") or f"Take #{card.number}"
        if data.get("file"):
            text = f"Delete “{name}”? Its files ({data['file']} and its .json) are removed from the takes folder."
        else:
            text = f"Delete “{name}”? It isn't saved anywhere, so it can't be recovered."
        if QMessageBox.question(self, "Delete take", text) != QMessageBox.StandardButton.Yes:
            return
        if data.get("file") and self.store:
            try:
                self.store.delete(data)
            except OSError as e:
                self.set_status(f"Couldn't delete: {e}", "#e74c3c")
                return
        else:
            self.profile.forget_takes([data.get("uid")])
        self.remove_card(card)
        self.set_status(f"Deleted {name}")
        self.refresh_progress()

    # --- MIC ---

    def change_mic(self):
        name = self.mic_choice.currentData()
        try:
            self.engine.open_stream(device_index(name))
        except sd.PortAudioError as e:
            self.set_status(f"Couldn't open {name or 'the default mic'}: {e}", "#e74c3c")
            return
        self.profile.set_input_device(name)
        self.set_status(f"Listening on {name or 'the system default mic'}")
        self.update_record_enabled()

    # --- PROGRESS ---

    def progress_entries(self):
        folder_takes = [d for d in self.all_takes() if d.get("file")]
        return collect_entries(self.profile.read_history(), folder_takes)

    def show_progress(self):
        if self.progress_dialog is None:
            self.progress_dialog = ProgressDialog(self.profile, self.progress_entries, self)
        else:
            self.progress_dialog.refresh()
        self.progress_dialog.show()
        self.progress_dialog.raise_()

    def refresh_progress(self, *_):
        if self.progress_dialog is not None and self.progress_dialog.isVisible():
            self.progress_dialog.refresh()

    def set_baseline(self, results):
        self.profile.set_baseline(results)
        self.refresh_profile("Baseline saved")

    def set_target(self, results):
        self.profile.set_targets_from_take(results)
        self.refresh_profile("Targets set from take. Fine-tune them under Targets.")

    def edit_targets(self):
        TargetsDialog(self.profile, self).exec()
        # Refresh even on Cancel: clearing the baseline applies immediately.
        self.refresh_profile()

    def refresh_profile(self, message=None):
        self.live_panel.refresh_profile()
        self.vowel_check.refresh_profile()
        for card in self.cards:
            card.refresh_profile()
        self.refresh_progress()
        if message:
            self.set_status(message, "#2ecc71")

    def closeEvent(self, event):
        self.engine.stop()
        self.voice_map.save()
        for worker in (self.analysis_worker, self.load_worker):
            if worker is not None:
                worker.wait()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Dark throughout, so message boxes and other stock dialogs get light text.
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    pg.setConfigOptions(antialias=True)
    profile = Profile()
    engine = LiveEngine()
    window = MainWindow(engine, profile)
    window.show()
    try:
        engine.start(device_index(profile.input_device))
    except sd.PortAudioError as e:
        # Still usable without a mic: imported files can be analysed and shifted.
        window.record_btn.setEnabled(False)
        window.status_label.setText("No microphone. Import audio still works.")
        QMessageBox.warning(window, "No microphone",
                            f"Couldn't open the microphone ({e}).\n"
                            "Connect one and pick it under Mic, or restart VoiceLab.")
    QTimer.singleShot(0, window.ask_for_takes_folder)
    sys.exit(app.exec())
