"""Takes, targets and practice UI (spec 07): re-analysis, the take card's quality and advice, targets,
progress, playback and the session reminder, as the window shows them. Offscreen Qt, no mic and no
speakers. The same logic is checked without a UI in test_core.py."""
import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

import app_core  # noqa: E402
import coach  # noqa: E402
import mic_noise  # noqa: E402
import profile_store  # noqa: E402
from analysis import FORMANT_CEILINGS, analyze_take  # noqa: E402
from profile_store import Profile  # noqa: E402
from progress import change_text, select  # noqa: E402
from ui.progress_dialog import ProgressDialog  # noqa: E402
from ui.take_card import AnalysisGraph, TakeCard, UntrustedMetric  # noqa: E402
from take_register import analyze_recording, finish_take  # noqa: E402
from take_store import ANALYSIS_VERSION, TakeStore, is_outdated  # noqa: E402
from ui.targets_dialog import TargetsDialog  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402
from conftest import wait_for  # noqa: E402
from test_app import record  # noqa: E402

VOICE = synth_vowel(140, MALE_FORMANTS, seconds=3, noise=0.05)
REALISTIC = mic_noise.at_level(synth_vowel(140, MALE_FORMANTS, seconds=3), -30) + mic_noise.mic_noise(3 * SR)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def old_take(folder, audio=VOICE, day=1, **overrides):
    """A take in the folder as an earlier version left it: no analysis version, a ghost-wide range."""
    result = finish_take(analyze_take(audio))
    result.pop("analysis_version", None)
    result.update(time=f"2026-09-{day:02d}T10:00:00", pitch_range=47.0, mode="speak", **overrides)
    store = TakeStore(folder)
    store.save(result)
    sidecar = json.loads(store.sidecar(result).read_text(encoding="utf-8"))
    sidecar.pop("analysis_version", None)
    store.sidecar(result).write_text(json.dumps(sidecar), encoding="utf-8")
    return result


def test_old_take_is_reanalysed_without_touching_the_wav(make_window, tmp_path):
    folder = tmp_path / "takes"
    take = old_take(folder)
    wav = folder / take["file"]
    before, mtime = sha(wav), wav.stat().st_mtime_ns
    window = make_window(folder)
    assert is_outdated(window.cards[0].data)
    assert wait_for(lambda: not window.core.reanalysis_active())
    card = window.cards[0]
    assert card.data["analysis_version"] == ANALYSIS_VERSION and not is_outdated(card.data)
    assert card.data["pitch_range"] < 20                       # the stored 47 st was ghosts
    sidecar = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["analysis_version"] == ANALYSIS_VERSION and sidecar["mode"] == "speak"
    assert sha(wav) == before and wav.stat().st_mtime_ns == mtime
    assert card.data["uid"] == take["uid"] and card.data["file"] == take["file"]
    assert "Re-analysed 1" in window.status_label.text()


def test_reanalysis_runs_one_at_a_time_and_reports_progress(make_window, tmp_path):
    folder = tmp_path / "takes"
    for day in (1, 2, 3):
        old_take(folder, day=day)
    window = make_window(folder)
    seen = []
    assert wait_for(lambda: (seen.append(window.status_label.text()), not window.core.reanalysis_active())[1])
    assert any("1 of 3" in text for text in seen) and any("3 of 3" in text for text in seen)
    assert all(not is_outdated(card.data) for card in window.cards)


def test_current_takes_are_left_alone(make_window, tmp_path):
    folder = tmp_path / "takes"
    take = finish_take(analyze_take(VOICE))
    take["analysis_version"] = ANALYSIS_VERSION
    TakeStore(folder).save(take | {"time": "2026-09-01T10:00:00"})
    window = make_window(folder)
    assert not window.core.outdated_takes() and window.core.jobs["reanalysis"] is None


def test_sung_take_is_analysed_in_sing_mode(make_window):
    window = make_window()
    window.live_panel.mode_buttons["sing"].click()
    assert window.profile.mode == "sing" and window.voice_map.mode == "sing"
    high = synth_vowel(700, MALE_FORMANTS, seconds=2, noise=0.02)
    card = record(window, high)
    assert card.data["mode"] == "sing"
    assert abs(card.data["pitch"] - 700) < 20                  # speech mode would cut it off above 500 Hz
    assert card.data["voiced_fraction"] > 0.5
    assert card.data["analysis_version"] == ANALYSIS_VERSION
    assert analyze_recording(high, mode="sing")["mode"] == "sing"
    assert analyze_recording(high, mode="speak")["mode"] == "speak"


def test_card_shows_tier_reasons_and_advice(make_window):
    window = make_window()
    card = record(window, VOICE)
    tier = card.quality["tier"]
    assert card.quality_chip.text().lower().startswith("●") and tier in card.quality_chip.text().lower() + "recording"
    # A target the resonance misses while pitch is on target calls for the brightening advice.
    pitch, delta_f = card.data["pitch"], card.data["delta_f"]
    window.profile.set_target("pitch", [pitch * 0.9, pitch * 1.1])
    window.profile.set_target("delta_f", [delta_f + 150, delta_f + 300])
    window.core.refresh_profile()
    texts = [m["text"] for m in card.advice]
    assert 1 <= len(card.advice) <= 3
    if card.quality["trust"]["pitch"] and card.quality["trust"]["delta_f"] and tier != "poor":
        assert any("resonance" in t for t in texts)
        assert card.advice_box.count() == len(card.advice)


def poor_data(window):
    """A copy of a good take recorded too quietly, in too much noise."""
    card = record(window, VOICE)
    data = copy.copy(card.data)
    data.update(snr=6.0, level=-52.0, uid="poor-1", file=None)
    return data


def test_poor_take_shows_dashes_with_reasons_not_bars(make_window):
    window = make_window()
    data = poor_data(window)
    card = window.add_card(data)
    assert card.quality["tier"] == "poor"
    assert "Poor" in card.quality_chip.text() and "closer" in card.quality_reasons.text()
    for key in ("pitch", "delta_f", "h1h2", "cpp", "ring", "pitch_range"):
        bar = card.bars[key]
        assert isinstance(bar, UntrustedMetric)
        assert "—" in bar.title.text() and "too noisy or quiet" in bar.title.text()
    assert not isinstance(card.bars["level"], UntrustedMetric)      # the level is always true
    assert "—" in card.register_title.text()
    assert [m["key"] for m in card.advice] == ["take_poor"]


def test_high_pitch_marks_only_resonance_untrusted(make_window):
    window = make_window()
    data = copy.copy(record(window, VOICE).data)
    data.update(pitch=420.0, uid="high-1", file=None)
    card = window.add_card(data)
    assert isinstance(card.bars["delta_f"], UntrustedMetric)
    assert f"above about {coach.FORMANT_MAX_F0:.0f} Hz" in card.bars["delta_f"].title.text()
    assert not isinstance(card.bars["pitch"], UntrustedMetric)


def test_joint_time_in_target_is_shown(make_window):
    window = make_window()
    card = record(window, VOICE)
    assert card.time_in_target.isHidden()
    window.set_target(card.data)
    assert "pitch" in card.time_in_target.text() and "pitch + resonance together" in card.time_in_target.text()


def test_set_as_target_scores_the_take_it_came_from(make_window):
    window = make_window()
    card = record(window, REALISTIC)
    assert card.quality["tier"] != "poor"
    window.set_target(card.data)
    tracks = card.data["tracks"]
    assert window.profile.time_in_target("delta_f", tracks["delta_f"]) >= 0.6
    assert window.profile.time_in_target("pitch", tracks["f0"]) >= 0.6
    assert window.profile.time_in_both(tracks["f0"], tracks["delta_f"]) >= 0.5


def test_a_take_cannot_set_targets_it_cannot_support(make_window):
    window = make_window()
    window.profile.set_target("delta_f", [1000, 1100])
    high = copy.copy(record(window, VOICE).data)
    high.update(pitch=420.0)
    window.set_target(high)
    assert window.profile.target("delta_f") == [1000, 1100]          # kept, not replaced
    assert window.profile.target("pitch") is not None
    assert "Not set from this take" in window.status_label.text()
    window.set_target(poor_data(window))
    assert "too noisy" in window.status_label.text()
    assert window.profile.target("pitch") is not None                # a poor take changes nothing


def test_targets_dialog_starts_from_baseline_or_preset(app, tmp_path):
    profile = Profile(tmp_path / "home")
    dialog = TargetsDialog(profile)
    low, high = profile_store.TARGET_PRESETS["speak"]["targets"]["pitch"]
    check, spins, hint = dialog.rows["pitch"]
    assert (spins[0].value(), spins[1].value()) == (low, high) and not check.isChecked()
    assert "yours may differ" in hint.text()
    assert dialog.start_from.itemText(1) == profile_store.TARGET_PRESETS["speak"]["label"]
    assert "baseline" not in dialog.start_from.itemText(1).lower()

    profile.baseline = {"pitch": 200.0, "delta_f": 1000.0, "recorded": "today"}
    dialog = TargetsDialog(profile)
    _, spins, hint = dialog.rows["pitch"]
    assert spins[0].value() < 200 < spins[1].value() and "baseline" in hint.text()
    dialog._start_from(dialog.start_from.findData("baseline"))
    assert dialog.rows["delta_f"][0].isChecked()
    assert dialog.rows["delta_f"][1][0].value() < 1000 < dialog.rows["delta_f"][1][1].value()
    dialog._start_from(dialog.start_from.findData("speak"))
    assert dialog.rows["delta_f"][1][0].value() == 1100 and "typical" in dialog.rows["delta_f"][2].text()


def entry(days, **values):
    start = datetime(2026, 9, 7, 12)
    return {"kind": "take", "time": (start + timedelta(days=days)).isoformat(), "mode": "speak"} | values


def test_progress_leaves_out_poor_takes_and_unlike_tasks():
    entries = [entry(0, delta_f=1000, tier="good"), entry(1, delta_f=900, tier="poor"),
               entry(2, delta_f=1010, tier="fair"), entry(3, delta_f=1200, task="passage: Rainbow"),
               entry(4, delta_f=1100, exercise={"name": "Scale"}, tier="good")]
    assert list(select(entries, "delta_f")[1]) == [1000, 1010, 1200, 1100]
    assert list(select(entries, "delta_f", include_poor=True)[1]) == [1000, 900, 1010, 1200, 1100]
    assert list(select(entries, "delta_f", task="free speech")[1]) == [1000, 1010]
    assert list(select(entries, "delta_f", task="Scale")[1]) == [1100]


def test_progress_says_nothing_about_a_trend_from_few_takes():
    few = [entry(0, delta_f=1000), entry(1, delta_f=1010), entry(8, delta_f=1100), entry(9, delta_f=1110)]
    text = change_text("delta_f", *select(few, "delta_f"))
    assert "too few" in text and "since week" not in text and "4 takes" in text


def test_progress_says_nothing_when_the_change_is_within_the_noise():
    noisy = [entry(d, delta_f=v) for d, v in ((0, 900), (1, 1100), (2, 1000), (8, 1120), (9, 920), (10, 1010))]
    text = change_text("delta_f", *select(noisy, "delta_f"))
    assert "no clear change" in text and "6 takes" in text
    clear = [entry(d, delta_f=v) for d, v in ((0, 1000), (1, 1005), (2, 995), (8, 1100), (9, 1105), (10, 1095))]
    text = change_text("delta_f", *select(clear, "delta_f"))
    assert "+100 Hz since week of Sep 7" in text


def test_progress_dialog_defaults(app):
    entries = [entry(0, pitch=200.0, tier="good"), entry(1, pitch=205.0, tier="poor"),
               entry(2, pitch=202.0, tier="fair"), entry(3, pitch=150.0, task="passage: Rainbow", tier="good")]
    dialog = ProgressDialog(_empty_profile(), lambda: entries)
    assert dialog.task.currentData() == "free speech"                # the most common task
    assert dialog.charts["pitch"].dots.getData()[0].size == 2        # the poor take is out
    assert not dialog.left_out.isHidden() and "1 poor recording" in dialog.left_out.text()
    dialog.poor.setChecked(True)
    assert dialog.charts["pitch"].dots.getData()[0].size == 3
    dialog._task_picked(0)                                           # All tasks
    assert dialog.charts["pitch"].dots.getData()[0].size == 4
    assert [label.toPlainText() for label in dialog.charts["pitch"].counts]


def _empty_profile():
    import tempfile
    return Profile(tempfile.mkdtemp())


def test_summary_carries_tier_and_task(make_window):
    window = make_window()
    card = record(window, VOICE)
    summary = profile_store.summarize(card.data)
    assert summary["tier"] in ("good", "fair", "poor") and summary["task"] == "free speech"
    assert profile_store.summarize(card.data | {"mode": "sing", "task": None})["task"] == "free singing"
    assert profile_store.summarize(card.data | {"exercise": {"name": "Five-note scale"}, "task": None})["task"] == "Five-note scale"


class FakeClock:
    now = 1000.0

    def __call__(self):
        return self.now


def test_playback_playhead_stop_seek_and_compare(make_window, monkeypatch):
    import sounddevice as sd
    played = []
    monkeypatch.setattr(sd, "play", lambda audio, rate, **k: played.append(len(audio)))
    stops = []
    monkeypatch.setattr(sd, "stop", lambda *a, **k: stops.append(1))
    window = make_window()
    card = record(window, VOICE)
    other = record(window, synth_vowel(180, MALE_FORMANTS, seconds=2, noise=0.05))
    n = len(card.audio())
    clock = card.clock = FakeClock()
    card.play_audio()
    assert card.playing and "Stop" in card.play_btn.text() and played[-1] == n
    clock.now += 1.0
    card._tick()
    assert card.graph.playheads[0].isVisible() and abs(card.graph.playheads[0].value() - 1.0) < 1e-6
    card.graph.seek_requested.emit(2.0)                              # click on the graph while playing
    assert card.playing and played[-1] == n - 2 * SR
    clock.now += 0.5
    card._tick()
    assert abs(card.graph.playheads[0].value() - 2.5) < 1e-6
    card.play_audio()                                                # stop
    assert not card.playing and not card.graph.playheads[0].isVisible() and stops

    card.graph.seek_requested.emit(1.5)                              # click while stopped: start there next
    assert card.start_at == 1.5 and card.graph.playheads[0].isVisible()
    card.play_audio()
    assert played[-1] == n - int(1.5 * SR)
    card.stop_playback()

    options = card.compare_options(card)
    assert options and options[0][1] is other.data
    card.set_compare(other.data)
    assert card.graph.overlay and "Comparing" in card.compare_label.text()
    card.play_audio()
    assert played[-1] > n + len(other.data["audio"])        # this take, a gap, the other
    clock.now += n / SR + 0.7
    card._tick()
    assert "Playing" in card.compare_label.text()
    card.stop_playback()
    card.set_compare(None)
    assert not card.graph.overlay


def test_starting_one_take_stops_another(make_window, monkeypatch):
    import sounddevice as sd
    monkeypatch.setattr(sd, "play", lambda *a, **k: None)
    monkeypatch.setattr(sd, "stop", lambda *a, **k: None)
    window = make_window()
    first, second = record(window, VOICE), record(window, VOICE)
    first.play_audio()
    second.play_audio()
    assert second.playing and not first.playing


def test_graph_click_maps_to_seconds(make_window):
    window = make_window()
    card = record(window, VOICE)
    assert isinstance(card.graph, AnalysisGraph)
    heard = []
    card.graph.seek_requested.connect(heard.append)
    card.graph.seek_requested.emit(99.0)
    assert heard == [99.0]
    assert card.graph.duration() == pytest.approx(len(VOICE) / SR, abs=0.1)


def test_session_length_and_rest_reminder(make_window):
    window = make_window()
    clock = window.core.clock = FakeClock()
    assert window.session_label.isHidden() and window.rest_label.isHidden()
    record(window, VOICE)
    assert not window.session_label.isHidden() and window.session_label.text() == "Session 0 min"
    clock.now += 10 * 60
    window.update_session()
    assert window.session_label.text() == "Session 10 min" and window.rest_label.isHidden()
    clock.now += 16 * 60
    window.update_session()
    assert not window.rest_label.isHidden() and "26 minutes" in window.rest_label.text()
    assert "unverified" in window.rest_label.toolTip()
    window.rest_label.hide()
    window.update_session()                                          # once per interval, not every tick
    assert window.rest_label.isHidden()


def test_voice_map_rebuilds_from_takes(make_window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    folder = tmp_path / "takes"
    window = make_window(folder)
    record(window, VOICE)
    window.voice_map.clear()
    assert window.voice_map.data.counts.sum() == 0
    window.voice_map.rebuild_requested.emit()
    assert wait_for(lambda: "rebuilt" in window.status_label.text())
    assert window.voice_map.data.counts.sum() > 50


def test_formant_ceiling_is_fixed_once(make_window):
    window = make_window()
    assert window.profile.formant_ceiling is None
    for _ in range(app_core.CEILING_TAKES - 1):
        record(window, VOICE)
        assert window.profile.formant_ceiling is None            # not enough takes to choose from yet
    record(window, VOICE)
    ceiling = window.profile.formant_ceiling
    assert ceiling in FORMANT_CEILINGS
    assert window.engine.analyzer.formant_ceiling == ceiling      # the live meters use it
    assert Profile(window.profile.directory).formant_ceiling == ceiling      # kept in the profile
    window.core.note_ceiling(dict(window.cards[-1].data, formant_ceiling=ceiling + 150))
    assert window.profile.formant_ceiling == ceiling              # later takes don't move it
    after = record(window, VOICE)
    assert after.data["formant_ceiling"] == ceiling               # and every analysis is given it
    assert window.engine.analyzer.formant_ceiling == ceiling


def test_vowel_check_ceiling_wins(make_window):
    window = make_window()
    window.core.events.emit("vowel_check_done", {"formant_ceiling": 4500, "vowels": {}})
    assert window.profile.formant_ceiling == 4500 and window.engine.analyzer.formant_ceiling == 4500
    for _ in range(app_core.CEILING_TAKES):
        record(window, VOICE)
    assert window.profile.formant_ceiling == 4500                 # takes never override it
    window.core.use_vowel_ceiling({"formant_ceiling": 5000})      # a later vowel check always does
    assert window.profile.formant_ceiling == 5000


def test_mic_check_result_is_stored(make_window, monkeypatch):
    window = make_window()
    live = window.live_panel
    assert live.engine is window.engine
    monkeypatch.setattr(live, "show_mic_check", lambda: live.mic_check_done.emit({"level_db": -30.0}))
    window.first_run_mic_check()
    assert window.profile.mic_check == {"level_db": -30.0} and window.status_label.text() == "Mic check saved"
    window.profile.mic_check = None
    window.first_run_mic_check()                                     # asked once, never again by itself
    assert window.profile.mic_check is None


def test_noise_floor_is_saved_on_close_and_kept_without_one(make_window):
    window = make_window()
    window.engine.analyzer.noise_floor = -61.5
    window.close()
    assert window.profile.noise_floor == -61.5
    assert window.core.stream_options() == {"noise_floor": -61.5}
    window.engine.analyzer.noise_floor = None
    window.close()
    assert window.profile.noise_floor == -61.5                       # an unheard floor doesn't erase it


def test_card_rebuilds_after_reanalysis(make_window):
    window = make_window()
    card = record(window, VOICE)
    old_graph = card.graph
    card.reload()
    assert card.graph is not old_graph and card.bars and card.body is not None
    assert card.findChildren(TakeCard) == []
