"""VoiceLab's entry point: the profile, the engine and the core, the Qt bridge, the theme and the window."""
import sys

import pyqtgraph as pg
import sounddevice as sd
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from app_core import VoiceLabCore
from engine import LiveEngine, device_index
from profile_store import Profile
from ui import theme
from ui.app_window import AppWindow
from ui.bridge import EngineSignals, qt_events


def build(engine, profile):
    """The core for `engine` and `profile`, with its events on the Qt main thread, and its window."""
    core = VoiceLabCore(profile, engine=engine, events=qt_events())
    return AppWindow(core, EngineSignals(engine))


def run():
    app = QApplication(sys.argv)
    theme.apply(app)
    pg.setConfigOptions(antialias=True)
    profile = Profile()
    engine = LiveEngine()
    window = build(engine, profile)
    window.show()
    try:
        engine.start(device_index(profile.input_device), **window.core.start_options())
    except sd.PortAudioError as e:
        # Still usable without a mic: imported files can be analysed and shifted.
        window.core.set_mic_available(False, f"{e}. Connect one and pick it in Settings")
    else:
        window.restore_egg()
    QTimer.singleShot(0, window.start_up)
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
