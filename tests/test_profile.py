import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from profile_store import Profile  # noqa: E402

TAKE = {"pitch": 200.0, "delta_f": 1150.0, "h1h2": np.float64(4.0), "cpp": 20.0,
        "ring": -15.0, "contour": 2.0, "pitch_range": 6.0, "level": -20.0, "vtl": 15.2,
        "formant_ceiling": np.int64(6000), "note": "G3", "cents": 35,
        "audio": np.zeros(10), "tracks": {}}


def test_round_trip(tmp_path):
    profile = Profile(tmp_path)
    profile.set_baseline(TAKE | {"cpp": np.nan})
    profile.set_target("pitch", (220, 180))
    profile.set_formant_ceiling(6000)

    reloaded = Profile(tmp_path)
    assert reloaded.baseline["pitch"] == 200.0
    assert reloaded.baseline["cpp"] is None          # NaN stored as null
    assert reloaded.targets["pitch"] == [180, 220]   # sorted
    assert reloaded.formant_ceiling == 6000


def test_targets_from_take(tmp_path):
    profile = Profile(tmp_path)
    profile.set_targets_from_take(TAKE | {"delta_f": None})
    low, high = profile.targets["pitch"]
    assert abs(12 * np.log2(high / low) - 3) < 1e-9  # +/- 1.5 semitones
    assert profile.targets["h1h2"] == [1.0, 7.0]
    assert "delta_f" not in profile.targets

    profile.set_targets_from_take(TAKE | {"pitch_range": 0.5})
    assert profile.targets["pitch_range"][0] == 0.0


def test_in_target_and_change(tmp_path):
    profile = Profile(tmp_path)
    profile.set_target("delta_f", (1100, 1200))
    assert profile.in_target("delta_f", 1150) is True
    assert profile.in_target("delta_f", 1050) is False
    assert profile.in_target("delta_f", np.nan) is None
    assert profile.in_target("pitch", 200) is None

    assert profile.change_text("pitch", 200) is None
    profile.set_baseline(TAKE)
    assert profile.change_text("pitch", 400) == "+12.0 st vs baseline"
    assert profile.change_text("delta_f", 1180) == "+30 Hz vs baseline"


def test_history_log(tmp_path):
    profile = Profile(tmp_path)
    profile.log_take(TAKE)
    profile.log_take(TAKE)
    lines = (tmp_path / "history.jsonl").read_text().splitlines()
    assert len(lines) == 2
    entry = json.loads(lines[0])
    assert entry["pitch"] == 200.0 and "audio" not in entry and "time" in entry


def test_time_in_target(tmp_path):
    profile = Profile(tmp_path)
    assert profile.time_in_target("pitch", [200, 210]) is None
    profile.set_target("pitch", (180, 220))
    assert profile.time_in_target("pitch", [np.nan, 170, 190, 200, 230]) == 0.5
    assert profile.time_in_target("pitch", [np.nan]) is None


def test_vowel_baseline_and_log(tmp_path):
    profile = Profile(tmp_path)
    result = {"delta_f": np.float64(1050.0), "formant_ceiling": np.int64(5500), "skipped": [],
              "vowels": {"heed": {"f1": np.float64(340.0), "f2": 2300.0, "delta_f": np.nan}}}
    profile.set_vowel_baseline(result)
    profile.log_vowel_check(result)
    reloaded = Profile(tmp_path)
    assert reloaded.vowel_baseline["vowels"]["heed"]["f1"] == 340.0
    assert reloaded.vowel_baseline["vowels"]["heed"]["delta_f"] is None
    entry = json.loads((tmp_path / "history.jsonl").read_text().splitlines()[0])
    assert entry["kind"] == "vowels" and entry["delta_f"] == 1050.0


def test_corrupt_profile_is_ignored(tmp_path):
    (tmp_path / "profile.json").write_text("{not json")
    profile = Profile(tmp_path)
    assert profile.baseline is None and profile.targets == {}
