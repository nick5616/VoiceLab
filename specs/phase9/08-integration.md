# 08 Integration (runs alone, after waves A and B merged)

Specs 01–07 are merged on `main`. They were built in parallel, so some seams are open. Close them, and leave every check green. You own every file; nobody else is working.

## Must do
1. **Failing test.** `tests/test_takes_ui.py::test_formant_ceiling_is_fixed_once` fails now that spec 05's real `analysis.choose_profile_ceiling` exists; spec 07 had written it against a fallback. Make the behaviour correct and update the test:
   - The ceiling is chosen once, from the vowel check (always wins) or from the first few non-poor takes via `choose_profile_ceiling`.
   - It is stored in the profile.
   - It is passed as `analyze_take(..., ceiling=)` and to the live meters.
   - `main.py` must no longer copy each take's ceiling into the live meters.
2. **Remove the transitional guards.** Spec 07 wrapped calls into specs 05 and 06 in `hasattr`/`getattr`/`accepts(...)` checks while those specs didn't exist. Remove them and call the APIs directly:
   - `main.py`: lines around `show_mic_check`, `set_mode`, `set_targets`, the `noise_floor` start option, `choose_profile_ceiling` and `engine.noise_floor`;
   - `take_store.py`: `ANALYSIS_VERSION = getattr(analysis, "ANALYSIS_VERSION", 1)` should import `analysis.ANALYSIS_VERSION`.
   - Keep genuine optional checks, e.g. `reading_prompt` if it can legitimately be absent.
3. **Spec 06 wiring requests.**
   - Set `live_panel.engine = engine`.
   - Connect `live_panel.mic_check_done` to save the dict in the profile (`mic_check`).
   - Save `engine.noise_floor()` on close and pass it back at start.
   - Make take cards build their pitch `MetricBar` for the take's mode, using `MetricBar.set_mode` or a mode-aware def. Speech takes currently get the 60–1500 Hz sing range.
4. **Spec 05 requests.**
   - `register.py` / `take_register.py` should mask F1 with `tracks["f1_ok"]` and stop relying on `FORMANT_RELIABLE_MAX_F0 = 350` where `F1_RELIABLE_MAX_F0` (230) is meant. Keep `test_abrupt_switch_at_330_hz_is_exactly_one_break` passing, and report what masking did to the register scores.
   - The take card shows `cpps` as the clarity number for takes (label "Clarity (CPPS)"). The live meter keeps per-frame CPP.
   - Tract length is labelled "apparent", from `vtl_apparent_cm`.
   - Show `delta_f_iqr` and `f1_missing_fraction` as reliability cues, e.g. in the resonance row's tooltip or reason.
   - When ΔF is withheld, the card says why: "pitch too high for resonance (above ~230 Hz)" or "not enough steady vowels".
   - Update the text in `speech.py`, `ui_common.py` and the README that says "≈ x cm tract" or "keep pitch under ~350 Hz".
   - Make `take_store` persist `noise` as plain JSON (numpy floats → float) and add a round-trip test.
5. **`coach.py` mode-awareness.** Speech-only rules (the low pitch floor at "ends of sentences", flat intonation) must not fire in sing mode. Singing rules must not fire in speak mode. Add tests.
6. **Pyflakes.** `tests/test_takes_ui.py` imports pytest fixtures from `test_app`, which pyflakes flags. Move shared fixtures into `tests/conftest.py`, the idiomatic fix. Get `python -m pyflakes` clean across the repo, except `isolation.py`, whose optional imports are deliberate.
7. **README.**
   - Update the metrics table and the noise and mic sections: what each meter means now, what "—" means, the quality chip, the mic check, and WASAPI capture.
   - Add a short "Your mic" section with the plain advice: 10–15 cm away, slightly off-axis; raise the input level after moving closer; try a rear USB port; turn off enhancements; a USB interface plus XLR mic is the real fix.
   - Explain the whine plainly: integer-kHz tones from the USB mic's own electronics, now subtracted. A missing DAC isn't the cause; recording uses an ADC.
   - The README must not claim accuracy that hasn't been measured.
8. **`packaging/smoke_entry.py`.** Add the new modules (`coach`, `denoise`, `mic_check`) to its required list.

## Verify
- The full suite passes with `-k "not isolation"`, and pyflakes is clean.
- **End-to-end offscreen run** against temp COPIES of `C:\Users\nickb\.voicelab` and `C:\Users\nickb\VoiceLab` (never the originals, never real audio hardware):
  - Old takes re-analyse to version 9, with the WAVs byte-identical.
  - The profile gets one ceiling.
  - The cards show tier, advice, CPPS and "—" where appropriate.
  - Progress works and the live panel renders in both modes.
  - Screenshots go in `C:\tmp\phase9-work\08-integration\`.
- Commit on `main` directly, in logical commits. Message prefix "phase 9: integration", attribution `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Stage explicit paths only, and never commit `specs/` or `.claude/settings.json`.

## Report
Files changed, guards removed, behaviour changes, test counts, before/after where relevant, anything left open.
