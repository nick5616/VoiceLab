"""The live panel and mic check (spec 06), driven offscreen with synthetic frames and audio.

No audio hardware: the mic check runs against a fake engine, and panels get frames directly or
through a LiveEngine that is fed audio by hand and never opens a stream.
"""
import os
import sys
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
import sounddevice as sd  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import mic_check  # noqa: E402
import mic_noise  # noqa: E402
import ui_common  # noqa: E402
from analysis import SAMPLE_RATE  # noqa: E402
from engine import UPDATE_INTERVAL, LiveEngine  # noqa: E402
from live_panel import COACH_EVERY, LivePanel  # noqa: E402
from profile_store import Profile  # noqa: E402
from test_analysis import MALE_FORMANTS, synth_vowel  # noqa: E402
from ui_common import NEUTRAL_COLOR, OFF_TARGET_COLOR, ON_TARGET_COLOR, VerdictTracker  # noqa: E402

SR = SAMPLE_RATE
HOP = int(UPDATE_INTERVAL * SR)
GREEN = ON_TARGET_COLOR


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app, tmp_path):
    engine = LiveEngine()
    live = LivePanel(engine.analyzer.spectrum_freqs, Profile(tmp_path / "home"), lambda: None)
    live.engine = engine
    live.window_provider = engine.latest_window
    live.set_mode("speak")
    for meter in live.meters.values():
        meter.text_interval = 0                 # live text is throttled by wall clock; tests run fast
    return live


def frame(f0=140.0, delta_f=1000.0, h1h2=5.0, cpp=20.0, ring=-15.0, level=-30.0):
    """A live frame as FrameAnalyzer.analyze makes it, with the given measurements."""
    return {"level": level, "band_level": level, "clipped": False, "f0": f0, "delta_f": delta_f,
            "h1h2": h1h2, "cpp": cpp, "ring": ring, "formants": np.array([500, 1500, 2500, 3500.0]),
            "resonance_reliable": True, "spectrum": np.full(len(LiveEngine().analyzer.spectrum_freqs), -90.0)}


def feed_frames(live, frames):
    for f in frames:
        live.update_frame(f)
    QApplication.processEvents()


def feed_audio(live, audio, every=1):
    """Feed audio through the engine and its analyzer, one live frame per hop."""
    engine = live.engine
    for n, i in enumerate(range(0, len(audio) - HOP + 1, HOP)):
        engine.feed(audio[i:i + HOP])
        if n % every == 0:
            live.update_frame(engine.analyzer.analyze(engine.latest_window()))
    QApplication.processEvents()


def voice(seconds, dbfs=None, f0=140):
    audio = synth_vowel(f0, MALE_FORMANTS, seconds=seconds)
    return mic_noise.at_level(audio, dbfs) if dbfs is not None else audio


# --- STABLE VERDICTS ---

def test_verdict_tracker_needs_voice_then_holds_with_hysteresis():
    tracker = VerdictTracker()
    target = [100.0, 200.0]
    assert [tracker.update(150.0, target) for _ in range(ui_common.VERDICT_MIN_FRAMES - 1)] == [None] * 11
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
    for _ in range(ui_common.VERDICT_RESET_FRAMES):                # a long pause forgets
        tracker.update(None, target)
    assert tracker.state is None


def test_robust_center_does_not_jump_between_two_vowel_clusters():
    low, high = [940.0] * 26, [1060.0] * 34            # 43% / 57%: the median would be 1060
    assert 990 < ui_common.robust_center(low + high) < 1040
    assert ui_common.robust_center([1.0, 2.0, 3.0, 100.0]) == 2.5


def test_verdict_does_not_flip_on_vowel_alternation(panel):
    """/a/ and /i/ alternate around the target: each is outside it, the voice as a whole is inside."""
    panel.profile.set_target("delta_f", [970, 1030])
    panel.refresh_profile()
    rng = np.random.default_rng(1)
    verdicts, per_frame = [], []
    for n in range(240):                                            # 6 s, a vowel every 0.35 s
        value = (940 if (n // 14) % 2 == 0 else 1060) + rng.normal(0, 12)
        panel.update_frame(frame(delta_f=value))
        verdicts.append(panel.verdicts["delta_f"].state)
        per_frame.append(970 <= value <= 1030)
    verdicts = [v for v in verdicts if v is not None]
    flips = sum(a != b for a, b in zip(verdicts, verdicts[1:]))
    frame_flips = sum(a != b for a, b in zip(per_frame, per_frame[1:]))
    assert flips <= 1 and frame_flips > 5 and verdicts[-1] is True
    assert panel.meters["delta_f"].bar.color == GREEN


# --- NOTE READOUT AND BAR COLOURS ---

def note_color(panel):
    panel.note_shown_at = 0.0
    panel.update_frame(frame(f0=panel.recent_f0[-1]))
    return panel.note_label.styleSheet()


def test_note_is_green_only_when_pitch_and_resonance_are_both_on_target(panel):
    panel.profile.set_target("pitch", [130, 150])
    panel.profile.set_target("delta_f", [1100, 1200])
    panel.refresh_profile()
    feed_frames(panel, [frame(f0=140, delta_f=1000)] * 100)         # pitch on target, resonance not
    assert panel.meters["pitch"].bar.color == GREEN                  # the pitch bar itself is judged alone
    assert panel.meters["delta_f"].bar.color == OFF_TARGET_COLOR
    assert GREEN not in note_color(panel)
    feed_frames(panel, [frame(f0=140, delta_f=1150)] * 100)         # both on target
    assert GREEN in note_color(panel)
    feed_frames(panel, [frame(f0=170, delta_f=1150)] * 100)         # resonance on target, pitch not
    assert GREEN not in note_color(panel)


def test_note_with_a_single_target_follows_that_target(panel):
    panel.profile.set_target("pitch", [130, 150])
    panel.refresh_profile()
    feed_frames(panel, [frame(f0=140)] * 60)
    assert GREEN in note_color(panel)


def test_bars_are_neutral_without_targets_and_never_red(panel):
    assert ui_common.bar_color(0.1, None) == ui_common.bar_color(0.9, None) == NEUTRAL_COLOR
    feed_frames(panel, [frame(delta_f=1350, cpp=10)] * 30)
    for key in ("delta_f", "cpp", "h1h2", "pitch"):
        assert panel.meters[key].bar.color == NEUTRAL_COLOR
    assert not hasattr(ui_common, "BAR_COLORS")


# --- UNTRUSTED READINGS ---

def test_noise_hides_the_readings_it_makes_unreliable(panel):
    noisy = mic_noise.at_level(voice(1.6), -42) + mic_noise.mic_noise(int(1.6 * SR))   # SNR about 16 dB
    feed_audio(panel, np.concatenate([mic_noise.mic_noise(int(1.5 * SR)), noisy, mic_noise.mic_noise(SR)]))
    feed_audio(panel, np.concatenate([noisy, mic_noise.mic_noise(int(0.5 * SR))]))
    assert panel.snr is not None and 10 < panel.snr < 20
    for key in ("cpp", "h1h2"):                                      # need 20 dB (coach.CPP_MIN_SNR)
        meter = panel.meters[key]
        assert "—" in meter.title.text() and "noisy" in meter.title.text()
        assert meter.bar.position is None and meter.bar.hatched
    assert not panel.meters["delta_f"].bar.hatched                   # formants need only 10 dB


def test_clean_voice_shows_every_reading(panel):
    quiet = 1e-4 * np.random.default_rng(0).standard_normal(int(1.5 * SR))
    feed_audio(panel, np.concatenate([quiet, voice(1.5, -25), quiet, voice(1.5, -25)]))
    for key in ("cpp", "h1h2", "delta_f"):
        meter = panel.meters[key]
        assert "—" not in meter.title.text() and not meter.bar.hatched and meter.bar.position is not None


def test_too_high_for_formants_shows_a_reason(panel):
    feed_frames(panel, [dict(frame(f0=420), resonance_reliable=False)] * 20)
    meter = panel.meters["delta_f"]
    assert "pitch too high" in meter.title.text() and meter.bar.hatched


def test_light_heavy_reading_is_cautioned_above_250_hz(panel):
    feed_frames(panel, [frame(f0=300)] * 20)
    assert "rough above 250 Hz" in panel.meters["h1h2"].title.text()
    feed_frames(panel, [frame(f0=140)] * 20)
    assert "rough" not in panel.meters["h1h2"].title.text()


def test_labels_say_what_is_measured(panel):
    assert panel.meters["h1h2"].label == "Heavy ↔ Light (H1*–H2*)"
    assert panel.meters["cpp"].label == "Clarity (CPP, noise-sensitive)"
    assert "apparent" in ui_common.format_value("delta_f", 1000)
    for key, definition in ui_common.METRIC_DEFS.items():
        assert definition[1] < definition[2], key


# --- MODES AND SCALES ---

def test_scales_and_meters_follow_the_mode(panel):
    pitch, ring = panel.meters["pitch"], panel.meters["ring"]
    speak_position = pitch._fraction(200)
    panel.set_mode("sing")
    assert pitch._fraction(200) < speak_position - 0.15               # 80-400 Hz becomes 60-1500 Hz
    assert pitch._fraction(1500) == 1.0
    assert panel.meters["delta_f"].modes == {"speak"} and ring.modes == {"sing"}
    panel.set_mode("speak")
    assert pitch._fraction(400) == 1.0 and pitch._fraction(80) == 0.0
    assert panel.engine.analyzer.mode == "speak"
    assert panel.meters["delta_f"].lo == 800 and panel.meters["delta_f"].hi == 1400


# --- SINGING ---

def sing_frames(cents_offsets, base_midi=69.0):
    return [frame(f0=440 * 2 ** ((base_midi - 69) / 12 + c / 1200)) for c in cents_offsets]


def test_tuning_stays_green_on_vibrato_around_the_target(panel):
    panel.set_mode("sing")
    panel.target_provider = lambda: 69
    for phase in (0.0, 0.7, 1.9, 3.1):                                # the colour mustn't depend on phase
        panel.recent_cents.clear()
        t = np.arange(80) * UPDATE_INTERVAL
        colors, shown = [], []
        for f in sing_frames(50 * np.sin(2 * np.pi * 5.5 * t + phase)):
            panel.update_frame(f)
            colors.append(panel.meters["tuning"].bar.color)
            shown.append(panel.meters["tuning"].bar.position)
        assert set(colors[10:]) == {GREEN}, phase
        raw = panel.meters["tuning"].bar.marker
        assert raw is not None
    assert np.ptp(shown[10:]) < 0.35                                  # the bar is calmer than the +-50 swing


def test_tuning_goes_amber_when_the_note_is_really_off(panel):
    panel.set_mode("sing")
    panel.target_provider = lambda: 69
    feed_frames(panel, sing_frames([40] * 30))
    assert panel.meters["tuning"].bar.color == OFF_TARGET_COLOR
    feed_frames(panel, sing_frames([5] * 30))
    assert panel.meters["tuning"].bar.color == GREEN


def test_tuning_without_a_target_uses_the_nearest_note(panel):
    panel.set_mode("sing")
    feed_frames(panel, sing_frames([-12] * 20, base_midi=62))
    meter = panel.meters["tuning"]
    assert "from D" in meter.title.text() and meter.bar.color == GREEN


# --- NOISE BADGE ---

def test_noise_badge_names_the_whine_and_opens_the_mic_check(panel):
    engine = panel.engine
    noise = mic_noise.mic_noise(int(7 * SR))
    audio = noise + np.concatenate([np.zeros(int(2 * SR)), voice(2.5, -30), np.zeros(int(2.5 * SR))])[:len(noise)]
    feed_audio(panel, audio, every=1)
    assert panel.noise_badge.text() == "whine", panel.noise_badge.text()
    assert "mic check" in panel.noise_badge.toolTip()
    assert engine is panel.engine
    panel.noise_badge.click()
    assert isinstance(panel.mic_dialog, mic_check.MicCheckDialog) and panel.mic_dialog.isVisible()
    panel.mic_dialog.reject()


def test_noise_badge_says_quiet_for_a_quiet_voice(panel):
    quiet = 1e-4 * np.random.default_rng(0).standard_normal(int(2 * SR))
    feed_audio(panel, np.concatenate([quiet, voice(2.5, -50), quiet, voice(1.5, -50)]))
    assert "quiet" in panel.noise_badge.text() or "SNR" in panel.noise_badge.text()
    panel._update_quality()
    assert panel.live_level is not None and panel.live_level < -40
    panel._update_noise_report()
    assert "quiet: move closer" in panel.noise_issues()


def test_slow_capture_path_is_flagged(panel):
    panel.engine.capture_info = {"path": "default", "host_api": "MME", "latency": 0.09}
    panel._update_noise_badge()
    assert not panel.path_badge.isHidden() or panel.path_badge.isVisibleTo(panel)
    panel.engine.capture_info = {"path": "wasapi", "host_api": "Windows WASAPI", "latency": 0.01}
    panel._update_noise_badge()
    assert not panel.path_badge.isVisibleTo(panel)


# --- COACH LINE ---

def test_coach_line_waits_for_voice_and_updates_slowly(panel):
    assert "Speak or sing" in panel.coach_label.text()
    panel.profile.set_target("pitch", [130, 150])
    panel.profile.set_target("delta_f", [1100, 1200])
    panel.refresh_profile()
    changes = []
    set_text = panel.coach_label.setText
    panel.coach_label.setText = lambda text: (changes.append(text), set_text(text))
    for n in range(4 * COACH_EVERY):                                  # 8 s
        panel.update_frame(frame(f0=140, delta_f=1000))
    assert len(changes) <= 4 and len(changes) >= 3
    assert "resonance" in panel.coach_label.text() and "darker" in panel.coach_label.text()
    assert "you" in panel.coach_label.text().lower()
    assert panel.coach_label.toolTip()


def test_coach_line_in_poor_input_explains_instead_of_judging(panel):
    for n in range(2 * COACH_EVERY):
        panel.update_frame(frame(f0=140, level=-55))
    panel.quality = None
    text, _ = panel.coach_text()
    assert "move closer" in text.lower()


# --- ENGINE ---

def test_engine_mode_noise_floor_and_recent_audio(app):
    engine = LiveEngine()
    engine.set_mode("sing")
    assert engine.analyzer.mode == "sing" and engine.noise_floor() is None
    engine.analyzer.noise_floor = -57.0
    assert engine.noise_floor() == -57.0
    assert len(engine.recent_audio()) == 0
    ramp = np.arange(int(8 * SR)) / SR
    for i in range(0, len(ramp), 512):
        engine.feed(ramp[i:i + 512])
    recent = engine.recent_audio(2.0)
    assert len(recent) == 2 * SR and np.allclose(recent, ramp[-2 * SR:])
    assert len(engine.recent_audio()) == 6 * SR


def test_open_stream_seeds_the_noise_floor(app, monkeypatch):
    opened = []

    class Stream:
        latency = 0.01

        def __init__(self, **kwargs):
            opened.append(kwargs)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass
    import engine as engine_module
    monkeypatch.setattr(engine_module.sd, "InputStream", Stream)
    monkeypatch.setattr(engine_module, "_find_wasapi", lambda device: None)
    monkeypatch.setattr(engine_module, "_host_api_name", lambda device: "MME")
    engine = LiveEngine()
    engine.open_stream(None, noise_floor=-58.5)
    assert engine.noise_floor() == -58.5 and opened
    engine.open_stream(None)
    assert engine.noise_floor() == -58.5                             # not reset by reopening


# --- MIC CHECK ---

class FakeEngine:
    """The mic check's view of the engine, with scripted recordings and no hardware."""
    sample_rate = SR

    def __init__(self, silence, ah, capture_info=None):
        self.takes = [silence, ah]
        self.capture_info = capture_info or {"path": "wasapi", "host_api": "Windows WASAPI",
                                             "native_rate": 48000, "latency": 0.01}
        self.capturing = False
        self.started = 0

    def is_capturing(self):
        return self.capturing

    def start_take(self):
        self.capturing = True
        self.started += 1

    def stop_take(self):
        self.capturing = False
        return self.takes.pop(0)


def forbid_hardware(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the mic check opened an audio stream")
    monkeypatch.setattr(sd, "InputStream", refuse)
    monkeypatch.setattr(sd, "rec", refuse)


def run_check(app, engine):
    dialog = mic_check.MicCheckDialog(engine, seconds=0.05, lead_seconds=0.02)
    dialog.start()
    end = time.perf_counter() + 10
    while dialog.result is None and time.perf_counter() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    assert dialog.result is not None
    return dialog


def test_mic_check_advises_moving_closer_for_quiet_input(app, monkeypatch):
    forbid_hardware(monkeypatch)
    quiet_floor = 10 ** (-70 / 20) * np.random.default_rng(1).standard_normal(3 * SR)
    ah = mic_noise.at_level(voice(3.0), -55) + 10 ** (-70 / 20) * np.random.default_rng(2).standard_normal(3 * SR)
    dialog = run_check(app, FakeEngine(quiet_floor, ah))
    result = dialog.result
    assert result["voice_db"] < -50 and not result["ok"]
    assert result["advice"][0] == mic_check.DISTANCE_ADVICE
    assert "10-15 cm" in result["advice"][0] and "input level" in result["advice"][0]
    assert "10-15 cm" in dialog.report.text() and dialog.start_btn.text() == "Run again"
    assert not result["whine"] and not result["rumble"]


def test_mic_check_names_the_whine_of_the_users_mic(app, monkeypatch):
    forbid_hardware(monkeypatch)
    floor = mic_noise.mic_noise(3 * SR, seed=1)
    ah = mic_noise.mic_noise(3 * SR, seed=2) + mic_noise.at_level(voice(3.0), -28)
    result = run_check(app, FakeEngine(floor, ah)).result
    assert result["whine"] and not result["ok"], result
    assert any(abs(hz - 4000) < 20 for hz, _ in result["lines"])
    assert mic_check.WHINE_ADVICE in result["advice"] and mic_check.ENHANCEMENTS_ADVICE in result["advice"]
    assert mic_check.DISTANCE_ADVICE not in result["advice"]          # the voice is loud enough
    assert result["native_rate"] == 48000 and abs(result["latency_ms"] - 10) < 1e-6


def test_mic_check_names_desk_rumble(app, monkeypatch):
    forbid_hardware(monkeypatch)
    rng = np.random.default_rng(3)
    from scipy import signal
    sos = signal.butter(4, 100, "lp", fs=SR, output="sos")
    rumble = 0.004 * signal.sosfilt(sos, rng.standard_normal(3 * SR)) / 0.1
    floor = rumble + 10 ** (-75 / 20) * rng.standard_normal(3 * SR)
    ah = rumble + mic_noise.at_level(voice(3.0), -28)
    result = run_check(app, FakeEngine(floor, ah)).result
    assert result["rumble"] and mic_check.RUMBLE_ADVICE in result["advice"]
    assert "rumble" in " ".join(mic_check.summary_lines(result))


def test_mic_check_on_a_clean_input_is_happy_and_flags_the_slow_path(app, monkeypatch):
    forbid_hardware(monkeypatch)
    floor = 10 ** (-70 / 20) * np.random.default_rng(1).standard_normal(3 * SR)
    ah = mic_noise.at_level(voice(3.0), -25) + 10 ** (-70 / 20) * np.random.default_rng(2).standard_normal(3 * SR)
    good = run_check(app, FakeEngine(floor, ah)).result
    assert good["ok"] and good["advice"] == [mic_check.GOOD_ADVICE]
    slow = mic_check.analyze_check(floor, ah, SR, {"path": "default", "host_api": "MME", "latency": 0.09})
    assert mic_check.PATH_ADVICE in slow["advice"] and not slow["ok"]


def test_mic_check_hears_no_voice_and_refuses_during_a_take(app, monkeypatch):
    forbid_hardware(monkeypatch)
    silent = 10 ** (-80 / 20) * np.random.default_rng(1).standard_normal(3 * SR)
    result = run_check(app, FakeEngine(silent, silent.copy())).result
    assert result["advice"] == [mic_check.NO_VOICE_ADVICE]
    busy = FakeEngine(silent, silent)
    busy.capturing = True
    dialog = mic_check.MicCheckDialog(busy)
    dialog.start()
    assert busy.started == 0 and "recording" in dialog.status.text() and dialog.result is None


def test_panel_stores_and_emits_the_mic_check_result(panel, monkeypatch):
    forbid_hardware(monkeypatch)
    floor = 10 ** (-70 / 20) * np.random.default_rng(1).standard_normal(3 * SR)
    ah = mic_noise.at_level(voice(3.0), -25) + 10 ** (-70 / 20) * np.random.default_rng(2).standard_normal(3 * SR)
    panel.engine = FakeEngine(floor, ah)
    received = []
    panel.mic_check_done.connect(received.append)
    dialog = panel.show_mic_check()
    dialog.seconds, dialog.lead_seconds = 0.05, 0.02
    dialog.start()
    end = time.perf_counter() + 10
    while not received and time.perf_counter() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    assert received and panel.last_mic_check is received[0] and "advice" in received[0]


def test_closing_the_mic_check_midway_discards_the_recording(app):
    engine = FakeEngine(np.zeros(10), np.zeros(10))
    dialog = mic_check.MicCheckDialog(engine, seconds=5, lead_seconds=0.01)
    dialog.start()
    end = time.perf_counter() + 5
    while dialog.phase != "silence" and time.perf_counter() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    assert engine.capturing
    dialog.reject()
    assert not engine.capturing and dialog.result is None

