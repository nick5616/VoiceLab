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


class FakeStream:
    """Records the InputStream arguments; `fail_rates` makes opening at those rates raise."""
    calls = []
    fail_rates = ()

    def __init__(self, **kwargs):
        if kwargs["samplerate"] in self.fail_rates:
            raise engine_module.sd.PortAudioError("Invalid sample rate")
        self.kwargs = kwargs
        self.calls.append(kwargs)

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass

    latency = 0.003


def fake_devices(monkeypatch, wasapi_rate=48000, wasapi_channels=1, fail_rates=()):
    """MME mic (truncated name, any rate) plus a WASAPI twin that only does `wasapi_rate`."""
    devices = [
        {"name": "Microphone (USB PnP Audio Devic", "hostapi": 0, "max_input_channels": 2,
         "default_samplerate": 44100.0},
        {"name": "Microphone (USB PnP Audio Device)", "hostapi": 1, "max_input_channels": wasapi_channels,
         "default_samplerate": float(wasapi_rate)}]
    apis = [{"name": "MME"}, {"name": "Windows WASAPI"}]
    FakeStream.calls = []
    FakeStream.fail_rates = fail_rates

    def query_devices(index=None, kind=None):
        return devices if index is None else devices[index]

    def query_hostapis(index=None):
        return apis if index is None else apis[index]

    monkeypatch.setattr(engine_module.sd, "InputStream", FakeStream)
    monkeypatch.setattr(engine_module.sd, "query_devices", query_devices)
    monkeypatch.setattr(engine_module.sd, "query_hostapis", query_hostapis)
    monkeypatch.setattr(engine_module.sd.default, "device", [0, 0])
    return FakeStream.calls


def feed_blocks(eng, audio, size, channels=1):
    for i in range(0, len(audio), size):
        block = audio[i:i + size]
        eng._on_audio(block.reshape(-1, 1) if channels == 1 else block, len(block), None, None)


def sine_residual_db(x, freq, skip=2000):
    """Level (dB re the sine) of what remains after removing the best-fit sine of `freq`."""
    x = x[skip:-skip]
    t = np.arange(len(x)) / SR
    basis = np.column_stack([np.sin(2 * np.pi * freq * t), np.cos(2 * np.pi * freq * t)])
    fit = basis @ np.linalg.lstsq(basis, x, rcond=None)[0]
    return 10 * np.log10(np.mean((x - fit) ** 2) / np.mean(fit ** 2))


def test_prefers_wasapi_at_the_native_rate(monkeypatch):
    calls = fake_devices(monkeypatch)
    eng = LiveEngine()
    eng.open_stream(device=0)               # the MME index, as the device list gives it
    assert len(calls) == 1 and calls[0]["samplerate"] == 48000 and calls[0]["device"] == 1
    assert eng.capture_info["path"] == "wasapi" and eng.capture_info["native_rate"] == 48000
    assert eng.capture_info["host_api"] == "Windows WASAPI" and eng.capture_info["latency"] == 0.003
    eng.open_stream()                       # the system default input finds the same device
    assert calls[-1]["samplerate"] == 48000 and calls[-1]["device"] == 1


def test_wasapi_stream_arrives_at_the_app_rate_without_clicks(monkeypatch):
    fake_devices(monkeypatch)
    eng = LiveEngine()
    eng.open_stream(device=0)
    seconds = 2
    t = np.arange(seconds * 48000) / 48000
    sine = 0.5 * np.sin(2 * np.pi * 1000 * t)
    eng.start_take()
    feed_blocks(eng, sine, 557)             # the callback size for 512 frames at 44.1 kHz
    got = eng.stop_take()
    # a few input samples of latency are still inside the resampler
    assert abs(len(got) - seconds * SR) <= 20
    assert len(got) / SR == pytest.approx(seconds, abs=0.001)
    assert sine_residual_db(got, 1000) < -60     # no seams at the block boundaries
    assert np.max(np.abs(got)) == pytest.approx(0.5, abs=0.01)


def test_resampling_does_not_depend_on_the_block_size():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(48000)
    outputs = []
    for size in (48000, 557, 100, 7):
        resampler = engine_module.StreamResampler(48000, 44100)
        outputs.append(np.concatenate([resampler.process(x[i:i + size]) for i in range(0, len(x), size)]))
    n = min(len(o) for o in outputs)
    assert n > 44000
    for other in outputs[1:]:
        assert np.allclose(other[:n], outputs[0][:n], atol=1e-9)
    # and it is the polyphase resampler's own answer
    from scipy.signal import resample_poly
    reference = resample_poly(x, 147, 160)
    assert np.allclose(outputs[0][50:n - 50], reference[50:n - 50], atol=1e-9)


def test_resampler_delay_is_under_a_millisecond():
    resampler = engine_module.StreamResampler(48000, 44100)
    x = np.zeros(2000)
    x[1000] = 1.0
    out = np.concatenate([resampler.process(x[i:i + 557]) for i in range(0, len(x), 557)])
    emitted_by_end = len(out)               # outputs available after all of the input arrived
    held_back = len(x) * 147 // 160 - emitted_by_end
    assert 0 <= held_back / SR < 0.001


def test_wasapi_two_channel_keeps_the_egg_aligned(monkeypatch):
    calls = fake_devices(monkeypatch, wasapi_channels=2)
    eng = LiveEngine()
    eng.open_stream(device=0, egg_channel=1)
    assert calls[0]["channels"] == 2 and calls[0]["samplerate"] == 48000
    t = np.arange(48000) / 48000
    both = np.column_stack([0.3 * np.sin(2 * np.pi * 500 * t), 0.6 * np.sin(2 * np.pi * 120 * t)])
    eng.start_take()
    feed_blocks(eng, both, 557, channels=2)
    mic, egg_samples = eng.stop_take(), eng.last_take_egg
    assert len(mic) == len(egg_samples) and abs(len(mic) - SR) <= 20
    assert sine_residual_db(mic, 500) < -60 and sine_residual_db(egg_samples, 120) < -60


def test_falls_back_to_the_default_path_when_wasapi_fails(monkeypatch):
    calls = fake_devices(monkeypatch, fail_rates=(48000,))
    eng = LiveEngine()
    eng.open_stream(device=0)
    assert [c["samplerate"] for c in calls] == [SR] and calls[0]["device"] == 0
    assert eng.capture_info["path"] == "default" and "WASAPI failed" in eng.capture_info["reason"]
    assert eng.capture_info["host_api"] == "MME" and eng.capture_info["native_rate"] == SR
    # samples then pass through untouched, as before
    eng.start_take()
    feed_blocks(eng, np.linspace(0, 0.1, 600), 512)
    assert np.allclose(eng.stop_take(), np.linspace(0, 0.1, 600))


def test_no_wasapi_device_uses_the_default_path(monkeypatch):
    calls = fake_devices(monkeypatch)
    monkeypatch.setattr(engine_module.sd, "query_hostapis", lambda index=None: [{"name": "MME"}] if index is None else {"name": "MME"})
    eng = LiveEngine()
    eng.open_stream(device=0)
    assert calls[0]["samplerate"] == SR and eng.capture_info["path"] == "default"


def test_open_stream_requests_two_channels(monkeypatch):
    calls = []
    monkeypatch.setattr(engine_module, "_find_wasapi", lambda device: None)

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
    fake_devices(monkeypatch, wasapi_channels=2)

    def fail(**kwargs):
        raise engine_module.sd.PortAudioError("no such channels")

    monkeypatch.setattr(engine_module.sd, "InputStream", fail)
    with pytest.raises(engine_module.sd.PortAudioError):
        LiveEngine().open_stream(egg_channel=0)
