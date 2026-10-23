"""What a take's report shows, as data: the recording's quality, advice, singing and exercise lines, the
register row, the metric readings and the time in target. A UI draws a take card from these. No UI toolkit."""
import numpy as np

import coach
import register
from analysis import IN_TUNE_CENTS
from display import METRIC_DEFS, exercise_summary, hiss_note, metric_reading, midi_name, singing_summary, take_date
from take_store import default_mode

TAKE_BAR_KEYS = ["pitch", "delta_f", "h1h2", "cpp", "ring", "pitch_range", "level"]
# Take bars whose reading coach.take_quality can mark unreliable (same names in its "trust").
TRUSTED_BARS = {"pitch", "delta_f", "h1h2", "cpp", "ring", "pitch_range"}
TIER_TEXT = {"good": "Good recording", "fair": "Fair recording", "poor": "Poor recording"}
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
