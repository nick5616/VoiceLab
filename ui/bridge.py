"""The Qt side of the core: handlers run on the Qt main thread, and engine frames arrive as a signal."""
from PyQt6.QtCore import QObject, pyqtSignal

from events import Events


class _Dispatcher(QObject):
    """Lives on the main thread: a call emitted from another thread is queued to it, one from the main
    thread runs at once."""
    call = pyqtSignal(object, object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run)

    @staticmethod
    def _run(fn, args):
        fn(*args)


_dispatcher = _Dispatcher()         # created on import, which happens on the main thread


def qt_dispatch(fn, *args):
    """Run fn(*args) on the Qt main thread (now, when already there)."""
    _dispatcher.call.emit(fn, args)


def qt_events():
    """An Events hub whose handlers run on the Qt main thread."""
    return Events(dispatch=qt_dispatch)


class EngineSignals(QObject):
    """frame_ready(frame) on the main thread for every live frame of `engine` (engine.subscribe)."""
    frame_ready = pyqtSignal(dict)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.unsubscribe = engine.subscribe(self.frame_ready.emit)
