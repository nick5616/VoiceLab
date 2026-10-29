"""The live session (what the live view shows, as data), driven with synthetic frames and audio. No Qt:
the assertions that used to read the live panel's widgets read the session's snapshot here."""
import os
import sys

import numpy as np
import pytest

sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
import coach  # noqa: E402
import display  # noqa: E402
import live_session  # noqa: E402
import mic_noise  # noqa: E402
from analysis import SAMPLE_RATE  # noqa: E402
from engine import UPDATE_INTERVAL, LiveEngine  # noqa: E402
from live_session import COACH_EVERY, LiveSession, VerdictTracker  # noqa: E402
from profile_store import Profile  # noqa: E402
from test_analysis import MALE_FORMANTS, synth_vowel  # noqa: E402

SR = SAMPLE_RATE
HOP = int(UPDATE_INTERVAL * SR)


@pytest.fixture
def session(tmp_path):
    live = LiveSession(Profile(tmp_path / "home"), "speak", engine=LiveEngine())
    live.text_interval = 0                  # live text is throttled by wall clock; tests run fast
    return live


def frame(f0=140.0, delta_f=1000.0, h1h2=5.0, cpp=20.0, ring=-15.0, level=-30.0):
    """A live frame as FrameAnalyzer.analyze makes it, with the given measurements."""
    return {"level": level, "band_level": level, "clipped": False, "f0": f0, "delta_f": delta_f,
            "h1h2": h1h2, "cpp": cpp, "ring": ring, "formants": np.array([500, 1500, 2500, 3500.0]),
            "resonance_reliable": True, "spectrum": np.full(10, -90.0)}


def feed_frames(live, frames):
    snapshot = None
    for f in frames:
        snapshot = live.update(f)
    return snapshot


def feed_audio(live, audio, every=1):
    """Feed audio through the engine and its analyzer, one live frame per hop."""
    engine, snapshot = live.engine, None
    for n, i in enumerate(range(0, len(audio) - HOP + 1, HOP)):
        engine.feed(audio[i:i + HOP])
        if n % every == 0:
            snapshot = live.update(engine.analyzer.analyze(engine.latest_window()))
    return snapshot


def voice(seconds, dbfs=None, f0=140):
    audio = synth_vowel(f0, MALE_FORMANTS, seconds=seconds)
    return mic_noise.at_level(audio, dbfs) if dbfs is not None else audio


# --- STABLE VERDICTS ---

def test_verdict_tracker_needs_voice_then_holds_with_hysteresis():
    tracker = VerdictTracker()
    target = [100.0, 200.0]
    assert [tracker.update(150.0, target) for _ in range(live_session.VERDICT_MIN_FRAMES - 1)] == [None] * 11
    assert tracker.update(150.0, target) is True
    assert tracker.update(150.0, None) is None                     # no target, no verdict
    for _ in range(80):
        tracker.update(150.0, target)
    for _ in range(60):                                            # drifting just outside: inside the margin
        assert tracker.update(205.0, target) is True
    for _ in range(80):
        state = tracker.update(260.0, target)
    assert state is False
    for _ in range(80):                                            # just inside the edge (190, within the 15 margin of 200) is not enough
        state = tracker.update(190.0, target)
    assert state is False
    for _ in range(80):
        state = tracker.update(150.0, target)
    assert state is True
    for _ in range(live_session.VERDICT_RESET_FRAMES):             # a long pause forgets
        tracker.update(None, target)
    assert tracker.state is None


def test_robust_center_does_not_jump_between_two_vowel_clusters():
    low, high = [940.0] * 26, [1060.0] * 34            # 43% / 57%: the median would be 1060
    assert 990 < live_session.robust_center(low + high) < 1040
    assert live_session.robust_center([1.0, 2.0, 3.0, 100.0]) == 2.5


def test_verdict_does_not_flip_on_vowel_alternation(session):
    """/a/ and /i/ alternate around the target: each is outside it, the voice as a whole is inside."""
    session.profile.set_target("delta_f", [970, 1030])
    session.refresh_profile()
    rng = np.random.default_rng(1)
    verdicts, per_frame = [], []
    for n in range(240):                                            # 6 s, a vowel every 0.35 s
        value = (940 if (n // 14) % 2 == 0 else 1060) + rng.normal(0, 12)
        snapshot = session.update(frame(delta_f=value))
        verdicts.append(snapshot["metrics"]["delta_f"]["verdict"])
        per_frame.append(970 <= value <= 1030)
    verdicts = [v for v in verdicts if v is not None]
    flips = sum(a != b for a, b in zip(verdicts, verdicts[1:]))
    frame_flips = sum(a != b for a, b in zip(per_frame, per_frame[1:]))
    assert flips <= 1 and frame_flips > 5 and verdicts[-1] is True
    assert "✓ on target" in snapshot["metrics"]["delta_f"]["title"]


# --- NOTE READOUT AND VERDICTS ---

def note(session):
    session.note_shown_at = 0.0
    return session.update(frame(f0=session.recent_f0[-1]))["note"]


def test_note_is_on_target_only_when_pitch_and_resonance_both_are(session):
    session.profile.set_target("pitch", [130, 150])
    session.profile.set_target("delta_f", [1100, 1200])
    session.refresh_profile()
    snapshot = feed_frames(session, [frame(f0=140, delta_f=1000)] * 100)     # pitch on target, resonance not
    assert snapshot["metrics"]["pitch"]["verdict"] is True                   # the pitch bar is judged alone
    assert snapshot["metrics"]["delta_f"]["verdict"] is False
    assert not note(session)["on_target"]
    feed_frames(session, [frame(f0=140, delta_f=1150)] * 100)               # both on target
    assert note(session)["on_target"]
    feed_frames(session, [frame(f0=170, delta_f=1150)] * 100)               # resonance on target, pitch not
    assert not note(session)["on_target"]
    assert "Hz" in note(session)["text"]


def test_note_with_a_single_target_follows_that_target(session):
    session.profile.set_target("pitch", [130, 150])
    session.refresh_profile()
    feed_frames(session, [frame(f0=140)] * 60)
    assert note(session)["on_target"]


def test_no_verdicts_without_targets(session):
    snapshot = feed_frames(session, [frame(delta_f=1350, cpp=10)] * 30)
    for key in ("delta_f", "cpp", "h1h2", "pitch"):
        assert snapshot["metrics"][key]["verdict"] is None and snapshot["metrics"][key]["position"] is not None


def test_note_is_readable_and_held_through_short_gaps(tmp_path):
    clock = [1000.0]
    live = LiveSession(Profile(tmp_path / "home"), "speak", clock=lambda: clock[0])
    texts = []
    for n in range(200):                                            # 5 s: voice, a 0.2 s gap, voice
        clock[0] += UPDATE_INTERVAL
        f0 = np.nan if 80 <= n < 88 else 100 + n
        text = live.update(frame(f0=f0))["note"]["text"]
        if not texts or texts[-1] != text:
            texts.append(text)
    assert len(texts) <= 80                 # a 19 st glide: a change per 0.25 st at most (spec 07), not one per frame
    assert "—" not in texts[1:]                                     # a 0.2 s gap holds the note


# --- UNTRUSTED READINGS ---

def test_noise_makes_untrusted_readings_unavailable(session):
    noisy = mic_noise.at_level(voice(1.6), -42) + mic_noise.mic_noise(int(1.6 * SR))   # SNR about 16 dB
    feed_audio(session, np.concatenate([mic_noise.mic_noise(int(1.5 * SR)), noisy, mic_noise.mic_noise(SR)]))
    snapshot = feed_audio(session, np.concatenate([noisy, mic_noise.mic_noise(int(0.5 * SR))]))
    assert session.snr is not None and 10 < session.snr < 20
    assert snapshot["input"]["snr"] == session.snr
    for key in ("cpp", "h1h2"):                                      # need 20 dB (coach.CPP_MIN_SNR)
        reading = snapshot["metrics"][key]
        assert reading["unavailable"] == "too noisy" and reading["position"] is None
        assert "—" in reading["title"] and "noisy" in reading["title"]
    assert snapshot["metrics"]["delta_f"]["unavailable"] is None     # formants need only 10 dB


def test_clean_voice_shows_every_reading(session):
    quiet = 1e-4 * np.random.default_rng(0).standard_normal(int(1.5 * SR))
    snapshot = feed_audio(session, np.concatenate([quiet, voice(1.5, -25), quiet, voice(1.5, -25)]))
    for key in ("cpp", "h1h2", "delta_f"):
        reading = snapshot["metrics"][key]
        assert "—" not in reading["title"] and reading["unavailable"] is None and reading["position"] is not None


def test_too_high_for_formants_gives_a_reason(session):
    snapshot = feed_frames(session, [dict(frame(f0=420), resonance_reliable=False)] * 20)
    reading = snapshot["metrics"]["delta_f"]
    assert reading["unavailable"] == "pitch too high to measure" and "pitch too high" in reading["title"]
    assert snapshot["plane"]["delta_f"] is None and snapshot["plane"]["f0"] is not None


def test_light_heavy_reading_is_cautioned_at_high_pitch(session):
    snapshot = feed_frames(session, [frame(f0=300)] * 20)
    assert f"rough above {coach.FORMANT_MAX_F0:.0f} Hz" in snapshot["metrics"]["h1h2"]["title"]
    snapshot = feed_frames(session, [frame(f0=140)] * 20)
    assert "rough" not in snapshot["metrics"]["h1h2"]["title"]


def test_labels_say_what_is_measured(session):
    metrics = session.snapshot()["metrics"]
    assert metrics["h1h2"]["label"] == "Heavy ↔ Light (H1*–H2*)"
    assert metrics["cpp"]["label"] == "Clarity (CPP, noise-sensitive)"
    assert metrics["pitch_range"]["label"] == "Intonation Range (last 6 s)"
    assert metrics["register"]["label"] == "Register (estimate)"
    assert "apparent" in display.format_value("delta_f", 1000)
    for key, definition in display.METRIC_DEFS.items():
        assert definition[1] < definition[2], key


# --- MODES AND SCALES ---

def test_scales_and_meters_follow_the_mode(session):
    speak_position = display.fraction("pitch", 200, "speak")
    assert display.fraction("pitch", 200, "sing") < speak_position - 0.15     # 80-400 Hz becomes 60-1500 Hz
    assert display.fraction("pitch", 1500, "sing") == 1.0
    assert display.fraction("pitch", 400, "speak") == 1.0 and display.fraction("pitch", 80, "speak") == 0.0
    assert display.metric_scale("delta_f", "speak")[:2] == (800, 1400)
    session.set_mode("sing")
    assert session.engine.analyzer.mode == "sing"
    visible = {key for key, reading in session.snapshot()["metrics"].items() if reading["visible"]}
    assert "ring" in visible and "delta_f" not in visible and "cq" not in visible
    snapshot = feed_frames(session, [frame(f0=200)] * 5)
    assert abs(snapshot["metrics"]["pitch"]["position"] - display.fraction("pitch", 200, "sing")) < 0.01
    session.set_mode("speak")
    assert session.engine.analyzer.mode == "speak"
    session.set_egg(True)
    assert session.snapshot()["metrics"]["cq"]["visible"] and session.snapshot()["egg"]["on"]


# --- SINGING ---

def sing_frames(cents_offsets, base_midi=69.0):
    return [frame(f0=440 * 2 ** ((base_midi - 69) / 12 + c / 1200)) for c in cents_offsets]


def test_tuning_stays_on_target_through_vibrato_around_the_target(session):
    session.set_mode("sing")
    session.target_provider = lambda: 69
    for phase in (0.0, 0.7, 1.9, 3.1):                                # the verdict mustn't depend on phase
        session.recent_cents.clear()
        t = np.arange(80) * UPDATE_INTERVAL
        verdicts, shown = [], []
        for f in sing_frames(50 * np.sin(2 * np.pi * 5.5 * t + phase)):
            reading = session.update(f)["metrics"]["tuning"]
            verdicts.append(reading["verdict"])
            shown.append(reading["position"])
        assert set(verdicts[10:]) == {True}, phase
        assert reading["marker"] is not None and reading["band_kind"] == "in_tune"
    assert np.ptp(shown[10:]) < 0.35                                  # the bar is calmer than the +-50 swing


def test_tuning_is_off_when_the_note_is_really_off(session):
    session.set_mode("sing")
    session.target_provider = lambda: 69
    assert feed_frames(session, sing_frames([40] * 30))["metrics"]["tuning"]["verdict"] is False
    assert feed_frames(session, sing_frames([5] * 30))["metrics"]["tuning"]["verdict"] is True


def test_tuning_without_a_target_uses_the_nearest_note(session):
    session.set_mode("sing")
    reading = feed_frames(session, sing_frames([-12] * 20, base_midi=62))["metrics"]["tuning"]
    assert "from D" in reading["title"] and reading["verdict"] is True


def test_register_zone_and_mix_band(session):
    reading = session.snapshot()["metrics"]["register"]
    assert reading["band"] == (0.35, 0.65) and reading["band_kind"] == "mix"
    assert live_session.register_zone(0.1) == "chest" and live_session.register_zone(0.5) == "mix"
    assert live_session.register_zone(0.9) == "head"


# --- INPUT BADGE ---

def test_input_badge_names_the_whine(session):
    noise = mic_noise.mic_noise(int(7 * SR))
    audio = noise + np.concatenate([np.zeros(int(2 * SR)), voice(2.5, -30), np.zeros(int(2.5 * SR))])[:len(noise)]
    snapshot = feed_audio(session, audio)
    assert snapshot["input"]["badge"] == "whine" and snapshot["input"]["severity"] == "warn"
    assert "mic check" in snapshot["input"]["tooltip"] and "whine" in snapshot["input"]["issues"]


def test_input_badge_says_quiet_for_a_quiet_voice(session):
    quiet = 1e-4 * np.random.default_rng(0).standard_normal(int(2 * SR))
    snapshot = feed_audio(session, np.concatenate([quiet, voice(2.5, -50), quiet, voice(1.5, -50)]))
    assert "quiet" in snapshot["input"]["badge"] or "SNR" in snapshot["input"]["badge"]
    session._update_quality()
    assert session.live_level is not None and session.live_level < -40
    session._update_noise_report()
    assert "quiet: move closer" in session.noise_issues()


def test_noise_report_feeds_the_badge(session):
    noise = mic_noise.mic_noise(int(5 * SR))
    info = session.noise_report(noise, SR)
    assert info["lines"] and session.snapshot()["input"]["badge"] == "whine"


def test_slow_capture_path_is_flagged(session):
    session.engine.capture_info = {"path": "default", "host_api": "MME", "latency": 0.09}
    session.refresh_input()
    info = session.snapshot()["input"]
    assert info["slow_path"] and "MME, 90 ms latency" in info["slow_path_tooltip"]
    session.engine.capture_info = {"path": "wasapi", "host_api": "Windows WASAPI", "latency": 0.01}
    session.refresh_input()
    assert not session.snapshot()["input"]["slow_path"]


def test_clipping_and_the_egg_signal(session):
    snapshot = session.update(dict(frame(), clipped=True))
    assert snapshot["input"]["clipping"]
    snapshot = feed_frames(session, [frame()] * int(live_session.CLIP_BADGE_SECONDS / UPDATE_INTERVAL))
    assert not snapshot["input"]["clipping"]
    session.set_egg(True)
    assert not session.snapshot()["egg"]["signal"]
    snapshot = session.update(dict(frame(), cq=0.5))
    assert snapshot["egg"]["signal"] and abs(snapshot["metrics"]["cq"]["value"] - 0.5) < 1e-9
    assert "measured" in snapshot["egg"]["tooltip"]


# --- COACH LINE ---

def test_coach_line_waits_for_voice_and_updates_slowly(session):
    assert "Speak or sing" in session.snapshot()["coach"]["line"]
    session.profile.set_target("pitch", [130, 150])
    session.profile.set_target("delta_f", [1100, 1200])
    session.refresh_profile()
    changes = []
    for n in range(4 * COACH_EVERY):                                  # 8 s
        line = session.update(frame(f0=140, delta_f=1000))["coach"]["line"]
        if not changes or changes[-1] != line:
            changes.append(line)
    assert 2 <= len(changes) <= 4
    coach = session.snapshot()["coach"]
    assert "resonance" in coach["line"] and "darker" in coach["line"] and "you" in coach["line"].lower()
    assert coach["full"]


def test_coach_line_in_poor_input_explains_instead_of_judging(session):
    for n in range(2 * COACH_EVERY):
        session.update(frame(f0=140, level=-55))
    session.quality = None
    text, _ = session.coach_text()
    assert "move closer" in text.lower()


# --- LIVE CARDS, BANNER AND THE PITCH AXIS (spec 05) ---

def test_live_cards_are_plain_and_follow_the_mode(session):
    snapshot = feed_frames(session, [frame(f0=200, delta_f=1100)] * 30)
    cards = live_session.live_cards(snapshot, session.profile)
    assert [c["key"] for c in cards] == ["pitch", "delta_f", "h1h2", "cpp", "pitch_range", "register"]
    titles = {c["key"]: c["title"] for c in cards}
    assert titles["delta_f"] == "Resonance" and titles["h1h2"] == "Weight"      # display.NAMES (audit A-052)
    assert titles["pitch"] == "Pitch · now" and titles["pitch_range"] == "Intonation · last 6 s"
    assert all("H1" not in c["title"] and "CPP" not in c["title"] for c in cards)      # jargon in the help
    assert "H1*–H2*" in live_session.CARD_HELP["h1h2"] and "CPP" in live_session.CARD_HELP["cpp"]
    pitch = cards[0]
    assert pitch["text"] == "200 Hz" and abs(pitch["value"] - 200) < 2 and pitch["unavailable"] is None
    assert live_session.card_scale("pitch")["zones"][1]["id"] == "overlap"
    assert live_session.card_scale("h1h2")["zones"] == []                    # no gender zones for weight
    session.set_mode("sing")
    session.set_egg(True)
    keys = [c["key"] for c in live_session.live_cards(session.snapshot(), session.profile)]
    assert keys == ["tuning", "vibrato", "register", "ring", "cpp", "h1h2", "cq"]


def test_card_text_is_throttled_like_the_titles(tmp_path):
    clock = [0.0]
    live = LiveSession(Profile(tmp_path / "home"), "speak", clock=lambda: clock[0])
    shown = []
    for i in range(20):
        clock[0] += 0.025
        snapshot = live.update(frame(f0=150 + i))
        shown.append(snapshot["metrics"]["pitch"]["shown"])
    assert len(set(shown)) <= 8      # every 0.2 s, or sooner when the voice has moved 0.25 st (spec 07: the readout tracks)


def test_noisy_readings_go_to_one_banner(session):
    noisy = mic_noise.at_level(voice(1.6), -42) + mic_noise.mic_noise(int(1.6 * SR))   # SNR about 16 dB
    feed_audio(session, np.concatenate([mic_noise.mic_noise(int(1.5 * SR)), noisy, mic_noise.mic_noise(SR)]))
    snapshot = feed_audio(session, np.concatenate([noisy, mic_noise.mic_noise(int(0.5 * SR))]))
    banner = snapshot["banner"]
    assert banner and banner["target"] == "mic_check" and "weight and clarity" in banner["text"]
    cards = {c["key"]: c for c in live_session.live_cards(snapshot, session.profile)}
    assert cards["cpp"]["noisy"] and cards["h1h2"]["noisy"] and not cards["delta_f"]["noisy"]


def test_banner_names_the_problem_and_what_to_do(session):
    assert session.snapshot()["banner"] is None
    snapshot = session.update(dict(frame(), clipped=True))
    assert "clipping" in snapshot["banner"]["text"] and snapshot["banner"]["action"] == "Mic check"
    feed_frames(session, [frame()] * int(live_session.CLIP_BADGE_SECONDS / UPDATE_INTERVAL))
    session.noise_report(mic_noise.mic_noise(int(5 * SR)), SR)
    assert session.snapshot()["banner"]["text"].startswith("A steady whine")
    session.noise_info = {}
    session.engine.capture_info = {"path": "default", "host_api": "MME", "latency": 0.09}
    session.refresh_input()
    assert session.snapshot()["banner"] == {"text": live_session.SLOW_PATH_BANNER, "kind": "info",
                                             "action": "Settings", "target": "settings"}


def test_note_carries_the_pitch_the_graph_draws(session):
    snapshot = feed_frames(session, [frame(f0=230)] * 20)
    assert abs(snapshot["note"]["hz"] - 230) < 1 and "230 Hz" in snapshot["note"]["text"]


def test_speak_axis_never_moves_and_sing_axis_has_hysteresis():
    assert live_session.pitch_axis("speak") == (70.0, 400.0)
    assert live_session.pitch_axis("speak", (100, 200), [600.0] * 40) == (70.0, 400.0)
    axis = live_session.pitch_axis("sing", None, (), (57, 69))             # A3-A4 from the Voice Map
    assert abs(axis[0] - 196.0) < 1 and abs(axis[1] - 493.9) < 1          # padded by 2 semitones
    assert live_session.pitch_axis("sing", axis, [300.0] * 40) == axis       # inside: no change
    assert live_session.pitch_axis("sing", axis, [600.0] * 5 + [300.0]) == axis   # a blip: no change
    wider = live_session.pitch_axis("sing", axis, [600.0] * 40)
    assert wider[0] == axis[0] and wider[1] > 600 * 2 ** (1.9 / 12)
    default = live_session.pitch_axis("sing")
    assert abs(default[0] - 116.5) < 1 and abs(default[1] - 587.3) < 1     # C3-C5, padded


# --- LISTENING STATE, STALE READINGS, IN-RANGE SESSION, ONE "NOW" (phase 11 / 03) ---

import synth  # noqa: E402

FRAMES_PER_SECOND = int(round(1 / UPDATE_INTERVAL))
SILENT = dict(f0=np.nan, delta_f=np.nan, h1h2=np.nan, cpp=np.nan, ring=np.nan, level=-75.0)


def voiced(seconds, **kwargs):
    return [frame(**kwargs)] * int(seconds * FRAMES_PER_SECOND)


def silent(seconds, **kwargs):
    return [dict(frame(**dict(SILENT, **{k: v for k, v in kwargs.items() if k != "too_quiet"})),
                 **({"too_quiet": True} if kwargs.get("too_quiet") else {}))] * int(seconds * FRAMES_PER_SECOND)


def test_one_pitch_everywhere_on_every_frame(session):
    """The readout, the Pitch card and the plane's dot are the same number (audit A-002, lab B1)."""
    for f0 in np.geomspace(100, 260, 160):
        snapshot = session.update(frame(f0=float(f0)))
        hz = snapshot["note"]["hz"]
        card = next(c for c in live_session.live_cards(snapshot, session.profile) if c["key"] == "pitch")
        assert hz is not None and card["text"] == f"{hz:.0f} Hz"
        assert snapshot["plane"]["f0"] == hz and snapshot["metrics"]["pitch"]["value"] == hz


def test_state_follows_the_voice(session):
    assert session.snapshot()["state"] == "silent"                        # open mic, nothing said yet
    assert feed_frames(session, voiced(1.0))["state"] == "hearing"
    assert feed_frames(session, silent(0.3))["state"] == "hearing"        # a short gap holds
    assert feed_frames(session, silent(0.4))["state"] == "silent"
    session.signal_age = lambda: 3.0                                      # the mic stopped delivering audio
    assert session.update(frame())["state"] == "no_signal"
    session.signal_age = lambda: None                                     # no heartbeat to ask: never "no signal"
    assert session.update(frame())["state"] == "hearing"


def test_a_quiet_voice_is_too_quiet_and_silence_is_not(session):
    audio = np.concatenate([synth.room_noise(2.0, dbfs=-75), synth.mix(synth.at_level(synth.steady(150, 4.0), -52),
                                                                         synth.room_noise(4.0, dbfs=-75))])
    seen = []
    for i in range(0, len(audio) - HOP + 1, HOP):
        session.engine.feed(audio[i:i + HOP])
        seen.append(session.update(session.engine.analyzer.analyze(session.engine.latest_window()))["state"])
    onset = int(2.0 / UPDATE_INTERVAL)
    assert set(seen[:onset]) == {"silent"}                                 # room noise alone stays silent
    assert "too_quiet" in seen[onset:onset + int(2.0 / UPDATE_INTERVAL)]    # within 2 s of the voice
    assert feed_frames(session, silent(2.0, level=-30.0))["state"] == "silent"   # loud unpitched noise is not a quiet voice
    assert feed_frames(session, silent(2.0, too_quiet=True))["state"] == "too_quiet"   # the analyser's own flag counts


def test_readings_age_then_empty_in_silence(session):
    """Stale after 1.5 s (muted), empty after 5 s: no card keeps a voice's number forever (audit A-001)."""
    snapshot = feed_frames(session, voiced(2.5))
    assert not any(r["stale"] for r in snapshot["metrics"].values()) and snapshot["metrics"]["pitch"]["age"] == 0
    snapshot = feed_frames(session, silent(1.6))
    for key in ("pitch", "delta_f", "h1h2", "cpp"):
        assert snapshot["metrics"][key]["stale"] and snapshot["metrics"][key]["value"] is not None
    assert snapshot["metrics"]["level"]["stale"] is False                  # the input level is always current
    card = next(c for c in live_session.live_cards(snapshot, session.profile) if c["key"] == "cpp")
    assert card["stale"] and abs(card["age"] - 1.6) < 0.1 and live_session.age_text(card["age"]) == "last heard 2 s ago"
    snapshot = feed_frames(session, silent(3.6))
    assert all(snapshot["metrics"][k]["value"] is None for k in ("pitch", "delta_f", "h1h2", "cpp", "pitch_range"))
    cards = live_session.live_cards(snapshot, session.profile)
    assert all(c["text"] == "—" and c["unavailable"] == "listening…" for c in cards if c["key"] != "register")


def test_held_time_in_range_and_the_session_share(tmp_path):
    clock = [0.0]
    ticking = lambda: clock.__setitem__(0, clock[0] + UPDATE_INTERVAL) or clock[0]     # one frame per call
    live = LiveSession(Profile(tmp_path / "home"), "speak", clock=ticking)
    live.text_interval = 0
    live.set_targets({"pitch": [120.0, 160.0], "delta_f": [900.0, 1100.0]})
    snapshot = feed_frames(live, voiced(3.0, f0=140.0, delta_f=1000.0))
    assert snapshot["note"]["on_target"] and 1.5 < snapshot["held_seconds"] <= 3.0
    longer = feed_frames(live, voiced(2.0, f0=140.0, delta_f=1000.0))
    assert longer["held_seconds"] > snapshot["held_seconds"] + 1.9         # it keeps counting
    snapshot = feed_frames(live, voiced(4.0, f0=300.0, delta_f=1000.0))    # out of range: the stretch ends
    assert snapshot["held_seconds"] == 0 and not snapshot["note"]["on_target"]
    share = snapshot["in_range_share"]
    assert 0.3 < share < 0.7 and 0.3 < snapshot["session"]["last_minute_share"] < 0.7   # about 5 s of 9
    assert "In range" in snapshot["session"]["line"] and "of the last minute" in snapshot["session"]["line"]
    assert snapshot["session"]["trend"]["kind"] == "in_range" and snapshot["session"]["trend"]["values"]
    live.reset_session()
    assert live.snapshot()["in_range_share"] is None


def test_without_a_target_the_session_says_so_and_trends_pitch(session):
    snapshot = feed_frames(session, voiced(2.0, f0=150.0))
    assert snapshot["held_seconds"] == 0 and snapshot["in_range_share"] is None
    assert snapshot["session"]["has_target"] is False and snapshot["session"]["line"] == live_session.TARGET_PROMPT
    trend = snapshot["session"]["trend"]
    assert trend["kind"] == "pitch" and abs(trend["values"][-1] - 150) < 2


def test_the_share_compares_the_first_minutes_with_now(tmp_path, monkeypatch):
    monkeypatch.setattr(live_session, "FIRST_FRAMES", 20 * FRAMES_PER_SECOND)      # "the first minutes" shortened to 20 s
    live = LiveSession(Profile(tmp_path / "home"), "speak")
    live.text_interval = 0
    live.set_targets({"pitch": [120.0, 160.0]})
    feed_frames(live, voiced(20.0, f0=300.0))
    snapshot = feed_frames(live, voiced(30.0, f0=140.0))
    assert snapshot["session"]["first_share"] == 0.0 and snapshot["session"]["last_minute_share"] > 0.3
    assert "was 0% in your first 5 minutes" in snapshot["session"]["line"]


def test_coach_respects_the_goal_and_shows_what_to_try(tmp_path):
    def line_for(goal):
        profile = Profile(tmp_path / f"home-{goal}")
        profile.voice_goal = goal
        live = LiveSession(profile, "speak")
        live.text_interval = 0
        feed_frames(live, voiced(8.0, f0=150.0, cpp=22.0))                # flat pitch, a good input
        return live.coach_text()
    line, full = line_for("feminine")
    assert "livelier" in full and "Try" in line                           # what happened, then what to try
    for goal in (None, "masculine"):                                      # exploring and masculine: no push to more melody
        line, full = line_for(goal)
        assert not any(w in full for w in ("livelier", "rise and fall", "good news"))
    flat = {"pitch": 150.0, "pitch_range": 1.0}
    assert coach.advice(flat, {}, goal="masculine") == [m for m in coach.advice(flat, {}) if m["key"] != "flat_intonation"]


def test_coach_never_names_a_reading_the_mode_has_no_card_for():
    pressed = {"key": "possible_pressing", "text": "Your pitch, weight and ring all went up together in this take. "
                                                    "That can mean some squeezing. Try a loose sigh."}
    speak = LiveSession._plain_tip(pressed, "speak")
    assert "ring" not in speak.lower() and speak.startswith("Your pitch and voice weight") and "squeezing" in speak
    assert LiveSession._plain_tip(dict(pressed, key="other"), "speak") == ""      # no rewrite: left out
    resonance = {"key": "cant_judge", "text": "I can't judge your brightness (resonance) in this take."}
    assert LiveSession._plain_tip(resonance, "sing") == ""
    assert "resonance" in LiveSession._plain_tip(resonance, "speak")
    assert live_session.coach_line("A happened. Then B. Try C. Try D.") == "A happened. Try C."
    assert live_session.coach_line("Only one.") == "Only one."


def test_cards_say_the_word_first_and_the_number_second(session):
    snapshot = feed_frames(session, voiced(2.0, h1h2=-1.9, cpp=29.7, delta_f=1100))
    cards = {c["key"]: c for c in live_session.live_cards(snapshot, session.profile)}
    assert (cards["h1h2"]["text"], cards["h1h2"]["number"]) == ("Heavier", "-1.9 dB")
    assert (cards["cpp"]["text"], cards["cpp"]["number"]) == ("Clear", "29.7 dB")
    assert cards["pitch"]["text"] == "140 Hz" and cards["pitch"]["number"] == "Masculine"      # the zone, in the sheet's short word
    assert cards["delta_f"]["title"] == "Resonance" and cards["register"]["text"] in ("Chest voice", "Mix", "Head voice")
    assert live_session.card_words("pitch_range", dict(value=4.3, position=4.3 / 12)) == ("Moderate", "4.3 semitones")
    assert live_session.card_words("pitch_range", dict(value=1.0, position=1 / 12))[0] == "Flat"
    assert cards["h1h2"]["trust"] == "medium" and cards["h1h2"]["trust_word"] == "Rough"     # weight is a rough guide
    assert cards["pitch"]["trust"] == "high" and cards["pitch"]["trust_word"] == "Reliable"
    assert not any(c["text"].endswith((" dB", " st")) for c in cards.values())    # never a bare unit as the headline


def test_an_input_problem_shows_on_the_cards_it_touches(session):
    feed_frames(session, voiced(1.0))
    session.update(dict(frame(), clipped=True))
    snapshot = feed_frames(session, voiced(0.2))
    cards = {c["key"]: c for c in live_session.live_cards(snapshot, session.profile)}
    assert snapshot["banner"] and cards["cpp"]["trust"] == "medium" and "clipping" in cards["cpp"]["trust_reason"]
    assert cards["pitch"]["trust"] == "high"                               # clipping doesn't touch the pitch


def test_state_strip_text_differs_for_every_state():
    texts = {live_session.state_strip(s)["text"] for s in ("no_signal", "silent", "too_quiet", "hearing")}
    assert len(texts) == 4 and live_session.state_strip("hearing")["kind"] == "success"
    assert live_session.state_strip("hearing", recording=True)["state"] == "recording"
    assert live_session.state_strip("hearing", mic_state="error")["state"] == "no_signal"   # a lost mic wins
    assert live_session.state_strip("silent", mic_state="lost", recording=True)["kind"] == "error"


def test_note_in_words_and_the_sing_prompt():
    assert live_session.note_words(220.0) == "A3, right on"
    assert live_session.note_words(226.0) == "A3, 47 cents sharp"
    assert live_session.note_words(214.0) == "A3, 48 cents flat"
    assert live_session.sing_prompt(None) == "Sing any note" and live_session.sing_prompt(57) == "Sing A3"


def test_the_legend_explains_the_green_outline_and_the_baseline(tmp_path):
    profile = Profile(tmp_path / "home")
    assert live_session.legend_text(profile) == ""
    profile.targets = {"pitch": [120.0, 160.0]}
    assert live_session.legend_text(profile) == "Green outline = your target range"
    profile.baseline = {"pitch": 150.0, "recorded": "2026-10-23 09:20"}
    assert live_session.legend_text(profile) == "Green outline = your target range · dashed line = your baseline (Fri 23 Oct)"


def test_next_step_for_the_dashboard():
    assert live_session.next_step(0)["action"] == "record" and live_session.next_step(3)["action"] == "practice"
    assert live_session.next_step(3, recording=True)["action"] is None


def test_readout_tracks_a_moving_voice_within_a_quarter_semitone(tmp_path):
    clock = [0.0]
    live = LiveSession(Profile(tmp_path / "home"), "speak", clock=lambda: clock.__setitem__(0, clock[0] + UPDATE_INTERVAL) or clock[0])
    track, worst = [], 0.0
    for n in range(160):
        track.append(150 * 2 ** (np.sin(n / 12) * 3 / 12))              # +-3 st swings, like speech
        hz = live.update(frame(f0=track[-1]))["note"]["hz"]
        median = np.median(track[-int(live_session.NOTE_WINDOW_SECONDS / UPDATE_INTERVAL):])
        worst = max(worst, abs(12 * np.log2(hz / median)))
    assert worst < live_session.NOTE_JUMP_SEMITONES + 0.01              # sample-and-hold was up to ~1.1 st off
