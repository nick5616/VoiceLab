# 03 Core split (wave A)

**Goal:** every piece of functionality lives in plain Python modules that don't know a UI exists. The Qt UI becomes a thin layer that calls the core and draws what it returns. A future UI (the wave B redesign, later a phone app or a web front end) can then reuse the core unchanged. **Behaviour stays the same in this spec**: the app looks and works as it does today, but sits on the core. Wave B redesigns the UI on top of it.

**Files you own:**
- Existing: `engine.py`, `main.py`, `live_panel.py`, `ui_common.py`, `live_views.py`, `widgets.py`, `take_card.py`, `exercise.py`, `speech.py`, `voice_map.py`, `mic_check.py`, `progress.py`, `shift_panel.py`, `targets_dialog.py`, `coach.py`, `take_store.py` (all but the `ANALYSIS_VERSION` value, which spec 01 bumps), `profile_store.py` (none of it except what moving code strictly needs; spec 02 adds `voice_goal`, so leave that region alone), `packaging/`.
- Tests: `test_app.py`, `test_live_ui.py`, `test_takes_ui.py`, `test_engine.py`, `test_coach.py`, `test_progress.py`, `test_take_store.py`, `test_shift.py`.
- New: the pure modules below, `ui/__init__.py` (empty), the `ui/` widget modules listed below, and new tests.

**Don't touch:**
- `analysis.py`, `register.py`, `take_register.py`, `egg.py`, `denoise.py` (spec 01);
- `report.py` (spec 02);
- `ui/theme.py`, `ui/anim.py`, `ui/components/`, `ui/gallery.py` (spec 04).

Spec 01 keeps the signatures you call. Spec 02's `report.build_report(take, profile, previous, goal)` exists after merge; you may add `core.report(uid)` that calls it behind a `getattr` guard.

## Layout after this spec
- **Pure (no PyQt6, pyqtgraph or qtawesome; only numpy, scipy, parselmouth, pyworld, sounddevice, soundfile and the standard library):**
  - every top-level `.py` except `main.py`;
  - `tools/`.
- **UI:** everything Qt lives in `ui/`.
- **`main.py`:** a short entry point. It builds the profile, engine and core, the Qt bridge and the window, then runs the app.
- **Legacy widgets move into `ui/` under their own names**, emptied of logic:
  - `ui/main_window.py`, `ui/live_panel.py`, `ui/live_views.py`;
  - `ui/meters.py` (RangeBar, ShareBar, MetricBar, from `ui_common`);
  - `ui/widgets.py`, `ui/take_card.py`, `ui/exercise_view.py`, `ui/vowel_check.py`;
  - `ui/voice_map_view.py`, `ui/mic_check_dialog.py`, `ui/progress_dialog.py`, `ui/shift_panel.py`, `ui/targets_dialog.py`;
  - `ui/bridge.py` (below).

  Wave B replaces most of these.
- **`tests/test_boundaries.py`:** an AST scan that fails if any module outside `ui/`, except `main.py`, imports PyQt6, pyqtgraph or qtawesome, directly or via `from`.
  - Also assert that nothing in `ui/` defines functions that compute readings (a light heuristic is enough, e.g. no `numpy` statistics beyond display scaling).
  - The real rule is human: if a UI file decides *what* to show (a verdict, a threshold, a text), that belongs in the core.

## Core API (wave B builds against exactly this; extend it, don't rename)
```python
# events.py — a tiny pub/sub hub. dispatch(fn, *args) decides the thread a handler runs on:
Events(dispatch=None)             # default dispatch calls the handler immediately (tests)
events.subscribe(name, fn) -> unsubscribe()
events.emit(name, *args)

# jobs.py — background work with visible progress
JobRunner(events)                 # threads; emits "busy" whenever the job list changes
runner.submit(kind, label, fn, *args, on_done=None, single=False) -> job_id
#   single=True: one job of this kind at a time; a newer submit replaces a queued (not running) one
#   fn may accept a `progress` keyword: progress(done, total, text=None) updates the job
runner.jobs()                     # [{"id", "kind", "label", "done", "total", "text", "started"}]
runner.wait_all(timeout=None)     # for close and tests

# engine.py — no Qt
LiveEngine(sample_rate)           # same capture, buffers and WASAPI logic as now
engine.subscribe(fn) -> unsubscribe()   # fn(frame) on the analysis thread, every UPDATE_INTERVAL
engine.start(device=None, noise_floor=None); engine.stop(); engine.open_stream(...); engine.set_mode(mode)
engine.start_take(); engine.stop_take(); engine.recent_audio(s); engine.latest_window(); engine.capture_info

# live_session.py — everything live_panel decides today, as data
LiveSession(profile, mode="speak", clock=time.monotonic)
session.update(frame, window=None) -> snapshot   # call once per frame; cheap
session.set_mode(mode); session.set_targets(targets); session.refresh_profile(); session.set_egg(on)
session.noise_report(audio, sample_rate)         # runs denoise.noise_report; feeds the badge
# snapshot = {
#   "mode", "voiced": bool, "f0": float|None,
#   "note": {"text": "G#3 +5¢ · 213 Hz" | "—", "on_target": bool|None},
#   "metrics": {key: {"label", "value", "display", "position": 0-1|None, "verdict": True|False|None,
#                     "unavailable": reason|None, "band": (lo, hi) fractions|None, "baseline": fraction|None,
#                     "low_label", "high_label", "visible": bool}},
#   "coach": {"line", "full"},                    # updates at most every 2 s (as now)
#   "input": {"badge", "severity": "ok"|"warn"|"error", "tooltip", "issues": [...], "slow_path": bool,
#             "clipping": bool, "snr": float|None},
#   "egg": {"on": bool, "signal": bool},
#   "pitch_history": [...],                       # last HISTORY_SECONDS of f0, for the graph
#   "changed_text": bool,                         # text fields are throttled to LIVE_TEXT_SECONDS
# }

# display.py — wording and number formatting shared by every UI
METRIC_DEFS, MODE_SCALES                          # moved from ui_common
format_value(key, value); pitch_label(f0); note_ticks(lo, hi); take_date(take)
singing_summary(singing); exercise_summary(exercise); hiss_note(snr, snr_full)

# playback.py — sounddevice playback, no Qt
Player(play=sd.play, stop=sd.stop, clock=time.monotonic)
player.play_take(samples, start=0.0, then=None, gap=0.5); player.stop(); player.position()
#   -> {"playing": bool, "piece": "take"|"gap"|"compare"|None, "seconds": float|None}
player.on_finished(fn)

# practice controllers — the state machines now inside widgets
exercise.ExerciseSession(engine, events)          # start/stop/current_target/on_frame/result
speech.VowelCheckSession(engine, profile, events) # start/stop/skip/on_frame/result; READING_PASSAGES stays here
mic_check.MicCheckSession(engine)                 # phases, countdown, result (analyze_check, mic_advice)
voice_map.VoiceMapData + voice_map.MapSession     # on_frame, metric choices, grid for drawing
progress (already mostly pure)                    # keep the pure functions; ProgressDialog moves to ui/

# app_core.py — everything MainWindow decides today
VoiceLabCore(profile, engine=None, store=None, events=None, runner=None, clock=time.monotonic)
core.events; core.runner; core.profile; core.store; core.mode
core.takes() -> newest first; core.take(uid); core.take_audio(uid); core.compare_options(uid)
core.start_recording(); core.stop_recording()      # analysis runs as a job; emits "take_added"
core.import_file(path, isolate=False, egg_channel=None)
core.file_channels(path)                           # for the EGG question before importing
core.rename(uid, title); core.delete(uid); core.label_register(uid, label)
core.set_baseline(uid); core.set_target(uid) -> message; core.set_targets(targets)
core.set_mode(mode); core.set_takes_folder(folder); core.reload()
core.open_mic(name, egg_channel=None) -> message; core.input_devices(); core.egg_possible(name)
core.rebuild_voice_map(); core.save_mic_check(result)
core.session_minutes(); core.rest_nudge() -> str|None
core.progress_entries()
core.report(uid) -> report.build_report(...) or None   # guarded until spec 02 merges
core.close()                                       # waits for jobs, saves noise floor, voice map
# events: "take_added"(take), "take_updated"(take), "take_removed"(uid), "takes_loaded"(count, problems),
#         "status"(text, level: "info"|"success"|"warning"|"error"), "busy"(jobs),
#         "profile_changed"(), "mode_changed"(mode), "mic_changed"(info), "recording"(bool)
```
Move the logic of `main.py`'s module functions and `MainWindow`'s methods into `VoiceLabCore` without changing it. That covers re-analysis, register refit and rescore, ceiling choice, the voice map rebuild, saving and the history log, the target-from-take rules, and the session and rest nudge. Each background job goes through `JobRunner`, so the UI can show what's running and how far along it is.

## Qt bridge (`ui/bridge.py`)
- `qt_dispatch(fn, *args)` runs a handler on the Qt main thread (a queued signal or `QMetaObject.invokeMethod`).
- `EngineSignals(engine)`: a QObject with `frame_ready = pyqtSignal(dict)`, fed from `engine.subscribe`.
- The widgets subscribe to core events through `Events(dispatch=qt_dispatch)`.

## Do
1. Create `events.py`, `jobs.py`, `display.py`, `playback.py`, `live_session.py`, `app_core.py` and the practice controllers. Make `engine.py` Qt-free. Move every widget into `ui/`.
2. Rewire the moved widgets to call the core and draw snapshots and events. No reading, verdict or wording is computed in `ui/`.
3. **Keep every user-visible behaviour.** Update the existing UI tests to the new imports. The assertions stay the same unless a behaviour moved and is now asserted at the core level instead. In that case, add the core test and say so.
4. **New core tests, no Qt** (`tests/test_core.py`, `tests/test_live_session.py`, `tests/test_jobs.py`, `tests/test_playback.py`):
   - record → stop → `take_added` with a fake engine;
   - re-analysis of an outdated take leaves the WAV bytes unchanged (as in phase 9);
   - "busy" fires with a job's progress;
   - the snapshot says "unavailable" for untrusted metrics;
   - verdicts don't flip on the /a/–/i/ alternation;
   - the playback position walks take → gap → compare.

   Port the existing behavioural assertions from `test_live_ui.py` and `test_takes_ui.py` to the core where they are about logic.
5. **`tests/test_boundaries.py`**, as above.
6. **Packaging:** update the PyInstaller spec (`packaging/`) for the `ui` package, and check that `python main.py` still starts.

## Verify
- The full suite passes, with test counts before and after.
- Run the app offscreen against a **copy** of the takes folder and profile in `C:\tmp\phase10-work\03-core\`. Screenshots before and after should look the same.
- Running `python -c "import app_core, live_session, engine, jobs, playback, display"` doesn't load PyQt6: assert that `"PyQt6" not in sys.modules` in a test.
