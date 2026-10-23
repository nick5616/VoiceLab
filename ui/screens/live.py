"""The live view (note readout, coach line, pitch graph / spectrum / pitch x resonance, live cards and the
phrase prompt) and the focused Live screen built from it. Both draw the core's live snapshots."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from ui import theme
from ui.components import Button, Caption, Chip, IconButton, Label, Tabs
from ui.live_cards import LiveCards
from ui.live_graph import LivePitchGraph
from ui.live_views import PitchResonancePlane, SpectrumView
from ui.screens.listen import listen

VIEWS = [("pitch", "Pitch"), ("spectrum", "Spectrum"), ("plane", "Pitch × Resonance")]
SPEAK_ONLY_VIEWS = {"plane"}       # resonance isn't measured in singing (as the old tab set)
PLAYHEAD_MS = 40                   # the replay playhead moves this often
COACH_IDLE_TONE = "muted"


class NumberLabel(Label):
    """The big readout number, in the figure face at a given size (kept through theme changes)."""

    def __init__(self, text, size):
        self.size = size
        super().__init__(text, role="figure")

    def restyle(self, *_):
        super().restyle()
        self.setFont(theme.font("figure", size=self.size))


class PhraseStrip(QWidget):
    """An everyday phrase to say, with "next phrase" (and, on the Live screen, a replay button)."""

    def __init__(self, core, large=False, replay=None):
        super().__init__()
        self.core = core
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S)
        self.label = Label("Try saying", role="label", tone="muted")
        self.text = Label("", role="title" if large else "heading", wrap=True)
        self.text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(self.label)
        words.addWidget(self.text)
        row.addLayout(words, 1)
        self.next_btn = IconButton("ph.arrows-clockwise", "Another phrase", variant="secondary")
        self.next_btn.clicked.connect(lambda: core.next_phrase())
        row.addWidget(self.next_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        if replay is not None:
            row.addWidget(replay, 0, Qt.AlignmentFlag.AlignVCenter)
        listen(self, core.events, ("phrase_changed", self.show_phrase))
        self.show_phrase(core.phrase())

    def show_phrase(self, phrase):
        self.text.setText(f"“{phrase['text']}”")


class LiveView(QWidget):
    """Everything that follows the voice live. `focused` is the big version for the Live screen."""

    def __init__(self, core, focused=False, replay=None):
        super().__init__()
        self.core = core
        self.focused = focused
        self.mode = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.M if not focused else theme.L)

        head = QHBoxLayout()
        head.setSpacing(theme.L)
        readout = QVBoxLayout()
        readout.setSpacing(0)
        self.note_label = NumberLabel("—", size=56 if focused else 36)
        self.note_label.setMinimumWidth(230 if focused else 170)
        self.note_label.setToolTip("The pitch you are on now (median of the last quarter second). "
                                   "Green when pitch and resonance are both on target.")
        self.note_name = Label("", role="heading" if focused else "label", tone="secondary")
        self.note_name.setFixedHeight(24 if focused else 20)      # empty or not, the same height
        readout.addWidget(self.note_label)
        readout.addWidget(self.note_name)
        self.coach_label = Label("", role="body", tone="secondary", wrap=True)
        self.coach_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.coach_label.setMinimumHeight(40)
        self.egg_chip = Chip("EGG", kind="neutral", dot=True)
        self.egg_chip.hide()
        head.addLayout(readout)
        head.addWidget(self.coach_label, 1, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.egg_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(head)

        self.tabs = Tabs(VIEWS)
        self.tabs.changed.connect(self._view_changed)
        box.addWidget(self.tabs)
        self.graph = LivePitchGraph(core.mode, compact=not focused)
        self.spectrum = SpectrumView(core.engine.analyzer.spectrum_freqs) if core.engine else QWidget()
        self.plane = PitchResonancePlane()
        self.stack = QStackedWidget()
        for widget in (self.graph, self.spectrum, self.plane):
            self.stack.addWidget(widget)
        self.stack.setMinimumHeight(300 if focused else 200)
        box.addWidget(self.stack, 1)
        self.cards = LiveCards(core, large=focused)
        box.addWidget(self.cards)
        self.phrase = PhraseStrip(core, large=focused, replay=replay)
        box.addWidget(self.phrase)

        self.playhead_timer = QTimer(self)
        self.playhead_timer.setInterval(PLAYHEAD_MS)
        self.playhead_timer.timeout.connect(self._follow_replay)
        self.note_text, self.note_tone, self.coach_text = None, None, None
        listen(self, core.events, *(
            ("mode_changed", self.apply_mode), ("profile_changed", self.refresh_profile),
            ("take_added", self._take_added), ("take_updated", self._take_updated),
            ("take_removed", self._take_removed), ("takes_loaded", lambda *_: self._load_takes()),
            ("voice_map_changed", lambda: self.apply_mode(self.core.mode)),
            ("mic_changed", lambda info: self.apply_mode(self.core.mode))))
        self.apply_mode(core.mode)
        self._load_takes()
        self.show_snapshot(None, core.live.snapshot())

    # --- mode and profile ---

    def apply_mode(self, mode):
        """Axis, views and cards of `mode` (the core already switched the live session)."""
        self.mode = mode
        self.graph.set_mode(mode, voice_range=self.core.voice_map.data.range_midi())
        speak = mode == "speak"
        if not speak and self.tabs.current() in SPEAK_ONLY_VIEWS:
            self.tabs.set_current("pitch", animate=False)
            self._view_changed("pitch")
        self.tabs.options = [o for o in [(k, label, None) for k, label in VIEWS] if speak or o[0] not in SPEAK_ONLY_VIEWS]
        self.tabs.keys = [o[0] for o in self.tabs.options]
        self.tabs.index = self.tabs.keys.index(self._current_view())
        self.tabs.line = None
        self.tabs.update()
        self.egg_chip.setVisible(self.core.live.egg_on)
        self.refresh_profile()

    def _current_view(self):
        return VIEWS[self.stack.currentIndex()][0]

    def _view_changed(self, key):
        self.stack.setCurrentIndex([k for k, _ in VIEWS].index(key))

    def show_view(self, key):
        self.tabs.set_current(key, animate=False)
        self._view_changed(key)

    def refresh_profile(self):
        p = self.core.profile
        self.graph.set_reference(p.baseline_value("pitch"), p.target("pitch"))
        baseline = (p.baseline_value("pitch"), p.baseline_value("delta_f")) if p.baseline else None
        self.plane.set_reference(baseline, p.target("pitch"), p.target("delta_f"))

    # --- takes on the plane ---

    def _load_takes(self):
        self.plane.clear_takes()
        for data in self.core.shown_takes():
            self._take_added(data)

    def _take_added(self, data):
        number = self.core.number(data)
        if data.get("delta_f") is not None and data.get("pitch") and number is not None:
            self.plane.add_take(number, data["pitch"], data["delta_f"])

    def _take_updated(self, data):
        number = self.core.number(data)
        if number is not None:
            self.plane.remove_take(number)
            self._take_added(data)

    def _take_removed(self, uid):
        known = {self.core.number(d) for d in self.core.takes()}
        for number in [n for n in self.plane.take_points if n not in known]:
            self.plane.remove_take(number)

    # --- frames ---

    def show_snapshot(self, frame, snapshot):
        """Draw one live frame: the readout and the graph from the same snapshot."""
        note = snapshot["note"]
        if note["text"] != self.note_text:
            self.note_text = note["text"]
            name, _, hz = note["text"].rpartition(" · ")
            self.note_label.setText(hz or note["text"])
            self.note_name.setText(name)
        tone = "on_target" if note["on_target"] else "primary"
        if tone != self.note_tone:
            self.note_tone = tone
            self.note_label.set_tone(tone)
        coach = snapshot["coach"]
        if coach["line"] != self.coach_text:
            self.coach_text = coach["line"]
            self.coach_label.setText(coach["line"])
            self.coach_label.setToolTip(coach["full"])
        self.graph.show_snapshot(snapshot)
        if frame is not None and self.stack.currentWidget() is self.spectrum and hasattr(self.spectrum, "update_frame"):
            self.spectrum.update_frame(frame)
        self.plane.update_point(snapshot["plane"]["f0"], snapshot["plane"]["delta_f"])
        self.cards.show_snapshot(snapshot)
        egg = snapshot["egg"]
        if egg["on"]:
            self.egg_chip.set_kind("success" if egg["signal"] else "neutral")
            self.egg_chip.setToolTip(egg["tooltip"])

    # --- replay ---

    def start_replay(self):
        """The core started a replay: hold the line and run the playhead."""
        self.graph.freeze(True)
        self.playhead_timer.start()
        self._follow_replay()

    def _follow_replay(self):
        progress = self.core.replay_progress()
        if progress is None:
            self.playhead_timer.stop()
            self.graph.freeze(False)
            return
        played, length = progress
        self.graph.set_playhead(length - played)


class LiveScreen(QWidget):
    """Focused practice: the big readout and graph, the phrase prompt, "Replay last 10 s"."""

    def __init__(self, core, shell):
        super().__init__()
        self.core = core
        box = QVBoxLayout(self)
        box.setContentsMargins(theme.XXL, theme.L, theme.XXL, theme.M)
        box.setSpacing(theme.M)
        self.replay_btn = Button("Replay last 10 s", variant="secondary", icon="ph.arrow-counter-clockwise")
        self.replay_btn.setToolTip("Hear the last few seconds of what the mic heard (R)")
        self.replay_btn.clicked.connect(shell.replay)
        self.view = LiveView(core, focused=True, replay=self.replay_btn)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.view)
        box.addWidget(scroll, 1)
        self.hint = Caption("Space records or stops · R replays the last 10 s · the zones are tendencies, not rules")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(self.hint)

    def show_frame(self, frame, views):
        self.view.show_snapshot(frame, views["live"])
