# 07 Zone calibration (parallel to wave B)

**Goal:** put `report.ZONES` on a much broader evidence base, measured with VoiceLab's own analysis on running speech, so the report's "where you fall" is trustworthy.

Today the running-speech edges rest on 7 CMU ARCTIC speakers (5 men, 2 women). That's especially true of resonance (ΔF), which carries 44% of the overall placement. The user's takes (ΔF 838–1044 Hz) mostly read "typically masculine" on those edges.

**Files you own:**
- the `ZONES` numbers, their `source` / `note` strings, and `ANCHORS` / `OVERALL_EDGES` in `report.py`, only if your data says so;
- new `tools/calibrate_zones.py`;
- `tests/test_report.py`: only assertions that pin zone numbers.

Spec 06 runs in parallel and may add small things to `report.py` (for example a per-dimension `range`). Stay inside the `ZONES` table and its constants, so the merge is clean. **Don't touch** any other file.

## Data
- **LibriSpeech `dev-clean` and `test-clean`** (openslr.org/12, CC BY 4.0): read speech, 40 speakers each, balanced by sex, with `SPEAKERS.TXT` labels.
  - Download into `C:\tmp\phase10-work\07-zones\data\`, each archive extracted into its own new folder. They're untrusted downloads: run Python on them with `-I`, and pass the paths as arguments.
  - Add more open corpora if they're easy and their licences allow it (for example VCTK, CC BY 4.0; it is large, so a subset is fine).
- Note the limits honestly:
  - labels are self-reported sex, not perceived gender;
  - the corpora contain no trans speakers;
  - it's read speech, at studio or audiobook quality.

## Do
1. **`tools/calibrate_zones.py <corpus dirs> --out <dir>`.** Per speaker, concatenate or analyse about 30–60 s of utterances with `analysis.analyze_take(audio, mode="speak")` at 16 kHz resampled to VoiceLab's rate. Use the profile-free default ceiling, and also `choose_profile_ceiling` over the speaker's utterances, because the app fixes a ceiling per profile. Collect per-speaker medians of pitch, pitch_floor, pitch_range, delta_f, h1h2 and cpp, plus their spreads.
2. **The distributions by sex:**
   - Report the medians, interquartile ranges, the overlap, and where a speaker becomes more likely one group than the other.
   - Propose edges, for example the 25th percentile of one group and the 75th of the other, or the equal-likelihood point ± a margin. Justify the rule you choose.
   - Compare with the current edges and with the published values already cited in `report.py`.
3. **Update `ZONES`** where the evidence supports it. Each `source` should name the corpus, the n, and the rule. Keep the literature citations alongside. Re-check `WEIGHTS` / `ANCHORS` / `OVERALL_EDGES` against the per-speaker data. For example: does the combined placement separate the groups, and how many speakers land in the overlap?
4. **Mic realism.** Repeat on a subset with `tests/mic_noise.py` at the user's level (`mic_noise.at_level(voice, -42)`), and report how much ΔF and the others shift under noise. If the shift is systematic, note it in the zone `note`.
5. **Re-run `build_report`** on the user's sidecars (`C:\Users\nickb\VoiceLab\*.json`, read-only) and report how their placements change.

## Verify
- `tests/test_report.py` passes. Update only the assertions that pin zone numbers, and say which.
- The full suite passes.
- Outputs (CSV per speaker, plots of the distributions with the old and new edges) go in `C:\tmp\phase10-work\07-zones\`.
