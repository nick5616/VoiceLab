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
    if np.ndim(f0):  # per-sample pitch contour (vibrato, melodies)
        n = len(f0)
        phase = (np.cumsum(f0) / SR) % 1.0
    else:
        n = int(seconds * SR)
        phase = (np.arange(n) / SR * f0) % 1.0
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


def test_pitch_range_in_semitones():
    def glide(f_start, f_end):
        f0 = np.geomspace(f_start, f_end, int(1.5 * SR))
        return 0.3 * np.sin(2 * np.pi * np.cumsum(f0) / SR)

    # An octave glide spans ~0.9 octave between its 5th and 95th percentiles.
    assert abs(analysis.analyze_take(glide(110, 220))["pitch_range"] - 10.8) < 1
    assert analysis.analyze_take(synth_vowel(150, MALE_FORMANTS))["pitch_range"] < 0.5


# Adult male averages from Hillenbrand et al. (1995): F1, F2, F3.
HVD_FORMANTS = {
    "heed": (342, 2322, 3000), "hid": (427, 2034, 2684), "head": (580, 1799, 2605),
    "had": (588, 1952, 2601), "hod": (768, 1333, 2522), "hud": (623, 1200, 2550),
    "hood": (469, 1122, 2434), "who'd": (378, 997, 2343),
}


def hvd_token(word, f0, scale=1.0):
    f1, f2, f3 = HVD_FORMANTS[word]
    formants = [(f1, 80), (f2, 100), (f3, 120), (3600, 150), (4500, 200)]
    vowel = synth_vowel(f0, scaled(formants, scale), seconds=0.35)
    gap = np.zeros(int(0.25 * SR))
    return np.concatenate([gap, vowel, gap])


def test_vowel_check_recovers_vowel_space():
    tokens = {word: hvd_token(word, 120) for word in analysis.VOWEL_WORDS}
    result = analysis.analyze_vowels(tokens)
    assert result["skipped"] == []
    for word, (f1, f2, _) in HVD_FORMANTS.items():
        v = result["vowels"][word]
        assert abs(v["f1"] - f1) / f1 < 0.12, word
        assert abs(v["f2"] - f2) / f2 < 0.08, word


def test_vowel_check_balanced_resonance_tracks_tract_size():
    words = analysis.VOWEL_WORDS
    larger = analysis.analyze_vowels({w: hvd_token(w, 120) for w in words})
    smaller = analysis.analyze_vowels({w: hvd_token(w, 200, scale=1.15) for w in words})
    assert abs(smaller["delta_f"] / larger["delta_f"] - 1.15) < 0.05


def test_vowel_check_skips_silent_tokens():
    tokens = {"heed": hvd_token("heed", 120), "hid": np.zeros(int(0.8 * SR))}
    result = analysis.analyze_vowels(tokens)
    assert result["skipped"] == ["hid"]
    assert "heed" in result["vowels"]


def test_take_has_resonance_track():
    result = analysis.analyze_take(synth_vowel(150, MALE_FORMANTS))
    track = result["tracks"]["delta_f"]
    assert len(track) == len(result["tracks"]["f0"])
    assert abs(np.nanmedian(track) - result["delta_f"]) < 1


def vibrato_contour(f0, seconds, rate=5.5, extent_cents=50):
    t = np.arange(int(seconds * SR)) / SR
    return f0 * 2 ** (extent_cents * np.sin(2 * np.pi * rate * t) / 1200)


def melody_contour(midis, note_seconds, offset_cents=0.0):
    hz = analysis.midi_to_hz(np.repeat(midis, int(note_seconds * SR)) + offset_cents / 100)
    return hz


def test_vibrato_rate_and_extent():
    result = analysis.analyze_take(synth_vowel(vibrato_contour(220, 2.0), MALE_FORMANTS))
    singing = result["singing"]
    assert singing["held_notes"] == 1 and singing["vibrato_notes"] == 1
    assert abs(singing["vibrato_rate"] - 5.5) < 0.3
    assert abs(singing["vibrato_extent"] - 50) < 10
    assert singing["vibrato_regularity"] < 0.1


def test_straight_tone_has_no_vibrato():
    singing = analysis.analyze_take(synth_vowel(220, MALE_FORMANTS, seconds=2.0))["singing"]
    assert singing["vibrato_notes"] == 0 and singing["vibrato_rate"] is None
    assert singing["drift"] < 3
    assert singing["tuning_offset"] < 5  # 220 Hz is exactly A3


def test_notes_are_segmented_without_splitting_vibrato():
    contour = np.concatenate([vibrato_contour(220, 1.0), vibrato_contour(277.2, 1.0),
                              vibrato_contour(329.6, 1.0)])
    singing = analysis.analyze_take(synth_vowel(contour, MALE_FORMANTS))["singing"]
    assert singing["notes"] == 3


def test_exercise_scoring_with_octave_folding():
    midis = [57, 59, 61, 62, 64]                 # A3 B3 C#4 D4 E4
    targets = [(0.5 + i * 0.6, 0.6, m) for i, m in enumerate(midis)]
    # Sung an octave low and 20 cents sharp, after half a second of silence.
    audio = np.concatenate([np.zeros(int(0.5 * SR)),
                            synth_vowel(melody_contour(np.array(midis) - 12, 0.6, 20), MALE_FORMANTS)])
    result = analysis.analyze_take(audio)
    score = analysis.score_exercise(result["tracks"]["times"], result["tracks"]["f0"], targets)
    assert score["notes_sung"] == 5 and score["notes_in_tune"] == 5
    assert all(n["octave"] == -1 for n in score["notes"])
    assert abs(score["mean_abs_cents"] - 20) < 5


def test_exercise_scoring_flags_missed_and_flat_notes():
    targets = [(0.0, 0.6, 57), (0.6, 0.6, 59), (1.2, 0.6, 61)]
    contour = np.concatenate([melody_contour([57], 0.6, -45), melody_contour([59], 0.6)])
    audio = np.concatenate([synth_vowel(contour, MALE_FORMANTS), np.zeros(int(0.6 * SR))])
    result = analysis.analyze_take(audio)
    score = analysis.score_exercise(result["tracks"]["times"], result["tracks"]["f0"], targets)
    assert score["notes_sung"] == 2 and score["notes_in_tune"] == 1
    assert score["notes"][0]["cents"] < -35
    assert score["notes"][2]["cents"] is None


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
