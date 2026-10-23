"""Headless frozen-build check: import every app module, analyse a synthetic tone."""
import importlib
import os
import sys
import tempfile

os.environ.setdefault("VOICELAB_HOME", tempfile.mkdtemp(prefix="voicelab_smoke_"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REQUIRED = ["analysis", "app_core", "coach", "denoise", "display", "egg", "engine", "events", "exercise",
            "isolation", "jobs", "live_session", "main", "mic_check", "playback", "profile_store", "progress",
            "register", "shift", "shift_session", "speech", "take_register", "take_store", "take_view", "targets",
            "voice_map", "ui.bridge", "ui.exercise_view", "ui.live_panel", "ui.live_views", "ui.main_window",
            "ui.meters", "ui.mic_check_dialog", "ui.progress_dialog", "ui.shift_panel", "ui.take_card",
            "ui.targets_dialog", "ui.voice_map_view", "ui.vowel_check", "ui.widgets"]
OPTIONAL = []


def main():
    """Import modules, run analyze_take on a 220 Hz tone, print the pitch."""
    import numpy as np
    for name in REQUIRED:
        importlib.import_module(name)
    for name in OPTIONAL:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name != name:
                raise
    import analysis
    import isolation
    sr = analysis.SAMPLE_RATE
    t = np.arange(int(sr * 1.5)) / sr
    audio = sum(np.sin(2 * np.pi * 220 * k * t) / k for k in range(1, 8)) * 0.1
    result = analysis.analyze_take(audio.astype(np.float32), sr)
    if "error" in result:
        raise RuntimeError(result["error"])
    print("pitch: %.1f Hz" % result["pitch"])
    assert abs(result["pitch"] - 220) < 5, "pitch off"
    print("isolation available:", isolation.available())
    for name in ("mido", "pyworld", "parselmouth", "sounddevice", "qtawesome"):
        importlib.import_module(name)
    import qtawesome as qta
    from PyQt6.QtWidgets import QApplication
    app = QApplication([])
    assert not qta.icon("fa5s.play", color="white").pixmap(16, 16).isNull(), "qtawesome font missing"
    app.quit()
    print("SMOKE OK")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
