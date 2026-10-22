"""Shared meter widgets, display constants and formatting helpers for the UI modules."""
import time

import numpy as np
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QBrush, QPainter, QColor, QPen

from analysis import VOICE_BAND_HZ, hz_to_note, vocal_tract_length_cm
from widgets import ElidedLabel

LIVE_TEXT_SECONDS = 0.2         # live numbers change at most this often, so they can be read
# --- METRIC DISPLAY ---
# key: (label, min, max, low label, high label, value format, scale)
# `scale` maps values onto the bar; pitch uses log2 so octaves are equal widths.
# Every range has a source. "unverified" = from memory, "own judgement" = chosen for display only.
METRIC_DEFS = {
    # Sing scale; speech uses MODE_SCALES (80-400 Hz). 60-1500 Hz covers bass to soprano
    # (the sing-mode tracker's ceiling is 1500 Hz, spec 01).
    "pitch": ("Pitch", 60, 1500, "Low", "High", "{:.0f} Hz", np.log2),
    # delta F = c / 2L: 800 Hz is a 21.9 cm tract, 1400 Hz a 12.5 cm one. Brackets the user's real
    # takes (845-1024 Hz, usefulness audit) and the typical regions drawn on the plane (960-1260).
    "delta_f": ("Resonance (ΔF)", 800, 1400, "Dark", "Bright", "{:.0f} Hz", None),
    # Corrected H1*-H2* of ordinary voices lies roughly in -5..15 dB (Hanson 1997; unverified).
    # A noisy proxy for weight, not a measure of fold contact; rough above 250 Hz (F1 meets H1).
    "h1h2": ("Heavy ↔ Light (H1*–H2*)", -5, 15, "Heavy", "Light", "{:+.1f} dB", None),
    # Our CPP is not the published CPPS (audit): the range is the span of the user's takes plus
    # healthy voices, own judgement. CPP falls with noise and reverb too (4-5 dB low at 16 dB SNR).
    "cpp": ("Clarity (CPP, noise-sensitive)", 8, 30, "Breathy", "Clear", "{:.1f} dB", None),
    # Level of 2-4 kHz against 0-2 kHz harmonics; own judgement, covers the user's takes (about -28 dB).
    "ring": ("Ring (2–4 kHz)", -30, 0, "Muffled", "Ringing", "{:+.1f} dB", None),
    # The 10-90th percentile range of real speech is 4-28 st (audit); 12 st is a lively voice.
    "pitch_range": ("Intonation Range", 0, 12, "Flat", "Expressive", "{:.1f} st", None),
    "level": ("Level", -60, 0, "Quiet", "Loud", "{:.0f} dBFS, uncalibrated", None),
    # A semitone is 100 cents, so ±50 cents is halfway to the neighbouring note.
    "tuning": ("Tuning", -50, 50, "Flat", "Sharp", "{:+.0f}¢", None),
    # Sung vibrato extent is typically ±30-100 cents (Sundberg 1987; unverified).
    "vibrato": ("Vibrato", 0, 100, "Straight", "Wide", "±{:.0f}¢", None),
    # Head share of a take, in %. The live meter plots the 0-100 register score on the same bar.
    "register": ("Register (head share)", 0, 100, "Chest", "Head", "{:.0f}%", None),
    # Contact quotient of ordinary voices is about 0.3-0.6 (Herbst; unverified).
    "cq": ("Contact quotient (EGG)", 0.2, 0.7, "Light", "Pressed", "{:.2f}", None),
}
# Per-mode bar ranges (min, max) that replace a metric's METRIC_DEFS range. Adult speaking f0 is
# mostly 85-255 Hz (Titze 1994; unverified), so 80-400 Hz keeps the speech zone wide where
# 60-1200 made it a thin slice.
MODE_SCALES = {"pitch": {"speak": (80, 400), "sing": (60, 1500)}}

BOTH_MODES = {"speak", "sing"}

HISS_GAP_DB = 6                 # band SNR this much above full-band SNR = noise is mostly hiss

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


def hiss_note(snr, snr_full):
    """Explains when most of the noise sits above the voice band, or None. Used by the take card;
    the live panel's noise badge (denoise.noise_report) replaced it there."""
    if snr is None or snr_full is None or snr - snr_full < HISS_GAP_DB:
        return None
    return (f"Most of the noise is high-pitched hiss above {VOICE_BAND_HZ // 1000} kHz "
            f"(full-band SNR {snr_full:.0f} dB). Measurements ignore that range.")


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


# --- STABLE VERDICTS ---

VERDICT_FRAMES = 80         # voiced frames judged (2 s at 25 ms): longer than a vowel (spec 06)
VERDICT_MIN_FRAMES = 12     # no verdict before this much voice
VERDICT_DWELL_FRAMES = 40   # a verdict holds at least this long (1 s); own judgement
VERDICT_MARGIN = 0.15       # of the target's width: hysteresis at its edges; own judgement
VERDICT_RESET_FRAMES = 80   # a pause this long (2 s) forgets the window


def robust_center(values):
    """The mean of the middle half of the values (interquartile mean). Like a median it ignores
    outliers, but it moves smoothly when a window holds two vowels' worth of values, where the
    median jumps between the two clusters as their shares cross 50%."""
    values = np.sort(np.asarray(values, dtype=float))
    n = len(values)
    return float(values[n // 4:n - n // 4].mean())


class VerdictTracker:
    """On/off-target verdict for a live value that doesn't flicker: judged on the robust centre
    of the last VERDICT_FRAMES voiced values, with hysteresis at the target's edges and a
    minimum dwell time. update() returns True, False, or None (no target or not enough voice)."""

    def __init__(self, frames=VERDICT_FRAMES):
        self.frames = frames
        self.reset()

    def reset(self):
        self.values, self.state, self.since_change, self.unvoiced = [], None, 0, 0

    def center(self):
        """The robust centre of the window, or None with too little voice."""
        return robust_center(self.values) if len(self.values) >= VERDICT_MIN_FRAMES else None

    def update(self, value, target):
        """Feed one frame's value (None or NaN when it has none); `target` is [low, high] or None."""
        if value is None or np.isnan(value):
            self.unvoiced += 1
            if self.unvoiced >= VERDICT_RESET_FRAMES:
                self.reset()
            return self.state if target is not None else None
        self.unvoiced = 0
        self.values.append(float(value))
        del self.values[:-self.frames]
        center = self.center()
        if target is None or center is None:
            self.state = None
            return None
        margin = VERDICT_MARGIN * (target[1] - target[0])
        self.since_change += 1
        if self.state is None:
            self.state, self.since_change = target[0] <= center <= target[1], 0
        elif self.since_change >= VERDICT_DWELL_FRAMES:
            if self.state:
                now = target[0] - margin <= center <= target[1] + margin
            else:
                now = target[0] + margin <= center <= target[1] - margin
            if now != self.state:
                self.state, self.since_change = now, 0
        return self.state


def format_value(key, value):
    if key == "pitch":
        note, cents = hz_to_note(value)
        return f"{note} {cents:+d}¢ · {value:.0f} Hz"
    if key == "delta_f":
        return f"{value:.0f} Hz, ≈{vocal_tract_length_cm(value):.1f} cm apparent tract"
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
    """Labelled RangeBar for one metric, aware of baseline and target."""

    def __init__(self, key, centered=False, live=False, tracked=True):
        """`tracked` False keeps the profile's baseline, target and change text off this bar."""
        super().__init__()
        self.key = key
        self.tracked = tracked
        self.parens = True          # "Label (value)", or "Label: value" when False
        self.modes = BOTH_MODES
        self.color_fn = None        # position -> colour, for bars whose position has a meaning (register)
        self.label, lo, hi, low_label, high_label, self.fmt, scale = METRIC_DEFS[key]
        self.scale = scale or (lambda v: v)
        self.lo, self.hi = self.scale(lo), self.scale(hi)
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

    def set_mode(self, mode):
        """Use this mode's bar range, if it has one (call set_reference afterwards)."""
        lo, hi = MODE_SCALES.get(self.key, {}).get(mode) or METRIC_DEFS[self.key][1:3]
        self.lo, self.hi = self.scale(lo), self.scale(hi)

    def _fraction(self, value):
        return float(np.clip((self.scale(value) - self.lo) / (self.hi - self.lo), 0, 1))

    def set_reference(self, profile):
        if not self.tracked:
            return
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

    def set_value(self, value, profile, note=None, display=None, on_target=None, use_profile=True):
        """`note` marks the reading as unavailable and explains why (the bar is hatched);
        `display` overrides the value text; `on_target` overrides the profile's verdict, and
        with `use_profile` False a None `on_target` means "no verdict yet" (the caller judges)."""
        if note or value is None or np.isnan(value):
            self._set_title(f"{self.label} — {note or 'no reading'}")
            self.bar.position, self.bar.color = None, DISABLED_COLOR
            self.bar.marker, self.bar.hatched = None, bool(note)
            self.bar.update()
            return
        parts = [display or format_value(self.key, value)]
        change = profile.change_text(self.key, value) if self.tracked else None
        if change:
            parts.append(change)
        if on_target is None and self.tracked and use_profile:
            on_target = profile.in_target(self.key, value)
        joined = " · ".join(parts)
        title = f"{self.label} ({joined})" if self.parens else f"{self.label}: {joined}"
        if on_target is not None:
            title += "  ✓ on target" if on_target else "  off target"
        self._set_title(title)
        self.bar.position = self._fraction(value)
        self.bar.hatched = False
        self.bar.color = (self.color_fn(self.bar.position) if self.color_fn and on_target is None
                          else bar_color(self.bar.position, on_target))
        self.bar.update()
