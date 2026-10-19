"""Shared meter widgets, display constants and formatting helpers for the UI modules."""
import time

import numpy as np
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen

from analysis import VOICE_BAND_HZ, hz_to_note, vocal_tract_length_cm

LIVE_TEXT_SECONDS = 0.2         # live numbers change at most this often, so they can be read
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

HISS_GAP_DB = 6                 # band SNR this much above full-band SNR = noise is mostly hiss

METER_MIN_WIDTH = 300           # meters and take bars go two-up when this fits twice

BAR_COLORS = ("#3498db", "#9b59b6", "#e74c3c")
ON_TARGET_COLOR = "#2ecc71"
OFF_TARGET_COLOR = "#e67e22"
DISABLED_COLOR = "#555"
TARGET_BAND_COLOR = QColor(46, 204, 113, 70)
BUTTON_STYLE = "background-color: #3a3a3a; color: white; padding: 5px 10px; border-radius: 5px;"


def hiss_note(snr, snr_full):
    """Explains when most of the noise sits above the voice band, or None."""
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
