# 03 Split main.py (wave 1)

## Goal

`main.py` is ~1400 lines and holds every widget. Split it into modules by responsibility, so spec 05 (and later work) can change the live panel or the take cards without touching the window. **This is a pure move: no behaviour, layout, text or styling change.**

## Target layout

| New module          | Moves into it                                                                                                                                                  |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ui_common.py`      | Shared display constants and helpers: `METRIC_DEFS`, `BAR_COLORS`, `ON_TARGET_COLOR`, `OFF_TARGET_COLOR`, `DISABLED_COLOR`, `TARGET_BAND_COLOR`, `BUTTON_STYLE`, `fix_text_height`, `bar_color`, `format_value`, `RangeBar`, `MetricBar`, and any constants they use |
| `live_panel.py`     | `LivePanel` and its constants (`LIVE_METERS`, `SMOOTHED_KEYS`, the window/throttle/badge constants, `METER_SMOOTHING`, `LIVE_VIEW_HEIGHT`, `METER_MIN_WIDTH` if only it uses it), plus `hiss_note` if only the panel and cards use it (otherwise put it in `ui_common.py`) |
| `take_card.py`      | `TakeCard`, `AnalysisGraph`, `NoteScoreChart`, `singing_summary`, `TAKE_BAR_KEYS`                                                                              |
| `targets_dialog.py` | `TargetsDialog`, `TARGET_EDIT`                                                                                                                                 |
| `main.py`           | `MainWindow`, `import_and_analyze`, `load_folder`, `take_date`, the layout constants, app startup (`if __name__ == "__main__"` and whatever it calls)           |

Adjust where a symbol is shared. The rule: a symbol lives in the lowest module that needs it, and imports only point downward: `main` → `live_panel` / `take_card` / `targets_dialog` → `ui_common`. No import cycles.

## Rules

- Move code verbatim. The only edits allowed are import lines and `module.` prefixes. If something seems worth fixing while you're in there, put it in your report and leave it.
- Don't touch `exercise.py`, `speech.py` or `voice_map.py`, even though they define their own copy of `BUTTON_STYLE`. Deduplicating that is a separate change.
- `tests/test_app.py` uses `main.MainWindow`, `main.WIDE_LAYOUT_WIDTH` and `main.TAKES_COLUMN_WIDTH`. Keep those in `main`. Change only the import lines of `tests/test_app.py`, and only if needed. No test body may change.
- Remove imports from `main.py` that are no longer used (pyflakes clean), and give each new module only the imports it uses.
- Put a one-line module docstring on each new file, in the style of `engine.py`.

## Verification

1. Run the full suite before and after. Same passes, same count.
2. Screenshot check: render the window offscreen at 1400×900 and at 820×1000, after loading one synthetic take. Do it before the split (on the original code) and after, and confirm the images match. A pixel diff with numpy is enough, allowing for anti-aliasing noise. `QT_QPA_PLATFORM=offscreen` plus `window.grab().save(...)` works, and `tests/test_app.py` shows how to drive the window headless. Keep the script in your scratchpad, not the repo.
3. `python main.py` starts and shows the window: launch it, wait 5 s, confirm the process is alive with no traceback, then close it.

## Acceptance

- `main.py` is under ~550 lines, and no new module is over ~600.
- The suite passes with an unchanged test count, the screenshots match, pyflakes is clean, there are no import cycles, and the app launches.
- The report includes a table of where each moved symbol now lives (spec 05 depends on it).
