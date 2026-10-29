"""The live view (spec 05: readout, graph, live cards) and the mic check dialog, driven offscreen with
synthetic frames and audio. The view draws the core's live session (its logic is tested without Qt in
test_live_session.py); these check that what it decides is what the widgets show.

No audio hardware: the mic check runs against a fake engine, and views get frames through a
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
from PyQt6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import mic_check  # noqa: E402
import mic_noise  # noqa: E402
from analysis import SAMPLE_RATE  # noqa: E402
from app_core import VoiceLabCore  # noqa: E402
from engine import UPDATE_INTERVAL, LiveEngine  # noqa: E402
from profile_store import Profile  # noqa: E402
from test_analysis import MALE_FORMANTS, synth_vowel  # noqa: E402
from ui import theme  # noqa: E402
from ui.live_cards import NOISY_TEXT  # noqa: E402
from ui.mic_check_dialog import MicCheckDialog  # noqa: E402
from ui.screens.live import LiveView  # noqa: E402

SR = SAMPLE_RATE
HOP = int(UPDATE_INTERVAL * SR)
SPECTRUM_BINS = len(LiveEngine().analyzer.spectrum_freqs)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app, tmp_path):
    core = VoiceLabCore(Profile(tmp_path / "home"), engine=LiveEngine())
    core.live.text_interval = 0             # live text is throttled by wall clock; tests run fast
    theme.set_reduce_motion(True)           # as in conftest's make_window: no tween outlives the view
    live = LiveView(core)
    yield live
    # Delete it now, in the event loop: left to the garbage collector, pyqtgraph items can be collected
    # in the middle of building the next view.
    live.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    theme.set_reduce_motion(False)


def frame(f0=140.0, delta_f=1000.0, h1h2=5.0, cpp=20.0, ring=-15.0, level=-30.0):
    """A live frame as FrameAnalyzer.analyze makes it, with the given measurements."""
    return {"level": level, "band_level": level, "clipped": False, "f0": f0, "delta_f": delta_f,
            "h1h2": h1h2, "cpp": cpp, "ring": ring, "formants": np.array([500, 1500, 2500, 3500.0]),
            "resonance_reliable": True, "spectrum": np.full(SPECTRUM_BINS, -90.0)}


def show(live, f):
    snapshot = live.core.on_frame(f)["live"]
    live.show_snapshot(f, snapshot)
    return snapshot


def feed_frames(live, frames):
    for f in frames:
        snapshot = show(live, f)
    QApplication.processEvents()
    return snapshot


def feed_audio(live, audio):
    """Feed audio through the engine and its analyzer, one live frame per hop."""
    engine, snapshot = live.core.engine, None
    for i in range(0, len(audio) - HOP + 1, HOP):
        engine.feed(audio[i:i + HOP])
        snapshot = show(live, engine.analyzer.analyze(engine.latest_window()))
    QApplication.processEvents()
    return snapshot


def voice(seconds, dbfs=None, f0=140):
    audio = synth_vowel(f0, MALE_FORMANTS, seconds=seconds)
    return mic_noise.at_level(audio, dbfs) if dbfs is not None else audio


# --- CARD COLOURS AND THE NOTE ---

def test_cards_are_neutral_without_targets_and_never_red(panel):
    feed_frames(panel, [frame(delta_f=1350, cpp=10)] * 30)
    for key in ("delta_f", "cpp", "h1h2", "pitch"):
        assert panel.cards.cards[key].value.tone == "primary"
    assert panel.note_label.tone == "primary"


def test_verdicts_colour_the_cards_and_the_note(panel):
    panel.core.set_targets({"pitch": [130, 150], "delta_f": [1100, 1200]})
    feed_frames(panel, [frame(f0=140, delta_f=1000)] * 100)          # pitch on target, resonance not
    cards = panel.cards.cards
    assert cards["pitch"].value.tone == "on_target"                  # the pitch card itself is judged alone
    assert cards["delta_f"].value.tone == "off_target"               # amber, not red
    assert cards["pitch"].bar.target == (130, 150)
    assert panel.note_label.tone != "on_target"
    feed_frames(panel, [frame(f0=140, delta_f=1150)] * 100)         # both on target
    panel.core.live.note_shown_at = 0.0
    feed_frames(panel, [frame(f0=140, delta_f=1150)])
    assert panel.note_label.tone == "on_target" and "Hz" in panel.note_label.text()
    assert panel.note_name.text().startswith("C#3")                  # the note under the number


# --- UNTRUSTED READINGS ---

def test_noise_pauses_the_readings_it_makes_unreliable(panel):
    noisy = mic_noise.at_level(voice(1.6), -42) + mic_noise.mic_noise(int(1.6 * SR))   # SNR about 16 dB
    feed_audio(panel, np.concatenate([mic_noise.mic_noise(int(1.5 * SR)), noisy, mic_noise.mic_noise(SR)]))
    snapshot = feed_audio(panel, np.concatenate([noisy, mic_noise.mic_noise(int(0.5 * SR))]))
    for key in ("cpp", "h1h2"):                                      # need 20 dB (coach.CPP_MIN_SNR)
        card = panel.cards.cards[key]
        assert card.text == "—" and card.unavailable == NOISY_TEXT and card.bar.value is None
    assert panel.cards.cards["delta_f"].unavailable != NOISY_TEXT     # formants need only 10 dB
    assert "weight and clarity" in snapshot["banner"]["text"]       # said once, by the shell's banner


def test_too_high_for_formants_shows_a_reason(panel):
    feed_frames(panel, [dict(frame(f0=420), resonance_reliable=False)] * 20)
    card = panel.cards.cards["delta_f"]
    assert "pitch too high" in card.unavailable and card.bar.value is None


# --- MODES AND SINGING ---

def test_cards_and_views_follow_the_mode(panel):
    panel.core.set_mode("sing")
    feed_frames(panel, [frame()])
    assert panel.mode == "sing" and "delta_f" not in panel.cards.visible_keys()
    assert "ring" in panel.cards.visible_keys() and "plane" not in panel.tabs.keys
    assert panel.core.engine.analyzer.mode == "sing" and panel.graph.y_range() != (70.0, 400.0)
    panel.core.set_mode("speak")
    feed_frames(panel, [frame()])
    assert "delta_f" in panel.cards.visible_keys() and panel.graph.y_range() == (70.0, 400.0)


def sing_frames(cents_offsets, base_midi=69.0):
    return [frame(f0=440 * 2 ** ((base_midi - 69) / 12 + c / 1200)) for c in cents_offsets]


def test_tuning_stays_green_on_vibrato(panel):
    panel.core.set_mode("sing")
    panel.core.live.target_provider = lambda: 69
    t = np.arange(80) * UPDATE_INTERVAL
    tones = []
    for f in sing_frames(50 * np.sin(2 * np.pi * 5.5 * t)):
        show(panel, f)
        tones.append(panel.cards.cards["tuning"].value.tone)
    assert set(tones[10:]) == {"on_target"}
    assert panel.cards.cards["tuning"].bar.target == (-25, 25)      # the in-tune band
    assert panel.core.live.metrics["tuning"]["marker"] is not None   # the raw pitch, for the record
    feed_frames(panel, sing_frames([40] * 30))
    assert panel.cards.cards["tuning"].value.tone == "off_target"


# --- THE COACH LINE ---

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


def test_spectrum_and_plane_draw_the_frames(panel):
    panel.show()
    panel.show_view("spectrum")
    feed_frames(panel, [frame()] * 5)
    assert np.isfinite(panel.spectrum.spec[-1]).all()
    panel.show_view("plane")
    feed_frames(panel, [frame(delta_f=1100)] * 20)
    assert len(panel.plane.current.getData()[0]) == 1
    panel.hide()


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


def test_the_shell_stores_the_mic_check_result(make_window, monkeypatch):
    forbid_hardware(monkeypatch)
    floor = 10 ** (-70 / 20) * np.random.default_rng(1).standard_normal(3 * SR)
    ah = mic_noise.at_level(voice(3.0), -25) + 10 ** (-70 / 20) * np.random.default_rng(2).standard_normal(3 * SR)
    window = make_window()
    window.engine = FakeEngine(floor, ah)
    dialog = window.mic_check()
    dialog.seconds, dialog.lead_seconds = 0.05, 0.02
    dialog.start()
    end = time.perf_counter() + 10
    while window.last_mic_check is None and time.perf_counter() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    result = window.last_mic_check
    assert result and window.profile.mic_check is result and "advice" in result
    assert window.status_text() == "Mic check saved"
    assert window.settings.mic_result.text().startswith("Last check")


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


# --- ONE "NOW", THE LISTENING STATE, STALE CARDS, THE SHORT-WINDOW LAYOUT (phase 11 / 03) ---

SILENT = dict(f0=np.nan, delta_f=np.nan, h1h2=np.nan, cpp=np.nan, ring=np.nan, level=-75.0)
SECOND = int(round(1 / UPDATE_INTERVAL))


def number_in(text):
    import re
    return float(re.search(r"(\d+(?:\.\d+)?)\s*Hz", text).group(1))


def test_the_readout_the_card_the_dot_and_the_plane_show_one_pitch(panel):
    """Lab B1 as a test: on every frame the same Hz everywhere, or none at all (audit A-002)."""
    panel.show_view("plane")
    for f0 in np.geomspace(100, 300, 120):
        show(panel, frame(f0=float(f0)))
        readout, card = number_in(panel.note_label.text()), number_in(panel.cards.cards["pitch"].value.text())
        assert round(readout) == round(card)
        x, y = panel.graph.dot.getData()
        assert abs(2 ** y[0] - readout) < 1
        ys = panel.plane.current.getData()[1]
        if ys is not None and len(ys):                          # resonance isn't judged above about 230 Hz: no dot then
            assert abs(2 ** ys[0] - readout) < 1


def test_listening_states_look_different(panel):
    seen = {}
    seen["silent"] = (show(panel, frame(**SILENT))["state"], panel.readout.state_chip.text, panel.note_label.text())
    show(panel, frame())
    seen["hearing"] = (panel.core.live.state, panel.readout.state_chip.text, panel.note_label.text())
    panel.core.live.signal_age = lambda: 5.0
    show(panel, frame(**SILENT))
    seen["no_signal"] = (panel.core.live.state, panel.readout.state_chip.text, panel.note_label.text())
    panel.core.live.signal_age = lambda: None
    panel.core.live.last_voiced_at -= 10          # the held pitch has long gone (the readout clock is real time)
    feed_frames(panel, [frame(**SILENT)] * (4 * SECOND))
    snapshot = feed_frames(panel, [frame(**dict(SILENT, level=-55.0))] * (3 * SECOND))     # sound that never became a pitch
    seen["too_quiet"] = (snapshot["state"], panel.readout.state_chip.text, panel.note_label.text())
    assert [v[0] for v in seen.values()] == ["silent", "hearing", "no_signal", "too_quiet"]
    assert len({v[1] for v in seen.values()}) == 4             # four different strips
    assert len({v[2] for v in seen.values()}) == 4             # and four different readouts: the number or the words
    assert seen["hearing"][2].endswith("Hz") and seen["silent"][2] == "Say something"
    assert panel.readout.state_chip.kind == "warning"


def test_in_range_shows_a_counting_chip_and_a_glowing_band(panel):
    panel.core.set_targets({"pitch": [130, 150], "delta_f": [1100, 1200]})
    first = feed_frames(panel, [frame(f0=140, delta_f=1150)] * (2 * SECOND))
    assert panel.readout.held_chip.isHidden() is False and panel.readout.held_chip.text.startswith("In range")
    assert panel.graph.in_range and panel.graph.target_band.brush.color().alpha() > 12
    second = feed_frames(panel, [frame(f0=140, delta_f=1150)] * (2 * SECOND))
    assert second["held_seconds"] > first["held_seconds"] + 1.5
    assert panel.readout.held_chip.text != f"In range {first['held_seconds']:.0f} s"          # it counts up
    feed_frames(panel, [frame(f0=300, delta_f=1150)] * (4 * SECOND))
    assert panel.readout.held_chip.isHidden() and not panel.graph.in_range
    assert "of the last minute" in panel.session_row.line.text()


def test_cards_go_muted_when_the_voice_stops_then_empty(panel):
    feed_frames(panel, [frame(cpp=29.0)] * (2 * SECOND))
    card = panel.cards.cards["cpp"]
    assert card.value.tone == "primary" and card.note.text() == "Reliable"
    feed_frames(panel, [frame(**SILENT)] * int(1.7 * SECOND))
    assert card.value.tone == "muted" and card.note.text().startswith("last heard") and card.text == "Clear"
    assert card.fade.opacity() < 1
    feed_frames(panel, [frame(**SILENT)] * (4 * SECOND))
    assert card.text == "—" and card.note.text() == "listening…"


def test_words_come_before_numbers_on_the_cards(panel):
    feed_frames(panel, [frame(h1h2=-1.9, cpp=29.7)] * 40)
    weight, clarity = panel.cards.cards["h1h2"], panel.cards.cards["cpp"]
    assert (weight.text, weight.number.text()) == ("Heavier", "-1.9 dB")
    assert (clarity.text, clarity.number.text()) == ("Clear", "29.7 dB")
    assert panel.cards.cards["delta_f"].name.text() == "Resonance"
    assert panel.cards.cards["pitch_range"].name.text() == "Intonation · last 6 s"


def test_the_graph_says_what_its_time_axis_is(panel):
    axis = panel.graph.getAxis("bottom")
    assert axis.labelText == "seconds ago" and axis.style["showValues"]
    assert [text for _, text in axis._tickLevels[0]] == ["8", "6", "4", "2", "now"]


def test_the_spectrum_and_the_plane_explain_themselves(panel):
    panel.show_view("spectrum")
    assert not panel.spectrum_caption.isHidden() and "Brighter colour = louder" in panel.spectrum_caption.text()
    panel.show_view("pitch")
    assert panel.spectrum_caption.isHidden()
    assert "ΔF" not in panel.plane.getAxis("bottom").labelText and "Resonance" in panel.plane.getAxis("bottom").labelText
    assert [label.toPlainText() for _, _, label in panel.plane.regions] == ["Masculine", "Feminine"]
    for number in range(1, 8):                                   # seven takes: only the newest three are named
        panel.plane.add_take(number, 140 + 10 * number, 1000 + 20 * number)
    assert sorted(n for n, (_, _, label) in panel.plane.take_points.items() if label.isVisible()) == [5, 6, 7]


def test_sing_mode_has_no_speech_leftovers(panel):
    panel.core.set_targets({"pitch": [130, 150]})
    panel.core.profile.baseline = {"pitch": 140.0, "recorded": "2026-10-23 09:20"}
    panel.apply_mode("speak")
    assert panel.graph.baseline_line.isVisible() and panel.legend.text() != ""
    panel.core.set_mode("sing")
    feed_frames(panel, [frame(f0=220.0)] * 40)
    assert not panel.graph.baseline_line.isVisible() and not panel.graph.target_band.isVisible()
    assert panel.legend.text() == ""
    assert panel.phrase.label.text() == "Your note" and panel.phrase.text.text() == "Sing any note"
    assert panel.phrase.next_btn.isHidden()
    panel.core.set_mode("speak")
    feed_frames(panel, [frame()] * 2)
    assert panel.phrase.label.text() == "Try saying" and not panel.phrase.next_btn.isHidden()


# --- THE SCREENS AT 1280x720 ---

def card_rects(live):
    from PyQt6.QtCore import QPoint
    out = {}
    for key, card in live.cards.cards.items():
        if not card.isHidden():
            top = card.mapTo(live, QPoint(0, 0))
            out[key] = (top.y(), top.y() + card.height())
    return out


def test_every_live_card_fits_on_a_720p_live_screen(make_window):
    window = make_window()
    window.show()
    window.resize(1280, 720)
    window.go("live")
    QApplication.processEvents()
    audio = voice(2.0)
    for i in range(0, len(audio) - HOP + 1, HOP):
        window.engine.feed(audio[i:i + HOP])
        window.engine.publish(window.engine.analyzer.analyze(window.engine.latest_window()))
    QApplication.processEvents()
    view = window.live.view
    area = view.parentWidget().parentWidget()                  # the scroll area's viewport
    assert view.compact
    assert area.verticalScrollBar().maximum() == 0 and not area.verticalScrollBar().isVisible()    # nothing to scroll to
    bottom = max(b for _, b in card_rects(view).values())
    assert bottom <= area.viewport().height()
    assert len(card_rects(view)) == 6
    window.resize(1920, 1080)
    QApplication.processEvents()
    assert not view.compact


def test_the_dashboard_is_today_not_a_second_live_screen(make_window):
    from ui.live_cards import LiveCards
    from ui.live_graph import LivePitchGraph
    from ui.live.strip import LiveStrip
    window = make_window()
    window.show()
    dash = window.dashboard
    assert not dash.findChildren(LivePitchGraph) and not dash.findChildren(LiveCards)
    assert isinstance(dash.live, LiveStrip) and dash.session.title_label.text()
    assert dash.live.title_label.text() == "Right now"
    window.on_frame(frame())                                   # a strip shows the state and the pitch only
    assert "Hz" in dash.live.note_label.text() and dash.live.readout.state_chip.text == "Hearing you"
    window.resize(1280, 720)
    QApplication.processEvents()
    assert dash.recent.limit == 3 and dash.progress.isHidden()
    window.resize(1920, 1080)
    QApplication.processEvents()
    assert dash.recent.limit == 6 and not dash.progress.isHidden()
    from PyQt6.QtWidgets import QLabel
    texts = " ".join(label.text() for label in dash.findChildren(QLabel))
    assert "Try saying" not in texts
