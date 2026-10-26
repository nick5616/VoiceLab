"""Speaking practice: reading passages and the guided vowel check (VowelCheckSession). No UI toolkit."""
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

    def state(self):
        """{"running", "prompt", "status", "can_save_baseline", "current", "baseline"}: the texts, and the
        vowels ({word: {"f1", "f2"}}) of this check and of the vowel baseline (None without them)."""
        baseline = self.profile.vowel_baseline
        return {"running": self.running, "prompt": self.prompt, "status": self.status,
                "can_save_baseline": self.can_save_baseline,
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
            self.status = result.get("error", NO_VOWELS)
            self._changed()
            return
        self.result = result
        self.profile.log_vowel_check(result)
        self.can_save_baseline = True
        self.refresh_profile()
        self.events.emit("vowel_check_done", result)

    def refresh_profile(self):
        """Re-read the vowel baseline: the result's text compares with it."""
        if self.result:
            df = self.result["delta_f"]
            baseline = self.profile.vowel_baseline
            parts = [f"Vowel-balanced resonance, ≈{vocal_tract_length_cm(df):.1f} cm tract."]
            if baseline and baseline.get("delta_f"):
                parts.append(f"{df - baseline['delta_f']:+.0f} Hz vs vowel baseline "
                             f"({baseline.get('recorded', '?')}).")
            if self.result["skipped"]:
                parts.append(f"Not measured: {', '.join(self.result['skipped'])}.")
            self.prompt, self.status = f"ΔF {df:.0f} Hz", " ".join(parts)
        self._changed()

    def save_baseline(self):
        if self.result:
            self.profile.set_vowel_baseline(self.result)
            self.refresh_profile()
            self.status += " Saved as vowel baseline."
            self._changed()
