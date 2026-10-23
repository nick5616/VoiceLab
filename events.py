"""A tiny publish/subscribe hub. No UI: a UI passes a `dispatch` that runs handlers on its own thread."""
import threading


def call_now(fn, *args):
    """The default dispatch: run the handler immediately, on the emitting thread."""
    fn(*args)


class Events:
    """events.subscribe(name, fn) -> unsubscribe(); events.emit(name, *args) calls every handler of
    `name` through dispatch(fn, *args), which decides the thread it runs on."""

    def __init__(self, dispatch=None):
        self.dispatch = dispatch or call_now
        self._handlers = {}
        self._lock = threading.Lock()

    def subscribe(self, name, fn):
        """Call fn(*args) on every emit(name, *args); returns a function that unsubscribes it."""
        with self._lock:
            self._handlers.setdefault(name, []).append(fn)

        def unsubscribe():
            with self._lock:
                handlers = self._handlers.get(name, [])
                if fn in handlers:
                    handlers.remove(fn)
        return unsubscribe

    def emit(self, name, *args):
        with self._lock:
            handlers = list(self._handlers.get(name, []))
        for fn in handlers:
            self.dispatch(fn, *args)

    def call(self, fn, *args):
        """Run one function through the dispatch, like a handler (e.g. a job's on_done)."""
        self.dispatch(fn, *args)
