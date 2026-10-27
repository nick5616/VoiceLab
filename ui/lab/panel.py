"""The in-app inspector (Ctrl+Shift+D): a dockable panel for "why does it say that?". It shows the core's own
dicts as they are: the live frame and snapshot, the input chain (capture path, noise floor, input badge, the
banner and why), the running jobs with how long they have run, the last events, and for the selected take its
confidence and the reason behind every "Can't judge" or capped confidence. It computes nothing."""
import json
import time
from collections import deque

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QDockWidget, QPlainTextEdit, QTabWidget

from tools.uilab.runio import jsonable, summarize
from ui import theme

SHORTCUT = "Ctrl+Shift+D"
REFRESH_MS = 250                # the panel redraws this often while it is open
EVENTS_KEPT = 200
TABS = ("Live", "Input", "Jobs", "Events", "Take")


def pretty(value):
    return json.dumps(jsonable(value), indent=1, ensure_ascii=False, default=str)


class InspectorPanel(QDockWidget):
    """The dock. refresh() redraws every tab from the core."""

    def __init__(self, window):
        super().__init__("Inspector", window)
        self.window = window
        self.core = window.core
        self.setObjectName("lab_inspector")
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea)
        self.tabs = QTabWidget()
        self.pages = {}
        for name in TABS:
            page = QPlainTextEdit()
            page.setReadOnly(True)
            page.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            page.setFont(theme.font("numeric", size=12))
            self.pages[name] = page
            self.tabs.addTab(page, name)
        self.setWidget(self.tabs)
        self.setMinimumWidth(380)
        self.frame = None
        self.events = deque(maxlen=EVENTS_KEPT)
        self.started = time.monotonic()
        emit = self.core.events.emit

        def logged(name, *args):
            self.events.append((round(time.monotonic() - self.started, 2), name, summarize(args)))
            emit(name, *args)
        self.core.events.emit = logged
        window.signals.frame_ready.connect(self._frame)
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)

    def _frame(self, frame):
        self.frame = frame

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        self.refresh()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()

    def refresh(self):
        core, w = self.core, self.window
        snap = core.live.snapshot()
        snap.pop("pitch_history", None)
        frame = {k: v for k, v in (self.frame or {}).items() if not hasattr(v, "__len__") or isinstance(v, str)}
        self._set("Live", {"frame": frame, "snapshot": snap})
        engine = core.engine
        self._set("Input", {"capture": getattr(engine, "capture_info", None),
                            "noise_floor_dbfs": engine.noise_floor() if engine is not None else None,
                            "input": snap.get("input"), "banner": snap.get("banner"),
                            "mic": core.mic_status(w.banner_info), "mic_available": core.mic_available,
                            "mic_problem": core.mic_problem, "last_mic_check": core.mic_check_text()})
        now = core.runner.clock()
        jobs = [dict(j, running_s=None if j.get("started") is None else round(now - j["started"], 1))
                for j in core.runner.jobs()]
        self._set("Jobs", {"jobs": jobs, "record_state": core.record_state(), "analyzing": core.analyzing,
                           "loading": core.loading, "takes_in_progress": core.takes_in_progress(),
                           "status": core.status})
        self.pages["Events"].setPlainText("\n".join(f"{t:8.2f}  {name}  {pretty(args)[:300]}"
                                                    for t, name, args in reversed(self.events)))
        self._set("Take", self.take_view())

    def take_view(self):
        """The selected take (Takes screen) or the newest: its quality, confidence and each dimension's reason."""
        from ui.takes import report_for
        uid = self.window.takes.selected or (self.core.takes()[0]["uid"] if self.core.takes() else None)
        data = self.core.take(uid) if uid else None
        if data is None:
            return {"take": None}
        rep = report_for(self.core, uid) or {}
        dims = [{k: d.get(k) for k in ("key", "name", "value", "display", "confidence", "reason", "headline")}
                for d in rep.get("dimensions", [])]
        return {"uid": uid, "title": data.get("title"), "quality": data.get("quality") or data.get("confidence"),
                "noise": data.get("noise"), "overall": rep.get("overall"), "banner": rep.get("banner"),
                "dimensions": dims}

    def _set(self, tab, value):
        page = self.pages[tab]
        bar = page.verticalScrollBar().value()
        page.setPlainText(pretty(value))
        page.verticalScrollBar().setValue(bar)


def install(window):
    """Ctrl+Shift+D toggles the inspector dock on the window (built the first time it opens)."""
    def toggle():
        panel = getattr(window, "lab_inspector", None)
        if panel is None:
            panel = window.lab_inspector = InspectorPanel(window)
            window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, panel)
            panel.show()
            return
        panel.setVisible(not panel.isVisible())
    shortcut = QShortcut(QKeySequence(SHORTCUT), window)
    shortcut.activated.connect(toggle)
    window.shortcuts["lab_inspector"] = shortcut
    return shortcut
