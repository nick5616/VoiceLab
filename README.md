# VoiceLab

A real-time vocal analysis tool for singers, voice actors, people doing gender-affirming voice training, and anyone training their voice. Live meters respond as you sing or speak, and each recorded take gets a detailed report, so you can consciously shape your instrument.

## What It Measures

| Metric                     | What it reflects                                                                                   | Live |
| -------------------------- | -------------------------------------------------------------------------------------------------- | ---- |
| **Pitch**                  | Fundamental frequency (F0), shown as note name, cents and Hz                                        | Yes  |
| **Resonance (ΔF)**         | Formant dispersion over F1–F4 and the apparent vocal tract length it implies (dark → bright)        | Yes  |
| **Fold Contact (H1*–H2*)** | First-minus-second harmonic level, corrected for F1/F2. A proxy for vocal weight (heavy → light)    | Yes  |
| **Clarity (CPP)**          | Cepstral peak prominence: how periodic the voice is (breathy/rough → clear)                         | Yes  |
| **Ring (2–4 kHz)**         | Energy in the singer's-formant region relative to 0–2 kHz (muffled → ringing)                       | Yes  |
| **Pitch Movement**         | Std. deviation of pitch in semitones (monotone → dynamic)                                           | Take |
| **Level**                  | Voiced level in dBFS. Uncalibrated, so compare takes on the same mic and gain only                  | Yes  |

Each take also shows an interactive graph: pitch track on top, F1/F2/F3 formant tracks below. Hover to inspect values at any point in time.

### Why these metrics

- **Resonance** uses ΔF rather than individual formants because single formants mostly reflect which vowel you're saying. In a 2025 study a single speaker deliberately varied pitch, resonance and weight; ΔF tracked resonance with 94% ranking accuracy and stayed largely independent of the other two ([Netzorg et al., Interspeech 2025](https://www.isca-archive.org/interspeech_2025/netzorg25_interspeech.pdf)).
- **Weight** has no reliable single acoustic measure. The same study found that only electroglottograph contact quotient and loudness tracked it, while spectral slope also moved with resonance. Fold Contact, Clarity and Level are proxies, and are best used to compare your own takes against each other.
- **Formant ceiling** (the frequency range Praat searches for formants) is chosen per take. VoiceLab tests several ceilings and keeps the one whose formant tracks are smoothest and don't merge. The live meters then use that ceiling.
- **High pitch**: above ~350 Hz the harmonics are too far apart for formant tracking, so resonance is greyed out rather than reporting a wrong number.

## Requirements

- Python 3.10+
- A working microphone

## Setup

```bash
# Clone the repo
git clone https://github.com/nick5616/VoiceLab.git
cd VoiceLab

# Create and activate a virtual environment
python3 -m venv venv

# if: macOS / Linux
source venv/bin/activate

# if: Windows
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Running

```bash
# if: not already active
source venv/bin/activate

python main.py
```

## Usage

1. The **Live** panel listens as soon as the app opens: a scrolling pitch trace with note names, plus live meters.
2. Click the red microphone button to record a take, speak or sing, then click stop.
3. The app analyzes the take and adds a card to the top of the list.
4. Compare takes as you experiment with different placements, vowel shapes, or resonance strategies.
5. Click **Play** on any card to hear it back alongside its metrics.

## Tips

- Record a take early in a session. It calibrates the formant ceiling that the live resonance meter uses.
- Very quiet input may not yield enough voiced frames to analyze.
- Longer takes (3–5 seconds of voice) produce more stable values. For resonance, connected speech with several vowels works better than one held vowel.
- Spectral metrics (Ring, Clarity, Fold Contact) depend on the mic and room. Compare takes recorded with the same setup.

## Tests

```bash
python -m pytest tests
```

The tests synthesize vowels with known pitch, formants, glottal source shape and noise, then check that each metric recovers them.
