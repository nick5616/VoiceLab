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
    assert len(texts) <= 5 / 0.2 + 2                                # at most ~5 changes a second
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
