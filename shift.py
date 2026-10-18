"""Re-render a take with the voice shifted along one or more dimensions, so you
can hear a direction before practising toward it.

Pitch, resonance, intonation range and tempo use Praat's "Change gender"
(PSOLA plus resampling for formants), which stays natural for moderate shifts.
Breathiness and brightness need the source and filter separated, so they use
the WORLD vocoder (pyworld), which sounds slightly buzzy at large settings.
"""
import numpy as np
import parselmouth
from parselmouth.praat import call

from analysis import PITCH_FLOOR, SAMPLE_RATE, analyze_take, pitch_track

# name: (label, min, max, neutral, unit). Slider values are in these units.
SHIFT_PARAMS = {
    "semitones": ("Pitch", -12.0, 12.0, 0.0, "st"),
    "resonance": ("Resonance", -20.0, 25.0, 0.0, "%"),
    "intonation": ("Intonation range", 50.0, 200.0, 100.0, "%"),
    "tempo": ("Tempo", 70.0, 140.0, 100.0, "%"),
    "breathiness": ("Breathiness", -100.0, 100.0, 0.0, ""),
    "brightness": ("Brightness", -6.0, 6.0, 0.0, "dB/oct"),
}
NEUTRAL = {key: spec[3] for key, spec in SHIFT_PARAMS.items()}
BRIGHTNESS_PIVOT_HZ = 1000     # tilt rotates around this frequency
WORLD_FRAME_MS = 5.0


def is_neutral(params):
    return all(params.get(k, v) == v for k, v in NEUTRAL.items())


def shift_voice(audio, sample_rate=SAMPLE_RATE, **params):
    p = NEUTRAL | params
    x = np.asarray(audio, dtype=float).flatten()
    rms = np.sqrt(np.mean(x ** 2)) + 1e-12

    if (p["semitones"], p["resonance"], p["intonation"], p["tempo"]) != (0, 0, 100, 100):
        sound = parselmouth.Sound(x, sampling_frequency=sample_rate)
        _, f0 = pitch_track(sound)
        if np.isnan(f0).all():
            raise ValueError("No voiced audio to shift.")
        median = float(np.nanmedian(f0))
        # Analysis ceiling just above the take's own range limits octave errors.
        ceiling = float(np.clip(np.nanmax(f0) * 1.2, 300, 1200))
        new_median = median * 2 ** (p["semitones"] / 12) if p["semitones"] else 0
        shifted = call(sound, "Change gender...", PITCH_FLOOR, ceiling, 1 + p["resonance"] / 100,
                       new_median, p["intonation"] / 100, 100 / p["tempo"])
        x = shifted.values[0]

    if p["breathiness"] or p["brightness"]:
        x = _world_shift(x, sample_rate, p["breathiness"] / 100, p["brightness"])

    x *= rms / (np.sqrt(np.mean(x ** 2)) + 1e-12)
    return np.clip(x, -1, 1)


def _world_shift(x, sample_rate, breathiness, brightness):
    import pyworld as pw
    x = np.ascontiguousarray(x, dtype=np.float64)
    f0, t = pw.dio(x, sample_rate, f0_floor=PITCH_FLOOR, f0_ceil=1200, frame_period=WORLD_FRAME_MS)
    f0 = pw.stonemask(x, f0, t, sample_rate)
    sp = pw.cheaptrick(x, f0, t, sample_rate)
    ap = pw.d4c(x, f0, t, sample_rate)
    freqs = np.linspace(0, sample_rate / 2, sp.shape[1])

    if brightness:
        # Tilt the spectral envelope: + lifts the upper harmonics (edgier,
        # heavier-sounding), - darkens (softer, lighter-sounding).
        gain_db = brightness * np.log2(np.maximum(freqs, 100) / BRIGHTNESS_PIVOT_HZ)
        sp = sp * 10 ** (gain_db / 10)
    if breathiness:
        # Scale aperiodicity in dB toward 0 dB (pure noise) or away from it.
        ap_db = 20 * np.log10(np.clip(ap, 1e-6, 1))
        ap = np.clip(10 ** (ap_db * (1 - 0.7 * breathiness) / 20), 0, 1)
    return pw.synthesize(f0, np.ascontiguousarray(sp), np.ascontiguousarray(ap),
                         sample_rate, WORLD_FRAME_MS)


def render_and_analyze(audio, params, sample_rate=SAMPLE_RATE):
    """Shifted audio plus its full analysis, so the shift can become a target."""
    shifted = shift_voice(audio, sample_rate, **params)
    return {"audio": shifted, "analysis": analyze_take(shifted, sample_rate), "params": params}
