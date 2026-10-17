"""Checks the measurements against synthetic vowels with known parameters.

Run with: python -m pytest tests
"""
import os
import sys

import numpy as np
from scipy.signal import lfilter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import analysis  # noqa: E402

SR = analysis.SAMPLE_RATE
# Real voices have an F5 near 4.5 kHz (male); without it Praat's 5-formant fit
# invents a spurious peak.
MALE_FORMANTS = [(500, 80), (1500, 100), (2500, 120), (3500, 150), (4500, 200)]


def scaled(formants, factor):
    return [(f * factor, b * factor) for f, b in formants]


def synth_vowel(f0, formants, seconds=1.5, open_quotient=0.6, noise=0.0, seed=0):
    """Glottal pulse train (Rosenberg-style) through a cascade of resonators.

    A larger open quotient gives a more sinusoidal source with a stronger first
    harmonic, i.e. a 'lighter' voice; a smaller one gives a 'heavier' one.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    phase = (t * f0) % 1.0
    opening = open_quotient * 0.66
    pulse = np.where(phase < opening, 0.5 * (1 - np.cos(np.pi * phase / opening)),
                     np.where(phase < open_quotient,
                              np.cos(0.5 * np.pi * (phase - opening) / (open_quotient - opening)),
                              0.0))
    source = np.diff(pulse, prepend=0.0)
    source = source + noise * np.std(source) * rng.standard_normal(n)
    out = source
    for freq, bw in formants:
        r = np.exp(-np.pi * bw / SR)
        theta = 2 * np.pi * freq / SR
        a = [1, -2 * r * np.cos(theta), r ** 2]
        out = lfilter([sum(a)], a, out)
    out = lfilter([1, -0.97], [1], out)  # lip radiation
    return 0.3 * out / np.max(np.abs(out))


def test_pitch_and_note():
    result = analysis.analyze_take(synth_vowel(220, MALE_FORMANTS))
    assert abs(result["pitch"] - 220) < 2
    assert result["note"] == "A3" and abs(result["cents"]) <= 10


def test_delta_f_recovers_formant_spacing():
    result = analysis.analyze_take(synth_vowel(120, MALE_FORMANTS))
    expected = analysis.delta_f(500, 1500, 2500, 3500)  # 1000 Hz
    assert abs(result["delta_f"] - expected) / expected < 0.06
    assert 16 < result["vtl"] < 19


def test_formant_ceiling_adapts_to_smaller_tract():
    for f0 in (200, 300):
        # Leading silence, as in a real take, must not change the result.
        audio = np.concatenate([np.zeros(int(0.3 * SR)), synth_vowel(f0, scaled(MALE_FORMANTS, 1.18))])
        result = analysis.analyze_take(audio)
        assert result["formant_ceiling"] > analysis.DEFAULT_FORMANT_CEILING
        assert abs(result["delta_f"] - 1180) / 1180 < 0.04


def test_smaller_tract_reads_brighter_regardless_of_pitch():
    low_pitch_small_tract = analysis.analyze_take(synth_vowel(120, scaled(MALE_FORMANTS, 1.18)))
    high_pitch_large_tract = analysis.analyze_take(synth_vowel(200, MALE_FORMANTS))
    assert low_pitch_small_tract["delta_f"] > high_pitch_large_tract["delta_f"] + 100


def test_resonance_withheld_at_high_pitch():
    result = analysis.analyze_take(synth_vowel(600, scaled(MALE_FORMANTS, 1.2)))
    assert result["delta_f"] is None
    assert "too high" in result["resonance_note"]


def test_h1h2_tracks_source_not_vowel():
    light = analysis.analyze_take(synth_vowel(150, MALE_FORMANTS, open_quotient=0.8))
    heavy = analysis.analyze_take(synth_vowel(150, MALE_FORMANTS, open_quotient=0.35))
    assert light["h1h2"] > heavy["h1h2"] + 3
    # A low F1 boosts H1/H2 differently; the correction should keep the
    # same source reading close across vowels.
    low_f1 = [(300, 60), (2200, 100), (3000, 120), (3600, 150), (4500, 200)]
    other_vowel = analysis.analyze_take(synth_vowel(150, low_f1, open_quotient=0.8))
    assert abs(other_vowel["h1h2"] - light["h1h2"]) < 4


def test_cpp_drops_with_breathiness():
    clear = analysis.analyze_take(synth_vowel(150, MALE_FORMANTS))
    breathy = analysis.analyze_take(synth_vowel(150, MALE_FORMANTS, noise=0.6))
    assert clear["cpp"] > breathy["cpp"] + 3


def test_contour_is_in_semitones():
    def glide(f_start, f_end):
        n = int(1.5 * SR)
        f0 = np.geomspace(f_start, f_end, n)
        phase = np.cumsum(f0) / SR
        return np.sin(2 * np.pi * phase) + 0.5 * np.sin(4 * np.pi * phase)

    low = analysis.analyze_take(0.3 * glide(100, 150))
    high = analysis.analyze_take(0.3 * glide(400, 600))
    # Same musical interval, so the same contour despite 4x the Hz spread.
    assert abs(low["contour"] - high["contour"]) < 0.3


def test_level_is_not_normalized_away():
    loud = analysis.analyze_take(synth_vowel(150, MALE_FORMANTS))
    quiet_audio = synth_vowel(150, MALE_FORMANTS) * 0.1
    quiet = analysis.analyze_take(quiet_audio)
    assert abs((loud["level"] - quiet["level"]) - 20) < 1


def test_live_frame_analyzer():
    analyzer = analysis.FrameAnalyzer()
    audio = synth_vowel(180, MALE_FORMANTS)
    window = audio[-int(analyzer.WINDOW_SECONDS * SR):]
    frame = analyzer.analyze(window)
    assert abs(frame["f0"] - 180) < 3
    assert frame["resonance_reliable"]
    assert abs(frame["delta_f"] - 1000) < 80


def test_live_frame_silence():
    frame = analysis.FrameAnalyzer().analyze(np.zeros(int(0.12 * SR)))
    assert np.isnan(frame["f0"])
