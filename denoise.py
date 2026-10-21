"""Steady-interference handling for the user's USB mic: noise characterisation and coherent line removal.

The mic adds steady narrowband lines at integer kHz (the strongest at 4 kHz) to a rumble-heavy
floor. Notches and spectral gating damage formants (phase 9 signal-chain audit), so the lines are
instead estimated coherently and subtracted, touching nothing but the lines themselves.

Intended call order for a recorded take (wave B wires it in; nothing here is called by analysis.py):
    noise = noise_audio_from_take(audio, sr, voiced)    # lead-in and pauses only
    lines = find_lines(noise, sr)                       # never detect on voiced audio
    clean = cancel_lines(audio, sr, lines)              # then analyse `clean`
    report = noise_report(audio, sr, voiced)            # on the raw take, for the quality tier
Lines must come from noise-only audio: detected on voiced audio they include voice harmonics.
Limitation: a voice harmonic within about 4-15 Hz of a line loses energy (a 200 Hz voice's 20th
harmonic sits on 4 kHz). Pass `lines` without that entry if it matters.
"""
import numpy as np
from scipy import signal
from scipy.ndimage import median_filter

SEGMENT_SECONDS = 0.3      # spectrum segment for line persistence (s)
LINE_MARGIN_DB = 8.0       # a line must stand this far over the local median floor (audit: 17-31 dB real, floor false-alarm < 1e-3)
LINE_PERSISTENCE = 0.8     # ... in this fraction of the segments
LINE_FLOOR_BAND_HZ = 300   # width of the local median floor (Hz)
LINE_MIN_HZ = 900          # below this the voice and mains hum live; lines there are not touched (audit: lines are >= 1 kHz)
LINE_MIN_DB = -110         # weaker than this is below 16-bit quantisation noise (dBFS peak)
LINE_RANGE_DB = 40         # lines this far under the strongest are leakage of it (audit lines span 22 dB)
COMB_MAX_HZ = 600          # equally spaced lines closer than this are a voice's harmonics (Hz)
COMB_MIN_LINES = 4         # ... when at least this many in a row
LINE_MIN_SECONDS = 0.9     # shortest noise audio that can be searched (s)
TRACK_HZ = 4.0             # coherent estimate bandwidth (Hz); about 40 ms to converge (audit)
HOLD_STEP = 44             # decimation of the noise-only amplitude samples (about 1 ms)
BLOCK_SECONDS = 0.05       # level block for finding quiet stretches (s)
QUIET_BAND_DB = 6.0        # blocks within this of the quietest are noise-only (dB)
VOICED_MARGIN_DB = 10.0    # without a mask, blocks this far over the quietest are voice (audit SNRs are 11.5-19.7 dB)
NOISE_MARGIN_S = 0.1       # keep this far from voiced frames (s)
RUMBLE_HZ = 150            # noise power below this counts as rumble (Hz)
CLIP_LEVEL = 0.999         # samples at or above are clipped
VOICE_BAND = 4000          # voice-band SNR upper edge (Hz); same as analysis.VOICE_BAND_HZ
_EPS = 1e-12


def _db(x):
    return 10 * np.log10(np.maximum(x, _EPS))


def _line_frequency(lines):
    return [float(line[0]) if np.ndim(line) else float(line) for line in lines]


def find_lines(noise_audio, sr, margin_db=LINE_MARGIN_DB, persistence=LINE_PERSISTENCE, min_hz=LINE_MIN_HZ):
    """Stationary narrowband lines in noise-only audio: [(frequency Hz, peak level dBFS)], ascending."""
    x = np.asarray(noise_audio, dtype=float)
    seg = int(SEGMENT_SECONDS * sr)
    count = len(x) // seg
    if len(x) < LINE_MIN_SECONDS * sr or count < 3:
        return []
    nfft = 1 << int(np.ceil(np.log2(seg * 4)))
    window = np.hanning(seg)
    smooth = max(3, int(LINE_FLOOR_BAND_HZ / (sr / nfft)) | 1)
    hits = np.zeros(nfft // 2 + 1)
    mean_power = np.zeros(nfft // 2 + 1)
    for i in range(count):
        power = np.abs(np.fft.rfft((x[i * seg:(i + 1) * seg] - np.mean(x[i * seg:(i + 1) * seg])) * window, nfft)) ** 2
        mean_power += power / count
        hits += _db(power) - _db(median_filter(power, size=smooth)) >= margin_db
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    candidates = np.flatnonzero((hits >= persistence * count) & (freqs >= min_hz) & (freqs <= 0.48 * sr))
    if not len(candidates):
        return []
    amp_scale = 2 / np.sum(window)
    found = []
    for group in np.split(candidates, np.flatnonzero(np.diff(candidates) > 3) + 1):
        peak = group[np.argmax(mean_power[group])]
        left, mid, right = (np.log(mean_power[peak + k] + _EPS) for k in (-1, 0, 1))
        shift = 0.5 * (left - right) / (left - 2 * mid + right) if left - 2 * mid + right < 0 else 0.0
        level = np.sqrt(mean_power[peak]) * amp_scale
        found.append((float((peak + np.clip(shift, -1, 1)) * sr / nfft), float(20 * np.log10(max(level, _EPS)))))
    return _drop_voice(found)


def _drop_voice(found):
    """Remove window leakage and runs of equally spaced close lines (harmonics of a voiced tone)."""
    if not found:
        return found
    top = max(level for _, level in found)
    found = [(f, level) for f, level in found if level >= max(LINE_MIN_DB, top - LINE_RANGE_DB)]
    freqs = [f for f, _ in found]
    drop = set()
    run = [0]
    for i in range(1, len(freqs) + 1):
        if i < len(freqs):
            gap = freqs[i] - freqs[i - 1]
            step = freqs[run[-1]] - freqs[run[-2]] if len(run) > 1 else gap
            if gap < COMB_MAX_HZ and abs(gap - step) < 2 + 0.005 * gap:
                run.append(i)
                continue
        if len(run) >= COMB_MIN_LINES:
            drop.update(run)
            spacing = np.median(np.diff([freqs[j] for j in run]))
            drop.update(j for j, f in enumerate(freqs)
                        if abs(f - round(f / spacing) * spacing) < 2 + 0.005 * f)   # harmonics the run skipped
        run = [i]
    return [line for i, line in enumerate(found) if i not in drop]


def _block_levels(x, sr):
    """RMS (dBFS) of consecutive BLOCK_SECONDS blocks; the remainder is dropped."""
    n = int(BLOCK_SECONDS * sr)
    blocks = x[:len(x) // n * n].reshape(-1, n)
    return _db(np.mean((blocks - blocks.mean()) ** 2, axis=1)), n


def _voiced_blocks(voiced, count, length):
    """Per-block bool from a mask of any length stretched evenly over `length` samples (None: all False)."""
    if voiced is None:
        return np.zeros(count, bool)
    voiced = np.asarray(voiced, dtype=bool)
    index = (np.arange(count) + 0.5) / count * len(voiced)
    return voiced[np.minimum(index.astype(int), len(voiced) - 1)]


def _noise_blocks(audio, sr, voiced):
    """Boolean per block: noise-only. Unvoiced and near the quietest level; a margin is kept from the voice."""
    levels, n = _block_levels(audio, sr)
    if not len(levels):
        return np.zeros(0, bool), n
    if voiced is None:
        return levels <= np.percentile(levels, 10) + QUIET_BAND_DB, n
    marked = _voiced_blocks(voiced, len(levels), len(audio))
    margin = max(1, int(round(NOISE_MARGIN_S / BLOCK_SECONDS)))
    near = np.convolve(marked.astype(float), np.ones(2 * margin + 1), "same") > 0
    if near.all():
        return ~near, n
    quiet = levels <= np.median(levels[~near]) + QUIET_BAND_DB   # breaths and fricatives in the pauses are louder
    return quiet & ~near, n


def noise_audio_from_take(audio, sr, voiced=None, seconds=None):
    """The noise-only stretches of a take (lead-in, pauses), joined. `voiced` is a bool mask of any
    length (per sample or per frame, stretched over the take); None picks the quietest blocks.
    `seconds` caps the length, keeping the quietest blocks. Empty array if nothing is quiet enough."""
    audio = np.asarray(audio, dtype=float)
    keep, n = _noise_blocks(audio, sr, voiced)
    if not keep.any():
        return np.zeros(0)
    if seconds is not None:
        levels, _ = _block_levels(audio, sr)
        limit = int(seconds / BLOCK_SECONDS)
        order = np.argsort(np.where(keep, levels, np.inf))[:limit]
        keep = np.zeros_like(keep)
        keep[order] = True
        keep &= np.isfinite(levels)
    return np.concatenate([audio[i * n:(i + 1) * n] for i in np.flatnonzero(keep)])


def _held_amplitude(amp, quiet, step):
    """Complex line amplitude kept from the noise-only samples and interpolated across the rest, so
    the voice never feeds the estimate. The residual frequency is fitted first so that the phase
    stays flat enough to interpolate through gaps of seconds."""
    index = np.flatnonzero(quiet)[::step]
    if len(index) < 3:
        return None
    phase = np.unwrap(np.angle(amp[index]))
    runs = np.split(np.arange(len(index)), np.flatnonzero(np.diff(index) > 2 * step) + 1)
    longest = max(runs, key=len)
    if len(longest) >= 3:
        slope = np.polyfit(index[longest], phase[longest], 1)[0]   # rad/sample: the frequency error
    else:
        slope = 0.0
    flat = np.angle(amp[index] * np.exp(-1j * slope * index))
    flat = np.unwrap(flat)
    where = np.arange(len(amp))
    magnitude = np.interp(where, index, np.abs(amp[index]))
    return magnitude * np.exp(1j * (np.interp(where, index, flat) + slope * where))


def cancel_lines(audio, sr, lines, voiced=None, track_hz=TRACK_HZ):
    """Subtract each line coherently: demodulate at its frequency, low-pass to `track_hz` (so the
    estimate follows slow drift and amplitude changes), remodulate and subtract. Only the given
    lines (tuples of (Hz, level) or plain Hz) are touched, within about `track_hz` of each.
    With `voiced` (mask as in noise_audio_from_take) the amplitude is taken from the noise-only
    stretches alone and interpolated through the voiced ones, so a voice harmonic sitting on a
    line survives; without it the line is tracked everywhere (use for audio with no voice).
    Audio with a `voiced` mask but no noise-only stretch is returned unchanged."""
    x = np.asarray(audio, dtype=float)
    out = x.copy()
    if not len(x):
        return out
    quiet = None
    if voiced is not None:
        keep, n = _noise_blocks(x, sr, voiced)
        quiet = np.zeros(len(x), bool)
        quiet[:len(keep) * n] = np.repeat(keep, n)
        if quiet.sum() < 3 * HOLD_STEP:
            return out
    t = np.arange(len(x)) / sr
    sos = signal.butter(2, track_hz, fs=sr, output="sos")
    for freq in _line_frequency(lines):
        carrier = np.exp(2j * np.pi * freq * t)
        z = x / carrier
        amp = signal.sosfiltfilt(sos, z.real) + 1j * signal.sosfiltfilt(sos, z.imag)
        if quiet is not None:
            amp = _held_amplitude(amp, quiet, HOLD_STEP)
            if amp is None:
                continue
        out -= 2 * np.real(amp * carrier)
    return out


def _band_rms_db(x, sr, high_hz):
    sos = signal.butter(4, high_hz, "lp", fs=sr, output="sos")
    return 10 * np.log10(max(np.mean(signal.sosfilt(sos, x - np.mean(x)) ** 2), _EPS))


def noise_report(audio, sr, voiced=None):
    """Plain dict describing the take's input quality. Noise is measured on the noise-only stretches,
    the voice on the rest (`voiced` as in noise_audio_from_take; None picks them by level).
    Keys: noise_db (floor dBFS), rumble_share (noise power below 150 Hz), lines [(Hz, dBFS)],
    voiced_db, snr_db (full band), snr_voice_db (below 4 kHz), peak, peak_db, clip_fraction, dc,
    noise_seconds. NaN where the take has no noise-only or no voiced stretch."""
    audio = np.asarray(audio, dtype=float)
    nan = float("nan")
    report = {"noise_db": nan, "rumble_share": nan, "lines": [], "voiced_db": nan, "snr_db": nan,
              "snr_voice_db": nan, "peak": 0.0, "peak_db": -np.inf, "clip_fraction": 0.0, "dc": 0.0,
              "noise_seconds": 0.0}
    if not len(audio):
        return report
    peak = float(np.max(np.abs(audio)))
    report.update(peak=peak, peak_db=float(20 * np.log10(max(peak, _EPS))),
                  clip_fraction=float(np.mean(np.abs(audio) >= CLIP_LEVEL)), dc=float(np.mean(audio)))
    keep, n = _noise_blocks(audio, sr, voiced)
    if not keep.any():
        return report
    noise = np.concatenate([audio[i * n:(i + 1) * n] for i in np.flatnonzero(keep)])
    report["noise_seconds"] = len(noise) / sr
    report["noise_db"] = _band_rms_db(noise, sr, 0.499 * sr)
    freqs, psd = signal.welch(noise - np.mean(noise), sr, nperseg=min(len(noise), 4096))
    report["rumble_share"] = float(psd[freqs < RUMBLE_HZ].sum() / max(psd.sum(), _EPS))
    report["lines"] = find_lines(noise, sr)
    levels, _ = _block_levels(audio, sr)
    loud = ~keep
    if voiced is not None:
        loud &= _voiced_blocks(voiced, len(keep), len(audio))
    else:
        loud &= levels >= np.percentile(levels, 10) + VOICED_MARGIN_DB
    if loud.any():
        speech = np.concatenate([audio[i * n:(i + 1) * n] for i in np.flatnonzero(loud)])
        report["voiced_db"] = _band_rms_db(speech, sr, 0.499 * sr)
        report["snr_db"] = report["voiced_db"] - report["noise_db"]
        report["snr_voice_db"] = _band_rms_db(speech, sr, VOICE_BAND) - _band_rms_db(noise, sr, VOICE_BAND)
    return report
