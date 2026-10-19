# 05 Integration: register + EGG in the app (wave 2)

Runs alone on `main`, after branches 01–04 are merged. Its input is the four final reports, especially 03's symbol table and everyone's "Requests for integration". Handle each request: do it, or explain why not.

## Goal

Put the register estimate and the EGG into the app, so both audiences see them live, on take cards, in Progress, and on the Voice Map. Behaviour without an EGG must not regress.

## Register

1. **Take analysis.** Wire `register.register_track` and `take_register_summary` into the take pipeline: `analyze_take`'s caller, or `analyze_take` itself if that's cleaner, in both the mic and import paths. Use the profile's calibrated `RegisterModel` when there is one. Store the per-frame score in `tracks["register"]` and the summary in `result["register"]`. Old takes in a folder (without these keys) must still load. Compute the register lazily when a card needs it, or show nothing; don't crash.
2. **Live meter.** Add a "Register" meter to the live panel, labelled Chest ↔ Head with a "mix" zone shown on the bar, in both Speak and Sing modes. Show "fry" as the readout text when `is_fry` fires. Smooth it like the other per-frame metrics. When the model is uncalibrated, the title says "(estimate)".
3. **Take card.** A register row with chest/mix/head/fry shares as a small stacked bar, the number of breaks, and "estimate", or "calibrated to your voice" when it is. In the analysis graph, colour the pitch track by register score, or add a thin register strip under it, whichever reads better at card size. Check with a screenshot.
4. **Calibration.** On each card, a "Label register…" action with the options Chest / Head / Clear label. The label is stored in the take's JSON sidecar (`take_store`) or the history entry. Once there are enough labelled frames, refit the model and store `RegisterModel.to_dict()` in the profile (`profile_store.py`). Re-scoring existing cards after a refit can be lazy. Tell the user in the status bar when calibration first becomes active.
5. **Progress.** Series for "Head share" and "Chest share" (speaking and singing), plus "Register breaks per minute" (Sing only).
6. **Targets.** Add `register` (head share) to `TARGET_KEYS` with a sensible default tolerance, so feminization users can target "mostly M2-leaning". Update the Targets dialog accordingly.

## EGG

1. **Mic dialog.** When the chosen device has 2 or more input channels (`engine.input_channels`), offer "EGG on channel: none / 1 / 2". Remember the choice in the profile next to the mic. If opening the stream fails, show the error and revert to none.
2. **Live.** While an EGG is configured, show a "Contact quotient (EGG)" meter and an "EGG ●" badge in the live-panel header (grey when `signal_ok` would be false). Feed `frame["cq"]` into the register model's live score.
3. **Takes.** Save the EGG channel alongside the take: write a stereo WAV (mic first, then EGG) and set `"egg_channel": 1` in the JSON sidecar. `take_store` must load both mono and stereo takes. Mono-mixing a stereo take with an EGG channel would be a bug; add a test for it. Run `egg.analyze_egg_take`, put `cq` on the card and the `cq_track` into the register score.
4. **Import.** When an imported file is stereo, ask whether one channel is an EGG, and if so which. Then use `egg.load_stereo_take`.
5. **Voice Map.** Add CQ as a `MAP_METRICS` entry (range ~0.2–0.7) in `voice_map.py`. Only show it in the metric picker while EGG data exists.
6. **Progress.** A CQ series.

## Docs

Update the README:
- the metrics table (Register, Contact quotient)
- "Why these metrics": replace the line saying weight has no reliable acoustic measure with how EGG CQ now covers it when available, keeping the caveat for mic-only use
- a short "Register" section (what M1/M2 mean in plain words, that the estimate is uncalibrated until you label takes, how to label)
- an "EGG" section (supported setup, channel choice, gain, no AGC)

Cite the same sources as `register.py` and `egg.py`. Update the roadmap memory only if told to.

## Tests (`tests/test_app.py` and the store/profile tests)

- A live synthetic high-OQ vowel moves the register meter toward head, and a low-OQ one toward chest.
- Recording a take produces a card with a register row. A folder take saved without register keys still loads and shows a card.
- Labelling takes on both sides and crossing the minimum calibrates the model, persists it in the profile, and reloads it in a new window.
- With a fake 2-channel engine fed `egg.synth_egg` plus `synth_vowel`, the CQ meter appears, the take saves as stereo, reloads with the EGG intact, and the card shows CQ within ±0.05.
- Without an EGG, no CQ meter or badge is visible, and the Voice Map picker has no CQ.
- Importing a stereo file with "channel 2 is EGG" uses the EGG.

## Acceptance

- Full suite passes, pyflakes clean.
- Screenshots at wide and narrow sizes, with and without an EGG, are attached to the report (scratchpad paths), and nothing overlaps or jumps.
- `python main.py` launches on the real mic with no EGG and behaves as before, apart from the new Register meter.
