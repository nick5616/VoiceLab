"""Small dialogs of the shell: the import sheet (one question sheet instead of message boxes) and the
keyboard-shortcut cheat-sheet (the "?" key)."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QVBoxLayout

import shell_state
from ui import theme
from ui.components import Button, Caption, Collapsible, Headline, Label, SegmentedControl


class _Sheet(QDialog):
    """A dialog on the surface colour with a title and a row of buttons."""

    def __init__(self, title, parent=None, width=520):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(theme.XL, theme.XL, theme.XL, theme.L)
        self.box.setSpacing(theme.M)
        self.box.addWidget(Headline(title, level="title"))

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), theme.color("surface"))

    def buttons(self, *buttons):
        row = QHBoxLayout()
        row.addStretch(1)
        for button in buttons:
            row.addWidget(button)
        self.box.addLayout(row)


class ImportSheet(_Sheet):
    """Import audio, in plain words: which file, voice only or with music, and (stereo files) whether one
    channel is a second sensor. The technical terms live under Details. `answers()` is (isolate, egg_channel)."""

    def __init__(self, filename, can_isolate=False, stereo=False, parent=None):
        super().__init__("Import audio", parent)
        self.name = Label(filename, role="heading")
        self.name.setAccessibleName(f"File: {filename}")
        self.box.addWidget(self.name)
        self.choices = {"voice": "voice", "channel": "mix"}
        self.voice = self.channel = None
        if can_isolate:
            self.box.addWidget(Label(shell_state.IMPORT_MUSIC_QUESTION, role="label", tone="secondary"))
            self.voice = SegmentedControl(shell_state.IMPORT_VOICE_CHOICES, current="voice")
            self.voice.changed.connect(lambda key: self.choices.update(voice=key))
            self.box.addWidget(self.voice, 0, Qt.AlignmentFlag.AlignLeft)
        if stereo:
            self.box.addWidget(Label(shell_state.IMPORT_CHANNEL_QUESTION, role="label", tone="secondary", wrap=True))
            self.channel = SegmentedControl(shell_state.IMPORT_CHANNEL_CHOICES, current="mix")
            self.channel.changed.connect(lambda key: self.choices.update(channel=key))
            self.box.addWidget(self.channel, 0, Qt.AlignmentFlag.AlignLeft)
        if can_isolate or stereo:
            details = Collapsible("Details")
            details.add(Caption(shell_state.IMPORT_DETAILS, tone="secondary"))
            self.box.addWidget(details)
        self.cancel_btn = Button("Cancel", variant="ghost")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = Button("Analyse", variant="primary", icon="ph.check")
        self.ok_btn.clicked.connect(self.accept)
        self.ok_btn.setDefault(True)
        self.buttons(self.cancel_btn, self.ok_btn)

    def answers(self):
        """(isolate, egg_channel) as answered."""
        return shell_state.import_options(self.choices)


class ShortcutSheet(_Sheet):
    """Every keyboard shortcut, in one list (the "?" key)."""

    def __init__(self, parent=None):
        super().__init__("Keyboard shortcuts", parent, width=480)
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.XL)
        grid.setVerticalSpacing(theme.S)
        for row, (keys, what) in enumerate(shell_state.SHORTCUTS):
            key = Label(keys, role="numeric")
            key.setAccessibleName(f"Shortcut {keys}")
            grid.addWidget(key, row, 0)
            grid.addWidget(Label(what, role="body", tone="secondary", wrap=True), row, 1)
        grid.setColumnStretch(1, 1)
        self.box.addLayout(grid)
        self.close_btn = Button("Close", variant="primary")
        self.close_btn.clicked.connect(self.accept)
        self.buttons(self.close_btn)
