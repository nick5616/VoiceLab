# 07 Takes, targets & practice UI (wave B)

Wave A is merged on `main`:
- **Pitch** (spec 01): `analyze_take(audio, sr, mode)` with `voiced_fraction`, `ghost_fraction` and `pitch_floor`.
- **Input** (spec 02): `denoise.noise_report`.
- **Singing** (spec 03): `score_exercise` adds `close`, `time_close`, `notes_close`, `lag`, `timing`, `mean_signed_cents` and `lag_ms`. `singing_stats` adds `tonic_offset`, `tuning_spread`, `interval_error` and `tremolo_notes`, and returns None for speech. `voice_map` adds `clear`, `rebuild` and `mode`.
- **Coach** (spec 04): `coach.take_quality`, `coach.advice`, `TARGET_PRESETS`, `target_from_value(key, value, frames)`, `Profile.in_both` / `time_in_both`.

**Files you own:**
- `main.py`, `take_card.py`, `targets_dialog.py`, `progress.py`
- `take_store.py`, `take_register.py`, `profile_store.py`
- `voice_map.py` (UI parts), `speech.py`, `shift_panel.py`
- `tests/test_app.py`, `tests/test_profile.py`, new `tests/test_takes_ui.py`

**Parallel specs:** spec 06 (live panel, `ui_common`, `engine`, mic check) and spec 05 (formants in `analysis.py`). Don't edit their files.

## Calls you make on `main.py`'s side (spec 06 provides them)
- `engine.set_mode(mode)` when the mode changes (`live_panel.mode_changed` → `profile.set_mode` today).
- Save `engine.noise_floor()` into the profile on close, and pass it back as `engine.open_stream(..., noise_floor=…)`.
- `live_panel.set_targets(targets)` wherever targets change.
- `live_panel.show_mic_check()` from a button or menu, and once on first launch. Store its returned dict in the profile as `mic_check`.
- Spec 05 adds `analysis.choose_profile_ceiling(results)` and `analyze_take(..., ceiling=…)`. Store `profile.formant_ceiling` from it once, from the vowel check or the first few good takes, rather than copying each take's ceiling. Pass it to `analyze_take` and the live meters.
- Spec 05 adds `result["noise"]`, the `noise_report`, and `ANALYSIS_VERSION`.
- If a function from 05 or 06 doesn't exist yet when you start, code against the named API and guard it with a `hasattr`/`getattr` fallback. Say so in your report; it gets cleaned up at merge.

## Measured problems (usefulness audit)
- **Mode isn't passed to analysis.** `main.import_and_analyze`, `take_register.analyze_recording` and `take_store` call `analyze_take(audio)` without a mode. Sung takes above 500 Hz are now cut off.
- **Stored takes carry pre-fix analysis.** Their pitch range is 44–49 st, and the Voice Map is about 49% ghosts.
- **Quality warning:** the take card shows one orange "may be unreliable" banner on every take.
- **No guidance:** the card offers numbers only, with no next step.
- **Targets fail their own take.** "Set as target" from a take used ±30 Hz and ±1.5 st (now fixed in `profile_store`), so the take that set the target scored only 7–36% time in target. The Targets dialog's defaults are the meter midpoints.
- **Progress:**
  - it ignores quality and mixes unlike tasks;
  - its weekly medians have no n;
  - it says "+3.8 st since …" on noise.
- **Singing card:** it shows "¢ from A440".
- **Playback:** no stop, no playhead, no A/B.

## Do
1. **Mode and versioning.**
   - Pass the take's mode into every `analyze_take` call, and store `mode` in the sidecar.
   - On load, any take whose stored `ANALYSIS_VERSION` is below the current one is re-analysed in the background (`AnalysisWorker`, one at a time, like `update_register`). Write the new analysis into its sidecar JSON. The WAV must never be modified. Show progress in the status bar.
   - Offer "Rebuild Voice Map from takes" via `voice_map.rebuild`.
2. **Take card.**
   - **Quality:** a tier chip (good / fair / poor) with its reasons, from `coach.take_quality(result, result.get("noise"))`, replacing the banner.
   - **Untrusted metrics:** show "—" with the reason, never a bar.
   - **Advice:** up to 3 `coach.advice` messages at the top of the card.
   - **Joint verdict:** a "pitch + resonance in target" figure (`time_in_both`) next to the per-metric ones.
   - **Singing card:** show `tuning_spread`, `tonic_offset`, `interval_error`, `notes_close` / `time_close` and `timing` instead of "¢ from A440".
3. **Targets.**
   - Make "Set as target" call `target_from_value` with the take's frames.
   - In the Targets dialog, offer `TARGET_PRESETS` with their "typical, yours may differ" label, and start from the user's baseline or preset, not meter midpoints.
4. **Progress (`progress.py`).**
   - Skip poor takes by default, with a toggle.
   - Tag each take by task (free speech, a passage id, an exercise name, the vowel check) and compare like with like.
   - Show n per point.
   - Suppress trend text when n is under about 5 or the change is within the noise. Use the frame spread or the take-to-take SD from repeated takes.
5. **Playback.**
   - A stop button and a playhead on `AnalysisGraph` while playing.
   - Click to seek. Loop is optional.
   - "Compare with…" plays and overlays a baseline or chosen take.
6. **Session hygiene (light).** Show the session duration. After about 20–30 min of recording, show a gentle rest nudge, citing vocal hygiene guidance and marked unverified if you can't confirm it.

## Verify
- Offscreen Qt tests (`tests/test_takes_ui.py` and `tests/test_app.py`):
  - an old-version take gets re-analysed, and its WAV bytes are unchanged;
  - the card shows the tier, reasons and advice;
  - an untrusted metric shows "—";
  - "Set as target" from a take gives ≥ 60% time in target for that take on ΔF;
  - Progress excludes a poor take, and shows no trend text at n < 5;
  - a sung take is analysed in sing mode, and a 700 Hz note is kept.
- Run the app offscreen against a **copy** of the real takes folder and profile, never the originals. Take screenshots into `C:\tmp\phase9-work\07-takes-ui\` and describe the user journey: open the app → record or import → read the card → set targets → check progress.
