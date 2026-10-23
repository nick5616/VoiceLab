"""The live panel and mic check dialog (spec 06), driven offscreen with synthetic frames and audio. The
panel draws the core's live session (its logic is tested without Qt in test_live_session.py); these
check that what it decides is what the widgets show.

No audio hardware: the mic check runs against a fake engine, and panels get frames through a
LiveEngine that is fed audio by hand and never opens a stream.
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
from analysis import SAMPLE_RATE  # noqa: E402
from app_core import VoiceLabCore  # noqa: E402
from engine import UPDATE_INTERVAL, LiveEngine  # noqa: E402
from profile_store import Profile  # noqa: E402
from test_analysis import MALE_FORMANTS, synth_vowel  # noqa: E402
from ui import meters  # noqa: E402
from ui.live_panel import LivePanel  # noqa: E402
from ui.meters import NEUTRAL_COLOR, OFF_TARGET_COLOR, ON_TARGET_COLOR  # noqa: E402
from ui.mic_check_dialog import MicCheckDialog  # noqa: E402

SR = SAMPLE_RATE
HOP = int(UPDATE_INTERVAL * SR)
GREEN = ON_TARGET_COLOR
SPECTRUM_BINS = len(LiveEngine().analyzer.spectrum_freqs)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app, tmp_path):
    core = VoiceLabCore(Profile(tmp_path / "home"), engine=LiveEngine())
    core.live.text_interval = 0             # live text is throttled by wall clock; tests run fast
    live = LivePanel(core, lambda: None)
    live.apply_mode("speak")
    return live


def frame(f0=140.0, delta_f=1000.0, h1h2=5.0, cpp=20.0, ring=-15.0, level=-30.0):
    """A live frame as FrameAnalyzer.analyze makes it, with the given measurements."""
    return {"level": level, "band_level": level, "clipped": False, "f0": f0, "delta_f": delta_f,
            "h1h2": h1h2, "cpp": cpp, "ring": ring, "formants": np.array([500, 1500, 2500, 3500.0]),
            "resonance_reliable": True, "spectrum": np.full(SPECTRUM_BINS, -90.0)}


def show(live, f):
    live.show_frame(f, live.core.on_frame(f)["live"])


def feed_frames(live, frames):
    for f in frames:
        show(live, f)
    QApplication.processEvents()


def feed_audio(live, audio):
    """Feed audio through the engine and its analyzer, one live frame per hop."""
    engine = live.core.engine
    for i in range(0, len(audio) - HOP + 1, HOP):
        engine.feed(audio[i:i + HOP])
        show(live, engine.analyzer.analyze(engine.latest_window()))
    QApplication.processEvents()


def voice(seconds, dbfs=None, f0=140):
    audio = synth_vowel(f0, MALE_FORMANTS, seconds=seconds)
    return mic_noise.at_level(audio, dbfs) if dbfs is not None else audio


# --- BAR COLOURS AND THE NOTE ---

def test_bars_are_neutral_without_targets_and_never_red(panel):
    assert meters.bar_color(0.1, None) == meters.bar_color(0.9, None) == NEUTRAL_COLOR
    feed_frames(panel, [frame(delta_f=1350, cpp=10)] * 30)
    for key in ("delta_f", "cpp", "h1h2", "pitch"):
        assert panel.meters[key].bar.color == NEUTRAL_COLOR
    assert not hasattr(meters, "BAR_COLORS")


def test_verdicts_colour_the_bars_and_the_note(panel):
    panel.core.set_targets({"pitch": [130, 150], "delta_f": [1100, 1200]})
    feed_frames(panel, [frame(f0=140, delta_f=1000)] * 100)          # pitch on target, resonance not
    assert panel.meters["pitch"].bar.color == GREEN                  # the pitch bar itself is judged alone
    assert panel.meters["delta_f"].bar.color == OFF_TARGET_COLOR
    assert panel.meters["pitch"].bar.band is not None
    assert GREEN not in panel.note_label.styleSheet()
    feed_frames(panel, [frame(f0=140, delta_f=1150)] * 100)         # both on target
    panel.core.live.note_shown_at = 0.0
    feed_frames(panel, [frame(f0=140, delta_f=1150)])
    assert GREEN in panel.note_label.styleSheet() and "Hz" in panel.note_label.text()


# --- UNTRUSTED READINGS ---

def test_noise_hatches_the_readings_it_makes_unreliable(panel):
    noisy = mic_noise.at_level(voice(1.6), -42) + mic_noise.mic_noise(int(1.6 * SR))   # SNR about 16 dB
    feed_audio(panel, np.concatenate([mic_noise.mic_noise(int(1.5 * SR)), noisy, mic_noise.mic_noise(SR)]))
    feed_audio(panel, np.concatenate([noisy, mic_noise.mic_noise(int(0.5 * SR))]))
    for key in ("cpp", "h1h2"):                                      # need 20 dB (coach.CPP_MIN_SNR)
        meter = panel.meters[key]
        assert "—" in meter.title.text() and "noisy" in meter.title.text()
        assert meter.bar.position is None and meter.bar.hatched
    assert not panel.meters["delta_f"].bar.hatched                   # formants need only 10 dB


def test_too_high_for_formants_shows_a_reason(panel):
    feed_frames(panel, [dict(frame(f0=420), resonance_reliable=False)] * 20)
    meter = panel.meters["delta_f"]
    assert "pitch too high" in meter.title.text() and meter.bar.hatched


# --- MODES AND SINGING ---

def test_meters_and_tabs_follow_the_mode(panel):
    panel.core.set_mode("sing")
    assert panel.mode == "sing" and panel.mode_buttons["sing"].isChecked()
    assert panel.meters["delta_f"].isHidden() and not panel.meters["ring"].isHidden()
    assert panel.read_btn.isHidden() and panel.core.engine.analyzer.mode == "sing"
    panel.mode_buttons["speak"].click()
    assert panel.core.mode == "speak" and not panel.meters["delta_f"].isHidden()


def sing_frames(cents_offsets, base_midi=69.0):
    return [frame(f0=440 * 2 ** ((base_midi - 69) / 12 + c / 1200)) for c in cents_offsets]


def test_tuning_stays_green_on_vibrato_and_shows_the_raw_pitch(panel):
    panel.core.set_mode("sing")
    panel.core.live.target_provider = lambda: 69
    t = np.arange(80) * UPDATE_INTERVAL
    colors = []
    for f in sing_frames(50 * np.sin(2 * np.pi * 5.5 * t)):
        show(panel, f)
        colors.append(panel.meters["tuning"].bar.color)
    assert set(colors[10:]) == {GREEN} and panel.meters["tuning"].bar.marker is not None
    feed_frames(panel, sing_frames([40] * 30))
    assert panel.meters["tuning"].bar.color == OFF_TARGET_COLOR


# --- BADGES AND THE COACH LINE ---

def test_noise_badge_names_the_whine_and_opens_the_mic_check(panel):
    noise = mic_noise.mic_noise(int(7 * SR))
    audio = noise + np.concatenate([np.zeros(int(2 * SR)), voice(2.5, -30), np.zeros(int(2.5 * SR))])[:len(noise)]
    feed_audio(panel, audio)
    assert panel.noise_badge.text() == "whine", panel.noise_badge.text()
    assert OFF_TARGET_COLOR in panel.noise_badge.styleSheet()
    assert "mic check" in panel.noise_badge.toolTip()
    panel.noise_badge.click()
    assert isinstance(panel.mic_dialog, MicCheckDialog) and panel.mic_dialog.isVisible()
    panel.mic_dialog.reject()


def test_slow_capture_path_is_flagged(panel):
    panel.core.engine.capture_info = {"path": "default", "host_api": "MME", "latency": 0.09}
    panel.core.live.refresh_input()
    feed_frames(panel, [frame()])
    assert panel.path_badge.isVisibleTo(panel) and "MME" in panel.path_badge.toolTip()
    panel.core.engine.capture_info = {"path": "wasapi", "host_api": "Windows WASAPI", "latency": 0.01}
    panel.core.live.refresh_input()
    feed_frames(panel, [frame()])
    assert not panel.path_badge.isVisibleTo(panel)


def test_clipping_badge(panel):
    feed_frames(panel, [dict(frame(), clipped=True)])
    assert not panel.clip_badge.isHidden()
    feed_frames(panel, [frame()] * 40)
    assert panel.clip_badge.isHidden()


def test_coach_line_shows_the_sessions_advice(panel):
    assert "Speak or sing" in panel.coach_label.text()
    panel.core.set_targets({"pitch": [130, 150], "delta_f": [1100, 1200]})
    changes = []
    set_text = panel.coach_label.setText
    panel.coach_label.setText = lambda text: (changes.append(text), set_text(text))
    feed_frames(panel, [frame(f0=140, delta_f=1000)] * 200)
    assert 1 <= len(changes) <= 3                                     # every 2 s at most, only when it changes
    assert "resonance" in panel.coach_label.text() and "darker" in panel.coach_label.text()
    assert panel.coach_label.toolTip()


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
    dialog = MicCheckDialog(engine, seconds=0.05, lead_seconds=0.02)
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
    assert dialog.status.text() == "Done" and not result["whine"] and not result["rumble"]


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
    dialog = MicCheckDialog(busy)
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
    dialog = MicCheckDialog(engine, seconds=5, lead_seconds=0.01)
    dialog.start()
    end = time.perf_counter() + 5
    while dialog.phase != "silence" and time.perf_counter() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    assert engine.capturing
    dialog.reject()
    assert not engine.capturing and dialog.result is None
