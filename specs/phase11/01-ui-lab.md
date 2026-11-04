# 01 UI Lab: observability and evals (wave A)

Read `00-overview.md` first.

**Goal:** a lab that can put the real app into any state, show exactly what it looks like and what it is doing, and score it with evals strong enough that "it looks fine to me" is never the standard. Every wave B agent will live in this tool, so it must be:
- fast to run on one screen;
- deterministic;
- easy to read for an agent that looks at PNGs and JSON.

Worktree: `C:\tmp\wt11-01-ui-lab`, branch `phase11-01-ui-lab`. Scratch and runs: `C:\tmp\phase11-work\01-lab\`.

## Files you own
- New `ui/lab/` (Qt side): scenario driver, capture, widget inspector, in-app inspector panel, CLI `python -m ui.lab`.
- New `tools/uilab/` (pure, no Qt): eval logic over the captured JSON, scorecard, HTML report, diff.
- New `synth.py` (pure numpy):
  - known-truth voices: `synth_voice(f0_track, formants, seconds, sr, breath=0, jitter=0)` and friends;
  - for example `glide`, `steady`, `speech_like` (pitch contour with phrase pauses), `sung_scale`, `silence`, `noise_only`, `clipped`;
  - adapt from `tests/test_analysis.synth_vowel` and `tests/mic_noise.py`, but don't change those.
- New `tests/test_uilab.py`, `tests/conftest.py` (additive fixtures only), and a trace hook in `main.py` (additive, see below).
- `tests/test_boundaries.py`: only to register `synth.py` / `tools/uilab` as pure, if needed.

Keep the boundary:
- `ui/lab` may import Qt and must not compute readings (no `analysis` and friends, no `np.median` and friends).
- Everything that judges goes in `tools/uilab` and is unit-tested without Qt.

## 1. Scenarios: put the app into a known state
A scenario is a small Python description:
- a profile (fresh, onboarded or not, a goal, targets and a baseline or none, a theme);
- a takes folder: none, empty, a **copy** of the user's real takes from `C:\Users\nickb\VoiceLab\` (copy into the run folder; never write to the original), or synthetic takes made with known truth;
- a list of **beats**. Each beat is one action, then a capture:
  - `go(screen)`, `open_practice(key)`, `set_mode`;
  - `feed(audio, realtime=False)`: drive `engine.feed` + `engine.publish` per hop, as `tests/test_shell.py` `play()` does;
  - `record_start` / `record_stop`, `wait_jobs`, `import(path)`, `click(object_name)`, `key(seq)`;
  - `resize(w, h)`, `theme(name)`;
  - `fake_mic_error`, `inject_status(text, level)`;
  - `start_reanalysis` (mark takes outdated so the real re-analysis job runs).

Ship at least these scenarios. Each is captured at 1920×1080 and 1280×720, dark and light. The CLI chooses the subset.

| id | what it shows |
| --- | --- |
| `first_run` | onboarding, every step, then the empty dashboard |
| `empty` | onboarded, no takes, mic idle (silence fed) |
| `listening_silence` | mic open, room noise only (`mic_noise` at the user's -42 dBFS level) |
| `speak_truth_steady` | synthetic steady voice at known f0 / formants (e.g. 150, 185, 220 Hz) |
| `speak_truth_glide` | a known glide 120→260 Hz |
| `speak_real_replay` | each real take's WAV (copy) fed live at hop rate: what a user would have seen while speaking it |
| `record_take` | record → analysing → take appears (Latest take, Recent, Takes badge, toast) |
| `noisy_input` | voice + desk rumble / whine / clipping, one beat each, so the banner, cards and confidence change |
| `no_mic` | `set_mic_available(False, ...)` |
| `reanalysis_running` | real takes copy with an outdated `analysis_version`: shows the activity centre mid-job |
| `sing_truth_scale` | sing mode, a known scale and held note with vibrato |
| `practice_each` | each practice (read, phrases, vowels, exercise, voicemap) opened, started, mid-run, finished |
| `takes_report` | Takes screen, every real take's full report, Details expanded |
| `progress` | progress screen with the real takes |
| `settings` | every settings section |
| `error_status` | a core `status` error and warning |

## 2. Capture: what each beat records
In `C:\tmp\phase11-work\<who>\lab\<run-id>\<scenario>\<beat>-<theme>-<WxH>.*`:
- **`.png`:** `window.grab()`, plus `.full.png` for any scroll area on the current screen, rendered at full content height so content below the fold is visible.
- **`.tree.json`:** the widget inspector's dump of every **visible** widget. For each one:
  - class, `objectName`, a stable path (screen › card › widget);
  - geometry in window coordinates, the visible-region rect (after clipping by ancestors and scroll areas), and `isVisible` / `isEnabled`;
  - text (label, button, line edit, tooltip, accessible name);
  - font size in px, whether the text is **elided or overflowing** (`QFontMetrics` width vs available width, and wrapped height vs available height), and whether it's clipped by an ancestor;
  - foreground and background colours (palette and stylesheet, and also **sampled from the PNG** under the text rect);
  - for pyqtgraph plots: the view range, axis ticks and labels, and the item data ranges (every curve's last x/y, scatter points, zone bands);
  - the component kind and its key state (ZoneBar marker value and zones, MetricCard value text, RecordButton state, Banner text and level, ActivityCenter rows, nav badges, toasts).
- **`.state.json`:** the core's own truth at that moment:
  - `core.record_state()`, `core.mode`, `core.loading`, `core.analyzing`;
  - `runner.jobs()`, `mic_available` / `mic_problem`, `takes_in_progress()`;
  - the latest live **snapshot** (whole dict);
  - the scenario's **ground truth** (known f0 and formants of the synthetic voice; injected noise kind; expected take count).
- **`events.jsonl`, per scenario:** every `core.events` emit with a timestamp and args (summarised), and every engine frame publish with its timestamp. Also when each UI widget last changed its text, so latency is measurable: frame publish → visible readout change.

## 3. Evals (`tools/uilab/evals.py`, pure, unit-tested)
Each check returns findings `{check, severity (error|warn|info), screen, scenario, beat, size, theme, widget_path, message, evidence}`. Severity rules are constants at the top, each with a reason.

**A. Layout**
1. **Truncation:** text elided or overflowing its widget, or clipped by an ancestor or the window (outside a scroll area).
2. **Overlap:** sibling widgets whose visible rects intersect (excluding intended overlays: toasts, popovers, the plot's own items).
3. **Off-screen:** a widget in the visible screen that lies partly outside the window, outside any scroll area.
4. **Fold:** the dashboard at 1920×1080 must not need scrolling (spec 05: "a desktop user should never need another screen"). Report how much each screen scrolls at each size.
5. **Small targets:** clickable widgets under 28×28 px; text under 11 px.
6. **Contrast:** WCAG contrast ratio from the **sampled** colours, ≥4.5 for normal text and ≥3 for large text and for non-text indicators (zone bands vs background, markers).

**B. Truth and consistency** (the most important; this is what "can I trust it" means)
1. **One quantity, one value:** every on-screen display of the same quantity at the same moment agrees within tolerance (pitch: ±1 Hz or the same rounding). That covers big readout vs Pitch card vs graph live dot vs plane dot. Where two displays deliberately differ (live vs last take, smoothed vs instant), the eval demands that they are **labelled** differently ("now", "last take", "last 3 s"). It reads the labels from the tree.
2. **Displayed vs ground truth:** for `*_truth_*` scenarios, the displayed pitch is within ±2 % of the known f0 after settling (n frames), and the zone shown is the zone of the known value under `report.zone_of`. Report error and settle time.
3. **Live vs take:** for `speak_real_replay`, the median of the readout over the replay vs the take report's pitch for the same WAV. Report the difference in Hz and semitones.
4. **Readout in range:** the readout value lies inside the graph's y-range, or an off-scale marker is shown (phase 10 rule).
5. **Units and vocabulary:** one name per quantity and one wording per zone across the app. Flag any zone label not in `report.ZONE_LABELS` (or a declared short form) and any quantity shown under two different names or units on one screen.
6. **Jargon:** visible text (not tooltips, not inside an expanded "Details") that contains a term from a list:
   - `ΔF`, `H1`, `CPP`, `dBFS`, `F0`, `f0`, `¢` without words, `st` without "semitones", `HNR`, `jitter`, `shimmer`, `M1`/`M2`;
   - raw units with no zone or scale words (a lone "dB" value).

**C. State legibility** ("intuiting the state of things")
1. **A state oracle:** a table from core truth (`state.json`) to the visible signature that must accompany it:
   - recording → RecordButton recording + elapsed time;
   - analysing → spinner and an activity row;
   - a job running → an ActivityCenter row naming it;
   - mic unavailable → an error mic chip + disabled record with a reason;
   - untrusted input → exactly one Banner;
   - voice heard vs silence → some visible "hearing you" vs "waiting for your voice" difference;
   - loading takes → a skeleton or spinner, not an empty state;
   - no takes → an EmptyState with a next action.

   Missing signatures are errors. Contradictions are errors too: "Good recording" with a Can't-judge card and no reason given, or "Nothing running" while `runner.jobs()` is non-empty.
2. **Distinguishability:** for every pair of states in a scenario that the core considers different, the screenshots must differ in the region the user is looking at (the RecordButton or the top bar), not only somewhere off to the side. Use a perceptual diff of the relevant crop.
3. **Responsiveness:** from frame publish to visible readout change; from record stop to the take visible; from status event to toast. Report p50/p95 against budgets (constants: e.g. readout ≤ 150 ms, take visible ≤ analysis time + 500 ms).
4. **Next action:** every empty state, error toast, banner and "Can't judge" offers a next action (a button or a link), or says why none exists.

**D. Reachability**
- A feature map (constant list, from spec 05's "keep every existing feature reachable" list) giving the click or key path to each feature. The eval drives each path and asserts the target is visible.
- Report clicks from the dashboard per feature.

**E. Visual regression**
- `python -m tools.uilab diff RUN_A RUN_B`: a per-beat perceptual diff (no new deps: numpy over the PNGs via `QImage` in `ui/lab`, or `soundfile`-free raw arrays) plus a findings delta: new, fixed and unchanged findings by check.

## 4. Scorecard and report
- **`scorecard.json`:** counts by check × severity × screen, and the truth metrics (pitch error, settle time, live-vs-take difference), latency p50/p95, and reachability.
- **`index.html`:** a contact sheet: per scenario, the beats as thumbnails (click for full size), each with its findings listed and drawn as rectangles over the screenshot (an overlay PNG `.marked.png` too, for agents that read images). At the top, the scorecard. Local file, no CDN.
- **The ratchet:**
  - `tests/uilab_baseline/<group>.json` holds the currently accepted findings, keyed by check + screen + widget path, with no pixel coordinates (so they're stable).
  - Groups match the wave B owners: `live` (dashboard, live), `takes` (takes, report, progress), `shell` (top bar, nav, onboarding, settings, toasts, banners, mic check), `practice`.
  - `tests/test_uilab.py` runs a **fast smoke set** (one size, dark, key beats; under ~60 s), and fails on any finding not in the baseline.
  - `python -m ui.lab baseline --group live` rewrites one group. Wave B agents may only **remove** entries from their group (and must justify any addition in their report).

## 5. In-app observability (real mic, real use)
1. **Trace:** with `VOICELAB_TRACE=<dir>` (or `--trace <dir>` on `main.py`), the app writes `events.jsonl` (as the lab does) and `frames.jsonl` (each live frame's analysis values, plus the snapshot every N frames) for a real session. It also writes a screenshot every M seconds and on every take.
   - Off by default and cheap when off.
   - Agents and the user can then run the evals over a real session: `python -m tools.uilab trace <dir>`.
2. **Inspector:** Ctrl+Shift+D toggles a dockable panel. It shows:
   - the live frame values and the snapshot;
   - the input chain (capture rate, host API, noise floor, banner reasons);
   - running jobs with durations;
   - the last 200 events;
   - for the selected take: confidence and the reasons for each "Can't judge" or confidence cap.

   It is for "why does it say that?". It doesn't compute anything; it displays the core's dicts.

## 6. CLI
```
python -m ui.lab run [--scenarios a,b|all] [--sizes 1920x1080,1280x720] [--themes dark,light]
                     [--group live|takes|shell|practice] [--out DIR] [--takes C:\Users\nickb\VoiceLab]
python -m ui.lab baseline --group G
python -m tools.uilab diff RUN_A RUN_B --out DIR
python -m tools.uilab trace DIR
```
- `--group` runs the scenarios relevant to one owner (fast iteration).
- Default `--out` is `C:\tmp\phase11-work\lab-runs\<timestamp>`.
- Print the path of `index.html` and a scorecard summary.
- Each run must be deterministic: fixed seeds, `text_interval = 0`, reduce-motion on for captures (and a separate `--motion` flag that records a short frame sequence as a strip PNG, to check that the animations exist and settle).

## 7. Prove the evals work (eval the evals)
For each check in `tools/uilab/evals.py`, `tests/test_uilab.py` has a **seeded defect** that the check must catch, and a clean case it must pass:
- an elided label;
- two overlapping buttons;
- a 3:1 grey-on-grey label;
- a readout that disagrees with a card by 14 Hz;
- an unlabeled live-vs-take pair;
- a missing spinner while analysing;
- a "Nothing running" with a job;
- an "ΔF" label;
- a screen that scrolls at 1920×1080;
- a feature path that dead-ends.

Use tiny Qt fixtures in `ui/lab` test helpers. Report each check's catch rate on the seeded set (should be 100 %) and its false-positive count on the current app, with a sample of the false positives you suppressed and why.

## Deliverables
1. The lab, merged-ready on the branch, with `tests/test_uilab.py` passing and the full suite passing.
2. **A full baseline run of today's `main`** (all scenarios, both sizes, both themes) at `C:\tmp\phase11-work\01-lab\baseline-run\`. In the report:
   - the scorecard;
   - the top 30 findings by severity, with screenshot paths;
   - the truth metrics.
3. `tests/uilab_baseline/*.json` generated from that run.
4. `C:\tmp\phase11-work\01-lab\HOWTO.md`: one page for wave B agents, covering:
   - how to run your group;
   - how to read `index.html` and the `.marked.png` files;
   - how to ratchet;
   - how to add a scenario.

Keep it short and concrete.
