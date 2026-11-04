"""Today's practice: a checklist card (warm-up, phrases, the passage, the result) with the step you are on, a Skip
for each step and a summary at the end. SessionCard(core) is also used by the dashboard (spec 03)."""
from PyQt6.QtCore import QRectF, QSize, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from session_plan import SESSION_MINUTES
from ui import theme
from ui.components import Button, Caption, Card, Label, ThemedWidget
from ui.components.base import draw_icon
from ui.takes import listen

MARK_PX = 22                    # px: the tick / circle at the start of a step
MARKS = {"done": ("ph.check-circle-fill", "success"), "skipped": ("ph.minus-circle", "text_muted"),
         "current": ("ph.arrow-circle-right-fill", "accent"), "todo": ("ph.circle", "text_muted")}
STATE_WORDS = {"done": "done", "skipped": "skipped", "current": "up next", "todo": "to do"}


class StepMark(ThemedWidget):
    """The round mark of a step: ticked, skipped, up next or still to do."""

    def __init__(self):
        super().__init__()
        self.state = "todo"
        self.setFixedSize(MARK_PX, MARK_PX)

    def set_state(self, state):
        self.state = state
        self.update()

    def sizeHint(self):
        return QSize(MARK_PX, MARK_PX)

    def paintEvent(self, event):
        from ui.components.base import painter
        p = painter(self)
        name, color = MARKS[self.state]
        draw_icon(p, name, QRectF(self.rect()), color)


class StepRow(QWidget):
    """One step: mark, title, progress ('2 of 3'), and for the step you are on, what to do with Start / Skip."""
    clicked = pyqtSignal(str)
    skipped = pyqtSignal(str)

    def __init__(self, step):
        super().__init__()
        self.key = step["key"]
        self.practice = step["practice"]
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(theme.S)
        self.mark = StepMark()
        self.title = Label("", role="body", wrap=False)
        self.progress = Caption("", wrap=False)
        self.state_word = Caption("", wrap=False)
        self.start_btn = Button("Start", variant="primary", small=True)
        self.skip_btn = Button("Skip", variant="ghost", small=True)
        self.start_btn.clicked.connect(lambda: self.clicked.emit(self.practice))
        self.skip_btn.clicked.connect(lambda: self.skipped.emit(self.key))
        top.addWidget(self.mark)
        top.addWidget(self.title)
        top.addWidget(self.progress)
        top.addStretch(1)
        top.addWidget(self.state_word)
        top.addWidget(self.skip_btn)
        top.addWidget(self.start_btn)
        outer.addLayout(top)
        self.detail = Caption("", tone="secondary")
        detail_row = QHBoxLayout()
        detail_row.setContentsMargins(MARK_PX + theme.S, 0, 0, 0)
        detail_row.addWidget(self.detail)
        outer.addLayout(detail_row)
        self.show_step(step)

    def show_step(self, step):
        self.practice = step["practice"]
        state = step["state"]
        self.mark.set_state(state)
        self.title.setText(step["title"])
        self.title.set_tone("primary" if state in ("current", "done") else "secondary")
        self.progress.setText(step["progress"])
        self.state_word.setText("" if state == "current" else STATE_WORDS[state])
        current = state == "current" and step["practice"] is not None
        self.start_btn.setVisible(current)
        self.skip_btn.setVisible(current)
        self.start_btn.setText("Start" if not step["done"] else "Continue")
        self.detail.setVisible(state == "current")
        self.detail.setText(step["detail"])
        self.setAccessibleName(f"{step['title']}, {STATE_WORDS[state]}")


class SessionCard(Card):
    """Today's practice. `open_step(key)` is emitted with a practice key ('vowels', 'phrases', 'read',
    'exercise') when a step is started."""
    open_step = pyqtSignal(str)
    open_report = pyqtSignal()          # the finished day's full report (History)

    def __init__(self, core, parent=None):
        super().__init__("Today's practice", parent=parent)
        self.core = core
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.where = Caption("", tone="secondary", wrap=False)
        self.add_action(self.where)
        self.rows = {}
        self.list = QVBoxLayout()
        self.list.setSpacing(theme.S)
        holder = QWidget()
        holder.setLayout(self.list)
        self.add(holder)
        self.summary_head = Label("", role="heading", wrap=True)
        self.summary_lines = Label("", role="body", tone="secondary", wrap=True)
        self.rest_line = Label("", role="caption", tone="muted", wrap=True)
        self.report_btn = Button("Open full report", variant="secondary", icon="ph.arrow-right", small=True)
        self.report_btn.clicked.connect(self.open_report.emit)
        self.report_btn.hide()
        self.add_action(self.report_btn)            # in the title row: the finished card grows no taller
        for w in (self.summary_head, self.summary_lines, self.rest_line):
            self.add(w)
            w.hide()
        self.rest_text = None
        listen(self, core, {"practice_plan_changed": self.refresh, "mode_changed": lambda *_: self.refresh(),
                            "profile_changed": self.refresh, "sessions_changed": self.refresh})
        self.refresh()

    def refresh(self, *_):
        plan = self.core.practice_plan()
        steps = plan.steps()
        for step in steps:
            row = self.rows.get(step["key"])
            if row is None:
                row = StepRow(step)
                row.clicked.connect(self.open_step)
                row.skipped.connect(self.core.skip_practice_step)
                self.rows[step["key"]] = row
                self.list.addWidget(row)
            row.show_step(step)
        for key, row in self.rows.items():             # a mode switch changes the steps
            row.setVisible(key in {s["key"] for s in steps})
        finished = plan.finished
        self.where.setText(f"{plan.title()} · about {SESSION_MINUTES} min" if not finished else plan.title())
        self.rows["result"].practice = None
        nudge = self.core.rest_text()                   # a long session earns a kind word, finished or not
        if nudge:
            self.rest_text = nudge
        if finished and any(s["state"] == "done" for s in steps[:-1]):
            summary = plan.summary()
            self.summary_head.setText(summary["headline"])
            self.summary_lines.setText("\n".join(summary["lines"]))
            self.summary_head.show()
            self.summary_lines.setVisible(bool(summary["lines"]))
            self.report_btn.setVisible(bool(self.core.practice_sessions()))
            self.rows["result"].hide()
        else:
            self.summary_head.hide()
            self.summary_lines.hide()
            self.report_btn.hide()
        self.rest_line.setText(self.rest_text or "")
        self.rest_line.setVisible(bool(self.rest_text))

    def current_key(self):
        """The practice the current step opens, or None."""
        return self.core.practice_plan().current()["practice"]
