"""Progress over weeks: every take and vowel check charted over time, with the
weekly median (and how many takes it rests on), your baseline and target, and the change since
your first week, said only when there are enough takes and the change is bigger than the
take-to-take variation. view() gives a UI everything to chart. No UI toolkit."""
from datetime import datetime, timedelta

import numpy as np

import report
from profile_store import summarize

MIN_TREND_TAKES = 5            # fewer takes than this say nothing about a trend (spec 07)
NOISE_Z = 2.0                  # a change must beat this many standard errors of the take-to-take spread
DAY = 86400
WEEK = 7 * DAY
PERIODS = [("All time", None), ("Last 90 days", 90), ("Last 30 days", 30)]
MODES = [("Speak & Sing", None), ("Speak", "speak"), ("Sing", "sing")]
EMPTY_TEXT = ("Nothing to chart yet. Record some takes over a few days, and they'll "
              "show up here with a weekly median (yellow) against your baseline and targets.")
TASK_TOOLTIP = "Compare like with like: free speech, a passage, an exercise."
IMPORTED_TOOLTIP = "Imported files may be someone else's voice, so they're left out by default."
POOR_TOOLTIP = "Takes too noisy or quiet to trust are left out by default."


def _get(*path):
    """Getter for a nested value, None when any step is missing."""
    def get(entry):
        for key in path:
            entry = entry.get(key) if isinstance(entry, dict) else None
        return entry
    return get


def _share_in_tune(entry):
    ex = entry.get("exercise") or {}
    if not ex.get("notes_total"):
        return None
    return 100 * ex["notes_in_tune"] / ex["notes_total"]


# key: (title, unit, kind of entry, getter, profile key for baseline/target).
# Pitch changes are reported in semitones, the rest in their own units.
SERIES = {
    "pitch": ("Pitch", "Hz", "take", _get("pitch"), "pitch"),
    "delta_f": ("Resonance (ΔF)", "Hz", "take", _get("delta_f"), "delta_f"),
    "vowel_delta_f": ("Vowel-balanced ΔF (vowel check)", "Hz", "vowels", _get("delta_f"), None),
    "h1h2": ("Fold Contact (H1*–H2*)", "dB", "take", _get("h1h2"), "h1h2"),
    "cpp": ("Clarity (CPP)", "dB", "take", _get("cpp"), "cpp"),
    "ring": ("Ring (2–4 kHz)", "dB", "take", _get("ring"), "ring"),
    "pitch_range": ("Intonation Range", "st", "take", _get("pitch_range"), "pitch_range"),
    "in_tune": ("Exercise notes in tune", "%", "take", _share_in_tune, None),
    "exercise_error": ("Exercise average error", "¢", "take", _get("exercise", "mean_abs_cents"), None),
    "vibrato_rate": ("Vibrato rate", "Hz", "take", _get("singing", "vibrato_rate"), None),
    "vibrato_extent": ("Vibrato extent", "¢", "take", _get("singing", "vibrato_extent"), None),
    "head_share": ("Head share (register estimate)", "%", "take", _get("register"), "register"),
    "chest_share": ("Chest share (register estimate)", "%", "take", _get("chest_share"), None),
    "register_breaks": ("Register breaks per minute", "/min", "take", _get("breaks_per_min"), None),
    "cq": ("Contact quotient (EGG)", "", "take", _get("cq"), None),
}
SING_ONLY = {"register_breaks"}        # series that only mean something when singing


def overall_of(entry):
    """The report's overall placement of a stored take, 0 (fully masculine) - 100 (fully feminine), from
    its summary: pitch and resonance are both needed (report.MAJOR), the floor and intonation join in
    with their report.WEIGHTS. None for sung takes or without both. Unlike the report it can't see
    which readings the take trusted, so poor takes (left out by default) can mislead."""
    if entry.get("mode") == "sing" or entry.get("exercise"):
        return None
    scores = {key: report.score(key, entry.get(key)) for key, weight in report.WEIGHTS.items() if weight}
    scores = {key: s for key, s in scores.items() if s is not None}
    if any(key not in scores for key in report.MAJOR):
        return None
    total = sum(report.WEIGHTS[key] for key in scores)
    point = sum(report.WEIGHTS[key] * s for key, s in scores.items()) / total
    return 100 * (point + 1) / 2


SERIES["pitch_floor"] = ("Pitch floor", "Hz", "take", _get("pitch_floor"), "pitch_floor")
SERIES["overall"] = ("Overall placement", "%", "take", overall_of, None)
# --- REPORT DIMENSIONS (spec 06) ---
OVERALL_KEY = "overall"
REPORT_KEYS = [OVERALL_KEY, "pitch", "delta_f", "pitch_range", "pitch_floor", "h1h2", "cpp"]   # one chart
#                 each; pitch and resonance lead, as on the take card (they carry the placement)
OVERALL_ZONES = {"name": "Overall placement", "unit": "%", "scale": (0, 100), "log": False,
                 "zones": [{"id": "masculine", "label": report.ZONE_LABELS["masculine"], "lo": 0.0,
                            "hi": 100 * report.OVERALL_EDGES[0]},
                           {"id": "overlap", "label": report.ZONE_LABELS["overlap"],
                            "lo": 100 * report.OVERALL_EDGES[0], "hi": 100 * report.OVERALL_EDGES[1]},
                           {"id": "feminine", "label": report.ZONE_LABELS["feminine"],
                            "lo": 100 * report.OVERALL_EDGES[1], "hi": 100.0}]}
OVERALL_EXPLANATION = ("Where pitch and resonance together place each take, from typically masculine "
                       "to typically feminine; the same placement as the take report's overall bar.")
MINI_WEEKS = 8                  # weeks the dashboard's sparklines cover
MINI_KEYS = ("pitch", "delta_f", OVERALL_KEY)


def collect_entries(history, folder_takes=()):
    """History log entries plus the takes in the takes folder, one per take."""
    entries, seen = [], set()
    for take in folder_takes:
        entries.append(summarize(take) | {"kind": "take", "time": take["time"]})
        if take.get("uid"):
            seen.add(take["uid"])
    # A take in both (logged, then saved when a folder was chosen) counts once.
    entries += [e for e in history if not (e.get("kind") == "take" and e.get("uid") in seen)]
    return entries


def timestamp(entry):
    try:
        return datetime.fromisoformat(entry["time"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def entry_task(entry):
    """What a take was for: its stored task, else the exercise, else free speech or singing."""
    if entry.get("kind") == "vowels":
        return "vowel check"
    if entry.get("task"):
        return entry["task"]
    exercise = entry.get("exercise") or {}
    if exercise.get("name"):
        return exercise["name"]
    return "free singing" if entry.get("mode") == "sing" else "free speech"


def select(entries, key, mode=None, include_imported=False, days=None, now=None,
           include_poor=False, task=None):
    """(times, values) of one series, oldest first, after the filters. Takes graded poor are left
    out unless include_poor; `task` keeps one kind of take (see entry_task), so unlike isn't mixed."""
    _, _, kind, getter, _ = SERIES[key]
    cutoff = ((now or datetime.now()) - timedelta(days=days)).timestamp() if days else None
    points = []
    for e in entries:
        if e.get("kind", "take") != kind:
            continue
        if key in SING_ONLY and e.get("mode") != "sing":
            continue
        if kind == "take" and ((mode and e.get("mode") != mode) or
                               (e.get("source") and not include_imported)):
            continue
        if kind == "take" and ((e.get("tier") == "poor" and not include_poor) or
                               (task and entry_task(e) != task)):
            continue
        if kind == "vowels" and mode == "sing":
            continue
        t, v = timestamp(e), getter(e)
        if t is None or v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        if cutoff is None or t >= cutoff:
            points.append((t, float(v)))
    points.sort()
    return np.array([p[0] for p in points]), np.array([p[1] for p in points])


def week_start(t):
    """Timestamp of local midnight on the Monday of t's week."""
    day = datetime.fromtimestamp(t).replace(hour=0, minute=0, second=0, microsecond=0)
    return (day - timedelta(days=day.weekday())).timestamp()


def weekly_groups(times, values):
    """{week start: [values]} for weeks with data."""
    weeks = {}
    for t, v in zip(times, values):
        weeks.setdefault(week_start(t), []).append(v)
    return weeks


def weekly_medians(times, values):
    """(week start times, median per week) for weeks with data."""
    weeks = weekly_groups(times, values)
    starts = sorted(weeks)
    return np.array(starts), np.array([float(np.median(weeks[w])) for w in starts])


def weekly_counts(times, values):
    """Takes behind each weekly median, in the same order."""
    weeks = weekly_groups(times, values)
    return np.array([len(weeks[w]) for w in sorted(weeks)])


def take_spread(key, times, values):
    """Take-to-take standard deviation: pooled within each week where a week has repeated takes,
    otherwise over all of them. Pitch is measured in semitones. None with fewer than two takes."""
    values = 12 * np.log2(values) if key == "pitch" else np.asarray(values, dtype=float)
    weeks = [np.asarray(v) for v in weekly_groups(times, values).values() if len(v) > 1]
    if weeks:
        dof = sum(len(w) - 1 for w in weeks)
        return float(np.sqrt(sum(((w - w.mean()) ** 2).sum() for w in weeks) / dof))
    return float(np.std(values, ddof=1)) if len(values) > 1 else None


def clear_change(key, times, values):
    """True if the last week's median differs from the first's by more than NOISE_Z standard errors of
    the take-to-take spread; False with too few takes or one week."""
    if len(values) < MIN_TREND_TAKES:
        return False
    starts, medians = weekly_medians(times, values)
    if len(starts) < 2:
        return False
    counts = weekly_counts(times, values)
    spread = take_spread(key, times, values)
    if spread is None:
        return False
    delta = 12 * np.log2(medians[-1] / medians[0]) if key == "pitch" else medians[-1] - medians[0]
    return bool(abs(delta) > NOISE_Z * spread * np.sqrt(1 / counts[0] + 1 / counts[-1]))


def change_text(key, times, values):
    """e.g. 'this week 1130 Hz · +45 Hz since week of Oct 6 · 12 takes', or the latest value when
    there is only one week. No change is claimed from fewer than MIN_TREND_TAKES takes, nor when it is
    within the take-to-take variation."""
    if len(values) == 0:
        return "no data yet"
    unit = SERIES[key][1]
    starts, medians = weekly_medians(times, values)
    current = "this week" if starts[-1] == week_start(datetime.now().timestamp()) else "latest week"
    latest = f"{current} {_format(medians[-1], unit)}"
    n = len(values)
    takes = f"{n} take{'s' if n != 1 else ''}"
    if len(starts) < 2:
        return f"{latest} · {takes}"
    if n < MIN_TREND_TAKES:
        return f"{latest} · {takes}: too few to call a trend"
    first = datetime.fromtimestamp(starts[0]).strftime("%b %d").replace(" 0", " ")
    if not clear_change(key, times, values):
        return f"{latest} · no clear change since week of {first} (within your usual take-to-take variation) · {takes}"
    if key == "pitch":
        delta = f"{12 * np.log2(medians[-1] / medians[0]):+.1f} st"
    else:
        delta = _format(medians[-1] - medians[0], unit, sign=True, scale=medians[0])
    return f"{latest} · {delta} since week of {first} · {takes}"


def _format(value, unit, sign=False, scale=None):
    """Whole numbers for large-valued metrics (Hz), one decimal otherwise."""
    decimals = 0 if abs(value if scale is None else scale) >= 100 else 2 if unit == "" else 1
    space = "" if unit in ("%", "¢", "") else " "
    return f"{value:{'+' if sign else ''}.{decimals}f}{space}{unit}"


# --- WHAT A UI CHARTS ---

def reference(profile, key):
    """(baseline, target) of a series on its chart's axis; either may be None."""
    if key == "vowel_delta_f":
        vb = profile.vowel_baseline
        return (vb.get("delta_f") if vb else None), profile.target("delta_f")
    profile_key = SERIES[key][4]
    if not profile_key:
        return None, None
    return profile.baseline_value(profile_key), profile.target(profile_key)


def task_counts(entries, include_poor=False):
    """[(task, takes)] of the tasks takes were made for, most common first."""
    counts = {}
    for e in entries:
        if e.get("kind", "take") == "take" and not (e.get("tier") == "poor" and not include_poor):
            counts[entry_task(e)] = counts.get(entry_task(e), 0) + 1
    return [(name, counts[name]) for name in sorted(counts, key=lambda t: -counts[t])]


def pick_task(tasks, choice=None):
    """The task to chart: `choice` once picked (a 1-tuple, so "All tasks", None, is a choice), else the
    most common. A choice no longer offered gives None (all tasks)."""
    names = [name for name, _ in tasks]
    wanted = choice[0] if choice else (names[0] if names else None)
    return wanted if wanted in names else None


def chart(key, times, values, baseline=None, target=None):
    """One series drawn: {"key", "name", "unit", "change", "times", "values", "median_times" (mid-week),
    "medians", "counts", "baseline", "target", "y_range", "x_range"}."""
    starts, medians = weekly_medians(times, values)
    span = [*values, *([baseline] if baseline is not None else []), *(target or [])]
    lo, hi = min(span), max(span)
    pad = max((hi - lo) * 0.15, 1e-3 * max(abs(hi), 1), 0.05 if SERIES[key][1] == "" else 0.5)
    t0, t1 = min(times[0], starts[0]), max(times[-1], starts[-1] + WEEK)
    return {"key": key, "name": SERIES[key][0], "unit": SERIES[key][1], "change": change_text(key, times, values),
            "times": times, "values": values, "median_times": starts + WEEK / 2, "medians": medians,
            "counts": weekly_counts(times, values), "baseline": baseline, "target": target,
            "y_range": (lo - pad, hi + pad), "x_range": (t0 - DAY / 2, t1 + DAY / 2)}


def left_out_text(entries, task=None, include_poor=False):
    """Says how many poor recordings the charts leave out, or None when there are none (or they're shown)."""
    hidden = sum(1 for e in entries if e.get("kind", "take") == "take" and e.get("tier") == "poor"
                 and (not task or entry_task(e) == task) and not e.get("source"))
    if not hidden or include_poor:
        return None
    return (f"{hidden} poor recording{'s' if hidden != 1 else ''} left out. "
            "Tick “Include poor recordings” to chart them.")


def view(entries, profile, mode=None, days=None, include_imported=False, include_poor=False, task_choice=None):
    """Everything the progress view shows: {"tasks": [(task, takes)], "task", "charts": {key: chart or None},
    "empty": bool, "left_out": text or None}. A series without data has no chart."""
    tasks = task_counts(entries, include_poor)
    task = pick_task(tasks, task_choice)
    charts = {}
    for key in SERIES:
        times, values = select(entries, key, mode=mode, include_imported=include_imported, days=days,
                               include_poor=include_poor, task=task)
        charts[key] = chart(key, times, values, *reference(profile, key)) if len(values) else None
    return {"tasks": tasks, "task": task, "charts": charts, "empty": not any(charts.values()),
            "left_out": left_out_text(entries, task, include_poor)}


# --- THE PROGRESS SCREEN AND THE DASHBOARD'S SPARKLINES (spec 06) ---

def zones_of(key):
    """The zones a dimension's chart shades (report.ZONES, or the overall scale), or None."""
    return OVERALL_ZONES if key == OVERALL_KEY else report.zones(key)


def explanation(key):
    return OVERALL_EXPLANATION if key == OVERALL_KEY else report.EXPLANATIONS.get(key, "")


def zone_chart(key, times, values, baseline=None, target=None):
    """chart() for a report dimension, on its zones: adds "zones", "log", "explanation", "latest_zone"
    and a y_range that always shows the whole zone scale (so the bands sit still from week to week),
    widened only when a take falls outside it."""
    out = chart(key, times, values, baseline, target)
    spec = zones_of(key)
    lo, hi = spec["scale"]
    span = [*values, *([baseline] if baseline is not None else []), *(target or [])]
    out.update(zones=spec, log=spec["log"], explanation=explanation(key), name=report.NAMES.get(key, out["name"]),
               y_range=(min(lo, min(span)), max(hi, max(span))),
               latest_zone=report.zone_of(key, out["medians"][-1]) if key != OVERALL_KEY else
               _overall_zone(out["medians"][-1]))
    if key == OVERALL_KEY:                  # a placement is said as its zone, not as a percentage
        first, _, rest = out["change"].partition(" · ")
        week = first.rsplit(" ", 1)[0]
        out["change"] = f"{week}: {report.ZONE_LABELS[out['latest_zone']]}" + (f" · {rest}" if rest else "")
    return out


def _overall_zone(value):
    edges = [100 * e for e in report.OVERALL_EDGES]
    return "masculine" if value < edges[0] else "feminine" if value > edges[1] else "overlap"


def report_view(entries, profile, task_choice=None, include_poor=False, days=None):
    """Everything the Progress screen shows: {"tasks", "task", "charts": [(key, zone chart or None)] in
    REPORT_KEYS order (speak takes, like with like), "more": {key: chart} of every other series with
    data, "empty", "left_out"}. Poor takes are left out unless include_poor; imported files always."""
    tasks = task_counts(entries, include_poor)
    task = pick_task(tasks, task_choice)
    charts = []
    for key in REPORT_KEYS:
        times, values = select(entries, key, mode="speak", days=days, include_poor=include_poor, task=task)
        charts.append((key, zone_chart(key, times, values, *reference(profile, key)) if len(values) else None))
    more = {}
    for key in SERIES:
        if key in REPORT_KEYS:
            continue
        times, values = select(entries, key, days=days, include_poor=include_poor, task=task)
        if len(values):
            more[key] = chart(key, times, values, *reference(profile, key))
    return {"tasks": tasks, "task": task, "charts": charts, "more": more,
            "empty": not any(c for _, c in charts) and not more,
            "left_out": left_out_text(entries, task, include_poor)}


def mini(entries, profile, weeks=MINI_WEEKS, now=None):
    """The dashboard's sparklines: {"task", "weeks", "series": [{"key", "name", "values" (weekly
    median per week, oldest first, None for weeks without takes), "latest" (text), "zone", "target",
    "count"}]} over the last `weeks` weeks, for the most common task, good and fair speak takes."""
    now = now or datetime.now()
    task = pick_task(task_counts(entries))
    this_week = week_start(now.timestamp())
    starts = [this_week - WEEK * i for i in range(weeks - 1, -1, -1)]
    series = []
    for key in MINI_KEYS:
        times, values = select(entries, key, mode="speak", task=task, days=7 * weeks, now=now)
        groups = weekly_groups(times, values)
        medians = [float(np.median(groups[s])) if s in groups else None for s in starts]
        known = [m for m in medians if m is not None]
        latest = None
        if known:
            last = known[-1]
            latest = f"{last:.0f}{'' if SERIES[key][1] == '%' else ' '}{SERIES[key][1]}"
            if key == OVERALL_KEY:
                latest = report.ZONE_LABELS[_overall_zone(last)]
        zone = (None if not known else _overall_zone(known[-1]) if key == OVERALL_KEY
                else report.zone_of(key, known[-1]))
        series.append({"key": key, "name": "Resonance" if key == "delta_f" else SERIES[key][0],
                       "values": medians, "latest": latest, "zone": zone,
                       "target": reference(profile, key)[1], "count": len(values)})
    return {"task": task, "weeks": weeks, "series": series}
