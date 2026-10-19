import sys
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
import sounddevice as sd
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QScrollArea,
                             QFrame, QFileDialog, QMessageBox, QComboBox, QSplitter)
from PyQt6.QtCore import Qt, QSize, QTimer
import qtawesome as qta

from analysis import SAMPLE_RATE, analyze_take
from engine import AnalysisWorker, LiveEngine, device_index, input_devices, take_analysis_worker
from exercise import ExerciseView
import isolation
from live_panel import LivePanel
from progress import ProgressDialog, collect_entries
from profile_store import Profile
from speech import VowelCheck
from take_card import TakeCard
from take_store import IMPORT_MAX_SECONDS, TakeStore, load_audio, new_uid
from targets_dialog import TargetsDialog
from ui_common import BUTTON_STYLE
from voice_map import VoiceMapView

TAKES_PAGE = 20                 # take cards shown at first; older ones on request
AUDIO_FILE_FILTER = "Audio files (*.wav *.flac *.ogg *.mp3 *.aiff *.aif)"
WIDE_LAYOUT_WIDTH = 1100        # from this window width, live and takes sit side by side...
TAKES_COLUMN_WIDTH = 480        # ...if the takes still get about this much


def import_and_analyze(path, isolate):
    audio, trimmed = load_audio(path)
    if isolate:
        audio = isolation.isolate_vocals(audio, SAMPLE_RATE)
    result = analyze_take(audio)
    if "error" not in result:
        name = path.replace("\\", "/").split("/")[-1]
        notes = [name] + (["vocals isolated with Demucs"] if isolate else []) + \
                ([f"first {IMPORT_MAX_SECONDS} s"] if trimmed else [])
        result["source"] = " · ".join(notes)
    return result


def load_folder(store):
    takes, problems = store.load_all()
    return {"takes": takes, "problems": problems}


class MainWindow(QMainWindow):
    def __init__(self, engine, profile):
        super().__init__()
        self.setWindowTitle("Voice Lab")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1400, int(screen.width() * 0.9)), min(950, int(screen.height() * 0.9)))
        self.setStyleSheet("background-color: #1e1e1e;")

        self.take_count = 0
        self.is_recording = False
        self.analyzing = False
        self.engine = engine
        self.profile = profile
        self.analysis_worker = None
        self.load_worker = None
        self.cards = []
        self.older_takes = []       # loaded takes not yet shown as cards, oldest first
        self.progress_dialog = None
        self.wide_layout = None
        self.store =TakeStore(profile.takes_dir) if profile.takes_dir else None
        if profile.formant_ceiling:
            engine.analyzer.formant_ceiling = profile.formant_ceiling

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.live_panel = LivePanel(engine.analyzer.spectrum_freqs, profile, self.edit_targets)
        self.engine.frame_ready.connect(self.live_panel.update_frame)
        self.vowel_check = VowelCheck(engine, profile)
        self.vowel_check.running_changed.connect(self.update_record_enabled)
        self.exercise_view = ExerciseView(engine)
        self.exercise_view.running_changed.connect(self.update_record_enabled)
        self.exercise_view.take_ready.connect(self.process_results)
        self.vowel_check.completed.connect(self.refresh_progress)
        self.voice_map = VoiceMapView(engine, profile.voice_map_path)
        self.live_panel.add_tab(self.vowel_check, "Vowels", {"speak"})
        self.live_panel.add_tab(self.exercise_view, "Exercise", {"sing"}, default_for="sing")
        self.live_panel.add_tab(self.voice_map, "Voice Map", {"sing"})
        self.live_panel.target_provider = self.exercise_view.current_target
        self.live_panel.mode_changed.connect(profile.set_mode)
        self.live_panel.set_mode(profile.mode)

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

        self.status_label = QLabel("Ready to Record")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #888; font-size: 14px;")

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
        self.mic_choice.addItem("System default mic", None)
        for _, name in input_devices():
            self.mic_choice.addItem(name, name)
        self.mic_choice.setCurrentIndex(max(0, self.mic_choice.findData(profile.input_device)))
        self.mic_choice.currentIndexChanged.connect(self.change_mic)
        mic_label = QLabel("Mic")
        mic_label.setStyleSheet("color: #aaa; border: none;")
        self.update_folder_button()

        # One row: files on the left, record in the middle, progress and mic on the right.
        left, right = QHBoxLayout(), QHBoxLayout()
        for w in (self.import_btn, self.folder_btn, self.reload_btn):
            left.addWidget(w)
        left.addStretch()
        right.addStretch()
        for w in (progress_btn, mic_label, self.mic_choice):
            right.addWidget(w)
        button_row = QHBoxLayout()
        button_row.addLayout(left, 1)
        button_row.addWidget(self.record_btn)
        button_row.addLayout(right, 1)
        controls_layout.setContentsMargins(10, 4, 10, 8)
        controls_layout.setSpacing(4)
        controls_layout.addWidget(self.status_label)
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
        if self.store:
            self.load_takes()

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

    def update_record_button_style(self, recording):
        if recording:
            self.record_btn.setIcon(qta.icon('fa5s.stop', color='white'))
            self.record_btn.setStyleSheet("QPushButton { background-color: #333; border-radius: 32px; border: 2px solid #555; }")
        else:
            self.record_btn.setIcon(qta.icon('fa5s.microphone', color='white'))
            self.record_btn.setStyleSheet("QPushButton { background-color: #e74c3c; border-radius: 32px; border: 2px solid #c0392b; }")

    def toggle_recording(self):
        if not self.is_recording:
            self.is_recording = True
            self.status_label.setText("Recording...")
            self.status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.update_record_button_style(recording=True)
            self.engine.start_take()
            self.update_record_enabled()
        else:
            self.is_recording = False
            self.status_label.setText("Processing...")
            self.status_label.setStyleSheet("color: #f1c40f;")
            self.update_record_button_style(recording=False)
            self.analyzing = True
            self.record_btn.setEnabled(False)
            self.analysis_worker = take_analysis_worker(self.engine.stop_take())
            self.analysis_worker.finished_analysis.connect(self.process_results)
            self.analysis_worker.start()

    def update_record_enabled(self, *_):
        """The record button is unavailable while a drill or exercise records."""
        busy = self.vowel_check.running or self.exercise_view.running or self.analyzing
        self.record_btn.setEnabled(not busy)
        self.import_btn.setEnabled(not busy and not self.is_recording)

    def import_audio(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import audio", "", AUDIO_FILE_FILTER)
        if not path:
            return
        isolate = False
        if isolation.available():
            answer = QMessageBox.question(
                self, "Isolate vocals?",
                "Is there music behind the voice? Demucs can isolate the vocals first.\n"
                "This is slower, and the first use downloads its model (~80 MB).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Cancel:
                return
            isolate = answer == QMessageBox.StandardButton.Yes
        self.analyzing = True
        self.update_record_enabled()
        self.status_label.setText("Isolating vocals and analyzing…" if isolate else "Analyzing file…")
        self.status_label.setStyleSheet("color: #f1c40f;")
        self.analysis_worker = AnalysisWorker(import_and_analyze, path, isolate)
        self.analysis_worker.finished_analysis.connect(self.process_results)
        self.analysis_worker.start()

    def process_results(self, results):
        # Exercise takes arrive here too; only our own worker finishing frees
        # the record button, or a second take could replace a running worker.
        if self.sender() is self.analysis_worker:
            self.analyzing = False
            self.update_record_enabled()
        if "error" in results:
            self.status_label.setText(results["error"])
            self.status_label.setStyleSheet("color: #e74c3c;")
            return
        # Calibrate the live formant tracker to the ceiling that fit this take,
        # unless it's an imported file, which may be someone else's voice.
        if not results.get("source"):
            self.engine.analyzer.formant_ceiling = results["formant_ceiling"]
            self.profile.set_formant_ceiling(results["formant_ceiling"])
        results.update(uid=new_uid(), time=datetime.now().isoformat(timespec="seconds"),
                       mode=self.profile.mode)
        message = "Done"
        # With a takes folder, the folder is the record of every take; without
        # one, a summary goes to the history log so progress still builds up.
        if self.store:
            try:
                self.store.save(results)
                message = f"Saved {results['file']}"
            except OSError as e:
                message = f"Couldn't save the take: {e}"
                self.profile.log_take(results)
        else:
            self.profile.log_take(results)
        self.add_card(results)
        self.set_status(message)
        self.refresh_progress()

    # --- TAKE CARDS ---

    def set_status(self, text, color="#888"):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")

    def take_audio(self, data):
        return self.store.audio(data) if data.get("file") and self.store else data["audio"]

    def add_card(self, data, number=None, at_bottom=False):
        if number is None:
            self.take_count += 1
            number = self.take_count
        if data["delta_f"] is not None:
            self.live_panel.plane.add_take(number, data["pitch"], data["delta_f"])
        card = TakeCard(data, number, self.profile, self.set_baseline, self.set_target,
                        on_rename=self.rename_take, on_delete=self.delete_take,
                        audio=lambda: self.take_audio(data))
        self.cards.append(card)
        self.scroll_layout.insertWidget(self.scroll_layout.indexOf(self.more_btn) if at_bottom else 0, card)
        return card

    def remove_card(self, card):
        self.cards.remove(card)
        self.live_panel.plane.remove_take(card.number)
        card.setParent(None)
        card.deleteLater()

    def show_older_takes(self):
        """Add the next page of older takes below the shown ones."""
        page, self.older_takes = self.older_takes[-TAKES_PAGE:], self.older_takes[:-TAKES_PAGE]
        for number, data in reversed(page):
            self.add_card(data, number, at_bottom=True)
        self.update_more_button()

    def update_more_button(self):
        self.more_btn.setText(f"Show older takes ({len(self.older_takes)} more)")
        self.more_btn.setVisible(bool(self.older_takes))

    # --- TAKES FOLDER ---

    def update_folder_button(self):
        if self.store:
            self.folder_btn.setText(f" {self.store.folder.name}")
            self.folder_btn.setToolTip(f"Takes are saved in {self.store.folder}. Click to change.")
        else:
            self.folder_btn.setText(" Choose takes folder…")
            self.folder_btn.setToolTip("Pick a folder to save takes in and load them from, "
                                       "so they're kept between sessions.")
        self.reload_btn.setVisible(self.store is not None)

    def ask_for_takes_folder(self):
        """First launch: offer to pick a takes folder, once."""
        if self.store or self.profile.takes_dir_asked:
            return
        answer = QMessageBox.question(
            self, "Keep your takes?",
            "Choose a folder to save takes in? Each take is saved there as a WAV file with its "
            "analysis beside it, and loads again next time.\n\n"
            "You can choose or change the folder any time with the folder button at the bottom.")
        if answer == QMessageBox.StandardButton.Yes:
            self.choose_takes_folder()
        else:
            self.profile.set_takes_dir(None)

    def choose_takes_folder(self):
        start = str(self.store.folder) if self.store else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Takes folder", start)
        if folder:
            self.set_takes_folder(folder)

    def set_takes_folder(self, folder):
        """Use `folder` for takes. This session's unsaved takes are saved into it."""
        self.profile.set_takes_dir(folder)
        self.store = TakeStore(folder)
        unsaved = [c.data for c in self.cards if not c.data.get("file")]
        for data in sorted(unsaved, key=lambda d: d["time"]):
            try:
                self.store.save(data)
            except OSError as e:
                self.set_status(f"Couldn't save a take: {e}", "#e74c3c")
        # They're in the folder now, so the history log shouldn't count them again.
        self.profile.forget_takes(d["uid"] for d in unsaved if d.get("file"))
        self.update_folder_button()
        self.load_takes()

    def load_takes(self):
        """(Re)load every take in the folder, in the background."""
        if not self.store or (self.load_worker and self.load_worker.isRunning()):
            return
        self.set_status(f"Loading takes from {self.store.folder}…", "#f1c40f")
        self.reload_btn.setEnabled(False)
        self.load_worker = AnalysisWorker(load_folder, self.store)
        self.load_worker.finished_analysis.connect(self._takes_loaded)
        self.load_worker.start()

    def _takes_loaded(self, result):
        self.reload_btn.setEnabled(True)
        if "error" in result:
            self.set_status(result["error"], "#e74c3c")
            return
        # Keep takes that aren't in the folder, and any saved while it loaded;
        # takes whose files were deleted outside VoiceLab go.
        loaded = {d["uid"] for d in result["takes"]}
        kept = [c.data for c in self.cards if c.data.get("uid") not in loaded and
                (not c.data.get("file") or self.store.path(c.data).exists())]
        for card in list(self.cards):
            self.remove_card(card)
        takes = sorted(result["takes"] + kept, key=lambda d: d["time"])
        numbered = list(enumerate(takes, start=1))
        self.take_count = len(takes)
        self.older_takes, shown = numbered[:-TAKES_PAGE], numbered[-TAKES_PAGE:]
        for number, data in shown:
            self.add_card(data, number)
        self.update_more_button()
        message = f"Loaded {len(result['takes'])} take{'s' if len(result['takes']) != 1 else ''} " \
                  f"from {self.store.folder.name}"
        if result["problems"]:
            message += f" · {len(result['problems'])} file(s) couldn't be read"
            self.status_label.setToolTip("\n".join(result["problems"]))
        self.set_status(message, "#e67e22" if result["problems"] else "#888")
        self.refresh_progress()

    def all_takes(self):
        return [c.data for c in self.cards] + [d for _, d in self.older_takes]

    def rename_take(self, card, title):
        data = card.data
        if data.get("file") and self.store:
            try:
                self.store.rename(data, title)
            except OSError as e:
                card.title.setText(data.get("title") or "")
                self.set_status(f"Couldn't rename: {e}", "#e74c3c")
                return
            card.title.setText(data["title"])
            self.set_status(f"Renamed to {data['file']}")
        else:
            data["title"] = title
        card.update_saved_state()

    def delete_take(self, card):
        data = card.data
        name = data.get("title") or f"Take #{card.number}"
        if data.get("file"):
            text = f"Delete “{name}”? Its files ({data['file']} and its .json) are removed from the takes folder."
        else:
            text = f"Delete “{name}”? It isn't saved anywhere, so it can't be recovered."
        if QMessageBox.question(self, "Delete take", text) != QMessageBox.StandardButton.Yes:
            return
        if data.get("file") and self.store:
            try:
                self.store.delete(data)
            except OSError as e:
                self.set_status(f"Couldn't delete: {e}", "#e74c3c")
                return
        else:
            self.profile.forget_takes([data.get("uid")])
        self.remove_card(card)
        self.set_status(f"Deleted {name}")
        self.refresh_progress()

    # --- MIC ---

    def change_mic(self):
        name = self.mic_choice.currentData()
        try:
            self.engine.open_stream(device_index(name))
        except sd.PortAudioError as e:
            self.set_status(f"Couldn't open {name or 'the default mic'}: {e}", "#e74c3c")
            return
        self.profile.set_input_device(name)
        self.set_status(f"Listening on {name or 'the system default mic'}")
        self.update_record_enabled()

    # --- PROGRESS ---

    def progress_entries(self):
        folder_takes = [d for d in self.all_takes() if d.get("file")]
        return collect_entries(self.profile.read_history(), folder_takes)

    def show_progress(self):
        if self.progress_dialog is None:
            self.progress_dialog = ProgressDialog(self.profile, self.progress_entries, self)
        else:
            self.progress_dialog.refresh()
        self.progress_dialog.show()
        self.progress_dialog.raise_()

    def refresh_progress(self, *_):
        if self.progress_dialog is not None and self.progress_dialog.isVisible():
            self.progress_dialog.refresh()

    def set_baseline(self, results):
        self.profile.set_baseline(results)
        self.refresh_profile("Baseline saved")

    def set_target(self, results):
        self.profile.set_targets_from_take(results)
        self.refresh_profile("Targets set from take. Fine-tune them under Targets.")

    def edit_targets(self):
        TargetsDialog(self.profile, self).exec()
        # Refresh even on Cancel: clearing the baseline applies immediately.
        self.refresh_profile()

    def refresh_profile(self, message=None):
        self.live_panel.refresh_profile()
        self.vowel_check.refresh_profile()
        for card in self.cards:
            card.refresh_profile()
        self.refresh_progress()
        if message:
            self.set_status(message, "#2ecc71")

    def closeEvent(self, event):
        self.engine.stop()
        self.voice_map.save()
        for worker in (self.analysis_worker, self.load_worker):
            if worker is not None:
                worker.wait()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Dark throughout, so message boxes and other stock dialogs get light text.
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    pg.setConfigOptions(antialias=True)
    profile = Profile()
    engine = LiveEngine()
    window = MainWindow(engine, profile)
    window.show()
    try:
        engine.start(device_index(profile.input_device))
    except sd.PortAudioError as e:
        # Still usable without a mic: imported files can be analysed and shifted.
        window.record_btn.setEnabled(False)
        window.status_label.setText("No microphone. Import audio still works.")
        QMessageBox.warning(window, "No microphone",
                            f"Couldn't open the microphone ({e}).\n"
                            "Connect one and pick it under Mic, or restart VoiceLab.")
    QTimer.singleShot(0, window.ask_for_takes_folder)
    sys.exit(app.exec())
