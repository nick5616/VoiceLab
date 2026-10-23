"""Fixtures shared by the app-level tests: a Qt application and a main window with no mic or speakers."""
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
import sounddevice as sd  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from engine import LiveEngine  # noqa: E402
from profile_store import Profile  # noqa: E402


def wait_for(condition, timeout=60):
    """Process Qt events until condition() is true; False on timeout."""
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        QApplication.processEvents()
        if condition():
            QApplication.processEvents()
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(app, tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(sd, "play", lambda *a, **k: None)
    monkeypatch.setattr(sd, "stop", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    windows = []

    def make(takes_dir=None):
        profile = Profile(tmp_path / "home")
        if takes_dir:
            profile.set_takes_dir(takes_dir)
        window = main.MainWindow(LiveEngine(), profile)
        windows.append(window)
        if takes_dir:
            wait_for(lambda: window.load_worker is None or not window.load_worker.isRunning())
        return window

    yield make
    for window in windows:
        window.close()
