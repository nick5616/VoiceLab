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
import register  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402

LIGHT, HEAVY = 0.8, 0.35


def take(audio):
    result = analysis.analyze_take(audio)
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
    start = time.perf_counter()
    for _ in range(50):
        model.score(register.frame_features(frame, SR, 200.0, formants, register.NOMINAL_BANDWIDTHS))
    assert (time.perf_counter() - start) / 50 < 0.002
