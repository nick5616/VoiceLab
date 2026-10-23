"""Coaching logic: how far to trust a take, and what to suggest next. Pure functions, no UI.

take_quality(summary, noise) says which readings the recording can support; advice(...)
turns the readings into at most three short, kind, specific suggestions, and never speaks
about a reading take_quality marked unreliable.

Summary keys used (all optional): pitch, pitch_floor, pitch_range, delta_f, h1h2, cpp, ring,
level, snr (voice band), snr_full, clip_events, voiced_fraction, ghost_fraction (spec 01), singing,
exercise. `noise` is the denoise.noise_report dict (spec 02): noise_db, rumble_share,
lines [(Hz, dBFS)], voiced_db, snr_db, snr_voice_db.

Thresholds without a literature source come from the phase 9 audits of the user's own USB mic
(takes at -38 to -47 dBFS, SNR 11.5-19.7 dB) or are marked "own judgement".
"""
import numpy as np

import profile_store
from analysis import F1_RELIABLE_MAX_F0

# --- TAKE QUALITY ---

CPP_MIN_SNR = 20            # dB voice band; at 16 dB CPP reads 4-5 dB low (audit)
RING_MIN_SNR = 15           # dB voice band; own judgement (ring is a band-energy ratio)
FORMANT_MIN_SNR = 10        # dB voice band; own judgement
FORMANT_MAX_F0 = F1_RELIABLE_MAX_F0   # Hz; F1 snaps to H1 above this, same limit as analysis
GOOD_SNR = 20               # dB voice band for a "good" take
POOR_SNR = 10               # dB; below the worst real take (11.5 dB)
QUIET_DBFS = -40            # voiced level; the user's takes sit at -38 to -47
VERY_QUIET_DBFS = -48       # just under the quietest real take (-47)
MAX_GHOST_FRACTION = 0.15   # share of frames the voicing gate removed; own judgement
POOR_GHOST_FRACTION = 0.40  # audit: raw tracker reported 15-45% ghosts
MIN_VOICED_FRACTION = 0.10  # below this there is too little voice to judge range or floor
CLIP_FAIR_EVENTS = 5        # runs of clipped samples; own judgement
CLIP_POOR_EVENTS = 30
RUMBLE_SHARE = 0.5          # noise power below 150 Hz; the user's mic is ~0.86 (audit)
LINE_REPORT_DBFS = -85      # steady tone this loud counts as a whine; user's 4 kHz line is -73.5

METRICS = ["pitch", "pitch_floor", "pitch_range", "contour", "delta_f", "h1h2", "cpp", "ring",
           "register", "singing"]
MOVE_CLOSER = "very quiet: move closer (about 10-15 cm) and speak up a little"


def _num(value):
    """float, or None for missing / NaN."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(value) else value


def _voice_snr(summary, noise):
    """Voice-band SNR in dB from the take, else the noise report; None if unknown."""
    snr = _num(summary.get("snr"))
    return snr if snr is not None else _num((noise or {}).get("snr_voice_db"))


def take_quality(summary, noise=None):
    """{"tier": "good"|"fair"|"poor", "reasons": [str], "trust": {metric: bool},
    "why_untrusted": {metric: str}}. An unknown condition never marks a metric untrusted."""
    noise = noise or {}
    reasons, poor = [], False
    snr = _voice_snr(summary, noise)
    level = _num(summary.get("level"))
    if level is None:
        level = _num(noise.get("voiced_db"))
    voiced = _num(summary.get("voiced_fraction"))
    ghosts = _num(summary.get("ghost_fraction"))
    clips = _num(summary.get("clip_events")) or 0
    fair = False

    if level is not None and level < VERY_QUIET_DBFS:
        reasons.append(MOVE_CLOSER)
        poor = True
    elif level is not None and level < QUIET_DBFS:
        reasons.append("quiet: a little closer to the mic (about 10-15 cm) will help")
        fair = True
    if snr is not None and snr < POOR_SNR:
        reasons.append("background noise is close to your voice level")
        poor = True
    elif snr is not None and snr < GOOD_SNR:
        reasons.append("some background noise: voice-quality readings are less precise")
        fair = True
    if clips >= CLIP_POOR_EVENTS:
        reasons.append("clipping: the signal is too loud, move back a little")
        poor = True
    elif clips >= CLIP_FAIR_EVENTS:
        reasons.append("a few loud peaks clipped: move back a little")
        fair = True
    if ghosts is not None and ghosts >= POOR_GHOST_FRACTION:
        reasons.append("much of the pitch signal looked like noise rather than voice")
        poor = True
    elif ghosts is not None and ghosts > MAX_GHOST_FRACTION:
        reasons.append("some pitch readings looked like noise and were dropped")
        fair = True
    if voiced is not None and voiced < MIN_VOICED_FRACTION:
        reasons.append("very little voiced sound: speak or sing for longer")
        poor = True
    if _num(noise.get("rumble_share")) is not None and noise["rumble_share"] > RUMBLE_SHARE:
        reasons.append("desk rumble: set the mic on something soft or off the desk")
    if any(_num(level_db) is not None and level_db > LINE_REPORT_DBFS
           for _, level_db in noise.get("lines") or []):
        reasons.append("steady electrical whine detected")

    tier = "poor" if poor else "fair" if fair else "good"
    trust, why = _trust(summary, snr, voiced, ghosts, poor)
    return {"tier": tier, "reasons": reasons, "trust": trust, "why_untrusted": why}


def _trust(summary, snr, voiced, ghosts, poor):
    trust, why = {m: True for m in METRICS}, {}

    def distrust(metrics, reason):
        for metric in metrics:
            if trust[metric]:
                trust[metric], why[metric] = False, reason

    if poor:
        distrust(METRICS, "this take is too noisy or quiet to judge")
        return trust, why
    pitchy = ["pitch", "pitch_floor", "pitch_range", "contour", "singing"]
    if ghosts is not None and ghosts > POOR_GHOST_FRACTION:
        distrust(pitchy, "too many pitch readings looked like noise")
    if voiced is not None and voiced < MIN_VOICED_FRACTION:
        distrust(["pitch_floor", "pitch_range", "contour"], "not enough voiced sound")
    f0 = _num(summary.get("pitch"))
    if f0 is not None and f0 > FORMANT_MAX_F0:
        distrust(["delta_f"], "brightness can't be measured reliably above about "
                              f"{FORMANT_MAX_F0:.0f} Hz")
    if snr is not None:
        if snr < CPP_MIN_SNR:
            distrust(["h1h2", "cpp"], "too much background noise to judge weight and clarity")
        if snr < RING_MIN_SNR:
            distrust(["ring", "register"], "too much background noise to judge ring")
        if snr < FORMANT_MIN_SNR:
            distrust(["delta_f"], "too much background noise to judge brightness")
    return trust, why


# --- ADVICE ---

CENTS_OFF = 20           # signed median error counted as consistently flat or sharp (strict criterion 25 cents)
LATE_MS = 300            # reaction time 150-250 ms plus guide latency (spec 03)
EARLY_MS = -150
FLAT_INTONATION_ST = 3   # 10-90th percentile speech range; own judgement, unverified
FLOOR_DIP_ST = 2         # f0 floor this far under the target's low edge; own judgement
FLOOR_DIP_NO_TARGET_ST = 6   # ... or under the take's own median when there is no target
HIGH_NOTE_HZ = 350       # roughly F4 and up counts as a "high note" for tension; own judgement
MAX_MESSAGES = 3

BRIGHTEN = ("Your pitch is where you aimed, and your resonance is a little darker than your target. "
            "Try saying 'ee', smile slightly, and let your tongue come forward and your lips spread. "
            "Keep your larynx relaxed, not pushed up: brightness should feel easy.")
LOW_FLOOR = ("Your pitch dipped to about {floor:.0f} Hz at the low points, often at the ends of "
             "sentences. Try finishing at the same height, or with a small lift, and keep the "
             "breath flowing to the last word.")


def _reference(key, targets, baseline):
    """[low, high] from the target, else around the baseline value, else None."""
    ref = (targets or {}).get(key)
    if ref is None and baseline:
        ref = profile_store.target_from_value(key, _num(baseline.get(key)))
    return ref


def _state(value, ref):
    """"below" / "in" / "above", or None when either is missing."""
    value = _num(value)
    if ref is None or value is None:
        return None
    return "below" if value < ref[0] else "above" if value > ref[1] else "in"


class _Context:
    def __init__(self, summary, targets, baseline, mode, quality):
        self.summary, self.mode, self.quality = summary, mode, quality
        trust = quality["trust"]
        self.trust = trust
        self.pitch = _state(summary.get("pitch"), _reference("pitch", targets, baseline)) \
            if trust["pitch"] else None
        self.res = _state(summary.get("delta_f"), _reference("delta_f", targets, baseline)) \
            if trust["delta_f"] else None
        # weight "below" = H1*-H2* under its target/baseline range = heavier phonation
        self.weight = _state(summary.get("h1h2"), _reference("h1h2", targets, baseline)) \
            if trust["h1h2"] else None
        ring_ref = _reference("ring", targets, baseline)
        self.ring = _state(summary.get("ring"), ring_ref) if trust["ring"] else None
        self.target_low = ((targets or {}).get("pitch") or [None])[0]

    @property
    def floor_dips(self):
        floor, median = _num(self.summary.get("pitch_floor")), _num(self.summary.get("pitch"))
        if floor is None or not self.trust["pitch_floor"]:
            return False
        if self.target_low:
            return 12 * np.log2(floor / self.target_low) < -FLOOR_DIP_ST
        return median is not None and 12 * np.log2(floor / median) < -FLOOR_DIP_NO_TARGET_ST

    def singing_value(self, group, key):
        return _num((self.summary.get(group) or {}).get(key)) if self.trust["singing"] else None


def _pressing(c):
    return (c.pitch in ("in", "above") and c.weight == "below" and c.ring == "above")


def _tense_high(c):
    f0 = _num(c.summary.get("pitch"))
    return (c.mode == "sing" and f0 is not None and f0 >= HIGH_NOTE_HZ
            and c.weight == "below" and c.ring == "above")


def _flat_sharp(c, sign):
    cents = c.singing_value("exercise", "mean_signed_cents")
    return cents is not None and sign * cents >= CENTS_OFF


def _timing(c, late):
    lag = c.singing_value("exercise", "lag_ms")
    return lag is not None and (lag >= LATE_MS if late else lag <= EARLY_MS)


# Each rule: (key, priority (lower = first), severity, applies(ctx), text(ctx)).
# severity: "important" (comfort/strain), "suggestion", "tentative" (a guess from the numbers), "info".
RULES = [
    # Strain first: ring and delta-F rise with larynx raising and constriction while H1*-H2*
    # falls with heavier (pressed) phonation (Hanson 1997 H1*-H2*; Sundberg 1987; unverified).
    ("possible_pressing", 1, "tentative", _pressing,
     lambda c: "Your pitch, weight and ring all went up together in this take. That can mean some "
               "squeezing in the throat, though it is only a guess from the numbers. Try a loose "
               "sigh on 'hoo' at the same pitch and notice if it feels freer. If anything feels "
               "tight, rest and sip some water."),
    # Same pattern on high sung notes (spec 04; unverified).
    ("tense_high_notes", 2, "tentative", _tense_high,
     lambda c: "Your high notes sounded a little pressed, which might mean extra tension. It is a "
               "guess from the numbers. Try the note on a gentle 'oo' or a light sigh with an easy "
               "jaw, and let the volume stay small."),
    # Resonance matters about as much as pitch for perceived gender (Leung, Oates & Chan 2018,
    # JSLHR 61(2); f0 alone explained about 42% of the variance).
    ("brighten_resonance", 3, "suggestion",
     lambda c: c.pitch == "in" and c.res == "below", lambda c: BRIGHTEN),
    ("raise_pitch", 4, "suggestion",
     lambda c: c.res == "in" and c.pitch == "below",
     lambda c: "Your resonance is right where you aimed. Your pitch is a little under your target "
               "now, so try nudging it up a touch while keeping the same bright, forward sound. "
               "Small steps are fine."),
    ("one_thing_at_a_time", 5, "suggestion",
     lambda c: c.pitch == "below" and c.res == "below",
     lambda c: "Both pitch and resonance are under your targets in this take. Pick one for the "
               "next try: first a gentle lift in pitch on an easy 'ee', then bring the brightness "
               "in once that feels comfortable."),
    # Low dips are heard as a lower voice; the pitch floor is the 10th percentile (spec 01).
    ("low_pitch_floor", 6, "suggestion", lambda c: c.floor_dips,
     lambda c: LOW_FLOOR.format(floor=c.summary["pitch_floor"])),
    # Flat intonation reads as less expressive (Leung et al. 2018: intonation associated with
    # perceived gender); threshold is own judgement.
    ("flat_intonation", 7, "suggestion",
     lambda c: (c.mode == "speak" and c.trust["pitch_range"]
                and _num(c.summary.get("pitch_range")) is not None
                and c.summary["pitch_range"] < FLAT_INTONATION_ST),
     lambda c: "Your pitch stayed quite even in this take. Letting it rise and fall more on the "
               "important words can sound livelier. Try reading a sentence as if you are sharing "
               "good news."),
    # Strict in-tune is +/-25 cents, research criterion +/-50 (Pfordresher & Demorest 2021).
    ("sing_flat", 8, "suggestion", lambda c: _flat_sharp(c, -1),
     lambda c: "Your notes sat a little flat overall, about {:.0f} cents under. Try thinking the "
               "note slightly higher and brighter before you start it.".format(
                   -c.singing_value("exercise", "mean_signed_cents"))),
    ("sing_sharp", 8, "suggestion", lambda c: _flat_sharp(c, 1),
     lambda c: "Your notes sat a little sharp overall, about {:.0f} cents over. Try relaxing your "
               "jaw and starting the note softer.".format(
                   c.singing_value("exercise", "mean_signed_cents"))),
    # Reaction time plus guide latency is 150-250 ms plus 100-300 ms (spec 03 audit).
    ("sing_late", 9, "info", lambda c: _timing(c, True),
     lambda c: "You came in about {:.1f} seconds after the guide. That is very normal. Try taking "
               "your breath on the beat before and starting the note right on the next one.".format(
                   c.singing_value("exercise", "lag_ms") / 1000)),
    ("sing_early", 9, "info", lambda c: _timing(c, False),
     lambda c: "You came in a little ahead of the guide. Try waiting for the beat and letting the "
               "note start gently."),
]
# Rules that only make sense in one mode; the rest apply in both. Resonance and pitch targets
# and the sentence-ending advice are speech targets; tuning and timing are exercise results.
RULE_MODES = {"brighten_resonance": "speak", "raise_pitch": "speak", "one_thing_at_a_time": "speak",
              "low_pitch_floor": "speak", "flat_intonation": "speak",
              "tense_high_notes": "sing", "sing_flat": "sing", "sing_sharp": "sing",
              "sing_late": "sing", "sing_early": "sing"}
# What to say when a target exists but the take cannot support judging it.
METRIC_NAMES = {"pitch": "pitch", "delta_f": "brightness (resonance)", "h1h2": "weight",
                "cpp": "clarity", "ring": "ring", "pitch_range": "pitch range", "register": "register"}


def advice(summary, targets, baseline=None, mode="speak", quality=None):
    """Up to 3 messages [{"key", "text", "severity"}], most useful first. Reads nothing from a
    metric quality["trust"] marks unreliable, and says what could not be judged instead."""
    quality = quality or take_quality(summary)
    context = _Context(summary, targets or {}, baseline, mode, quality)
    messages = []
    if quality["tier"] == "poor":
        reason = quality["reasons"][0] if quality["reasons"] else "the recording is too noisy"
        return [{"key": "take_poor", "severity": "info",
                 "text": f"This take is hard to judge: {reason}. A quick re-record will give "
                         "you much more useful feedback."}]
    for key, _, severity, applies, text in sorted(RULES, key=lambda r: r[1]):
        if RULE_MODES.get(key, mode) == mode and applies(context):
            messages.append({"key": key, "text": text(context), "severity": severity})
    untrusted = [METRIC_NAMES[m] for m in METRIC_NAMES
                 if (targets or {}).get(m) is not None and not quality["trust"].get(m, True)]
    if untrusted and len(messages) < MAX_MESSAGES:
        why = next((quality["why_untrusted"][m] for m in METRIC_NAMES
                    if m in quality["why_untrusted"] and (targets or {}).get(m) is not None), "")
        messages.append({"key": "cant_judge", "severity": "info",
                         "text": f"I can't judge your {' or '.join(untrusted[:2])} in this take"
                                 + (f": {why}." if why else ".")})
    if not messages and context.pitch == "in" and context.res == "in":
        messages.append({"key": "on_target", "severity": "info",
                         "text": "Pitch and resonance both landed on your targets. Nice work. "
                                 "Notice how that felt so you can find it again."})
    return messages[:MAX_MESSAGES]
