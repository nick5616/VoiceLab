# 01 Register core (wave 1)

## Goal

Estimate, from audio alone, which **laryngeal mechanism** the voice is using, frame by frame and per take. Use this from day one, and make it sharper once the user labels a few of their own takes. No UI in this spec.

Both audiences need it:
- **Singers**: chest vs head, where the break happens, how much of a phrase is in mix.
- **Voice feminization**: speaking in a lighter (M2-leaning) mechanism vs heavy M1, and catching vocal fry at phrase ends.

## Background you must research before choosing thresholds

There is no validated, acoustic-only, speaker-independent register classifier. The output is an **estimate with a confidence**, and every name and docstring must say so. Look up and cite (in the module docstring and your report):

- Henrich, "Mirroring the voice from Garcia to the present day" (2006), and Roubeau, Henrich & Castellengo (2009): mechanisms M0 (fry), M1, M2, M3.
- How H1–H2 (or H1*–H2*), spectral slope (e.g. H1–A3 or alpha ratio) and CPP differ between M1 and M2. Kreiman, Gerratt and Garellek's work on H1*–H2* is a good start.
- EGG contact quotient by mechanism (roughly M1 ≈ 0.4–0.6, M2 lower). Spec 02 provides CQ when an EGG is connected, and your model must use it when present.
- Fry: low f0 (typically below ~70 Hz) with irregular periods or subharmonics (Blomgren et al. 1998).

If a source contradicts these rough figures, follow the source and say so in your report.

## Deliverables

### `register.py` (new)

Required API (you may add private helpers):

```python
REGISTERS = ("fry", "chest", "mix", "head")      # display labels; chest=M1, head=M2
MIX_BAND = (0.35, 0.65)                          # m2 score in this band -> "mix" (tune with sources)

def frame_features(frame, sample_rate, f0, formants, bandwidths, cq=None):
    """Feature dict for one voiced frame: 'h1h2', 'h1a3' (or your chosen slope measure),
    'cpp', 'f0', and 'cq' (None without an EGG). NaN for any feature that can't be measured."""

def is_fry(f0_track, frame_rate):
    """Boolean array, aligned with f0_track: frames judged to be vocal fry (M0)."""

class RegisterModel:
    """M2 likelihood from features. A heuristic default; fit() calibrates it to one voice."""
    def score(self, features):            # -> float in [0, 1] (P(M2)), or NaN if not measurable
    def confidence(self, features):       # -> float in [0, 1]; low near 0.5 or with missing features
    def label(self, score):               # -> "chest" | "mix" | "head" (fry is decided by is_fry)
    def fit(self, labelled):              # labelled: list of (features, "chest"|"head"); returns self
    def is_calibrated(self):              # True after fit() with at least the minimum examples
    def to_dict(self) / @classmethod from_dict(cls, d)   # JSON-safe, for storing in the profile

def register_track(audio, sample_rate, tracks, model=None, cq_track=None):
    """Per-frame m2 score (NaN where unvoiced), aligned with tracks['times'].
    `tracks` is analyze_take()'s result['tracks']."""

def take_register_summary(tracks, scores, fry):
    """{'chest_share', 'mix_share', 'head_share', 'fry_share' (of voiced time, summing to 1),
        'median_score', 'breaks': [times in s of abrupt M1<->M2 switches], 'calibrated': bool}"""
```

Model requirements:
- **Default (uncalibrated).** A logistic combination of standardized features, with weights and centres taken from the literature. It must not depend on f0 alone: a high chest voice must not read as head just because it's high. f0 may enter as a weak prior.
- **With `cq`.** CQ dominates the score when present.
- **`fit()`.** Plain-numpy logistic regression (no sklearn) on the user's labelled frames. Require at least ~2 s of voiced frames per class, otherwise `is_calibrated()` stays False. Regularize toward the default weights so a handful of takes can't produce something wild.
- **Breaks.** A score crossing from below 0.3 to above 0.7 (or back) within ~150 ms, usually alongside an f0 jump. Smooth over ~50 ms before detecting, so jitter isn't reported as a break.
- **Speed.** Live use: `frame_features` plus `score` must take under 2 ms per frame on this machine. Measure it in a test.

### `analysis.py` (narrow change)

`analyze_take` currently discards its per-frame `h1h2` and `cpp` values. Add them to `result["tracks"]` as `"h1h2"` and `"cpp"`, aligned with `times` and NaN where unvoiced or unmeasured, so `register_track` doesn't recompute them. Don't change any other output or behaviour. `take_store` already serializes every array in `tracks`. Old takes lack these keys, so `register_track` must fall back to recomputing them from the audio.

Do **not** call the register code from `analyze_take` yet. Integration (spec 05) wires it in.

### `tests/test_register.py` (new)

Use `synth_vowel(..., open_quotient=...)` from `tests/test_analysis.py`: a high open quotient approximates M2, a low one M1. At minimum:
1. OQ 0.8 scores higher than OQ 0.35 at the **same** f0, at 150, 300 and 450 Hz.
2. Low-OQ at 450 Hz still labels "chest" or "mix", not "head" (not f0-driven).
3. A take switching OQ halfway at a fixed pitch reports exactly one break, within 0.2 s of the switch.
4. A synthetic fry signal (f0 around 50 Hz with ±20% period jitter) is flagged by `is_fry`. A steady 110 Hz vowel isn't.
5. `fit()` on labelled synthetic data shifts the decision boundary toward the labels. It refuses to calibrate (`is_calibrated() False`) with less than the minimum data.
6. `to_dict`/`from_dict` round-trips through `json.dumps`.
7. With `cq` supplied, the score follows CQ even when the acoustic features disagree.
8. Shares in `take_register_summary` sum to 1 (±1e-6).
9. Old-take fallback: `register_track` works on `tracks` without `h1h2`/`cpp` keys.
10. The timing test from Model requirements.

## Acceptance

- All tests in this spec pass, and so does the full suite.
- `analyze_take` output is identical apart from the two new track arrays.
- The report lists every numeric threshold and weight with its source, or "chosen empirically on synthetic data" if it has none.
