"""The take card, answer first (phase 11 spec 04): the headline, one statement of trust, a player with the
tips' moments marked, the overall placement in one sentence, last time's tip followed up, the top tips,
then the dimensions and every detail one click away. A compact form serves lists. The report comes from
report.py through the core; what to show comes from take_view; the card only draws it and sends actions
to the core."""
from PyQt6 import sip
from PyQt6.QtCore import QObject, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices, QPageSize
from PyQt6.QtWidgets import (QApplication, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QMenu, QMessageBox,
                             QSizePolicy, QVBoxLayout, QWidget)

import take_view
from ui import theme
from ui.components import (Button, Caption, Card, Chip, Collapsible, ElidedLabel, FlowGrid, Headline, HelpTip,
                           IconButton, Label, MetricCard, MomentChip, ProgressStrip, Section, ShareBar, StatusDot,
                           Toggle, WrapRow, ZoneBar)
from ui.components.base import ThemedWidget, draw_icon, painter, rounded
from ui.takes import listen, report_for
from ui.takes.compare_view import CompareView
from ui.takes.trust_line import NoteLine, TrustLine, reading_chip

PLAY_TICK_MS = 30               # playhead refresh while a take plays
MIN_TARGET = 28                  # px: the smallest click target the lab accepts
GRAPH_HEIGHT = 340              # px: pitch and formants
METRIC_COLUMN = 300             # px: narrowest dimension card
TIP_ICONS = {"tip": ("ph.lightbulb", "accent"), "info": ("ph.ear", "text_secondary"),
             "best": ("ph.star-fill", "warning")}
COMPACT_DIMENSIONS = ("pitch", "delta_f")   # the mini bars on a compact card
OVERALL_HELP = ("Where pitch and resonance together place this take, from typically masculine to typically "
                "feminine. The whisker is how sure that is: readings that couldn't be judged widen it. "
                "Zones are tendencies across many voices, not rules.")
TIPS_HELP = "Taken from this take's own pitch and resonance tracks. Click a time to hear that moment."
DIMENSIONS_HELP = ("Each reading on its zones. The green outline is your target, the small caret your baseline, "
                   "and the whisker the middle half of this take's frames.")
EXPORT_FILTER = "PDF document (*.pdf);;PNG image (*.png)"
PDF_DPI = 96                    # px per inch the card is drawn at when exported
POINTS_PER_INCH = 72


def ask(parent, question):
    """Yes/no confirmation (tests replace this)."""
    return QMessageBox.question(parent, "VoiceLab", question) == QMessageBox.StandardButton.Yes


def reveal(path):
    """Show a file's folder in the system file manager (tests replace this)."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.rsplit("\\", 1)[0] if "\\" in path else
                                                    path.rsplit("/", 1)[0])))


def choose_export_path(parent, suggestion):
    """Where to save an exported report (tests replace this); "" when cancelled."""
    path, _ = QFileDialog.getSaveFileName(parent, "Export report", suggestion, EXPORT_FILTER)
    return path


def clear_layout(layout):
    """Remove and delete everything in a layout (and nested layouts)."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def hbox(*widgets, spacing=theme.S, stretch_at=None):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for i, w in enumerate(widgets):
        if w is None:
            row.addStretch(1)
        else:
            row.addWidget(w, 1 if i == stretch_at else 0)
    return row


def holder(layout):
    w = QWidget()
    w.setLayout(layout)
    return w


class TakePlayback(QObject):
    """Plays one take through the core's player (one playback at a time across the app): the whole
    take, a moment, or the take then another. position(seconds or None) follows it; piece(name) says
    which part of a comparison plays; stopped() when it ends."""

    position = pyqtSignal(object)
    piece = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(self, core, uid, parent):
        super().__init__(parent)
        self.core, self.uid = core, uid
        self.player = core.player
        self.token = object()           # this card's playbacks, as the player's owner
        self._audio = None
        self.timer = QTimer(self)
        self.timer.setInterval(PLAY_TICK_MS)
        self.timer.timeout.connect(self.tick)
        token, player = self.token, self.player
        off = self.player.on_finished(self._finished)
        parent.destroyed.connect(lambda *_: (off(), player.owner is token and player.stop()))

    @property
    def playing(self):
        return self.player.playing and self.player.owner is self.token

    def audio(self):
        if self._audio is None:
            self._audio = self.core.take_audio(self.uid)
        return self._audio

    def play(self, start=0.0, then=None):
        """Play from `start` s, then (optionally) another take's samples after a short gap."""
        self.player.play_take(self.audio(), start, then=then, owner=self.token)
        self._started()

    def play_other(self, samples):
        """Play another take's samples alone, as this card's playback (the compare view's "play the other")."""
        self.player.play_take(samples, 0.0, owner=self.token)
        self._started()

    def play_moment(self, start, end):
        """Play the stretch start..end s of the take."""
        samples, start = take_view.moment_clip(self.audio(), start, end, self.player.sample_rate)
        self.player.play_take(samples, start, owner=self.token)
        self._started()

    def follow(self, start=0.0):
        """Follow a playback started elsewhere (the shifted version) from `start`."""
        self.player.track_take(self.audio(), start, owner=self.token)
        self._started()

    def _started(self):
        self.timer.start()
        self.tick()

    def stop(self):
        if self.playing:
            self.player.stop()
        else:
            self._reset()

    def tick(self):
        if sip.isdeleted(self):
            return
        if self.player.owner is not self.token:
            self._reset()
            return
        pos = self.player.position()
        if pos["playing"]:
            self.position.emit(pos["seconds"])
            self.piece.emit(pos["piece"])

    def _finished(self, owner):
        if owner is self.token:
            self._reset()

    def _reset(self):
        if sip.isdeleted(self) or sip.isdeleted(self.timer):     # the card went away while a playback ended
            return
        self.timer.stop()
        self.position.emit(None)
        self.piece.emit(None)
        self.stopped.emit()


class TitleEdit(QLineEdit):
    """The take's title, edited in place: it looks like a heading until hovered or focused."""

    def __init__(self, text, placeholder, tooltip):
        super().__init__(text)
        self.setPlaceholderText(placeholder)
        self.setToolTip(tooltip)
        self.setFrame(False)
        self.setMinimumWidth(120)
        self.setCursorPosition(0)
        theme.changed.connect(self.restyle)
        self.restyle()

    def restyle(self, *_):
        c = theme.current().colors
        self.setFont(theme.font("heading"))
        pal = self.palette()
        pal.setColor(pal.ColorRole.PlaceholderText, theme.color("text_secondary"))
        self.setPalette(pal)
        self.setStyleSheet(f"""
QLineEdit {{ background: transparent; color: {c['text']}; border: 1px solid transparent;
    border-radius: {theme.RADIUS['small']}px; padding: 1px 4px; placeholder-text-color: {c['text_secondary']}; }}
QLineEdit:hover {{ border-color: {c['border']}; }}
QLineEdit:focus {{ border-color: {c['accent']}; background: {c['surface_raised']}; }}""")


class _Icon(ThemedWidget):
    """A small painted icon that follows the theme (a bound method, so it disconnects when it dies)."""

    def __init__(self, name, color, size=18):
        super().__init__()
        self.name, self.color = name, color
        self.setFixedSize(size + 4, size + 4)

    def paintEvent(self, event):
        draw_icon(painter(self), self.name, QRectF(2, 2, self.width() - 4, self.height() - 4), self.color)


class TipRow(QWidget):
    """One tip: an icon by kind, what to try in a sentence, the evidence under it in a muted line, and
    its moments to replay (one click each)."""

    def __init__(self, tip, on_moment, compact=False):
        super().__init__()
        self.tip = tip
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.M)
        self.icon = _Icon(*TIP_ICONS.get(tip.get("kind"), TIP_ICONS["tip"]))
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(theme.XS)
        text, why = take_view.tip_sentence(tip)
        why = "" if compact else why
        self.text = Label(text, role="body", wrap=True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(self.text)
        self.why = None
        if why:
            self.why = Caption(why, tone="secondary")
            col.addWidget(self.why)
        self.chips = []
        if tip.get("moments"):
            chips = WrapRow(spacing=theme.XS + 2)
            for start, end in tip["moments"]:
                chip = MomentChip(start, end)
                chip.play.connect(lambda a, b, c=chip: on_moment(a, b, c))
                chips.add(chip)
                self.chips.append(chip)
            col.addWidget(chips)
        row.addLayout(col, 1)


class OverallBlock(QWidget):
    """The overall placement: its name, a chip when it is only a rough guide, the zone bar with its
    uncertainty whisker, and the one sentence on what pulls where."""

    def __init__(self, overall, compact=False):
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.XS if compact else theme.S)
        self.bar = ZoneBar.from_spec(take_view.overall_spec(), ticks=False, compact=compact,
                                     fmt=lambda v: f"{v:.2f}")
        self.bar.set_value(overall["position"], animate=False)
        self.bar.set_range(overall.get("range"))
        self.text = None
        self.trust = None
        if compact:
            box.addWidget(self.bar)
            return
        self.trust = reading_chip({"key": "overall", "confidence": overall.get("confidence") or "none",
                                   "reason": overall.get("reason"), "value": overall.get("position")})
        top = hbox(Label(take_view.OVERALL_NAME, role="label", tone="muted"), HelpTip(OVERALL_HELP), None, self.trust)
        box.addLayout(top)
        box.addWidget(self.bar)
        self.text = Label(overall.get("text") or "", role="body", tone="secondary", wrap=True)
        box.addWidget(self.text)


class MiniBar(QWidget):
    """A compact card's reading: the name, a slim zone bar and the value."""

    def __init__(self, dim):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S)
        name = Label(dim["name"], role="caption", tone="muted")
        name.setFixedWidth(72)
        self.bar = ZoneBar.from_spec(take_view.dimension_spec(dim), compact=True, labels=False, ticks=False)
        self.bar.setMinimumWidth(90)
        self.bar.set_value(dim.get("value"), animate=False)
        self.bar.set_target(dim.get("target"))
        value = Label(dim.get("display") if dim.get("value") is not None else "—", role="numeric",
                      tone="secondary" if dim.get("value") is not None else "muted")
        value.setFixedWidth(64)
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(name)
        row.addWidget(self.bar, 1)
        row.addWidget(value)
        if dim.get("value") is None:
            self.setToolTip(dim.get("headline") or "")


class LastTipRow(ThemedWidget):
    """Closing the loop: "Last time: start your sentences higher. This take: you did. Your first words
    were 12 Hz higher." with the verdict as a chip."""

    KINDS = {"yes": "success", "partly": "warning", "not yet": "neutral"}

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.M, theme.S, theme.M, theme.S)
        row.setSpacing(theme.M)
        self.chip = Chip(view["verdict"].capitalize(), kind=self.KINDS[view["verdict"]], dot=True)
        self.label = Label(view["text"], role="body", wrap=True)
        row.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.label, 1)
        self.setAccessibleName(view["text"])

    def paintEvent(self, event):
        t = theme.current()
        rounded(painter(self), self.rect(), theme.RADIUS["medium"], fill=t.q("surface_raised"), border=t.q("border"))


class SummaryView(QWidget):
    """The top of a report, in the order a reader needs it: the headline, the trust line, the player (when
    one is given), the overall placement with its one sentence, last time's tip followed up, and the top
    tips. moment(start, end, chip) when a moment is clicked; mic_check() from the trust line's action.
    Wider than SPLIT_WIDTH the placement and the tips sit side by side, so lines stay readable."""

    moment = pyqtSignal(float, float, object)
    mic_check = pyqtSignal()
    SPLIT_WIDTH = 1000          # px; below it, one column

    def __init__(self, rep, take, max_tips=None, headline_level="title", split=True, player=None,
                 compact=False, show_overall=True, show_last_tip=True):
        super().__init__()
        self.report = rep
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.M)
        self.headline = Headline(rep["headline"], level=headline_level)
        outer.addWidget(self.headline)
        self.trust_view = take_view.trust_line(rep, take)
        self.trust = None
        if not (compact and self.trust_view["clean"]):      # a compact summary only speaks up about a problem
            self.trust = TrustLine(self.trust_view, compact=compact)
            self.trust.mic_check.connect(self.mic_check.emit)
            outer.addWidget(self.trust)
        self.player = player
        if player is not None:
            outer.addWidget(player)
        columns = FlowGrid(min_column_width=self.SPLIT_WIDTH // 2 - theme.XL, spacing=theme.XL,
                           max_column_width=10000) if split else None
        left = QWidget()
        box = QVBoxLayout(left)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.L)
        self.overall = None
        overall = rep["overall"]
        if show_overall and overall.get("position") is not None:
            self.overall = OverallBlock(overall, compact=compact)
            box.addWidget(self.overall)
        elif show_overall and not compact and rep["mode"] == "speak" and take_view.quality(take)["tier"] != "poor"                 and overall.get("text"):
            box.addWidget(Caption(overall["text"], tone="secondary"))
        self.last_tip = None
        last = take_view.last_tip_view(rep) if show_last_tip and not compact else None
        if last:
            self.last_tip = LastTipRow(last)        # after the new tips: they are the next step
        shown, more = take_view.split_tips(rep["tips"] if max_tips is None else rep["tips"][:max_tips])
        self.tip_rows, self.more_rows = [], []
        section = None
        if shown:
            section = Section("Next time", help=TIPS_HELP, level="label")
            section.heading.set_tone("muted")
            for tip in shown:
                row = TipRow(tip, lambda a, b, c: self.moment.emit(a, b, c), compact)
                section.add(row)
                self.tip_rows.append(row)
            if more:
                extra = Collapsible(f"More tips ({len(more)})", key="take_card.more_tips", role="label")
                extra.header.setMinimumHeight(MIN_TARGET)
                for tip in more:
                    row = TipRow(tip, lambda a, b, c: self.moment.emit(a, b, c), compact)
                    extra.add(row)
                    self.tip_rows.append(row)
                    self.more_rows.append(row)
                section.add(extra)
        if self.last_tip is not None:
            if section is None:
                box.addWidget(self.last_tip)
            else:
                section.add(self.last_tip)
        has_left = box.count() > 0
        if columns is not None and section is not None and has_left:
            box.addStretch(1)
            columns.add(left)
            columns.add(section)
            outer.addWidget(columns)
            return
        if has_left:
            outer.addWidget(left)
        if section is not None:
            outer.addWidget(section)

    def chips(self):
        return [chip for row in self.tip_rows for chip in row.chips]


class TakeCard(Card):
    """One take, answer first (full) or as a compact card. Follows the core's events. open_report(uid)
    when a compact card is clicked (with no one listening it expands itself); mic_check_requested() from
    the trust line's action."""

    open_report = pyqtSignal(str)
    mic_check_requested = pyqtSignal()

    def __init__(self, core, uid, compact=False, parent=None):
        super().__init__(padding=theme.M if compact else theme.XL, parent=parent)
        self.core, self.uid, self.compact = core, uid, compact
        self.selected = False
        self.hover = False
        self.removed = False
        self.new = False                # a take just made: a "New" chip until it has been looked at
        self.compare = None             # the take played and drawn alongside this one
        self.compare_view = None
        self.start_at = 0.0             # where Play starts (s): set by clicking the graph
        self.playing_chip = None
        self.graph = self.shift_view = self.player = None
        self.updating = False
        self.update_text = None         # the "Updated" note, once a re-analysis changed this take's analysis
        self._digest = None             # take_view.report_digest of what is on screen
        self.playback = TakePlayback(core, uid, self)
        self.playback.position.connect(self._position)
        self.playback.piece.connect(self._piece)
        self.playback.stopped.connect(self._stopped)
        self.body.setSpacing(theme.S if compact else theme.L)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        listen(self, core, {"take_updated": self._take_updated, "take_removed": self._take_removed,
                            "profile_changed": self.rebuild, "busy": self._busy})
        self.rebuild()
        self._busy(core.runner.jobs())

    # --- state ---
    @property
    def data(self):
        return self.core.take(self.uid)

    @property
    def playing(self):
        return self.playback.playing

    def set_compact(self, compact):
        """Switch between the compact and the full form."""
        if compact == self.compact:
            return
        self.compact = compact
        m = theme.M if compact else theme.XL
        self.layout().setContentsMargins(m, m, m, m)
        self.body.setSpacing(theme.S if compact else theme.L)
        self.rebuild()

    def set_selected(self, selected):
        self.selected = bool(selected)
        self.update()

    def set_new(self, new):
        """Show (or hide) the "New" chip on a take just made."""
        self.new = bool(new)
        if hasattr(self, "new_chip"):
            self.new_chip.setVisible(self.new)

    # --- building ---
    def rebuild(self, *_, updated=False):
        """Draw the take again from its data and report. `updated`: a re-analysis changed it, so say what."""
        if self.removed:
            return
        data = self.data
        if data is None:
            return
        if self.playing:
            self.playback.stop()
        self.graph = self.shift_view = self.player = self.compare_view = None
        self.playing_chip = None
        clear_layout(self.body)
        self._tracks_id, self._register_id = id(data.get("tracks")), id(data.get("register"))
        self.report = report_for(self.core, self.uid)
        digest = take_view.report_digest(self.report, data)
        if updated:
            self.update_text = take_view.update_note(self._digest, digest)
        self._digest = digest
        self.head = take_view.header(data, self.core.number(data))
        self.strip = ProgressStrip()
        self.strip.hide()
        self.strip.setAccessibleName("Updating")
        if self.compact:
            self._build_compact(data)
        else:
            self._build_full(data)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.compact else Qt.CursorShape.ArrowCursor)
        self._busy(self.core.runner.jobs())

    def _build_header(self):
        h = self.head
        self.quality_dot = StatusDot(h["dot"])
        self.quality_dot.setToolTip(f"{h['tier_text']}. {take_view.QUALITY_TOOLTIP}")
        self.subtitle = Caption(h["subtitle"], wrap=False)
        self.subtitle.setToolTip(take_view.saved_state(self.data)[1])
        self.new_chip = Chip("New", kind="accent", dot=True)
        self.new_chip.setVisible(self.new)
        if self.compact:
            self.title = ElidedLabel(h["title"] or h["placeholder"], role="label")
            top = hbox(self.title, self.quality_dot, stretch_at=0)
            self.body.addLayout(top)
            self.body.addWidget(self.subtitle)
            return
        self.title = TitleEdit(h["title"], h["placeholder"], h["tooltip"])
        self.title.editingFinished.connect(self._title_edited)
        self.compare_btn = Button("Compare", "secondary", icon="ph.arrows-left-right", small=True)
        self.compare_btn.setToolTip("Put this take next to the previous one (or the baseline, or any other) and "
                                    "hear them one after the other.")
        self.compare_btn.clicked.connect(self.toggle_compare)
        self.more_btn = IconButton("ph.dots-three-outline-fill", "More actions")
        self.menu = QMenu(self.more_btn)
        self.menu.aboutToShow.connect(self._fill_menu)
        self.more_btn.clicked.connect(lambda: self.menu.popup(self.more_btn.mapToGlobal(
            self.more_btn.rect().bottomLeft())))
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(self.title)
        sub = hbox(self.subtitle, self.quality_dot, self.new_chip, None, spacing=theme.M)
        sub.setContentsMargins(5, 0, 0, 0)
        titles.addLayout(sub)
        top = QHBoxLayout()
        top.setSpacing(theme.S)
        top.addLayout(titles, 1)
        top.addWidget(self.compare_btn, 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(self.more_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(top)
        if self.head["source"]:
            self.body.addWidget(Caption(self.head["source"]))

    def _build_compact(self, data):
        self.body.setSpacing(theme.S)
        self._build_header()
        self.body.addWidget(self.strip)
        rep = self.report
        self.updating_note = Caption("", tone="secondary")
        self.updating_note.hide()
        self.body.addWidget(self.updating_note)
        self.headline = Label(rep["headline"], role="body", wrap=True)
        self.body.addWidget(self.headline)
        self.overall = None
        if rep["overall"].get("position") is not None:
            self.overall = OverallBlock(rep["overall"], compact=True)
            self.body.addWidget(self.overall)
        dims = {d["key"]: d for d in rep["dimensions"]}
        self.mini_bars = {}
        for key in COMPACT_DIMENSIONS if rep["mode"] == "speak" else ():
            if key in dims and dims[key].get("value") is not None:
                self.mini_bars[key] = MiniBar(dims[key])
                self.body.addWidget(self.mini_bars[key])
        self.metric_cards, self.summary, self.trust = {}, None, None

    def _build_full(self, data):
        rep = self.report
        self._build_header()
        self.body.addWidget(self.strip)
        self.updating_slot = QVBoxLayout()
        self.updating_slot.setContentsMargins(0, 0, 0, 0)
        self.body.addLayout(self.updating_slot)
        self.updating_note = None
        if self.update_text:
            self.body.addWidget(NoteLine(self.update_text, icon="ph.check-circle", kind="success"))
        moments = [m for tip in rep["tips"] for m in tip.get("moments") or []]
        if data.get("tracks") and len(data["tracks"].get("times", [])) > 1:
            from ui.takes.player_strip import PlayerStrip
            self.player = PlayerStrip(data, moments)
            self.player.play_clicked.connect(self.play_audio)
            self.player.seek_requested.connect(self.seek)
            self.player.moment_clicked.connect(lambda a, b: self.play_moment(a, b))
            self.play_btn = self.player.play_btn
        self.summary = SummaryView(rep, data, player=self.player)
        self.summary.moment.connect(self.play_moment)
        self.summary.mic_check.connect(self.mic_check_requested.emit)
        self.headline, self.trust = self.summary.headline, self.summary.trust
        self.body.addWidget(self.summary)
        self.compare_slot = QVBoxLayout()
        self.compare_slot.setContentsMargins(0, 0, 0, 0)
        self.body.addLayout(self.compare_slot)
        self._build_dimensions(data, rep)
        self._build_details(data, rep)
        self.body.addWidget(Caption(rep["caveat"]))
        if self.compare is not None:
            self.set_compare(self.compare)

    def _build_dimensions(self, data, rep):
        self.metric_cards = {}
        q = take_view.quality(data)
        section = Section("Your voice in this take" if rep["mode"] == "speak" else "Your singing in this take",
                          help=DIMENSIONS_HELP if rep["mode"] == "speak" else None, level="label")
        section.heading.set_tone("muted")
        if q["tier"] == "poor":
            section.add(Label("No readings: this recording can't support them. The line above says what to "
                              "try; a quick re-record gives you the full report.", role="body",
                              tone="secondary", wrap=True))
            self.body.addWidget(section)
            return
        grid = FlowGrid(min_column_width=METRIC_COLUMN, max_columns=3, spacing=theme.M, max_column_width=640)
        for dim in take_view.ordered_dimensions(rep):
            card = MetricCard(dim["name"], take_view.dimension_spec(dim))
            card.set_dimension(dim)
            self._own_trust_chip(card, dim)
            if take_view.end_labels(dim):
                card.bar.set_end_labels(*take_view.end_labels(dim))
                card.bar.set_ticks([])
            if dim.get("value") is not None:
                number, unit = take_view.value_parts(dim)
                card.value_label.setText(number)
                card.unit_label.setText(unit)
                card.unit_label.setVisible(bool(unit))
            grid.add(card)
            self.metric_cards[dim["key"]] = card
        section.add(grid)
        self.dimension_grid = grid
        stat = take_view.in_range(data, self.core.profile, q) if rep["mode"] == "speak" else {"text": ""}
        self.in_range = QWidget()
        row = hbox(Chip(stat["text"] or " ", kind="on_target", dot=True), Caption(stat.get("detail") or "", tone="secondary"),
                   None, spacing=theme.M)
        self.in_range.setLayout(row)
        self.in_range.setVisible(bool(stat["text"]))
        self.in_range_chip = row.itemAt(0).widget()
        section.add(self.in_range)
        self.body.addWidget(section)

    @staticmethod
    def _own_trust_chip(card, dim):
        """Swap the card's confidence chip for the take's wording (Rough / Can't judge), shown only when a
        reading isn't reliable."""
        card.confidence.hide()
        chip = reading_chip(dim)
        card.trust_chip = chip
        if chip is not None:
            top = card.body.itemAt(0).layout()
            top.insertWidget(top.count() - 1, chip)

    def _build_details(self, data, rep):
        box = QVBoxLayout()
        box.setSpacing(theme.XS)
        self.sections = {}

        def lazy(key, title, build):
            col = Collapsible(title, key=f"take_card.{key}", role="label")
            built = []

            def ensure(expanded):
                if expanded and not built:
                    built.append(True)
                    build(col)
            col.toggled.connect(ensure)
            ensure(col.expanded)
            self.sections[key] = col
            box.addWidget(col)
            return col

        lazy("graph", "Graph & formants", self._fill_graph)
        if rep["mode"] == "sing" or data.get("exercise"):
            lazy("singing", "Singing & exercise", self._fill_singing)
        lazy("register", "Register", self._fill_register)
        lazy("numbers", "All numbers", self._fill_numbers)
        lazy("shift", "Hear it shifted", self._fill_shift)
        self.body.addLayout(box)

    def _fill_graph(self, col):
        from ui.takes.take_graph import TakeGraph
        data = self.data
        reg = take_view.register_summary(data, self.core.profile)
        show_register = reg["confidence"] != "none" and bool(reg["shares"])
        self.graph = TakeGraph(data, register=show_register)
        self.graph.setMinimumHeight(GRAPH_HEIGHT)
        self.graph.set_target(self.core.profile.target("pitch"))
        self.graph.seek_requested.connect(self.seek)
        self.raw_toggle = Toggle(False, "Raw formants")
        self.raw_toggle.setToolTip("Show the formants as measured, frame by frame, instead of smoothed and "
                                   "voiced-only.")
        self.raw_toggle.toggled.connect(self.graph.set_raw)
        self.compare_label = Caption("", tone="secondary", wrap=False)
        col.add(holder(hbox(self.compare_label, None, self.raw_toggle, spacing=theme.M)))
        col.add(self.graph)
        t = theme.current()
        legend = [("Pitch", t.series["pitch"])]
        legend += [(f, t.series[f.lower()]) for f in ("F1", "F2", "F3")] if self.graph.formants else []
        legend += [(name.capitalize(), t.register[name]) for name in ("chest", "mix", "head")] \
            if show_register and self.graph.register_dots.points().size else []
        col.add(Caption("   ".join(f"● {name}" for name, _ in legend) +
                        "  · click the graph to play from there", tone="muted"))
        self.graph.set_overlay(None if self.compare is None else self.compare["tracks"])
        self.compare_label.setText(take_view.compare_text(self.compare))

    def _fill_singing(self, col):
        from ui.takes.take_graph import NoteScoreChart
        view = take_view.card(self.data, self.core.profile)
        if view["exercise"]:
            col.add(Label(view["exercise"], role="body", wrap=True))
            if view["exercise_notes"]:
                col.add(NoteScoreChart(view["exercise_notes"], take_view.IN_TUNE_CENTS))
        if view["singing"]:
            col.add(Label(view["singing"], role="body", tone="secondary", wrap=True))
        if not view["exercise"] and not view["singing"]:
            col.add(Caption("No held notes or exercise in this take.", tone="secondary"))

    def _fill_register(self, col):
        reg = take_view.register_summary(self.data, self.core.profile)
        self.register_info = reg
        head = Label(reg["headline"], role="body")
        chip = reading_chip({"key": "register", "confidence": reg["confidence"] if reg["confidence"] in
                             ("high", "medium", "low", "none") else "low", "reason": reg["reason"],
                             "value": None if reg["confidence"] == "none" else 1})
        label_btn = Button("Label register…", "ghost", icon="ph.tag", small=True)
        label_btn.setToolTip("Tell VoiceLab this take is chest or head voice. Once enough takes are labelled, "
                             "the register estimate is calibrated to your voice.")
        menu = QMenu(label_btn)
        self._label_menu(menu)
        label_btn.setMenu(menu)
        col.add(holder(hbox(head, chip, None, label_btn, spacing=theme.M)))
        if reg["shares"]:
            col.add(ShareBar(dict(reg["shares"])))
        col.add(Caption(reg["text"], tone="secondary"))
        if reg["label"]:
            col.add(Caption(f"You labelled this take {reg['label']}."))

    def _fill_numbers(self, col):
        info = take_view.banner(self.report)
        if info:
            col.add(Label("Recording notes", role="label", tone="muted"))
            for text in info["all"]:
                col.add(Label("• " + text, role="body", tone="secondary", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.L)
        grid.setVerticalSpacing(theme.XS)
        for row, (label, value, note) in enumerate(take_view.all_numbers(self.data)):
            grid.addWidget(Label(label, role="caption", tone="secondary"), row, 0)
            v = Label(value, role="numeric")
            v.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(v, row, 1)
            grid.addWidget(Label(note, role="caption", tone="muted", wrap=True), row, 2)
        grid.setColumnStretch(2, 1)
        self.numbers_grid = grid
        col.add(holder(grid))

    def _fill_shift(self, col):
        from ui.takes.shift_view import ShiftView
        self.shift_view = ShiftView(self.data, self.playback.audio, self.core.runner, self.core.player,
                                    self.core.set_target)
        col.add(self.shift_view)

    # --- menus and actions ---
    def _fill_menu(self):
        menu = self.menu
        menu.clear()
        menu.addAction("Set as baseline", lambda: self.core.set_baseline(self.uid))
        menu.addAction("Use as target", lambda: self.core.set_target(self.uid))
        label = menu.addMenu("Label register")
        self._label_menu(label)
        menu.addSeparator()
        menu.addAction("Copy summary", self.copy_summary)
        menu.addAction("Export report…", self.export_report)
        location = menu.addAction("Open file location", self.open_location)
        location.setEnabled(take_view.file_path(self.core, self.uid) is not None)
        menu.addSeparator()
        menu.addAction("Delete…", self.delete)

    def _label_menu(self, menu):
        for key, text in (("chest", "Chest"), ("head", "Head"), (None, "Clear label")):
            action = QAction(text, menu)
            action.triggered.connect(lambda _, k=key: self.core.label_register(self.uid, k))
            menu.addAction(action)

    def open_location(self):
        path = take_view.file_path(self.core, self.uid)
        if path:
            reveal(path)

    def copy_summary(self):
        """Put the report as plain text on the clipboard; returns it."""
        text = take_view.summary_text(self.data, self.report, self.core.number(self.data))
        QApplication.clipboard().setText(text)
        return text

    def export_report(self, path=None):
        """Save the report as a PDF or PNG picture of this card; returns the path, or None when cancelled."""
        path = path or choose_export_path(self, take_view.export_name(self.data, self.core.number(self.data)))
        if not path:
            return None
        if path.lower().endswith(".png"):
            self.grab().save(path, "PNG")
        else:
            from PyQt6.QtCore import QSizeF
            from PyQt6.QtGui import QPainter, QPdfWriter
            writer = QPdfWriter(path)
            writer.setResolution(PDF_DPI)
            size = QSizeF(self.width() * POINTS_PER_INCH / PDF_DPI, self.height() * POINTS_PER_INCH / PDF_DPI)
            writer.setPageSize(QPageSize(size, QPageSize.Unit.Point))
            pdf = QPainter(writer)
            self.render(pdf)
            pdf.end()
        return path

    def delete(self):
        """Delete the take after asking; a saved take goes to the takes folder's .trash and can be undone."""
        return self.core.delete(self.uid, confirm=lambda question: ask(self, question))

    def _title_edited(self):
        title = self.title.text().strip()
        if title != (self.data.get("title") or ""):
            self.core.rename(self.uid, title)

    # --- events ---
    def _take_updated(self, data):
        if data.get("uid") != self.uid:
            return
        if id(data.get("tracks")) != self._tracks_id or id(data.get("register")) != self._register_id:
            self.playback._audio = None
            self.rebuild(updated=True)
            return
        self.head = take_view.header(data, self.core.number(data))
        if self.compact:
            self.title.setText(self.head["title"] or self.head["placeholder"])
        elif not self.title.hasFocus():
            self.title.setText(self.head["title"])
        self.subtitle.setText(self.head["subtitle"])

    def _take_removed(self, uid):
        if uid == self.uid:
            self.removed = True
            self.playback.stop()
            self.hide()

    def _busy(self, jobs):
        busy = take_view.reanalysing_uid(self.core, jobs) == self.uid
        self.updating = busy
        self.strip.setVisible(busy)
        self.strip.setToolTip(take_view.UPDATING_TEXT if busy else "")
        if busy:
            self.strip.set_indeterminate()
        if self.compact:
            if self.updating_note is not None:
                self.updating_note.setText(take_view.UPDATING_SHORT if busy else "")
                self.updating_note.setVisible(busy)
            return
        clear_layout(self.updating_slot)
        if busy:
            self.updating_slot.addWidget(NoteLine(take_view.UPDATING_TEXT))

    # --- playback ---
    def play_audio(self):
        """Play from the start (or the point clicked on the graph); while playing, stop."""
        if self.playing:
            self.playback.stop()
            return
        self.start_playback(self.start_at)

    def start_playback(self, start=0.0):
        if self.shift_view is not None and self.sections["shift"].expanded and not self.shift_view.session.neutral():
            self.playback.follow(start)
            self.shift_view.play_shifted()
        else:
            other = self.core.take_audio(self.compare) if self.compare is not None else None
            self.playback.play(start, then=other)
        self._update_play()

    def play_other_take(self):
        """The compare view's "play the other take": just the compared take, from its start."""
        if self.compare is None:
            return
        if self.playing:
            self.playback.stop()
        self.playback.play_other(self.core.take_audio(self.compare))
        self._update_play()

    def play_moment(self, start, end, chip=None):
        """Play one moment of a tip (and move the playheads along it)."""
        if self.playing and self.playing_chip is chip and chip is not None:
            self.playback.stop()
            return
        self.playback.play_moment(start, end)
        self.playing_chip = chip
        if chip is not None:
            chip.set_playing(True)
        self._update_play()

    def stop_playback(self):
        self.playback.stop()

    def seek(self, seconds):
        """A click on a graph: play from there, or if stopped, start there next time."""
        if self.playing:
            self.start_playback(seconds)
            return
        self.start_at = seconds
        self._playhead(seconds)

    def _playhead(self, seconds):
        for view in (self.player, self.graph):
            if view is not None:
                view.set_playhead(seconds)

    def _position(self, seconds):
        if seconds is not None:
            self._playhead(seconds)

    def _piece(self, piece):
        if self.compare is not None and getattr(self, "compare_label", None) is not None and piece:
            self.compare_label.setText(take_view.compare_text(self.compare, piece))

    def _stopped(self):
        if self.playing_chip is not None:
            try:
                self.playing_chip.set_playing(False)
            except RuntimeError:            # rebuilt meanwhile
                pass
            self.playing_chip = None
        self.start_at = 0.0
        self._playhead(None)
        if getattr(self, "compare_label", None) is not None and self.graph is not None:
            self.compare_label.setText(take_view.compare_text(self.compare))
        self._update_play()

    def _update_play(self):
        if self.compact or self.player is None:
            return
        shifted = self.shift_view is not None and self.sections["shift"].expanded
        text = take_view.play_label(self.playing, shifted, self.compare is not None).strip()
        self.player.set_play_text(text, self.playing)

    # --- compare ---
    def toggle_compare(self):
        """The Compare button: open the comparison with the previous take (the default), or close it."""
        if self.compare is not None:
            self.set_compare(None)
            return
        choices = self.compare_choices()
        if choices:
            self.set_compare(choices[0][1])

    def compare_choices(self):
        """[(label, take)]: the previous take first, then the baseline and recent takes."""
        return take_view.compare_choices(self.core.compare_options(self.uid), self.core.previous_take(self.uid))

    def set_compare(self, take):
        """Compare with another take (None clears it): a table of readings, the other drawn dashed on the
        graphs; Play plays both."""
        if self.playing:
            self.playback.stop()
        self.compare = take
        if self.compact:
            return
        if self.compare_view is not None:
            self.compare_view.hide()
            self.compare_view.deleteLater()
            self.compare_view = None
        if take is not None:
            self.compare_view = CompareView(self.core, self.uid, take, self.compare_choices())
            self.compare_view.picked.connect(lambda uid: self.set_compare(self.core.take(uid)))
            self.compare_view.closed.connect(lambda: self.set_compare(None))
            self.compare_view.play_both.connect(self.play_audio)
            self.compare_view.play_other.connect(self.play_other_take)
            self.compare_slot.addWidget(self.compare_view)
        overlay = None if take is None else take["tracks"]
        if self.player is not None:
            self.player.set_overlay(overlay)
        if self.graph is not None:
            self.graph.set_overlay(overlay)
            self.compare_label.setText(take_view.compare_text(take))
        if hasattr(self, "compare_btn"):
            self.compare_btn.setText("Close comparison" if take is not None else "Compare")
        self._update_play()

    # --- compact: click to open; hover and selection ---
    def mouseReleaseEvent(self, event):
        if self.compact and self.rect().contains(event.position().toPoint()):
            if self.receivers(self.open_report) > 0:
                self.open_report.emit(self.uid)
            else:
                self.set_compact(False)
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        border = t.q("accent") if self.selected else t.q("border_strong") if (self.hover and self.compact) \
            else t.q("border")
        fill = t.q("accent_soft") if self.selected and self.compact else t.q("surface")
        rounded(p, self.rect(), theme.RADIUS["large"], fill=fill, border=border, width=2 if self.selected else 1)

