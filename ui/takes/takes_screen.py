"""The Takes screen: a searchable, filterable list of compact take cards on the left (newest first, older
ones loaded as you scroll), and the selected take's full report on the right."""
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

import take_view
from ui import theme
from ui.components import Button, Caption, EmptyState, Headline, Select, Skeleton, WrapRow
from ui.takes import listen
from ui.takes.take_card import TakeCard, clear_layout

LIST_WIDTH = (340, 440)         # px: the list column's narrowest and widest
LOAD_MARGIN = 240               # px from the bottom of the list at which older takes load
SEARCH_DELAY_MS = 150           # typing pause before the list filters


def _keyed(options):
    """take_view's (label, key) options as Select's (key, label)."""
    return [(key, label) for label, key in options]


class TakesScreen(QWidget):
    """Every take: the list (search, mode, task, quality and date filters) and the selected take's full
    card. show_take(uid) selects one. record_requested() / import_requested() from the empty state;
    mic_check_requested() from a card's banner."""

    record_requested = pyqtSignal()
    import_requested = pyqtSignal()
    mic_check_requested = pyqtSignal()

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.cards = {}                 # uid -> compact card in the list
        self.selected = None
        self.full = self.empty = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.XL)

        left = QWidget()
        left.setMinimumWidth(LIST_WIDTH[0])
        left.setMaximumWidth(LIST_WIDTH[1])
        side = QVBoxLayout(left)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(theme.M)
        self.heading = Headline("Takes", level="title")
        self.count = Caption("", tone="secondary")
        side.addWidget(self.heading)
        side.addWidget(self.count)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, task or date")
        self.search.setClearButtonEnabled(True)
        self.search.setFont(theme.font("body"))
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(SEARCH_DELAY_MS)
        self.search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _: self.search_timer.start())
        side.addWidget(self.search)
        self.mode = Select(_keyed(take_view.MODES))
        self.task = Select([(None, "All tasks")])
        self.tier = Select(_keyed(take_view.TIERS))
        self.days = Select(_keyed(take_view.DAYS))
        for select in (self.mode, self.task, self.tier, self.days):
            select.setMinimumWidth(120)
            select.changed.connect(self.refresh)
        side.addWidget(WrapRow([self.mode, self.task, self.tier, self.days], spacing=theme.S))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.list_box = QVBoxLayout(inner)
        self.list_box.setContentsMargins(0, 0, theme.S, 0)
        self.list_box.setSpacing(theme.S)
        self.list = QVBoxLayout()
        self.list.setSpacing(theme.S)
        self.list_box.addLayout(self.list)
        self.older_btn = Button("", "ghost", small=True)
        self.older_btn.clicked.connect(self.load_older)
        self.list_box.addWidget(self.older_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.list_box.addStretch(1)
        self.scroll.setWidget(inner)
        self.scroll.verticalScrollBar().valueChanged.connect(self._scrolled)
        side.addWidget(self.scroll, 1)
        row.addWidget(left)

        self.detail = QScrollArea()
        self.detail.setWidgetResizable(True)
        self.detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_body = QWidget()
        self.detail_box = QVBoxLayout(self.detail_body)
        self.detail_box.setContentsMargins(0, 0, theme.S, theme.L)
        self.detail.setWidget(self.detail_body)
        self.detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        row.addWidget(self.detail, 1)
        listen(self, core, {"take_added": self._added, "take_removed": self._removed,
                            "takes_loaded": self._loaded, "take_updated": self._updated})
        self.refresh()

    # --- filtering ---
    def filters(self):
        return {"query": self.search.text(), "mode": self.mode.current_key(), "task": self.task.current_key(),
                "tier": self.tier.current_key(), "days": self.days.current_key()}

    def visible_takes(self):
        """The shown takes (newest first) that pass the search and filters."""
        f = self.filters()
        return [t for t in self.core.shown_takes() if take_view.matches(t, **f)]

    def _fill_tasks(self):
        current = self.task.current_key()
        options = [(None, "All tasks")] + [(name, f"{name} ({n})") for name, n in
                                           take_view.task_options(self.core.shown_takes())]
        self.task.set_options(options, current)

    def refresh(self, *_):
        """Lay the list out again from the core's shown takes and the filters."""
        self._fill_tasks()
        while self.list.count():
            self.list.takeAt(0)
        shown = {t.get("uid") for t in self.core.shown_takes()}
        for uid in [u for u in self.cards if u not in shown]:
            self.cards.pop(uid).deleteLater()
        for card in self.cards.values():
            card.hide()
        if self.core.loading and not self.core.shown_takes():
            for _ in range(4):
                self.list.addWidget(Skeleton("block", height=120))
            self._show_detail(None)
            self._sync_count([])
            return
        takes = self.visible_takes()
        for take in takes:
            uid = take.get("uid")
            card = self.cards.get(uid)
            if card is None:
                card = self.cards[uid] = TakeCard(self.core, uid, compact=True)
                card.open_report.connect(self.select)
            self.list.addWidget(card)
            card.show()
            card.set_selected(uid == self.selected)
        if not takes and self.core.shown_takes():
            self.list.addWidget(Caption("No takes match. Clear the search or the filters to see them all.",
                                        tone="secondary"))
        self._sync_count(takes)
        if self.selected not in {t.get("uid") for t in takes}:
            self.select(takes[0].get("uid") if takes else None)

    def _sync_count(self, takes):
        total = len(self.core.takes())
        self.count.setText(f"{len(takes)} of {total} take{'s' if total != 1 else ''}" if total else "")
        older = self.core.older_count()
        self.older_btn.setText(self.core.older_text())
        self.older_btn.setVisible(older > 0)

    def load_older(self):
        if self.core.older_count() > 0:
            self.core.show_older()
            self.refresh()

    def _scrolled(self, value):
        bar = self.scroll.verticalScrollBar()
        if self.core.older_count() > 0 and bar.maximum() - value < LOAD_MARGIN:
            self.load_older()

    # --- selection ---
    def select(self, uid):
        """Show this take's full report on the right (None: nothing selected)."""
        if uid == self.selected and self.full is not None and not self.full.removed:
            return
        self.selected = uid
        for key, card in self.cards.items():
            card.set_selected(key == uid)
        self._show_detail(uid)

    def show_take(self, uid):
        """Select a take, loading older pages and clearing filters that hide it."""
        if self.core.take(uid) is None:
            return
        while uid not in {t.get("uid") for t in self.core.shown_takes()} and self.core.older_count() > 0:
            self.core.show_older()
        if uid not in {t.get("uid") for t in self.visible_takes()}:
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            for select in (self.mode, self.task, self.tier, self.days):
                select.blockSignals(True)
                select.setCurrentIndex(0)
                select.blockSignals(False)
        self.refresh()
        self.select(uid)
        card = self.cards.get(uid)
        if card is not None:
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(card, 0, theme.L))

    def _show_detail(self, uid):
        if self.full is not None and self.full.uid == uid and not self.full.removed:
            return
        clear_layout(self.detail_box)
        self.full = None
        if uid is None:
            if not self.core.takes() and not self.core.loading:
                empty = EmptyState("ph.tray", "No takes yet", action="Record a take",
                                   detail="Record a take or import an audio file, and its report appears here.")
                empty.action_clicked.connect(self.record_requested.emit)
                imp = Button("Import a file…", "ghost")
                imp.clicked.connect(self.import_requested.emit)
                self.detail_box.addWidget(empty)
                self.detail_box.addWidget(imp, 0, Qt.AlignmentFlag.AlignHCenter)
                self.empty = empty
            elif self.core.loading:
                self.detail_box.addWidget(Skeleton("text", lines=2))
                self.detail_box.addWidget(Skeleton("block", height=260))
            self.detail_box.addStretch(1)
            return
        self.full = TakeCard(self.core, uid)
        self.full.mic_check_requested.connect(self.mic_check_requested.emit)
        self.detail_box.addWidget(self.full)
        self.detail_box.addStretch(1)
        self.detail.verticalScrollBar().setValue(0)

    # --- core events ---
    def _added(self, data):
        self.refresh()

    def _removed(self, uid):
        if uid == self.selected:
            self.selected = None
        self.refresh()

    def _loaded(self, *_):
        self.refresh()

    def _updated(self, data):
        if data.get("uid") in self.cards and not take_view.matches(data, **self.filters()):
            self.refresh()
