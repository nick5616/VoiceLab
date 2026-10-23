"""Voice report: a plain-language reading of one take, for someone new to voice training.

build_report(take, profile, previous, goal) turns a take's summary and tracks into plain data:
dimensions placed on masculine / overlap / feminine zones with cited ranges, one honest overall
placement (never from pitch alone), the recording problems collapsed into a few issues, and up to
three tips for the next take that point at moments in this take. No UI; every field is
JSON-serialisable.

The zones describe tendencies of cisgender speakers' voices and how listeners hear them; they
overlap a lot, and VoiceLab's home-mic measurements are approximate. Sources sit next to the
numbers. Two own measurements calibrate them in VoiceLab's units (scripts and outputs in
C:/tmp/phase10-work/02-report/):
- "H95": the published Hillenbrand et al. (1995) data, 45 men and 48 women saying 12 hVd words;
  hand-measured values from its vowdata.dat, and analysis.analyze_take run on its recordings.
- "ARCTIC": analyze_take on 10 read sentences each from the CMU ARCTIC speakers (5 men: bdl, rms,
  jmk, ksp, awb; 2 women: slt, clb). Running speech like a real take, but a small sample.
"""
import warnings

import numpy as np

import coach
import profile_store

# --- ZONES ---

ZONE_LABELS = {"masculine": "typically masculine", "overlap": "overlap / androgynous",
               "feminine": "typically feminine"}
SPEAK_PITCH_SCALE = (80, 400)   # Hz; the speech bar of ui_common.MODE_SCALES


def _zones(scale, edges, feminine_high=True):
    """Three contiguous zones over `scale`, split at `edges` (low, high), ordered low to high."""
    order = ["masculine", "overlap", "feminine"]
    if not feminine_high:
        order.reverse()
    bounds = [scale[0], edges[0], edges[1], scale[1]]
    return [{"id": zone, "label": ZONE_LABELS[zone], "lo": float(bounds[i]), "hi": float(bounds[i + 1])}
            for i, zone in enumerate(order)]


ZONES = {
    # Speaking f0. Below 145 Hz typically masculine, 145-180 Hz overlap, above 180 typically feminine.
    # 145-165 Hz is the classic "gender-ambiguous range" (Oates & Dacakis 1983, via Dacakis, PEVOC 7),
    # which Dacakis' later listener studies found "far wider than originally believed"; listeners
    # misjudged men's voices shifted to 165 Hz and up (Gelfer & Bennett 2013, J Voice 27(5)); trans
    # women heard as men spoke at up to ~181 Hz and those heard as women from ~164 Hz (Gelfer &
    # Schofield 2000, J Voice 14(1); figures via secondary sources, the abstract only says the
    # female-perceived group was higher). H95 hand-measured: men 106-154 Hz (10th-90th pct of
    # talkers, median 131), women 195-248 Hz (median 220); VoiceLab on H95 agrees (medians 132 and
    # 218 Hz). ARCTIC running speech: men 97-126 Hz, women 184-187 Hz.
    "pitch": {"name": "Pitch", "unit": "Hz", "scale": SPEAK_PITCH_SCALE, "log": True,
              "zones": _zones(SPEAK_PITCH_SCALE, (145, 180)),
              "source": "Oates & Dacakis 1983 (145-165 Hz ambiguous range, via Dacakis PEVOC 7); "
                        "Gelfer & Schofield 2000; Gelfer & Bennett 2013; Hillenbrand et al. 1995 "
                        "data (own computation: men median 131 Hz, women 220 Hz)",
              "note": "The overlap is wide: at 145-180 Hz listeners lean on resonance to decide "
                      "(Gelfer & Bennett 2013), and shifting pitch alone rarely changes how a "
                      "voice is heard (Hillenbrand & Clark 2009)."},
    # The low points (10th percentile of voiced f0). No study found gives norms for them. ARCTIC
    # running speech: men 80-107 Hz, women 158 and 168 Hz; H95 single words read higher (men
    # median 121 Hz, women 206 Hz). Edges between the two groups; small sample. Low dips and creak
    # are heard as a lower voice (Holmberg et al. 2010 advise avoiding vocal fry).
    "pitch_floor": {"name": "Pitch floor", "unit": "Hz", "scale": SPEAK_PITCH_SCALE, "log": True,
                    "zones": _zones(SPEAK_PITCH_SCALE, (125, 155)),
                    "source": "Own measurement with VoiceLab on CMU ARCTIC read speech (5 men "
                              "80-107 Hz, 2 women 158-168 Hz); Holmberg et al. 2010 (avoid vocal fry)",
                    "note": "No study gives norms for the low points, our check had only 7 "
                            "speakers, and they move with pitch, so they count only a little in "
                            "the overall placement."},
    # 10th-90th percentile pitch spread in semitones. Listeners heard speakers with a larger
    # utterance semitone range and more upward intonation as female, though the groups' actual
    # intonation did not differ significantly (Hancock, Colton & Douglas 2014, J Voice 28(2));
    # intonation is associated with perceived gender (Leung, Oates & Chan 2018). ARCTIC read
    # speech found no difference either (men 5.3-7.3 st, women 4.5-5.0 st). So the edges mark
    # "flat" and "very melodic" speech (own judgement, unverified), not a measured sex difference.
    "pitch_range": {"name": "Intonation", "unit": "st", "scale": (0, 12), "log": False,
                    "zones": _zones((0, 12), (4, 8)),
                    "source": "Perceptual direction: Hancock, Colton & Douglas 2014; Leung, Oates & "
                              "Chan 2018. Edges (4 and 8 semitones): own judgement, unverified",
                    "note": "A cue in how listeners hear a voice more than a difference between "
                            "groups: measured melody barely differs between women and men "
                            "(Hancock et al. 2014; our own check). It counts only a little."},
    # Formant dispersion over F1-F4 as analysis.delta_f measures it. ARCTIC running speech: men
    # 917-968 Hz (median 963), women 1087 and 1162 Hz. H95 single words read ~90 Hz higher (VoiceLab
    # on H95: men median 1058, 75th / 90th pct 1080 / 1126; women median 1212, 5th / 10th pct 1149 /
    # 1161; hand-measured men 1013, women 1169, i.e. tract length 17.3 vs 14.8 cm). Speech edges =
    # the word edges (1090 / 1150) moved down that 90 Hz; they also separate every ARCTIC speaker.
    # VoiceLab reads 34-47 Hz above the hand measurements, more with a high formant ceiling
    # (+130-170 Hz at 6250-6500 Hz). Resonance matters about as much as pitch: both are needed to
    # change the perceived sex of a sentence (Hillenbrand & Clark 2009); formants decide in the
    # 145-165 Hz range (Gelfer & Bennett 2013) and contribute more than earlier thought (Leung et al.
    # 2021, JSLHR 64(7)); f0 and tract length shift judgements with no sharp threshold (Neuhaus,
    # Scherer & Whitfield 2024, J Voice).
    "delta_f": {"name": "Resonance", "unit": "Hz", "scale": (800, 1400), "log": False,
                "zones": _zones((800, 1400), (1000, 1060)),
                "source": "Own measurement with VoiceLab: CMU ARCTIC read speech (men 917-968 Hz, "
                          "women 1087-1162 Hz) and Hillenbrand et al. 1995 recordings (single words, "
                          "~90 Hz higher); weight from Hillenbrand & Clark 2009, Gelfer & Bennett "
                          "2013, Leung et al. 2021",
                "note": "Depends on the words you say and on the formant ceiling the analysis "
                        "picks, and the running-speech edges rest on only 7 speakers, so treat it "
                        "as approximate (roughly ±50 Hz). A vowel check gives the fairest reading."},
    # Corrected H1*-H2*, a proxy for a lighter (more open) or heavier phonation. Women are on
    # average breathier, with very large differences within each gender (Klatt & Klatt 1990, JASA
    # 87(2)); men's source measures are lower on average (Hanson & Chuang 1999); but for
    # high-pitched talkers there is no significant sex difference in H1*-H2* (Iseli, Shue & Alwan
    # 2007, JASA 121(4)), and it rises with f0 in low voices, so it partly repeats pitch. Own
    # measurements disagree with each other: H95 words, no difference (men median -2.6 dB, women
    # -2.9 dB); ARCTIC read speech, men -0.9..+8.3 dB, women +9.0 and +12.3 dB. A wide overlap
    # covers that. Home mics read high: the user's takes 7-15 dB (desk rumble under H1, spec 01).
    "h1h2": {"name": "Vocal weight", "unit": "dB", "scale": (-10, 15), "log": False,
             "zones": _zones((-10, 15), (0, 9)),
             "source": "Direction: Klatt & Klatt 1990; Hanson & Chuang 1999; no sex difference at "
                       "high pitch: Iseli, Shue & Alwan 2007. Edges: own measurement with VoiceLab "
                       "on Hillenbrand et al. 1995 and CMU ARCTIC (mixed results), unverified",
             "note": "A rough proxy, not a measure of the vocal folds. Studies and our own checks "
                     "disagree about how much it differs between women and men, and the mic and "
                     "desk rumble shift it by several dB, so it doesn't count toward the overall "
                     "placement."},
    # CPP (VoiceLab's, not the published CPPS). Women have lower cepstral values on average
    # (Buckley, Abur & Stepp 2023, AJSLP 32(4)), but a breathy voice is a mixed cue for gender
    # (Leung, Oates & Chan 2018) and costs stamina. Own measurements: H95 words, men median 28.4 dB,
    # women 25.7; ARCTIC read speech, men 20.5-25.0, women 24.0-24.2 (no difference). So the
    # overlap spans the healthy range; only very breathy (<12) or very clear (>28) leave it.
    # Higher = clearer, so the zones run feminine to masculine. CPP falls with noise and room echo
    # (4-5 dB low at 16 dB SNR, phase 9 audit): the user's takes read 12.6-17.2 dB.
    "cpp": {"name": "Clarity", "unit": "dB", "scale": (8, 32), "log": False,
            "zones": _zones((8, 32), (12, 28), feminine_high=False),
            "source": "Direction: Buckley, Abur & Stepp 2023; mixed evidence: Leung, Oates & Chan "
                      "2018. Edges: own measurement with VoiceLab on Hillenbrand et al. 1995 and "
                      "CMU ARCTIC, unverified",
            "note": "Shown mainly for comfort and health: a clear, easy voice suits every goal. "
                    "It barely differs between women and men in running speech, and noise and "
                    "room echo lower it a lot, so it doesn't count toward the overall placement."},
}
# Typical values where a dimension pulls fully one way (score -1 / +1): pitch from VoiceLab on
# H95 (medians 132 / 218 Hz); floor and resonance from ARCTIC running speech (men's median 97 Hz
# and 963 Hz; women's floor mean 163 Hz, resonance 1122 Hz = H95 women's median minus 90 Hz);
# intonation, weight and clarity a zone-width past their edges (own judgement).
ANCHORS = {"pitch": (132, 218), "pitch_floor": (97, 163), "pitch_range": (2, 10),
           "delta_f": (963, 1122), "h1h2": (-5, 13), "cpp": (31, 10)}
# Running speech reads lower than a vowel check's hVd words: H95 words vs ARCTIC sentences,
# men 1058 vs 963 Hz, women 1212 vs 1124 Hz (own measurement; corpus and speakers confounded).
VOWEL_CHECK_OFFSET_HZ = 90
# Each dimension's share of the overall placement. Pitch and resonance carry most and equally:
# speaking f0 explained 41.6% of the variance in perceived gender (Leung, Oates & Chan 2018,
# JSLHR 61(2)); f0 and formants together are needed and f0 is "somewhat more important"
# (Hillenbrand & Clark 2009), while formants contribute more in connected speech than earlier
# thought (Leung et al. 2021); pitch and resonance account for most of how listeners perceive
# gender (Hancock & Garabedian 2013). Intonation is a perceived cue (Leung et al. 2018; Hancock
# et al. 2014); the floor mostly repeats pitch. Weight and clarity get 0: mixed evidence (Leung et
# al. 2018; our own checks disagree) and dominated by the mic. Exact split: own judgement.
WEIGHTS = {"pitch": 0.44, "delta_f": 0.44, "pitch_range": 0.06, "pitch_floor": 0.06,
           "h1h2": 0.0, "cpp": 0.0}
MAJOR = ("pitch", "delta_f")
DIMENSIONS_SPEAK = ["pitch", "pitch_floor", "pitch_range", "delta_f", "h1h2", "cpp"]
DIMENSIONS_SING = ["range", "tuning", "steadiness", "register"]

# Overall scale: score -1 (fully masculine) .. +1 (fully feminine), shown as position 0-1.
OVERALL_EDGES = (0.25, 0.75)    # position; = score -0.5 / +0.5, where each dimension's zone edges sit
# Uncertainty of one dimension's score by confidence (score units); own judgement.
SCORE_SPREAD = {"high": 0.15, "medium": 0.3, "low": 0.5}
MIN_TRUSTED = 2                 # dimensions needed for an overall placement (spec)

# --- CONFIDENCE ---

LEVELS = ["none", "low", "medium", "high"]
# Most a reading can claim whatever the recording; spec 01's per-take confidence may lift delta_f.
CONFIDENCE_CAP = {"h1h2": ("low", "a rough proxy: the mic and desk rumble shift it by several dB"),
                  "cpp": ("low", "noise-sensitive: background noise and room echo lower it")}
FALLBACK_CAP = {"delta_f": ("medium", "resonance from running speech depends on the vowels spoken")}
# coach.take_quality reasons worth one banner of their own: they say what to change at the mic.
MIC_ADVICE_WORDS = ("quiet", "closer", "clip", "rumble", "whine")
CEILING_MISMATCH_HZ = 750      # take's formant ceiling this far from the vowel check's: low
                                # confidence (3 FORMANT_CEILINGS steps; H95: +40-130 Hz; own judgement)

# --- TIPS ---

MAX_TIPS = 3
DIP_ST = 3.0                # a dip: smoothed f0 this far under the take's median; own judgement
DIP_MIN_S = 0.06            # shortest dip counted
PHRASE_GAP_S = 0.3          # an unvoiced gap this long ends a phrase (sentence pauses); own judgement
PHRASE_END_S = 0.5          # a dip ending this close to a phrase end is "at the end of a sentence"
FLOOR_TARGET_ST = 2         # with a pitch target, dips this far under its low edge (as coach.py)
MOMENT_PAD_S = 0.15         # context around a highlighted moment
MAX_MOMENTS = 5
STRETCH_S = 0.8             # window for brightest / darkest / highest stretches
BEST_WINDOW_S = 1.5         # window for the best moment
MIN_WINDOW_FILL = 0.2       # share of a window's frames that must hold a reading (real takes
                            # are only 16-35% voiced, phase 10 measurement; own judgement)
ROUNDED_F2 = 1200           # Hz; median F2 under this in a dark stretch: rounded back vowels
                            # ('oo', 'oh': men's F2 ~ 900-1100 Hz in H95; own judgement)
SMOOTH_S = 0.05             # median smoothing of f0 before finding dips
# Smallest change worth naming against the last take or the baseline; own judgement, about the
# take-to-take spread of the user's takes (pitch 1 st; ΔF frame IQR 70-180 Hz, median steadier).
CHANGE_MIN = {"pitch": 1.0, "pitch_floor": 1.0, "pitch_range": 1.5, "delta_f": 40.0,
              "h1h2": 2.0, "cpp": 1.5}
CHANGE_WORDS = {"pitch": ("higher", "lower"), "pitch_floor": ("higher low points", "lower low points"),
                "pitch_range": ("more varied", "flatter"), "delta_f": ("brighter", "darker"),
                "h1h2": ("lighter", "heavier"), "cpp": ("clearer", "breathier")}

EXPLANATIONS = {
    "pitch": "How high or low your voice sits most of the time; one of the two biggest cues "
             "listeners use, together with resonance.",
    "pitch_floor": "The low points of your pitch, often at the ends of sentences; deep dips are "
                   "heard as a lower voice even when the average is higher.",
    "pitch_range": "How much your pitch moves while you talk; more melody tends to be heard as "
                   "more feminine and more expressive.",
    "delta_f": "How bright or dark your voice sounds, set by the size and shape of your throat and "
               "mouth; as important as pitch for how a voice is heard.",
    "h1h2": "Whether your voice sounds light or heavy; a rough estimate, so it doesn't count "
            "toward the overall placement.",
    "cpp": "How clear or breathy your voice sounds; mostly about ease and comfort, so it doesn't "
           "count toward the overall placement.",
    "range": "The span between the lowest and highest notes you sang.",
    "tuning": "How close your notes landed to the notes you aimed for.",
    "steadiness": "How steady your held notes stayed.",
    "register": "How much you sang in a lighter head voice versus chest voice.",
}
NAMES = {"pitch": "Pitch", "pitch_floor": "Pitch floor", "pitch_range": "Intonation",
         "delta_f": "Resonance", "h1h2": "Vocal weight", "cpp": "Clarity", "range": "Range",
         "tuning": "Tuning", "steadiness": "Steadiness", "register": "Register"}
# Plain names for "couldn't judge your ..." sentences.
PLAIN = {"pitch": "pitch", "pitch_floor": "low points", "pitch_range": "intonation",
         "delta_f": "brightness", "h1h2": "weight", "cpp": "clarity", "range": "range",
         "tuning": "tuning", "steadiness": "steadiness", "register": "register"}

CAVEAT = ("These zones describe tendencies across many speakers' voices, and they overlap a lot. "
          "They are not rules, they don't say who you are, and a home mic gives approximate "
          "numbers. Use them as a map for practice, at whatever pace feels good.")
SING_CAVEAT = ("Singing readings from a home mic are approximate, and the register estimate is a "
               "guess until the model has learned your voice. Use them as a guide for practice, "
               "not as a grade.")
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
GOAL_WORDS = {"feminine": "feminine", "androgynous": "androgynous", "masculine": "masculine"}

# Sing-mode bars (min, max): semitone span, cents off, drift in cents, head share in %.
SING_SCALES = {"range": (0, 36), "tuning": (0, 50), "steadiness": (0, 50), "register": (0, 100)}


# --- PUBLIC HELPERS ---

def zones(key):
    """ZONES[key], or None for a dimension without zones."""
    return ZONES.get(key)


def _num(value):
    """float, or None for missing / NaN / not a number."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(value) or np.isinf(value) else value


def _scaled(key, value):
    return np.log2(value) if ZONES[key]["log"] else value


def position(key, value):
    """0-1 on the dimension's scale (log for pitch), clipped; None without a value or scale."""
    value = _num(value)
    if value is None:
        return None
    if key in ZONES:
        lo, hi = ZONES[key]["scale"]
        if ZONES[key]["log"]:
            if value <= 0:
                return 0.0
            value, lo, hi = np.log2(value), np.log2(lo), np.log2(hi)
    elif key in SING_SCALES:
        lo, hi = SING_SCALES[key]
    else:
        return None
    return float(np.clip((value - lo) / (hi - lo), 0, 1))


def zone_of(key, value):
    """"masculine" | "overlap" | "feminine" for a value, or None."""
    value, spec = _num(value), ZONES.get(key)
    if value is None or spec is None:
        return None
    for zone in spec["zones"]:
        if value < zone["hi"]:
            return zone["id"]
    return spec["zones"][-1]["id"]


def score(key, value):
    """Pull of one value on the overall scale: -1 fully masculine .. +1 fully feminine, 0 at the
    middle of the overlap zone and +/-0.5 at its edges (piecewise linear, log for pitch)."""
    value, spec = _num(value), ZONES.get(key)
    if value is None or spec is None or (spec["log"] and value <= 0):
        return None
    overlap = next(z for z in spec["zones"] if z["id"] == "overlap")
    masc, fem = ANCHORS[key]
    flip = fem < masc                           # clarity: higher reads masculine
    lo_edge, hi_edge = sorted((overlap["lo"], overlap["hi"]))
    edge_m, edge_f = (hi_edge, lo_edge) if flip else (lo_edge, hi_edge)
    xs = [_scaled(key, v) for v in (masc, edge_m, edge_f, fem)]
    ys = [-1.0, -0.5, 0.5, 1.0]
    if flip:
        xs, ys = xs[::-1], ys[::-1]
    return float(np.interp(_scaled(key, value), xs, ys))


def live_zones(mode):
    """The zones the live pitch graph shades: speak -> the pitch zones (a copy), sing -> None."""
    if mode != "speak":
        return None
    spec = ZONES["pitch"]
    return {"key": "pitch", "unit": spec["unit"], "scale": tuple(spec["scale"]), "log": spec["log"],
            "zones": [dict(z) for z in spec["zones"]], "source": spec["source"]}


# --- CONFIDENCE ---

def _cap(level, cap):
    return LEVELS[min(LEVELS.index(level), LEVELS.index(cap))]


def _spec01_confidence(take, key):
    """(level, reason) from spec 01's take["confidence"], or None when absent / unreadable."""
    conf = take.get("confidence")
    if not isinstance(conf, dict) or key not in conf:
        return None
    entry, reasons = conf[key], conf.get("reasons")
    if isinstance(entry, dict):
        level, reason = entry.get("level") or entry.get("confidence"), entry.get("reason")
    else:
        level = entry
        reason = reasons.get(key) if isinstance(reasons, dict) else None
    return (level, reason) if level in LEVELS else None


def _confidence(take, key, value, quality, profile=None):
    """(level, reason or None) for one speak dimension: coach.take_quality gates, spec 01's
    confidence grades when present, and weak proxies are capped."""
    if value is None:
        note = take.get("resonance_note") if key == "delta_f" else None
        return "none", (note[0].lower() + note[1:]) if note else "not measured in this take"
    if not quality["trust"].get(key, True):
        return "none", quality["why_untrusted"].get(key) or "this take can't support it"
    reason = None
    graded = _spec01_confidence(take, key)
    if graded is not None:
        level, reason = graded
        if level == "none":
            return "none", reason or "this take can't support it"
    else:
        level = "high" if quality["tier"] == "good" else "medium"
        if key in FALLBACK_CAP and LEVELS.index(level) > LEVELS.index(FALLBACK_CAP[key][0]):
            level, reason = FALLBACK_CAP[key]
    if key in CONFIDENCE_CAP and LEVELS.index(level) > LEVELS.index(CONFIDENCE_CAP[key][0]):
        level, reason = CONFIDENCE_CAP[key]
    if key == "delta_f":
        own, calibrated = _num(take.get("formant_ceiling")), _num(getattr(profile, "formant_ceiling", None))
        if own and calibrated and abs(own - calibrated) >= CEILING_MISMATCH_HZ and not _vowel_delta_f(take):
            level = _cap(level, "low")
            reason = (f"measured with a {own:.0f} Hz formant ceiling, while your vowel check found "
                      f"{calibrated:.0f} Hz")
    return level, reason


def _vowel_delta_f(take):
    """Vowel-balanced ΔF when the take holds a vowel check (analysis.analyze_vowels result)."""
    check = take.get("vowel_check") or (take if isinstance(take.get("vowels"), dict) else None)
    return _num((check or {}).get("delta_f"))


# --- FORMATTING ---

def _fmt(key, value):
    if value is None:
        return "—"
    if key in ("pitch", "pitch_floor", "delta_f"):
        return f"{value:.0f} Hz"
    if key == "pitch_range":
        return f"{value:.1f} semitones"
    if key == "h1h2":
        return f"{value:+.1f} dB"
    if key == "cpp":
        return f"{value:.1f} dB"
    if key == "range":
        return f"{value:.0f} semitones"
    if key in ("tuning", "steadiness"):
        return f"±{value:.0f} cents"
    if key == "register":
        return f"{value:.0f}% head voice"
    return f"{value:g}"


def _note(hz):
    """Nearest note name, e.g. "A3" (as analysis.hz_to_note, without importing the analysis)."""
    midi = int(round(69 + 12 * np.log2(hz / 440.0)))
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def _secs(a, b=None):
    return f"{a:.1f} s" if b is None else f"{a:.1f}–{b:.1f} s"


def _join(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _cap_first(text):
    return text[:1].upper() + text[1:] if text else text


def _zone_phrase(zone):
    return {"masculine": "in the typically masculine range", "feminine": "in the typically feminine range",
            "overlap": "in the overlap zone"}[zone]


def _details(key, value, take, profile):
    spec = ZONES[key]
    edges = [z for z in spec["zones"]]
    zone_text = "; ".join(f"{z['label']} {z['lo']:g}–{z['hi']:g} {spec['unit']}" for z in edges)
    if key == "pitch":
        head = f"Median f0 of voiced frames {value:.1f} Hz"
        if take.get("note"):
            head += f" (about {take['note']})"
    elif key == "pitch_floor":
        head = f"10th percentile of voiced f0: {value:.1f} Hz"
    elif key == "pitch_range":
        head = f"10th–90th percentile f0 spread: {value:.1f} st"
    elif key == "delta_f":
        vtl = 35000 / (2 * value)
        head = f"ΔF (formant dispersion, F1–F4) {value:.0f} Hz ≈ apparent vocal tract length {vtl:.1f} cm"
        if _num(take.get("formant_ceiling")):
            head += f"; formant ceiling {take['formant_ceiling']:.0f} Hz"
        if _vowel_delta_f(take):
            head += (f"; from the vowel check ({_vowel_delta_f(take):.0f} Hz on single words, shown "
                     f"{VOWEL_CHECK_OFFSET_HZ} Hz lower to match running speech)")
        vb = getattr(profile, "vowel_baseline", None) or {}
        if _num(vb.get("delta_f")):
            head += f"; your vowel-check baseline {vb['delta_f']:.0f} Hz"
    elif key == "h1h2":
        head = f"H1*–H2* (formant-corrected) {value:+.1f} dB, median of voiced frames"
    else:
        head = f"CPP {value:.1f} dB (VoiceLab's cepstral peak prominence, not the published CPPS)"
        snr = _num(take.get("snr"))
        if snr is not None:
            head += f"; voice-band SNR {snr:.0f} dB"
    return f"{head}. Zones: {zone_text}. Source: {spec['source']}."


# --- TRACKS ---

def _tracks(take):
    """(times, {name: array}) with float arrays, or (None, {}) without usable tracks. A spec 01
    display track ("<name>_smooth") replaces the raw one where present."""
    tracks = take.get("tracks") or {}
    times = tracks.get("times")
    if times is None or len(times) < 2:
        return None, {}
    times = np.asarray(times, dtype=float)
    out = {}
    for name in ("f0", "delta_f", "h1h2", "cpp", "f1", "f2"):
        values = tracks.get(name + "_smooth", tracks.get(name))
        if values is not None and len(values) == len(times):
            out[name] = np.asarray(values, dtype=float)
    return times, out


def _rolling(values, n, fn):
    """fn over centred windows of n frames needing MIN_WINDOW_FILL readings; NaN elsewhere."""
    n = max(1, int(n))
    padded = np.pad(values, (n // 2, n - 1 - n // 2), constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, n)
    enough = (~np.isnan(windows)).sum(axis=1) >= max(1, MIN_WINDOW_FILL * n)
    out = np.full(len(values), np.nan)
    if enough.any():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            out[enough] = fn(windows[enough], axis=1)
    return out


def _runs(mask):
    """(start, end) index pairs of True runs, end exclusive."""
    padded = np.concatenate([[False], np.asarray(mask, dtype=bool), [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[::2], edges[1::2]))


def _phrases(f0, step):
    """(start, end) frame ranges of voiced phrases: voiced runs joined across gaps < PHRASE_GAP_S."""
    runs = _runs(~np.isnan(f0))
    phrases = []
    for start, end in runs:
        if phrases and (start - phrases[-1][1]) * step < PHRASE_GAP_S:
            phrases[-1] = (phrases[-1][0], end)
        else:
            phrases.append((start, end))
    return phrases


def _window(times, i, seconds, step):
    """(start_s, end_s) of a window of `seconds` centred on frame i, inside the take."""
    half = seconds / 2
    start = max(float(times[0]), float(times[i]) - half)
    end = min(float(times[-1]) + step, start + seconds)
    return round(start, 2), round(end, 2)


def _moment(times, a, b, step):
    """(start_s, end_s) for frames a..b-1 with a little context, inside the take."""
    start = max(float(times[0]), float(times[a]) - MOMENT_PAD_S)
    end = min(float(times[-1]) + step, float(times[b - 1]) + step + MOMENT_PAD_S)
    return round(start, 2), round(end, 2)


def _merge(moments):
    merged = []
    for start, end in sorted(moments):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def find_dips(times, f0, reference=None):
    """Low dips of a pitch track: [{"start", "end", "f0", "final"}] where the smoothed f0 falls
    DIP_ST under the take's median (and under `reference` Hz when given) for DIP_MIN_S or more.
    "final" = the dip ends near the end of a phrase (a sentence end)."""
    step = float(np.median(np.diff(times)))
    voiced = f0[~np.isnan(f0)]
    if len(voiced) < 20:
        return []
    smooth = _rolling(f0, round(SMOOTH_S / step) | 1, np.nanmedian)
    smooth[np.isnan(f0)] = np.nan
    limit = float(np.median(voiced)) * 2 ** (-DIP_ST / 12)
    if reference is not None:
        limit = min(limit, reference)
    low = smooth < limit
    phrase_ends = [end for _, end in _phrases(f0, step)]
    dips = []
    for a, b in _runs(low):
        if (b - a) * step < DIP_MIN_S:
            continue
        end_phrase = next((e for e in phrase_ends if e >= b), None)
        final = end_phrase is not None and (end_phrase - b) * step <= PHRASE_END_S
        dips.append({"start": int(a), "end": int(b), "f0": float(np.nanmin(f0[a:b])), "final": final})
    return dips


def _extreme_stretch(times, values, highest, seconds=STRETCH_S, away_from=None):
    """(start_s, end_s, centre value, frame) of the window with the highest (or lowest) rolling
    median; `away_from` (a frame) excludes windows overlapping the window centred there."""
    step = float(np.median(np.diff(times)))
    n = round(seconds / step)
    rolled = _rolling(values, n, np.nanmedian)
    if away_from is not None:
        rolled[max(0, away_from - n):away_from + n] = np.nan
    if np.all(np.isnan(rolled)):
        return None
    i = int(np.nanargmax(rolled) if highest else np.nanargmin(rolled))
    start, end = _window(times, i, seconds, step)
    return start, end, float(rolled[i]), i


# --- TAKE-LEVEL HELPERS ---

def _quality(take):
    """coach.take_quality on the take, robust to odd stored values."""
    try:
        return coach.take_quality(take, take.get("noise"))
    except (TypeError, ValueError, KeyError):
        return {"tier": "fair", "reasons": [], "trust": {m: True for m in coach.METRICS},
                "why_untrusted": {}}


def _comparable(take, previous):
    """The previous take when it is the same task and mode, else None."""
    if not previous or previous is take:
        return None
    same_task = profile_store.task_of(previous) == profile_store.task_of(take)
    same_mode = (previous.get("mode") or "speak") == (take.get("mode") or "speak")
    return previous if same_task and same_mode else None


def _difference(key, value, other):
    if key in ("pitch", "pitch_floor"):
        return 12 * np.log2(value / other)
    return value - other


def _change_text(key, value, other, vs):
    """{"vs", "text"} naming a change against the previous take or the baseline."""
    diff = _difference(key, value, other)
    against = "your last take" if vs == "previous" else "your baseline"
    unit = " st" if key in ("pitch", "pitch_floor", "pitch_range") else (
        " Hz" if key == "delta_f" else " dB")
    if abs(diff) < CHANGE_MIN[key]:
        return {"vs": vs, "text": f"About the same as {against}"}
    word = CHANGE_WORDS[key][0 if diff > 0 else 1]
    number = f"{diff:+.1f}{unit}" if unit != " Hz" else f"{diff:+.0f}{unit}"
    return {"vs": vs, "text": f"{_cap_first(word)} than {against} ({number})"}


# --- SPEAK DIMENSIONS ---

def _speak_dimension(key, take, quality, profile, previous, prev_quality):
    spec = ZONES[key]
    value = _num(take.get(key))
    if key == "delta_f" and _vowel_delta_f(take):
        value = _vowel_delta_f(take) - VOWEL_CHECK_OFFSET_HZ   # on the running-speech scale
    level, reason = _confidence(take, key, value, quality, profile)
    trusted = level != "none"
    target = (getattr(profile, "targets", None) or {}).get(key)
    baseline = _num((getattr(profile, "baseline", None) or {}).get(key))
    zone = zone_of(key, value) if trusted else None
    change = None
    if trusted:
        prev_value = _num(previous.get(key)) if previous else None
        if prev_value is not None and prev_quality["trust"].get(key, True):
            change = _change_text(key, value, prev_value, "previous")
        elif baseline is not None:
            change = _change_text(key, value, baseline, "baseline")
    if trusted and level == "low":
        headline = f"{spec['name']} reads {_zone_phrase(zone)} (a rough reading)"
    elif trusted:
        verb = "sits" if key in ("pitch", "pitch_floor") else "is"
        headline = f"{spec['name']} {verb} {_zone_phrase(zone)}"
        if change and not change["text"].startswith("About"):
            headline += f", {change['text'][0].lower() + change['text'][1:].split(' (')[0]}"
    else:
        headline = _cap_first(reason)
    return {"key": key, "name": spec["name"], "value": value if trusted else None,
            "display": _fmt(key, value) if trusted else "—", "unit": spec["unit"],
            "position": position(key, value) if trusted else None, "zone": zone,
            "zone_label": ZONE_LABELS[zone] if zone else None,
            "confidence": level, "reason": reason, "headline": headline,
            "explanation": EXPLANATIONS[key],
            "details": _details(key, value, take, profile) if value is not None else
                       f"No reading. Source for the zones: {spec['source']}.",
            "target": tuple(float(v) for v in target) if target else None,
            "baseline": baseline, "change": change}


def _issues(dimensions, quality):
    """Untrusted dimensions grouped by cause: one entry per cause, not one per bar."""
    if quality["tier"] == "poor":
        cause = quality["reasons"][0] if quality["reasons"] else "the recording is too noisy"
        return [{"text": f"This take is hard to judge: {cause}. A quick re-record will give you "
                         "much more useful feedback.",
                 "affects": [d["key"] for d in dimensions]}]
    groups = {}
    for d in dimensions:
        if d["confidence"] == "none":
            groups.setdefault(d["reason"], []).append(d["key"])
    issues = []
    for reason, keys in groups.items():
        if "judge" in reason or "can't be measured" in reason:   # coach's reasons name the reading
            text = f"{_cap_first(reason)}."
        else:
            text = f"Couldn't judge {_join(PLAIN[k] for k in keys)}: {reason}."
        issues.append({"text": text, "affects": keys})
    for reason in quality["reasons"]:               # mic advice that touches no single reading
        if any(word in reason for word in MIC_ADVICE_WORDS):
            issues.append({"text": f"{_cap_first(reason)}.", "affects": []})
    return issues


# --- OVERALL ---

def _direction(s):
    return "feminine" if s > 0.5 else "masculine" if s < -0.5 else "neutral"


def _overall_zone(pos):
    return "masculine" if pos < OVERALL_EDGES[0] else "feminine" if pos > OVERALL_EDGES[1] else "overlap"


def _overall(dimensions):
    """Weighted placement of the trusted dimensions with an uncertainty range (dimensions that
    couldn't be judged widen it to their full span), the pulls, and a plain sentence."""
    by_key = {d["key"]: d for d in dimensions}
    trusted = {k: d for k, d in by_key.items() if d["confidence"] != "none" and WEIGHTS.get(k)}
    empty = {"position": None, "range": None, "zone": None, "confidence": "none", "pulls": []}
    missing = [k for k in MAJOR if k not in trusted]
    if len(trusted) < MIN_TRUSTED or missing:
        if not trusted:
            reason = "this take can't support any of the readings"
        elif missing and len(missing) < len(MAJOR):
            why = by_key[missing[0]]["reason"]
            reason = (f"{PLAIN[missing[0]]} couldn't be judged ({why}), and pitch and resonance are "
                      "both needed: one alone doesn't decide how a voice is heard")
        else:
            reason = "too few readings this take can support"
        return empty | {"text": f"No overall placement: {reason}.", "reason": reason}
    scores = {k: score(k, d["value"]) for k, d in trusted.items()}
    total = sum(WEIGHTS[k] for k in scores)
    point = sum(WEIGHTS[k] * s for k, s in scores.items()) / total
    lo = hi = 0.0
    for key, weight in WEIGHTS.items():
        if not weight:
            continue
        if key in scores:
            spread = SCORE_SPREAD[trusted[key]["confidence"]]
            lo += weight * max(-1.0, scores[key] - spread)
            hi += weight * min(1.0, scores[key] + spread)
        else:
            lo, hi = lo - weight, hi + weight
    weights = sum(WEIGHTS.values())
    lo, hi = lo / weights, hi / weights
    pos = (point + 1) / 2
    zone = _overall_zone(pos)
    levels = [trusted[k]["confidence"] for k in MAJOR]
    if "low" in levels:
        confidence = "low"
    elif all(level == "high" for level in levels) and hi - lo < 0.5:
        confidence = "high"
    else:
        confidence = "medium"
    pulls = [{"key": k, "direction": _direction(s)} for k, s in scores.items()]
    return {"position": round(float(pos), 3), "range": (round(float((lo + 1) / 2), 3),
                                                         round(float((hi + 1) / 2), 3)),
            "zone": zone, "confidence": confidence, "pulls": pulls,
            "text": _overall_text(trusted, scores, zone), "reason": None}


def _overall_text(trusted, scores, zone):
    """e.g. "Pitch is in the typically feminine range; resonance is still in the overlap zone, and
    it pulls the overall impression toward androgynous." """
    zp, zr = trusted["pitch"]["zone"], trusted["delta_f"]["zone"]
    toward = {"overlap": "androgynous", "feminine": "feminine", "masculine": "masculine"}
    if zp == zr:
        text = (f"Pitch and resonance are both {_zone_phrase(zp)}, so the overall impression is "
                f"{ZONE_LABELS[zone]}.")
    elif "overlap" in (zp, zr):
        mover, other = ("delta_f", "pitch") if zr == "overlap" else ("pitch", "delta_f")
        names = {"pitch": "pitch", "delta_f": "resonance"}
        if zone == "overlap":
            text = (f"{_cap_first(names[other])} is {_zone_phrase(trusted[other]['zone'])}; "
                    f"{names[mover]} is still in the overlap zone, and it pulls the overall "
                    f"impression toward {toward['overlap']}.")
        else:
            text = (f"{_cap_first(names[other])} is {_zone_phrase(trusted[other]['zone'])} and "
                    f"carries the overall impression; {names[mover]} is in the overlap zone and "
                    f"softens it.")
    else:
        lands = ("in the overlap zone" if zone == "overlap" else f"nearer {ZONE_LABELS[zone]}")
        text = (f"Pitch is {_zone_phrase(zp)}; resonance is {_zone_phrase(zr)}. They pull in "
                f"opposite directions, so the overall impression lands {lands}.")
    against = "neutral" if zone == "overlap" else zone
    minor = [k for k in trusted if k not in MAJOR and trusted[k]["confidence"] != "low"
             and _direction(scores[k]) not in ("neutral", against)]
    if minor:
        text += " Smaller cues: " + _join(
            f"{NAMES[k].lower()} leans {_direction(scores[k])}" for k in minor) + "."
    return text


# --- SPEAK TIPS ---

def _goal_reference(goal, profile):
    """Pitch below which a dip matters for this goal, in Hz, or None."""
    target = (getattr(profile, "targets", None) or {}).get("pitch")
    if target:
        return target[0] * 2 ** (-FLOOR_TARGET_ST / 12)
    edges = {z["id"]: z for z in ZONES["pitch_floor"]["zones"]}
    if goal == "feminine":
        return edges["feminine"]["lo"]
    if goal == "androgynous":
        return edges["overlap"]["lo"]
    return None


def _floor_tip(times, f0, goal, profile):
    if goal == "masculine":
        return None
    step = float(np.median(np.diff(times)))
    dips = find_dips(times, f0, _goal_reference(goal, profile))
    if not dips:
        return None
    dips.sort(key=lambda d: d["f0"])
    shown = sorted(dips[:MAX_MOMENTS], key=lambda d: d["start"])
    lowest = min(d["f0"] for d in shown)
    at = _join(f"{times[d['start']]:.1f}" for d in shown) + " s"
    final = sum(d["final"] for d in shown) >= max(1, len(shown) / 2)
    where = ("" if not final else ", the end of a sentence" if len(shown) == 1 else
             ", the ends of sentences")
    text = f"Your pitch dropped to about {lowest:.0f} Hz at {at}{where}."
    if goal in ("feminine", "androgynous"):
        text += (" Next take, keep the last word up: finish at the same height or with a small "
                 "lift, and keep the breath going to the end.")
    moments = _merge(_moment(times, d["start"], d["end"], step) for d in shown)
    return {"key": "pitch_floor_dips", "text": text, "moments": moments,
            "kind": "tip" if goal else "info", "dimension": "pitch_floor"}


def _pitch_reference(goal, profile):
    """Pitch (Hz) a tip aims toward: the near edge of the pitch target, else of the goal's zone."""
    target = (getattr(profile, "targets", None) or {}).get("pitch")
    edges = {z["id"]: z for z in ZONES["pitch"]["zones"]}
    if goal == "feminine":
        return target[0] if target else edges["feminine"]["lo"]
    if goal == "masculine":
        return target[1] if target else edges["masculine"]["hi"]
    return float(np.sqrt(edges["overlap"]["lo"] * edges["overlap"]["hi"]))   # overlap centre


def _pitch_tip(times, f0, dims, goal, profile=None):
    """The stretch of this take nearest the goal's pitch, on the goal's side of the median (so a
    creaky dip or a squeak is not held up as the model)."""
    d = dims.get("pitch")
    if not goal or not d or d["confidence"] == "none" or d["zone"] == _goal_zone(goal):
        return None
    step = float(np.median(np.diff(times)))
    rolled = _rolling(f0, round(STRETCH_S / step), np.nanmedian)
    reference = _pitch_reference(goal, profile)
    up = d["value"] < reference
    side = rolled > d["value"] if up else rolled < d["value"]
    cents = np.where(side, np.abs(1200 * np.log2(rolled / reference)), np.nan)
    if np.all(np.isnan(cents)):
        return None
    i = int(np.nanargmin(cents))
    start, end = _window(times, i, STRETCH_S, step)
    value = float(rolled[i])
    lead = f"Your pitch sat around {d['value']:.0f} Hz, {_zone_phrase(d['zone'])}."
    if goal == "androgynous":
        move = "lift it a little" if up else "let it settle a little"
        text = (f"{lead} It came closest to the middle of the overlap zone, about {value:.0f} Hz, "
                f"at {_secs(start, end)}. Next take, {move} toward that, a small step at a time.")
    elif up:
        where = ("inside the typically feminine range" if value >= reference
                 else "the closest you came to the typically feminine range")
        text = (f"{lead} It reached about {value:.0f} Hz at {_secs(start, end)}, {where}. Next "
                "take, try starting your sentences near that height, a small step at a time, "
                "keeping it easy.")
    else:
        where = ("inside the typically masculine range" if value <= reference
                 else "the closest you came to the typically masculine range")
        text = (f"{lead} It settled to about {value:.0f} Hz at {_secs(start, end)}, {where}. Next "
                "take, let more of your speech rest near there, without pushing down.")
    return {"key": "pitch_toward_goal", "text": text, "moments": [(start, end)], "kind": "tip",
            "dimension": "pitch"}


def _goal_zone(goal):
    return {"feminine": "feminine", "masculine": "masculine", "androgynous": "overlap"}.get(goal)


def _resonance_tip(times, tracks, dims, goal):
    d = dims.get("delta_f")
    df = tracks.get("delta_f")
    if not d or d["confidence"] == "none" or df is None or np.all(np.isnan(df)):
        return None
    if goal and d["zone"] == _goal_zone(goal):
        return None
    bright = _extreme_stretch(times, df, highest=True)
    if bright is None:
        return None
    dark = _extreme_stretch(times, df, highest=False, away_from=bright[3])
    if dark is None:
        return None
    step = float(np.median(np.diff(times)))
    rounded = ""
    f2 = tracks.get("f2")
    if f2 is not None:
        a, b = int(np.searchsorted(times, dark[0])), int(np.searchsorted(times, dark[1]))
        voiced = ~np.isnan(df[a:b])
        if voiced.any() and np.nanmedian(f2[a:b][voiced]) < ROUNDED_F2:
            rounded = ", on rounded vowels like 'oo' and 'oh'"
    b_span, d_span = _secs(bright[0], bright[1]), _secs(dark[0], dark[1])
    brighter = goal == "feminine" or (goal == "androgynous" and d["zone"] == "masculine")
    if goal and not brighter:
        text = (f"Your darkest, fullest stretch was {d_span}; replay it and notice the space in "
                f"your throat and mouth. Brightness rose most at {b_span}: next take, try keeping "
                "that same relaxed, open space through those words.")
        moments = [(dark[0], dark[1]), (bright[0], bright[1])]
    elif goal:
        text = (f"Your brightest stretch was {b_span}; replay it and notice how it felt, perhaps a "
                f"smile in the voice and the tongue a little forward. Resonance darkened most at "
                f"{d_span}{rounded}")
        text += (": try keeping the lips a little less rounded there." if rounded else
                 ": next take, carry the brighter feeling into those words.")
        moments = [(bright[0], bright[1]), (dark[0], dark[1])]
    else:
        text = (f"Your brightest stretch was {b_span}, and resonance was darkest at {d_span}"
                f"{rounded}. Replay both to hear the difference.")
        moments = [(bright[0], bright[1]), (dark[0], dark[1])]
    moments = [(round(max(float(times[0]), a), 2), round(min(float(times[-1]) + step, b), 2))
               for a, b in moments]
    return {"key": "resonance_stretches", "text": text, "moments": moments,
            "kind": "tip" if goal else "info", "dimension": "delta_f"}


def _intonation_tip(times, f0, dims, goal):
    d = dims.get("pitch_range")
    if goal != "feminine" or not d or d["confidence"] == "none" or d["zone"] == "feminine":
        return None
    step = float(np.median(np.diff(times)))
    best = None
    for a, b in _phrases(f0, step):
        values = f0[a:b][~np.isnan(f0[a:b])]
        if (b - a) * step < 1.0 or len(values) < 20:
            continue
        lo, hi = np.percentile(values, [10, 90])
        spread = 12 * np.log2(hi / lo)
        if best is None or spread > best[0]:
            best = (spread, a, b)
    if best is None:
        return None
    start, end = _moment(times, best[1], best[2], step)
    return {"key": "intonation", "kind": "tip", "dimension": "pitch_range", "moments": [(start, end)],
            "text": (f"Your pitch stayed fairly even ({d['value']:.1f} semitones of movement). Your "
                     f"liveliest phrase was at {_secs(start, end)}; next take, let the important "
                     "words rise and fall like that, as if sharing good news.")}


def _frame_scores(tracks, dims):
    """Per-frame overall pull from pitch and resonance (trusted ones only), or None."""
    parts, weights = [], []
    for key, name in (("pitch", "f0"), ("delta_f", "delta_f")):
        d = dims.get(key)
        values = tracks.get(name)
        if d and d["confidence"] != "none" and values is not None:
            parts.append(np.array([np.nan if np.isnan(v) else score(key, v) for v in values]))
            weights.append(WEIGHTS[key])
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    both = np.vstack(parts)
    w = np.array(weights)[:, None] * ~np.isnan(both)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nansum(np.nan_to_num(both) * w, axis=0) / np.where(w.sum(axis=0) > 0, w.sum(axis=0), np.nan)


def _best_moment(times, tracks, dims, goal, profile):
    """One best stretch of the take: closest to the goal, else most on target, else clearest."""
    step = float(np.median(np.diff(times)))
    n = round(BEST_WINDOW_S / step)
    targets = getattr(profile, "targets", None) or {}
    if goal:
        frames = _frame_scores(tracks, dims)
        if frames is None:
            return None
        values = {"feminine": frames, "masculine": -frames, "androgynous": -np.abs(frames)}[goal]
        what = f"pitch and resonance came closest to your {GOAL_WORDS[goal]} goal"
    elif targets.get("pitch") or targets.get("delta_f"):
        f0, df = tracks.get("f0"), tracks.get("delta_f")
        if f0 is None:
            return None
        hits = []
        for key, values in (("pitch", f0), ("delta_f", df)):
            target = targets.get(key)
            if target and values is not None and dims.get(key, {}).get("confidence") != "none":
                hits.append(np.where(np.isnan(values), np.nan,
                                     (values >= target[0]) & (values <= target[1])))
        if not hits:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)      # frames with no reading stay NaN
            values = np.nanmin(np.vstack(hits), axis=0) if len(hits) > 1 else hits[0]
        what = "you were on your targets most of the time"
    elif tracks.get("cpp") is not None and dims.get("cpp", {}).get("confidence") not in (None, "none"):
        values = tracks["cpp"]
        what = "your voice sounded clearest"
    else:
        return None
    if np.all(np.isnan(values)):
        return None
    rolled = _rolling(values, n, np.nanmean)
    if np.all(np.isnan(rolled)):
        return None
    i = int(np.nanargmax(rolled))
    start, end = _window(times, i, BEST_WINDOW_S, step)
    return {"key": "best_moment", "kind": "best", "dimension": None, "moments": [(start, end)],
            "text": f"Best moment: {_secs(start, end)}, where {what}. Replay it and notice how it "
                    "felt, so you can find it again."}


def _safety_tips(take, quality, profile, mode):
    """coach.advice's strain warnings (and, in sing mode, its tuning and timing notes)."""
    keep = {"possible_pressing", "tense_high_notes"}
    if mode == "sing":
        keep |= {"sing_flat", "sing_sharp", "sing_late", "sing_early"}
    targets = getattr(profile, "targets", None) or {}
    baseline = getattr(profile, "baseline", None)
    try:
        messages = coach.advice(take, targets, baseline, mode=mode, quality=quality)
    except (TypeError, ValueError, KeyError, ZeroDivisionError):
        return []
    dimension = {"possible_pressing": "h1h2", "tense_high_notes": "h1h2", "sing_late": None,
                 "sing_early": None}
    return [{"key": m["key"], "text": m["text"], "moments": [],
             "kind": "tip" if m["severity"] != "info" else "info",
             "dimension": dimension.get(m["key"], "tuning" if mode == "sing" else None)}
            for m in messages if m["key"] in keep]


def _speak_tips(take, dims, goal, profile, quality):
    if quality["tier"] == "poor":
        return []
    times, tracks = _tracks(take)
    tips = _safety_tips(take, quality, profile, "speak")
    if times is None or "f0" not in tracks:
        return tips[:MAX_TIPS]
    f0 = tracks["f0"]
    candidates = []
    if dims.get("pitch", {}).get("confidence") != "none":
        candidates.append(_pitch_tip(times, f0, dims, goal, profile))
    candidates.append(_resonance_tip(times, tracks, dims, goal))
    if dims.get("pitch_floor", {}).get("confidence") != "none":
        candidates.append(_floor_tip(times, f0, goal, profile))
    if dims.get("pitch_range", {}).get("confidence") != "none":
        candidates.append(_intonation_tip(times, f0, dims, goal))
    tips += [c for c in candidates if c]
    best = _best_moment(times, tracks, dims, goal, profile)
    tips = tips[:MAX_TIPS - 1] if best else tips[:MAX_TIPS]
    return tips + ([best] if best else [])


# --- SING ---

def _sing_dimensions(take, quality):
    singing, exercise = take.get("singing") or {}, take.get("exercise") or {}
    times, tracks = _tracks(take)
    dims = []

    def add(key, value, reason, headline, details, level=None):
        trusted = value is not None and reason is None
        level = level or ("high" if quality["tier"] == "good" else "medium")
        dims.append({"key": key, "name": NAMES[key], "value": value if trusted else None,
                     "display": _fmt(key, value) if trusted else "—",
                     "unit": {"range": "st", "tuning": "cents", "steadiness": "cents",
                              "register": "%"}[key],
                     "position": position(key, value) if trusted else None, "zone": None,
                     "zone_label": None, "confidence": level if trusted else "none",
                     "reason": reason, "headline": headline if trusted else _cap_first(reason or ""),
                     "explanation": EXPLANATIONS[key], "details": details,
                     "target": None, "baseline": None, "change": None})

    pitch_ok = quality["trust"].get("singing", True) and quality["trust"].get("pitch", True)
    why = quality["why_untrusted"].get("singing") or quality["why_untrusted"].get("pitch")
    f0 = tracks.get("f0") if times is not None else None
    voiced = f0[~np.isnan(f0)] if f0 is not None else np.array([])
    if pitch_ok and len(voiced) >= 20:
        lo, hi = np.percentile(voiced, [5, 95])
        span = float(12 * np.log2(hi / lo))
        add("range", span, None, f"You sang from about {_note(lo)} to {_note(hi)}",
            f"5th–95th percentile of sung f0: {lo:.0f}–{hi:.0f} Hz ({span:.1f} st).")
    else:
        add("range", None, why or "not enough sung notes to judge", "", "")

    if not pitch_ok:
        add("tuning", None, why, "", "")
    elif _num(exercise.get("mean_abs_cents")) is not None:
        cents = float(exercise["mean_abs_cents"])
        in_tune = _num(exercise.get("time_in_tune"))
        headline = (f"{in_tune:.0%} of note time landed within ±25 cents" if in_tune is not None
                    else f"Notes were about {cents:.0f} cents off on average")
        add("tuning", cents, None, headline,
            f"{exercise.get('name') or 'Exercise'}: {exercise.get('notes_in_tune')}/"
            f"{exercise.get('notes_total')} notes within ±25 cents; mean error {cents:.0f} cents "
            "(strict in-tune ±25 cents, usual criterion ±50; Pfordresher & Demorest 2021).")
    elif _num(singing.get("tuning_spread")) is not None:
        spread = float(singing["tuning_spread"])
        add("tuning", spread, None, f"Your notes sat within about ±{spread:.0f} cents of your own centre",
            f"Median distance of note centres from your own tonic: {spread:.0f} cents"
            + (f"; steps between notes off by {singing['interval_error']:.0f} cents"
               if _num(singing.get("interval_error")) is not None else "") + ".")
    else:
        add("tuning", None, "no held notes to judge tuning", "", "")

    drift = _num(singing.get("drift"))
    if pitch_ok and drift is not None:
        vib = (f"vibrato on {singing.get('vibrato_notes')} of {singing.get('held_notes')} held notes"
               if singing.get("vibrato_notes") else "straight tone")
        add("steadiness", drift, None, f"Held notes wandered about ±{drift:.0f} cents",
            f"Slow drift of held notes (std after removing vibrato): {drift:.0f} cents; {vib}.")
    else:
        add("steadiness", None, why if not pitch_ok else "no held notes to judge steadiness", "", "")

    register = take.get("register")
    head = _num(register.get("head_share")) if isinstance(register, dict) else _num(register)
    if isinstance(register, dict) and head is not None:
        head *= 100
    if head is not None and quality["trust"].get("register", True):
        calibrated = isinstance(register, dict) and register.get("calibrated")
        level = None if calibrated else "low"
        word = "mostly head voice" if head >= 60 else "mostly chest voice" if head <= 40 else "a mix"
        add("register", head, None, f"{_cap_first(word)} ({head:.0f}% head)",
            f"Head share of voiced time {head:.0f}%"
            + ("" if calibrated else "; the register model isn't calibrated to your voice yet, "
                                     "so treat this as a rough guess") + ".", level=level)
    else:
        add("register", None, quality["why_untrusted"].get("register") or
            "register wasn't measured in this take", "", "")
    return dims


def _sing_best(take):
    times, tracks = _tracks(take)
    if times is None or "f0" not in tracks:
        return None
    step = float(np.median(np.diff(times)))
    cents = 1200 * np.log2(tracks["f0"] / 440.0)
    spread = _rolling(cents, round(1.0 / step), np.nanstd)
    fill = _rolling(np.where(np.isnan(cents), np.nan, 1.0), round(1.0 / step), np.nansum)
    spread[fill < 0.8 * round(1.0 / step)] = np.nan
    if np.all(np.isnan(spread)):
        return None
    i = int(np.nanargmin(spread))
    start, end = _window(times, i, 1.0, step)
    return {"key": "best_moment", "kind": "best", "dimension": "steadiness",
            "moments": [(start, end)],
            "text": f"Best moment: {_secs(start, end)}, your steadiest held note. Replay it and "
                    "notice how it felt, so you can find it again."}


# --- REPORT ---

def build_report(take, profile=None, previous=None, goal=None):
    """The voice report for one take as plain data (see specs/phase10/02-voice-report.md)."""
    if goal is None:
        goal = getattr(profile, "voice_goal", None)
    goal = goal if goal in GOAL_WORDS else None
    mode = take.get("mode") or ("sing" if take.get("exercise") else "speak")
    quality = _quality(take)
    previous = _comparable(take, previous)
    if mode == "sing":
        return _sing_report(take, quality, profile, goal)
    prev_quality = _quality(previous) if previous else None
    dimensions = [_speak_dimension(k, take, quality, profile, previous, prev_quality)
                  for k in DIMENSIONS_SPEAK]
    dims = {d["key"]: d for d in dimensions}
    overall = _overall(dimensions)
    issues = _issues(dimensions, quality)
    tips = _speak_tips(take, dims, goal, profile, quality)
    return {"mode": "speak", "headline": _headline(overall, dims, quality), "overall": overall,
            "dimensions": dimensions, "tips": tips, "issues": issues, "caveat": CAVEAT,
            "goal": goal}


def _headline(overall, dims, quality):
    if overall["position"] is None:
        if quality["tier"] == "poor":
            cause = quality["reasons"][0] if quality["reasons"] else "the recording is too noisy"
            return f"This take is hard to judge: {cause}."
        return f"No overall placement for this take: {overall['reason']}."
    text = f"Overall this take sits in the {ZONE_LABELS[overall['zone']]} zone"
    changed = [d for d in (dims.get("delta_f"), dims.get("pitch"))
               if d and d["change"] and d["change"]["vs"] == "previous"
               and not d["change"]["text"].startswith("About")]
    if changed:
        text += ", " + changed[0]["change"]["text"].split(" (")[0].lower()
    if overall["confidence"] == "low":
        text += " (low confidence)"
    return text + "."


def _sing_report(take, quality, profile, goal):
    dimensions = _sing_dimensions(take, quality)
    issues = _issues(dimensions, quality)
    tips = [] if quality["tier"] == "poor" else _safety_tips(take, quality, profile, "sing")
    best = None if quality["tier"] == "poor" else _sing_best(take)
    tips = (tips[:MAX_TIPS - 1] + [best]) if best else tips[:MAX_TIPS]
    reason = "sing mode: singing is described by range, tuning and steadiness, not by gender"
    overall = {"position": None, "range": None, "zone": None, "confidence": "none", "pulls": [],
               "text": "No gender placement in sing mode.", "reason": reason}
    if quality["tier"] == "poor":
        cause = quality["reasons"][0] if quality["reasons"] else "the recording is too noisy"
        headline = f"This take is hard to judge: {cause}."
    else:
        order = {"tuning": 0, "steadiness": 1, "range": 2, "register": 3}
        trusted = sorted((d for d in dimensions if d["confidence"] != "none"),
                         key=lambda d: order[d["key"]])
        headline = (trusted[0]["headline"] + "." if trusted else
                    "Not enough clear singing to describe this take.")
    return {"mode": "sing", "headline": headline, "overall": overall, "dimensions": dimensions,
            "tips": tips, "issues": issues, "caveat": SING_CAVEAT, "goal": goal}


def render_text(report):
    """The report as plain text, for logs and checking readability."""
    lines = [report["headline"], ""]
    o = report["overall"]
    if o["position"] is not None:
        lines.append(f"Overall: {ZONE_LABELS[o['zone']]} · position {o['position']:.2f} "
                     f"(range {o['range'][0]:.2f}–{o['range'][1]:.2f}) · {o['confidence']} confidence")
    lines += [o["text"], ""]
    for issue in report["issues"]:
        lines.append(f"! {issue['text']}")
    if report["issues"]:
        lines.append("")
    for d in report["dimensions"]:
        lines.append(f"{d['name']}: {d['headline']}" + (f"  [{d['display']}, {d['confidence']}]"
                                                        if d["value"] is not None else ""))
        lines.append(f"    {d['explanation']}")
        if d["change"]:
            lines.append(f"    {d['change']['text']}")
    lines.append("")
    lines.append("Tips:" if report["tips"] else "No tips for this take.")
    for tip in report["tips"]:
        spans = ", ".join(_secs(a, b) for a, b in tip["moments"])
        lines.append(f"- {tip['text']}" + (f"  [{spans}]" if spans else ""))
    lines += ["", report["caveat"]]
    return "\n".join(lines)
