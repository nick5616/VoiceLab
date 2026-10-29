"""The Practice screen: today's practice (a short routine) and a card per practice (read aloud, phrases, vowel
check, exercises, Voice Map). Opening one shows it focused, with its state, its own Start / Stop, live feedback and
a result that says what to do next; "All practice" goes back."""
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QPlainTextEdit, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout,
                             QWidget)

from exercise import TAIL_SECONDS
from practice_state import disabled_reason, readout_view, start_reason, state_view
from speech import (CUSTOM_PASSAGE_HINT, CUSTOM_PASSAGE_NAME, PHRASES, RangeTracker, phrases_summary,
                    reading_position)
from ui import theme
from ui.components import Button, Caption, Card, Chip, Collapsible, FlowGrid, Headline, IconButton, Label, Select
from ui.exercise_view import ExerciseView
from ui.live_graph import LivePitchGraph
from ui.practice.result_card import ResultCard
from ui.practice.session_card import SessionCard
from ui.practice.widgets import HighlightText, PhraseDots, PracticeStatus, Readout
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
GRAPH_MIN_PX, GRAPH_HINT_PX = 110, 150  # px: the pitch graph of the reading pages is this short, and grows into spare room
OWN_HEADER_PX = 28                      # px: the click target of "Your own passage"


class _RecordingPanel(QWidget):
    """Shared by Read aloud and Phrases: a Start / Stop that records a take with live feedback (the pitch you
    are making, whether it is in your range) and, afterwards, a result card with what to do next."""
    open_report = pyqtSignal(str)

    def __init__(self, core, start_text):
        super().__init__()
        self.core = core
        self.start_text = start_text
        self.active = False
        self.started = None                 # core.clock() when this recording began
        self.was_recording = False
        self.start_btn = Button(start_text, variant="primary", icon="ph.record-fill")
        self.start_btn.clicked.connect(self.toggle)
        self.graph = LivePitchGraph("speak", compact=True)
        self.graph.setMinimumHeight(GRAPH_MIN_PX)
        self.graph.sizeHint = lambda: QSize(480, GRAPH_HINT_PX)      # not the 480 px a plot asks for by default
        self.readout = Readout()
        self.tracker = RangeTracker(None)
        self.result_card = ResultCard()
        self.result_card.action.connect(self._result_action)
        self.error = None
        listen(self, core, {"recording": self._recording,
                            "controls_changed": lambda c: self._recording(c["recording"]),
                            "profile_changed": self._reference, "take_ready": self._take_ready,
                            "status": self._status})
        self._reference()

    def _reference(self):
        p = self.core.profile
        self.graph.set_reference(p.baseline_value("pitch"), p.target("pitch"))
        target = p.target("pitch")
        self.tracker.target = tuple(target) if target else None

    def _status(self, text, level):
        if self.active and level == "error":
            self.error = text

    def toggle(self):
        if self.core.is_recording:
            self.core.stop_recording()
        elif self.core.controls()["record"]:
            self.before_recording()
            self.error = None
            self.core.start_recording()

    def before_recording(self):
        """Tell the core what this take is for."""

    def _recording(self, on):
        self.start_btn.setText("Stop" if on else self.start_text)
        self.start_btn.set_icon("ph.stop-fill" if on else "ph.record-fill")
        self.start_btn.set_variant("secondary" if on else "primary")
        controls = self.core.controls()
        self.start_btn.setEnabled(bool(on or controls["record"]))
        self.start_btn.setToolTip("" if self.start_btn.isEnabled() else
                                  disabled_reason(controls, self.core.record_state()) or "")
        if on and not self.was_recording:
            self.started = self.core.clock()
            self.tracker.reset()
            self.result_card.clear()
            self.graph.setVisible(True)
        if on != self.was_recording:
            self.recording_changed(on)
        self.was_recording = bool(on)

    def recording_changed(self, on):
        """The recording started or stopped."""

    def owns(self, take):
        """True when a new take came from this practice."""
        return False

    def _take_ready(self, take):
        if self.active and self.owns(take):
            self.show_result(take)

    def show_result(self, take):
        """Draw what this take shows."""

    def _result_action(self, key):
        if key == "report" and self.result_card.data:
            self.open_report.emit(self.result_card.data.get("uid", ""))
        else:
            self.result_action(key)

    def result_action(self, key):
        """A button of the result card other than Open full report."""

    def seconds(self):
        return 0.0 if self.started is None else max(0.0, self.core.clock() - self.started)

    def show_frame(self, frame, views):
        snap = views["live"]
        self.graph.show_snapshot(snap)
        if self.core.is_recording:
            self.tracker.add(snap.get("f0"), self.core.clock())
        view = readout_view(snap, self.tracker, self.core.is_recording)
        self.readout.show_now(view["hz"], view["state_text"], view["range_text"], view["range_ok"],
                              view["resonance"])

    def facts(self):
        """The facts practice_state.state_view needs."""
        return {"recording": self.core.is_recording, "analysing": self.core.analyzing, "seconds": self.seconds(),
                "mic_ok": self.core.mic_available, "has_result": self.result_card.data is not None,
                "error": self.error}

    def reason(self):
        """Why Start can't be pressed now, or None."""
        return disabled_reason(self.core.controls(), self.core.record_state(), self.core.is_recording)

    def opened(self):
        """The panel became the focused practice."""
        self.active = True
        self.error = None
        self._recording(self.core.is_recording)

    def closed(self):
        """The panel was left."""
        self.active = False


class ReadAlouds(_RecordingPanel):
    """A passage to read while recording, with the sentence you should be on shaded; your own passage can be
    pasted and saved."""
    kind = "read"

    def __init__(self, core):
        super().__init__(core, "Start reading")
        self.index = 0
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.M)
        top = QHBoxLayout()
        top.setSpacing(theme.S)
        self.picker = Select()
        self.picker.setMinimumWidth(240)
        self.picker.activated.connect(lambda i: self.show_passage(i))
        self.prev_btn = IconButton("ph.caret-left", "Previous passage", variant="secondary")
        self.next_btn = IconButton("ph.caret-right", "Next passage", variant="secondary")
        self.prev_btn.clicked.connect(lambda: self.show_passage(self.index - 1))
        self.next_btn.clicked.connect(lambda: self.show_passage(self.index + 1))
        self.counter = Caption("")
        top.addWidget(self.picker)
        top.addWidget(self.prev_btn)
        top.addWidget(self.next_btn)
        top.addWidget(self.counter)
        top.addStretch(1)
        top.addWidget(self.start_btn)
        box.addLayout(top)
        middle = QHBoxLayout()
        middle.setSpacing(theme.XL)
        self.text = HighlightText()
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        middle.addWidget(self.text, 1, Qt.AlignmentFlag.AlignTop)
        middle.addWidget(self.readout, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(middle)
        self.own = Collapsible(CUSTOM_PASSAGE_NAME, key="practice.own_passage")
        self.own.header.setMinimumHeight(OWN_HEADER_PX)
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
        box.addWidget(self.result_card)
        box.addWidget(self.graph, 1)
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
        self.text.set_passage(shown["text"])
        if self.core.reading is not None:
            self.core.set_reading(self.index)
        if not self.core.is_recording:
            self.result_card.clear()
            self.focus_result(False)
            self.graph.setVisible(True)

    def save_own(self):
        index = self.core.set_custom_passage(self.editor.toPlainText())
        self.fill_picker()
        self.show_passage(index if index is not None else 0)

    def before_recording(self):
        self.core.set_reading(self.index)

    def recording_changed(self, on):
        for w in (self.picker, self.prev_btn, self.next_btn):
            w.setEnabled(not on)
        self.own.setVisible(not on and self.result_card.data is None)
        if not on:
            self.text.set_span(None)

    def owns(self, take):
        return str(take.get("task", "")).startswith("passage") and not take.get("exercise")

    def show_result(self, take):
        result = self.core.passage_result(take["uid"])
        if result is None:
            return
        rows = [(r["name"], r["value"], r["change"]) for r in result["rows"]]
        self.result_card.show_result({
            "title": "Your reading", "headline": result["headline"], "compare": result["compare"], "rows": rows,
            "tip": result["tip"], "uid": result["uid"],
            "actions": [("again", "Read again", "primary"), ("report", "Open full report", "secondary")]})
        self.graph.setVisible(False)
        self.focus_result(True)

    def focus_result(self, on):
        """With a result showing, the passage and the live readout step aside so the result fits the window."""
        for w in (self.text, self.readout, self.own):
            w.setVisible(not on)

    def result_action(self, key):
        if key == "again":
            self.result_card.clear()
            self.focus_result(False)
            self.graph.setVisible(True)
            self.toggle()

    def show_frame(self, frame, views):
        super().show_frame(frame, views)
        if self.core.is_recording:
            pos = reading_position(self.text.passage, self.seconds())
            self.text.set_span(pos["span"])

    def opened(self):
        super().opened()
        self.core.set_reading(self.index)

    def closed(self):
        super().closed()
        self.core.set_reading(None)


class Phrases(_RecordingPanel):
    """One everyday phrase at a time; record it, see how it went, then the next."""
    kind = "phrases"

    def __init__(self, core):
        super().__init__(core, "Record this phrase")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.M)
        self.current = 0                    # index of the phrase on screen
        self.counter = Label("", role="label", tone="muted")
        self.text = Headline("", level="display")
        self.dots = PhraseDots(len(PHRASES))
        self.today = Caption("", wrap=False)
        top = QVBoxLayout()
        top.setSpacing(theme.XS)
        top.addWidget(self.counter)
        top.addWidget(self.text)
        dots_row = QHBoxLayout()
        dots_row.setSpacing(theme.M)
        dots_row.addWidget(self.dots)
        dots_row.addWidget(self.today)
        dots_row.addStretch(1)
        top.addLayout(dots_row)
        middle = QHBoxLayout()
        middle.setSpacing(theme.XL)
        middle.addLayout(top, 1)
        middle.addWidget(self.readout, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(middle)
        row = QHBoxLayout()
        row.setSpacing(theme.S)
        self.prev_btn = Button("Previous", variant="ghost", icon="ph.caret-left")
        self.next_btn = Button("Next phrase", variant="secondary", icon="ph.caret-right")
        self.prev_btn.clicked.connect(lambda: core.next_phrase(-1))
        self.next_btn.clicked.connect(lambda: core.next_phrase())
        row.addWidget(self.start_btn)
        row.addWidget(self.next_btn)
        row.addWidget(self.prev_btn)
        row.addStretch(1)
        box.addLayout(row)
        box.addWidget(Caption("Say it the way you would to a stranger: easy, not performed. Each recording "
                              "is a take, and they count together in Progress as “phrases”."))
        box.addWidget(self.result_card)
        box.addWidget(self.graph, 1)
        listen(self, core, {"phrase_changed": self.show_phrase, "take_ready": lambda t: self.show_today()})
        self.show_phrase(core.phrase())

    def show_phrase(self, phrase):
        self.counter.setText(f"Phrase {phrase['number']} of {phrase['count']}")
        self.text.setText(f"“{phrase['text']}”")
        self.current = phrase["index"]
        self.show_today()
        if not self.core.is_recording:
            self.result_card.clear()
            self.graph.setVisible(True)

    def show_today(self):
        takes = self.core.phrases_today()
        numbered = {t["phrase"] for t in takes if t.get("phrase") is not None}
        count = min(len(PHRASES), len(numbered) + sum(1 for t in takes if t.get("phrase") is None))
        self.dots.set_state(numbered, self.current)
        self.today.setText(f"{count} of {len(PHRASES)} recorded today")
        self.dots.setToolTip("A filled dot is a phrase you have recorded today; the ring is the one on screen.")

    def recording_changed(self, on):
        for w in (self.next_btn, self.prev_btn):
            w.setEnabled(not on)

    def owns(self, take):
        return take.get("task") == "phrases"

    def show_result(self, take):
        result = self.core.phrase_result(take["uid"])
        if result is None:
            return
        chips = []
        for name, share in (("Pitch", result["pitch_share"]), ("Resonance", result["resonance_share"])):
            if share is not None:
                chips.append((f"{name} in your range {share:.0%}", "on_target" if share >= 0.5 else "warning"))
        summary = phrases_summary(self.core.phrases_today())
        self.result_card.show_result({
            "title": "That phrase", "headline": result["text"], "chips": chips, "uid": take["uid"],
            "lines": [summary] if summary else [],
            "actions": [("next", "Next phrase", "primary"), ("again", "Record it again", "secondary"),
                        ("report", "Open full report", "ghost")]})
        self.graph.setVisible(False)
        self.show_today()

    def result_action(self, key):
        if key == "next":
            self.core.next_phrase()
        elif key == "again":
            self.result_card.clear()
            self.graph.setVisible(True)
            self.toggle()

    def opened(self):
        super().opened()
        self.core.set_phrase_practice(True)
        self.show_today()

    def closed(self):
        super().closed()
        self.core.set_phrase_practice(False)


class _Wrapped(QWidget):
    """A practice view that draws its own frames (vowel check, exercise, Voice Map)."""

    def __init__(self, view, draw, kind, facts):
        super().__init__()
        self.view = view
        self.draw = draw
        self.kind = kind
        self.facts = facts
        self.core = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(view)

    def show_frame(self, frame, views):
        self.draw(views)

    def reason(self):
        return None

    def opened(self):
        if hasattr(self.view, "apply_defaults"):
            self.view.apply_defaults()

    def closed(self):
        pass


class PracticeScreen(QWidget):
    """Today's practice and the practice cards, and the focused practice when one is open."""

    def __init__(self, core, shell):
        super().__init__()
        self.core = core
        self.shell = shell
        self.current = None
        self.entry_mode = None              # the mode you were in before a practice switched it
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        # the overview: today's practice, then the practices to pick from
        overview = QWidget()
        box = QVBoxLayout(overview)
        box.setContentsMargins(theme.XXL, theme.XL, theme.XXL, theme.XL)
        box.setSpacing(theme.L)
        box.addWidget(Headline("Practice", level="title"))
        box.addWidget(Label("Follow today's practice, or pick one thing to work on. Each opens on its own, with a "
                            "clear start and stop.", role="body", tone="secondary", wrap=True))
        self.session_card = SessionCard(core)
        self.session_card.open_step.connect(self.open)
        box.addWidget(self.session_card)
        box.addWidget(Label("Pick your own", role="heading"))
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
            "vowels": _Wrapped(self.vowel_check, lambda v: self.vowel_check.show_state(v["vowels"]), "vowels",
                               self._vowel_facts),
            "exercise": _Wrapped(self.exercise, lambda v: self.exercise.show_frame(v["exercise"]), "exercise",
                                 self._exercise_facts),
            "voicemap": _Wrapped(self.voice_map, lambda v: self.voice_map.frame_added(v["map"]), "voicemap",
                                 self._map_facts),
        }
        for panel in (self.read, self.phrases, self.exercise):
            panel.open_report.connect(self._open_report)
        self.pages, self.status_strips, self.notices = {}, {}, {}
        for key, title, text, mode, icon in PRACTICES:
            page = QWidget()
            page_box = QVBoxLayout(page)
            page_box.setContentsMargins(theme.XXL, theme.L, theme.XXL, theme.L)
            page_box.setSpacing(theme.M)
            head = QHBoxLayout()
            back = Button("All practice", variant="ghost", icon="ph.arrow-left")
            back.clicked.connect(self.close_practice)
            head.addWidget(back)
            head.addSpacing(theme.M)
            head.addWidget(Headline(title, level="title"))
            notice = Caption("", tone="secondary", wrap=False)
            notice.hide()
            head.addSpacing(theme.L)
            head.addWidget(notice, 1)
            head.addStretch(1)
            head.addWidget(Chip(MODE_WORDS[mode] + " mode", kind="accent"))
            page_box.addLayout(head)
            strip = PracticeStatus()
            page_box.addWidget(strip)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFrameShape(scroll.Shape.NoFrame)
            scroll.setWidget(self.panels[key])
            page_box.addWidget(scroll, 1)
            self.stack.addWidget(page)
            self.pages[key] = page
            self.status_strips[key] = strip
            self.notices[key] = notice
        refresh = lambda *_: self.refresh_status()      # noqa: E731
        listen(self, core, {"recording": refresh, "controls_changed": refresh, "take_ready": refresh,
                            "vowels_changed": refresh, "exercise_changed": refresh, "busy": refresh,
                            "voice_map_changed": refresh, "status": refresh, "practice_plan_changed": refresh})

    # --- facts of the wrapped practices ---

    def _vowel_facts(self):
        s = self.vowel_check.session.state()
        return {"running": s["running"], "index": s["index"], "total": s["total"], "analysing": s["analysing"],
                "has_result": self.vowel_check.session.result is not None and not s["running"],
                "error": s["error"]}

    def _exercise_facts(self):
        session = self.exercise.session
        s = session.state()
        end = max((st + d for st, d, _ in session.targets), default=1.0) + TAIL_SECONDS
        return {"running": s["running"], "now": session.now() or 0.0, "end": end, "analysing": s["job"],
                "has_result": s["result"] is not None, "error": s["error"]}

    def _map_facts(self):
        return {"seconds": self.core.voice_map.data.total_seconds(), "rebuilding": self.core.map_rebuilding()}

    # --- showing a practice ---

    def refresh_status(self):
        """Draw the open practice's state, and why its Start is disabled when it is."""
        key = self.current
        if key is None:
            return
        panel = self.panels[key]
        facts = panel.facts() if hasattr(panel, "facts") and callable(panel.facts) else {}
        view = state_view(panel.kind, facts)
        reason = panel.reason() if isinstance(panel, _RecordingPanel) else self._start_reason(key, view)
        self.status_strips[key].show_view(view, reason)

    def _start_reason(self, key, view):
        """Why Start is unavailable for a vowel check or exercise (and so disabled), else None."""
        if key not in ("vowels", "exercise"):
            return None
        button = self.vowel_check.start_btn if key == "vowels" else self.exercise.start_btn
        reason = start_reason(view, self.core.is_recording, self.core.analyzing)
        button.setEnabled(reason is None)
        button.setToolTip(reason or "")
        return reason

    def open(self, key):
        """Show one practice focused (switching to the mode it needs, and saying so)."""
        mode = next(m for k, _, _, m, _ in PRACTICES if k == key)
        if self.current is None:
            self.entry_mode = self.core.mode
        if self.core.mode != mode:
            self.core.set_mode(mode)
        notice = self.notices[key]
        if mode != self.entry_mode:
            notice.setText(f"Switched to {MODE_WORDS[mode]} mode for this practice. Going back puts you back in "
                           f"{MODE_WORDS[self.entry_mode]} mode.")
            notice.show()
        else:
            notice.hide()
        if self.current and self.current != key:
            self.panels[self.current].closed()
        self.current = key
        self.stack.setCurrentWidget(self.pages[key])
        self.panels[key].opened()
        self.refresh_status()

    def close_practice(self):
        if self.current:
            self.panels[self.current].closed()
        self.current = None
        self.stack.setCurrentIndex(0)
        if self.entry_mode and self.core.mode != self.entry_mode:
            self.core.set_mode(self.entry_mode)
        self.entry_mode = None

    def _open_report(self, uid):
        opener = getattr(self.shell, "open_report", None)
        if opener and uid:
            opener(uid)

    def show_frame(self, frame, views):
        if self.current:
            self.panels[self.current].show_frame(frame, views)
            self.refresh_status()

    def showEvent(self, event):
        super().showEvent(event)
        if self.current:
            self.panels[self.current].opened()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.current:
            self.panels[self.current].closed()
