"""Takes, targets and progress UI (spec 07, redrawn in phase 10 spec 06): re-analysis, the take card's
quality, tips and targets, the Takes screen's filters, Progress and the targets editor, playback and
comparison. Offscreen Qt on a VoiceLabCore with a fake engine: no mic and no speakers. The session clock,
the rest nudge, the Voice Map rebuild, the mic check, the noise floor and the formant ceiling are core
behaviours, checked without a UI in test_core.py."""
import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
from PyQt6.QtWidgets import QApplication  # noqa: E402

import coach  # noqa: E402
import profile_store  # noqa: E402
import progress  # noqa: E402
import take_view  # noqa: E402
from analysis import analyze_take  # noqa: E402
from profile_store import Profile  # noqa: E402
from progress import change_text, select  # noqa: E402
from take_register import analyze_recording, finish_take  # noqa: E402
from take_store import ANALYSIS_VERSION, TakeStore, is_outdated  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402
import test_report_ui as shared  # noqa: E402
from test_report_ui import REALISTIC, VOICE, click, poor, record  # noqa: E402
from ui.components import Collapsible  # noqa: E402
from ui.progress.progress_screen import ProgressMini, ProgressScreen  # noqa: E402
from ui.takes import take_card as take_card_module  # noqa: E402
from ui.takes.take_card import TakeCard  # noqa: E402
from ui.takes.take_graph import TakeGraph  # noqa: E402
from ui.takes.takes_screen import TakesScreen  # noqa: E402
from ui.takes.targets_editor import TargetsEditor  # noqa: E402


# The shared fixtures: a core on qt_events with a fake engine, a fake speaker, closed sections.
make_core, speaker, fresh_collapsibles = shared.make_core, shared.speaker, shared.fresh_collapsibles


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


def with_graph():
    Collapsible.memory["take_card.graph"] = True


# --- RE-ANALYSIS ---

def test_old_take_is_reanalysed_without_touching_the_wav(make_core, tmp_path):
    folder = tmp_path / "takes"
    take = old_take(folder)
    wav = folder / take["file"]
    before, mtime = sha(wav), wav.stat().st_mtime_ns
    core = make_core(folder)
    data = core.takes()[0]
    assert data["analysis_version"] == ANALYSIS_VERSION and not is_outdated(data)
    assert data["pitch_range"] < 20                       # the stored 47 st was ghosts
    sidecar = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["analysis_version"] == ANALYSIS_VERSION and sidecar["mode"] == "speak"
    assert sha(wav) == before and wav.stat().st_mtime_ns == mtime
    assert data["uid"] == take["uid"] and data["file"] == take["file"]
    assert "Re-analysed 1" in core.status[0]


def test_card_rebuilds_after_reanalysis(make_core):
    with_graph()
    core = make_core()
    take = record(core)
    card = TakeCard(core, take["uid"])
    old_graph = card.graph
    take["tracks"] = dict(take["tracks"])           # re-analysis replaces the tracks
    core.events.emit("take_updated", take)
    QApplication.processEvents()
    assert card.graph is not None and card.graph is not old_graph and card.metric_cards
    assert card.findChildren(TakeCard) == []


def test_sung_take_is_analysed_in_sing_mode(make_core):
    core = make_core()
    core.set_mode("sing")
    high = synth_vowel(700, MALE_FORMANTS, seconds=2, noise=0.02)
    data = record(core, high)
    assert data["mode"] == "sing" and abs(data["pitch"] - 700) < 20     # speech mode would cut it off
    assert data["voiced_fraction"] > 0.5 and data["analysis_version"] == ANALYSIS_VERSION
    assert analyze_recording(high, mode="speak")["mode"] == "speak"
    card = TakeCard(core, data["uid"])
    assert card.head["mode"] == "sing" and "Sing" in card.subtitle.text()


# --- QUALITY, TIPS AND TARGETS ON THE CARD ---

def test_card_shows_quality_and_tips(make_core):
    core = make_core(goal="feminine")
    take = record(core)
    card = TakeCard(core, take["uid"])
    assert card.quality_dot.label == take_view.TIER_TEXT[card.head["tier"]]
    tips = card.report["tips"]
    assert 1 <= len(tips) <= 3 and len(card.summary.tip_rows) == len(tips)
    assert any("resonance" in t["text"].lower() or "pitch" in t["text"].lower() for t in tips)


def test_poor_take_shows_one_banner_and_no_readings(make_core):
    core = make_core()
    data = poor(core, record(core))
    card = TakeCard(core, data["uid"])
    assert card.head["tier"] == "poor" and "Poor" in card.quality_dot.label
    assert card.banner is not None and card.metric_cards == {}
    assert card.report["tips"] == [] and card.report["overall"]["position"] is None
    Collapsible.memory["take_card.register"] = True
    card.rebuild()
    assert "can't be judged" in card.register_info["headline"] and card.register_info["confidence"] == "none"
    assert card.register_info["shares"] == []


def test_high_pitch_marks_only_resonance_untrusted(make_core):
    core = make_core()
    data = copy.deepcopy(record(core))
    data.update(pitch=420.0, uid="high-1", file=None, time="2030-01-01T10:00:00")
    core._add(data)
    card = TakeCard(core, data["uid"])
    assert card.metric_cards["delta_f"].unavailable
    assert f"{coach.FORMANT_MAX_F0:.0f} Hz" in card.metric_cards["delta_f"].headline.text()
    assert not card.metric_cards["pitch"].unavailable


def test_joint_time_in_target_is_shown(make_core):
    core = make_core()
    take = record(core)
    card = TakeCard(core, take["uid"])
    assert card.time_in_target.isHidden()
    core.set_target(take["uid"])
    QApplication.processEvents()
    assert "pitch" in card.time_in_target.text() and "pitch + resonance together" in card.time_in_target.text()
    assert card.metric_cards["pitch"].bar.target is not None


def test_set_as_target_from_the_card_scores_the_take_it_came_from(make_core):
    core = make_core()
    take = record(core, REALISTIC)
    card = TakeCard(core, take["uid"])
    assert card.head["tier"] != "poor"
    card._fill_menu()
    next(a for a in card.menu.actions() if a.text() == "Use as target").trigger()
    tracks, profile = take["tracks"], core.profile
    assert profile.time_in_target("delta_f", tracks["delta_f"]) >= 0.6
    assert profile.time_in_target("pitch", tracks["f0"]) >= 0.6
    assert profile.time_in_both(tracks["f0"], tracks["delta_f"]) >= 0.5


def test_a_take_cannot_set_targets_it_cannot_support(make_core):
    core = make_core()
    core.profile.set_target("delta_f", [1000, 1100])
    high = copy.deepcopy(record(core))
    high.update(pitch=420.0)
    core.set_target(high)
    assert core.profile.target("delta_f") == [1000, 1100]          # kept, not replaced
    assert core.profile.target("pitch") is not None and "Not set from this take" in core.status[0]
    core.set_target(poor(core, high))
    assert "too noisy" in core.status[0] and core.profile.target("pitch") is not None


def test_summary_carries_tier_and_task(make_core):
    core = make_core()
    data = record(core)
    summary = profile_store.summarize(data)
    assert summary["tier"] in ("good", "fair", "poor") and summary["task"] == "free speech"
    assert profile_store.summarize(data | {"mode": "sing", "task": None})["task"] == "free singing"
    assert profile_store.summarize(data | {"exercise": {"name": "Five-note scale"}, "task": None})["task"] == \
        "Five-note scale"


# --- PLAYBACK AND COMPARISON ---

class FakeClock:
    now = 1000.0

    def __call__(self):
        return self.now


def test_playback_playhead_stop_seek_and_compare(make_core, speaker):
    with_graph()
    core = make_core()
    take = record(core)
    other = record(core, synth_vowel(180, MALE_FORMANTS, seconds=2, noise=0.05))
    card = TakeCard(core, take["uid"])
    n = len(core.take_audio(take["uid"]))
    clock = core.player.clock = FakeClock()
    card.play_audio()
    assert card.playing and "Stop" in card.play_btn.text() and len(speaker.played[-1]) == n
    clock.now += 1.0
    card.playback.tick()
    assert card.graph.playheads[0].isVisible() and abs(card.graph.playheads[0].value() - 1.0) < 1e-6
    card.graph.seek_requested.emit(2.0)                    # a click on the graph while playing
    assert card.playing and len(speaker.played[-1]) == n - 2 * SR
    clock.now += 0.5
    card.playback.tick()
    assert abs(card.graph.playheads[0].value() - 2.5) < 1e-6
    card.play_audio()                                      # stop
    assert not card.playing and not card.graph.playheads[0].isVisible() and speaker.stops

    card.graph.seek_requested.emit(1.5)                    # a click while stopped: start there next
    assert card.start_at == 1.5 and card.graph.playheads[0].isVisible()
    card.play_audio()
    assert len(speaker.played[-1]) == n - int(1.5 * SR)
    card.stop_playback()

    options = core.compare_options(take["uid"])
    assert options and options[0][1] is other
    card.set_compare(other)
    assert card.graph.overlay and "Comparing" in card.compare_label.text()
    assert "compare" in card.play_btn.text()
    card.play_audio()
    assert len(speaker.played[-1]) > n + len(core.take_audio(other["uid"]))      # this take, a gap, the other
    clock.now += n / SR + 0.7
    card.playback.tick()
    assert "Playing" in card.compare_label.text()
    card.stop_playback()
    card.set_compare(None)
    assert not card.graph.overlay


def test_starting_one_take_stops_another(make_core):
    core = make_core()
    first, second = TakeCard(core, record(core)["uid"]), TakeCard(core, record(core)["uid"])
    first.play_audio()
    second.play_audio()
    assert second.playing and not first.playing


def test_graph_click_maps_to_seconds_and_uses_the_fixed_speak_axis(make_core):
    with_graph()
    core = make_core()
    card = TakeCard(core, record(core)["uid"])
    assert isinstance(card.graph, TakeGraph)
    heard = []
    card.graph.seek_requested.connect(heard.append)
    card.graph.seek_requested.emit(99.0)
    assert heard == [99.0]
    assert card.graph.duration() == pytest.approx(len(VOICE) / SR, abs=0.1)
    assert (card.graph.axis["lo"], card.graph.axis["hi"]) == take_view.SPEAK_AXIS_HZ
    assert [z["id"] for z in card.graph.axis["zones"]["zones"]] == ["masculine", "overlap", "feminine"]
    lines = take_view.formant_lines(card.data["tracks"])
    import numpy as np
    unvoiced = np.isnan(np.asarray(card.data["tracks"]["f0"], dtype=float))
    assert all(np.isnan(v[unvoiced]).all() for v in lines.values())      # no formants drawn in the gaps
    card.raw_toggle.set_checked(True, emit=True)
    assert card.graph.raw


def test_shift_view_uses_the_core(make_core):
    Collapsible.memory["take_card.shift"] = True
    core = make_core()
    card = TakeCard(core, record(core)["uid"])
    view = card.shift_view
    assert view is not None and view.session.runner is core.runner and view.session.player is core.player
    view.sliders["semitones"].setValue(view.sliders["semitones"].value() + 4)
    assert not view.session.neutral() and view.shifted_btn.isEnabled()


# --- THE TAKES SCREEN ---

def takes_for_screen(core):
    speech = record(core)
    core.rename(speech["uid"], "Rainbow passage")
    core.set_mode("sing")
    sung = record(core, synth_vowel(330, MALE_FORMANTS, seconds=2, noise=0.02))
    core.set_mode("speak")
    bad = poor(core, record(core))
    return speech, sung, bad


def test_takes_screen_lists_filters_and_searches(make_core):
    core = make_core()
    speech, sung, bad = takes_for_screen(core)
    screen = TakesScreen(core)
    screen.resize(1600, 900)
    assert [t["uid"] for t in screen.visible_takes()][0] == bad["uid"]           # newest first
    assert screen.selected == bad["uid"] and screen.full.uid == bad["uid"]
    screen.search.setText("rainbow")
    screen.refresh()
    assert [t["uid"] for t in screen.visible_takes()] == [speech["uid"]]
    assert screen.selected == speech["uid"]
    screen.search.setText("")
    screen.mode.set_current_key("sing")
    assert [t["uid"] for t in screen.visible_takes()] == [sung["uid"]]
    screen.mode.set_current_key(None)
    screen.mode.setCurrentIndex(0)
    screen.tier.set_current_key("usable")
    assert bad["uid"] not in [t["uid"] for t in screen.visible_takes()]
    screen.tier.setCurrentIndex(0)
    screen.task.set_current_key("free singing")
    assert [t["uid"] for t in screen.visible_takes()] == [sung["uid"]]
    screen.task.setCurrentIndex(0)
    screen.show_take(speech["uid"])
    assert screen.selected == speech["uid"] and screen.cards[speech["uid"]].selected
    click(screen.cards[sung["uid"]])
    assert screen.selected == sung["uid"] and screen.full.uid == sung["uid"]
    now = datetime.now()
    assert take_view.matches(speech, days=7, now=now)
    assert not take_view.matches(speech, days=7, now=now + timedelta(days=30))


def test_takes_screen_delete_asks_first_and_selects_the_next(make_core, monkeypatch):
    core = make_core()
    first, second = record(core), record(core)
    screen = TakesScreen(core)
    asked = []
    monkeypatch.setattr(take_card_module, "ask", lambda parent, q: asked.append(q) or True)
    screen.show_take(second["uid"])
    screen.full.delete()
    QApplication.processEvents()
    assert asked == [take_view.delete_question(second, 2)[0]]
    assert core.take(second["uid"]) is None and second["uid"] not in screen.cards
    assert screen.selected == first["uid"]


def test_takes_screen_empty_and_older_pages(make_core, tmp_path):
    import app_core
    core = make_core()
    screen = TakesScreen(core)
    assert screen.empty is not None and screen.full is None
    asked = []
    screen.record_requested.connect(lambda: asked.append(1))
    screen.empty.action.click()
    assert asked == [1]
    folder = tmp_path / "many"
    store = TakeStore(folder)
    base = finish_take(analyze_take(VOICE))
    base["analysis_version"] = ANALYSIS_VERSION
    for i in range(app_core.TAKES_PAGE + 3):
        store.save(copy.deepcopy(base) | {"time": f"2026-09-{1 + i // 12:02d}T{10 + i % 12:02d}:00:00",
                                          "uid": f"take-{i}"})
    many = make_core(folder)
    screen = TakesScreen(many)
    assert len(screen.visible_takes()) == app_core.TAKES_PAGE and not screen.older_btn.isHidden()
    screen.show_take("take-0")                            # the oldest: older pages load to reach it
    assert screen.selected == "take-0" and many.older_count() == 0 and screen.older_btn.isHidden()


# --- TARGETS ---

def test_targets_editor_starts_from_baseline_or_preset_and_applies(make_core):
    core = make_core()
    editor = TargetsEditor(core)
    low, high = profile_store.TARGET_PRESETS["speak"]["targets"]["pitch"]
    row = editor.rows["pitch"]
    assert (row.spins[0].value(), row.spins[1].value()) == (low, high) and not row.toggle.isChecked()
    assert "yours may differ" in row.hint.text()
    assert editor.start_from.itemText(1) == profile_store.TARGET_PRESETS["speak"]["label"]
    assert row.bar.zones and row.bar.target is None
    assert "just exploring" in editor.goal.text()

    core.profile.baseline = {"pitch": 200.0, "delta_f": 1000.0, "recorded": "today"}
    editor.reload()
    row = editor.rows["pitch"]
    assert row.spins[0].value() < 200 < row.spins[1].value() and "baseline" in row.hint.text()
    assert row.bar.baseline == 200.0
    editor._start_from("baseline")
    assert editor.rows["delta_f"].toggle.isChecked() and editor.dirty
    assert editor.rows["delta_f"].spins[0].value() < 1000 < editor.rows["delta_f"].spins[1].value()
    editor._start_from("speak")
    assert editor.rows["delta_f"].spins[0].value() == 1100 and "typical" in editor.rows["delta_f"].hint.text()
    assert list(editor.rows["delta_f"].bar.target) == [1100, 1300]
    editor.rows["h1h2"].toggle.set_checked(True, emit=True)
    heard = []
    core.events.subscribe("profile_changed", lambda: heard.append(1))
    editor.apply()
    QApplication.processEvents()
    assert core.profile.target("delta_f") == [1100.0, 1300.0] and core.profile.target("h1h2") is not None
    assert core.profile.target("ring") is None and heard and not editor.dirty
    assert Profile(core.profile.directory).target("delta_f") == [1100.0, 1300.0]      # saved
    editor.clear_baseline()
    assert core.profile.baseline is None and not editor.clear_btn.isEnabled()


# --- PROGRESS ---

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
    assert "no clear change" in change_text("delta_f", *select(noisy, "delta_f"))
    clear = [entry(d, delta_f=v) for d, v in ((0, 1000), (1, 1005), (2, 995), (8, 1100), (9, 1105), (10, 1095))]
    assert "+100 Hz since week of Sep 7" in change_text("delta_f", *select(clear, "delta_f"))


def test_progress_screen_defaults(make_core, monkeypatch):
    core = make_core()
    entries = [entry(0, pitch=200.0, delta_f=1000.0, tier="good"), entry(1, pitch=205.0, delta_f=990.0, tier="poor"),
               entry(2, pitch=202.0, delta_f=1010.0, tier="fair"),
               entry(8, pitch=210.0, delta_f=1020.0, tier="good"),
               entry(3, pitch=150.0, task="passage: Rainbow", tier="good")]
    monkeypatch.setattr(core, "progress_entries", lambda: entries)
    screen = ProgressScreen(core)
    assert screen.shown["task"] == "free speech"                      # the most common task
    pitch = screen.charts["pitch"]
    assert len(pitch.data["values"]) == 3                             # the poor take is out
    assert "too few" in pitch.subtitle_label.text()                    # n < 5: no trend claimed
    assert [z["id"] for z in pitch.data["zones"]["zones"]] == ["masculine", "overlap", "feminine"]
    assert not screen.left_out.isHidden() and "1 poor recording" in screen.left_out.text()
    assert "overall" in screen.charts and screen.charts["overall"].data["values"].size == 3
    screen.poor.set_checked(True, emit=True)
    assert len(screen.charts["pitch"].data["values"]) == 4
    screen.task.set_current_key(None)
    screen.task.changed.emit(None)
    assert len(screen.charts["pitch"].data["values"]) == 5
    assert [label.toPlainText() for label in screen.charts["pitch"].counts]
    mini = ProgressMini(core)
    keys = [s["key"] for s in mini.data["series"]]
    assert keys == ["pitch", "delta_f", "overall"]


def test_progress_screen_empty(make_core):
    core = make_core()
    screen = ProgressScreen(core)
    assert screen.shown["empty"] and screen.empty is not None
    assert progress.mini([], core.profile)["series"][0]["latest"] is None
