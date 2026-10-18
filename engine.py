"""Always-on microphone stream feeding both the live meters and take recording."""
import threading
import time

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QObject, QThread, pyqtSignal

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
        self._stream = None
        self._worker = _LiveWorker(self)
        self._worker.frame_ready.connect(self.frame_ready)

    def start(self, device=None):
        """Open the mic (device index, or None for the system default) and
        start the live analysis. The analysis starts even if the mic fails, so
        picking another device later brings the meters to life."""
        self._worker.start()
        self.open_stream(device)

    def open_stream(self, device=None):
        """(Re)open the input stream, e.g. after choosing another mic."""
        self._close_stream()
        stream = sd.InputStream(samplerate=self.sample_rate, channels=1, device=device,
                                blocksize=BLOCK_SIZE, callback=self._on_audio)
        stream.start()
        self._stream = stream

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
        self.feed(indata[:, 0])

    def feed(self, samples):
        samples = np.asarray(samples, dtype=float)
        with self._lock:
            n = min(len(samples), len(self._ring))
            self._ring = np.roll(self._ring, -n)
            self._ring[-n:] = samples[-n:]
            if self._take is not None:
                self._take.append(samples.copy())

    def latest_window(self):
        with self._lock:
            return self._ring.copy()

    def is_capturing(self):
        with self._lock:
            return self._take is not None

    def start_take(self):
        with self._lock:
            self._take = []

    def stop_take(self):
        with self._lock:
            chunks, self._take = self._take, None
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


def device_index(name):
    """Index of the input device called `name`, or None (system default)."""
    return next((i for i, n in input_devices() if n == name), None)


class _LiveWorker(QThread):
    frame_ready = pyqtSignal(dict)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.running = False

    def run(self):
        self.running = True
        while self.running:
            started = time.perf_counter()
            try:
                self.frame_ready.emit(self.engine.analyzer.analyze(self.engine.latest_window()))
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
