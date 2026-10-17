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
        self.analyzer = FrameAnalyzer(sample_rate)
        self._ring = np.zeros(int(sample_rate * FrameAnalyzer.WINDOW_SECONDS))
        self._lock = threading.Lock()
        self._take = None
        self._stream = None
        self._worker = _LiveWorker(self)
        self._worker.frame_ready.connect(self.frame_ready)

    def start(self):
        self._stream = sd.InputStream(samplerate=self.sample_rate, channels=1,
                                      blocksize=BLOCK_SIZE, callback=self._on_audio)
        self._stream.start()
        self._worker.start()

    def stop(self):
        self._worker.running = False
        self._worker.wait()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()

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

    def start_take(self):
        with self._lock:
            self._take = []

    def stop_take(self):
        with self._lock:
            chunks, self._take = self._take, None
        return np.concatenate(chunks) if chunks else np.zeros(0)


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


class TakeAnalysisWorker(QThread):
    finished_analysis = pyqtSignal(dict)

    def __init__(self, audio, sample_rate=SAMPLE_RATE):
        super().__init__()
        self.audio = audio
        self.sample_rate = sample_rate

    def run(self):
        try:
            result = analyze_take(self.audio, self.sample_rate)
        except Exception as e:
            result = {"error": f"Error: {e}"}
        self.finished_analysis.emit(result)
