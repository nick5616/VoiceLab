"""In-app tracing for a real session (VOICELAB_TRACE=<dir> or main.py --trace <dir>): every core event and
toast, every live frame (the analysis values in frames.jsonl, the snapshot every SNAPSHOT_EVERY frames, and the
readouts as the lab logs them in events.jsonl), and a capture (screenshot, widget tree, core state) every
SHOT_SECONDS and after every take. The folder has a lab run's layout, so `python -m tools.uilab trace <dir>`
runs the evals over it. Nothing is installed unless asked for; it only reads the window and the core."""
import os
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QTimer

from tools.uilab.runio import JsonLines, summarize, write_json
from ui import theme
from ui.lab.capture import capture

SNAPSHOT_EVERY = 40             # frames (1 s) between full snapshots in frames.jsonl
SHOT_SECONDS = 30               # s between periodic captures
FRAME_KEYS = ("f0", "level", "delta_f", "h1h2", "cpp", "ring", "resonance_reliable", "clipped", "cq", "egg_f0")
ENV = "VOICELAB_TRACE"


def trace_dir(argv):
    """The trace folder from `--trace DIR` or VOICELAB_TRACE, or None (tracing off)."""
    if "--trace" in argv:
        i = argv.index("--trace")
        if i + 1 < len(argv):
            return argv[i + 1]
    return os.environ.get(ENV) or None


class Tracer:
    """Records a real session into `folder`/session/ (beats.json, events.jsonl, frames.jsonl, captures)."""

    def __init__(self, window, folder, shot_seconds=SHOT_SECONDS):
        self.window, self.core = window, window.core
        self.dir = Path(folder) / "session"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events = JsonLines(self.dir / "events.jsonl")
        self.frames = JsonLines(self.dir / "frames.jsonl")
        self.started = time.perf_counter()
        self.count = 0
        self.beats = []
        emit = self.core.events.emit

        def logged(name, *args):
            self.events.write({"type": "event", "name": name, "args": summarize(args), "t": self.now(),
                               "wall": round(time.perf_counter(), 4)})
            emit(name, *args)
            if name == "take_added":
                QTimer.singleShot(500, lambda: self.shoot("take"))
        self.core.events.emit = logged
        signals = window.signals
        signals.frame_ready.disconnect(window.on_frame)
        signals.frame_ready.connect(self._frame)
        self.timer = QTimer(window)
        self.timer.setInterval(int(shot_seconds * 1000))
        self.timer.timeout.connect(lambda: self.shoot("periodic"))
        self.timer.start()
        write_json(self.dir.parent / "run.json", {"trace": True, "at": datetime.now().isoformat(timespec="seconds")})
        self._write_beats()

    def now(self):
        return round(time.perf_counter() - self.started, 4)

    def _frame(self, frame):
        t0 = time.perf_counter()
        self.window.on_frame(frame)
        cost = time.perf_counter() - t0
        self.count += 1
        rec = {"i": self.count, "t": self.now()}
        rec.update({k: frame.get(k) for k in FRAME_KEYS if k in frame})
        if self.count % SNAPSHOT_EVERY == 0:
            snap = self.core.live.snapshot()
            snap.pop("pitch_history", None)
            rec["snapshot"] = snap
        self.frames.write(rec)
        w = self.window
        view = w.dashboard.live if w.stack.currentWidget() is w.dashboard else (
            w.live.view if w.stack.currentWidget() is w.live else None)
        line = {"type": "frame", "i": self.count, "t": rec["t"], "ui_ms": round(cost * 1000, 2), "f0": frame.get("f0"),
                "screen": w.current()}
        if view is not None:
            line["readout"] = view.note_label.text()
            cards = getattr(view, "cards", None)        # the dashboard's strip has no cards
            card = cards.cards.get("pitch") if cards is not None else None
            line["card_pitch"] = card.value.text() if card is not None and card.isVisible() else None
        line["banner"] = w.banner.label.text() if not w.banner.isHidden() else None
        self.events.write(line)

    def shoot(self, why):
        """One capture of the window as it is now (a beat in beats.json)."""
        n = len(self.beats) + 1
        name = f"{n:03d}-{why}"
        size = (self.window.width(), self.window.height())
        stem = f"{name}-{theme.current().name}-{size[0]}x{size[1]}"
        try:
            meta = capture(self.window, str(self.dir / stem), {"kind": "trace", "why": why}, full=False)
        except Exception as e:      # tracing must never break the app
            self.events.write({"type": "error", "where": "capture", "error": str(e), "t": self.now()})
            return
        self.events.flush()
        self.frames.flush()
        self.beats.append({"name": name, "do": "trace", "error": None,
                           "truth": {"beat_start": self.now(), "beat_end": self.now()},
                           "captures": [{"theme": theme.current().name, "size": list(size), "stem": stem,
                                         "actual": meta["size"], "screen": meta["screen"], "dialog": meta["dialog"],
                                         "full": [], "capture_ms": meta["capture_ms"]}]})
        self._write_beats()

    def _write_beats(self):
        write_json(self.dir / "beats.json", {"scenario": {"id": "session", "title": "A real session (trace)",
                                                          "groups": []}, "beats": self.beats})

    def close(self):
        self.timer.stop()
        self.events.close()
        self.frames.close()


def install(window, folder):
    """Start tracing the window into `folder`; returns the Tracer (kept on the window)."""
    window.tracer = Tracer(window, folder)
    return window.tracer
