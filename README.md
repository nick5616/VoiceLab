# VoiceLab

A real-time vocal analysis tool for singers, voice actors, and anyone training their voice. Record a take, get immediate acoustic feedback on pitch, register, brightness, tonal weight, and pitch contour — so you can consciously shape your instrument before laying down any recordings.

## What It Measures

| Metric              | What it reflects                                                         |
| ------------------- | ------------------------------------------------------------------------ |
| **Pitch**           | Fundamental frequency (F0) — how high or low your voice sits             |
| **Register / F1**   | First formant — jaw openness and register placement (closed → open)      |
| **Brightness / F2** | Second formant — tongue position and tonal color (dark → bright)         |
| **Tonal Weight**    | Spectral slope — balance of low vs. high harmonic energy (heavy → light) |
| **Pitch Contour**   | Std. deviation of F0 — how much your pitch moves (monotone → dynamic)    |

Each take also shows an interactive graph: pitch track on top, F1/F2/F3 formant tracks below. Hover to inspect values at any point in time.

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

1. Click the red microphone button to start recording.
2. Speak or sing for at least a few seconds.
3. Click the stop button — the app analyzes your take and adds a card to the top of the list.
4. Compare takes as you experiment with different placements, vowel shapes, or resonance strategies.
5. Click **Play** on any card to hear it back alongside its metrics.

## Tips

- Speak/sing at a consistent volume — very quiet input may not yield enough voiced frames to analyze.
- Longer takes (3–5 seconds of sustained voice) produce more stable median values.
- Watch F1 and F2 together: lowering F1 while raising F2 is the classic "bright but placed" sound.
