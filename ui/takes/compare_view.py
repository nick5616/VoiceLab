"""Compare two takes (audit A-023): a picker for the other take (the previous one, the baseline or any), one
sentence on the main changes, a table of all six readings with both values and the difference in words, and
Play for the A/B replay. take_view.compare_rows and compare_summary decide; this draws them."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

import take_view
from ui import theme
from ui.components import Button, Caption, Card, IconButton, Label, Select
from ui.takes import report_for

PICKER_WIDTH = 220              # px: the "compare with" picker


class CompareView(Card):
    """This take against another. picked(uid) when the user chooses another take; closed() from the X;
    play_both() / play_other() for the A/B replay (the card plays them)."""

    picked = pyqtSignal(str)
    closed = pyqtSignal()
    play_both = pyqtSignal()
    play_other = pyqtSignal()

    def __init__(self, core, uid, other, choices, parent=None):
        super().__init__("Compare", padding=theme.L, parent=parent)
        self.core, self.uid, self.other = core, uid, other
        self.picker = Select([(t.get("uid"), label) for label, t in choices], current=other.get("uid"))
        self.picker.setMinimumWidth(PICKER_WIDTH)
        self.picker.changed.connect(lambda key: key and self.picked.emit(key))
        self.add_action(Label("with", role="caption", tone="muted"))
        self.add_action(self.picker)
        close = IconButton("ph.x", "Close the comparison", size=28)
        close.clicked.connect(self.closed.emit)
        self.add_action(close)
        mine, theirs = report_for(core, uid), report_for(core, other.get("uid"))
        self.rows = take_view.compare_rows(mine, theirs)
        self.sentence = Label(take_view.compare_summary(self.rows, take_view.take_name(other)), role="body", wrap=True)
        self.add(self.sentence)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(theme.XL)
        self.grid.setVerticalSpacing(theme.S)
        for col, text in enumerate(("Reading", "This take", take_view.take_name(other), "Change")):
            head = Caption(text, tone="muted", wrap=False)
            head.setAlignment(Qt.AlignmentFlag.AlignRight if col in (1, 2) else Qt.AlignmentFlag.AlignLeft)
            self.grid.addWidget(head, 0, col)
        for row, r in enumerate(self.rows, start=1):
            self.grid.addWidget(Label(r["name"], role="label"), row, 0)
            for col, key in ((1, "this"), (2, "other")):
                value = Label(r[key], role="numeric", tone="primary" if col == 1 else "secondary")
                value.setAlignment(Qt.AlignmentFlag.AlignRight)
                self.grid.addWidget(value, row, col)
            self.grid.addWidget(Label(r["change"], role="body", tone="muted" if r["same"] or not r["comparable"] else "primary"),
                                row, 3)
        self.grid.setColumnStretch(3, 1)
        holder = QWidget()
        holder.setLayout(self.grid)
        self.add(holder)
        buttons = QHBoxLayout()
        buttons.setSpacing(theme.S)
        self.both_btn = Button("Play this, then that", "secondary", icon="ph.play-fill", small=True)
        self.both_btn.setToolTip("Plays this take, a short gap, then the other take, with the other drawn dashed on the graph.")
        self.both_btn.clicked.connect(self.play_both.emit)
        self.other_btn = Button("Play the other take", "ghost", icon="ph.play", small=True)
        self.other_btn.clicked.connect(self.play_other.emit)
        buttons.addWidget(self.both_btn)
        buttons.addWidget(self.other_btn)
        buttons.addStretch(1)
        wrap = QVBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addLayout(buttons)
        holder2 = QWidget()
        holder2.setLayout(wrap)
        self.add(holder2)
