# 04 Takes, report & progress (wave B, lab group `takes`)

Read `00-overview.md`, then `C:\tmp\phase11-work\01-lab\HOWTO.md`, then `C:\tmp\phase11-work\02-audit\REPORT.md`:
- its "Per group → takes" list;
- the State model;
- the Vocabulary sheet;
- every `findings.json` entry with `group` `takes`, or `cross` touching your files.

Worktree: `C:\tmp\wt11-04-takes`, branch `phase11-04-takes`. Work folder: `C:\tmp\phase11-work\04-takes\`.

**Goal:** after a take, the user gets one clear answer and one clear next step, then as much depth as she wants. Over weeks, she can see whether she's getting better, and why a given day was different.

## Files you own
- `ui/takes/`, `ui/progress/`
- `take_view.py`, `progress.py`, `targets.py`, `shift_session.py`
- `report.py`: wording, structure, tips, confidence reasons and new fields. **Not** the `ZONES` / `ANCHORS` / `OVERALL_EDGES` / `WEIGHTS` numbers.
- `tests/test_takes_ui.py`, `tests/test_report_ui.py`, `tests/test_report.py`, `tests/test_progress.py`, and the `takes` group of `tests/uilab_baseline/`
- Additive only: `app_core.py`, `profile_store.py`

## Must fix (observed; confirm each in the lab first)
1. **Contradictory trust signals.**
   - "Good recording" sits next to "Medium confidence".
   - "Can't judge" cards sit beside a noise banner.
   - Six confidence chips appear before the first actionable sentence.

   One trust model for the take, in plain words: what was measured well, what wasn't, why, and what to do. The detail moves into an expandable "How sure is this?". Every "Can't judge" says why and offers the fix (Mic check, move the mic, speak longer).
2. **Zone and quantity vocabulary.**
   - One name per quantity and one wording per zone, per the audit's vocabulary sheet. "Masculine" / "Overlap" short forms only where the same words are used everywhere.
   - Overall-bar whiskers must not cover the zone labels.
3. **Hierarchy.**
   - The headline first, then the overall placement with **one** sentence, then the top tip with its replay button.
   - Everything else below, in a consistent order.
   - At 1280×720 the headline, the overall bar and the first tip are visible without scrolling in the report and in the dashboard's Latest take panel.
4. **Tips that can be acted on.** Each tip:
   - has one sentence of "what to try";
   - plays its moment with one click;
   - shows the moment on the take's graph while playing (playhead).

   Tips must not repeat numbers the card already shows. Keep them short (lab: sentence length).
5. **The Latest take panel** on the dashboard says which take and how long ago ("Your last take · 3 min ago"), so it can't be mistaken for live readings (spec 03 owns the live side of this).

## Must build (adapt to the audit's findings)
1. **Compare.** Any take against the previous take, the baseline or a chosen take: the per-dimension change in plain words ("higher by 1.3 semitones") and a replay-both A/B. `take_view`/`report` decide; the UI draws.
2. **Closing the loop.** If the previous take had tips, the new report says whether this take moved in that direction ("Last time: start sentences higher. This take: you did. Your first words were 12 Hz higher.").
3. **Progress that answers "am I getting better?"**
   - A headline sentence for the last 7 / 30 days per major dimension, with the trend direction and its confidence (n takes, spread).
   - Per-day variation explained where data allows: noise, mode, time of day, take length.
   - Sparklines labelled with units and zones.
4. **Takes housekeeping:**
   - undo delete (the take moves to a trash subfolder of the takes folder, and a toast offers Undo);
   - filter by mode and tag;
   - search by title;
   - rename inline.

   All as pure logic in `take_view.py` / the core, with tests. The trash folder stays inside the user's takes folder: plain files, the user's CRUD rule.
5. Anything else the audit's takes list ranks as major or blocker, if it's inside your files.

## Constraints
- Report text must never imply that pitch alone decides gender, or that one gender is "correct". Keep the caveat. Keep the masculine and androgynous goals symmetrical with feminine. Test all three goals.
- No logic in `ui/`. New decisions go in `report.py` / `take_view.py` / `progress.py` with tests.
- Keep register labelling, shift and targets reachable from the take card.

## Verify
- Tests for:
  - the trust model (one consistent statement per take);
  - tips that reference the previous take;
  - compare deltas;
  - the progress headline;
  - undo delete (file moved to trash and restored, sidecar included);
  - filter and search.
- Lab, group `takes`: `takes_report` over **every** real take (copy), `record_take` and `progress`, at both sizes and in both themes:
  - zero C (state) errors;
  - zero jargon (B6) outside Details;
  - the baseline entries you can remove are removed;
  - the scorecard before and after goes in your report.
- Look at the before and after PNGs yourself, and list them in your report.
