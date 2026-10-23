"""Layout components: Card, Section, Collapsible, FlowGrid, WrapRow and Divider."""
from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPainterPath, QPen
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLayout, QSizePolicy, QVBoxLayout, QWidget

from ui import anim, theme
from ui.components.base import Label, ThemedWidget, painter, rounded
from ui.components.text import HelpTip

CARD_MIN_WIDTH = 240        # px: narrower than this a card's headline wraps every word
CARD_PADDING = theme.L      # px inside a card
CHEVRON = 10                # px: the Collapsible's arrow


class Card(ThemedWidget):
    """A surface with an optional title, subtitle and action buttons, and a body below.
    Add content with add(widget) or card.body (a QVBoxLayout)."""

    def __init__(self, title=None, subtitle=None, raised=False, padding=CARD_PADDING, parent=None):
        super().__init__(parent)
        self.raised = raised
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(padding, padding, padding, padding)
        outer.setSpacing(theme.M)
        self.header = QWidget()
        head = QHBoxLayout(self.header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(theme.S)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = Label(title or "", role="heading", wrap=True)
        self.subtitle_label = Label(subtitle or "", role="caption", tone="muted", wrap=True)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        head.addLayout(titles, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(theme.XS)
        head.addLayout(self.actions)
        head.setAlignment(self.actions, Qt.AlignmentFlag.AlignTop)
        outer.addWidget(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(theme.M)
        outer.addLayout(self.body)
        self._sync_header()

    def _sync_header(self):
        self.title_label.setVisible(bool(self.title_label.text()))
        self.subtitle_label.setVisible(bool(self.subtitle_label.text()))
        self.header.setVisible(bool(self.title_label.text() or self.subtitle_label.text() or self.actions.count()))

    def set_title(self, text):
        self.title_label.setText(text or "")
        self._sync_header()

    def set_subtitle(self, text):
        self.subtitle_label.setText(text or "")
        self._sync_header()

    def add_action(self, widget):
        """A button at the right of the title row."""
        self.actions.addWidget(widget)
        self._sync_header()
        return widget

    def add(self, widget, stretch=0):
        self.body.addWidget(widget, stretch)
        return widget

    def paintEvent(self, event):
        t = theme.current()
        p = painter(self)
        rounded(p, self.rect(), theme.RADIUS["large"], fill=t.q("surface_raised" if self.raised else "surface"),
                border=t.q("border"))


class Section(QWidget):
    """A heading (with an optional "?" help) over a block of content, without a surface of its own."""

    def __init__(self, heading, help=None, level="heading", parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.S)
        row = QHBoxLayout()
        row.setSpacing(theme.XS)
        self.heading = Label(heading, role=level)
        row.addWidget(self.heading)
        self.help = HelpTip(help) if help else None
        if self.help:
            row.addWidget(self.help)
        row.addStretch(1)
        self.trailing = row                 # add widgets at the right of the heading row
        outer.addLayout(row)
        self.body = QVBoxLayout()
        self.body.setSpacing(theme.M)
        outer.addLayout(self.body)

    def add(self, widget, stretch=0):
        self.body.addWidget(widget, stretch)
        return widget


class _Chevron(ThemedWidget):
    def __init__(self):
        super().__init__()
        self.angle = 0.0                    # 0 = pointing right (collapsed), 90 = down
        self.setFixedSize(16, 16)

    def set_angle(self, angle):
        self.angle = angle
        self.update()

    def paintEvent(self, event):
        p = painter(self)
        p.translate(8, 8)
        p.rotate(self.angle)
        p.setPen(QPen(theme.color("text_secondary"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath()
        path.moveTo(-2, -4)
        path.lineTo(2.5, 0)
        path.lineTo(-2, 4)
        p.drawPath(path)


class _Header(ThemedWidget):
    clicked = pyqtSignal()

    def __init__(self, title, role):
        super().__init__()
        self.hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.XS, theme.XS, theme.S, theme.XS)
        row.setSpacing(theme.XS)
        self.chevron = _Chevron()
        self.label = Label(title, role=role, tone="secondary")
        row.addWidget(self.chevron)
        row.addWidget(self.label)
        row.addStretch(1)
        self.trailing = row

    def mouseReleaseEvent(self, event):
        if self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
        else:
            super().keyPressEvent(event)

    def enterEvent(self, event):
        self.hover = True
        self.label.set_tone("primary")
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.label.set_tone("secondary")
        self.update()

    def paintEvent(self, event):
        if self.hover or self.hasFocus():
            p = painter(self)
            t = theme.current()
            rounded(p, self.rect(), theme.RADIUS["medium"], fill=t.q("surface_raised") if self.hover else None,
                    border=t.q("accent") if self.hasFocus() else None)


class Collapsible(QWidget):
    """A header that opens and closes the content below it, with a short slide. With a `key` it
    remembers whether it was open in Collapsible.memory (a dict; the app may swap in a persistent one)."""

    toggled = pyqtSignal(bool)
    memory = {}

    def __init__(self, title, key=None, expanded=False, role="label", parent=None):
        super().__init__(parent)
        self.key = key
        if key is not None and key in Collapsible.memory:
            expanded = bool(Collapsible.memory[key])
        self.expanded = expanded
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.XS)
        self.header = _Header(title, role)
        self.header.clicked.connect(self.toggle)
        outer.addWidget(self.header)
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(theme.L + theme.XS, 0, 0, theme.XS)
        self.body.setSpacing(theme.S)
        outer.addWidget(self.content)
        self.content.setVisible(expanded)
        self.header.chevron.set_angle(90 if expanded else 0)
        self.header.setAccessibleName(title)

    def add(self, widget, stretch=0):
        self.body.addWidget(widget, stretch)
        return widget

    def toggle(self):
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded, animate=True):
        if expanded == self.expanded:
            return
        self.expanded = expanded
        if self.key is not None:
            Collapsible.memory[self.key] = expanded
        chevron = self.header.chevron
        anim.tween(chevron.set_angle, chevron.angle, 90 if expanded else 0, anim.ENTER_MS,
                   owner=chevron, name="turn") if animate else chevron.set_angle(90 if expanded else 0)
        if expanded:
            anim.slide_down(self.content) if animate else self.content.show()
        else:
            anim.collapse(self.content) if animate else self.content.hide()
        self.toggled.emit(expanded)


class FlowGrid(QWidget):
    """Lays widgets out in as many columns as fit (each at least `min_column_width`), so a full-
    screen window gets more columns rather than stretched cards. add(widget, span) takes `span`
    columns (-1: the whole row). Hidden widgets leave no gap; call relayout() after hiding some."""

    def __init__(self, min_column_width=320, max_columns=None, spacing=theme.L, max_column_width=640, parent=None):
        super().__init__(parent)
        self.min_column_width, self.max_columns, self.spacing = min_column_width, max_columns, spacing
        self.max_column_width = max_column_width
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(spacing)
        self.grid.setVerticalSpacing(spacing)
        self.items = []                     # (widget, span)
        self.columns = 0
        self._stretched = 0

    def add(self, widget, span=1):
        self.items.append((widget, span))
        self.relayout()
        return widget

    addWidget = add

    def columns_for(self, width):
        """As many columns as fit at min_column_width. With fewer items than that, only as many as
        the items need (so a few cards widen instead of leaving a gap), but never wider than
        max_column_width."""
        n = max(1, (width + self.spacing) // (self.min_column_width + self.spacing))
        if self.max_columns:
            n = min(n, self.max_columns)
        used = sum(1 if span == -1 else span for w, span in self.items
                   if not (w.isHidden() and w.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide)))
        if used and used < n:
            narrowest = -(-(width + self.spacing) // (self.max_column_width + self.spacing))
            n = min(n, max(used, narrowest))
        return n

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.columns_for(self.width()) != self.columns:
            self.relayout()

    def placements(self, columns=None):
        """[(widget, row, column, span)] for a column count (the current one by default)."""
        columns = columns or self.columns or 1
        out, row, col = [], 0, 0
        for widget, span in self.items:
            if widget.isHidden() and widget.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide):
                continue
            span = columns if span == -1 else max(1, min(span, columns))
            if col + span > columns:
                row, col = row + 1, 0
            out.append((widget, row, col, span))
            col += span
            if col >= columns:
                row, col = row + 1, 0
        return out

    def relayout(self):
        self.columns = self.columns_for(self.width() or self.min_column_width)
        for widget, _ in self.items:
            self.grid.removeWidget(widget)
        for widget, row, col, span in self.placements():
            self.grid.addWidget(widget, row, col, 1, span, Qt.AlignmentFlag.AlignTop)
        for column in range(max(self._stretched, self.columns)):
            self.grid.setColumnStretch(column, 1 if column < self.columns else 0)
            self.grid.setColumnMinimumWidth(column, 0)
        self._stretched = self.columns

    def sizeHint(self):
        return QSize(self.min_column_width, super().sizeHint().height())


class _WrapLayout(QLayout):
    """Lays items left to right and wraps to a new line when the width runs out (like CSS flex-wrap)."""

    def __init__(self, spacing):
        super().__init__()
        self.items = []
        self.gap = spacing

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _place(self, rect, apply):
        """Lay the items out (when apply) and return the height used; items centre on their line."""
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        lines, line, x = [], [], r.x()
        for item in self.items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            if line and x + hint.width() > r.right() + 1:
                lines.append(line)
                line, x = [], r.x()
            line.append((item, x, hint))
            x += hint.width() + self.gap
        if line:
            lines.append(line)
        y = r.y()
        for line in lines:
            height = max(h.height() for _, _, h in line)
            if apply:
                for item, x, hint in line:
                    item.setGeometry(QRect(QPoint(x, y + (height - hint.height()) // 2), hint))
            y += height + self.gap
        return max(0, y - self.gap - rect.y()) + m.bottom() if lines else m.top() + m.bottom()


class WrapRow(QWidget):
    """Small widgets (chips, moments, buttons) in a row that wraps onto more lines when narrow."""

    def __init__(self, widgets=(), spacing=theme.S, parent=None):
        super().__init__(parent)
        self.flow = _WrapLayout(spacing)
        self.flow.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self.flow)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        for w in widgets:
            self.add(w)

    def add(self, widget):
        self.flow.addWidget(widget)
        return widget

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.flow.heightForWidth(width)


class Divider(ThemedWidget):
    """A hairline between groups."""

    def __init__(self, vertical=False, parent=None):
        super().__init__(parent)
        self.vertical = vertical
        if vertical:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        p = painter(self)
        p.fillRect(QRectF(self.rect()), theme.color("border"))
