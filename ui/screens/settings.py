"""Settings: microphone (and EGG) with the mic check, the takes folder, the voice goal, targets and
baseline, appearance, and the kindness note."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

import shell_state
from app_core import VOICE_GOAL_CHOICES, VOICE_GOAL_TEXT
from ui import theme
from ui.components import Button, Caption, Card, Headline, Label, Section, SegmentedControl, Select, Toggle
from ui.screens import TargetsEditor
from ui.takes import listen

EGG_CHOICES = [("none", "None"), ("0", "Channel 1"), ("1", "Channel 2")]     # the EGG input channel
THEME_CHOICES = [("dark", "Dark"), ("light", "Light"), ("system", "Same as Windows")]
COLUMN_WIDTH = 880              # px: settings read best as one calm column
AUTOSAVE_MS = 700               # ms after the last edit of a target before it is saved
ABOUT_TEXT = ("VoiceLab is a practice tool, not a judge. It describes how your voice sounds today and "
              "suggests what to try next; it never decides who you are. Voices vary a lot, and the "
              "coloured zones are tendencies across many speakers, not rules. Go at your own pace, and "
              "rest whenever your voice feels tired.")


def goal_key(goal):
    return goal or "exploring"


class SettingsTargets(TargetsEditor):
    """The targets in Settings: speaking hides the singing-only rows (Ring, Register), every edit is saved by
    itself a moment later ("Saved" says so; no Save and Revert), and the baseline line has no technical names."""

    def __init__(self, core):
        super().__init__(core, buttons=False)
        self.saved_note = Caption("", tone="secondary")
        self.layout().insertWidget(1, self.saved_note)
        listen(self, core, {"mode_changed": lambda *_: self.reload()})
        self._filter()

    def _timer(self):
        timer = self.__dict__.get("_autosave")
        if timer is None:
            timer = self._autosave = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(AUTOSAVE_MS)
            timer.timeout.connect(self.apply)
        return timer

    def reload(self, *_):
        super().reload()
        if "saved_note" in self.__dict__:
            self.saved_note.setText("")
        self._filter()

    def _filter(self):
        keep = shell_state.target_keys_for_mode(list(self.rows), self.core.mode)
        for key, row in self.rows.items():
            row.setVisible(key in keep)
        self.grid.relayout()
        self.baseline_label.setText(shell_state.plain_baseline_text(self.baseline_label.text()))

    def _edited(self):
        super()._edited()
        self.saved_note.setText("Saving…")
        self._timer().start()

    def apply(self):
        self.__dict__["_applying"] = True
        try:
            super().apply()
        finally:
            self.__dict__["_applying"] = False
        self.saved_note.setText("Saved")

    def _profile_changed(self):
        if self.__dict__.get("_applying"):
            self.baseline_label.setText(shell_state.plain_baseline_text(self.baseline_label.text()))
            return
        super()._profile_changed()
        self.baseline_label.setText(shell_state.plain_baseline_text(self.baseline_label.text()))


class _Centred(QWidget):
    """Holds the settings column centred in the scroll area. Its height for a width is the column's at the
    column's own (capped) width; the plain layout asked at the full width, where text wraps less, so the
    column got less height than it needs and its rows were squeezed (lab: the Settings target rows)."""

    def __init__(self, content):
        super().__init__()
        self.content = content
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(content, 1, Qt.AlignmentFlag.AlignHCenter)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        # centred, the column gets its preferred width (within its limits), not the whole row
        hint = max(self.content.sizeHint().width(), self.content.minimumWidth())
        return self.content.heightForWidth(min(width, hint, self.content.maximumWidth()))


class SettingsScreen(QWidget):
    def __init__(self, core, shell):
        super().__init__()
        self.core = core
        self.shell = shell
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(theme.XXL, theme.XL, theme.XXL, theme.XL)
        column.setSpacing(theme.L)
        column.addWidget(Headline("Settings", level="title"))

        # --- microphone ---
        mic = Card("Microphone", "Where VoiceLab listens. A second input can carry an electroglottograph (EGG).")
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.M)
        grid.setVerticalSpacing(theme.S)
        self.mic_choice = Select([(name or "", label) for label, name in core.input_devices()])
        self.mic_choice.setMinimumWidth(320)
        self.mic_choice.setCurrentIndex(max(0, self.mic_choice.findData(core.profile.input_device or "")))
        self.mic_choice.currentIndexChanged.connect(self._mic_picked)
        self.egg_label = Label("EGG", role="label", tone="secondary")
        self.egg_choice = Select(EGG_CHOICES)
        self.egg_choice.setToolTip("An electroglottograph on one input channel, with the mic on the other. "
                                   "Needs a device with two inputs.")
        self.egg_choice.currentIndexChanged.connect(lambda *_: self.change_mic())
        grid.addWidget(Label("Microphone", role="label", tone="secondary"), 0, 0)
        grid.addWidget(self.mic_choice, 0, 1)
        grid.addWidget(self.egg_label, 1, 0)
        grid.addWidget(self.egg_choice, 1, 1)
        grid.setColumnStretch(2, 1)
        mic.body.addLayout(grid)
        check_row = QHBoxLayout()
        self.mic_check_btn = Button("Run mic check", variant="secondary", icon="ph.microphone")
        self.mic_check_btn.clicked.connect(shell.mic_check)
        check_row.addWidget(self.mic_check_btn)
        check_row.addWidget(Caption("About 8 seconds: a moment of quiet, then a steady “ah”."), 1)
        mic.body.addLayout(check_row)
        self.mic_result = Label("", role="body", wrap=True)
        self.mic_numbers = Caption("")
        self.mic_fixes = Label("", role="body", tone="secondary", wrap=True)
        mic.add(self.mic_result)
        mic.add(self.mic_fixes)
        mic.add(self.mic_numbers)
        self.mic_card = mic
        column.addWidget(mic)

        # --- takes folder ---
        folder = Card("Takes folder", "Each take is saved here as a WAV file with its analysis beside it.")
        self.folder_label = Label("", role="body", wrap=True)
        folder.add(self.folder_label)
        row = QHBoxLayout()
        self.folder_btn = Button("Change folder…", variant="secondary", icon="ph.folder-open")
        self.folder_btn.clicked.connect(shell.choose_takes_folder)
        self.reload_btn = Button("Reload", variant="ghost", icon="ph.arrows-clockwise")
        self.reload_btn.setToolTip("Pick up files added, renamed or deleted in the folder.")
        self.reload_btn.clicked.connect(shell.load_takes)
        row.addWidget(self.folder_btn)
        row.addWidget(self.reload_btn)
        row.addStretch(1)
        folder.body.addLayout(row)
        column.addWidget(folder)

        # --- voice goal ---
        goal = Card("Voice goal", VOICE_GOAL_TEXT)
        self.goal = SegmentedControl([(goal_key(g), label) for g, label in VOICE_GOAL_CHOICES],
                                     current=goal_key(core.profile.voice_goal))
        self.goal.changed.connect(lambda key: core.set_voice_goal(None if key == "exploring" else key))
        goal.add(self.goal)
        column.addWidget(goal)

        # --- targets ---
        # The editor (spec 06) brings its own cards, so it sits under a plain heading, not in one more card.
        target_section = Section("Targets and baseline")
        self.targets = SettingsTargets(core)
        target_section.add(self.targets)
        column.addSpacing(theme.S)
        column.addWidget(target_section)
        column.addSpacing(theme.S)

        # --- appearance ---
        look = Card("Appearance")
        row = QHBoxLayout()
        row.setSpacing(theme.M)
        row.addWidget(Label("Theme", role="label", tone="secondary"))
        self.theme_choice = Select(THEME_CHOICES, current=core.ui_pref("theme"))
        self.theme_choice.changed.connect(lambda key: core.set_ui_pref(theme=key))
        row.addWidget(self.theme_choice)
        row.addSpacing(theme.XL)
        self.motion = Toggle(bool(core.ui_pref("reduce_motion")), "Reduce motion")
        self.motion.toggled.connect(lambda on: core.set_ui_pref(reduce_motion=on))
        row.addWidget(self.motion)
        row.addStretch(1)
        look.body.addLayout(row)
        column.addWidget(look)

        # --- about ---
        about = Card("About VoiceLab")
        about.add(Label(ABOUT_TEXT, role="body", tone="secondary", wrap=True))
        keys = Section("Keyboard shortcuts", level="label")
        for keys_text, what in shell_state.SHORTCUTS:
            row = QHBoxLayout()
            row.setSpacing(theme.L)
            key = Label(keys_text, role="numeric")
            key.setMinimumWidth(120)
            key.setAccessibleName(f"Shortcut {keys_text}")
            row.addWidget(key)
            row.addWidget(Label(what, role="body", tone="secondary", wrap=True), 1)
            keys.body.addLayout(row)
        about.add(keys)
        welcome = Button("Show the welcome again", variant="ghost", icon="ph.hand-waving")
        welcome.clicked.connect(shell.show_onboarding)
        about.add(welcome)
        column.addWidget(about)
        column.addStretch(1)

        content.setMaximumWidth(COLUMN_WIDTH + 2 * theme.XXL)
        holder = _Centred(content)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(holder)
        outer.addWidget(self.scroll)

        listen(self, core, dict((
            ("mic_changed", self._mic_changed), ("folder_changed", self.refresh),
            ("profile_changed", self.refresh), ("controls_changed", self._controls))))
        self.update_egg_choice()
        self.refresh()

    # --- what it shows ---

    def refresh(self, *_):
        core = self.core
        text, tooltip = core.folder_label()
        self.folder_label.setText(str(core.store.folder) if core.store else "No folder yet: takes are kept in "
                                  "your history only.")
        self.folder_btn.setText("Change folder…" if core.store else "Choose folder…")
        self.folder_btn.setToolTip(tooltip)
        self.reload_btn.setVisible(core.store is not None)
        self.goal.set_current(goal_key(core.profile.voice_goal), animate=False)
        lines = core.mic_check_text()
        last = core.mic_check_summary()
        if last is None:
            self.mic_result.setText("No mic check yet. It takes 8 seconds and says what to fix.")
        elif last["ok"]:
            self.mic_result.setText(f"Last checked {last['when']}: your input looks good.")
        else:
            self.mic_result.setText(f"Last checked {last['when']}: {last['issues']} thing"
                                    f"{'s' if last['issues'] != 1 else ''} to fix.")
        self.mic_fixes.setText("\n".join(f"• {fix}" for fix in (last or {}).get("fixes", [])))
        self.mic_fixes.setVisible(bool(last and last["fixes"]))
        self.mic_numbers.setText(" · ".join(lines[1:]))
        self.mic_numbers.setVisible(len(lines) > 1)

    def _controls(self, controls):
        self.reload_btn.setEnabled(controls["reload"])

    # --- mic and EGG ---

    def show_mic(self):
        """Scroll to the microphone card and put the cursor on its picker (a "Choose mic" button lands here)."""
        self.scroll.ensureWidgetVisible(self.mic_card)
        self.mic_choice.setFocus()

    def current_mic(self):
        return self.mic_choice.currentData() or None

    def egg_channel(self):
        key = self.egg_choice.currentData()
        return None if self.egg_choice.isHidden() or key in (None, "none") else int(key)

    def update_egg_choice(self):
        """Offer the EGG option only if the chosen mic has two or more inputs."""
        two_inputs = self.core.egg_possible(self.current_mic())
        self.egg_label.setVisible(two_inputs)
        self.egg_choice.setVisible(two_inputs)
        if not two_inputs:
            self.set_egg_choice(None)

    def set_egg_choice(self, channel):
        self.egg_choice.blockSignals(True)
        self.egg_choice.set_current_key("none" if channel is None else str(channel))
        self.egg_choice.blockSignals(False)

    def restore_egg(self):
        """At startup, once the mic is open: switch the remembered EGG channel on."""
        if self.core.profile.egg_channel is not None and not self.egg_choice.isHidden():
            self.set_egg_choice(self.core.profile.egg_channel)
            self.change_mic()

    def _mic_picked(self, *_):
        self.update_egg_choice()
        self.change_mic()

    def change_mic(self):
        self.core.open_mic(self.current_mic(), self.egg_channel())

    def _mic_changed(self, info):
        if info["egg_channel"] is None:
            self.set_egg_choice(None)
