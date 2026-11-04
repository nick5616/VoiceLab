# 06 Practice flows (wave B, lab group `practice`)

Read `00-overview.md`, then `C:\tmp\phase11-work\01-lab\HOWTO.md`, then `C:\tmp\phase11-work\02-audit\REPORT.md`:
- its "Per group → practice" list;
- the Flow redesign;
- the State model;
- every `findings.json` entry with `group` `practice`, or `cross` touching your files.

Worktree: `C:\tmp\wt11-06-practice`, branch `phase11-06-practice`. Work folder: `C:\tmp\phase11-work\06-practice\`.

**Goal:** practising is a guided flow with a clear start, visible progress, live feedback and a result that leads somewhere. It's not a set of separate tools.

The practices are:
- read aloud (passages and the user's own passage);
- phrases;
- vowel check;
- exercises (sing, MIDI, cents scoring);
- Voice Map.

## Files you own
- `ui/screens/practice.py`, `ui/exercise_view.py`, `ui/vowel_check.py`, `ui/voice_map_view.py`
- `speech.py`, `exercise.py`, `voice_map.py`
- `tests/test_singing.py` (UI parts only; the analysis assertions stay), new `tests/test_practice_ui.py`, and the `practice` group of `tests/uilab_baseline/`
- New files under `ui/practice/` if you need them
- Additive only: `app_core.py`, `profile_store.py`

## Must fix (observed via the lab and the audit; confirm each first)
1. Every practice shows its **state**: ready, running (with progress), paused, done, or error. It has one obvious Start/Stop, and the record button and other controls show why they're disabled while it runs.
2. Every practice ends in a **result** that says, in plain words, what happened, and offers the next step:
   - read aloud → its take report;
   - vowel check → "use this as your resonance ceiling?" with what that changes;
   - exercise → score per note + the weakest note to retry;
   - Voice Map → what changed in your range.
3. Layout at 1280×720: the practice content, its live feedback and its controls are all visible without scrolling while it runs.
4. Vocabulary per the audit's sheet (cents, semitones, note names, "in tune" wording).

## Must build (adapt to the audit's findings)
1. **A guided session** ("Today's practice", the first card on the Practice screen):
   - a short routine, warm-up → one exercise or phrases → the standard passage → result;
   - built from the user's goal and mode;
   - it shows the step N of M, lets you skip a step, and ends with a summary that compares with last session.
   - Session logic is pure (`speech.py` / a new pure module), tested; the UI draws.
   - Kind tone and a rest suggestion after long sessions (reuse the core's rest nudge).
2. **Phrases with feedback:** each phrase gets a per-phrase result (pitch and resonance vs target, in-target share) right after it's spoken, using the live session's in-target logic. If spec 03's in-target API isn't on `main` yet, use a small adapter in your files and note it.
3. **Read aloud:** a highlighted current line or sentence (time-based is fine), with the passage visible while reading, and a clear finish.
4. **Exercises for singers:** a target-note lane with the sung pitch drawn against it, and per-note colour on finishing. Make sure the existing MIDI and cents scoring is visible and explained.
5. Anything else the audit's practice list ranks as major or blocker, if it's inside your files.

## Constraints
- No logic in `ui/`; the state machines stay in the pure session modules (`ExerciseSession`, `VowelCheckSession`, `MapSession`, and any new session class).
- Keep every practice and option reachable (lab D), including the custom passage, Voice Map rebuild and clear, and MIDI.

## Verify
- `tests/test_practice_ui.py` and pure tests:
  - the guided session's steps, skip and summary;
  - per-phrase results from synthetic voices with known f0 (`synth.py`);
  - each practice's state transitions rendered;
  - the result → next-step actions.
- Lab, group `practice`: `practice_each` plus a new `guided_session` scenario you add, at both sizes and in both themes:
  - zero C (state) errors;
  - zero A errors in practice screens;
  - the baseline entries you can remove are removed;
  - the scorecard before and after goes in your report.
- Look at the before and after PNGs yourself, and list them in your report.
