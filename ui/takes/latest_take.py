"""The dashboard's latest-take panel: the newest take's summary (headline, overall, the issue, the top
tips with their moments), a skeleton while a new take is analysed, and a kind empty state before the
first take."""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

import take_view
from ui import theme
from ui.components import Button, Caption, Card, EmptyState, Skeleton, StatusDot
from ui.components.text import ElidedLabel
from ui.takes import listen, report_for
from ui.takes.take_card import SummaryView, TakePlayback, clear_layout, hbox

LATEST_TIPS = 2                 # the panel shows the top tips; the full report has the rest
EMPTY_TEXT = "Your first take's report appears here"
EMPTY_DETAIL = "Record a few sentences in your everyday voice. There are no wrong answers: it's a starting point."


class LatestTakePanel(Card):
    """The newest take, summary first. open_report(uid) from "Open full report"; record_requested() from the
    empty state; mic_check_requested() from the issue banner."""

    open_report = pyqtSignal(str)
    record_requested = pyqtSignal()
    mic_check_requested = pyqtSignal()

    def __init__(self, core, parent=None):
        super().__init__("Latest take", parent=parent)
        self.core = core
        self.uid = None
        self.pending = take_view.new_take_pending(core.runner.jobs())
        self.playback = None
        self.playing_chip = None
        self.state = None
        self.content = QWidget()
        self.box = QVBoxLayout(self.content)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(theme.L)
        self.add(self.content)
        self.open_btn = Button("Open full report", "secondary", icon="ph.arrow-right")
        self.open_btn.clicked.connect(lambda: self.uid and self.open_report.emit(self.uid))
        self.add_action(self.open_btn)
        listen(self, core, {"take_added": self.refresh, "take_removed": self.refresh, "takes_loaded": self.refresh,
                            "take_updated": self._updated, "profile_changed": self.refresh, "busy": self._busy})
        self.refresh()

    def newest(self):
        takes = self.core.takes()
        return takes[0] if takes else None

    def _updated(self, data):
        if data.get("uid") == self.uid:
            self.refresh()

    def _busy(self, jobs):
        pending = take_view.new_take_pending(jobs)
        if pending != self.pending:
            self.pending = pending
            self.refresh()

    def refresh(self, *_):
        """Show the skeleton, the empty state or the newest take."""
        if self.playback is not None:
            self.playback.stop()
        clear_layout(self.box)
        self.summary = self.skeleton = self.empty = None
        newest = self.newest()
        self.uid = newest.get("uid") if newest else None
        self.open_btn.setVisible(self.uid is not None and not self.pending)
        if self.pending or (self.core.loading and newest is None):
            self.state = "loading"
            self.skeleton = QWidget()
            col = QVBoxLayout(self.skeleton)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(theme.M)
            col.addWidget(Skeleton("text", lines=2))
            col.addWidget(Skeleton("block", height=34))
            col.addWidget(Skeleton("text", lines=3))
            col.addWidget(Caption("Analysing your take…" if self.pending else "Loading your takes…", tone="secondary"))
            self.box.addWidget(self.skeleton)
            return
        if newest is None:
            self.state = "empty"
            self.empty = EmptyState("ph.microphone", EMPTY_TEXT, action="Record a take", detail=EMPTY_DETAIL)
            self.empty.action_clicked.connect(self.record_requested.emit)
            self.box.addWidget(self.empty)
            return
        self.state = "take"
        head = take_view.header(newest, self.core.number(newest))
        title = ElidedLabel(head["title"] or head["placeholder"], role="label")
        dot = StatusDot(head["dot"], head["tier_text"])
        dot.setToolTip(take_view.QUALITY_TOOLTIP)
        self.box.addLayout(hbox(title, dot, stretch_at=0, spacing=theme.M))
        self.box.addWidget(Caption(head["subtitle"], wrap=False))
        rep = report_for(self.core, self.uid)
        self.summary = SummaryView(rep, newest, max_tips=LATEST_TIPS, headline_level="heading", split=False)
        self.summary.moment.connect(self._play_moment)
        self.summary.mic_check.connect(self.mic_check_requested.emit)
        self.box.addWidget(self.summary)
        self.playback = TakePlayback(self.core, self.uid, self.summary)
        self.playback.stopped.connect(self._stopped)

    def _play_moment(self, start, end, chip):
        if self.playback.playing and self.playing_chip is chip:
            self.playback.stop()
            return
        self.playback.play_moment(start, end)
        self.playing_chip = chip
        chip.set_playing(True)

    def _stopped(self):
        if self.playing_chip is not None:
            try:
                self.playing_chip.set_playing(False)
            except RuntimeError:
                pass
            self.playing_chip = None
