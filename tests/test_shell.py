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
    assert graph.zone_labels() == ["typically masculine", "overlap / androgynous", "typically feminine"]
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


def test_one_banner_for_an_untrusted_input(make_window):
    window = make_window()
    window.core.live.text_interval = 0
    noisy = mic_noise.at_level(synth_vowel(140, MALE_FORMANTS, seconds=1.6), -42) + mic_noise.mic_noise(int(1.6 * SR))
    play(window, np.concatenate([mic_noise.mic_noise(int(1.5 * SR)), noisy, mic_noise.mic_noise(SR)]))
    play(window, np.concatenate([noisy, mic_noise.mic_noise(int(0.5 * SR))]))
    banners = [b for b in window.findChildren(Banner) if not b.isHidden()]
    assert banners == [window.banner]
    assert "weight and clarity" in window.banner.label.text() or "whine" in window.banner.label.text()
    cards = window.dashboard.live.cards.cards
    for key in ("cpp", "h1h2"):
        assert cards[key].unavailable == "paused: noisy input" and cards[key].bar.value is None
    assert not hasattr(cards["cpp"].bar, "hatched")
    assert window.top.mic.state == "warn"
    window.banner.action.click()                                       # "Mic check"
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
    window.core.set_mode(mode)
    live = window.dashboard.live
    play(window, VOICE)
    keys = live.cards.visible_keys()
    if mode == "speak":
        assert keys[:2] == ["pitch", "delta_f"] and "plane" in live.tabs.keys
    else:
        assert keys[:2] == ["tuning", "vibrato"] and "plane" not in live.tabs.keys
        assert live.graph.y_range() != (70.0, 400.0)
