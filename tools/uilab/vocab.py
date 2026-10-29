"""Words the evals look for: jargon, the zone wordings the app allows, the names of each quantity, the words that
label a reading's time ("now", "last take") and the words that say the app hears you. Pure data and parsers."""
import re

import report

# Jargon that may appear only in tooltips or inside an expanded "Details" (phase 10 rule: plain words first).
JARGON = [
    ("ΔF", re.compile(r"ΔF")),
    ("H1*–H2*", re.compile(r"\bH1\b|H1\*")),
    ("CPP", re.compile(r"\bCPP\b")),
    ("dBFS", re.compile(r"\bdBFS\b")),
    ("F0", re.compile(r"\b[Ff]0\b")),
    ("¢ without words", re.compile(r"¢")),
    ("st without 'semitones'", re.compile(r"\d\s*st\b")),
    ("HNR", re.compile(r"\bHNR\b")),
    ("jitter", re.compile(r"\bjitter\b", re.I)),
    ("shimmer", re.compile(r"\bshimmer\b", re.I)),
    ("M1/M2", re.compile(r"\bM[12]\b")),
]
CENT_WORDS = re.compile(r"\bcents?\b", re.I)
SEMITONE_WORDS = re.compile(r"\bsemitones?\b", re.I)
LONE_DB = re.compile(r"^[+\-−]?\d+(\.\d+)?\s*dB$")
# Paths under these segments are "Details": jargon is allowed there (spec 01, B6).
DETAIL_SEGMENTS = ("/details", "sections.numbers", "sections.register", "sections.singing", "/help")

# The zone wordings the app may use: report's labels and the theme's short forms.
ZONE_ALLOWED = set(report.ZONE_LABELS.values()) | {"Masculine", "Overlap", "Feminine"}
ZONE_WORDS = re.compile(r"(typically\s+)?(masculine|feminine|androgynous)|overlap(\s*/\s*androgynous)?", re.I)
ZONE_OF_WORD = {"masculine": "masculine", "feminine": "feminine", "androgynous": "overlap", "overlap": "overlap"}

# One name per quantity: every name the app uses for it (a screen should show one).
QUANTITY_NAMES = {
    "pitch": ["Pitch"],
    "delta_f": ["Brightness (resonance)", "Brightness", "Resonance", "Resonance ΔF", "Formant spacing"],
    "pitch_range": ["Intonation", "Intonation range", "Intonation Range"],
    "h1h2": ["Weight", "Heavy ↔ Light", "Vocal weight"],
    "cpp": ["Clarity"],
    "pitch_floor": ["Lowest notes", "Pitch floor", "Low points"],
}
NAME_TO_QUANTITY = {name.lower(): key for key, names in QUANTITY_NAMES.items() for name in names}

# Words that say which moment a reading belongs to.
NOW_WORDS = re.compile(r"\b(now|live|right now|this moment|last (\d+(\.\d+)?|few) ?s(ec(ond)?s?)?)\b", re.I)
TAKE_WORDS = re.compile(r"\b(last|latest|this|your) take\b|\btake #?\d+", re.I)
# Words that say the app hears (or is waiting for) a voice.
HEARD_WORDS = re.compile(r"hearing you|\bheard\b|\bvoice detected\b|\blistening\b", re.I)
WAITING_WORDS = re.compile(r"listening|waiting for (your )?voice|can'?t hear|\bsilen(t|ce)\b|no voice", re.I)
# A next step in words (a toast or line that says what to do).
NEXT_STEP = re.compile(r"\b(try|move|check|choose|pick|lower|raise|record|use|wait|connect|set|label|open|"
                       r"rest|speak|sing|say|run|free up|close)\b", re.I)

HZ = re.compile(r"(?<![\w.])([+\-−]?\d+(?:\.\d+)?)\s*Hz\b")
NUMBER = re.compile(r"[+\-−]?\d+(?:\.\d+)?")


def hz_values(text):
    """Every "<number> Hz" in a text, as floats."""
    return [float(m.group(1).replace("−", "-")) for m in HZ.finditer(text or "")]


def first_number(text):
    m = NUMBER.search(text or "")
    return float(m.group(0).replace("−", "-")) if m else None


def jargon(text):
    """[term] of the jargon in a visible text."""
    found = []
    for term, pattern in JARGON:
        if not pattern.search(text):
            continue
        if term == "¢ without words" and CENT_WORDS.search(text):
            continue
        if term == "st without 'semitones'" and SEMITONE_WORDS.search(text):
            continue
        found.append(term)
    return found


def zone_wordings(text):
    """[(zone id, wording)] of the zone words in a text."""
    out = []
    for m in ZONE_WORDS.finditer(text or ""):
        word = m.group(0)
        key = next((ZONE_OF_WORD[w] for w in ZONE_OF_WORD if w in word.lower()), None)
        if key:
            out.append((key, word))
    return out


def unbalanced(text):
    """True when a text opens a bracket it never closes (often cut short at its source)."""
    return text.count("(") > text.count(")") or text.count("[") > text.count("]")
