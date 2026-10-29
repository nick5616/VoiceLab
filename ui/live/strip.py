"""The dashboard's "Right now" card: the listening state, the pitch you are on now and, when a target is set,
how long you have held it, with a button to the full Live screen. No graph and no card grid here: the full
instrument lives on Live (audit A-012)."""
from ui import theme
from ui.components import Button, Caption, Card
from ui.live.readout import Readout

STRIP_TITLE = "Right now"
STRIP_SUBTITLE = "Live from your mic"
READOUT_SIZE = 44               # px: the pitch on the strip
OPEN_LIVE = "Open Live"


class LiveStrip(Card):
    """Right now: Readout + the in-range line + "Open Live". It has the readout's names (note_label,
    note_name) so the lab and the shell treat it like the Live view's readout."""

    def __init__(self, core, go_live, parent=None):
        super().__init__(STRIP_TITLE, subtitle=STRIP_SUBTITLE, padding=theme.L, parent=parent)
        self.core = core
        self.readout = Readout(core, size=READOUT_SIZE)
        self.note_label, self.note_name = self.readout.note_label, self.readout.note_name
        self.add(self.readout)
        self.session_line = Caption("", tone="secondary")
        self.session_line.setWordWrap(True)
        self.add(self.session_line)
        self.open_btn = Button(OPEN_LIVE, variant="secondary", icon="ph.arrow-right")
        self.open_btn.clicked.connect(go_live)
        self.add_action(self.open_btn)

    def show_snapshot(self, frame, snapshot):
        """Draw one live snapshot (the same one the Live screen draws)."""
        self.readout.show_snapshot(snapshot)
        session = snapshot["session"]
        text = session["line"] if session["has_target"] else ""
        if text != self.session_line.text():
            self.session_line.setText(text)
            self.session_line.setVisible(bool(text))

    def set_compact(self, compact):
        """The short-window strip: no subtitle and tighter padding, so the last-take panel under it fits."""
        self.set_subtitle("" if compact else STRIP_SUBTITLE)
        pad = theme.M if compact else theme.L
        self.layout().setContentsMargins(pad, pad, pad, pad)

    def start_replay(self):
        """A replay holds the Live screen's graph; this strip has none."""
