"""Fixtures shared by the app-level tests: a Qt application and the main window (the spec 05 shell, built as
main.py builds it, on a VoiceLabCore) with no mic or speakers."""
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
    from ui import theme
    monkeypatch.setenv("VOICELAB_REDUCE_MOTION", "1")
    # Motion off: tweens jump to their end. Windows from earlier tests are deleted between tests, and an
    # animation caught mid-flight by that deletion can crash the test process (never the app, whose
    # window lives until it quits). tests/test_ui_components.py covers the animations themselves.
    theme.set_reduce_motion(True)
    windows = []

    def make(takes_dir=None):
        profile = Profile(tmp_path / "home")
        if takes_dir:
            profile.set_takes_dir(takes_dir)
        window = main.build(LiveEngine(), profile)
        windows.append(window)
        if takes_dir:
            wait_for(lambda: not window.core.loading)
        return window

    yield make
    for window in windows:
        window.close()
    # Delete the windows here, in the event loop, rather than whenever the garbage collector gets to
    # them: collected mid-animation, a widget tree can vanish under a running tween.
    from PyQt6.QtCore import QCoreApplication, QEvent
    for window in windows:
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    QApplication.processEvents()
    theme.set_theme("dark")             # a test may have switched it; the theme is app-wide
    theme.set_reduce_motion(False)


# phase 11 / 01-ui-lab
@pytest.fixture
def lab_env(app):
    """The UI lab's process fakes (no mic, speakers, file pickers or blocking message boxes; motion off), put
    back afterwards so the tests that follow see the real ones."""
    from ui import theme
    from ui.lab import env
    env.setup()
    yield env
    env.restore()
    theme.set_theme("dark")
    theme.set_reduce_motion(False)
