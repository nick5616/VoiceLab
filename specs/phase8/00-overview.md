# Phase 8: parallel work plan

Phase 8 on the roadmap is the **register classifier** and **EGG hardware support**. Both need UI that would collide in `main.py`, so the work is split into:

- **Wave 1**: four parallel agents. Each owns its own files, so their branches merge without conflicts.
- **Wave 2**: one integration agent, after the four branches are merged.

| Spec                                    | Wave | Owns (may create or edit)                                                                                                | Must not touch                 |
| --------------------------------------- | ---- | ------------------------------------------------------------------------------------------------------------------------ | ------------------------------ |
| [01 Register core](01-register-core.md) | 1    | `register.py` (new), `tests/test_register.py` (new), `analysis.py` (only `analyze_take`'s returned `tracks`)             | everything else                |
| [02 EGG core](02-egg-core.md)           | 1    | `egg.py` (new), `tests/test_egg.py` (new), `engine.py`, `tests/test_engine.py` (new)                                     | everything else                |
| [03 Split main.py](03-split-main.md)    | 1    | `main.py`, new `live_panel.py`, `take_card.py`, `targets_dialog.py`, `ui_common.py`; import lines in `tests/test_app.py` | all other modules, test bodies |
| [04 Packaging + CI](04-packaging-ci.md) | 1    | `packaging/` (new), `.github/workflows/` (new), `requirements-build.txt` (new), one new README section                   | all `.py` files                |
| [05 Integration](05-integration.md)     | 2    | UI modules, `profile_store.py`, `progress.py`, `voice_map.py`, `README.md`, `tests/test_app.py`                          | n/a (runs alone)               |

## Rules for every agent

1. **Environment.** Windows 11. Use the main checkout's venv by absolute path: `C:\Users\nickb\repos\VoiceLab\venv\Scripts\python.exe`. Do not create another venv. Run tests with `python -m pytest tests -q -k "not isolation"` (isolation tests need PyTorch).
2. **Baseline first.** Run the full test suite before changing anything and note the result. When you finish, the whole suite must pass, not only your new tests.
3. **Stay inside your files.** If you need a change in a file you don't own, don't make it. Write it under "Requests for integration" in your final report.
4. **Match the code style.** Plain module-level functions, constants at the top of the file with a short trailing comment for units or rationale, one-line docstrings, no type annotations (the codebase has none), no new dependencies unless your spec allows one. Run `python -m pyflakes` on the files you touched and leave it clean (install pyflakes into the venv if it's missing).
5. **Tests use synthetic signals** with known ground truth, like `tests/test_analysis.py::synth_vowel`. Import its helpers; don't copy them. Never use a microphone or speakers.

6. **Final report.** Include: files changed, the public API you added (signatures), test results (counts plus any failures), design decisions and the sources behind each threshold you chose, known limitations, and requests for integration.

## Launching

Commit `specs/` first, so each worktree has these files. Then start agents 01–04 together with `isolation: "worktree"` and `model: "sonnet"`, each with the prompt:

> Implement `specs/phase8/0N-<name>.md`. Read `specs/phase8/00-overview.md` first; its rules apply.

When all four have reported, merge their branches into `main` in this order: 03, 01, 02, 04. 03 is a pure move, and the others don't touch the files it moves. Run the full suite after each merge. Then launch 05 on `main`, passing it the four final reports.
