"""The dashboard: everything on one screen. Live on the left (readout, graph, cards, phrase), the latest
take, recent takes and progress on the right. Built for 1920x1080; both columns scroll on their own
down to 1280x720."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from ui import theme
from ui.components import Button, Card
from ui.screens import LatestTakePanel, ProgressMini, RecentTakes
from ui.screens.live import LiveView

RIGHT_MIN, RIGHT_MAX = 380, 620     # px: the right column's width range


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


class DashboardScreen(QWidget):
    def __init__(self, core, shell):
        super().__init__()
        self.core = core
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.L, theme.L, theme.L, theme.M)
        row.setSpacing(theme.L)

        self.live_card = Card(padding=theme.L)
        self.live_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.live = LiveView(core)
        self.live_card.add(self.live, 1)
        row.addWidget(scrolling(self.live_card), 3)

        right = QWidget()
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, theme.XS, 0)
        column.setSpacing(theme.L)
        self.latest = LatestTakePanel(core)
        self.latest.open_report.connect(shell.open_report)
        self.latest.record_requested.connect(shell.start_recording)
        self.latest.mic_check_requested.connect(shell.mic_check)
        self.recent = RecentTakes(core, limit=6)
        self.recent.open_take.connect(shell.open_report)
        self.recent.show_all.connect(lambda: shell.go("takes"))
        self.progress = ProgressMini(core)
        open_progress = Button("Open", variant="ghost", small=True, icon="ph.arrow-right")
        open_progress.clicked.connect(lambda: shell.go("progress"))
        self.progress.add_action(open_progress)
        for card in (self.latest, self.recent, self.progress):
            column.addWidget(card)
        column.addStretch(1)
        self.right = scrolling(right, RIGHT_MIN, RIGHT_MAX)
        row.addWidget(self.right, 2)

    def show_frame(self, frame, views):
        self.live.show_snapshot(frame, views["live"])
