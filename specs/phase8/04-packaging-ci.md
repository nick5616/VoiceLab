# 04 Packaging + CI (wave 1)

## Goal

1. A Windows build that runs VoiceLab without Python installed: a folder with `VoiceLab.exe`, zipped.
2. A GitHub Actions workflow that runs the tests on every push and PR.

You may not edit any `.py` file. If the build needs a code change (for example, a resource path that breaks when frozen), list it under requests for integration.

## Deliverables

### Build

- `requirements-build.txt`: `pyinstaller`, pinned to the version you verified.
- `packaging/voicelab.spec`: a PyInstaller spec in **onedir** mode (onefile unpacks ~200 MB on each launch, which is too slow).
  - Entry point `main.py`, windowed (no console), name `VoiceLab`.
  - It must bundle what PyInstaller misses by default. Check each one; don't assume:
    - parselmouth's native library
    - sounddevice's PortAudio DLL (`_sounddevice_data`)
    - qtawesome's font files
    - pyqtgraph's lazily imported modules
    - pyworld
    - mido's backend modules, if imported dynamically
  - Exclude the isolation extras (torch, torchaudio, demucs), plus tkinter, matplotlib and unused Qt modules (QtWebEngine, Qt3D, QtMultimedia unless used), to keep the size down. `isolation.py` already handles a missing Demucs; confirm the frozen app shows the no-isolation path.
- `packaging/build.ps1`: installs `requirements-build.txt` into the given venv, runs PyInstaller with the spec, and zips `dist/VoiceLab/` into `dist/VoiceLab-windows.zip`. It takes the Python path as a parameter, defaulting to `.\venv\Scripts\python.exe`. Windows PowerShell 5.1 compatible (no `&&`, no ternary).
- Report the unzipped size and the zip size. Aim for under ~250 MB unzipped. If you can't reach that, say what dominates.

### Verify the build runs

Smoke-test the frozen exe:
1. Launch `dist\VoiceLab\VoiceLab.exe` with `VOICELAB_HOME` pointed at a temp folder, so the user's real profile isn't touched.
2. Wait ~10 s and confirm the process is still alive. With no console, also check for a crash dialog and look for a traceback file or Windows Event Log entry.
3. Kill the process.
4. Run a second check: a headless frozen run that imports every app module. Make it a separate tiny PyInstaller target in the same spec, `packaging/smoke_entry.py`, that imports `analysis`, `engine`, `main` and so on, analyses one synthetic tone with `analysis.analyze_take`, and prints the pitch. You own `packaging/`, so that file is allowed.

### CI: `.github/workflows/tests.yml`

- Triggers: push and pull_request on `main`.
- Job `test`: `windows-latest`, Python 3.12. Pip cache keyed on `requirements.txt`. Install `requirements.txt` and `pyflakes`. Run `python -m pyflakes *.py tests`, then `python -m pytest tests -q -k "not isolation"` with `QT_QPA_PLATFORM=offscreen`.
- Add an `ubuntu-latest` matrix entry too, if you can make it work. It needs `libportaudio2` and the Qt offscreen dependencies (`libegl1`, `libxkbcommon0`, `libgl1`, etc.). If sounddevice still can't load, drop Ubuntu and say why.
- Job `build` (Windows only, `needs: test`, only on push to `main`): run `packaging/build.ps1` and upload the zip with `actions/upload-artifact`.
- Validate the YAML locally: use `actionlint` if you can get it (it has standalone binaries), otherwise at least parse it with Python's `yaml`. You can't run the workflow from here, so don't push. Report what still needs verifying on GitHub.

### README

Add one section, `## Building a Windows app`, directly above `## Tests`: how to run `packaging/build.ps1`, where the output lands, and that Demucs isolation isn't included in the build. Match the README's voice: short, second person, no marketing. Don't edit any other section.

## Acceptance

- `packaging/build.ps1` produces a zip on this machine, the exe passes both smoke checks, and the workflow YAML validates.
- `build/` and `dist/` are already gitignored. Check that no build output gets committed.
