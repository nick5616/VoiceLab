"""Voice shifting, noise estimation and (optional) vocal isolation."""
import os
import sys

import numpy as np
import pytest

sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
import analysis  # noqa: E402
from shift import shift_voice  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402

BASE = synth_vowel(120, MALE_FORMANTS, seconds=2.0)


@pytest.fixture(scope="module")
def base():
    return analysis.analyze_take(BASE)


def test_neutral_shift_is_a_no_op():
    out = shift_voice(BASE, SR)
    assert np.allclose(out, BASE, atol=1e-9)


def test_pitch_shift(base):
    out = analysis.analyze_take(shift_voice(BASE, SR, semitones=4))
    assert abs(out["pitch"] / base["pitch"] - 2 ** (4 / 12)) < 0.015
    assert abs(out["delta_f"] / base["delta_f"] - 1) < 0.04      # resonance kept


def test_resonance_shift_keeps_pitch(base):
    out = analysis.analyze_take(shift_voice(BASE, SR, resonance=15))
    assert abs(out["delta_f"] / base["delta_f"] - 1.15) < 0.04
    assert abs(out["pitch"] / base["pitch"] - 1) < 0.02


def test_tempo_changes_duration_only(base):
    out = shift_voice(BASE, SR, tempo=125)
    assert abs(len(out) / len(BASE) - 0.8) < 0.02
    assert abs(analysis.analyze_take(out)["pitch"] / base["pitch"] - 1) < 0.02


def test_intonation_range_scales_movement():
    f0 = 150 * 2 ** (3 * np.sin(np.linspace(0, 4 * np.pi, int(2.0 * SR))) / 12)
    glide = synth_vowel(f0, MALE_FORMANTS)
    before = analysis.analyze_take(glide)["pitch_range"]
    after = analysis.analyze_take(shift_voice(glide, SR, intonation=200))["pitch_range"]
    assert abs(after / before - 2) < 0.25


def test_breathiness_lowers_clarity(base):
    out = analysis.analyze_take(shift_voice(BASE, SR, breathiness=80))
    assert out["cpp"] < base["cpp"] - 3


def test_brightness_raises_ring_not_resonance(base):
    brighter = analysis.analyze_take(shift_voice(BASE, SR, brightness=4))
    darker = analysis.analyze_take(shift_voice(BASE, SR, brightness=-4))
    assert brighter["ring"] > base["ring"] + 3 > darker["ring"] + 6
    for shifted in (brighter, darker):
        assert abs(shifted["delta_f"] / base["delta_f"] - 1) < 0.05


def test_shift_keeps_loudness():
    out = shift_voice(BASE, SR, semitones=-3, brightness=3)
    assert abs(analysis.level_dbfs(out) - analysis.level_dbfs(BASE)) < 1


def hiss(n, cutoff=4500, seed=1):
    """High-pitched mic hiss: white noise above `cutoff`."""
    from scipy.signal import butter, sosfilt
    w = np.random.default_rng(seed).standard_normal(n)
    return sosfilt(butter(6, cutoff, "high", fs=SR, output="sos"), w)


def with_pauses(voice, noise):
    gap = np.zeros(int(0.5 * SR))
    audio = np.concatenate([gap, voice, gap])
    return audio + noise[:len(audio)]


def test_snr_from_pauses():
    rng = np.random.default_rng(1)
    voice = synth_vowel(150, MALE_FORMANTS, seconds=1.0)
    for noise_db in (-60, -40):
        noise = 10 ** (noise_db / 20) * rng.standard_normal(2 * SR)
        result = analysis.analyze_take(with_pauses(voice, noise))
        expected = analysis.level_dbfs(voice) - analysis.level_dbfs(noise)
        assert abs(result["snr_full"] - expected) < 4, noise_db
        # White noise has less energy in the voice band, so band SNR is higher.
        assert result["snr"] > result["snr_full"]


def test_snr_ignores_hiss_above_the_voice_band():
    voice = synth_vowel(150, MALE_FORMANTS, seconds=1.0)
    noise = hiss(2 * SR)
    noise *= 10 ** (-20 / 20) * np.sqrt(np.mean(voice ** 2)) / np.sqrt(np.mean(noise ** 2))
    result = analysis.analyze_take(with_pauses(voice, noise))
    assert abs(result["snr_full"] - 20) < 3
    assert result["snr"] > 40


def test_measures_robust_to_high_pitched_noise():
    voice = synth_vowel(130, MALE_FORMANTS, seconds=1.5, noise=0.15)
    clean = analysis.analyze_take(voice)
    noise = hiss(len(voice))
    noise *= 10 ** (-20 / 20) * np.sqrt(np.mean(voice ** 2)) / np.sqrt(np.mean(noise ** 2))
    noisy = analysis.analyze_take(voice + noise)
    assert abs(noisy["cpp"] - clean["cpp"]) < 1.5
    assert abs(noisy["ring"] - clean["ring"]) < 1
    assert abs(noisy["h1h2"] - clean["h1h2"]) < 1
    assert abs(noisy["pitch"] - clean["pitch"]) < 1


def test_pitch_tracking_survives_heavy_hiss():
    voice = synth_vowel(130, MALE_FORMANTS, seconds=1.5, noise=0.15)
    noise = hiss(len(voice))
    noise *= 10 ** (-10 / 20) * np.sqrt(np.mean(voice ** 2)) / np.sqrt(np.mean(noise ** 2))
    clean = analysis.analyze_take(voice)["tracks"]["f0"]
    noisy = analysis.analyze_take(voice + noise)["tracks"]["f0"]
    assert (~np.isnan(noisy)).sum() >= 0.95 * (~np.isnan(clean)).sum()


def test_snr_needs_pauses():
    assert analysis.analyze_take(BASE)["snr"] is None


def test_noise_monitor():
    monitor = analysis.NoiseMonitor(400)
    for _ in range(50):
        monitor.add({"level": -50.0, "band_level": -70.0, "f0": np.nan})
    assert monitor.snr() == (None, None)              # no voice yet
    for _ in range(30):
        monitor.add({"level": -20.0, "band_level": -20.0, "f0": 200.0})
    assert monitor.snr() == (50.0, 30.0)


def test_vocal_isolation_removes_accompaniment():
    isolation = pytest.importorskip("isolation")
    if not isolation.available():
        pytest.skip("Demucs not installed (requirements-isolation.txt)")
    t = np.arange(int(4.0 * SR)) / SR
    vocal = synth_vowel(220 * 2 ** (30 * np.sin(2 * np.pi * 5.5 * t) / 1200), MALE_FORMANTS)
    # Accompaniment: sustained sawtooth chord plus a kick drum pattern.
    chord = sum(0.05 * (2 * ((t * f) % 1) - 1) for f in (130.8, 164.8, 196.0))
    kick = np.zeros_like(t)
    for start in np.arange(0, 4, 0.5):
        i = int(start * SR)
        n = int(0.15 * SR)
        kick[i:i + n] = 0.4 * np.sin(2 * np.pi * 60 * t[:n]) * np.exp(-t[:n] * 25)
    mix = vocal + chord + kick
    isolated = isolation.isolate_vocals(mix, SR)
    assert len(isolated) == len(mix)

    def corr(a, b):
        return np.corrcoef(a, b)[0, 1]
    assert corr(isolated, vocal) > corr(mix, vocal) + 0.1
    result = analysis.analyze_take(isolated)
    assert abs(result["pitch"] - 220) < 5
