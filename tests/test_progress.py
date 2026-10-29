"""Progress series: filtering, weekly medians and change since the first week."""
import os
import sys
from datetime import datetime, timedelta

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import progress  # noqa: E402
from progress import change_text, collect_entries, select, week_start, weekly_medians  # noqa: E402

MONDAY = datetime(2026, 9, 7, 12)


def entry(days, **values):
    return {"kind": "take", "time": (MONDAY + timedelta(days=days)).isoformat(), "mode": "speak"} | values


def test_select_filters():
    entries = [
        entry(0, delta_f=1000),
        entry(1, delta_f=1010, mode="sing"),
        entry(2, delta_f=1300, source="soprano.wav"),     # someone else's voice
        entry(3, delta_f=None),
        {"kind": "vowels", "time": MONDAY.isoformat(), "delta_f": 990},
        entry(4, delta_f=float("nan")),
    ]
    _, values = select(entries, "delta_f")
    assert list(values) == [1000, 1010]
    assert list(select(entries, "delta_f", mode="speak")[1]) == [1000]
    assert list(select(entries, "delta_f", include_imported=True)[1]) == [1000, 1010, 1300]
    assert list(select(entries, "vowel_delta_f")[1]) == [990]
    now = MONDAY + timedelta(days=30)
    assert len(select(entries, "delta_f", days=29.5, now=now)[1]) == 1


def test_nested_series():
    entries = [entry(0, exercise={"notes_total": 8, "notes_in_tune": 6, "mean_abs_cents": 18.0},
                     singing={"vibrato_rate": 5.5, "vibrato_extent": None})]
    assert list(select(entries, "in_tune")[1]) == [75.0]
    assert list(select(entries, "exercise_error")[1]) == [18.0]
    assert list(select(entries, "vibrato_rate")[1]) == [5.5]
    assert len(select(entries, "vibrato_extent")[1]) == 0


def test_weekly_medians_group_by_monday():
    entries = [entry(d, delta_f=v) for d, v in ((0, 1000), (2, 1020), (6, 1100), (7, 1050), (13, 1070))]
    times, values = select(entries, "delta_f")
    starts, medians = weekly_medians(times, values)
    assert len(starts) == 2
    assert datetime.fromtimestamp(starts[0]).weekday() == 0
    assert list(medians) == [1020, 1060]
    assert week_start(times[2]) == starts[0]          # Sunday belongs to the week before


def test_change_text():
    # At least 5 takes each, or no change is claimed (spec 07).
    entries = [entry(0, delta_f=1000), entry(1, delta_f=1010), entry(2, delta_f=1005), entry(8, delta_f=1050),
               entry(9, delta_f=1050),
               *[entry(d, pitch=150.0) for d in (0, 1, 2)], *[entry(d, pitch=300.0) for d in (8, 9)],
               *[entry(d, cpp=20.0) for d in (0, 1, 2)], *[entry(d, cpp=21.5) for d in (8, 9)]]
    assert "+45 Hz since week of Sep 7" in change_text("delta_f", *select(entries, "delta_f"))
    assert "+12.0 semitones" in change_text("pitch", *select(entries, "pitch"))
    assert "+1.5 dB" in change_text("cpp", *select(entries, "cpp"))
    one_week = select([entry(0, cpp=20.0)], "cpp")
    assert change_text("cpp", *one_week).endswith("20.0 dB · 1 take")
    assert change_text("cpp", np.array([]), np.array([])) == "no data yet"


def test_collect_entries_counts_each_take_once():
    folder_take = {"uid": "a", "time": MONDAY.isoformat(), "pitch": 200.0, "mode": "speak"}
    history = [{"kind": "take", "uid": "a", "time": MONDAY.isoformat(), "pitch": 200.0},
               {"kind": "take", "time": MONDAY.isoformat(), "pitch": 180.0},       # older log, no uid
               {"kind": "vowels", "time": MONDAY.isoformat(), "delta_f": 1000.0}]
    entries = collect_entries(history, [folder_take])
    assert sorted(select(entries, "pitch")[1]) == [180.0, 200.0]
    assert len(select(entries, "vowel_delta_f")[1]) == 1


def test_register_and_cq_series():
    entries = [entry(0, register=60.0, chest_share=30.0, breaks_per_min=4.0, cq=0.41, mode="sing"),
               entry(1, register=40.0, chest_share=50.0, breaks_per_min=9.0, cq=0.43)]
    assert list(select(entries, "head_share")[1]) == [60.0, 40.0]
    assert list(select(entries, "chest_share")[1]) == [30.0, 50.0]
    assert list(select(entries, "register_breaks")[1]) == [4.0]       # only singing
    assert list(select(entries, "cq")[1]) == [0.41, 0.43]
    assert select([entry(0)], "cq")[1].size == 0                      # no EGG, no series
    assert "0.42" in change_text("cq", *select(entries, "cq"))


# --- THE PROGRESS SCREEN (phase 10 spec 06) ---

def test_overall_placement_needs_pitch_and_resonance():
    import progress
    import report
    assert progress.overall_of(entry(0, pitch=220.0)) is None
    assert progress.overall_of(entry(0, pitch=220.0, delta_f=1130.0, mode="sing")) is None
    (pitch_m, pitch_f), (df_m, df_f) = report.ANCHORS["pitch"], report.ANCHORS["delta_f"]
    feminine = progress.overall_of(entry(0, pitch=pitch_f + 5.0, delta_f=df_f + 10.0))
    masculine = progress.overall_of(entry(0, pitch=pitch_m - 5.0, delta_f=df_m - 10.0))
    assert feminine == 100.0 and masculine == 0.0                      # beyond the report's anchors
    middle = progress.overall_of(entry(0, pitch=162.0, delta_f=1030.0))
    assert 100 * report.OVERALL_EDGES[0] < middle < 100 * report.OVERALL_EDGES[1]


def test_report_view_charts_each_dimension_on_its_zones():
    import progress
    from profile_store import Profile
    import tempfile
    profile = Profile(tempfile.mkdtemp())
    profile.set_target("pitch", (180, 220))
    entries = [entry(d, pitch=150.0 + d, delta_f=1000.0 + d, pitch_floor=120.0, tier="good") for d in range(6)]
    entries.append(entry(1, pitch=90.0, delta_f=900.0, tier="poor"))
    entries.append(entry(2, pitch=300.0, delta_f=1200.0, mode="sing", task="free singing"))
    view = progress.report_view(entries, profile)
    keys = [k for k, _ in view["charts"]]
    assert keys == progress.REPORT_KEYS and keys[0] == "overall"
    charts = dict(view["charts"])
    pitch = charts["pitch"]
    assert len(pitch["values"]) == 6 and pitch["target"] == [180.0, 220.0] and pitch["log"]
    assert pitch["y_range"] == (80, 400) and pitch["latest_zone"] == "overlap"
    assert charts["h1h2"] is None and charts["overall"]["zones"] is progress.OVERALL_ZONES
    assert view["task"] == "free speech" and "1 poor recording" in view["left_out"]
    assert len(progress.report_view(entries, profile, include_poor=True)["charts"][1][1]["values"]) == 7


def test_mini_gives_weekly_medians_for_the_last_weeks():
    import progress
    from profile_store import Profile
    import tempfile
    now = MONDAY + timedelta(days=20)
    entries = [entry(d, pitch=150.0 + d, delta_f=1000.0, tier="good") for d in (0, 1, 15, 16)]
    out = progress.mini(entries, Profile(tempfile.mkdtemp()), weeks=4, now=now)
    pitch = out["series"][0]
    assert pitch["key"] == "pitch" and len(pitch["values"]) == 4
    assert pitch["values"] == [None, 150.5, None, 165.5] and pitch["latest"] == "166 Hz"
    assert out["series"][2]["latest"] in ("overlap", "typically masculine")


# --- PHASE 11 / 04: AM I GETTING BETTER? ---

NOW = MONDAY + timedelta(days=29, hours=4)


def month(pitch_by_day, **extra):
    """One good speak take per (day, pitch) pair; resonance rises with pitch so both lines move."""
    return [entry(d, pitch=p, delta_f=900 + p / 2, tier="good", **extra) for d, p in pitch_by_day]


def test_a_steady_voice_says_so_and_names_no_goal_direction():
    entries = month([(d, 150.0 + (d % 3)) for d in range(0, 30)])
    for goal in (None, "feminine", "masculine", "androgynous"):
        text = progress.summary_text(entries, goal=goal, now=NOW)
        assert "no clear change" in text and "30 days" in text and "30 takes" in text
        assert "toward" not in text and "away" not in text


def test_the_goal_decides_toward_and_away_and_exploring_never_says_either():
    rising = month([(d, 140.0) for d in range(0, 15)] + [(d, 165.0) for d in range(15, 30)])
    fem = progress.summary_text(rising, goal="feminine", now=NOW)
    masc = progress.summary_text(rising, goal="masculine", now=NOW)
    free = progress.summary_text(rising, goal=None, now=NOW)
    assert "moving toward your goal" in fem and "higher" in fem
    assert "further from your goal" in masc and "higher" in masc
    assert "toward" not in free and "goal" not in free and "higher" in free
    assert progress.summary_text(rising, goal="exploring", now=NOW) == free     # an unknown goal reads as none
    falling = month([(d, 165.0) for d in range(0, 15)] + [(d, 140.0) for d in range(15, 30)])
    assert "moving toward your goal" in progress.summary_text(falling, goal="masculine", now=NOW)
    assert "lower" in progress.summary_text(falling, goal="masculine", now=NOW)


def test_androgynous_goal_is_toward_the_middle_of_the_overlap_zone():
    far = month([(d, 120.0) for d in range(0, 15)] + [(d, 150.0) for d in range(15, 30)])
    assert "moving toward your goal" in progress.summary_text(far, goal="androgynous", now=NOW)
    passed = month([(d, 150.0) for d in range(0, 15)] + [(d, 210.0) for d in range(15, 30)])
    assert "further from your goal" in progress.summary_text(passed, goal="androgynous", now=NOW)


def test_summary_counts_takes_and_admits_too_few():
    few = month([(28, 150.0), (29, 151.0)])
    assert "Not enough takes" in progress.summary_text(few, now=NOW) and "2 so far" in progress.summary_text(few, now=NOW)
    out = progress.summary(month([(d, 150.0) for d in range(0, 30)]), now=NOW)
    assert out["takes"] == 30 and {ln["days"] for ln in out["lines"]} == {7, 30}
    assert all(ln["n"] >= 1 and ln["text"] for ln in out["lines"])
    only_week = progress.summary_text(month([(d, 150.0) for d in range(0, 30)]), days=7, now=NOW)
    assert "last 7 days" in only_week


def test_sure_needs_enough_takes_on_both_sides():
    rough = progress.summary(month([(d, 140.0) for d in range(22, 25)] + [(d, 170.0) for d in range(26, 30)]),
                             goal="feminine", now=NOW)
    assert all(not ln["sure"] for ln in rough["lines"])
    sure = progress.summary(month([(d, 140.0) for d in range(0, 15)] + [(d, 165.0) for d in range(15, 30)]),
                            goal="feminine", now=NOW)
    assert any(ln["sure"] for ln in sure["lines"] if ln["days"] == 30)


def test_variation_notes_name_a_real_difference_and_stay_quiet_otherwise():
    morning = [dict(entry(d, pitch=170.0 + d % 2, delta_f=1000.0, tier="good"), time=(MONDAY + timedelta(days=d, hours=-3)).isoformat())
               for d in range(0, 6)]
    evening = [dict(entry(d, pitch=150.0 + d % 2, delta_f=1000.0, tier="good"), time=(MONDAY + timedelta(days=d, hours=8)).isoformat())
               for d in range(6, 12)]
    notes = progress.variation_notes(morning + evening, now=NOW)
    assert any(n["factor"] == "time of day" and "mornings" in n["text"] and "higher" in n["text"] for n in notes)
    assert all("semitones" in n["text"] for n in notes if n["key"] == "pitch")
    flat = [entry(d, pitch=150.0 + d % 2, delta_f=1000.0, tier="good") for d in range(12)]
    assert progress.variation_notes(flat, now=NOW) == []
    assert progress.variation_notes(morning[:2], now=NOW) == []


def test_variation_notes_compare_take_length_and_recording_quality():
    short = [entry(d, pitch=150.0 + d % 2, delta_f=1000.0, tier="good", duration=6.0) for d in range(0, 6)]
    long_ = [entry(d, pitch=135.0 + d % 2, delta_f=1000.0, tier="fair", duration=25.0) for d in range(6, 12)]
    factors = {n["factor"] for n in progress.variation_notes(short + long_, now=NOW)}
    assert {"take length", "recording quality"} <= factors


def test_practice_days_counts_days_and_stays_quiet_with_none():
    entries = [entry(d, pitch=150.0) for d in (26, 26, 27, 29)] + [entry(10, pitch=150.0), entry(28, pitch=150.0, source="x.wav")]
    out = progress.practice_days(entries, now=NOW)
    assert out["days"] == 3 and out["window"] == 7 and out["text"] == "Practised 3 of the last 7 days"
    none = progress.practice_days([entry(1, pitch=150.0)], now=NOW)
    assert none["days"] == 0 and none["text"] == ""
    assert progress.practice_days([], now=NOW)["text"] == ""


def test_task_counts_match_what_the_charts_draw():
    entries = [entry(d, pitch=150.0, delta_f=1000.0, tier="good", task="free speech") for d in range(5)] +               [entry(6, pitch=150.0, delta_f=1000.0, tier="good", task="free speech", source="friend.wav"),
               entry(7, pitch=150.0, delta_f=1000.0, tier="poor", task="free speech")]
    assert progress.task_counts(entries) == [("free speech", 5)]
    assert progress.task_counts(entries, include_poor=True) == [("free speech", 6)]
    view = progress.report_view(entries, __import__("profile_store").Profile(__import__("tempfile").mkdtemp()))
    assert len(dict(view["charts"])["pitch"]["values"]) == 5 == view["tasks"][0][1]


def test_charts_say_when_there_are_too_few_weeks_for_a_line():
    two_weeks = [entry(d, pitch=150.0 + d) for d in (0, 1, 8)]
    times, values = select(two_weeks, "pitch")
    chart = progress.chart("pitch", times, values)
    assert chart["line"] is False and "Line = weekly median" in chart["legend"]
    three = [entry(d, pitch=150.0 + d) for d in (0, 8, 15)]
    assert progress.chart("pitch", *select(three, "pitch"))["line"] is True


def test_steady_charts_say_steady_in_one_short_line():
    entries = [entry(d, pitch=150.0 + (d % 2)) for d in (0, 1, 2, 3, 8, 9, 10, 11)]
    chart = progress.chart("pitch", *select(entries, "pitch"))
    assert chart["steady"] and "steady" in chart["change"] and "variation" not in chart["change"]
    assert len(chart["change"]) < 70
