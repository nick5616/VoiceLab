"""Data components: ZoneBar, MetricCard, BigNumber, ShareBar and Sparkline."""
import math

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainterPath, QPen
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout

from ui import anim, theme
from ui.components.base import Label, ThemedWidget, painter, rounded, rounded_path, text_width
from ui.components.chips import ConfidenceChip, Chip
from ui.components.layout import Card, Collapsible

# --- ZONE BAR ---
BAND_HEIGHT = 26            # px: tall enough for a 12 px label inside each zone
COMPACT_BAND = 18           # px: live meters
HEADROOM = 16               # px above the band: the marker's overshoot and the baseline caret
TICK_ROW = 20               # px below the band for tick labels
END_PAD = 10                # px at each end so the marker never clips
GAP = 2                     # px surface gap between zones (dataviz spacer)
MARKER_WIDTH = 5            # px; with a 2 px surface ring
LABEL_PAD = 12              # px a zone label needs beside its text to be drawn
ZONE_BAR_MIN_WIDTH = 160    # px


def _free(a, b, blocked):
    """The parts of the interval (a, b) not covered by any blocked interval."""
    out = [(a, b)]
    for ba, bb in blocked:
        nxt = []
        for x0, x1 in out:
            if bb <= x0 or ba >= x1:
                nxt.append((x0, x1))
                continue
            if ba > x0:
                nxt.append((x0, ba))
            if bb < x1:
                nxt.append((bb, x1))
        out = nxt
    return out


def _fmt(fmt, value):
    return fmt.format(value) if isinstance(fmt, str) else fmt(value)


class ZoneBar(ThemedWidget):
    """A horizontal scale with coloured, labelled zones, an optional target band, a baseline caret,
    an uncertainty whisker and a value marker that glides to new values. Linear or log scale.
    Zones are [{"id", "label", "lo", "hi"}] in scale units (lo/hi None = open to the scale's end),
    the shape of report.ZONES[key]["zones"]."""

    def __init__(self, scale=(0.0, 1.0), zones=None, log=False, unit="", fmt="{:.0f}", ticks=True,
                 labels=True, compact=False, parent=None):
        super().__init__(parent)
        self.scale, self.log = tuple(scale), log
        self.zones = list(zones or [])
        self.unit, self.fmt = unit, fmt
        self.show_ticks, self.show_labels, self.compact = ticks, labels, compact
        self.value = None
        self.shown = None               # the marker's drawn position (fraction), tweened toward value
        self.range = None               # (lo, hi) uncertainty, scale units
        self.target = None              # (lo, hi), scale units
        self.baseline = None
        self.tick_values = None         # None: the zone boundaries
        self.end_labels = None          # ("Dark", "Bright") under the ends
        self.gap_color = "surface"      # the token the bar sits on (its gaps and marker ring)
        self.drawn_labels = []          # [(zone id, text)] from the last paint, for tests
        self.setMinimumWidth(ZONE_BAR_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self._height())

    @classmethod
    def from_spec(cls, spec, **kwargs):
        """A bar for one report.ZONES entry: {"scale", "log", "zones", "unit", ...}."""
        bar = cls(**kwargs)
        bar.set_spec(spec)
        return bar

    def set_spec(self, spec):
        self.unit = spec.get("unit", self.unit)
        self.set_scale(*spec["scale"], log=spec.get("log", False))
        self.set_zones(spec.get("zones"))

    def _height(self):
        band = COMPACT_BAND if self.compact else BAND_HEIGHT
        head = 7 if self.compact else HEADROOM
        foot = TICK_ROW if (self.show_ticks or self.end_labels) and not self.compact else 7
        return head + band + foot

    def sizeHint(self):
        return QSize(320, self._height())

    # --- mapping ---
    def fraction(self, value):
        """Where a value sits on the bar, 0-1, clipped; None for no value."""
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        lo, hi = self.scale
        if self.log:
            value, lo, hi = math.log(max(value, 1e-9)), math.log(lo), math.log(hi)
        if hi == lo:
            return 0.5
        return min(1.0, max(0.0, (value - lo) / (hi - lo)))

    def value_at(self, fraction):
        """The scale value at a fraction of the bar (inverse of fraction())."""
        lo, hi = self.scale
        if self.log:
            return math.exp(math.log(lo) + fraction * (math.log(hi) - math.log(lo)))
        return lo + fraction * (hi - lo)

    def zone_at(self, value):
        """The id of the zone holding value, or None."""
        for z in self.zones:
            lo = self.scale[0] if z.get("lo") is None else z["lo"]
            hi = self.scale[1] if z.get("hi") is None else z["hi"]
            if lo <= value < hi or (value == hi == self.scale[1]):
                return z.get("id")
        return None

    def _zone_span(self, z):
        lo = self.fraction(self.scale[0] if z.get("lo") is None else z["lo"])
        hi = self.fraction(self.scale[1] if z.get("hi") is None else z["hi"])
        return lo, hi

    # --- setters ---
    def set_scale(self, lo, hi, log=None):
        self.scale = (lo, hi)
        if log is not None:
            self.log = log
        self.shown = self.fraction(self.value)
        self.update()

    def set_zones(self, zones):
        self.zones = list(zones or [])
        self.update()

    def set_value(self, value, animate=True):
        """Move the marker to value (None hides it), gliding unless animate is False."""
        new = self.fraction(value)
        self.value = None if new is None else value
        if new is None or self.shown is None or not animate:
            anim.stop(self, "marker")
            self.shown = new
        else:
            anim.tween(self._show, self.shown, new, anim.MOVE_MS, owner=self, name="marker")
        self._describe()
        self.update()

    def _show(self, fraction):
        self.shown = fraction
        self.update()

    def set_range(self, rng):
        """The uncertainty (lo, hi) around the value, drawn as a whisker; None hides it."""
        self.range = tuple(rng) if rng else None
        self.update()

    def set_target(self, band):
        self.target = tuple(band) if band else None
        self._describe()
        self.update()

    def set_baseline(self, value):
        self.baseline = value
        self.update()

    def set_ticks(self, values):
        """Tick values to label under the bar; None = the zone boundaries, [] = none."""
        self.tick_values = values
        self.update()

    def set_end_labels(self, low, high):
        self.end_labels = (low, high) if (low or high) else None
        self.setFixedHeight(self._height())
        self.update()

    def _describe(self):
        if self.value is None:
            self.setToolTip("")
            return
        parts = [f"{_fmt(self.fmt, self.value)} {self.unit}".strip()]
        zone = self.zone_at(self.value)
        label = next((z.get("label") for z in self.zones if z.get("id") == zone), None)
        if label:
            parts.append(label)
        if self.target:
            parts.append(f"target {_fmt(self.fmt, self.target[0])}–{_fmt(self.fmt, self.target[1])} {self.unit}".strip())
        self.setToolTip(" · ".join(parts))
        self.setAccessibleDescription(" · ".join(parts))

    def boundaries(self):
        """Inner zone boundaries in scale units (the default ticks)."""
        out = []
        for z in self.zones[:-1]:
            if z.get("hi") is not None and self.scale[0] < z["hi"] < self.scale[1]:
                out.append(z["hi"])
        return out

    # --- painting ---
    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        x0, x1 = END_PAD, self.width() - END_PAD
        span = max(1.0, x1 - x0)

        def X(f):
            return x0 + f * span

        band_h = COMPACT_BAND if self.compact else BAND_HEIGHT
        top = 7 if self.compact else HEADROOM
        track = QRectF(x0, top, span, band_h)
        radius = band_h / 2 if self.compact else theme.RADIUS["medium"]
        gap = t.q(self.gap_color)

        # zones, inside the track's rounded outline, split by surface gaps
        p.save()
        p.setClipPath(rounded_path(track, radius))
        p.fillRect(track, t.q("surface_raised"))
        spans = []
        for z in self.zones:
            fa, fb = self._zone_span(z)
            if fb <= fa:
                continue
            colors = t.zone(z.get("id"))
            rect = QRectF(X(fa), top, X(fb) - X(fa), band_h)
            p.fillRect(rect, QColor(colors["fill"]))
            spans.append((z, rect, colors))
        p.setPen(Qt.PenStyle.NoPen)
        for _, rect, _ in spans[:-1]:
            if rect.right() < x1 - 1:
                p.fillRect(QRectF(rect.right() - GAP / 2, top, GAP, band_h), gap)
        p.restore()
        rounded(p, track, radius, border=t.q("border"))

        # target band: a green outline a little taller than the band (drawn after the labels, which
        # keep clear of its edges); no wash, which would muddy the zone colours under it
        target_rect = None
        if self.target:
            fa, fb = self.fraction(self.target[0]), self.fraction(self.target[1])
            target_rect = QRectF(X(fa), top - 4, max(4.0, X(fb) - X(fa)), band_h + 8)

        # zone labels: the full label, else the short one, else none (the tooltip has it); each is
        # placed in the widest stretch of its zone that the marker, whisker and target edges leave free
        self.drawn_labels = []
        if self.show_labels:
            font = theme.font("caption", size=11 if self.compact else 12, weight=600)
            p.setFont(font)
            blocked = []
            if self.shown is not None:
                blocked.append((X(self.shown) - 8, X(self.shown) + 8))
                if self.range:
                    blocked.append((X(self.fraction(self.range[0])) - 6, X(self.fraction(self.range[1])) + 6))
            if target_rect is not None:
                blocked += [(target_rect.left() - 4, target_rect.left() + 4),
                            (target_rect.right() - 4, target_rect.right() + 4)]
            for z, rect, colors in spans:
                free = sorted(_free(rect.left(), rect.right(), blocked), key=lambda ab: ab[0] - ab[1])
                options = (z.get("label"), theme.ZONE_SHORT.get(z.get("id")), (z.get("label") or "").split(" ")[-1])
                spot = next(((text, ab) for text in options if text for ab in free
                             if text_width(font, text) + LABEL_PAD <= ab[1] - ab[0]), None)
                if spot:
                    text, (fa, fb) = spot
                    p.setPen(QColor(colors["text"]))
                    p.drawText(QRectF(fa, top, fb - fa, band_h), int(Qt.AlignmentFlag.AlignCenter), text)
                    self.drawn_labels.append((z.get("id"), text))
        if target_rect is not None:
            rounded(p, target_rect, theme.RADIUS["small"] + 1, border=t.q("on_target"), width=2)

        # baseline: a small caret above the band
        if self.baseline is not None and not self.compact:
            x = X(self.fraction(self.baseline))
            caret = QPainterPath()
            caret.moveTo(x - 5, top - 11)
            caret.lineTo(x + 5, top - 11)
            caret.lineTo(x, top - 5)
            caret.closeSubpath()
            p.setPen(QPen(gap, 1.5))
            p.setBrush(t.q("text_secondary"))
            p.drawPath(caret)

        ink = QColor(t.series["pitch"])
        mid = top + band_h / 2
        # uncertainty whisker
        if self.range and self.shown is not None:
            fa, fb = self.fraction(self.range[0]), self.fraction(self.range[1])
            for color, width in ((gap, 5.0), (ink, 2.0)):
                p.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(X(fa), mid), QPointF(X(fb), mid))
                p.drawLine(QPointF(X(fa), mid - 5), QPointF(X(fa), mid + 5))
                p.drawLine(QPointF(X(fb), mid - 5), QPointF(X(fb), mid + 5))

        # value marker: an ink needle with a surface ring
        if self.shown is not None:
            x = X(self.shown)
            over = 4 if self.compact else 7
            needle = QRectF(x - MARKER_WIDTH / 2, top - over, MARKER_WIDTH, band_h + 2 * over)
            rounded(p, needle.adjusted(-2, -2, 2, 2), MARKER_WIDTH / 2 + 2, fill=gap)
            rounded(p, needle, MARKER_WIDTH / 2, fill=ink)

        # ticks and end labels
        if not self.compact:
            font = theme.font("caption")
            p.setFont(font)
            p.setPen(t.q("text_muted"))
            y = top + band_h + (7 if self.target or self.shown is not None else 4)
            row = QRectF(x0, y, span, TICK_ROW - 4)
            used = []
            if self.end_labels:
                low, high = self.end_labels
                if low:
                    p.drawText(row, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), low)
                    used.append((x0, x0 + text_width(font, low)))
                if high:
                    p.drawText(row, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop), high)
                    used.append((x1 - text_width(font, high), x1))
            if self.show_ticks:
                values = self.boundaries() if self.tick_values is None else self.tick_values
                for i, v in enumerate(values):
                    text = _fmt(self.fmt, v) + (f" {self.unit}" if self.unit and i == len(values) - 1 else "")
                    w = text_width(font, text)
                    cx = min(max(X(self.fraction(v)), x0 + w / 2), x1 - w / 2)
                    a, b = cx - w / 2 - 6, cx + w / 2 + 6
                    if any(a < ub and b > ua for ua, ub in used):
                        continue
                    used.append((a, b))
                    p.drawText(QRectF(cx - w / 2 - 1, y, w + 2, TICK_ROW - 4),
                               int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), text)


# --- METRIC CARD ---

class MetricCard(Card):
    """One dimension of a report: a plain headline, the number at the side, a ZoneBar, a
    confidence chip, a one-line explanation and a collapsible "Details" with the jargon. When the
    reading can't be trusted it shows one calm line instead of the bar."""

    def __init__(self, name="", spec=None, parent=None):
        super().__init__(parent=parent)
        self.setMinimumWidth(280)
        self.unavailable = False
        top = QHBoxLayout()
        top.setSpacing(theme.S)
        self.name_label = Label(name, role="label", tone="muted")
        self.change_chip = Chip("", kind="neutral")
        self.change_chip.hide()
        self.confidence = ConfidenceChip("none", compact=False)
        top.addWidget(self.name_label)
        top.addStretch(1)
        top.addWidget(self.confidence)
        self.body.addLayout(top)

        head = QHBoxLayout()
        head.setSpacing(theme.L)
        words = QVBoxLayout()
        words.setSpacing(theme.XS + 2)
        self.headline = Label("", role="heading", wrap=True)
        self.headline.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        words.addWidget(self.headline)
        words.addWidget(self.change_chip, 0, Qt.AlignmentFlag.AlignLeft)
        words.addStretch(1)
        head.addLayout(words, 1)
        values = QVBoxLayout()
        values.setSpacing(0)
        self.value_label = Label("", role="title")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.unit_label = Label("", role="caption", tone="muted")
        self.unit_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        values.addWidget(self.value_label)
        values.addWidget(self.unit_label)
        values.addStretch(1)
        head.addLayout(values)
        self.body.addLayout(head)

        self.bar = ZoneBar.from_spec(spec) if spec else ZoneBar()
        self.body.addWidget(self.bar)
        self.explanation = Label("", role="body", tone="secondary", wrap=True)
        self.body.addWidget(self.explanation)
        self.details = Collapsible("Details")
        self.details_text = Label("", role="caption", tone="secondary", wrap=True)
        self.details_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.add(self.details_text)
        self.body.addWidget(self.details)
        self.body.setSpacing(theme.S + 2)
        self.details.hide()
        self.explanation.hide()

    def set_name(self, name):
        self.name_label.setText(name or "")

    def set_headline(self, text):
        self.headline.setText(text or "")

    def set_value(self, value, display=None, unit="", animate=True):
        """The number at the side (`display` overrides formatting) and the bar's marker."""
        self.value_label.setText(display if display is not None else ("—" if value is None else f"{value:g}"))
        self.unit_label.setText(unit or "")
        self.unit_label.setVisible(bool(unit))
        self.bar.set_value(value, animate=animate)

    def set_explanation(self, text):
        self.explanation.setText(text or "")
        self.explanation.setVisible(bool(text) and not self.unavailable)

    def set_details(self, text):
        self.details_text.setText(text or "")
        self.details.setVisible(bool(text))

    def set_confidence(self, level, reason=None):
        self.confidence.set_level(level or "none", reason)

    def set_change(self, text):
        """A short comparison ("brighter than your last take"), or None."""
        self.change_chip.set_text(text or "")
        self.change_chip.setVisible(bool(text) and not self.unavailable)

    def set_unavailable(self, reason):
        """Show only `reason` as one calm line (no bar, number or explanation); None restores."""
        self.unavailable = bool(reason)
        if reason:
            self.headline.setText(reason)
        self.headline.set_tone("secondary" if reason else "primary")
        self.headline.set_role("body" if reason else "heading")
        for w in (self.bar, self.value_label):
            w.setVisible(not reason)
        self.unit_label.setVisible(not reason and bool(self.unit_label.text()))
        self.explanation.setVisible(not reason and bool(self.explanation.text()))
        self.change_chip.setVisible(not reason and bool(self.change_chip.text))
        if reason:
            self.confidence.set_level("none", self.confidence.reason)

    def set_dimension(self, dim, spec=None):
        """Fill the card from one report dimension (report.build_report()["dimensions"][i]) and,
        optionally, its zone spec (report.ZONES[key])."""
        if spec:
            self.bar.set_spec(spec)
        self.set_name(dim.get("name"))
        level = dim.get("confidence") or "none"
        self.set_confidence(level, dim.get("reason"))
        self.set_details(dim.get("details"))
        change = dim.get("change")
        self.set_change(change.get("text") if change else None)
        if level == "none" or dim.get("value") is None:
            self.set_unavailable(dim.get("headline") or dim.get("reason") or "This take can't show this.")
            self.set_explanation(dim.get("explanation"))
            return
        self.set_unavailable(None)
        self.set_headline(dim.get("headline"))
        self.set_explanation(dim.get("explanation"))
        display, unit = dim.get("display"), dim.get("unit") or ""
        if display and unit and display.endswith(unit):
            display = display[: -len(unit)].strip()
        self.set_value(dim.get("value"), display, unit)
        self.bar.set_target(dim.get("target"))
        self.bar.set_baseline(dim.get("baseline"))
        self.bar.set_range(dim.get("range"))


# --- BIG NUMBER ---

class BigNumber(ThemedWidget):
    """A value, its unit and a sublabel, large: the one number a screen leads with."""

    def __init__(self, value="—", unit="", sublabel="", size=None, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(140)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        box = QVBoxLayout(self)
        box.setContentsMargins(theme.L, theme.M, theme.L, theme.M)
        box.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(theme.XS + 2)
        self.value_label = Label(value, role="figure")
        if size:
            self.value_label.setFont(theme.font("figure", size=size))
        self.unit_label = Label(unit, role="body", tone="secondary")
        row.addWidget(self.value_label, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.unit_label, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        box.addLayout(row)
        self.sublabel = Label(sublabel, role="caption", tone="muted", wrap=True)
        box.addWidget(self.sublabel)
        self.size = size

    def set(self, value, unit=None, sublabel=None):
        self.value_label.setText(str(value))
        if unit is not None:
            self.unit_label.setText(unit)
        if sublabel is not None:
            self.sublabel.setText(sublabel)

    def restyle(self):
        if self.size:
            self.value_label.setFont(theme.font("figure", size=self.size))

    def paintEvent(self, event):
        t = theme.current()
        rounded(painter(self), self.rect(), theme.RADIUS["large"], fill=t.q("surface_raised"))


# --- SHARE BAR ---

class ShareBar(ThemedWidget):
    """Shares of a whole (register shares of a take) as one bar with 2 px gaps, and a legend
    that names each part, so colour never carries it alone."""

    BAR = 10                    # px

    def __init__(self, shares=None, labels=None, legend=True, parent=None):
        super().__init__(parent)
        self.legend = legend
        self.labels = dict(labels or {})
        self.segments = []
        self.setMinimumWidth(160)
        self.setFixedHeight(self.BAR + (24 if legend else 2))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if shares:
            self.set_shares(shares)

    def set_shares(self, shares, labels=None):
        """{key: fraction} or [(key, fraction)]; keys are register names or any id."""
        if labels:
            self.labels.update(labels)
        items = shares.items() if isinstance(shares, dict) else shares
        self.segments = [(k, float(v)) for k, v in items if v and v > 0]
        self.setToolTip(" · ".join(f"{self._label(k)} {v:.0%}" for k, v in self.segments))
        self.update()

    def _label(self, key):
        return self.labels.get(key, str(key).capitalize())

    def _color(self, key):
        t = theme.current()
        if key in t.register:
            return QColor(t.register[key])
        return QColor(t.zone(key)["edge"])

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        w = self.width()
        track = QRectF(0, 1, w, self.BAR)
        p.save()
        p.setClipPath(rounded_path(track, self.BAR / 2))
        p.fillRect(track, t.q("surface_raised"))
        total = sum(v for _, v in self.segments) or 1.0
        x = 0.0
        for i, (key, v) in enumerate(self.segments):
            seg = w * v / total
            p.fillRect(QRectF(x, 1, seg - (GAP if i < len(self.segments) - 1 else 0), self.BAR), self._color(key))
            x += seg
        p.restore()
        if not self.legend:
            return
        font = theme.font("caption")
        p.setFont(font)
        x = 0.0
        for key, v in self.segments:
            text = f"{self._label(key)} {v / total:.0%}"
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._color(key))
            p.drawRoundedRect(QRectF(x, self.BAR + 9, 8, 8), 2, 2)
            p.setPen(t.q("text_secondary"))
            p.drawText(QRectF(x + 12, self.BAR + 4, w - x, 18), int(Qt.AlignmentFlag.AlignVCenter), text)
            x += 12 + text_width(font, text) + theme.M


# --- SPARKLINE ---

class Sparkline(ThemedWidget):
    """A small trend line (progress). The line is quiet; the latest point is the accent; an optional
    target band is a green wash. None values leave gaps."""

    def __init__(self, values=None, target=None, height=36, parent=None):
        super().__init__(parent)
        self.values, self.target = list(values or []), target
        self.setFixedHeight(height)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values, target=None):
        self.values, self.target = list(values), target
        self.update()

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        pts = [(i, v) for i, v in enumerate(self.values) if v is not None and not math.isnan(v)]
        if not pts:
            return
        vals = [v for _, v in pts] + (list(self.target) if self.target else [])
        lo, hi = min(vals), max(vals)
        if hi == lo:
            lo, hi = lo - 1, hi + 1
        pad = 5
        w, h = self.width() - 2 * pad, self.height() - 2 * pad
        n = max(1, len(self.values) - 1)

        def pt(i, v):
            return QPointF(pad + w * i / n, pad + h * (1 - (v - lo) / (hi - lo)))

        if self.target:
            y0, y1 = pt(0, self.target[1]).y(), pt(0, self.target[0]).y()
            p.fillRect(QRectF(pad, y0, w, max(1.0, y1 - y0)), t.q("on_target", 34))
        p.setPen(QPen(t.q("text_muted"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path, prev = QPainterPath(), None
        for i, v in pts:
            if prev is not None and i == prev + 1:
                path.lineTo(pt(i, v))
            else:
                path.moveTo(pt(i, v))
            prev = i
        p.drawPath(path)
        last = pt(*pts[-1])
        p.setPen(QPen(t.q("surface"), 2))
        p.setBrush(t.q("accent"))
        p.drawEllipse(last, 4.5, 4.5)
