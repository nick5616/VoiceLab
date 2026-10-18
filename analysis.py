"""Acoustic measurements shared by the live meters and the per-take report.

Metric choices follow the research summary in the README:
- Resonance uses formant dispersion (Delta F) over F1-F4, which tracks perceived
  resonance largely independently of pitch and weight (Netzorg et al., 2025).
- Weight has no reliable single acoustic correlate, so we report proxies:
  formant-corrected H1*-H2* (Iseli & Alwan), cepstral peak prominence and level.
- Pitch variation is measured in semitones so it is comparable across registers.
"""
from collections import deque

import numpy as np
import parselmouth
from parselmouth.praat import call

SAMPLE_RATE = 44100

PITCH_FLOOR, PITCH_CEILING = 60, 1200
# Above this F0, harmonics are too sparse for LPC to find the vocal tract
# resonances; it locks onto harmonics instead.
FORMANT_RELIABLE_MAX_F0 = 350
FORMANT_CEILINGS = np.arange(4500, 6501, 250)
DEFAULT_FORMANT_CEILING = 5500
# Adjacent real formants are rarely closer than this.
MIN_FORMANT_GAP = 250
# Real F1-F4 bandwidths stay well under this; wider "formants" are fitting artifacts.
MAX_FORMANT_BANDWIDTH = 600
# Median F1 over a take or a set of vowels stays well below this in speech and
# sung vowels below ~350 Hz pitch.
MAX_MEDIAN_F1 = 900
SPEED_OF_SOUND_CM = 35000

# Analysis frame for spectral measures (~46 ms at 44.1 kHz).
SPECTRAL_FRAME = 2048
# Live spectrum display: covers F1-F4 of most voices and the 2-4 kHz ring region.
SPECTRUM_MAX_HZ = 5500
# Voice-quality measures and SNR use only this band, where the harmonics carry
# most of the voice's energy; hiss and whine above it are ignored.
VOICE_BAND_HZ = 4000
# Below this level live frames skip analysis and report silence.
SILENCE_DBFS = -50
# Samples at or above this are treated as clipped (input overloaded).
CLIP_LEVEL = 0.99
# Live frames must be this far above the input's noise floor to be analysed as
# voice. Mains hum is genuinely periodic (pitch ~60 Hz), so on a mic with a high
# noise floor a fixed silence level alone lets hum and noise read as voice.
VOICE_MARGIN_DB = 8
# The noise floor drops at once to a quieter frame but rises only this fast,
# so a long held note doesn't become the "floor".
NOISE_FLOOR_RISE_DB_PER_S = 1.0
DIGITAL_SILENCE_DBFS = -100     # e.g. the empty buffer at startup: not a noise floor
# Until this much input has been heard the floor is unknown, so only the fixed
# silence level applies (speaking right at launch still registers).
NOISE_FLOOR_WARMUP_SECONDS = 0.5

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


def pitch_range_st(f0_values, min_voiced=20):
    """Intonation range: 5th-95th percentile pitch spread in semitones.
    Percentiles keep a single octave error or creak from inflating it."""
    f0 = np.asarray(f0_values, dtype=float)
    f0 = f0[~np.isnan(f0)]
    if len(f0) < min_voiced:
        return np.nan
    lo, hi = np.percentile(f0, [5, 95])
    return float(12 * np.log2(hi / lo))


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
    return float(20 * np.log10(rms + 1e-6))  # floors digital silence at -120 dBFS


def clip_events(audio):
    """Number of separate runs of clipped samples. Clipping squares off the
    waveform, adding high harmonics that inflate Ring and lower Clarity."""
    clipped = np.abs(np.asarray(audio, dtype=float)) >= CLIP_LEVEL
    return int(np.count_nonzero(clipped[1:] & ~clipped[:-1]) + (clipped[0] if len(clipped) else 0))


def _power_spectrum_db(frame, sample_rate, pad=1):
    n = len(frame)
    nfft = int(2 ** np.ceil(np.log2(n * pad)))
    spec = np.fft.rfft(frame * np.hanning(n), n=nfft)
    freqs = np.fft.rfftfreq(nfft, 1 / sample_rate)
    return freqs, 10 * np.log10(np.abs(spec) ** 2 + 1e-20)


def band_level_db(frame, sample_rate, max_hz=VOICE_BAND_HZ):
    """Level (dBFS) of the frame's content below max_hz."""
    freqs, power_db = _power_spectrum_db(frame, sample_rate)
    window_power = np.sum(np.hanning(len(frame)) ** 2)
    band = 10 ** (power_db[freqs <= max_hz] / 10)
    return float(10 * np.log10(2 * band.sum() / (len(frame) * window_power) + 1e-12))


def cpp(frame, sample_rate, f0):
    """Cepstral peak prominence (dB): height of the rahmonic peak near 1/f0
    above the regression line through the cepstrum. Higher = clearer, more
    periodic voice; lower = breathier or rougher.

    Computed over the voice band (0-VOICE_BAND_HZ) only, where the harmonics
    are strong: hiss or whine above it then barely moves the value (in tests at
    20 dB SNR, within ~1 dB instead of 4-6 dB low)."""
    if f0 is None or np.isnan(f0):
        return np.nan
    freqs, power_db = _power_spectrum_db(frame, sample_rate, pad=2)
    band = freqs <= VOICE_BAND_HZ
    # Power cepstrum in dB, as in Praat's PowerCepstrum, so values are comparable
    # with published CPP figures. Using only the band's bins is equivalent to
    # analysing at a sample rate of 2 x VOICE_BAND_HZ.
    ceps = 20 * np.log10(np.abs(np.fft.irfft(power_db[band])) + 1e-12)
    quef = np.arange(len(ceps)) / (2 * freqs[band][-1])
    ceps, quef = ceps[:len(ceps) // 2], quef[:len(ceps) // 2]

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


def ring_db(frame, sample_rate, f0):
    """Harmonic energy in 2-4 kHz (singer's formant / 'ring' region) relative
    to 0-2 kHz. Only the peaks at multiples of f0 are summed, so noise between
    the harmonics doesn't count as voice."""
    if f0 is None or np.isnan(f0):
        return np.nan
    freqs, power_db = _power_spectrum_db(frame, sample_rate, pad=4)
    power = 10 ** (power_db / 10)
    low = high = 0.0
    for k in range(1, int(VOICE_BAND_HZ // f0) + 1):
        band = (freqs >= k * f0 * 0.95) & (freqs <= k * f0 * 1.05)
        peak = power[band].max()
        if k * f0 < 2000:
            low += peak
        else:
            high += peak
    return float(10 * np.log10((high + 1e-20) / (low + 1e-20)))


# --- NOISE ---

# Below ~30 dB voice-to-noise ratio, voice-quality measures (CPP, H1-H2, HNR,
# jitter/shimmer) lose reliability; pitch and formants hold up better.
MIN_RELIABLE_SNR = 30
NOISE_PERCENTILE = 10   # quietest unvoiced frames = the pauses between phrases


def estimate_snr(levels, voiced, min_noise_frames=20, min_voice_frames=20):
    """Voice-to-noise ratio (dB) from per-frame levels: median voiced level over
    the level of the quietest unvoiced frames. None without enough pauses."""
    levels = np.asarray(levels, dtype=float)
    voiced = np.asarray(voiced, dtype=bool)
    if (~voiced).sum() < min_noise_frames or voiced.sum() < min_voice_frames:
        return None
    noise = np.percentile(levels[~voiced], NOISE_PERCENTILE)
    return float(np.median(levels[voiced]) - noise)


def frame_levels(audio, sample_rate, times, band=False):
    """dBFS level of a frame centred on each time (voice band only if `band`)."""
    half = SPECTRAL_FRAME // 2
    levels = []
    for c in (np.asarray(times) * sample_rate).astype(int):
        frame = audio[max(0, c - half):c + half]
        if len(frame) < SPECTRAL_FRAME:
            levels.append(np.nan)
        else:
            levels.append(band_level_db(frame, sample_rate) if band else level_dbfs(frame))
    return np.array(levels)


def snr_pair(full_levels, band_levels, voiced, **kwargs):
    """(voice-band SNR, full-band SNR); either may be None."""
    ok = ~np.isnan(full_levels) & ~np.isnan(band_levels)
    return (estimate_snr(band_levels[ok], voiced[ok], **kwargs),
            estimate_snr(full_levels[ok], voiced[ok], **kwargs))


class NoiseMonitor:
    """Rolling voice-to-noise ratio from live frames, in the voice band (what
    the measurements see) and full band (what you hear)."""

    def __init__(self, frames):
        self.levels = deque(maxlen=frames)
        self.band_levels = deque(maxlen=frames)
        self.voiced = deque(maxlen=frames)

    def add(self, frame):
        self.levels.append(frame["level"])
        self.band_levels.append(frame.get("band_level", frame["level"]))
        self.voiced.append(not np.isnan(frame["f0"]))

    def snr(self):
        """(voice-band SNR, full-band SNR) or (None, None)."""
        # Live frames come every ~25 ms: need ~1 s of pauses and ~0.5 s of voice.
        return snr_pair(np.array(self.levels), np.array(self.band_levels), np.array(self.voiced),
                        min_noise_frames=40, min_voice_frames=20)


# --- PRAAT WRAPPERS ---

def pitch_track(sound, time_step=0.01):
    # Track pitch on the voice band only: high-pitched hiss otherwise weakens the
    # periodicity Praat sees, so voiced frames get marked unvoiced (in tests, up
    # to 85% of them), and it also cuts octave-jump spikes.
    voice_band = call(sound, "Filter (pass Hann band)...", 0, VOICE_BAND_HZ, 100)
    pitch = voice_band.to_pitch_ac(time_step=time_step, pitch_floor=PITCH_FLOOR,
                                   pitch_ceiling=PITCH_CEILING)
    f0 = pitch.selected_array["frequency"].astype(float)
    f0[f0 == 0] = np.nan
    return pitch.xs(), f0


def formant_values(formant_obj, times):
    """Arrays of shape (len(times), 4) for F1-F4 and their bandwidths B1-B4."""
    freqs = np.array([[formant_obj.get_value_at_time(i, t) for i in range(1, 5)] for t in times])
    bws = np.array([[formant_obj.get_bandwidth_at_time(i, t) for i in range(1, 5)] for t in times])
    return freqs.reshape(-1, 4), bws.reshape(-1, 4)


def to_formants(sound, ceiling, time_step=0.01):
    return sound.to_formant_burg(time_step=time_step, max_number_of_formants=5,
                                 maximum_formant=ceiling, window_length=0.025)


def _ceiling_score(sound, times, ceiling):
    """(score, F1 values): lower score is better; penalizes invented, merged,
    missing and jumpy formants."""
    freqs, bws = formant_values(to_formants(sound, ceiling), times)
    spurious = np.sum(np.nanmedian(bws, axis=0) > MAX_FORMANT_BANDWIDTH)
    missing = np.isnan(freqs).mean()
    jumps = np.abs(np.diff(freqs, axis=0)) / np.nanmedian(freqs, axis=0)
    # A ceiling that is too low splits one resonance into two formants
    # sitting almost on top of each other.
    merged = np.mean(np.any(np.diff(freqs, axis=1) < MIN_FORMANT_GAP, axis=1))
    return spurious + np.nansum(np.nanmedian(jumps, axis=0)) + missing + merged, freqs[:, 0]


def choose_formant_ceiling(segments):
    """Pick the Burg ceiling that fits a speaker best over `segments`, a list of
    (sound, times) pairs scored jointly (e.g. one per vowel token).

    A fixed ceiling biases results as the speaker changes their vocal tract
    (e.g. 5000 Hz suits a large tract, 5500 Hz a smaller one), so we try a range
    and keep the one with the least frame-to-frame jitter, in the spirit of
    FastTrack (Barreda, 2021). A ceiling that is too low forces Praat to invent
    a formant to fill the band; those show up as very wide bandwidths.
    """
    segments = [(s, t) for s, t in segments if len(t) >= 3]
    if sum(len(t) for _, t in segments) < 10:
        return DEFAULT_FORMANT_CEILING
    best, best_score = DEFAULT_FORMANT_CEILING, np.inf
    for ceiling in FORMANT_CEILINGS:
        scores, f1s = zip(*(_ceiling_score(s, t, ceiling) for s, t in segments))
        # Small pull toward the default breaks ties on very steady vowels.
        prior = 1e-3 * abs(ceiling - DEFAULT_FORMANT_CEILING) / 1000
        score = sum(scores) / len(segments) + prior
        # A typical F1 this high means the real F1 was missed and every formant
        # slid up one slot (happens with very bright, tilted spectra).
        if np.nanmedian(np.concatenate(f1s)) > MAX_MEDIAN_F1:
            score += 1
        if score < best_score:
            best, best_score = int(ceiling), score
    return best


# --- LIVE FRAME ANALYSIS ---

class FrameAnalyzer:
    """Measures the most recent slice of audio for the live meters."""

    WINDOW_SECONDS = 0.12

    def __init__(self, sample_rate=SAMPLE_RATE, frame_interval=0.025):
        self.sample_rate = sample_rate
        self.formant_ceiling = DEFAULT_FORMANT_CEILING
        self.noise_floor = None       # voice-band dBFS
        self._floor_rise = NOISE_FLOOR_RISE_DB_PER_S * frame_interval
        self._warmup_frames = int(NOISE_FLOOR_WARMUP_SECONDS / frame_interval)
        freqs, _ = _power_spectrum_db(np.zeros(SPECTRAL_FRAME), sample_rate, pad=2)
        self._spectrum_bins = freqs <= SPECTRUM_MAX_HZ
        self.spectrum_freqs = freqs[self._spectrum_bins]

    def _above_noise(self, band_level):
        """Update the noise floor with this frame; True if it stands out
        from the floor enough to be voice."""
        if band_level <= DIGITAL_SILENCE_DBFS:
            return False
        if self.noise_floor is None or band_level < self.noise_floor:
            self.noise_floor = band_level
        else:
            self.noise_floor += self._floor_rise
        if self._warmup_frames > 0:       # floor not known yet
            self._warmup_frames -= 1
            return True
        return band_level >= self.noise_floor + VOICE_MARGIN_DB

    def spectrum(self, frame):
        """Power spectrum (dB) of the frame over 0-SPECTRUM_MAX_HZ."""
        _, power_db = _power_spectrum_db(frame, self.sample_rate, pad=2)
        return power_db[self._spectrum_bins]

    def analyze(self, window):
        frame = window[-SPECTRAL_FRAME:]
        result = {"level": level_dbfs(frame), "band_level": band_level_db(frame, self.sample_rate),
                  "clipped": bool(np.max(np.abs(frame), initial=0) >= CLIP_LEVEL),
                  "f0": np.nan, "delta_f": np.nan, "h1h2": np.nan, "cpp": np.nan, "ring": np.nan,
                  "formants": np.full(4, np.nan), "resonance_reliable": False,
                  "spectrum": self.spectrum(frame)}
        if not self._above_noise(result["band_level"]) or result["level"] < SILENCE_DBFS:
            return result

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
        result["ring"] = ring_db(frame, self.sample_rate, f0)
        result["formants"] = freqs
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
    ceiling = choose_formant_ceiling([(sound, times[reliable])])
    freqs, bws = formant_values(to_formants(sound, ceiling), times)
    df_track = delta_f(*freqs.T)
    df_track[~reliable] = np.nan

    h1h2, cpps, rings, levels = [], [], [], []
    half = SPECTRAL_FRAME // 2
    for t, f, fr, bw in zip(times[voiced], f0[voiced], freqs[voiced], bws[voiced]):
        center = int(t * sample_rate)
        frame = audio[max(0, center - half):center + half]
        if len(frame) < SPECTRAL_FRAME:
            continue
        h1h2.append(h1_h2_corrected(frame, sample_rate, f, fr, bw))
        cpps.append(cpp(frame, sample_rate, f))
        rings.append(ring_db(frame, sample_rate, f))
        levels.append(level_dbfs(frame))

    df_frames = df_track[~np.isnan(df_track)]
    if len(df_frames) >= 10:
        resonance, resonance_note = float(np.median(df_frames)), None
    else:
        resonance = None
        resonance_note = ("Pitch too high for reliable resonance measurement"
                          if voiced.sum() > reliable.sum() * 2 else
                          "Not enough clear formant data")

    median_f0 = float(np.nanmedian(f0))
    note, cents = hz_to_note(median_f0)
    snr, snr_full = snr_pair(frame_levels(audio, sample_rate, times),
                             frame_levels(audio, sample_rate, times, band=True), voiced)
    return {
        "snr": snr,              # voice band: what the measurements see
        "snr_full": snr_full,    # full band: includes hiss above the voice band
        "clip_events": clip_events(audio),
        "singing": singing_stats(times, f0),
        "pitch": median_f0,
        "note": note,
        "cents": cents,
        "contour": semitone_std(f0),
        "pitch_range": pitch_range_st(f0),
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
            "delta_f": df_track,
        },
    }


# --- SINGING ---

NOTE_MIN_SECONDS = 0.4
HELD_NOTE_SECONDS = 0.6
NOTE_CHANGE_CENTS = 80        # smoothed pitch moving this far starts a new note
VIBRATO_BAND = (3.5, 9.0)     # Hz; typical vibrato rates are ~4.5-7 Hz
MIN_VIBRATO_CENTS = 10
TREND_SECONDS = 0.3           # averaging window separating slow drift from vibrato
EXERCISE_SETTLE_SECONDS = 0.15
IN_TUNE_CENTS = 25


def hz_to_midi(f0):
    return 69 + 12 * np.log2(np.asarray(f0, dtype=float) / 440.0)


def midi_to_hz(midi):
    return 440.0 * 2 ** ((np.asarray(midi, dtype=float) - 69) / 12)


def _moving_average(x, n):
    n = max(1, int(round(n)))
    padded = np.pad(x, (n // 2, n - 1 - n // 2), mode="edge")
    return np.convolve(padded, np.ones(n) / n, mode="valid")


def _voiced_runs(f0):
    voiced = np.append(~np.isnan(f0), False)
    runs, start = [], None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    return runs


def segment_notes(f0, frame_rate, min_seconds=NOTE_MIN_SECONDS):
    """(start, end) frame ranges of sung notes: voiced runs, split wherever the
    smoothed pitch moves more than NOTE_CHANGE_CENTS from the note so far.
    The ~0.2 s smoothing averages vibrato away so it doesn't split notes."""
    notes = []
    min_frames = min_seconds * frame_rate
    for run_start, run_end in _voiced_runs(f0):
        cents = 100 * hz_to_midi(f0[run_start:run_end])
        smooth = _moving_average(cents, 0.2 * frame_rate)
        start, total = 0, smooth[0]
        for i in range(1, len(smooth)):
            if abs(smooth[i] - total / (i - start)) > NOTE_CHANGE_CENTS:
                if i - start >= min_frames:
                    notes.append((run_start + start, run_start + i))
                start, total = i, 0.0
            total += smooth[i]
        if len(smooth) - start >= min_frames:
            notes.append((run_start + start, run_end))
    return notes


def vibrato_stats(cents, frame_rate):
    """Vibrato in a held note's pitch (in cents): rate (Hz), extent (+/- cents),
    regularity (cycle-length variation, lower = steadier) and drift (slow
    wandering, std in cents). Vibrato fields are None when there is none."""
    trend = _moving_average(cents, TREND_SECONDS * frame_rate)
    trim = int(TREND_SECONDS * frame_rate / 2)
    detrended = (cents - trend)[trim:len(cents) - trim]
    trend = trend[trim:len(cents) - trim]
    result = {"rate": None, "extent": None, "regularity": None,
              "drift": float(np.std(trend)) if len(trend) else np.nan}
    if len(detrended) < frame_rate * 0.5:
        return result

    nfft = 4096
    spec = np.abs(np.fft.rfft(detrended * np.hanning(len(detrended)), nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1 / frame_rate)
    band = (freqs >= VIBRATO_BAND[0]) & (freqs <= VIBRATO_BAND[1])
    in_band = spec[band].sum() / (spec[freqs >= 1.0].sum() + 1e-12)
    extent = float(np.sqrt(2) * np.std(detrended))   # amplitude of a sinusoid
    if in_band < 0.5 or extent < MIN_VIBRATO_CENTS:
        return result

    # Upward zero crossings give individual cycle lengths.
    s = detrended
    idx = np.flatnonzero((s[:-1] < 0) & (s[1:] >= 0))
    crossings = idx + s[idx] / (s[idx] - s[idx + 1])
    periods = np.diff(crossings) / frame_rate
    if len(periods) >= 2:
        rate = 1 / np.mean(periods)
        regularity = float(np.std(periods) / np.mean(periods))
    else:
        rate, regularity = freqs[band][np.argmax(spec[band])], None
    result.update(rate=float(rate), extent=extent, regularity=regularity)
    return result


def singing_stats(times, f0):
    """Summary of sung notes in a take: note count, vibrato on held notes,
    sustain drift and tuning relative to A440 equal temperament."""
    if len(times) < 2:
        return None
    frame_rate = 1 / (times[1] - times[0])
    notes = segment_notes(f0, frame_rate)
    if not notes:
        return None
    offsets, held = [], []
    for a, b in notes:
        midi = hz_to_midi(f0[a:b])
        median = np.median(midi)
        offsets.append(100 * (median - np.round(median)))
        if (b - a) / frame_rate >= HELD_NOTE_SECONDS:
            held.append(vibrato_stats(100 * midi, frame_rate))

    def median_of(key):
        values = [h[key] for h in held if h[key] is not None and not np.isnan(h[key])]
        return float(np.median(values)) if values else None

    return {
        "notes": len(notes),
        "held_notes": len(held),
        "vibrato_notes": sum(h["rate"] is not None for h in held),
        "vibrato_rate": median_of("rate"),
        "vibrato_extent": median_of("extent"),
        "vibrato_regularity": median_of("regularity"),
        "drift": median_of("drift"),
        "tuning_offset": float(np.median(np.abs(offsets))),
    }


def score_exercise(times, f0, targets):
    """Score a take against target notes [(start_s, duration_s, midi), ...].

    Each note is judged after a short settle time. Errors are folded to the
    nearest octave, so singing the melody an octave down still scores."""
    notes = []
    for start, duration, midi in targets:
        window = ((times >= start + EXERCISE_SETTLE_SECONDS) & (times < start + duration)
                  & ~np.isnan(f0))
        if window.sum() < 3:
            notes.append({"midi": int(midi), "cents": None, "in_tune": 0.0, "octave": 0})
            continue
        cents = 100 * (hz_to_midi(f0[window]) - midi)
        octave = int(np.round(np.median(cents) / 1200))
        cents = cents - 1200 * octave
        notes.append({"midi": int(midi), "cents": float(np.median(cents)), "octave": octave,
                      "in_tune": float(np.mean(np.abs(cents) <= IN_TUNE_CENTS))})
    sung = [n for n in notes if n["cents"] is not None]
    return {
        "notes": notes,
        "notes_total": len(notes),
        "notes_sung": len(sung),
        "notes_in_tune": sum(abs(n["cents"]) <= IN_TUNE_CENTS for n in sung),
        "mean_abs_cents": float(np.mean([abs(n["cents"]) for n in sung])) if sung else None,
        "time_in_tune": float(np.mean([n["in_tune"] for n in notes])) if notes else None,
    }


# --- VOWEL CHECK ---

# hVd words isolate one vowel each between /h/ and /d/ (Hillenbrand et al., 1995).
VOWEL_WORDS = ["heed", "hid", "head", "had", "hod", "hud", "hood", "who'd"]
MIN_NUCLEUS_FRAMES = 8  # 80 ms of voicing


def vowel_nucleus(times, f0):
    """Times in the steady middle (30-70%) of the longest voiced run, which in
    an hVd word is the vowel."""
    voiced = ~np.isnan(f0)
    best_start, best_len, start = 0, 0, None
    for i, v in enumerate(np.append(voiced, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > best_len:
                best_start, best_len = start, i - start
            start = None
    if best_len < MIN_NUCLEUS_FRAMES:
        return None
    a = best_start + int(0.3 * best_len)
    b = best_start + max(int(0.7 * best_len), int(0.3 * best_len) + 3)
    return times[a:b], float(np.nanmedian(f0[a:b]))


def analyze_vowels(tokens, sample_rate=SAMPLE_RATE):
    """Formants per vowel from {word: audio}. The formant ceiling is chosen
    jointly over all tokens, as one speaker's vocal tract fits them all.

    Returns per-word F1-F4 and Delta F, plus the vowel-balanced mean Delta F,
    which removes the effect of which vowels happened to be spoken."""
    segments, skipped = {}, []
    for word, audio in tokens.items():
        sound = parselmouth.Sound(np.asarray(audio, dtype=float).flatten(),
                                  sampling_frequency=sample_rate)
        times, f0 = pitch_track(sound)
        nucleus = vowel_nucleus(times, f0)
        if nucleus is None or nucleus[1] > FORMANT_RELIABLE_MAX_F0:
            skipped.append(word)
            continue
        segments[word] = (sound, nucleus[0], nucleus[1])

    ceiling = choose_formant_ceiling([(s, t) for s, t, _ in segments.values()])
    vowels = {}
    for word, (sound, nucleus_times, f0) in segments.items():
        freqs, _ = formant_values(to_formants(sound, ceiling), nucleus_times)
        f1, f2, f3, f4 = (float(v) for v in np.nanmedian(freqs, axis=0))
        df = float(delta_f(f1, f2, f3, f4))
        if np.isnan(df):
            skipped.append(word)
            continue
        vowels[word] = {"f0": f0, "f1": f1, "f2": f2, "f3": f3, "f4": f4, "delta_f": df}

    mean_df = float(np.mean([v["delta_f"] for v in vowels.values()])) if vowels else None
    return {"vowels": vowels, "skipped": [w for w in VOWEL_WORDS if w in skipped],
            "delta_f": mean_df, "formant_ceiling": ceiling}
