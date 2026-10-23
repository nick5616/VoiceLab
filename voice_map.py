"""Voice map: a pitch x level grid accumulated from live frames and coloured by
a voice-quality metric, in the spirit of FonaDyn (Ternström & Pabon). It shows
your range and where in it your voice is clearest, brightest or lightest. MapSession is the live map as
data; a UI draws its grid(). No UI toolkit."""
import numpy as np

from analysis import SAMPLE_RATE, frame_levels, hz_to_midi
from display import midi_name

MIDI_LO, MIDI_HI = 36, 96            # C2-C7
LEVEL_LO, LEVEL_HI, LEVEL_STEP = -60, 0, 2   # dBFS
MIN_FRAMES = 12                      # frames before a cell is shown: 0.3 s of live frames (25 ms apart)
# Plausible f0 (Hz) per mode; frames outside it are tracker ghosts (60-70 Hz rumble, the ~1 kHz
# subharmonic of the mic's 4 kHz tone: phase 9 audits). Speech 70-500 and singing 70-1100 are
# judgment calls (unverified); mode None uses the wider range.
MODE_F0 = {"speak": (70.0, 500.0), "sing": (70.0, 1100.0)}
REDRAW_EVERY = 10                    # live frames between redraws
# key: (label, colour range, unit)
MAP_METRICS = {
    "cpp": ("Clarity (CPP)", (8, 30), " dB"),
    "ring": ("Ring (2–4 kHz)", (-30, 0), " dB"),
    "h1h2": ("Fold Contact (H1*–H2*)", (-5, 15), " dB"),
    "cq": ("Contact quotient (EGG)", (0.2, 0.7), ""),
}
EGG_METRICS = {"cq"}                 # only offered once an EGG has filled some cells
HINT = ("Builds up as you sing or speak, across sessions. Level is uncalibrated: "
        "keep mic gain and distance the same.")
REBUILD_TOOLTIP = ("Replace the map with one built from your saved takes, without the noise "
                   "older versions counted as voice.")


class VoiceMapData:
    def __init__(self):
        shape = (MIDI_HI - MIDI_LO + 1, (LEVEL_HI - LEVEL_LO) // LEVEL_STEP)
        self.counts = np.zeros(shape)
        self.sums = {k: np.zeros(shape) for k in MAP_METRICS}
        self.metric_counts = {k: np.zeros(shape) for k in MAP_METRICS}

    def add(self, frame, mode=None):
        """Count a live frame; False if it was skipped (unvoiced, f0 implausible for the mode, or
        level off the map). Level is the voice-band one when the frame has it."""
        f0, level = frame["f0"], frame.get("band_level", frame["level"])
        low, high = MODE_F0.get(mode, (MODE_F0["speak"][0], MODE_F0["sing"][1]))
        if np.isnan(f0) or not low <= f0 <= high or not LEVEL_LO <= level < LEVEL_HI:
            return False
        col = int(round(float(hz_to_midi(f0)))) - MIDI_LO
        if not 0 <= col < self.counts.shape[0]:
            return False
        row = int((level - LEVEL_LO) // LEVEL_STEP)
        self.counts[col, row] += 1
        for key in MAP_METRICS:
            value = frame.get(key, np.nan)
            if not np.isnan(value):
                self.sums[key][col, row] += value
                self.metric_counts[key][col, row] += 1
        return True

    def mean(self, key):
        n = self.metric_counts[key]
        out = np.full(n.shape, np.nan)
        shown = self.counts >= MIN_FRAMES
        out[shown & (n > 0)] = self.sums[key][shown & (n > 0)] / n[shown & (n > 0)]
        return out

    def clear(self):
        """Forget everything."""
        self.__init__()

    def has_data(self, key):
        return bool(self.metric_counts[key].sum() > 0)

    def range_midi(self):
        cols = np.flatnonzero((self.counts >= MIN_FRAMES).any(axis=1))
        return (cols[0] + MIDI_LO, cols[-1] + MIDI_LO) if len(cols) else None

    def save(self, path):
        np.savez(path, counts=self.counts,
                 **{f"sum_{k}": v for k, v in self.sums.items()},
                 **{f"n_{k}": v for k, v in self.metric_counts.items()})

    @classmethod
    def load(cls, path):
        data = cls()
        try:
            with np.load(path) as f:
                if f["counts"].shape == data.counts.shape:
                    data.counts = f["counts"]
                    for k in MAP_METRICS:       # maps saved before a metric existed lack it
                        if f"sum_{k}" in f:
                            data.sums[k] = f[f"sum_{k}"]
                            data.metric_counts[k] = f[f"n_{k}"]
        except (OSError, KeyError, ValueError):
            pass
        return data


def rebuild(takes, mode=None):
    """A fresh VoiceMapData from saved takes (dicts with 'audio' and 'tracks'), so the map can be
    rebuilt without the ghost frames older versions stored. Takes without a pitch track are skipped."""
    data = VoiceMapData()
    for take in takes:
        tracks = take.get("tracks")
        if not tracks or take.get("audio") is None:
            continue
        times, f0 = np.asarray(tracks["times"], dtype=float), np.asarray(tracks["f0"], dtype=float)
        levels = frame_levels(np.asarray(take["audio"], dtype=float).flatten(), SAMPLE_RATE, times, band=True)
        for i in np.flatnonzero(~np.isnan(f0)):
            frame = {"f0": f0[i], "level": levels[i]}
            for key in MAP_METRICS:
                values = tracks.get(key)
                frame[key] = np.nan if values is None else values[i]
            data.add(frame, take.get("mode") or mode)
    return data


class MapSession:
    """The Voice Map kept across sessions at `path`: live frames add to it (on_frame), and grid(key)
    gives what to draw for one metric. `mode` ("speak" | "sing") range-checks the pitch."""

    def __init__(self, path):
        self.path = path
        self.mode = None
        self.data = VoiceMapData.load(path) if path.exists() else VoiceMapData()
        self.pending = 0            # frames added since the last grid()

    def metric_keys(self):
        """Metrics the picker offers: EGG ones only while the map holds EGG data."""
        return [k for k in MAP_METRICS if k not in EGG_METRICS or self.data.has_data(k)]

    def metric_choices(self):
        """[(key, label)] for the metric picker."""
        return [(k, MAP_METRICS[k][0]) for k in self.metric_keys()]

    def on_frame(self, frame):
        """Count a live frame; True if it was added to the map."""
        added = self.data.add(frame, self.mode)
        if added:
            self.pending += 1
        return added

    def due(self):
        """True once enough frames came in since the last grid() to draw again."""
        return self.pending >= REDRAW_EVERY

    def grid(self, key):
        """What to draw for metric `key`: {"values" (cell means, with empty cells below the colour range),
        "levels" (lo, hi) of the colour range, "rect" (x, y, width, height) in MIDI and dBFS, "x_range",
        "y_range", "scale_text", "range_text", "ticks" [(x, name)]}."""
        self.pending = 0
        lo, hi = MAP_METRICS[key][1]
        values = self.data.mean(key)
        # Real values map to colour-table indices 1-255 and empty cells below them to 0.
        step = (hi - lo) / 254
        rng = self.data.range_midi()
        if rng:
            lo_m, hi_m = rng
            range_text = f"Range: {midi_name(lo_m)}–{midi_name(hi_m)} ({hi_m - lo_m} semitones)"
            x_range = (max(MIDI_LO, lo_m - 4), min(MIDI_HI + 1, hi_m + 5))
        else:
            range_text, x_range = "Nothing mapped yet.", (MIDI_LO + 6, MIDI_LO + 42)
        return {"values": np.where(np.isnan(values), lo - 10 * step, values), "levels": (lo - step, hi),
                "rect": (MIDI_LO, LEVEL_LO, MIDI_HI - MIDI_LO + 1, LEVEL_HI - LEVEL_LO),
                "x_range": x_range, "y_range": (LEVEL_LO, LEVEL_HI),
                "scale_text": f"Colour: purple {lo:g} → yellow {hi:g}{MAP_METRICS[key][2]}",
                "range_text": range_text,
                "ticks": [(m + 0.5, midi_name(m)) for m in range(MIDI_LO, MIDI_HI + 1, 12)]}

    def clear(self):
        """Forget everything, and keep that."""
        self.data = VoiceMapData()
        self.save()

    def rebuild(self, takes):
        """Replace the map with one built from saved takes (see rebuild)."""
        self.use(rebuild(takes, self.mode))

    def use(self, data):
        """Replace the map with `data` (a VoiceMapData) and keep it."""
        self.data = data
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data.save(self.path)
