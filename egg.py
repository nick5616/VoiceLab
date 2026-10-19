"""Electroglottograph (EGG) analysis: contact quotient (CQ) and EGG-derived f0.

The EGG arrives as the second channel of a stereo interface (mic on one channel,
EGG on the other), as in FonaDyn and VoceVista. Contact is plotted upward.

Method (hybrid; literature cited from memory, not re-checked online):
- Rothenberg & Mahshie (1988): criterion-level CQ, i.e. the contact phase is where
  the signal exceeds a fixed fraction of the cycle's peak-to-peak amplitude.
- Howard (1995): the hybrid method. The closing instant comes from the peak of the
  derivative (dEGG), the opening instant from a criterion level (3/7 of the cycle
  amplitude) on the falling side.
- Henrich, d'Alessandro, Doval & Castellengo (2004): DECOM takes both instants from
  dEGG peaks and shows that pure criterion levels are biased, because the level
  at which contact "really" starts or ends depends on the waveform shape.
- Ternstrom and colleagues (FonaDyn): cycle-by-cycle metrics, cycles segmented at
  the closing peaks of dEGG, implausible cycles discarded.

Why hybrid: the closing peak of dEGG is sharp and reliable, while the opening peak
is weak and noisy in real signals, so the opening is taken from a level crossing.
Known bias versus DECOM: the level crossing follows the waveform's skewness, so
CQ differs by a few hundredths on shapes with a long slow decay or an opening
hump. Compare numbers between sessions on the same set-up, not between methods.

Pre-processing high-passes at EGG_HIGHPASS_HZ (zero phase, so the waveform shape is
kept) to remove the slow drift from larynx height changes.
"""
from functools import lru_cache

import numpy as np
import soundfile as sf
from scipy.signal import butter, find_peaks, resample_poly, sosfiltfilt

SAMPLE_RATE = 44100            # same as analysis.SAMPLE_RATE (kept local: no heavy imports)
CQ_CRITERION = 3 / 7           # hybrid opening level, fraction of cycle amplitude (Howard 1995)
EGG_HIGHPASS_HZ = 30           # removes larynx-height drift; usual range is 20-80 Hz
F0_RANGE = (40, 1500)          # plausible cycle rates (Hz)
CQ_RANGE = (0.05, 0.95)        # plausible contact quotients
PEAK_FRACTION = 0.4            # closing peak must reach this fraction of the largest dEGG peak
SMOOTH_SECONDS = 0.0001        # dEGG smoothing (s), about 4 samples at 44.1 kHz
CONSISTENT_RATIO = 1.2         # neighbouring periods must agree within this factor
MIN_GOOD_CYCLES = 5            # fewer consistent cycles than this means no usable EGG
MIN_GOOD_FRACTION = 0.5        # share of detected cycles that must be consistent
FLAT_LEVEL = 1e-4              # peak-to-peak below this is an unconnected or muted channel
RECENT_CYCLES = 3              # live frames report the median over this many cycles


@lru_cache(maxsize=4)
def _highpass_sos(sample_rate):
    return butter(2, EGG_HIGHPASS_HZ, "highpass", fs=sample_rate, output="sos")


def _highpass(egg, sample_rate):
    return sosfiltfilt(_highpass_sos(sample_rate), egg)


def _degg(egg, sample_rate):
    """Smoothed first difference (per sample)."""
    d = np.diff(egg, prepend=egg[:1])
    n = max(1, int(round(SMOOTH_SECONDS * sample_rate)))
    return np.convolve(d, np.ones(n) / n, mode="same") if n > 1 else d


def _polarity_of_filtered(x, sample_rate):
    hi, lo = np.percentile(_degg(x, sample_rate), [99.5, 0.5])
    return 1 if hi >= -lo else -1


def detect_polarity(egg, sample_rate):
    """+1 if contact is already upward, -1 if inverted, from dEGG peak asymmetry.
    With contact up, closing is a fast rise (tall dEGG peak) and opening a slower fall."""
    egg = np.asarray(egg, dtype=float)
    if len(egg) < 32:
        return 1
    return _polarity_of_filtered(_highpass(egg - np.mean(egg), sample_rate), sample_rate)


def preprocess(egg, sample_rate):
    """High-pass, polarity-correct (contact up) and normalise an EGG signal."""
    egg = np.asarray(egg, dtype=float)
    if len(egg) < 32:
        return np.zeros(len(egg))
    x = _highpass(egg - np.mean(egg), sample_rate)
    if _polarity_of_filtered(x, sample_rate) < 0:
        x = -x
    lo, hi = np.percentile(x, [0.5, 99.5])
    return x / (hi - lo) if hi - lo > 1e-12 else np.zeros(len(x))


def _cycles_from_clean(x, sample_rate, last=None):
    """(start_s, period_s, cq, closing_peak) rows, before the consistency filter."""
    empty = np.zeros((0, 4))
    if len(x) < 32:
        return empty
    d = _degg(x, sample_rate)
    top = d.max()
    if top <= 0:
        return empty
    peaks, _ = find_peaks(d, height=PEAK_FRACTION * top, distance=max(1, int(sample_rate / F0_RANGE[1])))
    peaks = peaks[(peaks > 0) & (peaks < len(d) - 1)]
    if last:
        peaks = peaks[-(last + 1):]
    if len(peaks) < 2:
        return empty
    # sub-sample peak position: parabola through the three samples around each peak
    a, b, c = d[peaks - 1], d[peaks], d[peaks + 1]
    denom = a - 2 * b + c
    shift = np.where(denom < -1e-12, 0.5 * (a - c) / np.where(denom < -1e-12, denom, -1), 0.0)
    pos = peaks + np.clip(shift, -1, 1)
    rows = []
    for i in range(len(peaks) - 1):
        s, e = peaks[i], peaks[i + 1]
        period = (pos[i + 1] - pos[i]) / sample_rate
        if not F0_RANGE[0] <= 1 / period <= F0_RANGE[1]:
            continue
        seg = x[s:e]
        k = int(np.argmax(seg))
        hi, lo = seg[k], seg.min()
        if hi - lo <= 0:
            continue
        level = lo + CQ_CRITERION * (hi - lo)
        below = np.nonzero(seg[k:] < level)[0]
        if len(below) == 0 or below[0] == 0:
            continue
        j = k + below[0]
        y0, y1 = seg[j - 1], seg[j]
        cross = (j - 1) + (y0 - level) / (y0 - y1) if y0 != y1 else float(j)
        cq = (s + cross - pos[i]) / (sample_rate * period)
        if CQ_RANGE[0] <= cq <= CQ_RANGE[1]:
            rows.append((pos[i] / sample_rate, period, cq, d[s]))
    return np.array(rows) if rows else empty


def cycles(egg, sample_rate, last=None):
    """Per-cycle array of (start_s, period_s, cq, closing_peak) from the preprocessed signal.
    Cycles that fail sanity checks (period outside 40-1500 Hz, CQ outside 0.05-0.95) are dropped."""
    return _cycles_from_clean(preprocess(egg, sample_rate), sample_rate, last)


def _consistent(rows):
    """Keep cycles whose period agrees with an adjacent cycle's.
    Noise and unvoiced stretches give erratic periods and fall out here."""
    if len(rows) < 2:
        return rows[:0]
    p = rows[:, 1]
    adjacent = np.abs(rows[1:, 0] - (rows[:-1, 0] + p[:-1])) < 0.25 * p[:-1]
    ratio = np.maximum(p[1:], p[:-1]) / np.minimum(p[1:], p[:-1])
    ok_pair = adjacent & (ratio < CONSISTENT_RATIO)
    keep = np.zeros(len(rows), dtype=bool)
    keep[:-1] |= ok_pair
    keep[1:] |= ok_pair
    return rows[keep]


def _recent(egg_window, sample_rate):
    egg_window = np.asarray(egg_window, dtype=float)
    if len(egg_window) < 64 or np.ptp(egg_window) < FLAT_LEVEL:
        return np.zeros((0, 4))
    return _consistent(cycles(egg_window, sample_rate, last=RECENT_CYCLES + 3))[-RECENT_CYCLES:]


def frame_cq(egg_window, sample_rate):
    """Median CQ over the last ~3 cycles of a live window, or NaN. Must be fast (< 1 ms)."""
    rows = _recent(egg_window, sample_rate)
    return float(np.median(rows[:, 2])) if len(rows) else np.nan


def frame_egg_f0(egg_window, sample_rate):
    """f0 from the EGG's cycle periods (NaN if none)."""
    rows = _recent(egg_window, sample_rate)
    return float(np.median(1 / rows[:, 1])) if len(rows) else np.nan


def _onto(times, rows, f0=False):
    """Resample CQ (or f0) per cycle onto `times`; NaN farther than a period from a cycle."""
    times = np.asarray(times, dtype=float)
    out = np.full(len(times), np.nan)
    if len(rows) == 0 or len(times) == 0:
        return out
    mid = rows[:, 0] + rows[:, 1] / 2
    values = 1 / rows[:, 1] if f0 else rows[:, 2]
    i = np.clip(np.searchsorted(mid, times), 1, max(len(mid) - 1, 1))
    i = np.minimum(i, len(mid) - 1)
    left = np.maximum(i - 1, 0)
    i = np.where(np.abs(times - mid[left]) <= np.abs(times - mid[i]), left, i)
    near = np.abs(times - mid[i]) <= rows[i, 1]
    out[near] = values[i[near]]
    return out


def analyze_egg_take(egg, sample_rate, times):
    """{'cq': median CQ over voiced cycles, 'cq_track': CQ resampled onto `times` (NaN gaps),
        'egg_f0_track': same for f0, 'cycles': n, 'polarity': +1/-1, 'signal_ok': bool, 'note': str|None}
    signal_ok is False (with a human-readable note) if the channel looks unconnected:
    flat, pure noise, or no periodicity."""
    egg = np.asarray(egg, dtype=float)
    result = {"cq": np.nan, "cq_track": np.full(len(times), np.nan),
              "egg_f0_track": np.full(len(times), np.nan), "cycles": 0,
              "polarity": 1, "signal_ok": False, "note": None}
    if len(egg) < sample_rate // 10 or np.ptp(egg) < FLAT_LEVEL:
        result["note"] = "EGG channel is flat or silent: check the cable, the channel choice and the EGG's gain."
        return result
    result["polarity"] = detect_polarity(egg, sample_rate)
    rows = cycles(egg, sample_rate)
    good = _consistent(rows)
    if len(good) < MIN_GOOD_CYCLES or len(good) < MIN_GOOD_FRACTION * len(rows):
        result["note"] = ("EGG channel shows no regular vocal-fold cycles (noise or not connected): "
                          "check the electrodes' contact and the EGG's gain.")
        return result
    result.update(cq=float(np.median(good[:, 2])), cycles=len(good), signal_ok=True,
                  cq_track=_onto(times, good), egg_f0_track=_onto(times, good, f0=True))
    return result


def load_stereo_take(path, egg_channel):
    """(mic, egg) mono arrays at SAMPLE_RATE from a 2-channel file (FonaDyn/VoceVista style).
    Raise ValueError with a clear message if the file isn't stereo."""
    if egg_channel not in (0, 1):
        raise ValueError("egg_channel must be 0 or 1")
    data, rate = sf.read(path, dtype="float64", always_2d=True)
    if data.shape[1] != 2:
        raise ValueError(f"Expected a stereo (2-channel) file with mic and EGG, got {data.shape[1]} channel(s).")
    mic, egg = data[:, 1 - egg_channel], data[:, egg_channel]
    if rate != SAMPLE_RATE:
        mic, egg = (resample_poly(c, SAMPLE_RATE, rate) for c in (mic, egg))
    return np.ascontiguousarray(mic), np.ascontiguousarray(egg)


def synth_egg(f0, seconds, cq, sample_rate=SAMPLE_RATE, polarity=1, drift=0.0, noise=0.0, seed=0):
    """Test-signal generator: a Lx-style EGG waveform with known contact quotient.

    Per cycle: a fast half-cosine rise at closure (5% of the cycle), a plateau that
    droops by 15%, a slower half-cosine fall (16% of the cycle) to the flat open
    phase. The closing instant is the steepest rise and the opening instant the
    steepest fall; they are `cq` of a period apart by construction. Amplitude is 0.4
    (0 to 0.4). `f0` is a number or a per-sample contour (then `seconds` is ignored,
    like synth_vowel). `drift` is a 3 Hz sine of that amplitude relative to the cycle
    amplitude, `noise` the white-noise standard deviation relative to it,
    `polarity` -1 inverts the signal.
    """
    rise, half_fall, droop = 0.05, 0.08, 0.15
    if np.ndim(f0):
        n = len(f0)
        phase = (np.cumsum(f0) / sample_rate) % 1.0
    else:
        n = int(seconds * sample_rate)
        phase = (np.arange(n) / sample_rate * f0) % 1.0
    ph = (phase + rise / 2) % 1.0        # closing centre sits at phase 0
    start = cq + rise / 2 - half_fall    # where the fall begins
    contact = np.select(
        [ph < rise, ph < start, ph < start + 2 * half_fall],
        [0.5 * (1 - np.cos(np.pi * ph / rise)),
         1 - droop * (ph - rise) / (start - rise),
         (1 - droop) * 0.5 * (1 + np.cos(np.pi * (ph - start) / (2 * half_fall)))],
        default=0.0)
    out = 0.4 * contact
    out = out + 0.4 * drift * np.sin(2 * np.pi * 3.0 * np.arange(n) / sample_rate)
    if noise:
        out = out + 0.4 * noise * np.random.default_rng(seed).standard_normal(n)
    return polarity * out
