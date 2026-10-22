"""Progress series: filtering, weekly medians and change since the first week."""
import os
import sys
from datetime import datetime, timedelta

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
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
    assert "+12.0 st" in change_text("pitch", *select(entries, "pitch"))
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
