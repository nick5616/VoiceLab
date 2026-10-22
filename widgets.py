"""Layout helpers shared by the window, take cards and Progress.

Charts don't need wheel handling: with zoom and pan off, pyqtgraph passes the
wheel on to the scrolling page."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QSlider, QWidget


class ElidedLabel(QLabel):
    """A one-line label that shows "..." where its text doesn't fit, instead of cutting it off or
    widening its parent. Keep the full text in the tooltip."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def paintEvent(self, event):
        painter = QPainter(self)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.setPen(self.palette().windowText().color())
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)


class PageSlider(QSlider):
    """A slider that only takes the wheel once clicked, so scrolling past it
    moves the page instead of the slider."""

    def __init__(self, orientation):
        super().__init__(orientation)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ResponsiveGrid(QWidget):
    """Lays its widgets out in as many columns as fit, each at least
    `min_column_width` wide (up to `max_columns`). Hidden widgets leave no gap;
    call relayout() after showing or hiding some."""

    def __init__(self, min_column_width, max_columns=2, spacing=16):
        super().__init__()
        self.min_column_width, self.max_columns = min_column_width, max_columns
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(spacing)
        self.grid.setVerticalSpacing(0)
        self.items = []
        self.columns = 0

    def addWidget(self, widget):
        self.items.append(widget)
        self.relayout()

    def columns_for(self, width):
        return max(1, min(self.max_columns, width // self.min_column_width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.columns_for(self.width()) != self.columns:
            self.relayout()

    def relayout(self):
        self.columns = self.columns_for(self.width())
        for widget in self.items:
            self.grid.removeWidget(widget)
        # New widgets count as hidden until first shown; skip only ones hidden on purpose.
        shown = [w for w in self.items if not (w.isHidden() and
                 w.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide))]
        for i, widget in enumerate(shown):
            self.grid.addWidget(widget, i // self.columns, i % self.columns)
        for column in range(self.max_columns):
            self.grid.setColumnStretch(column, 1 if column < self.columns else 0)
