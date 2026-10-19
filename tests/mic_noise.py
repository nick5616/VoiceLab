"""Synthetic noise matching the user's USB desk mic, measured from the quiet frames of real takes."""
import numpy as np

SAMPLE_RATE = 44100

# Broadband floor: (band centre Hz, power spectral density dB re 1/Hz) on a log-spaced grid.
# Mostly rumble: ~86% of the power is below 1 kHz. Total about -58 dBFS rms.
BROADBAND_PSD = [
    (22, -81.2), (35, -81.2), (44, -81.2), (55, -83.2), (69, -83.2), (86, -83.3), (108, -84.1),
    (135, -85.8), (169, -86.3), (211, -87.0), (264, -88.1), (331, -90.0), (414, -92.4),
    (518, -94.2), (648, -96.4), (811, -98.4), (1015, -100.4), (1271, -101.2), (1590, -100.7),
    (1990, -102.0), (2491, -104.3), (3118, -105.9), (3902, -105.7), (4884, -106.7),
    (6112, -107.7), (7650, -108.9), (9575, -109.9), (11983, -110.7), (14998, -111.2),
    (18771, -110.3)]

# Steady tones (Hz, dBFS peak), the same in every take: the audible "screech".
# Integer-kHz lines suggest USB-frame (1 ms) interference in the mic's codec.
TONES = [
    (1000, -88), (2000, -85), (3000, -90), (4000, -73.5), (5000, -88), (6000, -79),
    (7000, -91), (8000, -84), (9000, -95), (10000, -81), (12000, -96), (13100, -92),
    (14115, -96), (18000, -88), (19000, -86), (20160, -93)]


def broadband(n, seed=0, gain_db=0.0, sample_rate=SAMPLE_RATE):
    """Gaussian noise shaped to BROADBAND_PSD."""
    spectrum = np.fft.rfft(np.random.default_rng(seed).standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / sample_rate)
    centres, psd_db = np.array(BROADBAND_PSD).T
    psd = 10 ** (np.interp(np.log(np.maximum(freqs, 1)), np.log(centres), psd_db) / 10)
    return np.fft.irfft(spectrum * np.sqrt(psd * sample_rate / 2), n) * 10 ** (gain_db / 20)


def tones(n, seed=0, gain_db=0.0, only=None, sample_rate=SAMPLE_RATE):
    """The steady interference lines; `only` limits them to a list of frequencies."""
    rng = np.random.default_rng(seed + 99)
    t = np.arange(n) / sample_rate
    out = np.zeros(n)
    for freq, peak_db in TONES:
        if freq < sample_rate / 2 and (only is None or freq in only):
            out += 10 ** (peak_db / 20) * np.sin(2 * np.pi * freq * t + rng.uniform(0, 2 * np.pi))
    return out * 10 ** (gain_db / 20)


def mic_noise(n, seed=0, gain_db=0.0, sample_rate=SAMPLE_RATE):
    """Broadband floor plus tones, at the level measured from the user's takes (about -58 dBFS)."""
    return (broadband(n, seed, gain_db, sample_rate)
            + tones(n, seed, gain_db, sample_rate=sample_rate))


def at_level(audio, dbfs):
    """Scale audio so its rms is `dbfs`; the user's voiced takes sit around -42 dBFS."""
    rms = np.sqrt(np.mean(audio ** 2))
    return audio * 10 ** (dbfs / 20) / rms
