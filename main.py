"""VoiceLab's entry point: the profile, the engine and the core, the Qt bridge and the window."""
import sys

import pyqtgraph as pg
import sounddevice as sd
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt, QTimer

from app_core import VoiceLabCore
from engine import LiveEngine, device_index
from profile_store import Profile
from ui.bridge import EngineSignals, qt_events
from ui.main_window import MainWindow


def build(engine, profile):
    """The core for `engine` and `profile`, with its events on the Qt main thread, and its window."""
    core = VoiceLabCore(profile, engine=engine, events=qt_events())
    return MainWindow(core, EngineSignals(engine))


def run():
    app = QApplication(sys.argv)
    # Dark throughout, so message boxes and other stock dialogs get light text.
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    pg.setConfigOptions(antialias=True)
    profile = Profile()
    engine = LiveEngine()
    window = build(engine, profile)
    window.show()
    try:
        engine.start(device_index(profile.input_device), **window.core.start_options())
    except sd.PortAudioError as e:
        # Still usable without a mic: imported files can be analysed and shifted.
        window.record_btn.setEnabled(False)
        window.status_label.setText("No microphone. Import audio still works.")
        QMessageBox.warning(window, "No microphone",
                            f"Couldn't open the microphone ({e}).\n"
                            "Connect one and pick it under Mic, or restart VoiceLab.")
    else:
        window.restore_egg()
        QTimer.singleShot(0, window.first_run_mic_check)
    QTimer.singleShot(0, window.ask_for_takes_folder)
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
