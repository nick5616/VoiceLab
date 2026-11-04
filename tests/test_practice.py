"""Phase 11 spec 06: the pure side of the practice flows (session plan, reading position, in-range strip,
per-phrase and per-passage results, exercise results, Voice Map words, practice states). No Qt."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import analysis  # noqa: E402
import exercise  # noqa: E402
import practice_state  # noqa: E402
import speech  # noqa: E402
import synth  # noqa: E402
import voice_map  # noqa: E402
from profile_store import Profile  # noqa: E402
from session_plan import SessionPlan  # noqa: E402

TODAY = "2026-10-28"
NOW = f"{TODAY}T10:00:00"


def take(task, day=TODAY, hour=9, mode="speak", pitch=170.0, **extra):
    return dict({"task": task, "time": f"{day}T{hour:02d}:00:00", "mode": mode, "pitch": pitch}, **extra)


def plan(takes=(), mode="speak", goal="feminine", state=None):
    from datetime import datetime
    return SessionPlan(mode, goal, takes, state, now=datetime.fromisoformat(NOW))


# --- the session plan ---

def test_a_new_speak_session_has_four_steps_and_starts_at_the_warm_up():
    steps = plan().steps()
    assert [s["key"] for s in steps] == ["warmup", "phrases", "read", "result"]
    assert [s["state"] for s in steps] == ["current", "todo", "todo", "todo"]
    assert plan().position() == (1, 4) and plan().title() == "Step 1 of 4"
    assert steps[0]["practice"] == "vowels" and steps[1]["practice"] == "phrases" and steps[2]["practice"] == "read"


def test_steps_tick_off_from_todays_takes_and_the_vowel_check():
    p = plan([take("phrases"), take("phrases")], state={"day": TODAY, "skipped": [], "vowels": True})
    steps = {s["key"]: s for s in p.steps()}
    assert steps["warmup"]["state"] == "done"
    assert steps["phrases"]["state"] == "current" and steps["phrases"]["progress"] == "2 of 3"
    assert p.position() == (2, 4)
    p = plan([take("phrases")] * 3 + [take("passage: Vowel tour")], state={"day": TODAY, "skipped": [], "vowels": True})
    assert p.finished and p.current()["key"] == "result" and p.title() == "All done for today"


def test_yesterdays_takes_and_state_do_not_count():
    old = take("phrases", day="2026-10-27")
    p = plan([old] * 5, state={"day": "2026-10-27", "skipped": ["warmup"], "vowels": True})
    assert p.steps()[0]["state"] == "current" and p.state["skipped"] == []


def test_skip_moves_on_and_is_remembered():
    p = plan()
    state = p.skip("warmup")
    assert state["skipped"] == ["warmup"]
    steps = plan(state=state).steps()
    assert steps[0]["state"] == "skipped" and steps[1]["state"] == "current"
    assert plan(state=state).position() == (2, 4)
    everything = plan(state={"day": TODAY, "skipped": ["warmup", "phrases", "read"], "vowels": False})
    assert everything.finished                            # skipping the lot goes to the result


def test_sing_session_is_a_warm_up_then_an_exercise():
    ex = {"notes_total": 9, "notes_in_tune": 7}
    p = plan([take("Five-note scale from C3", mode="sing", exercise=ex)], mode="sing")
    steps = p.steps()
    assert [s["key"] for s in steps] == ["warmup", "exercise", "result"]
    assert steps[0]["state"] == "done" and steps[1]["state"] == "current"
    p = plan([take("x", mode="sing", exercise=ex)] * 2, mode="sing")
    assert p.finished


def test_each_result_points_to_the_next_thing_to_do():
    vowels = {"day": TODAY, "skipped": [], "vowels": True}
    assert plan().next_step("vowels") is None                                # the warm-up is still this one
    assert plan().next_step("phrases") is None                               # phrases still to do: keep going here
    assert plan().next_step("voicemap") == {"key": "vowels", "label": "Next: Warm up"}
    assert plan(state=vowels).next_step("vowels") == {"key": "phrases", "label": "Next: 3 phrases"}
    two = plan([take("phrases")] * 2, state=vowels)
    assert two.next_step("phrases") is None                                 # one more phrase to go
    three = plan([take("phrases")] * 3, state=vowels)
    assert three.next_step("phrases") == {"key": "read", "label": "Next: Read the passage"}
    assert three.next_step("voicemap")["key"] == "read"                    # off the plan, it still shows the way
    assert three.next_step("read") is None                                  # the passage is the step: read it here
    done = plan([take("phrases")] * 3 + [take("passage: Vowel tour")], state=vowels)
    assert done.next_step("read") == {"key": "summary", "label": "See today's report"}
    assert done.next_step("phrases")["key"] == "summary"
    skipped = plan(state={"day": TODAY, "skipped": ["warmup", "phrases", "read"], "vowels": False})
    assert skipped.next_step("read") is None                                # nothing done: no summary to see
    ex = {"notes_total": 9, "notes_in_tune": 7}
    sing = plan([take("x", mode="sing", exercise=ex)], mode="sing")
    assert sing.next_step("exercise") is None                               # both sing steps are exercises
    assert plan([take("x", mode="sing", exercise=ex)] * 2, mode="sing").next_step("exercise")["key"] == "summary"


def test_goal_shapes_the_phrases_hint():
    assert "brighter" in plan(goal="feminine").steps()[1]["detail"]
    assert "low and warm" in plan(goal="masculine").steps()[1]["detail"]
    assert "No target" in plan(goal=None).steps()[1]["detail"]


def test_summary_compares_with_the_last_session_and_is_kind():
    before = [take("phrases", day="2026-10-20", pitch=180.0), take("passage: Vowel tour", day="2026-10-20", pitch=175.0)]
    today = [take("phrases", pitch=190.0)] * 3 + [take("passage: Vowel tour", pitch=170.0)]
    summary = plan(before + today, state={"day": TODAY, "skipped": [], "vowels": True}).summary(rest_text="Rest.")
    assert summary["headline"].startswith("Nice work")
    text = " ".join(summary["lines"])
    assert "Phrases: 3 recorded, pitch around 190 Hz, 10 Hz higher than your last session" in text
    assert "Passage: pitch around 170 Hz, 5 Hz lower than last time" in text
    assert "Vowel check: done" in text and summary["rest"] == "Rest."
    assert "first session" not in text


def test_first_session_summary_says_so():
    summary = plan([take("phrases")] * 3).summary()
    assert any("first session" in line for line in summary["lines"])
    skipped = plan([take("phrases")] * 3, state={"day": TODAY, "skipped": ["read"], "vowels": False}).summary()
    assert any("Skipped today: read the passage" in line for line in skipped["lines"])


# --- reading position and the in-range strip ---

PASSAGE = "One two three four. Five six seven eight nine ten. Eleven twelve."


def test_sentence_spans_cover_each_sentence():
    spans = speech.sentence_spans(PASSAGE)
    assert [PASSAGE[s:e].strip() for s, e in spans] == ["One two three four.", "Five six seven eight nine ten.",
                                                       "Eleven twelve."]


def follow(text, timeline, step=0.025):
    """A ReadingFollower fed [(seconds, voiced)] stretches, frame by frame; the sentence after each."""
    f, t, out = speech.ReadingFollower(text), 0.0, []
    f.add(False, t)
    for seconds, voiced in timeline:
        for _ in range(int(round(seconds / step))):
            t += step
            f.add(voiced, t)
        out.append(f.position()["sentence"])
    return f, out


def test_the_reading_highlight_waits_for_you():
    f, at = follow(PASSAGE, [(10.0, False)])                    # silence: it never moves on its own
    assert at == [0] and f.position()["span"] == speech.sentence_spans(PASSAGE)[0]
    _, at = follow(PASSAGE, [(1.0, True), (0.3, False), (0.5, True)])
    assert at == [0, 0, 0]                                       # a short pause mid-sentence keeps it there


def test_the_reading_highlight_moves_when_you_pause_after_a_sentence():
    _, at = follow(PASSAGE, [(1.2, True), (0.6, False), (1.8, True), (0.6, False), (0.6, True), (0.6, False)])
    assert at == [0, 1, 1, 2, 2, 2]
    f, _ = follow(PASSAGE, [(1.2, True), (0.6, False), (1.8, True), (0.6, False), (0.6, True), (0.6, False)])
    assert f.position()["done"] and f.position()["span"] is None


def test_reading_straight_on_still_moves_but_never_ahead():
    _, at = follow(PASSAGE, [(4 * speech.FOLLOW_MAX_PER_WORD - 0.1, True), (0.2, True)])
    assert at == [0, 1]                                          # well past four words of voice: next sentence
    _, at = follow(PASSAGE, [(30.0, False), (0.5, True)])
    assert at == [0, 0]                                          # a long wait first changes nothing
    assert speech.ReadingFollower("").position()["count"] == 0
    assert speech.clock_text(42) == "0:42" and speech.clock_text(75) == "1:15"


def test_range_tracker_holds_and_resets_in_silence():
    tracker = speech.RangeTracker((150, 200))
    now = 0.0
    for _ in range(45):                                   # a bit over 1 s in range at 25 ms
        now += 0.025
        tracker.add(180.0, now)
    assert 1.0 < tracker.held < 1.2 and tracker.share() == pytest.approx(1.0)
    assert tracker.text() == "In range 1 s"
    for _ in range(20):                                   # 0.5 s of silence ends the stretch
        now += 0.025
        tracker.add(None, now)
    assert tracker.held == 0 and tracker.text() == "Not in range yet"
    for _ in range(40):                                   # a second below the range
        now += 0.025
        tracker.add(120.0, now)
    assert tracker.share() == pytest.approx(0.5, abs=0.05)
    assert speech.RangeTracker(None).text() == "" and speech.RangeTracker(None).share() is None


# --- per-phrase and per-passage results, from synthetic voices with a known f0 ---

@pytest.fixture(scope="module")
def steady_take():
    track = synth.with_silence(synth.steady_track(150, 2.5), 0.3, 0.3)
    return analysis.analyze_take(synth.synth_voice(track, synth.NEUTRAL_FORMANTS))


def profile_with(tmp_path, low, high):
    profile = Profile(tmp_path / "home")
    profile.set_target("pitch", (low, high))
    return profile


def test_phrase_result_scores_the_time_in_the_pitch_target(steady_take, tmp_path):
    result = speech.phrase_result(steady_take, profile_with(tmp_path, 140, 165))
    assert result["ok"] and result["pitch"] == pytest.approx(150, abs=4)
    assert result["pitch_share"] > 0.9 and "in your range" in result["text"] and "Pitch 150 Hz" in result["text"]
    far = speech.phrase_result(steady_take, profile_with(tmp_path, 200, 260))
    assert far["pitch_share"] < 0.1 and "0% in your range" in far["text"] or far["pitch_share"] < 0.1


def test_phrase_result_without_a_target_still_reports_the_pitch(steady_take):
    result = speech.phrase_result(steady_take, None)
    assert result["ok"] and result["pitch_share"] is None and result["text"].startswith("Pitch 150 Hz")


def test_a_phrase_with_too_little_voice_asks_for_another_go():
    quiet = analysis.analyze_take(synth.room_noise(2.0))
    if "error" in quiet:
        quiet = {"tracks": {"f0": [np.nan] * 50, "times": list(np.arange(50) * 0.01)}}
    result = speech.phrase_result(quiet, None)
    assert not result["ok"] and "Try it again" in result["text"]


def test_passage_result_for_a_first_reading_and_a_repeat(steady_take, tmp_path):
    profile = profile_with(tmp_path, 140, 165)
    first = speech.passage_result(steady_take | {"uid": "a"}, None, profile)
    assert first["first"] and first["compare"] == speech.FIRST_READING and first["headline"]
    assert all(row["change"] is None for row in first["rows"])
    earlier = dict(steady_take, pitch=steady_take["pitch"] - 20, time="2026-10-20T09:00:00")
    again = speech.passage_result(steady_take | {"uid": "b", "time": NOW}, earlier, profile)
    pitch = next(r for r in again["rows"] if r["key"] == "pitch")
    assert not again["first"] and "last time you read this" in again["compare"]
    assert pitch["change"] == "20 Hz higher than last time" or pitch["change"].endswith("higher than last time")
    assert [r["name"] for r in again["rows"]][0] == "Pitch"


def test_phrases_summary_after_three():
    assert speech.phrases_summary([{"pitch": 190}] * 2) == ""
    assert speech.phrases_summary([{"pitch": 180}, {"pitch": 190}, {"pitch": 200}]) == \
        "Three phrases done. Your pitch averaged 190 Hz."


# --- the vowel check result ---

def test_vowel_card_says_what_the_check_changed():
    class Stub:
        vowel_baseline = None

        def log_vowel_check(self, result):
            pass

    session = speech.VowelCheckSession.__new__(speech.VowelCheckSession)
    session.profile, session.baseline_saved = Stub(), False
    session.result = {"delta_f": 1100.0, "formant_ceiling": 5500, "skipped": ["hod"], "vowels": {}}
    card = session.card()
    assert card["headline"] == "Your resonance across eight vowels is 1100 Hz"
    assert "5500 Hz ceiling" in card["lines"][0] and "Not measured: hod." in card["lines"]
    assert card["question"].startswith("Use this as your vowel baseline?")
    session.baseline_saved = True
    assert session.card()["question"].startswith("Saved as your vowel baseline")
    session.result = None
    assert session.card() is None


# --- exercises ---

def score(cents):
    notes = [{"midi": 48 + i, "cents": c, "in_tune": 1.0, "close": 1.0, "octave": 0} for i, c in enumerate(cents)]
    sung = [c for c in cents if c is not None]
    return {"notes": notes, "notes_total": len(cents), "notes_sung": len(sung),
            "notes_in_tune": sum(abs(c) <= 25 for c in sung), "notes_close": sum(abs(c) <= 50 for c in sung),
            "mean_abs_cents": float(np.mean([abs(c) for c in sung])) if sung else None, "timing": "timing: on time"}


def test_exercise_result_grades_each_note_and_names_the_weakest():
    result = exercise.exercise_result(score([3, -12, 31, None, 70]))
    assert [n["grade"] for n in result["notes"]] == ["in_tune", "in_tune", "close", "missed", "off"]
    assert result["headline"] == "2 of 5 notes in tune"
    assert result["weakest"]["name"] == "D#3" and result["weakest"]["text"] == "not heard"      # missed first
    assert "Retry D#3" in result["retry"] and "100 cents is one semitone" in result["explain"]
    flat = exercise.exercise_result(score([0, -60, 10]))
    assert flat["weakest"]["name"] == "C#3" and flat["weakest"]["text"] == "60 cents flat"
    perfect = exercise.exercise_result(score([1, 2, 3]))
    assert perfect["weakest"] is None and "landed" in perfect["retry"]
    silent = exercise.exercise_result(score([None, None]))
    assert silent["headline"] == "No sung notes were heard." and silent["detail"] == ""


def test_cents_are_said_in_words_without_negative_zero():
    assert exercise.cents_words(-0.2) == "0 cents" and exercise.cents_words(12.4) == "+12 cents"
    assert exercise.cents_words(-31) == "-31 cents"


def test_the_start_note_sits_in_your_range():
    low, high = 48, 72
    start = exercise.comfortable_start((low, high))
    assert low <= start <= high and start + 12 <= high
    assert exercise.comfortable_start((60, 66)) == 60                               # a narrow range: its bottom
    assert exercise.comfortable_start(None) == exercise.DEFAULT_START_MIDI
    assert exercise.comfortable_start(None, [220.0, 225.0]) == 57 - exercise.START_FROM_BOTTOM


# --- the Voice Map ---

def test_range_text_names_its_support_in_seconds():
    data = voice_map.VoiceMapData()
    for midi, frames in ((48, 120), (55, 400), (60, 40)):
        col = midi - voice_map.MIDI_LO
        data.counts[col, 20] += frames
    text = voice_map.range_text(data)
    head, low, high = text.split("\n")
    assert head.startswith("Your range so far: C3–C4") and "octave" in head
    assert low == "Lowest note C3: 3 s of data" and high == "Highest note C4: 1 s of data (thin)"
    assert voice_map.range_text(voice_map.VoiceMapData()) == voice_map.EMPTY_RANGE


def test_range_changes_and_legends_are_plain():
    assert voice_map.range_change_text((36, 86), (48, 62)) == "Your range changed from C2–D6 to C3–D4."
    assert voice_map.range_change_text((48, 62), (48, 62)) == "Your range stays C3–D4."
    assert voice_map.range_change_text(None, (48, 62)) == "Your range is now C3–D4."
    assert voice_map.range_change_text((48, 62), None) == "The map is empty now."
    assert voice_map.legend_text("cpp") == "Yellow = clear, purple = breathy (8 to 30 dB)"
    for key in voice_map.MAP_METRICS:
        first_word = voice_map.legend_text(key).split()[0]
        assert first_word == "Yellow" and "CPP" not in voice_map.legend_text(key)
        assert "CPP" not in voice_map.MAP_METRICS[key][0] and "H1" not in voice_map.MAP_METRICS[key][0]
    grid = voice_map.MapSession.__new__(voice_map.MapSession)
    assert "semitones" not in voice_map.octaves_text(30) and voice_map.octaves_text(5) == "less than an octave"
    assert grid is not None


# --- states ---

def test_every_state_has_words_and_a_reason_when_start_is_off():
    ready = practice_state.state_view("read", {})
    assert ready["state"] == "ready" and "Press Start reading" in ready["detail"]
    running = practice_state.state_view("read", {"recording": True, "seconds": 42})
    assert running["state"] == "running" and running["elapsed"] == "0:42"
    paused = practice_state.state_view("phrases", {"recording": True, "seconds": 3, "mic_ok": False})
    assert paused["state"] == "paused" and "mic" in paused["detail"]
    analysing = practice_state.state_view("read", {"analysing": True})
    assert analysing["state"] == "running" and analysing["indeterminate"]
    assert practice_state.state_view("read", {"has_result": True})["state"] == "done"
    assert practice_state.state_view("read", {"error": "No microphone"})["state"] == "error"
    vowels = practice_state.state_view("vowels", {"running": True, "index": 2, "total": 8})
    assert vowels["label"] == "Word 3 of 8" and vowels["progress"] == (2, 8)
    sing = practice_state.state_view("exercise", {"running": True, "now": 3.0, "end": 6.0})
    assert sing["progress"] == (3.0, 6.0) and sing["state"] == "running"
    assert practice_state.state_view("voicemap", {"seconds": 90})["label"] == "Map ready"
    assert practice_state.state_view("voicemap", {"rebuilding": True})["indeterminate"]
    assert practice_state.disabled_reason({"record": True}, ("idle", "Record")) is None
    assert "still being analysed" in practice_state.disabled_reason({"record": False}, ("analysing", "Analysing…"))
    assert "microphone" in practice_state.disabled_reason({"record": False}, ("disabled", "Mic unavailable"))
    assert "Another practice" in practice_state.disabled_reason({"record": False}, ("disabled", "Practice running"))
    assert practice_state.disabled_reason({"record": False}, ("recording", None), recording=True) is None


def test_the_readout_says_what_it_hears_and_whether_you_are_in_range():
    tracker = speech.RangeTracker((150, 200))
    for i in range(50):
        tracker.add(180.0, i * 0.025)
    snap = {"f0": 180.0, "metrics": {"delta_f": {"shown": "1010 Hz"}}}
    view = practice_state.readout_view(snap, tracker, True)
    assert view["hz"] == 180.0 and view["state_text"] == "Hearing you" and view["range_ok"]
    assert view["range_text"].startswith("In range ") and "of the time so far" in view["range_text"]
    assert view["resonance"] == "Resonance 1010 Hz"
    silent = practice_state.readout_view({"f0": None}, tracker, False)
    assert silent["hz"] is None and silent["state_text"] == "Say something" and silent["range_text"] == ""
    quiet = practice_state.readout_view({"f0": None, "state": "too_quiet"}, tracker, True)
    assert quiet["state_text"].startswith("Too quiet")
    withheld = practice_state.readout_view({"f0": 180.0, "metrics": {"delta_f": {"shown": "—"}}}, tracker, True)
    assert withheld["resonance"] == ""


def test_start_is_off_for_a_drill_only_when_something_else_runs():
    ready = practice_state.state_view("vowels", {})
    running = practice_state.state_view("vowels", {"running": True, "index": 1})
    busy = practice_state.state_view("exercise", {"analysing": True})
    assert practice_state.start_reason(ready, False, False) is None
    assert practice_state.start_reason(running, True, False) is None            # Stop stays available
    assert "recording is running" in practice_state.start_reason(ready, True, False)
    assert "still being analysed" in practice_state.start_reason(ready, False, True)
    assert "still being worked out" in practice_state.start_reason(busy, False, False)


# --- the report on a day's routine ---

def test_a_whole_routine_report_has_each_step_and_compares_with_last_session():
    import session_report
    before = [take("phrases", day="2026-10-27", pitch=190.0, delta_f=1000.0, uid="old")]
    phrases = [take("phrases", hour=9, pitch=p, delta_f=1100.0, uid=f"p{i}") for i, p in enumerate((200, 205, 210))]
    reading = take("passage: Vowel tour", hour=10, pitch=173.0, uid="r1")
    vowel = {"delta_f": 1180.0, "formant_ceiling": 4800, "time": f"{TODAY}T08:50:00"}
    passage = {"headline": "Your voice sat in the overlap.", "tip": "Let the ends of sentences lift.",
               "rows": [{"name": "Pitch", "value": "173 Hz", "change": None},
                        {"name": "Intonation", "value": "5.0 st", "change": "1.6 st more varied than last time"}]}
    rep = session_report.build({"day": TODAY, "mode": "speak", "skipped": []}, phrases + [reading], vowel,
                               {"p0": 0.8, "p1": 0.7, "p2": 0.9}, passage, before, (180, 220))
    assert rep["key"] == f"session:{TODAY}:speak" and rep["headline"] == "You did the whole routine."
    assert (rep["steps_done"], rep["steps"]) == (3, 3) and rep["minutes"] == 70
    warmup, phr, read = rep["sections"]
    assert warmup["state"] == "Done" and "1180 Hz" in warmup["headline"]
    rows = {name: (value, change) for name, value, change in phr["rows"]}
    assert rows["Pitch"] == ("205 Hz", "15 Hz higher than last session") and rows["Resonance"][1].endswith("brighter than last session")
    assert rows["In your pitch range"][0] == "80% of the time" and phr["uids"] == ["p0", "p1", "p2"]
    assert read["headline"] == passage["headline"] and read["uids"] == ["r1"] and read["rows"][0][:2] == ("Pitch", "173 Hz")
    assert read["rows"][1] == ("Intonation", "5.0 semitones", "1.6 semitones more varied than last time")
    assert rep["next_time"] == "Let the ends of sentences lift."
    assert session_report.parse_key(rep["key"]) == (TODAY, "speak") and session_report.parse_key("abc") is None


def test_a_partial_routine_says_what_was_left_and_aims_at_the_range():
    import session_report
    phrases = [take("phrases", uid=f"p{i}") for i in range(2)]
    rep = session_report.build({"day": TODAY, "mode": "speak", "skipped": ["read"]}, phrases)
    assert rep["headline"] == "You did 0 of 3 steps." or rep["steps_done"] == 0
    states = [s["state"] for s in rep["sections"]]
    assert states == ["Not done", "Not done", "Skipped"] and rep["sections"][2]["headline"] == "Skipped today."
    assert "whole routine" in rep["next_time"]
    low = session_report.build({"day": TODAY, "mode": "speak", "skipped": []}, phrases, None,
                               {"p0": 0.2, "p1": 0.3}, None, (), (180, 220))
    assert "between 180 and 220 Hz" in low["next_time"] and "25%" in low["next_time"]


def test_a_sing_routine_reports_the_notes_in_tune():
    import session_report
    ex = [take("Five-note scale", mode="sing", exercise={"notes_total": 9, "notes_in_tune": 7}, uid=f"e{i}")
          for i in range(2)]
    rep = session_report.build({"day": TODAY, "mode": "sing", "skipped": []}, ex)
    assert rep["key"].endswith(":sing") and rep["steps_done"] == 2
    assert [x["title"] for x in rep["sections"]] == ["Exercises"]                 # each exercise counted once
    assert rep["sections"][0]["headline"] == "14 of 18 notes in tune." and rep["sections"][0]["state"] == "Done"


def test_history_mixes_sessions_with_takes_and_filters_them():
    import session_report
    from datetime import datetime
    s = {"day": TODAY, "mode": "speak", "time": f"{TODAY}T11:00:00"}
    old = {"day": "2026-10-20", "mode": "speak", "time": "2026-10-20T11:00:00"}
    takes = [take("phrases", hour=10, uid="a"), take("phrases", day="2026-10-21", uid="b")]
    merged = session_report.merge(takes, [s, old])
    assert [k for k, _ in merged] == ["session", "take", "take", "session"]
    assert [k for k, _ in session_report.merge(takes, [s, old], show="sessions")] == ["session", "session"]
    assert [k for k, _ in session_report.merge(takes, [s], show="takes")] == ["take", "take"]
    assert len(session_report.merge(takes, [s, old], oldest="2026-10-21")) == 3       # the old one waits
    same = take("phrases", hour=11, uid="c") | {"time": f"{TODAY}T11:00:00.500000"}
    assert session_report.merge([same], [s])[0][0] == "session"                     # above its own last take
    now = datetime.fromisoformat(NOW)
    assert session_report.matches(s, "practice", now=now) and not session_report.matches(s, "soup", now=now)
    assert not session_report.matches(s, task="phrases") and not session_report.matches(s, mode="sing")
    assert not session_report.matches(old, days=7, now=now) and session_report.matches(s, days=7, now=now)
    view = session_report.row_view(s, takes)
    assert view["title"] == "Daily practice" and "Speak" in view["subtitle"] and view["chip"] == "0 of 3 steps"
