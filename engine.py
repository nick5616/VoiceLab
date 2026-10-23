"""Always-on microphone stream feeding both the live meters and take recording. No UI: subscribers get
each live frame on the analysis thread (a UI moves it to its own thread)."""
import math
import threading
import time

import numpy as np
import sounddevice as sd
from scipy import signal

import egg
from analysis import SAMPLE_RATE, FrameAnalyzer

UPDATE_INTERVAL = 0.025   # live meter refresh (s)
BLOCK_SIZE = 512          # frames per callback at the app rate; scaled for the device's native rate
WASAPI = "Windows WASAPI"  # preferred host API: native rate, 3-10 ms latency (MME: 90 ms, audit)
HISTORY_SECONDS = 6       # recent audio kept for the noise badge (a few seconds of pauses and voice)
RESAMPLER_GUARD = 16      # input samples kept either side of an output sample (filter half-width is about 11)


class StreamResampler:
    """Block-wise rational resampler (polyphase, scipy.signal.resample_poly) with the state kept
    between blocks, so the output has no seams. Output lags the input by RESAMPLER_GUARD input samples."""

    def __init__(self, source_rate, target_rate):
        divisor = math.gcd(int(source_rate), int(target_rate))
        self.up, self.down = int(target_rate) // divisor, int(source_rate) // divisor
        self._buffer = np.zeros(0)
        self._emitted = 0   # outputs of the current buffer already returned

    def process(self, block):
        """Resampled samples that are now final for this input block (may be empty)."""
        block = np.asarray(block, dtype=float)
        if self.up == self.down:
            return block
        self._buffer = np.concatenate([self._buffer, block])
        out = signal.resample_poly(self._buffer, self.up, self.down)
        ready = max(0, (len(self._buffer) - 1 - RESAMPLER_GUARD) * self.up // self.down + 1)
        result = out[self._emitted:ready]
        # Drop input that no later output needs, in multiples of `down` so the output grid stays aligned.
        cut = max(0, (ready * self.down // self.up - RESAMPLER_GUARD) // self.down * self.down)
        self._buffer = self._buffer[cut:]
        self._emitted = ready - cut * self.up // self.down
        return result


def _find_wasapi(device):
    """(index, info) of the WASAPI input matching `device` (None: the default input), or None.
    Windows lists a mic once per host API, and MME truncates the name, hence the prefix match."""
    try:
        apis = sd.query_hostapis()
        wasapi = next(i for i, api in enumerate(apis) if api["name"] == WASAPI)
        if device is None:
            device = sd.default.device[0]
            if device is None or device < 0:
                return None
        wanted = sd.query_devices(device)
        if wanted["hostapi"] == wasapi:
            return device, wanted
        for i, info in enumerate(sd.query_devices()):
            if (info["hostapi"] == wasapi and info["max_input_channels"] > 0
                    and info["name"].startswith(wanted["name"])):
                return i, info
    except (StopIteration, sd.PortAudioError, ValueError, KeyError, IndexError):
        pass
    return None


class LiveEngine:
    """The mic stream, its buffers and the live analysis. subscribe(fn) gets every live frame."""

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.analyzer = FrameAnalyzer(sample_rate, UPDATE_INTERVAL)
        self._ring = np.zeros(int(sample_rate * FrameAnalyzer.WINDOW_SECONDS))
        self._history = np.zeros(int(sample_rate * HISTORY_SECONDS))   # circular, written at _history_pos
        self._history_pos = 0
        self._history_filled = 0
        self._lock = threading.Lock()
        self._take = None
        self._ring_egg = None       # allocated when an EGG is configured or fed
        self._take_egg = None
        self.last_take_egg = None   # EGG samples of the last take (None without an EGG)
        self.egg_channel = None
        self._stream = None
        self._resamplers = None     # (mic, egg) when the stream runs at another rate than sample_rate
        self.capture_info = {}      # how the stream was opened: host_api, native_rate, latency, ...
        self._subscribers = []
        self._worker = _LiveWorker(self)

    def subscribe(self, fn):
        """Call fn(frame) for every live frame, on the analysis thread; returns an unsubscribe function."""
        with self._lock:
            self._subscribers.append(fn)

        def unsubscribe():
            with self._lock:
                if fn in self._subscribers:
                    self._subscribers.remove(fn)
        return unsubscribe

    def publish(self, frame):
        """Deliver a frame to the subscribers, as the analysis thread does (tests feed frames this way)."""
        with self._lock:
            subscribers = list(self._subscribers)
        for fn in subscribers:
            fn(frame)

    def start(self, device=None, noise_floor=None):
        """Open the mic (device index, or None for the system default) and
        start the live analysis. The analysis starts even if the mic fails, so
        picking another device later brings the meters to life."""
        self._worker.start()
        self.open_stream(device, noise_floor=noise_floor)

    def set_mode(self, mode):
        """Tell the pitch tracker whether this is speech or singing ("speak" or "sing")."""
        self.analyzer.mode = mode

    def noise_floor(self):
        """The live analyzer's voice-band noise floor (dBFS), or None until a pause was heard.
        Save it and pass it back to open_stream next session."""
        return self.analyzer.noise_floor

    def open_stream(self, device=None, egg_channel=None, noise_floor=None):
        """(Re)open the input stream, e.g. after choosing another mic.
        With egg_channel 0 or 1 the stream has two channels: that one carries the
        EGG, the other the mic. Failing to open it raises (no silent mono fallback).
        `noise_floor` (dBFS) seeds the analyzer's floor, e.g. from the last session."""
        if egg_channel not in (None, 0, 1):
            raise ValueError("egg_channel must be None, 0 or 1")
        if noise_floor is not None:
            self.analyzer.noise_floor = float(noise_floor)
        self._close_stream()
        self.egg_channel = None
        with self._lock:
            self._ring_egg = None
        channels = 1 if egg_channel is None else 2
        stream, info = self._open_wasapi(device, channels)
        if stream is None:
            stream = sd.InputStream(samplerate=self.sample_rate, channels=channels,
                                    device=device, blocksize=BLOCK_SIZE, callback=self._on_audio)
            stream.start()
            info.update(path="default", host_api=_host_api_name(device), native_rate=self.sample_rate,
                        latency=_stream_latency(stream))
            self._resamplers = None
        self.capture_info = info
        self._stream = stream
        self.egg_channel = egg_channel
        if egg_channel is not None:
            with self._lock:
                self._ring_egg = np.zeros(len(self._ring))

    def _open_wasapi(self, device, channels):
        """Try WASAPI at the device's native rate, resampling to sample_rate. (stream, info);
        stream is None when that is unavailable or fails, and info then says why."""
        match = _find_wasapi(device)
        if match is None:
            return None, {"reason": "no WASAPI device"}
        index, details = match
        rate = int(details["default_samplerate"])
        if details["max_input_channels"] < channels:
            return None, {"reason": "WASAPI device has too few channels"}
        self._resamplers = (StreamResampler(rate, self.sample_rate), StreamResampler(rate, self.sample_rate))
        try:
            stream = sd.InputStream(samplerate=rate, channels=channels, device=index, latency="low",
                                    blocksize=max(1, round(BLOCK_SIZE * rate / self.sample_rate)),
                                    callback=self._on_audio)
            stream.start()
        except Exception as e:     # exclusive-mode owner, rate refused, driver quirks: use the old path
            self._resamplers = None
            return None, {"reason": f"WASAPI failed: {e}"}
        delay = RESAMPLER_GUARD / rate if rate != self.sample_rate else 0.0
        return stream, {"path": "wasapi", "host_api": WASAPI, "native_rate": rate, "device": index,
                        "latency": _stream_latency(stream), "resampler_delay": delay}

    def _close_stream(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def stop(self):
        self._worker.stop()
        self._close_stream()

    def _on_audio(self, indata, frames, time_info, status):
        if self.egg_channel is None:
            mic, egg_samples = indata[:, 0], None
        else:
            mic, egg_samples = indata[:, 1 - self.egg_channel], indata[:, self.egg_channel]
        if self._resamplers is not None:
            mic = self._resamplers[0].process(mic)
            if egg_samples is not None:
                egg_samples = self._resamplers[1].process(egg_samples)
        self.feed(mic, egg_samples)

    def feed(self, samples, egg=None):
        """Append mic samples (and the matching EGG samples, if any) to the buffers."""
        samples = np.asarray(samples, dtype=float)
        if egg is not None:
            egg = np.asarray(egg, dtype=float)
        with self._lock:
            n = min(len(samples), len(self._ring))
            self._ring = np.roll(self._ring, -n)
            self._ring[-n:] = samples[-n:]
            self._append_history(samples[-n:])
            if egg is not None:
                if self._ring_egg is None:
                    self._ring_egg = np.zeros(len(self._ring))
                m = min(len(egg), len(self._ring_egg))
                self._ring_egg = np.roll(self._ring_egg, -m)
                self._ring_egg[-m:] = egg[-m:]
            if self._take is not None:
                self._take.append(samples.copy())
                if egg is not None:
                    self._take_egg.append(egg.copy())

    def _append_history(self, samples):
        """Add samples to the circular history (the caller holds the lock)."""
        size = len(self._history)
        first = min(len(samples), size - self._history_pos)
        self._history[self._history_pos:self._history_pos + first] = samples[:first]
        self._history[:len(samples) - first] = samples[first:]
        self._history_pos = (self._history_pos + len(samples)) % size
        self._history_filled = min(size, self._history_filled + len(samples))

    def recent_audio(self, seconds=HISTORY_SECONDS):
        """The last `seconds` of mic audio (at most HISTORY_SECONDS; less just after start), oldest first."""
        with self._lock:
            count = min(self._history_filled, int(seconds * self.sample_rate))
            ordered = np.roll(self._history, -self._history_pos)
            return ordered[len(ordered) - count:].copy()

    def latest_window(self):
        with self._lock:
            return self._ring.copy()

    def latest_egg_window(self):
        """The EGG counterpart of latest_window(), or None without an EGG."""
        with self._lock:
            return None if self._ring_egg is None else self._ring_egg.copy()

    def is_capturing(self):
        with self._lock:
            return self._take is not None

    def start_take(self):
        with self._lock:
            self._take = []
            self._take_egg = []

    def stop_take(self):
        """The recorded mic samples. The EGG samples, if any, go to last_take_egg."""
        with self._lock:
            chunks, self._take = self._take, None
            egg_chunks, self._take_egg = self._take_egg, None
        self.last_take_egg = np.concatenate(egg_chunks) if egg_chunks else None
        return np.concatenate(chunks) if chunks else np.zeros(0)


def _host_api_name(device):
    try:
        return sd.query_hostapis(sd.query_devices(device, "input")["hostapi"])["name"]
    except (sd.PortAudioError, ValueError, KeyError, TypeError):
        return "unknown"


def _stream_latency(stream):
    """Input latency (s) the stream reports, or None."""
    try:
        latency = stream.latency
        return float(latency[0] if np.ndim(latency) else latency)
    except (AttributeError, TypeError, IndexError):
        return None


def input_devices():
    """[(index, name)] of microphones. Only the default host API is listed:
    Windows shows every device once per API (MME, DirectSound, WASAPI...).
    Its "Sound Mapper" is the system default under another name, so it's left out."""
    try:
        hostapi = sd.default.hostapi
        return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0 and d["hostapi"] == hostapi
                and "Sound Mapper" not in d["name"]]
    except sd.PortAudioError:
        return []


def input_channels(index):
    """Maximum number of input channels of device `index` (None: system default); 0 if unknown.
    The UI offers the EGG option only when this is at least 2."""
    try:
        return int(sd.query_devices(index, "input")["max_input_channels"])
    except (sd.PortAudioError, ValueError):
        return 0


def device_index(name):
    """Index of the input device called `name`, or None (system default)."""
    return next((i for i, n in input_devices() if n == name), None)


class _LiveWorker:
    """The analysis thread: analyses the latest window every UPDATE_INTERVAL and publishes the frame."""

    def __init__(self, engine):
        self.engine = engine
        self.running = False
        self._thread = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self.running = True
        self._thread = threading.Thread(target=self.run, daemon=True, name="live-analysis")
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def frame(self):
        """Analyse the latest window; adds the EGG keys cq and egg_f0 (NaN without an EGG)."""
        engine = self.engine
        frame = engine.analyzer.analyze(engine.latest_window())
        frame["cq"] = frame["egg_f0"] = np.nan
        egg_window = engine.latest_egg_window()
        if egg_window is not None:
            frame["cq"] = egg.frame_cq(egg_window, engine.sample_rate)
            frame["egg_f0"] = egg.frame_egg_f0(egg_window, engine.sample_rate)
        return frame

    def run(self):
        while self.running:
            started = time.perf_counter()
            try:
                self.engine.publish(self.frame())
            except Exception as e:  # keep the meters alive on odd frames
                print(f"Live analysis error: {e}")
            elapsed = time.perf_counter() - started
            time.sleep(max(0.0, UPDATE_INTERVAL - elapsed))
