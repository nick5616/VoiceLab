# 03 Live & dashboard (wave B, lab group `live`)

Read `00-overview.md`, then `C:\tmp\phase11-work\01-lab\HOWTO.md`, then `C:\tmp\phase11-work\02-audit\REPORT.md`:
- its "Per group → live" list;
- the State model;
- the Vocabulary sheet;
- every `findings.json` entry with `group` `live`, or `cross` touching your files.

Worktree: `C:\tmp\wt11-03-live`, branch `phase11-03-live`. Work folder: `C:\tmp\phase11-work\03-live\`.

**Goal:** the live view is the heart of the app, and it should be the most trustworthy thing in it.
- At a glance, a user knows: whether the app hears her; where her voice is **right now** relative to her zones and target; whether that reading can be trusted; and how she's doing over the last few seconds and the session.
- Every number on the screen agrees with every other, or is labelled as a different thing.

## Files you own
- `ui/screens/live.py`, `ui/screens/dashboard.py`, `ui/live_graph.py`, `ui/live_cards.py`, `ui/live_views.py`
- `live_session.py`
- `tests/test_live_ui.py`, `tests/test_live_session.py`, and the `live` group of `tests/uilab_baseline/`
- New files under `ui/live/` if you need them
- Additive only: `app_core.py`, `profile_store.py`

The dashboard *hosts* spec 04's `LatestTakePanel`, `RecentTakes` and `ProgressMini`. You own their placement and size on the dashboard, not their insides.

## Must fix (observed; confirm each in the lab first)
1. **One pitch, one value.**
   - The big readout, the Pitch card, the graph's live dot and the plane dot must show the same value at the same moment, or be labelled as different things.
   - Decide the model and write it down. For example: the readout and the dot show the smoothed "now" value, and the card shows the median "last 3 s" with that label.
   - The same applies to resonance between the card and the plane.
   - The lab's B1 check must pass on every live beat.
2. **"Now" vs "last take".** On the dashboard, the live area and the Latest take panel must be unmistakably different things. Use headings and visual treatment, and say which take and when. Coordinate wording with the audit's vocabulary sheet.
3. **1280×720:**
   - no clipped cards on the live screen;
   - nothing cut mid-text;
   - the dashboard's right column doesn't end mid-row.
   - At 1920×1080 the dashboard doesn't scroll (lab A4).
4. **Raw units:**
   - Weight (dB) and Clarity (dB) need a meaning: a zone or scale words and a plain headline, with the number second.
   - Register "chest/mix/head" needs a one-line explanation and a confidence.
   - Readings that don't apply right now collapse into one line with the reason. They're never shown as broken bars.
5. **Time axis:** label it with units ("seconds ago") on both screens, or remove it where it adds nothing. Use one convention for both.
6. **Zone wording** on cards matches `report.ZONE_LABELS` / the vocabulary sheet. No "Masculine" in one place and "typically masculine" in another. If a bar is too narrow for the label, change the layout, not the words.

## Must build (state and feedback; adapt to the audit's findings)
1. **Listening state.** A clear, calm, visible difference between:
   - mic off / unavailable;
   - listening with no voice ("Waiting for your voice…", readings dimmed or held with an "as of N s ago" hint);
   - hearing a voice;
   - recording.

   This lives where the eye already is (the readout area), not in the top bar alone.
2. **In-target feedback.**
   - When pitch (and resonance) sit inside the target, the user sees it immediately (glow or colour on the readout and the graph band), plus a **"held in target" timer** for the current stretch.
   - A session counter ("in target 42 % of the last minute").
   - The logic goes in `live_session.py` (pure, tested). The UI only draws it.
3. **Within-session trend:** a small strip or sparkline of the last few minutes' in-target share or median pitch, so "am I improving right now?" has an answer without recording.
4. **Trust at a glance:**
   - each live card carries a confidence cue (from the snapshot's existing reliability notes);
   - a tooltip or HelpTip says why;
   - when the Banner is up, the cards it affects show it.
5. Anything else the audit's live list ranks as major or blocker, if it's inside your files.

## Constraints
- **The speak-mode y-axis stays fixed** (phase 10). The sing-mode axis keeps its hysteresis.
- No logic in `ui/`. New decisions (in-target timer, session share, the "now" / "last 3 s" model, listening state) go in `live_session.py` with tests, and are exposed in the snapshot.
- Keep everything that is reachable today.

## Verify
- `tests/test_live_session.py`: in-target timer, session share and listening state from synthetic frame sequences (truth from `synth.py`).
- `tests/test_live_ui.py`: the same pitch on the readout, card and dot (or labelled differently); the listening states render distinctly; nothing clipped at 1280×720 for a full set of cards.
- Lab, group `live`, all scenarios, both sizes and themes:
  - **zero B (truth/consistency) and C (state) errors**;
  - every entry you can remove from `tests/uilab_baseline/live.json` is removed;
  - the scorecard before and after goes in your report.
- Look at the before and after PNGs yourself, and list them in your report.
