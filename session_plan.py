"""Today's practice: a short routine (warm-up, phrases, the standard passage, a result) built from your mode and
goal, ticked off by the takes you make, skippable and resumable. SessionPlan is the plan as data; the Practice
screen and the dashboard draw it. No UI toolkit."""
from datetime import datetime

import numpy as np

# Steps: key -> (title, the practice screen it opens). The plan picks which apply to the mode.
PHRASES_PER_SESSION = 3         # phrase takes that complete the phrases step
EXERCISES_PER_SESSION = 2       # exercise takes that complete a singing session
STANDARD_PASSAGE = 0            # index of the passage the session suggests (the one whose takes compare best)
SESSION_MINUTES = 10            # min: about how long the routine takes
PITCH_SAME_HZ = 3.0             # Hz: pitch within this of the last session is "about the same"
GOAL_HINTS = {
    "feminine": "Aim for a lighter, brighter voice, easy rather than forced.",
    "masculine": "Let your voice settle low and warm, easy rather than pushed.",
    "androgynous": "Stay near the middle of the overlap range, steady and relaxed.",
    "exploring": "No target today: notice what feels easy and what takes effort.",
}
STATES = ("done", "skipped", "current", "todo")     # a step's state, in the order it is decided


def _day(value):
    """The date part ('2026-10-27') of an ISO time or a datetime."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def _is_phrase(take):
    return take.get("task") == "phrases"


def _is_passage(take):
    return str(take.get("task", "")).startswith("passage")


def _is_exercise(take):
    return bool(take.get("exercise"))


def _median(values):
    values = [v for v in values if v is not None and not np.isnan(v)]
    return float(np.median(values)) if values else None


class SessionPlan:
    """The routine for `mode` ("speak" | "sing") and `goal`, as of `now`. `takes` are every take (any order);
    `state` is what was remembered today: {"day", "skipped" [keys], "vowels" (a vowel check was done)}."""

    def __init__(self, mode="speak", goal=None, takes=(), state=None, now=None):
        self.mode = "sing" if mode == "sing" else "speak"
        self.goal = goal
        self.today = _day(now or datetime.now())
        state = dict(state or {})
        if state.get("day") != self.today:
            state = {"day": self.today, "skipped": [], "vowels": False}
        self.state = {"day": self.today, "skipped": list(state.get("skipped", [])),
                      "vowels": bool(state.get("vowels"))}
        self.takes = sorted(takes, key=lambda t: t.get("time", ""))
        self.today_takes = [t for t in self.takes if _day(t.get("time", "")) == self.today]
        self.earlier = [t for t in self.takes if _day(t.get("time", "")) < self.today]

    # --- the steps ---

    def _count(self, test):
        return sum(1 for t in self.today_takes if test(t) and (t.get("mode") or self.mode) == self.mode)

    def _specs(self):
        """[(key, title, detail, practice, done_count, needed)] for the mode."""
        hint = GOAL_HINTS.get(self.goal, GOAL_HINTS["exploring"])
        if self.mode == "sing":
            n = self._count(_is_exercise)
            return [("warmup", "Warm up", "Sing an easy five-note scale, slowly.", "exercise",
                     min(n, 1), 1),
                    ("exercise", "One exercise", "Pick an arpeggio or a long tone and sing it through.", "exercise",
                     max(0, min(n - 1, EXERCISES_PER_SESSION - 1)), EXERCISES_PER_SESSION - 1)]
        return [("warmup", "Warm up", "Eight short words: a gentle start that also tunes the analysis.", "vowels",
                 1 if self.state["vowels"] else 0, 1),
                ("phrases", f"{PHRASES_PER_SESSION} phrases", f"Everyday things to say, one at a time. {hint}",
                 "phrases", min(self._count(_is_phrase), PHRASES_PER_SESSION), PHRASES_PER_SESSION),
                ("read", "Read the passage", "Read it aloud once, at an easy pace.", "read",
                 min(self._count(_is_passage), 1), 1)]

    def steps(self):
        """[{"key", "title", "detail", "practice" (the practice to open), "state" (done | skipped | current |
        todo), "done", "needed", "progress" ('2 of 3' or '')}], then the result step."""
        out, current_found = [], False
        for key, title, detail, practice, done, needed in self._specs():
            if done >= needed:
                state = "done"
            elif key in self.state["skipped"]:
                state = "skipped"
            elif not current_found:
                state, current_found = "current", True
            else:
                state = "todo"
            out.append({"key": key, "title": title, "detail": detail, "practice": practice, "state": state,
                        "done": done, "needed": needed,
                        "progress": f"{done} of {needed}" if needed > 1 and state != "skipped" else ""})
        finished = not current_found
        out.append({"key": "result", "title": "See your result",
                    "detail": "How today compared with your last session.", "practice": None,
                    "state": "current" if finished else "todo", "done": 0, "needed": 1, "progress": ""})
        return out

    def current(self):
        """The step to do next (the result step once the others are done or skipped)."""
        return next(s for s in self.steps() if s["state"] == "current")

    def position(self):
        """(n, m): the current step's number and how many steps there are."""
        steps = self.steps()
        return next(i for i, s in enumerate(steps) if s["state"] == "current") + 1, len(steps)

    @property
    def finished(self):
        return self.current()["key"] == "result"

    def skip(self, key):
        """Skip a step for today; the next one becomes current. Returns the new state to remember."""
        if key in {s["key"] for s in self.steps()[:-1]} and key not in self.state["skipped"]:
            self.state["skipped"].append(key)
        return dict(self.state)

    def note_vowel_check(self):
        """A vowel check was finished today (it ticks the warm-up). Returns the state to remember."""
        self.state["vowels"] = True
        return dict(self.state)

    def next_step(self, practice):
        """What a result in `practice` offers as the way on: {"key" (a practice, or "summary"), "label"}. None
        while the plan still has a step for this practice (keep going here); else the plan's current step, or
        today's summary once the plan is done (and something in it was)."""
        steps = self.steps()
        if any(s["practice"] == practice and s["state"] in ("current", "todo") for s in steps):
            return None
        current = self.current()
        if current["key"] == "result":
            if not any(s["state"] == "done" for s in steps[:-1]):
                return None
            return {"key": "summary", "label": "See today's report"}
        return {"key": current["practice"], "label": f"Next: {current['title']}"}

    def title(self):
        """'Today's practice' with where you are: 'Step 2 of 4' or 'All done'."""
        n, m = self.position()
        return "All done for today" if self.finished else f"Step {n} of {m}"

    # --- the summary ---

    def _phrase_pitch(self, takes):
        return _median([t.get("pitch") for t in takes if _is_phrase(t)])

    def summary(self, rest_text=None):
        """The end-of-session card: {"headline", "lines", "rest"}. Each line compares with the last session
        where there is one; kind in tone, and `rest_text` (the core's rest nudge) rides along."""
        lines = []
        today, before = self.today_takes, self.earlier
        phrases = [t for t in today if _is_phrase(t)]
        if phrases:
            now_hz = self._phrase_pitch(phrases)
            line = f"Phrases: {len(phrases)} recorded"
            if now_hz is not None:
                line += f", pitch around {now_hz:.0f} Hz"
                last_hz = self._phrase_pitch(before)
                if last_hz is not None:
                    diff = now_hz - last_hz
                    line += (", about the same as your last session" if abs(diff) < PITCH_SAME_HZ else
                             f", {abs(diff):.0f} Hz {'higher' if diff > 0 else 'lower'} than your last session")
            lines.append(line + ".")
        passages = [t for t in today if _is_passage(t)]
        if passages:
            take = passages[-1]
            line = "Passage: "
            if take.get("pitch") is not None:
                line += f"pitch around {take['pitch']:.0f} Hz"
                prev = next((t for t in reversed(before) if t.get("task") == take.get("task")), None)
                if prev is not None and prev.get("pitch") is not None:
                    diff = take["pitch"] - prev["pitch"]
                    line += (", about the same as last time" if abs(diff) < PITCH_SAME_HZ else
                             f", {abs(diff):.0f} Hz {'higher' if diff > 0 else 'lower'} than last time")
                else:
                    line += ", your first reading of this one"
            lines.append(line.rstrip(": ") + ".")
        exercises = [t for t in today if _is_exercise(t)]
        if exercises:
            scores = [t["exercise"] for t in exercises if t["exercise"].get("notes_total")]
            if scores:
                tuned = sum(s["notes_in_tune"] for s in scores)
                total = sum(s["notes_total"] for s in scores)
                lines.append(f"Singing: {tuned} of {total} notes in tune across {len(scores)} "
                             f"exercise{'s' if len(scores) > 1 else ''}.")
        if self.state["vowels"]:
            lines.append("Vowel check: done, and the analysis is tuned to your voice.")
        skipped = [s["title"].lower() for s in self.steps()[:-1] if s["state"] == "skipped"]
        if skipped:
            lines.append(f"Skipped today: {', '.join(skipped)}. That is fine; they will be here next time.")
        if not before:
            lines.append("This is your first session, so next time you will see how it compares.")
        headline = "Nice work. Today's practice is done." if lines else "Today's practice is done."
        return {"headline": headline, "lines": lines, "rest": rest_text}
