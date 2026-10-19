# 02 EGG core (wave 1)

## Goal

Support an **electroglottograph** (e.g. Glottal Enterprises EG2-PCX or Laryngograph), connected as the second channel of a stereo audio interface: the microphone on one channel, the EGG on the other. This is how FonaDyn and VoceVista take EGG input. The EGG gives the measure the README calls the only reliable weight correlate: **contact quotient (CQ)**. No UI in this spec.

There's no hardware to test with, so everything is checked on synthetic EGG signals with known CQ.

## Background you must research first

Cite these in the `egg.py` docstring and your report:
- Rothenberg & Mahshie (1988): criterion-level CQ. Howard (1995): the hybrid method (3/7 threshold).
- Henrich, d'Alessandro, Doval & Castellengo (2004): dEGG peak detection (DECOM) for closing/opening instants, and why criterion levels are biased.
- Polarity convention: increasing contact plotted upward. Devices differ, so detect polarity and correct it.
- Pre-processing: high-pass (on the order of 20–80 Hz; find the usual value) to remove the slow drift from larynx height changes.
- Ternström's FonaDyn papers, for which per-cycle metrics are worth having and how cycles are segmented.

## Deliverables

### `egg.py` (new)

```python
CQ_CRITERION = 0.25        # or the hybrid; justify the choice in the docstring

def preprocess(egg, sample_rate):
    """High-pass, polarity-correct (contact up) and normalise an EGG signal."""

def detect_polarity(egg, sample_rate):
    """+1 if contact is already upward, -1 if inverted, from dEGG peak asymmetry."""

def cycles(egg, sample_rate):
    """Per-cycle array of (start_s, period_s, cq, closing_peak) from the preprocessed signal.
    Cycles that fail sanity checks (period outside 40-1500 Hz, CQ outside 0.05-0.95) are dropped."""

def frame_cq(egg_window, sample_rate):
    """Median CQ over the last ~3 cycles of a live window, or NaN. Must be fast (< 1 ms)."""

def frame_egg_f0(egg_window, sample_rate):
    """f0 from the EGG's cycle periods (NaN if none)."""

def analyze_egg_take(egg, sample_rate, times):
    """{'cq': median CQ over voiced cycles, 'cq_track': CQ resampled onto `times` (NaN gaps),
        'egg_f0_track': same for f0, 'cycles': n, 'polarity': +1/-1, 'signal_ok': bool, 'note': str|None}
    signal_ok is False (with a human-readable note) if the channel looks unconnected:
    flat, pure noise, or no periodicity."""

def load_stereo_take(path, egg_channel):
    """(mic, egg) mono arrays at SAMPLE_RATE from a 2-channel file (FonaDyn/VoceVista style).
    Raise ValueError with a clear message if the file isn't stereo."""
```

Also write `synth_egg(f0, seconds, cq, sample_rate=SAMPLE_RATE, polarity=1, drift=0.0, noise=0.0)` in `egg.py`, as a documented test-signal generator. A plain test helper would do, but integration's app tests need it too. Use a standard Lx model: a fast rise at closure, a slower decay to the open phase, and a flat open phase, so that the true CQ is known by construction. `f0` may be an array, like `synth_vowel`.

### `engine.py`

- `LiveEngine.open_stream(device=None, egg_channel=None)`. With `egg_channel` in {0, 1}, open the stream with `channels=2`; the other channel is the mic. With None, behaviour is exactly as now (`channels=1`).
- Keep a second ring buffer for EGG samples, and a second take buffer while a take is recording.
- `stop_take()` keeps its return value (the mic array), so every existing caller still works. The matching EGG array goes in `self.last_take_egg` (None without an EGG).
- `latest_egg_window()` mirrors `latest_window()`, or returns None.
- `feed(samples, egg=None)`: the tests feed audio this way. Existing calls with one argument must keep working.
- `_LiveWorker` adds `frame["cq"]` and `frame["egg_f0"]` (NaN without an EGG) to each frame dict, computed with `egg.frame_cq` and `egg.frame_egg_f0`. Don't change `FrameAnalyzer` (`analysis.py` belongs to spec 01). Add the keys in the worker after `analyze()` returns.
- `input_channels(index)`: the maximum number of input channels of a device, so the UI can offer the EGG option only on stereo devices. Wrap PortAudio errors like `input_devices()` does.
- If opening a 2-channel stream fails, raise. Don't silently fall back to mono, because the UI must tell the user.

### Tests

`tests/test_egg.py`:
1. `cycles`/`analyze_egg_take` recover CQ within ±0.03 for true CQ 0.3, 0.45 and 0.6, at f0 100, 220 and 440 Hz.
2. Inverted polarity is detected and corrected, giving the same CQ.
3. Slow drift (`drift` at a few Hz, larger than the cycle amplitude) doesn't bias CQ by more than 0.03.
4. EGG f0 is within 1 Hz of the true f0, including a glide from 150 to 300 Hz.
5. Flat input, white noise and silence each give `signal_ok False`, with a note.
6. `frame_cq` runs in under 1 ms on a 0.12 s window (timing test).
7. `load_stereo_take` round-trips a stereo WAV written with soundfile (`tmp_path`), and rejects a mono file.

`tests/test_engine.py`:
1. Feeding mic plus EGG during a take: `stop_take()` returns the mic samples and `last_take_egg` the EGG samples, with equal lengths.
2. Without an EGG, `last_take_egg is None`, and frames carry `cq` and `egg_f0` as NaN.
3. A live frame computed from a fed synthetic EGG carries a CQ within ±0.05 of the true value. Call the worker's per-frame step directly (factor it into a method if needed). Don't start the thread.
4. `open_stream(egg_channel=1)` requests 2 channels. Monkeypatch `sd.InputStream` to capture its kwargs.

## Acceptance

- The new tests and the full suite pass, including `tests/test_app.py` unchanged.
- With no EGG configured, the engine behaves exactly as before.
- The report covers the CQ method chosen and why, its known bias versus DECOM, and what a user must set on their interface (channel, gain, no AGC).
