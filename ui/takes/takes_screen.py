"""The Takes screen: a searchable list of compact rows on the left (newest first, older ones loaded as you
scroll; the filters in one popover), and the selected take's full report on the right. A take you just
made is selected as soon as it is ready; a deleted one can be brought back from the toast."""
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLineEdit, QMenu, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
                             QWidgetAction)

import take_view
from ui import theme
from ui.components import Button, Caption, EmptyState, Headline, Label, Select, Skeleton
from ui.takes import listen
from ui.takes.recent_list import RecentRow
from ui.takes.take_card import TakeCard, clear_layout

LIST_WIDTH = (340, 440)         # px: the list column's narrowest and widest
LOAD_MARGIN = 240               # px from the bottom of the list at which older takes load
SEARCH_DELAY_MS = 150           # typing pause before the list filters
UNDO_TOAST_MS = 10000           # how long the "deleted · Undo" toast stays
PAGE_MARGIN = theme.XL          # px: the screen's left and top margin (as Progress)
FILTER_LABEL_WIDTH = 64         # px: the filter popover's labels


def _keyed(options):
    """take_view's (label, key) options as Select's (key, label)."""
    return [(key, label) for label, key in options]


class SearchBox(QLineEdit):
    """The search field, with a placeholder that meets contrast in both themes."""

    def __init__(self, placeholder):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setAccessibleName("Search takes")     # the placeholder disappears once you type (spec 05: every stop named)
        self.setClearButtonEnabled(True)
        theme.changed.connect(self.restyle)
        self.restyle()

    def restyle(self, *_):
        c = theme.current().colors
        self.setFont(theme.font("body"))
        self.setStyleSheet(f"""
QLineEdit {{ background: {c['surface_raised']}; color: {c['text']}; border: 1px solid {c['border_strong']};
    border-radius: {theme.RADIUS['medium']}px; padding: 5px {theme.S}px; placeholder-text-color: {c['text_secondary']}; }}
QLineEdit:focus {{ border-color: {c['accent']}; }}""")


class TakesScreen(QWidget):
    """Every take: the list (search, and a Filter popover for mode, task, quality and date) and the selected
    take's full card. show_take(uid) selects one. record_requested() / import_requested() from the empty
    state; mic_check_requested() from a card's trust line."""

    record_requested = pyqtSignal()
    import_requested = pyqtSignal()
    mic_check_requested = pyqtSignal()

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.cards = {}                 # uid -> row in the list
        self.new_uids = set()           # takes made this session that haven't been opened yet
        self.updating_uid = None
        self.last_undo = None           # (uid, name) of the take the last Undo toast offered back
        self.selected = None
        self.full = self.empty = None
        row = QHBoxLayout(self)
        row.setContentsMargins(PAGE_MARGIN, theme.L, theme.L, theme.M)
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
        self.search = SearchBox("Search by name, task or date")
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(SEARCH_DELAY_MS)
        self.search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _: self.search_timer.start())
        self.mode = Select(_keyed(take_view.MODES))
        self.task = Select([(None, "All tasks")])
        self.tier = Select(_keyed(take_view.TIERS))
        self.days = Select(_keyed(take_view.DAYS))
        for select in (self.mode, self.task, self.tier, self.days):
            select.changed.connect(self.refresh)
        self.filter_btn = Button(take_view.filter_label({}), "secondary", icon="ph.funnel", small=True)
        self.filter_btn.setToolTip("Narrow the list by mode, task, recording quality or date.")
        self.filter_menu = QMenu(self.filter_btn)
        self.filter_menu.addAction(self._filter_panel())
        self.filter_btn.setMenu(self.filter_menu)
        find = QHBoxLayout()
        find.setSpacing(theme.S)
        find.addWidget(self.search, 1)
        find.addWidget(self.filter_btn)
        side.addLayout(find)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.list_box = QVBoxLayout(inner)
        self.list_box.setContentsMargins(0, 0, theme.S, 0)
        self.list_box.setSpacing(theme.XS)
        self.list = QVBoxLayout()
        self.list.setSpacing(theme.XS)
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
        listen(self, core, {"take_added": self._added, "take_removed": self._removed, "take_ready": self._ready,
                            "takes_loaded": self._loaded, "take_updated": self._updated,
                            "take_trashed": self._trashed, "busy": self._busy, "profile_changed": self._rebuild_rows})
        self.refresh()

    def _filter_panel(self):
        """The popover's content as a menu action: a labelled drop-down for each filter, and Clear."""
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(theme.M, theme.M, theme.M, theme.M)
        box.setSpacing(theme.S)
        for text, select in (("Mode", self.mode), ("Task", self.task), ("Quality", self.tier), ("Date", self.days)):
            line = QHBoxLayout()
            label = Label(text, role="label", tone="secondary")
            label.setFixedWidth(FILTER_LABEL_WIDTH)
            select.setMinimumWidth(170)
            line.addWidget(label)
            line.addWidget(select, 1)
            box.addLayout(line)
        clear = Button("Clear filters", "ghost", small=True)
        clear.clicked.connect(self.clear_filters)
        box.addWidget(clear, 0, Qt.AlignmentFlag.AlignRight)
        action = QWidgetAction(self.filter_btn)
        action.setDefaultWidget(panel)
        return action

    # --- filtering ---
    def filters(self):
        return {"query": self.search.text(), "mode": self.mode.current_key(), "task": self.task.current_key(),
                "tier": self.tier.current_key(), "days": self.days.current_key()}

    def clear_filters(self):
        """Back to every take: the search emptied and each filter on its first option."""
        for widget in (self.search, self.mode, self.task, self.tier, self.days):
            widget.blockSignals(True)
        self.search.clear()
        for select in (self.mode, self.task, self.tier, self.days):
            select.setCurrentIndex(0)
        for widget in (self.search, self.mode, self.task, self.tier, self.days):
            widget.blockSignals(False)
        self.refresh()

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
        self.filter_btn.setText(take_view.filter_label(self.filters()))
        while self.list.count():
            self.list.takeAt(0)
        shown = {t.get("uid") for t in self.core.shown_takes()}
        for uid in [u for u in self.cards if u not in shown]:
            self.cards.pop(uid).deleteLater()
        for card in self.cards.values():
            card.hide()
        if self.core.loading and not self.core.shown_takes():
            for _ in range(4):
                self.list.addWidget(Skeleton("block", height=56))
            self._show_detail(None)
            self._sync_count([])
            return
        takes = self.visible_takes()
        for take in takes:
            uid = take.get("uid")
            card = self.cards.get(uid)
            if card is None:
                card = self.cards[uid] = RecentRow(self.core, take)
                card.clicked.connect(self.select)
            self.list.addWidget(card)
            card.show()
            card.set_selected(uid == self.selected)
            card.set_status(self._status_of(uid))
        if not takes and self.core.shown_takes():
            self.list.addWidget(Caption("No takes match. Clear the search or the filters to see them all.",
                                        tone="secondary"))
        self._sync_count(takes)
        if self.selected not in {t.get("uid") for t in takes}:
            self.select(takes[0].get("uid") if takes else None)

    def _status_of(self, uid):
        if uid == self.updating_uid:
            return take_view.UPDATING_SHORT
        return take_view.NEW_TEXT if uid in self.new_uids else ""

    def _sync_status(self):
        for uid, row in self.cards.items():
            row.set_status(self._status_of(uid))

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
            self.clear_filters()
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
        was_new = uid in self.new_uids          # a take just made: its first view says "New"
        self.new_uids.discard(uid)
        self.full = TakeCard(self.core, uid)
        self.full.set_new(was_new)
        self.full.mic_check_requested.connect(self.mic_check_requested.emit)
        self.detail_box.addWidget(self.full)
        self.detail_box.addStretch(1)
        self.detail.verticalScrollBar().setValue(0)
        self._sync_status()

    # --- core events ---
    def _added(self, data):
        self.refresh()

    def _ready(self, data):
        """A take you just made (or imported): select it, so the report you open is the new one."""
        uid = data.get("uid")
        self.new_uids.add(uid)
        self.show_take(uid)

    def _removed(self, uid):
        self.new_uids.discard(uid)
        if uid == self.selected:
            self.selected = None
        self.refresh()

    def _trashed(self, uid, name):
        """A saved take was deleted: offer it back for a while ("Take #7 deleted · Undo")."""
        self.last_undo = (uid, name)
        toasts = getattr(self.window(), "toasts", None)
        if toasts is not None:
            toasts.show_toast(f"{name} deleted", "info", action="Undo",
                              on_action=lambda: self.core.undo_delete(uid), timeout_ms=UNDO_TOAST_MS)

    def _busy(self, jobs):
        uid = take_view.reanalysing_uid(self.core, jobs)
        if uid != self.updating_uid:
            self.updating_uid = uid
            self._sync_status()

    def _loaded(self, *_):
        self.refresh()

    def _updated(self, data):
        """A take changed (renamed, re-analysed): draw its row again."""
        uid = data.get("uid")
        if uid in self.cards:
            self._drop(uid)
            self.refresh()

    def _rebuild_rows(self, *_):
        """The profile changed (goal, targets, baseline), so every row's report did too."""
        for uid in list(self.cards):
            self._drop(uid)
        self.refresh()

    def _drop(self, uid):
        row = self.cards.pop(uid)
        row.hide()
        row.deleteLater()
