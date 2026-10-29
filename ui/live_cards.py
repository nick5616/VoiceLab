"""Live cards: one small card per live reading, plain word first and the number second ("Lighter", then
"-1.9 dB"), each with a slim zone bar whose marker glides and a one-word cue for how far to trust it. The jargon
sits in a "?" help tip. A reading that can't be shown says why in one calm line; noise is reported once, in the
shell's banner, never as a row of hatched bars. A reading the voice stopped feeding turns muted ("last heard
3 s ago") and then empties. Everything comes from live_session.live_cards()."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QSizePolicy, QWidget

from live_session import age_text, card_scale, live_cards
from ui import theme
from ui.components import Card, FlowGrid, HelpTip, Label, ZoneBar

CARD_MIN_WIDTH = 210            # px: a card's narrowest column in the grid
BAR_EVERY = 3                   # live frames between bar updates (~13 per second): each glides in between
NOISY_TEXT = "paused: noisy input"
STALE_BAR_OPACITY = 0.4         # the bar fades to this once a reading is stale
VALUE_PX, VALUE_PX_LARGE = 17, 19   # px: the plain word
NUMBER_PX = 13                  # px: the number beside it


class SizedLabel(Label):
    """A label whose size and weight survive a tone or theme change (Label.restyle resets the role's font)."""

    def __init__(self, text, size, weight=None, tone="primary"):
        self.size, self.weight = size, weight
        super().__init__(text, role="numeric", tone=tone)

    def restyle(self, *_):
        super().restyle()
        self.setFont(theme.font("numeric", size=self.size, weight=self.weight))


class LiveCard(Card):
    """One live reading: name, "?" help, the word, its number, a compact zone bar and the trust cue."""

    def __init__(self, key, title, help_text, large=False, compact=False):
        super().__init__(padding=theme.M)
        self.key = key
        self.large = large
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.body.setSpacing(theme.XS)
        self.top = top = QHBoxLayout()
        top.setSpacing(theme.XS)
        self.name = Label(title, role="label", tone="secondary")
        self.help = HelpTip(help_text)
        self.note = Label("", role="caption", tone="secondary")
        self.note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.name)
        top.addWidget(self.help)
        top.addStretch(1)
        top.addWidget(self.note)
        self.body.addLayout(top)
        self.value_box = QWidget()
        mid = QHBoxLayout(self.value_box)
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(theme.S)
        self.value = SizedLabel("—", VALUE_PX_LARGE if large else VALUE_PX, weight=600)
        self.number = SizedLabel("", NUMBER_PX, tone="muted")
        mid.addWidget(self.value)
        mid.addWidget(self.number)
        mid.addStretch(1)
        self.body.addWidget(self.value_box)
        spec = card_scale(key)
        self.bar = ZoneBar(spec["scale"], spec["zones"], log=spec["log"], ticks=False, compact=True,
                           labels=spec["labels"])
        if spec["fixed_band"]:
            self.bar.set_target(spec["fixed_band"])
        self.fixed_band = spec["fixed_band"]
        self.fade = QGraphicsOpacityEffect(self.bar)
        self.fade.setOpacity(1.0)
        self.bar.setGraphicsEffect(self.fade)
        self.body.addWidget(self.bar)
        foot = QHBoxLayout()
        foot.setContentsMargins(4, 0, 4, 0)
        low, high = spec["ends"] or ("", "")
        self.low = Label(low, role="caption", tone="muted")
        self.high = Label(high, role="caption", tone="muted")
        for w in (self.low, self.high):
            w.setFixedHeight(16)
        foot.addWidget(self.low)
        foot.addStretch(1)
        foot.addWidget(self.high)
        self.foot = foot
        self.body.addLayout(foot)
        self.compact = False
        self.cue_now = ""
        self.text = None
        self.number_text = None
        self.unavailable = None
        self.verdict = None
        self.stale = False
        self.empty = True
        self.trust = None
        self.cue = None
        if compact:
            self.set_compact(True)

    def set_compact(self, compact):
        """The short-window layout: the word and number share the name's row, there are no end words under the
        bar and the cue replaces the number when it matters, so a card is a third shorter."""
        if compact == self.compact:
            return
        self.compact = compact
        for w in (self.low, self.high, self.note):
            w.setVisible(not compact)
        if compact:
            self.body.removeWidget(self.value_box)
            self.top.addWidget(self.value_box)
        else:
            self.top.removeWidget(self.value_box)
            self.body.insertWidget(1, self.value_box)
        pad = theme.S if compact else theme.M
        self.layout().setContentsMargins(pad, pad, pad, pad)
        self._show_number()

    def show_card(self, card, move_bar=True):
        """Draw one live_cards() entry; the bar moves only when move_bar (it is throttled)."""
        stale = card["stale"]
        if card["text"] != self.text:
            self.text = card["text"]
            self.value.setText(card["text"])
        self.number_text = card["number"]
        verdict = None if stale else card["verdict"]
        empty = card["value"] is None
        if verdict != self.verdict or stale != self.stale or empty != self.empty:
            self.verdict, self.stale, self.empty = verdict, stale, empty
            self.fade.setOpacity(STALE_BAR_OPACITY if stale or empty else 1.0)
            muted = stale or empty
            self.value.set_tone("muted" if muted else {True: "on_target", False: "off_target"}.get(verdict, "primary"))
        reason = NOISY_TEXT if card["noisy"] else card["unavailable"]
        if reason != self.unavailable:
            self.unavailable = reason
        cue = reason or (age_text(card["age"]) if stale else card["trust_word"])
        self.cue_now = reason or (age_text(card["age"]) if stale else "")
        if cue != self.cue:
            self.cue = cue
            self.note.setText(cue or "")
            self.note.set_tone("secondary" if reason else "muted")
        self._show_number()
        trust = card["trust"]
        if trust != self.trust:
            self.trust = trust
            self.setToolTip(card["trust_reason"] if card["trust_reason"] else "")
        if move_bar:
            self.bar.set_value(card["value"])
        if not self.fixed_band and tuple(card["target"] or ()) != tuple(self.bar.target or ()):
            self.bar.set_target(card["target"])


    def _show_number(self):
        """The number beside the word; in the short layout a problem (paused, last heard) takes its place."""
        text = self.cue_now if self.compact and self.cue_now else (self.number_text or "")
        if text != self.number.text():
            self.number.setText(text)


class LiveCards(FlowGrid):
    """The live cards of the current mode in a grid that reflows with the width."""

    def __init__(self, core, large=False, max_columns=3):
        super().__init__(min_column_width=CARD_MIN_WIDTH, max_columns=max_columns, spacing=theme.S,
                         max_column_width=420)
        self.core = core
        self.large = large
        self.compact = False
        self.cards = {}
        self.frames = 0
        self.shown_keys = None

    def set_compact(self, compact):
        """The short-window layout for every card."""
        self.compact = compact
        for widget in self.cards.values():
            widget.set_compact(compact)
        self.updateGeometry()

    def show_snapshot(self, snapshot):
        """Draw the snapshot's cards; the set of cards changes with the mode and the EGG."""
        cards = live_cards(snapshot, self.core.profile)
        keys = [c["key"] for c in cards]
        if keys != self.shown_keys:
            self._rebuild(cards)
        self.frames += 1
        move = self.frames % BAR_EVERY == 0 or not snapshot.get("voiced")
        for card in cards:
            self.cards[card["key"]].show_card(card, move_bar=move)

    def _rebuild(self, cards):
        for widget in self.cards.values():
            widget.hide()
        for card in cards:
            if card["key"] not in self.cards:
                widget = LiveCard(card["key"], card["title"], card["help"], large=self.large, compact=self.compact)
                self.cards[card["key"]] = widget
                self.add(widget)
        for card in cards:
            self.cards[card["key"]].show()
        order = {c["key"]: i for i, c in enumerate(cards)}
        self.items.sort(key=lambda item: order.get(item[0].key, 99))
        self.shown_keys = [c["key"] for c in cards]
        self.relayout()

    def visible_keys(self):
        return list(self.shown_keys or [])
