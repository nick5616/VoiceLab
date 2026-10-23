"""Laryngeal register (mechanism) estimate from audio, optionally with EGG contact quotient.

THIS IS AN ESTIMATE. There is no validated, acoustic-only, speaker-independent
register classifier; every output here is a likelihood with a confidence, and
it improves only when fit() calibrates it to one voice's labelled frames.

Terms: "chest" = laryngeal mechanism M1, "head" = M2 (falsetto/light), "mix" =
an ambiguous score between them, "fry" = M0. Mechanisms: Henrich, "Mirroring
the voice from Garcia to the present day" (Logopedics Phoniatrics Vocology,
2006); Roubeau, Henrich & Castellengo, "Laryngeal vibratory mechanisms: the
notion of vocal register revisited" (J. Voice, 2009).

Evidence behind the default model:
- H1*-H2* (formant-corrected, as in analysis.h1_h2_corrected) is larger in
  light/M2 phonation than in pressed M1 phonation (Kreiman, Gerratt, Garellek
  and colleagues; Garellek et al. 2016). Caveat from Henrich, d'Alessandro &
  Doval (Eurospeech 2001): it depends on open quotient AND pulse asymmetry, so
  it is a noisy proxy, and formant correction fails at high f0.
- Spectral tilt (H1*-A3): M2 has a steeper source spectrum (strong H1, weak
  high harmonics); used as the second, partly independent cue.
- CPP: M2 is usually more sinusoidal/periodic but breathy voices lower it; a
  weak cue only.
- EGG contact quotient: M1 roughly 0.4-0.6; M2 about 0.27 lower (Henrich,
  Smith & Wolfe, JASA 2011, seven male singers). Dominates when supplied.
- Fry (M0): f0 below ~70 Hz with irregular periods or subharmonics
  (Blomgren, Chen, Ng & Gilbert, JASA 1998).

All numeric thresholds and weights are marked "(lit.)" when set from the
figures above, otherwise "(synthetic)" = chosen empirically on the synthetic
vowels of tests/test_analysis.py::synth_vowel. None is validated on real voices.
On real speech those defaults read nearly everything as head, so an uncalibrated model scores
speak-mode takes with the SPEECH_* defaults instead (see there), and take_register grades the
result "none" unless it is clear-cut.
"""
import warnings

import numpy as np
from scipy.ndimage import maximum_filter1d
from scipy.signal import find_peaks, hilbert

import analysis

REGISTERS = ("fry", "chest", "mix", "head")      # display labels; chest=M1, head=M2
MIX_BAND = (0.35, 0.65)                          # m2 score in this band -> "mix" (synthetic)

FEATURES = ("h1h2", "h1a3", "cpp", "logf0", "cq")
# Per feature: centre, scale (z = (x - centre) / scale), default weight.
DEFAULT_CENTRES = {"h1h2": 3.0, "h1a3": 5.0, "cpp": 25.0, "logf0": 0.0, "cq": 0.38}
# H1*-H2* centre ~3 dB, between modal (~0-5) and falsetto (>8) (lit.); others (synthetic)
DEFAULT_SCALES = {"h1h2": 6.0, "h1a3": 6.0, "cpp": 6.0, "logf0": 1.0, "cq": 0.07}
# cq centre = midpoint of M1 ~0.5 and M2 ~0.25 (lit.)
DEFAULT_WEIGHTS = {"h1h2": 1.0, "h1a3": 1.3, "cpp": 0.0, "logf0": 0.3, "cq": -3.5}
# weights (synthetic); cpp is carried so fit() can learn it, but defaults to 0;
# logf0 (log2(f0/150) in z) is only a weak prior and its centre is unused
DEFAULT_BIAS = 0.0
Z_CLIP = 3.0
HIGH_F0 = analysis.FORMANT_RELIABLE_MAX_F0       # above this, formant correction is untrustworthy
HIGH_F0_H1H2_TRUST = 0.3                         # h1h2 weight multiplier above HIGH_F0 (synthetic)
H1A3_F0_SLOPE = 30.0                             # dB per octave above HIGH_F0 added to the h1a3 centre:
                                                 # uncorrected H1 rises with F1 proximity (synthetic)
H1A3_SLOPE_MAX_F0 = 500.0                        # Hz; the centre stops rising here, so it cannot run away
                                                 # and pin every high note to "chest" (synthetic)
HEAD_PRIOR_F0 = 500.0                            # Hz; above this the prior shifts toward head: M1 rarely
                                                 # exceeds ~500-700 Hz (Henrich 2006; unverified for any one voice)
HEAD_PRIOR_LOGIT = 3.0                           # logit added per octave above HEAD_PRIOR_F0 (synthetic)
HEAD_PRIOR_MAX_OCTAVES = 1.5                     # prior stops growing here
HIGH_MIX_BAND = (0.2, 0.8)                       # wider mix band above HEAD_PRIOR_F0: less certain (synthetic)
# Speech (speak mode) with an uncalibrated model. Phase 10 measurement on the user's 10 real speech
# takes (147-213 Hz, all ordinary speech): the default model read 81-100% head on every one. H1-A3
# was the main cause: real speech reads 21-34 dB (take medians) against a centre of 5 dB set on the
# synthetic vowels above (which read -5 to 10 dB), so it pinned the logit at +3.5 to +3.9 on every
# take; H1*-H2* (take medians 7-21 dB on frames with a believable F1, vs a 3 dB centre) added up to
# +1.4. Desk rumble was not a cause: H1 sat 29-35 dB above the noise in its band, and removing that
# noise moved H1-H2 by 0.2 dB at most (median).
# No speech centre for H1-A3 could be sourced, and its absolute level depends on the mic and the
# vowel, so speech leaves it out; H1*-H2* is re-centred above the user's modal speech, where
# women's modal H1-H2 sits several dB above men's (Klatt & Klatt 1990; Hanson & Chuang 1999,
# direction verified, values unverified); and a prior toward M1 reflects that speech is produced
# mostly in M1 by men and women (Roubeau, Henrich & Castellengo 2009; from memory, unverified).
SPEECH_CENTRES = dict(DEFAULT_CENTRES, h1h2=12.0)   # dB; above the median of 8 of the 10 takes (own measurement)
SPEECH_WEIGHTS = dict(DEFAULT_WEIGHTS, h1a3=0.0)    # H1-A3 has no sourced speech centre (see above)
SPEECH_PRIOR_LOGIT = -1.5     # toward chest; a frame then needs H1*-H2* near 25 dB to read head (own judgement)
SPEECH_PRIOR_MAX_F0 = 350.0   # Hz; above this speech is no longer "ordinary" and the prior fades out
SPEECH_PRIOR_FADE_OCTAVES = 0.5   # ... over this many octaves (own judgement)
MIN_CALIBRATION_FRAMES = 200                     # voiced frames per class (~2 s at 10 ms)
PRIOR_STRENGTH = 30.0                            # ridge pull toward default weights, in frame-equivalents
FIT_ITERATIONS = 200
FIT_RATE = 0.5

NOMINAL_BANDWIDTHS = (80.0, 100.0, 120.0)        # tracks hold no bandwidths; typical Hz (old-take fallback)
FRAME = analysis.SPECTRAL_FRAME

BREAK_SWING = 0.4                                # score change that counts as an M1<->M2 switch (spec)
BREAK_WINDOW = 0.15                              # s: the switch must complete within this
BREAK_SMOOTH = 0.05                              # s: smoothing before detection
BREAK_MIN_GAP = 0.3                              # s: two breaks closer than this are one

FRY_MAX_F0 = 70.0                                # Hz (Blomgren et al. 1998, lit.)
FRY_MIN_FRAMES = 3                               # shortest fry run kept
FRY_JUMP_ST = 4.0                                # semitone f0 jump counted as erratic (synthetic)
FRY_JUMPS_IN_5 = 3                               # erratic jumps within 5 frames -> irregular tracking
FRY_PULSE_WINDOW = 0.05                          # s each side of a frame for pulse timing
FRY_PULSE_CV = 0.03                              # interval variation separating fry from a low steady tone (synthetic)
PULSE_DISTANCE = 0.008                           # s between pulse peaks (allows up to 125 Hz)
PULSE_SMOOTH = 0.002                             # s envelope smoothing
PULSE_LEVEL = 0.003                              # linear amplitude below which no pulses are sought


def _a3(power_db, freqs, f3):
    band = (freqs >= f3 * 0.88) & (freqs <= f3 * 1.12)
    return power_db[band].max() if band.any() else np.nan


def _h1a3(frame, sample_rate, f0, formants, bandwidths):
    """H1 (corrected for F1/F2 when f0 is low enough to trust them) minus the F3 peak."""
    if f0 is None or np.isnan(f0) or len(formants) < 3 or np.isnan(formants[2]):
        return np.nan
    freqs, power_db = analysis._power_spectrum_db(frame, sample_rate, pad=4)
    band = (freqs >= f0 * 0.9) & (freqs <= f0 * 1.1)
    if not band.any():
        return np.nan
    h1 = power_db[band].max()
    if f0 <= HIGH_F0:
        for formant, bw in zip(formants[:2], bandwidths[:2]):
            if not (np.isnan(formant) or np.isnan(bw)):
                h1 -= analysis._resonance_gain_db(f0, formant, bw, sample_rate)
    return float(h1 - _a3(power_db, freqs, formants[2]))


def frame_features(frame, sample_rate, f0, formants, bandwidths, cq=None):
    """Feature dict for one voiced frame: 'h1h2', 'h1a3' (H1 minus the F3 peak), 'cpp', 'f0',
    and 'cq' (None without an EGG). NaN for any feature that can't be measured."""
    out = {"h1h2": np.nan, "h1a3": np.nan, "cpp": np.nan,
           "f0": np.nan if f0 is None else float(f0), "cq": None if cq is None or np.isnan(cq) else float(cq)}
    if f0 is None or np.isnan(f0) or len(frame) < FRAME:
        return out
    out["h1h2"] = analysis.h1_h2_corrected(frame, sample_rate, f0, formants, bandwidths)
    out["h1a3"] = _h1a3(frame, sample_rate, f0, formants, bandwidths)
    out["cpp"] = analysis.cpp(frame, sample_rate, f0)
    return out


def _z(features, centres=DEFAULT_CENTRES):
    """Standardized, clipped feature values; NaN where missing. Reliability scaling is applied here."""
    f0 = features.get("f0")
    high = f0 is not None and not np.isnan(f0) and f0 > HIGH_F0
    z = {}
    for name in FEATURES:
        if name == "logf0":
            x = np.nan if f0 is None or np.isnan(f0) else np.log2(f0 / 150.0)
            z[name] = np.clip(x / 1.5, -Z_CLIP, Z_CLIP) if not np.isnan(x) else np.nan
            continue
        x = features.get(name)
        x = np.nan if x is None else float(x)
        centre = centres[name]
        if name == "h1a3" and high:
            centre += H1A3_F0_SLOPE * np.log2(min(f0, H1A3_SLOPE_MAX_F0) / HIGH_F0)
        v = (x - centre) / DEFAULT_SCALES[name] if not np.isnan(x) else np.nan
        if name == "h1h2" and high and not np.isnan(v):
            v *= HIGH_F0_H1H2_TRUST
        z[name] = np.clip(v, -Z_CLIP, Z_CLIP) if not np.isnan(v) else np.nan
    return z


def _vector(features, centres=DEFAULT_CENTRES):
    """(z vector with 0 for missing, True if the score is measurable)."""
    z = _z(features, centres)
    measurable = any(not np.isnan(z[n]) for n in ("h1h2", "h1a3", "cq"))
    return np.array([0.0 if np.isnan(z[n]) else z[n] for n in FEATURES]), measurable


def head_prior(f0):
    """Logit added toward head at high f0 (0 at or below HEAD_PRIOR_F0, or when f0 is unknown)."""
    if f0 is None or np.isnan(f0) or f0 <= HEAD_PRIOR_F0:
        return 0.0
    return HEAD_PRIOR_LOGIT * min(np.log2(f0 / HEAD_PRIOR_F0), HEAD_PRIOR_MAX_OCTAVES)


def speech_prior(f0):
    """Logit added toward chest for speech: SPEECH_PRIOR_LOGIT up to SPEECH_PRIOR_MAX_F0, fading to 0
    over SPEECH_PRIOR_FADE_OCTAVES above it."""
    if f0 is None or np.isnan(f0) or f0 <= SPEECH_PRIOR_MAX_F0:
        return SPEECH_PRIOR_LOGIT
    fade = np.log2(f0 / SPEECH_PRIOR_MAX_F0) / SPEECH_PRIOR_FADE_OCTAVES
    return SPEECH_PRIOR_LOGIT * max(0.0, 1.0 - fade)


def mix_band(f0=None):
    """(low, high) m2 scores bounding "mix": wider above HEAD_PRIOR_F0, where the evidence is thinner."""
    return HIGH_MIX_BAND if f0 is not None and not np.isnan(f0) and f0 > HEAD_PRIOR_F0 else MIX_BAND


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


class RegisterModel:
    """M2 likelihood from features. A heuristic default; fit() calibrates it to one voice."""

    def __init__(self):
        self.weights = np.array([DEFAULT_WEIGHTS[n] for n in FEATURES])
        self.bias = DEFAULT_BIAS
        self.calibrated = False

    def speech_default(self, mode):
        """True when this uncalibrated model scores speech with the speech centres and prior."""
        return mode == "speak" and not self.calibrated

    def score(self, features, mode=None):
        """P(M2) in [0, 1], or NaN if not measurable. mode="speak" uses the speech defaults
        (SPEECH_*) while the model is uncalibrated; a calibrated model ignores the mode."""
        f0 = features.get("f0")
        if self.speech_default(mode):
            x, _ = _vector(features, SPEECH_CENTRES)
            weights = np.array([SPEECH_WEIGHTS[n] for n in FEATURES])
            h1h2 = features.get("h1h2")
            if (h1h2 is None or np.isnan(h1h2)) and features.get("cq") is None:
                return np.nan          # H1-A3 alone doesn't count in speech
            return float(_sigmoid(self.bias + weights @ x + speech_prior(f0) + head_prior(f0)))
        x, measurable = _vector(features)
        if not measurable:
            return np.nan
        return float(_sigmoid(self.bias + self.weights @ x + head_prior(f0)))

    def confidence(self, features):
        """0..1: low near a score of 0.5 or when features are missing."""
        s = self.score(features)
        if np.isnan(s):
            return 0.0
        z = _z(features)
        have = np.mean([not np.isnan(z[n]) for n in ("h1h2", "h1a3", "cpp")]) if features.get("cq") is None else 1.0
        return float(abs(s - 0.5) * 2 * (0.5 + 0.5 * have))

    def label(self, score, f0=None):
        """"chest" | "mix" | "head" (fry is decided by is_fry); None for NaN.
        Pass f0 to use the wider mix band that applies above HEAD_PRIOR_F0."""
        if score is None or np.isnan(score):
            return None
        low, high = mix_band(f0)
        if score < low:
            return "chest"
        return "head" if score > high else "mix"

    def fit(self, labelled):
        """labelled: list of (features, "chest"|"head"); returns self. Ridge toward the default weights."""
        rows, ys, shifts = [], [], []
        for features, name in labelled:
            x, measurable = _vector(features)
            if measurable and name in ("chest", "head"):
                rows.append(x)
                ys.append(1.0 if name == "head" else 0.0)
                shifts.append(head_prior(features.get("f0")))
        ys = np.array(ys)
        if len(ys) == 0 or min(ys.sum(), len(ys) - ys.sum()) < MIN_CALIBRATION_FRAMES:
            return self
        x = np.array(rows)
        x = np.hstack([x, np.ones((len(x), 1))])
        shifts = np.array(shifts)
        prior = np.append([DEFAULT_WEIGHTS[n] for n in FEATURES], DEFAULT_BIAS)
        w = prior.copy()
        for _ in range(FIT_ITERATIONS):
            p = _sigmoid(x @ w + shifts)
            grad = x.T @ (p - ys) + PRIOR_STRENGTH * (w - prior)
            hess = (x.T * (p * (1 - p))) @ x + PRIOR_STRENGTH * np.eye(len(w))
            step = np.linalg.solve(hess, grad)
            w = w - np.clip(step, -2, 2)
            if np.max(np.abs(step)) < 1e-6:
                break
        self.weights, self.bias = w[:-1], float(w[-1])
        self.calibrated = True
        return self

    def is_calibrated(self):
        return bool(self.calibrated)

    def to_dict(self):
        return {"weights": [float(v) for v in self.weights], "bias": float(self.bias),
                "calibrated": bool(self.calibrated), "features": list(FEATURES)}

    @classmethod
    def from_dict(cls, d):
        model = cls()
        if list(d.get("features", FEATURES)) == list(FEATURES) and len(d.get("weights", [])) == len(FEATURES):
            model.weights = np.array(d["weights"], dtype=float)
            model.bias = float(d.get("bias", DEFAULT_BIAS))
            model.calibrated = bool(d.get("calibrated", False))
        return model


# --- FRY ---

def _pulse_times(audio, sample_rate):
    """Times (s) of glottal-pulse-like envelope peaks."""
    audio = np.asarray(audio, dtype=float)
    env = np.abs(hilbert(audio))
    size = max(1, int(PULSE_SMOOTH * sample_rate))
    env = np.convolve(env, np.ones(size) / size, mode="same")
    local = maximum_filter1d(env, size=int(0.2 * sample_rate))
    norm = env / (local + 1e-12)
    peaks, _ = find_peaks(norm, height=0.3, distance=max(1, int(PULSE_DISTANCE * sample_rate)))
    peaks = peaks[local[peaks] > PULSE_LEVEL]
    return peaks / sample_rate


def _audio_fry(times, audio, sample_rate):
    """Frames whose neighbouring pulses are slow (<FRY_MAX_F0) and irregular."""
    pulses = _pulse_times(audio, sample_rate)
    flag = np.zeros(len(times), dtype=bool)
    for i, t in enumerate(times):
        near = pulses[(pulses >= t - FRY_PULSE_WINDOW) & (pulses <= t + FRY_PULSE_WINDOW)]
        if len(near) < 3:
            continue
        gaps = np.diff(near)
        if np.median(gaps) > 1 / FRY_MAX_F0 and np.std(gaps) / np.mean(gaps) > FRY_PULSE_CV:
            flag[i] = True
    return flag


def _drop_short_runs(flag, minimum):
    out = flag.copy()
    i = 0
    while i < len(flag):
        if flag[i]:
            j = i
            while j < len(flag) and flag[j]:
                j += 1
            if j - i < minimum:
                out[i:j] = False
            i = j
        else:
            i += 1
    return out


def is_fry(f0_track, frame_rate, audio=None, sample_rate=None, times=None):
    """Boolean array, aligned with f0_track: frames judged to be vocal fry (M0).

    From f0 alone: f0 below ~70 Hz, or a tracker thrashing between octaves.
    Praat's pitch floor is 60 Hz and it often locks onto the formant ring of
    each pulse, so f0 is weak evidence of fry; pass `audio`, `sample_rate` and
    the frame `times` to also test pulse spacing (slow and irregular)."""
    f0 = np.asarray(f0_track, dtype=float)
    if len(f0) == 0:
        return np.zeros(0, dtype=bool)
    low = np.zeros(len(f0), dtype=bool)
    valid = ~np.isnan(f0)
    low[valid] = f0[valid] < FRY_MAX_F0
    st = 12 * np.log2(np.where(valid, f0, 1.0))
    jump = np.zeros(len(f0), dtype=bool)
    jump[1:] = valid[1:] & valid[:-1] & (np.abs(np.diff(st)) > FRY_JUMP_ST)
    erratic = np.convolve(jump, np.ones(5))[2:2 + len(f0)] >= FRY_JUMPS_IN_5   # not mode="same": that is wrong for len < 5
    flag = low | erratic
    if audio is not None and sample_rate is not None and times is not None:
        flag |= _audio_fry(np.asarray(times, dtype=float), audio, sample_rate)
    return _drop_short_runs(flag, FRY_MIN_FRAMES)


# --- TRACKS ---

def _frame_at(audio, t, sample_rate):
    center = int(t * sample_rate)
    half = FRAME // 2
    return audio[max(0, center - half):center + half]


def take_frame_features(audio, sample_rate, tracks, i, cq=None):
    """frame_features for frame i of an analysed take, or None if the frame is too near an edge.
    analyze_take's own H1*-H2* and CPP are reused where present. Where the take says F1 isn't a
    believable resonance (tracks["f1_ok"] false: snapped onto H1, or f0 too high for LPC), F1 is
    left out of every correction and H1*-H2* is withheld, as analyze_take does: a snapped F1
    over-corrects H1*-H2* by about 14 dB (phase 9 audit)."""
    frame = _frame_at(audio, tracks["times"][i], sample_rate)
    if len(frame) < FRAME:
        return None
    f1_ok = tracks.get("f1_ok")
    believable = f1_ok is None or bool(f1_ok[i])
    formants = [tracks["f1"][i] if believable else np.nan, tracks["f2"][i], tracks["f3"][i]]
    features = frame_features(frame, sample_rate, tracks["f0"][i], formants, NOMINAL_BANDWIDTHS, cq)
    for k in ("h1h2", "cpp"):                 # reuse analyze_take's values when present
        values = tracks.get(k)
        if values is not None and not np.isnan(values[i]):
            features[k] = float(values[i])
    if not believable:
        features["h1h2"] = np.nan
    return features


def register_track(audio, sample_rate, tracks, model=None, cq_track=None, mode=None):
    """Per-frame m2 score (NaN where unvoiced), aligned with tracks['times'].
    `tracks` is analyze_take()'s result['tracks']; `mode` is the take's ("speak" uses the speech defaults)."""
    model = model or RegisterModel()
    audio = np.asarray(audio, dtype=float).flatten()
    times, f0 = np.asarray(tracks["times"]), np.asarray(tracks["f0"])
    scores = np.full(len(times), np.nan)
    for i in np.flatnonzero(~np.isnan(f0)):
        features = take_frame_features(audio, sample_rate, tracks, i, None if cq_track is None else cq_track[i])
        if features is not None:
            scores[i] = model.score(features, mode=mode)
    return scores


def _smooth(scores, frame_rate):
    """NaN-aware moving average over BREAK_SMOOTH."""
    n = max(1, int(round(BREAK_SMOOTH * frame_rate)))
    valid = ~np.isnan(scores)
    kernel = np.ones(n)
    num = np.convolve(np.where(valid, scores, 0.0), kernel, mode="same")
    den = np.convolve(valid.astype(float), kernel, mode="same")
    out = np.full(len(scores), np.nan)
    ok = valid & (den > 0)
    out[ok] = num[ok] / den[ok]
    return out


def find_breaks(times, scores):
    """Times (s) of abrupt M1<->M2 switches: where the smoothed score's mean over the next
    BREAK_WINDOW differs from that over the last BREAK_WINDOW by BREAK_SWING or more,
    whatever the absolute levels. One break per switch."""
    times = np.asarray(times, dtype=float)
    if len(times) < 2:
        return []
    rate = 1 / np.median(np.diff(times))
    smooth = _smooth(np.asarray(scores, dtype=float), rate)
    n = max(2, int(round(BREAK_WINDOW * rate)))
    padded = np.concatenate([np.full(n, np.nan), smooth, np.full(n, np.nan)])
    swing = np.zeros(len(smooth))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for i in range(len(smooth)):
            before, after = padded[i:i + n], padded[i + n:i + 2 * n]
            if np.isnan(before).sum() <= n // 2 and np.isnan(after).sum() <= n // 2:
                swing[i] = abs(np.nanmean(after) - np.nanmean(before))
    peaks, _ = find_peaks(swing, height=BREAK_SWING, distance=max(1, int(round(BREAK_MIN_GAP * rate))))
    return [float(times[i]) for i in peaks]


SPEECH_MOSTLY_CHEST = 0.6     # chest share for "mostly chest" (own judgement) ...
SPEECH_MOSTLY_CHEST_HEAD = 0.1   # ... with at most this head share ...
SPEECH_CHEST_MAX_F0 = 220.0   # ... and a median pitch at or below this (Hz). Above it, light M1 and M2
                              # speech overlap in pitch and these features can't tell them apart: a
                              # falsetto-like synthetic at 250-400 Hz reads H1-A3 28-40 dB, inside the
                              # user's modal speech (21-34 dB), and above 230 Hz H1*-H2* is withheld
                              # (analysis.F1_RELIABLE_MAX_F0); own measurement.
                              # Speech at 140-220 Hz is taken as M1 (spec premise; Roubeau et al. 2009, unverified)
SPEECH_LIGHTER_HEAD = 0.2     # head share for "some lighter phonation" (own judgement)
SPEECH_LABEL_SECONDS = 2.0    # voiced seconds needed for either label (as analysis.SECONDS_STEPS["register"])


def speech_label(summary, seconds, median_f0):
    """"mostly chest" | "some lighter phonation" | None: the only register wording an uncalibrated
    model gives speech, and only when the shares are clear-cut."""
    if seconds < SPEECH_LABEL_SECONDS:
        return None
    if summary["head_share"] >= SPEECH_LIGHTER_HEAD:
        return "some lighter phonation"
    if (summary["chest_share"] >= SPEECH_MOSTLY_CHEST and summary["head_share"] < SPEECH_MOSTLY_CHEST_HEAD
            and median_f0 <= SPEECH_CHEST_MAX_F0):
        return "mostly chest"
    return None


def take_register_summary(tracks, scores, fry, model=None, mode=None):
    """{'chest_share', 'mix_share', 'head_share', 'fry_share' (of voiced time, summing to 1),
        'median_score', 'breaks': [times in s of abrupt M1<->M2 switches], 'calibrated': bool,
        'mode', 'speech_label': see speech_label (speak mode with an uncalibrated model, else None)}"""
    scores = np.asarray(scores, dtype=float)
    fry = np.asarray(fry, dtype=bool)
    f0 = np.asarray(tracks["f0"], dtype=float)
    counted = ~np.isnan(f0) & (fry | ~np.isnan(scores))
    n = int(counted.sum())
    summary = {"chest_share": 0.0, "mix_share": 0.0, "head_share": 0.0, "fry_share": 0.0,
               "median_score": float("nan"), "breaks": [],
               "calibrated": bool(model.is_calibrated()) if model else False,
               "mode": mode, "speech_label": None}
    if n == 0:
        return summary
    breathy = counted & ~fry
    s = scores[breathy]
    summary["fry_share"] = float((counted & fry).sum() / n)
    low = np.array([mix_band(f)[0] for f in f0[breathy]])
    high = np.array([mix_band(f)[1] for f in f0[breathy]])
    summary["chest_share"] = float((s < low).sum() / n)
    summary["head_share"] = float((s > high).sum() / n)
    summary["mix_share"] = 1.0 - summary["fry_share"] - summary["chest_share"] - summary["head_share"]
    summary["median_score"] = float(np.median(s)) if len(s) else float("nan")
    summary["breaks"] = find_breaks(tracks["times"], np.where(fry, np.nan, scores))
    if mode == "speak" and not summary["calibrated"]:
        times = np.asarray(tracks["times"], dtype=float)
        step = float(np.median(np.diff(times))) if len(times) > 1 else 0.01
        summary["speech_label"] = speech_label(summary, n * step, float(np.median(f0[counted])))
    return summary
