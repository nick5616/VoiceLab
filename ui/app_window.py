"""The main window: a nav rail (Dashboard, Live, Practice, Takes, Progress, Settings), a top bar (Speak /
Sing, voice goal, mic, what's running, session time), one input banner, the screens, a bottom action
bar (import, folder, record, replay, mic check) and toasts. The window asks the questions (dialogs) and
shows the results; app_core.VoiceLabCore decides everything else."""
import os
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QKeySequence, QPainter, QShortcut
from PyQt6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QMainWindow, QMessageBox,
                             QStackedWidget, QVBoxLayout, QWidget)

import shell_state
from app_core import REST_TOOLTIP, VOICE_GOAL_CHOICES, status_display
from ui import theme
from ui.components import (ActivityCenter, Banner, Button, FitLabel, IconButton, Label, MicIndicator, NavRail,
                           RecordButton, RecordingLine, RecordingPill, SegmentedControl, Select, ThemedWidget,
                           ToastStack)
from ui.mic_check_dialog import MicCheckDialog
from ui.onboarding import Onboarding
from ui.sheets import ImportSheet, ShortcutSheet
from ui.screens import ProgressScreen, TakesScreen
from ui.screens.dashboard import DashboardScreen
from ui.takes import listen
from ui.screens.live import LiveScreen
from ui.screens.practice import PracticeScreen
from ui.screens.settings import SettingsScreen

REANALYSED_PREFIX = "Re-analysed"       # the core's success status when the older takes are done
AUDIO_FILE_FILTER = "Audio files (*.wav *.flac *.ogg *.mp3 *.aiff *.aif)"
NAV = [("dashboard", "ph.squares-four", "Dashboard"), ("live", "ph.activity", "Live"),
       ("practice", "ph.barbell", "Practice"), ("takes", "ph.list-bullets", "Takes"),
       ("progress", "ph.chart-line-up", "Progress")]
FOOTER = [("settings", "ph.gear-six", "Settings")]
SCREENS = [key for key, _, _ in NAV + FOOTER]
ACTION_BAR_SCREENS = {"dashboard", "live", "practice"}      # screens that record
SESSION_TICK_MS = 15000
BANNER_HOLD_MS = 3000           # an input banner stays at least this long, so it doesn't flicker
REST_TOAST_MS = 15000           # the rest nudge stays a little longer than other toasts
MODES = [("speak", "Speak", "ph.chat-circle"), ("sing", "Sing", "ph.music-note")]
GOALS = [(g or "exploring", label) for g, label in VOICE_GOAL_CHOICES]
TOP_BAR_HEIGHT = 60             # px: room for the two-line input banner
MIC_POLL_MS = 1000              # ms between looks at the mic's heartbeat
OFFER_UPDATE_KEY = "update"     # the banner action that accepts the re-analysis offer
SHORTCUT_HINT = "Keyboard shortcuts (?)"
BASELINE_TOAST_MS = 15000       # the baseline question stays a little longer than other toasts
ACTION_BAR_HEIGHT = 112         # px: the record button and its caption
RECORD_SIZE = 56                # px: the record button's circle


def ask(parent, title, text):
    """A yes/no question; True for yes."""
    return QMessageBox.question(parent, title, text) == QMessageBox.StandardButton.Yes


class Bar(ThemedWidget):
    """A flat strip on the surface colour with a hairline on one edge (top bar, action bar). It follows the
    theme through ThemedWidget, so its hook goes away with it (a lambda would outlive the bar)."""

    def __init__(self, edge="bottom"):
        super().__init__()
        self.edge = edge

    def paintEvent(self, event):
        p = QPainter(self)
        t = theme.current()
        p.fillRect(self.rect(), t.q("surface"))
        if self.edge == "right":
            p.fillRect(self.width() - 1, 0, 1, self.height(), t.q("border"))
            return
        y = self.height() - 1 if self.edge == "bottom" else 0
        p.fillRect(0, y, self.width(), 1, t.q("border"))


class ActivityButton(ActivityCenter):
    """The top bar's activity: a spinner and the job's name ("Updating takes 3/11"); click it to see each job,
    what it is doing and what it changes."""

    def __init__(self):
        super().__init__(expanded=False)
        self.setMinimumWidth(168)
        self.setMaximumWidth(260)
        self.popup = QFrame(self, Qt.WindowType.Popup)      # parented: deleted with the button, never by the GC
        box = QVBoxLayout(self.popup)
        box.setContentsMargins(0, 0, 0, 0)
        self.detail = ActivityCenter(expanded=True)
        self.detail.setMinimumWidth(380)
        self.detail.setMaximumWidth(460)
        box.addWidget(self.detail)
        self.setAccessibleName("Background work")

    def set_jobs(self, jobs):
        super().set_jobs(jobs)
        if getattr(self, "popup", None) is not None:
            self.detail.set_jobs(jobs)
            if not jobs:
                self.popup.hide()

    def set_expanded(self, expanded):
        """Open the job list as a popup instead of growing the top bar."""
        if getattr(self, "popup", None) is None or not self.rows:
            return
        self.popup.adjustSize()
        self.popup.move(self.mapToGlobal(QPoint(self.width() - self.popup.width(), self.height() + 4)))
        self.popup.show()


class TopBar(Bar):
    """Speak / Sing, the goal, the mic (name, state in words, level), a quiet status line, the recording
    pill, the session time and what is running. Nothing in it is cut: the status line shows a whole phrase
    that fits, or none."""

    def __init__(self, core):
        super().__init__("bottom")
        self.setFixedHeight(TOP_BAR_HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.L, theme.S, theme.L, theme.S)
        row.setSpacing(theme.M)
        self.mode = SegmentedControl(MODES, current=core.mode)
        self.mode.changed.connect(core.set_mode)
        self.mode.setToolTip("Speak or sing: the readings, zones and tips follow the mode.")
        row.addWidget(self.mode)
        row.addSpacing(theme.S)
        self.goal_label = Label("Goal", role="label", tone="muted")
        row.addWidget(self.goal_label)
        self.goal = Select(GOALS, current=core.profile.voice_goal or "exploring")
        self.goal.setToolTip("Where the tips after each take point. Change it here or in Settings.")
        self.goal.changed.connect(lambda key: core.set_voice_goal(None if key == "exploring" else key))
        row.addWidget(self.goal)
        row.addSpacing(theme.S)
        self.mic = MicIndicator()
        row.addWidget(self.mic)
        self.status = FitLabel("", role="caption", tone="muted")
        row.addWidget(self.status, 1)
        self.recording = RecordingPill()
        row.addWidget(self.recording)
        self.session = Label("", role="caption", tone="secondary")
        self.session.setToolTip(REST_TOOLTIP)
        row.addWidget(self.session)
        self.activity = ActivityButton()
        row.addWidget(self.activity)

    def make_room(self, on):
        """While the input banner shows, the goal, the quiet status line and the session time give way to it:
        the banner takes their width, so it never pushes the screen below it about."""
        self.room = on
        for w in (self.goal_label, self.goal, self.session):
            w.setVisible(not on)
        self.status.setVisible(not on and not self.recording.isVisible())

    def set_recording(self, on):
        """The REC pill in, the quiet status line out (unless the banner already took its place)."""
        if on and not self.recording.isVisible():
            self.recording.start()
        elif not on and self.recording.isVisible():
            self.recording.stop()
        self.status.setVisible(not on and not getattr(self, "room", False))


class ActionBar(Bar):
    """Import and the folder on the left, Record in the middle, Replay and the mic check on the right."""

    def __init__(self, shell):
        super().__init__("top")
        self.setFixedHeight(ACTION_BAR_HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.L, 0, theme.L, 0)
        left, right = QHBoxLayout(), QHBoxLayout()
        left.setSpacing(theme.S)
        right.setSpacing(theme.S)
        self.import_btn = Button("Import audio…", variant="secondary", icon="ph.file-arrow-up")
        self.import_btn.setToolTip("Analyse a recording: your own, or a reference singer or speaker (Ctrl+I).")
        self.import_btn.clicked.connect(shell.import_audio)
        self.folder_btn = Button("", variant="ghost", icon="ph.folder-open")
        self.folder_btn.clicked.connect(shell.choose_takes_folder)
        self.reload_btn = IconButton("ph.arrows-clockwise", "Reload the takes folder, picking up files added, "
                                     "renamed or deleted there.")
        self.reload_btn.clicked.connect(shell.load_takes)
        for w in (self.import_btn, self.folder_btn, self.reload_btn):
            left.addWidget(w)
        left.addStretch(1)
        self.record_btn = RecordButton(RECORD_SIZE)
        self.record_btn.setToolTip("Record a take (Space)")
        self.record_btn.clicked.connect(shell.toggle_recording)
        self.replay_btn = Button("Replay last 10 s", variant="secondary", icon="ph.arrow-counter-clockwise")
        self.replay_btn.setToolTip("Hear the last few seconds the mic heard (R)")
        self.replay_btn.clicked.connect(shell.replay)
        self.mic_check_btn = Button("Mic check", variant="ghost", icon="ph.microphone")
        self.mic_check_btn.setToolTip("Check your input level, noise and distance to the mic.")
        self.mic_check_btn.clicked.connect(shell.mic_check)
        right.addStretch(1)
        right.addWidget(self.replay_btn)
        right.addWidget(self.mic_check_btn)
        row.addLayout(left, 1)
        row.addWidget(self.record_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(right, 1)


class AppWindow(QMainWindow):
    def __init__(self, core, signals):
        super().__init__()
        self.core = core
        self.engine = core.engine
        self.profile = core.profile
        self.signals = signals
        self.setWindowTitle("VoiceLab")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1600, int(screen.width() * 0.92)), min(980, int(screen.height() * 0.92)))
        self.mic_dialog = None
        self.last_mic_check = None
        self.onboarding = None
        self.last_status = core.status
        self.status_label = Label(core.status[0])      # the last status, toast or not (hidden; tests read it)
        self.status_label.hide()
        self.banner_info = None         # the live snapshot's input banner, if any
        self.live_state = None          # the snapshot's state (hearing, silent, ...) the mic block shows
        self.apply_ui_prefs(dict(self.profile.ui))

        central = QWidget()
        self.setCentralWidget(central)
        frame = QVBoxLayout(central)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        self.rec_line = RecordingLine()
        frame.addWidget(self.rec_line)
        outer = QHBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        frame.addLayout(outer, 1)
        self.nav = NavRail(NAV, FOOTER, current="dashboard")
        self.nav.selected.connect(self.go)
        rail = Bar("right")
        rail_box = QVBoxLayout(rail)
        rail_box.setContentsMargins(0, theme.S, 0, theme.S)
        rail_box.addWidget(self.nav)
        outer.addWidget(rail)
        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        outer.addLayout(main, 1)

        self.top = TopBar(core)
        self.activity = self.top.activity
        main.addWidget(self.top)
        # One banner in the top bar, in place of the goal, the status line and the session time while it shows,
        # so it never pushes the screens about when it comes and goes. It wraps to two lines; it never cuts.
        self.banner = Banner("", kind="warning", action="Mic check", on_action=self._banner_action)
        self.banner.hide()
        self.banner.set_compact(True)
        self.banner.stack_narrow = False        # it lives in the top bar: one row, text to the left of the button
        row = self.top.layout()
        row.insertWidget(row.indexOf(self.top.status) + 1, self.banner, 1)
        self.offer = Banner("", kind="info", action="Update now", on_action=self.accept_update, second_action="Later",
                            on_second=self.defer_update)
        self.offer.hide()
        main.addWidget(self.offer)
        self.banner_timer = QTimer(self)
        self.banner_timer.setSingleShot(True)
        self.banner_timer.setInterval(BANNER_HOLD_MS)
        self.banner_timer.timeout.connect(lambda: self.show_banner(self.banner_info, force=True))

        self.stack = QStackedWidget()
        main.addWidget(self.stack, 1)
        self.dashboard = DashboardScreen(core, self)
        self.live = LiveScreen(core, self)
        self.practice = PracticeScreen(core, self)
        self.takes = TakesScreen(core)
        self.takes.record_requested.connect(self.start_recording)
        self.takes.import_requested.connect(self.import_audio)
        self.takes.mic_check_requested.connect(self.mic_check)
        self.progress = ProgressScreen(core)
        self.settings = SettingsScreen(core, self)
        self.screens = dict(zip(SCREENS, (self.dashboard, self.live, self.practice, self.takes, self.progress,
                                          self.settings)))
        for key in SCREENS:
            self.stack.addWidget(self.screens[key])
        self.action_bar = ActionBar(self)
        self.record_btn = self.action_bar.record_btn
        main.addWidget(self.action_bar)
        self.toasts = ToastStack(central)

        # the views the old tests and the practice screen reach for
        self.vowel_check = self.practice.vowel_check
        self.exercise_view = self.practice.exercise
        self.voice_map = self.practice.voice_map
        self.mic_choice, self.egg_choice = self.settings.mic_choice, self.settings.egg_choice
        self.egg_label = self.settings.egg_label

        self.session_timer = QTimer(self)
        self.session_timer.setInterval(SESSION_TICK_MS)
        self.session_timer.timeout.connect(self.update_session)
        self.shortcuts = {}
        self._shortcuts()
        self.shortcut_sheet = None
        self.mic_timer = QTimer(self)
        self.mic_timer.setInterval(MIC_POLL_MS)
        self.mic_timer.timeout.connect(self.poll_mic)
        self.mic_timer.start()

        signals.frame_ready.connect(self.on_frame)
        listen(self, core, dict((("status", self.set_status), ("controls_changed", self.show_controls),
                              ("recording", lambda on: self.show_record_state()), ("busy", self._busy),
                              ("session_started", self.start_session), ("folder_changed", self.update_folder),
                              ("mode_changed", self._mode_changed), ("profile_changed", self._profile_changed),
                              ("ui_changed", self.apply_ui_prefs), ("take_added", lambda *_: self.update_badges()),
                              ("takes_loaded", lambda *_: self.update_badges()),
                              ("take_updated", lambda *_: self.update_badges()),
                              ("take_ready", self._take_ready), ("update_offered", self._update_offered),
                              ("reanalysis_done", self._reanalysis_done),
                              ("mic_changed", lambda info: self.show_banner(self.banner_info, force=True)))))
        self.update_folder()
        self.show_controls(core.controls())
        self.update_mic_status()
        self.go("dashboard")
        if core.store:
            self.load_takes()

    # --- navigation ---

    def current(self):
        return SCREENS[self.stack.currentIndex()]

    def go(self, key):
        """Show a screen (nav, Ctrl+1-6)."""
        self.stack.setCurrentWidget(self.screens[key])
        self.nav.set_current(key)
        self.action_bar.setVisible(key in ACTION_BAR_SCREENS)
        self.toasts.set_bottom_inset(ACTION_BAR_HEIGHT if key in ACTION_BAR_SCREENS else 0)
        self.action_bar.replay_btn.setVisible(key != "live")      # the Live screen has its own

    def open_report(self, uid):
        """A take's full report, on the Takes screen."""
        self.go("takes")
        if uid:
            self.takes.show_take(uid)

    def _shortcuts(self):
        keys = {"record": ("Space", self.toggle_recording), "replay": ("R", self.replay),
                "import": ("Ctrl+I", self.import_audio), "fullscreen": ("F11", self.toggle_fullscreen),
                "shortcuts": ("?", self.show_shortcuts)}
        for i, key in enumerate(SCREENS, start=1):
            keys[f"screen_{key}"] = (f"Ctrl+{i}", lambda k=key: self.go(k))
        for name, (sequence, fn) in keys.items():
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(fn)
            self.shortcuts[name] = shortcut

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    # --- frames ---

    def on_frame(self, frame):
        """One live frame: the core follows it, the visible screen draws it, the mic block shows the level."""
        views = self.core.on_frame(frame)
        screen = self.stack.currentWidget()
        if hasattr(screen, "show_frame"):
            screen.show_frame(frame, views)
        if views["map"] and not self.voice_map.isVisible():
            self.voice_map.update_metric_choices()      # e.g. the EGG metric appears once it has data
        banner = views["live"]["banner"]
        if banner != self.banner_info:
            self.show_banner(banner)
        level = shell_state.mic_level_fraction(frame.get("level"))
        self.top.mic.set_level(level)
        state = shell_state.live_state(frame, views["live"])
        if state != self.live_state:
            self.live_state = state
            self.update_mic_status()
        if self.onboarding is not None:
            self.onboarding.show_live(state, frame.get("f0"), level)

    # --- banner, status and toasts ---

    def show_banner(self, info, force=False):
        """The input banner (what the live frames say is wrong with the input); once shown it stays at least
        BANNER_HOLD_MS. A mic that is missing or stopped outranks it."""
        self.banner_info = info
        shown = self.core.mic_banner() or info
        if shown is None:
            if not self.banner.isHidden() and self.banner_timer.isActive() and not force:
                return
            self.banner.hide()
            self.top.make_room(False)
        else:
            self.banner.kind = shown["kind"]
            self.banner.set_text(shown["text"], shown.get("more"))
            self.banner.action.setText(shown["action"])
            if self.banner.isHidden():
                self.top.make_room(True)
                self.banner.show()
                self.banner_timer.start()
            self.banner.update()
        self.update_mic_status()

    def _banner_action(self):
        target = (self.core.mic_banner() or self.banner_info or {}).get("target")
        if target == "mic":
            self.run_action("choose_mic")
        elif target == "settings":
            self.go("settings")
        else:
            self.mic_check()

    def set_status(self, text, level="info"):
        """A core "status": a toast for successes, warnings and errors (an error with the button that fixes
        it), a quiet line otherwise."""
        self.last_status = (text, level)
        self.status_label.setText(text)
        shown = status_display(text, level)
        if shown["toast"] and text.startswith(REANALYSED_PREFIX):
            pass                        # "reanalysis_done" gives this one a toast with its own button
        elif shown["toast"]:
            action = shell_state.error_action(text, level)
            self.toasts.show_toast(text, shown["toast"], action=action[1] if action else None,
                                   on_action=(lambda checked=False, key=action[0]: self.run_action(key))
                                   if action else None)
        else:
            self.top.status.set_alternatives(shown["line"], (shown["line"] or "").split(":")[0])
        self.update_badges()

    def run_action(self, key):
        """The button of a toast or banner: what to do about the problem it names."""
        if key == "choose_mic":
            self.go("settings")
            self.settings.show_mic()
        elif key == "mic_check":
            self.mic_check()
        elif key == "choose_folder":
            self.choose_takes_folder()
        elif key == "open_takes":
            self.go("takes")
        elif key == "rebuild_map":
            self.go("practice")
            self.rebuild_voice_map()
        elif key == "update_takes":
            self.accept_update()
        else:
            self.go("settings")

    def status_text(self):
        """The last core status, whether it became a toast or the quiet line."""
        return self.last_status[0]

    def show_toast(self, text, kind="info", timeout_ms=None, action=None, on_action=None):
        if timeout_ms or action:
            return self.toasts.show_toast(text, kind, action=action, on_action=on_action,
                                          **({"timeout_ms": timeout_ms} if timeout_ms else {}))
        return self.toasts.show_toast(text, kind)

    def _busy(self, jobs):
        described = self.core.describe_jobs(jobs)
        self.activity.set_jobs(described)
        summary = shell_state.activity_summary(described)
        self.activity.set_summary(summary["text"] if described else None, summary["tooltip"])
        self.update_badges()
        self.show_record_state()

    def update_badges(self):
        n = self.core.takes_in_progress()
        self.nav.set_badge("takes", str(n) if n else None)

    def update_mic_status(self):
        status = self.core.mic_status(self.banner_info, self.live_state)
        ind = self.core.mic_indicator(self.live_state, self.banner_info)
        tooltip = f"{status['full']}\n{ind['tooltip']}"
        if status["state"] != "ok" and status["tooltip"] != ind["tooltip"]:
            tooltip += f"\n{status['tooltip']}"
        self.top.mic.set_mic(status["label"], ind["words"], ind["dot"], ind["key"], tooltip, status["full"])

    def poll_mic(self):
        """About once a second: the mic's heartbeat. A mic that stopped (or came back) changes the banner and
        the mic block."""
        before = self.core.mic_lost
        self.core.check_mic()
        if self.core.mic_lost != before:
            self.show_banner(self.banner_info, force=True)

    def _take_ready(self, take):
        """A new take is analysed: say so, with the button that opens its report; once, offer a baseline."""
        ready = self.core.take_ready_toast(take)
        uid = take.get("uid")
        if ready:
            self.toasts.show_toast(ready[0], "success", action=ready[1], timeout_ms=REST_TOAST_MS,
                                   on_action=lambda checked=False: self.open_report(uid))
        question = self.core.baseline_prompt(take)
        if question:
            self.toasts.show_toast(question[0], "info", action=question[1], timeout_ms=BASELINE_TOAST_MS,
                                   on_action=lambda checked=False: self.set_baseline(uid))

    def _reanalysis_done(self, count):
        self.toasts.show_toast(f"Updated {count} take{'s' if count != 1 else ''} to the new analysis.", "success",
                               action=shell_state.ACTIONS["rebuild_map"], timeout_ms=REST_TOAST_MS,
                               on_action=lambda checked=False: self.run_action("rebuild_map"))

    def _update_offered(self, count, seconds):
        """Takes made by an older analysis: ask before changing their numbers."""
        self.offer.set_text(self.core.update_offer_text(count))
        self.offer.show()

    def accept_update(self):
        self.offer.hide()
        self.core.accept_reanalysis()

    def defer_update(self):
        self.offer.hide()
        self.core.defer_reanalysis()

    def show_shortcuts(self):
        """The keyboard cheat-sheet."""
        self.shortcut_sheet = ShortcutSheet(self)
        self.shortcut_sheet.open()
        return self.shortcut_sheet

    # --- session ---

    def start_session(self):
        self.session_timer.start()
        self.update_session()

    def update_session(self):
        self.top.session.setText(self.core.session_text())
        nudge = self.core.rest_nudge()
        if nudge:
            self.show_toast(nudge, "info", timeout_ms=REST_TOAST_MS)

    # --- mode, profile, preferences ---

    def _mode_changed(self, mode):
        self.top.mode.set_current(mode)

    def _profile_changed(self):
        self.top.goal.blockSignals(True)
        self.top.goal.set_current_key(self.profile.voice_goal or "exploring")
        self.top.goal.blockSignals(False)

    def apply_ui_prefs(self, prefs):
        """Theme and motion, live. VOICELAB_REDUCE_MOTION=1 in the environment keeps motion off regardless."""
        forced = os.environ.get("VOICELAB_REDUCE_MOTION", "") not in ("", "0")
        theme.set_reduce_motion(bool(prefs.get("reduce_motion")) or forced)
        theme.set_theme(prefs.get("theme") or "dark")

    # --- recording and import ---

    def show_controls(self, controls):
        self.action_bar.import_btn.setEnabled(controls["import"])
        self.action_bar.reload_btn.setEnabled(controls["reload"])
        self.show_record_state()

    def show_record_state(self):
        state, caption = self.core.record_state()
        if caption:
            self.record_btn.captions[state] = caption
        self.record_btn.set_state(state)
        self.record_btn.setToolTip("Stop (Space)" if state == "recording" else "Record a take (Space)")
        self.record_btn.update()
        # the recording signature: a red line along the top, a REC pill in the top bar, the quiet line away
        recording = state == "recording"
        self.rec_line.set_on(recording)
        self.top.set_recording(recording)
        self.update_mic_status()

    def toggle_recording(self):
        usable = self.core.mic_available and not self.core.mic_lost
        if self.core.is_recording or self.core.controls()["record"] and usable:
            self.core.toggle_recording()

    def start_recording(self):
        """Record (from an empty state's "Record" button): a screen that records, then the take."""
        if self.current() not in ACTION_BAR_SCREENS:
            self.go("dashboard")
        if not self.core.is_recording:
            self.toggle_recording()

    def replay(self):
        """Play the last 10 s; the live graphs hold still and show a playhead."""
        if self.core.replay_progress() is not None:
            self.core.stop_replay()
            return
        if self.core.replay():
            for view in (self.dashboard.live, self.live.view):
                view.start_replay()

    def import_audio(self):
        if not self.core.controls()["import"]:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import audio", "", AUDIO_FILE_FILTER)
        if not path:
            return
        options = self.choose_import_options(path)
        if options is None:
            return
        isolate, egg_channel = options
        self.core.import_file(path, isolate, egg_channel)

    def choose_import_options(self, path):
        """(isolate, egg_channel), or None when cancelled. One sheet asks only what applies: voice only or with
        music (when vocals can be separated) and, for a stereo file, whether a channel is a second sensor. A
        plain mono file with nothing to choose imports straight away."""
        can_isolate = self.core.can_isolate()
        stereo = self.core.file_channels(path) == 2
        if not (can_isolate or stereo):
            return False, None
        sheet = ImportSheet(Path(path).name, can_isolate, stereo, self)
        if sheet.exec() != sheet.DialogCode.Accepted:
            return None
        return sheet.answers()

    # --- takes folder and Voice Map ---

    def update_folder(self):
        text, tooltip = self.core.folder_label()
        self.action_bar.folder_btn.setText(f"Folder: {text.strip()}" if self.core.store else text.strip())
        self.action_bar.folder_btn.setToolTip(tooltip)
        self.action_bar.reload_btn.setVisible(self.core.store is not None)

    def choose_takes_folder(self):
        start = str(self.core.store.folder) if self.core.store else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Takes folder", start)
        if folder:
            self.set_takes_folder(folder)

    def set_takes_folder(self, folder):
        self.core.set_takes_folder(folder)

    def load_takes(self):
        self.core.reload()

    def rebuild_voice_map(self):
        self.core.rebuild_voice_map(confirm=lambda text: ask(self, "Rebuild Voice Map", text))

    # --- mic ---

    def update_egg_choice(self):
        self.settings.update_egg_choice()

    def restore_egg(self):
        self.settings.restore_egg()

    def mic_check(self):
        """Open the mic check (non-modal); its result is kept in the profile. Nothing without an engine."""
        if self.engine is None:
            return None
        self.mic_dialog = MicCheckDialog(self.engine, self, core=self.core)
        self.mic_dialog.finished_check.connect(self._mic_checked)
        self.mic_dialog.show()
        return self.mic_dialog

    def _mic_checked(self, result):
        self.last_mic_check = result
        self.core.save_mic_check(result)
        self.settings.refresh()
        if self.onboarding is not None:
            self.onboarding.mic_checked()

    # --- first run ---

    def show_onboarding(self):
        """The welcome steps (first run, or from Settings)."""
        self.onboarding = Onboarding(self.core, self.choose_takes_folder, self.mic_check, self)
        self.onboarding.completed.connect(self._onboarded)
        self.onboarding.first_take.connect(self.start_recording)
        self.onboarding.open()
        return self.onboarding

    def _onboarded(self):
        self.settings.refresh()

    def start_up(self):
        """After the window shows: the welcome on the first run."""
        if self.core.should_onboard():
            self.show_onboarding()

    # --- takes ---

    def set_target(self, uid):
        return self.core.set_target(uid)

    def set_baseline(self, uid):
        self.core.set_baseline(uid)

    def closeEvent(self, event):
        self.core.close()
        super().closeEvent(event)
