"""Phase 10 measurement truth: readings shown only when right, and graded when unsure.

Run with: python -m pytest tests/test_truth.py
"""
import os
import sys

import numpy as np
from scipy.signal import lfilter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import accuracy_check  # noqa: E402
import analysis  # noqa: E402
import mic_noise  # noqa: E402
import register  # noqa: E402
import take_register  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, scaled, synth_vowel  # noqa: E402

FEMALE_FORMANTS = scaled(MALE_FORMANTS, 1.15)


def on_mic(voice, level=-42, seed=1, gain_db=0.0):
    """The voice at a realistic level over the user's mic noise (rumble + kHz tones)."""
    return mic_noise.at_level(voice, level) + mic_noise.mic_noise(len(voice), seed, gain_db=gain_db)


def falsetto_like(f0, seconds=2.0, slope_db=24, breath=0.15, seed=0):
    """A breathy, nearly sinusoidal source (harmonics falling slope_db per octave, plus aspiration
    noise) through female formants: the steep, H1-dominated spectrum of M2."""
    t = np.arange(int(seconds * SR)) / SR
    source = sum(10 ** (-slope_db * np.log2(k) / 20) * np.sin(2 * np.pi * k * f0 * t)
                 for k in range(1, int(5000 // f0)))
    source = source + breath * np.std(source) * np.random.default_rng(seed).standard_normal(len(t))
    out = source
    for freq, bw in FEMALE_FORMANTS:
        r, theta = np.exp(-np.pi * bw / SR), 2 * np.pi * freq / SR
        a = [1, -2 * r * np.cos(theta), r ** 2]
        out = lfilter([sum(a)], a, out)
    return 0.3 * out / np.max(np.abs(out))


def phrases(f0=150, gain_db=0.0, seed=0):
    """Four 1 s vowels with pauses, over the mic noise raised by gain_db."""
    parts = []
    for i in range(4):
        parts += [np.zeros(int(0.5 * SR)),
                  mic_noise.at_level(synth_vowel(f0 * (1 + 0.05 * i), MALE_FORMANTS, seconds=1.0), -42)]
    voice = np.concatenate(parts + [np.zeros(int(0.5 * SR))])
    return voice + mic_noise.mic_noise(len(voice), seed, gain_db=gain_db)


def take(audio, mode="speak"):
    return take_register.finish_take(analysis.analyze_take(audio, mode=mode))


# --- REGISTER ---

def test_modal_speech_with_mic_noise_is_not_head():
    for f0 in (120, 150, 180, 220):
        for oq in (0.5, 0.6, 0.8):              # pressed .. light modal
            formants = MALE_FORMANTS if f0 < 170 else FEMALE_FORMANTS
            result = take(on_mic(synth_vowel(f0, formants, seconds=2.5, open_quotient=oq)))
            reg = result["register"]
            assert reg["head_share"] < 0.3 and reg["median_score"] < register.MIX_BAND[1], (f0, oq, reg)


def test_modal_source_at_150_hz_with_rumble_is_not_head():
    voice = synth_vowel(150, MALE_FORMANTS, seconds=3.0, open_quotient=0.6)
    rumble = mic_noise.broadband(len(voice), seed=3, gain_db=6)      # the desk rumble, 6 dB up
    reg = take(mic_noise.at_level(voice, -42) + rumble)["register"]
    assert reg["head_share"] < 0.1 and reg["chest_share"] > 0.6


def test_breathy_falsetto_like_voice_is_head():
    for f0 in (300, 400):
        reg = take(on_mic(falsetto_like(f0)), mode="sing")["register"]
        assert reg["head_share"] > 0.7 and reg["median_score"] > register.MIX_BAND[1], (f0, reg)


def test_speech_register_is_withheld_unless_clear_cut():
    # Uncalibrated speech gets at most a coarse label, at "low"; otherwise none, with the reason.
    clear = take(on_mic(synth_vowel(150, MALE_FORMANTS, seconds=3.0)))
    assert clear["register"]["speech_label"] == "mostly chest"
    assert clear["confidence"]["register"] == "low"
    short = take(on_mic(synth_vowel(150, MALE_FORMANTS, seconds=1.5)))
    assert short["register"]["speech_label"] is None
    assert short["confidence"]["register"] == "none"
    assert short["confidence"]["reasons"]["register"] == take_register.SPEECH_UNCALIBRATED
    # Falsetto-like speech above 220 Hz must not be called "mostly chest".
    high = take(on_mic(falsetto_like(300, seconds=3.0)))
    assert high["register"]["speech_label"] != "mostly chest"
    assert high["confidence"]["register"] == "none"
    # A calibrated model, or sing mode, isn't touched by the speech defaults.
    features = {"h1h2": 8.0, "h1a3": 28.0, "cpp": 15.0, "f0": 160.0, "cq": None}
    model = register.RegisterModel()
    assert model.score(features, mode="speak") < 0.35 < 0.9 < model.score(features)
    model.calibrated = True
    assert model.score(features, mode="speak") == model.score(features)


# --- SINGING GATE ---

def test_speak_mode_has_no_singing_stats():
    held = synth_vowel(220, MALE_FORMANTS, seconds=2.0)
    assert analysis.analyze_take(held, mode="speak")["singing"] is None
    assert analysis.analyze_take(held, mode="sing")["singing"] is not None


def test_tuning_needs_two_held_notes_or_one_long_one():
    times = np.arange(0, 4, 0.01)

    def notes(*spans):
        f0 = np.full(len(times), np.nan)
        for start, stop, hz in spans:
            f0[int(start * 100):int(stop * 100)] = hz
        return analysis.singing_stats(times, f0)

    one = notes((0.5, 1.3, 220.0))                       # one 0.8 s note: sung, but too little to judge
    assert one is not None and one["held_notes"] == 1 and not one["tuning_judged"]
    assert one["tonic_offset"] is None and one["drift"] is None and one["interval_error"] is None
    two = notes((0.5, 1.3, 220.0), (1.5, 2.3, 246.9))
    assert two["tuning_judged"] and two["tonic_offset"] is not None and two["interval_error"] is not None
    long = notes((0.5, 2.2, 220.0))                      # one 1.7 s note
    assert long["tuning_judged"] and long["tonic_offset"] is not None and long["drift"] is not None


# --- OCTAVE ERRORS ---

def test_octave_errors_under_2_percent_on_sweeps_with_mic_noise():
    for lo, hi, formants in ((90, 200, MALE_FORMANTS), (140, 300, FEMALE_FORMANTS), (110, 260, MALE_FORMANTS)):
        n = int(3.0 * SR)
        contour = lo * (hi / lo) ** (0.5 - 0.5 * np.cos(np.linspace(0, 2 * np.pi, n)))   # up and back down
        result = analysis.analyze_take(on_mic(synth_vowel(contour, formants)))
        times, f0 = result["tracks"]["times"], result["tracks"]["f0"]
        truth = np.interp(times, np.arange(n) / SR, contour)
        voiced = ~np.isnan(f0)
        assert voiced.mean() > 0.8, (lo, hi)
        off = np.abs(12 * np.log2(f0[voiced] / truth[voiced]))
        assert np.mean(np.abs(off - 12) < 1.5) < 0.02 and np.mean(off > 1.5) < 0.02, (lo, hi)


# --- CONFIDENCE ---

def test_formant_readings_have_no_confidence_at_300_hz():
    result = analysis.analyze_take(on_mic(synth_vowel(300, FEMALE_FORMANTS, seconds=2.0)))
    assert abs(result["pitch"] - 300) < 5
    assert result["confidence"]["delta_f"] == "none"
    assert "250 Hz" in result["confidence"]["reasons"]["delta_f"]
    assert result["confidence"]["pitch"] in ("high", "medium")


def test_low_confidence_at_10_db_snr():
    result = analysis.analyze_take(phrases(gain_db=9))
    assert 7 < result["snr"] < 13
    for metric in ("h1h2", "cpp", "ring"):
        assert result["confidence"][metric] == "low", metric
        assert "noise" in result["confidence"]["reasons"][metric]
    assert result["confidence"]["pitch"] in ("low", "medium")


def test_confidence_grades_clean_takes_and_short_ones():
    clean = analysis.analyze_take(phrases(gain_db=-30))
    assert clean["confidence"]["pitch"] == "high"
    assert clean["confidence"]["h1h2"] in ("medium", "high")
    levels = {k: v for k, v in clean["confidence"].items() if k != "reasons"}
    assert set(levels) == set(clean["confidence"]["reasons"])
    assert all(level in analysis.CONFIDENCE_LEVELS for level in levels.values())
    short = analysis.analyze_take(on_mic(synth_vowel(150, MALE_FORMANTS, seconds=0.6)))
    assert short["confidence"]["pitch_range"] == "none"
    assert short["confidence"]["singing"] == "none"


# --- DISPLAY FORMANT TRACKS ---

def test_smoothed_formant_tracks_are_steady_and_nan_where_untrusted():
    voice = np.concatenate([synth_vowel(150, MALE_FORMANTS, seconds=1.5, noise=0.3), np.zeros(SR // 2),
                            synth_vowel(150, MALE_FORMANTS, seconds=1.0, noise=0.3)])
    result = analysis.analyze_take(on_mic(voice))
    tracks = result["tracks"]
    for key in analysis.SMOOTH_TRACK_KEYS:
        assert len(tracks[key]) == len(tracks["f0"])
        assert np.isnan(tracks[key][np.isnan(tracks["f0"])]).all(), key
    raw, smooth = tracks["f2"], tracks["f2_smooth"]
    both = ~np.isnan(raw) & ~np.isnan(smooth)
    assert both.sum() > 100
    jitter = lambda x: np.nanmedian(np.abs(np.diff(x)))       # noqa: E731
    assert jitter(np.where(both, smooth, np.nan)) <= jitter(np.where(both, raw, np.nan))
    assert abs(np.nanmedian(tracks["f1_smooth"]) - 500) < 80
    # A snapped F1 (on a harmonic) is not drawn.
    freqs = np.array([[160.0, 1500, 2500, 3500]] * 10)
    bws = np.full((10, 4), 100.0)
    assert not analysis.trusted_formant_frames(np.full(10, 150.0), freqs, bws).any()


# --- ACCURACY CHECK TOOL ---

def test_accuracy_check_reads_the_folder_only(tmp_path):
    import soundfile as sf
    folder, out = tmp_path / "takes", tmp_path / "report"
    folder.mkdir()
    sf.write(str(folder / "a.wav"), on_mic(synth_vowel(180, FEMALE_FORMANTS, seconds=2.0)), SR)
    (folder / "a.json").write_text('{"mode": "speak"}', encoding="utf-8")
    before = {p.name: p.stat().st_mtime_ns for p in folder.iterdir()}
    report = accuracy_check.run(folder, out, [("a", 175.0, 178.0)], "Voice Tools", progress=lambda _: None)
    assert {p.name: p.stat().st_mtime_ns for p in folder.iterdir()} == before
    text = report.read_text(encoding="utf-8")
    assert "| a.wav | speak |" in text and "VoiceLab vs Voice Tools" in text
    try:
        accuracy_check.run(folder, folder / "inside", progress=lambda _: None)
        assert False, "wrote inside the takes folder"
    except ValueError:
        pass


def test_accuracy_check_comparisons():
    f0 = np.array([np.nan, 200.0, 400.0, 205.0, np.nan, 100.0])
    ref = np.array([np.nan, 200.0, 200.0, 200.0, 150.0, 210.0])
    octave, other, n = accuracy_check.pitch_errors(f0, ref)
    assert n == 4 and abs(octave - 0.5) < 1e-9 and other == 0.0
    assert accuracy_check.voicing_agreement(f0, ref) == 5 / 6
    row = {"mean": {"voicelab": 220.0}, "median": {"voicelab": 200.0}}
    comp = accuracy_check.compare_app(row, 200.0, 200.0)
    assert abs(comp["average"]["hz"] - 20) < 1e-9 and abs(comp["average"]["st"] - 12 * np.log2(1.1)) < 1e-9
    assert comp["median"]["hz"] == 0
