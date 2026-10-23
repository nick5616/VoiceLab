"""Check VoiceLab's pitch and voicing against independent trackers, take by take.

    python tools/accuracy_check.py <takes folder> [--out <folder>] [--app "Voice Tools"]
                                   [--app-reading <file name> <average Hz> <median Hz>] ...

Every audio file in the folder is analysed with VoiceLab (its own mode, from the sidecar when there
is one, else speak) and with two references that share none of its code:
- WORLD harvest (pyworld) with its default range, 71-800 Hz;
- Praat's autocorrelation pitch (Sound: To Pitch (ac)) with Praat's default settings.

The Markdown report gives per take: the median pitch by each method, the share of frames an octave
apart (VoiceLab vs each reference), how often VoiceLab agrees with each on voiced / unvoiced, and the
register shares VoiceLab reports (default, uncalibrated model).

Comparing with another app: record a passage in VoiceLab while the other app (e.g. Voice Tools on a
phone) listens too, then pass that app's average and median for the file with --app-reading. The
report gives the difference in Hz and semitones.

The takes folder is only read: no sidecar, cache or report is ever written into it.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analysis  # noqa: E402
import take_register  # noqa: E402
from take_store import AUDIO_EXTENSIONS, load_audio  # noqa: E402

HARVEST_RATE = 16000          # Hz; harvest on a decimated copy, as in analysis._harvest_f0
OCTAVE_WINDOW_ST = 1.5        # two readings 12 +/- this many semitones apart are an octave error
GROSS_ST = 1.5                # readings further apart than this (and not an octave) disagree
FRAME_MATCH_S = 0.006         # a reference frame within this of a VoiceLab frame is "the same frame"


# --- REFERENCES ---

def harvest_track(audio, times, sample_rate=analysis.SAMPLE_RATE):
    """pyworld harvest with its default range, on `times` (NaN where unvoiced)."""
    import pyworld as pw
    from scipy.signal import resample_poly
    x = np.ascontiguousarray(resample_poly(audio, HARVEST_RATE, sample_rate), dtype=np.float64)
    f0, t = pw.harvest(x, HARVEST_RATE, frame_period=10.0)
    f0[f0 == 0] = np.nan
    return _on_times(t, f0, times)


def praat_track(audio, times, sample_rate=analysis.SAMPLE_RATE):
    """Praat To Pitch (ac) with all default settings, on `times` (NaN where unvoiced)."""
    import parselmouth
    pitch = parselmouth.Sound(audio, sampling_frequency=sample_rate).to_pitch_ac()
    f0 = pitch.selected_array["frequency"].astype(float)
    f0[f0 == 0] = np.nan
    return _on_times(pitch.xs(), f0, times)


def _on_times(ref_times, ref_f0, times):
    ref_times, times = np.asarray(ref_times, dtype=float), np.asarray(times, dtype=float)
    if not len(ref_times):
        return np.full(len(times), np.nan)
    idx = np.clip(np.searchsorted(ref_times, times), 0, len(ref_times) - 1)
    prev = np.clip(idx - 1, 0, len(ref_times) - 1)
    idx = np.where(np.abs(ref_times[prev] - times) < np.abs(ref_times[idx] - times), prev, idx)
    return np.where(np.abs(ref_times[idx] - times) <= FRAME_MATCH_S, ref_f0[idx], np.nan)


# --- COMPARISON ---

def pitch_errors(f0, ref):
    """(octave-error share, other-disagreement share, frames compared) where both are voiced."""
    f0, ref = np.asarray(f0, dtype=float), np.asarray(ref, dtype=float)
    both = ~np.isnan(f0) & ~np.isnan(ref)
    if not both.any():
        return None, None, 0
    st = np.abs(12 * np.log2(f0[both] / ref[both]))
    octave = np.abs(st - 12) < OCTAVE_WINDOW_ST
    return float(octave.mean()), float(((st > GROSS_ST) & ~octave).mean()), int(both.sum())


def voicing_agreement(f0, ref):
    """Share of frames where both call the frame voiced or both unvoiced."""
    a, b = ~np.isnan(np.asarray(f0, dtype=float)), ~np.isnan(np.asarray(ref, dtype=float))
    return float(np.mean(a == b)) if len(a) else None


def check_take(audio, mode="speak"):
    """One take's row: VoiceLab vs the references, plus VoiceLab's register shares. None if VoiceLab
    finds too little voice (the reason is in row["error"])."""
    result = analysis.analyze_take(audio, mode=mode)
    if "error" in result:
        return {"error": result["error"], "mode": mode}
    take_register.finish_take(result)
    times, f0 = result["tracks"]["times"], result["tracks"]["f0"]
    refs = {"harvest": harvest_track(audio, times), "praat": praat_track(audio, times)}
    voiced = ~np.isnan(f0)
    both_refs = ~np.isnan(refs["harvest"]) & ~np.isnan(refs["praat"])
    neither = np.isnan(refs["harvest"]) & np.isnan(refs["praat"])
    row = {"mode": mode, "seconds": len(audio) / analysis.SAMPLE_RATE,
           "median": {"voicelab": _median(f0), **{k: _median(v) for k, v in refs.items()}},
           "mean": {"voicelab": _mean(f0), **{k: _mean(v) for k, v in refs.items()}},
           "voiced_fraction": {"voicelab": float(voiced.mean()),
                               **{k: float((~np.isnan(v)).mean()) for k, v in refs.items()}},
           "agreement": {k: voicing_agreement(f0, v) for k, v in refs.items()},
           "kept": float(voiced[both_refs].mean()) if both_refs.any() else None,
           "extra": float(voiced[neither].mean()) if neither.any() else None,
           "octave": {}, "other": {}, "register": result.get("register") or {},
           "confidence": result.get("confidence") or {}}
    for k, v in refs.items():
        row["octave"][k], row["other"][k], _ = pitch_errors(f0, v)
    return row


def _median(f0):
    f0 = np.asarray(f0, dtype=float)
    return float(np.nanmedian(f0)) if (~np.isnan(f0)).any() else None


def _mean(f0):
    f0 = np.asarray(f0, dtype=float)
    return float(np.nanmean(f0)) if (~np.isnan(f0)).any() else None


def compare_app(row, average, median):
    """Differences between VoiceLab and another app's average and median (Hz and semitones)."""
    out = {}
    for name, ours, theirs in (("average", row["mean"]["voicelab"], average),
                               ("median", row["median"]["voicelab"], median)):
        if ours is None or theirs is None or theirs <= 0:
            out[name] = None
            continue
        out[name] = {"voicelab": ours, "app": float(theirs), "hz": ours - theirs,
                     "st": 12 * np.log2(ours / theirs)}
    return out


# --- REPORT ---

def _fmt(value, spec=".0f", none="–"):
    return none if value is None or (isinstance(value, float) and np.isnan(value)) else format(value, spec)


def _pct(value):
    return _fmt(None if value is None else 100 * value, ".1f") + ("" if value is None else "%")


def render(rows, comparisons=None, app="the other app", folder=""):
    """Markdown report for {file name: row} (and {file name: compare_app result})."""
    lines = ["# VoiceLab accuracy check", "", f"Folder: `{folder}`", "",
             "References: WORLD harvest (default range 71-800 Hz) and Praat To Pitch (ac) with default "
             "settings. Neither shares code with VoiceLab's tracker. *Octave* = frames where both are voiced "
             "and the readings sit an octave apart; *other* = further apart than 1.5 semitones but not an "
             "octave. *Agree* = same voiced/unvoiced call. *Kept* = share of the frames both references "
             "call voiced that VoiceLab keeps; *extra* = share of the frames neither calls voiced that "
             "VoiceLab calls voiced.", "",
             "## Pitch and voicing", "",
             "| Take | Mode | s | Median Hz VoiceLab / harvest / Praat | Octave vs harvest / Praat | "
             "Other vs harvest / Praat | Voiced VoiceLab / harvest / Praat | Agree harvest / Praat | Kept | Extra |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, row in rows.items():
        if "error" in row:
            lines.append(f"| {name} | {row['mode']} | | {row['error']} | | | | | | |")
            continue
        m, o, g, v, a = row["median"], row["octave"], row["other"], row["voiced_fraction"], row["agreement"]
        lines.append(f"| {name} | {row['mode']} | {row['seconds']:.1f} | "
                     f"{_fmt(m['voicelab'])} / {_fmt(m['harvest'])} / {_fmt(m['praat'])} | "
                     f"{_pct(o['harvest'])} / {_pct(o['praat'])} | {_pct(g['harvest'])} / {_pct(g['praat'])} | "
                     f"{_pct(v['voicelab'])} / {_pct(v['harvest'])} / {_pct(v['praat'])} | "
                     f"{_pct(a['harvest'])} / {_pct(a['praat'])} | {_pct(row['kept'])} | {_pct(row['extra'])} |")
    lines += ["", "## Register (VoiceLab's default model, not calibrated to your voice)", "",
              "| Take | Chest | Mix | Head | Fry | Speech label | Confidence |", "|---|---|---|---|---|---|---|"]
    for name, row in rows.items():
        reg = row.get("register") or {}
        if not reg:
            continue
        lines.append(f"| {name} | {_pct(reg.get('chest_share'))} | {_pct(reg.get('mix_share'))} | "
                     f"{_pct(reg.get('head_share'))} | {_pct(reg.get('fry_share'))} | "
                     f"{reg.get('speech_label') or '–'} | {reg.get('confidence', '–')}: "
                     f"{reg.get('confidence_reason', '')} |")
    if comparisons:
        lines += ["", f"## VoiceLab vs {app}", "",
                  f"| Take | Average Hz VoiceLab / {app} | Difference | Median Hz VoiceLab / {app} | Difference |",
                  "|---|---|---|---|---|"]
        for name, comp in comparisons.items():
            cells = []
            for key in ("average", "median"):
                c = comp.get(key)
                cells += (["–", "–"] if c is None else
                          [f"{c['voicelab']:.0f} / {c['app']:.0f}", f"{c['hz']:+.0f} Hz ({c['st']:+.1f} st)"])
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
        lines += ["", "Within about ±0.5 semitone (about ±3% in Hz) the two apps agree as well as two trackers "
                      "usually do; apps differ in how they treat pauses, creak and the ends of phrases."]
    return "\n".join(lines) + "\n"


# --- MAIN ---

def take_mode(path):
    """The mode in the take's sidecar (read only), else speak."""
    sidecar = path.with_suffix(".json")
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "speak"
    return data.get("mode") or ("sing" if data.get("exercise") else "speak")


def run(folder, out=None, app_readings=None, app="the other app", progress=print):
    """Analyse every audio file in `folder` (read only) and write the report; returns its path."""
    folder = Path(folder).resolve()
    out = Path(out).resolve() if out else Path(tempfile.gettempdir()) / "voicelab_accuracy"
    if out == folder or folder in out.parents:
        raise ValueError("The report can't go inside the takes folder: that folder is only read.")
    rows, comparisons = {}, {}
    for path in sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file()):
        progress(f"Analysing {path.name}…")
        audio, _ = load_audio(path)
        rows[path.name] = check_take(audio, take_mode(path))
    for name, average, median in app_readings or []:
        key = next((n for n in rows if n == name or Path(n).stem == name), None)
        if key is None or "error" in rows[key]:
            progress(f"No analysed take called {name}: skipped the {app} comparison for it.")
            continue
        comparisons[key] = compare_app(rows[key], average, median)
    out.mkdir(parents=True, exist_ok=True)
    report = out / "accuracy_check.md"
    report.write_text(render(rows, comparisons, app, folder), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", help="folder of takes (only read)")
    parser.add_argument("--out", help="folder for accuracy_check.md (default: a temp folder)")
    parser.add_argument("--app", default="Voice Tools", help="name of the app you compare with")
    parser.add_argument("--app-reading", nargs=3, action="append", metavar=("FILE", "AVERAGE_HZ", "MEDIAN_HZ"),
                        help="the other app's average and median pitch for one take (repeatable)")
    args = parser.parse_args(argv)
    readings = [(name, float(avg), float(med)) for name, avg, med in args.app_reading or []]
    report = run(args.folder, args.out, readings, args.app)
    print(report.read_text(encoding="utf-8"))
    print(f"Report written to {report}")


if __name__ == "__main__":
    main()
