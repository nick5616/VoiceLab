"""EGG analysis against synthetic Lx signals with known contact quotient."""
import os
import sys
import time

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import egg  # noqa: E402

SR = egg.SAMPLE_RATE


@pytest.mark.parametrize("f0", [100, 220, 440])
@pytest.mark.parametrize("cq", [0.3, 0.45, 0.6])
def test_cq_recovered(f0, cq):
    signal = egg.synth_egg(f0, 1.0, cq)
    rows = egg.cycles(signal, SR)
    assert abs(np.median(rows[:, 2]) - cq) < 0.03
    result = egg.analyze_egg_take(signal, SR, np.arange(0, 1, 0.01))
    assert result["signal_ok"] and abs(result["cq"] - cq) < 0.03
    assert np.nanmax(np.abs(result["cq_track"] - cq)) < 0.05


def test_inverted_polarity_corrected():
    normal = egg.analyze_egg_take(egg.synth_egg(180, 1.0, 0.45), SR, [0.5])
    flipped = egg.analyze_egg_take(egg.synth_egg(180, 1.0, 0.45, polarity=-1), SR, [0.5])
    assert normal["polarity"] == 1 and flipped["polarity"] == -1
    assert abs(flipped["cq"] - normal["cq"]) < 0.01


def test_drift_does_not_bias_cq():
    signal = egg.synth_egg(200, 2.0, 0.4, drift=3.0)
    assert abs(egg.analyze_egg_take(signal, SR, [1.0])["cq"] - 0.4) < 0.03


def test_egg_f0_accuracy():
    result = egg.analyze_egg_take(egg.synth_egg(123.0, 1.0, 0.5), SR, np.arange(0.1, 0.9, 0.05))
    assert np.nanmax(np.abs(result["egg_f0_track"] - 123.0)) < 1.0
    n = 2 * SR
    f0 = np.linspace(150, 300, n)
    times = np.arange(0.2, 1.8, 0.05)
    result = egg.analyze_egg_take(egg.synth_egg(f0, 0, 0.5), SR, times)
    assert np.isfinite(result["egg_f0_track"]).all()
    assert np.max(np.abs(result["egg_f0_track"] - np.interp(times, np.arange(n) / SR, f0))) < 1.0


@pytest.mark.parametrize("kind", ["flat", "noise", "silence"])
def test_unconnected_channel_flagged(kind):
    n = SR
    rng = np.random.default_rng(1)
    signal = {"flat": np.full(n, 0.2), "silence": np.zeros(n), "noise": 0.1 * rng.standard_normal(n)}[kind]
    result = egg.analyze_egg_take(signal, SR, np.arange(0, 1, 0.1))
    assert result["signal_ok"] is False and result["note"]
    assert np.isnan(result["cq_track"]).all()


def test_frame_cq_is_fast_and_right():
    window = egg.synth_egg(200, 0.12, 0.5)
    assert abs(egg.frame_cq(window, SR) - 0.5) < 0.03
    assert abs(egg.frame_egg_f0(window, SR) - 200) < 1.0
    egg.frame_cq(window, SR)
    started = time.perf_counter()
    for _ in range(20):
        egg.frame_cq(window, SR)
    assert (time.perf_counter() - started) / 20 < 1e-3


def test_frame_nan_on_noise():
    assert np.isnan(egg.frame_cq(np.zeros(5000), SR))


def test_load_stereo_take(tmp_path):
    mic = 0.3 * np.sin(2 * np.pi * 200 * np.arange(SR // 2) / SR)
    signal = egg.synth_egg(200, 0.5, 0.45)
    path = str(tmp_path / "take.wav")
    sf.write(path, np.column_stack([signal, mic]), SR, subtype="FLOAT")
    got_mic, got_egg = egg.load_stereo_take(path, egg_channel=0)
    assert np.allclose(got_mic, mic, atol=1e-6) and np.allclose(got_egg, signal, atol=1e-6)
    mono = str(tmp_path / "mono.wav")
    sf.write(mono, mic, SR)
    with pytest.raises(ValueError, match="stereo"):
        egg.load_stereo_take(mono, 1)
