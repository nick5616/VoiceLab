"""The Practice screen: a card per practice (read aloud, phrases, vowel check, exercises, Voice Map).
Opening one shows it focused, with its own Start / Stop and progress; "All practice" goes back."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QHBoxLayout, QPlainTextEdit, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout,
                             QWidget)

from speech import CUSTOM_PASSAGE_HINT, CUSTOM_PASSAGE_NAME
from ui import theme
from ui.components import Button, Caption, Card, Chip, Collapsible, FlowGrid, Headline, IconButton, Label, Select
from ui.exercise_view import ExerciseView
from ui.live_graph import LivePitchGraph
from ui.takes import listen
from ui.voice_map_view import VoiceMapView
from ui.vowel_check import VowelCheck

# key: (title, one line, mode it needs or None, icon)
PRACTICES = [
    ("read", "Read aloud", "Read a passage in your practice voice; the same passage each time makes takes "
     "easy to compare.", "speak", "ph.book-open"),
    ("phrases", "Phrases", "Everyday things to say, one at a time: an order, a question, a hello.",
     "speak", "ph.chat-circle-text"),
    ("vowels", "Vowel check", "Eight short words that measure your resonance fairly and tune the analysis "
     "to your voice.", "speak", "ph.activity"),
    ("exercise", "Exercises", "Scales, arpeggios, long tones or your own MIDI melody, scored note by note.",
     "sing", "ph.music-notes"),
    ("voicemap", "Voice Map", "Your range and where in it your voice is clearest, built up as you sing.",
     "sing", "ph.map-trifold"),
]
MODE_WORDS = {"speak": "Speak", "sing": "Sing"}
READING_FONT_PX = 22            # px: the passage, big enough to read from a step back


class ReadingText(Label):
    """A passage in a book face, big enough to read from a step back."""

    def __init__(self):
        super().__init__("", role="body", wrap=True)

    def restyle(self, *_):
        super().restyle()
        font = QFont()
        font.setFamilies(["Georgia", "Cambria", "Segoe UI"])
        font.setPixelSize(READING_FONT_PX)
        self.setFont(font)


class _RecordingPanel(QWidget):
    """Shared by Read aloud and Phrases: a Start / Stop that records a take, and a small live graph."""

    def __init__(self, core, start_text):
        super().__init__()
        self.core = core
        self.start_text = start_text
        self.start_btn = Button(start_text, variant="primary", icon="ph.record-fill")
        self.start_btn.clicked.connect(self.toggle)
        self.graph = LivePitchGraph("speak", compact=True)
        self.graph.setMinimumHeight(160)
        self.graph.setMaximumHeight(240)
        listen(self, core, {"recording": self._recording,
                            "controls_changed": lambda c: self._recording(c["recording"]),
                            "profile_changed": self._reference})
        self._reference()

    def _reference(self):
        p = self.core.profile
        self.graph.set_reference(p.baseline_value("pitch"), p.target("pitch"))

    def toggle(self):
        if self.core.is_recording:
            self.core.stop_recording()
        elif self.core.controls()["record"]:
            self.before_recording()
            self.core.start_recording()

    def before_recording(self):
        """Tell the core what this take is for."""

    def _recording(self, on):
        self.start_btn.setText("Stop" if on else self.start_text)
        self.start_btn.set_icon("ph.stop-fill" if on else "ph.record-fill")
        self.start_btn.set_variant("secondary" if on else "primary")

    def show_frame(self, frame, views):
        self.graph.show_snapshot(views["live"])

    def opened(self):
        """The panel became the focused practice."""

    def closed(self):
        """The panel was left."""


class ReadAlouds(_RecordingPanel):
    """A passage to read while recording; your own passage can be pasted and saved."""

    def __init__(self, core):
        super().__init__(core, "Start reading")
        self.index = 0
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.L)
        top = QHBoxLayout()
        top.setSpacing(theme.S)
        self.picker = Select()
        self.picker.setMinimumWidth(260)
        self.picker.activated.connect(lambda i: self.show_passage(i))
        prev_btn = IconButton("ph.caret-left", "Previous passage", variant="secondary")
        next_btn = IconButton("ph.caret-right", "Next passage", variant="secondary")
        prev_btn.clicked.connect(lambda: self.show_passage(self.index - 1))
        next_btn.clicked.connect(lambda: self.show_passage(self.index + 1))
        self.counter = Caption("")
        top.addWidget(self.picker)
        top.addWidget(prev_btn)
        top.addWidget(next_btn)
        top.addWidget(self.counter)
        top.addStretch(1)
        box.addLayout(top)
        self.text = ReadingText()
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setMaximumWidth(980)
        box.addWidget(self.text)
        box.addWidget(self.start_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.own = Collapsible(CUSTOM_PASSAGE_NAME, key="practice.own_passage")
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(CUSTOM_PASSAGE_HINT)
        self.editor.setFixedHeight(110)
        self.editor.setPlainText(core.custom_passage() or "")
        save_row = QHBoxLayout()
        save_btn = Button("Save my passage", variant="secondary", icon="ph.floppy-disk")
        save_btn.clicked.connect(self.save_own)
        save_row.addWidget(save_btn)
        save_row.addWidget(Caption("Saved in your profile; it joins the list above."), 1)
        self.own.add(self.editor)
        holder = QWidget()
        holder.setLayout(save_row)
        self.own.add(holder)
        box.addWidget(self.own)
        box.addWidget(self.graph)
        box.addStretch(1)
        self.fill_picker()
        self.show_passage(0)

    def fill_picker(self):
        self.picker.set_options([(str(i), self.core.passage(i)["name"]) for i in range(self.core.passage_count())],
                                str(self.index))

    def show_passage(self, index):
        shown = self.core.passage(index)
        self.index = shown["index"]
        self.picker.set_current_key(str(self.index))
        self.counter.setText(f"{self.index + 1} of {self.core.passage_count()}")
        self.text.setText(shown["text"])
        if self.core.reading is not None:
            self.core.set_reading(self.index)

    def save_own(self):
        index = self.core.set_custom_passage(self.editor.toPlainText())
        self.fill_picker()
        self.show_passage(index if index is not None else 0)

    def before_recording(self):
        self.core.set_reading(self.index)

    def opened(self):
        self.core.set_reading(self.index)

    def closed(self):
        self.core.set_reading(None)


class Phrases(_RecordingPanel):
    """One everyday phrase at a time; record it, then the next."""

    def __init__(self, core):
        super().__init__(core, "Record this phrase")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.L)
        self.counter = Label("", role="label", tone="muted")
        self.text = Headline("", level="display")
        box.addWidget(self.counter)
        box.addWidget(self.text)
        row = QHBoxLayout()
        row.setSpacing(theme.S)
        prev_btn = Button("Previous", variant="ghost", icon="ph.caret-left")
        next_btn = Button("Next phrase", variant="secondary", icon="ph.caret-right")
        prev_btn.clicked.connect(lambda: core.next_phrase(-1))
        next_btn.clicked.connect(lambda: core.next_phrase())
        row.addWidget(self.start_btn)
        row.addWidget(next_btn)
        row.addWidget(prev_btn)
        row.addStretch(1)
        box.addLayout(row)
        box.addWidget(Caption("Say it the way you would to a stranger: easy, not performed. Each recording "
                              "is a take, and they count together in Progress as “phrases”."))
        box.addWidget(self.graph)
        box.addStretch(1)
        listen(self, core, {"phrase_changed": self.show_phrase})
        self.show_phrase(core.phrase())

    def show_phrase(self, phrase):
        self.counter.setText(f"Phrase {phrase['number']} of {phrase['count']}")
        self.text.setText(f"“{phrase['text']}”")

    def opened(self):
        self.core.set_phrase_practice(True)

    def closed(self):
        self.core.set_phrase_practice(False)


class _Wrapped(QWidget):
    """A practice view that draws its own frames (vowel check, exercise, Voice Map)."""

    def __init__(self, view, draw):
        super().__init__()
        self.view = view
        self.draw = draw
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(view)

    def show_frame(self, frame, views):
        self.draw(views)

    def opened(self):
        pass

    def closed(self):
        pass


class PracticeScreen(QWidget):
    """The practice cards, and the focused practice when one is open."""

    def __init__(self, core, shell):
        super().__init__()
        self.core = core
        self.shell = shell
        self.current = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        # the overview
        overview = QWidget()
        box = QVBoxLayout(overview)
        box.setContentsMargins(theme.XXL, theme.XL, theme.XXL, theme.XL)
        box.setSpacing(theme.L)
        box.addWidget(Headline("Practice", level="title"))
        box.addWidget(Label("Pick one thing to work on. Each opens on its own, with a clear start and stop.",
                            role="body", tone="secondary", wrap=True))
        grid = FlowGrid(min_column_width=300, max_columns=3, max_column_width=520)
        self.cards = {}
        for key, title, text, mode, icon in PRACTICES:
            card = Card(title)
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            card.add_action(Chip(MODE_WORDS[mode], kind="neutral", icon="ph.chat-circle" if mode == "speak"
                                 else "ph.music-note"))
            card.add(Label(text, role="body", tone="secondary", wrap=True))
            open_btn = Button("Open", variant="primary" if key == "read" else "secondary", icon=icon)
            open_btn.clicked.connect(lambda _, k=key: self.open(k))
            card.add(open_btn)
            grid.add(card)
            self.cards[key] = card
        box.addWidget(grid)
        box.addStretch(1)
        overview_scroll = QScrollArea()
        overview_scroll.setWidgetResizable(True)
        overview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        overview_scroll.setWidget(overview)
        self.stack.addWidget(overview_scroll)

        # the focused practices
        self.read = ReadAlouds(core)
        self.phrases = Phrases(core)
        self.vowel_check = VowelCheck(core)
        self.exercise = ExerciseView(core)
        self.voice_map = VoiceMapView(core)
        self.voice_map.rebuild_requested.connect(shell.rebuild_voice_map)
        self.panels = {
            "read": self.read, "phrases": self.phrases,
            "vowels": _Wrapped(self.vowel_check, lambda v: self.vowel_check.show_state(v["vowels"])),
            "exercise": _Wrapped(self.exercise, lambda v: self.exercise.show_frame(v["exercise"])),
            "voicemap": _Wrapped(self.voice_map, lambda v: self.voice_map.frame_added(v["map"])),
        }
        self.pages = {}
        for key, title, text, mode, icon in PRACTICES:
            page = QWidget()
            page_box = QVBoxLayout(page)
            page_box.setContentsMargins(theme.XXL, theme.L, theme.XXL, theme.L)
            page_box.setSpacing(theme.L)
            head = QHBoxLayout()
            back = Button("All practice", variant="ghost", icon="ph.arrow-left")
            back.clicked.connect(self.close_practice)
            head.addWidget(back)
            head.addSpacing(theme.M)
            head.addWidget(Headline(title, level="title"))
            head.addStretch(1)
            head.addWidget(Chip(MODE_WORDS[mode] + " mode", kind="accent"))
            page_box.addLayout(head)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(self.panels[key])
            page_box.addWidget(scroll, 1)
            self.stack.addWidget(page)
            self.pages[key] = page

    def open(self, key):
        """Show one practice focused (switching to the mode it needs)."""
        mode = next(m for k, _, _, m, _ in PRACTICES if k == key)
        if self.core.mode != mode:
            self.core.set_mode(mode)
        if self.current and self.current != key:
            self.panels[self.current].closed()
        self.current = key
        self.stack.setCurrentWidget(self.pages[key])
        self.panels[key].opened()

    def close_practice(self):
        if self.current:
            self.panels[self.current].closed()
        self.current = None
        self.stack.setCurrentIndex(0)

    def show_frame(self, frame, views):
        if self.current:
            self.panels[self.current].show_frame(frame, views)

    def showEvent(self, event):
        super().showEvent(event)
        if self.current:
            self.panels[self.current].opened()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.current:
            self.panels[self.current].closed()
