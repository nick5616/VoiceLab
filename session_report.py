"""The report on one day's practice routine (Today's practice): how long it took, what each step showed, how
it compared with the last session and one thing to try next time. Built from the day's takes and its vowel
check; the History screen draws it. No UI toolkit."""
import re
from datetime import datetime, timedelta

import numpy as np

from session_plan import EXERCISES_PER_SESSION, PHRASES_PER_SESSION, SessionPlan

KEY_PREFIX = "session:"         # a session's key in the History list: "session:<day>:<mode>"
SAME_PITCH_HZ = 3.0             # Hz: a pitch within this of last session is "about the same"
SAME_RESONANCE_HZ = 40.0        # Hz: report.CHANGE_MIN for resonance
LOW_IN_RANGE = 0.5              # share of a phrase in your pitch range below which next time aims there
STEP_WORDS = {"done": "Done", "skipped": "Skipped", "current": "Not done", "todo": "Not done"}
SHOW = [("Everything", None), ("Practice sessions", "sessions"), ("Takes", "takes")]   # History's "Show" filter


def key_of(day, mode):
    return f"{KEY_PREFIX}{day}:{mode}"


def parse_key(key):
    """(day, mode) of a session key, or None when it isn't one."""
    if not str(key or "").startswith(KEY_PREFIX):
        return None
    day, _, mode = key[len(KEY_PREFIX):].partition(":")
    return day, mode or "speak"


def is_phrase(take):
    return take.get("task") == "phrases"


def is_passage(take):
    return str(take.get("task", "")).startswith("passage") and not take.get("exercise")


def is_exercise(take):
    return bool(take.get("exercise"))


def session_takes(takes, day, mode):
    """The takes of a day's routine in `mode` (phrases, passages, exercises), oldest first."""
    mine = [t for t in takes if str(t.get("time", ""))[:10] == day and (t.get("mode") or "speak") == mode
            and (is_phrase(t) or is_passage(t) or is_exercise(t))]
    return sorted(mine, key=lambda t: t.get("time", ""))


def _median(values):
    values = [float(v) for v in values if v is not None and not np.isnan(v)]
    return float(np.median(values)) if values else None


def _change(now, before, same, up, down):
    if now is None or before is None:
        return ""
    diff = now - before
    if abs(diff) < same:
        return "about the same as last session"
    return f"{abs(diff):.0f} Hz {up if diff > 0 else down} than last session"


def _seconds(take):
    times = (take.get("tracks") or {}).get("times")
    return float(times[-1]) if times is not None and len(times) else 0.0


def minutes(takes, vowel_time=None):
    """About how long the routine took: from its first moment (vowel check or take) to the end of its last
    take, in whole minutes (at least 1), or None with nothing to time."""
    starts = [datetime.fromisoformat(t["time"]) for t in takes if t.get("time")]
    if vowel_time:
        starts.append(datetime.fromisoformat(vowel_time))
    if not starts:
        return None
    end = max(datetime.fromisoformat(t["time"]).timestamp() + _seconds(t) for t in takes) if takes else \
        max(s.timestamp() for s in starts)
    return max(1, round((end - min(s.timestamp() for s in starts)) / 60))


def build(session, takes, vowel=None, phrase_shares=None, passage=None, previous_takes=(), target=None):
    """The report: {"key", "title", "when", "minutes", "headline", "steps_done", "steps", "sections"
    [{"key", "title", "state", "headline", "rows" [(name, value, change)], "uids"}], "next_time"}.
    `session` is {"day", "mode", "skipped", "vowels"}; `takes` the day's takes (any); `vowel` the day's vowel
    check log entry; `phrase_shares` {uid: share of the phrase in your pitch range}; `passage` the core's
    passage_result for the day's last reading; `previous_takes` the last session's takes; `target` your pitch
    range (lo, hi) Hz or None."""
    day, mode = session["day"], session.get("mode", "speak")
    mine = session_takes(takes, day, mode)
    plan = SessionPlan(mode, None, mine, {"day": day, "skipped": session.get("skipped", []),
                                          "vowels": bool(session.get("vowels") or vowel)},
                       now=datetime.fromisoformat(f"{day}T23:59:00"))
    steps = plan.steps()[:-1]
    done = sum(1 for s in steps if s["state"] == "done")
    sections = []
    if mode == "sing":              # its warm-up and exercise steps are both exercises: one section for them
        state = "done" if done == len(steps) else "skipped" if all(st["state"] == "skipped" for st in steps)             else "current"
        steps_shown = [{"key": "exercise", "title": "Exercises", "state": state}]
    else:
        steps_shown = steps
    for step in steps_shown:
        if step["key"] == "warmup" and mode == "speak":
            sections.append(_warmup(step, vowel))
        elif step["key"] == "phrases":
            sections.append(_phrases(step, [t for t in mine if is_phrase(t)], phrase_shares or {},
                                     [t for t in previous_takes if is_phrase(t)]))
        elif step["key"] == "read":
            sections.append(_passage(step, [t for t in mine if is_passage(t)], passage))
        else:
            sections.append(_exercises(step, [t for t in mine if is_exercise(t)]))
    whole = done == len(steps)
    headline = ("You did the whole routine." if whole else
                f"You did {done} of {len(steps)} steps." if done else "Nothing from the routine was recorded.")
    took = minutes(mine, vowel.get("time") if vowel else None)
    return {"key": key_of(day, mode), "title": "Daily practice", "when": _when(day), "minutes": took,
            "headline": headline, "steps_done": done, "steps": len(steps), "sections": sections,
            "next_time": _next_time(sections, phrase_shares or {}, target, passage, whole)}


def _when(day):
    d = datetime.fromisoformat(day).date()
    today = datetime.now().date()
    if d == today:
        return "Today"
    if (today - d).days == 1:
        return "Yesterday"
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _section(step, headline, rows=(), uids=()):
    return {"key": step["key"], "title": step["title"], "state": STEP_WORDS[step["state"]], "headline": headline,
            "rows": list(rows), "uids": list(uids)}


def _warmup(step, vowel):
    if not vowel or vowel.get("delta_f") is None:
        words = {"skipped": "Skipped today.", "done": "Vowel check done."}
        return _section(step, words.get(step["state"], "No vowel check today."))
    rows = []
    if vowel.get("formant_ceiling"):
        rows.append(("Analysis tuned to", f"{vowel['formant_ceiling']:.0f} Hz ceiling", ""))
    return _section(step, f"Your resonance across eight vowels was {vowel['delta_f']:.0f} Hz.", rows)


def _phrases(step, phrases, shares, before):
    if not phrases:
        return _section(step, "Skipped today." if step["state"] == "skipped" else "No phrases today.")
    pitch, last_pitch = _median([t.get("pitch") for t in phrases]), _median([t.get("pitch") for t in before])
    res, last_res = _median([t.get("delta_f") for t in phrases]), _median([t.get("delta_f") for t in before])
    n = len(phrases)
    rows = [("Recorded", f"{n} phrase{'s' if n != 1 else ''}", f"{PHRASES_PER_SESSION} make the step" if
             n < PHRASES_PER_SESSION else "")]
    if pitch is not None:
        rows.append(("Pitch", f"{pitch:.0f} Hz", _change(pitch, last_pitch, SAME_PITCH_HZ, "higher", "lower")))
    if res is not None:
        rows.append(("Resonance", f"{res:.0f} Hz", _change(res, last_res, SAME_RESONANCE_HZ, "brighter", "darker")))
    known = [shares[t["uid"]] for t in phrases if shares.get(t.get("uid")) is not None]
    if known:
        rows.append(("In your pitch range", f"{np.mean(known):.0%} of the time", ""))
    headline = f"{n} phrase{'s' if n != 1 else ''}" + (f", pitch around {pitch:.0f} Hz." if pitch else ".")
    return _section(step, headline, rows, [t["uid"] for t in phrases if t.get("uid")])


def _passage(step, readings, passage):
    if not readings:
        return _section(step, "Skipped today." if step["state"] == "skipped" else "No reading today.")
    rows = [(r["name"], _words(r["value"]), _words(r["change"] or "")) for r in (passage or {}).get("rows", [])]
    headline = (passage or {}).get("headline") or "You read the passage."
    return _section(step, headline, rows, [t["uid"] for t in readings if t.get("uid")])


def _words(text):
    """A reading's units in words where the short form is jargon ("5.0 st" -> "5.0 semitones")."""
    return re.sub(r"\bst\b", "semitones", text or "")


def _exercises(step, exercises):
    if not exercises:
        return _section(step, "Skipped today." if step["state"] == "skipped" else "No exercises today.")
    scores = [t["exercise"] for t in exercises if t["exercise"].get("notes_total")]
    tuned, total = sum(s["notes_in_tune"] for s in scores), sum(s["notes_total"] for s in scores)
    rows = [("Exercises sung", str(len(exercises)), f"{EXERCISES_PER_SESSION} make a session"
             if len(exercises) < EXERCISES_PER_SESSION else "")]
    if total:
        rows.append(("Notes in tune", f"{tuned} of {total}", ""))
    headline = f"{tuned} of {total} notes in tune." if total else f"{len(exercises)} exercises sung."
    return _section(step, headline, rows, [t["uid"] for t in exercises if t.get("uid")])


def _next_time(sections, shares, target, passage, whole):
    known = [s for s in shares.values() if s is not None]
    if known and target and np.mean(known) < LOW_IN_RANGE:
        lo, hi = target
        return (f"Aim to keep more of each phrase between {lo:.0f} and {hi:.0f} Hz: today it was "
                f"{np.mean(known):.0%} of the time.")
    if passage and passage.get("tip"):
        return passage["tip"]
    if not whole:
        missing = [s["title"].lower() for s in sections if s["state"] != "Done"]
        return f"Try the whole routine: {', '.join(missing)} {'was' if len(missing) == 1 else 'were'} left today."
    return "Keep the same routine: the same steps each day make your progress easy to see."


def row_view(session, takes):
    """A session's row in the History list: {"title", "subtitle", "chip", "kind", "tooltip"} (cheap: no
    per-take analysis)."""
    day, mode = session["day"], session.get("mode", "speak")
    mine = session_takes(takes, day, mode)
    plan = SessionPlan(mode, None, mine, {"day": day, "skipped": session.get("skipped", []),
                                          "vowels": bool(session.get("vowels"))},
                       now=datetime.fromisoformat(f"{day}T23:59:00"))
    steps = plan.steps()[:-1]
    done = sum(1 for s in steps if s["state"] == "done")
    whole = done == len(steps)
    return {"title": "Daily practice", "subtitle": f"{_when(day)} · {'Sing' if mode == 'sing' else 'Speak'} · "
            f"{len(mine)} take{'s' if len(mine) != 1 else ''}",
            "chip": "All steps" if whole else f"{done} of {len(steps)} steps", "kind": "success" if whole else "info",
            "tooltip": "Your report on the whole routine: each step, how it compared with last time, and what to "
                       "try next."}


def matches(session, query="", mode=None, task=None, tier=None, days=None, now=None):
    """True when a session passes History's search and filters (the take filters: a task or quality filter
    is about takes, so it hides sessions)."""
    if task or tier:
        return False
    day, own = session["day"], session.get("mode", "speak")
    if mode and own != mode:
        return False
    if days and datetime.fromisoformat(day) < (now or datetime.now()) - timedelta(days=days):
        return False
    words = f"daily practice routine session {day} {_when(day)} {own}".lower()
    return all(w in words for w in (query or "").lower().split())


def merge(takes, sessions, show=None, oldest=None):
    """History's list: [("take" | "session", item)] newest first. `show` is a SHOW key; sessions older than
    `oldest` (the oldest take shown, while older ones are still to load) wait for them."""
    items = []
    if show != "sessions":
        items += [((t.get("time", "")[:19], 0), "take", t) for t in takes]
    if show != "takes":                 # to the second; a session goes above a take of the same second
        items += [(((s.get("time") or s["day"])[:19], 1), "session", s) for s in sessions
                  if oldest is None or (s.get("time") or s["day"]) >= oldest]
    items.sort(key=lambda item: item[0], reverse=True)
    return [(kind, item) for _, kind, item in items]
