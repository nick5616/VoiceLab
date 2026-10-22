"""Checks the register estimate against synthetic vowels with known open quotient.

Run with: python -m pytest tests
"""
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import analysis  # noqa: E402
import mic_noise  # noqa: E402
import register  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402

LIGHT, HEAVY = 0.8, 0.35


def take(audio):
    result = analysis.analyze_take(audio, mode="sing")   # register is a singing measure
    return result["tracks"], register.register_track(audio, SR, result["tracks"])


def median_score(f0, oq):
    return float(np.nanmedian(take(synth_vowel(f0, MALE_FORMANTS, open_quotient=oq))[1]))


def labelled_frames(f0, oq, name, model=None):
    audio = synth_vowel(f0, MALE_FORMANTS, seconds=3.0, open_quotient=oq)
    tracks = analysis.analyze_take(audio)["tracks"]
    out = []
    for i in np.flatnonzero(~np.isnan(tracks["f0"])):
        center = int(tracks["times"][i] * SR)
        frame = audio[center - 1024:center + 1024]
        if len(frame) < 2048:
            continue
        formants = [tracks[k][i] for k in ("f1", "f2", "f3")]
        out.append((register.frame_features(frame, SR, tracks["f0"][i], formants,
                                            register.NOMINAL_BANDWIDTHS), name))
    return out


def test_light_source_scores_higher_at_same_pitch():
    for f0 in (150, 300, 450):
        assert median_score(f0, LIGHT) > median_score(f0, HEAVY) + 0.2, f0


def test_high_chest_is_not_called_head():
    score = median_score(450, HEAVY)
    assert RegisterModelLabel(score) in ("chest", "mix")


def RegisterModelLabel(score):
    return register.RegisterModel().label(score)


def test_single_break_when_source_switches():
    audio = np.concatenate([synth_vowel(150, MALE_FORMANTS, seconds=1.0, open_quotient=HEAVY),
                            synth_vowel(150, MALE_FORMANTS, seconds=1.0, open_quotient=LIGHT)])
    tracks, scores = take(audio)
    breaks = register.find_breaks(tracks["times"], scores)
    assert len(breaks) == 1 and abs(breaks[0] - 1.0) < 0.2


def fry_f0(seed, base=50, jitter=0.2, seconds=1.5):
    rng = np.random.default_rng(seed)
    contour = []
    while len(contour) < seconds * SR:
        period = 1 / base * (1 + rng.uniform(-jitter, jitter))
        contour += [1 / period] * int(period * SR)
    return np.array(contour[:int(seconds * SR)])


def fry_flags(audio):
    import parselmouth
    times, f0 = analysis.pitch_track(parselmouth.Sound(audio, sampling_frequency=SR))
    return register.is_fry(f0, 100, audio=audio, sample_rate=SR, times=times)


def test_fry_is_flagged_and_steady_vowel_is_not():
    for seed in range(3):
        assert fry_flags(synth_vowel(fry_f0(seed), MALE_FORMANTS)).mean() > 0.6
    assert fry_flags(synth_vowel(110, MALE_FORMANTS)).mean() < 0.05
    assert not register.is_fry(np.full(100, 110.0), 100).any()
    assert register.is_fry(np.full(100, 55.0), 100).all()


def test_fit_moves_boundary_toward_labels_and_needs_enough_data():
    default = register.RegisterModel()
    # This voice's chest is lighter than the default expects: label OQ 0.5 as chest.
    data = labelled_frames(150, 0.5, "chest") + labelled_frames(150, LIGHT, "head")
    probe = data[0][0]
    fitted = register.RegisterModel().fit(data)
    assert fitted.is_calibrated()
    assert fitted.score(probe) < default.score(probe)
    assert not register.RegisterModel().fit(data[:50] + data[-50:]).is_calibrated()
    only_chest = labelled_frames(150, 0.5, "chest")
    assert not register.RegisterModel().fit(only_chest).is_calibrated()


def test_model_round_trips_through_json():
    model = register.RegisterModel().fit(labelled_frames(150, 0.5, "chest") + labelled_frames(150, LIGHT, "head"))
    restored = register.RegisterModel.from_dict(json.loads(json.dumps(model.to_dict())))
    features = labelled_frames(150, 0.6, "chest")[0][0]
    assert restored.is_calibrated() and abs(restored.score(features) - model.score(features)) < 1e-9


def test_cq_dominates_when_present():
    model = register.RegisterModel()
    heavy_acoustics = labelled_frames(150, HEAVY, "chest")[0][0]
    assert model.score(dict(heavy_acoustics, cq=0.2)) > 0.8
    light_acoustics = labelled_frames(150, LIGHT, "head")[0][0]
    assert model.score(dict(light_acoustics, cq=0.55)) < 0.2


def test_shares_sum_to_one():
    audio = np.concatenate([synth_vowel(150, MALE_FORMANTS, seconds=1.0, open_quotient=HEAVY),
                            synth_vowel(150, MALE_FORMANTS, seconds=1.0, open_quotient=0.5),
                            synth_vowel(150, MALE_FORMANTS, seconds=1.0, open_quotient=LIGHT)])
    tracks, scores = take(audio)
    fry = np.zeros(len(scores), dtype=bool)
    fry[:20] = True
    summary = register.take_register_summary(tracks, scores, fry)
    total = sum(summary[k] for k in ("chest_share", "mix_share", "head_share", "fry_share"))
    assert abs(total - 1) < 1e-6 and summary["fry_share"] > 0 and summary["chest_share"] > 0
    assert summary["calibrated"] is False


def test_old_takes_without_h1h2_and_cpp_still_work():
    audio = synth_vowel(150, MALE_FORMANTS, open_quotient=LIGHT)
    tracks = analysis.analyze_take(audio)["tracks"]
    old = {k: v for k, v in tracks.items() if k not in ("h1h2", "cpp")}
    new_scores = register.register_track(audio, SR, tracks)
    old_scores = register.register_track(audio, SR, old)
    assert np.array_equal(np.isnan(new_scores), np.isnan(old_scores))
    assert abs(np.nanmedian(new_scores) - np.nanmedian(old_scores)) < 0.1


def test_frame_features_and_score_are_fast_enough_for_live_use():
    audio = synth_vowel(200, MALE_FORMANTS, open_quotient=0.6)
    frame = audio[20000:20000 + 2048]
    model = register.RegisterModel()
    formants = [500, 1500, 2500]
    register.frame_features(frame, SR, 200.0, formants, register.NOMINAL_BANDWIDTHS)
    timings = []
    for _ in range(50):
        start = time.perf_counter()
        model.score(register.frame_features(frame, SR, 200.0, formants, register.NOMINAL_BANDWIDTHS))
        timings.append(time.perf_counter() - start)
    assert min(timings) < 0.005   # best run, so a busy machine doesn't flake it; live tick is 25 ms


# --- phase 9: high notes, score-swing breaks, short arrays ---

def soprano(f0):
    """Formants with F1 tuned up toward f0, as sopranos do (the singing audit's vowel)."""
    f1 = max(750, 1.05 * f0) if f0 > 450 else 750
    return [(f1, 90), (1150 if f0 < 800 else 1500, 110), (2700, 140), (3600, 180), (4600, 250)]


def on_mic(voice, level=-42, seed=0):
    return mic_noise.at_level(voice, level) + mic_noise.mic_noise(len(voice), seed)


def test_light_phonation_at_high_pitch_is_not_called_chest():
    for f0 in (784, 1000):
        voice = synth_vowel(f0, soprano(f0), seconds=1.2, open_quotient=0.9)
        for audio in (voice, on_mic(voice)):
            tracks, scores = take(audio)
            assert np.nanmedian(scores) > 0.5, f0
            assert register.RegisterModel().label(np.nanmedian(scores), f0) == "head"


def test_head_prior_starts_above_500_and_widens_the_mix_band():
    assert register.head_prior(450.0) == 0.0 and register.head_prior(np.nan) == 0.0
    assert register.head_prior(1000.0) > register.head_prior(600.0) > 0
    assert register.mix_band(400.0) == register.MIX_BAND
    low, high = register.mix_band(800.0)
    assert low < register.MIX_BAND[0] and high > register.MIX_BAND[1]
    model = register.RegisterModel()
    assert model.label(0.3) == "chest" and model.label(0.3, 400.0) == "chest"
    assert model.label(0.22, 800.0) == "mix" and model.label(0.85, 800.0) == "head"


def test_abrupt_switch_at_330_hz_is_exactly_one_break():
    audio = np.concatenate([synth_vowel(330, soprano(330), seconds=1.0, open_quotient=HEAVY),
                            synth_vowel(330, soprano(330), seconds=1.0, open_quotient=0.9)])
    for signal in (audio, on_mic(audio)):
        tracks, scores = take(signal)
        breaks = register.find_breaks(tracks["times"], scores)
        assert len(breaks) == 1 and abs(breaks[0] - 1.0) < 0.2, breaks


def test_breaks_use_score_swings_not_absolute_levels():
    times = np.arange(200) / 100
    step = np.where(times < 1.0, 0.14, 0.65)
    assert len(register.find_breaks(times, step)) == 1
    assert len(register.find_breaks(times, np.where(times < 1.0, 0.45, 0.9))) == 1      # never crosses 0.3/0.7
    assert register.find_breaks(times, 0.14 + 0.5 * times / 2) == []                    # a slow glide is no break
    assert register.find_breaks(times, np.where(times < 1.0, 0.4, 0.55)) == []          # small step
    assert register.find_breaks(times[:1], step[:1]) == []


def test_is_fry_handles_short_arrays():
    for n in range(0, 5):
        assert register.is_fry(np.full(n, 110.0), 100).shape == (n,)
        assert register.is_fry(np.full(n, 110.0), 100).sum() == 0
        assert register.is_fry(np.full(n, np.nan), 100).shape == (n,)
    assert register.is_fry(np.array([55.0, 56.0, 57.0, 55.0]), 100).all()
