"""The UI boundary (phase 10 rule 7): only ui/ and the main.py entry point use the Qt libraries, and ui/
draws what the core decides rather than computing readings itself."""
import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_LIBRARIES = {"PyQt6", "pyqtgraph", "qtawesome"}
# Core modules that compute readings, verdicts or advice: ui/ asks the core instead of calling them.
COMPUTING_MODULES = {"analysis", "coach", "denoise", "egg", "isolation", "register", "shift", "take_register"}
# numpy statistics that turn measurements into readings (display scaling, such as an axis range from
# percentiles or nanmin/nanmax, is allowed).
READING_STATISTICS = {"median", "nanmedian", "mean", "nanmean", "std", "nanstd", "var", "nanvar", "average"}
PURE = ["app_core", "live_session", "engine", "jobs", "playback", "display", "events", "exercise", "speech",
        "voice_map", "mic_check", "progress", "take_view", "targets", "shift_session", "report"]


def imported_modules(path):
    """Top-level names of every module a file imports, directly or with `from`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def core_files():
    files = [p for p in ROOT.glob("*.py") if p.name != "main.py"]
    files += list((ROOT / "tools").rglob("*.py")) if (ROOT / "tools").is_dir() else []
    return files


def ui_files():
    return list((ROOT / "ui").rglob("*.py"))


def test_only_ui_and_main_import_the_ui_libraries():
    offenders = {p.relative_to(ROOT).as_posix(): sorted(imported_modules(p) & UI_LIBRARIES) for p in core_files()}
    assert {name: libs for name, libs in offenders.items() if libs} == {}
    assert len(core_files()) > 15


def test_ui_does_not_compute_readings():
    found = {}
    for path in ui_files():
        name = path.relative_to(ROOT).as_posix()
        computing = imported_modules(path) & COMPUTING_MODULES
        tree = ast.parse(path.read_text(encoding="utf-8"))
        statistics = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                      and isinstance(node.value, ast.Name) and node.value.id in ("np", "numpy")
                      and node.attr in READING_STATISTICS}
        if computing or statistics:
            found[name] = sorted(computing | statistics)
    assert found == {}


def test_the_pure_modules_load_without_qt():
    code = (f"import sys; import {', '.join(PURE)}; "
            "loaded = sorted(m for m in sys.modules if m.split('.')[0] in {'PyQt6', 'pyqtgraph', 'qtawesome'}); "
            "assert not loaded, loaded; print('ok')")
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr
