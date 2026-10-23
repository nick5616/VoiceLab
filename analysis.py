"""Acoustic measurements shared by the live meters and the per-take report.

Metric choices follow the research summary in the README:
- Resonance uses formant dispersion (Delta F) over F1-F4, which tracks perceived
  resonance largely independently of pitch and weight (Netzorg et al., 2025).
- Weight has no reliable single acoustic correlate, so we report proxies:
  formant-corrected H1*-H2* (Iseli & Alwan), cepstral peak prominence and level.
- Pitch variation is measured in semitones so it is comparable across registers.
"""
import warnings
from collections import deque

import numpy as np
import parselmouth
from parselmouth.praat import call
from scipy.ndimage import median_filter

import denoise

SAMPLE_RATE = 44100

PITCH_FLOOR, PITCH_CEILING = 60, 1200
# Above this F0, harmonics are too sparse for LPC to find the vocal tract
# resonances; it locks onto harmonics instead. Own measurement: synthetic vowels
# with known formants read F1 within 10% up to 220 Hz, 13% of cases are worse at
# 230 Hz and 40% at 250 Hz (errors up to +31% at 260 Hz); the old 350 Hz was far too generous.
F1_RELIABLE_MAX_F0 = 230
# register.py still reads this older, looser limit for its own weighting; it was not retuned here.
FORMANT_RELIABLE_MAX_F0 = 350
FORMANT_CEILINGS = np.arange(4000, 6501, 250)
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
    """Intonation range: 10th-90th percentile pitch spread in semitones of the
    gated track. Percentiles keep a stray octave error or creak from inflating
    it; real speech spans 4-28 st this way (audit), ghosts made it 44-49."""
    f0 = np.asarray(f0_values, dtype=float)
    f0 = f0[~np.isnan(f0)]
    if len(f0) < min_voiced:
        return np.nan
    lo, hi = np.percentile(f0, [10, 90])
    return float(12 * np.log2(hi / lo))


# --- RESONANCE ---

def delta_f(f1, f2, f3, f4):
    """Formant dispersion: mean of Fi / (i - 0.5). Works on scalars or arrays."""
    return (f1 / 0.5 + f2 / 1.5 + f3 / 2.5 + f4 / 3.5) / 4


def vocal_tract_length_cm(df):
    """Apparent vocal tract length implied by Delta F (uniform tube model). Not a measured
    length: ΔF also moves with the vowel (F1/F2 vary ±33% across vowels), lip rounding and
    larynx height, so use it as a trend between takes, not as an anatomical fact."""
    return SPEED_OF_SOUND_CM / (2 * df)


ANALYSIS_VERSION = 10          # stored on every take result so older takes can be re-analysed (10: phase 10)
F1_MIN_HZ = 250                # an "F1" below this is rumble or H1, not a resonance (audit: snapped F1 median 246 Hz)
F1_F0_RATIO = 1.3              # ... and one closer than this to f0 has snapped onto H1 (audit: 43-44% of frames at 202-212 Hz)
NUCLEUS_STEADY = 0.15          # a steady frame's F1 and F2 move less than this over +/-1 frame
NUCLEUS_RUN_FRAMES = 3         # shortest steady stretch kept (30 ms)
NUCLEUS_LOUD_PERCENTILE = 25   # steady frames are above this percentile of usable frames' level
# The three above are own choices: on the user's takes only 0-112 frames survive at +/-2 frames,
# 10%, 4-frame runs and the median level, against 6-468 at these values; real formant tracks move constantly.
NUCLEUS_MIN_FRAMES = 10        # fewer steady frames than this and the take falls back to all clear frames
DF_OUTLIER_HZ = 300             # ΔF this far from the take median is a tracker error (vowels span ~±150, audit)
VOWEL_BOX_F1, VOWEL_BOX_F2 = 0.10, 0.08   # frames this close in F1 and F2 (relative) are the same vowel (own choice)


def f1_missing(f1, f0):
    """True where F1 is missing or not a believable vocal-tract resonance: absent,
    below F1_MIN_HZ, within F1_F0_RATIO of f0 (snapped onto the first harmonic), or
    when f0 is above F1_RELIABLE_MAX_F0 (harmonics too sparse for LPC)."""
    f1, f0 = np.asarray(f1, dtype=float), np.asarray(f0, dtype=float)
    with np.errstate(invalid="ignore"):
        return ~((f1 >= np.maximum(F1_MIN_HZ, F1_F0_RATIO * f0)) & (f0 <= F1_RELIABLE_MAX_F0))


def steady_vowel_frames(freqs, levels, usable):
    """Mask of loud, steady frames, where the vowel is held rather than moving between sounds:
    usable, level above the NUCLEUS_LOUD_PERCENTILE of usable frames' levels, and F1 and F2
    within NUCLEUS_STEADY of their values 1 frame earlier and later. Steady runs shorter than
    NUCLEUS_RUN_FRAMES go."""
    usable = np.asarray(usable, bool)
    if not usable.any():
        return usable.copy()
    loud = np.asarray(levels) >= np.percentile(np.asarray(levels)[usable], NUCLEUS_LOUD_PERCENTILE)
    steady = np.zeros(len(freqs), bool)
    if len(freqs) > 2:
        with np.errstate(invalid="ignore"):
            moves = np.maximum(*(np.abs(freqs[2:, i] - freqs[:-2, i]) / freqs[1:-1, i] for i in (0, 1)))
        steady[1:-1] = moves < NUCLEUS_STEADY
    mask = usable & loud & steady
    keep = np.zeros(len(mask), bool)
    for a, b in _mask_runs(mask):
        if b - a >= NUCLEUS_RUN_FRAMES:
            keep[a:b] = True
    return keep


def _mask_runs(mask):
    """[(start, stop)] of the True runs in a bool mask."""
    edges = np.diff(np.concatenate([[0], np.asarray(mask, int), [0]]))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def vowel_balanced_delta_f(freqs, df_track, mask):
    """Weighted mean ΔF over the masked frames in which every vowel counts once, however long
    it is held or how often it occurs: each frame is weighted by 1 / the number of masked frames
    within VOWEL_BOX of its F1 and F2, so a crowded vowel shares one vote. This removes most of
    what the passage's vowel mix does to ΔF (up to ~90 Hz, audit). Frames more than
    DF_OUTLIER_HZ from the median are tracker errors and dropped. NaN with no usable frames."""
    index = np.flatnonzero(mask & ~np.isnan(df_track))
    if len(index):
        index = index[np.abs(df_track[index] - np.median(df_track[index])) <= DF_OUTLIER_HZ]
    if not len(index):
        return np.nan
    f1, f2, df = freqs[index, 0], freqs[index, 1], df_track[index]
    near = ((np.abs(f1[:, None] / f1[None, :] - 1) < VOWEL_BOX_F1)
            & (np.abs(f2[:, None] / f2[None, :] - 1) < VOWEL_BOX_F2))
    return float(np.average(df, weights=1 / near.sum(axis=1)))


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
    """Per-frame cepstral peak prominence (dB) for the LIVE meter only: height of the
    rahmonic peak near 1/f0 above the regression line through one 46 ms frame's cepstrum.
    Higher = clearer, more periodic voice; lower = breathier or rougher.

    NOT comparable with published CPP/CPPS: it is unsmoothed and band-limited, and on the
    user's takes it reads 12.6-16.2 dB where Praat's CPPS reads 4.6-5.9 (audit). Use `cpps`
    for any number that is compared with the literature or tracked across takes.

    Computed over the voice band (0-VOICE_BAND_HZ) only, where the harmonics
    are strong: hiss or whine above it then barely moves the value, though at the user's
    ~16 dB SNR it still reads 4-5 dB low (audit)."""
    if f0 is None or np.isnan(f0):
        return np.nan
    freqs, power_db = _power_spectrum_db(frame, sample_rate, pad=2)
    band = freqs <= VOICE_BAND_HZ
    # Power cepstrum in dB, as in Praat's PowerCepstrum (but unsmoothed, so not CPPS).
    # Using only the band's bins is equivalent to analysing at a sample rate of 2 x VOICE_BAND_HZ.
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


# Praat "Get CPPS" settings of Maryn & Weenink (2015), after Hillenbrand & Houde (1996):
# cepstrogram 60 Hz / 2 ms / 5 kHz / pre-emphasis 50 Hz; smoothing 0.02 s x 0.0005 s;
# peak search 60-330 Hz, tolerance 0.05, parabolic; tilt line 0.001 s to end, straight, robust.
CPPS_PITCH_FLOOR, CPPS_TIME_STEP, CPPS_MAX_FREQ, CPPS_PREEMPHASIS = 60, 0.002, 5000, 50
CPPS_SEARCH_HZ = (60, 330)
CPPS_MAX_SECONDS = 6            # voiced audio used at most, in chunks spread over the take: ~0.12 s of Praat per second of audio
CPPS_CHUNK_SECONDS = 0.5
CPPS_MIN_SECONDS = 0.3          # less voiced audio than this gives no stable CPPS


def cpps(audio, sample_rate, voiced=None, times=None):
    """Smoothed cepstral peak prominence (dB) as Praat computes it (`PowerCepstrogram` +
    `Get CPPS...` with the settings above), so it can be compared with published CPPS.
    Over voiced stretches only (`voiced` bool per frame of `times`; None = whole audio), joined,
    because pauses and noise would otherwise pull the average down. Higher = clearer, more
    periodic phonation; lower = breathier or rougher. It is lowered by background noise and
    room reverb as well as by voice quality, and speech reads lower than a sustained vowel, so
    compare takes of the same kind, in the same room. NaN when under CPPS_MIN_SECONDS is voiced."""
    audio = np.asarray(audio, dtype=float)
    if voiced is not None:
        step = float(np.median(np.diff(times))) if len(times) > 1 else 0.01
        pieces = [audio[max(0, int((times[a] - step / 2) * sample_rate)):int((times[b - 1] + step / 2) * sample_rate)]
                  for a, b in _mask_runs(voiced)]
        chunk = int(CPPS_CHUNK_SECONDS * sample_rate)
        pieces = [piece[i:i + chunk] for piece in pieces for i in range(0, len(piece), chunk)]
        pieces = [pieces[i] for i in np.unique(np.linspace(0, len(pieces) - 1, int(CPPS_MAX_SECONDS / CPPS_CHUNK_SECONDS)).astype(int))]             if len(pieces) * CPPS_CHUNK_SECONDS > CPPS_MAX_SECONDS else pieces
        audio = np.concatenate(pieces) if pieces else np.zeros(0)
    if len(audio) < CPPS_MIN_SECONDS * sample_rate or np.max(np.abs(audio)) < 1e-6:
        return np.nan
    sound = parselmouth.Sound(audio, sampling_frequency=sample_rate)
    cepstrogram = call(sound, "To PowerCepstrogram...", CPPS_PITCH_FLOOR, CPPS_TIME_STEP,
                       CPPS_MAX_FREQ, CPPS_PREEMPHASIS)
    return float(call(cepstrogram, "Get CPPS...", "no", 0.02, 0.0005, *CPPS_SEARCH_HZ, 0.05,
                      "Parabolic", 0.001, 0, "Straight", "Robust"))


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
    from F1/F2 removed (Iseli & Alwan). Higher = lighter / less vocal fold contact; lower =
    heavier / more pressed. A relative trend within one speaker and mic, not an absolute weight.

    The correction needs a real F1: where `formants[0]` is NaN (see `f1_missing`) the F1 term
    is skipped rather than subtracting a snapped F1 (which took ~14 dB off, audit), so
    take-level callers should still drop such frames."""
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
    the harmonics doesn't count as voice.

    Unvalidated for speech: it falls 4-11 dB with room reverb (audit) and has no established
    meaning for feminine speech, so treat it as a spectral-tilt trend within one room."""
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

# Per-mode tracker settings. Measured against a harvest + CREPE consensus on the
# user's real speech takes (2169 reference frames, 3390 silent): floor 75 /
# ceiling 500 / octave cost 0.1 halves 2.6% of frames and reads 0.2% of silence
# as voiced, against 21.2% and 15.7% for 60 / 1200. Sing mode has no real-take
# reference; its ceiling covers soprano notes above 1200 Hz that used to read an
# octave low, and its higher octave cost resists sub-octave jumps on breathy lows.
PITCH_MODES = {
    "speak": {"floor": 75, "ceiling": 500, "octave_cost": 0.1, "voicing": 0.45},
    "sing": {"floor": 60, "ceiling": 1500, "octave_cost": 0.12, "voicing": 0.45},
}
# Lower edge of the tracking band. Own measurement on the 7 real takes: 70 Hz
# agrees with the harvest + CREPE consensus on 93.7% of frames vs 93.0% with none,
# and halving on breathy synthetic voices is 0% either way.
PITCH_HIGHPASS_HZ = 70


def _praat_pitch(sound, time_step, mode):
    """(times, f0 with NaN unvoiced, autocorrelation strength) from Praat."""
    cfg = PITCH_MODES[mode]
    # Track on the voice band only: high-pitched hiss otherwise weakens the
    # periodicity Praat sees, so voiced frames get marked unvoiced (in tests, up
    # to 85% of them), and it also cuts octave-jump spikes.
    voice_band = call(sound, "Filter (pass Hann band)...", PITCH_HIGHPASS_HZ, VOICE_BAND_HZ, 100)
    pitch = voice_band.to_pitch_ac(time_step=time_step, pitch_floor=cfg["floor"],
                                   pitch_ceiling=cfg["ceiling"], voicing_threshold=cfg["voicing"],
                                   octave_cost=cfg["octave_cost"])
    f0 = pitch.selected_array["frequency"].astype(float)
    strength = pitch.selected_array["strength"].astype(float)
    f0[f0 == 0] = np.nan
    return pitch.xs(), f0, strength


def pitch_track(sound, time_step=0.01, mode="speak"):
    """(times, f0) with NaN where unvoiced; ungated, see `gated_pitch` for takes."""
    times, f0, _ = _praat_pitch(sound, time_step, mode)
    return times, f0


def formant_values(formant_obj, times):
    """Arrays of shape (len(times), 4) for F1-F4 and their bandwidths B1-B4."""
    freqs = np.array([[formant_obj.get_value_at_time(i, t) for i in range(1, 5)] for t in times])
    bws = np.array([[formant_obj.get_bandwidth_at_time(i, t) for i in range(1, 5)] for t in times])
    return freqs.reshape(-1, 4), bws.reshape(-1, 4)


def to_formants(sound, ceiling, time_step=0.01):
    return sound.to_formant_burg(time_step=time_step, max_number_of_formants=5,
                                 maximum_formant=ceiling, window_length=0.025)


CEILING_PLATEAU = 0.1       # ceilings scoring this close to the best are equally good (own measurement)
CEILING_MAX_FRAMES = 400    # frames scored per segment: more only costs time (a 17 s take is ~1700 frames)
CEILING_MARGIN = 0.05       # s of audio around each run, Praat's Burg window is 2 x 25 ms


def _excerpt_formants(sound, times, ceiling):
    """(times, freqs, bandwidths) at `times` computed on excerpts around each run of consecutive
    frames rather than the whole sound, which is ~8x faster for a take's steady vowel frames.
    Beyond CEILING_MAX_FRAMES every n-th run is kept (and its times returned)."""
    edges = np.concatenate([[0], np.flatnonzero(np.diff(times) > 1.5 * 0.01) + 1, [len(times)]])
    runs = list(zip(edges[:-1], edges[1:]))
    runs = runs[::max(1, int(np.ceil(len(times) / CEILING_MAX_FRAMES)))]
    kept, freqs, bws = [], [], []
    for a, b in runs:
        part = sound.extract_part(from_time=max(sound.xmin, times[a] - CEILING_MARGIN),
                                  to_time=min(sound.xmax, times[b - 1] + CEILING_MARGIN), preserve_times=True)
        f, w = formant_values(to_formants(part, ceiling), times[a:b])
        kept.append(np.arange(a, b)), freqs.append(f), bws.append(w)
    return np.concatenate(kept), np.concatenate(freqs), np.concatenate(bws)


def _ceiling_score(sound, times, ceiling, f0=None):
    """(score, F1 values): lower score is better; penalizes invented, merged,
    missing, snapped (F1 on H1 or rumble, when `f0` per frame is given) and jumpy formants.
    `times` may skip frames; jumps are only counted between neighbouring frames."""
    kept, freqs, bws = _excerpt_formants(sound, times, ceiling)
    times = times[kept]
    f0 = f0 if f0 is None or np.ndim(f0) == 0 else np.asarray(f0)[kept]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN columns when a ceiling finds nothing
        spurious = np.sum(np.nanmedian(bws, axis=0) > MAX_FORMANT_BANDWIDTH)
        jumps = np.abs(np.diff(freqs, axis=0)) / np.nanmedian(freqs, axis=0)
        jumps[np.diff(times) > 1.5 * 0.01] = np.nan   # between runs
        jump_score = np.nansum(np.nanmedian(jumps, axis=0))
    missing = np.isnan(freqs).mean()
    # A ceiling that is too low splits one resonance into two formants
    # sitting almost on top of each other.
    merged = np.mean(np.any(np.diff(freqs, axis=1) < MIN_FORMANT_GAP, axis=1))
    snapped = 0.0 if f0 is None else float(np.mean(f1_missing(freqs[:, 0], f0)))
    return spurious + jump_score + missing + merged + snapped, freqs[:, 0]


def choose_formant_ceiling(segments):
    """Pick the Burg ceiling that fits a speaker best over `segments`, a list of
    (sound, times) or (sound, times, f0) tuples scored jointly (e.g. one per vowel token;
    f0 is a Hz value or one per time, and lets snapped F1s count against a ceiling).

    A fixed ceiling biases results as the speaker changes their vocal tract
    (e.g. 5000 Hz suits a large tract, 5500 Hz a smaller one), so we try a range
    and keep the one with the least frame-to-frame jitter, in the spirit of
    FastTrack (Barreda, 2021). A ceiling that is too low forces Praat to invent
    a formant to fill the band; those show up as very wide bandwidths.

    Fed one short take the choice is unstable (adjacent takes picked 5500 and 4500 Hz,
    audit); pool takes with `choose_profile_ceiling` and pass the result as `ceiling`.
    """
    segments = [seg for seg in segments if len(seg[1]) >= 3]
    if sum(len(seg[1]) for seg in segments) < 10:
        return DEFAULT_FORMANT_CEILING
    scored = []
    for ceiling in FORMANT_CEILINGS:
        scores, f1s = zip(*(_ceiling_score(seg[0], seg[1], ceiling, *seg[2:]) for seg in segments))
        # Small pull toward the default breaks ties on very steady vowels.
        prior = 1e-3 * abs(ceiling - DEFAULT_FORMANT_CEILING) / 1000
        score = sum(scores) / len(segments) + prior
        # A typical F1 this high means the real F1 was missed and every formant
        # slid up one slot (happens with very bright, tilted spectra).
        if np.nanmedian(np.concatenate(f1s)) > MAX_MEDIAN_F1:
            score += 1
        scored.append(score)
    # Scores of ceilings that all fit are within noise of each other (0.06-0.2 on the user's takes,
    # while a spurious formant adds 1), so take the middle of the plateau, not its noisy minimum.
    plateau = FORMANT_CEILINGS[np.array(scored) <= min(scored) + CEILING_PLATEAU]
    return int(FORMANT_CEILINGS[np.argmin(np.abs(FORMANT_CEILINGS - np.median(plateau)))])


# --- OFFLINE PITCH ---

GATE_STRENGTH = 0.6           # Praat strength: ghosts 0.57-0.71 vs voice 0.85-0.9 (audit)
GATE_MARGIN_DB = VOICE_MARGIN_DB   # voice-band level over the take's quietest-decile level
GATE_MIN_RUN = 5              # frames (50 ms); shorter voiced runs are noise
MEDIAN_FRAMES = 5             # f0 median within a run
HARVEST_RATE = 16000          # harvest runs on a decimated copy: ~3x faster, same f0
OCTAVE_WINDOW_ST = 1.5        # Praat vs harvest closer than this to 12 st count as an octave error


def _runs(mask):
    """(start, stop) of each run of True values."""
    edges = np.flatnonzero(np.diff(np.concatenate([[0], np.asarray(mask, dtype=int), [0]])))
    return list(zip(edges[::2], edges[1::2]))


def _harvest_f0(audio, sample_rate, times, mode):
    """WORLD harvest f0 interpolated onto `times`; NaN where harvest is unvoiced."""
    import pyworld as pw
    from scipy.signal import resample_poly
    cfg = PITCH_MODES[mode]
    x = resample_poly(audio, HARVEST_RATE, sample_rate) if sample_rate != HARVEST_RATE else audio
    x = np.ascontiguousarray(x, dtype=np.float64)
    f0, t = pw.harvest(x, HARVEST_RATE, f0_floor=cfg["floor"], f0_ceil=cfg["ceiling"], frame_period=10.0)
    f0[f0 == 0] = np.nan
    return np.interp(times, t, f0, left=np.nan, right=np.nan)


def gated_pitch(audio, sample_rate=SAMPLE_RATE, mode="speak"):
    """Take-level pitch track with the ghosts removed.

    Praat proposes f0 (and its strength); harvest fixes octave errors; frames
    are then dropped unless strong enough, loud enough over the take's own noise
    floor (voice band), and part of a run. Returns a dict: times, f0 (NaN where
    gated out), voiced_fraction of all frames, ghost_fraction (share of the
    frames Praat called voiced that the gate removed).
    """
    audio = np.asarray(audio, dtype=float).flatten()
    sound = parselmouth.Sound(audio, sampling_frequency=sample_rate)
    times, f0, strength = _praat_pitch(sound, 0.01, mode)
    proposed = ~np.isnan(f0)

    harvest = _harvest_f0(audio, sample_rate, times, mode)
    both = proposed & ~np.isnan(harvest)
    off = np.full(len(f0), np.nan)
    off[both] = 12 * np.log2(f0[both] / harvest[both])
    octave = both & (np.abs(np.abs(off) - 12) < OCTAVE_WINDOW_ST)
    f0 = np.where(octave, harvest, f0)

    band = frame_levels(audio, sample_rate, times, band=True)
    ok = proposed & (strength >= GATE_STRENGTH) & ~np.isnan(band)
    if ok.any():
        floor = np.nanpercentile(band, NOISE_PERCENTILE)
        # A take with no pauses has no noise to measure: never ask for more than
        # a little under the level of the clearly voiced frames.
        loud = np.median(band[ok & (strength >= 0.8)]) if (ok & (strength >= 0.8)).any() else np.inf
        ok &= band >= min(floor + GATE_MARGIN_DB, loud - 6)
    for start, stop in _runs(ok):
        if stop - start < GATE_MIN_RUN:
            ok[start:stop] = False
    f0 = np.where(ok, f0, np.nan)
    for start, stop in _runs(ok):
        f0[start:stop] = median_filter(f0[start:stop], size=MEDIAN_FRAMES, mode="nearest")
    return {"times": times, "f0": f0,
            "voiced_fraction": float(ok.mean()),
            "ghost_fraction": float((proposed & ~ok).sum() / max(proposed.sum(), 1))}


# --- LIVE FRAME ANALYSIS ---

LIVE_HOLD_MARGIN_DB = 4       # while voiced, a frame needs only this much over the floor
LIVE_JUMP_CENTS = 600         # a bigger jump must be confirmed by recent frames
LIVE_JUMP_AGREE = 3           # of the last 5 candidates within LIVE_JUMP_CENTS / 2
LIVE_HISTORY = 5
DEFAULT_NOISE_FLOOR_DBFS = -60   # ceiling on the first learned floor (quiet room; the user's mic sits near -58)


class FrameAnalyzer:
    """Measures the most recent slice of audio for the live meters."""

    WINDOW_SECONDS = 0.12

    def __init__(self, sample_rate=SAMPLE_RATE, frame_interval=0.025, mode="speak", noise_floor=None):
        """`noise_floor` seeds the voice-band floor (dBFS), e.g. from the last session."""
        self.sample_rate = sample_rate
        self.mode = mode
        self.formant_ceiling = DEFAULT_FORMANT_CEILING
        self.noise_floor = noise_floor      # voice-band dBFS; None until a pause is heard
        self._floor_rise = NOISE_FLOOR_RISE_DB_PER_S * frame_interval
        self._voiced, self._last_f0 = False, np.nan   # previous frame reported a pitch
        self._candidates = deque(maxlen=LIVE_HISTORY)
        freqs, _ = _power_spectrum_db(np.zeros(SPECTRAL_FRAME), sample_rate, pad=2)
        self._spectrum_bins = freqs <= SPECTRUM_MAX_HZ
        self.spectrum_freqs = freqs[self._spectrum_bins]

    def _above_noise(self, band_level):
        """True if this frame stands out from the floor enough to be voice. With
        no floor yet, the pitch tracker alone decides (voice may start at t=0)."""
        if band_level <= DIGITAL_SILENCE_DBFS:
            return False
        if self.noise_floor is None:
            return True
        self.noise_floor = min(self.noise_floor, band_level)
        margin = LIVE_HOLD_MARGIN_DB if self._voiced else VOICE_MARGIN_DB
        return band_level >= self.noise_floor + margin

    def _end_frame(self, band_level, f0):
        """Track the floor from frames the pitch tracker called unvoiced: it
        drops at once to a quieter frame but rises slowly, and never while the
        previous frame was voiced, so a long held note doesn't become the floor."""
        if np.isnan(f0):
            if band_level > DIGITAL_SILENCE_DBFS:
                if self.noise_floor is None:
                    # Capped: the first "unvoiced" frame may be the start of a note.
                    self.noise_floor = min(band_level, DEFAULT_NOISE_FLOOR_DBFS)
                elif not self._voiced and band_level > self.noise_floor:
                    self.noise_floor += self._floor_rise
            self._candidates.clear()
        self._voiced, self._last_f0 = not np.isnan(f0), f0

    def _confirmed(self, f0):
        """False for a jump of more than LIVE_JUMP_CENTS that recent frames
        don't back: a pitch step halves for a single frame."""
        self._candidates.append(f0)
        if not self._voiced or abs(1200 * np.log2(f0 / self._last_f0)) <= LIVE_JUMP_CENTS:
            return True
        near = sum(abs(1200 * np.log2(f0 / c)) <= LIVE_JUMP_CENTS / 2 for c in self._candidates)
        return near >= LIVE_JUMP_AGREE

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
        band_level = result["band_level"]
        if not self._above_noise(band_level) or result["level"] < SILENCE_DBFS:
            self._end_frame(band_level, np.nan)
            return result

        sound = parselmouth.Sound(window, sampling_frequency=self.sample_rate)
        _, f0_track = pitch_track(sound, mode=self.mode)
        recent = f0_track[-3:]
        if np.isnan(recent).all():
            self._end_frame(band_level, np.nan)
            return result
        f0 = float(np.nanmedian(recent))
        if not self._confirmed(f0):
            return result
        self._end_frame(band_level, f0)
        result["f0"] = f0

        # Praat's Burg window is twice `window_length`, so 80 ms gives a few frames.
        tail = sound.extract_part(from_time=sound.duration - 0.08, to_time=sound.duration)
        formants = to_formants(tail, self.formant_ceiling, time_step=0.005)
        freqs, bws = formant_values(formants, formants.xs())
        freqs, bws = np.nanmedian(freqs, axis=0), np.nanmedian(bws, axis=0)

        freqs[0] = np.nan if f1_missing(freqs[0], f0) else freqs[0]
        result["cpp"] = cpp(frame, self.sample_rate, f0)
        if not np.isnan(freqs[0]):   # the F1 correction needs a real F1 (a snapped one is ~14 dB off)
            result["h1h2"] = h1_h2_corrected(frame, self.sample_rate, f0, freqs, bws)
        result["ring"] = ring_db(frame, self.sample_rate, f0)
        result["formants"] = freqs
        if not np.isnan(freqs).any():
            result["delta_f"] = float(delta_f(*freqs))
        result["resonance_reliable"] = bool(not np.isnan(freqs[0]))
        return result


# --- DISPLAY FORMANT TRACKS ---

# The raw tracks keep every frame for analysis; the *_smooth ones are for drawing. A frame is trusted
# when it is voiced, its F1 is believable (see f1_missing: not snapped onto H1, f0 low enough for LPC),
# every formant has a real bandwidth and F1-F3 are apart.
SMOOTH_FORMANT_FRAMES = 7     # median over 70 ms: shorter than a vowel nucleus (own judgement)
MIN_FORMANT_RUN = 5           # frames (50 ms); shorter trusted stretches are not drawn
SMOOTH_TRACK_KEYS = ("f1_smooth", "f2_smooth", "f3_smooth", "delta_f_smooth")


def trusted_formant_frames(f0, freqs, bws):
    """Boolean per frame: formants worth drawing (see the rules above)."""
    f0 = np.asarray(f0, dtype=float)
    freqs, bws = np.asarray(freqs, dtype=float), np.asarray(bws, dtype=float)
    with np.errstate(invalid="ignore"):
        ok = ~np.isnan(f0) & ~f1_missing(freqs[:, 0], f0)
        ok &= ~np.isnan(freqs[:, :3]).any(axis=1)
        ok &= (bws[:, :3] < MAX_FORMANT_BANDWIDTH).all(axis=1)
        ok &= (np.diff(freqs[:, :3], axis=1) >= MIN_FORMANT_GAP).all(axis=1)
    for start, stop in _runs(ok):
        if stop - start < MIN_FORMANT_RUN:
            ok[start:stop] = False
    return ok


def smooth_track(values, trusted, frames=SMOOTH_FORMANT_FRAMES):
    """Median-filtered copy of `values` within each trusted run; NaN elsewhere."""
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), np.nan)
    ok = trusted & ~np.isnan(values)
    for start, stop in _runs(ok):
        if stop - start >= MIN_FORMANT_RUN:
            out[start:stop] = median_filter(values[start:stop], size=frames, mode="nearest")
    return out


def smooth_formant_tracks(f0, freqs, bws):
    """{"f1_smooth", "f2_smooth", "f3_smooth", "delta_f_smooth"}: display-ready, NaN where untrusted."""
    trusted = trusted_formant_frames(f0, freqs, bws)
    out = {f"f{k + 1}_smooth": smooth_track(freqs[:, k], trusted) for k in range(3)}
    out["delta_f_smooth"] = smooth_track(delta_f(*np.asarray(freqs, dtype=float).T), trusted)
    return out


# --- CONFIDENCE ---

# How far each reading of a take can be believed: "high" | "medium" | "low" | "none", with a reason.
# coach.take_quality stays the gate (trusted or not); this grades the readings for the report and UI.
# A metric takes the lowest level any rule gives it.
CONFIDENCE_LEVELS = ("none", "low", "medium", "high")
# Voice-band SNR (dB) under which a metric drops to none / low / medium.
SNR_STEPS = {
    "pitch": (5, 10, 15),        # pitch holds at 8 dB SNR on synthetic voice (test_pitch); own judgement
    "pitch_floor": (5, 10, 15),
    "pitch_range": (5, 10, 15),
    "contour": (5, 10, 15),
    "singing": (5, 10, 15),
    "delta_f": (5, 10, 20),      # coach.FORMANT_MIN_SNR = 10 (own judgement)
    "h1h2": (5, 20, MIN_RELIABLE_SNR),   # coach.CPP_MIN_SNR = 20: at 16 dB CPP reads 4-5 dB low (audit)
    "cpp": (5, 20, MIN_RELIABLE_SNR),
    "ring": (5, 15, 25),         # coach.RING_MIN_SNR = 15 (own judgement)
    "register": (5, 15, MIN_RELIABLE_SNR),   # built on H1*-H2*; coach distrusts it under 15 dB
}
# Seconds of usable frames under which a metric drops to none / low / medium (own judgement: about a
# word, a phrase and a few sentences of voiced speech; the real takes hold 1.7-23 s of voice).
SECONDS_STEPS = {
    "pitch": (0.2, 1.0, 3.0),
    "pitch_floor": (1.0, 3.0, 8.0),
    "pitch_range": (1.0, 3.0, 8.0),
    "contour": (1.0, 3.0, 8.0),
    "delta_f": (0.1, 1.0, 3.0),  # trusted formant frames; 10 frames is the least analyze_take reports
    "h1h2": (0.2, 1.0, 3.0),
    "cpp": (0.2, 1.0, 3.0),
    "ring": (0.2, 1.0, 3.0),
    "register": (0.5, 2.0, 5.0),
    "singing": (0.6, 1.2, 3.0),  # one held note (MIN_SUNG_SECONDS) .. two of them .. a phrase
}
# Frame spread (interquartile range) over which a reading drops one level: the frames disagree.
# Own measurement on the 10 real speech takes: ΔF IQR (delta_f_iqr) 6-15 % of ΔF, H1*-H2* IQR
# 4-9 dB, CPP IQR 3-6 dB; these limits sit above all of them.
SPREAD_LIMITS = {"delta_f": 0.25, "h1h2": 14.0, "cpp": 7.0}   # ΔF relative, others dB
FORMANT_CONFIDENCE_MAX_F0 = 250   # Hz median f0; F1 snaps to H1 above this (coach.FORMANT_MAX_F0, audit)
CLIP_MEDIUM_EVENTS, CLIP_LOW_EVENTS = 5, 30    # coach.CLIP_FAIR_EVENTS / CLIP_POOR_EVENTS
GHOST_MEDIUM, GHOST_LOW = 0.15, 0.40           # coach.MAX_GHOST_FRACTION / POOR_GHOST_FRACTION
POOR_SNR_DB = 10                               # dB; coach.POOR_SNR, for the wording only
PITCHY = ("pitch", "pitch_floor", "pitch_range", "contour", "singing")
QUALITY = ("h1h2", "cpp", "ring", "register")


def _level_index(level):
    return CONFIDENCE_LEVELS.index(level)


def _steps(value, steps, what):
    """(level, reason) for a value against (none, low, medium) thresholds, higher is better. `what` is
    the reason, or {level: reason}."""
    if value is None or np.isnan(value):
        return None
    for level, limit in zip(CONFIDENCE_LEVELS, steps):
        if value < limit:
            return level, what[level] if isinstance(what, dict) else what
    return None


SECONDS_REASONS = {"none": "too little voiced sound to judge", "low": "only a little voiced sound",
                   "medium": "a longer take would make this more certain"}
FORMANT_REASONS = {"none": "too few clear formant readings to judge resonance",
                   "low": "only a few clear formant readings",
                   "medium": "a longer take would make this more certain"}


def grade(rules):
    """The lowest (level, reason) of a list of rule results (None = no objection)."""
    worst = ("high", "clear recording with plenty of voice")
    for rule in rules:
        if rule is not None and _level_index(rule[0]) < _level_index(worst[0]):
            worst = rule
    return worst


def _iqr(values):
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    return float(np.subtract(*np.percentile(values, [75, 25]))) if len(values) >= 10 else np.nan


def take_confidence(summary, tracks, mode="speak"):
    """({metric: level}, {metric: reason}) for an analyze_take result's readings. analyze_take stores
    them as result["confidence"] = {metric: level, ..., "reasons": {metric: reason}}."""
    f0 = np.asarray(tracks["f0"], dtype=float)
    times = np.asarray(tracks["times"], dtype=float)
    step = float(np.median(np.diff(times))) if len(times) > 1 else 0.01
    voiced_s = float((~np.isnan(f0)).sum() * step)
    snr = summary.get("snr")
    snr = np.nan if snr is None else float(snr)
    pitch = summary.get("pitch")
    clips = summary.get("clip_events") or 0
    ghosts = summary.get("ghost_fraction") or 0.0
    formant_s = float((~np.isnan(tracks.get("delta_f_smooth", tracks["delta_f"]))).sum() * step)
    levels, reasons = {}, {}
    for metric in SNR_STEPS:
        formants = metric == "delta_f"
        rules = [_steps(snr, SNR_STEPS[metric], "background noise is close to the voice level"
                        if snr < POOR_SNR_DB else "background noise makes this reading less precise"),
                 _steps(formant_s if formants else voiced_s, SECONDS_STEPS[metric],
                        FORMANT_REASONS if formants else SECONDS_REASONS)]
        if metric in PITCHY and ghosts >= GHOST_LOW:
            rules.append(("low", "much of the pitch signal looked like noise"))
        elif metric in PITCHY and ghosts > GHOST_MEDIUM:
            rules.append(("medium", "some pitch readings looked like noise"))
        if metric in QUALITY and clips >= CLIP_LOW_EVENTS:
            rules.append(("low", "the recording clipped"))
        elif metric in QUALITY and clips >= CLIP_MEDIUM_EVENTS:
            rules.append(("medium", "a few loud peaks clipped"))
        if metric == "delta_f":          # first, so their reason wins over "too few readings"
            if summary.get("delta_f") is None:
                rules.insert(0, ("none", summary.get("resonance_note") or "not enough clear formant data"))
            if pitch is not None and pitch > FORMANT_CONFIDENCE_MAX_F0:
                rules.insert(0, ("none", "pitch is too high to measure resonance (above about 250 Hz)"))
        if metric == "h1h2" and pitch is not None and pitch > FORMANT_RELIABLE_MAX_F0:
            rules.append(("low", "weight is hard to measure on high notes"))
        if metric in SPREAD_LIMITS:
            if metric == "delta_f":       # ΔF spread over the steady vowel frames behind the reading
                spread = (summary.get("delta_f_iqr") or np.nan) / (summary.get("delta_f") or np.nan)
            else:
                spread = _iqr(tracks.get(metric, []))
            if spread > SPREAD_LIMITS[metric]:
                rules.append(("medium", "the readings varied a lot through the take"))
        if metric in ("h1h2", "cpp", "ring") and summary.get(metric) is not None and np.isnan(summary[metric]):
            rules.append(("none", "not measured"))
        if metric == "singing":
            singing = summary.get("singing")
            if mode != "sing":
                rules.append(("none", "singing is only judged in sing mode"))
            elif not singing:
                rules.append(("none", "no held notes"))
            elif not singing.get("tuning_judged", True):
                rules.append(("low", "too few held notes to judge tuning"))
        # For "register" this is the recording's part; take_register adds the model's.
        levels[metric], reasons[metric] = grade(rules)
    return levels, reasons


# --- TAKE ANALYSIS ---

def _clean_for_formants(audio, sample_rate, voiced, lines=None):
    """(audio with the mic's steady interference lines subtracted, noise report). The 4 kHz line
    adds +174 Hz to F4 and +23 Hz to ΔF in male-range vowels (audit); `cancel_lines` with the
    voiced mask removes it without touching voice harmonics. Lines come from noise-only stretches."""
    noise = denoise.noise_report(audio, sample_rate, voiced)
    lines = noise["lines"] if lines is None else lines
    return _cancel_formant_lines(audio, sample_rate, lines, voiced), noise


def _cancel_formant_lines(audio, sample_rate, lines, voiced):
    """Subtract the lines the formant analysis can see: Burg resamples to twice the ceiling, so
    lines above the highest ceiling never reach it (and cost ~0.13 s each per 17 s take)."""
    lines = [line for line in lines if line[0] <= FORMANT_CEILINGS[-1]]
    return denoise.cancel_lines(audio, sample_rate, lines, voiced) if lines else audio


def _take_formants(sound, times, f0, levels, ceiling):
    """(freqs, bandwidths, usable, steady, raw F1) at one ceiling; freqs has F1 withheld where missing. `usable` frames have a believable
    F1 and F1-F3; `steady` are the loud, held-vowel subset (see `steady_vowel_frames`)."""
    freqs, bws = formant_values(to_formants(sound, ceiling), times)
    f1_raw = freqs[:, 0].copy()
    freqs[f1_missing(freqs[:, 0], f0), 0] = np.nan
    usable = ~np.isnan(freqs[:, :3]).any(axis=1) & ~np.isnan(f0)
    steady = steady_vowel_frames(freqs, np.nan_to_num(levels, nan=-np.inf), usable)
    return freqs, bws, usable, steady, f1_raw


def _ceiling_segment(sound, times, f0, steady):
    """The frames a ceiling is judged on: steady vowel frames, else every frame with a pitch the
    formants can be read at."""
    use = steady if steady.sum() >= NUCLEUS_MIN_FRAMES else ~np.isnan(f0) & (f0 <= F1_RELIABLE_MAX_F0)
    return sound, times[use], f0[use]


def analyze_take(audio, sample_rate=SAMPLE_RATE, mode="speak", ceiling=None):
    """Measure a take. `ceiling` (Hz) fixes the formant ceiling, e.g. the profile's from
    `choose_profile_ceiling`; None picks one from this take's steady vowels, which is less stable.
    Pitch runs on the raw audio; formants and weight on a copy with the mic's interference
    lines subtracted. Result["noise"] is `denoise.noise_report` of the raw take."""
    audio = np.asarray(audio, dtype=float).flatten()
    if len(audio) < sample_rate * 0.3:
        return {"error": "Take too short. Record at least a second of voice."}

    pitch = gated_pitch(audio, sample_rate, mode)
    times, f0 = pitch["times"], pitch["f0"]
    voiced = ~np.isnan(f0)
    if voiced.sum() < 20:
        return {"error": f"Not enough voiced audio ({int(voiced.sum())} voiced frames). "
                         "Try speaking louder or longer."}

    clean, noise = _clean_for_formants(audio, sample_rate, voiced)
    sound = parselmouth.Sound(clean, sampling_frequency=sample_rate)
    levels = frame_levels(clean, sample_rate, times)
    reliable = voiced & (f0 <= F1_RELIABLE_MAX_F0)
    if ceiling is None:
        first = _take_formants(sound, times, f0, levels, DEFAULT_FORMANT_CEILING)
        ceiling = choose_formant_ceiling([_ceiling_segment(sound, times, f0, first[3])])
        found = first if ceiling == DEFAULT_FORMANT_CEILING else _take_formants(sound, times, f0, levels, ceiling)
    else:
        ceiling = int(ceiling)
        found = _take_formants(sound, times, f0, levels, ceiling)
    freqs, bws, usable, steady, f1_raw = found
    df_track = np.where(usable, delta_f(*freqs.T), np.nan)
    f1_ok = ~np.isnan(freqs[:, 0]) & reliable

    h1h2, cpp_frames, rings, level_db = [], [], [], []
    h1h2_track, cpp_track = np.full(len(times), np.nan), np.full(len(times), np.nan)
    half = SPECTRAL_FRAME // 2
    for i, t, f, fr, bw in zip(np.flatnonzero(voiced), times[voiced], f0[voiced], freqs[voiced], bws[voiced]):
        center = int(t * sample_rate)
        frame = clean[max(0, center - half):center + half]
        if len(frame) < SPECTRAL_FRAME:
            continue
        if f1_ok[i]:    # H1*-H2* needs a real F1: a snapped one over-corrects by ~14 dB (audit)
            h1h2_track[i] = h1_h2_corrected(frame, sample_rate, f, fr, bw)
            h1h2.append(h1h2_track[i])
        cpp_track[i] = cpp(frame, sample_rate, f)
        cpp_frames.append(cpp_track[i])
        rings.append(ring_db(frame, sample_rate, f))
        level_db.append(level_dbfs(audio[max(0, center - half):center + half]))

    resonance, resonance_note, spread, vowel_frames = None, None, None, 0
    vowel_frames = int(steady.sum())
    if vowel_frames >= NUCLEUS_MIN_FRAMES:
        resonance = vowel_balanced_delta_f(freqs, df_track, steady)
        spread = float(np.subtract(*np.nanpercentile(df_track[steady], [75, 25])))
    else:
        resonance_note = ("Pitch too high for reliable resonance measurement"
                          if f1_ok[voiced].mean() < 0.5 else
                          "Not enough clear formant data")

    median_f0 = float(np.nanmedian(f0))
    note, cents = hz_to_note(median_f0)
    snr, snr_full = snr_pair(frame_levels(audio, sample_rate, times),
                             frame_levels(audio, sample_rate, times, band=True), voiced)
    noise = dict(noise, lines=[[float(hz), float(db)] for hz, db in noise["lines"]])
    result = {
        "analysis_version": ANALYSIS_VERSION,
        "mode": mode,
        "sample_rate": sample_rate,
        "noise": noise,
        "snr": snr,              # voice band: what the measurements see
        "snr_full": snr_full,    # full band: includes hiss above the voice band
        "clip_events": clip_events(audio),
        "singing": singing_stats(times, f0) if mode == "sing" else None,   # speech is never "singing"
        "pitch": median_f0,
        "pitch_floor": float(np.percentile(f0[voiced], 10)),   # low floor reads as male
        "voiced_fraction": pitch["voiced_fraction"],
        "ghost_fraction": pitch["ghost_fraction"],
        "note": note,
        "cents": cents,
        "contour": semitone_std(f0),
        "pitch_range": pitch_range_st(f0),
        "delta_f": resonance,                # vowel-balanced ΔF of loud, steady vowel frames
        "delta_f_iqr": spread,               # spread of ΔF over those frames (interquartile range, Hz)
        "f3": float(np.nanmedian(freqs[steady, 2])) if resonance else None,
        "f4": float(np.nanmedian(freqs[steady, 3])) if resonance and not np.isnan(freqs[steady, 3]).all() else None,
        "vowel_frames": vowel_frames,        # steady vowel frames behind delta_f
        "f1_missing_fraction": float(1 - f1_ok[voiced].mean()),   # voiced frames whose F1 was withheld
        "vtl_apparent_cm": vocal_tract_length_cm(resonance) if resonance else None,
        "vtl": vocal_tract_length_cm(resonance) if resonance else None,    # old key, for old takes
        "resonance_note": resonance_note,
        "formant_ceiling": ceiling,
        "h1h2": float(np.nanmedian(h1h2)) if h1h2 else np.nan,
        "cpps": cpps(clean, sample_rate, voiced, times),   # Praat CPPS: comparable with the literature
        "cpp": float(np.nanmedian(cpp_frames)) if cpp_frames else np.nan,   # per-frame, live-meter scale
        "ring": float(np.nanmedian(rings)) if rings else np.nan,
        "level": float(np.median(level_db)) if level_db else np.nan,
        "audio": audio,
        "tracks": {
            "times": times,
            "f0": f0,
            "f1": f1_raw,           # as measured: only trust it where f1_ok (register.py still reads it)
            "f1_ok": f1_ok,
            "f2": freqs[:, 1],
            "f3": freqs[:, 2],
            "f4": freqs[:, 3],
            "delta_f": df_track,
            "steady": steady,
            "h1h2": h1h2_track,
            "cpp": cpp_track,
            **smooth_formant_tracks(f0, freqs, bws),
        },
    }
    levels, reasons = take_confidence(result, result["tracks"], mode)
    result["confidence"] = dict(levels, reasons=reasons)
    return result


def choose_profile_ceiling(results):
    """One formant ceiling for a speaker from `results`: analyze_vowels results (the vowel
    check: isolated hVd vowels, the cleanest evidence, so they win and their ceilings are
    medianed) or analyze_take results, whose steady vowel frames are scored jointly. The
    interference lines are removed again from each take with the lines its report stored.
    Falls back to DEFAULT_FORMANT_CEILING with no usable evidence."""
    checks = [r["formant_ceiling"] for r in results if "vowels" in r and r.get("formant_ceiling")]
    if checks:
        return int(FORMANT_CEILINGS[np.argmin(np.abs(FORMANT_CEILINGS - np.median(checks)))])
    segments = []
    for r in results:
        if "tracks" not in r or "audio" not in r or r["tracks"].get("steady") is None:
            continue
        sr, tracks = r.get("sample_rate", SAMPLE_RATE), r["tracks"]
        steady = tracks["steady"]
        if steady.sum() < NUCLEUS_MIN_FRAMES:
            continue
        voiced = ~np.isnan(tracks["f0"])
        lines = r.get("noise", {}).get("lines", [])
        clean = _cancel_formant_lines(r["audio"], sr, lines, voiced)
        segments.append((parselmouth.Sound(clean, sampling_frequency=sr),
                         tracks["times"][steady], tracks["f0"][steady]))
    return choose_formant_ceiling(segments)


# --- SINGING ---

NOTE_MIN_SECONDS = 0.15       # shortest sung note kept: a 0.25 s legato note must count
HELD_NOTE_SECONDS = 0.6
NOTE_CHANGE_CENTS = 80        # smoothed pitch moving this far starts a new note
NOTE_SMOOTH_SECONDS = 0.1     # smoothing for segmentation: short enough to keep 0.25 s notes apart
NOTE_GAP_SECONDS = 0.03       # unvoiced gaps this short don't end a note
VIBRATO_BAND = (3.5, 9.0)     # Hz; typical vibrato rates are ~4.5-7 Hz
TREMOLO_BAND = (9.0, 12.0)    # Hz; faster than vibrato: a tremolo or "wobble"
MIN_VIBRATO_CENTS = 15        # smallest +/- extent called vibrato
MIN_VIBRATO_CYCLES = 4        # cycles needed in the note to call it
MIN_VIBRATO_SHARE = 0.6       # share of 1.5-15 Hz power inside the vibrato (or tremolo) band
MIN_VIBRATO_FIT = 0.4         # share of the note's variance the fitted sinusoid explains (own measurement)
MAX_GAP_SECONDS = 0.06        # tracker dropouts up to this long are interpolated
TREND_SECONDS = 0.3           # averaging window for slow drift once vibrato is removed
PERCEIVED_SECONDS = 0.2       # median window for perceived pitch: at least one vibrato period
EXERCISE_SETTLE_SECONDS = 0.15
IN_TUNE_CENTS = 25            # strict "in tune"
CLOSE_CENTS = 50              # "close"; the usual note-accuracy criterion (Pfordresher & Demorest 2021)
LAG_RANGE = (-0.2, 0.6)       # s; singer early .. late relative to the targets
LAG_WRONG_CENTS = 200         # error charged to a missing or far-off frame when estimating lag
LAG_TIE_CENTS = 2             # lags this close to the best are ties: the smallest |lag| wins
LAG_ON_TIME_SECONDS = 0.03    # inside this a singer is "on time"
MIN_SUNG_SECONDS = 0.6        # a take has singing if it holds a steady note this long
SUNG_SPREAD_CENTS = 100       # ... whose pitch std is under this
SUNG_MIN_HZ = 75              # ... and above desk rumble (judgment, unverified)
INTERVAL_GAP_SECONDS = 0.5    # notes further apart than this don't form an interval
# Tuning centre, interval error and drift need this much held material. Own measurement on the 10 real
# speech takes: their longest "note" is 0.29-0.61 s, and only one take holds one note of 0.6 s or more
# (0.61 s on 09-21-26, the stretch that read as "1 held note, 3 cents below A440"); a sung scale or
# phrase has several held notes or one long one. 1.5 s is about 2.5 x the longest speech "note".
TUNING_MIN_HELD_NOTES = 2
TUNING_LONG_NOTE_SECONDS = 1.5


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


def _fill_gaps(x, max_gap):
    """Copy of x with NaN gaps of at most max_gap frames (not at the ends) interpolated."""
    x = np.array(x, dtype=float)
    for start, end in _voiced_runs(np.where(np.isnan(x), 0.0, np.nan)):
        if 0 < start and end < len(x) and end - start <= max_gap:
            x[start:end] = np.interp(np.arange(start, end), [start - 1, end], [x[start - 1], x[end]])
    return x


def perceived_cents(cents_window):
    """Perceived pitch of a short window of pitch values (cents): their median, which follows the
    vibrato mean rather than each swing (NaN if fewer than 3 are voiced). The live UI keeps about
    PERCEIVED_SECONDS of frames and colours by this, not by the raw f0."""
    values = np.asarray(cents_window, dtype=float)
    values = values[~np.isnan(values)]
    return float(np.median(values)) if len(values) >= 3 else np.nan


def perceived_track(cents, frame_rate, seconds=PERCEIVED_SECONDS):
    """perceived_cents at every frame, over a centred window (shorter at the edges)."""
    cents = np.asarray(cents, dtype=float)
    half = max(1, int(round(seconds * frame_rate / 2)))
    padded = np.pad(cents, half, constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
    enough = (~np.isnan(windows)).sum(axis=1) >= 3
    out = np.full(len(cents), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out[enough] = np.nanmedian(windows[enough], axis=1)
    return out


def segment_notes(f0, frame_rate, min_seconds=NOTE_MIN_SECONDS):
    """(start, end) frame ranges of sung notes: voiced runs, split wherever the
    smoothed pitch moves more than NOTE_CHANGE_CENTS from the note so far.
    A short median and average smooth tracker glitches and most of the vibrato."""
    notes = []
    min_frames = min_seconds * frame_rate
    cents = _fill_gaps(100 * hz_to_midi(f0), NOTE_GAP_SECONDS * frame_rate)
    for run_start, run_end in _voiced_runs(cents):
        run = median_filter(cents[run_start:run_end], size=5, mode="nearest")
        smooth = _moving_average(run, NOTE_SMOOTH_SECONDS * frame_rate)
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


def _sinusoid_fit(t, y, freq):
    """Least-squares mean + linear drift + sinusoid at freq: (amplitude, fitted sinusoid, residual)."""
    basis = np.column_stack([np.ones_like(t), t, np.sin(2 * np.pi * freq * t), np.cos(2 * np.pi * freq * t)])
    coef = np.linalg.lstsq(basis, y, rcond=None)[0]
    wave = basis[:, 2:] @ coef[2:]
    return float(np.hypot(coef[2], coef[3])), wave, y - basis @ coef


def _drift(y, frame_rate):
    """Std of the slow trend of y (cents), the part left after a TREND_SECONDS average."""
    trend = _moving_average(y, TREND_SECONDS * frame_rate)
    trim = int(TREND_SECONDS * frame_rate / 2)
    trend = trend[trim:len(y) - trim]
    return float(np.std(trend)) if len(trend) else np.nan


def vibrato_stats(cents, frame_rate):
    """Vibrato in a held note's pitch (in cents): rate (Hz), extent (+/- cents),
    regularity (cycle-length variation, lower = steadier), drift (slow wandering,
    std in cents) and tremolo (rate in Hz of a 9-12 Hz wobble, else None).
    Rate is the zero-padded FFT peak in 3.5-9 Hz; extent is a least-squares sinusoid fit with
    mean and linear drift. It needs >= 4 cycles, extent >= 15 cents and most of the pitch
    movement in the band. Short tracker dropouts are interpolated. Vibrato fields are None
    when there is none."""
    result = {"rate": None, "extent": None, "regularity": None, "drift": np.nan, "tremolo": None}
    y = _fill_gaps(cents, MAX_GAP_SECONDS * frame_rate)
    runs = _voiced_runs(y)
    if not runs:
        return result
    a, b = max(runs, key=lambda r: r[1] - r[0])        # longest unbroken stretch
    y = y[a:b]
    n = len(y)
    if n < frame_rate * 0.5:
        return result
    t = np.arange(n) / frame_rate
    detrended = y - np.polyval(np.polyfit(t, y, 1), t)
    result["drift"] = _drift(y, frame_rate)

    nfft = 1 << int(np.ceil(np.log2(n * 16)))
    spec = np.abs(np.fft.rfft(detrended * np.hanning(n), nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1 / frame_rate)
    search = np.flatnonzero((freqs >= VIBRATO_BAND[0]) & (freqs <= TREMOLO_BAND[1]))
    peak = search[np.argmax(spec[search])]
    rate = freqs[peak]
    if search[0] < peak < search[-1]:                  # parabolic interpolation on the log spectrum
        left, mid, right = np.log(spec[peak - 1:peak + 2] + 1e-12)
        denom = left - 2 * mid + right
        if denom < 0:
            rate += 0.5 * (left - right) / denom * (freqs[1] - freqs[0])
    # Refine on the fit itself: the FFT bin is coarse for a note of only a few cycles.
    trial = rate + np.linspace(-0.3, 0.3, 25)
    rate = float(trial[np.argmin([np.sum(_sinusoid_fit(t, y, f)[2] ** 2) for f in trial])])
    extent, wave, residual = _sinusoid_fit(t, y, rate)

    tremolo = rate > VIBRATO_BAND[1]
    lo, hi = TREMOLO_BAND if tremolo else VIBRATO_BAND
    whole = (freqs >= 1.5) & (freqs <= min(15.0, frame_rate / 2))
    share = spec[(freqs >= lo) & (freqs <= hi)].sum() / (spec[whole].sum() + 1e-12)
    fit = 1 - np.sum(residual ** 2) / (np.sum(detrended ** 2) + 1e-12)
    if (rate * n / frame_rate < MIN_VIBRATO_CYCLES or extent < MIN_VIBRATO_CENTS
            or share < MIN_VIBRATO_SHARE or fit < MIN_VIBRATO_FIT):
        return result
    result["drift"] = _drift(y - wave, frame_rate)
    if tremolo:
        result["tremolo"] = rate
        return result

    # Cycle lengths from upward zero crossings of the band-passed contour.
    spectrum = np.fft.rfft(detrended)
    f = np.fft.rfftfreq(n, 1 / frame_rate)
    spectrum[(f < 0.6 * rate) | (f > 1.6 * rate)] = 0
    s = np.fft.irfft(spectrum, n)
    idx = np.flatnonzero((s[:-1] < 0) & (s[1:] >= 0))
    crossings = idx + s[idx] / (s[idx] - s[idx + 1])
    periods = np.diff(crossings) / frame_rate
    regularity = float(np.std(periods) / np.mean(periods)) if len(periods) >= 2 else None
    result.update(rate=rate, extent=extent, regularity=regularity)
    return result


def _wrap_cents(x):
    """Fold cents into [-50, 50): the offset from the nearest semitone."""
    return (np.asarray(x, dtype=float) + 50) % 100 - 50


def _has_singing(f0, notes, frame_rate):
    """True if some note is a steady pitch held MIN_SUNG_SECONDS above the rumble range."""
    for a, b in notes:
        if (b - a) / frame_rate < MIN_SUNG_SECONDS:
            continue
        if np.nanmedian(f0[a:b]) >= SUNG_MIN_HZ and np.nanstd(100 * hz_to_midi(f0[a:b])) < SUNG_SPREAD_CENTS:
            return True
    return False


def singing_stats(times, f0):
    """Summary of sung notes in a take, or None when it holds no sung material (a steady note
    of 0.6 s): note count, vibrato and tremolo on held notes, sustain drift and tuning judged
    from the singer's own tonic: tonic_offset (signed, vs A440), tuning_spread (how far notes
    sit from that tonic) and interval_error (consecutive notes vs equal-tempered steps)."""
    times, f0 = np.asarray(times, dtype=float), np.asarray(f0, dtype=float)
    if len(times) < 2:
        return None
    frame_rate = 1 / float(np.median(np.diff(times)))
    notes = segment_notes(f0, frame_rate)
    if not notes or not _has_singing(f0, notes, frame_rate):
        return None
    midis, held, longest = [], [], 0.0
    for a, b in notes:
        midi = hz_to_midi(f0[a:b])
        midis.append(float(np.nanmedian(midi)))
        if (b - a) / frame_rate >= HELD_NOTE_SECONDS:
            held.append(vibrato_stats(100 * midi, frame_rate))
            longest = max(longest, (b - a) / frame_rate)
    enough = len(held) >= TUNING_MIN_HELD_NOTES or longest >= TUNING_LONG_NOTE_SECONDS

    def median_of(key):
        values = [h[key] for h in held if h[key] is not None and not np.isnan(h[key])]
        return float(np.median(values)) if values else None

    offsets = 100 * (np.array(midis) - np.round(midis))
    angle = 2 * np.pi * offsets / 100                  # offsets repeat every semitone
    tonic = np.degrees(np.arctan2(np.sin(angle).mean(), np.cos(angle).mean())) / 360 * 100
    tonic += float(np.median(_wrap_cents(offsets - tonic)))
    tonic = float(_wrap_cents(tonic))
    spread = float(np.median(np.abs(_wrap_cents(offsets - tonic))))
    errors = [abs(float(_wrap_cents(100 * (midis[i + 1] - midis[i]))))
              for i in range(len(notes) - 1)
              if (notes[i + 1][0] - notes[i][1]) / frame_rate <= INTERVAL_GAP_SECONDS]
    return {
        "notes": len(notes),
        "held_notes": len(held),
        "vibrato_notes": sum(h["rate"] is not None for h in held),
        "tremolo_notes": sum(h["tremolo"] is not None for h in held),
        "vibrato_rate": median_of("rate"),
        "vibrato_extent": median_of("extent"),
        "vibrato_regularity": median_of("regularity"),
        "drift": median_of("drift") if enough else None,
        "tonic_offset": tonic if enough else None,
        "tuning_spread": spread if enough else None,
        "tuning_offset": spread if enough else None,   # old key, now the spread about the singer's own tonic
        "interval_error": float(np.median(errors)) if errors and enough else None,
        "tuning_judged": bool(enough),     # False: too little held singing for tuning, steps or drift
    }


def estimate_lag(times, f0, targets):
    """Seconds the singer is late (negative: early) against the targets [(start_s, duration_s, midi)],
    one value for the whole run: the lag in LAG_RANGE where perceived pitch best matches the target
    contour (octave-folded, missing frames charged LAG_WRONG_CENTS). 0 when too little is sung."""
    times, f0 = np.asarray(times, dtype=float), np.asarray(f0, dtype=float)
    if len(times) < 2 or not targets or (~np.isnan(f0)).sum() < 10:
        return 0.0
    step = float(np.median(np.diff(times)))
    sung = perceived_track(100 * hz_to_midi(f0), 1 / step)
    grid = np.arange(min(s for s, _, _ in targets), max(s + d for s, d, _ in targets), step)
    wanted = np.full(len(grid), np.nan)
    for start, duration, midi in targets:
        wanted[(grid >= start) & (grid < start + duration)] = 100 * midi
    scored = ~np.isnan(wanted)
    if not scored.any():
        return 0.0
    lags = np.arange(LAG_RANGE[0], LAG_RANGE[1] + step / 2, step)
    errors = []
    for lag in lags:
        idx = np.round((grid + lag - times[0]) / step).astype(int)
        inside = (idx >= 0) & (idx < len(times))
        diff = np.full(len(grid), np.nan)
        diff[inside] = sung[idx[inside]] - wanted[inside]
        diff = np.abs(diff - 1200 * np.round(diff / 1200))
        errors.append(np.where(np.isnan(diff), LAG_WRONG_CENTS, np.minimum(diff, LAG_WRONG_CENTS))[scored].mean())
    errors = np.array(errors)
    ties = np.flatnonzero(errors <= errors.min() + LAG_TIE_CENTS)
    return float(lags[ties[np.argmin(np.abs(lags[ties]))]])


def timing_text(lag):
    """"timing: 120 ms late" for a lag in seconds (negative = early)."""
    if abs(lag) < LAG_ON_TIME_SECONDS:
        return "timing: on time"
    return f"timing: {abs(lag) * 1000:.0f} ms {'late' if lag > 0 else 'early'}"


def score_exercise(times, f0, targets, lag=None):
    """Score a take against target notes [(start_s, duration_s, midi), ...].

    One global lag (estimate_lag unless given) shifts the targets onto the singing first, so a
    steady 0.4 s lateness isn't charged as wrong notes. Each note is then judged after a short
    settle time on perceived pitch (median over PERCEIVED_SECONDS), so vibrato around the
    note counts as in tune: `in_tune` is the share within +/-25 cents, `close` within +/-50.
    Errors are folded to the nearest octave, so singing the melody an octave down still scores."""
    times, f0 = np.asarray(times, dtype=float), np.asarray(f0, dtype=float)
    if lag is None:
        lag = estimate_lag(times, f0, targets)
    frame_rate = 1 / float(np.median(np.diff(times))) if len(times) > 1 else 100.0
    notes = []
    for start, duration, midi in targets:
        settle = min(EXERCISE_SETTLE_SECONDS, 0.3 * duration)
        window = ((times >= start + lag + settle) & (times < start + lag + duration))
        cents = 100 * (hz_to_midi(f0[window]) - midi)
        if (~np.isnan(cents)).sum() < 3:
            notes.append({"midi": int(midi), "cents": None, "in_tune": 0.0, "close": 0.0, "octave": 0})
            continue
        octave = int(np.round(np.nanmedian(cents) / 1200))
        cents = cents - 1200 * octave
        felt = perceived_track(cents, frame_rate)[~np.isnan(cents)]
        notes.append({"midi": int(midi), "cents": float(np.nanmedian(cents)), "octave": octave,
                      "in_tune": float(np.mean(np.abs(felt) <= IN_TUNE_CENTS)),
                      "close": float(np.mean(np.abs(felt) <= CLOSE_CENTS))})
    sung = [n for n in notes if n["cents"] is not None]
    return {
        "notes": notes,
        "notes_total": len(notes),
        "notes_sung": len(sung),
        "notes_in_tune": sum(abs(n["cents"]) <= IN_TUNE_CENTS for n in sung),
        "notes_close": sum(abs(n["cents"]) <= CLOSE_CENTS for n in sung),
        "mean_abs_cents": float(np.mean([abs(n["cents"]) for n in sung])) if sung else None,
        "time_in_tune": float(np.mean([n["in_tune"] for n in notes])) if notes else None,
        "time_close": float(np.mean([n["close"] for n in notes])) if notes else None,
        "mean_signed_cents": float(np.median([n["cents"] for n in sung])) if sung else None,   # negative = flat
        "lag": float(lag),
        "lag_ms": float(lag) * 1000,        # positive = late
        "timing": timing_text(lag),
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
    jointly over all tokens, as one speaker's vocal tract fits them all. Pitch is gated
    (see `gated_pitch`) and the mic's interference lines, found in the tokens' pauses, are
    subtracted first.

    Returns per-word F1-F4 and Delta F, plus the vowel-balanced mean Delta F,
    which removes the effect of which vowels happened to be spoken."""
    audio_by_word = {w: np.asarray(a, dtype=float).flatten() for w, a in tokens.items()}
    pitches = {w: gated_pitch(a, sample_rate) for w, a in audio_by_word.items()}
    pauses = [denoise.noise_audio_from_take(a, sample_rate, ~np.isnan(pitches[w]["f0"]))
              for w, a in audio_by_word.items() if len(a)]
    lines = denoise.find_lines(np.concatenate(pauses), sample_rate) if pauses else []

    segments, skipped = {}, []
    for word, audio in audio_by_word.items():
        voiced = ~np.isnan(pitches[word]["f0"])
        if len(lines) and len(audio):
            audio = denoise.cancel_lines(audio, sample_rate, lines, voiced)
        sound = parselmouth.Sound(audio, sampling_frequency=sample_rate)
        nucleus = vowel_nucleus(pitches[word]["times"], pitches[word]["f0"])
        if nucleus is None or nucleus[1] > F1_RELIABLE_MAX_F0:
            skipped.append(word)
            continue
        segments[word] = (sound, nucleus[0], nucleus[1])

    ceiling = choose_formant_ceiling(list(segments.values()))
    vowels = {}
    for word, (sound, nucleus_times, f0) in segments.items():
        freqs, _ = formant_values(to_formants(sound, ceiling), nucleus_times)
        f1, f2, f3, f4 = (float(v) for v in np.nanmedian(freqs, axis=0))
        df = float(delta_f(f1, f2, f3, f4))
        if np.isnan(df) or f1_missing(f1, f0):
            skipped.append(word)
            continue
        vowels[word] = {"f0": f0, "f1": f1, "f2": f2, "f3": f3, "f4": f4, "delta_f": df}

    mean_df = float(np.mean([v["delta_f"] for v in vowels.values()])) if vowels else None
    return {"analysis_version": ANALYSIS_VERSION, "vowels": vowels,
            "skipped": [w for w in VOWEL_WORDS if w in skipped],
            "delta_f": mean_df, "formant_ceiling": ceiling}
