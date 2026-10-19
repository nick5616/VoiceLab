"""Register and EGG results added to an analysed take.

analyze_take knows nothing of either, so recording, importing and the lazy
re-scoring of older takes all go through here. The register is an estimate
(see register.py); with an EGG channel its contact quotient joins the score.
"""
import numpy as np

import egg
import register
from analysis import SAMPLE_RATE, analyze_take

LABELS = ("chest", "head")      # what a take can be labelled as, for calibration
# register.is_fry can also test pulse spacing in the audio, which is the only way to catch fry that
# Praat tracks at the pulse ring's frequency. On clean synthetic vowels it flagged 14-94% of ordinary
# frames as fry (pitch alone: 0%), so it stays off until it has been checked on real recordings.
FRY_FROM_AUDIO = False


def register_model(state):
    """The RegisterModel stored in the profile (a to_dict() dict), or the default."""
    return register.RegisterModel.from_dict(state) if state else register.RegisterModel()


def frame_rate(times):
    times = np.asarray(times, dtype=float)
    return 1 / float(np.median(np.diff(times))) if len(times) > 1 else 100.0


def add_egg(result, egg_samples):
    """Analyse the EGG channel of a take; adds result['cq'], result['egg'] and tracks['cq'].
    The samples stay in result['egg_samples'] so the take store can save them."""
    tracks = result["tracks"]
    n = min(len(egg_samples), len(result["audio"]))
    analysis = egg.analyze_egg_take(egg_samples[:n], SAMPLE_RATE, tracks["times"])
    result["egg_samples"] = np.asarray(egg_samples[:n], dtype=float)
    result["egg"] = {"signal_ok": analysis["signal_ok"], "note": analysis["note"],
                     "cycles": analysis["cycles"], "polarity": analysis["polarity"]}
    if analysis["signal_ok"]:
        result["cq"] = analysis["cq"]
        tracks["cq"] = analysis["cq_track"]
    return result


def score_register(audio, tracks, model):
    """(per-frame score, fry flags, summary) for a take, using its cq track if it has one."""
    times = tracks["times"]
    scores = register.register_track(audio, SAMPLE_RATE, tracks, model, tracks.get("cq"))
    pulses = (audio, SAMPLE_RATE, times) if FRY_FROM_AUDIO else ()
    fry = register.is_fry(tracks["f0"], frame_rate(times), *pulses)
    summary = register.take_register_summary(tracks, scores, fry, model)
    voiced = ~np.isnan(np.asarray(tracks["f0"], dtype=float))
    seconds = float(voiced.sum() / frame_rate(times))
    summary["voiced_seconds"] = seconds
    summary["breaks_per_min"] = len(summary["breaks"]) * 60 / seconds if seconds > 0 else None
    return scores, fry, summary


def add_register(result, state=None, version=0):
    """Store tracks['register'], tracks['fry'] and result['register'] for a take.
    `state` is the profile's register model dict (None: the default model)."""
    scores, fry, summary = score_register(result["audio"], result["tracks"], register_model(state))
    summary["model_version"] = version
    result["tracks"]["register"] = scores
    result["tracks"]["fry"] = fry.astype(float)
    result["register"] = summary
    return result


def finish_take(result, egg_samples=None, state=None, version=0):
    """Add the EGG (if any) and register results to an analyze_take result."""
    if "error" in result:
        return result
    if egg_samples is not None:
        add_egg(result, egg_samples)
    return add_register(result, state, version)


def analyze_recording(audio, egg_samples=None, state=None, version=0):
    """analyze_take plus register and EGG, for a worker thread."""
    return finish_take(analyze_take(audio), egg_samples, state, version)


def is_stale(data, version):
    """True if a take has no register result from the current model."""
    reg = data.get("register")
    return not reg or reg.get("model_version") != version


def label_rows(audio, tracks):
    """[(features, tracks index)] of the voiced, non-fry frames of a take, for fitting."""
    f0 = np.asarray(tracks["f0"], dtype=float)
    times = np.asarray(tracks["times"], dtype=float)
    fry = tracks.get("fry")
    cq = tracks.get("cq")
    audio = np.asarray(audio, dtype=float).flatten()
    rows = []
    for i in np.flatnonzero(~np.isnan(f0)):
        if fry is not None and fry[i] > 0.5:
            continue
        center = int(times[i] * SAMPLE_RATE)
        frame = audio[max(0, center - register.FRAME // 2):center + register.FRAME // 2]
        if len(frame) < register.FRAME:
            continue
        formants = [tracks[k][i] for k in ("f1", "f2", "f3")]
        features = register.frame_features(frame, SAMPLE_RATE, f0[i], formants,
                                           register.NOMINAL_BANDWIDTHS, None if cq is None else cq[i])
        for k in ("h1h2", "cpp"):            # reuse analyze_take's values when present
            values = tracks.get(k)
            if values is not None and not np.isnan(values[i]):
                features[k] = float(values[i])
        rows.append(features)
    return rows


def fit_labelled(labelled):
    """labelled: [(label, rows)]. Returns (model dict or None, frames per class).
    The dict is None until each class has register.MIN_CALIBRATION_FRAMES frames."""
    pairs = [(features, label) for label, rows in labelled for features in rows]
    counts = {name: sum(1 for _, label in pairs if label == name) for name in LABELS}
    model = register.RegisterModel().fit(pairs)
    return (model.to_dict() if model.is_calibrated() else None), counts
