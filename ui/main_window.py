"""The main window: the live panel, the take cards and the controls, drawn from app_core.VoiceLabCore.
The window asks the questions (dialogs) and shows the results; the core decides everything else."""
from pathlib import Path

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QScrollArea, QFrame, QFileDialog, QMessageBox, QComboBox, QSplitter)
from PyQt6.QtCore import Qt, QSize, QTimer
import qtawesome as qta

from app_core import EGG_QUESTION, ISOLATE_QUESTION, REST_TOOLTIP, TAKES_FOLDER_QUESTION
from ui.exercise_view import ExerciseView
from ui.live_panel import LivePanel
from ui.meters import BUTTON_STYLE, status_style
from ui.progress_dialog import ProgressDialog
from ui.take_card import TakeCard
from ui.targets_dialog import TargetsDialog
from ui.voice_map_view import VoiceMapView
from ui.vowel_check import VowelCheck

AUDIO_FILE_FILTER = "Audio files (*.wav *.flac *.ogg *.mp3 *.aiff *.aif)"
EGG_CHOICES = [("none", None), ("channel 1", 0), ("channel 2", 1)]    # the EGG input channel
WIDE_LAYOUT_WIDTH = 1100        # from this window width, live and takes sit side by side...
TAKES_COLUMN_WIDTH = 480        # ...if the takes still get about this much
SESSION_TICK_MS = 15000


def ask(parent, title, text):
    """A yes/no question; True for yes."""
    return QMessageBox.question(parent, title, text) == QMessageBox.StandardButton.Yes


class MainWindow(QMainWindow):
    def __init__(self, core, signals):
        super().__init__()
        self.core = core
        self.engine = core.engine
        self.profile = core.profile
        self.signals = signals
        self.setWindowTitle("Voice Lab")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1400, int(screen.width() * 0.9)), min(950, int(screen.height() * 0.9)))
        self.setStyleSheet("background-color: #1e1e1e;")
        self.cards = []
        self.progress_dialog = None
        self.wide_layout = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.live_panel = LivePanel(core, self.edit_targets)
        self.vowel_check = VowelCheck(core)
        self.exercise_view = ExerciseView(core)
        self.voice_map = VoiceMapView(core)
        self.live_panel.add_tab(self.vowel_check, "Vowels", {"speak"})
        self.live_panel.add_tab(self.exercise_view, "Exercise", {"sing"}, default_for="sing")
        self.live_panel.add_tab(self.voice_map, "Voice Map", {"sing"})
        self.voice_map.rebuild_requested.connect(self.rebuild_voice_map)
        self.live_panel.apply_mode(core.mode)
        signals.frame_ready.connect(self.on_frame)

        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.more_btn = QPushButton()
        self.more_btn.setStyleSheet(BUTTON_STYLE)
        self.more_btn.clicked.connect(self.show_older_takes)
        self.more_btn.hide()
        self.scroll_layout.addWidget(self.more_btn)
        self.scroll_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.scroll_content)
        scroll.setStyleSheet("border: none;")

        controls = QFrame()
        controls.setStyleSheet("background-color: #252525; border-top: 1px solid #333;")
        controls_layout = QVBoxLayout(controls)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #888; font-size: 14px;")
        self.status_label.setText(core.status[0])
        # Session length, and a gentle rest reminder after a while of voice use.
        self.session_label = QLabel()
        self.session_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.session_label.setStyleSheet("color: #777; font-size: 11px; border: none;")
        self.session_label.hide()
        self.rest_label = QLabel()
        self.rest_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rest_label.setWordWrap(True)
        self.rest_label.setStyleSheet("color: #f1c40f; font-size: 12px; border: none;")
        self.rest_label.hide()
        self.session_timer = QTimer(self)
        self.session_timer.setInterval(SESSION_TICK_MS)
        self.session_timer.timeout.connect(self.update_session)

        self.record_btn = QPushButton()
        self.record_btn.setFixedSize(64, 64)
        self.record_btn.setIconSize(QSize(30, 30))
        self.update_record_button_style(recording=False)
        self.record_btn.clicked.connect(self.toggle_recording)

        self.import_btn = QPushButton(qta.icon("fa5s.file-audio", color="white"), " Import audio…")
        self.import_btn.setStyleSheet(BUTTON_STYLE)
        self.import_btn.setToolTip("Analyse a recording: your own, or a reference singer or speaker.")
        self.import_btn.clicked.connect(self.import_audio)
        progress_btn = QPushButton(qta.icon("fa5s.chart-line", color="white"), " Progress")
        progress_btn.setStyleSheet(BUTTON_STYLE)
        progress_btn.setToolTip("Your measurements over weeks.")
        progress_btn.clicked.connect(self.show_progress)
        self.mic_check_btn = QPushButton(qta.icon("fa5s.microphone-alt", color="white"), " Mic check")
        self.mic_check_btn.setStyleSheet(BUTTON_STYLE)
        self.mic_check_btn.setToolTip("Check your input level, noise and distance to the mic.")
        self.mic_check_btn.clicked.connect(self.mic_check)
        self.mic_check_btn.setVisible(hasattr(self.live_panel, "show_mic_check"))
        self.folder_btn = QPushButton(qta.icon("fa5s.folder-open", color="white"), "")
        self.folder_btn.setStyleSheet(BUTTON_STYLE)
        self.folder_btn.clicked.connect(self.choose_takes_folder)
        self.reload_btn = QPushButton(qta.icon("fa5s.sync-alt", color="white"), "")
        self.reload_btn.setStyleSheet(BUTTON_STYLE)
        self.reload_btn.setToolTip("Reload the takes folder, picking up files added, renamed or deleted there.")
        self.reload_btn.clicked.connect(self.load_takes)
        self.mic_choice = QComboBox()
        self.mic_choice.setStyleSheet("QComboBox { background: #1e1e1e; color: white; padding: 3px; }")
        self.mic_choice.setMaximumWidth(260)
        for label, name in core.input_devices():
            self.mic_choice.addItem(label, name)
        self.mic_choice.setCurrentIndex(max(0, self.mic_choice.findData(self.profile.input_device)))
        self.mic_choice.currentIndexChanged.connect(self.change_mic)
        mic_label = QLabel("Mic")
        mic_label.setStyleSheet("color: #aaa; border: none;")
        # The EGG goes on one channel of a two-input device, the mic on the other.
        self.egg_choice = QComboBox()
        self.egg_choice.setStyleSheet("QComboBox { background: #1e1e1e; color: white; padding: 3px; }")
        for text, channel in EGG_CHOICES:
            self.egg_choice.addItem(text, channel)
        self.egg_choice.setToolTip("An electroglottograph on one input channel, with the mic on the other. "
                                   "Needs a device with two inputs.")
        self.egg_choice.currentIndexChanged.connect(self.change_mic)
        self.egg_label = QLabel("EGG")
        self.egg_label.setStyleSheet("color: #aaa; border: none;")
        self.update_egg_choice()
        self.update_folder_button()

        # One row: files on the left, record in the middle, progress and mic on the right.
        left, right = QHBoxLayout(), QHBoxLayout()
        for w in (self.import_btn, self.folder_btn, self.reload_btn):
            left.addWidget(w)
        left.addStretch()
        right.addStretch()
        for w in (progress_btn, self.mic_check_btn, mic_label, self.mic_choice, self.egg_label, self.egg_choice):
            right.addWidget(w)
        button_row = QHBoxLayout()
        button_row.addLayout(left, 1)
        button_row.addWidget(self.record_btn)
        button_row.addLayout(right, 1)
        controls_layout.setContentsMargins(10, 4, 10, 8)
        controls_layout.setSpacing(4)
        controls_layout.addWidget(self.status_label)
        controls_layout.addWidget(self.session_label)
        controls_layout.addWidget(self.rest_label)
        controls_layout.addLayout(button_row)

        # Live panel and takes: side by side on a wide window, stacked on a
        # narrow one. The divider can be dragged either way.
        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        self.splitter.addWidget(self.live_panel)
        self.splitter.addWidget(scroll)
        self.takes_scroll = scroll
        scroll.setMinimumSize(320, 160)
        self.update_layout()
        main_layout.addWidget(self.splitter, 1)
        main_layout.addWidget(controls)

        events = core.events
        for name, handler in (("status", self.set_status), ("controls_changed", self.show_controls),
                              ("recording", self.update_record_button_style), ("take_added", self._take_added),
                              ("take_updated", self._take_updated), ("take_removed", self._take_removed),
                              ("takes_loaded", self._takes_loaded), ("profile_changed", self._profile_changed),
                              ("progress_changed", self.refresh_progress), ("session_started", self.start_session),
                              ("folder_changed", self.update_folder_button), ("mic_changed", self._mic_changed)):
            events.subscribe(name, handler)
        if core.store:
            self.load_takes()

    # --- FRAMES, MODE AND TARGETS ---

    def on_frame(self, frame):
        """One live frame: the core follows it, the views draw what it says."""
        views = self.core.on_frame(frame)
        self.live_panel.show_frame(frame, views["live"])
        self.vowel_check.show_state(views["vowels"])
        self.exercise_view.show_frame(views["exercise"])
        self.voice_map.frame_added(views["map"])

    def edit_targets(self):
        TargetsDialog(self.profile, self).exec()
        # Refresh even on Cancel: clearing the baseline applies immediately.
        self.core.refresh_profile()

    def _profile_changed(self):
        for card in self.cards:
            card.refresh_profile()

    # --- STATUS AND SESSION ---

    def set_status(self, text, level="info"):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(status_style(level))

    def start_session(self):
        self.session_label.show()
        self.session_timer.start()
        self.update_session()

    def update_session(self):
        self.session_label.setText(self.core.session_text())
        nudge = self.core.rest_nudge()
        if nudge:
            self.rest_label.setText(nudge)
            self.rest_label.setToolTip(REST_TOOLTIP)
            self.rest_label.show()

    # --- LAYOUT ---

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "splitter"):
            self.update_layout()

    def wheelEvent(self, event):
        """A wheel turn nothing else used (over the live panel or the bottom
        bar) scrolls the takes, so it works wherever the pointer is."""
        QApplication.sendEvent(self.takes_scroll.verticalScrollBar(), event)

    def update_layout(self):
        # Side by side only when the takes still get a comfortable column; this
        # also keeps the window able to shrink back into the stacked layout.
        wide_from = max(WIDE_LAYOUT_WIDTH, self.live_panel.minimumSizeHint().width() + TAKES_COLUMN_WIDTH)
        wide = self.width() >= wide_from
        if wide == self.wide_layout:
            return
        self.wide_layout = wide
        self.splitter.setOrientation(Qt.Orientation.Horizontal if wide else Qt.Orientation.Vertical)
        if wide:     # even split
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 1)
            self.splitter.setSizes([self.width() // 2] * 2)
        else:        # live panel as compact as it goes, takes get the rest
            self.splitter.setStretchFactor(0, 0)
            self.splitter.setStretchFactor(1, 1)
            live = self.live_panel.minimumSizeHint().height()
            self.splitter.setSizes([live, max(160, self.height() - live)])

    # --- RECORDING AND IMPORT ---

    def update_record_button_style(self, recording):
        if recording:
            self.record_btn.setIcon(qta.icon('fa5s.stop', color='white'))
            self.record_btn.setStyleSheet("QPushButton { background-color: #333; border-radius: 32px; border: 2px solid #555; }")
        else:
            self.record_btn.setIcon(qta.icon('fa5s.microphone', color='white'))
            self.record_btn.setStyleSheet("QPushButton { background-color: #e74c3c; border-radius: 32px; border: 2px solid #c0392b; }")

    def show_controls(self, controls):
        self.record_btn.setEnabled(controls["record"])
        self.import_btn.setEnabled(controls["import"])
        self.reload_btn.setEnabled(controls["reload"])

    def toggle_recording(self):
        self.core.toggle_recording()

    def import_audio(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import audio", "", AUDIO_FILE_FILTER)
        if not path:
            return
        proceed, egg_channel = self.choose_egg_channel(path)
        if not proceed:
            return
        isolate = False
        if self.core.can_isolate():
            answer = QMessageBox.question(
                self, "Isolate vocals?", ISOLATE_QUESTION,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Cancel:
                return
            isolate = answer == QMessageBox.StandardButton.Yes
        self.core.import_file(path, isolate, egg_channel)

    def choose_egg_channel(self, path):
        """(proceed, channel): for a stereo file, asks whether one channel is an EGG.
        channel is 0, 1 or None (no EGG: the file is mixed to mono)."""
        if self.core.file_channels(path) != 2:
            return True, None
        box = QMessageBox(QMessageBox.Icon.Question, "EGG channel?", EGG_QUESTION, parent=self)
        buttons = [(box.addButton(text, QMessageBox.ButtonRole.AcceptRole), channel) for text, channel in
                   (("Channel 1 is EGG", 0), ("Channel 2 is EGG", 1), ("No EGG: mix to mono", None))]
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        for button, channel in buttons:
            if box.clickedButton() is button:
                return True, channel
        return False, None

    # --- TAKE CARDS ---

    def add_card(self, data, number=None, at_bottom=False):
        number = number if number is not None else self.core.number(data)
        if data["delta_f"] is not None:
            self.live_panel.plane.add_take(number, data["pitch"], data["delta_f"])
        card = TakeCard(data, number, self.profile, self.set_baseline, self.set_target,
                        on_rename=self.rename_take, on_delete=self.delete_take,
                        audio=lambda: self.core.take_audio(data), on_label=self.label_register,
                        compare_options=lambda c: self.core.compare_options(c.data),
                        compare_audio=self.core.take_audio, player=self.core.player, runner=self.core.runner)
        self.cards.append(card)
        self.scroll_layout.insertWidget(self.scroll_layout.indexOf(self.more_btn) if at_bottom else 0, card)
        return card

    def remove_card(self, card):
        self.cards.remove(card)
        self.live_panel.plane.remove_take(card.number)
        card.dispose()
        card.setParent(None)
        card.deleteLater()

    def card_for(self, take):
        return next((c for c in self.cards if c.data is take), None)

    def _take_added(self, take):
        self.add_card(take)

    def _take_updated(self, take):
        card = self.card_for(take)
        if card is None:
            return
        reanalysed = take["tracks"] is not card._tracks
        card.take_updated()
        if reanalysed:
            self.live_panel.plane.remove_take(card.number)
            if take["delta_f"] is not None:
                self.live_panel.plane.add_take(card.number, take["pitch"], take["delta_f"])

    def _take_removed(self, uid):
        for card in [c for c in self.cards if c.data.get("uid") == uid]:
            self.remove_card(card)

    def _takes_loaded(self, count, problems):
        for card in list(self.cards):
            self.remove_card(card)
        for data in reversed(self.core.shown_takes()):
            self.add_card(data)
        self.update_more_button()
        if problems:
            self.status_label.setToolTip("\n".join(problems))

    def show_older_takes(self):
        """Add the next page of older takes below the shown ones."""
        for data in self.core.show_older():
            self.add_card(data, at_bottom=True)
        self.update_more_button()

    def update_more_button(self):
        self.more_btn.setText(self.core.older_text())
        self.more_btn.setVisible(bool(self.core.older_count()))

    def rename_take(self, card, title):
        self.core.rename(card.data, title)

    def delete_take(self, card):
        self.core.delete(card.data, confirm=lambda text: ask(self, "Delete take", text))

    def label_register(self, card, label):
        self.core.label_register(card.data, label)

    def set_baseline(self, results):
        self.core.set_baseline(results)

    def set_target(self, results):
        self.core.set_target(results)

    # --- TAKES FOLDER ---

    def update_folder_button(self):
        text, tooltip = self.core.folder_label()
        self.folder_btn.setText(text)
        self.folder_btn.setToolTip(tooltip)
        self.reload_btn.setVisible(self.core.store is not None)

    def ask_for_takes_folder(self):
        """First launch: offer to pick a takes folder, once."""
        if not self.core.should_ask_takes_folder():
            return
        if ask(self, "Keep your takes?", TAKES_FOLDER_QUESTION):
            self.choose_takes_folder()
        else:
            self.core.decline_takes_folder()

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

    # --- MIC ---

    def update_egg_choice(self):
        """Offer the EGG option only if the chosen mic has two or more inputs."""
        two_inputs = self.core.egg_possible(self.mic_choice.currentData())
        self.egg_label.setVisible(two_inputs)
        self.egg_choice.setVisible(two_inputs)
        if not two_inputs:
            self._set_egg_choice(None)

    def _set_egg_choice(self, channel):
        self.egg_choice.blockSignals(True)
        self.egg_choice.setCurrentIndex(max(0, self.egg_choice.findData(channel)))
        self.egg_choice.blockSignals(False)

    def restore_egg(self):
        """At startup, once the mic is open: switch the remembered EGG channel on."""
        if self.profile.egg_channel is not None and not self.egg_choice.isHidden():
            self._set_egg_choice(self.profile.egg_channel)
            self.change_mic()

    def change_mic(self, *_):
        if self.sender() is self.mic_choice:
            self.update_egg_choice()
        channel = None if self.egg_choice.isHidden() else self.egg_choice.currentData()
        self.core.open_mic(self.mic_choice.currentData(), channel)

    def _mic_changed(self, info):
        if info["egg_channel"] is None:
            self._set_egg_choice(None)

    def mic_check(self):
        show = getattr(self.live_panel, "show_mic_check", None)
        if show is None:
            self.set_status("The mic check isn't available in this build.")
            return
        result = show()
        if isinstance(result, dict):
            self.core.save_mic_check(result)

    def first_run_mic_check(self):
        """Once, the first time VoiceLab opens a mic."""
        if self.core.should_run_first_mic_check():
            self.mic_check()

    # --- PROGRESS ---

    def show_progress(self):
        if self.progress_dialog is None:
            self.progress_dialog = ProgressDialog(self.profile, self.core.progress_entries, self)
        else:
            self.progress_dialog.refresh()
        self.progress_dialog.show()
        self.progress_dialog.raise_()

    def refresh_progress(self, *_):
        if self.progress_dialog is not None and self.progress_dialog.isVisible():
            self.progress_dialog.refresh()

    def closeEvent(self, event):
        self.core.close()
        super().closeEvent(event)
