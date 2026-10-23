"""Take playback through sounddevice, with a position a UI can follow. No UI toolkit."""
import inspect
import time

import numpy as np
import sounddevice as sd

from analysis import SAMPLE_RATE

COMPARE_GAP_SECONDS = 0.5       # silence between a take and the one it is compared with
END_MARGIN_SECONDS = 0.05       # playback starts at least this long before the end


def _takes_an_argument(fn):
    try:
        inspect.signature(fn).bind(None)
        return True
    except TypeError:
        return False
    except ValueError:          # no signature to inspect (some builtins): assume it takes the owner
        return True


class Player:
    """One playback at a time. play_take() plays a take from `start`, then optionally another after a
    gap; position() says which piece is playing and where. `play`, `stop` and `clock` default to
    sd.play, sd.stop and time.monotonic (looked up when used, so tests can replace them)."""

    def __init__(self, play=None, stop=None, clock=time.monotonic, sample_rate=SAMPLE_RATE):
        self._play, self._stop, self.clock = play, stop, clock
        self.sample_rate = sample_rate
        self.playing = False
        self.owner = None           # whoever started the playback (e.g. a take card)
        self.pieces = []            # (piece, offset in its take or None for the gap, seconds)
        self.started = 0.0
        self._finished = []

    def _sound(self, buffer):
        (self._play or sd.play)(buffer, self.sample_rate)

    def clip_start(self, samples, start):
        """`start` (s) kept just inside the take, so playback always has something to play."""
        duration = len(samples) / self.sample_rate
        return float(np.clip(start, 0, max(duration - END_MARGIN_SECONDS, 0)))

    def play_take(self, samples, start=0.0, then=None, gap=COMPARE_GAP_SECONDS, owner=None):
        """Play `samples` from `start` seconds (kept just inside the take), then, if given, `then` after `gap`
        seconds of silence. Another owner's playback is stopped first. Returns the start actually used."""
        self._stop_other(owner)
        samples = np.asarray(samples, dtype=float)
        start = self.clip_start(samples, start)
        buffer = samples[int(start * self.sample_rate):]
        pieces = [("take", start, len(buffer) / self.sample_rate)]
        if then is not None:
            other = np.asarray(then, dtype=float)
            silence = np.zeros(int(gap * self.sample_rate))
            pieces += [("gap", None, gap), ("compare", 0.0, len(other) / self.sample_rate)]
            buffer = np.concatenate([buffer, silence, other])
        self._sound(buffer)
        self._begin(pieces, owner)
        return start

    def track(self, pieces, owner=None):
        """Follow a playback started elsewhere (e.g. a shifted render): pieces as in play_take, nothing played."""
        self._stop_other(owner)
        self._begin(pieces, owner)

    def track_take(self, samples, start=0.0, owner=None):
        """Follow a take played elsewhere (e.g. its shifted render) from `start`, as play_take would.
        Returns the start used."""
        start = self.clip_start(samples, start)
        self.track([("take", start, len(samples) / self.sample_rate - start)], owner)
        return start

    def sound(self, samples):
        """Just play `samples`, without tracking a position (previews such as the shifted voice)."""
        self._sound(samples)

    def _stop_other(self, owner):
        if self.playing and self.owner is not owner:
            self.stop()

    def _begin(self, pieces, owner):
        self.pieces, self.owner = list(pieces), owner
        self.playing = True
        self.started = self.clock()

    def stop(self):
        """Stop playback; on_finished handlers hear about it."""
        if not self.playing:
            return
        self.playing = False
        owner, self.owner = self.owner, None
        (self._stop or sd.stop)()
        for fn in list(self._finished):
            if _takes_an_argument(fn):
                fn(owner)
            else:
                fn()

    def position(self):
        """{"playing": bool, "piece": "take"|"gap"|"compare"|None, "seconds": float|None, "index": int|None}.
        `seconds` is the time within the piece's own take (None in the gap). Past the end, playback stops."""
        if self.playing:
            elapsed = self.clock() - self.started
            for index, (piece, offset, length) in enumerate(self.pieces):
                if elapsed < length:
                    return {"playing": True, "piece": piece, "index": index,
                            "seconds": None if offset is None else offset + elapsed}
                elapsed -= length
            self.stop()
        return {"playing": False, "piece": None, "seconds": None, "index": None}

    def on_finished(self, fn):
        """Call fn(owner) (or fn() if it takes no argument) whenever playback stops or ends; returns an
        unsubscribe function."""
        self._finished.append(fn)
        return lambda: fn in self._finished and self._finished.remove(fn)
