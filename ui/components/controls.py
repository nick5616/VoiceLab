"""Controls: Button, IconButton, SegmentedControl, Tabs, RecordButton, NavRail, Toggle and Select."""
import time

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QComboBox, QPushButton, QSizePolicy

from ui import anim, theme
from ui.components.base import ThemedWidget, draw_icon, icon, mix, painter, rgba, rounded, text_width
from ui.components.chips import paint_badge

VARIANTS = ("primary", "secondary", "ghost", "danger")
BUTTON_HEIGHT = 34          # px: comfortable click target at 100% scaling (WCAG 2.5.8 asks 24)
SMALL_HEIGHT = 28           # px
ICON_SIZE = 16              # px
SEGMENT_HEIGHT = 34         # px
TAB_HEIGHT = 38             # px
RECORD_DIAMETER = 64        # px
RECORD_STATES = ("idle", "recording", "analysing", "disabled")
RECORD_CAPTIONS = {"idle": "Record", "analysing": "Analysing…", "disabled": "Mic unavailable"}
NAV_WIDTH = 88              # px
NAV_ITEM = 64               # px per item
TOGGLE_SIZE = (38, 22)      # px


def _options(options):
    """[(key, label, icon)] from a list of keys, (key, label) or (key, label, icon) tuples."""
    out = []
    for o in options:
        if isinstance(o, str):
            out.append((o, o, None))
        else:
            out.append((o[0], o[1], o[2] if len(o) > 2 else None))
    return out


def spinner_pixmap(phase, color, size=ICON_SIZE, width=2.0):
    """A spinner frame (a 100° arc over a faint ring) as a QPixmap, for button icons."""
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(width, width, size - 2 * width, size - 2 * width)
    faint = QColor(color)
    faint.setAlpha(60)
    p.setPen(QPen(faint, width))
    p.drawEllipse(rect)
    p.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(rect, int((90 - phase * 360) * 16), int(-100 * 16))
    p.end()
    return pm


# --- BUTTONS ---

class Button(QPushButton):
    """A push button in one of four variants: primary (the one main action of a view), secondary,
    ghost (low emphasis) and danger. `icon` is a qtawesome name. set_loading(True) shows an inline
    spinner and ignores clicks until it's done."""

    def __init__(self, text="", variant="secondary", icon=None, small=False, parent=None):
        super().__init__(text, parent)
        if variant not in VARIANTS:
            raise ValueError(f"variant must be one of {VARIANTS}")
        self.variant, self.icon_name, self.small = variant, icon, small
        self.loading = False
        self._spin = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        theme.changed.connect(self.restyle)
        self.restyle()

    def colors(self):
        """{bg, hover, pressed, text, border} for this variant in the current theme (QColors)."""
        t = theme.current()
        clear = QColor(0, 0, 0, 0)
        if self.variant == "primary":
            return {"bg": t.q("accent"), "hover": mix(t.q("accent"), t.q("text"), 0.14),
                    "pressed": mix(t.q("accent"), t.q("bg"), 0.18), "text": t.q("accent_text"), "border": clear}
        if self.variant == "secondary":
            raised = t.q("surface_raised")
            return {"bg": raised, "hover": mix(raised, t.q("text"), 0.06), "pressed": mix(raised, t.q("text"), 0.11),
                    "text": t.q("text"), "border": t.q("border_strong" if not t.dark else "border")}
        if self.variant == "ghost":
            return {"bg": clear, "hover": t.q("surface_raised"), "pressed": mix(t.q("surface_raised"), t.q("text"), 0.08),
                    "text": t.q("text_secondary"), "border": clear}
        return {"bg": clear, "hover": t.q("error", 34), "pressed": t.q("error", 60), "text": t.q("error"),
                "border": t.q("error", 140)}

    def restyle(self, *_):
        t, c = theme.current(), self.colors()
        height = SMALL_HEIGHT if self.small else BUTTON_HEIGHT
        pad = theme.M if self.text() else 0
        hover_text = t["text"] if self.variant == "ghost" else c["text"].name()
        self.setStyleSheet(f"""
QPushButton {{ background: {rgba(c['bg'])}; color: {c['text'].name()}; border: 1px solid {rgba(c['border'])};
    border-radius: {theme.RADIUS['medium']}px; padding: 0 {pad + (2 if not self.small else 0)}px;
    min-height: {height - 2}px; max-height: {height - 2}px; }}
QPushButton:hover {{ background: {rgba(c['hover'])}; color: {hover_text}; }}
QPushButton:pressed {{ background: {rgba(c['pressed'])}; }}
QPushButton:focus {{ border: 1px solid {t['accent'] if self.variant != 'primary' else t['text']}; }}
QPushButton:disabled {{ background: {t['surface_raised'] if self.variant in ('primary', 'secondary') else 'transparent'};
    color: {t['text_muted']}; border: 1px solid {t['border']}; }}
""")
        self.setFont(theme.font("label", size=12 if self.small else None))
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._apply_icon()

    def _apply_icon(self):
        color = self.colors()["text"]
        if not self.isEnabled():
            color = theme.color("text_muted")
        if self.loading:
            self.setIcon(QIcon(spinner_pixmap(self._spin, color)))
        elif self.icon_name:
            self.setIcon(icon(self.icon_name, color.name()))
        else:
            self.setIcon(QIcon())

    def set_icon(self, name):
        self.icon_name = name
        self._apply_icon()

    def set_variant(self, variant):
        self.variant = variant
        self.restyle()

    @property
    def spin_phase(self):
        return self._spin

    @spin_phase.setter
    def spin_phase(self, value):
        self._spin = value
        if self.loading:
            self._apply_icon()

    def set_loading(self, loading, text=None):
        """Show an inline spinner (and optionally new text) while work runs; clicks are ignored."""
        self.loading = bool(loading)
        if text is not None:
            self.setText(text)
        self.setCursor(Qt.CursorShape.BusyCursor if loading else Qt.CursorShape.PointingHandCursor)
        if loading:
            anim.spin(self)
        else:
            anim.stop(self, "spin_phase")
        self._apply_icon()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.EnabledChange:
            self._apply_icon()

    def mousePressEvent(self, event):
        if not self.loading:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if not self.loading:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if not self.loading:
            super().keyPressEvent(event)


class IconButton(Button):
    """A square button with only an icon; `tooltip` names it (required: icons need words too)."""

    def __init__(self, icon, tooltip, variant="ghost", size=BUTTON_HEIGHT, parent=None):
        super().__init__("", variant=variant, icon=icon, parent=parent)
        self.setFixedSize(size, size)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)


# --- SEGMENTED CONTROL AND TABS ---

class SegmentedControl(ThemedWidget):
    """A few mutually exclusive choices side by side (Speak / Sing). The selection slides."""

    changed = pyqtSignal(str)

    def __init__(self, options, current=None, parent=None):
        super().__init__(parent)
        self.options = _options(options)
        self.keys = [o[0] for o in self.options]
        self.index = self.keys.index(current) if current in self.keys else 0
        self.shown = float(self.index)      # the drawn selection position, tweened
        self.hover = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(SEGMENT_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.restyle()

    def restyle(self):
        font = theme.font("label")
        widest = max(text_width(font, label) + (ICON_SIZE + theme.XS if ic else 0) for _, label, ic in self.options)
        self.segment = int(widest + 2 * theme.L)
        self.setFixedWidth(self.segment * len(self.options) + 6)

    def current(self):
        return self.keys[self.index]

    def set_current(self, key, animate=True, emit=False):
        i = self.keys.index(key)
        if i == self.index:
            return
        self.index = i
        anim.tween(self._show, self.shown, float(i), anim.MOVE_MS if animate else 0, owner=self, name="slide")
        self.update()
        if emit:
            self.changed.emit(key)

    def _show(self, v):
        self.shown = v
        self.update()

    def _at(self, x):
        return min(len(self.keys) - 1, max(0, int((x - 3) // self.segment)))

    def mouseMoveEvent(self, event):
        self.hover = self._at(event.position().x())
        self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.update()

    def mouseReleaseEvent(self, event):
        self.set_current(self.keys[self._at(event.position().x())], emit=True)

    def keyPressEvent(self, event):
        step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}.get(event.key())
        if step:
            self.set_current(self.keys[(self.index + step) % len(self.keys)], emit=True)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        rounded(p, self.rect(), theme.RADIUS["medium"] + 1, fill=t.q("bg"), border=t.q("border"))
        sel = QRectF(3 + self.shown * self.segment, 3, self.segment, self.height() - 6)
        rounded(p, sel, theme.RADIUS["medium"] - 2, fill=t.q("surface_raised"),
                border=t.q("border_strong" if t.dark else "border"))
        if self.hasFocus():
            rounded(p, sel.adjusted(-1, -1, 1, 1), theme.RADIUS["medium"] - 1, border=t.q("accent"), width=1.5)
        p.setFont(theme.font("label"))
        for i, (_, label, ic) in enumerate(self.options):
            rect = QRectF(3 + i * self.segment, 0, self.segment, self.height())
            ink = t.q("text") if i == self.index or i == self.hover else t.q("text_secondary")
            if ic:
                w = ICON_SIZE + theme.XS + text_width(p.font(), label)
                x = rect.center().x() - w / 2
                draw_icon(p, ic, QRectF(x, (self.height() - ICON_SIZE) / 2, ICON_SIZE, ICON_SIZE), ink.name())
                rect = QRectF(x + ICON_SIZE + theme.XS, 0, w, self.height())
                p.setPen(ink)
                p.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), label)
            else:
                p.setPen(ink)
                p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), label)


class Tabs(ThemedWidget):
    """Underlined tabs for switching views within a screen; the underline slides. Tabs can carry badges."""

    changed = pyqtSignal(str)

    def __init__(self, options, current=None, parent=None):
        super().__init__(parent)
        self.options = _options(options)
        self.keys = [o[0] for o in self.options]
        self.index = self.keys.index(current) if current in self.keys else 0
        self.badges = {}
        self.line = None                    # (x, width) of the underline as drawn
        self.hover = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(TAB_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _rects(self):
        font = theme.font("label")
        x, out = 0.0, []
        for key, label, _ in self.options:
            w = text_width(font, label) + 2 * theme.M + (24 if self.badges.get(key) else 0)
            out.append(QRectF(x, 0, w, TAB_HEIGHT))
            x += w + theme.XS
        return out

    def sizeHint(self):
        r = self._rects()
        return QSize(int(r[-1].right()) if r else 0, TAB_HEIGHT)

    def current(self):
        return self.keys[self.index]

    def set_badge(self, key, text):
        self.badges[key] = text
        self.updateGeometry()
        self.update()

    def set_current(self, key, animate=True, emit=False):
        i = self.keys.index(key)
        if i == self.index:
            return
        old = self._line_for(self.index)
        self.index = i
        new = self._line_for(i)
        anim.tween(lambda f: self._slide(old, new, f), 0.0, 1.0, anim.MOVE_MS if animate else 0, owner=self,
                   name="slide")
        if emit:
            self.changed.emit(key)

    def _line_for(self, i):
        r = self._rects()[i]
        return (r.x() + theme.S, r.width() - 2 * theme.S)

    def _slide(self, old, new, f):
        self.line = (old[0] + (new[0] - old[0]) * f, old[1] + (new[1] - old[1]) * f)
        self.update()

    def _at(self, x):
        for i, r in enumerate(self._rects()):
            if r.left() <= x <= r.right():
                return i
        return None

    def mouseMoveEvent(self, event):
        self.hover = self._at(event.position().x())
        self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.update()

    def mouseReleaseEvent(self, event):
        i = self._at(event.position().x())
        if i is not None:
            self.set_current(self.keys[i], emit=True)

    def keyPressEvent(self, event):
        step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}.get(event.key())
        if step:
            self.set_current(self.keys[(self.index + step) % len(self.keys)], emit=True)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        p.fillRect(QRectF(0, self.height() - 1, self.width(), 1), t.q("border"))
        font = theme.font("label")
        p.setFont(font)
        for i, (r, (key, label, _)) in enumerate(zip(self._rects(), self.options)):
            p.setPen(t.q("text") if i in (self.index, self.hover) else t.q("text_secondary"))
            text_rect = QRectF(r.x() + theme.M, 0, text_width(font, label) + 2, r.height() - 2)
            p.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), label)
            if self.badges.get(key):
                paint_badge(p, QRectF(text_rect.right() + 6, (r.height() - 18) / 2 - 1, 18, 18), str(self.badges[key]))
                p.setFont(font)
        x, w = self.line or self._line_for(self.index)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.q("accent"))
        p.drawRoundedRect(QRectF(x, self.height() - 3, w, 3), 1.5, 1.5)
        if self.hasFocus():
            rounded(p, self._rects()[self.index].adjusted(1, 4, -1, -5), theme.RADIUS["small"], border=t.q("accent"))


# --- RECORD BUTTON ---

class RecordButton(ThemedWidget):
    """The big record control: idle (red circle, mic), recording (stop square, a slow pulse and
    the elapsed time), analysing (a spinner ring), disabled. Emits clicked in idle and recording."""

    clicked = pyqtSignal()

    def __init__(self, diameter=RECORD_DIAMETER, clock=time.monotonic, parent=None):
        super().__init__(parent)
        self.diameter, self.clock = diameter, clock
        self.state = "idle"
        self.elapsed = 0.0
        self.started = None
        self.captions = dict(RECORD_CAPTIONS)
        self.pulse_phase = 0.0
        self.spin_phase = 0.0
        self.hover = False
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        ring = diameter + 2 * 18          # the pulse needs room
        self.setFixedSize(max(ring, 110), ring + 26)
        self.setAccessibleName("Record")

    def set_state(self, state):
        if state not in RECORD_STATES:
            raise ValueError(f"state must be one of {RECORD_STATES}")
        if state == self.state:
            return
        self.state = state
        anim.stop(self, "pulse_phase")
        anim.stop(self, "spin_phase")
        self.pulse_phase = self.spin_phase = 0.0
        if state == "recording":
            self.started, self.elapsed = self.clock(), 0.0
            self.timer.start()
            anim.pulse(self)
        else:
            self.timer.stop()
        if state == "analysing":
            anim.spin(self, ms=1100)
        self.setCursor(Qt.CursorShape.PointingHandCursor if state in ("idle", "recording")
                       else Qt.CursorShape.ArrowCursor)
        self.setAccessibleName({"idle": "Record", "recording": "Stop recording"}.get(state, self.caption()))
        self.update()

    def set_elapsed(self, seconds):
        """Show this elapsed time (the clock keeps counting from it)."""
        self.elapsed = float(seconds)
        self.started = self.clock() - self.elapsed
        self.update()

    def _tick(self):
        if self.started is not None:
            self.elapsed = self.clock() - self.started
            self.update()

    def caption(self):
        if self.state == "recording":
            s = int(self.elapsed)
            return f"{s // 60}:{s % 60:02d}"
        return self.captions[self.state]

    def mouseReleaseEvent(self, event):
        if self.state in ("idle", "recording") and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.state in ("idle", "recording"):
            self.clicked.emit()
        else:
            super().keyPressEvent(event)

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        r = self.diameter / 2
        c = QPointF(self.width() / 2, r + 18)
        red = t.q("recording")
        white = QColor("#ffffff")
        if self.hasFocus():
            p.setPen(QPen(t.q("accent"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, r + 6, r + 6)
        if self.state == "recording":
            p.setBrush(Qt.BrushStyle.NoBrush)
            if theme.reduce_motion:          # no pulse, but still plainly live
                p.setPen(QPen(t.q("recording", 120), 2.5))
                p.drawEllipse(c, r + 7, r + 7)
            for k in () if theme.reduce_motion else (self.pulse_phase, (self.pulse_phase + 0.5) % 1.0):
                ring = QColor(red)
                ring.setAlpha(int(150 * (1 - k)))
                p.setPen(QPen(ring, 2.5))
                p.drawEllipse(c, r + 3 + 14 * k, r + 3 + 14 * k)
        elif self.state == "idle" and self.hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.q("recording", 50))
            p.drawEllipse(c, r + 6, r + 6)

        p.setPen(Qt.PenStyle.NoPen)
        if self.state in ("idle", "recording"):
            p.setBrush(red)
            p.drawEllipse(c, r, r)
        else:
            p.setBrush(t.q("surface_raised"))
            p.setPen(QPen(t.q("border_strong" if self.state == "disabled" else "border"), 1.5))
            p.drawEllipse(c, r - 1, r - 1)
        if self.state == "recording":
            s = r * 0.62
            rounded(p, QRectF(c.x() - s / 2, c.y() - s / 2, s, s), 5, fill=white)
        else:
            size = r * 0.8
            ink = "#ffffff" if self.state == "idle" else t["text_muted"]
            draw_icon(p, "ph.microphone-fill" if self.state == "idle" else "ph.microphone",
                      QRectF(c.x() - size / 2, c.y() - size / 2, size, size), ink)
        if self.state == "analysing":
            p.setPen(QPen(t.q("accent"), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            arc = QRectF(c.x() - r - 5, c.y() - r - 5, 2 * r + 10, 2 * r + 10)
            p.drawArc(arc, int((90 - self.spin_phase * 360) * 16), -110 * 16)

        caption_rect = QRectF(0, c.y() + r + 12, self.width(), 22)
        if self.state == "recording":
            p.setFont(theme.font("numeric", size=15, weight=600))
            p.setPen(t.q("text"))
        else:
            p.setFont(theme.font("label"))
            p.setPen(t.q("text_secondary" if self.state != "disabled" else "text_muted"))
        p.drawText(caption_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), self.caption())


# --- NAV RAIL ---

class NavRail(ThemedWidget):
    """Vertical navigation: an icon over a short label per screen, a sliding pill on the selected
    one, optional badges. `items` and `footer` are [(key, icon, label)]; footer items sit at the bottom."""

    selected = pyqtSignal(str)

    def __init__(self, items, footer=(), current=None, parent=None):
        super().__init__(parent)
        self.items, self.footer = list(items), list(footer)
        self.keys = [k for k, _, _ in self.items + self.footer]
        self.index = self.keys.index(current) if current in self.keys else 0
        self.shown = None                   # drawn pill top (px), tweened
        self.badges = {}
        self.hover = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(NAV_WIDTH)
        self.setMinimumHeight(NAV_ITEM * len(self.keys) + theme.XL)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def current(self):
        return self.keys[self.index]

    def item_rect(self, key):
        i = self.keys.index(key)
        if i < len(self.items):
            top = theme.M + i * NAV_ITEM
        else:
            top = self.height() - theme.M - (len(self.keys) - i) * NAV_ITEM
        return QRectF(0, top, self.width(), NAV_ITEM)

    def set_badge(self, key, text):
        """A count or word on an item's icon; None or "" removes it."""
        self.badges[key] = text
        self.update()

    def set_current(self, key, animate=True, emit=False):
        i = self.keys.index(key)
        if i == self.index:
            return
        start = self.item_rect(self.keys[self.index]).top()
        self.index = i
        anim.tween(self._show, start, self.item_rect(key).top(), anim.MOVE_MS if animate else 0, owner=self,
                   name="pill")
        if emit:
            self.selected.emit(key)

    def _show(self, top):
        self.shown = top
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        anim.stop(self, "pill")
        self.shown = None

    def _at(self, pos):
        for key in self.keys:
            if self.item_rect(key).contains(pos):
                return key
        return None

    def mouseMoveEvent(self, event):
        self.hover = self._at(event.position())
        self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.update()

    def mouseReleaseEvent(self, event):
        key = self._at(event.position())
        if key is not None:
            self.set_current(key, emit=True)

    def keyPressEvent(self, event):
        step = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1}.get(event.key())
        if step:
            self.set_current(self.keys[(self.index + step) % len(self.keys)], emit=True)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        pill_w, pill_h = 56, 32
        px = (self.width() - pill_w) / 2
        sel_top = self.shown if self.shown is not None else self.item_rect(self.current()).top()
        if self.hover is not None and self.hover != self.current():
            hr = self.item_rect(self.hover)
            rounded(p, QRectF(px, hr.top() + 6, pill_w, pill_h), pill_h / 2, fill=t.q("surface_raised"))
        pill = QRectF(px, sel_top + 6, pill_w, pill_h)
        rounded(p, pill, pill_h / 2, fill=t.q("accent_soft"))
        if self.hasFocus():
            rounded(p, pill.adjusted(-2, -2, 2, 2), pill_h / 2 + 2, border=t.q("accent"), width=1.5)
        for key, ic, label in self.items + self.footer:
            r = self.item_rect(key)
            on = key == self.current()
            ink = t["accent"] if on else t["text_secondary"]
            draw_icon(p, ic, QRectF(r.center().x() - 11, r.top() + 6 + (pill_h - 22) / 2, 22, 22), ink)
            p.setFont(theme.font("caption", weight=600 if on else 400))
            p.setPen(t.q("text") if on else t.q("text_secondary"))
            p.drawText(QRectF(2, r.top() + pill_h + 9, self.width() - 4, 18), int(Qt.AlignmentFlag.AlignHCenter), label)
            badge = self.badges.get(key)
            if badge:
                text = str(badge)
                bw = max(18.0, text_width(theme.font("caption", size=11, weight=700), text) + 10)
                paint_badge(p, QRectF(px + pill_w - bw / 2 - 4, r.top() + 1, bw, 18), text)


# --- TOGGLE AND SELECT ---

class Toggle(ThemedWidget):
    """An on/off switch with an optional label to its right; the knob slides."""

    toggled = pyqtSignal(bool)

    def __init__(self, checked=False, text="", parent=None):
        super().__init__(parent)
        self.checked, self.text = bool(checked), text
        self.knob = 1.0 if checked else 0.0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        self.setAccessibleName(text or "Toggle")
        self.restyle()

    def restyle(self):
        w = TOGGLE_SIZE[0] + (theme.S + text_width(theme.font("body"), self.text) + 2 if self.text else 0)
        self.setFixedWidth(int(w) + 2)

    def isChecked(self):
        return self.checked

    def set_checked(self, checked, animate=True, emit=False):
        checked = bool(checked)
        if checked == self.checked:
            return
        self.checked = checked
        anim.tween(self._move, self.knob, 1.0 if checked else 0.0, anim.ENTER_MS if animate else 0, owner=self,
                   name="knob")
        if emit:
            self.toggled.emit(checked)

    setChecked = set_checked

    def _move(self, v):
        self.knob = v
        self.update()

    def mouseReleaseEvent(self, event):
        if self.rect().contains(event.position().toPoint()):
            self.set_checked(not self.checked, emit=True)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.set_checked(not self.checked, emit=True)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        w, h = TOGGLE_SIZE
        track = QRectF(1, (self.height() - h) / 2, w, h)
        off, on = t.q("surface_raised"), t.q("accent")
        rounded(p, track, h / 2, fill=mix(off, on, self.knob),
                border=mix(t.q("border_strong"), on, self.knob))
        if self.hasFocus():
            rounded(p, track.adjusted(-2, -2, 2, 2), h / 2 + 2, border=t.q("accent"), width=1.5)
        d = h - 6
        x = track.left() + 3 + self.knob * (w - 6 - d)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix(t.q("text_secondary"), t.q("accent_text"), self.knob))
        p.drawEllipse(QRectF(x, track.top() + 3, d, d))
        if self.text:
            p.setFont(theme.font("body"))
            p.setPen(t.q("text"))
            p.drawText(QRectF(w + theme.S + 1, 0, self.width(), self.height()), int(Qt.AlignmentFlag.AlignVCenter),
                       self.text)


class Select(QComboBox):
    """A drop-down of (key, label) options; `changed` gives the key."""

    changed = pyqtSignal(str)

    def __init__(self, options=(), current=None, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(140)
        self.set_options(options, current)
        self.currentIndexChanged.connect(lambda i: i >= 0 and self.changed.emit(self.itemData(i)))
        theme.changed.connect(self.restyle)
        self.restyle()

    def set_options(self, options, current=None):
        self.blockSignals(True)
        self.clear()
        for key, label, _ in _options(options):
            self.addItem(label, key)
        if current is not None:
            self.set_current_key(current)
        self.blockSignals(False)

    def current_key(self):
        return self.currentData()

    def set_current_key(self, key):
        i = self.findData(key)
        if i >= 0:
            self.setCurrentIndex(i)

    def restyle(self, *_):
        c = theme.current().colors
        self.setFont(theme.font("body"))
        self.setStyleSheet(f"""
QComboBox {{ background: {c['surface_raised']}; color: {c['text']}; border: 1px solid {c['border_strong']};
    border-radius: {theme.RADIUS['medium']}px; padding: 0 {theme.S}px; min-height: {BUTTON_HEIGHT - 2}px; }}
QComboBox:hover {{ border-color: {c['text_muted']}; }}
QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: none; }}
QComboBox QAbstractItemView {{ background: {c['surface_raised']}; color: {c['text']}; border: 1px solid {c['border']};
    selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; outline: none; padding: 4px; }}
""")

    def paintEvent(self, event):
        super().paintEvent(event)
        p = painter(self)
        x, y = self.width() - 16, self.height() / 2
        p.setPen(QPen(theme.color("text_secondary"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)])
