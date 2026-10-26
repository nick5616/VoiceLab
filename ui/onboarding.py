"""The first-run welcome: a kind word, the voice goal, the takes folder and the mic check. Every step can
be skipped; Settings can open it again. The shell passes in what the steps do (choose a folder, run the
mic check); the choices go to the core."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from app_core import VOICE_GOAL_CHOICES, VOICE_GOAL_TEXT
from ui import theme
from ui.components import Button, Caption, Headline, Label, SegmentedControl

STEPS = ("welcome", "goal", "folder", "mic")
WELCOME_TITLE = "Welcome to VoiceLab"
WELCOME_POINTS = [
    ("A tool, not a judge",
     "VoiceLab listens and describes how your voice sounds today, then suggests one thing to try next. "
     "It never decides who you are, and no number here is a grade."),
    ("Every voice is different",
     "The coloured zones show tendencies across many speakers, and they overlap a lot. They are a map "
     "for practice, not rules your voice has to follow."),
    ("Go gently",
     "Short, regular sessions work better than long ones. If your voice feels tired, tight or scratchy, "
     "stop, rest and have some water. VoiceLab will nudge you now and then."),
]
GOAL_TITLE = "What would you like to work toward?"
FOLDER_TITLE = "Where should your takes live?"
FOLDER_TEXT = ("Pick a folder and every take is saved there as a WAV file with its analysis beside it, "
               "so your progress builds up across sessions. You can change it any time in Settings.")
MIC_TITLE = "Check your mic"
MIC_TEXT = ("Eight seconds: stay quiet for a moment, then say a steady “ah”. VoiceLab tells you if the room "
            "is noisy, the mic too far, or the desk rumbling, so the readings you get can be trusted.")


class Onboarding(QDialog):
    """Four short steps. `choose_folder()` and `run_mic_check()` are the shell's; completed() when done
    or skipped (the core remembers it)."""

    completed = pyqtSignal()

    def __init__(self, core, choose_folder, run_mic_check, parent=None):
        super().__init__(parent)
        self.core = core
        self.choose_folder, self.run_mic_check = choose_folder, run_mic_check
        self.setWindowTitle("Welcome")
        self.setModal(True)
        self.setMinimumSize(640, 520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.XXL, theme.XXL, theme.XXL, theme.L)
        outer.setSpacing(theme.L)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.pages = {}
        for key in STEPS:
            page = getattr(self, f"_{key}_page")()
            self.pages[key] = page
            self.stack.addWidget(page)

        nav = QHBoxLayout()
        self.dots = Caption("")
        self.skip_all = Button("Skip the welcome", variant="ghost")
        self.skip_all.clicked.connect(self.finish)
        self.back_btn = Button("Back", variant="ghost", icon="ph.arrow-left")
        self.back_btn.clicked.connect(lambda: self.go(self.index() - 1))
        self.next_btn = Button("Next", variant="primary", icon="ph.arrow-right")
        self.next_btn.clicked.connect(self.next)
        nav.addWidget(self.dots)
        nav.addStretch(1)
        nav.addWidget(self.skip_all)
        nav.addWidget(self.back_btn)
        nav.addWidget(self.next_btn)
        outer.addLayout(nav)
        self.go(0)

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), theme.color("surface"))

    # --- pages ---

    def _page(self, title):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.L)
        box.addWidget(Headline(title, level="display"))
        return page, box

    def _welcome_page(self):
        page, box = self._page(WELCOME_TITLE)
        for title, text in WELCOME_POINTS:
            point = QVBoxLayout()
            point.setSpacing(theme.XS)
            point.addWidget(Label(title, role="heading"))
            point.addWidget(Label(text, role="body", tone="secondary", wrap=True))
            box.addLayout(point)
        box.addStretch(1)
        return page

    def _goal_page(self):
        page, box = self._page(GOAL_TITLE)
        self.goal = SegmentedControl([(g or "exploring", label) for g, label in VOICE_GOAL_CHOICES],
                                     current=self.core.profile.voice_goal or "exploring")
        self.goal.changed.connect(lambda key: self.core.set_voice_goal(None if key == "exploring" else key))
        box.addWidget(self.goal)
        box.addWidget(Label(VOICE_GOAL_TEXT, role="body", tone="secondary", wrap=True))
        box.addWidget(Caption("Not sure? “Just exploring” is a fine place to start, and you can change it in "
                              "Settings whenever you like."))
        box.addStretch(1)
        return page

    def _folder_page(self):
        page, box = self._page(FOLDER_TITLE)
        box.addWidget(Label(FOLDER_TEXT, role="body", tone="secondary", wrap=True))
        self.folder_label = Label("", role="label")
        box.addWidget(self.folder_label)
        self.folder_btn = Button("Choose a folder…", variant="secondary", icon="ph.folder-open")
        self.folder_btn.clicked.connect(self._choose_folder)
        box.addWidget(self.folder_btn, 0, Qt.AlignmentFlag.AlignLeft)
        box.addStretch(1)
        return page

    def _mic_page(self):
        page, box = self._page(MIC_TITLE)
        box.addWidget(Label(MIC_TEXT, role="body", tone="secondary", wrap=True))
        self.mic_btn = Button("Run the mic check", variant="secondary", icon="ph.microphone")
        self.mic_btn.clicked.connect(self.run_mic_check)
        box.addWidget(self.mic_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.mic_result = Label("", role="body", wrap=True)
        box.addWidget(self.mic_result)
        box.addStretch(1)
        return page

    # --- steps ---

    def index(self):
        return self.stack.currentIndex()

    def step(self):
        return STEPS[self.index()]

    def go(self, index):
        index = max(0, min(len(STEPS) - 1, index))
        self.stack.setCurrentIndex(index)
        last = index == len(STEPS) - 1
        self.back_btn.setVisible(index > 0)
        self.next_btn.setText("Start using VoiceLab" if last else "Next")
        self.dots.setText(f"Step {index + 1} of {len(STEPS)}")
        self._refresh()

    def next(self):
        if self.index() == len(STEPS) - 1:
            self.finish()
        else:
            self.go(self.index() + 1)

    def _choose_folder(self):
        self.choose_folder()
        self._refresh()

    def _refresh(self):
        store = self.core.store
        self.folder_label.setText(f"Saving to {store.folder}" if store else "")
        self.folder_label.setVisible(store is not None)
        self.folder_btn.setText("Choose another folder…" if store else "Choose a folder…")
        lines = self.core.mic_check_text()
        self.mic_result.setText(lines[0] if lines else "")

    def mic_checked(self):
        """The shell's mic check finished: show what it found."""
        self._refresh()

    def finish(self):
        """Done or skipped: remembered, so it doesn't come back (Settings can reopen it)."""
        if self.core.should_ask_takes_folder():
            self.core.decline_takes_folder()
        self.core.finish_onboarding()
        self.completed.emit()
        self.accept()

    def reject(self):
        """Closing the window skips the rest."""
        self.core.finish_onboarding()
        self.completed.emit()
        super().reject()
