# 01 Measurement truth (wave A)

**Goal:** VoiceLab shows a reading only when it's right, and says so plainly when it can't tell. Fewer false positives (confident wrong readings) and fewer false negatives (real voice dropped, or a reading withheld that was fine). Prove it with numbers.

**Files you own:**
- `analysis.py`, `register.py`, `take_register.py`, `egg.py`, `denoise.py`.
- `ANALYSIS_VERSION` in `take_store.py`. Bump it so stored takes re-analyse; change nothing else in that file.
- New `tools/accuracy_check.py`.
- Tests: `test_analysis.py`, `test_pitch.py`, `test_singing.py`, `test_register.py`, `test_egg.py`, `test_denoise.py`, new `test_truth.py`.

**Parallel specs:** 02 (report), 03 (core split), 04 (design system).
- 03 is moving the logic out of `main.py` and the widgets, and calls `take_register.analyze_recording`, `analyze_with_options`, `finish_take`, `score_register`, `label_rows`, `fit_labelled`, `register_model`, `is_stale` and `analysis.analyze_take`. Keep those signatures; add keyword arguments only.
- 02 reads take summary keys. If you rename or remove a summary key, list it under requests for integration.

## Measured problems
1. **Register says "head" for ordinary speech.**
   - Every one of the user's 10 speech takes, at 147–212 Hz, scores head 94–100% (`register.median_score` ≈ 0.99).
   - Their H1\*–H2\* reads 7–15 dB, against the default centre of 3 dB.
   - Two causes, both possibly true:
     - **(a)** H1\*–H2\* is inflated on this mic. Desk rumble below 150 Hz sits under H1 (the mic's rumble share is about 0.86, per the phase 9 audit), and the formant correction may over-correct.
     - **(b)** The default model's centres don't suit real speech.
   - Find out which, with measurements.
2. **Singing stats on speech.** `analyze_take` calls `singing_stats` in speak mode, and `_has_singing` fired on a speech take: one 0.6 s steady stretch became "1 held note, ±20¢ drift, centre 3¢ below A440". In speak mode, singing stats must be None.
3. **Voiced fraction is 0.2–0.3 on running speech.** Check that real voice isn't being gated away (false negatives).
   - Listen-equivalent check: compare against pyworld `harvest` and Praat `to_pitch_ac` with default settings on the same takes.
   - Then hand-label a few seconds per take from the spectrogram, using clear harmonics as ground truth.
   - Report the frames each method calls voiced, and the agreement between them.
4. **Pitch halving and doubling.** Phase 9 fixed most of it. Re-measure octave errors on the real takes against harvest and the labelled frames.
5. **Formant tracks are noisy.** Take #9's F1–F3 plot is spaghetti, and F1 reads 312 Hz with F3 at 1958 Hz in places.
   - Add display-ready smoothed formant tracks: voiced and trusted frames only, median-filtered, with NaN where untrusted.
   - Keep the raw tracks for analysis.
   - Name the new track keys in your report.
6. **Every reading needs a confidence.** Add a per-take `confidence` dict, `{metric: "high"|"medium"|"low"|"none"}`, with a short reason per metric. Base it on SNR, voiced seconds, f0 range (formants above about 250 Hz), and the spread of the frames. `coach.take_quality` stays the gate; this adds the gradation the report (spec 02) and the UI need. Document the rules.

## Do
1. **Register.**
   - Fix the cause you find:
     - correct H1\*–H2\* for rumble (measure H1 only when the band below f0 is quiet; or use the noise floor per band from `denoise`);
     - re-centre the default model on cited values for modal speech;
     - or both.
   - In **speak** mode with an uncalibrated model, report register only as "mostly chest / some lighter phonation" when the evidence is strong. Otherwise mark it `confidence: none` with the reason "register needs a few labelled takes to be reliable for speech".
   - Target on the user's takes: no speech take at 140–220 Hz reads above 30% head with the uncalibrated model.
   - Add a synthetic test: modal-like source at 150 Hz plus `mic_noise` rumble → not head.
2. **Singing gate.** No singing stats in speak mode. In sing mode, keep `_has_singing`, but require at least 2 held notes, or one held note of 1.5 s or more, before reporting tuning centre, interval error or drift. Cite or measure the thresholds.
3. **Voicing.** If you find real voice being dropped, tune the gate. Report before/after voiced fraction and the agreement with the references. Ghosts must not come back: the 60–70 Hz and ~1000 Hz ghosts from the phase 9 audit stay out (`tests/mic_noise.py`).
4. **Smoothed tracks and confidence**, as in problems 5 and 6.
5. **`tools/accuracy_check.py`:** a script the user can run, `python tools/accuracy_check.py <folder> [--out C:\tmp\…]`.
   - It analyses every WAV with VoiceLab and with the independent references (pyworld harvest; Praat default autocorrelation).
   - It writes an HTML or Markdown table with, per take:
     - median f0 by each method;
     - octave-error share;
     - voiced-frame agreement;
     - register shares.
   - It should also support a "compare with another app" check. The user records the same passage while Voice Tools runs on their phone, enters Voice Tools' average and median, and the script reports the difference.
   - Read-only on the folder.
6. **Bump `ANALYSIS_VERSION`** so the app re-analyses stored takes.

## Verify
- `tests/test_truth.py`:
  - **Register:** modal synthetic speech at 120–220 Hz with mic noise is not head; a breathy falsetto-like synthetic is head.
  - **Singing gate:** speak mode returns None for singing stats.
  - **Octave errors:** below 2% on the synthetic sweeps with mic noise.
  - **Confidence:** "none" for formant-based readings at 300 Hz; "low" at 10 dB SNR.
- **Real takes**, read-only: a table of before/after register shares, voiced fraction, octave-error share against harvest, and the `accuracy_check` output. Put it in `C:\tmp\phase10-work\01-truth\` and summarise it in your report.
