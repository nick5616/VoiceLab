# 04 Coach core

Pure logic, no UI. It decides how much to trust a take, what to tell the user next, and what sensible targets look like. Wave B wires it into the UI.

## Measured problems (from the usefulness and speech audits)
- **Noise warning carries no information.** It fires on every take (SNR 11.5–19.7 dB against the 30 dB threshold), so it means nothing. Metrics are still drawn as confident bars labelled "Breathy" or "Muffled".
- **Targets contradict themselves.**
  - `profile_store.DEFAULT_TOLERANCES` is ±30 Hz ΔF and ±1.5 st pitch.
  - Measured within ±30 Hz of the take's own median: 16–36% of frames. Within ±1.5 st: 2–42%.
  - So a take fails its own target, and the on/off verdict flips 1.5–3.6 times a second.
  - Frame-level ΔF IQR is 70–180 Hz.
- **The feedback trains the wrong thing.**
  - Pitch alone turns the readout green.
  - Nothing flags possible pressing or strain. ΔF and ring rise with laryngeal raising and constriction, while H1*–H2* goes "heavy".
- **Defaults have no basis.** The Targets dialog starts at the meter midpoints (about 268 Hz, ΔF 1100), which aren't considered values.

## Do
1. **New `coach.py`: `take_quality(summary, noise=None)`.**
   - Returns `{"tier": "good" | "fair" | "poor", "reasons": [...], "trust": {metric_key: bool}}`.
   - Inputs:
     - SNR (full-band and voice-band);
     - voiced level;
     - `voiced_fraction` / `ghost_fraction` (spec 01 adds these; treat them as optional keys);
     - clipping;
     - the spec 02 `noise_report` dict when given: rumble share, lines.
   - `trust` says which metrics are reliable given the conditions. For example:
     - CPP and H1*–H2* need about 20 dB or more voice-band SNR; at 16 dB CPP reads 4–5 dB low.
     - Formant metrics need median f0 under about 250 Hz, because F1 snaps to H1 above.
     - Pitch range needs enough voiced frames.
   - Ground each threshold in the audit numbers above, or in literature.
   - Reasons are short plain-English strings, e.g. "very quiet: move closer (about 10–15 cm)", "steady electrical whine detected", "desk rumble".
2. **`coach.py`: `advice(summary, targets, baseline=None, mode="speak", quality=None)`.**
   - Returns an ordered list of at most 3 messages: `{"key", "text", "severity"}`.
   - **Table-driven rules**, each with a one-line source comment. Cover at least:
     - *Pitch on target, resonance off:* "brighten" cues such as "say 'ee', smile slightly, tongue forward; don't push your larynx up". Resonance matters as much as pitch for perceived gender (Leung, Oates & Chacon 2018, unverified).
     - *Resonance on target, pitch low.*
     - *Low pitch floor* (10th percentile, `pitch_floor` from spec 01): dips read as male.
     - *Possible pressing:* pitch up and weight heavy and ring up together, labelled tentative.
     - *Intonation flat.*
     - *Take too noisy to judge X:* suppress advice on untrusted metrics.
     - Singing: *consistently flat or sharp*, *late timing*, *tense high notes* (tentative).
   - Never advise on a metric `quality.trust` marks unreliable.
3. **`profile_store.py` target logic.**
   - `target_from_value` tolerance: from the take's frame spread (IQR/2) when the frames are available, otherwise a floor.
     - ΔF at least ±100 Hz at take level;
     - pitch at least ±2 st;
     - weight at least ±3 dB.
   - Measure that a take used as its own target gives ≥ 60% time in target on the real takes.
   - Add `TARGET_PRESETS`: literature-based starting ranges, e.g. speaking f0 about 165–255 Hz and ΔF about 1100+ Hz (Hillenbrand 1995; Gelfer & Bennett 2013; verify). Label them "typical cis-female speech, yours may differ".
   - Add a joint pitch-and-resonance in-target helper, so the UI can show green only when both are on target.
   - Keep the profile JSON backwards-compatible.

## Verify (`tests/test_coach.py`)
- Each advice rule has a table-driven test: an input summary gives the expected message key. The no-trust case suppresses advice.
- `take_quality` on synthetic results: clean gives "good". `mic_noise` at −42 dBFS voice gives "fair" with the right reasons. Very quiet or mostly ghost frames give "poor".
- Real takes: report each take's tier, reasons and top advice. Plain text is enough; it shows what the user would see. Build the summaries by calling `analysis.analyze_take` on the WAVs, and use the stored `.json` too.
- Tolerance: a take as its own target gives ≥ 60% time in target on the 7 real takes. Compute it with the current `analyze_take`, and note that spec 01 will lower the ghost rate.
