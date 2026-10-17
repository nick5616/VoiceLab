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

TARGET_KEYS = ["pitch", "delta_f", "h1h2", "cpp", "ring", "contour"]
# Half-width of a target created from a take. Pitch is in semitones, the rest
# in each metric's own units.
DEFAULT_TOLERANCES = {"pitch": 1.5, "delta_f": 30, "h1h2": 3, "cpp": 3, "ring": 3, "contour": 0.75}
SUMMARY_KEYS = TARGET_KEYS + ["level", "vtl", "formant_ceiling", "note", "cents"]


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
    if key == "contour":  # a spread can't be negative
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

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        data = {"baseline": self.baseline, "targets": self.targets,
                "formant_ceiling": self.formant_ceiling}
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
        unit = "st" if key == "contour" else "dB"
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

    # --- CALIBRATION / HISTORY ---

    def set_formant_ceiling(self, ceiling):
        self.formant_ceiling = int(ceiling)
        self.save()

    def log_take(self, result):
        self.directory.mkdir(parents=True, exist_ok=True)
        entry = summarize(result) | {"time": datetime.now().isoformat(timespec="seconds")}
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
