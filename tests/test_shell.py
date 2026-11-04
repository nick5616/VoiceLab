"""The new shell (spec 05), offscreen: nav rail and screens, shortcuts, onboarding, the record button and
activity, toasts, the fixed-axis pitch graph, the single input banner and replay. No mic or speakers:
audio is fed into the LiveEngine by hand and sounddevice is faked."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
import sounddevice as sd  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QKeySequence  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import mic_noise  # noqa: E402
from conftest import wait_for  # noqa: E402
from engine import UPDATE_INTERVAL  # noqa: E402
from profile_store import Profile  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402
from ui import app_window  # noqa: E402
from ui.components import Banner  # noqa: E402

VOICE = synth_vowel(140, MALE_FORMANTS, seconds=1.5, noise=0.05)
HOP = int(UPDATE_INTERVAL * SR)


def play(window, audio, each=None):
    """Feed audio as the mic would, one live frame per hop; each(window) runs after every frame."""
    engine = window.engine
    for i in range(0, len(audio) - HOP + 1, HOP):
        engine.feed(audio[i:i + HOP])
        engine.publish(engine.analyzer.analyze(engine.latest_window()))
        if each:
            each(window)
    QApplication.processEvents()


def test_window_builds_and_every_screen_opens(make_window):
    window = make_window()
    window.resize(1280, 720)
    window.show()
    QApplication.processEvents()
    assert window.size().width() == 1280 and window.size().height() == 720     # fits 1280x720
    for key in app_window.SCREENS:
        window.go(key)
        QApplication.processEvents()
        assert window.current() == key and window.nav.current() == key
        assert window.stack.currentWidget().isVisible()
        assert window.action_bar.isVisible() == (key in app_window.ACTION_BAR_SCREENS)
    for key in ("read", "phrases", "vowels", "exercise", "voicemap"):
        window.practice.open(key)
        assert window.practice.stack.currentWidget() is window.practice.pages[key]
    assert window.core.mode == "sing"                     # the Voice Map is a singing practice
    window.practice.close_practice()


def test_nav_and_shortcuts(make_window):
    window = make_window()
    window.show()
    window.activateWindow()
    QApplication.processEvents()
    window.nav.selected.emit("practice")
    assert window.current() == "practice"
    expected = {"record": "Space", "replay": "R", "import": "Ctrl+I", "fullscreen": "F11",
                "screen_dashboard": "Ctrl+1", "screen_settings": "Ctrl+6"}
    for name, keys in expected.items():
        assert window.shortcuts[name].key() == QKeySequence(keys), name
    window.shortcuts["screen_progress"].activated.emit()
    assert window.current() == "progress"
    QTest.keyClick(window, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()
    if window.isActiveWindow():                           # the offscreen platform may not activate windows
        assert window.current() == "live"
    window.shortcuts["record"].activated.emit()
    assert window.core.is_recording and window.record_btn.state == "recording"
    window.shortcuts["record"].activated.emit()
    assert not window.core.is_recording
    assert wait_for(lambda: window.record_btn.state == "idle")            # nothing was said: no take
    assert window.toasts.toasts and window.toasts.toasts[-1].kind == "error"


def test_onboarding_shows_once_and_can_be_skipped(make_window, tmp_path):
    window = make_window()
    assert window.core.should_onboard()
    window.start_up()
    dialog = window.onboarding
    assert dialog is not None and dialog.isVisible() and dialog.step() == "welcome"
    dialog.next()
    assert dialog.step() == "goal"
    dialog.goal.set_current("feminine", emit=True)
    assert window.profile.voice_goal == "feminine" and window.top.goal.current_key() == "feminine"
    dialog.skip_all.click()
    QApplication.processEvents()
    assert not dialog.isVisible() and not window.core.should_onboard()
    assert window.profile.takes_dir_asked                  # skipping the folder step is remembered too
    again = make_window()                                  # same profile directory
    again.start_up()
    assert again.onboarding is None
    reopened = again.show_onboarding()                     # Settings can open it again
    assert reopened.isVisible()
    reopened.reject()


def test_record_button_and_activity_follow_the_take(make_window):
    window = make_window()
    assert window.record_btn.state == "idle"
    window.toggle_recording()
    assert window.record_btn.state == "recording"
    play(window, VOICE)
    window.toggle_recording()
    assert window.record_btn.state == "analysing"
    assert window.activity.count() >= 1 and "Analysing" in window.activity.detail.rows[
        next(iter(window.activity.detail.rows))].label.text()
    assert window.nav.badges.get("takes") == "1"
    assert wait_for(lambda: window.core.takes())
    assert wait_for(lambda: window.record_btn.state == "idle")
    assert wait_for(lambda: window.activity.count() == 0 or not window.core.runner.jobs())
    assert not window.nav.badges.get("takes")


def test_status_errors_become_toasts(make_window):
    window = make_window()
    window.core._status("Couldn't save the take: disk full", "error")
    toast = window.toasts.toasts[-1]
    assert toast.kind == "error" and "disk full" in toast.text
    window.core._status("Loading takes…", "busy")
    assert window.top.status.text() == "Loading takes…" and len(window.toasts.toasts) == 1


def test_speak_axis_is_fixed_and_the_readout_is_always_on_it(make_window):
    window = make_window()
    window.go("live")
    graph = window.live.view.graph
    assert graph.y_range() == (70.0, 400.0)
    assert graph.zone_labels() == ["Masculine", "Overlap", "Feminine"]      # display.ZONE_WORDS, short form
    sweep = synth_vowel(np.geomspace(60, 700, int(4 * SR)), MALE_FORMANTS)
    checked = []
    frames = [0]
    window.core.live.clock = lambda: frames[0] * UPDATE_INTERVAL      # audio time, not wall-clock time

    def check(w):
        frames[0] += 1
        assert graph.y_range() == (70.0, 400.0)
        hz = w.core.live.note["hz"]
        if hz is not None:
            assert graph.readout_hz == hz
            assert graph.in_view(hz) or (graph.off_scale == ("above" if hz > 400 else "below")
                                         and graph.marker.isVisible())
            checked.append(hz)
    window.core.live.text_interval = 0
    play(window, sweep, each=check)
    assert max(checked) > 400 and min(checked) < 120                   # it went off the top of the axis


def test_one_input_chip_for_an_untrusted_input(make_window):
    window = make_window()
    window.go("live")      # the full live view is on the Live screen; the dashboard has a strip
    window.core.live.text_interval = 0
    noisy = mic_noise.at_level(synth_vowel(140, MALE_FORMANTS, seconds=1.6), -42) + mic_noise.mic_noise(int(1.6 * SR))
    play(window, np.concatenate([mic_noise.mic_noise(int(1.5 * SR)), noisy, mic_noise.mic_noise(SR)]))
    play(window, np.concatenate([noisy, mic_noise.mic_noise(int(0.5 * SR))]))
    assert not [b for b in window.findChildren(Banner) if not b.isHidden()]          # a chip, not a banner
    assert not window.banner.isHidden() and window.banner.action_text == "Mic check"
    assert "weight and clarity" in window.banner.full or "whine" in window.banner.full
    assert window.banner.full in window.banner.toolTip() and len(window.banner.text) <= 28
    cards = window.live.view.cards.cards
    for key in ("cpp", "h1h2"):
        assert cards[key].unavailable == "paused: noisy input" and cards[key].bar.value is None
    assert not hasattr(cards["cpp"].bar, "hatched")
    assert window.top.mic.state == "warn"
    window.banner.click()                                              # "Mic check"
    assert window.mic_dialog is not None and window.mic_dialog.isVisible()
    window.mic_dialog.reject()


def test_replay_plays_recent_audio_with_a_playhead(make_window, monkeypatch):
    played = []
    monkeypatch.setattr(sd, "play", lambda buffer, rate, *a, **k: played.append(np.array(buffer)))
    window = make_window()
    window.go("live")
    play(window, VOICE)
    recent = window.engine.recent_audio(10)
    window.replay()
    assert len(played) == 1 and np.allclose(played[0], recent)
    graph = window.live.view.graph
    assert graph.frozen and window.core.replay_progress() is not None
    window.live.view._follow_replay()
    assert graph.playhead.isVisible()
    QTest.qWait(50)
    window.replay()                                                    # pressed again: stops
    window.live.view._follow_replay()
    assert window.core.replay_progress() is None and not graph.frozen


def test_theme_and_motion_apply_live(make_window, monkeypatch):
    from ui import theme
    window = make_window()
    monkeypatch.delenv("VOICELAB_REDUCE_MOTION")
    window.core.set_ui_pref(theme="light", reduce_motion=True)
    assert theme.current().name == "light" and theme.reduce_motion
    window.core.set_ui_pref(theme="dark", reduce_motion=False)
    assert theme.current().name == "dark" and not theme.reduce_motion
    assert Profile(window.profile.directory).ui["theme"] == "dark"


def test_rest_nudge_is_a_gentle_toast(make_window):
    window = make_window()
    clock = [0.0]
    window.core.clock = lambda: clock[0]
    window.core.start_session()
    clock[0] = 26 * 60
    window.update_session()
    assert window.top.session.text() == "Session 26 min"
    assert window.toasts.toasts[-1].kind == "info" and "rest" in window.toasts.toasts[-1].text


@pytest.mark.parametrize("mode", ["speak", "sing"])
def test_live_cards_and_views_follow_the_mode(make_window, mode):
    window = make_window()
    window.go("live")      # the full live view is on the Live screen; the dashboard has a strip
    window.core.set_mode(mode)
    live = window.live.view
    play(window, VOICE)
    keys = live.cards.visible_keys()
    if mode == "speak":
        assert keys[:2] == ["pitch", "delta_f"] and "plane" in live.tabs.keys
    else:
        assert keys[:2] == ["tuning", "vibrato"] and "plane" not in live.tabs.keys
        assert live.graph.y_range() != (70.0, 400.0)


def test_take_widgets_are_wired_to_the_shell(make_window, monkeypatch):
    window = make_window()
    window.toggle_recording()
    play(window, VOICE)
    window.toggle_recording()
    assert wait_for(lambda: window.core.takes())
    take = window.core.takes()[0]
    dash = window.dashboard
    dash.latest.open_report.emit(take["uid"])                 # "Open full report"
    assert window.current() == "takes" and window.takes.selected == take["uid"]
    assert window.takes.full is not None and window.takes.full.uid == take["uid"]
    window.go("dashboard")
    dash.recent.open_take.emit(take["uid"])
    assert window.current() == "takes"
    window.go("dashboard")
    dash.recent.show_all.emit()
    assert window.current() == "takes"
    window.takes.mic_check_requested.emit()                   # from a take's "too noisy" banner
    assert window.mic_dialog is not None and window.mic_dialog.isVisible()
    window.mic_dialog.reject()
    imports = []
    monkeypatch.setattr(window, "import_audio", lambda: imports.append(1))
    window.takes.import_requested.disconnect()
    window.takes.import_requested.connect(window.import_audio)
    window.takes.import_requested.emit()
    assert imports == [1]
    window.takes.record_requested.emit()                      # from the Takes screen's empty state
    assert window.current() == "dashboard" and window.core.is_recording
    window.toggle_recording()
    assert wait_for(lambda: window.record_btn.state == "idle")


def test_voice_map_rebuild_and_clear_from_practice(make_window, tmp_path):
    window = make_window(tmp_path / "takes")
    window.toggle_recording()
    play(window, VOICE)
    window.toggle_recording()
    assert wait_for(lambda: window.core.takes())
    window.practice.open("voicemap")
    view = window.voice_map
    view.confirm = lambda text: True
    view.clear()
    assert view.data.counts.sum() == 0
    view.rebuild_requested.emit()                             # QMessageBox.question is faked to "Yes"
    assert wait_for(lambda: "rebuilt" in window.status_text())
    assert view.data.counts.sum() > 50 and window.toasts.toasts[-1].kind == "success"


def test_settings_targets_and_goal(make_window):
    window = make_window()
    window.go("settings")
    settings = window.settings
    settings.goal.set_current("masculine", emit=True)
    assert window.profile.voice_goal == "masculine" and window.top.goal.current_key() == "masculine"
    window.top.goal.set_current_key("exploring")
    assert window.profile.voice_goal is None and settings.goal.current() == "exploring"
    settings.targets.apply()
    assert window.toasts.toasts[-1].text == "Targets saved"
    settings.theme_choice.set_current_key("light")
    assert window.profile.ui["theme"] == "light"


# --- phase 11 / 05: the frame tells the truth (state, errors with actions, the mic heartbeat, onboarding) ---

import shell_state  # noqa: E402
from engine import LiveEngine  # noqa: E402

REAL_MIC_NAMES = [          # engine.input_devices() style strings: Windows MME cuts a name at 31 characters
    ("Microphone (USB PnP Audio Devic", "USB PnP Audio"),
    ("Microphone (USB PnP Audio Device)", "USB PnP Audio Device"),
    ("Microphone Array (Realtek(R) Audio)", "Microphone Array"),
    ("Headset Microphone (Logitech USB Headset)", "Headset Microphone (Logitech USB Headset)"),
    ("Microphone", "Microphone"), ("", ""), (None, ""),
]


@pytest.mark.parametrize("raw,short", REAL_MIC_NAMES)
def test_device_names_are_short_and_never_cut_mid_word(raw, short):
    assert shell_state.short_device_name(raw) == short


def test_mic_indicator_is_distinct_for_every_state():
    states = {
        "unavailable": shell_state.mic_indicator("error"),
        "lost": shell_state.mic_indicator("lost"),
        "listening": shell_state.mic_indicator("ok", "silent"),
        "hearing": shell_state.mic_indicator("ok", "hearing"),
        "too_quiet": shell_state.mic_indicator("ok", "too_quiet"),
        "recording": shell_state.mic_indicator("ok", "hearing", recording=True),
        "paused": shell_state.mic_indicator("ok", "silent", practice=True),
    }
    assert {k: v["key"] for k, v in states.items()} == {k: k for k in states}
    assert len({v["words"] for v in states.values()}) == len(states)          # no two states share their words
    assert shell_state.mic_indicator("ok", "no_signal")["key"] == "lost"       # the live state agrees
    assert shell_state.mic_indicator("warn", "hearing")["dot"] == "warn"       # an input problem tints the dot
    assert shell_state.mic_level_fraction(-80) == 0 and shell_state.mic_level_fraction(-5) == 1
    assert 0 < shell_state.mic_level_fraction(-30) < 1 and shell_state.mic_level_fraction(None) == 0


def test_mic_errors_are_plain_and_every_error_has_an_action():
    text, details = shell_state.plain_mic_error("Error opening InputStream: Unanticipated host error "
                                                "[PaErrorCode -9999]")
    assert "PaErrorCode" not in text and "unplugged" in text and "-9999" in details
    assert "another app" in shell_state.plain_mic_error("Device unavailable [PaErrorCode -9985]")[0]
    assert shell_state.plain_mic_error("something new")[0] == shell_state.GENERIC_MIC_ERROR
    for text, level, key in (("No microphone found. Import still works.", "error", "choose_mic"),
                             ("Couldn't open the mic: device busy", "error", "choose_mic"),
                             ("Couldn't save the take: [Errno 28] No space left on device", "error", "choose_folder"),
                             ("This take is too noisy to set targets from.", "warning", "open_takes"),
                             ("Something else broke", "error", "settings")):
        action = shell_state.error_action(text, level)
        assert action and action[0] == key and action[1], text
    assert shell_state.error_action("Saved", "success") is None and shell_state.error_action("x", "info") is None


def test_the_input_chip_names_the_problem_in_a_few_words_and_never_cuts_one():
    import live_session
    chip = shell_state.alert_chip({"text": "Desk rumble: set the mic on something soft · can't judge weight right "
                                           "now", "action": "Mic check", "kind": "warning"})
    assert chip["label"] == "Desk rumble" and chip["tooltip"].startswith("Desk rumble: set the mic")
    assert chip["tooltip"].endswith("Click: Mic check")
    for text in list(live_session.BANNER_TEXT.values()) + [live_session.SLOW_PATH_BANNER, shell_state.LOST_TEXT]:
        label = shell_state.alert_chip({"text": text, "action": "Mic check"})["label"]
        assert label and len(label) <= shell_state.ALERT_CHIP_CHARS and text.startswith(label), text
    long = shell_state.alert_chip({"text": shell_state.ERROR_TEXTS[0][1], "action": "Choose mic", "kind": "error"})
    assert long["label"] == "Mic problem"                                  # a cut phrase never shows
    assert shell_state.alert_chip(None) is None


def test_activity_text_names_the_job_and_agrees_with_the_job_list():
    job = {"id": 1, "kind": "reanalysis", "label": "Updating takes 3/11", "done": 0, "total": None, "text": None}
    described = shell_state.describe_job(job)
    assert described["chip"] == "Updating takes 3/11" and "older takes" in described["affects"]
    assert "Live readings are not affected" in described["affects"]
    other = {"id": 2, "kind": "voicemap", "label": "Rebuilding Voice Map", "done": 0, "total": None, "text": None}
    both = [described, shell_state.describe_job(other)]
    assert shell_state.activity_summary([])["text"] == "Nothing running"
    assert len(shell_state.activity_summary(both)["text"]) <= shell_state.CHIP_CHARS
    assert shell_state.activity_summary(both[:1])["text"] == "Updating takes 3/11"
    long = shell_state.describe_job({"id": 3, "kind": "shift", "label": "Rendering the shifted voice, slowly"})
    assert len(long["chip"]) <= shell_state.CHIP_CHARS


def test_import_choices_and_target_rows_by_mode():
    assert shell_state.import_options({"voice": "music", "channel": "1"}) == (True, 1)
    assert shell_state.import_options({"voice": "voice", "channel": "mix"}) == (False, None)
    assert shell_state.target_keys_for_mode(["pitch", "ring", "register", "delta_f"], "speak") == ["pitch", "delta_f"]
    assert "ring" in shell_state.target_keys_for_mode(["pitch", "ring"], "sing")
    assert shell_state.plain_baseline_text("146 Hz · ΔF 993 Hz") == "146 Hz · Resonance 993 Hz"
    assert shell_state.live_state({"f0": float("nan")}) == "silent" and shell_state.live_state({"f0": 150.0}) == "hearing"
    assert shell_state.live_state({"f0": 150.0}, {"state": "too_quiet"}) == "too_quiet"


def test_mic_check_findings_retest_and_level():
    import mic_check
    old = {"rumble": True, "whine": False, "clipped": False, "voice_db": -30.0, "noise_db": -50.0,
           "advice": [mic_check.RUMBLE_ADVICE, mic_check.ENHANCEMENTS_ADVICE]}
    new = {"rumble": False, "whine": True, "clipped": False, "voice_db": -24.0, "noise_db": -50.0,
           "advice": [mic_check.WHINE_ADVICE]}
    found = mic_check.findings(old)
    assert [f["issue"] for f in found] == ["Low rumble", "Windows audio enhancements"] and found[0]["fix"]
    assert mic_check.findings({"advice": [mic_check.GOOD_ADVICE]}) == []
    lines = mic_check.compare(old, new)
    assert "Rumble: was loud, now gone" in lines and "Whine: new since the last test" in lines
    assert any(line.startswith("Your voice: 6 dB louder") for line in lines)
    assert mic_check.compare(None, new) == [] and mic_check.compare(old, old) == ["Rumble: still there"]
    assert mic_check.level_dbfs(np.zeros(100)) == -120 and mic_check.level_dbfs(np.full(100, 0.1)) == pytest.approx(-20)
    assert "48 kHz" in mic_check.capture_note({"native_rate": 48000, "path": "wasapi"})
    assert mic_check.capture_note({}) is None


def test_engine_heartbeat_sees_a_stream_that_stopped():
    engine = LiveEngine()
    now = [100.0]
    engine.clock = lambda: now[0]
    assert engine.seconds_since_audio() is None and engine.mic_alive()          # no stream: never "lost"
    engine.stream_opened_at = now[0]
    now[0] += 2.0
    assert engine.mic_alive()                                                   # a stream gets time for its first block
    engine._on_audio(np.zeros((HOP, 1)), HOP, None, None)
    now[0] += 1.0
    assert engine.mic_alive() and engine.seconds_since_audio() == pytest.approx(1.0)
    now[0] += 1.0
    assert not engine.mic_alive()                                               # 2 s without a block
    engine._on_audio(np.zeros((HOP, 1)), HOP, None, "input overflow")
    assert engine.mic_alive() and engine.stream_status == "input overflow"
    engine._on_stream_finished()
    assert not engine.mic_alive()                                               # the device went away


def test_a_dead_mic_is_lost_in_two_seconds_and_recovers(make_window):
    window = make_window()
    engine, core = window.engine, window.core
    now = [0.0]
    engine.clock = core.clock = lambda: now[0]
    engine.stream_opened_at = 0.0
    engine._on_audio(np.zeros((HOP, 1)), HOP, None, None)
    window.poll_mic()
    assert core.mic_status()["state"] == "ok" and window.banner.isHidden()
    now[0] = 2.0                                                                # nothing arrived for 2 s
    window.poll_mic()
    assert core.mic_status()["state"] == "lost" and core.mic_lost
    assert window.top.mic.key == "lost" and window.top.mic.state == "error" and "Mic stopped" in window.top.mic.label
    assert not window.banner.isHidden() and "stopped sending audio" in window.banner.full
    assert window.banner.action_text == "Choose mic"
    assert window.record_btn.state == "disabled" and "Mic stopped" in window.record_btn.caption()
    play(window, VOICE[:HOP * 3])                                               # stale frames must not look live
    assert window.top.mic.key == "lost"
    now[0] = 3.0
    engine._on_audio(np.zeros((HOP, 1)), HOP, None, None)                       # the stream is feeding again
    window.poll_mic()
    assert not core.mic_lost and core.mic_status()["state"] == "ok" and window.record_btn.state == "idle"
    assert any("reconnected" in t.text for t in window.toasts.toasts)
    assert window.banner.isHidden() or "stopped" not in window.banner.full


def test_the_mic_block_follows_the_voice_and_the_level(make_window):
    window = make_window()
    mic = window.top.mic
    play(window, np.zeros(HOP * 6))
    assert mic.key == "listening" and mic.level == 0 and "Listening" in mic.label
    play(window, VOICE)
    assert mic.key == "hearing" and mic.level > 0 and "Hearing you" in mic.label
    assert "Microphone" in mic.accessibleName() and "Hearing you" in mic.accessibleName()
    window.core.set_mic_available(False, "Error opening InputStream: Device unavailable [PaErrorCode -9985]")
    assert mic.key == "unavailable" and mic.state == "error"
    assert not window.banner.isHidden() and "another app" in window.banner.full
    assert window.banner.action_text == "Choose mic"
    assert window.toasts.toasts[-1].action is not None                          # the toast says what to do too
    window.banner.click()
    assert window.current() == "settings"


def test_recording_has_a_signature_you_can_see_from_across_the_room(make_window):
    window = make_window()
    assert not window.top.recording.isVisible() and not window.rec_line.on
    window.show()
    window.toggle_recording()
    assert window.top.recording.isVisible() and window.top.recording.text().startswith("REC")
    assert window.rec_line.on and not window.top.status.isVisible()
    assert window.top.mic.key == "recording"
    play(window, VOICE)
    window.toggle_recording()
    assert not window.top.recording.isVisible() and not window.rec_line.on


def test_a_new_take_says_so_with_a_button_that_opens_its_report(make_window):
    window = make_window()
    window.toggle_recording()
    play(window, VOICE)
    window.toggle_recording()
    assert wait_for(lambda: window.core.takes())
    ready = [t for t in window.toasts.toasts if t.text.startswith("Take #1 is ready")]
    assert ready and ready[0].action.text() == "Open report" and ready[0].kind == "success"
    window.go("dashboard")
    ready[0].action.click()
    assert window.current() == "takes" and window.takes.selected == window.core.takes()[0]["uid"]
    assert "Saved 20" not in window.status_text()                                # no file name as a status


def test_the_baseline_question_comes_once_after_the_first_good_take(make_window):
    window = make_window()
    take = {"uid": "a", "pitch": 150.0, "confidence": {"pitch": "high"}}
    assert window.core.baseline_prompt({"uid": "b", "pitch": 150.0, "confidence": {"pitch": "low"}}) is None
    first = window.core.baseline_prompt(take)
    assert first and "baseline" in first[0] and first[1] == "Set baseline"
    assert window.core.baseline_prompt(take) is None                             # asked once
    assert window.profile.ui["baseline_prompted"] is True


def test_older_takes_are_updated_only_after_a_yes(make_window, tmp_path):
    from analysis import analyze_take
    from take_register import finish_take
    from take_store import TakeStore
    folder = tmp_path / "takes"
    old = finish_take(analyze_take(VOICE))
    old.pop("analysis_version", None)
    old.update(time="2026-09-01T10:00:00", mode="speak")
    TakeStore(folder).save(old)
    window = make_window(folder)
    assert window.core.outdated_takes() and not window.core.runner.jobs()
    assert not window.offer.isHidden() and "Update your 1 take now" in window.offer.label.text()
    window.offer.second.click()                                                  # Later
    assert window.offer.isHidden() and not window.core.runner.jobs()
    window.core.reanalysis_deferred = False
    window.core.offer_reanalysis()
    window.offer.action.click()                                                  # Update now
    assert wait_for(lambda: not window.core.outdated_takes() and not window.core.runner.jobs())
    done = [t for t in window.toasts.toasts if t.text.startswith("Updated 1 take")]
    assert done and done[0].action.text() == "Rebuild Voice Map"


def test_the_activity_chip_names_the_job_and_what_it_changes(make_window):
    window = make_window()
    window.core.reanalysis_total, window.core.reanalysis_done = 11, 2
    job = {"id": 1, "kind": "reanalysis", "label": "Updating takes 3/11", "done": 0, "total": None, "text": None,
           "started": 1.0}
    window.core.events.emit("busy", [job])
    assert window.activity.title.text() == "Updating takes 3/11"
    row = next(iter(window.activity.detail.rows.values()))
    assert "older takes" in row.text.text() and "Updating your takes" in row.label.text()
    assert not window.activity.isHidden()
    window.core.events.emit("busy", [])
    assert window.activity.title.text() == "Nothing running" and window.activity.isHidden()   # idle: not shown


def test_the_takes_badge_counts_a_re_analysis_that_is_only_just_submitted(make_window, tmp_path):
    window = make_window()
    core = window.core
    core.store = object()                       # only its truthiness matters here
    core.outdated_takes = lambda: [1, 2, 3]
    core.runner._jobs = [{"id": 9, "kind": "reanalysis", "label": "x", "done": 0, "total": None, "text": None,
                          "started": 1.0}]          # running, but jobs["reanalysis"] isn't stored yet
    assert core.jobs["reanalysis"] is None and core.takes_in_progress() == 3
    core.runner._jobs = []


def test_onboarding_ends_with_a_first_take_and_hears_you(make_window):
    window = make_window()
    window.start_up()
    dialog = window.onboarding
    seen = []
    dialog.first_take.connect(lambda: seen.append("record"))
    for _ in range(4):
        dialog.next()
    assert dialog.step() == "try" and dialog.next_btn.text() == "Record my first take"
    assert dialog.skip_all.text() == "Finish without recording" and dialog.skip_all.variant == "ghost"
    assert "Waiting" in dialog.try_label.text() and dialog.meter.level == 0
    play(window, VOICE)                                                          # live frames reach the try step
    assert dialog.heard and "I can hear you" in dialog.try_label.text() and dialog.meter.level > 0
    dialog.next()
    assert seen == ["record"] and not window.core.should_onboard()
    assert window.current() == "dashboard" and window.core.is_recording            # the first take starts
    window.toggle_recording()
    assert wait_for(lambda: window.record_btn.state == "idle")


def test_the_welcome_opens_a_mic_check_it_does_not_block(make_window):
    window = make_window()
    window.start_up()
    dialog = window.onboarding
    dialog.mic_btn.click()
    mic = window.mic_dialog
    assert mic.isVisible() and mic.parent() is dialog       # a child of the modal welcome, so it takes clicks
    mic.reject()
    dialog.reject()
    window.mic_check()
    assert window.mic_dialog.parent() is window              # on its own it stays the window's
    window.mic_dialog.reject()


def test_the_folder_step_offers_one_click_default(make_window, tmp_path, monkeypatch):
    window = make_window()
    monkeypatch.setattr(window.core, "default_takes_folder", lambda: tmp_path / "Documents" / "VoiceLab")
    window.start_up()
    dialog = window.onboarding
    dialog.go(3)
    assert dialog.step() == "folder" and str(tmp_path / "Documents" / "VoiceLab") in dialog.folder_label.text()
    assert dialog.use_folder_btn.text() == "Use this folder" and dialog.use_folder_btn.variant == "primary"
    dialog.use_folder_btn.click()
    assert window.core.store and window.core.store.folder == tmp_path / "Documents" / "VoiceLab"
    assert dialog.use_folder_btn.isHidden()
    dialog.reject()


def test_the_banner_never_squeezes_other_issues_into_a_sentence(app):
    banner = Banner("Desk rumble: set the mic on something soft. (+2 more)", "warning", action="Mic check")
    banner.setToolTip("Desk rumble: set the mic on something soft.\nA steady whine.\nToo quiet.")
    banner.resize(700, 60)
    banner.show()
    assert "more" not in banner.label.text() and not banner.more_link.isHidden()
    assert banner.more_link.text() == "and 2 more issues"
    banner.more_link.click()
    assert "A steady whine." in banner.label.text() and "Too quiet." in banner.label.text()
    assert banner.more_link.text() == "Hide the others"
    banner.more_link.click()
    assert banner.label.text() == "Desk rumble: set the mic on something soft."
    explicit = Banner("Lead", more=["Second", "Third"])
    explicit.more_link.click()
    assert explicit.label.text().splitlines() == ["Lead", "• Second", "• Third"]
    narrow = Banner("Short", action="Mic check", more=["x"])
    narrow.resize(400, 80)
    narrow.show()
    QApplication.processEvents()
    assert narrow.buttons.y() > narrow.label.y()                                 # the buttons drop under the words


def test_every_stop_on_every_screen_has_an_accessible_name(make_window):
    from PyQt6.QtWidgets import QAbstractButton, QWidget
    window = make_window()
    window.resize(1600, 900)
    window.show()
    missing = []
    skip = ("QLineEdit", "QAbstractScrollArea", "QScrollBar", "QDoubleSpinBox", "QSpinBox", "QScrollArea", "QComboBox",
            "QStackedWidget", "QPlainTextEdit", "QTextEdit", "QLabel", "PlotWidget", "PlotItem", "QFrame", "LivePitchGraph")
    for key in app_window.SCREENS:
        window.go(key)
        QApplication.processEvents()
        for w in window.findChildren(QWidget):
            if not w.isVisible() or w.focusPolicy() == Qt.FocusPolicy.NoFocus or w.__class__.__name__ in skip or type(w.parent()).__name__ in skip:
                continue
            name = w.accessibleName() or (w.text() if isinstance(w, QAbstractButton) else "")
            if not name:
                missing.append((key, w.__class__.__name__, w.toolTip()))
    assert not missing, "; ".join(sorted({f"{a}|{b}|{c[:30]}" for a, b, c in missing}))


def test_the_shortcut_sheet_lists_every_shortcut(make_window):
    from ui.components import Label
    window = make_window()
    assert window.shortcuts["shortcuts"].key().toString() == "?"
    sheet = window.show_shortcuts()
    assert sheet.isVisible()
    text = " ".join(label.text() for label in sheet.findChildren(Label))
    for keys, what in shell_state.SHORTCUTS:
        assert keys in text and what in text
    sheet.accept()
    assert "Space" in window.record_btn.toolTip() and "(R)" in window.action_bar.replay_btn.toolTip()
    window.go("settings")
    settings_text = " ".join(label.text() for label in window.settings.findChildren(Label))
    assert "Ctrl+I" in settings_text and "Keyboard shortcuts" in settings_text


def test_import_sheet_asks_in_plain_words(app):
    from ui.components import Label
    from ui.sheets import ImportSheet
    sheet = ImportSheet("session.wav", can_isolate=True, stereo=True)
    sheet.show()
    visible = " ".join(w.text() for w in sheet.findChildren(Label) if w.isVisible())
    assert "Demucs" not in visible and "EGG" not in visible and "session.wav" in visible
    assert sheet.answers() == (False, None)
    sheet.voice.set_current("music", emit=True)
    sheet.channel.set_current("1", emit=True)
    assert sheet.answers() == (True, 1)
    mono = ImportSheet("a.wav", can_isolate=False, stereo=False)
    assert mono.voice is None and mono.channel is None


def test_settings_targets_save_by_themselves_and_speaking_hides_singing_rows(make_window):
    window = make_window()
    window.go("settings")
    targets = window.settings.targets
    assert targets.save_btn is None and targets.rows["ring"].isHidden() and targets.rows["register"].isHidden()
    assert not targets.rows["pitch"].isHidden()
    row = targets.rows["pitch"]
    row.toggle.set_checked(True)
    row.spins[0].setValue(170)
    row.spins[1].setValue(230)
    assert targets.saved_note.text() == "Saving…" and window.profile.target("pitch") != (170.0, 230.0)
    targets._timer().timeout.emit()                                              # the moment after the last edit
    assert tuple(window.profile.target("pitch")) == (170.0, 230.0) and targets.saved_note.text() == "Saved"
    assert targets.rows["pitch"] is row                                          # the row you were editing stays
    window.core.set_mode("sing")
    assert not targets.rows["ring"].isHidden()


def test_settings_shows_the_last_mic_check_and_its_fixes(make_window):
    import mic_check
    window = make_window()
    window.go("settings")
    assert "No mic check yet" in window.settings.mic_result.text()
    window.profile.set_mic_check({"time": "2026-10-23T11:59:00", "ok": False,
                                  "advice": [mic_check.RUMBLE_ADVICE, mic_check.WHINE_ADVICE]})
    window.settings.refresh()
    assert window.settings.mic_result.text() == "Last checked Fri 23 Oct: 2 things to fix."
    assert mic_check.RUMBLE_ADVICE in window.settings.mic_fixes.text()
    assert window.core.mic_check_summary()["issues"] == 2


def test_error_toasts_carry_the_button_that_fixes_them(make_window):
    window = make_window()
    window.core._status("Couldn't save the take: [Errno 28] No space left on device", "error")
    toast = window.toasts.toasts[-1]
    assert toast.action is not None and toast.action.text() == "Choose folder"
    window.core._status("Couldn't open the mic: device busy", "error")
    toast = window.toasts.toasts[-1]
    assert toast.action.text() == "Choose mic"
    toast.action.click()
    assert window.current() == "settings"
    window.core._status("Updating your takes to the new analysis: 3 of 11", "busy")
    assert window.top.status.alternatives[0].startswith("Updating your takes")


def test_the_status_line_shows_a_whole_phrase_or_none(app):
    from ui.components import FitLabel
    label = FitLabel("")
    label.resize(500, 20)
    label.set_alternatives("Updating your takes to the new analysis: 3 of 11", "Updating your takes to the new analysis")
    assert label.text().startswith("Updating your takes to the new analysis: 3")
    label.show()
    label.resize(30, 20)
    QApplication.processEvents()
    assert label.text() == "" and "3 of 11" in label.toolTip()


def test_the_top_bar_never_cuts_a_label_at_1280(make_window):
    window = make_window()
    window.resize(1280, 720)
    window.show()
    QApplication.processEvents()
    mic = window.top.mic
    assert mic.name == "System default mic" and mic.width() >= mic._width() - 1
    window.profile.set_input_device("Microphone (USB PnP Audio Devic")
    window.update_mic_status()
    QApplication.processEvents()
    assert mic.name == "USB PnP Audio" and "Microphone (USB PnP Audio Devic" in mic.toolTip()
    assert not window.top.activity.title.text().endswith("…")
