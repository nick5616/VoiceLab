"""What a take's report shows, as data: the recording's quality, advice, singing and exercise lines, the
register row, the metric readings and the time in target. A UI draws a take card from these. No UI toolkit."""
from datetime import datetime, timedelta

import numpy as np

import coach
import profile_store
import register
import report
from analysis import IN_TUNE_CENTS, hz_to_note
from display import (METRIC_DEFS, READING_TRUST_WORDS, TAKE_TRUST_WORDS, exercise_summary, hiss_note, metric_reading,
                     midi_name, note_ticks, quantity_name, singing_summary, take_date)
from take_store import default_mode

TAKE_BAR_KEYS = ["pitch", "delta_f", "h1h2", "cpp", "ring", "pitch_range", "level"]
# Take bars whose reading coach.take_quality can mark unreliable (same names in its "trust").
TRUSTED_BARS = {"pitch", "delta_f", "h1h2", "cpp", "ring", "pitch_range"}
TIER_TEXT = TAKE_TRUST_WORDS    # "Good recording" / "Okay recording" / "Poor recording" (display, phase 11)
QUALITY_TOOLTIP = ("How far this recording can be trusted. Readings it can't support "
                   "are shown as — instead of a number.")
NOT_SAVED_TOOLTIP = "Choose a takes folder to keep takes between sessions."
REGISTER_ZONES = ("chest", "mix", "head", "fry")
NOTE_CHART_CENTS = 100          # the exercise chart shows tuning errors up to this many cents either way


def quality(take):
    """coach.take_quality for a take and its noise report."""
    return coach.take_quality(take, take.get("noise"))


def saved_state(take):
    """(text, tooltip): the take's date, and where it is stored."""
    return (take_date(take) + ("" if take.get("file") else " · not saved"),
            take.get("file") or NOT_SAVED_TOOLTIP)


def title_hint(take, number):
    """(placeholder, tooltip) of the take's title field."""
    return f"Take #{number}", "Click to name this take" + (" (renames its files)" if take.get("file") else "")


def exercise_notes(notes):
    """Per-note bars of an exercise: [{"label", "cents", "height", "zone": "in_tune"|"off"|None}]."""
    out = []
    for note in notes:
        cents = note["cents"]
        out.append({"label": midi_name(note["midi"]), "cents": cents,
                    "height": float(np.clip(cents, -NOTE_CHART_CENTS, NOTE_CHART_CENTS)) if cents is not None else 0.0,
                    "zone": None if cents is None else "in_tune" if abs(cents) <= IN_TUNE_CENTS else "off"})
    return out


def card(take, profile=None, number=None):
    """The fixed parts of a take's report: {"quality", "tier", "tier_text", "tier_tooltip", "reasons",
    "hiss", "source", "exercise", "exercise_notes", "singing", "egg_warning", "bar_keys"}. Texts are None
    when there is nothing to say."""
    q = quality(take)
    trust = q["trust"]
    why_singing = q["why_untrusted"].get("singing", "")
    exercise = take.get("exercise")
    exercise_text = notes = None
    if exercise:
        exercise_text = exercise_summary(exercise) if trust["singing"] else f"{exercise['name']}: — ({why_singing})"
        notes = exercise_notes(exercise["notes"]) if trust["singing"] else None
    singing = singing_summary(take.get("singing")) if trust["singing"] else None
    if take.get("singing") and not trust["singing"]:
        singing = f"Singing details: — ({why_singing})"
    egg = take.get("egg")
    egg_warning = "⚠ EGG: " + (egg.get("note") or "no usable signal") if egg and not egg["signal_ok"] else None
    has_cq = egg is not None and egg["signal_ok"] and take.get("cq") is not None
    return {"quality": q, "tier": q["tier"], "tier_text": TIER_TEXT[q["tier"]], "tier_tooltip": QUALITY_TOOLTIP,
            "reasons": " · ".join(q["reasons"]) or None, "hiss": hiss_note(take.get("snr"), take.get("snr_full")),
            "source": take.get("source") or None, "exercise": exercise_text, "exercise_notes": notes,
            "singing": singing or None, "egg_warning": egg_warning,
            "bar_keys": TAKE_BAR_KEYS + (["cq"] if has_cq else [])}


def untrusted_title(key, reason):
    """What stands in for a reading the recording can't support: a dash and the reason."""
    return f"{METRIC_DEFS[key][0]}: — ({reason})"


def bars(take, profile, q=None):
    """{key: reading} of the take's metric bars (display.metric_reading); a reading the recording can't
    support has "untrusted": True and the reason in "unavailable"."""
    q = q or quality(take)
    out = {}
    for key in card(take, profile)["bar_keys"]:
        if key in TRUSTED_BARS and not q["trust"][key]:
            reason = q["why_untrusted"][key]
            reading = metric_reading(key, None, profile, note=reason)
            reading.update(untrusted=True, title=untrusted_title(key, reason))
        elif key == "delta_f" and take["delta_f"] is None:
            reading = metric_reading(key, None, profile, note=take["resonance_note"])
        else:
            reading = metric_reading(key, take[key], profile)
        reading.setdefault("untrusted", False)
        out[key] = reading
    return out


def register_zones(tracks):
    """Per-frame register zone of the take's pitch track ("chest", "mix", "head", "fry" or None), or None
    when the take has no register track."""
    scores = tracks.get("register")
    if scores is None:
        return None
    fry = tracks.get("fry")
    zones = []
    for i, score in enumerate(scores):
        if fry is not None and fry[i] > 0.5:
            zones.append("fry")
        elif np.isnan(score):
            zones.append(None)
        else:
            zones.append("chest" if score < register.MIX_BAND[0] else
                         "head" if score > register.MIX_BAND[1] else "mix")
    return zones


def register_row(take, profile, q=None):
    """{"title", "segments": [(share, zone)], "parts": [(zone, text)], "placeholder": text or None,
    "show_track": bool}. A take without a register result (an older take, scored in the background)
    says so, keeping the row."""
    q = q or quality(take)
    reg = take.get("register")
    if not q["trust"]["register"]:
        return {"title": f"Register — — ({q['why_untrusted']['register']})", "segments": [], "parts": [],
                "placeholder": "—", "show_track": False}
    if not reg or "chest_share" not in reg:
        return {"title": "Register — not scored yet", "segments": [], "parts": [], "placeholder": "estimating…",
                "show_track": True}
    shares = [(reg[f"{name}_share"], name) for name in REGISTER_ZONES]
    breaks = len(reg.get("breaks") or [])
    notes = [f"{breaks} break{'s' if breaks != 1 else ''}",
             "calibrated to your voice" if reg.get("calibrated") else "estimate"]
    if take.get("register_label"):
        notes.append(f"you labelled it {take['register_label']}")
    on_target = profile.in_target("register", 100 * reg["head_share"])
    if on_target is not None:
        notes.append("head share ✓ on target" if on_target else "head share off target")
    return {"title": "Register — " + " · ".join(notes), "segments": [(share, name) for share, name in shares],
            "parts": [(name, f"{name} {share:.0%}") for share, name in shares if share > 0 or name != "fry"],
            "placeholder": None, "show_track": True}


def time_in_target(take, profile, q=None):
    """"Time in target: pitch 80% · ..." (share of voiced time inside each target), or "" with no target."""
    q = q or quality(take)
    tracks, trust = take["tracks"], q["trust"]
    parts = []
    for name, key in (("pitch", "pitch"), ("resonance", "delta_f")):
        share = profile.time_in_target(key, tracks[key if key == "delta_f" else "f0"]) if trust[key] else None
        if share is not None:
            parts.append(f"{name} {share:.0%}")
    if trust["pitch"] and trust["delta_f"] and profile.target("pitch") and profile.target("delta_f"):
        both = profile.time_in_both(tracks["f0"], tracks["delta_f"])
        if both is not None:
            parts.append(f"pitch + resonance together {both:.0%}")
    return f"Time in target: {' · '.join(parts)}" if parts else ""


def advice(take, profile, q=None):
    """Up to three next steps from the take and the targets (coach.advice)."""
    return coach.advice(take, profile.targets, profile.baseline, mode=default_mode(take), quality=q or quality(take))


# --- PLAYBACK AND COMPARISON TEXTS ---

def take_name(take, fallback="the other take"):
    return take.get("title") or take_date(take) or fallback


def compare_text(compare, piece=None):
    """The line beside Play: what is being compared, or (while playing) which piece is playing."""
    if compare is None:
        return ""
    if piece == "take":
        return "Playing this take"
    if piece == "gap":
        return "…"
    if piece == "compare":
        return f"Playing {take_name(compare)}"
    return f"Comparing with {take_name(compare)} (dashed). Play runs this take, then that one."


def play_label(playing, shifted=False, comparing=False):
    if playing:
        return " Stop"
    return " Play shifted" if shifted else " Play, then compare" if comparing else " Play"


def delete_question(take, number=None):
    """The question asked before deleting a take, and the name it goes by."""
    name = take.get("title") or f"Take #{number}"
    if take.get("file"):
        return f"Delete “{name}”? Its files ({take['file']} and its .json) are removed from the takes folder.", name
    return f"Delete “{name}”? It isn't saved anywhere, so it can't be recovered.", name


# --- THE REDESIGNED TAKE CARD (phase 10 spec 06) ---
# Additive: what the summary-first card, the Takes screen, the latest-take panel and the recent list
# show, decided here so a future UI (a phone app, say) can reuse it.

TIER_DOT = {"good": "ok", "fair": "warn", "poor": "error"}      # quality -> StatusDot state
MODE_TEXT = {"speak": "Speak", "sing": "Sing"}
NEW_TAKE_JOBS = ("analysis", "record")      # runner job kinds that bring a new take (spec 06 contract)
REANALYSIS_JOB = "reanalysis"
# Fixed pitch axis of the take graph in speak mode: the live graph's (spec 05: 70-400 Hz, log), so a
# take and the live view look alike and a line's height means the same thing in both.
SPEAK_AXIS_HZ = (70, 400)
SPEAK_TICKS_HZ = (80, 100, 125, 145, 180, 220, 300, 400)    # report pitch zone edges plus even steps
SING_MIN_SPAN_ST = 12           # a sung take's axis spans at least an octave (steady notes don't zoom in)
SING_PAD_ST = 2                 # semitones of headroom above and below the sung notes
FORMANT_SMOOTH_S = 0.05         # median smoothing of the formant lines when the take has no *_smooth
#                                 tracks (spec 01 adds them); 50 ms, as report.SMOOTH_S
MIC_WORDS = ("quiet", "closer", "clip", "rumble", "whine", "noise", "noisy", "hiss")   # issues a mic check helps
DAYS = [("Any time", None), ("Last 7 days", 7), ("Last 30 days", 30), ("Last 90 days", 90)]
TIERS = [("Any quality", None), ("Good and fair", "usable"), ("Good", "good"), ("Fair", "fair"), ("Poor", "poor")]
MODES = [("Speak & sing", None), ("Speak", "speak"), ("Sing", "sing")]


def overall_spec():
    """The overall placement's bar: report.OVERALL_EDGES on a 0-1 scale, in the shape of report.ZONES. Its
    zones carry the short words, as every bar's do."""
    lo, hi = report.OVERALL_EDGES
    bounds = [("masculine", 0.0, lo), ("overlap", lo, hi), ("feminine", hi, 1.0)]
    return {"name": OVERALL_NAME, "unit": "", "scale": (0.0, 1.0), "log": False,
            "zones": [{"id": z, "label": report.ZONE_SHORT[z], "lo": a, "hi": b} for z, a, b in bounds]}


def dimension_spec(dim):
    """The bar a report dimension is drawn on: report.ZONES for speak dimensions (zone labels in the short
    words), the plain sing scale (no zones) for sing ones."""
    spec = report.zones(dim["key"])
    if spec:
        return short_zones(spec)
    scale = report.SING_SCALES.get(dim["key"], (0, 1))
    return {"name": dim.get("name"), "unit": dim.get("unit") or "", "scale": scale, "log": False, "zones": []}


CARD_ORDER = ["pitch", "delta_f", "pitch_range", "pitch_floor", "h1h2", "cpp",
               "tuning", "steadiness", "range", "register"]   # pitch and resonance lead: they carry the placement


def value_parts(dim):
    """(number, unit) a dimension card shows at its side: "147", "Hz"; "+7.3", "dB"; "4.0", "semitones"."""
    display = dim.get("display") or "—"
    if dim.get("value") is None or " " not in display:
        return display, ""
    number, unit = display.split(" ", 1)
    return number, unit


def ordered_dimensions(rep):
    """The report's dimensions in CARD_ORDER."""
    rank = {key: i for i, key in enumerate(CARD_ORDER)}
    return sorted(rep["dimensions"], key=lambda d: rank.get(d["key"], len(rank)))


def short_date(take):
    """'Thu 23 Oct 9:21', or '' without a usable time."""
    try:
        when = datetime.fromisoformat(take["time"])
    except (KeyError, TypeError, ValueError):
        return ""
    return f"{when:%a} {when.day} {when:%b} {when.hour}:{when:%M}"


def header(take, number=None):
    """What a card's top line shows: {"title", "placeholder", "tooltip", "date", "task", "mode",
    "subtitle", "tier", "tier_text", "dot", "saved", "source"}."""
    q = quality(take)
    placeholder, tooltip = title_hint(take, number)
    mode = default_mode(take)
    task = profile_store.task_of(take | {"mode": mode})
    date = short_date(take)
    parts = [p for p in (date, task, MODE_TEXT.get(mode, mode)) if p]
    if not take.get("file"):
        parts.append("not saved")
    return {"title": take.get("title") or "", "placeholder": placeholder, "tooltip": tooltip, "date": date,
            "task": task, "mode": mode, "subtitle": " · ".join(parts), "tier": q["tier"],
            "tier_text": TIER_TEXT[q["tier"]], "dot": TIER_DOT[q["tier"]], "saved": bool(take.get("file")),
            "source": take.get("source") or None}


def banner(rep):
    """The report's issues collapsed into one line: {"text", "kind", "action", "more", "all", "affects"},
    or None. The issue that leaves the most readings unjudged leads; the rest are counted and listed in
    "more". "warning" when readings are missing, "info" for advice that touches none; "action" is
    "Mic check" when a better mic setup would help."""
    issues = list((rep or {}).get("issues") or [])
    if not issues:
        return None
    issues.sort(key=lambda i: -len(i.get("affects") or []))
    lead, rest = issues[0], issues[1:]
    text = lead["text"]
    headline = (rep.get("headline") or "").rstrip(".")
    if headline and text.startswith(headline) and text[len(headline):].strip(" ."):
        text = text[len(headline):].strip(" .") + "."       # the headline already says the cause
    if rest:
        text += f" (+{len(rest)} more)"
    affects = sorted({k for i in issues for k in (i.get("affects") or [])})
    mic = any(word in i["text"].lower() for i in issues for word in MIC_WORDS)
    return {"text": text, "kind": "warning" if affects else "info", "action": "Mic check" if mic else None,
            "more": [i["text"] for i in rest], "all": [i["text"] for i in issues], "affects": affects}


def _number(value, fmt, scale=1.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(value):
        return None
    return fmt.format(value * scale)


def all_numbers(take):
    """Every summary value of a take, jargon and all: [(label, value text, note)]. Missing values are
    left out; why resonance was withheld is a row of its own."""
    rows = []

    def add(label, key, fmt, note=""):
        text = _number(take.get(key), fmt)
        if text is not None:
            rows.append((label, text, note))

    add("Median pitch (f0)", "pitch", "{:.1f} Hz", f"about {take['note']}" if take.get("note") else "")
    add("Pitch floor (10th percentile f0)", "pitch_floor", "{:.1f} Hz")
    add("Intonation range (10th–90th percentile)", "pitch_range", "{:.1f} st")
    add("Pitch contour", "contour", "{:.2f} st")
    add("Formant dispersion ΔF", "delta_f", "{:.0f} Hz", "resonance: higher is brighter")
    add("ΔF spread (frame IQR)", "delta_f_iqr", "{:.0f} Hz", "how much resonance moved within the take")
    vtl = take.get("vtl_apparent_cm") or take.get("vtl")
    if _number(take.get("delta_f"), "{}") is not None and _number(vtl, "{}") is not None:
        rows.append(("Apparent vocal tract length", f"{float(vtl):.1f} cm",
                     "from ΔF (c / 2ΔF): a cue, not a measure of anatomy"))
    if take.get("resonance_note"):
        rows.append(("Why ΔF was withheld", "—", take["resonance_note"]))
    add("F3", "f3", "{:.0f} Hz")
    add("F4", "f4", "{:.0f} Hz")
    add("F1 missing in voiced frames", "f1_missing_fraction", "{:.0%}")
    add("Frames used for formants", "vowel_frames", "{:.0f}")
    add("Formant ceiling", "formant_ceiling", "{:.0f} Hz", "the analysis' upper search limit")
    add("H1*–H2* (corrected)", "h1h2", "{:+.1f} dB", "vocal weight proxy; mic-sensitive")
    add("CPP (VoiceLab)", "cpp", "{:.1f} dB", "clarity; noise lowers it")
    add("CPPS", "cpps", "{:.1f} dB", "smoothed cepstral peak prominence")
    add("Ring (2–4 kHz)", "ring", "{:+.1f} dB")
    add("Contact quotient (EGG)", "cq", "{:.2f}")
    add("Signal-to-noise (voice band)", "snr", "{:.1f} dB")
    add("Signal-to-noise (full band)", "snr_full", "{:.1f} dB")
    add("Level", "level", "{:.1f} dBFS", "uncalibrated")
    add("Clipped moments", "clip_events", "{:.0f}")
    add("Voiced fraction", "voiced_fraction", "{:.0%}", "share of the take with a pitch")
    add("Ghost fraction", "ghost_fraction", "{:.0%}", "pitch readings dropped as unreliable")
    reg = take.get("register")
    if isinstance(reg, dict):
        for name in REGISTER_ZONES:
            text = _number(reg.get(f"{name}_share"), "{:.0%}")
            if text is not None:
                rows.append((f"Register: {name} share", text, "" if reg.get("calibrated") else "estimate"))
        if reg.get("breaks") is not None:
            rows.append(("Register breaks", f"{len(reg.get('breaks') or [])}", ""))
    add("Analysis version", "analysis_version", "{:.0f}")
    add("Sample rate", "sample_rate", "{:.0f} Hz")
    if take.get("file"):
        rows.append(("File", take["file"], ""))
    if take.get("source"):
        rows.append(("Source", take["source"], ""))
    return rows


def register_summary(take, profile, q=None):
    """The card's register section: {"headline", "shares": [(zone, share)], "text", "placeholder", "label",
    "confidence", "reason"}. Spec 01's register grade decides: "none" shows the reason and no shares
    (an uncalibrated model can't split speech into chest and head), a speech label ("mostly chest") is
    the headline when there is one."""
    row = register_row(take, profile, q)
    reg = take.get("register") if isinstance(take.get("register"), dict) else {}
    q = q or quality(take)
    if row["placeholder"] == "—":           # the recording can't support it (coach.take_quality)
        level, reason = "none", q["why_untrusted"].get("register")
    else:
        level = reg.get("confidence") or ("low" if reg and not reg.get("calibrated") else "medium" if reg else "none")
        reason = reg.get("confidence_reason")
    text = row["title"].split(" — ", 1)[-1]
    label = reg.get("speech_label")
    if row["placeholder"] == "estimating…":
        headline = "Register not estimated yet"
    elif level == "none":
        headline = "Register can't be judged from this take"
        text = reason or text
    elif label:
        headline = f"Register: {label}"
    else:
        head = reg.get("head_share") or 0
        headline = ("Mostly head voice" if head >= 0.6 else "Mostly chest voice" if reg.get("chest_share", 0) >= 0.6
                    else "A mix of chest and head voice")
    shares = [] if level == "none" else [(zone, share) for share, zone in row["segments"]]
    return {"headline": headline, "shares": shares, "text": text, "placeholder": row["placeholder"],
            "label": take.get("register_label"), "confidence": level, "reason": reason}


def new_take_pending(jobs):
    """True while a job that brings a new take (a recording or an import being analysed) runs."""
    return any(job.get("kind") in NEW_TAKE_JOBS for job in jobs or [])


def reanalysing_uid(core, jobs):
    """The uid of the take being re-analysed now (the core works newest first), or None."""
    if not any(job.get("kind") == REANALYSIS_JOB for job in jobs or []):
        return None
    stale = core.outdated_takes()
    return stale[0].get("uid") if stale else None


def search_text(take):
    """The words a take is found by: title, task, mode, dates, source and file."""
    parts = [take.get("title"), profile_store.task_of(take), MODE_TEXT.get(default_mode(take)), short_date(take),
             take_date(take), take.get("source"), take.get("file")]
    return " ".join(p for p in parts if p).lower()


def matches(take, query="", mode=None, task=None, tier=None, days=None, now=None):
    """True when a take passes the Takes screen's search and filters. `tier` is a quality, or "usable"
    for good and fair; `days` keeps takes from the last so many days."""
    if mode and default_mode(take) != mode:
        return False
    if task and profile_store.task_of(take) != task:
        return False
    if tier:
        own = quality(take)["tier"]
        if (tier == "usable" and own == "poor") or (tier != "usable" and own != tier):
            return False
    if days:
        try:
            when = datetime.fromisoformat(take["time"])
        except (KeyError, TypeError, ValueError):
            return False
        if when < (now or datetime.now()) - timedelta(days=days):
            return False
    text = search_text(take)
    return all(word in text for word in (query or "").lower().split())


def task_options(takes):
    """[(task, count)] of the tasks among the takes, most common first."""
    counts = {}
    for take in takes:
        task = profile_store.task_of(take)
        counts[task] = counts.get(task, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _rolling_median(values, n):
    n = max(1, int(n)) | 1
    padded = np.pad(values, (n // 2, n // 2), constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, n)
    out = np.full(len(values), np.nan)
    ok = (~np.isnan(windows)).any(axis=1)
    if ok.any():
        out[ok] = np.nanmedian(windows[ok], axis=1)
    return out


def formant_lines(tracks, raw=False):
    """{"f1", "f2", "f3"}: the formant tracks the graph draws. By default smoothed (spec 01's *_smooth
    tracks when present, else a FORMANT_SMOOTH_S median) and only where the voice is voiced, so unvoiced
    gaps draw no spaghetti; `raw` gives the tracks as measured."""
    times = np.asarray(tracks.get("times", []), dtype=float)
    f0 = tracks.get("f0")
    voiced = ~np.isnan(np.asarray(f0, dtype=float)) if f0 is not None else np.ones(len(times), bool)
    step = float(np.median(np.diff(times))) if len(times) > 1 else 0.01
    out = {}
    for name in ("f1", "f2", "f3"):
        values = tracks.get(name)
        if values is None or len(values) != len(times):
            continue
        values = np.asarray(values, dtype=float)
        if raw:
            out[name] = values
            continue
        smooth = tracks.get(name + "_smooth")
        if smooth is not None and len(smooth) == len(times):
            line = np.asarray(smooth, dtype=float).copy()
        else:
            line = _rolling_median(np.where(voiced, values, np.nan), round(FORMANT_SMOOTH_S / step))
        line[~voiced] = np.nan
        out[name] = line
    return out


def has_formants(tracks):
    """True when the take has formant readings worth a panel (sung takes often have none)."""
    return any(np.isfinite(v).any() for v in formant_lines(tracks).values())


# Words without descenders: spec 04's ZoneBar end-label row clips them.
SING_END_LABELS = {"tuning": ("In tune", "50 cents off"), "steadiness": ("Still", "Wanders"),
                   "range": ("", "3 octaves"), "register": ("Chest", "Head")}


def end_labels(dim):
    """(low, high) words under the ends of a bar without zones (the sing dimensions), or None."""
    return SING_END_LABELS.get(dim["key"]) if not report.zones(dim["key"]) else None


def pitch_axis(take):
    """The take graph's pitch axis: {"mode", "lo", "hi" (Hz), "zones" (report.live_zones or None),
    "ticks": [(Hz, label)]}. Speak: fixed SPEAK_AXIS_HZ with Hz-and-note ticks; sing: note names around
    the sung notes, at least SING_MIN_SPAN_ST wide."""
    mode = default_mode(take)
    if mode == "speak":
        lo, hi = SPEAK_AXIS_HZ
        zones = report.live_zones("speak")
        if zones:
            zones["zones"][0]["lo"] = float(lo)       # the masculine band reaches the axis floor
        return {"mode": mode, "lo": float(lo), "hi": float(hi), "zones": zones,
                "ticks": [(float(hz), f"{hz}  {hz_to_note(hz)[0]}") for hz in SPEAK_TICKS_HZ]}
    f0 = np.asarray((take.get("tracks") or {}).get("f0", []), dtype=float)
    f0 = f0[~np.isnan(f0)]
    lo_st, hi_st = (12 * np.log2(np.percentile(f0, [1, 99]) / 440.0)) if len(f0) else (-12.0, 0.0)
    lo_st, hi_st = lo_st - SING_PAD_ST, hi_st + SING_PAD_ST
    if hi_st - lo_st < SING_MIN_SPAN_ST:
        mid = (hi_st + lo_st) / 2
        lo_st, hi_st = mid - SING_MIN_SPAN_ST / 2, mid + SING_MIN_SPAN_ST / 2
    lo, hi = 440.0 * 2 ** (lo_st / 12), 440.0 * 2 ** (hi_st / 12)
    return {"mode": mode, "lo": float(lo), "hi": float(hi), "zones": None,
            "ticks": [(float(2 ** y), label.split()[0]) for y, label in note_ticks(lo, hi)[0]]}


def moment_clip(samples, start, end, rate):
    """(samples up to `end` s, start): a moment plays from `start` and stops at `end`."""
    stop = int(round(min(end, len(samples) / rate) * rate))
    return samples[:max(stop, int(start * rate) + 1)], float(start)


def file_path(core, uid):
    """Where a saved take's audio file is, or None."""
    data = core.take(uid)
    if data is None or not data.get("file") or not core.store:
        return None
    return str(core.store.path(data))


# --- PHASE 11 / 04: ONE TRUST MODEL, THE FIRST SCREEN, COMPARE, UPDATES (audit A-016..A-031) ---
# All decided here, drawn by ui/takes: a take says once how far it can be trusted, which readings are
# affected and why, and what to try; the first screen is the headline, the player and two tips.

OVERALL_NAME = quantity_name("overall")      # "Overall placement"
UPDATING_SHORT = "Updating…"
VISIBLE_TIPS = 2                    # tips shown before "More tips" (A-019)
# Readings that are rough guides by nature (weight, clarity; resonance from running speech): their reason is the
# cap's own, not a problem with this take, so a take isn't faulted for it.
DESIGN_REASONS = {key: cap[1] for key, cap in (report.FALLBACK_CAP | report.CONFIDENCE_CAP).items()}
ROUGH_GUIDE = "Rough guide"         # the chip on a reading that is rough by design (weight, clarity)
LAST_TAKE_TITLE = "Your last take"  # the dashboard panel's title (display.TIME_WORDS["last_take"], capitalised)
ZONE_BAR_WORDS = report.ZONE_SHORT         # bars label their zones with the short words everywhere, so one wording per screen


def short_zones(spec):
    """A copy of a report zones spec whose zone labels are the short words (Masculine / Overlap /
    Feminine): a bar names its zones the same way wherever it is, and prose uses the long ones."""
    if not spec or not spec.get("zones"):
        return spec
    return spec | {"zones": [z | {"label": ZONE_BAR_WORDS.get(z["id"], z.get("label"))} for z in spec["zones"]]}


def _plain_join(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def reading_trust(dim):
    """How to say how far one reading can be trusted: {"word", "kind", "reason", "show"}. A reliable
    reading says nothing ("show" False); a rough one says "Rough" (or "Rough guide" when it always is);
    one that can't be judged says "Can't judge", with the reason for the tooltip."""
    level = dim.get("confidence") or "none"
    reason = dim.get("reason")
    if level == "none" or dim.get("value") is None:
        return {"word": READING_TRUST_WORDS["none"], "kind": "error", "reason": reason, "show": True}
    if level == "high":
        return {"word": READING_TRUST_WORDS["high"], "kind": "success", "reason": reason, "show": False}
    return {"word": ROUGH_GUIDE if by_design(dim) else READING_TRUST_WORDS[level], "kind": "warning",
            "reason": reason, "show": True}


def by_design(dim):
    """True when a reading is a rough guide by its nature (its reason is the cap's own), not because of this take."""
    return (dim.get("confidence") in ("low", "medium") and dim["key"] in DESIGN_REASONS
            and dim.get("reason") == DESIGN_REASONS[dim["key"]])


def trust_line(rep, take, q=None):
    """The one statement of how far a take can be trusted (A-016): {"text", "tier", "verdict", "cant",
    "rough", "fix", "action", "clean", "short"}. "short" is the text without what to try (a compact panel). The text holds the verdict, the readings that are affected with
    the cause, and what to try, in one or two sentences, so the badge, the chips and the banner can't
    disagree. "action" is "Mic check" when the cause is the mic or the room."""
    q = q or quality(take)
    tier = q["tier"]
    verdict = TIER_TEXT[tier]
    dims = (rep or {}).get("dimensions") or []
    names = {d["key"]: d["name"].lower() for d in dims}
    base = {"tier": tier, "verdict": verdict, "cant": [], "rough": [], "fix": None, "action": None, "clean": False}
    if tier == "poor":
        reason = q["reasons"][0] if q["reasons"] else "the recording is too noisy or quiet"
        fix = report.fix_for(reason)
        return base | {"text": f"{verdict}: {report.cause_of(reason)} leaves nothing to measure. {fix}",
                       "short": f"{verdict}: {report.cause_of(reason)} leaves nothing to measure.",
                       "cant": [d["key"] for d in dims], "fix": fix, "action": _mic_action(reason, fix)}
    cant, rough = {}, {}
    for d in dims:
        if d.get("confidence") == "none":
            cant.setdefault(d.get("reason") or "", []).append(d["key"])
        elif d.get("confidence") == "low" and not by_design(d):
            rough.setdefault(d.get("reason") or "", []).append(d["key"])
    parts, lead_reason = [], None
    for reason, keys in cant.items():
        parts.append(f"{_plain_join(names[k] for k in keys)} can't be judged because of {report.cause_of(reason)}")
        lead_reason = lead_reason or reason
    for reason, keys in rough.items():
        verb = "is a rough guide" if len(keys) == 1 else "are rough guides"
        parts.append(f"{_plain_join(names[k] for k in keys)} {verb} because of {report.cause_of(reason)}")
        lead_reason = lead_reason or reason
    if parts:
        text = f"{verdict}, but {parts[0]}" + (f"; {parts[1]}" if len(parts) > 1 else "") + "."
    elif q["reasons"]:
        lead_reason = q["reasons"][0]
        text = f"{verdict}, with {report.cause_of(lead_reason)}."
    else:
        guides = [names[d["key"]] for d in dims if by_design(d)]
        reliable = [names[d["key"]] for d in dims if not reading_trust(d)["show"]]
        if not guides:
            return base | {"text": f"{verdict}: every reading is reliable.", "clean": True}
        text = f"{verdict}: " + (f"{_plain_join(reliable)} {'is' if len(reliable) == 1 else 'are'} reliable; " if reliable else "")             + f"{_plain_join(guides)} {'is a rough guide' if len(guides) == 1 else 'are rough guides'}."
        return base | {"text": text, "clean": True}
    fix = report.fix_for(lead_reason)
    return base | {"text": f"{text} {fix}", "short": text, "cant": [k for ks in cant.values() for k in ks],
                   "rough": [k for ks in rough.values() for k in ks], "fix": fix,
                   "action": _mic_action(lead_reason, fix)}


def _mic_action(reason, fix):
    low = f"{reason} {fix}".lower()
    return "Mic check" if any(word in low for word in MIC_WORDS) or "mic check" in low else None


def split_tips(tips, visible=VISIBLE_TIPS):
    """(shown, more): the first `visible` tips, and the rest behind "More tips"."""
    return list(tips[:visible]), list(tips[visible:])


def tip_sentence(tip):
    """What to try, in one sentence, then the evidence in a second (muted) one: (text, why or "")."""
    return tip["text"], tip.get("why") or ""


def change_text(dim):
    """The words naming a reading's change against the last take or the baseline ("Higher than your last
    take, by 1.3 semitones"), or None. The direction is a word, the size has no sign."""
    change = (dim or {}).get("change")
    return change["text"] if change else None


def last_tip_view(rep):
    """The closing-the-loop row: {"text", "verdict", "tip"} from the report's last_tip, or None."""
    tip = (rep or {}).get("last_tip")
    return None if not tip else {"text": tip["text"], "verdict": tip["verdict"], "tip": tip["tip"]}


# --- WHEN: "Your last take · 3 min ago" ---

def relative_time(take, now=None):
    """"just now", "3 min ago", "2 hours ago", "yesterday", "3 days ago", "2 weeks ago"; "" without a time."""
    try:
        when = datetime.fromisoformat(take["time"])
    except (KeyError, TypeError, ValueError):
        return ""
    now = now or datetime.now()
    seconds = (now - when).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400 and when.date() == now.date():
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = (now.date() - when.date()).days
    if days <= 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


def last_take_title(take, now=None):
    """"Your last take · 3 min ago", or with the day once it is more than a few hours old:
    "Your last take · Fri 23 Oct 11:41 (3 days ago)". The panel can't be mistaken for the live readings."""
    ago = relative_time(take, now)
    try:
        when = datetime.fromisoformat(take["time"])
        recent = ((now or datetime.now()) - when).total_seconds() < 6 * 3600
    except (KeyError, TypeError, ValueError):
        return LAST_TAKE_TITLE
    if recent or not short_date(take):
        return f"{LAST_TAKE_TITLE} · {ago}" if ago else LAST_TAKE_TITLE
    return f"{LAST_TAKE_TITLE} · {short_date(take)} ({ago})"


# --- IN RANGE ---

def in_range(take, profile, q=None):
    """The share of the take spent inside the user's targets, as a stat: {"text", "detail", "value"
    (0-1 or None), "parts"}. "In range 2% of this take" with pitch and resonance apart in the detail;
    {"text": ""} when there is no target or nothing can be judged."""
    q = q or quality(take)
    tracks, trust = take["tracks"], q["trust"]
    parts = []
    for name, key in (("pitch", "pitch"), ("resonance", "delta_f")):
        share = profile.time_in_target(key, tracks[key if key == "delta_f" else "f0"]) if trust[key] else None
        if share is not None:
            parts.append((name, share))
    both = None
    if trust["pitch"] and trust["delta_f"] and profile.target("pitch") and profile.target("delta_f"):
        both = profile.time_in_both(tracks["f0"], tracks["delta_f"])
    if not parts:
        return {"text": "", "detail": "", "value": None, "parts": []}
    detail = ", ".join(f"{name} {share:.0%}" for name, share in parts)
    if both is not None:
        return {"text": f"In range {both:.0%} of this take", "detail": f"pitch and resonance together; {detail} on their own",
                "value": both, "parts": parts}
    return {"text": "In range: " + detail, "detail": "share of the take spent inside your targets",
            "value": None, "parts": parts}


# --- COMPARE (A-023) ---

COMPARE_KEYS = ("pitch", "delta_f", "pitch_range", "pitch_floor", "h1h2", "cpp")


def compare_rows(rep, other_rep):
    """One row per reading for two reports: [{"key", "name", "this", "other", "change", "same",
    "comparable"}]. `change` says it in words from this take's side ("Higher by 1.3 semitones"), or why
    the two can't be compared. The reports are report.build_report results."""
    mine = {d["key"]: d for d in rep["dimensions"]}
    theirs = {d["key"]: d for d in other_rep["dimensions"]}
    rows = []
    for key in COMPARE_KEYS:
        a, b = mine.get(key), theirs.get(key)
        if not a or not b:
            continue
        ok = a.get("value") is not None and b.get("value") is not None
        if ok:
            change = report.compare_change(key, a["value"], b["value"])
        else:
            missing = a if a.get("value") is None else b
            change = {"text": "Can't compare: " + (missing.get("reason") or "not measured"), "same": False}
        rows.append({"key": key, "name": a["name"], "this": a["display"], "other": b["display"],
                     "change": change["text"], "same": change["same"], "comparable": ok})
    return rows


def compare_summary(rows, other_name):
    """One sentence on the main changes: "Compared with Take #10: pitch is higher by 1.3 semitones;
    resonance is about the same." """
    main = [r for r in rows if r["key"] in report.MAJOR]
    if not main:
        return f"Compared with {other_name}."
    bits = [f"{r['name'].lower()} {'is about the same' if r['same'] else 'is ' + r['change'][0].lower() + r['change'][1:]}"
            if r["comparable"] else f"{r['name'].lower()} can't be compared" for r in main]
    return f"Compared with {other_name}: " + "; ".join(bits) + "."


def compare_choices(core_options, previous):
    """The compare picker's entries: the previous take first (the default), then the baseline take and
    the other options, without repeats. `core_options` is core.compare_options(uid): [(label, take)]."""
    seen, out = set(), []
    if previous is not None:
        out.append(("Previous take", previous))
        seen.add(id(previous))
    for label, take in core_options:
        if id(take) not in seen:
            out.append((label, take))
            seen.add(id(take))
    return out


# --- UPDATED BY A NEWER ANALYSIS (A-017) ---

UPDATING_TEXT = "Updating this take with the improved analysis… The tips below are from the earlier analysis and may change."


def report_digest(rep, take):
    """What a re-analysis can change, kept to compare after it: tier, overall zone and confidence, tips."""
    overall = (rep or {}).get("overall") or {}
    return {"tier": quality(take)["tier"], "zone": overall.get("zone"), "confidence": overall.get("confidence"),
            "tips": [t["text"] for t in (rep or {}).get("tips") or []]}


def update_note(before, after):
    """The one-line "Updated" note once a re-analysis finished: what changed, or that nothing important
    did. `before` and `after` are report_digest results."""
    if before is None or after is None:
        return "Updated with the improved analysis."
    changes = []
    if before["tier"] != after["tier"]:
        changes.append(f"the recording now reads as {TIER_TEXT[after['tier']].lower()}")
    if before["confidence"] != after["confidence"] and after["confidence"] in READING_TRUST_WORDS:
        changes.append(f"the overall reading is now {READING_TRUST_WORDS[after['confidence']].lower()}")
    if before["zone"] != after["zone"] and after["zone"]:
        changes.append(f"the overall placement is now {report.ZONE_LABELS[after['zone']]}")
    if before["tips"] != after["tips"]:
        changes.append("the tips changed")
    if not changes:
        return "Updated with the improved analysis. Nothing important changed."
    return "Updated with the improved analysis: " + _plain_join(changes) + "."


# --- ROWS, FILTERS AND THE SUMMARY TEXT (A-027, A-024) ---

def row_chip(rep):
    """(chip text, chip kind, value text) a list row shows from a take's report: the overall zone (or
    Sing) and the pitch (or the tuning)."""
    if rep["mode"] == "sing":
        tuning = next((d for d in rep["dimensions"] if d["key"] == "tuning"), None)
        return MODE_TEXT["sing"], "neutral", tuning["display"] if tuning and tuning.get("value") is not None else ""
    zone = rep["overall"].get("zone")
    pitch = next((d for d in rep["dimensions"] if d["key"] == "pitch"), None)
    value = pitch["display"] if pitch and pitch.get("value") is not None else "—"
    return (ZONE_BAR_WORDS[zone], zone, value) if zone else ("No placement", "neutral", value)


DEFAULT_TASK = "free speech"     # the task a list row leaves unsaid: nearly every take is one


def row_view(take, rep, number=None):
    """A take's list row: {"title", "subtitle", "chip", "kind", "value", "tooltip", "dot", "tier_text"}.
    One short line each; the chip is the overall zone (or Sing); the value its pitch."""
    head = header(take, number)
    chip, kind, value = row_chip(rep)
    return {"title": head["title"] or head["placeholder"], "number": number,
            "subtitle": " · ".join(p for p in (head["date"], "" if head["task"] == DEFAULT_TASK else head["task"]) if p),
            "chip": chip, "kind": kind,
            "value": value, "tooltip": rep["headline"], "dot": head["dot"], "tier_text": head["tier_text"]}


FILTER_NAMES = {"mode": "mode", "task": "task", "tier": "quality", "days": "date"}


def active_filters(filters):
    """The names of the filters set to something (not the search box): ["mode", "task"]."""
    return [FILTER_NAMES[k] for k in FILTER_NAMES if (filters or {}).get(k)]


def filter_label(filters):
    """The Filter button's text: "Filter" or "Filter · 2"."""
    n = len(active_filters(filters))
    return f"Filter · {n}" if n else "Filter"


def summary_text(take, rep, number=None):
    """A take's report as plain text for the clipboard: title and date, the trust line, the headline, the
    tips and each reading."""
    head = header(take, number)
    lines = [f"{head['title'] or head['placeholder']} · {head['subtitle']}", "",
             trust_line(rep, take)["text"], ""]
    lines.append(report.render_text(rep))
    return "\n".join(lines)


def moment_at(moments, seconds):
    """The (start, end) moment that holds `seconds`, or None: a click on a marked moment plays it."""
    return next(((a, b) for a, b in moments if a <= seconds <= b), None)


def export_name(take, number=None):
    """A file name to suggest when exporting a report: "Take 12 2026-10-23 1141" (no extension)."""
    try:
        stamp = datetime.fromisoformat(take["time"]).strftime("%Y-%m-%d %H%M")
    except (KeyError, TypeError, ValueError):
        stamp = ""
    name = f"Take {number}" if number else (take.get("title") or "Take")
    return f"{name} {stamp}".strip()


NEW_TEXT = "New"                  # the chip on a take just made, until it has been looked at
