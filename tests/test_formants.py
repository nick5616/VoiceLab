"""Formant, resonance and weight measurements on synthetic vowels with known parameters."""
import os
import sys

import numpy as np
import parselmouth

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import analysis  # noqa: E402
import mic_noise  # noqa: E402
from test_analysis import HVD_FORMANTS, SR, scaled, synth_vowel  # noqa: E402

CEILING = 5500
WORDS = ["heed", "hid", "head", "had", "hod", "hud", "hood", "who'd"]


def vowel(word, f0, scale=1.0, seconds=0.5, **kwargs):
    f1, f2, f3 = HVD_FORMANTS[word]
    formants = [(f1, 80), (f2, 100), (f3, 120), (3600, 150), (4500, 200)]
    return synth_vowel(f0, scaled(formants, scale), seconds=seconds, **kwargs)


def passage(words, f0=130, scale=1.0, lead=1.2):
    """Vowels with pauses between them, a quiet lead-in first (noise-only audio for the line search)."""
    gap = np.zeros(int(0.2 * SR))
    pieces = [np.zeros(int(lead * SR))]
    for word in words:
        pieces += [vowel(word, f0, scale), gap]
    return np.concatenate(pieces)


def track_median(result, key):
    return float(np.nanmedian(result["tracks"][key][result["tracks"]["steady"]]))


def test_f1_withheld_when_pitch_is_too_high():
    # Synthetic /i/ and /u/ read F1 +23% at 260 Hz and +34% at 300 Hz (speech audit): withhold it.
    for word in ("heed", "who'd"):
        for f0 in (260, 300):
            result = analysis.analyze_take(vowel(word, f0, seconds=1.2), mode="sing")
            assert not result["tracks"]["f1_ok"].any(), (word, f0)
            assert result["delta_f"] is None and "too high" in result["resonance_note"]
            assert np.isnan(result["h1h2"])      # needs a real F1
            assert result["f1_missing_fraction"] > 0.95


def test_f1_to_f3_accurate_at_speaking_pitch():
    # Not "heed" and "who'd": with F1 under 2 x f0 (342 Hz at 200 Hz) F1 reads high even at speaking pitch.
    for word in ("hid", "head", "hod", "hood"):
        for f0 in (150, 180, 220):
            result = analysis.analyze_take(vowel(word, f0, seconds=1.2), ceiling=CEILING)
            for i, key in enumerate(("f1", "f2", "f3")):
                true = HVD_FORMANTS[word][i]
                assert abs(track_median(result, key) / true - 1) < 0.10, (word, f0, key)


def test_f1_missing_rules():
    f1 = np.array([200.0, 300.0, 600.0, np.nan, 600.0])
    f0 = np.array([100.0, 250.0, 150.0, 150.0, 300.0])
    # below 250 Hz; under 1.3 x f0 (and over the f0 limit); fine; absent; f0 over the limit
    assert list(analysis.f1_missing(f1, f0)) == [True, True, False, True, True]


def test_vowel_mix_barely_moves_delta_f():
    # Same voice and vowels, same fixed ceiling, different counts of each (the plain mean moves ~110 Hz).
    base = ["heed", "hid", "head", "hod", "who'd"]
    mixes = [base, ["heed"] * 3 + ["hid", "head", "head", "hod", "hod", "who'd"], ["who'd"] * 3 + base[:4]]
    results = [analysis.analyze_take(passage(words), ceiling=CEILING) for words in mixes]
    plain = [np.nanmean(r["tracks"]["delta_f"][r["tracks"]["steady"]]) for r in results]
    balanced = [r["delta_f"] for r in results]
    assert np.ptp(balanced) < 30
    assert np.ptp(plain) > 2 * np.ptp(balanced)


def test_take_reports_spread_and_higher_formants():
    result = analysis.analyze_take(passage(["hid", "had", "hood"]), ceiling=CEILING)
    assert result["delta_f_iqr"] >= 0 and result["vowel_frames"] >= analysis.NUCLEUS_MIN_FRAMES
    assert abs(result["f3"] / 2550 - 1) < 0.15 and 3000 < result["f4"] < 4000
    assert result["vtl_apparent_cm"] == result["vtl"] == analysis.vocal_tract_length_cm(result["delta_f"])
    assert result["analysis_version"] == analysis.ANALYSIS_VERSION == 10


def test_fixed_ceiling_is_used_and_default_is_on_the_grid():
    audio = passage(["hid", "head", "hod"])
    assert analysis.analyze_take(audio, ceiling=6000)["formant_ceiling"] == 6000
    assert analysis.analyze_take(audio)["formant_ceiling"] in analysis.FORMANT_CEILINGS


def test_profile_ceiling_pools_takes_and_prefers_vowel_check():
    takes = [analysis.analyze_take(passage(words)) for words in (["hid", "head", "hod", "hood"], ["head", "had", "hud"])]
    ceiling = analysis.choose_profile_ceiling(takes)
    assert ceiling in analysis.FORMANT_CEILINGS
    assert analysis.choose_profile_ceiling(takes) == ceiling       # deterministic
    check = {"vowels": {}, "formant_ceiling": 5000, "delta_f": 1000.0}
    assert analysis.choose_profile_ceiling(takes + [check]) == 5000
    assert analysis.choose_profile_ceiling([]) == analysis.DEFAULT_FORMANT_CEILING


def test_steady_frames_skip_moving_and_quiet_frames():
    n = 30
    freqs = np.tile([500.0, 1500.0, 2500.0, 3500.0], (n, 1))
    freqs[15:, 0] = 500 * 1.3 ** np.arange(15)      # F1 glides: not a held vowel
    levels = np.full(n, -30.0)
    levels[:5] = -60                                # quiet
    steady = analysis.steady_vowel_frames(freqs, levels, np.ones(n, bool))
    assert steady[5:15].all() and not steady[:5].any() and not steady[15:].any()


def test_delta_f_counts_each_vowel_once():
    freqs = np.array([[500, 1500, 2500, 3500]] * 20 + [[300, 2300, 3000, 4000]] * 5, float)
    df = analysis.delta_f(*freqs.T)
    mask = np.ones(25, bool)
    mask[[10, 11, 12, 13, 14]] = False              # two stretches of vowel A, one of vowel B
    balanced = analysis.vowel_balanced_delta_f(freqs, df, mask)
    assert abs(balanced - (df[0] + df[-1]) / 2) < 1


def test_line_cancellation_leaves_formants_and_repairs_f4():
    voice = mic_noise.at_level(passage(["hid", "head", "hod", "hood"], f0=120, lead=1.5), -42)
    quiet = analysis.analyze_take(voice, ceiling=CEILING)
    noisy_audio = voice + mic_noise.mic_noise(len(voice), seed=3)
    noisy = analysis.analyze_take(noisy_audio, ceiling=CEILING)
    for key in ("f1", "f2", "f3"):
        assert abs(track_median(noisy, key) - track_median(quiet, key)) < 10, key
    assert 4000 in [round(hz) for hz, _ in noisy["noise"]["lines"]]

    # Without cancellation (formants straight off the noisy audio) F4 strays further from the quiet value.
    sound = parselmouth.Sound(noisy_audio, sampling_frequency=SR)
    times, steady = quiet["tracks"]["times"], quiet["tracks"]["steady"] & noisy["tracks"]["steady"]
    raw_f4 = analysis.formant_values(analysis.to_formants(sound, CEILING), times)[0][:, 3]
    reference = quiet["tracks"]["f4"]
    cleaned_error = np.nanmean(np.abs(noisy["tracks"]["f4"][steady] - reference[steady]))
    raw_error = np.nanmean(np.abs(raw_f4[steady] - reference[steady]))
    assert cleaned_error < 0.8 * raw_error


def test_noise_report_is_stored_and_serialisable():
    audio = mic_noise.at_level(passage(["hid", "head"], lead=1.5), -42)
    result = analysis.analyze_take(audio + mic_noise.mic_noise(len(audio), seed=1))
    noise = result["noise"]
    assert noise["snr_db"] > 5 and all(len(line) == 2 for line in noise["lines"])


def cpps_of(audio):
    return analysis.analyze_take(audio, ceiling=CEILING)["cpps"]


def test_cpps_ranks_clean_above_breathy_above_noisy():
    clean = vowel("hod", 150, seconds=2.0)
    breathy = vowel("hod", 150, seconds=2.0, open_quotient=0.9, noise=0.6)
    noisy = mic_noise.at_level(clean, -42) + mic_noise.broadband(len(clean), seed=5, gain_db=20)
    values = [analysis.cpps(a, SR) for a in (clean, breathy, noisy)]   # 32, 12, 6.5 dB when written
    assert values[0] > values[1] > values[2], values


def test_cpps_value_for_modal_voice_is_in_published_range():
    # A modal vowel at the user's mic level and SNR (~16 dB). Praat CPPS of healthy sustained vowels
    # is roughly 8-20 dB (Maryn & Weenink 2015; the range is unverified, from memory); the app's
    # per-frame CPP of the same audio reads 27 dB.
    voice = mic_noise.at_level(vowel("hod", 150, seconds=2.0), -42)
    assert 8 < cpps_of(voice + mic_noise.mic_noise(len(voice), seed=2)) < 20


def test_cpps_needs_voiced_audio():
    assert np.isnan(analysis.cpps(np.zeros(SR), SR))


def test_live_frame_withholds_snapped_f1():
    analyzer = analysis.FrameAnalyzer()
    window = vowel("heed", 280, seconds=1.0)[-int(analyzer.WINDOW_SECONDS * SR):]
    frame = analyzer.analyze(window)
    assert abs(frame["f0"] - 280) < 5
    assert not frame["resonance_reliable"] and np.isnan(frame["formants"][0])
    assert np.isnan(frame["h1h2"]) and np.isnan(frame["delta_f"])
