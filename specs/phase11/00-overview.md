# Phase 11: see it, prove it, fix it

Phase 10 built a new shell (nav rail, dashboard, live, practice, takes, progress, settings), a voice report and a design system. The user's next ask (2026-10-26):

1. **Observability.** Get as much visibility as possible into how the app actually looks and behaves.
2. **Strong evals** that can tell when it looks or behaves wrong.
3. **Agents that observe the UI, fix it, and think about UX:**
   - what is broken;
   - what is missing, in function and in **intuiting the state of things** (can the user tell what the app is doing, what it heard, and whether to trust it?);
   - how the flows could make more sense.

The bar is still the one in `specs/phase10/00-overview.md`. Read it. In short: Voice Tools (iOS) is the bar for intuitiveness. Keep the desktop depth, cut nothing, use plain-language headlines with numbers second, and prove accuracy rather than asserting it.

## Ground truth already observed (phase 10 screenshots, `C:\tmp\phase10-work\05-shell\`, `06-takes-ui\`)
These are examples, not the full list. The lab (spec 01) and the audit (spec 02) find the rest.
- **The same quantity disagrees across one screen.**
  - Dashboard: the big readout says **210 Hz** while the Pitch card says **196 Hz**, and the graph's live dot sits near 185 Hz.
  - The Latest-take panel says pitch "around 146 Hz" right beside the live readout. Nothing says that one is "now" and the other is "your last take".
- **Truncation:**
  - the top bar reads "Microphone (USB PnP Audio Devic";
  - the status line reads "Re-analysing your takes with the improved analysis: 1 of 11…";
  - the banner wraps to "(+1 more)".
- **Inconsistent vocabulary:**
  - zone bars say "typically masculine" in one place and "Masculine" / "Feminine" / "Overlap" in others;
  - "Brightness (resonance)" vs "Resonance";
  - "Intonation" 4.3 st vs 14.8 semitones.
- **Contradictory trust signals:** "Good recording" next to "Medium confidence", and "Can't judge" cards beside a "Too much background noise" banner, with no way to see why or what to do.
- **1280×720:**
  - the live screen's second row of cards is clipped mid-text ("Clarity", "29.7 dB" cut in half);
  - the recent-takes list is cut at the bottom of the dashboard.
- **Raw units with no meaning attached:**
  - Weight "-1.9 dB" and Clarity "29.7 dB" have no zones;
  - the Register "chest" bar has no explanation;
  - the time axis ("-7 … -1") has no unit on the live screen and is missing on the dashboard.
- **State:**
  - "1 running" with nothing visible on the page that says what that does to the readings;
  - no visible "listening / hearing you / silent" state;
  - nothing tells you whether the live readings come from the mic right now.

## Waves

| Spec | Wave | Agent | Owns |
| --- | --- | --- | --- |
| [01 UI Lab: observability & evals](01-ui-lab.md) | A | Opus | new `ui/lab/`, new `tools/uilab/`, new `synth.py`, new `tests/test_uilab.py`, `tests/conftest.py` (additive), the trace hook in `main.py` |
| [02 UX audit: journeys, gaps, flows](02-ux-audit.md) | A | Sonnet | no repo files; writes `C:\tmp\phase11-work\02-audit\` |
| [03 Live & dashboard](03-live-dashboard.md) | B | Sonnet | `ui/screens/live.py`, `ui/screens/dashboard.py`, `ui/live_graph.py`, `ui/live_cards.py`, `ui/live_views.py`, `live_session.py`, `tests/test_live_ui.py`, `tests/test_live_session.py` |
| [04 Takes, report & progress](04-takes-report-progress.md) | B | Sonnet | `ui/takes/`, `ui/progress/`, `take_view.py`, `progress.py`, `targets.py`, `shift_session.py`, `report.py` (wording and structure, not the `ZONES` numbers), `tests/test_takes_ui.py`, `tests/test_report_ui.py`, `tests/test_report.py`, `tests/test_progress.py` |
| [05 Shell, state & design system](05-shell-state.md) | B | Sonnet | `ui/app_window.py`, `ui/onboarding.py`, `ui/screens/settings.py`, `ui/screens/__init__.py`, `ui/mic_check_dialog.py`, `ui/components/`, `ui/theme.py`, `ui/anim.py`, `ui/gallery.py`, `ui/bridge.py`, `mic_check.py`, `tests/test_shell.py`, `tests/test_app.py`, `tests/test_ui_components.py` |
| [07 Measurement truth from the lab](07-measurement-truth.md) | B | Sonnet | `analysis.py`, `register.py`, `take_register.py`, `denoise.py`, `egg.py`, the measurement path of `engine.py`, `ANALYSIS_VERSION`, `voice_map.py` range computation, analysis tests |
| [06 Practice flows](06-practice.md) | B | Sonnet | `ui/screens/practice.py`, `ui/exercise_view.py`, `ui/vowel_check.py`, `ui/voice_map_view.py`, `speech.py`, `exercise.py`, `voice_map.py`, `tests/test_singing.py` (UI parts only), new `tests/test_practice_ui.py` |

**Shared files.** `app_core.py`, `profile_store.py` and `events.py` take **additive** changes only: new methods, events or fields; never renamed or removed ones. Each agent adds its code in a block headed by a comment `# phase 11 / <spec>`. The integrator resolves merges.

**Measurement code is out of scope** (except the two additive grants in the Wave B addendum below). Don't touch it: `analysis.py`, `register.py`, `take_register.py`, `egg.py`, `denoise.py`, `engine.py`, `coach.py`, the `ZONES` numbers in `report.py`. If the lab shows a measurement is wrong, record it as a finding with evidence. Don't fix it.

**Wave A** runs 01 and 02 in parallel. **Wave B** starts when 01 is merged on `main` and 02's report exists. Every wave B agent gets:
- the lab to observe its screens;
- the eval baseline for its screens, to ratchet down;
- 02's findings for its area, to fix and to build what's missing.

## Wave B addendum (after the audit, 2026-10-26)
The audit is done: `C:\tmp\phase11-work\02-audit\REPORT.md` and `findings.json` (55 findings, A-001 to A-055).
- **Your work list** is your group's list in REPORT.md, "Per-group work lists", in that order, with the acceptance criteria from `findings.json`. It supersedes the "Must fix / Must build" examples in your spec where they differ. Where both name the same thing, use the audit's acceptance.
- **Already on `main`** (`3586e3c`), so use them; don't redefine them:
  - `display.NAMES`, `ZONE_WORDS`, `GOAL_WORDS`, `TIME_WORDS`, `TAKE_TRUST_WORDS`, `READING_TRUST_WORDS`, `BANNED_TEXT`, `quantity_name(key)` and `zone_word(zone, short=False)`. That's the vocabulary sheet. Every visible label comes from these.
  - The core event `"take_ready"(take)`, which fires only for new takes (recorded, imported, exercise), unlike `"take_added"`, which also fires for loaded ones.
- **Extra ownership granted by the audit:**
  - **05 shell** gets an *additive* mic heartbeat in `engine.py` (A-034):
    - `last_audio_at`;
    - stream `status` and `finished_callback` handling;
    - `seconds_since_audio()`.

    The measurement path in `engine.py` stays untouched.
  - **03 live** gets an *additive* `goal=None` keyword in `coach.py` (A-010). Default behaviour must stay identical.
  - **04 takes** changes `report.ZONE_LABELS` wording to match `display.ZONE_WORDS` (the zone is "overlap", never "overlap / androgynous"), and updates the tests that pin that text.
- **Contracts between wave B agents.** The dashboard becomes "Today" (A-012), and 03 owns its layout. Build your side, and import the others' widgets behind a small `try/except ImportError` placeholder, so each branch works alone:
  - **03 → all:** `snapshot["state"]` in `{"no_signal", "silent", "too_quiet", "hearing"}`, plus `snapshot["held_seconds"]`, `snapshot["in_range_share"]`, and a per-metric `age` (seconds) / `stale` (bool). Readers use `.get()` with defaults.
  - **04 → 03:** `LatestTakePanel(core, compact=True)`: "Your last take · <date> (N days ago)", headline, one tip, Open report; at most 320 px tall. The default `compact=False` keeps today's panel.
  - **06 → 03:** `ui/practice/session_card.py` `SessionCard(core)`: today's session checklist; signal `open_step(str)` with a practice key. 03 connects it to `window.practice.open(key)` through a `go_practice` callback the shell passes in, or by emitting a signal the dashboard re-emits.
  - **05 → all:** `core.mic_status()` may return `state` `"lost"`. The top bar shows the snapshot's `state` when present.
  - **07 → 03:** live frames may carry `frame["too_quiet"]` (bool). Read it with `.get()`.
- **The lab is on `main`** (`87d0b4d`, 526 tests pass). Read `C:\tmp\phase11-work\01-lab\HOWTO.md` and `REPORT.md`. Its integration items go to:
  - **04:** fix the theme hooks that never disconnect at `ui/takes/take_card.py:208` and `ui/takes/take_graph.py:262` (use bound methods on a themed widget); fix the deleted QTimer at `TakePlayback._reset` (`take_card.py:143`).
  - **05:** the same theme-hook fix at `ui/app_window.py:54`. Also the Takes badge race: the busy event fires inside `runner.submit` before `jobs["reanalysis"]` is stored. Fix it in `app_core.py` / `app_window.py`.
- **Spec 07 (measurement truth)** runs alongside wave B. It owns the measurement code, so wave B still doesn't touch it.
- **Lab runs:** run your group with `--out C:\tmp\phase11-work\<spec>\lab\<name>` so the runs don't collide. A full lab run takes about 16 minutes, so use `--group` and `--smoke` while iterating. Run a full both-sizes, both-themes run of your group before and after.

## Operating mode: autonomous
**You have enough context to do this work.** The user is not available for questions and doesn't want to approve steps.
- Don't ask questions or stop for confirmation. Make the reasonable decision within your spec's scope, write it down with its reason under "Decisions" in your report, and keep going.
- If something outside your files blocks you, work around it inside your files: wrap it, subclass it, or add an adapter. Write the proper fix under "Requests for integration".
- A minimal, backwards-compatible edit to a file you don't own is allowed only when there's no workaround. Mark it `CROSS-OWNER EDIT: <file> — <why>` in your report.
- If a tool, download or test is flaky, retry once, then note it and move on.
- Finish the whole spec. If time runs short, finish the highest-impact items completely rather than everything halfway, and list what's left.

## Rules for every agent
1. **Environment.**
   - Windows 11. Use `C:\Users\nickb\repos\VoiceLab\venv\Scripts\python.exe` by absolute path; don't create a venv.
   - Suite: `python -m pytest tests -q -k "not isolation"` with `QT_QPA_PLATFORM=offscreen`.
   - Specs: read them by absolute path from `C:\Users\nickb\repos\VoiceLab\specs\phase11\` (and `phase10\` for background).
2. **Worktree.** Work only in your worktree, `C:\tmp\wt11-0N-<name>` on branch `phase11-0N-<name>`. It's already created from `main`.
3. **Baseline first.**
   - Run the full suite and (wave B) the lab on your screens before changing anything. Save both.
   - When you finish, the suite must pass.
   - Existing tests change only where your spec changes the behaviour they assert. List which ones and why.
4. **Code style** (as phase 10):
   - plain functions and small classes;
   - constants at the top, each with a trailing comment giving its unit or source;
   - one-line docstrings; no type annotations;
   - `python -m pyflakes` clean on the files you touch;
   - no new runtime dependencies (numpy, scipy, parselmouth, pyworld, sounddevice, soundfile, PyQt6, pyqtgraph and qtawesome are available).
5. **The UI boundary.**
   - Only `ui/` and `main.py` import PyQt6, pyqtgraph or qtawesome.
   - `ui/` draws what the core decides. Any new decision (text, state, thresholds) goes into a pure module with a test.
   - `tests/test_boundaries.py` enforces both.
6. **Real takes are read-only.**
   - Never modify, commit or upload anything in `C:\Users\nickb\VoiceLab\`.
   - Copy what you need to `C:\tmp\phase11-work\<spec>\`.
   - Scratch scripts, screenshots and lab runs go in `C:\tmp\phase11-work\<spec>\`.
   - Never open a real microphone or play audio. Fake `sounddevice` as the tests do.
7. **Look at what you build.** Every visual change is checked in lab screenshots (Read the PNGs) at 1920×1080 and 1280×720, dark and light, before you call it done. Iterate until it looks finished.
8. **Commit.**
   - Commit on your branch: `phase 11: <spec name>`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
   - Stage explicit paths only; never `git add -A` or `commit -a`; never commit `specs/` or `.claude/`.
   - Don't push, merge or rebase.
9. **Final report** (also saved as `C:\tmp\phase11-work\<spec>\REPORT.md`). Include:
   - files changed;
   - public API added or changed;
   - test counts before and after;
   - **lab scorecard before and after** for your screens;
   - before/after screenshots (paths);
   - decisions, each with its reason;
   - findings you didn't fix (for example measurement bugs);
   - requests for integration;
   - CROSS-OWNER EDITs.

## Integration (the orchestrating session)
- Merge each branch into local `main` with `--no-ff`. Run the full suite and the full lab after each merge. Fix merge fallout directly on `main`.
- Commits stay local (not pushed).
- Then produce the final lab run comparing pre-phase-11 `main` with post-phase-11 `main`.
