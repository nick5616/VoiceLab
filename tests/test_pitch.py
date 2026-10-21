"""Pitch tracking and voicing on the user's noisy mic (tests/mic_noise.py), speak and sing modes."""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import analysis  # noqa: E402
import mic_noise  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402

SOPRANO_FORMANTS = [(800, 100), (1200, 120), (2800, 150), (3800, 200), (4800, 250)]


def noisy(voice, level=-42, seed=0):
    """Voice at the user's typical level over the measured mic noise."""
    return mic_noise.at_level(voice, level) + mic_noise.mic_noise(len(voice), seed)


def padded(voice, lead=1.0, tail=1.0, level=-42):
    """Voice between stretches of mic noise alone, at `level` dBFS."""
    quiet = np.zeros(int(lead * SR))
    tail_silence = np.zeros(int(tail * SR))
    audio = np.concatenate([quiet, mic_noise.at_level(voice, level), tail_silence])
    return audio + mic_noise.mic_noise(len(audio))


def cents_off(f0, truth):
    return 1200 * np.log2(f0[~np.isnan(f0)] / truth)


def test_mic_noise_alone_is_unvoiced_offline_and_live():
    noise = mic_noise.mic_noise(10 * SR)
    track = analysis.gated_pitch(noise, SR)
    assert track["voiced_fraction"] < 0.01
    assert np.mean(~np.isnan(live_f0s(noise))) < 0.01


def test_mic_noise_alone_is_not_a_take():
    assert "error" in analysis.analyze_take(mic_noise.mic_noise(5 * SR))


def test_speech_pitches_are_exact_with_no_halving():
    for f0 in (110, 150, 200, 250):
        audio = noisy(synth_vowel(f0, MALE_FORMANTS, seconds=2.0))
        result = analysis.analyze_take(audio)
        track = result["tracks"]["f0"]
        assert result["voiced_fraction"] > 0.8, f0
        assert abs(np.median(cents_off(track, f0))) < 10, f0
        assert np.nanmin(track) > f0 * 0.9 and np.nanmax(track) < f0 * 1.1, f0


def test_breathy_low_voice_is_unvoiced_or_right_never_an_octave_off():
    for f0, aspiration in ((130, 0.6), (165, 0.6), (130, 1.0), (165, 1.0)):
        voice = synth_vowel(f0, MALE_FORMANTS, seconds=2.0, open_quotient=0.9, noise=aspiration)
        track = analysis.gated_pitch(noisy(voice), SR)["f0"]
        cents = cents_off(track, f0)
        assert np.sum(np.abs(np.abs(cents) - 1200) < 150) < 0.05 * len(track), (f0, aspiration)
        assert np.sum(np.abs(cents) > 150) < 0.05 * len(track), (f0, aspiration)
    clear = analysis.gated_pitch(noisy(synth_vowel(165, MALE_FORMANTS, 2.0, 0.9, 0.6)), SR)["f0"]
    assert np.mean(~np.isnan(clear)) > 0.8        # moderately breathy is still tracked


def test_speech_contour_range_is_not_inflated_by_the_mic():
    f0 = 140 * 2 ** (np.sin(2 * np.pi * 0.6 * np.arange(int(3 * SR)) / SR) * 2 / 12)   # +-2 st
    result = analysis.analyze_take(padded(synth_vowel(f0, MALE_FORMANTS)))
    assert 2 < result["pitch_range"] < 6
    assert result["pitch_floor"] > 120


def test_pitch_range_uses_the_10th_to_90th_percentile():
    f0 = np.concatenate([np.linspace(100, 200, 100), [400.0] * 5, [50.0] * 5])
    assert 9 < analysis.pitch_range_st(f0) < 11.5        # the 5th-95th would read 12 st or more


def test_ghost_and_voiced_fractions_report_the_gate():
    audio = padded(synth_vowel(150, MALE_FORMANTS, seconds=2.0))
    result = analysis.analyze_take(audio)
    assert 0.35 < result["voiced_fraction"] < 0.6          # 2 s of 4
    assert 0 <= result["ghost_fraction"] < 0.2


def test_continuous_voice_without_pauses_is_not_gated_away():
    audio = noisy(synth_vowel(150, MALE_FORMANTS, seconds=3.0))
    assert analysis.gated_pitch(audio, SR)["voiced_fraction"] > 0.9


def test_sing_mode_tracks_notes_above_1200_hz():
    for f0 in (1000, 1320, 1400):
        audio = noisy(synth_vowel(f0, SOPRANO_FORMANTS, seconds=1.5), level=-30)
        track = analysis.gated_pitch(audio, SR, mode="sing")["f0"]
        assert np.mean(~np.isnan(track)) > 0.7, f0
        assert abs(np.median(cents_off(track, f0))) < 15, f0


def test_sing_mode_low_note_and_speak_mode_default():
    audio = noisy(synth_vowel(90, MALE_FORMANTS, seconds=2.0))
    assert abs(analysis.analyze_take(audio, SR, mode="sing")["pitch"] - 90) < 3
    assert abs(analysis.analyze_take(audio)["pitch"] - 90) < 3


def test_offline_speed_stays_under_half_realtime():
    audio = padded(synth_vowel(150, MALE_FORMANTS, seconds=16.0))       # 18 s
    start = time.perf_counter()
    analysis.analyze_take(audio)
    assert time.perf_counter() - start < 0.5 * len(audio) / SR


# --- live ---

def live_f0s(audio, mode="speak", **kwargs):
    analyzer = analysis.FrameAnalyzer(SR, 0.025, mode=mode, **kwargs)
    hop, win = int(0.025 * SR), int(analyzer.WINDOW_SECONDS * SR)
    padded_audio = np.concatenate([np.zeros(win), audio])
    return np.array([analyzer.analyze(padded_audio[end - win:end])["f0"]
                     for end in range(win, len(padded_audio), hop)])


def test_live_voice_from_the_first_frame_is_voiced_within_300_ms():
    audio = noisy(synth_vowel(150, MALE_FORMANTS, seconds=2.0))
    f0 = live_f0s(audio)
    assert np.mean(~np.isnan(f0[12:])) > 0.9
    assert np.any(~np.isnan(f0[:12]))             # 12 frames = 0.3 s


def test_live_held_note_at_8_db_snr_stays_voiced():
    note = synth_vowel(165, MALE_FORMANTS, seconds=9.0)
    noise = mic_noise.mic_noise(len(note) + SR)
    band = analysis.band_level_db(noise[:analysis.SPECTRAL_FRAME * 40], SR)
    voice = mic_noise.at_level(note, 0)
    voice_band = analysis.band_level_db(voice[SR:SR + analysis.SPECTRAL_FRAME * 40], SR)
    voice *= 10 ** ((band + 8 - voice_band) / 20)
    f0 = live_f0s(noise + np.concatenate([np.zeros(SR), voice]))
    held = f0[int(1.5 / 0.025):]
    assert np.mean(~np.isnan(held)) > 0.9
    assert np.mean(~np.isnan(held[-40:])) > 0.9


def test_live_seeded_noise_floor_is_used():
    analyzer = analysis.FrameAnalyzer(SR, 0.025, noise_floor=-60)
    assert analyzer.noise_floor == -60


def test_live_single_octave_frame_is_not_reported():
    steps = np.concatenate([synth_vowel(150, MALE_FORMANTS, seconds=1.0),
                            synth_vowel(300, MALE_FORMANTS, seconds=1.0)])
    f0 = live_f0s(noisy(steps))
    jumps = np.abs(1200 * np.log2(f0[1:] / f0[:-1]))
    assert np.nanmax(jumps) < 1300                # the step itself is one real jump
    cents = 1200 * np.log2(f0[~np.isnan(f0)] / 150)
    stray = (cents > 150) & (cents < 1050) | (cents < -150)
    assert stray.mean() < 0.05


def test_live_frame_cost_fits_the_tick():
    audio = noisy(synth_vowel(150, MALE_FORMANTS, seconds=2.0))
    analyzer = analysis.FrameAnalyzer(SR, 0.025)
    hop, win = int(0.025 * SR), int(analyzer.WINDOW_SECONDS * SR)
    times = []
    for end in range(win, len(audio), hop):
        start = time.perf_counter()
        analyzer.analyze(audio[end - win:end])
        times.append(time.perf_counter() - start)
    p50, p95 = np.percentile(times, [50, 95])
    print(f"live frame cost p50 {p50 * 1000:.1f} ms, p95 {p95 * 1000:.1f} ms")
    assert p95 < 0.025
