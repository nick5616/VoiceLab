"""What the frame around the screens says, as plain functions (phase 11 / 05 shell, audit A-032..A-044):
a short device name, the mic's state words, plain-language mic errors, the action an error carries, the
words for a background job and what it affects. No UI toolkit; the window only draws these."""
import re

# --- the mic block ---
DRIVER_WORDS = ("audio", "realtek", "high definition", "driver", "usb pnp", "codec", "intel", "nvidia", "amd")   # noise in "(...)"
MIC_PREFIXES = ("microphone", "mic", "line in", "line", "input")      # a name that is only a kind of input
MME_NAME_LIMIT = 31             # chars: Windows MME cuts a device name here
MIC_STATES = {                  # key: (words, StatusDot state, plain explanation)
    "unavailable": ("No mic", "error", "VoiceLab can't use a microphone right now."),
    "lost": ("Mic stopped", "error", "The mic stopped sending audio."),
    "listening": ("Listening", "ok", "The mic is on and quiet. Say something."),
    "hearing": ("Hearing you", "ok", "The mic hears a voice."),
    "too_quiet": ("Too quiet", "warn", "The mic hears you, but very quietly."),
    "recording": ("Recording", "recording", "A take is being recorded."),
    "paused": ("Practice on", "idle", "A practice step is using the mic."),
}
LEVEL_FLOOR_DB = -60.0          # dBFS shown as an empty meter
LEVEL_CEIL_DB = -10.0           # dBFS shown as a full meter

# --- errors ---
ERROR_TEXTS = [                 # (substring of the raw error, plain text); first match wins
    ("-9985", "The mic is in use by another app, or Windows is blocking it. Close other apps that record, "
              "then choose the mic again."),
    ("device unavailable", "The mic is in use by another app, or Windows is blocking it. Close other apps that "
                           "record, then choose the mic again."),
    ("-9999", "Windows couldn't open the mic. It may be unplugged or in use by another app."),
    ("unanticipated host error", "Windows couldn't open the mic. It may be unplugged or in use by another app."),
    ("-9996", "That mic isn't there any more. Plug it back in, or choose another."),
    ("invalid device", "That mic isn't there any more. Plug it back in, or choose another."),
    ("-9997", "This mic doesn't accept the sample rate VoiceLab asked for. Choose it again, or try another."),
    ("invalid sample rate", "This mic doesn't accept the sample rate VoiceLab asked for. Choose it again, or "
                            "try another."),
    ("invalid number of channels", "This mic doesn't have the inputs asked for. Choose it again without the EGG."),
]
GENERIC_MIC_ERROR = "Windows couldn't open the mic. Check that it is plugged in, then choose it again."
LOST_TEXT = "Mic stopped sending audio. Plug it back in, then choose your mic."
RECONNECTED_TEXT = "Mic reconnected."
ACTIONS = {                     # key: button text
    "choose_mic": "Choose mic", "mic_check": "Mic check", "choose_folder": "Choose folder",
    "retry": "Try again", "settings": "Open Settings", "open_takes": "Open Takes",
    "rebuild_map": "Rebuild Voice Map", "open_report": "Open report", "update_takes": "Update now",
    "set_baseline": "Set baseline",
}

# --- jobs ---
CHIP_CHARS = 24                 # the activity chip's text, at most
JOB_WORDS = {                   # kind: (short chip text, what it changes)
    "reanalysis": ("Updating takes", "Numbers in your older takes may change while this runs. Live readings "
                                     "are not affected."),
    "analysis": ("Analysing your take", "Your new take appears in Takes when it is ready."),
    "load": ("Loading your takes", "Your takes appear as they load."),
    "register": ("Updating register", "Register shares in your takes may change. Live readings are not affected."),
    "voicemap": ("Rebuilding Voice Map", "The Voice Map in Practice is replaced when this finishes."),
    "exercise": ("Scoring exercise", "Your score appears when it finishes."),
    "vowels": ("Measuring vowels", "The vowel plot updates when it finishes."),
    "shift": ("Rendering voice", "The shifted voice plays when it is ready."),
}
PROGRESS_OF = re.compile(r"(\d+)\s*(?:of|/)\s*(\d+)")


def short_device_name(name):
    """A mic's name as a person would say it: 'Microphone (USB PnP Audio Device)' -> 'USB PnP Audio Device'.
    Windows MME cuts names at 31 characters, so a name cut mid-word loses its last, partial word."""
    name = " ".join((name or "").split())
    if not name:
        return ""
    match = re.match(r"^(.*?)\s*\((.*?)\)?$", name)
    if not match or not match.group(2):
        return name
    outside, inside = match.group(1).strip(), match.group(2).strip()
    closed = name.endswith(")")
    if not closed:
        inside = _drop_partial_word(inside, len(name))
    if outside.lower() in MIC_PREFIXES or not outside:
        return inside or outside
    if any(word in inside.lower() for word in DRIVER_WORDS):
        return outside
    return f"{outside} ({inside})"


def _drop_partial_word(text, name_length):
    """A name cut at the MME limit: drop its last word, which is probably partial."""
    if name_length < MME_NAME_LIMIT or " " not in text:
        return text
    return text.rsplit(" ", 1)[0]


def mic_level_fraction(level_db):
    """The mic meter's fill, 0-1, for a level in dBFS (None or very quiet: empty)."""
    if level_db is None or level_db != level_db:
        return 0.0
    return min(1.0, max(0.0, (level_db - LEVEL_FLOOR_DB) / (LEVEL_CEIL_DB - LEVEL_FLOOR_DB)))


def mic_indicator(mic_state="ok", live_state=None, recording=False, practice=False):
    """What the top bar's mic block shows: {"key", "words", "dot" (a StatusDot state), "tooltip"}. `mic_state` is
    core.mic_status()["state"] (ok, warn, error, lost); `live_state` the snapshot's (no_signal, silent, too_quiet,
    hearing) or None; `recording` a take being recorded; `practice` a drill or exercise using the mic."""
    if mic_state == "lost" or live_state == "no_signal":
        key = "lost"
    elif mic_state == "error":
        key = "unavailable"
    elif recording:
        key = "recording"
    elif practice:
        key = "paused"
    elif live_state in ("hearing", "too_quiet"):
        key = live_state
    else:
        key = "listening"
    words, dot, explanation = MIC_STATES[key]
    if mic_state == "warn" and dot == "ok":
        dot = "warn"                    # the input has a problem the banner names
    return {"key": key, "words": words, "dot": dot, "tooltip": explanation}


def plain_mic_error(raw):
    """A mic error in plain words, without PortAudio codes: (text, details), details being the raw message."""
    low = (raw or "").lower()
    for needle, text in ERROR_TEXTS:
        if needle in low:
            return text, raw
    return GENERIC_MIC_ERROR, raw


def error_action(text, level="error"):
    """The button an error or warning carries: (key, label) from ACTIONS, or None for what needs none
    (info and success). Every error offers something to do."""
    if level not in ("error", "warning"):
        return None
    low = (text or "").lower()
    if any(w in low for w in ("microphone", " mic", "mic ", "audio device", "inputstream")):
        key = "mic_check" if "noisy" in low or "quiet" in low or "clipping" in low else "choose_mic"
    elif any(w in low for w in ("folder", "save the take", "no space", "permission denied", "read-only")):
        key = "choose_folder"
    elif "voice map" in low and "re-analys" in low:
        key = "open_takes"
    elif "take" in low or "analys" in low:
        key = "open_takes"
    else:
        key = "settings"
    return key, ACTIONS[key]


def describe_job(job, progress=None):
    """A background job in plain words: {"id", "kind", "chip", "label", "affects", "done", "total", "text"}.
    `progress` is (n, of) when the job is one step of a longer round (re-analysis, take by take)."""
    kind = job.get("kind") or ""
    chip, affects = JOB_WORDS.get(kind, (None, "Working in the background."))
    label = job.get("label") or chip or "Working"
    if progress is None and kind == "reanalysis":
        found = PROGRESS_OF.search(label)
        progress = (int(found.group(1)), int(found.group(2))) if found else None
    if kind == "reanalysis" and progress:
        chip = f"Updating takes {progress[0]}/{progress[1]}"
        label = f"Updating your takes to the new analysis: {progress[0]} of {progress[1]}"
    if len(label) <= CHIP_CHARS and kind != "reanalysis":
        chip = label                    # the job's own words, when they fit: the chip and the job agree
    chip = chip or label
    if len(chip) > CHIP_CHARS:
        chip = chip[:CHIP_CHARS - 1].rstrip() + "…"
    done, total = job.get("done") or 0, job.get("total") or 0
    if progress:
        done, total = progress[0] - 1 + (done / total if total else 0), progress[1]
    return {"id": job.get("id"), "kind": kind, "chip": chip, "label": label, "affects": affects,
            "done": done, "total": total, "text": affects}


def activity_summary(jobs):
    """The chip for a job list [describe_job()...]: {"text", "tooltip"}; text is at most CHIP_CHARS."""
    if not jobs:
        return {"text": "Nothing running", "tooltip": "Nothing is running in the background."}
    first = jobs[0]["chip"]
    text = first if len(jobs) == 1 else f"{first} +{len(jobs) - 1}"
    if len(text) > CHIP_CHARS:
        text = f"{len(jobs)} tasks running"
    return {"text": text, "tooltip": "\n".join(f"{j['label']}: {j['affects']}" for j in jobs)}


def take_ready_text(number):
    """The toast after a take is analysed."""
    return f"Take #{number} is ready" if number else "Your take is ready"


def baseline_prompt_due(baseline, prompted, take):
    """True when the first good take should offer 'Make this your starting point?': no baseline yet, not asked
    before, and the take's pitch is at least medium-confidence."""
    if baseline or prompted or not take or take.get("exercise"):
        return False
    confidence = take.get("confidence") or {}
    return take.get("pitch") is not None and confidence.get("pitch") in ("high", "medium")


# --- shortcuts and the import sheet ---
SHORTCUTS = [               # (keys, what it does): the cheat-sheet, the Settings list and the tooltips
    ("Space", "Start or stop recording a take"),
    ("R", "Replay the last 10 seconds"),
    ("Ctrl+I", "Import an audio file"),
    ("Ctrl+1 … 6", "Go to Today, Live, Practice, Takes, Progress or Settings"),
    ("F11", "Full screen"),
    ("Ctrl+Shift+D", "Show what the app is measuring (the inspector)"),
    ("?", "Show this list"),
]
IMPORT_VOICE_CHOICES = [("voice", "Voice only"), ("music", "Voice with music (slower)")]
IMPORT_CHANNEL_CHOICES = [("mix", "No, mix them together"), ("0", "Yes, channel 1"), ("1", "Yes, channel 2")]
IMPORT_CHANNEL_QUESTION = "This file has two channels. Is one of them a second sensor recorded alongside your voice?"
IMPORT_MUSIC_QUESTION = "Is there music behind the voice?"
IMPORT_DETAILS = ("A second sensor here means an electroglottograph (EGG): a band on the neck that records vocal "
                  "fold contact. If you have no such file, answer no. Separating a voice from music uses Demucs, "
                  "a model that is slower and downloads about 80 MB the first time.")
SPEAK_HIDDEN_TARGETS = ("ring", "register")     # targets that only mean something when singing


def import_options(choices):
    """(isolate, egg_channel) from the import sheet's answers {"voice": "voice"|"music", "channel": "mix"|"0"|"1"}."""
    channel = choices.get("channel", "mix")
    return choices.get("voice") == "music", None if channel in ("mix", None) else int(channel)


def target_keys_for_mode(keys, mode):
    """The target rows to show in a mode: speaking hides Ring and Register (singing readings)."""
    return [k for k in keys if mode != "speak" or k not in SPEAK_HIDDEN_TARGETS]


def plain_baseline_text(text):
    """A baseline line without the technical name: 'ΔF 993 Hz' reads 'Resonance 993 Hz'."""
    return (text or "").replace("ΔF", "Resonance")


def live_state(frame, snapshot=None):
    """The state the mic block shows: the live snapshot's own when it has one (no_signal, silent, too_quiet,
    hearing), else hearing when the frame has a pitch and silent when it has none."""
    state = (snapshot or {}).get("state")
    if state:
        return state
    f0 = frame.get("f0")
    return "hearing" if f0 is not None and f0 == f0 else "silent"
