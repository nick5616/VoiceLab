"""Line detection, coherent cancelling and the noise report, on the synthetic model of the user's mic."""
import os
import sys

import numpy as np
import parselmouth

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import analysis  # noqa: E402
import denoise  # noqa: E402
import mic_noise  # noqa: E402
from test_analysis import MALE_FORMANTS, scaled, synth_vowel  # noqa: E402

SR = mic_noise.SAMPLE_RATE
LEAD = int(1.5 * SR)
VOICE_SECONDS = 2.0


def line_power_db(x, freq, half_width=20):
    """Power (dB) in freq +- half_width Hz."""
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    return 10 * np.log10(spectrum[np.abs(freqs - freq) < half_width].sum() + 1e-30)


def take(f0=200, seed=0, noise=mic_noise.mic_noise, level=-42):
    """(clean, noisy, voiced mask): lead-in, a female /a/ at `level`, a tail; `noise` is added to make noisy."""
    voice = mic_noise.at_level(synth_vowel(f0, scaled(MALE_FORMANTS, 1.17), seconds=VOICE_SECONDS, seed=seed), level)
    clean = np.concatenate([np.zeros(LEAD), voice, np.zeros(SR // 2)])
    voiced = np.zeros(len(clean), bool)
    voiced[LEAD:LEAD + len(voice)] = True
    return clean, clean + noise(len(clean), seed), voiced


def formants(audio, ceiling=6000):
    sound = parselmouth.Sound(audio, sampling_frequency=SR)
    formant = analysis.to_formants(sound, ceiling)
    times = np.arange(1.6, 3.4, 0.01)
    return np.array([np.nanmedian([formant.get_value_at_time(k, t) for t in times]) for k in (1, 2, 3, 4)])


def test_find_lines_finds_the_strong_mic_tones():
    for seed in range(3):
        found = [f for f, _ in denoise.find_lines(mic_noise.mic_noise(LEAD, seed), SR)]
        for freq in (4000, 6000, 8000, 10000):
            assert any(abs(f - freq) < 3 for f in found), (seed, freq, found)
        # nothing is reported where the model has no tone
        assert all(min(abs(f - t) for t, _ in mic_noise.TONES) < 3 for f in found), found


def test_find_lines_levels_match_the_model():
    found = dict((round(f / 1000), level) for f, level in denoise.find_lines(mic_noise.mic_noise(LEAD, 1), SR))
    assert abs(found[4] - (-73.5)) < 1.5 and abs(found[6] - (-79)) < 1.5


def test_find_lines_has_no_false_lines_on_noise_or_voice():
    false = sum(len(denoise.find_lines(mic_noise.broadband(LEAD, seed), SR)) for seed in range(30))
    false += sum(len(denoise.find_lines(np.random.default_rng(seed).standard_normal(2 * SR), SR)) for seed in range(10))
    assert false == 0
    for f0 in (120, 200, 330):
        voice = mic_noise.at_level(synth_vowel(f0, MALE_FORMANTS, seconds=1.5), -42)
        assert denoise.find_lines(voice, SR) == []


def test_find_lines_needs_enough_audio():
    assert denoise.find_lines(mic_noise.mic_noise(SR // 4), SR) == []


def test_noise_audio_from_take_skips_the_voice():
    clean, noisy, voiced = take()
    picked = denoise.noise_audio_from_take(noisy, SR, voiced)
    assert 1.5 < len(picked) / SR < 2.1            # the lead-in and tail, not the 2 s of voice
    assert np.sqrt(np.mean(picked ** 2)) < 3 * np.sqrt(np.mean(mic_noise.mic_noise(SR) ** 2))
    quietest = denoise.noise_audio_from_take(noisy, SR, None, seconds=1.0)
    assert 0.9 < len(quietest) / SR <= 1.0
    assert len(denoise.noise_audio_from_take(noisy, SR, np.ones(len(noisy), bool))) == 0


def test_cancel_lines_cuts_the_4k_line_and_keeps_the_formants():
    for seed in range(3):
        clean, noisy, voiced = take(seed=seed)
        lines = denoise.find_lines(denoise.noise_audio_from_take(noisy, SR, voiced), SR)
        cancelled = denoise.cancel_lines(noisy, SR, lines, voiced)
        tail = slice(LEAD + int(VOICE_SECONDS * SR) + SR // 10, None)
        assert line_power_db(noisy[tail], 4000, 5) - line_power_db(cancelled[tail], 4000, 5) >= 15
        # What is left is the broadband floor: formants must match a take with only that floor.
        reference = formants(clean + mic_noise.broadband(len(clean), seed))
        before, after = formants(noisy), formants(cancelled)
        assert np.all(np.abs(after - reference) < 20), (after, reference)
        assert np.sum(np.abs(after - reference)) <= np.sum(np.abs(before - reference)) + 20
        # F1-F3 stay within 20 Hz of the clean vowel
        assert np.all(np.abs(after[:3] - formants(clean)[:3]) < 20)


def test_a_voice_harmonic_on_the_line_survives_when_voiced_is_given():
    clean, noisy, voiced = take(f0=200)       # the 20th harmonic is exactly 4000 Hz
    lines = [(4000.0, -73.5)]
    held = denoise.cancel_lines(noisy, SR, lines, voiced)
    tracked = denoise.cancel_lines(noisy, SR, lines)
    voice = slice(LEAD, LEAD + int(VOICE_SECONDS * SR))
    want = line_power_db(clean[voice], 4000)
    assert abs(line_power_db(held[voice], 4000) - want) < 3
    assert line_power_db(tracked[voice], 4000) < want - 10


def test_cancel_lines_leaves_a_clean_vowel_alone():
    clean = take()[0]
    lines = [(4000.0, -73.5), (6000.0, -79), (8000.0, -84), (10000.0, -81)]
    cancelled = denoise.cancel_lines(clean, SR, lines)

    def metrics(audio):
        frame = audio[LEAD + SR // 2:LEAD + SR // 2 + 2048] * np.hanning(2048)
        return np.array([analysis.cpp(frame, SR, 200), analysis.ring_db(frame, SR, 200),
                         analysis.h1_h2_corrected(frame, SR, 200, [600, 1700, 2900], [80, 100, 120])])
    assert np.all(np.abs(metrics(cancelled) - metrics(clean)) < 0.5)


def test_cancel_lines_only_touches_the_lines():
    x = mic_noise.mic_noise(3 * SR, 2)
    out = denoise.cancel_lines(x, SR, [(4000.0, -73.5)])
    for freq in (3000, 5000, 6000, 8000, 10000):
        assert abs(line_power_db(out, freq) - line_power_db(x, freq)) < 0.1
    assert abs(line_power_db(out, 1000, 500) - line_power_db(x, 1000, 500)) < 0.1


def test_a_drifting_line_is_still_cancelled():
    n = 3 * SR
    t = np.arange(n) / SR
    floor = mic_noise.broadband(n, 3)
    amp = 10 ** (-73.5 / 20)
    voiced = np.zeros(n, bool)
    voiced[SR:2 * SR] = True
    for ramp_ppm, offset_ppm in ((0, 10), (10, 0), (10, 10)):
        freq = 4000 * (1 + offset_ppm * 1e-6 + ramp_ppm * 1e-6 * t / t[-1])
        x = floor + amp * np.sin(2 * np.pi * np.cumsum(freq) / SR + 1.0)
        for mask in (None, voiced):
            out = denoise.cancel_lines(x, SR, [(4000.0, -73.5)], mask)
            assert line_power_db(x, 4000, 4) - line_power_db(out, 4000, 4) >= 15, (ramp_ppm, offset_ppm)


def test_noise_report_matches_the_model():
    clean, noisy, voiced = take(seed=4)
    report = denoise.noise_report(noisy, SR, voiced)
    assert abs(report["noise_db"] - (-58)) < 1.5
    assert abs(report["rumble_share"] - 0.47) < 0.06   # the share of BROADBAND_PSD below 150 Hz (spec guessed over 0.5)
    assert {4000, 6000, 8000, 10000} <= {round(f / 1000) * 1000 for f, _ in report["lines"]}
    assert abs(report["voiced_db"] - (-42)) < 1.5
    assert abs(report["snr_db"] - 16) < 2 and report["snr_voice_db"] > 0
    assert abs(report["peak"] - np.max(np.abs(noisy))) < 1e-12
    assert report["clip_fraction"] == 0 and abs(report["dc"]) < 1e-3
    # without a mask the voice is found by level
    blind = denoise.noise_report(noisy, SR)
    assert abs(blind["noise_db"] - report["noise_db"]) < 1.5 and abs(blind["snr_db"] - report["snr_db"]) < 3


def test_noise_report_sees_clipping_dc_and_missing_stretches():
    loud = np.clip((take(level=-6)[1] + 0.05) * 4, -1, 1)
    report = denoise.noise_report(loud, SR)
    assert report["clip_fraction"] > 0 and report["dc"] > 0.01
    assert np.isnan(denoise.noise_report(np.ones(SR), SR, np.ones(SR, bool))["noise_db"])
    assert denoise.noise_report(np.zeros(0), SR)["lines"] == []
