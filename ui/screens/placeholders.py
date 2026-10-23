"""Stand-ins for spec 06's widgets (ui/takes/, ui/progress/) until that spec merges, with the same
constructor and signals (see ui/screens/__init__.py). They keep every take feature reachable: the full
take card here is the legacy ui/take_card.py card (rename, play, compare, baseline, target, register
labels, shift), the progress screen and targets editor embed the legacy dialogs. Delete this module once
spec 06 is in."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QMessageBox, QScrollArea, QVBoxLayout, QWidget

import progress
import report
import targets
import take_view
from display import take_date
from ui import theme
from ui.screens.listen import listen
from ui.components import (Button, Caption, Card, EmptyState, Headline, Label, Skeleton, Sparkline, StatusDot,
                           ZoneBar)

TIER_DOT = {"good": "ok", "fair": "warn", "poor": "error"}
MINI_WEEKS = 8                  # weeks the dashboard sparklines show


class _Listener:
    """Subscribe to core events for as long as the widget lives."""

    def listen(self, core, *pairs):
        listen(self, core.events, *pairs)


def _overall_bar():
    zones = [{"id": "masculine", "label": "typically masculine", "lo": 0.0, "hi": report.OVERALL_EDGES[0]},
             {"id": "overlap", "label": "overlap", "lo": report.OVERALL_EDGES[0], "hi": report.OVERALL_EDGES[1]},
             {"id": "feminine", "label": "typically feminine", "lo": report.OVERALL_EDGES[1], "hi": 1.0}]
    return ZoneBar((0.0, 1.0), zones, ticks=False)


def _legacy_card(core, data):
    from ui.take_card import TakeCard as LegacyCard

    def ask(text):
        return QMessageBox.question(None, "Delete take", text) == QMessageBox.StandardButton.Yes
    return LegacyCard(data, core.number(data), core.profile, core.set_baseline, core.set_target,
                      on_rename=lambda c, title: core.rename(c.data, title),
                      on_delete=lambda c: core.delete(c.data, confirm=ask),
                      audio=lambda: core.take_audio(data),
                      on_label=lambda c, label: core.label_register(c.data, label),
                      compare_options=lambda c: core.compare_options(c.data),
                      compare_audio=core.take_audio, player=core.player, runner=core.runner)


class TakeCard(QWidget, _Listener):
    """compact: a one-glance summary (date, task, quality, headline); full: the legacy take card."""

    open_report = pyqtSignal(str)

    def __init__(self, core, uid, compact=False, parent=None):
        super().__init__(parent)
        self.core, self.uid, self.compact = core, uid, compact
        self.data = core.take(uid)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        if compact:
            card = Card(take_date(self.data) or "Take", self.data.get("task"))
            self.headline = Label("", role="body", wrap=True)
            card.add(self.headline)
            box.addWidget(card)
            self._refresh()
            self.listen(core, ("take_updated", lambda t: t is self.data and self._refresh()))
        else:
            self.card = _legacy_card(core, self.data)
            box.addWidget(self.card)
            self.listen(core, ("take_updated", lambda t: t is self.data and self.card.take_updated()),
                        ("profile_changed", lambda: self.card.refresh_profile()))

    def _refresh(self):
        rep = self.core.report(self.data)
        self.headline.setText(rep["headline"] if rep else "")

    def mouseReleaseEvent(self, event):
        if self.compact and self.data.get("uid"):
            self.open_report.emit(self.data["uid"])


class TakesScreen(QWidget, _Listener):
    """Every shown take as a full (legacy) card, newest first, with "Show older takes"."""

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.cards = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.empty = EmptyState("ph.activity", "No takes yet",
                                detail="Record a take (Space) or import a recording (Ctrl+I); it appears here.")
        outer.addWidget(self.empty)
        self.content = QWidget()
        self.list = QVBoxLayout(self.content)
        self.list.setContentsMargins(theme.XL, theme.L, theme.XL, theme.L)
        self.list.setSpacing(theme.M)
        self.more_btn = Button("", variant="secondary")
        self.more_btn.clicked.connect(self.show_older_takes)
        self.list.addWidget(self.more_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.list.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll, 1)
        self.listen(core, ("take_added", self._added), ("take_removed", self._removed),
                    ("takes_loaded", lambda *_: self._reload()), ("take_updated", self._updated),
                    ("profile_changed", self._profile_changed))
        self._reload()

    def add_card(self, data):
        card = self._insert(data)
        self._sync()
        return card

    def card_for(self, take):
        return next((c for c in self.cards if c.data is take), None)

    def _insert(self, data, at_bottom=False):
        card = _legacy_card(self.core, data)
        self.cards.append(card)
        self.list.insertWidget(self.list.indexOf(self.more_btn) if at_bottom else 0, card)
        return card

    def _remove(self, card):
        self.cards.remove(card)
        card.dispose()
        card.setParent(None)
        card.deleteLater()

    def _reload(self):
        for card in list(self.cards):
            self._remove(card)
        for data in reversed(self.core.shown_takes()):
            self._insert(data)
        self._sync()

    def _added(self, take):
        self._insert(take)
        self._sync()

    def _removed(self, uid):
        for card in [c for c in self.cards if c.data.get("uid") == uid]:
            self._remove(card)
        self._sync()

    def _updated(self, take):
        card = self.card_for(take)
        if card is not None:
            card.take_updated()

    def _profile_changed(self):
        for card in self.cards:
            card.refresh_profile()

    def show_older_takes(self):
        for data in self.core.show_older():
            self._insert(data, at_bottom=True)
        self._sync()

    def _sync(self):
        self.more_btn.setText(self.core.older_text())
        self.more_btn.setVisible(bool(self.core.older_count()))
        self.empty.setVisible(not self.cards)
        self.scroll.setVisible(bool(self.cards))

    def show_take(self, uid):
        card = next((c for c in self.cards if c.data.get("uid") == uid), None)
        if card is not None:
            self.scroll.ensureWidgetVisible(card, 0, 0)


class LatestTakePanel(QWidget, _Listener):
    """The newest take: its headline, where it sits overall, the top tips and "Open full report"."""

    open_report = pyqtSignal(str)

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.uid = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.M)
        self.meta = Caption("")
        self.headline = Headline("", level="heading")
        self.bar = _overall_bar()
        self.overall = Label("", role="body", tone="secondary", wrap=True)
        self.tips = Label("", role="body", wrap=True)
        self.open_btn = Button("Open full report", variant="primary", icon="ph.article")
        self.open_btn.clicked.connect(lambda: self.uid and self.open_report.emit(self.uid))
        self.skeleton = Skeleton("text", lines=4)
        self.empty = EmptyState("ph.microphone", "Your first take's summary appears here",
                                detail="Press Record (or Space), say a few sentences, then stop.")
        for w in (self.meta, self.headline, self.bar, self.overall, self.tips, self.skeleton, self.empty):
            box.addWidget(w)
        box.addWidget(self.open_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.listen(core, ("take_added", lambda *_: self.refresh()), ("take_removed", lambda *_: self.refresh()),
                    ("takes_loaded", lambda *_: self.refresh()), ("take_updated", lambda *_: self.refresh()),
                    ("profile_changed", self.refresh), ("busy", lambda *_: self._busy()))
        self.refresh()

    def _busy(self):
        self.skeleton.setVisible(self.core.analyzing)

    def refresh(self):
        takes = self.core.takes()
        data = takes[0] if takes else None
        self.uid = data.get("uid") if data else None
        rep = self.core.report(data) if data else None
        for w in (self.meta, self.headline, self.bar, self.overall, self.tips, self.open_btn):
            w.setVisible(rep is not None)
        self.empty.setVisible(rep is None and not self.core.analyzing)
        self.skeleton.setVisible(self.core.analyzing)
        if rep is None:
            return
        self.meta.setText(" · ".join(x for x in (data.get("title"), take_date(data), data.get("task")) if x))
        self.headline.setText(rep["headline"])
        overall = rep["overall"]
        self.bar.setVisible(overall["position"] is not None)
        self.bar.set_value(overall["position"])
        self.bar.set_range(overall["range"])
        self.overall.setText(overall["text"])
        tips = [t["text"].split(". ")[0].rstrip(".") + "." for t in rep["tips"][:2]]
        self.tips.setText("\n".join("•  " + t for t in tips) if tips else "")
        self.tips.setVisible(bool(tips))


class RecentTakes(QWidget, _Listener):
    """The last few takes as one-line rows; click one to open it."""

    open_take = pyqtSignal(str)

    def __init__(self, core, limit=6, parent=None):
        super().__init__(parent)
        self.core, self.limit = core, limit
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(theme.XS)
        self.rows = []
        self.listen(core, ("take_added", lambda *_: self.refresh()), ("take_removed", lambda *_: self.refresh()),
                    ("takes_loaded", lambda *_: self.refresh()), ("take_updated", lambda *_: self.refresh()))
        self.refresh()

    def refresh(self):
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        takes = self.core.takes()[:self.limit]
        if not takes:
            row = Caption("No takes yet.")
            self.box.addWidget(row)
            self.rows.append(row)
        for data in takes:
            row = _Row(data)
            row.clicked.connect(self.open_take.emit)
            self.box.addWidget(row)
            self.rows.append(row)


class _Row(QWidget):
    clicked = pyqtSignal(str)

    def __init__(self, data):
        super().__init__()
        self.uid = data.get("uid") or ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.XS, theme.XS, theme.XS, theme.XS)
        row.setSpacing(theme.S)
        tier = take_view.quality(data)["tier"]
        row.addWidget(StatusDot(TIER_DOT[tier]))
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(Label(data.get("title") or take_date(data) or "Take", role="label"))
        words.addWidget(Caption(" · ".join(x for x in (data.get("task"), take_view.TIER_TEXT[tier]) if x)))
        row.addLayout(words, 1)
        if data.get("pitch"):
            row.addWidget(Label(f"{data['pitch']:.0f} Hz", role="numeric", tone="secondary"))

    def mouseReleaseEvent(self, event):
        self.clicked.emit(self.uid)


class ProgressScreen(QWidget, _Listener):
    """The legacy progress dialog's charts and filters, as a page."""

    def __init__(self, core, parent=None):
        super().__init__(parent)
        from ui.progress_dialog import ProgressDialog
        self.core = core
        box = QVBoxLayout(self)
        box.setContentsMargins(theme.L, theme.L, theme.L, theme.L)
        self.dialog = ProgressDialog(core.profile, core.progress_entries, self)
        self.dialog.setWindowFlags(Qt.WindowType.Widget)
        box.addWidget(self.dialog)
        self.listen(core, ("progress_changed", lambda: self.isVisible() and self.dialog.refresh()))

    def showEvent(self, event):
        self.dialog.refresh()
        super().showEvent(event)


class ProgressMini(QWidget, _Listener):
    """Sparklines of the weekly median pitch and resonance over the last weeks."""

    SERIES = (("pitch", "Pitch", "Hz"), ("delta_f", "Brightness", "Hz"))

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.S)
        self.lines = {}
        for key, name, unit in self.SERIES:
            top = QHBoxLayout()
            title = Label(name, role="label", tone="secondary")
            value = Label("—", role="numeric")
            top.addWidget(title)
            top.addStretch(1)
            top.addWidget(value)
            box.addLayout(top)
            spark = Sparkline()
            box.addWidget(spark)
            self.lines[key] = (value, spark, unit)
        self.note = Caption("")
        box.addWidget(self.note)
        self.listen(core, ("progress_changed", self.refresh), ("profile_changed", self.refresh))
        self.refresh()

    def refresh(self):
        entries = self.core.progress_entries()
        counts = []
        for key, (value, spark, unit) in self.lines.items():
            times, values = progress.select(entries, key, mode="speak", days=7 * MINI_WEEKS)
            if len(values):
                _, medians = progress.weekly_medians(times, values)
                spark.set_values(list(medians), self.core.profile.target(key))
                value.setText(f"{medians[-1]:.0f} {unit}")
            else:
                spark.set_values([])
                value.setText("—")
            counts.append(len(values))
        n = max(counts) if counts else 0
        self.note.setText(f"Weekly medians of your speech takes, last {MINI_WEEKS} weeks · {n} take"
                          f"{'s' if n != 1 else ''}" if n else "Record a few takes to see a trend here.")


class TargetsEditor(QWidget):
    """The legacy targets dialog as a panel; apply() saves."""

    def __init__(self, core, parent=None):
        super().__init__(parent)
        from ui.targets_dialog import TargetsDialog
        self.core = core
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        self.dialog = TargetsDialog(core.profile, self)
        self.dialog.setWindowFlags(Qt.WindowType.Widget)
        from PyQt6.QtWidgets import QDialogButtonBox
        for buttons in self.dialog.findChildren(QDialogButtonBox):
            buttons.hide()
        box.addWidget(self.dialog)
        self.apply_btn = Button("Save targets", variant="primary")
        self.apply_btn.clicked.connect(self.apply)
        box.addWidget(self.apply_btn, 0, Qt.AlignmentFlag.AlignLeft)

    def apply(self):
        rows = self.dialog.rows
        targets.save(self.core.profile, {key: (spins[0].value(), spins[1].value()) if check.isChecked() else None
                                         for key, (check, spins, _) in rows.items()})
        self.core.refresh_profile("Targets saved")

