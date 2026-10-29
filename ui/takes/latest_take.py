"""The dashboard's last-take panel: titled "Your last take" with when it was made ("3 min ago", or the day
and "3 days ago"), so it can't be mistaken for the live readings; the newest take's headline, overall
placement and the top tip with its moment. A skeleton while a new take is analysed, and a kind empty state
before the first take. LatestTakePanel(core, compact=True) is the dashboard's version: at most
COMPACT_MAX_HEIGHT tall, one tip, and a problem only when there is one."""
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

import take_view
from ui import theme
from ui.components import Button, Caption, Card, EmptyState, Skeleton
from ui.takes import listen, report_for
from ui.takes.take_card import SummaryView, TakePlayback, clear_layout

LATEST_TIPS = 2                 # the full panel shows the top tips; the full report has the rest
COMPACT_TIPS = 1                # the compact panel shows the top one
COMPACT_MAX_HEIGHT = 320        # px (audit A-026)
HFW_SLACK = 24                  # px: the card's own height-for-width comes out this short in a scroll column
TITLE_REFRESH_MS = 30000        # "3 min ago" is refreshed this often
EMPTY_TEXT = "Your first take's report appears here"
EMPTY_DETAIL = "Record a few sentences in your everyday voice. There are no wrong answers: it's a starting point."


class LatestTakePanel(Card):
    """The newest take, answer first. open_report(uid) from "Open report"; record_requested() from the
    empty state; mic_check_requested() from the trust line's action."""

    open_report = pyqtSignal(str)
    record_requested = pyqtSignal()
    mic_check_requested = pyqtSignal()

    def __init__(self, core, compact=False, parent=None):
        super().__init__(take_view.LAST_TAKE_TITLE, parent=parent, padding=theme.M if compact else theme.L)
        self.core, self.compact = core, compact
        self.uid = None
        self.pending = take_view.new_take_pending(core.runner.jobs())
        self.playback = None
        self.playing_chip = None
        self.state = None
        self.content = QWidget()
        self.box = QVBoxLayout(self.content)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(theme.M if compact else theme.L)
        self.add(self.content)
        self.open_btn = Button("Open report" if compact else "Open full report", "secondary", icon="ph.arrow-right",
                               small=compact)
        self.open_btn.clicked.connect(lambda: self.uid and self.open_report.emit(self.uid))
        self.add_action(self.open_btn)
        if compact:
            self.setMaximumHeight(COMPACT_MAX_HEIGHT)
        listen(self, core, {"take_added": self.refresh, "take_removed": self.refresh, "takes_loaded": self.refresh,
                            "take_updated": self._updated, "profile_changed": self.refresh, "busy": self._busy})
        self.clock = QTimer(self)
        self.clock.setInterval(TITLE_REFRESH_MS)
        self.clock.timeout.connect(self._retitle)
        self.clock.start()
        self.refresh()

    def resizeEvent(self, event):
        """Never shorter than its wrapped text needs: a host column that sizes by minimumSize would squash it."""
        super().resizeEvent(event)
        if self.compact:
            return
        need = self.heightForWidth(self.width()) + HFW_SLACK
        if need > 0 and need != self.minimumHeight():
            self.setMinimumHeight(need)

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

    def _retitle(self):
        newest = self.newest()
        if newest is not None and self.state == "take":
            self.set_title(take_view.last_take_title(newest))

    def refresh(self, *_):
        """Show the skeleton, the empty state or the newest take."""
        if self.playback is not None:
            self.playback.stop()
        clear_layout(self.box)
        self.summary = self.skeleton = self.empty = None
        newest = self.newest()
        self.uid = newest.get("uid") if newest else None
        self.open_btn.setVisible(self.uid is not None and not self.pending)
        self.set_title(take_view.LAST_TAKE_TITLE)
        self.set_subtitle("")
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
        self.set_title(take_view.last_take_title(newest))
        self.set_subtitle(" · ".join(p for p in (head["title"] or head["placeholder"], head["task"]) if p))
        rep = report_for(self.core, self.uid)
        self.summary = SummaryView(rep, newest, max_tips=COMPACT_TIPS if self.compact else LATEST_TIPS,
                                   headline_level="heading", split=False, compact=self.compact)
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
