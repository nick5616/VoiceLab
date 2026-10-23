import glob
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import analysis  # noqa: E402
import coach  # noqa: E402
import mic_noise  # noqa: E402
import profile_store  # noqa: E402
from profile_store import Profile  # noqa: E402
from test_analysis import MALE_FORMANTS, scaled, synth_vowel  # noqa: E402

SR = analysis.SAMPLE_RATE
TARGETS = {"pitch": [165.0, 255.0], "delta_f": [1100.0, 1300.0], "h1h2": [2.0, 8.0],
           "ring": [-22.0, -16.0]}
# A take whose readings are all on target; each case below changes some of them.
ON_TARGET = {"pitch": 200.0, "delta_f": 1200.0, "h1h2": 5.0, "ring": -19.0, "cpp": 18.0,
             "pitch_range": 6.0, "pitch_floor": 170.0, "snr": 25.0, "level": -30.0}


def keys(summary, targets=TARGETS, **kwargs):
    return [m["key"] for m in coach.advice(summary, targets, **kwargs)]


# --- advice rules: one table-driven case each ---

RULE_CASES = [
    ("brighten_resonance", {"delta_f": 1000.0}, {}),
    ("raise_pitch", {"pitch": 150.0}, {}),
    ("one_thing_at_a_time", {"pitch": 150.0, "delta_f": 1000.0}, {}),
    ("low_pitch_floor", {"pitch_floor": 110.0}, {}),
    ("flat_intonation", {"pitch_range": 1.5}, {}),
    ("possible_pressing", {"h1h2": -2.0, "ring": -12.0}, {}),
    ("tense_high_notes", {"pitch": 400.0, "h1h2": -2.0, "ring": -12.0, "delta_f": None},
     {"mode": "sing"}),
    ("sing_flat", {"exercise": {"mean_signed_cents": -35.0}}, {"mode": "sing"}),
    ("sing_sharp", {"exercise": {"mean_signed_cents": 40.0}}, {"mode": "sing"}),
    ("sing_late", {"exercise": {"lag_ms": 450.0}}, {"mode": "sing"}),
    ("sing_early", {"exercise": {"lag_ms": -250.0}}, {"mode": "sing"}),
]
WIDE_PITCH = TARGETS | {"pitch": [165.0, 450.0]}


@pytest.mark.parametrize("key,change,kwargs", RULE_CASES)
def test_rule_fires(key, change, kwargs):
    assert key in keys(ON_TARGET | change, WIDE_PITCH, **kwargs)


def test_on_target_gets_praise_not_advice():
    assert keys(ON_TARGET) == ["on_target"]


def test_at_most_three_ordered_messages():
    summary = ON_TARGET | {"pitch": 150.0, "delta_f": 1000.0, "pitch_floor": 100.0,
                           "pitch_range": 1.0}
    messages = coach.advice(summary, TARGETS)
    assert len(messages) == 3
    assert set(messages[0]) == {"key", "text", "severity"}
    assert messages[0]["key"] == "one_thing_at_a_time"


def test_pressing_is_labelled_tentative():
    messages = coach.advice(ON_TARGET | {"h1h2": -2.0, "ring": -12.0}, TARGETS)
    assert messages[0]["key"] == "possible_pressing"
    assert messages[0]["severity"] == "tentative"
    assert "guess" in messages[0]["text"]


def test_pressing_needs_all_three():
    assert "possible_pressing" not in keys(ON_TARGET | {"ring": -12.0})
    assert "possible_pressing" not in keys(ON_TARGET | {"h1h2": -2.0})
    assert "possible_pressing" not in keys(ON_TARGET | {"h1h2": -2.0, "ring": -12.0, "pitch": 150.0})


def test_baseline_stands_in_for_missing_targets():
    baseline = {"pitch": 200.0, "delta_f": 1200.0}
    assert "brighten_resonance" in keys(ON_TARGET | {"delta_f": 1000.0}, {}, baseline=baseline)
    assert keys(ON_TARGET, {}) == []


# --- trust suppresses advice ---

def test_untrusted_metric_gets_no_advice():
    noisy = ON_TARGET | {"delta_f": 1000.0, "h1h2": -2.0, "ring": -12.0, "snr": 12.0}
    quality = coach.take_quality(noisy)
    assert not quality["trust"]["h1h2"]
    assert not quality["trust"]["ring"]
    result = keys(noisy, quality=quality)
    assert "possible_pressing" not in result
    assert "brighten_resonance" in result      # deltaF is still trusted at 12 dB
    assert keys(noisy | {"snr": 8.0}) == ["take_poor"]


def test_high_pitch_blocks_resonance_advice():
    summary = ON_TARGET | {"pitch": 300.0, "delta_f": 1000.0, "pitch_floor": 270.0}
    quality = coach.take_quality(summary)
    assert quality["trust"]["delta_f"] is False
    result = coach.advice(summary, TARGETS | {"pitch": [250.0, 350.0]}, quality=quality)
    assert [m["key"] for m in result] == ["cant_judge"]
    assert "brightness" in result[0]["text"]


def test_ghosts_degrade_then_block_pitch_advice():
    summary = ON_TARGET | {"pitch_floor": 110.0, "ghost_fraction": 0.3, "voiced_fraction": 0.5}
    quality = coach.take_quality(summary)
    assert quality["tier"] == "fair"
    assert "low_pitch_floor" in keys(summary, quality=quality)
    assert coach.take_quality(summary | {"ghost_fraction": 0.5})["tier"] == "poor"


def test_untrusted_singing_gives_no_singing_advice():
    summary = ON_TARGET | {"exercise": {"mean_signed_cents": -40.0}}
    quality = coach.take_quality(summary)
    assert "sing_flat" in keys(summary, quality=quality, mode="sing")
    quality["trust"]["singing"] = False
    assert "sing_flat" not in keys(summary, quality=quality, mode="sing")


# --- take_quality on synthetic takes ---

def make_take(noisy, level):
    voice = mic_noise.at_level(synth_vowel(200, scaled(MALE_FORMANTS, 1.15), seconds=1.5), level)
    gap = np.zeros(int(0.8 * SR))
    audio = np.concatenate([gap, voice, gap, voice, gap])
    if noisy:
        return audio + mic_noise.mic_noise(len(audio))
    return audio + np.random.default_rng(1).standard_normal(len(audio)) * 10 ** (-85 / 20)


def test_clean_take_is_good():
    quality = coach.take_quality(analysis.analyze_take(make_take(False, -30)))
    assert quality["tier"] == "good" and quality["reasons"] == []
    assert all(quality["trust"].values())


def test_mic_noise_at_user_level_is_fair():
    result = analysis.analyze_take(make_take(True, -42))
    noise = {"rumble_share": 0.86, "lines": [(4000.0, -73.5), (6000.0, -79.0)],
             "snr_voice_db": result["snr"]}
    quality = coach.take_quality(result, noise)
    assert quality["tier"] == "fair"
    text = " ".join(quality["reasons"])
    assert "quiet" in text and "background noise" in text
    assert "rumble" in text and "whine" in text
    assert quality["trust"]["pitch"] and quality["trust"]["delta_f"]
    assert not quality["trust"]["cpp"] and not quality["trust"]["h1h2"]     # 18 dB < 20 dB


def test_very_quiet_take_is_poor():
    quality = coach.take_quality(analysis.analyze_take(make_take(True, -52)))
    assert quality["tier"] == "poor"
    assert "move closer" in quality["reasons"][0]
    assert not any(quality["trust"].values())


def test_mostly_ghost_frames_is_poor():
    quality = coach.take_quality({"level": -35.0, "snr": 25.0, "ghost_fraction": 0.6})
    assert quality["tier"] == "poor"
    assert quality["trust"]["pitch"] is False


def test_clipping_and_missing_data():
    assert coach.take_quality({"clip_events": 50})["tier"] == "poor"
    assert coach.take_quality({"clip_events": 8})["tier"] == "fair"
    assert coach.take_quality({})["tier"] == "good"       # nothing known: nothing to distrust
    assert coach.take_quality({"snr": float("nan")})["tier"] == "good"


def test_noise_report_supplies_missing_snr():
    quality = coach.take_quality({"pitch": 200.0}, {"snr_voice_db": 12.0, "voiced_db": -45.0})
    assert quality["tier"] == "fair" and not quality["trust"]["cpp"]


# --- advice wording ---

def test_advice_text_is_plain_and_kind():
    banned = ("should have", "wrong", "bad", "fail", "male", "masculine", "feminine", "woman")
    for _, change, kwargs in RULE_CASES:
        for message in coach.advice(ON_TARGET | change, WIDE_PITCH, **kwargs):
            lowered = message["text"].lower()
            assert not any(word in lowered for word in banned), message
            assert message["text"].endswith(".") and len(message["text"]) < 400


# --- profile targets ---

def test_tolerance_floor_without_frames():
    assert profile_store.target_from_value("delta_f", 1000.0) == [900.0, 1100.0]
    low, high = profile_store.target_from_value("pitch", 200.0)
    assert abs(12 * np.log2(high / low) - 4) < 1e-9


def test_tolerance_from_frame_spread_with_cap_and_floor():
    rng = np.random.default_rng(0)
    wide = 1000 + rng.normal(0, 130, 400)
    low, high = profile_store.target_from_value("delta_f", 1000.0, wide)
    assert 100 < (high - low) / 2 < 200
    assert 0.6 < np.mean((wide >= low) & (wide <= high)) < 0.8
    low, high = profile_store.target_from_value("delta_f", 1000.0, 1000 + rng.normal(0, 600, 400))
    assert (high - low) / 2 == 200                                   # capped
    low, high = profile_store.target_from_value("delta_f", 1000.0, 1000 + rng.normal(0, 5, 400))
    assert (high - low) / 2 == 100                                   # floored
    assert profile_store.target_from_value("delta_f", 1000.0, [1.0, 2.0]) == [900.0, 1100.0]


def test_take_as_own_target_scores_over_sixty_percent():
    rng = np.random.default_rng(1)
    f0 = 200 * 2 ** (rng.normal(0, 1.5, 500) / 12)
    df = 1100 + rng.normal(0, 90, 500)
    result = {"pitch": float(np.median(f0)), "delta_f": float(np.median(df)), "h1h2": 4.0,
              "tracks": {"f0": f0, "delta_f": df}}
    profile = Profile.__new__(Profile)
    profile.targets, profile.save = {}, lambda: None
    profile.set_targets_from_take(result)
    assert profile.time_in_target("pitch", f0) > 0.6
    assert profile.time_in_target("delta_f", df) > 0.6
    assert profile.time_in_both(f0, df) > 0.45


def test_presets_and_joint_target(tmp_path):
    profile = Profile(tmp_path)
    assert "yours may differ" in profile_store.TARGET_PRESETS["speak"]["label"]
    profile.set_targets_from_preset("speak")
    assert Profile(tmp_path).targets["pitch"] == [165.0, 255.0]
    assert profile.in_both(200, 1200) is True
    assert profile.in_both(200, 1000) is False      # pitch alone is not green
    assert profile.in_both(150, 1200) is False
    assert profile.in_both(200, None) is None       # unknown is not on target
    assert profile.in_both(150, None) is False
    assert Profile(tmp_path / "empty").in_both(200, 1200) is None
    assert profile.time_in_both([200, 200, 150, np.nan], [1200, 1000, 1200, 1200]) == 1 / 3
    assert profile.time_in_both([np.nan], [np.nan]) is None


def test_old_profile_json_still_loads(tmp_path):
    (tmp_path / "profile.json").write_text(
        '{"targets": {"pitch": [180, 220], "delta_f": [1100, 1200]}, "mode": "speak"}')
    profile = Profile(tmp_path)
    assert profile.targets["delta_f"] == [1100, 1200]
    assert profile.in_both(200, 1150) is True


# --- real takes (private; skipped when absent) ---

REAL = sorted(glob.glob(os.path.join(os.path.expanduser("~"), "VoiceLab", "*.wav")))


@pytest.mark.skipif(not REAL, reason="no real takes on this machine")
def test_real_takes_own_target_resonance_time_in_target():
    import soundfile
    for path in REAL:
        audio, rate = soundfile.read(path)
        result = analysis.analyze_take(audio if audio.ndim == 1 else audio[:, 0], rate)
        if result["delta_f"] is None:   # phase 9 spec 05: resonance is withheld when most F1 are unreadable
            continue
        profile = Profile.__new__(Profile)
        profile.targets, profile.save = {}, lambda: None
        profile.set_targets_from_take(result)
        assert profile.time_in_target("delta_f", result["tracks"]["delta_f"]) >= 0.6, path
