"""Acoustic measurements shared by the live meters and the per-take report.

Metric choices follow the research summary in the README:
- Resonance uses formant dispersion (Delta F) over F1-F4, which tracks perceived
  resonance largely independently of pitch and weight (Netzorg et al., 2025).
- Weight has no reliable single acoustic correlate, so we report proxies:
  formant-corrected H1*-H2* (Iseli & Alwan), cepstral peak prominence and level.
- Pitch variation is measured in semitones so it is comparable across registers.
"""
import numpy as np
import parselmouth

SAMPLE_RATE = 44100

PITCH_FLOOR, PITCH_CEILING = 60, 1200
# Above this F0, harmonics are too sparse for LPC to find the vocal tract
# resonances; it locks onto harmonics instead.
FORMANT_RELIABLE_MAX_F0 = 350
FORMANT_CEILINGS = np.arange(4500, 6501, 250)
DEFAULT_FORMANT_CEILING = 5500
# Adjacent real formants are rarely closer than this.
MIN_FORMANT_GAP = 250
SPEED_OF_SOUND_CM = 35000

# Analysis frame for spectral measures (~46 ms at 44.1 kHz).
SPECTRAL_FRAME = 2048

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


# --- PITCH HELPERS ---

def hz_to_note(f0):
    """Return (note name, cents offset) for a frequency, e.g. ("A3", +12)."""
    midi = 69 + 12 * np.log2(f0 / 440.0)
    nearest = int(round(midi))
    cents = int(round((midi - nearest) * 100))
    return f"{NOTE_NAMES[nearest % 12]}{nearest // 12 - 1}", cents


def semitone_std(f0_values):
    f0 = np.asarray(f0_values, dtype=float)
    f0 = f0[~np.isnan(f0)]
    if len(f0) < 2:
        return np.nan
    return float(np.std(12 * np.log2(f0 / np.median(f0))))


# --- RESONANCE ---

def delta_f(f1, f2, f3, f4):
    """Formant dispersion: mean of Fi / (i - 0.5). Works on scalars or arrays."""
    return (f1 / 0.5 + f2 / 1.5 + f3 / 2.5 + f4 / 3.5) / 4


def vocal_tract_length_cm(df):
    """Apparent vocal tract length implied by Delta F (uniform tube model)."""
    return SPEED_OF_SOUND_CM / (2 * df)


# --- SOURCE / WEIGHT PROXIES ---

def level_dbfs(frame):
    rms = np.sqrt(np.mean(np.square(frame)))
    return float(20 * np.log10(rms + 1e-12))


def _power_spectrum_db(frame, sample_rate, pad=1):
    n = len(frame)
    nfft = int(2 ** np.ceil(np.log2(n * pad)))
    spec = np.fft.rfft(frame * np.hanning(n), n=nfft)
    freqs = np.fft.rfftfreq(nfft, 1 / sample_rate)
    return freqs, 10 * np.log10(np.abs(spec) ** 2 + 1e-20)


def cpp(frame, sample_rate, f0):
    """Cepstral peak prominence (dB): height of the rahmonic peak near 1/f0
    above the regression line through the cepstrum. Higher = clearer, more
    periodic voice; lower = breathier or rougher."""
    if f0 is None or np.isnan(f0):
        return np.nan
    _, power_db = _power_spectrum_db(frame, sample_rate)
    # Power cepstrum in dB, as in Praat's PowerCepstrum, so values are comparable
    # with published CPP figures.
    ceps = 20 * np.log10(np.abs(np.fft.irfft(power_db)) + 1e-12)
    quef = np.arange(len(ceps)) / sample_rate

    lo, hi = 1 / (1.25 * f0), 1 / (0.8 * f0)
    search = (quef >= lo) & (quef <= hi)
    fit = (quef >= 0.001) & (quef <= max(hi, 1 / PITCH_FLOOR))
    if not search.any() or fit.sum() < 2:
        return np.nan
    peak = np.flatnonzero(search)[np.argmax(ceps[search])]
    slope, intercept = np.polyfit(quef[fit], ceps[fit], 1)
    return float(ceps[peak] - (slope * quef[peak] + intercept))


def _resonance_gain_db(freq, formant, bandwidth, sample_rate):
    """Gain of a unit-DC-gain two-pole resonator at `freq` (Iseli & Alwan)."""
    r = np.exp(-np.pi * bandwidth / sample_rate)
    wi = 2 * np.pi * formant / sample_rate
    w = 2 * np.pi * freq / sample_rate
    num = (1 - 2 * r * np.cos(wi) + r ** 2) ** 2
    den = (1 - 2 * r * np.cos(w - wi) + r ** 2) * (1 - 2 * r * np.cos(w + wi) + r ** 2)
    return 10 * np.log10(num / den)


def h1_h2_corrected(frame, sample_rate, f0, formants, bandwidths):
    """H1*-H2*: level difference between the first two harmonics with the boost
    from F1/F2 removed. Higher = lighter / less vocal fold contact; lower =
    heavier / more pressed."""
    if f0 is None or np.isnan(f0):
        return np.nan
    freqs, power_db = _power_spectrum_db(frame, sample_rate, pad=4)

    def harmonic_db(k):
        band = (freqs >= k * f0 * 0.9) & (freqs <= k * f0 * 1.1)
        return power_db[band].max() if band.any() else np.nan

    h1, h2 = harmonic_db(1), harmonic_db(2)
    for formant, bandwidth in zip(formants[:2], bandwidths[:2]):
        if np.isnan(formant) or np.isnan(bandwidth):
            continue
        h1 -= _resonance_gain_db(f0, formant, bandwidth, sample_rate)
        h2 -= _resonance_gain_db(2 * f0, formant, bandwidth, sample_rate)
    return float(h1 - h2)


def ring_db(frame, sample_rate):
    """Energy in 2-4 kHz (singer's formant / 'ring' region) relative to 0-2 kHz."""
    freqs, power_db = _power_spectrum_db(frame, sample_rate)
    power = 10 ** (power_db / 10)
    low = power[(freqs > 50) & (freqs < 2000)].sum()
    high = power[(freqs >= 2000) & (freqs < 4000)].sum()
    return float(10 * np.log10((high + 1e-20) / (low + 1e-20)))


# --- PRAAT WRAPPERS ---

def pitch_track(sound, time_step=0.01):
    pitch = sound.to_pitch_ac(time_step=time_step, pitch_floor=PITCH_FLOOR,
                              pitch_ceiling=PITCH_CEILING)
    f0 = pitch.selected_array["frequency"].astype(float)
    f0[f0 == 0] = np.nan
    return pitch.xs(), f0


def formant_values(formant_obj, times):
    """Arrays of shape (len(times), 4) for F1-F4 and (len(times), 2) for B1-B2."""
    freqs = np.array([[formant_obj.get_value_at_time(i, t) for i in range(1, 5)] for t in times])
    bws = np.array([[formant_obj.get_bandwidth_at_time(i, t) for i in range(1, 3)] for t in times])
    return freqs.reshape(-1, 4), bws.reshape(-1, 2)


def to_formants(sound, ceiling, time_step=0.01):
    return sound.to_formant_burg(time_step=time_step, max_number_of_formants=5,
                                 maximum_formant=ceiling, window_length=0.025)


def choose_formant_ceiling(sound, times):
    """Pick the Burg ceiling whose F1-F4 tracks are smoothest over `times`.

    A fixed ceiling biases results as the speaker changes their vocal tract
    (e.g. 5000 Hz suits a large tract, 5500 Hz a smaller one), so we try a range
    and keep the one with the least frame-to-frame jitter, in the spirit of
    FastTrack (Barreda, 2021).
    """
    if len(times) < 10:
        return DEFAULT_FORMANT_CEILING
    best, best_score = DEFAULT_FORMANT_CEILING, np.inf
    for ceiling in FORMANT_CEILINGS:
        freqs, _ = formant_values(to_formants(sound, ceiling), times)
        missing = np.isnan(freqs).mean()
        jumps = np.abs(np.diff(freqs, axis=0)) / np.nanmedian(freqs, axis=0)
        # A ceiling that is too low splits one resonance into two formants
        # sitting almost on top of each other.
        merged = np.mean(np.nanmin(np.diff(freqs, axis=1), axis=1) < MIN_FORMANT_GAP)
        # Small pull toward the default breaks ties on very steady vowels.
        prior = 1e-3 * abs(ceiling - DEFAULT_FORMANT_CEILING) / 1000
        score = np.nansum(np.nanmedian(jumps, axis=0)) + missing + merged + prior
        if score < best_score:
            best, best_score = int(ceiling), score
    return best


# --- LIVE FRAME ANALYSIS ---

class FrameAnalyzer:
    """Measures the most recent slice of audio for the live meters."""

    WINDOW_SECONDS = 0.12

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.formant_ceiling = DEFAULT_FORMANT_CEILING

    def analyze(self, window):
        frame = window[-SPECTRAL_FRAME:]
        result = {"level": level_dbfs(frame), "f0": np.nan, "delta_f": np.nan,
                  "h1h2": np.nan, "cpp": np.nan, "ring": np.nan,
                  "resonance_reliable": False}

        sound = parselmouth.Sound(window, sampling_frequency=self.sample_rate)
        _, f0_track = pitch_track(sound)
        recent = f0_track[-3:]
        if np.isnan(recent).all():
            return result
        f0 = float(np.nanmedian(recent))
        result["f0"] = f0

        # Praat's Burg window is twice `window_length`, so 80 ms gives a few frames.
        tail = sound.extract_part(from_time=sound.duration - 0.08, to_time=sound.duration)
        formants = to_formants(tail, self.formant_ceiling, time_step=0.005)
        freqs, bws = formant_values(formants, formants.xs())
        freqs, bws = np.nanmedian(freqs, axis=0), np.nanmedian(bws, axis=0)

        result["cpp"] = cpp(frame, self.sample_rate, f0)
        result["h1h2"] = h1_h2_corrected(frame, self.sample_rate, f0, freqs, bws)
        result["ring"] = ring_db(frame, self.sample_rate)
        if not np.isnan(freqs).any():
            result["delta_f"] = float(delta_f(*freqs))
        result["resonance_reliable"] = f0 <= FORMANT_RELIABLE_MAX_F0
        return result


# --- TAKE ANALYSIS ---

def analyze_take(audio, sample_rate=SAMPLE_RATE):
    audio = np.asarray(audio, dtype=float).flatten()
    if len(audio) < sample_rate * 0.3:
        return {"error": "Take too short. Record at least a second of voice."}

    sound = parselmouth.Sound(audio, sampling_frequency=sample_rate)
    times, f0 = pitch_track(sound)
    voiced = ~np.isnan(f0)
    if voiced.sum() < 20:
        return {"error": f"Not enough voiced audio ({int(voiced.sum())} voiced frames). "
                         "Try speaking louder or longer."}

    reliable = voiced & (f0 <= FORMANT_RELIABLE_MAX_F0)
    ceiling = choose_formant_ceiling(sound, times[reliable])
    freqs, bws = formant_values(to_formants(sound, ceiling), times)

    h1h2, cpps, rings, levels = [], [], [], []
    half = SPECTRAL_FRAME // 2
    for t, f, fr, bw in zip(times[voiced], f0[voiced], freqs[voiced], bws[voiced]):
        center = int(t * sample_rate)
        frame = audio[max(0, center - half):center + half]
        if len(frame) < SPECTRAL_FRAME:
            continue
        h1h2.append(h1_h2_corrected(frame, sample_rate, f, fr, bw))
        cpps.append(cpp(frame, sample_rate, f))
        rings.append(ring_db(frame, sample_rate))
        levels.append(level_dbfs(frame))

    df_frames = delta_f(*freqs[reliable].T)
    df_frames = df_frames[~np.isnan(df_frames)]
    if len(df_frames) >= 10:
        resonance, resonance_note = float(np.median(df_frames)), None
    else:
        resonance = None
        resonance_note = ("Pitch too high for reliable resonance measurement"
                          if voiced.sum() > reliable.sum() * 2 else
                          "Not enough clear formant data")

    median_f0 = float(np.nanmedian(f0))
    note, cents = hz_to_note(median_f0)
    return {
        "pitch": median_f0,
        "note": note,
        "cents": cents,
        "contour": semitone_std(f0),
        "delta_f": resonance,
        "vtl": vocal_tract_length_cm(resonance) if resonance else None,
        "resonance_note": resonance_note,
        "formant_ceiling": ceiling,
        "h1h2": float(np.nanmedian(h1h2)) if h1h2 else np.nan,
        "cpp": float(np.nanmedian(cpps)) if cpps else np.nan,
        "ring": float(np.nanmedian(rings)) if rings else np.nan,
        "level": float(np.median(levels)) if levels else np.nan,
        "audio": audio,
        "tracks": {
            "times": times,
            "f0": f0,
            "f1": freqs[:, 0],
            "f2": freqs[:, 1],
            "f3": freqs[:, 2],
        },
    }
