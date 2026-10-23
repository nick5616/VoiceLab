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
    assert abs(12 * np.log2(high / low) - 4) < 1e-9  # +/- 2 semitones (the floor)
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


def test_history_keeps_exercise_and_singing(tmp_path):
    profile = Profile(tmp_path)
    take = TAKE | {"uid": "u1", "mode": "sing",
                   "singing": {"vibrato_rate": np.float64(5.6), "drift": np.nan},
                   "exercise": {"name": "Scale", "notes_total": 9, "notes_in_tune": np.int64(7),
                                "mean_abs_cents": 14.0, "notes": [{"midi": 48}], "targets": [(0, 1, 48)]}}
    profile.log_take(take)
    entry = profile.read_history()[0]
    assert entry["mode"] == "sing" and entry["uid"] == "u1"
    assert entry["singing"] == {"vibrato_rate": 5.6, "drift": None}
    assert entry["exercise"]["notes_in_tune"] == 7 and "notes" not in entry["exercise"]


def test_forget_takes(tmp_path):
    profile = Profile(tmp_path)
    for uid in ("a", "b"):
        profile.log_take(TAKE | {"uid": uid})
    profile.log_vowel_check({"delta_f": 1000.0, "formant_ceiling": 5500, "vowels": {}})
    with profile.history_path.open("a") as f:
        f.write("{truncated\n")
    profile.forget_takes(["a"])
    assert [e.get("uid") for e in profile.read_history()] == ["b", None]


def test_settings_round_trip(tmp_path):
    profile = Profile(tmp_path)
    profile.set_takes_dir(tmp_path / "takes")
    profile.set_input_device("USB Mic")
    reloaded = Profile(tmp_path)
    assert reloaded.takes_dir == str(tmp_path / "takes") and reloaded.takes_dir_asked
    assert reloaded.input_device == "USB Mic"


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


REGISTER = {"chest_share": 0.2, "mix_share": 0.1, "head_share": 0.7, "fry_share": 0.0,
            "breaks": [1.0, 2.0], "breaks_per_min": 12.0, "calibrated": False}


def test_register_summary_and_target(tmp_path):
    profile = Profile(tmp_path)
    take = TAKE | {"register": REGISTER, "cq": np.float64(0.45)}
    profile.set_targets_from_take(take)
    low, high = profile.targets["register"]
    assert abs(low - 55) < 1e-9 and abs(high - 85) < 1e-9        # head share 70% +/- 15
    profile.log_take(take)
    entry = profile.read_history()[0]
    assert abs(entry["register"] - 70) < 1e-9 and abs(entry["chest_share"] - 20) < 1e-9
    assert entry["breaks_per_min"] == 12.0 and entry["cq"] == 0.45
    profile.set_target("register", (0, 100))
    assert profile.targets["register"] == [0.0, 100.0]
    assert Profile(tmp_path).targets["register"] == [0.0, 100.0]
    assert profile.change_text("register", 50) is None            # no baseline


def test_register_model_and_egg_channel_persist(tmp_path):
    profile = Profile(tmp_path)
    assert profile.register_model is None and profile.register_version == 0 and profile.egg_channel is None
    profile.set_register_model({"weights": [1.0], "bias": 0.1, "calibrated": True})
    profile.set_egg_channel(1)
    again = Profile(tmp_path)
    assert again.register_model["calibrated"] and again.register_version == 1 and again.egg_channel == 1


def test_targets_from_take_only_for_trusted_metrics(tmp_path):
    profile = Profile(tmp_path)
    profile.set_target("delta_f", (1000, 1100))
    changed = profile.set_targets_from_take(TAKE, trusted={"pitch", "cpp"})
    assert profile.target("delta_f") == [1000, 1100]            # untrusted: left as it was
    assert profile.target("pitch") and profile.target("cpp") and profile.target("ring") is None
    assert set(changed) == {"pitch", "cpp"}
    profile.set_targets_from_take(TAKE)                          # no limit: replaces everything
    assert profile.target("delta_f") != [1000, 1100] and profile.target("ring")


def test_summary_has_task_and_tier(tmp_path):
    from profile_store import summarize
    assert summarize(TAKE)["task"] == "free speech"
    assert summarize(TAKE | {"mode": "sing"})["task"] == "free singing"
    assert summarize(TAKE | {"exercise": {"name": "Scale"}})["task"] == "Scale"
    assert summarize(TAKE | {"task": "passage: Rainbow"})["task"] == "passage: Rainbow"
    assert summarize(TAKE | {"snr": 5.0})["tier"] == "poor"


def test_noise_floor_and_mic_check_persist(tmp_path):
    profile = Profile(tmp_path)
    assert profile.noise_floor is None and profile.mic_check is None and not profile.mic_check_asked
    profile.set_noise_floor(-61.5)
    profile.set_mic_check({"level_db": -30.0})
    reloaded = Profile(tmp_path)
    assert reloaded.noise_floor == -61.5 and reloaded.mic_check == {"level_db": -30.0}
    assert reloaded.mic_check_asked


def test_voice_goal_persists_and_validates(tmp_path):
    profile = Profile(tmp_path)
    assert profile.voice_goal is None                  # just exploring until chosen
    profile.set_voice_goal("androgynous")
    assert Profile(tmp_path).voice_goal == "androgynous"
    profile.set_voice_goal(None)
    assert Profile(tmp_path).voice_goal is None
    try:
        profile.set_voice_goal("female")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown goal must be refused")
    data = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
    data["voice_goal"] = "nonsense"                     # a hand-edited file falls back to None
    (tmp_path / "profile.json").write_text(json.dumps(data), encoding="utf-8")
    assert Profile(tmp_path).voice_goal is None
