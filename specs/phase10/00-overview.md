# Phase 10: clear, trustworthy, decoupled

The user compared VoiceLab with the iOS app **Voice Tools** (pink/blue zones, made for trans voice training). Their verdict:
- VoiceLab is more powerful.
- It is also crowded, messy and hard to understand.
- Its reports are hard to read.
- There's no way to tell whether it actually works.
- Wrong readings ("error-code-smelly functionality") now hurt the experience more than the missing features do.

What they asked for:
1. **Intuitive UI, cutting nothing.**
   - One unified full-screen view that shows everything; a desktop user should need no other screen.
   - Focused screens for single tasks as well.
   - Take cards: there's a lot of space but also not enough, because of how things are coloured and spaced.
2. **Fewer false positives and negatives.**
3. **Visible activity.** Animations and indications that work is happening.
4. **Our own femininity/masculinity report.**
   - Ranges for each dimension, and where you fall.
   - Tips for your next take, specific to this take.
5. **All functionality fully decoupled from the UI,** so a future UI (a phone app, for example) can reuse it.

## What Voice Tools does well (the bar to clear)
- **A fixed pitch axis with coloured zones.** Where the line sits means something at a glance.
- **One idea per screen.** A practice phrase and a "replay last 10 s" button sit on the live screen.
- **Standard passage → one results screen,** with a big number first and details below.
- **Harmonics cards:**
  - a plain headline ("Brighter, upper harmonics are strong");
  - the number at the side;
  - a one-line explanation.
- **A kind first-run screen:** "your voice does not define your worth".
- **Its weakness:** gender is judged from pitch alone. "42% female, 48% male, 60% androgynous" adds up to 150%, and the user raised their "female score" just by pitching up. Our report must not do that.

## Measured problems (real takes in `C:\Users\nickb\VoiceLab\`, 10 speech takes)
- **Register:**
  - The default (uncalibrated) model reports **head 94–100% on every speech take**, at 147–212 Hz (median score 0.99).
  - Ordinary speech in this range is almost always M1 (chest).
  - Stored H1\*–H2\* is 7–15 dB on all of them, well above the model's 3 dB centre.
- **Singing on speech:** the speech take `2026-10-23 09-21-26` carries `singing` stats: "Held notes 1 · straight tone · your centre is 3¢ below A440".
- **Live pitch graph:** it showed an axis of C4–A#4 (262–466 Hz) while the readout said 248 Hz. The graph re-zooms to follow the voice (`live_views.PitchView._follow_pitch`), so the height of the line means nothing from one moment to the next.
- **Take card:**
  - a formant "spaghetti" chart;
  - jargon labels (ΔF, H1\*–H2\*, CPP, dBFS, ¢);
  - hatched "too noisy" bars repeated on four rows;
  - every number given the same visual weight.
- **Logic tangled into widgets:**
  - `main.py` holds take orchestration, job queues, ceiling choice and re-analysis.
  - `live_panel.py` holds the verdicts, the rolling summary, the coach line, noise issues and live register scoring.
  - `engine.py` is a `QObject`/`QThread`.
  - `exercise.py`, `speech.py`, `voice_map.py`, `mic_check.py` and `progress.py` mix state machines with widgets.

## Waves

| Spec | Wave | Owns |
| ---- | ---- | ---- |
| [01 Measurement truth](01-measurement-truth.md) | A | `analysis.py`, `register.py`, `take_register.py`, `egg.py`, `denoise.py`, the `ANALYSIS_VERSION` constant in `take_store.py`, new `tools/accuracy_check.py`; tests `test_analysis`, `test_pitch`, `test_singing`, `test_register`, `test_egg`, `test_denoise`, new `test_truth.py` |
| [02 Voice report](02-voice-report.md) | A | new `report.py`, new `tests/test_report.py`, `profile_store.py` (the `voice_goal` field only), `tests/test_profile.py` |
| [03 Core split](03-core-split.md) | A | `engine.py`, `main.py`, `live_panel.py`, `ui_common.py`, `live_views.py`, `widgets.py`, `take_card.py`, `exercise.py`, `speech.py`, `voice_map.py`, `mic_check.py`, `progress.py`, `shift_panel.py`, `targets_dialog.py`, `coach.py`, `take_store.py` (all but `ANALYSIS_VERSION`), `packaging/`; new pure modules; `ui/__init__.py` and the legacy widgets moved into `ui/`; tests `test_app`, `test_live_ui`, `test_takes_ui`, `test_engine`, `test_coach`, `test_progress`, `test_take_store`, `test_shift`, new core tests, `test_boundaries.py` |
| [04 Design system](04-design-system.md) | A | new `ui/theme.py`, `ui/anim.py`, `ui/components/` (package), `ui/gallery.py`, `tests/test_ui_components.py` |
| [05 Shell, dashboard & live](05-shell-live.md) | B | written after wave A merges |
| [06 Take report & progress UI](06-take-report-ui.md) | B | written after wave A merges |

Wave A runs in parallel in `C:\tmp\wt10-0N-<name>` on branch `phase10-0N-<name>`. Wave B builds the new UI on 02's report, 03's core and 04's components.

## Rules for every agent
1. **Environment.**
   - Windows 11. Use `C:\Users\nickb\repos\VoiceLab\venv\Scripts\python.exe` by absolute path; don't create a venv.
   - Run the suite with `python -m pytest tests -q -k "not isolation"`. Qt tests run offscreen: `QT_QPA_PLATFORM=offscreen`, as in the existing tests.
   - Specs are uncommitted. Read them by absolute path from `C:\Users\nickb\repos\VoiceLab\specs\phase10\`.
2. **Baseline first.** Run the full suite before changing anything and note the result. When you finish, the whole suite must pass. Existing tests change only where your spec changes the behaviour they assert; list which ones and why.
3. **Stay inside your files.** Anything you need elsewhere goes under "Requests for integration" in your report. Keep the public signatures other specs rely on; add keyword arguments rather than changing positional ones.
4. **Code style.**
   - Plain module-level functions and small classes.
   - Constants at the top of their zone, each with a short trailing comment giving units or the source.
   - One-line docstrings; no type annotations.
   - Leave `python -m pyflakes` clean on the files you touch.
   - No new runtime dependencies: numpy, scipy, parselmouth, pyworld, sounddevice, soundfile, PyQt6, pyqtgraph and qtawesome are already there.
5. **Thresholds and ranges need a source.** Cite literature, the measurements quoted here, or your own measurement, and say which. Mark anything from memory "unverified".
6. **Real takes are read-only.**
   - Never modify, copy into the repo, commit or upload anything in `C:\Users\nickb\VoiceLab\`.
   - If you need a writable copy, copy it to `C:\tmp\phase10-work\<spec>\` and point a profile there.
   - Scratch scripts, plots and screenshots go in `C:\tmp\phase10-work\<spec>\`.
   - Never open a real microphone or play audio in tests.
7. **The UI boundary.** No module outside `ui/` except the `main.py` entry point may import PyQt6, pyqtgraph or qtawesome. `tests/test_boundaries.py` (spec 03) enforces this. Specs 01 and 02 write pure modules only.
8. **Commit.**
   - Commit on your worktree branch with message `phase 10: <spec name>`, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
   - Stage explicit paths only; never `git add -A` or `commit -a`; never commit `specs/` or `.claude/`.
   - Don't push, merge or rebase.
9. **Final report.** Include:
   - files changed;
   - public API added or changed (signatures);
   - test counts before and after;
   - before/after numbers where measurement changed;
   - design decisions, with the source for each;
   - known limitations;
   - requests for integration.
