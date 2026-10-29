"""The result of a practice, in plain words, with where to go next: a headline, a few rows (with "vs last time"),
one tip and buttons. One card for read aloud, phrases, vowel check, exercises and the Voice Map."""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ui import theme
from ui.components import Button, Caption, Card, Chip, Headline, Label, WrapRow


class ResultCard(Card):
    """show_result(data) draws {"title", "headline", "compare", "rows" [(name, value, change)], "chips"
    [(text, kind)], "tip", "lines" [str], "actions" [(key, label, variant)]}; a click emits `action(key)`."""
    action = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__("Your result", raised=True, parent=parent)
        self.headline = Headline("", level="heading")
        self.compare = Caption("", tone="secondary")
        self.grid_holder = QWidget()
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(theme.L)
        self.grid.setVerticalSpacing(theme.XS)
        self.chips = QWidget()
        self.chips_box = QVBoxLayout(self.chips)
        self.chips_box.setContentsMargins(0, 0, 0, 0)
        self.lines = Label("", role="body", tone="secondary", wrap=True)
        self.tip = Label("", role="body", wrap=True)
        self.buttons_holder = QWidget()
        self.buttons = QHBoxLayout(self.buttons_holder)
        self.buttons.setContentsMargins(0, 0, 0, 0)
        self.buttons.setSpacing(theme.S)
        for w in (self.headline, self.compare, self.grid_holder, self.chips, self.lines, self.tip,
                  self.buttons_holder):
            self.add(w)
        self.button_widgets = {}
        self.data = None
        self.hide()

    def clear(self):
        self.data = None
        self.hide()

    def show_result(self, data):
        self.data = data
        self.title_label.setText(data.get("title", "Your result"))
        self._sync_header()
        self.headline.setText(data["headline"])
        self.compare.setText(data.get("compare", ""))
        self.compare.setVisible(bool(data.get("compare")))
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for r, (name, value, change) in enumerate(data.get("rows", [])):
            self.grid.addWidget(Label(name, role="body", tone="secondary"), r, 0)
            self.grid.addWidget(Label(value, role="body"), r, 1)
            self.grid.addWidget(Label(change or "", role="body", tone="muted"), r, 2)
        self.grid.setColumnStretch(3, 1)
        self.grid_holder.setVisible(bool(data.get("rows")))
        while self.chips_box.count():
            item = self.chips_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if data.get("chips"):
            self.chips_box.addWidget(WrapRow([Chip(text, kind=kind, dot=True) for text, kind in data["chips"]]))
        self.chips.setVisible(bool(data.get("chips")))
        self.lines.setText("\n".join(data.get("lines", [])))
        self.lines.setVisible(bool(data.get("lines")))
        self.tip.setText(data.get("tip", ""))
        self.tip.setVisible(bool(data.get("tip")))
        while self.buttons.count():
            item = self.buttons.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.button_widgets = {}
        for key, label, variant in data.get("actions", []):
            button = Button(label, variant=variant)
            button.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            self.buttons.addWidget(button)
            self.button_widgets[key] = button
        self.buttons.addStretch(1)
        self.show()
