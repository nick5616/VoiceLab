"""Always-on microphone stream feeding both the live meters and take recording."""
import threading
import time

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QObject, QThread, pyqtSignal

import egg
from analysis import SAMPLE_RATE, FrameAnalyzer, analyze_take

UPDATE_INTERVAL = 0.025   # live meter refresh (s)
BLOCK_SIZE = 512


class LiveEngine(QObject):
    frame_ready = pyqtSignal(dict)

    def __init__(self, sample_rate=SAMPLE_RATE):
        super().__init__()
        self.sample_rate = sample_rate
        self.analyzer = FrameAnalyzer(sample_rate, UPDATE_INTERVAL)
        self._ring = np.zeros(int(sample_rate * FrameAnalyzer.WINDOW_SECONDS))
        self._lock = threading.Lock()
        self._take = None
        self._ring_egg = None       # allocated when an EGG is configured or fed
        self._take_egg = None
        self.last_take_egg = None   # EGG samples of the last take (None without an EGG)
        self.egg_channel = None
        self._stream = None
        self._worker = _LiveWorker(self)
        self._worker.frame_ready.connect(self.frame_ready)

    def start(self, device=None):
        """Open the mic (device index, or None for the system default) and
        start the live analysis. The analysis starts even if the mic fails, so
        picking another device later brings the meters to life."""
        self._worker.start()
        self.open_stream(device)

    def open_stream(self, device=None, egg_channel=None):
        """(Re)open the input stream, e.g. after choosing another mic.
        With egg_channel 0 or 1 the stream has two channels: that one carries the
        EGG, the other the mic. Failing to open it raises (no silent mono fallback)."""
        if egg_channel not in (None, 0, 1):
            raise ValueError("egg_channel must be None, 0 or 1")
        self._close_stream()
        self.egg_channel = None
        with self._lock:
            self._ring_egg = None
        stream = sd.InputStream(samplerate=self.sample_rate, channels=1 if egg_channel is None else 2,
                                device=device, blocksize=BLOCK_SIZE, callback=self._on_audio)
        stream.start()
        self._stream = stream
        self.egg_channel = egg_channel
        if egg_channel is not None:
            with self._lock:
                self._ring_egg = np.zeros(len(self._ring))

    def _close_stream(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def stop(self):
        self._worker.running = False
        self._worker.wait()
        self._close_stream()

    def _on_audio(self, indata, frames, time_info, status):
        if self.egg_channel is None:
            self.feed(indata[:, 0])
        else:
            self.feed(indata[:, 1 - self.egg_channel], indata[:, self.egg_channel])

    def feed(self, samples, egg=None):
        """Append mic samples (and the matching EGG samples, if any) to the buffers."""
        samples = np.asarray(samples, dtype=float)
        if egg is not None:
            egg = np.asarray(egg, dtype=float)
        with self._lock:
            n = min(len(samples), len(self._ring))
            self._ring = np.roll(self._ring, -n)
            self._ring[-n:] = samples[-n:]
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


class _LiveWorker(QThread):
    frame_ready = pyqtSignal(dict)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.running = False

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
        self.running = True
        while self.running:
            started = time.perf_counter()
            try:
                self.frame_ready.emit(self.frame())
            except Exception as e:  # keep the meters alive on odd frames
                print(f"Live analysis error: {e}")
            elapsed = time.perf_counter() - started
            time.sleep(max(0.0, UPDATE_INTERVAL - elapsed))


class AnalysisWorker(QThread):
    """Runs fn(*args) off the UI thread and emits its result dict."""
    finished_analysis = pyqtSignal(dict)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            result = self.fn(*self.args)
        except Exception as e:
            result = {"error": f"Error: {e}"}
        self.finished_analysis.emit(result)


def take_analysis_worker(audio, sample_rate=SAMPLE_RATE):
    return AnalysisWorker(analyze_take, audio, sample_rate)
