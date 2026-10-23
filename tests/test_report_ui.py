"""The take report UI (phase 10 spec 06): the summary-first card, its compact form, moments, the ⋯ menu,
the latest-take panel, and every widget letting go of the core's events when it goes. Offscreen Qt on a
real VoiceLabCore with a fake engine: no mic and no speakers."""
import copy
import os
import sys
import threading

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
import sounddevice as sd  # noqa: E402
from PyQt6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import mic_noise  # noqa: E402
import report  # noqa: E402
import take_view  # noqa: E402
from app_core import VoiceLabCore  # noqa: E402
from conftest import wait_for  # noqa: E402
from profile_store import Profile  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402
from test_core import FakeEngine  # noqa: E402
from ui import theme  # noqa: E402
from ui.bridge import qt_events  # noqa: E402
from ui.components import Collapsible, MetricCard  # noqa: E402
from ui.takes import CARD_EVENTS  # noqa: E402
from ui.takes import take_card as take_card_module  # noqa: E402
from ui.takes.latest_take import LatestTakePanel  # noqa: E402
from ui.takes.recent_list import RecentTakes  # noqa: E402
from ui.takes.take_card import TakeCard  # noqa: E402

VOICE = synth_vowel(140, MALE_FORMANTS, seconds=3, noise=0.05)
REALISTIC = mic_noise.at_level(synth_vowel(140, MALE_FORMANTS, seconds=3), -30) + mic_noise.mic_noise(3 * SR)


class Speaker:
    """Stands in for sounddevice: what was played, and how often it stopped."""

    def __init__(self):
        self.played, self.stops = [], 0

    def play(self, audio, rate, **kwargs):
        self.played.append(np.asarray(audio))

    def stop(self, *args, **kwargs):
        self.stops += 1


@pytest.fixture
def speaker(monkeypatch):
    fake = Speaker()
    monkeypatch.setattr(sd, "play", fake.play)
    monkeypatch.setattr(sd, "stop", fake.stop)
    return fake


@pytest.fixture
def make_core(app, tmp_path, speaker):
    cores = []

    def make(takes_dir=None, goal=None):
        profile = Profile(tmp_path / f"home{len(cores)}")
        if takes_dir:
            profile.set_takes_dir(takes_dir)
        profile.voice_goal = goal
        core = VoiceLabCore(profile, engine=FakeEngine(VOICE), events=qt_events())
        cores.append(core)
        if takes_dir:
            core.reload()
            assert wait_for(lambda: not core.loading and not core.runner.jobs(), 120)
        return core
    yield make
    for core in cores:
        core.runner.wait_all(60)
        QApplication.processEvents()


def record(core, audio=VOICE):
    """Record `audio` as a take and wait until the core has it; returns the take."""
    count = len(core.takes())
    core.engine.take = audio
    core.start_recording()
    core.stop_recording()
    assert wait_for(lambda: len(core.takes()) > count and not core.runner.jobs(), 120), core.status
    return core.takes()[0]


def flush():
    """Run deleteLater and queued events."""
    for _ in range(3):
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        QApplication.processEvents()


def click(widget):
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        QApplication.sendEvent(widget, QMouseEvent(kind, pos, widget.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                                   Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def poor(core, take):
    """A copy of a take recorded too quietly, in too much noise, added to the core."""
    data = copy.deepcopy(take)
    data.update(snr=6.0, level=-52.0, uid="poor-1", file=None, time="2030-01-01T10:00:00")
    core._add(data)
    return data


@pytest.fixture(autouse=True)
def fresh_collapsibles(app):
    """Each test starts with every section closed, and ends by deleting its widgets in Qt's own order
    (left to Python's garbage collector, pyqtgraph scenes can be torn down out of order)."""
    saved = dict(Collapsible.memory)
    Collapsible.memory.clear()
    yield
    Collapsible.memory.clear()
    Collapsible.memory.update(saved)
    from ui.progress.progress_screen import ProgressMini, ProgressScreen
    from ui.takes.takes_screen import TakesScreen
    from ui.takes.targets_editor import TargetsEditor
    ours = (TakeCard, LatestTakePanel, RecentTakes, TakesScreen, ProgressScreen, ProgressMini, TargetsEditor)
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, ours):
            widget.deleteLater()
    flush()


# --- THE CARD ---

def test_card_leads_with_headline_overall_and_tips_with_moments(make_core):
    core = make_core(goal="feminine")
    take = record(core)
    card = TakeCard(core, take["uid"])
    rep = card.report
    assert card.headline.text() == rep["headline"]
    assert rep["overall"]["position"] is not None and card.summary.overall is not None
    assert card.summary.overall.bar.value == pytest.approx(rep["overall"]["position"])
    assert card.summary.overall.bar.range == tuple(rep["overall"]["range"])
    assert card.summary.overall.text.text() == rep["overall"]["text"]
    assert len(card.summary.tip_rows) == len(rep["tips"]) >= 1
    moments = [tuple(m) for tip in rep["tips"] for m in tip["moments"]]
    assert [(c.start, c.end) for c in card.summary.chips()] == pytest.approx(moments)
    assert set(card.metric_cards) == {d["key"] for d in rep["dimensions"]}
    first = list(card.metric_cards)[:2]
    assert first == ["pitch", "delta_f"]                          # the two that carry the placement lead
    pitch = card.metric_cards["pitch"]
    assert pitch.headline.text() == next(d for d in rep["dimensions"] if d["key"] == "pitch")["headline"]
    assert pitch.value_label.text() and pitch.unit_label.text() == "Hz" and not pitch.bar.isHidden()
    from ui.components import Label
    assert any(label.text() == rep["caveat"] for label in card.findChildren(Label))


def test_poor_take_has_one_banner_and_no_bars(make_core):
    core = make_core()
    data = poor(core, record(core))
    card = TakeCard(core, data["uid"])
    assert card.head["tier"] == "poor" and card.quality_dot.state == "error"
    assert card.banner is not None and len(card.findChildren(type(card.banner))) == 1
    assert "closer" in card.headline.text() or "quiet" in card.headline.text()
    assert card.metric_cards == {}
    assert not card.findChildren(MetricCard)
    assert card.summary.overall is None and card.summary.tip_rows == []
    assert card.banner.action is not None and card.banner.action.text() == "Mic check"
    heard = []
    card.mic_check_requested.connect(lambda: heard.append(1))
    card.banner.action.click()
    assert heard == [1]


def test_untrusted_dimension_shows_a_line_not_a_bar(make_core):
    core = make_core()
    data = copy.deepcopy(record(core))
    data.update(pitch=420.0, uid="high-1", file=None, time="2030-01-01T10:00:00")
    core._add(data)
    card = TakeCard(core, data["uid"])
    resonance, pitch = card.metric_cards["delta_f"], card.metric_cards["pitch"]
    assert resonance.unavailable and resonance.bar.isHidden() and resonance.value_label.isHidden()
    assert not pitch.unavailable and not pitch.bar.isHidden()
    info = take_view.banner(card.report)
    assert info and "delta_f" in info["affects"] and card.banner is not None


def test_moment_chip_plays_that_stretch(make_core, speaker):
    core = make_core(goal="feminine")
    take = record(core)
    card = TakeCard(core, take["uid"])
    card.resize(1200, 1600)
    chip = card.summary.chips()[0]
    click(chip)
    audio = core.take_audio(take["uid"])
    expected = audio[:int(round(chip.end * SR))][int(chip.start * SR):]
    assert len(speaker.played[-1]) == len(expected)
    assert np.allclose(speaker.played[-1], expected)
    assert card.playing and chip.playing
    card.stop_playback()
    assert not card.playing and not chip.playing


def test_compact_card_opens_full_form(make_core):
    core = make_core()
    take = record(core)
    card = TakeCard(core, take["uid"], compact=True)
    card.resize(420, 300)
    assert card.compact and card.summary is None and set(card.mini_bars) == {"pitch", "delta_f"}
    assert card.headline.text() == card.report["headline"] and card.overall is not None
    click(card)                                    # nobody listening: it opens itself
    assert not card.compact and card.summary is not None and card.metric_cards
    card.set_compact(True)
    opened = []
    card.open_report.connect(opened.append)
    click(card)
    assert opened == [take["uid"]] and card.compact


def test_more_menu_actions_reach_the_core(make_core, tmp_path, monkeypatch):
    folder = tmp_path / "takes"
    core = make_core(folder)
    take = record(core, REALISTIC)
    card = TakeCard(core, take["uid"])
    card._fill_menu()
    actions = {a.text(): a for a in card.menu.actions()}
    assert {"Set as baseline", "Use as target", "Compare with…", "Label register", "Open file location",
            "Delete…"} <= set(actions)
    actions["Set as baseline"].trigger()
    assert core.profile.baseline and core.profile.baseline["uid"] == take["uid"]
    actions["Use as target"].trigger()
    assert core.profile.target("pitch")
    revealed = []
    monkeypatch.setattr(take_card_module, "reveal", revealed.append)
    actions["Open file location"].trigger()
    assert revealed == [str(folder / take["file"])]
    label = {a.text(): a for a in actions["Label register"].menu().actions()}
    label["Chest"].trigger()
    assert core.take(take["uid"])["register_label"] == "chest"
    asked = []
    monkeypatch.setattr(take_card_module, "ask", lambda parent, q: asked.append(q) or False)
    actions["Delete…"].trigger()
    assert asked == [core.delete_question(take["uid"])] and core.take(take["uid"]) is not None
    monkeypatch.setattr(take_card_module, "ask", lambda parent, q: True)
    actions["Delete…"].trigger()
    assert core.take(take["uid"]) is None and card.removed and card.isHidden()
    assert not (folder / take["file"]).exists()


def test_rename_in_place(make_core, tmp_path):
    core = make_core(tmp_path / "takes")
    take = record(core)
    card = TakeCard(core, take["uid"])
    card.title.setText("Morning reading")
    card.title.editingFinished.emit()
    assert core.take(take["uid"])["title"] == "Morning reading"
    assert "Morning reading" in core.take(take["uid"])["file"]


def test_card_shows_a_strip_while_its_take_is_reanalysed(make_core, monkeypatch):
    core = make_core()
    take = record(core)
    card = TakeCard(core, take["uid"])
    monkeypatch.setattr(core, "outdated_takes", lambda: [take])
    core.events.emit("busy", [{"id": 9, "kind": "reanalysis", "label": "Re-analysing"}])
    QApplication.processEvents()
    assert not card.strip.isHidden()
    core.events.emit("busy", [])
    QApplication.processEvents()
    assert card.strip.isHidden()


def test_sing_take_has_sing_dimensions_and_no_placement(make_core):
    core = make_core()
    core.set_mode("sing")
    take = record(core, synth_vowel(330, MALE_FORMANTS, seconds=2, noise=0.02))
    card = TakeCard(core, take["uid"])
    assert card.report["mode"] == "sing" and card.summary.overall is None
    assert set(card.metric_cards) == set(report.DIMENSIONS_SING)
    assert "singing" in card.sections


# --- THE DASHBOARD'S PANELS ---

def test_latest_take_panel_skeleton_then_take(make_core):
    core = make_core(goal="feminine")
    panel = LatestTakePanel(core)
    assert panel.state == "empty" and panel.empty is not None
    gate = threading.Event()
    core.runner.submit("analysis", "Analysing your take", gate.wait, 30)
    assert wait_for(lambda: panel.state == "loading", 10) and panel.skeleton is not None
    gate.set()
    assert wait_for(lambda: panel.state == "empty", 10)
    take = record(core)
    assert wait_for(lambda: panel.state == "take", 10)
    assert panel.uid == take["uid"] and panel.summary.headline.text() == core.report(take["uid"])["headline"]
    assert len(panel.summary.tip_rows) <= 2
    opened = []
    panel.open_report.connect(opened.append)
    panel.open_btn.click()
    assert opened == [take["uid"]]


def test_recent_takes_rows_open_takes(make_core):
    core = make_core()
    first, second = record(core), record(core)
    recent = RecentTakes(core, limit=6)
    assert [row.uid for row in recent.rows] == [second["uid"], first["uid"]]
    opened = []
    recent.open_take.connect(opened.append)
    click(recent.rows[1])
    assert opened == [first["uid"]]
    record(core)
    assert len(recent.rows) == 3


# --- LETTING GO ---

def handler_counts(core):
    return {name: len(core.events._handlers.get(name, [])) for name in CARD_EVENTS}


def test_widgets_unsubscribe_when_destroyed(make_core):
    from ui.progress.progress_screen import ProgressMini, ProgressScreen
    from ui.takes.takes_screen import TakesScreen
    from ui.takes.targets_editor import TargetsEditor
    core = make_core()
    take = record(core)
    before = handler_counts(core)
    widgets = [TakeCard(core, take["uid"]), TakeCard(core, take["uid"], compact=True), LatestTakePanel(core),
               RecentTakes(core), TakesScreen(core), ProgressScreen(core), ProgressMini(core), TargetsEditor(core)]
    assert handler_counts(core) != before
    for w in widgets:
        w.deleteLater()
    del w
    widgets.clear()
    flush()
    assert handler_counts(core) == before
    for name in ("take_updated", "profile_changed", "progress_changed", "takes_loaded"):
        core.events.emit(name, take) if name == "take_updated" else core.events.emit(name)
    core.events.emit("busy", [])
    core.events.emit("take_removed", take["uid"])
    QApplication.processEvents()


def test_theme_switch_redraws_without_errors(make_core):
    core = make_core(goal="feminine")
    take = record(core)
    Collapsible.memory["take_card.graph"] = True
    card = TakeCard(core, take["uid"])
    assert card.graph is not None
    theme.set_theme("light")
    theme.set_theme("dark")
    assert card.graph.pitch_curve is not None


# --- WHAT THE CARD SHOWS, WITHOUT QT (take_view) ---

def test_banner_collapses_issues_into_one_line():
    rep = {"headline": "Overall this take sits in the overlap zone.", "issues": [
        {"text": "Desk rumble: set the mic on something soft.", "affects": []},
        {"text": "Too much background noise to judge weight and clarity.", "affects": ["h1h2", "cpp"]},
        {"text": "Steady electrical whine detected.", "affects": []}]}
    info = take_view.banner(rep)
    assert info["text"].startswith("Too much background noise") and info["text"].endswith("(+2 more)")
    assert info["kind"] == "warning" and info["action"] == "Mic check" and len(info["all"]) == 3
    assert take_view.banner({"issues": []}) is None
    poor_rep = {"headline": "This take is hard to judge: very quiet.",
                "issues": [{"text": "This take is hard to judge: very quiet. A quick re-record helps.",
                            "affects": ["pitch"]}]}
    assert take_view.banner(poor_rep)["text"] == "A quick re-record helps."     # the headline has the cause


def test_value_parts_and_card_order():
    assert take_view.value_parts({"value": 4.0, "display": "4.0 semitones"}) == ("4.0", "semitones")
    assert take_view.value_parts({"value": None, "display": "—"}) == ("—", "")
    rep = {"dimensions": [{"key": k} for k in report.DIMENSIONS_SPEAK]}
    assert [d["key"] for d in take_view.ordered_dimensions(rep)][:2] == ["pitch", "delta_f"]


def test_pitch_axis_is_fixed_in_speak_mode_and_follows_the_notes_in_sing_mode():
    times = np.arange(300) * 0.01
    speak = take_view.pitch_axis({"mode": "speak", "tracks": {"times": times, "f0": np.full(300, 250.0)}})
    assert (speak["lo"], speak["hi"]) == take_view.SPEAK_AXIS_HZ and speak["zones"]["zones"][0]["lo"] == 70
    again = take_view.pitch_axis({"mode": "speak", "tracks": {"times": times, "f0": np.full(300, 120.0)}})
    assert (again["lo"], again["hi"]) == (speak["lo"], speak["hi"])            # never re-zooms
    sing = take_view.pitch_axis({"mode": "sing", "tracks": {"times": times, "f0": np.full(300, 440.0)}})
    assert sing["lo"] < 440 < sing["hi"] and 12 * np.log2(sing["hi"] / sing["lo"]) >= take_view.SING_MIN_SPAN_ST
    assert sing["zones"] is None and all(label[0] in "ABCDEFG" for _, label in sing["ticks"])


def test_formant_lines_prefer_the_smoothed_tracks_and_skip_unvoiced_frames():
    times = np.arange(10) * 0.01
    f0 = np.array([np.nan, 200, 200, 200, np.nan, 200, 200, 200, 200, np.nan])
    tracks = {"times": times, "f0": f0, "f1": np.full(10, 500.0), "f2": np.full(10, 1500.0),
              "f3": np.full(10, 2500.0), "f1_smooth": np.full(10, 510.0)}
    lines = take_view.formant_lines(tracks)
    assert np.isnan(lines["f1"][0]) and lines["f1"][1] == 510.0 and lines["f2"][1] == 1500.0
    raw = take_view.formant_lines(tracks, raw=True)
    assert raw["f1"][0] == 500.0


def test_matches_and_moment_clip():
    take = {"time": "2026-10-23T09:21:26", "mode": "speak", "title": "Rainbow", "snr": 25.0, "level": -30.0,
            "voiced_fraction": 0.5, "clip_events": 0, "pitch": 150.0, "delta_f": 1000.0}
    assert take_view.matches(take, "rainbow") and take_view.matches(take, "oct 23")
    assert not take_view.matches(take, "scale") and not take_view.matches(take, mode="sing")
    assert take_view.matches(take, task="free speech") and not take_view.matches(take, task="Five-note scale")
    samples, start = take_view.moment_clip(np.arange(SR * 3), 1.0, 2.0, SR)
    assert start == 1.0 and len(samples) == 2 * SR
    rows = take_view.all_numbers(take | {"cpps": 8.6, "delta_f_iqr": 120.0, "resonance_note": "Too high"})
    labels = [r[0] for r in rows]
    assert "CPPS" in labels and "ΔF spread (frame IQR)" in labels and "Why ΔF was withheld" in labels
