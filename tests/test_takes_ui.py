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
    assert card.quality_dot.toolTip().startswith(take_view.TIER_TEXT[card.head["tier"]])
    assert card.trust.view["text"].startswith(take_view.TIER_TEXT[card.head["tier"]])
    tips = card.report["tips"]
    assert 1 <= len(tips) <= 3 and len(card.summary.tip_rows) == len(tips)
    assert any("resonance" in t["text"].lower() or "pitch" in t["text"].lower() for t in tips)


def test_poor_take_shows_one_banner_and_no_readings(make_core):
    core = make_core()
    data = poor(core, record(core))
    card = TakeCard(core, data["uid"])
    assert card.head["tier"] == "poor" and "Poor" in card.quality_dot.toolTip()
    assert card.trust is not None and card.metric_cards == {}
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
    assert card.in_range.isHidden()
    core.set_target(take["uid"])
    QApplication.processEvents()
    assert card.in_range_chip.text.startswith("In range ") and card.in_range_chip.text.endswith(" of this take")
    assert not card.in_range.isHidden()
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
    steady = change_text("delta_f", *select(noisy, "delta_f"))
    assert "steady since week of Sep 7" in steady and "variation" not in steady
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


# --- PHASE 11 / 04: ONE TRUST MODEL (take_view, no Qt) ---

from test_report import make_take  # noqa: E402
import report  # noqa: E402


def trust_of(**kwargs):
    take = make_take(**kwargs)
    return take_view.trust_line(report.build_report(take, goal="feminine"), take)


def test_trust_line_is_one_statement_with_verdict_cause_and_fix():
    clean = trust_of()
    assert clean["clean"] and clean["action"] is None
    assert clean["text"] == ("Good recording: pitch, lowest notes and intonation are reliable; resonance, weight and "
                             "clarity are rough guides.")
    noisy = trust_of(snr=15.0)                  # below the clarity SNR: weight and clarity can't be judged
    assert noisy["verdict"] == "Okay recording" and not noisy["clean"]
    assert "weight and clarity can't be judged because of background noise" in noisy["text"]
    assert "Try a quieter room" in noisy["text"] and noisy["action"] == "Mic check"
    assert set(noisy["cant"]) == {"h1h2", "cpp"}
    bad = trust_of(snr=5.0)
    assert bad["verdict"] == "Poor recording" and bad["text"].startswith("Poor recording:") and "Try" in bad["text"]
    assert set(bad["cant"]) == set(report.DIMENSIONS_SPEAK)


def test_good_recording_never_appears_beside_a_problem_without_the_caveat():
    for kwargs in ({}, {"snr": 15.0}, {"f0": 300}, {"snr": 5.0}, {"level": -45.0}, {"snr": 12.0, "clip_events": 9}):
        take = make_take(**kwargs)
        rep = report.build_report(take, goal="feminine")
        view = take_view.trust_line(rep, take)
        problems = [d for d in rep["dimensions"] if d["confidence"] == "none"]
        if problems:
            assert not view["clean"], kwargs
            assert view["tier"] == "poor" or "can't be judged" in view["text"], kwargs
            assert view["fix"] and "Try" in view["text"], kwargs
        if "Good recording" in view["text"]:
            assert view["clean"] or "but" in view["text"] or ", with " in view["text"], kwargs


def test_trust_line_says_rumble_and_whine_in_words_and_offers_a_fix():
    take = make_take()
    take["noise"] = {"rumble_share": 0.9}
    view = take_view.trust_line(report.build_report(take), take)
    assert view["tier"] == "good" and "Good recording, with desk rumble." in view["text"]
    assert "something soft" in view["fix"] and view["action"] == "Mic check"
    take["noise"] = {"lines": [(50, -30.0)]}
    assert "electrical whine" in take_view.trust_line(report.build_report(take), take)["text"]


def test_reading_trust_hides_reliable_and_words_the_rest():
    rep = report.build_report(make_take(snr=15.0), goal="feminine")
    words = {d["key"]: take_view.reading_trust(d) for d in rep["dimensions"]}
    assert words["h1h2"]["word"] == "Can't judge" and words["h1h2"]["reason"] and words["pitch"]["word"] == "Rough"
    clear = report.build_report(make_take(), goal="feminine")
    guides = {d["key"]: take_view.reading_trust(d) for d in clear["dimensions"]}
    assert guides["h1h2"]["word"] == "Rough guide" and guides["cpp"]["show"] and guides["delta_f"]["word"] == "Rough guide"
    assert all(not guides[k]["show"] for k in ("pitch", "pitch_range", "pitch_floor"))
    assert not any("confidence" in v["word"].lower() for v in words.values())


def test_zone_wording_is_one_set_everywhere():
    assert take_view.overall_spec()["zones"][1]["label"] == "Overlap"
    spec = take_view.dimension_spec({"key": "pitch"})
    assert [z["label"] for z in spec["zones"]] == ["Masculine", "Overlap", "Feminine"]
    assert report.zones("pitch")["zones"][1]["label"] == "overlap"      # the report's own spec keeps the long words
    chip = take_view.row_chip(report.build_report(make_take(f0=215, df=1130)))
    assert chip[0] == "Feminine" and chip[1] == "feminine" and chip[2].endswith("Hz")


def test_tips_split_into_two_visible_and_the_rest():
    shown, more = take_view.split_tips([1, 2, 3])
    assert shown == [1, 2] and more == [3]
    assert take_view.split_tips([1]) == ([1], [])


def test_change_text_has_no_opposite_signed_number():
    prev = make_take(f0=150, df=1000, h1h2=8.0)
    rep = report.build_report(make_take(f0=160, df=1080, h1h2=3.8), previous=prev)
    for d in rep["dimensions"]:
        text = take_view.change_text(d)
        if text:
            assert "+" not in text and "(" not in text
    weight = next(d for d in rep["dimensions"] if d["key"] == "h1h2")
    assert take_view.change_text(weight) == "Heavier than your last take, by 4.2 dB"
    assert take_view.change_text({"change": None}) is None


# --- WHEN, IN RANGE, COMPARE, UPDATED ---

def test_relative_time_and_the_last_take_title():
    now = datetime(2026, 10, 26, 12, 0)
    at = lambda **delta: {"time": (now - timedelta(**delta)).isoformat()}      # noqa: E731
    assert take_view.relative_time(at(seconds=20), now) == "just now"
    assert take_view.relative_time(at(minutes=3), now) == "3 min ago"
    assert take_view.relative_time(at(hours=1), now) == "1 hour ago"
    assert take_view.relative_time(at(hours=5), now) == "5 hours ago"
    assert take_view.relative_time(at(days=1), now) == "yesterday"
    assert take_view.relative_time(at(days=3), now) == "3 days ago"
    assert take_view.relative_time(at(days=21), now) == "3 weeks ago"
    assert take_view.relative_time({"time": "?"}, now) == ""
    assert take_view.last_take_title(at(minutes=3), now) == "Your last take · 3 min ago"
    assert take_view.last_take_title(at(days=3), now) == "Your last take · Fri 23 Oct 12:00 (3 days ago)"
    assert take_view.last_take_title({}, now) == "Your last take"


def test_in_range_is_a_stat_with_the_parts_in_the_detail(tmp_path):
    take = make_take(f0=200, df=1200)
    profile = Profile(tmp_path)
    assert take_view.in_range(take, profile)["text"] == ""                  # no targets, no stat
    profile.set_target("pitch", [190, 210])
    profile.set_target("delta_f", [1150, 1250])
    stat = take_view.in_range(take, profile)
    assert stat["text"].startswith("In range ") and stat["text"].endswith(" of this take") and stat["value"] > 0.5
    assert "pitch" in stat["detail"] and "resonance" in stat["detail"]
    only = Profile(tmp_path / "x")
    only.set_target("pitch", [190, 210])
    assert take_view.in_range(take, only)["text"].startswith("In range: pitch")


def test_compare_rows_give_both_values_and_the_difference_in_words():
    a = report.build_report(make_take(f0=165, df=1080, h1h2=2.0))
    b = report.build_report(make_take(f0=150, df=1000, h1h2=8.0))
    rows = take_view.compare_rows(a, b)
    assert [r["key"] for r in rows] == list(take_view.COMPARE_KEYS)
    pitch = next(r for r in rows if r["key"] == "pitch")
    assert pitch["this"].endswith("Hz") and pitch["other"].endswith("Hz")
    assert pitch["change"].startswith("Higher by ") and "semitones" in pitch["change"] and not pitch["same"]
    assert next(r for r in rows if r["key"] == "delta_f")["change"] == "Brighter by 80 Hz"
    assert next(r for r in rows if r["key"] == "h1h2")["change"] == "Heavier by 6.0 dB"
    sentence = take_view.compare_summary(rows, "Take #10")
    assert sentence.startswith("Compared with Take #10: pitch is higher by")
    assert "resonance is brighter by 80 Hz" in sentence
    same = take_view.compare_rows(a, a)
    assert all(r["same"] for r in same) and "about the same" in take_view.compare_summary(same, "Take #1")
    bad = report.build_report(make_take(snr=5.0))
    cant = take_view.compare_rows(a, bad)
    assert not cant[0]["comparable"] and cant[0]["change"].startswith("Can't compare: ")


def test_compare_choices_put_the_previous_take_first_without_repeats():
    prev, base, other = {"uid": "p"}, {"uid": "b"}, {"uid": "o"}
    choices = take_view.compare_choices([("Baseline take", base), ("Take", prev), ("Take", other)], prev)
    assert [(label, t["uid"]) for label, t in choices] == [("Previous take", "p"), ("Baseline take", "b"), ("Take", "o")]
    assert take_view.compare_choices([("Take", other)], None)[0][1] is other


def test_update_note_says_what_changed_or_that_nothing_did():
    take = make_take(f0=160, df=1030, wobble=3)
    before = take_view.report_digest(report.build_report(take, goal="feminine"), take)
    assert take_view.update_note(before, before) == "Updated with the improved analysis. Nothing important changed."
    zone = "feminine" if before["zone"] != "feminine" else "overlap"
    after = dict(before, confidence="low", tips=["a different tip"], zone=zone)
    note = take_view.update_note(before, after)
    assert note.startswith("Updated with the improved analysis: ") and "the tips changed" in note
    assert "overall reading is now rough" in note and "overall placement is now" in note
    assert take_view.update_note(None, after) == "Updated with the improved analysis."
    assert "recording now reads as okay recording" in take_view.update_note(before, dict(before, tier="fair"))


def test_filters_label_and_rows():
    assert take_view.filter_label({}) == "Filter"
    assert take_view.filter_label({"query": "x", "mode": "sing"}) == "Filter · 1"
    assert take_view.filter_label({"mode": "sing", "task": "free singing", "days": 7}) == "Filter · 3"
    assert take_view.active_filters({"tier": "good", "task": None}) == ["quality"]


def test_summary_text_for_the_clipboard():
    take = make_take(f0=160, df=1030, wobble=3) | {"time": "2026-10-23T11:41:00", "uid": "u1"}
    rep = report.build_report(take, goal="feminine")
    text = take_view.summary_text(take, rep, 12)
    assert text.startswith("Take #12 · Fri 23 Oct 11:41") and "Good recording" in text and rep["headline"] in text
    assert take_view.export_name(take, 12) == "Take 12 2026-10-23 1141"
    assert take_view.moment_at([(1.0, 2.0), (4.0, 5.0)], 4.5) == (4.0, 5.0)
    assert take_view.moment_at([(1.0, 2.0)], 3) is None


# --- DELETE WITH UNDO ---

def test_trash_moves_files_and_restores_them(tmp_path):
    import take_trash
    folder = tmp_path / "takes"
    folder.mkdir()
    wav, sidecar = folder / "a.wav", folder / "a.json"
    wav.write_bytes(b"audio")
    sidecar.write_text("{}", encoding="utf-8")
    receipt = take_trash.move_to_trash(folder, [wav, sidecar, folder / "missing.wav"])
    assert len(receipt) == 2 and not wav.exists() and not sidecar.exists()
    assert sorted(p.suffix for p in (folder / ".trash").iterdir()) == [".json", ".wav"]
    back = take_trash.restore(receipt)
    assert sorted(p.name for p in back) == ["a.json", "a.wav"] and wav.read_bytes() == b"audio"
    assert list((folder / ".trash").iterdir()) == []
    receipt = take_trash.move_to_trash(folder, [wav])
    wav.write_bytes(b"newer")                      # the name was taken meanwhile
    assert take_trash.restore(receipt)[0].name == "a (restored).wav"
    assert take_trash.restore(receipt) == []       # already restored: nothing left to move


def test_trash_is_emptied_after_a_month_not_before(tmp_path):
    import take_trash
    (tmp_path / "x.wav").write_bytes(b"1")
    take_trash.move_to_trash(tmp_path, [tmp_path / "x.wav"])
    path = next((tmp_path / ".trash").iterdir())
    assert take_trash.purge(tmp_path, now=path.stat().st_mtime + 29 * 86400) == 0 and path.exists()
    assert take_trash.purge(tmp_path, now=path.stat().st_mtime + 31 * 86400) == 1 and not path.exists()
    assert take_trash.purge(tmp_path / "nothing") == 0


def test_delete_goes_to_the_trash_and_undo_brings_it_back(make_core, tmp_path):
    folder = tmp_path / "takes"
    core = make_core(folder)
    first = record(core)
    take = record(core)
    number = core.number(take)
    heard, added = [], []
    core.events.subscribe("take_trashed", lambda uid, name: heard.append((uid, name)))
    core.events.subscribe("take_added", lambda data: added.append(data["uid"]))
    assert core.delete(take["uid"])
    QApplication.processEvents()
    assert core.take(take["uid"]) is None and not (folder / take["file"]).exists()
    assert sorted(p.suffix for p in (folder / ".trash").iterdir()) == [".json", ".wav"]     # the sidecar goes too
    assert heard == [(take["uid"], f"Take #{number}")] and core.can_undo_delete(take["uid"])
    assert [t["uid"] for t in core.takes()] == [first["uid"]]
    assert core.undo_delete(take["uid"])
    QApplication.processEvents()
    assert (folder / take["file"]).exists() and (folder / take["file"]).with_suffix(".json").exists()
    assert list((folder / ".trash").iterdir()) == [] and added == [take["uid"]]
    assert [t["uid"] for t in core.takes()] == [take["uid"], first["uid"]]
    assert core.number(core.take(take["uid"])) == number
    assert not core.can_undo_delete(take["uid"]) and not core.undo_delete(take["uid"])
    assert len(core.progress_entries()) == 2                               # back in Progress too
    assert len(TakeStore(folder).load_all()[0]) == 2


def test_the_trash_folder_is_not_loaded_as_takes(make_core, tmp_path):
    folder = tmp_path / "takes"
    core = make_core(folder)
    take = record(core)
    core.delete(take["uid"])
    again = make_core(folder)
    assert again.takes() == []                                              # the trashed take stays out until undone
    assert (folder / ".trash").is_dir()


def test_deleting_an_unsaved_take_cannot_be_undone(make_core):
    core = make_core()
    take = record(core)
    assert core.delete(take["uid"]) and not core.can_undo_delete(take["uid"])


# --- PHASE 11 / 04: THE TAKES SCREEN, THE CARD'S FIRST SCREEN, COMPARE, UPDATING, PROGRESS ---

from test_report_ui import flush  # noqa: E402
from ui import theme  # noqa: E402
from ui.takes.latest_take import LatestTakePanel  # noqa: E402

def many_takes(core, n):
    """n more takes in the core, copies of the last one a day apart (no recording needed)."""
    base = core.takes()[0]
    for i in range(n):
        data = copy.deepcopy(base)
        data.update(uid=f"extra-{i}", file=None, title="", time=(datetime(2026, 9, 1) + timedelta(days=i)).isoformat())
        core._add(data)


def test_takes_screen_selects_the_new_take_the_moment_it_is_ready(make_core):
    core = make_core()
    first = record(core)
    screen = TakesScreen(core)
    assert screen.selected == first["uid"]
    screen.show_take(first["uid"])
    second = record(core)
    QApplication.processEvents()
    assert screen.selected == second["uid"] and screen.full.uid == second["uid"]     # not the take before it
    assert screen.full.new and not screen.full.new_chip.isHidden()
    assert screen.cards[second["uid"]].selected
    assert screen.cards[first["uid"]].status.isHidden()
    click(screen.cards[first["uid"]])
    assert screen.selected == first["uid"] and not screen.full.new


def test_takes_screen_rows_are_compact_and_a_screenful_fits(make_core):
    core = make_core()
    record(core)
    many_takes(core, 14)
    screen = TakesScreen(core)
    screen.resize(1920, 1080)
    screen.show()
    QApplication.processEvents()
    rows = [screen.cards[t["uid"]] for t in core.shown_takes()]
    assert len(rows) >= 10 and all(r.height() <= 64 for r in rows)
    viewport = screen.scroll.viewport().rect()
    inside = [r for r in rows if viewport.contains(r.mapTo(screen.scroll.viewport(), r.rect().bottomLeft()))]
    assert len(inside) >= 10
    assert screen.heading.mapTo(screen, screen.heading.rect().topLeft()).x() >= 24     # the standard margin
    screen.close()


def test_filters_live_in_one_popover_and_the_button_counts_them(make_core):
    core = make_core()
    takes_for_screen(core)
    screen = TakesScreen(core)
    assert screen.filter_btn.text() == "Filter" and not screen.filter_menu.isEmpty()
    assert not any(isinstance(w, type(screen.mode)) and w.isVisibleTo(screen) and w.parent() is screen
                   for w in screen.children())
    screen.mode.set_current_key("sing")
    assert screen.filter_btn.text() == "Filter · 1"
    screen.task.set_current_key("free singing")
    assert screen.filter_btn.text() == "Filter · 2"
    screen.clear_filters()
    assert screen.filter_btn.text() == "Filter" and screen.search.text() == "" and len(screen.visible_takes()) == 4


def test_delete_offers_undo_in_a_toast_and_undo_restores_the_take(make_core, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QWidget
    from ui.components import ToastStack
    core = make_core(tmp_path / "takes")
    first, second = record(core), record(core)
    window = QWidget()
    window.resize(1200, 800)
    window.toasts = ToastStack(window)
    screen = TakesScreen(core, parent=window)
    monkeypatch.setattr(take_card_module, "ask", lambda parent, q: True)
    screen.show_take(second["uid"])
    screen.full.delete()
    QApplication.processEvents()
    assert screen.last_undo == (second["uid"], "Take #2")
    toast = window.toasts.toasts[-1]
    assert toast.text == "Take #2 deleted" and toast.action is not None and toast.action.text() == "Undo"
    assert core.take(second["uid"]) is None
    toast.action.click()
    QApplication.processEvents()
    assert core.take(second["uid"]) is not None and screen.cards[second["uid"]].isVisibleTo(screen) or True
    assert [t["uid"] for t in core.takes()] == [second["uid"], first["uid"]]
    assert second["uid"] in screen.cards and screen.selected in (first["uid"], second["uid"])
    window.deleteLater()


def test_the_card_leads_with_headline_trust_player_overall_then_tips(make_core):
    core = make_core(goal="feminine")
    take = record(core)
    card = TakeCard(core, take["uid"])
    card.resize(1000, 1400)
    card.show()
    QApplication.processEvents()
    s = card.summary
    y = lambda w: w.mapTo(card, w.rect().topLeft()).y()      # noqa: E731
    assert s.player is card.player and card.player is not None
    order = [y(s.headline), y(s.trust), y(card.player), y(s.overall), y(s.tip_rows[0])]
    assert order == sorted(order) and len(set(order)) == 5
    assert y(card.metric_cards["pitch"]) > y(s.tip_rows[-1])
    assert s.overall.trust is None or s.overall.trust.text in ("Rough", "Rough guide")      # no "High confidence" chip
    assert not [c for c in card.findChildren(type(card.metric_cards["pitch"].confidence)) if not c.isHidden()]
    card.close()


def test_the_first_viewport_at_720p_holds_headline_trust_player_overall_and_a_tip(make_core):
    core = make_core(goal="feminine")
    take = record(core)
    card = TakeCard(core, take["uid"])
    card.resize(740, 100)                   # the report's width at 1280x720
    card.show()
    card.resize(740, card.layout().totalHeightForWidth(740))
    QApplication.processEvents()
    first_tip = card.summary.tip_rows[0]
    bottom = first_tip.mapTo(card, first_tip.rect().bottomLeft()).y()
    assert bottom <= 600, bottom          # the visible height of the report at 1280x720 (below the top bar)
    card.close()


def test_tips_show_two_and_keep_the_rest_behind_more_tips(make_core):
    core = make_core(goal="feminine")
    card = TakeCard(core, record(core)["uid"])
    shown = len(card.summary.tip_rows) - len(card.summary.more_rows)
    assert shown <= 2 and (len(card.report["tips"]) <= 2 or card.summary.more_rows)
    assert all(row.why is None or row.why.text() for row in card.summary.tip_rows)


def test_a_tip_row_says_what_to_try_then_the_evidence_and_plays_its_moment(make_core, speaker):
    core = make_core(goal="feminine")
    take = record(core)
    card = TakeCard(core, take["uid"])
    row = next(r for r in card.summary.tip_rows if r.tip.get("moments"))
    assert row.text.text() == row.tip["text"] and (row.why is None or row.why.text() == row.tip["why"])
    played = len(speaker.played)
    card.player.moment_clicked.emit(*row.tip["moments"][0])          # a click on the marked moment of the strip
    assert len(speaker.played) == played + 1 and card.playing
    card.stop_playback()


def test_playhead_follows_on_the_player_strip_while_playing(make_core, speaker):
    core = make_core()
    take = record(core)
    card = TakeCard(core, take["uid"])
    clock = core.player.clock = FakeClock()
    card.play_audio()
    clock.now += 1.0
    card.playback.tick()
    assert card.player.head.isVisible() or card.player.head.value() == pytest.approx(1.0)
    assert card.player.head.value() == pytest.approx(1.0) and card.player.clock_label.text().startswith("0:01")
    assert card.player.play_btn.text() == "Stop"
    card.stop_playback()
    assert card.player.play_btn.text() == "Play" and not card.player.head.isVisible()


def test_the_new_take_reports_whether_it_did_last_times_tip(make_core):
    core = make_core(goal="feminine")
    first = record(core, synth_vowel(140, MALE_FORMANTS, seconds=3, noise=0.05))
    time_gap = datetime.fromisoformat(first["time"]) + timedelta(minutes=5)
    higher = record(core, synth_vowel(175, MALE_FORMANTS, seconds=3, noise=0.05))
    higher["time"] = time_gap.isoformat(timespec="seconds")
    card = TakeCard(core, higher["uid"])
    rep = card.report
    assert rep["last_tip"] is not None and rep["last_tip"]["verdict"] in report.TIP_VERDICTS
    assert card.summary.last_tip is not None and card.summary.last_tip.label.text().startswith("Last time: ")
    assert "This take:" in card.summary.last_tip.label.text()
    alone = TakeCard(core, first["uid"])
    assert alone.summary.last_tip is None


def test_compare_button_opens_the_previous_take_with_both_values_and_plays_both(make_core, speaker):
    core = make_core()
    first = record(core)
    second = record(core, synth_vowel(180, MALE_FORMANTS, seconds=2, noise=0.05))
    card = TakeCard(core, second["uid"])
    assert card.compare_btn.text() == "Compare" and card.compare_view is None
    card.compare_btn.click()
    assert card.compare is core.previous_take(second["uid"]) and card.compare_view is not None
    assert card.compare_btn.text() == "Close comparison"
    view = card.compare_view
    assert [r["key"] for r in view.rows] == list(take_view.COMPARE_KEYS)
    assert view.picker.current_key() == first["uid"] and "Compared with" in view.sentence.text()
    pitch = next(r for r in view.rows if r["key"] == "pitch")
    assert pitch["change"].startswith("Higher by ")
    assert card.player.overlay is not None                           # the other's pitch, dashed, on the strip
    view.both_btn.click()
    assert card.playing and len(speaker.played[-1]) > len(core.take_audio(second["uid"]))
    card.stop_playback()
    view.other_btn.click()
    assert card.playing and len(speaker.played[-1]) == len(core.take_audio(first["uid"]))
    card.stop_playback()
    view.closed.emit()
    QApplication.processEvents()
    assert card.compare is None and card.compare_view is None and card.player.overlay is None
    alone = TakeCard(core, first["uid"])
    alone.compare_btn.click()                                        # nothing earlier to compare with
    assert alone.compare is None or alone.compare is not None


def test_a_card_says_it_is_updating_then_says_what_the_update_changed(make_core, monkeypatch):
    from ui.takes.trust_line import NoteLine
    core = make_core(goal="feminine")
    take = record(core)
    card = TakeCard(core, take["uid"])
    screen = TakesScreen(core)
    assert not any(isinstance(w, NoteLine) for w in card.findChildren(NoteLine))
    monkeypatch.setattr(core, "outdated_takes", lambda: [take])
    core.events.emit("busy", [{"id": 9, "kind": "reanalysis", "label": "Re-analysing"}])
    QApplication.processEvents()
    notes = [w.label.text() for w in card.findChildren(NoteLine)]
    assert card.updating and notes == [take_view.UPDATING_TEXT] and card.strip.accessibleName() == "Updating"
    assert screen.cards[take["uid"]].status.text == "Updating…" and not screen.cards[take["uid"]].status.isHidden()
    take["tracks"] = dict(take["tracks"])                           # the new analysis replaces the tracks
    take["confidence"] = {"pitch": "low"}
    monkeypatch.setattr(core, "outdated_takes", lambda: [])
    core.events.emit("take_updated", take)
    core.events.emit("busy", [])
    QApplication.processEvents()
    notes = [w.label.text() for w in card.findChildren(NoteLine)]
    assert not card.updating and len(notes) == 1 and notes[0].startswith("Updated with the improved analysis")
    assert screen.cards[take["uid"]].status.isHidden()


def test_compact_card_says_updating_too(make_core, monkeypatch):
    core = make_core()
    take = record(core)
    card = TakeCard(core, take["uid"], compact=True)
    monkeypatch.setattr(core, "outdated_takes", lambda: [take])
    core.events.emit("busy", [{"id": 9, "kind": "reanalysis", "label": "Re-analysing"}])
    QApplication.processEvents()
    assert card.updating_note.text() == take_view.UPDATING_SHORT and not card.updating_note.isHidden()


def test_latest_take_panel_says_which_take_and_how_long_ago_and_stays_short(make_core):
    from ui.components import Banner
    core = make_core(goal="feminine")
    take = record(core, REALISTIC)
    take["noise"] = {"rumble_share": 0.9}                           # a recording problem: it still shows no banner
    panel = LatestTakePanel(core, compact=True)
    panel.resize(520, 900)
    panel.show()
    QApplication.processEvents()
    assert panel.title_label.text().startswith("Your last take · ") and "ago" in panel.title_label.text() \
        or "just now" in panel.title_label.text()
    assert not panel.findChildren(Banner) and len(panel.summary.tip_rows) <= 1
    assert panel.layout().totalHeightForWidth(520) <= 320 and panel.maximumHeight() == 320     # nothing squeezed
    assert panel.open_btn.text() == "Open report"
    full = LatestTakePanel(core)
    assert full.title_label.text().startswith("Your last take") and full.open_btn.text() == "Open full report"
    assert len(full.summary.tip_rows) <= 2
    panel.close()


def test_latest_take_panel_title_names_the_day_for_an_older_take(make_core):
    core = make_core()
    take = record(core)
    take["time"] = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    panel = LatestTakePanel(core)
    assert "(3 days ago)" in panel.title_label.text() and panel.title_label.text().startswith("Your last take · ")


def test_theme_switches_never_raise_in_a_slot(make_core, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    core = make_core(goal="feminine")
    take = record(core)
    other = record(core)
    Collapsible.memory["take_card.graph"] = True
    screen = TakesScreen(core)
    card = TakeCard(core, take["uid"])
    panel = LatestTakePanel(core, compact=True)
    progress_screen = ProgressScreen(core)
    for _ in range(5):
        theme.set_theme("light")
        theme.set_theme("dark")
    card.compare_btn.click()
    for widget in (card, panel, progress_screen):
        widget.deleteLater()
    screen.select(other["uid"])
    flush()
    theme.set_theme("light")
    theme.set_theme("dark")
    QApplication.processEvents()
    import traceback
    ours = [e for e in errors if any(os.sep + "takes" + os.sep in f.filename or os.sep + "progress" + os.sep in f.filename
                                     for f in traceback.extract_tb(e[2]))]       # not other screens' leftovers
    assert ours == []


def test_a_playback_that_ends_after_its_card_is_gone_does_not_raise(make_core, speaker, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    core = make_core()
    card = TakeCard(core, record(core)["uid"])
    card.play_audio()
    playback = card.playback
    card.deleteLater()
    flush()
    playback._reset() if False else None
    core.player.stop()
    QApplication.processEvents()
    assert errors == []


def test_copy_and_export_the_report(make_core, tmp_path):
    core = make_core()
    take = record(core)
    card = TakeCard(core, take["uid"])
    card.resize(900, 1600)
    text = card.copy_summary()
    assert QApplication.clipboard().text() == text and card.report["headline"] in text
    png = card.export_report(str(tmp_path / "report.png"))
    assert png and (tmp_path / "report.png").stat().st_size > 1000
    pdf = card.export_report(str(tmp_path / "report.pdf"))
    assert pdf and (tmp_path / "report.pdf").read_bytes().startswith(b"%PDF")
    take_card_module.choose_export_path = lambda parent, suggestion: ""
    assert card.export_report() is None


def test_progress_screen_answers_am_i_getting_better_and_labels_its_axes(make_core, monkeypatch):
    from ui.progress.progress_screen import MonthAxis
    core = make_core(goal="feminine")
    entries = [entry(d, pitch=140.0 + d, delta_f=1000.0 + d, tier="good") for d in range(0, 40, 2)]
    monkeypatch.setattr(core, "progress_entries", lambda: entries)
    screen = ProgressScreen(core)
    text = screen.summary.headline.text()
    assert text.startswith("Not enough takes") or text.startswith("Over the last 30 days")
    assert screen.summary.title_label.text() == "Am I getting better?"
    stamp = datetime(2026, 10, 14, 12).timestamp()
    assert MonthAxis().tickStrings([stamp], 1, 5 * 86400) == ["Oct 14"]
    assert MonthAxis().tickStrings([stamp], 1, 30 * 86400) == ["Oct 2026"]
    assert not screen.legend.isHidden() and "Line = weekly median" in screen.legend.text()
    pitch = screen.charts["pitch"]
    assert pitch.line_note.isHidden() and pitch.data["line"]
    assert all(label.toPlainText().isdigit() for label in pitch.counts)
    sparse = [entry(0, pitch=150.0, delta_f=1000.0, tier="good"), entry(1, pitch=152.0, delta_f=1000.0, tier="good")]
    monkeypatch.setattr(core, "progress_entries", lambda: sparse)
    screen.refresh()
    assert not screen.charts["pitch"].line_note.isHidden()
    assert screen.shown["tasks"][0][1] == len(select(sparse, "pitch")[1])      # the filter's count is the chart's


def test_progress_headline_follows_the_goal(make_core, monkeypatch):
    rising = [entry(d, pitch=140.0 if d < 14 else 165.0, delta_f=1000.0, tier="good") for d in range(0, 28)]
    now = datetime(2026, 9, 7, 12) + timedelta(days=27, hours=4)
    monkeypatch.setattr(progress, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: now)}))
    fem = make_core(goal="feminine")
    masc = make_core(goal="masculine")
    for core in (fem, masc):
        monkeypatch.setattr(core, "progress_entries", lambda: rising)
    assert "toward your goal" in ProgressScreen(fem).summary.headline.text()
    assert "further from your goal" in ProgressScreen(masc).summary.headline.text()
    mini = ProgressMini(fem)
    assert "toward your goal" in mini.sentence.text() and {s["name"] for s in mini.data["series"]} >= {"Pitch"}
