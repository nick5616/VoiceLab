# 03 Singing

Make singing scores reflect what a teacher hears. Fix vibrato, timing, register at high notes, and garbage in singing stats and the Voice Map. Spec 01 fixes ghost pitch frames in the tracker at the same time. You consume `analyze_take`'s f0 as given, but your code must also be robust to the occasional ghost frame.

## Measured problems (from the singing audit, all on synthetic signals; no real sung take exists yet)
**Vibrato is penalised**
- Perfectly centred vibrato at ±50 ¢ and 6 Hz scores 0.34–0.45 "time in tune", while 9/9 note medians are within ±17 ¢. Straight tone scores 0.96.
- The live tuning meter colours raw f0, so it flips every 80 ms on vibrato.
- Perceived pitch is about the vibrato mean (Sundberg; d'Alessandro & Castellengo, unverified).

**Timing**
- `score_exercise` uses the fixed window `[start+0.15 s, end)`.
- A perfect singer 0.4 s late on 0.5 s notes scores 1/15 in tune (mean error 158 ¢).
- Realistic lag is guide output latency (100–300 ms, uncompensated: `t0` is set after `sd.play`) plus 150–250 ms reaction time.

**`vibrato_stats`**
- Rate comes from zero crossings, which break under tracker noise: 5.5 Hz reads 7.8 Hz with 8 ¢ noise and 11.5 Hz with 12 ¢.
- The 0.3 s moving-average trend biases extent: +21% at 4.5 Hz, −10 to −15% at 7.5 Hz.
- Random 10 ¢ wander low-passed at 8 Hz is flagged as vibrato in 25/30 notes.
- 9–12 Hz tremolo is reported as straight tone.
- The live meter needs zero NaN frames in 1.2 s.

**Register (`register.py`)**
- Above about 440 Hz the H1–A3 centre's +30 dB/octave slope (`H1A3_F0_SLOPE`) clips the z-score at −3. Light phonation at 784–1000 Hz scores P(head) 0.00–0.18, i.e. "chest".
- An abrupt chest→falsetto switch at 330 Hz (0.14→0.65) finds no break, because `find_breaks` needs absolute 0.3/0.7 crossings.
- `is_fry` crashes on f0 arrays shorter than 5 frames (`np.convolve(..., "same")`).
- `take_register.FRY_FROM_AUDIO` is off because the audio check flagged 14–94% of clean frames as fry.

**Segmentation and stats**
- `NOTE_MIN_SECONDS` 0.4 drops 0.25–0.4 s notes.
- "¢ from A440 notes" measures equal-temperament offset, not intonation. A singer whose tonic is 40 ¢ flat but who keeps every interval correct is in tune.
- Speech takes get singing stats: a "held note" at 71 Hz that is rumble.

**Voice Map**
- `MIN_FRAMES` 3 is only about 75 ms of audio.
- Level is full-band, so it includes the noise and tones.
- The saved map is about 49% ghost frames (26% below 93 Hz, 23% above 523 Hz).

**Tolerance**
- Research commonly uses ±50 ¢ as the note-accuracy criterion (Pfordresher & Demorest 2021).
- ±25 ¢ is fine as a strict "in tune" level, with ±50 ¢ as "close".

## Do
1. **Perceived-pitch tuning.**
   - In `score_exercise` and singing stats, judge on medians over about 150–300 ms windows (at least one vibrato period), not raw frames.
   - Report "in tune (±25 ¢)" and "close (±50 ¢)".
   - Expose a helper the live UI can call, e.g. `perceived_cents(cents_window)`. `exercise.on_frame` and `PianoRoll` in `exercise.py` should colour by it.
2. **Timing.**
   - Estimate one global lag per exercise run by cross-correlating the target contour with sung f0 over −0.2 to +0.6 s. Apply it before scoring and report "timing: N ms late/early".
   - Compensate the guide's output latency with the stream's reported latency in `exercise.py`.
3. **Vibrato.**
   - Rate from a zero-padded FFT peak (parabolic interpolation) inside 3.5–9 Hz.
   - Extent from a least-squares sinusoid fit (with mean and linear drift).
   - Require at least 4 cycles, extent of at least 15 ¢ and an in-band power share of at least 0.6.
   - Add a tremolo flag for 9–12 Hz. Tolerate isolated NaN frames by interpolating short gaps.
4. **Segmentation and stats.**
   - Minimum note length about 0.15 s, and use the target-based segmentation when targets exist.
   - Replace the A440 offset with relative intonation: interval errors between consecutive held notes, and offset relative to the singer's own median tonic, plus spread.
   - Produce singing stats only when the take has sung material (at least one 0.6 s run with spread under 100 ¢), so a speech take gets none.
5. **Register.**
   - Stop extrapolating H1–A3 at 30 dB/octave. Above about 500 Hz shift the prior toward head (M1 rarely exceeds about 500–700 Hz in most voices; verify and cite) and widen the mix band.
   - `find_breaks` uses score swings (e.g. ≥ 0.4 within 150 ms) rather than absolute crossings.
   - Fix the short-array `is_fry` crash.
   - Re-test `FRY_FROM_AUDIO`. Turn it on only if clean synthetic vowels at 100–250 Hz give under 2% false fry.
   - Keep the "estimate" wording until the user calibrates. Calibration data is the user's job.
6. **Voice Map.** Require about 12 frames (0.3 s) per cell, use the voice-band level, and skip frames outside a plausible f0 range for the mode. Add a `rebuild`/`clear` function so wave B can offer "rebuild from takes".

## Verify (`tests/test_singing.py`, plus `tests/test_register.py` additions)
- ±50 ¢ vibrato at 6 Hz on target scores ≥ 0.9 in tune.
- A 0.4 s-late perfect singer scores 15/15, and the reported lag is within 50 ms. Lags from −0.2 to +0.5 s all pass.
- Vibrato on a grid of 4.5–7.5 Hz × ±20/50/100 ¢ × 0/4/8/12 ¢ tracker noise recovers rate within 0.3 Hz and extent within 10%.
- Straight tone with 10 ¢ wander low-passed at 8 Hz is flagged as vibrato in fewer than 3/30 cases. A 10 Hz tremolo is flagged as tremolo.
- A legato scale at 0.25 s per note gives 9 segments.
- A speech-like contour gives no singing stats.
- Register: open quotient 0.9 at 784 and 1000 Hz scores > 0.5, the 330 Hz switch gives exactly one break, and `is_fry` handles length 1–4 arrays.
- The Voice Map gets no cells from `mic_noise` alone.
- All signals use `tests/mic_noise.py` at a realistic level, as well as clean.
