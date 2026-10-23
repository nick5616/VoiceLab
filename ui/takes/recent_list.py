"""The dashboard's recent takes: one compact row per take (quality, name and date, where it lands
overall, its pitch), newest first. Click a row to open that take."""
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

import take_view
from ui import theme
from ui.components import Button, Caption, Card, Chip, ElidedLabel, Label, Skeleton, StatusDot
from ui.components.base import ThemedWidget, painter, rounded
from ui.takes import listen, report_for
from ui.takes.take_card import clear_layout

ROW_HEIGHT = 52                 # px: two lines of text and some air


def row_summary(rep):
    """(chip text, chip kind, value text) a recent row shows from a take's report."""
    if rep["mode"] == "sing":
        tuning = next((d for d in rep["dimensions"] if d["key"] == "tuning"), None)
        return "Sing", "neutral", tuning["display"] if tuning and tuning.get("value") is not None else ""
    zone = rep["overall"].get("zone")
    chip = theme.ZONE_SHORT.get(zone, "No placement") if zone else "No placement"
    pitch = next((d for d in rep["dimensions"] if d["key"] == "pitch"), None)
    return chip, zone or "neutral", pitch["display"] if pitch and pitch.get("value") is not None else "—"


class RecentRow(ThemedWidget):
    """One take in the recent list; clicked(uid)."""

    clicked = pyqtSignal(str)

    def __init__(self, core, take):
        super().__init__()
        self.uid = take.get("uid")
        self.hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(ROW_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        head = take_view.header(take, core.number(take))
        rep = report_for(core, self.uid)
        chip_text, kind, value = row_summary(rep)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.S, theme.XS, theme.M, theme.XS)
        row.setSpacing(theme.M)
        self.dot = StatusDot(head["dot"])
        self.dot.setToolTip(head["tier_text"])
        row.addWidget(self.dot)
        words = QVBoxLayout()
        words.setSpacing(0)
        self.title = ElidedLabel(head["title"] or head["placeholder"], role="label")
        self.subtitle = ElidedLabel(" · ".join(p for p in (head["date"], head["task"]) if p), role="caption",
                                    tone="muted")
        words.addWidget(self.title)
        words.addWidget(self.subtitle)
        row.addLayout(words, 1)
        self.chip = Chip(chip_text, kind=kind, dot=kind != "neutral")
        self.chip.setToolTip(rep["headline"])
        row.addWidget(self.chip)
        self.value = Label(value, role="numeric", tone="secondary")
        self.value.setFixedWidth(64)
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.value)
        self.setToolTip(rep["headline"])
        self.setAccessibleName(f"{head['title'] or head['placeholder']}, {head['subtitle']}: {rep['headline']}")

    def mouseReleaseEvent(self, event):
        if self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.uid)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.uid)
        else:
            super().keyPressEvent(event)

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def paintEvent(self, event):
        t = theme.current()
        if self.hover or self.hasFocus():
            rounded(painter(self), QRectF(self.rect()), theme.RADIUS["medium"],
                    fill=t.q("surface_raised") if self.hover else None,
                    border=t.q("accent") if self.hasFocus() else None)


class RecentTakes(Card):
    """The `limit` newest takes as compact rows. open_take(uid) on a click; show_all() from "All takes"."""

    open_take = pyqtSignal(str)
    show_all = pyqtSignal()

    def __init__(self, core, limit=6, parent=None):
        super().__init__("Recent takes", parent=parent)
        self.core, self.limit = core, limit
        self.all_btn = Button("All takes", "ghost", small=True)
        self.all_btn.clicked.connect(self.show_all.emit)
        self.add_action(self.all_btn)
        self.list = QWidget()
        self.box = QVBoxLayout(self.list)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(2)
        self.add(self.list)
        self.rows = []
        listen(self, core, {"take_added": self.refresh, "take_removed": self.refresh, "takes_loaded": self.refresh,
                            "take_updated": self._updated, "profile_changed": self.refresh})
        self.refresh()

    def _updated(self, data):
        if any(row.uid == data.get("uid") for row in self.rows):
            self.refresh()

    def refresh(self, *_):
        clear_layout(self.box)
        self.rows = []
        takes = self.core.takes()[:self.limit]
        if self.core.loading and not takes:
            for _ in range(3):
                self.box.addWidget(Skeleton("text", lines=2))
            return
        if not takes:
            self.box.addWidget(Caption("No takes yet. They'll be listed here as you record.", tone="secondary"))
            return
        for take in takes:
            row = RecentRow(self.core, take)
            row.clicked.connect(self.open_take.emit)
            self.box.addWidget(row)
            self.rows.append(row)
