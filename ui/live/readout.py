"""The readout: the listening state ("Hearing you", "Listening", "Too quiet", "No signal"), the big pitch you
are on right now with its note in words, and the "In range 4 s" chip. One widget for the Live screen and the
dashboard's "Right now" strip, so the two always say the same thing. Everything comes from the live snapshot
(live_session decides; this draws)."""
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from live_session import state_strip
from ui import theme
from ui.components import Chip, Label

IDLE_WORDS = {"silent": "Say something", "too_quiet": "Can't hear you well", "no_signal": "No signal",
              "recording": "Say something"}     # the big number's place when there is no pitch
IDLE_SIZE_RATIO = 0.5           # the idle words are this fraction of the number's size
HELD_MIN_SECONDS = 1.0          # the "In range" chip appears after this long in range
NOW_PREFIX = "now"
STATE_TICK_MS = 500             # the state is re-read this often between frames (a dead mic sends none)


class NumberLabel(Label):
    """The big readout number, in the figure face at a given size (kept through theme changes)."""

    def __init__(self, text, size):
        self.size = size
        super().__init__(text, role="figure")

    def set_size(self, size):
        self.size = size
        self.restyle()

    def restyle(self, *_):
        super().restyle()
        self.setFont(theme.font("figure", size=self.size))


class Readout(QWidget):
    """State chip + held chip, the big number and the note under it. show_snapshot(snapshot) draws one
    live snapshot; `core` supplies the mic's state and whether a take is recording."""

    def __init__(self, core, size=56, parent=None):
        super().__init__(parent)
        self.core = core
        self.full_size = size
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(theme.S)
        self.state_chip = Chip(state_strip("silent")["text"], kind="neutral", dot=True)
        self.held_chip = Chip("In range", kind="on_target", dot=True)
        self.held_chip.hide()
        chips.addWidget(self.state_chip)
        chips.addWidget(self.held_chip)
        self.note_inline = Label("", role="caption", tone="secondary")     # the short layout's note, beside the chips
        self.note_inline.hide()
        chips.addWidget(self.note_inline)
        chips.addStretch(1)
        col.addLayout(chips)
        self.note_label = NumberLabel(IDLE_WORDS["silent"], size)
        self.note_label.setMinimumWidth(int(size * 4.1))
        self.note_label.setToolTip("The pitch you are on right now: the median of the last quarter second, the "
                                   "same number as the Pitch card. Green when pitch and resonance are both in "
                                   "your target.")
        self.note_name = Label("", role="label", tone="secondary")
        self.note_name.setFixedHeight(24 if size >= 48 else 20)       # empty or not, the same height
        col.addWidget(self.note_label)
        col.addWidget(self.note_name)
        self.shown = {}                 # what is drawn now, so a frame that changes nothing repaints nothing
        self.last = None                # the snapshot drawn last
        self.compact = False
        self.set_size(size)
        self.timer = QTimer(self)
        self.timer.setInterval(STATE_TICK_MS)
        self.timer.timeout.connect(self.refresh_state)
        self.timer.start()

    def refresh_state(self):
        """Re-read the listening state between frames: a mic that went silent sends none to say so."""
        if self.last is None or not self.isVisible():
            return
        self.show_snapshot(dict(self.last, state=self.core.live.current_state()))

    def set_size(self, size):
        """The number's size in px (the Live screen shrinks it on a short window)."""
        self.full_size = size
        self.shown.pop("number", None)
        self.note_label.setMinimumWidth(int(size * 4.1))
        self.note_name.set_role("heading" if size >= 48 else "label")

    def set_compact(self, compact):
        """The short-window layout: the note goes beside the chips and the row under the number disappears."""
        self.compact = bool(compact)
        self.note_name.setVisible(not self.compact)
        self.note_inline.setVisible(self.compact)

    def _set(self, name, value, apply):
        if self.shown.get(name) != value:
            self.shown[name] = value
            apply(value)

    def show_snapshot(self, snapshot):
        """Draw the state, the held time, and the pitch (or the words that stand in for it)."""
        self.last = snapshot
        mic = self.core.mic_status().get("state", "ok")
        strip = state_strip(snapshot.get("state", "silent"), mic, bool(self.core.is_recording))
        self._set("strip", (strip["text"], strip["kind"]), lambda v: self.state_chip.set_text(v[0], v[1]))
        self.state_chip.setToolTip(self.core_tip(strip["state"]))
        held = snapshot.get("held_seconds", 0.0)
        shown = held >= HELD_MIN_SECONDS and strip["state"] in ("hearing", "recording")
        word = snapshot.get("session", {}).get("word", "In range")      # "In tune" when singing
        self._set("held", f"{word} {held:.0f} s" if shown else None, self._apply_held)
        note = snapshot["note"]
        hz_text = note["text"].rpartition(" · ")[2] if note.get("hz") and strip["state"] != "no_signal" else None
        idle = hz_text is None
        text = hz_text or IDLE_WORDS.get(strip["state"], IDLE_WORDS["silent"])
        tone = "on_target" if note.get("on_target") and not idle else "primary" if not idle else "muted"
        self._set("number", (text, idle, self.full_size), self._apply_number)
        self._set("tone", tone, self.note_label.set_tone)
        words = note.get("words", "")
        self._set("name", f"{words} · {NOW_PREFIX}" if words and not idle else "", self._apply_name)

    def _apply_name(self, text):
        self.note_name.setText(text)
        self.note_inline.setText(text)

    def _apply_number(self, value):
        text, idle, size = value
        self.note_label.setText(text)
        self.note_label.set_size(int(size * IDLE_SIZE_RATIO) if idle else size)

    def _apply_held(self, text):
        self.held_chip.setVisible(text is not None)
        if text is not None:
            self.held_chip.set_text(text)

    @staticmethod
    def core_tip(state):
        """What the state chip's tooltip says."""
        return {"hearing": "A voice is coming through the mic.",
                "silent": "The mic is open and nothing is being said.",
                "too_quiet": "There is sound, but too quiet to find your pitch. Move closer to the mic or raise "
                             "its level.",
                "no_signal": "The mic is not delivering sound. Check the cable, or choose the mic again in "
                             "Settings.",
                "recording": "A take is being recorded."}.get(state, "")
