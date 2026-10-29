"""Progress over weeks: every take and vowel check charted over time, with the
weekly median (and how many takes it rests on), your baseline and target, and the change since
your first week, said only when there are enough takes and the change is bigger than the
take-to-take variation. view() gives a UI everything to chart. No UI toolkit."""
from datetime import datetime, timedelta

import numpy as np

import report
from display import quantity_name
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
MIN_LINE_WEEKS = 3             # weekly medians needed to draw a line; fewer are drawn as dots only
CHART_LEGEND = ("Line = weekly median · dots = single takes · the number is how many takes that week rests on")
NO_LINE_TEXT = "Not enough weeks for a line yet: the dots are single takes."
STEADY_TOOLTIP = "Within your usual take-to-take variation, so no change is claimed."
PRACTICE_WINDOW_DAYS = 7       # the quiet "Practised N of the last 7 days" line


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
    "pitch": (quantity_name("pitch"), "Hz", "take", _get("pitch"), "pitch"),
    "delta_f": (quantity_name("delta_f"), "Hz", "take", _get("delta_f"), "delta_f"),
    "vowel_delta_f": ("Resonance from the vowel check", "Hz", "vowels", _get("delta_f"), None),
    "h1h2": (quantity_name("h1h2"), "dB", "take", _get("h1h2"), "h1h2"),
    "cpp": (quantity_name("cpp"), "dB", "take", _get("cpp"), "cpp"),
    "ring": (quantity_name("ring"), "dB", "take", _get("ring"), "ring"),
    "pitch_range": (quantity_name("pitch_range"), "st", "take", _get("pitch_range"), "pitch_range"),
    "in_tune": ("Exercise notes in tune", "%", "take", _share_in_tune, None),
    "exercise_error": ("Exercise average error", "¢", "take", _get("exercise", "mean_abs_cents"), None),
    "vibrato_rate": ("Vibrato rate", "Hz", "take", _get("singing", "vibrato_rate"), None),
    "vibrato_extent": ("Vibrato extent", "¢", "take", _get("singing", "vibrato_extent"), None),
    "head_share": ("Head voice share (rough guide)", "%", "take", _get("register"), "register"),
    "chest_share": ("Chest voice share (rough guide)", "%", "take", _get("chest_share"), None),
    "register_breaks": ("Register breaks per minute", "/min", "take", _get("breaks_per_min"), None),
    "cq": ("Contact", "", "take", _get("cq"), None),
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


SERIES["pitch_floor"] = (quantity_name("pitch_floor"), "Hz", "take", _get("pitch_floor"), "pitch_floor")
SERIES["overall"] = (quantity_name("overall"), "%", "take", overall_of, None)
# --- REPORT DIMENSIONS (spec 06) ---
OVERALL_KEY = "overall"
REPORT_KEYS = [OVERALL_KEY, "pitch", "delta_f", "pitch_range", "pitch_floor", "h1h2", "cpp"]   # one chart
#                 each; pitch and resonance lead, as on the take card (they carry the placement)
OVERALL_ZONES = {"name": quantity_name("overall"), "unit": "%", "scale": (0, 100), "log": False,
                 "zones": [{"id": "masculine", "label": report.ZONE_LABELS["masculine"], "lo": 0.0,
                            "hi": 100 * report.OVERALL_EDGES[0]},
                           {"id": "overlap", "label": report.ZONE_LABELS["overlap"],
                            "lo": 100 * report.OVERALL_EDGES[0], "hi": 100 * report.OVERALL_EDGES[1]},
                           {"id": "feminine", "label": report.ZONE_LABELS["feminine"],
                            "lo": 100 * report.OVERALL_EDGES[1], "hi": 100.0}]}
OVERALL_EXPLANATION = ("Where pitch and resonance together place each take, from typically masculine "
                       "to typically feminine; the same placement as the take report's overall bar.")
STEADY_WORD = "steady"
MINI_WEEKS = 8                  # weeks the dashboard's sparklines cover
MINI_KEYS = ("pitch", "delta_f", OVERALL_KEY)


def collect_entries(history, folder_takes=()):
    """History log entries plus the takes in the takes folder, one per take."""
    entries, seen = [], set()
    for take in folder_takes:
        entries.append(summarize(take) | {"kind": "take", "time": take["time"], "duration": _duration(take)})
        if take.get("uid"):
            seen.add(take["uid"])
    # A take in both (logged, then saved when a folder was chosen) counts once.
    entries += [e for e in history if not (e.get("kind") == "take" and e.get("uid") in seen)]
    return entries


def _duration(take):
    """Seconds of audio a take's tracks cover, or None."""
    times = (take.get("tracks") or {}).get("times")
    try:
        return float(times[-1]) if times is not None and len(times) else None
    except (TypeError, ValueError, IndexError):
        return None


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


def select_entries(entries, key, mode=None, include_imported=False, days=None, now=None,
                   include_poor=False, task=None):
    """[(time, value, entry)] of one series, oldest first, after the filters (see select)."""
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
            points.append((t, float(v), e))
    points.sort(key=lambda p: p[:2])
    return points


def select(entries, key, mode=None, include_imported=False, days=None, now=None,
           include_poor=False, task=None):
    """(times, values) of one series, oldest first, after the filters. Takes graded poor are left
    out unless include_poor; `task` keeps one kind of take (see entry_task), so unlike isn't mixed."""
    points = select_entries(entries, key, mode, include_imported, days, now, include_poor, task)
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
    within the take-to-take variation: then it just says 'steady' (the tooltip says why)."""
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
        return f"{latest} · {STEADY_WORD} since week of {first} · {takes}"
    if key == "pitch":
        delta = f"{12 * np.log2(medians[-1] / medians[0]):+.1f} semitones"
    else:
        delta = _format(medians[-1] - medians[0], unit, sign=True, scale=medians[0])
    return f"{latest} · {delta} since week of {first} · {takes}"


def _format(value, unit, sign=False, scale=None):
    """Whole numbers for large-valued metrics (Hz), one decimal otherwise."""
    decimals = 0 if abs(value if scale is None else scale) >= 100 else 2 if unit == "" else 1
    space = "" if unit in ("%", "¢", "") else " "
    unit = "semitones" if unit == "st" else unit
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
    for e in entries:       # the same takes the charts draw: speaking, not poor (unless asked) and not imported files
        if e.get("kind", "take") == "take" and not (e.get("tier") == "poor" and not include_poor)                 and not e.get("source") and e.get("mode", "speak") == "speak":
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
            "line": len(medians) >= MIN_LINE_WEEKS, "steady": STEADY_WORD in change_text(key, times, values),
            "legend": CHART_LEGEND,
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
        series.append({"key": key, "name": SERIES[key][0],
                       "values": medians, "latest": latest, "zone": zone,
                       "target": reference(profile, key)[1], "count": len(values)})
    return {"task": task, "weeks": weeks, "series": series}


# --- AM I GETTING BETTER? (phase 11 / 04, audit A-020, A-031) ---

SUMMARY_KEYS = ("pitch", "delta_f")     # the two readings that carry the overall placement (report.MAJOR)
SUMMARY_WINDOWS = (7, 30)               # days: the last week and the last month
MIN_WINDOW_TAKES = 3                    # takes in a window before it says anything about a change
MIN_COMPARE_TAKES = 2                   # takes in the window before it, to compare against
SPLIT_MIN_TAKES = 4                     # with no earlier window, a window with this many compares its halves
SURE_TAKES = 5                          # takes on each side for a fairly sure statement; fewer is "rough"
GOAL_SIGN = {"feminine": 1, "masculine": -1}    # which way is toward the goal: up for feminine, down for masculine
DIRECTION_WORDS = {"pitch": ("higher", "lower"), "delta_f": ("brighter", "darker")}


def _window_split(points, now, days):
    """(earlier points, recent points, what 'earlier' is) for one window."""
    cutoff = (now - timedelta(days=days)).timestamp()
    older_cutoff = (now - timedelta(days=2 * days)).timestamp()
    recent = [p for p in points if p[0] >= cutoff]
    earlier = [p for p in points if older_cutoff <= p[0] < cutoff]
    if len(earlier) >= MIN_COMPARE_TAKES or len(recent) < SPLIT_MIN_TAKES:
        return earlier, recent, f"the {days} days before"
    half = len(recent) // 2
    return recent[:half], recent[half:], "earlier in this period"


def _toward(key, goal, now_value, before_value):
    """"toward" | "away" | None (no goal sense) for a change from before to now, given the voice goal."""
    if goal in GOAL_SIGN:
        sign = 1 if now_value > before_value else -1
        return "toward" if sign == GOAL_SIGN[goal] else "away"
    if goal == "androgynous":
        closer = abs(report.score(key, now_value) or 0) < abs(report.score(key, before_value) or 0)
        return "toward" if closer else "away"
    return None


def _line(key, entries, goal, now, days, task):
    """One window of one reading as {"key", "name", "days", "direction", "n", "n_before", "text", "sure"}
    or None when the window holds no take. direction: "steady" | "toward" | "away" | "changed" | "few"."""
    points = select_entries(entries, key, mode="speak", days=2 * days, now=now, task=task)
    earlier, recent, against = _window_split(points, now, days)
    name, unit = SERIES[key][0], SERIES[key][1]
    if not recent:
        return None
    med = float(np.median([p[1] for p in recent]))
    n = len(recent)
    base = {"key": key, "name": name, "days": days, "n": n, "n_before": len(earlier), "direction": "few",
            "sure": False, "value": med}
    here = _format(med, unit)
    if n < MIN_WINDOW_TAKES or len(earlier) < MIN_COMPARE_TAKES:
        return base | {"text": f"{name}: about {here} over {n} take{'s' if n != 1 else ''}; too few to compare yet."}
    before = float(np.median([p[1] for p in earlier]))
    times = np.array([p[0] for p in earlier + recent])
    values = np.array([p[1] for p in earlier + recent])
    spread = take_spread(key, times, values)
    delta = 12 * np.log2(med / before) if key == "pitch" else med - before
    noise = NOISE_Z * (spread or 0) * np.sqrt(1 / n + 1 / len(earlier))
    sure = n >= SURE_TAKES and len(earlier) >= SURE_TAKES
    window = f"the last {days} days"
    if spread is None or abs(delta) <= noise:
        return base | {"direction": "steady", "sure": sure,
                       "text": f"{name} is steady at about {here} over {window} ({n} takes)."}
    up, down = DIRECTION_WORDS[key]
    word = up if delta > 0 else down
    size = f"{abs(delta):.1f} semitones" if key == "pitch" else _format(abs(delta), unit)
    sense = _toward(key, goal, med, before)
    if sense == "toward":
        text = f"{name} is moving toward your goal: {word} by about {size} over {window} ({n} takes)."
    elif sense == "away":
        text = f"{name} is {word} by about {size} over {window}, a little further from your goal ({n} takes)."
    else:
        text = f"{name} is {word} by about {size} over {window}, compared with {against} ({n} takes)."
    return base | {"direction": sense or "changed", "sure": sure, "text": text}


def summary(entries, goal=None, now=None, task=None, windows=SUMMARY_WINDOWS):
    """The answer to "am I getting better?": {"lines": [one per reading and window], "headline", "takes",
    "windows"}. Each line says the direction (toward your goal, away, steady, or just changed when the
    goal is "exploring" or unset) in words, with the take count; "sure" is False below SURE_TAKES."""
    now = now or datetime.now()
    goal = goal if goal in ("feminine", "masculine", "androgynous") else None
    lines = []
    for days in windows:
        for key in SUMMARY_KEYS:
            line = _line(key, entries, goal, now, days, task)
            if line:
                lines.append(line)
    longest = windows[-1]
    takes = len(select_entries(entries, "pitch", mode="speak", days=longest, now=now, task=task))
    return {"lines": lines, "takes": takes, "windows": list(windows),
            "headline": _headline_text(lines, takes, longest)}


def _headline_text(lines, takes, days):
    window = [ln for ln in lines if ln["days"] == days]
    if takes < MIN_WINDOW_TAKES or not window:
        return (f"Not enough takes in the last {days} days to see a trend yet ({takes} so far): "
                "record a few more and this will tell you how you're doing.")
    moved = [ln for ln in window if ln["direction"] in ("toward", "away", "changed")]
    lead = f"Over the last {days} days ({takes} takes): "
    if not moved:
        few = all(ln["direction"] == "few" for ln in window)
        return lead + ("too few takes to compare yet." if few else "no clear change; your voice is steady.")
    return lead + " ".join(ln["text"] for ln in moved)


def summary_text(entries, goal=None, days=SUMMARY_WINDOWS[-1], now=None, task=None):
    """The answer for the last `days` days (7 or 30) as a sentence or two, e.g. "Over the last 30 days
    (11 takes): Resonance is moving toward your goal: brighter by about 60 Hz over the last 30 days
    (11 takes)." """
    return summary(entries, goal, now, task, windows=(days,))["headline"]


# --- WHY A DAY WAS DIFFERENT ---

SHORT_TAKE_S = 10.0             # a take shorter than this is "short"; own choice
MIN_GROUP_TAKES = 3             # takes in each group before a difference is named
VARIATION_KEYS = ("pitch", "delta_f")


def _time_of_day(entry):
    t = timestamp(entry)
    if t is None:
        return None
    hour = datetime.fromtimestamp(t).hour
    return "mornings" if hour < 12 else "afternoons" if hour < 17 else "evenings"


def _recording(entry):
    return {"good": "good recordings", "fair": "okay recordings"}.get(entry.get("tier"))


def _length(entry):
    d = entry.get("duration")
    return None if d is None else "short takes" if d < SHORT_TAKE_S else "longer takes"


VARIATION_FACTORS = {"time of day": _time_of_day, "recording quality": _recording, "take length": _length}


def variation_notes(entries, now=None, days=None, task=None, include_poor=False):
    """Plain sentences on what goes with the take-to-take differences, where the data allows: for pitch
    and resonance, the time of day, the recording quality and the take length. A factor is named only
    when each side has MIN_GROUP_TAKES takes and the difference is bigger than the usual variation.
    [{"factor", "key", "text", "size"}], biggest first."""
    notes = []
    for key in VARIATION_KEYS:
        points = select_entries(entries, key, mode="speak", days=days, now=now, include_poor=include_poor, task=task)
        if len(points) < 2 * MIN_GROUP_TAKES:
            continue
        spread = take_spread(key, np.array([p[0] for p in points]), np.array([p[1] for p in points]))
        if not spread:
            continue
        for factor, label in VARIATION_FACTORS.items():
            groups = {}
            for _, value, entry in points:
                name = label(entry)
                if name:
                    groups.setdefault(name, []).append(value)
            groups = {name: v for name, v in groups.items() if len(v) >= MIN_GROUP_TAKES}
            if len(groups) < 2:
                continue
            (low_name, low), (high_name, high) = sorted(groups.items(), key=lambda item: float(np.median(item[1])))[::len(groups) - 1]
            m_low, m_high = float(np.median(low)), float(np.median(high))
            diff = 12 * np.log2(m_high / m_low) if key == "pitch" else m_high - m_low
            if abs(diff) <= NOISE_Z * spread * np.sqrt(1 / len(low) + 1 / len(high)):
                continue
            size = f"{abs(diff):.1f} semitones" if key == "pitch" else _format(abs(diff), SERIES[key][1])
            notes.append({"factor": factor, "key": key, "size": abs(diff) / spread,
                          "text": f"Your {SERIES[key][0].lower()} runs {DIRECTION_WORDS[key][0]} by about {size} in your "
                                  f"{high_name} than in your {low_name} ({len(high)} takes against {len(low)})."})
    return sorted(notes, key=lambda n: -n["size"])


# --- PRACTICE DAYS ---

def practice_days(entries, window=PRACTICE_WINDOW_DAYS, now=None):
    """{"days": days of the last `window` (today included) with a take, "window", "text"}. The text is
    "Practised 4 of the last 7 days", or "" with none: it never scolds."""
    now = now or datetime.now()
    first = (now - timedelta(days=window - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    days = set()
    for e in entries:
        if e.get("kind", "take") != "take" or e.get("source"):
            continue
        t = timestamp(e)
        if t is not None and first.timestamp() <= t <= now.timestamp():
            days.add(datetime.fromtimestamp(t).date())
    count = len(days)
    return {"days": count, "window": window,
            "text": f"Practised {count} of the last {window} days" if count else ""}
