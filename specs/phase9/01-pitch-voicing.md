# 01 Pitch & voicing

The foundation everything else rests on. Make `analyze_take`'s f0 track and the live `FrameAnalyzer` f0 trustworthy on the user's mic.

## Measured problem (from the audits)
- **Current settings.** `pitch_track`: Praat `to_pitch_ac`, floor 60, ceiling 1200, default octave cost, a 0–4000 Hz band, no voicing gate in `analyze_take`.
- **Reference.** Frames where pyworld harvest and torchcrepe agree within 1 st on the real takes: 2169 voiced frames, plus 3390 silent frames.

| Settings | Agreement within 1 st | Halved | Voiced in silence |
|---|---|---|---|
| App now (60/1200) | 76.6% | 21.2% | 15.7% |
| floor 75, ceiling 500, octave cost 0.1, voicing threshold 0.45 | 92.3% | 2.6% | 0.2% |
| floor 75, ceiling 500, octave cost 0.1, voicing threshold 0.5 | 90.8% | 2.5% | 0.1% |

- **Ghost cause.** A 4 kHz tone alone makes Praat report exactly 1000 Hz in 66 of 66 silent frames. Tone-free broadband noise at about −56 dBFS also gives ghosts with Praat strength 0.57–0.71, against 0.85–0.9 for voice.
- **Gating.**
  - Keeping only frames with strength ≥ 0.6, voice-band level ≥ the take's 10th-percentile band level + 8 dB, and a run ≥ 5 frames cut ghosts from 23% to 5%, missing 10% of truly voiced frames.
  - Strength 0.7 cut ghosts to 3% but missed 14%.
- **High-pass: conflicting evidence.** One audit found an 80 Hz high-pass removed the ghosts. Another found a 70 Hz high-pass made halving worse (13.7% vs 5.6% at floor 75). **Measure both and pick by the numbers.**
- **Breathy low notes.** Praat halves 27–97% of frames at noise ≥ 0.6. Harvest is about 0–8% on the same signals and takes about 0.2× realtime offline.
- **Live path.**
  - It is cleaner (2–24% ghosts) thanks to the noise-floor gate.
  - It stays unvoiced if the voice starts within the first second, because the noise floor seeds at the voice level.
  - The floor creeps up 1 dB/s during held notes and drops a 9 s note at 8 dB band SNR after about 7.8 s.
  - A single frame halves at pitch steps.

## Do
1. **Mode-aware tracking.**
   - `pitch_track(sound, time_step=0.01, mode="speak")` with per-mode constants:
     - speak: floor 75, ceiling 500, octave cost about 0.1, voicing threshold 0.45–0.5;
     - sing: floor 60, ceiling about 1400–1600 (notes above 1200 currently read an octave low), higher octave cost.
   - Choose the exact values by measurement on the reference.
   - `FrameAnalyzer(..., mode="speak")` and `analyze_take(audio, sample_rate, mode="speak")`. Default `"speak"` keeps current callers working.
2. **Offline voicing gate in `analyze_take`.**
   - Strength threshold, voice-band level relative to the take's noise floor (reuse `frame_levels(band=True)` / `VOICE_MARGIN_DB`), and a minimum run length.
   - A short (5-frame) median on f0 within runs.
   - Unvoice gated frames (NaN) so every downstream metric, including formants and medians, sees only real voice.
   - Return a `voiced_fraction` and a `ghost_fraction` (frames removed by the gate) in the result, for wave B's quality tier.
3. **Octave resolution offline.**
   - Cross-check with `pyworld.harvest`, already a dependency.
   - Where Praat and harvest disagree by about an octave, take the one consistent with the surrounding run, or simply harvest.
   - Keep `analyze_take` under about 0.5× realtime on a 20 s take. Measure it.
4. **Plausible-range statistics.**
   - `pitch_range_st`: use the 10th–90th percentile of gated frames, documented.
   - Add the take's f0 10th percentile as `pitch_floor`. A low floor reads as male, so it's a training cue.
5. **Live `FrameAnalyzer`.**
   - Seed the noise floor from an argument (the previous session or profile floor) when given.
   - Don't raise the floor while the previous frame was voiced.
   - Require agreement over a 5-frame f0 median before reporting a jump of more than 600 ¢.
   - Keep the per-frame cost within the 25 ms tick and report p50/p95.
6. **`shift.py`.** Its ceiling is `clip(nanmax(f0)·1.2, 300, 1200)`, which ghosts pin at 1200. Base it on the gated track.

## Verify (tests in `tests/test_pitch.py`)
- `mic_noise.mic_noise(10 s)` alone gives under 1% voiced frames offline and live.
- `synth_vowel` at f0 of 110, 150, 200 and 250 Hz plus mic noise at −42 dBFS gives a median error under 10 ¢ with no halving.
- A breathy vowel (high open quotient plus aspiration) at 130 and 165 Hz with mic noise gives fewer than 5% octave errors. Unvoiced is acceptable; wrong is not.
- A speech-like contour spanning about 4 st gives a `pitch_range` under 6 st with mic noise.
- Sing mode: notes at 1320 Hz are reported correctly.
- Live: voice starting at t = 0 is voiced within 0.3 s, and a 9 s held note at 8 dB band SNR stays voiced.
- Real takes: report agreement, halving and ghost rates against a harvest + CREPE consensus, before and after. torchcrepe is installed in the venv for validation only; don't import it from app code. Also report `pitch_range` per take.
