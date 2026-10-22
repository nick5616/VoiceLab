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

TARGET_KEYS = ["pitch", "delta_f", "h1h2", "cpp", "ring", "pitch_range", "register"]
# Smallest half-width of a target created from a take. Pitch is in semitones, the rest
# in each metric's own units. Frame-level spreads in real takes are far wider than the old
# +/-30 Hz and +/-1.5 st (phase 9 audit: 16-36% and 2-42% of frames inside), so deltaF is
# +/-100 Hz (frame IQR is 70-180 Hz) and pitch +/-2 st.
DEFAULT_TOLERANCES = {"pitch": 2, "delta_f": 100, "h1h2": 3, "cpp": 3, "ring": 3, "pitch_range": 2,
                       "register": 15}     # register target = head share of voiced time, in %
# Widest half-width taken from a take's own frame spread: beyond this a "target" says nothing.
MAX_TOLERANCES = {"pitch": 4, "delta_f": 200, "h1h2": 6, "cpp": 5}   # own judgement
SPREAD_PERCENTILES = (15, 85)   # central 70% of frames: a take scores ~70% against itself
MIN_SPREAD_FRAMES = 20          # 0.2 s of measured frames
# result["tracks"] entry holding the per-frame values of each target metric.
TARGET_TRACKS = {"pitch": "f0", "delta_f": "delta_f", "h1h2": "h1h2", "cpp": "cpp"}
# Typical cis-female speech, as a starting point only (the dialog labels it so). f0 and deltaF
# ranges are from memory of Hillenbrand et al. 1995 (women's mean f0 about 220 Hz), Gelfer & Bennett
# 2013 and Leung, Oates & Chan 2018 (f0 about 40% of the variance in perceived gender);
# deltaF 1100+ Hz follows from a ~14.5 cm tract (c / 2L = 1200 Hz). All UNVERIFIED figures.
TARGET_PRESETS = {
    "speak": {"label": "Typical cis-female speech - yours may differ",
              "targets": {"pitch": [165.0, 255.0], "delta_f": [1100.0, 1300.0]}},
}
SUMMARY_KEYS = TARGET_KEYS + ["contour", "level", "vtl", "formant_ceiling", "note", "cents",
                              "snr", "snr_full", "clip_events", "source", "mode", "uid", "cq",
                              "pitch_floor", "voiced_fraction", "ghost_fraction"]
# Scalar parts of a take's exercise score worth tracking over time.
EXERCISE_SUMMARY_KEYS = ["name", "notes_total", "notes_sung", "notes_in_tune",
                         "mean_abs_cents", "time_in_tune"]
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
    """The scalar metrics of an analyze_take result (plus its singing stats and
    exercise score, if any), ready to store."""
    summary = {key: _clean(result.get(key)) for key in SUMMARY_KEYS if key != "register"}
    summary.update(register_summary(result.get("register")))
    summary["task"] = task_of(result)
    summary["tier"] = take_tier(result)
    if result.get("singing"):
        summary["singing"] = {k: _clean(v) for k, v in result["singing"].items()}
    if result.get("exercise"):
        summary["exercise"] = {k: _clean(result["exercise"].get(k)) for k in EXERCISE_SUMMARY_KEYS}
    return summary


def task_of(result):
    """What the take was for, so like is compared with like: a stored task (a passage, 'vowel check'),
    else the exercise name, else free speech or free singing."""
    if result.get("task"):
        return result["task"]
    exercise = result.get("exercise")
    if exercise and exercise.get("name"):
        return exercise["name"]
    return "free singing" if result.get("mode") == "sing" else "free speech"


def take_tier(result):
    """'good' / 'fair' / 'poor' from coach.take_quality (imported late: coach imports this module)."""
    import coach
    try:
        return coach.take_quality(result, result.get("noise"))["tier"]
    except (KeyError, TypeError, ValueError):
        return None


def register_summary(register):
    """Scalar register figures of a take's register summary: head and chest share in %
    (the 'register' target is the head share) and breaks per minute of voiced time."""
    register = register or {}
    return {"register": _percent(register.get("head_share")),
            "chest_share": _percent(register.get("chest_share")),
            "breaks_per_min": _clean(register.get("breaks_per_min"))}


def _percent(share):
    share = _clean(share)
    return None if share is None else 100 * share


def frame_tolerance(key, frames):
    """Half-width from the spread of a take's frames (central 70%, pitch in semitones), at least
    the floor and at most MAX_TOLERANCES. None without enough frames or for untracked metrics."""
    if frames is None or key not in MAX_TOLERANCES:
        return None
    x = np.asarray(frames, dtype=float)
    x = x[~np.isnan(x)]
    if key == "pitch":
        x = 12 * np.log2(x[x > 0])
    if len(x) < MIN_SPREAD_FRAMES:
        return None
    low, high = np.percentile(x, SPREAD_PERCENTILES)
    return float(min(max((high - low) / 2, DEFAULT_TOLERANCES[key]), MAX_TOLERANCES[key]))


def target_from_value(key, value, frames=None):
    """[low, high] around `value`; half-width from `frames` (the take's per-frame values) when
    given, otherwise the floor in DEFAULT_TOLERANCES."""
    if value is None:
        return None
    tol = frame_tolerance(key, frames) or DEFAULT_TOLERANCES[key]
    if key == "pitch":
        return [value * 2 ** (-tol / 12), value * 2 ** (tol / 12)]
    if key == "register":
        return [max(0.0, value - tol), min(100.0, value + tol)]
    if key in NONNEGATIVE_KEYS:
        return [max(0.0, value - tol), value + tol]
    return [value - tol, value + tol]


def _inside(target, values):
    return (values >= target[0]) & (values <= target[1])


def joint_in_target(targets, pitch, delta_f):
    """True only when pitch AND resonance are both on target, so a readout goes green for
    neither alone. False when either is off, None when nothing can be judged (no target set,
    or a needed value is missing: unknown is not on target). A metric with no target is skipped."""
    verdicts = []
    for key, value in (("pitch", pitch), ("delta_f", delta_f)):
        target = targets.get(key)
        if target is None:
            continue
        if value is None or np.isnan(value):
            return None if all(verdicts) else False
        verdicts.append(bool(target[0] <= value <= target[1]))
    return all(verdicts) if verdicts else None


def joint_time_in_target(targets, f0_track, delta_f_track):
    """Fraction of frames where both measured values are inside their targets. Only frames with
    every needed value count; None if there is no target or no such frame."""
    keyed = [(targets.get(k), np.asarray(t, dtype=float))
             for k, t in (("pitch", f0_track), ("delta_f", delta_f_track)) if targets.get(k)]
    if not keyed:
        return None
    ok = np.ones(len(keyed[0][1]), dtype=bool)
    hit = np.ones(len(ok), dtype=bool)
    for target, values in keyed:
        ok &= ~np.isnan(values)
        hit &= _inside(target, values)
    return float(np.mean(hit[ok])) if ok.any() else None


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
        self.takes_dir = None          # folder takes are saved to and loaded from
        self.takes_dir_asked = False   # offered to pick one on first launch
        self.input_device = None       # mic name; None = system default
        self.egg_channel = None        # input channel (0 or 1) carrying an EGG; None = no EGG
        self.register_model = None     # RegisterModel.to_dict() once calibrated to this voice
        self.register_version = 0      # counts model changes, so takes know when to re-score
        self.noise_floor = None        # the engine's noise floor from the last session (spec 02)
        self.mic_check = None          # result of the last mic check (spec 06)
        self.mic_check_asked = False   # offered once on first launch
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
        self.takes_dir = data.get("takes_dir")
        self.takes_dir_asked = data.get("takes_dir_asked", False)
        self.input_device = data.get("input_device")
        self.egg_channel = data.get("egg_channel")
        self.register_model = data.get("register_model")
        self.register_version = data.get("register_version", 0)
        self.noise_floor = data.get("noise_floor")
        self.mic_check = data.get("mic_check")
        self.mic_check_asked = data.get("mic_check_asked", False)

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        data = {"baseline": self.baseline, "targets": self.targets,
                "formant_ceiling": self.formant_ceiling, "vowel_baseline": self.vowel_baseline,
                "mode": self.mode, "takes_dir": self.takes_dir,
                "takes_dir_asked": self.takes_dir_asked, "input_device": self.input_device,
                "egg_channel": self.egg_channel, "register_model": self.register_model,
                "register_version": self.register_version, "noise_floor": self.noise_floor,
                "mic_check": self.mic_check, "mic_check_asked": self.mic_check_asked}
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
        if key == "cq":
            return f"{value - base:+.2f} vs baseline"
        unit = "st" if key in NONNEGATIVE_KEYS else "%" if key == "register" else "dB"
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

    def set_targets_from_take(self, result, trusted=None):
        """Targets around this take's values, widened to its frame spread (target_from_value), so the
        take scores about 70% against itself. `trusted` limits it to metrics the take can support
        (coach.take_quality); targets for the others are left as they were. Returns the keys set."""
        summary = summarize(result)
        tracks = result.get("tracks") or {}
        targets = {k: v for k, v in self.targets.items() if trusted is not None and k not in trusted}
        for key in TARGET_KEYS:
            if trusted is not None and key not in trusted:
                continue
            target = target_from_value(key, summary[key], tracks.get(TARGET_TRACKS.get(key)))
            if target is not None:
                targets[key] = target
        changed = [k for k in TARGET_KEYS if targets.get(k) != self.targets.get(k)]
        self.targets = targets
        self.save()
        return changed

    def set_noise_floor(self, floor):
        self.noise_floor = None if floor is None else float(floor)
        self.save()

    def set_mic_check(self, result):
        self.mic_check = result
        self.mic_check_asked = True
        self.save()

    def set_mic_check_asked(self):
        self.mic_check_asked = True
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

    def set_targets_from_preset(self, name="speak"):
        """Start from a TARGET_PRESETS entry (replaces pitch and deltaF targets only)."""
        for key, target in TARGET_PRESETS[name]["targets"].items():
            self.targets[key] = list(target)
        self.save()

    def in_both(self, pitch, delta_f):
        """Joint pitch-and-resonance verdict, see joint_in_target."""
        return joint_in_target(self.targets, pitch, delta_f)

    def time_in_both(self, f0_track, delta_f_track):
        """Fraction of frames with pitch and resonance both on target, or None."""
        return joint_time_in_target(self.targets, f0_track, delta_f_track)

    # --- VOWEL CHECK ---

    def set_vowel_baseline(self, vowel_result):
        self.vowel_baseline = _vowel_summary(vowel_result) | {
            "recorded": datetime.now().strftime("%Y-%m-%d %H:%M")}
        self.save()

    def set_mode(self, mode):
        self.mode = mode
        self.save()

    def set_takes_dir(self, path):
        self.takes_dir = str(path) if path else None
        self.takes_dir_asked = True
        self.save()

    def set_input_device(self, name):
        self.input_device = name
        self.save()

    def set_egg_channel(self, channel):
        self.egg_channel = channel
        self.save()

    def set_register_model(self, model):
        """Store a calibrated RegisterModel.to_dict() (None resets to the default)."""
        self.register_model = model
        self.register_version += 1
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

    def read_history(self):
        """All logged entries, skipping unreadable lines."""
        if not self.history_path.exists():
            return []
        entries = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def forget_takes(self, uids):
        """Drop takes from the history log, e.g. once a takes folder holds them."""
        uids = set(uids)
        if not uids or not self.history_path.exists():
            return
        kept = [e for e in self.read_history() if e.get("uid") not in uids]
        tmp = self.history_path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in kept), encoding="utf-8")
        tmp.replace(self.history_path)


def _vowel_summary(vowel_result):
    return {"delta_f": _clean(vowel_result["delta_f"]),
            "formant_ceiling": _clean(vowel_result["formant_ceiling"]),
            "vowels": {w: {k: _clean(v) for k, v in vals.items()}
                       for w, vals in vowel_result["vowels"].items()}}
