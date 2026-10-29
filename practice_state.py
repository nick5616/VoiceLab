"""What state a practice is in, in words: ready, running (with progress), paused, done or an error, and why its
controls are disabled. Pure; the Practice screen draws it. No UI toolkit."""
from speech import clock_text

STATES = ("ready", "running", "paused", "done", "error")
CHIP_KINDS = {"ready": "neutral", "running": "info", "paused": "warning", "done": "success", "error": "error"}
START_HINTS = {
    "read": "Press Start reading, then read the passage aloud at an easy pace.",
    "phrases": "Press Record this phrase, say it the way you would to a stranger, then press Stop.",
    "vowels": "Press Start. Eight short words appear one at a time; say each once, then pause.",
    "exercise": "Press Start. After a short count-in, sing along with the bars.",
    "voicemap": "It fills in as you sing or speak anywhere in VoiceLab. Nothing to start.",
}
VERBS = {"read": "Reading", "phrases": "Recording the phrase", "vowels": "Vowel check", "exercise": "Singing"}


def disabled_reason(controls, record_state, recording=False):
    """Why Start can't be pressed now (words), or None when it can. `controls` is core.controls(),
    `record_state` core.record_state()."""
    if recording or controls.get("record"):
        return None
    state, caption = record_state
    if state == "analysing":
        return "Wait a moment: your last recording is still being analysed."
    if caption == "Mic unavailable":
        return "No microphone is available. Choose one in Settings, then come back."
    return "Another practice is running. Stop it first."


def state_view(kind, facts):
    """{"state", "label", "detail", "progress" (done, total) or None, "elapsed" ('0:42' or '')} of a practice.
    `facts` by kind:
      read / phrases: recording, analysing, seconds, mic_ok, has_result, error
      vowels: running, index, total, analysing, has_result, error
      exercise: running, now, end, analysing, has_result, error
      voicemap: seconds (voice mapped), rebuilding"""
    f = facts
    error = f.get("error")
    if error:
        return _view("error", "Needs attention", error)
    if kind in ("read", "phrases"):
        if f.get("recording"):
            elapsed = clock_text(f.get("seconds", 0))
            if not f.get("mic_ok", True):
                return _view("paused", "Paused", "Waiting for your mic. Check it is plugged in.", elapsed=elapsed)
            return _view("running", f"{VERBS[kind]}", "Press Stop when you have finished.", elapsed=elapsed)
        if f.get("analysing"):
            return _view("running", "Analysing", "Working out your result…", indeterminate=True)
        if f.get("has_result"):
            return _view("done", "Done", "Your result is below.")
    elif kind == "vowels":
        if f.get("running"):
            total = f.get("total", 8)
            return _view("running", f"Word {min(f.get('index', 0) + 1, total)} of {total}",
                         "Say the word once, then pause.", progress=(f.get("index", 0), total))
        if f.get("analysing"):
            return _view("running", "Measuring your vowels", "This takes a few seconds.", indeterminate=True)
        if f.get("has_result"):
            return _view("done", "Done", "Your result is below.")
    elif kind == "exercise":
        if f.get("running"):
            end = f.get("end") or 1.0
            now = f.get("now") or 0.0
            return _view("running", "Singing" if now >= 0 else "Count-in", "Sing along with the bars.",
                         progress=(min(now, end), end), elapsed=clock_text(now))
        if f.get("analysing"):
            return _view("running", "Scoring", "Marking each note…", indeterminate=True)
        if f.get("has_result"):
            return _view("done", "Done", "Your score is below.")
    elif kind == "voicemap":
        if f.get("rebuilding"):
            return _view("running", "Rebuilding", "Building the map again from your saved takes…",
                         indeterminate=True)
        seconds = f.get("seconds", 0)
        if seconds > 0:
            return _view("done", "Map ready", f"{seconds:.0f} s of voice mapped so far.")
    return _view("ready", "Ready", START_HINTS[kind])


def _view(state, label, detail, progress=None, elapsed="", indeterminate=False):
    return {"state": state, "label": label, "detail": detail, "progress": progress, "elapsed": elapsed,
            "indeterminate": indeterminate, "kind": CHIP_KINDS[state]}


HEARING = {"hearing": "Hearing you", "silent": "Say something", "too_quiet": "Too quiet: move a little closer",
           "no_signal": "No sound from your mic"}      # the live snapshot's state, in words


def readout_view(snapshot, tracker, recording):
    """What the "right now" readout of a reading page says: {"hz", "state_text", "range_text", "range_ok",
    "resonance"}. `snapshot` is the live snapshot (its "state" when spec 03's is there, else voiced or not);
    `tracker` is a speech.RangeTracker; the range line only counts while `recording`."""
    hz = snapshot.get("f0")
    state = snapshot.get("state") or ("hearing" if hz is not None else "silent")
    range_text = tracker.text() if recording else ""
    share = tracker.share()
    if range_text and share is not None:
        range_text += f" · {share:.0%} of the time so far"
    metric = (snapshot.get("metrics") or {}).get("delta_f") or {}
    shown = metric.get("shown")
    resonance = f"Resonance {shown}" if shown and shown != "—" and not metric.get("withheld") else ""
    return {"hz": hz, "state_text": HEARING.get(state, ""), "range_text": range_text,
            "range_ok": tracker.held >= 1, "resonance": resonance}


def start_reason(view, recording, analysing):
    """Why Start is unavailable for a vowel check or exercise whose state_view is `view`, or None."""
    if view["state"] == "running" and not view["indeterminate"]:
        return None                                     # it is the thing running: the button is Stop
    if recording:
        return "A recording is running: stop it first."
    if analysing:
        return "Wait a moment: your last recording is still being analysed."
    if view["indeterminate"]:
        return "Wait a moment: this is still being worked out."
    return None
