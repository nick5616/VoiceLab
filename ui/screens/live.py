"""The live view (listening state, the pitch you are on now, coach line, pitch graph / spectrum / pitch x resonance,
the in-range session row, live cards and the phrase prompt) and the focused Live screen built from it. Both draw
the core's live snapshots; live_session decides what they say."""
from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from live_session import legend_text
from ui import theme
from ui.components import Button, Caption, Chip, IconButton, Label, Tabs
from ui.live.readout import Readout
from ui.live.session_row import SessionRow
from ui.live_cards import LiveCards
from ui.live_graph import LivePitchGraph
from ui.live_views import SPECTRUM_CAPTION, PitchResonancePlane, SpectrumView
from ui.takes import listen

VIEWS = [("pitch", "Pitch"), ("spectrum", "Spectrum"), ("plane", "Pitch × Resonance")]
SPEAK_ONLY_VIEWS = {"plane"}       # resonance isn't measured in singing (as the old tab set)
PLAYHEAD_MS = 40                   # the replay playhead moves this often
COMPACT_BELOW = 830                # px: a Live screen shorter than this uses the compact layout (audit A-005)
SIZES = {"full": {"number": 56, "graph": 180, "spacing": theme.M},
         "compact": {"number": 40, "graph": 130, "spacing": theme.XS}}
SING_PROMPT_LABEL = "Your note"
EGG_CHIP_TEXT = "Contact sensor"


class ViewStack(QStackedWidget):
    """The graph views. Its preferred height is its minimum, so a scroll area around the live view only
    scrolls when the window really is too short; any spare height goes to the graph (stretch)."""

    def sizeHint(self):
        return QSize(super().sizeHint().width(), self.minimumHeight())


class PhraseStrip(QWidget):
    """An everyday phrase to say, with "next phrase" (and, on the Live screen, a replay button). In Sing mode it
    names the note to sing instead (the exercise's, else "Sing any note")."""

    def __init__(self, core, large=False, replay=None):
        super().__init__()
        self.core = core
        self.large = large
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
        self.prompt = None
        listen(self, core, {"phrase_changed": self.show_phrase})
        self.show_phrase(core.phrase())

    def set_compact(self, compact):
        """The short-window layout: one line, no "Try saying" caption over it."""
        self.label.setVisible(not compact)
        self.text.set_role("body" if compact else "title" if self.large else "heading")

    def show_phrase(self, phrase):
        self.phrase = phrase
        if self.prompt is None:
            self.text.setText(f"“{phrase['text']}”")

    def show_prompt(self, prompt):
        """Sing mode: `prompt` is the sing prompt (live_session.sing_prompt); None goes back to the phrase."""
        if prompt == self.prompt:
            return
        self.prompt = prompt
        self.label.setText(SING_PROMPT_LABEL if prompt else "Try saying")
        self.next_btn.setVisible(prompt is None)
        self.text.setText(prompt if prompt else f"“{self.phrase['text']}”")


class LiveView(QWidget):
    """Everything that follows the voice live. `focused` is the big version for the Live screen.
    targets_requested() comes from the session row's "Set target" button."""

    targets_requested = pyqtSignal()

    def __init__(self, core, focused=False, replay=None):
        super().__init__()
        self.core = core
        self.focused = focused
        self.mode = None
        self.compact = False
        self.box = box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.M)

        head = QHBoxLayout()
        head.setSpacing(theme.L)
        self.readout = Readout(core, size=SIZES["full"]["number"] if focused else 36)
        self.note_label, self.note_name = self.readout.note_label, self.readout.note_name
        self.coach_label = Label("", role="body", tone="secondary", wrap=True)
        self.coach_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.coach_label.setMinimumHeight(60 if focused else 40)
        self.egg_chip = Chip(EGG_CHIP_TEXT, kind="neutral", dot=True)
        self.egg_chip.hide()
        head.addWidget(self.readout)
        head.addWidget(self.coach_label, 1, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.egg_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(head)

        self.tabs = Tabs(VIEWS)
        self.tabs.changed.connect(self._view_changed)
        box.addWidget(self.tabs)
        self.graph = LivePitchGraph(core.mode, compact=not focused)
        self.spectrum = SpectrumView(core.engine.analyzer.spectrum_freqs) if core.engine else QWidget()
        self.plane = PitchResonancePlane()
        self.stack = ViewStack()
        for widget in (self.graph, self.spectrum, self.plane):
            self.stack.addWidget(widget)
        self.stack.setMinimumHeight(SIZES["full"]["graph"] if focused else 200)
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        box.addWidget(self.stack, 1)
        self.spectrum_caption = Caption(SPECTRUM_CAPTION, tone="secondary")
        self.spectrum_caption.hide()
        box.addWidget(self.spectrum_caption)
        self.session_row = SessionRow()
        self.session_row.targets_requested.connect(self.targets_requested.emit)
        box.addWidget(self.session_row)
        self.cards = LiveCards(core, large=focused)
        box.addWidget(self.cards)
        self.legend = Caption("", tone="muted")
        self.legend.setMinimumHeight(16)
        self.legend.hide()
        box.addWidget(self.legend)
        self.phrase = PhraseStrip(core, large=focused, replay=replay)
        box.addWidget(self.phrase)

        self.playhead_timer = QTimer(self)
        self.playhead_timer.setInterval(PLAYHEAD_MS)
        self.playhead_timer.timeout.connect(self._follow_replay)
        self.coach_text = None
        listen(self, core, {
            "mode_changed": self.apply_mode, "profile_changed": self.refresh_profile,
            "take_added": self._take_added, "take_updated": self._take_updated,
            "take_removed": self._take_removed, "takes_loaded": lambda *_: self._load_takes(),
            "voice_map_changed": lambda: self.apply_mode(self.core.mode),
            "mic_changed": lambda info: self.apply_mode(self.core.mode),
            "recording": lambda on: self.readout.refresh_state()})
        self.apply_mode(core.mode)
        self._load_takes()
        self.show_snapshot(None, core.live.snapshot())

    # --- size ---

    def set_compact(self, compact):
        """The short-window layout (audit A-005): a smaller number and graph, compact cards, no legend."""
        compact = bool(compact)
        if compact == self.compact or not self.focused:
            return
        self.compact = compact
        size = SIZES["compact" if compact else "full"]
        self.box.setSpacing(size["spacing"])
        self.readout.set_size(size["number"])
        self.readout.set_compact(compact)
        self.phrase.set_compact(compact)
        self.session_row.set_compact(compact)
        self.stack.setMinimumHeight(size["graph"])
        self.cards.set_compact(compact)
        self.legend.setVisible(bool(self.legend.text()) and not compact)
        self.updateGeometry()

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
        self.spectrum_caption.setVisible(key == "spectrum")

    def show_view(self, key):
        self.tabs.set_current(key, animate=False)
        self._view_changed(key)

    def refresh_profile(self):
        p = self.core.profile
        speak = self.mode == "speak"       # the speaking target and baseline are not drawn over a song
        self.graph.set_reference(p.baseline_value("pitch") if speak else None, p.target("pitch") if speak else None)
        baseline = (p.baseline_value("pitch"), p.baseline_value("delta_f")) if p.baseline else None
        self.plane.set_reference(baseline, p.target("pitch"), p.target("delta_f"))
        text = legend_text(p) if speak else ""
        self.legend.setText(text)
        self.legend.setVisible(bool(text) and not self.compact)

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
        """Draw one live frame: the readout, the graph and the cards from the same snapshot."""
        self.readout.show_snapshot(snapshot)
        coach = snapshot["coach"]
        if coach["line"] != self.coach_text:
            self.coach_text = coach["line"]
            self.coach_label.setText(coach["line"])
            self.coach_label.setToolTip(coach["full"])
        self.graph.show_snapshot(snapshot)
        self.graph.set_in_range(bool(snapshot["note"].get("on_target")))
        if frame is not None and self.stack.currentWidget() is self.spectrum and hasattr(self.spectrum, "update_frame"):
            self.spectrum.update_frame(frame)
        self.plane.update_point(snapshot["plane"]["f0"], snapshot["plane"]["delta_f"])
        self.session_row.show_session(snapshot["session"])
        self.cards.show_snapshot(snapshot)
        self.phrase.show_prompt(snapshot.get("sing_prompt"))
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

    targets_requested = pyqtSignal()

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
        self.view.targets_requested.connect(self.targets_requested.emit)
        self.view.targets_requested.connect(lambda: shell.go("settings"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.view)
        box.addWidget(scroll, 1)
        self.hint = Caption("Space records or stops · R replays the last 10 s · the zones are tendencies, not rules")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(self.hint)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.view.set_compact(self.height() < COMPACT_BELOW)
        self.hint.setVisible(self.height() >= COMPACT_BELOW)

    def show_frame(self, frame, views):
        self.view.show_snapshot(frame, views["live"])
