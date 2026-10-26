# 05 Shell, dashboard, live & practice (wave B)

Wave A is merged on `main`:
- **02 `report.py`:** `build_report`, `ZONES`, `zones`, `position`, `zone_of`, `live_zones`, and `profile.voice_goal` / `set_voice_goal`.
- **03 core split:**
  - `app_core.VoiceLabCore`, `live_session.LiveSession` snapshots, `jobs.JobRunner` ("busy" events), `events.Events`;
  - `display`, `take_view`, `targets`, `playback.Player`;
  - the practice sessions `ExerciseSession`, `VowelCheckSession`, `MicCheckSession`, `MapSession`;
  - `ui/bridge.py` (`qt_events`, `EngineSignals`);
  - the legacy widgets in `ui/`.
- **04 design system:** `ui/theme.py`, `ui/anim.py`, `ui/components/` (Card, Section, Collapsible, FlowGrid, Headline, HelpTip, ZoneBar, MetricCard, BigNumber, ShareBar, Sparkline, chips, Button, SegmentedControl, Tabs, RecordButton, NavRail, Toggle, Select, Spinner, ProgressStrip, ActivityCenter, Skeleton, ToastStack, Banner, EmptyState, MomentChip). Run `python -m ui.gallery` to see them all.

Spec 01 (measurement truth) may still be landing in `analysis.py`, `register.py` and `take_register.py`. Don't touch those files. It adds `take["confidence"]` and `tracks["<name>_smooth"]`; use them when present.

Read `00-overview.md` (the user's goals, and what Voice Tools does well) and spec 06 (which runs in parallel and gives you take, report and progress widgets).

## Goal
Replace the window with a new shell: a nav rail and these screens.
1. **Dashboard.** The unified full-screen view. A desktop user at 1920×1080 should never need another screen.
2. **Live:** focused practice.
3. **Practice:** passages, phrases, vowel check, exercises, Voice Map.
4. **Takes:** spec 06's screen.
5. **Progress:** spec 06's screen.
6. **Settings.**

Also: first-run onboarding, an activity indicator for everything running, and keyboard shortcuts. Every feature in today's window must still be reachable. Nothing is cut.

## Files you own
- `main.py`
- New: `ui/app_window.py`, `ui/screens/__init__.py`, `ui/screens/dashboard.py`, `ui/screens/live.py`, `ui/screens/practice.py`, `ui/screens/settings.py`, `ui/onboarding.py`, `ui/live_graph.py`, `ui/live_cards.py`.
- Legacy, yours to restyle or replace: `ui/main_window.py`, `ui/live_panel.py`, `ui/live_views.py`, `ui/meters.py`, `ui/widgets.py`, `ui/exercise_view.py`, `ui/vowel_check.py`, `ui/voice_map_view.py`, `ui/mic_check_dialog.py`.
- Delete `ui/main_window.py`, `ui/live_panel.py` and `ui/meters.py` when nothing uses them; spec 06 is told not to import them.
- Pure modules, **additive changes only** (new functions and fields; keep existing ones): `app_core.py`, `live_session.py`, `speech.py`, `exercise.py`, `voice_map.py`, `mic_check.py`, `profile_store.py` (UI preferences only).
- Tests: `tests/conftest.py`, `tests/test_app.py`, `tests/test_live_ui.py`, `tests/test_core.py`, `tests/test_live_session.py`, new `tests/test_shell.py`.

**Spec 06 owns** `ui/takes/`, `ui/progress/`, `ui/targets_dialog.py`, `ui/shift_panel.py`, `ui/take_card.py`, `ui/progress_dialog.py`, `take_view.py`, `progress.py`, `targets.py`, `shift_session.py` and `tests/test_takes_ui.py`. Use its widgets through this contract:
```python
from ui.takes.take_card import TakeCard           # TakeCard(core, uid, compact=False); signal open_report(str)
from ui.takes.takes_screen import TakesScreen     # TakesScreen(core); .show_take(uid)
from ui.takes.latest_take import LatestTakePanel  # LatestTakePanel(core): newest take's summary, a skeleton while
                                                  #   one is analysing, EmptyState before the first; signal open_report(str)
from ui.takes.recent_list import RecentTakes      # RecentTakes(core, limit=6); signal open_take(str)
from ui.progress.progress_screen import ProgressScreen, ProgressMini   # (core)
from ui.takes.targets_editor import TargetsEditor # TargetsEditor(core): a panel (embed it in Settings) with .apply()
```
Until spec 06 merges, guard these imports with a small placeholder widget, and say so in your report.

## Design
- **Theme:**
  - `theme.apply(app)` at startup.
  - The theme setting (dark / light / system) and `reduce_motion` live in the profile as UI preferences (`profile.ui`, a dict with a setter) and are applied live.
  - One `ToastStack` on the window.
- **Nav rail:** Dashboard, Live, Practice, Takes, Progress, Settings, with badges (for example, the Takes badge counts takes still being analysed).
- **Status and activity:**
  - The old status line goes. Core `"status"` events become toasts (`success`, `warning`, `error`) or a quiet line in the top bar (`info`).
  - `"busy"` feeds an `ActivityCenter` in the top bar, so re-analysis, loading, register refits and Voice Map rebuilds show what is running and how far along it is.
  - The `RecordButton` shows idle, recording (pulse and elapsed time) or analysing (spinner) from the core's `"recording"` event and the analysis job.
- **Input problems:** one `Banner` from the live snapshot's `input` (for example, "Desk rumble: set the mic on something soft · Mic check"), not badges and hatched bars. The path badge and the clipping badge fold into it.

### Dashboard, at 1920×1080 (degrade gracefully down to 1280×720)
```
┌Nav┬ Top bar: [Speak|Sing] · Goal: feminine ▾ · 🎙 USB mic ● ok · ActivityCenter · Session 12 min ┐
│   ├────────────────────────────────────┬──────────────────────────────────────────────┤
│   │ LIVE                               │ LATEST TAKE  (LatestTakePanel, spec 06)      │
│   │ big note readout   coach line      │ headline · overall ZoneBar · top tips ·      │
│   │ Pitch graph, FIXED axis, zones     │ [Open full report]                           │
│   │ (Pitch | Spectrum | Pitch×Res)     ├──────────────────────────────────────────────┤
│   │ live cards: pitch · resonance ·    │ RECENT TAKES (RecentTakes)                   │
│   │ weight · clarity · intonation      ├──────────────────────────────────────────────┤
│   │ (sing: tuning · vibrato · register)│ PROGRESS (ProgressMini sparklines)           │
│   │ phrase strip: "Can I get your…" ⟳  │                                              │
│   ├────────────────────────────────────┴──────────────────────────────────────────────┤
│   │ Import · Folder        ( RECORD )  Replay last 10 s · Mic check                   │
└───┴────────────────────────────────────────────────────────────────────────────────────┘
```

### Live pitch graph (`ui/live_graph.py`)
- **Speak mode:** a fixed log axis from 70 to 400 Hz, with `report.live_zones("speak")` shaded as soft bands, each labelled with its text label ("typically masculine", "overlap / androgynous", "typically feminine"). Show the target band and the baseline line. Ticks in Hz with note names. **The axis never re-zooms.** That's the Voice Tools lesson: a line's height has to mean the same thing every time.
- **Sing mode:** a note-name axis sized from the profile's Voice Map or comfortable range, with hysteresis. It changes only when the voice leaves the range, and then glides with `anim`.
- **The note readout and the graph are drawn from the same snapshot data.** On 23 Oct the readout said 248 Hz while the axis started at 262 Hz. That must be impossible now; test it.

### Live cards (`ui/live_cards.py`)
- Compact `MetricCard`s or `ZoneBar`s fed from `snapshot["metrics"]`, in plain language: "Pitch", "Brightness (resonance)", "Weight", "Clarity", "Intonation".
- The jargon goes in a `HelpTip`.
- Speak pitch and resonance show the report zones.
- Unavailable readings collapse into one line or the Banner, never four hatched bars.
- Markers glide (`anim.tween`).
- The text throttle from the snapshot stays.

### Live screen (focused)
- A big graph and a big note readout.
- A **phrase prompt**: a rotating set of everyday phrases, like Voice Tools' "Can I get your phone number?". Put the list in `speech.py`; write your own phrases.
- **Replay last 10 s**: `engine.recent_audio(10)` through `core.player`, with a playhead on the graph while it plays.
- The record button.
- Spectrum and Pitch × Resonance as tabs.
- Press **Space** to record or stop, and **R** to replay.

### Practice screen
Cards for:
- **Read aloud:** the existing passages, plus a "your own passage" option that pastes text and saves it in the profile.
- **Phrases.**
- **Vowel check:** restyled, on the `VowelCheckSession`.
- **Exercises:** sing mode, on the `ExerciseSession`.
- **Voice Map:** sing mode, on the `MapSession`.

Starting one shows it focused, with its own clear Start / Stop and progress.

### Settings screen
Sections:
- **Microphone:** picker, EGG channel, Mic check button, and the last mic check result in plain words.
- **Takes folder:** current folder, change, reload.
- **Voice goal:** feminine / androgynous / masculine / just exploring, with a sentence on what it changes.
- **Targets:** spec 06's `TargetsEditor`, plus baseline info.
- **Appearance:** theme, reduce motion.
- **About:** the kindness note.

### Onboarding (first run, `profile.ui["onboarded"]`)
1. A **kind welcome**, written in our own words, not Voice Tools'. Make these points:
   - this is a tool, not a judge;
   - voices vary, and the zones are tendencies, not rules;
   - rest when your voice feels tired.
2. **Voice goal**, or "just exploring".
3. **Takes folder.**
4. **Mic check.**

Every step can be skipped. Settings can reopen onboarding.

### Keyboard
- **Space:** record / stop.
- **R:** replay.
- **Ctrl+1…6:** screens.
- **Ctrl+I:** import.
- **F11:** full screen.

## Do
1. Build the shell, the screens and onboarding above on the core, with no logic in `ui/`. Anything new the UI needs to decide goes into the core as additive API: the phrase list, the replay clip, UI preferences, the custom passage, the onboarding state. Test it there.
2. Keep every existing feature reachable:
   - Speak/Sing;
   - targets, baseline, read aloud, vowel check, exercises, Voice Map (including rebuild and clear);
   - the Pitch × Resonance plane with take dots, and the spectrum;
   - import with Demucs isolation and the EGG choice;
   - takes folder, reload, mic and EGG pickers, mic check;
   - progress, session time and the rest nudge (now a gentle toast);
   - register labelling and shift (both through spec 06's card).

   List the mapping from old place to new place in your report.
3. **Visual quality bar.** Calm and readable, one clear headline per area, no near-identical grey buttons (use the Button variants), consistent spacing from `theme`. Look at your screenshots, and iterate until it looks finished at 1920×1080 and is still usable at 1280×720.

## Verify
- `tests/test_shell.py` (offscreen):
  - the window builds, and every screen opens;
  - nav and shortcuts work;
  - onboarding shows once, and can be skipped;
  - a recorded take turns the RecordButton to analysing and then idle, and the ActivityCenter shows the job;
  - a "status" error becomes a toast;
  - in speak mode the pitch graph's y-range stays fixed across a pitch sweep, and the readout value is always inside the drawn range (or the graph shows an off-scale marker);
  - one Banner, not several hatched bars, for an untrusted input;
  - replay plays `recent_audio` through the player (fake sounddevice).
- Port `test_app.py` and `test_live_ui.py` to the new shell. Their behaviours stay asserted.
- **Screenshots** of every screen, in both themes, at 1920×1080 and 1280×720, with live frames and a copy of the real takes. Put them in `C:\tmp\phase10-work\05-shell\`. Describe the user journey: first run → record → read the latest take → open the report → practise → check progress.
