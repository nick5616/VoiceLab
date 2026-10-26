# 05 Formants, resonance & weight (wave B)

Wave A is merged on `main`:
- **Pitch** (spec 01): gated, mode-aware tracking. `analyze_take(audio, sr, mode)`, `gated_pitch`, plus `voiced_fraction`, `ghost_fraction` and `pitch_floor`.
- **`denoise.py`** (spec 02): `noise_audio_from_take`, `find_lines`, `cancel_lines`, `noise_report`.
- **Singing** (spec 03) and **`coach.py`** (spec 04).

You own the formant and weight measurements in `analysis.py`:
- `cpp`, `_resonance_gain_db`, `h1_h2_corrected`, `ring_db`;
- `formant_values`, `to_formants`, `_ceiling_score`, `choose_formant_ceiling`;
- `vowel_nucleus`, `analyze_vowels`;
- the formant and weight part of `analyze_take` and `FrameAnalyzer`.

Nobody else edits `analysis.py` in this wave. You also own new `tests/test_formants.py`.

## Measured problems (speech audit, real takes + synthetic)
1. **F1 snaps to H1 at f0 ≥ about 200 Hz.**
   - On the user's 202 and 212 Hz takes the median F1 is 246 Hz, and 43–44% of frames have F1 within ±12% of f0. Hand LPC agrees.
   - Synthetic /i/ and /u/: F1 error is +23% at 260 Hz, +34% at 300 Hz, and up to +54% pressed.
   - `FORMANT_RELIABLE_MAX_F0 = 350` is too generous.
   - On snapped frames the Iseli–Alwan H1*–H2* correction subtracts about 14 dB.
2. **The formant ceiling is unstable.**
   - Two takes 13 s apart: stored ΔF 995 vs 845 Hz (ceilings 5500 vs 4500). At a fixed 5500 they read 955 vs 981.
   - 3 of 7 takes pick the grid floor of 4500.
   - `main.py` copies the last take's ceiling into the live meters.
3. **ΔF is vowel-dependent.**
   - Across vowels for one speaker: F1/F2 about ±33%, F3 8%, F4 4%. Passage vowel mix alone shifts a take's ΔF by up to about 90 Hz.
   - The frame-level IQR is 70–180 Hz.
   - "≈ x cm tract" is shown as fact.
4. **CPP isn't CPPS.**
   - The app's per-frame CPP reads 12.6–16.2 dB on the takes, where Praat CPPS reads 4.6–5.9.
   - At the user's ~16 dB SNR the app's CPP reads 4–5 dB low.
   - The docstring's "comparable with published CPP" is false.
5. **Ring is unvalidated for speech.** It falls 4–11 dB with room reverb, and has no feminine-speech meaning.
6. **The 4 kHz interference tone corrupts F4.** It adds +174 Hz to F4 and +23 Hz to ΔF in male-range vowels. `denoise.cancel_lines`, with the voiced mask, cuts it 21–30 dB on real takes while protecting voice harmonics.
7. **`analyze_vowels` uses ungated `pitch_track`.** Spec 01's retune nudged a test's F1 tolerance from 0.12 to 0.125. Restore it if gating fixes it.

## Do
1. **Clean the input to formant analysis.** In `analyze_take`:
   - Run `denoise.noise_report` on the raw audio and add it to the result as `noise`.
   - Run `cancel_lines` using the gated voiced mask, and use the cleaned audio for the formant and weight measures. Pitch stays on the raw audio, already gated.
   - Measure the cost on a 20 s take.
2. **Sanitise F1.**
   - Treat F1 as missing where F1 < 1.3·f0 or F1 < 250 Hz.
   - For H1*–H2*, skip the F1 correction term when F1 is missing, or mark the frame unreliable. Choose by measurement.
   - Lower the formant-reliable f0 limit to about 250 Hz, or justify another value with your data.
   - Count snapped frames in `_ceiling_score`.
3. **Stable ceiling.**
   - Extend the grid to 4000–6500.
   - Add `choose_profile_ceiling(results)`, which picks one ceiling from the vowel check or from several takes' vowel-nucleus frames.
   - Make `analyze_take(..., ceiling=None)` accept a fixed ceiling, used when given.
   - Spec 07 stores it in the profile, and live meters use it.
4. **Honest resonance.**
   - Measure ΔF on loud, steady vowel-nucleus frames only (energy plus formant steadiness).
   - Also report `f3`, `f4` and a frame-spread `delta_f_iqr`.
   - Rename the tract-length output as apparent (`vtl_apparent_cm`), or drop it. Keep the old key readable for old takes.
5. **Weight and breathiness.**
   - Add a real CPPS: Praat `PowerCepstrogram` with the standard smoothing settings (Hillenbrand-style; Maryn & Weenink 2015). Cite the parameters.
   - Keep the per-frame CPP only for the live meter, and relabel its docstring.
   - Report `h1h2` only from frames that pass the F1 check.
   - Document in each function's docstring what the metric does and doesn't mean.
6. **Analysis version.** Add `ANALYSIS_VERSION = 9` to the result, so spec 07 can re-analyse old takes.

## Verify (`tests/test_formants.py`)
- At f0 260–300 Hz, /i/ and /u/ (synthetic, known formants): F1 is reported as missing, not wrong. At 150–220 Hz, F1–F3 stay within 10%.
- Two synthetic takes of the same voice with different vowel mixes give a vowel-nucleus ΔF within 30 Hz with a fixed ceiling.
- `cancel_lines` in `analyze_take` changes F1–F3 on a clean synthetic vowel by under 10 Hz. With `mic_noise` added, F4 moves back toward the clean value.
- CPPS ranks clean > breathy > noisy, and its absolute value lands in the published range for modal voice (cite it).
- Real takes: report per-take ΔF, F1-snap fraction, ceiling and h1h2 before and after. The two 02-37 takes, 13 s apart, should agree within about 30 Hz with the profile ceiling.
