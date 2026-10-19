"""LiveEngine buffers, EGG routing and live frames, without audio hardware."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import egg  # noqa: E402
import engine as engine_module  # noqa: E402
from engine import LiveEngine  # noqa: E402

SR = egg.SAMPLE_RATE


def test_take_returns_mic_and_egg_with_equal_lengths():
    eng = LiveEngine()
    mic = np.random.default_rng(0).standard_normal(SR // 2) * 0.1
    signal = egg.synth_egg(200, 0.5, 0.5)[:len(mic)]
    eng.start_take()
    for i in range(0, len(mic), 512):
        eng.feed(mic[i:i + 512], signal[i:i + 512])
    got = eng.stop_take()
    assert np.allclose(got, mic) and np.allclose(eng.last_take_egg, signal)
    assert len(got) == len(eng.last_take_egg)


def test_without_egg_everything_is_as_before():
    eng = LiveEngine()
    eng.start_take()
    eng.feed(np.zeros(1000))
    assert len(eng.stop_take()) == 1000
    assert eng.last_take_egg is None and eng.latest_egg_window() is None
    eng.feed(np.zeros(2000))
    frame = eng._worker.frame()
    assert np.isnan(frame["cq"]) and np.isnan(frame["egg_f0"])


def test_live_frame_carries_cq():
    eng = LiveEngine()
    n = len(eng.latest_window())
    eng.feed(np.zeros(n), egg.synth_egg(200, n / SR, 0.45)[:n])
    frame = eng._worker.frame()
    assert abs(frame["cq"] - 0.45) < 0.05 and abs(frame["egg_f0"] - 200) < 2


def test_open_stream_requests_two_channels(monkeypatch):
    calls = []

    class FakeStream:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(engine_module.sd, "InputStream", FakeStream)
    eng = LiveEngine()
    eng.open_stream(egg_channel=1)
    assert calls[-1]["channels"] == 2 and eng.egg_channel == 1 and eng.latest_egg_window() is not None
    # channel 1 is the EGG, channel 0 the mic
    data = np.column_stack([np.full(4, 0.1), np.full(4, 0.7)])
    eng._on_audio(data, 4, None, None)
    assert eng.latest_window()[-1] == pytest.approx(0.1) and eng.latest_egg_window()[-1] == pytest.approx(0.7)
    eng.open_stream()
    assert calls[-1]["channels"] == 1 and eng.latest_egg_window() is None


def test_two_channel_open_failure_raises(monkeypatch):
    def fail(**kwargs):
        raise engine_module.sd.PortAudioError("no such channels")

    monkeypatch.setattr(engine_module.sd, "InputStream", fail)
    with pytest.raises(engine_module.sd.PortAudioError):
        LiveEngine().open_stream(egg_channel=0)
