"""The component gallery: every component in its main states with realistic VoiceLab content. It is
also the visual spec for the redesigned screens.

    python -m ui.gallery [--theme dark|light] [--screenshot path.png] [--width 1440]

The sample report content is illustrative. When report.py exists, the pitch and resonance bars
use its ZONES (the cited ranges) instead of the sample ones here.
"""
import argparse
import os
import sys

from PyQt6.QtCore import QEventLoop, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ui import theme
from ui.components import (ActivityCenter, Badge, Banner, BigNumber, Button, Caption, Card, Chip, Collapsible,
                           ConfidenceChip, Divider, EmptyState, FlowGrid, Headline, HelpTip, IconButton, Label,
                           MetricCard, MomentChip, NavRail, ProgressStrip, RecordButton, Section, SegmentedControl,
                           Select, ShareBar, Skeleton, Sparkline, Spinner, StatusDot, Tabs, ThemedWidget,
                           ToastStack, Toggle, WrapRow, ZoneBar)
from ui.components.base import icon, painter, rounded

SETTLE_MS = 700             # ms to let fades and glides finish before a screenshot
# Illustrative zones (not cited; the real ones come from report.ZONES once spec 02 lands).
SAMPLE_ZONES = {
    "pitch": {"label": "Pitch", "unit": "Hz", "scale": (80, 400), "log": True, "zones": [
        {"id": "masculine", "label": "typically masculine", "lo": 80, "hi": 150},
        {"id": "overlap", "label": "overlap / androgynous", "lo": 150, "hi": 220},
        {"id": "feminine", "label": "typically feminine", "lo": 220, "hi": 400}]},
    "resonance": {"label": "Resonance", "unit": "Hz", "scale": (800, 1400), "log": False, "zones": [
        {"id": "masculine", "label": "typically masculine", "lo": 800, "hi": 1000},
        {"id": "overlap", "label": "overlap / androgynous", "lo": 1000, "hi": 1120},
        {"id": "feminine", "label": "typically feminine", "lo": 1120, "hi": 1400}]},
    "weight": {"label": "Vocal weight", "unit": "dB", "scale": (-5, 15), "log": False, "zones": [
        {"id": "masculine", "label": "typically masculine", "lo": -5, "hi": 3},
        {"id": "overlap", "label": "overlap / androgynous", "lo": 3, "hi": 7},
        {"id": "feminine", "label": "typically feminine", "lo": 7, "hi": 15}]},
}
SAMPLE_DIMENSIONS = [
    {"key": "pitch", "name": "Pitch", "value": 205.0, "display": "205", "unit": "Hz", "zone": "overlap",
     "confidence": "high", "reason": "4.1 s of clear voiced speech",
     "headline": "Pitch sits in the overlap zone",
     "explanation": "How high your voice sits on average. It matters, but listeners weigh resonance as much.",
     "details": "Median f0 of voiced frames: 205 Hz (G#3 −12¢). 10–90th percentile 168–247 Hz.",
     "target": (190, 240), "baseline": 165.0, "range": (196, 214),
     "change": {"vs": "previous", "text": "+12 Hz vs last take"}},
    {"key": "resonance", "name": "Resonance", "value": None, "display": None, "unit": "Hz", "zone": None,
     "confidence": "none", "reason": "Signal-to-noise 9 dB; formants need about 20 dB.",
     "headline": "Too much background noise to judge brightness",
     "explanation": "How bright or dark the voice sounds, from the spacing of its formants.",
     "details": "ΔF (formant spacing) needs SNR ≥ 20 dB; this take measured 9 dB."},
    {"key": "weight", "name": "Vocal weight", "value": 9.2, "display": "+9.2", "unit": "dB", "zone": "feminine",
     "confidence": "medium", "reason": "A proxy: H1*–H2* also moves with breathiness.",
     "headline": "Lighter than your baseline",
     "explanation": "How heavy or light the voice feels. A rough proxy; trust trends more than one take.",
     "details": "H1*–H2* (corrected first-minus-second harmonic level): +9.2 dB. Baseline +5.1 dB.",
     "baseline": 5.1, "change": {"vs": "baseline", "text": "lighter than baseline"}},
]
JOBS = [
    {"id": 1, "kind": "analyse", "label": "Analysing “Rainbow passage”", "done": 3, "total": 8, "text": "Measuring formants"},
    {"id": 2, "kind": "reanalyse", "label": "Updating 12 older takes", "done": 5, "total": 12, "text": "Take 5 of 12"},
]
NAV = [("home", "ph.house", "Home"), ("live", "ph.microphone", "Live"), ("takes", "ph.tray", "Takes"),
       ("progress", "ph.chart-line-up", "Progress"), ("practice", "ph.music-notes", "Practice")]
NAV_FOOTER = [("settings", "ph.gear-six", "Settings")]


def zone_spec(key):
    """report.ZONES[key] when spec 02's report module is present, else the sample."""
    try:
        import report
        zones = getattr(report, "ZONES", {})
        real = {"resonance": zones.get("delta_f") or zones.get("resonance"), "weight": zones.get("h1h2") or
                zones.get("weight"), "pitch": zones.get("pitch")}.get(key)
        if real and real.get("zones"):
            return real
    except Exception:
        pass
    return SAMPLE_ZONES[key]


class _Swatch(ThemedWidget):
    def __init__(self, key, label=None, getter=None):
        super().__init__()
        self.key, self.label, self.getter = key, label or key, getter
        self.setFixedSize(112, 78)

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        value = self.getter(t) if self.getter else t[self.key]
        rounded(p, QRectF(0, 0, self.width(), 40), theme.RADIUS["medium"], fill=QColor(value), border=t.q("border"))
        p.setFont(theme.font("caption", weight=600))
        p.setPen(t.q("text_secondary"))
        p.drawText(QRectF(0, 44, self.width(), 16), int(Qt.AlignmentFlag.AlignLeft), self.label)
        p.setFont(theme.font("caption", size=11))
        p.setPen(t.q("text_muted"))
        p.drawText(QRectF(0, 60, self.width(), 16), int(Qt.AlignmentFlag.AlignLeft), value)


def _row(*widgets, spacing=theme.M, stretch=True):
    row = QHBoxLayout()
    row.setSpacing(spacing)
    for w in widgets:
        if isinstance(w, int):
            row.addSpacing(w)
        else:
            row.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
    if stretch:
        row.addStretch(1)
    return row


def _holder(layout):
    w = QWidget()
    w.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    return w


def colour_section():
    sec = Section("Colour and type", help="Tokens from ui/theme.py. Every text pairing meets WCAG AA.")
    grid = FlowGrid(min_column_width=420, spacing=theme.L)
    tokens = Card("Surfaces, ink and state")
    flow = FlowGrid(min_column_width=112, spacing=theme.S)
    for key in ("bg", "surface", "surface_raised", "border", "text", "text_secondary", "text_muted", "accent",
                "success", "warning", "error", "info", "on_target", "off_target", "recording"):
        flow.add(_Swatch(key))
    tokens.add(flow)
    grid.add(tokens)
    zones = Card("Zones, registers, series", "Zones always carry a text label; colour is the second cue.")
    flow = FlowGrid(min_column_width=112, spacing=theme.S)
    for zid in ("masculine", "overlap", "feminine"):
        flow.add(_Swatch(zid, f"{zid} fill", lambda t, z=zid: t.zone(z)["fill"]))
    for zid in ("masculine", "overlap", "feminine"):
        flow.add(_Swatch(zid, f"{zid} edge", lambda t, z=zid: t.zone(z)["edge"]))
    for reg in ("chest", "mix", "head", "fry"):
        flow.add(_Swatch(reg, reg, lambda t, r=reg: t.register[r]))
    for s in ("pitch", "f1", "f2", "f3", "compare"):
        flow.add(_Swatch(s, s, lambda t, k=s: t.series[k]))
    zones.add(flow)
    grid.add(zones)
    type_card = Card("Type scale", "Segoe UI Variable / Segoe UI; numbers in tabular figures where they line up.")
    for role, text in (("display", "Your voice today"), ("title", "Pitch sits in the overlap zone"),
                       ("heading", "Resonance is still darker than your target"),
                       ("body", "How bright or dark the voice sounds, from the spacing of its formants."),
                       ("caption", "Recorded 23 Oct, 09:21 · 41 s · speech"), ("numeric", "205 Hz   1 012 Hz   +9.2 dB")):
        type_card.add(_holder(_row(Label(role, role="caption", tone="muted"), 8, Label(text, role=role))))
    grid.add(type_card, span=-1)
    sec.add(grid)
    return sec


def report_section():
    sec = Section("Report", help="A take's report: a big plain headline first, then one card per dimension.")
    hero = Card()
    hero.add(Label("Overall impression", role="label", tone="muted"))
    hero.add(Headline("Androgynous, leaning feminine", level="display"))
    hero.add(Label("Pitch is in the overlap zone, near the feminine side; resonance couldn't be judged in this "
                   "take, so this is a rough read.", role="body", tone="secondary", wrap=True))
    overall = ZoneBar(scale=(0, 1), zones=[
        {"id": "masculine", "label": "typically masculine", "lo": 0, "hi": 0.4},
        {"id": "overlap", "label": "overlap / androgynous", "lo": 0.4, "hi": 0.6},
        {"id": "feminine", "label": "typically feminine", "lo": 0.6, "hi": 1}], ticks=False)
    overall.set_value(0.57, animate=False)
    overall.set_range((0.46, 0.68))
    hero.add(overall)
    hero.add(_holder(_row(ConfidenceChip("low", "Only pitch and weight could be judged."),
                          Caption("These are population tendencies with wide overlap, not rules."))))
    sec.add(hero)
    pitch = Card("Pitch on its zones", "205 Hz in the overlap zone · target 190–240 Hz (green) · baseline caret "
                 "· whisker = the take's spread")
    bar = ZoneBar.from_spec(zone_spec("pitch"))
    bar.set_value(205, animate=False)
    bar.set_target((190, 240))
    bar.set_baseline(165)
    bar.set_range((196, 214))
    pitch.add(bar)
    live = ZoneBar.from_spec(zone_spec("pitch"), compact=True)
    live.set_value(212, animate=False)
    live.set_target((190, 240))
    pitch.add(_holder(_row(Caption("Compact (live meters)"), stretch=False)))
    pitch.add(live)
    plain = ZoneBar(scale=(8, 30), ticks=False)
    plain.set_end_labels("Breathy", "Clear")
    plain.set_value(21.5, animate=False)
    pitch.add(_holder(_row(Caption("Without zones, with end labels"), stretch=False)))
    pitch.add(plain)
    sec.add(pitch)
    cards = FlowGrid(min_column_width=340, spacing=theme.L)
    for dim in SAMPLE_DIMENSIONS:
        card = MetricCard(dim["name"], zone_spec(dim["key"]))
        card.set_dimension(dim)
        cards.add(card)
    sec.add(cards)
    stats = FlowGrid(min_column_width=200, spacing=theme.L)
    stats.add(BigNumber("205", "Hz", "median pitch, voiced speech"))
    stats.add(BigNumber("7.4", "st", "intonation range (10–90%)"))
    stats.add(BigNumber("3:12", "", "practised today"))
    shares = Card("Register", "Share of voiced time")
    shares.add(ShareBar({"chest": 0.62, "mix": 0.30, "head": 0.08}))
    stats.add(shares)
    trend = Card("Pitch, last 12 sessions", "Median Hz; green band = target")
    trend.add(Sparkline([168, 172, None, 181, 179, 188, 192, 186, 197, 201, 199, 205], target=(190, 240), height=44))
    stats.add(trend)
    chips = Card("Chips and status")
    chips.add(WrapRow([ConfidenceChip(level, f"Example: {level}") for level in ("high", "medium", "low", "none")]))
    chips.add(WrapRow([Chip("Overlap", "overlap", dot=True), Chip("Feminine", "feminine", dot=True),
                       Chip("Masculine", "masculine", dot=True), Chip("On target", "on_target", icon="ph.check-circle"),
                       Chip("Speech"), Chip("New", "accent"), Badge("3"), Badge("New", "neutral")]))
    chips.add(WrapRow([StatusDot("ok", "Mic ready"), StatusDot("busy", "Analysing"), StatusDot("warn", "Quiet room?"),
                       StatusDot("error", "Clipping")], spacing=theme.L))
    sec.add(stats)
    sec.add(chips)
    return sec


def tips_section():
    sec = Section("Tips for next time", help="Each tip points at moments in this take; click one to hear it.")
    card = Card("Next take", "From this take's pitch track")
    tips = [("ph.arrow-down-right", "Your pitch dropped to about 110 Hz at the ends of sentences. Next take, keep the "
             "last word up.", [(4.2, None), (9.8, None)]),
            ("ph.sparkle", "Your brightest stretch was here. Replay it and notice how it felt.", [(3.1, 4.0)])]
    for ic, text, moments in tips:
        row = QGridLayout()
        row.setHorizontalSpacing(theme.M)
        row.setVerticalSpacing(theme.S)
        glyph = QLabel()
        glyph.setPixmap(icon(ic, "accent").pixmap(20, 20))
        row.addWidget(glyph, 0, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(Label(text, role="body", wrap=True), 0, 1)
        row.addWidget(WrapRow([MomentChip(start, end) for start, end in moments]), 1, 1)
        row.setColumnStretch(1, 1)
        card.add(_holder(row))
    more = Collapsible("What couldn't be judged", key="gallery.couldnt")
    more.add(Label("Resonance: too much background noise (9 dB SNR).", role="caption", tone="secondary", wrap=True))
    card.add(Divider())
    card.add(more)
    sec.add(card)
    return sec


def controls_section():
    sec = Section("Controls")
    grid = FlowGrid(min_column_width=460, spacing=theme.L)
    buttons = Card("Buttons", "One primary action per view; the rest step down")
    loading = Button("Analysing…", "primary")
    loading.set_loading(True)
    disabled = Button("Export", "secondary", icon="ph.export")
    disabled.setEnabled(False)
    buttons.add(_holder(_row(Button("Record a take", "primary", icon="ph.microphone-fill"),
                             Button("Import audio…", "secondary", icon="ph.file-arrow-down"),
                             Button("Compare with…", "ghost", icon="ph.arrows-left-right"), spacing=theme.S)))
    buttons.add(_holder(_row(Button("Delete take", "danger", icon="ph.trash"), loading, disabled, spacing=theme.S)))
    buttons.add(_holder(_row(IconButton("ph.play-fill", "Play"), IconButton("ph.flag", "Set as baseline"),
                             IconButton("ph.target", "Set target"), IconButton("ph.dots-three", "More", "secondary"),
                             Button("Small", "secondary", small=True), Button("Small primary", "primary", small=True),
                             spacing=theme.S)))
    grid.add(buttons)
    choice = Card("Choices")
    choice.add(_holder(_row(SegmentedControl([("speak", "Speak", "ph.chat-circle-text"), ("sing", "Sing", "ph.music-notes")]),
                            SegmentedControl(["Week", "Month", "All"], current="Month"))))
    tabs = Tabs([("overview", "Overview"), ("pitch", "Pitch"), ("resonance", "Resonance"), ("tips", "Tips")])
    tabs.set_badge("tips", 2)
    choice.add(tabs)
    choice.add(_holder(_row(Toggle(True, "Show target bands"), Toggle(False, "Reduce motion"))))
    choice.add(_holder(_row(Label("Microphone", role="body", tone="secondary"),
                            Select([("usb", "USB Audio (Shure MV7)"), ("built", "Built-in microphone")]),
                            HelpTip("Pick the mic you'll practise with. A mic check sets its noise floor."))))
    grid.add(choice)
    record = Card("Record button", "Idle · recording (pulse and time) · analysing · disabled")
    states = []
    for state in ("idle", "recording", "analysing", "disabled"):
        b = RecordButton()
        b.set_state(state)
        if state == "recording":
            b.set_elapsed(12.4)
        states.append(b)
    record.add(_holder(_row(*states, spacing=theme.XL)))
    grid.add(record, span=-1)
    sec.add(grid)
    return sec


def feedback_section():
    sec = Section("Activity and feedback", help="Anything that takes longer than a moment shows that it is working.")
    grid = FlowGrid(min_column_width=420, spacing=theme.L)
    activity = Card("Activity center", "Expanded, and collapsed to a spinner and a count")
    center = ActivityCenter(expanded=True)
    center.set_jobs(JOBS)
    small = ActivityCenter(expanded=False)
    small.set_jobs(JOBS)
    idle = ActivityCenter(expanded=False)
    activity.add(center)
    activity.add(_holder(_row(small, idle)))
    grid.add(activity)
    progress = Card("Progress and loading")
    strip = ProgressStrip()
    strip.set_progress(6, 10)
    busy = ProgressStrip()
    busy.set_indeterminate()
    progress.add(_holder(_row(Spinner(16), Spinner(24), Spinner(32), Label("Loading takes…", tone="secondary"))))
    progress.add(Caption("Determinate"))
    progress.add(strip)
    progress.add(Caption("Indeterminate"))
    progress.add(busy)
    progress.add(Caption("Skeletons while a report loads"))
    progress.add(_holder(_row(Skeleton("circle", height=40), _holder(_skeleton_lines()), stretch=False)))
    progress.add(Skeleton("block", height=72))
    grid.add(progress)
    toasts = Card("Toasts", "Stacked, auto-dismiss, optional action")
    stack = ToastStack(overlay=False)
    stack.show_toast("Take saved: “Rainbow passage”", "success", timeout_ms=0)
    stack.show_toast("The mic is quiet; readings may be less reliable.", "warning", action="Mic check", timeout_ms=0)
    stack.show_toast("Couldn't read that file.", "error", action="Details", timeout_ms=0)
    stack.show_toast("Updating 12 older takes in the background.", "info", timeout_ms=0)
    toasts.add(stack)
    grid.add(toasts)
    states = Card("Banners and empty states")
    states.add(Banner("Too noisy to judge brightness · 3 readings need a quieter room", "warning", action="Run mic check"))
    states.add(Banner("Re-analysis finished: 12 takes updated", "success", dismissible=True))
    states.add(EmptyState("ph.microphone", "No takes yet", action="Record your first take",
                          detail="Read the passage aloud; your report appears here."))
    grid.add(states)
    sec.add(grid)
    return sec


def _skeleton_lines():
    box = QVBoxLayout()
    box.addWidget(Skeleton("text", lines=2))
    return box


class Gallery(QWidget):
    """The whole gallery: a nav rail and a scrolling column of sections."""

    def __init__(self, scroll=True):
        super().__init__()
        self.setAutoFillBackground(True)
        self.setWindowTitle("VoiceLab components")
        self.setPalette(theme.palette())
        theme.changed.connect(self._restyle)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.rail = NavRail(NAV, NAV_FOOTER, current="home")
        self.rail.set_badge("takes", 3)
        rail_holder = QVBoxLayout()
        rail_holder.setContentsMargins(0, theme.S, 0, theme.S)
        rail_holder.addWidget(self.rail)
        root.addLayout(rail_holder)
        root.addWidget(Divider(vertical=True))
        self.content = QWidget()
        col = QVBoxLayout(self.content)
        col.setContentsMargins(theme.XXL, theme.XL, theme.XXL, theme.XXL)
        col.setSpacing(theme.XXL)
        head = QGridLayout()
        head.setHorizontalSpacing(theme.L)
        head.addWidget(Headline("VoiceLab components", level="display"), 0, 0)
        head.addWidget(Label("Calm, plain, and always showing when it's working. Every component below "
                             "follows the theme live.", role="body", tone="secondary", wrap=True), 1, 0)
        self.theme_switch = SegmentedControl([("dark", "Dark", "ph.moon"), ("light", "Light", "ph.sun")],
                                             current=theme.current().name)
        self.theme_switch.changed.connect(theme.set_theme)
        motion = Toggle(theme.reduce_motion, "Reduce motion")
        motion.toggled.connect(theme.set_reduce_motion)
        head.addLayout(_row(self.theme_switch, motion, stretch=False), 0, 1, 2, 1, Qt.AlignmentFlag.AlignRight)
        head.setColumnStretch(0, 1)
        col.addLayout(head)
        for build in (report_section, tips_section, controls_section, feedback_section, colour_section):
            col.addWidget(build())
        col.addStretch(1)
        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QScrollArea.Shape.NoFrame)
            area.setWidget(self.content)
            root.addWidget(area, 1)
        else:
            root.addWidget(self.content, 1)
        self.toasts = ToastStack(self)

    def _restyle(self, *_):
        self.setPalette(theme.palette())
        self.theme_switch.set_current(theme.current().name)


def settle(ms=SETTLE_MS):
    """Run the event loop for a while so layouts settle and animations reach their end."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def render(path, width=1440, name=None):
    """Render the full gallery (no scrolling) to a PNG; returns its (width, height)."""
    if name:
        theme.set_theme(name)
    gallery = Gallery(scroll=False)
    gallery.resize(width, 800)
    gallery.show()
    settle(100)
    height = gallery.content.layout().totalHeightForWidth(gallery.content.width()) if \
        gallery.content.layout().hasHeightForWidth() else gallery.content.sizeHint().height()
    gallery.resize(width, max(800, height))
    settle()
    gallery.resize(width, max(800, gallery.content.sizeHint().height()))
    settle(200)
    image = gallery.grab()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    image.save(path)
    size = (image.width(), image.height())
    gallery.close()
    gallery.deleteLater()
    return size


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--theme", choices=("dark", "light", "system"), default="dark")
    parser.add_argument("--screenshot", help="render the gallery to this PNG and exit")
    parser.add_argument("--width", type=int, default=1440)
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    theme.set_theme(args.theme)
    theme.apply(app)
    if args.screenshot:
        print(render(args.screenshot, args.width))
        return 0
    gallery = Gallery()
    gallery.resize(args.width, 900)
    gallery.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
