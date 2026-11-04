# 05 Shell, state & design system (wave B, lab group `shell`)

Read `00-overview.md`, then `C:\tmp\phase11-work\01-lab\HOWTO.md`, then `C:\tmp\phase11-work\02-audit\REPORT.md`:
- its "Per group → shell" list;
- the **State model** (you implement most of it);
- the Vocabulary sheet;
- every `findings.json` entry with `group` `shell`, or `cross` touching your files.

Worktree: `C:\tmp\wt11-05-shell`, branch `phase11-05-shell`. Work folder: `C:\tmp\phase11-work\05-shell\`.

**Goal:** the frame around everything always tells the truth about what the app is doing. That covers:
- the mic, and whether it's listening;
- background work and what it affects;
- errors and what to do about them;
- first run and settings.

The design system's components never truncate, clash or contradict.

## Files you own
- `ui/app_window.py` (top bar, nav, action bar, toasts, shortcuts), `ui/onboarding.py`, `ui/screens/settings.py`, `ui/screens/__init__.py`, `ui/mic_check_dialog.py`
- `ui/components/`, `ui/theme.py`, `ui/anim.py`, `ui/gallery.py`, `ui/bridge.py`
- `mic_check.py`
- `tests/test_shell.py`, `tests/test_app.py`, `tests/test_ui_components.py`, and the `shell` group of `tests/uilab_baseline/`
- Additive only: `app_core.py` (status texts and state helpers like `record_state`, `mic_status`), `profile_store.py`, `events.py`

Other agents use your components at the same time. **Keep every component's public signature backwards compatible.** Add keyword arguments; don't change existing ones. Fixes inside components (eliding, wrapping, label fitting) benefit everyone, so do them. Note any visible behaviour change in your report.

## Must fix (observed; confirm each in the lab first)
1. **Top-bar truncation:**
   - "Microphone (USB PnP Audio Devic";
   - the status line "…1 of 11…".

   Give a short, human device name (core helper: strip "Microphone (", the driver noise and the host API; full name in the tooltip), and a top-bar layout that never elides at 1280×720.
2. **Activity:**
   - "1 running" / "Nothing running" must name the job and what it affects ("Updating 11 takes to the new analysis · 3 of 11 · readings in older takes may change");
   - it must agree with `runner.jobs()` (lab C1);
   - finished jobs leave a short success toast.
3. **ZoneBar / MetricCard / Banner in `ui/components`:**
   - labels that don't fit switch to a layout that fits (labels under the bar, or a legend) instead of being shortened to different words;
   - whiskers never cover labels;
   - Banner text never wraps into "(+1 more)" mid-sentence. Show "and 1 more issue" as a link that expands.
4. **Contrast and size:** every finding from lab A5/A6 in components and the shell, in both themes.

## Must build (state legibility; adapt to the audit's State model)
1. **A global state indicator** in the top bar for the mic.
   - States: off / unavailable (with reason and a fix), listening (subtle level meter), hearing voice, recording, paused (practice running).
   - It must be readable in under half a second and distinct per state (lab C2).
2. **Errors with next actions:**
   - every error toast and Banner carries an action button (Mic check, choose mic, choose folder, retry);
   - persistent problems (no mic) stay visible as a Banner until fixed, not as a toast that vanishes.
3. **Mic setup wizard** (from Settings and onboarding, reusing `MicCheckSession`):
   - pick the mic;
   - check the level ("say 'ah'"), then noise, then whine;
   - a plain-language result with fixes;
   - offer the right capture rate if the device is 48 kHz-only (the user's USB mic). Use existing core/engine info; record a request if the engine needs API.
4. **Onboarding** that ends with something to *do*: a first guided "say a sentence" with live feedback, and the first take, not an empty dashboard. Every step still skippable.
5. **Keyboard and accessibility:**
   - shortcuts shown in tooltips and a "?" cheat-sheet overlay;
   - a visible focus ring;
   - accessible names on icon-only buttons (lab A5 / inspector).
6. Anything else the audit's shell list ranks as major or blocker, if it's inside your files.

## Constraints
- No logic in `ui/`. Device-name shortening, state derivation and error-to-action mapping are pure core helpers with tests.
- Keep every shortcut and every Settings item reachable (lab D).

## Verify
- Tests:
  - device-name shortening (the user's real device names from `engine.input_devices()` style strings, as test data);
  - the state indicator for every state;
  - the error → action mapping;
  - the activity text vs jobs;
  - the components fitting their labels at narrow widths;
  - the onboarding path to a first take.
- `python -m ui.gallery` screenshots of every component in both themes, before and after.
- Lab, group `shell`: `first_run`, `empty`, `no_mic`, `reanalysis_running`, `error_status`, `settings`, `listening_silence`, at both sizes and in both themes:
  - zero A (layout) errors in the shell;
  - zero C (state) errors;
  - the baseline entries you can remove are removed;
  - the scorecard before and after goes in your report.
- Look at the before and after PNGs yourself, and list them in your report.
