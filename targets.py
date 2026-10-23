"""Editing targets: the ranges offered, where they start from, and the texts beside them. A UI draws the
editor from these. No UI toolkit."""
import numpy as np

from analysis import hz_to_note
from display import METRIC_DEFS, format_value
from profile_store import TARGET_KEYS, TARGET_PRESETS, target_from_value

# Targets editor: key -> (spinbox min, max, decimals, unit)
TARGET_EDIT = {
    "pitch": (50, 1200, 0, "Hz"),
    "delta_f": (800, 1500, 0, "Hz"),
    "h1h2": (-20, 30, 1, "dB"),
    "cpp": (0, 50, 1, "dB"),
    "ring": (-50, 10, 1, "dB"),
    "pitch_range": (0, 24, 1, "st"),
    "register": (0, 100, 0, "%"),      # head share of voiced time
}
TOOLTIPS = {"register": "Share of voiced time in head voice (M2). A target of 70-100% means "
                        "mostly head-leaning. The register is an estimate until you label takes."}
NOTE_BASELINE = "around your baseline"
NOTE_PRESET = "typical, yours may differ"
NOTE_NONE = "not based on your voice yet"
NO_BASELINE = "No baseline set. Use “Set as baseline” on a take of your everyday or starting voice."


def label(key):
    return METRIC_DEFS[key][0]


def step(key):
    """Spinbox step of a target: one unit of its last decimal, or 5 Hz / 1 unit for whole numbers."""
    _, _, decimals, unit = TARGET_EDIT[key]
    return 10 ** -decimals if decimals else (5 if unit == "Hz" else 1)


def preset(profile, key):
    """The preset range for the profile's mode that covers `key`, with its label, or None."""
    chosen = TARGET_PRESETS.get(profile.mode)
    if chosen and key in chosen["targets"]:
        return chosen["targets"][key], chosen["label"]
    return None


def default_range(profile, key):
    """Starting range for an unset target: around your baseline, else a typical range for the mode,
    else (the metric has neither) around the middle of its meter."""
    base = profile.baseline_value(key)
    if base is not None:
        return target_from_value(key, base)
    found = preset(profile, key)
    if found:
        return found[0]
    lo, hi = METRIC_DEFS[key][1:3]
    mid = np.sqrt(lo * hi) if key == "pitch" else (lo + hi) / 2
    return target_from_value(key, mid)


def default_note(profile, key):
    if profile.baseline_value(key) is not None:
        return NOTE_BASELINE
    if preset(profile, key):
        return NOTE_PRESET
    return NOTE_NONE


def rows(profile):
    """{key: {"label", "on", "range", "note", "tooltip"}}: each target as the editor opens it."""
    out = {}
    for key in TARGET_KEYS:
        current = profile.target(key)
        out[key] = {"label": label(key), "on": current is not None,
                    "range": current or default_range(profile, key),
                    "note": None if current else default_note(profile, key), "tooltip": TOOLTIPS.get(key)}
    return out


def start_options(profile):
    """[(text, choice)] for "Start from…": your baseline (when there is one), then the presets."""
    options = [("Start from…", None)]
    if profile.baseline:
        options.append(("Your baseline", "baseline"))
    options += [(chosen["label"], name) for name, chosen in TARGET_PRESETS.items()]
    return options


def start_from(profile, choice):
    """{key: (range, note)} to fill in from your baseline ("baseline") or a preset; keys without one are left out."""
    if choice is None:
        return {}
    filled = {}
    for key in TARGET_KEYS:
        if choice == "baseline":
            base = profile.baseline_value(key)
            rng, note = (target_from_value(key, base) if base is not None else None), NOTE_BASELINE
        else:
            rng, note = TARGET_PRESETS[choice]["targets"].get(key), NOTE_PRESET
        if rng is not None:
            filled[key] = (rng, note)
    return filled


def hint(key, values, note=None):
    """The text beside a target: note names for pitch, and why the range is what it is."""
    parts = []
    if key == "pitch":
        parts.append(" – ".join(hz_to_note(v)[0] for v in values))
    if note:
        parts.append(note)
    return " · ".join(parts)


def baseline_text(profile):
    b = profile.baseline
    if not b:
        return NO_BASELINE
    parts = [f"Recorded {b.get('recorded', '?')}"]
    if b.get("pitch"):
        parts.append(format_value("pitch", b["pitch"]))
    if b.get("delta_f"):
        parts.append(f"ΔF {b['delta_f']:.0f} Hz")
    return " · ".join(parts)


def save(profile, ranges):
    """Save the targets: {key: (low, high) or None (no target)}."""
    for key, rng in ranges.items():
        profile.set_target(key, tuple(rng) if rng is not None else None)
