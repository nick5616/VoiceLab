"""Personal baseline, targets and take history, persisted between sessions.

Everything is compared against the user's own voice rather than population
norms, which also cancels out most mic and room coloration.
"""
import json
import math
import os
from datetime import datetime
from pathlib import Path

import numpy as np

TARGET_KEYS = ["pitch", "delta_f", "h1h2", "cpp", "ring", "pitch_range"]
# Half-width of a target created from a take. Pitch is in semitones, the rest
# in each metric's own units.
DEFAULT_TOLERANCES = {"pitch": 1.5, "delta_f": 30, "h1h2": 3, "cpp": 3, "ring": 3, "pitch_range": 2}
SUMMARY_KEYS = TARGET_KEYS + ["contour", "level", "vtl", "formant_ceiling", "note", "cents",
                              "snr", "source"]
# Metrics measured as spreads, which can't go below zero.
NONNEGATIVE_KEYS = {"pitch_range", "contour"}


def data_dir():
    return Path(os.environ.get("VOICELAB_HOME", Path.home() / ".voicelab"))


def _clean(value):
    """JSON-safe scalar: numpy types to Python, NaN to None."""
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return None if math.isnan(value) else value
    if isinstance(value, np.integer):
        return int(value)
    return value


def summarize(result):
    """The scalar metrics of an analyze_take result, ready to store."""
    return {key: _clean(result.get(key)) for key in SUMMARY_KEYS}


def target_from_value(key, value):
    if value is None:
        return None
    tol = DEFAULT_TOLERANCES[key]
    if key == "pitch":
        return [value * 2 ** (-tol / 12), value * 2 ** (tol / 12)]
    if key in NONNEGATIVE_KEYS:
        return [max(0.0, value - tol), value + tol]
    return [value - tol, value + tol]


class Profile:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else data_dir()
        self.path = self.directory / "profile.json"
        self.history_path = self.directory / "history.jsonl"
        self.baseline = None
        self.targets = {}
        self.formant_ceiling = None
        self.vowel_baseline = None
        self.mode = "speak"
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"Could not read profile {self.path}: {e}")
            return
        self.baseline = data.get("baseline")
        self.targets = {k: v for k, v in data.get("targets", {}).items() if k in TARGET_KEYS}
        self.formant_ceiling = data.get("formant_ceiling")
        self.vowel_baseline = data.get("vowel_baseline")
        self.mode = data.get("mode", "speak")

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        data = {"baseline": self.baseline, "targets": self.targets,
                "formant_ceiling": self.formant_ceiling, "vowel_baseline": self.vowel_baseline,
                "mode": self.mode}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # --- BASELINE ---

    def set_baseline(self, result):
        self.baseline = summarize(result) | {"recorded": datetime.now().strftime("%Y-%m-%d %H:%M")}
        self.save()

    def clear_baseline(self):
        self.baseline = None
        self.save()

    def baseline_value(self, key):
        return self.baseline.get(key) if self.baseline else None

    def change_text(self, key, value):
        """e.g. '+2.3 st vs baseline', or None when there is nothing to compare."""
        base = self.baseline_value(key)
        if base is None or value is None or np.isnan(value):
            return None
        if key == "pitch":
            return f"{12 * np.log2(value / base):+.1f} st vs baseline"
        if key == "delta_f":
            return f"{value - base:+.0f} Hz vs baseline"
        unit = "st" if key in NONNEGATIVE_KEYS else "dB"
        return f"{value - base:+.1f} {unit} vs baseline"

    # --- TARGETS ---

    def target(self, key):
        return self.targets.get(key)

    def set_target(self, key, low_high):
        if low_high is None:
            self.targets.pop(key, None)
        else:
            low, high = sorted(float(v) for v in low_high)
            self.targets[key] = [low, high]
        self.save()

    def set_targets_from_take(self, result):
        summary = summarize(result)
        self.targets = {}
        for key in TARGET_KEYS:
            target = target_from_value(key, summary[key])
            if target is not None:
                self.targets[key] = target
        self.save()

    def in_target(self, key, value):
        """True/False, or None when there is no target or no value."""
        target = self.targets.get(key)
        if target is None or value is None or np.isnan(value):
            return None
        return target[0] <= value <= target[1]

    def time_in_target(self, key, track):
        """Fraction of measured frames of `track` inside the target, or None."""
        target = self.targets.get(key)
        values = np.asarray(track, dtype=float)
        values = values[~np.isnan(values)]
        if target is None or len(values) == 0:
            return None
        return float(np.mean((values >= target[0]) & (values <= target[1])))

    # --- VOWEL CHECK ---

    def set_vowel_baseline(self, vowel_result):
        self.vowel_baseline = _vowel_summary(vowel_result) | {
            "recorded": datetime.now().strftime("%Y-%m-%d %H:%M")}
        self.save()

    def set_mode(self, mode):
        self.mode = mode
        self.save()

    @property
    def voice_map_path(self):
        return self.directory / "voicemap.npz"

    # --- CALIBRATION / HISTORY ---

    def set_formant_ceiling(self, ceiling):
        self.formant_ceiling = int(ceiling)
        self.save()

    def log_take(self, result):
        self._log(summarize(result) | {"kind": "take"})

    def log_vowel_check(self, vowel_result):
        self._log(_vowel_summary(vowel_result) | {"kind": "vowels"})

    def _log(self, entry):
        self.directory.mkdir(parents=True, exist_ok=True)
        entry["time"] = datetime.now().isoformat(timespec="seconds")
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def _vowel_summary(vowel_result):
    return {"delta_f": _clean(vowel_result["delta_f"]),
            "formant_ceiling": _clean(vowel_result["formant_ceiling"]),
            "vowels": {w: {k: _clean(v) for k, v in vals.items()}
                       for w, vals in vowel_result["vowels"].items()}}
