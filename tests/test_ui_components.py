"""The design system (spec 04): theme tokens and contrast, motion helpers, the component kit and
the gallery, offscreen."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), "..")]
from PyQt6.QtCore import QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from ui import anim, gallery, theme  # noqa: E402
from ui import components as c  # noqa: E402

PITCH = {"unit": "Hz", "scale": (80, 400), "log": True, "zones": [
    {"id": "masculine", "label": "typically masculine", "lo": 80, "hi": 150},
    {"id": "overlap", "label": "overlap / androgynous", "lo": 150, "hi": 220},
    {"id": "feminine", "label": "typically feminine", "lo": 220, "hi": 400}]}
JOBS = [{"id": 1, "kind": "analyse", "label": "Analysing take", "done": 3, "total": 8, "text": "Formants"},
        {"id": 2, "kind": "reanalyse", "label": "Updating 12 older takes", "done": 5, "total": 12, "text": ""}]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def reset_theme(app):
    theme.set_theme("dark")
    theme.set_reduce_motion(False)
    yield
    theme.set_theme("dark")
    theme.set_reduce_motion(False)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def dim(confidence="high", **extra):
    out = {"key": "pitch", "name": "Pitch", "value": 205.0, "display": "205 Hz", "unit": "Hz", "zone": "overlap",
           "confidence": confidence, "reason": "clear speech", "headline": "Pitch sits in the overlap zone",
           "explanation": "How high your voice sits.", "details": "Median f0 205 Hz.", "target": (190, 240),
           "baseline": 165.0, "change": None}
    out.update(extra)
    return out


# --- every component, both themes ---

def factories():
    """(name, make) for every component, each in a typical state."""
    def metric():
        card = c.MetricCard("Pitch", PITCH)
        card.set_dimension(dim())
        return card

    def zonebar():
        bar = c.ZoneBar.from_spec(PITCH)
        bar.set_value(205, animate=False)
        bar.set_target((190, 240))
        return bar

    def activity():
        a = c.ActivityCenter()
        a.set_jobs(JOBS)
        return a

    def record():
        b = c.RecordButton()
        b.set_state("recording")
        return b

    def stack():
        s = c.ToastStack(overlay=False)
        s.show_toast("Take saved", "success", timeout_ms=0)
        return s

    def card():
        k = c.Card("Title", "Subtitle")
        k.add(c.Label("Body"))
        return k

    def section():
        s = c.Section("Heading", help="Plain help")
        s.add(c.Label("Body"))
        return s

    def collapsible():
        k = c.Collapsible("Details", expanded=True)
        k.add(c.Label("Jargon"))
        return k

    def grid():
        g = c.FlowGrid(min_column_width=120)
        for i in range(3):
            g.add(c.Card(f"Card {i}"))
        return g

    return [
        ("Card", card), ("Section", section), ("Collapsible", collapsible), ("FlowGrid", grid),
        ("WrapRow", lambda: c.WrapRow([c.Chip("a"), c.Chip("b")])), ("Divider", c.Divider),
        ("Headline", lambda: c.Headline("Your voice today", "display")), ("Caption", lambda: c.Caption("Recorded")),
        ("HelpTip", lambda: c.HelpTip("Plain help")), ("ElidedLabel", lambda: c.ElidedLabel("A long line " * 9)),
        ("ZoneBar", zonebar), ("MetricCard", metric), ("BigNumber", lambda: c.BigNumber("205", "Hz", "median")),
        ("ShareBar", lambda: c.ShareBar({"chest": 0.6, "mix": 0.3, "head": 0.1})),
        ("Sparkline", lambda: c.Sparkline([1, 3, None, 2, 4], target=(2, 3))),
        ("ConfidenceChip", lambda: c.ConfidenceChip("medium", "why")), ("Chip", lambda: c.Chip("Overlap", "overlap", dot=True)),
        ("Badge", lambda: c.Badge("3")), ("StatusDot", lambda: c.StatusDot("ok", "Mic ready")),
        ("Button", lambda: c.Button("Record", "primary", icon="ph.microphone-fill")),
        ("IconButton", lambda: c.IconButton("ph.play-fill", "Play", "secondary")),
        ("SegmentedControl", lambda: c.SegmentedControl(["Speak", "Sing"])),
        ("Tabs", lambda: c.Tabs(["Overview", "Pitch"])), ("RecordButton", record),
        ("NavRail", lambda: c.NavRail(gallery.NAV, gallery.NAV_FOOTER)), ("Toggle", lambda: c.Toggle(True, "On")),
        ("Select", lambda: c.Select([("a", "First"), ("b", "Second")])), ("Spinner", c.Spinner),
        ("ProgressStrip", c.ProgressStrip), ("ActivityCenter", activity), ("Skeleton", lambda: c.Skeleton("block")),
        ("Toast", lambda: c.Toast("Saved", "success", action="Undo", timeout_ms=0)), ("ToastStack", stack),
        ("Banner", lambda: c.Banner("Too noisy to judge brightness", "warning", action="Run mic check")),
        ("EmptyState", lambda: c.EmptyState("ph.microphone", "No takes yet", action="Record")),
        ("MomentChip", lambda: c.MomentChip(3.1, 4.0)),
    ]


def test_every_exported_component_has_a_factory():
    names = {name for name, _ in factories()}
    assert set(c.__all__) - {"Label", "ThemedWidget"} == names


@pytest.mark.parametrize("name,make", factories(), ids=[n for n, _ in factories()])
def test_component_builds_in_both_themes_and_restyles_live(app, name, make):
    theme.set_reduce_motion(True)           # still frames, so the two renders differ only by theme
    for start in ("light", "dark"):
        theme.set_theme(start)
        widget = make()
        widget.resize(max(widget.sizeHint().width(), 360), max(widget.sizeHint().height(), 40))
        widget.show()
        app.processEvents()
        before = widget.grab().toImage()
        theme.set_theme("dark" if start == "light" else "light")
        app.processEvents()
        after = widget.grab().toImage()
        assert before != after, f"{name} didn't restyle on a theme switch from {start}"
        widget.close()


def test_deleted_components_leave_no_dangling_hooks(app):
    """Theme switches and running animations must not call into widgets Qt has deleted (an
    exception in a slot aborts PyQt)."""
    from PyQt6.QtCore import QCoreApplication, QEvent
    host = QWidget()
    for _, make in factories():
        make().setParent(host)
    host.show()
    app.processEvents()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    theme.set_theme("light")
    wait(150)
    theme.set_theme("dark")


# --- theme ---

def test_set_theme_switches_tokens_and_fires_changed(app):
    seen = []
    theme.changed.connect(seen.append)
    try:
        assert theme.set_theme("light").name == "light"
        assert theme.current()["bg"] == theme.LIGHT["bg"]
        theme.set_theme("light")                    # no change, no signal
        theme.set_theme("dark")
    finally:
        theme.changed.disconnect(seen.append)
    assert seen == ["light", "dark"]
    with pytest.raises(ValueError):
        theme.set_theme("sepia")


def test_system_theme_resolves_to_a_real_theme(app):
    assert theme.set_theme("system").name in ("dark", "light")


def test_tokens_are_complete_in_both_themes():
    keys = {"bg", "surface", "surface_raised", "border", "text", "text_secondary", "text_muted", "accent",
            "accent_text", "success", "warning", "error", "info", "on_target", "off_target", "recording"}
    for name in theme.THEMES:
        t = theme.Theme(name)
        assert keys <= set(t.colors)
        for zone in ("feminine", "overlap", "masculine"):
            assert set(t.zone(zone)) == {"fill", "edge", "text"}
        assert set(t.register) == {"chest", "mix", "head", "fry"}
        assert {"pitch", "f1", "f2", "f3", "compare"} <= set(t.series)
    assert theme.SPACE == (4, 8, 12, 16, 24, 32)
    assert {"display", "title", "heading", "body", "caption", "numeric"} <= set(theme.TYPE)


def test_qss_uses_the_current_tokens():
    assert theme.DARK["surface_raised"] in theme.qss()
    theme.set_theme("light")
    assert theme.LIGHT["surface_raised"] in theme.qss()


def test_numeric_font_has_tabular_figures(app):
    f = theme.font("numeric")
    assert f.pixelSize() == theme.TYPE["numeric"][0]
    if hasattr(f, "featureValue"):
        from PyQt6.QtGui import QFont
        assert f.featureValue(QFont.Tag("tnum")) == 1


# --- contrast (WCAG 2.1: 4.5:1 body text, 3:1 large text and UI parts) ---

def luminance(hexcolor):
    h = hexcolor.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# (foreground, background, required, computed). "zone:<id>:<part>" and "series:<key>" name the
# zone and series tokens; "badge:<fill>" is a Badge's ink on that fill.
CONTRAST = {
    "dark": [
        ("text", "bg", 4.5, 15.72), ("text", "surface", 4.5, 14.25), ("text", "surface_raised", 4.5, 12.37),
        ("text_secondary", "bg", 4.5, 9.61), ("text_secondary", "surface", 4.5, 8.71),
        ("text_secondary", "surface_raised", 4.5, 7.56),
        ("text_muted", "bg", 4.5, 6.41), ("text_muted", "surface", 4.5, 5.81), ("text_muted", "surface_raised", 4.5, 5.04),
        ("accent_text", "accent", 4.5, 6.43), ("text", "accent_soft", 4.5, 11.53), ("error", "surface", 4.5, 5.59),
        ("accent", "surface", 3.0, 5.80), ("success", "surface", 3.0, 7.28), ("warning", "surface", 3.0, 8.25),
        ("info", "surface", 3.0, 7.86), ("on_target", "surface", 3.0, 7.28), ("off_target", "surface", 3.0, 7.57),
        ("recording", "surface", 3.0, 5.28), ("border_strong", "surface", 3.0, 3.53),
        ("badge:success", "success", 4.5, 8.03), ("badge:warning", "warning", 4.5, 9.10),
        ("badge:error", "error", 4.5, 6.17), ("badge:info", "info", 4.5, 8.67),
        ("badge:text_secondary", "text_secondary", 4.5, 9.61),
        ("zone:masculine:text", "zone:masculine:fill", 4.5, 5.54), ("zone:overlap:text", "zone:overlap:fill", 4.5, 9.46),
        ("zone:feminine:text", "zone:feminine:fill", 4.5, 5.69),
        ("zone:masculine:edge", "surface", 3.0, 4.98), ("zone:overlap:edge", "surface", 3.0, 9.12),
        ("zone:feminine:edge", "surface", 3.0, 4.86),
        ("series:pitch", "zone:masculine:fill", 3.0, 5.78), ("series:pitch", "zone:feminine:fill", 3.0, 6.02),
    ],
    "light": [
        ("text", "bg", 4.5, 14.96), ("text", "surface", 4.5, 16.37), ("text", "surface_raised", 4.5, 17.25),
        ("text_secondary", "bg", 4.5, 7.29), ("text_secondary", "surface", 4.5, 7.98),
        ("text_secondary", "surface_raised", 4.5, 8.41),
        ("text_muted", "bg", 4.5, 5.02), ("text_muted", "surface", 4.5, 5.50), ("text_muted", "surface_raised", 4.5, 5.79),
        ("accent_text", "accent", 4.5, 6.01), ("text", "accent_soft", 4.5, 13.83), ("error", "surface", 4.5, 4.90),
        ("accent", "surface", 3.0, 5.71), ("success", "surface", 3.0, 4.76), ("warning", "surface", 3.0, 4.75),
        ("info", "surface", 3.0, 5.15), ("on_target", "surface", 3.0, 4.76), ("off_target", "surface", 3.0, 4.83),
        ("recording", "surface", 3.0, 4.31), ("border_strong", "surface", 3.0, 3.10),
        ("badge:success", "success", 4.5, 5.01), ("badge:warning", "warning", 4.5, 5.00),
        ("badge:error", "error", 4.5, 5.16), ("badge:info", "info", 4.5, 5.42),
        ("badge:text_secondary", "text_secondary", 4.5, 8.41),
        ("zone:masculine:text", "zone:masculine:fill", 4.5, 7.52), ("zone:overlap:text", "zone:overlap:fill", 4.5, 9.57),
        ("zone:feminine:text", "zone:feminine:fill", 4.5, 7.12),
        ("zone:masculine:edge", "surface", 3.0, 5.06), ("zone:overlap:edge", "surface", 3.0, 9.64),
        ("zone:feminine:edge", "surface", 3.0, 4.54),
        ("series:pitch", "zone:masculine:fill", 3.0, 9.96), ("series:pitch", "zone:feminine:fill", 3.0, 9.02),
    ],
}


def token(t, name):
    if name.startswith("zone:"):
        _, zone, part = name.split(":")
        return t.zone(zone)[part]
    if name.startswith("series:"):
        return t.series[name.split(":")[1]]
    if name.startswith("badge:"):
        return t["bg" if t.dark else "surface_raised"]       # chips.paint_badge's ink on a status fill
    return t[name]


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_text_and_ui_colours_meet_wcag_aa(mode):
    t = theme.Theme(mode)
    for fg, bg, required, recorded in CONTRAST[mode]:
        ratio = contrast(token(t, fg), token(t, bg))
        assert ratio >= required, f"{mode}: {fg} on {bg} is {ratio:.2f}:1, needs {required}:1"
        assert ratio == pytest.approx(recorded, abs=0.01), f"{mode}: {fg} on {bg} changed to {ratio:.2f}:1"


# --- motion ---

def test_reduce_motion_applies_end_states_at_once(app):
    theme.set_reduce_motion(True)
    got = []
    assert anim.tween(got.append, 0.0, 1.0) is None and got == [1.0]
    w = QWidget()
    assert anim.fade_in(w) is None and w.isVisible() and w.graphicsEffect() is None
    done = []
    assert anim.fade_out(w, then=lambda: done.append(1)) is None and w.isHidden() and done == [1]
    assert anim.slide_down(w) is None and w.isVisible() and w.maximumHeight() == anim.WIDGET_MAX
    assert anim.collapse(w) is None and w.isHidden()
    w.show()
    assert anim.pulse(w) is None and w.pulse_phase == 0.0
    assert anim.shimmer(w) is None and w.shimmer_phase == 0.0
    bar = c.ZoneBar.from_spec(PITCH)
    bar.set_value(100, animate=False)
    bar.set_value(300)
    assert bar.shown == pytest.approx(bar.fraction(300))
    w.close()


def test_tween_glides_to_the_end_without_reduce_motion(app):
    got = []
    a = anim.tween(got.append, 0.0, 10.0, ms=60)
    assert a is not None
    wait(250)
    assert got[-1] == pytest.approx(10.0) and len(got) > 1
    bar = c.ZoneBar.from_spec(PITCH)
    bar.set_value(100, animate=False)
    bar.set_value(300)
    assert bar.shown < bar.fraction(300)            # still on its way
    wait(anim.MOVE_MS + 150)
    assert bar.shown == pytest.approx(bar.fraction(300))


def test_a_new_tween_replaces_the_running_one(app):
    owner = QWidget()
    got = []
    anim.tween(got.append, 0, 1, ms=500, owner=owner, name="x")
    anim.tween(got.append, 0, 5, ms=40, owner=owner, name="x")
    wait(650)
    assert got[-1] == pytest.approx(5)


# --- ZoneBar ---

def test_zonebar_maps_linear_and_log_values_with_clipping():
    lin = c.ZoneBar(scale=(0, 10))
    assert lin.fraction(5) == 0.5 and lin.fraction(-3) == 0.0 and lin.fraction(20) == 1.0
    assert lin.fraction(None) is None and lin.fraction(float("nan")) is None
    log = c.ZoneBar.from_spec(PITCH)
    assert log.log and log.fraction((80 * 400) ** 0.5) == pytest.approx(0.5)     # geometric middle
    assert log.fraction(160) == pytest.approx(0.4307, abs=1e-4)                   # ln 2 / ln 5
    assert log.value_at(log.fraction(205)) == pytest.approx(205)
    assert log.fraction(50) == 0.0 and log.fraction(2000) == 1.0
    assert [log.zone_at(v) for v in (100, 150, 205, 220, 400)] == ["masculine", "overlap", "overlap", "feminine", "feminine"]
    assert log.boundaries() == [150, 220]


def test_zonebar_draws_zone_labels_and_shortens_them_when_narrow(app):
    bar = c.ZoneBar.from_spec(PITCH)
    bar.resize(900, bar.height())
    bar.grab()
    assert bar.drawn_labels == [("masculine", "typically masculine"), ("overlap", "overlap / androgynous"),
                                ("feminine", "typically feminine")]
    bar.resize(300, bar.height())
    bar.grab()
    drawn = dict(bar.drawn_labels)
    assert drawn["overlap"] in ("Overlap", "androgynous") or "overlap" not in drawn
    assert drawn["masculine"] in ("typically masculine", "Masculine", "masculine")


def test_zonebar_labels_keep_clear_of_the_marker(app):
    bar = c.ZoneBar.from_spec(PITCH)
    bar.resize(900, bar.height())
    bar.set_value(185, animate=False)               # the middle of the overlap zone, under its label
    bar.grab()
    assert "overlap" in dict(bar.drawn_labels)      # still labelled, beside the marker
    bar.set_value(None)
    assert bar.shown is None and bar.toolTip() == ""


def test_zonebar_tooltip_names_value_zone_and_target():
    bar = c.ZoneBar.from_spec(PITCH)
    bar.set_target((190, 240))
    bar.set_value(205, animate=False)
    assert bar.toolTip() == "205 Hz · overlap / androgynous · target 190–240 Hz"


# --- MetricCard ---

def test_metric_card_unavailable_shows_one_line_and_hides_the_bar(app):
    card = c.MetricCard("Resonance", PITCH)
    card.set_dimension(dim("none", value=None, headline="Too much background noise to judge brightness"))
    card.show()
    assert card.unavailable and card.bar.isHidden() and card.value_label.isHidden()
    assert card.headline.text() == "Too much background noise to judge brightness"
    assert card.explanation.isHidden() and card.confidence.level == "none"
    card.set_dimension(dim("high"))
    assert not card.unavailable and card.bar.isVisible() and card.value_label.text() == "205"
    assert card.unit_label.text() == "Hz" and card.confidence.level == "high"
    assert card.bar.target == (190, 240) and card.bar.baseline == 165.0
    card.close()


def test_metric_card_shows_change_and_details():
    card = c.MetricCard("Pitch", PITCH)
    card.set_dimension(dim(change={"vs": "previous", "text": "+12 Hz vs last take"}))
    assert card.change_chip.text == "+12 Hz vs last take" and not card.change_chip.isHidden()
    assert card.details_text.text() == "Median f0 205 Hz." and not card.details.isHidden()


# --- ActivityCenter ---

def test_activity_center_follows_the_job_list(app):
    center = c.ActivityCenter()
    center.show()
    center.set_jobs(JOBS)
    assert center.count() == 2 and center.title.text() == "2 tasks running"
    assert center.rows[1].percent.text() == "38%" and center.rows[1].text.text() == "Formants"
    assert center.spinner.isVisible() and center.list.isVisible()
    center.set_jobs([dict(JOBS[0], done=6), JOBS[1]])
    assert center.rows[1].percent.text() == "75%"
    center.set_jobs([JOBS[1]])
    assert center.count() == 1 and center.title.text() == "1 task running"
    center.set_expanded(False)
    assert center.title.text() == "1 running" and center.list.isHidden()
    center.set_jobs([])
    assert center.count() == 0 and center.title.text() == "Nothing running" and center.spinner.isHidden()
    center.close()


def test_activity_center_indeterminate_job_and_hide_when_idle(app):
    center = c.ActivityCenter(hide_when_idle=True)
    center.set_jobs([{"id": "x", "label": "Loading takes", "done": 0, "total": 0}])
    assert center.rows["x"].strip.fraction is None and center.rows["x"].percent.text() == ""
    center.set_jobs([])
    assert center.isHidden()


# --- RecordButton ---

def test_record_button_states_and_elapsed_time(app):
    now = [100.0]
    b = c.RecordButton(clock=lambda: now[0])
    clicks = []
    b.clicked.connect(lambda: clicks.append(b.state))
    assert b.caption() == "Record"
    QTest.keyClick(b, Qt.Key.Key_Space)
    b.set_state("recording")
    now[0] += 7.6
    b._tick()
    assert b.caption() == "0:07" and b.timer.isActive()
    b.set_elapsed(75)
    assert b.caption() == "1:15"
    QTest.keyClick(b, Qt.Key.Key_Space)
    b.set_state("analysing")
    assert b.caption() == "Analysing…" and not b.timer.isActive()
    QTest.keyClick(b, Qt.Key.Key_Space)             # ignored while analysing
    b.set_state("disabled")
    QTest.keyClick(b, Qt.Key.Key_Space)
    assert clicks == ["idle", "recording"]
    with pytest.raises(ValueError):
        b.set_state("paused")


def test_record_button_pulses_only_while_recording(app):
    b = c.RecordButton()
    b.show()
    b.set_state("recording")
    assert anim.running(b, "pulse_phase")
    b.set_state("idle")
    assert not anim.running(b, "pulse_phase") and b.pulse_phase == 0.0
    b.close()


# --- other controls and layout ---

def test_button_variants_and_loading(app):
    for variant in ("primary", "secondary", "ghost", "danger"):
        assert c.Button("Go", variant).variant == variant
    with pytest.raises(ValueError):
        c.Button("Go", "loud")
    b = c.Button("Save", "primary")
    clicks = []
    b.clicked.connect(lambda: clicks.append(1))
    b.set_loading(True, "Saving…")
    QTest.mouseClick(b, Qt.MouseButton.LeftButton)
    assert clicks == [] and b.text() == "Saving…" and not b.icon().isNull()
    b.set_loading(False)
    QTest.mouseClick(b, Qt.MouseButton.LeftButton)
    assert clicks == [1]


def test_segmented_tabs_nav_toggle_emit_keys(app):
    seg = c.SegmentedControl([("speak", "Speak"), ("sing", "Sing")])
    tabs = c.Tabs(["Overview", "Pitch"])
    nav = c.NavRail(gallery.NAV, gallery.NAV_FOOTER, current="home")
    toggle = c.Toggle(False, "Reduce motion")
    got = []
    for w, sig in ((seg, seg.changed), (tabs, tabs.changed), (nav, nav.selected), (toggle, toggle.toggled)):
        sig.connect(got.append)
    seg.set_current("sing", emit=True)
    tabs.set_current("Pitch", emit=True)
    nav.set_current("settings", emit=True)
    toggle.set_checked(True, emit=True)
    assert got == ["sing", "Pitch", "settings", True]
    assert seg.current() == "sing" and nav.current() == "settings" and toggle.isChecked()
    nav.set_badge("takes", 3)
    assert nav.badges["takes"] == 3
    sel = c.Select([("a", "First"), ("b", "Second")], current="b")
    assert sel.current_key() == "b"
    sel.changed.connect(got.append)
    sel.set_current_key("a")
    assert got[-1] == "a"


def test_collapsible_remembers_its_state_by_key(app):
    c.Collapsible.memory.clear()
    first = c.Collapsible("Details", key="card.details")
    first.set_expanded(True, animate=False)
    assert c.Collapsible.memory["card.details"] is True
    assert c.Collapsible("Details", key="card.details").expanded
    assert not c.Collapsible("Details", key="other").expanded


def test_flow_grid_adds_columns_as_it_widens():
    grid = c.FlowGrid(min_column_width=300, spacing=16)
    assert [grid.columns_for(w) for w in (200, 616, 1000, 2560)] == [1, 2, 3, 8]
    assert c.FlowGrid(min_column_width=300, max_columns=2).columns_for(2560) == 2
    cards = [QLabel(str(i)) for i in range(4)]
    for card, span in zip(cards, (1, 2, -1, 1)):
        grid.add(card, span)
    assert [(r, col, s) for _, r, col, s in grid.placements(3)] == [(0, 0, 1), (0, 1, 2), (1, 0, 3), (2, 0, 1)]
    cards[1].hide()
    assert [(r, col, s) for _, r, col, s in grid.placements(3)] == [(0, 0, 1), (1, 0, 3), (2, 0, 1)]


def test_flow_grid_widens_a_few_cards_instead_of_leaving_a_gap():
    grid = c.FlowGrid(min_column_width=300, spacing=16, max_column_width=640)
    for i in range(3):
        grid.add(QLabel(str(i)))
    assert grid.columns_for(1000) == 3          # fits three, has three
    assert grid.columns_for(2560) == 4          # fits eight; three cards, each at most 640 wide
    assert grid.columns_for(1900) == 3


def test_wrap_row_wraps_onto_more_lines(app):
    row = c.WrapRow([c.Chip(f"Chip {i}") for i in range(8)])
    assert row.heightForWidth(2000) < row.heightForWidth(150)


def test_toast_dismisses_itself_and_the_stack_caps_its_size(app):
    theme.set_reduce_motion(True)
    host = QWidget()
    host.resize(800, 600)
    stack = c.ToastStack(host)
    host.show()
    closed = []
    toast = stack.show_toast("Saved", "success", timeout_ms=50)
    toast.closed.connect(lambda: closed.append(1))
    wait(200)
    assert closed == [1] and stack.toasts == []
    for i in range(6):
        stack.show_toast(f"Toast {i}", "info", timeout_ms=0)
    assert len(stack.toasts) == 4
    with pytest.raises(ValueError):
        c.Toast("x", "loud")
    host.close()


def test_moment_chip_plays_its_moment(app):
    chip = c.MomentChip(3.1, 4.0)
    point = c.MomentChip(4.2)
    assert chip.text == "3.1–4.0 s" and point.text == "4.2 s"
    got = []
    chip.play.connect(lambda s, e: got.append((s, e)))
    QTest.mouseClick(chip, Qt.MouseButton.LeftButton)
    assert got == [(3.1, 4.0)]


def test_confidence_chip_levels():
    chip = c.ConfidenceChip("low", "Only 1.2 s of voice")
    assert chip.label == "Low confidence" and "Only 1.2 s of voice" in chip.toolTip()
    with pytest.raises(ValueError):
        chip.set_level("certain")


# --- gallery ---

def test_gallery_renders_both_themes_to_png(app, tmp_path):
    for name in ("dark", "light"):
        path = tmp_path / f"gallery-{name}.png"
        width, height = gallery.render(str(path), width=1280, name=name)
        assert path.exists() and path.stat().st_size > 50_000
        assert width == 1280 and height > 2000
