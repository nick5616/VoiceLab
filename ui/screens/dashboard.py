"""The dashboard: everything on one screen. Live on the left (readout, graph, cards, phrase), the latest
take, recent takes and progress on the right. Built for 1920x1080; the right column narrows and
scrolls down to 1280x720."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from ui import theme
from ui.components import Button, Card
from ui.screens import LatestTakePanel, ProgressMini, RecentTakes
from ui.screens.live import LiveView

RIGHT_MIN, RIGHT_MAX = 360, 600     # px: the right column's width range


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
        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left.setWidget(self.live_card)
        row.addWidget(left, 3)

        right = QWidget()
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, theme.XS, 0)
        column.setSpacing(theme.L)
        self.latest_card = Card("Latest take")
        self.latest = LatestTakePanel(core)
        self.latest.open_report.connect(shell.open_report)
        self.latest_card.add(self.latest)
        self.recent_card = Card("Recent takes")
        all_takes = Button("All takes", variant="ghost", small=True, icon="ph.arrow-right")
        all_takes.clicked.connect(lambda: shell.go("takes"))
        self.recent_card.add_action(all_takes)
        self.recent = RecentTakes(core, limit=6)
        self.recent.open_take.connect(shell.open_report)
        self.recent_card.add(self.recent)
        self.progress_card = Card("Progress")
        open_progress = Button("Open", variant="ghost", small=True, icon="ph.arrow-right")
        open_progress.clicked.connect(lambda: shell.go("progress"))
        self.progress_card.add_action(open_progress)
        self.progress = ProgressMini(core)
        self.progress_card.add(self.progress)
        for card in (self.latest_card, self.recent_card, self.progress_card):
            column.addWidget(card)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(right)
        scroll.setMinimumWidth(RIGHT_MIN)
        scroll.setMaximumWidth(RIGHT_MAX)
        self.right = scroll
        row.addWidget(scroll, 2)

    def show_frame(self, frame, views):
        self.live.show_snapshot(frame, views["live"])
