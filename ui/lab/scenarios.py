"""The lab's scenarios: a profile, a takes folder and a list of beats (one action, then a capture). Audio is
synthetic with a known truth (synth.py) or a copy of the user's real takes. GROUPS says which scenarios
each wave B owner runs; SMOKE is the fast set tests/test_uilab.py runs."""
from pathlib import Path

import numpy as np
import soundfile as sf

import synth
from ui.lab import driver
from ui.lab.driver import SAMPLE_RATE, TARGETS

REPLAY_SECONDS = 20.0           # s of each real take fed in speak_real_replay (the whole take is slow)
LEAD_SILENCE = 0.6              # s of room noise before a synthetic voice starts
STEADY_SECONDS = 2.0            # s of each steady voice: enough to settle and hold (the smoke run feeds them)
STEADY_F0 = (150, 185, 220)     # Hz: masculine, overlap and feminine under report.ZONES (145 / 170 Hz edges)
STEADY_FORMANTS = {150: synth.MALE_FORMANTS, 185: synth.NEUTRAL_FORMANTS, 220: synth.FEMALE_FORMANTS}
GLIDE = (120.0, 260.0, 4.0)     # Hz, Hz, s
SCALE_START_MIDI = 57           # A3
VIBRATO = (220.0, 5.5, 50)      # Hz, rate Hz, extent cents
TRUTH_DBFS = -30.0              # rms of the synthetic truth voices: a clear input, so every card can read
USER_DBFS = synth.VOICE_DBFS    # the user's own level (-42 dBFS), for record_take and the noisy beats
USER = {"base": "user", "goal": "feminine", "targets": TARGETS, "onboarded": True}
FRESH = {"base": "fresh", "onboarded": False, "asked": False}


# --- beats ---

def beat(name, do, capture=True, truth=None, variants=None, seconds=None, instant=False, **args):
    out = {"name": name, "do": do, "args": args, "capture": capture, "instant": instant}
    if truth:
        out["truth"] = truth
    if variants:
        out["variants"] = variants
    if seconds is not None:
        out["seconds"] = seconds
    return out


def go(screen, capture=True, **kw):
    return beat(f"go-{screen}", "go", capture, screen=screen, **kw)


def feed(name, audio, truth=None, capture=True, realtime=False):
    return beat(name, "feed", capture, truth=truth, audio=audio, realtime=realtime)


def with_room(audio, dbfs=synth.ROOM_DBFS, seed=0):
    return synth.mix(audio, synth.room_noise(len(audio) / SAMPLE_RATE, dbfs, seed))


def track_truth(track, kind, formants=None, lead=LEAD_SILENCE, step=0.01, **extra):
    """The truth of a voice: its pitch track every `step` s (NaN silent), relative to the feed's start."""
    full = synth.with_silence(track, before=lead)
    idx = (np.arange(0, len(full) / SAMPLE_RATE, step) * SAMPLE_RATE).astype(int)
    return {"kind": kind, "track_step": step, "track": [None if not np.isfinite(v) else round(float(v), 2)
                                                         for v in full[idx]],
            "voiced": synth.voiced_spans(full), "formants": formants, **extra}


def voice(track, formants, lead=LEAD_SILENCE, tail=0.0, seed=0, level=None, **kw):
    """A synthetic voice after `lead` s of room noise, as a feed() audio function. No tail by default: the
    beat is captured while the voice still sounds, as a user sees it mid-sentence."""
    level = TRUTH_DBFS if level is None else level
    return lambda: with_room(synth.synth_voice(synth.with_silence(track, lead, tail), formants, seed=seed,
                                               level_dbfs=level, **kw), seed=seed)


# --- scenarios ---

def first_run():
    beats = [beat("welcome", "start_up")]
    beats += [beat(f"step-{i}", "click", path="onboarding/next_btn") for i in (2, 3, 4, 5)]
    tr = synth.steady_track(150, 1.5)       # phase 11 / 05: the last step ("Try it") reacts to a voice
    beats += [feed("try-it-hears-you", voice(tr, synth.NEUTRAL_FORMANTS)),
              beat("finish", "click", path="onboarding/next_btn"), feed("dashboard-silence",
                                                                       lambda: synth.room_noise(2.0))]
    return {"id": "first_run", "title": "Onboarding, every step, then the empty dashboard", "profile": FRESH,
            "takes": "none", "beats": beats}


def empty():
    return {"id": "empty", "title": "Onboarded, no takes, mic idle", "profile": dict(USER, baseline=False),
            "takes": "empty", "beats": [
                feed("dashboard-silence", lambda: synth.silence(2.0)), go("live"),
                feed("live-silence", lambda: synth.silence(1.0)), go("takes"), go("progress")]}


def listening_silence():
    return {"id": "listening_silence", "title": "Mic open, room noise only", "profile": USER, "takes": "real",
            "truth": {"noise": "room"}, "beats": [
                feed("room-58dBFS", lambda: synth.room_noise(4.0, synth.ROOM_DBFS),
                     truth={"noise": "room", "dbfs": synth.ROOM_DBFS, "voiced": []}),
                feed("room-42dBFS", lambda: synth.room_noise(4.0, -42.0, seed=2),
                     truth={"noise": "room", "dbfs": -42.0, "voiced": []}),
                go("live"), feed("live-room", lambda: synth.room_noise(3.0, synth.ROOM_DBFS, seed=4),
                                 truth={"noise": "room", "voiced": []})]}


def speak_truth_steady():
    beats = []
    for f0 in STEADY_F0:
        forms = STEADY_FORMANTS[f0]
        tr = synth.steady_track(f0, STEADY_SECONDS)
        beats.append(feed(f"steady-{f0}Hz", voice(tr, forms), truth=track_truth(tr, "steady", forms, f0=f0)))
    tr = synth.steady_track(220, STEADY_SECONDS)
    beats += [go("live", capture=False),
              feed("live-steady-220Hz", voice(tr, synth.FEMALE_FORMANTS),
                   truth=track_truth(tr, "steady", synth.FEMALE_FORMANTS, f0=220))]
    return {"id": "speak_truth_steady", "title": "Synthetic steady voices at known pitch", "profile": USER,
            "takes": "real", "beats": beats}


def speak_truth_glide():
    lo, hi, secs = GLIDE
    tr = synth.glide_track(lo, hi, secs)
    truth = track_truth(tr, "glide", synth.NEUTRAL_FORMANTS, start_hz=lo, end_hz=hi, seconds=secs)
    return {"id": "speak_truth_glide", "title": "A known glide 120 to 260 Hz", "profile": USER, "takes": "real",
            "beats": [feed("dashboard-glide", voice(tr, synth.NEUTRAL_FORMANTS), truth=truth), go("live", False),
                      feed("live-glide", voice(tr, synth.NEUTRAL_FORMANTS, seed=1), truth=truth)]}


def _wav(path, seconds):
    audio, rate = sf.read(str(path), dtype="float64", always_2d=True)
    audio = audio[:, 0]
    return audio[:int(seconds * rate)] if rate == SAMPLE_RATE else audio[:0]


def speak_real_replay():
    beats = []
    for i, wav in enumerate(sorted(driver.REAL_TAKES.glob("*.wav"))):
        stem = wav.stem
        beats.append(beat(f"replay-{i + 1:02d}", "replay_take", stem=stem, seconds_max=REPLAY_SECONDS,
                          truth={"kind": "replay", "take": stem, "replayed_seconds": REPLAY_SECONDS}))
    return {"id": "speak_real_replay", "title": "Each real take fed live: what you saw while speaking it",
            "profile": USER, "takes": "real", "beats": beats}


def record_take():
    tr = synth.speech_track(165, 4.0, seed=5)
    truth = track_truth(tr, "speech", synth.NEUTRAL_FORMANTS, center_hz=165)
    return {"id": "record_take", "title": "Record, analyse, the take appears", "profile": USER, "takes": "real",
            "beats": [beat("record-start", "record_start", capture=False),
                      feed("recording", voice(tr, synth.NEUTRAL_FORMANTS, seed=5, level=USER_DBFS), truth=truth),
                      beat("record-stop", "record_stop", instant=True),
                      beat("take-ready", "wait_jobs"), go("takes")]}


def noisy_input():
    tr = synth.speech_track(170, 6.0, seed=7)
    clean = lambda: synth.synth_voice(synth.with_silence(tr, 0.6, 0.6), synth.NEUTRAL_FORMANTS, seed=7)  # noqa: E731
    return {"id": "noisy_input", "title": "Voice with desk rumble, whine, clipping", "profile": USER,
            "takes": "real", "beats": [
                feed("rumble", lambda: synth.mix(with_room(clean()), synth.rumble(7.2, -40.0)),
                     truth=track_truth(tr, "speech", noise="rumble")),
                feed("whine", lambda: synth.mix(with_room(clean()), synth.whine(7.2, dbfs=-50.0)),
                     truth=track_truth(tr, "speech", noise="whine")),
                feed("clipping", lambda: with_room(synth.clipped(clean(), 36.0)),
                     truth=track_truth(tr, "speech", noise="clipping")),
                feed("quiet-after", lambda: synth.room_noise(4.0), truth={"noise": "room", "voiced": []})]}


def no_mic():
    return {"id": "no_mic", "title": "The mic couldn't be opened", "profile": USER, "takes": "real",
            "truth": {"mic": "unavailable"}, "beats": [
                beat("mic-error", "fake_mic_error"), go("live"), go("takes"), go("settings")]}


def mic_lost():
    """phase 11 / 05: the stream stops delivering audio (unplugged), then comes back."""
    tr = synth.steady_track(150, 1.5)
    return {"id": "mic_lost", "title": "The mic stops sending audio, then returns", "profile": USER, "takes": "real",
            "truth": {"mic": "lost"}, "beats": [
                feed("hearing", voice(tr, synth.NEUTRAL_FORMANTS)), beat("lost", "call", target="core.mark_mic_lost"),
                go("live"), beat("back", "call", target="core.mark_mic_back")]}


def reanalysis_running():
    return {"id": "reanalysis_running", "title": "Re-analysing older takes in the background", "profile": USER,
            "takes": "real", "beats": [
                beat("started", "start_reanalysis", count=3, instant=True), go("takes"),
                beat("one-done", "wait_event", event="take_updated"), beat("all-done", "wait_jobs")]}


def sing_truth_scale():
    tr = synth.scale_track(SCALE_START_MIDI)
    vib = synth.vibrato_track(*VIBRATO[:1], 3.0, VIBRATO[1], VIBRATO[2])
    return {"id": "sing_truth_scale", "title": "Sing mode: a known scale and a held note with vibrato",
            "profile": USER, "takes": "real", "beats": [
                beat("sing-mode", "set_mode", mode="sing"),
                feed("scale", voice(tr, synth.NEUTRAL_FORMANTS), truth=track_truth(
                    tr, "scale", synth.NEUTRAL_FORMANTS, start_midi=SCALE_START_MIDI)),
                go("live", False),
                feed("vibrato", voice(vib, synth.NEUTRAL_FORMANTS, seed=2), truth=track_truth(
                    vib, "vibrato", synth.NEUTRAL_FORMANTS, f0=VIBRATO[0], rate_hz=VIBRATO[1],
                    extent_cents=VIBRATO[2])),
                beat("speak-mode", "set_mode", mode="speak", capture=False)]}


VOWEL_WORDS_FORMANTS = [  # (F1, F2) Hz for eight vowels, roughly Peterson & Barney's women (from memory, unverified)
    (310, 2790), (430, 2480), (610, 2330), (860, 2050), (850, 1220), (590, 920), (470, 1160), (370, 950)]


def _vowel_words():
    parts = [synth.room_noise(0.8, seed=11)]
    for i, (f1, f2) in enumerate(VOWEL_WORDS_FORMANTS):
        forms = [(f1, 90), (f2, 110), (2900, 140), (3900, 180), (4800, 220)]
        parts.append(with_room(synth.steady(200, 0.7, forms, seed=i), seed=20 + i))
        parts.append(synth.room_noise(1.0, seed=40 + i))
    return np.concatenate(parts)


def practice_each():
    speech = synth.speech_track(170, 4.0, seed=9)
    say = voice(speech, synth.NEUTRAL_FORMANTS, seed=9)
    beats = [go("practice")]
    for key in ("read", "phrases"):
        beats += [beat(f"{key}-open", "open_practice", key=key),
                  beat(f"{key}-start", "call", target=f"practice.{key}.toggle"),
                  feed(f"{key}-mid", say, truth=track_truth(speech, "speech")),
                  beat(f"{key}-stop", "call", target=f"practice.{key}.toggle", instant=True),
                  beat(f"{key}-finished", "wait_jobs")]
    beats += [beat("vowels-open", "open_practice", key="vowels"),
              beat("vowels-start", "call", target="practice.vowel_check.session.start"),
              feed("vowels-mid", lambda: _vowel_words()[:int(6.0 * SAMPLE_RATE)]),
              feed("vowels-rest", lambda: _vowel_words()[int(6.0 * SAMPLE_RATE):], capture=False),
              beat("vowels-finished", "wait_jobs"),
              beat("exercise-open", "open_practice", key="exercise"),
              beat("exercise-start", "call", target="practice.exercise.start"),
              beat("exercise-sung", "exercise_sing"),
              beat("exercise-finished", "wait_jobs"),
              beat("voicemap-open", "open_practice", key="voicemap"),
              feed("voicemap-singing", voice(synth.scale_track(52, steps=tuple(range(0, 16, 2)), note_seconds=0.5),
                                             synth.NEUTRAL_FORMANTS, seed=4)),
              beat("voicemap-rebuild", "call", target="voice_map.rebuild_requested.emit", instant=True),
              beat("voicemap-rebuilt", "wait_jobs"),
              beat("close", "close_practice")]
    return {"id": "practice_each", "title": "Each practice opened, started, mid-run and finished",
            "profile": USER, "takes": "real", "beats": beats}


def takes_report():
    beats = [go("takes")]
    for i in range(len(list(driver.REAL_TAKES.glob("*.wav")))):
        beats += [beat(f"take-{i + 1:02d}", "show_take", capture=False, index=i),
                  beat(f"take-{i + 1:02d}-details", "expand_all", root="takes/full")]
    return {"id": "takes_report", "title": "Every real take's full report, Details expanded", "profile": USER,
            "takes": "real", "beats": beats}


def progress():
    return {"id": "progress", "title": "Progress with the real takes", "profile": USER, "takes": "real",
            "beats": [go("progress"), go("dashboard")]}


def settings():
    return {"id": "settings", "title": "Every settings section", "profile": USER, "takes": "real", "beats": [
        go("settings"), beat("mic-check", "click", path="settings/mic_check_btn"), beat("closed", "dismiss"),
        beat("light-theme", "theme", theme="light", capture=False), go("settings", capture=True),
        beat("dark-theme", "theme", theme="dark", capture=False)]}


def error_status():
    return {"id": "error_status", "title": "A core error and warning", "profile": USER, "takes": "real",
            "beats": [beat("error", "inject_status", text="Couldn't save the take: [Errno 28] No space left on device",
                           level="error", instant=True),
                      beat("warning", "inject_status", level="warning", instant=True,
                           text="This take is too noisy to set targets from (too much background noise). "
                                "Record a cleaner one."),
                      beat("busy-line", "inject_status", text="Re-analysing your takes with the improved analysis: "
                                                               "1 of 11…", level="busy", instant=True),
                      beat("mic-warning", "inject_status", text="Couldn't open the mic: device busy", level="error",
                           instant=True)]}


def reach():
    from tools.uilab.features import FEATURES
    return {"id": "reach", "title": "Every feature reached from the dashboard", "profile": USER, "takes": "real",
            "beats": [beat(f["id"], "reach", variants="home", feature=f["id"]) for f in FEATURES]}


SCENARIOS = {f.__name__: f for f in (first_run, empty, listening_silence, speak_truth_steady, speak_truth_glide,
                                      speak_real_replay, record_take, noisy_input, no_mic, reanalysis_running,
                                      sing_truth_scale, practice_each, takes_report, progress, settings,
                                      error_status, reach, mic_lost)}
GROUPS = {
    "live": ["empty", "listening_silence", "speak_truth_steady", "speak_truth_glide", "speak_real_replay",
             "record_take", "noisy_input", "sing_truth_scale"],
    "takes": ["empty", "record_take", "takes_report", "progress", "reanalysis_running"],
    "shell": ["first_run", "no_mic", "error_status", "settings", "reanalysis_running", "noisy_input", "reach",
              "mic_lost", "record_take", "listening_silence"],
    "practice": ["practice_each", "sing_truth_scale"],
}
SMOKE = ["empty", "speak_truth_steady", "record_take", "no_mic", "error_status"]
SMOKE_SIZE, SMOKE_THEME = (1280, 720), "dark"


def build(names):
    """Scenario dicts for names ("all" for every one), in SCENARIOS order."""
    if names in ("all", None):
        names = list(SCENARIOS)
    out = []
    for name in names:
        if name not in SCENARIOS:
            raise KeyError(f"unknown scenario {name!r}; known: {', '.join(SCENARIOS)}")
        sc = SCENARIOS[name]()
        sc["groups"] = [g for g, members in GROUPS.items() if name in members]
        out.append(sc)
    return out


def group_scenarios(group):
    """The scenarios a group runs: its own, plus the smoke set (so its baseline covers what the test sees)."""
    names = list(GROUPS[group])
    return names + [n for n in SMOKE if n not in names]


def replay_audio(stem, seconds_max=REPLAY_SECONDS, folder=None):
    return _wav(Path(folder or driver.REAL_TAKES) / f"{stem}.wav", seconds_max)
