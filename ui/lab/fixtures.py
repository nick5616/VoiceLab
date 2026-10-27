"""Tiny Qt fixtures for the eval-of-evals: each builds a small window with one seeded defect (or its clean twin)
that a check in tools/uilab/evals.py must catch (or pass). Class and attribute names mirror the real ones
(LiveView.note_label, LiveCard, LatestTakePanel, ...), so the inspector reads them as it reads the app."""
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from tools.uilab.evals import Capture
from ui import theme
from ui.components import ActivityCenter, Button, ElidedLabel, Label, RecordButton, ZoneBar
from ui.lab.capture import settle, to_array
from ui.lab.inspector import Inspector

GREY_ON_GREY = ("#8a8a8a", "#4a4a4a")       # fg, bg: about 2.4:1 (the "3:1 grey-on-grey" seeded label)


class Shell(QWidget):
    """A stand-in main window: its attributes name the paths (dashboard/live/note_label, ...)."""

    def __init__(self, w=640, h=400):
        super().__init__()
        self.setFixedSize(w, h)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(8, 8, 8, 8)
        self.setStyleSheet(f"background: {theme.current()['surface']};")


class LiveCard(QWidget):
    def __init__(self, key, value):
        super().__init__()
        self.key, self.unavailable, self.verdict = key, None, None
        row = QHBoxLayout(self)
        self.name = Label("Pitch", role="label")
        self.value = Label(value, role="numeric")
        self.note, self.low, self.high = Label(""), Label("Low"), Label("High")
        for w in (self.name, self.value):
            row.addWidget(w)


class LiveView(QWidget):
    def __init__(self, readout, card, now_label=False):
        super().__init__()
        box = QVBoxLayout(self)
        if now_label:
            self.now = Label("Now", role="label")
            box.addWidget(self.now)
        self.note_label = Label(readout, role="figure")
        box.addWidget(self.note_label)
        self.cards = QWidget()
        self.cards.cards = {"pitch": LiveCard("pitch", card)}
        QVBoxLayout(self.cards).addWidget(self.cards.cards["pitch"])
        box.addWidget(self.cards)


class LatestTakePanel(QWidget):
    def __init__(self, text, titled=False):
        super().__init__()
        box = QVBoxLayout(self)
        if titled:
            self.title_label = Label("Latest take", role="heading")
            box.addWidget(self.title_label)
        self.tip = Label(text, role="body", wrap=True)
        box.addWidget(self.tip)


def capture(window, screen="dashboard", state=None, name="fixture"):
    """Show the fixture, grab it and dump it as the lab would: an evals.Capture."""
    window.show()
    settle()
    pixels = to_array(window.grab())
    tree = Inspector(window).tree(pixels)
    size = (window.width(), window.height())
    meta = {"size": list(size), "screen": screen, "dialog": None, "full": []}
    st = dict(state or {}, screen=screen)
    return Capture("fixture", name, theme.current().name, size, name, {"meta": meta, "widgets": tree}, st)


# --- the seeded defects: (window, screen, state) builders; clean=True builds the twin without the defect ---

def elided_label(clean=False):
    w = Shell()
    w.status = ElidedLabel("Re-analysing your takes with the improved analysis: 1 of 11…", role="body")
    w.status.setFixedWidth(600 if clean else 160)
    w.box.addWidget(w.status)
    w.box.addStretch(1)
    return w, "dashboard", None


def overlapping_buttons(clean=False):
    w = Shell()
    w.first = QPushButton("Import", w)
    w.second = QPushButton("Record", w)
    w.first.setGeometry(20, 20, 120, 34)
    w.second.setGeometry(160 if clean else 90, 20, 120, 34)
    return w, "dashboard", None


def grey_on_grey(clean=False):
    w = Shell()
    w.label = QLabel("Clarity can't be judged right now")
    fg, bg = ("#ffffff", "#202020") if clean else GREY_ON_GREY
    w.label.setStyleSheet(f"color: {fg}; background: {bg}; padding: 6px;")
    w.label.tone = None
    w.box.addWidget(w.label)
    w.box.addStretch(1)
    return w, "dashboard", None


def readout_disagrees(clean=False):
    w = Shell()
    w.dashboard = QWidget()
    w.dashboard.live = LiveView("210 Hz", "210 Hz" if clean else "196 Hz")
    QVBoxLayout(w.dashboard).addWidget(w.dashboard.live)
    w.box.addWidget(w.dashboard)
    return w, "dashboard", None


def unlabelled_pair(clean=False):
    w = Shell()
    w.dashboard = QWidget()
    w.dashboard.live = LiveView("210 Hz", "210 Hz", now_label=clean)
    w.dashboard.latest = LatestTakePanel("Your pitch sat around 146 Hz, in the overlap zone.", titled=clean)
    row = QHBoxLayout(w.dashboard)
    row.addWidget(w.dashboard.live)
    row.addWidget(w.dashboard.latest)
    w.box.addWidget(w.dashboard)
    return w, "dashboard", None


def missing_spinner(clean=False):
    w = Shell()
    w.action_bar = QWidget()
    w.action_bar.record_btn = RecordButton(56)
    w.action_bar.record_btn.set_state("analysing" if clean else "idle")
    QHBoxLayout(w.action_bar).addWidget(w.action_bar.record_btn)
    w.box.addWidget(w.action_bar)
    state = {"analyzing": True, "record_state": "analysing", "jobs": [], "recording": False}
    return w, "dashboard", state


def nothing_running(clean=False):
    w = Shell()
    w.top = QWidget()
    w.top.activity = ActivityCenter(expanded=True)
    job = {"id": 1, "kind": "reanalysis", "label": "Re-analysing your takes", "done": 0, "total": 1}
    w.top.activity.set_jobs([job] if clean else [])
    QHBoxLayout(w.top).addWidget(w.top.activity)
    w.box.addWidget(w.top)
    w.box.addStretch(1)
    return w, "dashboard", {"jobs": [job]}


def delta_f_label(clean=False):
    w = Shell()
    w.name = Label("Brightness (resonance)" if clean else "Resonance ΔF", role="label")
    w.box.addWidget(w.name)
    w.box.addStretch(1)
    return w, "dashboard", None


def scrolls_at_1080(clean=False):
    w = Shell(1920, 1080)
    w.dashboard = QWidget()
    w.dashboard.scroll = QScrollArea()
    w.dashboard.scroll.setWidgetResizable(True)
    inner = QWidget()
    inner.setMinimumHeight(600 if clean else 1600)
    w.dashboard.scroll.setWidget(inner)
    QVBoxLayout(w.dashboard).addWidget(w.dashboard.scroll)
    w.box.addWidget(w.dashboard)
    return w, "dashboard", None


def small_target(clean=False):
    w = Shell()
    w.close_btn = Button("x", variant="ghost", small=True)
    w.close_btn.setFixedSize(40 if clean else 18, 34 if clean else 18)
    w.box.addWidget(w.close_btn)
    w.box.addStretch(1)
    return w, "dashboard", None


def bands_without_contrast(clean=False):
    w = Shell()
    w.bar = ZoneBar((0, 10), [{"id": "masculine", "label": "low", "lo": 0, "hi": 5},
                              {"id": "feminine", "label": "high", "lo": 5, "hi": 10}])
    w.box.addWidget(w.bar)
    w.box.addStretch(1)
    if clean:
        w.setStyleSheet("background: #ffffff;")
    return w, "dashboard", None


def dead_end(clean=False):
    """A button that should open a panel; seeded, it does nothing (for the reachability check)."""
    w = Shell()
    w.open_btn = QPushButton("Open the panel")
    w.panel = Label("The panel", role="body")
    w.panel.hide()
    if clean:
        w.open_btn.clicked.connect(w.panel.show)
    w.box.addWidget(w.open_btn)
    w.box.addWidget(w.panel)
    w.box.addStretch(1)
    return w, "dashboard", None


DEAD_END_FEATURE = {"id": "fixture_panel", "name": "The fixture's panel", "steps": [("click", "open_btn")],
                    "target": {"visible": "panel"}}


SEEDED = {                          # check -> fixture that seeds a defect for it
    "truncation": elided_label,
    "overlap": overlapping_buttons,
    "contrast": grey_on_grey,
    "consistency": readout_disagrees,
    "unlabelled_pair": unlabelled_pair,
    "state:spinner": missing_spinner,
    "state:nothing_running": nothing_running,
    "jargon": delta_f_label,
    "fold": scrolls_at_1080,
    "small_target": small_target,
}


def run(name, clean=False):
    """The Capture of one seeded fixture (or its clean twin)."""
    window, screen, state = SEEDED[name](clean)
    try:
        return capture(window, screen, state, name=f"{name}{'-clean' if clean else ''}")
    finally:
        window.close()
        window.deleteLater()
        settle()
