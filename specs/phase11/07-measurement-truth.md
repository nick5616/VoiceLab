# 07 Measurement truth from the lab (wave B, parallel)

Read `00-overview.md`, `C:\tmp\phase11-work\01-lab\REPORT.md` (truth metrics and measurement findings) and `C:\tmp\phase11-work\01-lab\HOWTO.md`.

Worktree: `C:\tmp\wt11-07-truth`, branch `phase11-07-truth`. Work folder: `C:\tmp\phase11-work\07-truth\`.

**Goal:** the lab now measures what the user sees against known truth, end to end through the real UI. Fix the measurement errors it found, so the readings people trust are right. Prove each fix with the lab's truth metrics, before and after.

## Files you own
- `analysis.py`, `register.py`, `take_register.py`, `denoise.py`, `egg.py`
- the measurement path of `engine.py`: frame analysis, the resampler and the input chain. **Not** the heartbeat or stream status code: spec 05 adds those additively, so keep your edits away from `_on_audio`'s status handling and `start` / `stop`.
- `ANALYSIS_VERSION` in `take_store.py`
- the live smoothing in `live_session.py` **only** where the truth error comes from it. Coordinate by keeping the edit small and isolated, mark it `CROSS-OWNER EDIT` (spec 03 owns `live_session.py`), and prefer a parameter in `analysis.py` if you can.
- tests: `test_analysis`, `test_pitch`, `test_singing` (analysis parts), `test_register`, `test_denoise`, `test_engine`, `test_truth`, and new truth tests
- the `ZONES` numbers in `report.py` are **not** yours

## The work, in order (confirm each with the lab before fixing)
1. **Sing-mode octave errors.** On a clean synthetic sung A3 scale (`synth.sung_scale`), C#4 and D4 read as C#5 and D5: 47 % octave errors, 12.3 % median error. Find the cause (candidate selection, the octave-jump cost, the sing-mode ceiling, the vibrato/hold logic) and fix it. Target: 0 % octave errors and ≤2 % median error on the lab's `sing_truth_scale`, and no regression on `speak_truth_*` or held vibrato.
2. **Desk rumble drags speech down.** Speech plus rumble reads 82 Hz for a ~170 Hz voice: 37 % octave errors, 30 % median error. Phase 9 established:
   - rumble at 60–70 Hz is reported as voice;
   - no notch filters, because they damage F4;
   - coherent tone subtraction is used for the USB mic's integer-kHz tones.

   Find out why rumble wins over the voice here: voicing threshold, a missing high-pass before the pitch candidates, or candidate scoring. Fix it without hurting low male voices (85–100 Hz) or F4. Target: ≤3 % median error and <2 % octave errors in the lab's rumble beat.
3. **The banner names the wrong problem.** Under rumble, the input banner names the mic's whine. Fix the classification only if it lives in your files (`denoise.py` / `analysis.py` noise report). If it lives in `live_session.py`, write a request instead.
4. **Live vs take.** Live pitch runs up to +1.19 st above the take's own pitch (`2026-10-18 11-28-50`, live 169 Hz vs take 158 Hz).
   - Explain the gap: windowing, voicing decisions, smoothing, and which one is right against an independent tracker (`tools/accuracy_check.py` has WORLD and Praat ac references).
   - Bring the two within ±0.5 st on every real take, or document why they can't be and what the UI should say instead (a request to spec 03).
5. **Quiet voices.** A −52 dBFS synthetic voice over a −75 dBFS floor yields no pitch at all (audit A-011). Decide the correct behaviour:
   - a reading with a "too quiet" flag;
   - or no reading plus a `too_quiet` signal the live session can use.

   Expose it in the frame dict additively (for example `frame["too_quiet"]`). Spec 03 builds the `too_quiet` state on top; until then it reads `.get()`.
6. **Voice Map extremes.** The range shows "C2–D6" for a speech voice at 146–210 Hz. Find whether the extremes are octave errors or noise frames, and fix the map's inclusion rule. You own `voice_map.py`'s *range computation* only; spec 06 owns its display.

## Rules
- **Every change** gets:
  - a synthetic-truth test from `synth.py`;
  - a before/after lab truth table (`python -m ui.lab run --scenarios speak_truth_steady,speak_truth_glide,sing_truth_scale,noisy_input,speak_real_replay`);
  - `tools/accuracy_check.py` on a copy of the real takes, before and after.
- **No regressions:** the steady voices stay at 0.0 % error and 100 % in tolerance.
- **Version bump:** bump `ANALYSIS_VERSION` once, if take results change. Say what changes for the user's 11 takes (pitch, zones, report headline) in your report.
- Phase 9 and 10 constraints still hold (see `specs/phase9/00-overview.md` and `specs/phase10/01-measurement-truth.md`). Read them before changing the pitch tracker.
