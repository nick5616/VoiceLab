"""Live cards: one small card per live reading, in plain words ("Pitch", "Brightness (resonance)",
"Weight", "Clarity", "Intonation"), each with a slim zone bar whose marker glides. The jargon sits in a
"?" help tip. A reading that can't be shown says why in one calm line; noise is reported once, in the
shell's banner, never as a row of hatched bars. Everything comes from live_session.live_cards()."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy

from live_session import card_scale, live_cards
from ui import theme
from ui.components import Card, FlowGrid, HelpTip, Label, ZoneBar

CARD_MIN_WIDTH = 210            # px: a card's narrowest column in the grid
BAR_EVERY = 3                   # live frames between bar updates (~13 per second): each glides in between
NOISY_TEXT = "paused: noisy input"


class LiveCard(Card):
    """One live reading: name, "?" help, the value, and a compact zone bar."""

    def __init__(self, key, title, help_text, large=False):
        super().__init__(padding=theme.M)
        self.key = key
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.body.setSpacing(theme.XS)
        top = QHBoxLayout()
        top.setSpacing(theme.XS)
        self.name = Label(title, role="label", tone="secondary")
        self.help = HelpTip(help_text)
        self.value = Label("—", role="numeric", tone="primary")
        self.value.setFont(theme.font("numeric", size=18 if large else 16, weight=600))
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.name)
        top.addWidget(self.help)
        top.addStretch(1)
        top.addWidget(self.value)
        self.body.addLayout(top)
        spec = card_scale(key)
        self.bar = ZoneBar(spec["scale"], spec["zones"], log=spec["log"], ticks=False, compact=True,
                           labels=spec["labels"])
        if spec["fixed_band"]:
            self.bar.set_target(spec["fixed_band"])
        self.fixed_band = spec["fixed_band"]
        self.body.addWidget(self.bar)
        foot = QHBoxLayout()
        foot.setContentsMargins(4, 0, 4, 0)
        low, high = spec["ends"] or ("", "")
        self.low = Label(low, role="caption", tone="muted")
        self.note = Label("", role="caption", tone="secondary")
        self.note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.high = Label(high, role="caption", tone="muted")
        for w in (self.low, self.note, self.high):
            w.setFixedHeight(16)
        foot.addWidget(self.low)
        foot.addWidget(self.note, 1)
        foot.addWidget(self.high)
        self.body.addLayout(foot)
        self.text = None
        self.unavailable = None
        self.verdict = None

    def show_card(self, card, move_bar=True):
        """Draw one live_cards() entry; the bar moves only when move_bar (it is throttled)."""
        if card["text"] != self.text:
            self.text = card["text"]
            self.value.setText(card["text"])
        if card["verdict"] != self.verdict:
            self.verdict = card["verdict"]
            self.value.set_tone({True: "on_target", False: "off_target"}.get(card["verdict"], "primary"))
        reason = NOISY_TEXT if card["noisy"] else card["unavailable"]
        if reason != self.unavailable:
            self.unavailable = reason
            self.note.setText(reason or "")
        if move_bar:
            self.bar.set_value(card["value"])
        if not self.fixed_band and tuple(card["target"] or ()) != tuple(self.bar.target or ()):
            self.bar.set_target(card["target"])


class LiveCards(FlowGrid):
    """The live cards of the current mode in a grid that reflows with the width."""

    def __init__(self, core, large=False, max_columns=3):
        super().__init__(min_column_width=CARD_MIN_WIDTH, max_columns=max_columns, spacing=theme.S,
                         max_column_width=420)
        self.core = core
        self.large = large
        self.cards = {}
        self.frames = 0
        self.shown_keys = None

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
                widget = LiveCard(card["key"], card["title"], card["help"], large=self.large)
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
