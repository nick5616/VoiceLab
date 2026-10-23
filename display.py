"""Wording and number formatting shared by every UI: metric labels and scales, value text, and a
metric "reading" (what a meter shows: text, bar position, verdict, target band). No UI toolkit."""
from datetime import datetime

import numpy as np

from analysis import CLOSE_CENTS, IN_TUNE_CENTS, NOTE_NAMES, VOICE_BAND_HZ, hz_to_note, vocal_tract_length_cm

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


# --- NUMBERS AND NOTES ---

def midi_name(midi):
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def pitch_label(f0):
    note, cents = hz_to_note(f0)
    return f"{note} {cents:+d}¢ · {f0:.0f} Hz"


def note_ticks(lo_hz, hi_hz):
    """Pitch-axis ticks (in log2 Hz) labelled with note names, thinned to fit."""
    lo_midi = int(np.floor(69 + 12 * np.log2(lo_hz / 440)))
    hi_midi = int(np.ceil(69 + 12 * np.log2(hi_hz / 440)))
    span = hi_midi - lo_midi
    step = 1 if span <= 12 else 2 if span <= 24 else 5
    major, minor = [], []
    for midi in range(lo_midi, hi_midi + 1):
        y = np.log2(440 * 2 ** ((midi - 69) / 12))
        if midi % step == 0:
            major.append((y, f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}  {2 ** y:.0f}"))
        else:
            minor.append((y, ""))
    return [major, minor]


def formants_label(f1, f2, f3):
    """The formants at one moment, for a graph's hover readout."""
    return f"F1:{f1:.0f} F2:{f2:.0f} F3:{f3:.0f}"


def format_value(key, value):
    if key == "pitch":
        note, cents = hz_to_note(value)
        return f"{note} {cents:+d}¢ · {value:.0f} Hz"
    if key == "delta_f":
        return f"{value:.0f} Hz, ≈{vocal_tract_length_cm(value):.1f} cm apparent tract"
    return METRIC_DEFS[key][5].format(value)


def take_date(take):
    try:
        return datetime.fromisoformat(take["time"]).strftime("%a %b %d · %H:%M").replace(" 0", " ")
    except (KeyError, TypeError, ValueError):
        return ""


# --- TAKE SUMMARIES ---

def hiss_note(snr, snr_full):
    """Explains when most of the noise sits above the voice band, or None. Used by the take card;
    the live noise badge (denoise.noise_report) replaced it there."""
    if snr is None or snr_full is None or snr - snr_full < HISS_GAP_DB:
        return None
    return (f"Most of the noise is high-pitched hiss above {VOICE_BAND_HZ // 1000} kHz "
            f"(full-band SNR {snr_full:.0f} dB). Measurements ignore that range.")


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


# --- METRIC READINGS ---

def metric_scale(key, mode=None):
    """(lo, hi, scale) of a metric's bar in `mode` (None: the METRIC_DEFS range), lo and hi already scaled."""
    scale = METRIC_DEFS[key][6] or (lambda v: v)
    lo, hi = MODE_SCALES.get(key, {}).get(mode) or METRIC_DEFS[key][1:3]
    return scale(lo), scale(hi), scale


def fraction(key, value, mode=None):
    """Where `value` sits on the metric's bar, 0-1 (clipped)."""
    lo, hi, scale = metric_scale(key, mode)
    return float(np.clip((scale(value) - lo) / (hi - lo), 0, 1))


def reference(key, profile, mode=None):
    """(band, baseline) on the metric's bar from the profile's target and baseline: fractions or None."""
    base = profile.baseline_value(key)
    target = profile.target(key)
    return (tuple(fraction(key, v, mode) for v in target) if target else None,
            fraction(key, base, mode) if base is not None else None)


def metric_reading(key, value, profile, mode=None, note=None, display=None, on_target=None, use_profile=True,
                   tracked=True, label=None, parens=True, zone_fn=None):
    """What a meter shows for `value`:
    {"key", "label", "value", "display", "title", "position", "verdict", "unavailable", "band", "band_kind",
     "baseline", "low_label", "high_label", "visible", "marker", "zone"}.
    `note` marks the reading unavailable and says why; `display` overrides the value text; `on_target`
    overrides the profile's verdict, and with `use_profile` False a None `on_target` means "no verdict yet".
    `tracked` False keeps the profile's baseline, target and change text off the reading. `zone_fn(position)`
    names the zone a value without a verdict sits in (the register's chest / mix / head)."""
    label = label or METRIC_DEFS[key][0]
    band, baseline = reference(key, profile, mode) if tracked else (None, None)
    reading = {"key": key, "label": label, "value": None, "display": None, "position": None, "verdict": None,
               "unavailable": None, "band": band, "band_kind": "target" if band else None, "baseline": baseline,
               "low_label": METRIC_DEFS[key][3], "high_label": METRIC_DEFS[key][4], "visible": True,
               "marker": None, "zone": None}
    if note or value is None or np.isnan(value):
        reading["unavailable"] = note or None
        reading["title"] = f"{label} — {note or 'no reading'}"
        return reading
    parts = [display or format_value(key, value)]
    change = profile.change_text(key, value) if tracked else None
    if change:
        parts.append(change)
    if on_target is None and tracked and use_profile:
        on_target = profile.in_target(key, value)
    joined = " · ".join(parts)
    title = f"{label} ({joined})" if parens else f"{label}: {joined}"
    if on_target is not None:
        title += "  ✓ on target" if on_target else "  off target"
    position = fraction(key, value, mode)
    reading.update(value=float(value), display=joined, title=title, position=position, verdict=on_target,
                   zone=zone_fn(position) if zone_fn and on_target is None else None)
    return reading
