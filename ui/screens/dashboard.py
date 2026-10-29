"""The dashboard, "Today": what to do next, what the voice is doing right now, your last take, the recent takes
and one line of progress. The full live instrument (graph, cards, replay) is the Live screen's job (audit
A-012). Built for 1920x1080 without scrolling; at 1280x720 the lists shrink and each column scrolls on its own."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from live_session import next_step
from ui import theme
from ui.components import Button, Card, Label
from ui.live.strip import LiveStrip
from ui.screens import LatestTakePanel, ProgressMini, RecentTakes
from ui.takes import listen

try:                            # spec 06's session checklist; without it the dashboard has a plain next step
    from ui.practice.session_card import SessionCard
except ImportError:
    SessionCard = None

RIGHT_MIN, RIGHT_MAX = 380, 620     # px: the right column's width range
SHORT_BELOW = 760                   # px: a dashboard shorter than this shows fewer recent takes and no progress
RECENT_ROWS = {"tall": 6, "short": 3}


def scrolling(widget, min_width=None, max_width=None):
    """`widget` in a vertical scroll area without a frame."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(widget)
    if min_width:
        scroll.setMinimumWidth(min_width)
    if max_width:
        scroll.setMaximumWidth(max_width)
    return scroll


class NextStepCard(Card):
    """The plain "Next step" card (used when the practice screen has no session card): one sentence and one
    button. record_requested() / practice_requested(key) say which."""

    record_requested = pyqtSignal()
    practice_requested = pyqtSignal(str)

    def __init__(self, core, parent=None):
        super().__init__("Next step", parent=parent)
        self.core = core
        self.text = Label("", role="body", tone="secondary", wrap=True)
        self.add(self.text)
        self.button = Button("Start practising", variant="primary")
        self.button.clicked.connect(self._clicked)
        self.add(self.button)
        self.action = None
        listen(self, core, {event: lambda *_: self.refresh() for event in
                            ("take_added", "take_removed", "takes_loaded", "recording")})
        self.refresh()

    def refresh(self):
        step = next_step(len(self.core.takes()), self.core.profile.voice_goal, bool(self.core.is_recording))
        self.set_title(step["title"])
        self.text.setText(step["text"])
        self.action = step["action"]
        self.button.setVisible(self.action is not None)
        self.button.setText("Record" if self.action == "record" else "Start practising")

    def _clicked(self):
        if self.action == "record":
            self.record_requested.emit()
        elif self.action == "practice":
            self.practice_requested.emit("")


class DashboardScreen(QWidget):
    """Today. `live` is the Right now strip; `session` the next-step card; `latest`, `recent` and `progress`
    are spec 04's widgets, placed here."""

    practice_requested = pyqtSignal(str)

    def __init__(self, core, shell):
        super().__init__()
        self.core = core
        self.shell = shell
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.L, theme.L, theme.L, theme.M)
        row.setSpacing(theme.L)

        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, theme.XS, 0)
        column.setSpacing(theme.L)
        if SessionCard is not None:
            self.session = SessionCard(core)
            self.session.open_step.connect(self.open_practice)
        else:
            self.session = NextStepCard(core)
            self.session.record_requested.connect(shell.start_recording)
            self.session.practice_requested.connect(self.open_practice)
        self.recent = RecentTakes(core, limit=RECENT_ROWS["tall"])
        self.recent.open_take.connect(shell.open_report)
        self.recent.show_all.connect(lambda: shell.go("takes"))
        self.progress = ProgressMini(core)
        open_progress = Button("Open", variant="ghost", small=True, icon="ph.arrow-right")
        open_progress.clicked.connect(lambda: shell.go("progress"))
        self.progress.add_action(open_progress)
        for card in (self.session, self.recent, self.progress):
            column.addWidget(card)
        column.addStretch(1)
        self.left = scrolling(left)
        row.addWidget(self.left, 3)

        right = QWidget()
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, theme.XS, 0)
        column.setSpacing(theme.L)
        self.live = LiveStrip(core, lambda: shell.go("live"))
        try:
            self.latest = LatestTakePanel(core, compact=True)
        except TypeError:       # the panel before spec 04's compact form
            self.latest = LatestTakePanel(core)
        self.latest.open_report.connect(shell.open_report)
        self.latest.record_requested.connect(shell.start_recording)
        self.latest.mic_check_requested.connect(shell.mic_check)
        for card in (self.live, self.latest):
            column.addWidget(card)
        column.addStretch(1)
        self.right = scrolling(right, RIGHT_MIN, RIGHT_MAX)
        row.addWidget(self.right, 2)
        self.short = None
        self._fit()

    def open_practice(self, key):
        """A practice (or the practice screen when `key` is empty) from the session card."""
        self.practice_requested.emit(key)
        go = getattr(self.shell, "go_practice", None)
        if go is not None and key:
            go(key)
            return
        self.shell.go("practice")
        opener = getattr(getattr(self.shell, "practice", None), "open", None)
        if key and opener is not None:
            opener(key)

    def _fit(self):
        """A short window shows fewer recent takes and leaves progress to its own screen."""
        short = self.height() < SHORT_BELOW
        if short == self.short:
            return
        self.short = short
        self.recent.limit = RECENT_ROWS["short" if short else "tall"]
        self.recent.refresh()
        self.progress.setVisible(not short)
        self.live.set_compact(short)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def show_frame(self, frame, views):
        self.live.show_snapshot(frame, views["live"])
