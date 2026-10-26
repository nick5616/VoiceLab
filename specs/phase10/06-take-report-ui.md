# 06 Take report, takes & progress UI (wave B)

Wave A is merged on `main`; see spec 05's opening for the full list:
- `report.py` (02);
- the core split (03): `app_core.VoiceLabCore`, `take_view.py`, `progress.py`, `targets.py`, `shift_session.py`, `playback.Player`, `display.py`;
- the design system (04): `ui/theme.py`, `ui/anim.py`, `ui/components/`. Run `python -m ui.gallery` to see them.

Spec 01 (measurement truth) may still be landing. It adds `take["confidence"]` and `tracks["f1_smooth"]` / `f2_smooth` / `f3_smooth` (and maybe `delta_f_smooth`). Use them when present; `report.py` already does.

Read `00-overview.md` for the user's goals and what Voice Tools does well. Read spec 05, which builds the shell in parallel and embeds your widgets.

## Goal
The take card is where the user said there's "a lot of space but also not enough". Today every number has the same weight, labels lead with jargon, hatched bars repeat, and the formant chart is spaghetti. Rebuild it **summary-first**, in the report's plain language, with all detail one click away. Then build the Takes screen (list + full report), the dashboard's latest-take panel and recent list, Progress, and the targets editor.

## Files you own
- New: `ui/takes/__init__.py`, `ui/takes/take_card.py`, `ui/takes/takes_screen.py`, `ui/takes/latest_take.py`, `ui/takes/recent_list.py`, `ui/takes/take_graph.py`, `ui/takes/targets_editor.py`, `ui/takes/shift_view.py`, `ui/progress/__init__.py`, `ui/progress/progress_screen.py`.
- Legacy, yours to replace and then delete once nothing imports them: `ui/take_card.py`, `ui/progress_dialog.py`, `ui/targets_dialog.py`, `ui/shift_panel.py`.
- Pure modules, additive changes only: `take_view.py`, `progress.py`, `targets.py`, `shift_session.py`, and `report.py` (small additions such as a `range` per dimension for the whisker; keep its API).
- Tests: `tests/test_takes_ui.py`, `tests/test_progress.py`, `tests/test_report.py` (additions only), new `tests/test_report_ui.py`.

**Don't touch:** `main.py`, `app_core.py`, `live_session.py`, `tests/conftest.py`, `ui/screens/`, `ui/app_window.py` (spec 05); `analysis.py`, `register.py`, `take_register.py` (spec 01). Don't import `ui/meters.py`, `ui/widgets.py` or `ui/live_panel.py`; spec 05 deletes them. If you need core API that doesn't exist, add the logic as a pure function in `take_view.py` or `progress.py` that takes `core` (or the take and profile) as an argument, or list it under requests for integration.

## Contract (spec 05 imports exactly these)
```python
TakeCard(core, uid, compact=False)      # signal open_report(str uid)
TakesScreen(core)                       # .show_take(uid)
LatestTakePanel(core)                   # newest take's summary; a Skeleton while one is analysing
                                        # (core runner job of kind "analysis"/"record"); EmptyState before the first take
                                        # signal open_report(str uid)
RecentTakes(core, limit=6)              # compact rows; signal open_take(str uid)
ProgressScreen(core), ProgressMini(core)
TargetsEditor(core)                     # a panel, not a dialog; .apply() saves; also usable inside a dialog
```
All of them subscribe to the core events (`take_added`, `take_updated`, `take_removed`, `takes_loaded`, `profile_changed`, `progress_changed`, `busy`) and update themselves. Unsubscribe on destroy.

## The take card (`TakeCard`), full form
```
┌ Title (click to rename)   Thu 23 Oct 9:21 · free speech · Speak   ● Good recording   [▶ Play] [⋯] ┐
│ Headline sentence                       (report["headline"], Headline component)                  │
│ Overall: ZoneBar masc ↔ overlap ↔ fem, with uncertainty whisker + one sentence on what pulls where │
│ Banner: the single collapsed issue, if any ("Too noisy to judge weight and clarity · Mic check")   │
│ Next time:  tip · tip · ★ best moment   (each with MomentChips that play that stretch)            │
│ Dimensions: FlowGrid of MetricCards (pitch, pitch floor, intonation, resonance, weight, clarity), │
│   each: plain headline, ZoneBar with target band + baseline + whisker, value, confidence chip,     │
│   change vs previous take, "Details" (jargon + numbers)                                            │
│ ▸ Graph & playback   pitch track over the zone bands, register dots, smoothed F1–F3 by default    │
│                      (raw toggle), click to seek, playhead, Compare with… (overlay + A/B play)    │
│ ▸ Singing / exercise (sing takes): the report's sing dimensions + NoteScoreChart                   │
│ ▸ Register: share bar, breaks, "Label register…" (calibration)                                    │
│ ▸ All numbers: a table of every summary value, including those phase 9's integration wanted       │
│     shown: CPPS, apparent tract length, delta_f_iqr, f1_missing_fraction, why ΔF was withheld,    │
│     SNR, level, voiced fraction, ghost fraction, formant ceiling, analysis version                 │
│ ▸ Hear it shifted (the shift panel, restyled)                                                     │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
⋯ menu: Set as baseline · Use as target · Compare with… · Label register · Open file location · Delete
```
- **Compact form** (lists, dashboard): title, date, task, the quality dot, the headline, a slim overall ZoneBar, and mini pitch and resonance bars. Click it to open the full form.
- **Hierarchy:**
  - One headline per card.
  - Numbers are secondary text.
  - Colour carries meaning only: the zones, quality, and on/off target.
  - Spacing comes from theme tokens, and sections are grouped visually.
  - Collapsibles remember their state (`Collapsible.memory`).
- **Moments:** a `MomentChip` plays that stretch through `core.player` and moves the playhead on the graph, if it is open.
- **Graph:** the pitch axis matches the live graph's speak-mode axis (fixed 70–400 Hz log, zones shaded), so takes and live look alike. Sing takes use note names. Formants are smoothed and voiced-only by default.
- **No hatched bars anywhere.** Unavailable readings use the MetricCard unavailable state and the single Banner.
- **Analysing state:** while a take is being re-analysed (`take_updated` replaces `tracks`; `busy` lists the job), show a subtle ProgressStrip on that card.

## Takes screen
- **Left:** a searchable, filterable list of compact cards (mode, task, quality, date range). Sort newest first. "Show older" becomes lazy loading as you scroll, using `core.show_older()`.
- **Right:** the full card for the selected take.
- An EmptyState when there are no takes, with an action to record or import.
- Skeletons while the folder loads (`core.loading`, `takes_loaded`).
- Delete asks for confirmation using `core.delete_question(uid)`.

## Progress screen
- One chart per report dimension: weekly medians with n, the zone bands shaded behind (the same colours as the report), the target band, and the baseline line.
- Filters: task (like with like), include poor takes (off by default), and time range.
- The existing pure logic stays in `progress.py`: `select`, `weekly_medians`, `change_text` and the suppression of trend text at small n. Add what the screen needs there, as pure functions.
- `ProgressMini`: sparklines of pitch and resonance (and the overall placement if you add it to `progress.py`) for the last few weeks, for the dashboard.

## Targets editor
A panel version of today's dialog, built on `targets.py`:
- presets ("typical, yours may differ");
- start from your baseline;
- per-dimension ranges shown on ZoneBars, so the user sees where the target sits in the zones;
- clear baseline;
- the voice goal is shown, and editing it lives in Settings (spec 05).

## Verify
- `tests/test_report_ui.py` and `tests/test_takes_ui.py` (offscreen):
  - the card shows the headline, overall, tips with moments, and one banner for a poor take, with no MetricCard bars for untrusted dimensions;
  - a MomentChip plays the right sample range (fake sounddevice);
  - compact ↔ full;
  - the ⋯ menu actions call the core;
  - TakesScreen filters and search;
  - LatestTakePanel shows a skeleton during an analysis job, then the take;
  - Progress excludes poor takes by default, and shows no trend text at n < 5;
  - TargetsEditor applies;
  - widgets unsubscribe on destroy (no events to deleted widgets).
- Keep the behavioural assertions of the old `test_takes_ui.py`, ported: re-analysis keeps the WAV bytes, set-as-target gives ≥ 60% time in target, and so on.
- **Screenshots** of the full and compact cards (good, fair and poor takes, speak and sing), the Takes screen, Progress and the Targets editor. Both themes, at 1920×1080 and 1280×720, using a **copy** of the real takes. Put them in `C:\tmp\phase10-work\06-takes-ui\`. Look at them and iterate until they read clearly at a glance. Include two in your report as paths, and describe what a first-time user understands from the card within 5 seconds.
