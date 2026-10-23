"""Feedback: Spinner, ProgressStrip, ActivityCenter, Skeleton, Toast (+ ToastStack), Banner and EmptyState."""
from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPen
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ui import anim, theme
from ui.components.base import KIND_ICONS, KINDS, Label, ThemedWidget, draw_icon, mix, painter, rounded, rounded_path
from ui.components.controls import Button, IconButton
from ui.components.layout import _Chevron

STRIP_HEIGHT = 4            # px: thin, so progress is visible without shouting
INDETERMINATE_MS = 1400     # one sweep of an indeterminate strip
TOAST_MS = 4500             # ms a toast stays: time to read a short sentence plus slack (own judgement)
TOAST_WIDTH = 360           # px
MAX_TOASTS = 4              # older ones are dismissed beyond this
ICON_LEFT = 18              # px: where a toast's kind icon sits
SHAPES = ("text", "block", "circle")


class Spinner(ThemedWidget):
    """A small turning arc that says "working". It turns only while visible; with reduce_motion it
    holds still (pair it with words either way)."""

    def __init__(self, size=20, color="accent", parent=None):
        super().__init__(parent)
        self.color = color
        self.spin_phase = 0.0
        self.setFixedSize(size, size)

    def showEvent(self, event):
        anim.spin(self)

    def hideEvent(self, event):
        anim.stop(self, "spin_phase")

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        w = max(2.0, self.width() / 9)
        rect = QRectF(w, w, self.width() - 2 * w, self.height() - 2 * w)
        p.setPen(QPen(t.q("border"), w))
        p.drawEllipse(rect)
        p.setPen(QPen(t.q(self.color), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, int((90 - self.spin_phase * 360) * 16), -100 * 16)


class ProgressStrip(ThemedWidget):
    """A thin progress bar: determinate (set_progress(done, total)) with a gliding fill, or
    indeterminate (total None or 0) with a sweeping segment."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fraction = None                # None = indeterminate
        self.shown = 0.0
        self.sweep_phase = 0.0
        self.setFixedHeight(STRIP_HEIGHT)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_progress(self, done, total):
        if not total:
            self.set_indeterminate()
            return
        new = min(1.0, max(0.0, float(done) / float(total)))
        was_indeterminate = self.fraction is None
        self.fraction = new
        anim.stop(self, "sweep_phase")
        if was_indeterminate:
            self.shown = new
            self.update()
        else:
            anim.tween(self._show, self.shown, new, anim.MOVE_MS, owner=self, name="fill")

    def set_indeterminate(self):
        if self.fraction is None and anim.running(self, "sweep_phase"):
            return
        self.fraction = None
        if self.isVisible():
            anim.loop(self, "sweep_phase", INDETERMINATE_MS)
        self.update()

    def showEvent(self, event):
        if self.fraction is None:
            anim.loop(self, "sweep_phase", INDETERMINATE_MS)

    def hideEvent(self, event):
        anim.stop(self, "sweep_phase")

    def _show(self, v):
        self.shown = v
        self.update()

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        w, h = self.width(), self.height()
        rounded(p, QRectF(0, 0, w, h), h / 2, fill=t.q("border"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.q("accent"))
        if self.fraction is not None:
            if self.shown > 0:
                p.drawRoundedRect(QRectF(0, 0, max(h, w * self.shown), h), h / 2, h / 2)
            return
        if theme.reduce_motion:             # a still, partial bar: busy, amount unknown
            p.drawRoundedRect(QRectF(0, 0, w * 0.3, h), h / 2, h / 2)
            return
        seg = w * 0.3
        x = -seg + (w + seg) * self.sweep_phase
        p.setClipPath(rounded_path(QRectF(0, 0, w, h), h / 2))
        p.drawRoundedRect(QRectF(x, 0, seg, h), h / 2, h / 2)


class _JobRow(QWidget):
    def __init__(self, job):
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(0, theme.XS, 0, theme.XS)
        box.setSpacing(theme.XS + 1)
        top = QHBoxLayout()
        top.setSpacing(theme.S)
        self.label = Label("", role="body")
        self.percent = Label("", role="numeric", tone="secondary")
        top.addWidget(self.label, 1)
        top.addWidget(self.percent)
        box.addLayout(top)
        self.strip = ProgressStrip()
        box.addWidget(self.strip)
        self.text = Label("", role="caption", tone="muted")
        box.addWidget(self.text)
        self.update_job(job)

    def update_job(self, job):
        self.label.setText(job.get("label") or job.get("kind") or "Working")
        done, total = job.get("done") or 0, job.get("total") or 0
        self.strip.set_progress(done, total)
        self.percent.setText(f"{min(100, round(100 * done / total))}%" if total else "")
        self.text.setText(job.get("text") or "")
        self.text.setVisible(bool(job.get("text")))


class ActivityCenter(ThemedWidget):
    """What's running in the background, from a job list [{"id", "label", "done", "total", "text"}]
    (the shape of JobRunner.jobs()). Expanded it lists each job with a progress strip; collapsed it is
    a spinner and a count. Click the header to switch."""

    def __init__(self, expanded=True, hide_when_idle=False, parent=None):
        super().__init__(parent)
        self.expanded, self.hide_when_idle = expanded, hide_when_idle
        self.rows = {}
        self.setMinimumWidth(220)
        self.setMaximumWidth(480)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        box = QVBoxLayout(self)
        box.setContentsMargins(theme.M, theme.S, theme.M, theme.S)
        box.setSpacing(theme.XS)
        self.header = QWidget()
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        head = QHBoxLayout(self.header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(theme.S)
        self.spinner = Spinner(16)
        self.idle_dot = _IdleDot()
        self.title = Label("", role="label")
        self.chevron = _Chevron()
        head.addWidget(self.spinner)
        head.addWidget(self.idle_dot)
        head.addWidget(self.title, 1)
        head.addWidget(self.chevron)
        box.addWidget(self.header)
        self.list = QWidget()
        self.list_box = QVBoxLayout(self.list)
        self.list_box.setContentsMargins(0, theme.XS, 0, 0)
        self.list_box.setSpacing(theme.XS)
        box.addWidget(self.list)
        self.header.installEventFilter(self)
        self.set_jobs([])

    def eventFilter(self, obj, event):
        if obj is self.header and event.type() == QEvent.Type.MouseButtonRelease:
            self.set_expanded(not self.expanded)
            return True
        return False

    def count(self):
        return len(self.rows)

    def set_expanded(self, expanded):
        self.expanded = expanded
        self._sync()
        if expanded and self.rows:
            anim.slide_down(self.list)

    def set_jobs(self, jobs):
        """Show this job list: rows update in place, new jobs fade in, finished ones leave."""
        seen = set()
        for job in jobs:
            key = job.get("id", job.get("label"))
            seen.add(key)
            row = self.rows.get(key)
            if row is None:
                row = self.rows[key] = _JobRow(job)
                self.list_box.addWidget(row)
                if self.expanded and self.isVisible():
                    anim.fade_in(row)
            else:
                row.update_job(job)
        for key in [k for k in self.rows if k not in seen]:
            row = self.rows.pop(key)
            self.list_box.removeWidget(row)
            row.deleteLater()
        self._sync()

    def _sync(self):
        n = len(self.rows)
        busy = n > 0
        self.spinner.setVisible(busy)
        self.idle_dot.setVisible(not busy)
        if busy:
            self.title.setText(f"{n} task{'s' if n != 1 else ''} running" if self.expanded else f"{n} running")
        else:
            self.title.setText("Nothing running")
        self.title.set_tone("primary" if busy else "muted")
        self.chevron.setVisible(busy)
        self.chevron.set_angle(90 if self.expanded else 0)
        self.list.setVisible(busy and self.expanded)
        self.setToolTip("" if self.expanded or not busy else
                        "\n".join(r.label.text() + (f" · {r.percent.text()}" if r.percent.text() else "")
                                  for r in self.rows.values()))
        if self.hide_when_idle:
            self.setVisible(busy)
        self.updateGeometry()

    def paintEvent(self, event):
        t = theme.current()
        rounded(painter(self), self.rect(), theme.RADIUS["large"], fill=t.q("surface_raised"), border=t.q("border"))


class _IdleDot(ThemedWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(16, 16)

    def paintEvent(self, event):
        p = painter(self)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color("text_muted"))
        p.drawEllipse(QRectF(4, 4, 8, 8))


class Skeleton(ThemedWidget):
    """A shimmering placeholder in the shape of content that's still loading: "text" (`lines` bars),
    "block" (a card-sized rectangle) or "circle"."""

    def __init__(self, shape="text", width=None, height=None, lines=3, parent=None):
        super().__init__(parent)
        if shape not in SHAPES:
            raise ValueError(f"shape must be one of {SHAPES}")
        self.shape, self.lines = shape, lines
        self.shimmer_phase = 0.0
        h = height or {"text": lines * 22 - 8, "block": 96, "circle": 40}[shape]
        self.setFixedHeight(h)
        if width or shape == "circle":
            self.setFixedWidth(width or h)
        else:
            self.setMinimumWidth(80)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Loading")

    def showEvent(self, event):
        anim.shimmer(self)

    def hideEvent(self, event):
        anim.stop(self, "shimmer_phase")

    def _shapes(self):
        w, h = self.width(), self.height()
        if self.shape == "circle":
            return [(QRectF(0, 0, w, h), h / 2)]
        if self.shape == "block":
            return [(QRectF(0, 0, w, h), theme.RADIUS["large"])]
        out = []
        for i in range(self.lines):
            frac = 0.6 if i == self.lines - 1 and self.lines > 1 else 1.0
            out.append((QRectF(0, i * 22, w * frac, 14), 7))
        return out

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        if t.dark:
            base, light = t.q("surface_raised"), t.q("border")
        else:
            base, light = mix(t.q("surface"), t.q("border"), 0.7), t.q("surface_raised")
        for rect, radius in self._shapes():
            rounded(p, rect, radius, fill=base)
        if theme.reduce_motion:
            return
        w = self.width()
        x = -0.4 * w + 1.8 * w * self.shimmer_phase
        grad = QLinearGradient(QPointF(x - 0.25 * w, 0), QPointF(x + 0.25 * w, 0))
        clear = QColor(light)
        clear.setAlpha(0)
        grad.setColorAt(0, clear)
        grad.setColorAt(0.5, light)
        grad.setColorAt(1, clear)
        for rect, radius in self._shapes():
            rounded(p, rect, radius, fill=grad)


class Toast(ThemedWidget):
    """A short message that leaves by itself: success / warning / error / info, an optional action
    button, and a close button. Hovering holds it. Emits closed when gone."""

    closed = pyqtSignal()
    action_clicked = pyqtSignal()

    def __init__(self, text, kind="info", action=None, on_action=None, timeout_ms=TOAST_MS, parent=None):
        super().__init__(parent)
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        self.kind, self.text = kind, text
        self.setFixedWidth(TOAST_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(ICON_LEFT + 18 + theme.M, theme.M, theme.S, theme.M)   # icon painted at left
        row.setSpacing(theme.M)
        self.label = Label(text, role="body", wrap=True)
        row.addWidget(self.label, 1)
        self.action = None
        if action:
            self.action = Button(action, variant="ghost", small=True)
            self.action.clicked.connect(self.action_clicked.emit)
            if on_action:
                self.action.clicked.connect(on_action)
            self.action.clicked.connect(self.dismiss)
            row.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)
        self.close_button = IconButton("ph.x", "Dismiss", size=28)
        self.close_button.clicked.connect(self.dismiss)
        row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        # A fixed text width gives the wrapped label a definite height inside the fixed-width toast.
        side = 28 + theme.M + (self.action.sizeHint().width() + theme.M if self.action else 0)
        self.label.setFixedWidth(TOAST_WIDTH - (ICON_LEFT + 18 + theme.M) - theme.S - side)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        self.timeout_ms = timeout_ms
        self.closing = False
        self.setAccessibleName(f"{kind}: {text}")

    def start(self):
        """Show with a fade and start the dismiss timer (timeout_ms 0 = stays until closed)."""
        anim.fade_in(self)
        if self.timeout_ms:
            self.timer.start(self.timeout_ms)

    def enterEvent(self, event):
        self.timer.stop()

    def leaveEvent(self, event):
        if self.timeout_ms and not self.closing:
            self.timer.start(max(1500, self.timeout_ms // 2))

    def dismiss(self):
        if self.closing:
            return
        self.closing = True
        self.timer.stop()
        anim.fade_out(self, then=self._gone)

    def _gone(self):
        self.closed.emit()
        self.deleteLater()

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        r = QRectF(self.rect())
        rounded(p, r, theme.RADIUS["large"], fill=t.q("surface_raised"), border=t.q("border"))
        draw_icon(p, KIND_ICONS[self.kind], QRectF(ICON_LEFT, (r.height() - 18) / 2, 18, 18), self.kind)


class ToastStack(QWidget):
    """Stacks toasts. As an overlay (`overlay=True`, the default) it floats in its parent window's
    bottom-right corner and follows resizes; otherwise it's an ordinary widget in a layout."""

    def __init__(self, parent=None, overlay=True):
        super().__init__(parent)
        self.overlay = overlay and parent is not None
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(theme.S)
        self.box.addStretch(1)
        self.toasts = []
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        self.setFixedWidth(TOAST_WIDTH)
        if self.overlay:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            parent.installEventFilter(self)
            self._place()

    def show_toast(self, text, kind="info", action=None, on_action=None, timeout_ms=TOAST_MS):
        """Add a toast at the bottom of the stack and return it."""
        toast = Toast(text, kind, action, on_action, timeout_ms)
        toast.closed.connect(lambda: self._closed(toast))
        self.toasts.append(toast)
        self.box.addWidget(toast)
        while len(self.toasts) > MAX_TOASTS:
            self.toasts.pop(0).dismiss()
        self.show()
        self.raise_()
        toast.start()
        self._place()
        return toast

    def _closed(self, toast):
        if toast in self.toasts:
            self.toasts.remove(toast)
        self._place()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            self._place()
        return False

    def _place(self):
        self.adjustSize()
        if not self.overlay:
            return
        parent = self.parentWidget()
        h = self.sizeHint().height()
        self.setGeometry(parent.width() - TOAST_WIDTH - theme.XL, parent.height() - h - theme.XL, TOAST_WIDTH, h)
        self.raise_()

    def sizeHint(self):
        return QSize(TOAST_WIDTH, self.box.sizeHint().height())


class Banner(ThemedWidget):
    """One persistent issue line with an action ("Too noisy to judge brightness · Run mic check")."""

    action_clicked = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self, text, kind="warning", action=None, on_action=None, dismissible=False, parent=None):
        super().__init__(parent)
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        self.kind = kind
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.M + 30, theme.S, theme.S, theme.S)
        row.setSpacing(theme.M)
        self.label = Label(text, role="body", wrap=True)
        row.addWidget(self.label, 1)
        self.action = None
        if action:
            self.action = Button(action, variant="secondary", small=True)
            self.action.clicked.connect(self.action_clicked.emit)
            if on_action:
                self.action.clicked.connect(on_action)
            row.addWidget(self.action)
        if dismissible:
            close = IconButton("ph.x", "Dismiss", size=28)
            close.clicked.connect(self._dismiss)
            row.addWidget(close)
        self.setAccessibleName(f"{kind}: {text}")
        self.setMinimumHeight(44)

    def set_text(self, text):
        self.label.setText(text)

    def _dismiss(self):
        anim.collapse(self, then=self.dismissed.emit)

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        rounded(p, self.rect(), theme.RADIUS["medium"], fill=t.q(self.kind, 30 if t.dark else 26),
                border=t.q(self.kind, 90))
        draw_icon(p, KIND_ICONS[self.kind], QRectF(theme.M, (self.height() - 20) / 2, 20, 20), self.kind)


class EmptyState(ThemedWidget):
    """What a screen says when there's nothing to show yet: an icon, one kind sentence, an optional
    detail line and an action."""

    action_clicked = pyqtSignal()

    def __init__(self, icon, text, action=None, on_action=None, detail=None, parent=None):
        super().__init__(parent)
        self.icon_name = icon
        box = QVBoxLayout(self)
        box.setContentsMargins(theme.XL, theme.XL, theme.XL, theme.XL)
        box.setSpacing(theme.S)
        box.addSpacing(48 + theme.S)        # the icon is painted here
        self.label = Label(text, role="heading", wrap=True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(self.label)
        self.detail = Label(detail or "", role="body", tone="secondary", wrap=True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.detail.setVisible(bool(detail))
        box.addWidget(self.detail)
        self.action = None
        if action:
            box.addSpacing(theme.S)
            self.action = Button(action, variant="primary")
            self.action.clicked.connect(self.action_clicked.emit)
            if on_action:
                self.action.clicked.connect(on_action)
            box.addWidget(self.action, 0, Qt.AlignmentFlag.AlignHCenter)
        self.setMinimumWidth(260)

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        c = QPointF(self.width() / 2, theme.XL + 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.q("surface_raised"))
        p.drawEllipse(c, 30, 30)
        draw_icon(p, self.icon_name, QRectF(c.x() - 15, c.y() - 15, 30, 30), "text_muted")
