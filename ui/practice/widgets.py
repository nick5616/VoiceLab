"""Small widgets of the practice pages: the state strip, the "right now" readout, the reading text with its
"you are here" highlight, and the row of phrase dots."""
import html

import pyqtgraph as pg
from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ui import theme
from ui.components import Caption, Chip, Label, ProgressStrip, ThemedWidget
from ui.components.base import mix, painter

READING_FONT_PX = 22            # px: the passage, big enough to read from a step back
STRIP_PROGRESS_WIDTH = 180      # px: the progress bar in the state strip
PLOT_HINT_PX = 220              # px: the height a practice plot asks for
DOT_PX = 12                     # px: one phrase dot
DOT_GAP = 6                     # px: between phrase dots
SHADE_MIX = 0.3                # how much accent colour tints the sentence you should be on
READOUT_WIDTH = 250             # px: the "right now" readout beside the passage


class PracticeStatus(QWidget):
    """One line saying what the practice is doing: a state chip, a sentence, a timer or progress bar, and why
    Start is unavailable when it is."""

    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.M)
        self.chip = Chip("Ready", kind="neutral", dot=True)
        self.detail = Label("", role="body", tone="secondary")
        self.detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.elapsed = Label("", role="numeric", tone="primary")
        self.progress = ProgressStrip()
        self.progress.setFixedWidth(STRIP_PROGRESS_WIDTH)
        self.reason = Label("", role="caption", tone="secondary")
        row.addWidget(self.chip)
        row.addWidget(self.detail, 1)
        row.addWidget(self.elapsed)
        row.addWidget(self.progress)
        row.addWidget(self.reason)
        self.view = None

    def show_view(self, view, reason=None):
        """Draw a practice_state.state_view; `reason` says why Start is disabled (or None)."""
        self.view = view
        self.chip.set_text(view["label"], view["kind"])
        self.detail.setText(view["detail"])
        self.detail.setToolTip(view["detail"])
        self.elapsed.setText(view["elapsed"])
        self.elapsed.setVisible(bool(view["elapsed"]))
        if view["progress"]:
            self.progress.set_progress(*view["progress"])
        elif view["indeterminate"]:
            self.progress.set_indeterminate()
        self.progress.setVisible(bool(view["progress"]) or view["indeterminate"])
        self.reason.setText(reason or "")
        self.reason.setVisible(bool(reason))
        self.setAccessibleName(f"{view['label']}. {view['detail']}")


class Readout(QWidget):
    """"Right now": the pitch you are making, whether you are heard and whether it is in your range."""

    def __init__(self):
        super().__init__()
        self.setFixedWidth(READOUT_WIDTH)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.XS)
        self.title = Caption("Right now")
        self.pitch = Label("—", role="figure")
        self.state = Label("", role="body", tone="secondary")
        self.range = Label("", role="body", tone="secondary", wrap=True)
        self.resonance = Caption("")
        for w in (self.title, self.pitch, self.state, self.range, self.resonance):
            box.addWidget(w)
        box.addStretch(1)

    def show_now(self, hz, state_text, range_text="", range_ok=False, resonance=""):
        """hz: the pitch now (None when silent)."""
        self.pitch.setText("—" if hz is None else f"{hz:.0f} Hz")
        self.pitch.set_tone("primary" if hz is not None else "muted")
        self.state.setText(state_text)
        self.range.setText(range_text)
        self.range.set_tone("on_target" if range_ok else "secondary")
        self.resonance.setText(resonance)

    def reset(self):
        self.show_now(None, "")


class HighlightText(Label):
    """A passage in a book face, big enough to read from a step back, with the sentence you should be on shaded."""

    def __init__(self):
        super().__init__("", role="body", wrap=True)
        self.passage = ""
        self.span = None
        self.setTextFormat(Qt.TextFormat.RichText)

    def restyle(self, *_):
        super().restyle()
        font = QFont()
        font.setFamilies(["Georgia", "Cambria", "Segoe UI"])
        font.setPixelSize(READING_FONT_PX)
        self.setFont(font)
        if hasattr(self, "passage"):
            self._render()

    def set_passage(self, text):
        self.passage = text
        self.span = None
        self._render()

    def set_span(self, span):
        """Shade the characters (start, end) of the passage; None shades nothing."""
        if span != self.span:
            self.span = span
            self._render()

    def _render(self):
        text, span = self.passage, self.span

        def esc(part):
            return html.escape(part).replace("\n", "<br>")

        if not span:
            self.setText(esc(text))
            return
        t = theme.current()
        shade = mix(t.q("bg"), t.q("accent"), SHADE_MIX).name()
        start, end = span
        spoken = text[start:end].rstrip()
        self.setText(esc(text[:start]) + f'<span style="background-color:{shade}">{esc(spoken)}</span>'
                     + esc(text[start + len(spoken):]))


class PhraseDots(ThemedWidget):
    """One dot per phrase: filled for those recorded today, a ring for the one on screen."""

    def __init__(self, count):
        super().__init__()
        self.count = count
        self.done = set()
        self.current = 0
        self.extra = 0                  # takes today that are not tied to a phrase number
        self.setFixedHeight(DOT_PX + 4)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(self.count * (DOT_PX + DOT_GAP))

    def sizeHint(self):
        return QSize(self.count * (DOT_PX + DOT_GAP), DOT_PX + 4)

    def set_state(self, done, current):
        self.done, self.current = set(done), current
        self.update()

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        p.setRenderHint(p.RenderHint.Antialiasing)
        for i in range(self.count):
            x = i * (DOT_PX + DOT_GAP) + 1
            rect = QRectF(x, 2, DOT_PX, DOT_PX)
            if i in self.done:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(t.q("success"))
            else:
                p.setPen(t.q("text_muted"))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(rect)
            if i == self.current:
                p.setPen(t.q("accent"))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(rect.adjusted(-2, -2, 2, 2))


class SizedPlot(pg.PlotWidget):
    """A plot that asks for a modest size (a pyqtgraph plot hints 480 px tall), so a page of word-wrapped text
    plus a plot fits the window instead of scrolling. It still grows into any room there is."""
    hint_size = QSize(480, PLOT_HINT_PX)

    def sizeHint(self):
        return self.hint_size
