"""A day of Today's practice in History: its row in the list and its report (each step, how it compared with
the last session, what to try next, and the takes it was made of). session_report decides; this draws."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

import session_report
from ui import theme
from ui.components import Button, Caption, Card, Chip, ElidedLabel, Headline, Label
from ui.components.base import ThemedWidget
from ui.takes.recent_list import ROW_HEIGHT, RecentRow

MAX_TAKE_LINKS = 6              # take buttons a section shows before "and N more"
STATE_KINDS = {"Done": "success", "Skipped": "neutral", "Not done": "warning"}


class SessionRow(RecentRow):
    """A practice session in the History list; clicked(key). It shares RecentRow's look and selection."""

    def __init__(self, core, session):
        ThemedWidget.__init__(self)     # RecentRow's own setup is for a take; this is a session
        self.uid = session["key"]
        self.hover = False
        self.selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(ROW_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        view = session_report.row_view(session, core.takes())
        self.view = view
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.S, theme.XS, theme.M, theme.XS)
        row.setSpacing(theme.M)
        icon = Chip("", kind="accent", icon="ph.barbell")
        icon.setFixedWidth(26)
        row.addWidget(icon)
        words = QVBoxLayout()
        words.setSpacing(0)
        self.title = ElidedLabel(view["title"], role="label")
        self.subtitle = ElidedLabel(view["subtitle"], role="caption", tone="muted")
        words.addWidget(self.title)
        words.addWidget(self.subtitle)
        row.addLayout(words, 1)
        self.status = Chip("", kind="accent", dot=True)
        self.status.hide()
        row.addWidget(self.status)
        self.chip = Chip(view["chip"], kind=view["kind"], dot=True)
        row.addWidget(self.chip)
        self.setToolTip(view["tooltip"])
        self.setAccessibleName(f"{view['title']}, {view['subtitle']}: {view['chip']}")

    def set_status(self, text):
        self.status.setVisible(bool(text))
        if text:
            self.status.set_text(text, kind="accent")


class SessionReportCard(Card):
    """The report on one day's routine; open_take(uid) when one of its takes is clicked."""

    open_take = pyqtSignal(str)

    def __init__(self, core, key, parent=None):
        super().__init__("", parent=parent)
        self.uid = key                  # History treats it like a take's card
        self.removed = False
        rep = core.session_report(key)
        self.report = rep
        if rep is None:
            self.add(Label("This practice session is no longer available.", role="body", tone="secondary"))
            return
        self.title_label.setText(f"{rep['title']} · {rep['when']}")
        self._sync_header()
        self.add(Headline(rep["headline"], level="title"))
        meta = [f"about {rep['minutes']} min" if rep["minutes"] else "", f"{rep['steps_done']} of {rep['steps']} steps"]
        self.add(Caption(" · ".join(m for m in meta if m).capitalize(), tone="secondary"))
        for section in rep["sections"]:
            self.add(self._section(core, section))
        tip = Card("Next time")
        tip.add(Label(rep["next_time"], role="body", wrap=True))
        self.add(tip)

    def _section(self, core, section):
        box = Card(section["title"])
        box.add_action(Chip(section["state"], kind=STATE_KINDS.get(section["state"], "neutral"), dot=True))
        box.add(Label(section["headline"], role="heading", wrap=True))
        if section["rows"]:
            holder = QWidget()
            grid = QGridLayout(holder)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(theme.L)
            grid.setVerticalSpacing(theme.XS)
            for r, (name, value, change) in enumerate(section["rows"]):
                grid.addWidget(Label(name, role="body", tone="secondary"), r, 0)
                grid.addWidget(Label(value, role="body"), r, 1)
                grid.addWidget(Label(change or "", role="body", tone="muted"), r, 2)
            grid.setColumnStretch(3, 1)
            box.add(holder)
        if section["uids"]:
            links = QWidget()
            row = QHBoxLayout(links)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(theme.XS)
            for uid in section["uids"][:MAX_TAKE_LINKS]:
                take = core.take(uid)
                number = core.number(take) if take else None
                button = Button(f"Take #{number}" if number else "Open take", "ghost", small=True)
                button.clicked.connect(lambda _=False, u=uid: self.open_take.emit(u))
                row.addWidget(button)
            extra = len(section["uids"]) - MAX_TAKE_LINKS
            if extra > 0:
                row.addWidget(Caption(f"and {extra} more in the list", tone="muted"))
            row.addStretch(1)
            box.add(links)
        return box
