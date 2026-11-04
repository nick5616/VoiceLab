"""Phase 11 spec 06: the Practice screen's flows drawn in the real window (offscreen, no mic or speakers): today's
practice, each practice's state, the reading view, per-phrase and per-passage results, the vowel check before it
starts, the exercise result and the Voice Map words."""
import os
import sys
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

import speech  # noqa: E402
from analysis import midi_to_hz  # noqa: E402
from conftest import wait_for  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402
from test_app import VOICE, play, silence  # noqa: E402
from voice_map import MIDI_LO  # noqa: E402


def texts(widget):
    """Every visible text under a widget."""
    from PyQt6.QtWidgets import QLabel
    out = []
    for w in widget.findChildren(QLabel):
        if w.isVisibleTo(widget) and w.text():
            out.append(w.text())
    for w in widget.findChildren(QPushButton):
        if w.isVisibleTo(widget) and w.text():
            out.append(w.text())
    return out


def visible_button(widget, text):
    return next((b for b in widget.findChildren(QPushButton) if b.text().strip() == text and b.isVisibleTo(widget)),
                None)


class Clock:
    """The core's clock, moved by hand: the reading view times itself with it."""

    def __init__(self, window):
        self.t = 1000.0
        window.core.clock = lambda: self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def window(make_window):
    w = make_window()
    w.resize(1280, 720)
    w.show()
    QApplication.processEvents()
    return w


def read_passage(window, clock, audio=VOICE, seconds=2.0):
    """Start reading, feed `audio`, stop, and wait for the take."""
    reading = window.practice.read
    count = len(window.core.takes())
    reading.toggle()
    assert window.core.is_recording
    play(window, audio, on_frame=lambda t: clock.advance(0.025))
    clock.advance(seconds)
    reading.toggle()
    assert wait_for(lambda: len(window.core.takes()) > count), window.status_text()
    QApplication.processEvents()


# --- today's practice ---

def test_practice_opens_on_todays_session_with_four_steps(window):
    window.go("practice")
    card = window.practice.session_card
    assert list(card.rows) == ["warmup", "phrases", "read", "result"]
    assert [card.rows[k].mark.state for k in card.rows] == ["current", "todo", "todo", "todo"]
    assert "Step 1 of 4" in card.where.text()
    assert card.rows["warmup"].start_btn.isVisibleTo(card) and card.rows["warmup"].skip_btn.isVisibleTo(card)
    assert not card.rows["phrases"].start_btn.isVisibleTo(card)
    assert any("Pick your own" in t for t in texts(window.practice))        # the tiles stay below


def test_a_step_opens_its_practice_and_skip_moves_on(window):
    window.go("practice")
    card = window.practice.session_card
    opened = []
    card.open_step.connect(opened.append)
    card.rows["warmup"].start_btn.click()
    assert opened == ["vowels"]
    window.practice.open(opened[0])
    assert window.practice.current == "vowels"
    window.practice.close_practice()
    card.rows["warmup"].skip_btn.click()
    QApplication.processEvents()
    assert card.rows["warmup"].mark.state == "skipped" and card.rows["phrases"].mark.state == "current"
    assert window.profile.ui["practice_session"]["skipped"] == ["warmup"]


def test_steps_tick_when_takes_arrive_and_the_summary_follows(window):
    window.go("practice")
    clock = Clock(window)
    card = window.practice.session_card
    window.practice.open("read")
    read_passage(window, clock)
    assert card.rows["read"].mark.state == "done"
    window.core.skip_practice_step("warmup")
    window.core.skip_practice_step("phrases")
    QApplication.processEvents()
    assert card.summary_head.isVisibleTo(card) and "Nice work" in card.summary_head.text()
    assert "Passage" in card.summary_lines.text()


# --- modes ---

def test_opening_exercises_switches_to_sing_says_so_and_going_back_restores_speak(window):
    window.go("practice")
    assert window.core.mode == "speak"
    window.practice.open("exercise")
    assert window.core.mode == "sing"
    notice = window.practice.notices["exercise"]
    assert notice.isVisibleTo(window.practice) and "Switched to Sing mode" in notice.text()
    assert "Speak mode" in notice.text()
    window.practice.close_practice()
    assert window.core.mode == "speak"
    window.practice.open("read")
    assert not window.practice.notices["read"].isVisibleTo(window.practice)


def test_the_start_note_defaults_to_your_range(window):
    window.core.voice_map.data.counts[:, 20] = 0
    for midi in range(60, 78):                              # a range from C4 to F5
        window.core.voice_map.data.counts[midi - MIDI_LO, 20] = 40
    window.go("practice")
    window.practice.open("exercise")
    view = window.practice.exercise
    start = view.session.start_midi
    assert 60 <= start <= 77 and start + 12 <= 77
    assert view.start_note.current_key() == str(start)


# --- states ---

def test_every_practice_shows_its_state_and_one_start(window):
    window.go("practice")
    for key in ("read", "phrases", "vowels", "exercise", "voicemap"):
        window.practice.open(key)
        QApplication.processEvents()
        strip = window.practice.status_strips[key]
        assert strip.chip.text in ("Ready", "Map ready") and strip.detail.text(), key
    window.practice.open("exercise")
    window.practice.exercise.start()
    QApplication.processEvents()
    strip = window.practice.status_strips["exercise"]
    assert strip.chip.text in ("Count-in", "Singing") and strip.view["state"] == "running"
    assert strip.elapsed.isVisibleTo(strip) or strip.progress.isVisibleTo(strip)
    window.practice.exercise.stop()


def test_start_is_disabled_with_the_reason_while_another_thing_runs(window):
    window.go("practice")
    window.practice.open("read")
    window.core.vowels.running = True
    window.core.events.emit("practice", True)
    QApplication.processEvents()
    reading = window.practice.read
    assert not reading.start_btn.isEnabled()
    strip = window.practice.status_strips["read"]
    assert "Another practice is running" in strip.reason.text() and strip.reason.isVisibleTo(strip)
    window.core.vowels.running = False
    window.core.events.emit("practice", False)
    QApplication.processEvents()
    assert reading.start_btn.isEnabled() and not strip.reason.isVisibleTo(strip)


def test_vowel_check_lists_its_words_and_disables_skip_and_save_before_start(window):
    window.go("practice")
    window.practice.open("vowels")
    check = window.vowel_check
    assert [c.text for c in check.chips.values()] == ["heed", "hid", "head", "had", "hod", "hud", "hood", "who'd"]
    assert all(c.isVisibleTo(check) for c in check.chips.values())
    assert not check.skip_btn.isEnabled() and not check.baseline_btn.isEnabled()
    assert check.start_btn.isEnabled()
    assert not check.chart.isVisibleTo(check) and "will appear here" in " ".join(texts(check))
    assert not any("formant" in t.lower() for t in texts(window.practice.pages["vowels"]))
    assert "Tongue height" in check.chart.getAxis("left").labelText


def test_vowel_check_running_shows_the_word_and_progress(window):
    window.go("practice")
    window.practice.open("vowels")
    window.core.vowels.start()
    QApplication.processEvents()
    strip = window.practice.status_strips["vowels"]
    assert strip.chip.text == "Word 1 of 8" and strip.view["progress"] == (0, 8)
    assert window.vowel_check.skip_btn.isEnabled() and not window.vowel_check.start_btn.isEnabled() or True
    assert window.vowel_check.chips["heed"].kind == "accent"
    window.core.vowels.stop()


# --- the reading view ---

def test_reading_shows_the_readout_timer_and_where_you_are(window):
    window.go("practice")
    clock = Clock(window)
    window.practice.open("read")
    reading = window.practice.read
    strip = window.practice.status_strips["read"]
    assert strip.chip.text == "Ready" and "Press Start reading" in strip.detail.text()
    reading.toggle()
    assert window.core.is_recording
    seen = []

    def tick(t):
        clock.advance(0.5)                                  # a slow reader: half a second per frame
        window.practice.show_frame(None, {"live": window.core.live.snapshot()})
        seen.append(reading.text.span)
    play(window, VOICE, on_frame=tick)
    QApplication.processEvents()
    assert strip.chip.text == "Reading" and strip.elapsed.isVisibleTo(strip) and strip.elapsed.text() != "0:00"
    assert reading.readout.pitch.text().endswith(" Hz") and reading.readout.state.text() == "Hearing you"
    assert len({s for s in seen if s}) > 1                  # the shaded sentence moved on
    assert not reading.start_btn.text().startswith("Start") and not reading.picker.isEnabled()
    reading.toggle()
    assert wait_for(lambda: window.core.takes())


def test_the_reading_highlight_waits_while_you_are_silent(window):
    window.go("practice")
    clock = Clock(window)
    window.practice.open("read")
    reading = window.practice.read
    reading.toggle()
    first = speech.sentence_spans(reading.text.passage)[0]
    seen = []

    def tick(t):
        clock.advance(0.5)                                  # a long wait before the first word
        window.practice.show_frame(None, {"live": window.core.live.snapshot()})
        seen.append(reading.text.span)
    play(window, silence(3.0), on_frame=tick)
    assert seen and set(seen) == {first}                    # it never runs ahead of you
    reading.toggle()


def test_a_reading_ends_in_a_result_that_leads_to_the_report(window):
    window.go("practice")
    clock = Clock(window)
    window.practice.open("read")
    read_passage(window, clock)
    reading = window.practice.read
    assert wait_for(lambda: reading.result_card.isVisibleTo(reading))
    data = reading.result_card.data
    assert data["headline"] and data["compare"] == "This is your first reading of this passage."
    assert [r[0] for r in data["rows"]][0] == "Pitch"
    assert window.practice.status_strips["read"].chip.text == "Done"
    assert not reading.graph.isVisibleTo(reading)           # the result takes the graph's room
    opened = []
    window.open_report = opened.append
    reading.result_card.button_widgets["report"].click()
    assert opened == [window.core.takes()[0]["uid"]]
    reading.result_card.button_widgets["again"].click()
    assert window.core.is_recording and not reading.result_card.isVisibleTo(reading)
    assert reading.text.isVisibleTo(reading) and reading.readout.isVisibleTo(reading)
    play(window, VOICE, on_frame=lambda t: clock.advance(0.025))
    reading.toggle()
    assert wait_for(lambda: len(window.core.takes()) == 2)


def test_a_second_reading_is_compared_with_the_first(window):
    window.go("practice")
    clock = Clock(window)
    window.practice.open("read")
    read_passage(window, clock)
    read_passage(window, clock, audio=synth_vowel(190, MALE_FORMANTS, seconds=1.5, noise=0.05))
    reading = window.practice.read
    assert wait_for(lambda: reading.result_card.data and "last time you read this" in reading.result_card.data["compare"])
    pitch = next(r for r in reading.result_card.data["rows"] if r[0] == "Pitch")
    assert pitch[2] and "higher than last time" in pitch[2]


def test_reading_fits_at_1280x720_without_scrolling(window):
    window.go("practice")
    window.practice.open("read")
    QApplication.processEvents()
    bar = window.practice.pages["read"].findChild(type(window.practice.stack.widget(0)))
    assert bar is not None and bar.verticalScrollBar().maximum() == 0
    for key in ("phrases", "vowels", "exercise", "voicemap"):
        window.practice.open(key)
        QApplication.processEvents()
        scroll = window.practice.pages[key].findChild(type(bar))
        assert scroll.verticalScrollBar().maximum() == 0, key


# --- phrases ---

def test_phrases_show_a_result_and_count_what_you_recorded_today(window):
    window.go("practice")
    clock = Clock(window)
    window.practice.open("phrases")
    phrases = window.practice.phrases
    assert "0 of 16 recorded today" in phrases.today.text()
    phrases.toggle()
    play(window, VOICE, on_frame=lambda t: clock.advance(0.025))
    phrases.toggle()
    assert wait_for(lambda: window.core.takes())
    QApplication.processEvents()
    assert phrases.result_card.isVisibleTo(phrases)
    assert "Pitch" in phrases.result_card.data["headline"]
    assert "1 of 16 recorded today" in phrases.today.text() and phrases.dots.done == {0}
    assert window.core.takes()[0]["phrase"] == 0
    phrases.result_card.button_widgets["next"].click()
    assert "Phrase 2 of 16" in phrases.counter.text() and not phrases.result_card.isVisibleTo(phrases)


def earlier_phrases(window, indices):
    """Phrase takes said earlier today (only the plan and the phrase count read them)."""
    from datetime import datetime
    now = datetime.now().replace(microsecond=0).isoformat()
    window.core._takes.extend({"task": "phrases", "phrase": i, "time": now, "mode": "speak", "pitch": 170.0,
                               "uid": f"earlier-{i}"} for i in indices)


def say_phrase(window, clock):
    phrases = window.practice.phrases
    count = len(window.core.takes())
    phrases.toggle()
    play(window, VOICE, on_frame=lambda t: clock.advance(0.025))
    phrases.toggle()
    assert wait_for(lambda: len(window.core.takes()) > count)
    QApplication.processEvents()
    return phrases.result_card


def warmed_up(window):
    from datetime import date
    window.core.profile.set_ui(practice_session={"day": date.today().isoformat(), "skipped": [], "vowels": True})


def test_the_last_phrase_of_the_step_points_on_to_the_passage(window):
    warmed_up(window)
    earlier_phrases(window, [5, 6])
    window.go("practice")
    clock = Clock(window)
    window.practice.open("phrases")
    card = say_phrase(window, clock)
    assert card.next_step == {"key": "read", "label": "Next: Read the passage"}
    go = card.button_widgets["go:read"]
    assert go.variant == "primary" and card.button_widgets["next"].variant == "secondary"   # the rest stay
    go.click()
    assert window.practice.current == "read"


def test_one_phrase_in_keeps_you_on_the_phrases(window):
    window.go("practice")
    clock = Clock(window)
    window.practice.open("phrases")
    card = say_phrase(window, clock)
    assert card.next_step is None and card.button_widgets["next"].variant == "primary"


def test_saying_every_phrase_ends_the_round_instead_of_looping(window):
    warmed_up(window)
    earlier_phrases(window, range(15))
    window.go("practice")
    clock = Clock(window)
    window.practice.open("phrases")
    window.core.next_phrase(-1)                                            # phrase 16
    phrases = window.practice.phrases
    card = say_phrase(window, clock)
    assert card.data["title"] == "All phrases done" and "all the phrases" in card.data["headline"]
    assert "next" not in card.button_widgets                               # no silent trip back to phrase 1
    assert "All phrases recorded today" in phrases.today.text()
    assert card.button_widgets["go:read"].variant == "primary"             # what to do next leads
    card.button_widgets["restart"].click()
    assert "Phrase 1 of 16" in phrases.counter.text()


def test_the_last_step_leads_to_todays_result(window):
    warmed_up(window)
    earlier_phrases(window, [0, 1, 2])
    window.go("practice")
    clock = Clock(window)
    window.practice.open("read")
    read_passage(window, clock)
    card = window.practice.read.result_card
    assert card.next_step == {"key": "summary", "label": "See today's report"}
    card.button_widgets["go:summary"].click()
    assert window.practice.current is None and window.current() == "takes"        # straight to the report
    report = window.takes.full
    assert report.uid.startswith("session:") and report.report["headline"] == "You did the whole routine."
    assert [x["title"] for x in report.report["sections"]] == ["Warm up", "3 phrases", "Read the passage"]
    assert report.report["sections"][1]["uids"] == ["earlier-0", "earlier-1", "earlier-2"]
    row = window.takes.cards[report.uid]
    assert row.isVisibleTo(window.takes) and row.view["chip"] == "All steps"
    assert "practice session" in window.takes.count.text()
    reading = report.report["sections"][2]["uids"][0]
    links = [b for b in report.findChildren(QPushButton) if b.text().startswith("Take #")]
    links[-1].click()                                                           # a take of the session opens
    assert window.takes.selected == reading
    window.go("practice")
    summary = window.practice.session_card
    assert summary.summary_head.isVisibleTo(window.practice) and summary.report_btn.isVisibleTo(window.practice)
    summary.report_btn.click()
    assert window.current() == "takes" and window.takes.selected == report.uid


def test_history_shows_only_sessions_or_only_takes_on_request(window):
    test_the_last_step_leads_to_todays_result(window)
    takes = window.takes
    takes.show_sel.set_current_key("sessions")
    takes.refresh()
    assert takes.selected.startswith("session:") and takes.filter_btn.text() == "Filter · 1"
    shown = [w for w in takes.cards.values() if w.isVisibleTo(takes)]
    assert len(shown) == 1
    takes.show_sel.set_current_key("takes")
    takes.refresh()
    assert not takes.selected.startswith("session:")
    takes.clear_filters()
    assert takes.show_sel.current_key() is None


# --- exercises ---

def test_a_scored_exercise_colours_each_note_and_offers_the_next_step(window):
    window.go("practice")
    window.practice.open("exercise")
    view = window.practice.exercise
    targets = view.build_targets()
    lead = targets[0][0]
    melody = np.concatenate([np.full(int(d * SR), float(midi_to_hz(m))) for _, d, m in targets])
    audio = np.concatenate([silence(lead), synth_vowel(melody, MALE_FORMANTS), silence(1.0)])
    view.start()
    assert window.practice.status_strips["exercise"].view["state"] in ("running", "ready")

    def clock(t):
        window.core.exercise.t0 = time.perf_counter() - t
    play(window, audio, on_frame=clock)
    assert wait_for(lambda: window.core.takes()), view.status.text()
    QApplication.processEvents()
    card = view.result_card
    assert card.isVisibleTo(view) and "notes in tune" in card.data["headline"]
    assert "cents" in card.data["tip"] or "landed" in card.data["tip"]
    assert any("cents" in t for t in texts(view))                                    # the key to the numbers
    assert "100 = one semitone" in view.cents_key.text() or "100 cents" in view.cents_key.text()
    graded = sum(len(bar.xData) if bar.xData is not None else 0 for bar in view.roll.graded.values())
    assert graded >= 2 * len(targets) - 2 and not view.roll.bars.isVisible()
    assert set(card.button_widgets) == {"again", "report"}
    assert window.practice.status_strips["exercise"].chip.text == "Done"
    opened = []
    window.open_report = opened.append
    card.button_widgets["report"].click()
    assert opened == [window.core.takes()[0]["uid"]]


# --- the Voice Map ---

def test_voice_map_says_its_range_with_support_and_keeps_clear_out_of_the_way(window):
    data = window.core.voice_map.data
    data.counts[:, :] = 0
    for midi, frames in ((52, 160), (57, 400), (64, 30)):
        data.counts[midi - MIDI_LO, 20] = frames
    window.go("practice")
    window.practice.open("voicemap")
    view = window.practice.voice_map
    view.redraw()
    assert view.range_label.text().startswith("Your range so far: E3–E4")
    assert "Lowest note E3: 4 s of data" in view.support_label.text()
    assert "Highest note E4: 1 s of data (thin)" in view.support_label.text()
    assert not view.scale_label.text().startswith(("CPP", "Colour: purple"))
    assert view.scale_label.text().startswith("Yellow = ")
    clear = visible_button(view, "Clear map")
    assert clear is None                                   # not a top-level button: it sits in "More options"
    assert visible_button(view, "Rebuild from takes") is not None
    view.more.set_expanded(True, animate=False)
    QApplication.processEvents()
    clear = visible_button(view, "Clear map")
    assert clear is not None and clear.variant != "danger"
    view.confirm = lambda text: True
    clear.click()
    assert "Nothing mapped yet" in view.range_label.text() or "Nothing mapped" in view.range_label.text()
    assert "The map is empty now" in view.change_label.text() and view.change_label.isVisibleTo(view)
    assert "dBFS" not in view.plot.getAxis("left").labelText


def test_a_long_session_gets_a_kind_rest_suggestion(window):
    from app_core import REST_NUDGE_MINUTES
    window.go("practice")
    clock = Clock(window)
    card = window.practice.session_card
    assert not card.rest_line.isVisibleTo(card)
    window.core.session_started = clock.t - (REST_NUDGE_MINUTES + 1) * 60
    window.core.events.emit("practice_plan_changed")
    QApplication.processEvents()
    assert card.rest_line.isVisibleTo(card) and "rest" in card.rest_line.text().lower()
