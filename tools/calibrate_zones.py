"""Calibrate the voice report's zones (report.ZONES) on an open speech corpus, with VoiceLab's own analysis.

    python -I tools/calibrate_zones.py <corpus dir> [<corpus dir> ...] --out <folder>
                                       [--seconds 45] [--take-seconds 15] [--noise-speakers 20]
                                       [--jobs 8] [--limit N]
    python -I tools/calibrate_zones.py --from-csv <folder>/chunks.csv --out <folder>

Corpus dirs are LibriSpeech-style: a SPEAKERS.TXT ("ID | SEX | SUBSET | MINUTES | NAME") somewhere
inside, next to subset folders <subset>/<speaker>/<chapter>/*.flac (e.g. dev-clean, test-clean from
openslr.org/12). Per speaker, about `--seconds` of utterances are joined into takes of about
`--take-seconds` (resampled to VoiceLab's rate) and each take is measured with
analysis.analyze_take(mode="speak") twice, as the app does:
- "auto": no profile ceiling, so each take picks its own formant ceiling;
- "profile": one ceiling from analysis.choose_profile_ceiling over the speaker's takes.
`--noise-speakers` (balanced by sex) are measured again with the user's mic noise added
(tests/mic_noise.py: voice at -42 dBFS rms plus the measured floor and tones).

Writes into --out: chunks.csv (one row per take and condition), speakers.csv (per-speaker medians and
spreads), summary.txt (distributions by sex, overlap, separability, edges under several rules against
report.ZONES, noise shifts, overall placements with report's current constants) and, when
matplotlib is installed, one plot per dimension. The corpus folders are only read.
"""
import argparse
import csv
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from math import gcd
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import analysis  # noqa: E402
import report  # noqa: E402

KEYS = ["pitch", "pitch_floor", "pitch_range", "delta_f", "h1h2", "cpp"]
EXTRA = ["formant_ceiling", "snr", "delta_f_iqr", "vowel_frames", "cpps"]
AUDIO_EXTENSIONS = (".flac", ".wav")
TAKE_DBFS = -30               # clean takes scaled to this rms; the analysis is level-independent above the gate
NOISE_DBFS = -42              # the user's voiced takes sit around -42 dBFS (tests/mic_noise.py)
LR_EDGE = 3.0                 # likelihood-ratio rule: 3:1 one group over the other (own choice)
PERCENTILE_RULES = {"quartile": (75, 25), "decile": (90, 10)}   # (men's pct, women's pct) for the edges
OVERALL_KEYS = [k for k, w in report.WEIGHTS.items() if w]


# --- CORPUS ---

def find_speakers(corpus):
    """[(speaker id, sex "m"/"f", [audio paths])] of a LibriSpeech-style corpus folder."""
    corpus = Path(corpus)
    table = next(corpus.rglob("SPEAKERS.TXT"), None)
    if table is None:
        raise SystemExit(f"No SPEAKERS.TXT under {corpus}")
    speakers = []
    for line in table.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(";") or line.count("|") < 2:
            continue
        sid, sex, subset = (part.strip() for part in line.split("|")[:3])
        folder = table.parent / subset / sid
        if not folder.is_dir():
            continue
        files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_EXTENSIONS)
        if files and sex.upper() in ("M", "F"):
            speakers.append((f"{subset}/{sid}", sex.lower(), [str(p) for p in files]))
    return speakers


def load_takes(files, seconds, take_seconds):
    """About `seconds` of the speaker's utterances, in order, joined into takes of ~take_seconds."""
    import soundfile as sf
    from scipy.signal import resample_poly
    takes, current, total = [], [], 0.0
    for path in files:
        audio, sr = sf.read(path, dtype="float64", always_2d=True)
        audio = audio.mean(axis=1)
        g = gcd(analysis.SAMPLE_RATE, sr)
        current.append(resample_poly(audio, analysis.SAMPLE_RATE // g, sr // g))
        length = sum(len(a) for a in current) / analysis.SAMPLE_RATE
        if length >= take_seconds:
            takes.append(np.concatenate(current))
            total += length
            current = []
        if total >= seconds:
            break
    if current and sum(len(a) for a in current) / analysis.SAMPLE_RATE >= take_seconds / 2:
        takes.append(np.concatenate(current))
    return takes


def _rms_to(audio, dbfs):
    return audio * 10 ** (dbfs / 20) / np.sqrt(np.mean(audio ** 2))


# --- MEASUREMENT ---

def _value(result, key):
    value = result.get(key)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(value) else value


def _measure(takes):
    """Rows for one condition: every take with its own ceiling ("auto"), then with the speaker's
    profile ceiling ("profile"), as the app does before and after it fixes one."""
    rows, results = [], []
    for i, audio in enumerate(takes):
        result = analysis.analyze_take(audio, mode="speak")
        if "error" in result:
            continue
        results.append(result)
        rows.append(("auto", i, result))
    if results:
        ceiling = analysis.choose_profile_ceiling(results)
        for i, audio in enumerate(takes):
            result = analysis.analyze_take(audio, mode="speak", ceiling=ceiling)
            if "error" not in result:
                rows.append(("profile", i, result))
    return rows


def measure_speaker(job):
    """All rows (dicts) for one speaker: clean, and with the user's mic noise when asked."""
    speaker, sex, files, seconds, take_seconds, noise_seed = job
    takes = load_takes(files, seconds, take_seconds)
    conditions = [("clean", [_rms_to(t, TAKE_DBFS) for t in takes])]
    if noise_seed is not None:
        sys.path.insert(0, str(ROOT / "tests"))
        import mic_noise
        noisy = []
        for i, take in enumerate(takes):
            voice = mic_noise.at_level(take, NOISE_DBFS)
            noisy.append(voice + mic_noise.mic_noise(len(voice), seed=noise_seed + i))
        conditions.append(("noisy", noisy))
    out = []
    for condition, audio in conditions:
        for ceiling_mode, take, result in _measure(audio):
            row = {"speaker": speaker, "sex": sex, "condition": condition, "ceiling_mode": ceiling_mode,
                   "take": take, "seconds": round(len(audio[take]) / analysis.SAMPLE_RATE, 2)}
            row.update({k: _value(result, k) for k in KEYS + EXTRA})
            out.append(row)
    print(f"{speaker} ({sex}): {len(takes)} takes, {len(out)} rows", flush=True)
    return out


# --- STATISTICS ---

def _percentiles(values, pcts=(10, 25, 50, 75, 90)):
    return dict(zip(pcts, np.percentile(values, pcts))) if len(values) else {}


def per_speaker(rows):
    """{(condition, ceiling_mode): {speaker: {"sex", key: median, key_spread: max-min over takes}}}."""
    groups = {}
    for row in rows:
        groups.setdefault((row["condition"], row["ceiling_mode"]), {}).setdefault(
            row["speaker"], []).append(row)
    out = {}
    for group, speakers in groups.items():
        out[group] = {}
        for speaker, takes in speakers.items():
            entry = {"sex": takes[0]["sex"], "takes": len(takes)}
            for key in KEYS + EXTRA:
                values = [t[key] for t in takes if t[key] is not None]
                entry[key] = float(np.median(values)) if values else None
                entry[key + "_spread"] = float(np.ptp(values)) if len(values) > 1 else None
            out[group][speaker] = entry
    return out


def _scaled(key, values):
    values = np.asarray(values, dtype=float)
    return np.log2(values) if report.ZONES[key]["log"] else values


def auc(men, women):
    """P(a random woman's value > a random man's): 0.5 = no separation, 1 or 0 = complete."""
    men, women = np.asarray(men), np.asarray(women)
    greater = (women[:, None] > men[None, :]).mean()
    ties = (women[:, None] == men[None, :]).mean()
    return float(greater + ties / 2)


def balanced_threshold(men, women):
    """(threshold, balanced accuracy) of the best single cut, women above (or below) it."""
    values = np.unique(np.concatenate([men, women]))
    cuts = (values[1:] + values[:-1]) / 2
    best = (None, 0.0)
    for cut in cuts:
        acc = ((np.mean(np.asarray(men) < cut) + np.mean(np.asarray(women) > cut)) / 2)
        acc = max(acc, 1 - acc)
        if acc > best[1]:
            best = (float(cut), float(acc))
    return best


def gaussian_points(key, men, women, ratio=LR_EDGE):
    """Equal-likelihood point and the points where one group is `ratio` times likelier, from normal
    fits (in log2 Hz for pitch), searched between the two medians and a little beyond."""
    m, w = _scaled(key, men), _scaled(key, women)
    mu_m, sd_m, mu_w, sd_w = m.mean(), m.std(ddof=1), w.mean(), w.std(ddof=1)
    lo, hi = sorted((np.median(m), np.median(w)))
    grid = np.linspace(lo - (hi - lo), hi + (hi - lo), 4001)

    def logpdf(x, mu, sd):
        return -0.5 * ((x - mu) / sd) ** 2 - np.log(sd)
    llr = logpdf(grid, mu_w, sd_w) - logpdf(grid, mu_m, sd_m)     # > 0: likelier a woman
    inside = (grid >= lo) & (grid <= hi)

    def crossing(level):
        sign = np.sign(llr - level)
        idx = np.flatnonzero(np.diff(sign) != 0)
        if not len(idx):
            return None
        mid = (lo + hi) / 2
        x = grid[idx[np.argmin(np.abs(grid[idx] - mid))]]
        return float(2 ** x) if report.ZONES[key]["log"] else float(x)
    equal = crossing(0.0) if inside.any() else None
    return equal, crossing(-np.log(ratio)), crossing(np.log(ratio))


def zone_shares(key, values, edges=None):
    """{zone: share of values} under report's zones, or under (lo, hi) edges."""
    if edges is None:
        zones = [report.zone_of(key, v) for v in values]
    else:
        spec = report.ZONES[key]
        feminine_high = spec["zones"][0]["id"] == "masculine"
        order = ["masculine", "overlap", "feminine"] if feminine_high else ["feminine", "overlap", "masculine"]
        zones = [order[int(v >= edges[0]) + int(v >= edges[1])] for v in values]
    return {z: float(np.mean([x == z for x in zones])) for z in ("masculine", "overlap", "feminine")}


def overall_position(entry):
    """Overall position 0-1 with report's current scores and weights (every dimension trusted)."""
    scores = {k: report.score(k, entry.get(k)) for k in OVERALL_KEYS}
    scores = {k: s for k, s in scores.items() if s is not None}
    if any(k not in scores for k in report.MAJOR):
        return None
    total = sum(report.WEIGHTS[k] for k in scores)
    return (sum(report.WEIGHTS[k] * s for k, s in scores.items()) / total + 1) / 2


def _fmt(key, value):
    if value is None:
        return "—"
    return f"{value:.0f}" if key in ("pitch", "pitch_floor", "delta_f", "formant_ceiling") else f"{value:.1f}"


def summarise(speakers, lines):
    """Distribution, separability and edge rules per dimension for one (condition, ceiling) group."""
    proposals = {}
    for key in KEYS:
        men = np.array([e[key] for e in speakers.values() if e["sex"] == "m" and e[key] is not None])
        women = np.array([e[key] for e in speakers.values() if e["sex"] == "f" and e[key] is not None])
        if len(men) < 3 or len(women) < 3:
            continue
        pm, pw = _percentiles(men), _percentiles(women)
        spec = report.ZONES[key]
        old = tuple(z["lo"] for z in spec["zones"][1:])
        lines.append(f"\n{key} ({spec['unit']})  men n={len(men)}, women n={len(women)}")
        for label, p, values in (("men", pm, men), ("women", pw, women)):
            lines.append(f"  {label:6s} median {_fmt(key, p[50])}  IQR {_fmt(key, p[25])}-{_fmt(key, p[75])}"
                         f"  10-90th {_fmt(key, p[10])}-{_fmt(key, p[90])}"
                         f"  min-max {_fmt(key, values.min())}-{_fmt(key, values.max())}")
        a = auc(men, women)
        cut, acc = balanced_threshold(men, women)
        equal, lr_m, lr_w = gaussian_points(key, men, women)
        lines.append(f"  separation: AUC {a:.2f} (women higher = >0.5); best single cut {_fmt(key, cut)} "
                     f"(balanced accuracy {acc:.0%}); equal-likelihood point (normal fits) {_fmt(key, equal)}; "
                     f"{LR_EDGE:g}:1 points {_fmt(key, lr_m)} / {_fmt(key, lr_w)}")
        overlap_m = np.mean(men > np.min(women)) if a >= 0.5 else np.mean(men < np.max(women))
        overlap_w = np.mean(women < np.max(men)) if a >= 0.5 else np.mean(women > np.min(men))
        lines.append(f"  overlap: {overlap_m:.0%} of men lie inside the women's span, {overlap_w:.0%} of women "
                     f"inside the men's")
        rules, labels = {"now": old}, {"now": "report.py now"}
        for name, (pct_m, pct_w) in PERCENTILE_RULES.items():
            if a < 0.5:                         # women lower (clarity): mirror the percentiles
                pct_m, pct_w = 100 - pct_m, 100 - pct_w
            rules[name] = tuple(sorted((np.percentile(men, pct_m), np.percentile(women, pct_w))))
            labels[name] = f"{name} (men {pct_m}th / women {pct_w}th)"
        if lr_m is not None and lr_w is not None:
            rules["likelihood"] = tuple(sorted((lr_m, lr_w)))
            labels["likelihood"] = f"likelihood {LR_EDGE:g}:1"
        proposals[key] = rules
        for name, edges in rules.items():
            sm, sw = zone_shares(key, men, edges), zone_shares(key, women, edges)
            lines.append(f"  {labels[name]:34s} {_fmt(key, edges[0])}-{_fmt(key, edges[1])}:  "
                         f"men {sm['masculine']:.0%}/{sm['overlap']:.0%}/{sm['feminine']:.0%}  "
                         f"women {sw['masculine']:.0%}/{sw['overlap']:.0%}/{sw['feminine']:.0%}  (masc/overlap/fem)")
        spreads = [e[key + "_spread"] for e in speakers.values() if e.get(key + "_spread") is not None]
        if spreads:
            lines.append(f"  take-to-take spread within a speaker (max-min over takes): median "
                         f"{_fmt(key, float(np.median(spreads)))}, 90th pct {_fmt(key, float(np.percentile(spreads, 90)))}")
    return proposals


def summarise_overall(speakers, lines):
    """Overall placements with report's current constants: counts per zone by sex."""
    lines.append(f"\noverall placement with report.py's current ZONES / ANCHORS / WEIGHTS "
                 f"(OVERALL_EDGES {report.OVERALL_EDGES}):")
    for sex, label in (("m", "men"), ("f", "women")):
        positions = [overall_position(e) for e in speakers.values() if e["sex"] == sex]
        positions = np.array([p for p in positions if p is not None])
        if not len(positions):
            continue
        counts = [int(np.sum(positions < report.OVERALL_EDGES[0])),
                  int(np.sum((positions >= report.OVERALL_EDGES[0]) & (positions <= report.OVERALL_EDGES[1]))),
                  int(np.sum(positions > report.OVERALL_EDGES[1]))]
        pct = _percentiles(positions)
        lines.append(f"  {label:6s} n={len(positions)}: masculine {counts[0]}, overlap {counts[1]}, feminine "
                     f"{counts[2]}; position median {pct[50]:.2f}, 10-90th {pct[10]:.2f}-{pct[90]:.2f}, "
                     f"range {positions.min():.2f}-{positions.max():.2f}")


def noise_shifts(groups, lines):
    """Paired noisy-minus-clean differences per speaker and ceiling mode."""
    for mode in ("auto", "profile"):
        clean, noisy = groups.get(("clean", mode)), groups.get(("noisy", mode))
        if not clean or not noisy:
            continue
        lines.append(f"\nnoise shift (noisy - clean, {mode} ceiling, voice at {NOISE_DBFS} dBFS + the user's mic noise):")
        for key in KEYS + ["formant_ceiling", "snr"]:
            for sex, label in (("m", "men"), ("f", "women"), (None, "all")):
                pairs = [(noisy[s][key], clean[s][key]) for s in noisy
                         if s in clean and noisy[s][key] is not None and clean[s][key] is not None
                         and (sex is None or clean[s]["sex"] == sex)]
                if key in ("pitch", "pitch_floor"):
                    diffs = [12 * np.log2(n / c) for n, c in pairs]
                else:
                    diffs = [n - c for n, c in pairs]
                if diffs:
                    p = _percentiles(np.array(diffs))
                    unit = " st" if key in ("pitch", "pitch_floor") else ""
                    lines.append(f"  {key:15s} {label:6s} n={len(diffs):2d}: median {p[50]:+.1f}{unit}, "
                                 f"IQR {p[25]:+.1f}..{p[75]:+.1f}, 10-90th {p[10]:+.1f}..{p[90]:+.1f}")


def plot(groups, proposals, out, rule_names):
    """One strip plot per dimension: speakers by sex, report.py's edges and the proposed ones."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: no plots")
        return
    speakers = groups[("clean", "profile")]
    for key in KEYS:
        spec = report.ZONES[key]
        fig, ax = plt.subplots(figsize=(9, 3.2))
        for row, (sex, colour, label) in enumerate((("m", "#2b6cb0", "men"), ("f", "#c53030", "women"))):
            values = [e[key] for e in speakers.values() if e["sex"] == sex and e[key] is not None]
            jitter = np.random.default_rng(row).uniform(-0.15, 0.15, len(values))
            ax.scatter(values, row + jitter, s=14, color=colour, alpha=0.7, label=f"{label} (n={len(values)})")
        styles = [("now", "k", "-")] + [(n, c, "--") for n, c in zip(rule_names, ("#6b46c1", "#2f855a"))]
        for name, colour, style in styles:
            edges = proposals.get(key, {}).get(name)
            if edges:
                for i, edge in enumerate(edges):
                    ax.axvline(edge, color=colour, ls=style, lw=1.2,
                               label=("report.py now" if name == "now" else name + " rule") if i == 0 else None)
        ax.set_xlim(*spec["scale"])
        if spec["log"]:
            ax.set_xscale("log")
        ax.set_yticks([0, 1], ["men", "women"])
        ax.set_ylim(-0.6, 1.6)
        ax.set_xlabel(f"{spec['name']} ({key}, {spec['unit']}), profile ceiling")
        ax.legend(fontsize=7, loc="upper left", ncol=2)
        fig.tight_layout()
        fig.savefig(Path(out) / f"zones_{key}.png", dpi=110)
        plt.close(fig)


# --- MAIN ---

def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for key in KEYS + EXTRA + ["take", "seconds"]:
                row[key] = float(row[key]) if row.get(key) not in (None, "") else None
            rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("corpus", nargs="*", help="LibriSpeech-style corpus folders")
    parser.add_argument("--out", required=True, help="folder for the CSVs, summary and plots")
    parser.add_argument("--from-csv", help="summarise an earlier chunks.csv instead of measuring")
    parser.add_argument("--seconds", type=float, default=45, help="audio per speaker (s)")
    parser.add_argument("--take-seconds", type=float, default=15, help="length of one take (s)")
    parser.add_argument("--noise-speakers", type=int, default=20, help="speakers also measured with mic noise")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--limit", type=int, help="first N speakers of each sex only (a quick check)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fields = ["speaker", "sex", "condition", "ceiling_mode", "take", "seconds"] + KEYS + EXTRA

    if args.from_csv:
        rows = read_csv(args.from_csv)
    else:
        speakers = [s for corpus in args.corpus for s in find_speakers(corpus)]
        if args.limit:
            speakers = ([s for s in speakers if s[1] == "m"][:args.limit]
                        + [s for s in speakers if s[1] == "f"][:args.limit])
        per_sex = args.noise_speakers // 2
        noisy = set(s[0] for s in [s for s in speakers if s[1] == "m"][::2][:per_sex]) | set(
            s[0] for s in [s for s in speakers if s[1] == "f"][::2][:per_sex])
        jobs = [(sid, sex, files, args.seconds, args.take_seconds, 1000 * i if sid in noisy else None)
                for i, (sid, sex, files) in enumerate(speakers)]
        print(f"{len(jobs)} speakers ({sum(s[1] == 'm' for s in speakers)} men), {len(noisy)} also with "
              f"noise, {args.jobs} processes", flush=True)
        with ProcessPoolExecutor(args.jobs) as pool:
            rows = [row for result in pool.map(measure_speaker, jobs) for row in result]
        write_csv(out / "chunks.csv", rows, fields)

    groups = per_speaker(rows)
    speaker_rows = [dict(e, speaker=s, condition=c, ceiling_mode=m) for (c, m), sp in sorted(groups.items())
                    for s, e in sorted(sp.items())]
    write_csv(out / "speakers.csv", speaker_rows,
              ["speaker", "sex", "condition", "ceiling_mode", "takes"]
              + [k for key in KEYS + EXTRA for k in (key, key + "_spread")])

    lines = [f"VoiceLab zone calibration: {len({r['speaker'] for r in rows})} speakers, "
             f"{len(rows)} take analyses. Per-speaker medians over takes; zone shares are "
             "masculine/overlap/feminine."]
    proposals = {}
    for group in [("clean", "profile"), ("clean", "auto"), ("noisy", "profile"), ("noisy", "auto")]:
        if group not in groups:
            continue
        lines.append(f"\n===== {group[0]} speech, {group[1]} formant ceiling: {len(groups[group])} speakers =====")
        found = summarise(groups[group], lines)
        if group == ("clean", "profile"):
            proposals = found
        summarise_overall(groups[group], lines)
    noise_shifts(groups, lines)
    text = "\n".join(lines) + "\n"
    (out / "summary.txt").write_text(text, encoding="utf-8")
    print(text)
    if ("clean", "profile") in groups:
        plot(groups, proposals, out, ["quartile", "likelihood"])


if __name__ == "__main__":
    main()
