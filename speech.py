"""Speaking practice: reading passages and the guided vowel check (VowelCheckSession). No UI toolkit."""
import re

import numpy as np

from analysis import VOWEL_WORDS, analyze_vowels, vocal_tract_length_cm
from engine import UPDATE_INTERVAL
from jobs import JobRunner

# Original passages, written to cover different speaking situations. Reading the
# same passage each session makes takes comparable over time.
READING_PASSAGES = [
    ("Vowel tour",
     "We see three green leaves. Is it his big fish? Ten red hens fed well. "
     "The black cat sat back. Father wants hot soup. The sun comes up. "
     "Look, the good book stood. Who knew the blue moon would move so soon?"),
    ("Questions and answers",
     "Are you coming tonight? Yes, I'd love to! What time does it start? "
     "Around eight, I think. Should I bring anything? Just yourself, "
     "and maybe a few snacks if you're feeling generous."),
    ("Everyday story",
     "This morning I walked to the little café on the corner. The barista remembered "
     "my name and my usual order. While I waited, I watched the rain roll across the "
     "windows, and I realized how much I enjoy these small, quiet routines."),
    ("Phone call",
     "Hi, it's me! I'm just calling to check in. Do you have a minute? Great. "
     "So, about Saturday: are we still meeting at noon, or would a bit later "
     "work better for you? Either way is fine with me."),
    ("Counting and days",
     "One, two, three, four, five, six, seven, eight, nine, ten. "
     "Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday."),
]

# Vowel check capture: this much voicing counts as hearing the word, and this
# much silence afterwards means it has ended.
HEARD_SECONDS = 0.2
END_SILENCE_SECONDS = 0.4
VOWEL_PROMPT = "Vowel check"
VOWEL_INTRO = ("Say 8 short words, one at a time. Each is captured when you "
               "pause. Use your practice voice; keep pitch under ~230 Hz.")
NO_VOWELS = "No vowels could be measured. Try a bit louder, and keep your pitch under ~230 Hz."


# Everyday phrases for the live phrase prompt: short, ordinary things people say out loud, so the
# practice voice gets used where it matters. Written for VoiceLab; a mix of questions (rising
# endings) and statements, greetings, requests and small talk.
PHRASES = [
    "Hi, could I get a medium latte, please?",
    "What's the best number to reach you on?",
    "Sorry, I didn't catch your name.",
    "Is this seat taken?",
    "Thanks so much, have a lovely evening!",
    "Could you tell me where the station is?",
    "I'd like to book a table for two at seven.",
    "Hello, this is Sam calling about my appointment.",
    "Oh, that sounds wonderful!",
    "Can I pay by card?",
    "How was your weekend?",
    "Excuse me, is this the right line?",
    "I'll have the soup, and a glass of water.",
    "Nice to meet you, I've heard so much about you.",
    "Do you know what time it closes?",
    "No worries at all, take your time.",
]
CUSTOM_PASSAGE_NAME = "Your own passage"
CUSTOM_PASSAGE_HINT = "Paste or type anything you like to read: a poem, a script, an email."


def passages(custom=None):
    """[(name, text)] of the passages to read: the built-in ones, then your own when you have one."""
    return READING_PASSAGES + ([(CUSTOM_PASSAGE_NAME, custom)] if custom and custom.strip() else [])


def passage(index, custom=None):
    """{"index", "name", "title", "text", "custom"} of a reading passage; the index wraps around.
    `custom` is your own passage's text, offered after the built-in ones."""
    shown = passages(custom)
    index %= len(shown)
    name, text = shown[index]
    return {"index": index, "name": name, "text": text, "custom": index >= len(READING_PASSAGES),
            "title": f"Read aloud · {index + 1}/{len(shown)} · {name}"}


def phrase(index):
    """{"index", "text", "number", "count"} of an everyday phrase; the index wraps around."""
    index %= len(PHRASES)
    return {"index": index, "text": PHRASES[index], "number": index + 1, "count": len(PHRASES)}


class VowelCheckSession:
    """Hands-free drill: prompts each hVd word, captures it when you finish saying it, then measures
    your vowel space and vowel-balanced resonance. Emits "vowels_changed"(state) when its text changes,
    "practice"(running) when it starts or stops, and "vowel_check_done"(result) with a result."""

    def __init__(self, engine, profile, events, runner=None):
        self.engine, self.profile, self.events = engine, profile, events
        self.runner = runner or JobRunner(events)
        self.running = False
        self.result = None
        self.job = None
        self.tokens, self.index = {}, 0
        self.voiced_frames = self.silent_frames = 0
        self.prompt, self.status = VOWEL_PROMPT, VOWEL_INTRO
        self.can_save_baseline = False
        self.baseline_saved = False     # this result is the vowel baseline now
        self.failed = False             # the last check couldn't be measured

    def state(self):
        """{"running", "prompt", "status", "can_save_baseline", "current", "baseline"}: the texts, and the
        vowels ({word: {"f1", "f2"}}) of this check and of the vowel baseline (None without them)."""
        baseline = self.profile.vowel_baseline
        return {"running": self.running, "prompt": self.prompt, "status": self.status,
                "can_save_baseline": self.can_save_baseline, "index": self.index, "total": len(VOWEL_WORDS),
                "analysing": self.job is not None, "error": self.status if self.failed else None,
                "heard": list(self.tokens),
                "current": self.result["vowels"] if self.result else None,
                "baseline": baseline["vowels"] if baseline else None}

    def _changed(self):
        self.events.emit("vowels_changed", self.state())

    # --- DRILL FLOW ---

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.engine.is_capturing():
            self.status = "Stop the current recording first."
            self._changed()
            return False
        self.tokens, self.index = {}, 0
        self.failed = False
        self._set_running(True)
        self._prompt_word()
        return True

    def stop(self):
        if self.engine.is_capturing():
            self.engine.stop_take()
        self.prompt, self.status = VOWEL_PROMPT, "Stopped."
        self._set_running(False)

    def skip(self):
        if self.running:
            self.engine.stop_take()
            self._next_word()

    def _set_running(self, running):
        self.running = running
        self._changed()
        self.events.emit("practice", running)

    def _prompt_word(self):
        word = VOWEL_WORDS[self.index]
        self.prompt = f"“{word}”"
        self.status = f"Word {self.index + 1} of {len(VOWEL_WORDS)}: say it once, then pause."
        self.voiced_frames = self.silent_frames = 0
        self.engine.start_take()
        self._changed()

    def on_frame(self, frame):
        """Follow one live frame: a word ends after some voice and then a pause. Returns state() while
        running, else None."""
        if not self.running:
            return None
        if not np.isnan(frame["f0"]):
            self.voiced_frames += 1
            self.silent_frames = 0
        else:
            self.silent_frames += 1
        heard = self.voiced_frames * UPDATE_INTERVAL >= HEARD_SECONDS
        if heard and self.silent_frames * UPDATE_INTERVAL >= END_SILENCE_SECONDS:
            self.tokens[VOWEL_WORDS[self.index]] = self.engine.stop_take()
            self._next_word()
        return self.state()

    def _next_word(self):
        self.index += 1
        if self.index < len(VOWEL_WORDS):
            self._prompt_word()
            return
        self.prompt, self.status = "Analyzing…", ""
        self._set_running(False)
        self.job = self.runner.submit("vowels", "Measuring your vowels", analyze_vowels, self.tokens,
                                      on_done=self._show_result)

    # --- RESULTS ---

    def _show_result(self, result):
        self.job = None
        if "error" in result or not result["vowels"]:
            self.prompt = VOWEL_PROMPT
            self.failed = True
            self.status = result.get("error", NO_VOWELS)
            self._changed()
            return
        self.result = result
        self.baseline_saved = False
        self.profile.log_vowel_check(result)
        self.can_save_baseline = True
        self.refresh_profile()
        self.events.emit("vowel_check_done", result)

    def refresh_profile(self):
        """Re-read the vowel baseline: the result's text compares with it."""
        if self.result:
            df = self.result["delta_f"]
            baseline = self.profile.vowel_baseline
            parts = [f"A fair reading of your resonance across eight vowels (≈{vocal_tract_length_cm(df):.1f} cm "
                     "apparent tract)."]
            if baseline and baseline.get("delta_f"):
                parts.append(f"{df - baseline['delta_f']:+.0f} Hz vs vowel baseline "
                             f"({baseline.get('recorded', '?')}).")
            if self.result["skipped"]:
                parts.append(f"Not measured: {', '.join(self.result['skipped'])}.")
            self.prompt, self.status = f"Resonance {df:.0f} Hz", " ".join(parts)
        self._changed()

    def card(self):
        """The result of a finished check in plain words, or None: {"headline", "lines", "ceiling" (Hz the
        analysis now measures with, or None), "question" (use it as your baseline?), "baseline_saved"}."""
        if not self.result:
            return None
        df = self.result["delta_f"]
        baseline = self.profile.vowel_baseline
        lines = []
        ceiling = self.result.get("formant_ceiling")
        if ceiling:
            lines.append(f"It tuned the analysis to your voice: your takes are now measured with a {ceiling:.0f} Hz "
                         "ceiling, so resonance readings are fairer.")
        if baseline and baseline.get("delta_f") and not self.baseline_saved:
            lines.append(f"{df - baseline['delta_f']:+.0f} Hz compared with your vowel baseline "
                         f"({baseline.get('recorded', 'earlier')}).")
        if self.result["skipped"]:
            lines.append(f"Not measured: {', '.join(self.result['skipped'])}.")
        question = ("Saved as your vowel baseline. Your next check will compare with it." if self.baseline_saved else
                    "Use this as your vowel baseline? Your next check will compare with it.")
        return {"headline": f"Your resonance across eight vowels is {df:.0f} Hz", "lines": lines,
                "ceiling": ceiling, "question": question, "baseline_saved": self.baseline_saved}

    def save_baseline(self):
        if self.result:
            self.baseline_saved = True
            self.profile.set_vowel_baseline(self.result)
            self.refresh_profile()
            self.status += " Saved as vowel baseline."
            self._changed()


# --- phase 11 / 06-practice: reading position, in-range strip and per-take results ---
READING_WPM = 150               # words per minute: a relaxed reading pace, for the "you are here" highlight
RANGE_GAP_SECONDS = 0.3         # s: a silence this long ends a held stretch in range
RANGE_MAX_STEP = 0.25           # s: the most one frame step can add to the clocks (a paused feed adds nothing)
MIN_PHRASE_SECONDS = 1.0        # s of clear voice a phrase needs before it gets a pitch result
PITCH_SAME_PERCENT = 3.0        # %: a pitch within this of last time is "about the same"
RESONANCE_SAME_HZ = 40.0        # Hz: report.CHANGE_MIN for resonance
INTONATION_SAME_ST = 1.5        # st: report.CHANGE_MIN for intonation
FIRST_READING = "This is your first reading of this passage."
PHRASES_FOR_SUMMARY = 3          # phrases recorded in a day before they are summed up


def sentence_spans(text):
    """[(start, end)] character spans of the sentences of `text` (a sentence ends at . ! or ?)."""
    spans, start = [], 0
    for match in re.finditer(r"[.!?]+(?:\s+|$)", text):
        spans.append((start, match.end()))
        start = match.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def reading_position(text, seconds, wpm=READING_WPM):
    """Where a reader at `wpm` has got to after `seconds`: {"sentence" (index), "count", "span" (start, end
    characters of that sentence), "fraction" (0-1 of the passage), "done" (the passage should be finished)}."""
    spans = sentence_spans(text)
    if not spans:
        return {"sentence": 0, "count": 0, "span": (0, 0), "fraction": 0.0, "done": False}
    counts = [len(text[s:e].split()) for s, e in spans]
    words = max(0.0, seconds) * wpm / 60.0
    total = sum(counts)
    done_words, index = 0, 0
    for index, n in enumerate(counts):
        if words < done_words + n:
            break
        done_words += n
    else:
        index = len(counts) - 1
    return {"sentence": index, "count": len(spans), "span": spans[index],
            "fraction": min(1.0, words / total) if total else 0.0, "done": words >= total}


def clock_text(seconds):
    """'0:42' for a time in seconds."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class RangeTracker:
    """Time in the pitch target while you speak: the stretch held right now and the share of voiced time.
    An adapter with the live session's in-range logic (the dashboard's "held N s"), for the reading view."""

    def __init__(self, target):
        self.target = target            # (low, high) Hz or None
        self.held = 0.0                 # s in range without a break
        self.voiced_seconds = self.in_seconds = 0.0
        self.last_at = None
        self.gap = 0.0

    def add(self, hz, now):
        """One frame: `hz` is the pitch (None unvoiced) at clock time `now`. Returns True when in range."""
        step = 0.0 if self.last_at is None else min(RANGE_MAX_STEP, max(0.0, now - self.last_at))
        self.last_at = now
        if hz is None or self.target is None:
            self.gap += step
            if self.gap >= RANGE_GAP_SECONDS:
                self.held = 0.0
            return False
        self.gap = 0.0
        self.voiced_seconds += step
        inside = self.target[0] <= hz <= self.target[1]
        if inside:
            self.in_seconds += step
            self.held += step
        else:
            self.held = 0.0
        return inside

    def share(self):
        """Share (0-1) of voiced time in range, or None before any voice or without a target."""
        if self.target is None or self.voiced_seconds <= 0:
            return None
        return self.in_seconds / self.voiced_seconds

    def reset(self):
        self.__init__(self.target)

    def text(self):
        """'In range 4 s' while held, 'Not in range yet' while not, '' without a target."""
        if self.target is None:
            return ""
        return f"In range {self.held:.0f} s" if self.held >= 1 else "Not in range yet"


def _trusted(take):
    """The take's per-reading trust ({key: bool}); empty when it can't be told."""
    try:
        import coach
        return coach.take_quality(take, take.get("noise"))["trust"]
    except Exception:
        return {}


def _median_of(take, track):
    """Median of a take's track (None without one)."""
    values = np.asarray((take.get("tracks") or {}).get(track, []), dtype=float)
    values = values[~np.isnan(values)]
    return float(np.median(values)) if len(values) else None


def phrase_result(take, profile=None):
    """What one spoken phrase showed, right after it: {"ok", "text", "pitch" (Hz), "resonance" (Hz),
    "pitch_share", "resonance_share" (0-1 inside the target, None without one), "seconds" (voiced)}. `ok` is
    False when too little clear voice was heard."""
    tracks = take.get("tracks") or {}
    f0 = np.asarray(tracks.get("f0", []), dtype=float)
    times = np.asarray(tracks.get("times", []), dtype=float)
    voiced = f0[~np.isnan(f0)]
    step = float(np.median(np.diff(times))) if len(times) > 1 else 0.0
    seconds = len(voiced) * step
    if seconds < MIN_PHRASE_SECONDS:
        return {"ok": False, "seconds": seconds, "pitch": None, "resonance": None, "pitch_share": None,
                "resonance_share": None,
                "text": "Not enough of your voice was heard in that one. Try it again a little louder."}
    trust = _trusted(take)
    pitch = float(np.median(voiced))
    resonance = _median_of(take, "delta_f") if trust.get("delta_f", True) else None
    shares = {"pitch_share": None, "resonance_share": None}
    if profile is not None:
        shares["pitch_share"] = profile.time_in_target("pitch", f0)
        if resonance is not None:
            shares["resonance_share"] = profile.time_in_target("delta_f", tracks.get("delta_f", []))
    pitch_text = f"Pitch {pitch:.0f} Hz"
    if shares["pitch_share"] is not None:
        pitch_text += f", {shares['pitch_share']:.0%} in your range"
    parts = [pitch_text]
    if resonance is not None:
        share = shares["resonance_share"]
        parts.append(f"Resonance {resonance:.0f} Hz" + (f", {share:.0%} in your range" if share is not None else ""))
    return {"ok": True, "seconds": seconds, "pitch": pitch, "resonance": resonance, "text": ". ".join(parts) + ".",
            **shares}


def _change_word(value, other, same, up, down, unit, digits=0):
    diff = value - other
    if abs(diff) < same:
        return "about the same as last time"
    return f"{abs(diff):.{digits}f} {unit} {up if diff > 0 else down} than last time"


def passage_result(take, previous=None, profile=None, rep=None):
    """The card after a read-aloud take: {"headline", "tier_text", "rows" [{"key", "name", "value", "change"}]
    (pitch, resonance, intonation, in words), "tip", "first" (no earlier reading), "compare" (a line saying
    what it is compared with), "uid"}. `previous` is the latest earlier take of the same passage."""
    import report
    import take_view
    from display import NAMES
    rep = rep or report.build_report(take, profile, previous)
    first = previous is None
    rows = []
    for dim in rep["dimensions"]:
        if dim["key"] not in ("pitch", "delta_f", "pitch_range") or dim["value"] is None:
            continue
        key = dim["key"]
        other = None if first else previous.get(key)
        change = None
        if other is not None and not np.isnan(other):
            if key == "pitch":
                change = _change_word(dim["value"], other, other * PITCH_SAME_PERCENT / 100, "higher", "lower", "Hz")
            elif key == "delta_f":
                change = _change_word(dim["value"], other, RESONANCE_SAME_HZ, "brighter", "darker", "Hz")
            else:
                change = _change_word(dim["value"], other, INTONATION_SAME_ST, "more varied", "flatter", "st", 1)
        rows.append({"key": key, "name": NAMES[key]["name"], "value": dim["display"], "change": change})
    tips = rep.get("tips") or []
    tip = tips[0] if tips else ""
    if isinstance(tip, dict):
        tip = tip.get("text", "")
    when = take_view.short_date(previous) if previous else ""
    compare = FIRST_READING if first else (f"Compared with the last time you read this ({when})." if when
                                           else "Compared with the last time you read this.")
    tier = take_view.TIER_TEXT.get(take_view.quality(take)["tier"], "")
    return {"headline": rep["headline"], "tier_text": tier, "rows": rows, "tip": tip, "first": first,
            "compare": compare, "uid": take.get("uid")}


NUMBER_WORDS = {3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten"}


def phrases_summary(takes):
    """'Three phrases done. Your pitch averaged 190 Hz.' for the phrase takes made today, or '' before three."""
    if len(takes) < PHRASES_FOR_SUMMARY:
        return ""
    pitches = [t["pitch"] for t in takes if t.get("pitch")]
    n = len(takes)
    text = f"{NUMBER_WORDS.get(n, n)} phrases done."
    if pitches:
        text += f" Your pitch averaged {float(np.mean(pitches)):.0f} Hz."
    return text
