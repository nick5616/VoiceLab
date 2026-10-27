"""Synthetic voices with a known truth, for the UI lab (and tests): a glottal pulse train through formant
resonators, with pitch tracks you choose, plus silence, room noise like the user's desk mic, rumble, whine
and clipping. Pure numpy and scipy; every random part has a fixed seed, so the same call gives the same
samples. Adapted from tests/test_analysis.synth_vowel and tests/mic_noise.py (which stay as they are)."""
import numpy as np
from scipy.signal import lfilter

SR = 44100                      # Hz; analysis.SAMPLE_RATE (tests/test_uilab.py checks they agree)
# Formants (Hz, bandwidth Hz). MALE is tests/test_analysis.MALE_FORMANTS; FEMALE and NEUTRAL are typical
# /a/-like vowels for shorter vocal tracts (Peterson & Barney 1952 averages, rounded; from memory, unverified).
MALE_FORMANTS = [(500, 80), (1500, 100), (2500, 120), (3500, 150), (4500, 200)]
NEUTRAL_FORMANTS = [(560, 85), (1650, 105), (2700, 130), (3700, 160), (4600, 200)]
FEMALE_FORMANTS = [(620, 90), (1750, 110), (2800, 140), (3800, 180), (4700, 220)]
PEAK = 0.3                      # peak amplitude of a voice before level scaling (as synth_vowel)
VOICE_DBFS = -42.0              # rms of the user's voiced takes (tests/mic_noise.at_level's note)
ROOM_DBFS = -58.0               # rms of the user's desk-mic floor (tests/mic_noise.py)
OPEN_QUOTIENT = 0.6             # Rosenberg pulse: the share of a cycle the glottis is open
FADE_SECONDS = 0.01             # raised-cosine edges on every voiced stretch, so they don't click
PHRASE_SECONDS = (1.4, 2.2)     # speech_like: phrase lengths drawn from this range (s)
PAUSE_SECONDS = (0.35, 0.6)     # speech_like: pauses between phrases (s); longer than report.PHRASE_GAP_S
DECLINATION_ST = 2.5            # speech_like: pitch falls this much over a phrase (semitones; own judgement)
INTONATION_ST = 1.5             # speech_like: +/- semitones of slow wobble inside a phrase
# The user's mic: broadband floor as (band centre Hz, PSD dB re 1/Hz) and steady tones (Hz, dBFS peak).
# Copied from tests/mic_noise.py, measured from the quiet frames of the user's real takes.
BROADBAND_PSD = [
    (22, -81.2), (35, -81.2), (44, -81.2), (55, -83.2), (69, -83.2), (86, -83.3), (108, -84.1),
    (135, -85.8), (169, -86.3), (211, -87.0), (264, -88.1), (331, -90.0), (414, -92.4),
    (518, -94.2), (648, -96.4), (811, -98.4), (1015, -100.4), (1271, -101.2), (1590, -100.7),
    (1990, -102.0), (2491, -104.3), (3118, -105.9), (3902, -105.7), (4884, -106.7),
    (6112, -107.7), (7650, -108.9), (9575, -109.9), (11983, -110.7), (14998, -111.2),
    (18771, -110.3)]
TONES = [
    (1000, -88), (2000, -85), (3000, -90), (4000, -73.5), (5000, -88), (6000, -79),
    (7000, -91), (8000, -84), (9000, -95), (10000, -81), (12000, -96), (13100, -92),
    (14115, -96), (18000, -88), (19000, -86), (20160, -93)]
RUMBLE_HZ = (20, 90)            # desk rumble band (Hz): below the voice, where denoise looks for it
WHINE_HZ = 4000                 # a steady whine (Hz): the user's strongest interference line


def samples(seconds, sr=SR):
    return int(round(seconds * sr))


def at_level(audio, dbfs):
    """Scale audio so its rms over the non-silent part is `dbfs`."""
    audio = np.asarray(audio, dtype=float)
    live = audio[np.abs(audio) > 1e-9]
    rms = np.sqrt(np.mean(live ** 2)) if len(live) else 0.0
    return audio if rms == 0 else audio * 10 ** (dbfs / 20) / rms


def _fades(voiced, sr):
    """A 0-1 envelope that ramps in and out over FADE_SECONDS at the edges of each voiced stretch."""
    env = voiced.astype(float)
    n = max(1, samples(FADE_SECONDS, sr))
    kernel = np.hanning(2 * n + 1)
    kernel /= kernel.sum()
    return np.clip(np.convolve(env, kernel, mode="same"), 0, 1) * env


def synth_voice(f0_track, formants=MALE_FORMANTS, seconds=None, sr=SR, breath=0.0, jitter=0.0,
                open_quotient=OPEN_QUOTIENT, seed=0, level_dbfs=VOICE_DBFS):
    """A voice with a known pitch: `f0_track` is a number (Hz, held for `seconds`) or one value per sample
    (Hz; NaN or 0 is silence, so a track can hold pauses). `breath` adds aspiration noise (share of the
    source's rms), `jitter` random cycle-to-cycle pitch wobble (share of f0, e.g. 0.005). Scaled to
    `level_dbfs` rms over the voiced part (None keeps PEAK)."""
    rng = np.random.default_rng(seed)
    if np.ndim(f0_track):
        f0 = np.asarray(f0_track, dtype=float).copy()
    else:
        f0 = np.full(samples(seconds, sr), float(f0_track))
    voiced = np.isfinite(f0) & (f0 > 0)
    f0 = np.where(voiced, f0, 0.0)
    if jitter:
        wobble = lfilter([1.0], [1.0, -0.995], rng.standard_normal(len(f0))) * jitter * 0.1
        f0 = f0 * (1 + np.clip(wobble, -3 * jitter, 3 * jitter))
    phase = np.cumsum(f0 / sr) % 1.0
    opening = open_quotient * 0.66
    pulse = np.where(phase < opening, 0.5 * (1 - np.cos(np.pi * phase / opening)),
                     np.where(phase < open_quotient,
                              np.cos(0.5 * np.pi * (phase - opening) / (open_quotient - opening)), 0.0))
    source = np.diff(pulse * voiced, prepend=0.0)
    if breath:
        scale = np.std(source[voiced]) if voiced.any() else 0.0
        source = source + breath * scale * rng.standard_normal(len(f0)) * voiced
    out = source
    for freq, bw in formants:
        r = np.exp(-np.pi * bw / sr)
        theta = 2 * np.pi * freq / sr
        a = [1, -2 * r * np.cos(theta), r ** 2]
        out = lfilter([sum(a)], a, out)
    out = lfilter([1, -0.97], [1], out) * _fades(voiced, sr)
    peak = np.max(np.abs(out)) if len(out) else 0.0
    out = PEAK * out / peak if peak > 0 else out
    if level_dbfs is not None and voiced.any():
        out = at_level(out, level_dbfs)
    return out


# --- PITCH TRACKS (Hz per sample, NaN where silent) ---

def steady_track(f0, seconds, sr=SR):
    return np.full(samples(seconds, sr), float(f0))


def glide_track(start_hz, end_hz, seconds, sr=SR):
    """A glide at a constant rate in semitones (geometric in Hz)."""
    return np.geomspace(float(start_hz), float(end_hz), samples(seconds, sr))


def vibrato_track(f0, seconds, rate_hz=5.5, extent_cents=50, sr=SR):
    """A held note with sinusoidal vibrato of +/- extent_cents."""
    t = np.arange(samples(seconds, sr)) / sr
    return f0 * 2 ** (extent_cents / 1200 * np.sin(2 * np.pi * rate_hz * t))


def scale_track(start_midi=57, steps=(0, 2, 4, 5, 7, 5, 4, 2, 0), note_seconds=0.6, sr=SR):
    """A sung scale: one note per step (semitones above start_midi), legato."""
    hz = [440.0 * 2 ** ((start_midi + s - 69) / 12) for s in steps]
    return np.concatenate([np.full(samples(note_seconds, sr), h) for h in hz])


def speech_track(center_hz, seconds, seed=0, sr=SR):
    """Speech-like pitch: phrases that fall DECLINATION_ST with a slow wobble, split by pauses. The phrase
    medians sit around `center_hz`."""
    rng = np.random.default_rng(seed)
    out = []
    total = 0.0
    while total < seconds:
        length = min(rng.uniform(*PHRASE_SECONDS), seconds - total)
        n = samples(length, sr)
        t = np.arange(n) / sr
        st = DECLINATION_ST * (0.5 - t / max(length, 1e-6)) + INTONATION_ST * np.sin(
            2 * np.pi * t * rng.uniform(1.5, 3.0) + rng.uniform(0, 2 * np.pi))
        out.append(center_hz * 2 ** (st / 12))
        total += length
        if total < seconds:
            pause = min(rng.uniform(*PAUSE_SECONDS), seconds - total)
            out.append(np.full(samples(pause, sr), np.nan))
            total += pause
    return np.concatenate(out) if out else np.zeros(0)


def with_silence(track, before=0.0, after=0.0, sr=SR):
    """A pitch track with silent (NaN) lead-in and tail."""
    return np.concatenate([np.full(samples(before, sr), np.nan), np.asarray(track, dtype=float),
                           np.full(samples(after, sr), np.nan)])


# --- READY-MADE VOICES ---

def steady(f0, seconds=2.0, formants=MALE_FORMANTS, **kwargs):
    return synth_voice(steady_track(f0, seconds), formants, **kwargs)


def glide(start_hz, end_hz, seconds=3.0, formants=NEUTRAL_FORMANTS, **kwargs):
    return synth_voice(glide_track(start_hz, end_hz, seconds), formants, **kwargs)


def speech_like(center_hz=150, seconds=6.0, formants=MALE_FORMANTS, seed=0, **kwargs):
    return synth_voice(speech_track(center_hz, seconds, seed), formants, seed=seed, **kwargs)


def sung_scale(start_midi=57, note_seconds=0.6, formants=NEUTRAL_FORMANTS, **kwargs):
    return synth_voice(scale_track(start_midi, note_seconds=note_seconds), formants, **kwargs)


def held_vibrato(f0=220.0, seconds=3.0, rate_hz=5.5, extent_cents=50, formants=NEUTRAL_FORMANTS, **kwargs):
    return synth_voice(vibrato_track(f0, seconds, rate_hz, extent_cents), formants, **kwargs)


def silence(seconds, sr=SR):
    return np.zeros(samples(seconds, sr))


# --- NOISE AND FAULTS ---

def room_noise(seconds, dbfs=ROOM_DBFS, seed=0, sr=SR):
    """The user's desk-mic floor (broadband plus steady tones), scaled to `dbfs` rms."""
    n = samples(seconds, sr)
    if n == 0:
        return np.zeros(0)
    spectrum = np.fft.rfft(np.random.default_rng(seed).standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    centres, psd_db = np.array(BROADBAND_PSD).T
    psd = 10 ** (np.interp(np.log(np.maximum(freqs, 1)), np.log(centres), psd_db) / 10)
    out = np.fft.irfft(spectrum * np.sqrt(psd * sr / 2), n)
    rng = np.random.default_rng(seed + 99)
    t = np.arange(n) / sr
    for freq, peak_db in TONES:
        if freq < sr / 2:
            out += 10 ** (peak_db / 20) * np.sin(2 * np.pi * freq * t + rng.uniform(0, 2 * np.pi))
    return at_level(out, dbfs)


def noise_only(seconds, dbfs=ROOM_DBFS, seed=0, sr=SR):
    """Room noise and nothing else: what the mic hears while you are silent."""
    return room_noise(seconds, dbfs, seed, sr)


def rumble(seconds, dbfs=-45.0, seed=1, sr=SR):
    """Low desk rumble (RUMBLE_HZ band-limited noise) at `dbfs` rms."""
    n = samples(seconds, sr)
    spectrum = np.fft.rfft(np.random.default_rng(seed).standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    spectrum[(freqs < RUMBLE_HZ[0]) | (freqs > RUMBLE_HZ[1])] = 0
    return at_level(np.fft.irfft(spectrum, n), dbfs)


def whine(seconds, freq=WHINE_HZ, dbfs=-55.0, sr=SR):
    """A steady tone (USB interference) at `dbfs` rms."""
    t = np.arange(samples(seconds, sr)) / sr
    return at_level(np.sin(2 * np.pi * freq * t), dbfs)


def clipped(audio, gain_db=30.0, ceiling=0.99):
    """Audio driven `gain_db` louder and hard-clipped at +/- ceiling (an overloaded input)."""
    return np.clip(np.asarray(audio, dtype=float) * 10 ** (gain_db / 20), -ceiling, ceiling)


def mix(*parts):
    """Sum signals of different lengths (shorter ones are padded with silence)."""
    n = max(len(p) for p in parts)
    out = np.zeros(n)
    for p in parts:
        out[:len(p)] += p
    return out


def voiced_spans(track, sr=SR):
    """[(start s, end s)] of the voiced stretches of a pitch track (the truth for settle times)."""
    voiced = np.isfinite(track) & (np.asarray(track) > 0)
    edges = np.flatnonzero(np.diff(np.concatenate([[0], voiced.astype(int), [0]])))
    return [(a / sr, b / sr) for a, b in zip(edges[::2], edges[1::2])]
