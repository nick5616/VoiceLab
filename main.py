import inspect
import sys
import time
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
import sounddevice as sd
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QScrollArea,
                             QFrame, QFileDialog, QMessageBox, QComboBox, QSplitter)
from PyQt6.QtCore import Qt, QSize, QTimer
import qtawesome as qta

import analysis
import coach
import profile_store
import register
from analysis import SAMPLE_RATE, score_exercise
from egg import load_stereo_take
from engine import AnalysisWorker, LiveEngine, device_index, input_channels, input_devices
from exercise import ExerciseView
import isolation
from live_panel import LivePanel
from progress import ProgressDialog, collect_entries
from profile_store import TARGET_KEYS, Profile
from speech import VowelCheck
from take_card import TakeCard, take_date
from take_register import (analyze_recording, analyze_with_options, finish_take, fit_labelled, is_stale,
                           label_rows, register_model, score_register)
from take_store import (ANALYSIS_VERSION, IMPORT_MAX_SECONDS, TakeStore, default_mode, is_outdated, load_audio,
                        new_uid)
from targets_dialog import TargetsDialog
from ui_common import BUTTON_STYLE, METRIC_DEFS
import voice_map
from voice_map import VoiceMapView

TAKES_PAGE = 20                 # take cards shown at first; older ones on request
COMPARE_CHOICES = 15            # recent takes offered under "Compare with…"
AUDIO_FILE_FILTER = "Audio files (*.wav *.flac *.ogg *.mp3 *.aiff *.aif)"
EGG_CHOICES = [("none", None), ("channel 1", 0), ("channel 2", 1)]    # the EGG input channel
WIDE_LAYOUT_WIDTH = 1100        # from this window width, live and takes sit side by side...
TAKES_COLUMN_WIDTH = 480        # ...if the takes still get about this much
CEILING_TAKES = 3               # usable takes the profile's formant ceiling is chosen from (spec 05)
REST_NUDGE_MINUTES = 25         # first rest reminder, then every so often. Own judgement: vocal-hygiene
#                                 advice commonly says to rest the voice regularly, but I could not confirm
#                                 a specific interval (unverified)
SESSION_TICK_MS = 15000
# What a take keeps through re-analysis: who it is, not what was measured.
KEPT_KEYS = ("uid", "time", "title", "dated", "file", "source", "register_label", "egg_channel", "task")


def accepts(fn, name):
    """True if the callable takes a parameter called `name` (spec 05 and 06 add some after this was written)."""
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def import_and_analyze(path, isolate, egg_channel=None, state=None, version=0, mode="speak", ceiling=None):
    """Analyse a file in `mode`. With egg_channel (0 or 1) that channel of a stereo file is the EGG
    and the other the mic; `state` and `version` are the profile's register model."""
    if egg_channel is None:
        audio, trimmed = load_audio(path)
        egg_samples = None
    else:
        audio, egg_samples = load_stereo_take(path, egg_channel)
        limit = IMPORT_MAX_SECONDS * SAMPLE_RATE
        trimmed = len(audio) > limit
        audio, egg_samples = audio[:limit], egg_samples[:limit]
    if isolate:
        audio = isolation.isolate_vocals(audio, SAMPLE_RATE)
    result = finish_take(analyze_with_options(audio, mode, ceiling), egg_samples, state, version)
    if "error" not in result:
        name = path.replace("\\", "/").split("/")[-1]
        notes = [name] + (["vocals isolated with Demucs"] if isolate else []) + \
                ([f"first {IMPORT_MAX_SECONDS} s"] if trimmed else [])
        result["source"] = " · ".join(notes)
    return result


def load_folder(store):
    takes, problems = store.load_all()
    return {"takes": takes, "problems": problems}


def _take_audio(data, store):
    return store.audio(data) if data.get("file") and store else data["audio"]


def refit_register(takes, store, cache):
    """Refit the register model to the labelled takes. `cache` holds each take's frame
    features (uid -> rows), so a label change doesn't re-measure every take."""
    labelled = []
    for data in takes:
        if data["uid"] not in cache:
            cache[data["uid"]] = label_rows(_take_audio(data, store), data["tracks"])
        labelled.append((data["register_label"], cache[data["uid"]]))
    model, counts = fit_labelled(labelled)
    return {"job": "refit", "model": model, "counts": counts}


def reanalyze_take(data, store, state, version, ceiling=None):
    """Analyse a stored take again with the current analysis, in its own mode. Only the sidecar changes,
    never the audio file. Returns {"job": "reanalyze", "uid", "take"} or {..., "failed": message}."""
    try:
        audio = _take_audio(data, store)
        egg_samples = store.egg(data) if store and data.get("egg_channel") is not None else None
        mode = default_mode(data)
        result = finish_take(analyze_with_options(audio, mode, ceiling), egg_samples, state, version)
    except Exception as e:  # an unreadable file shouldn't stop the others
        return {"job": "reanalyze", "uid": data["uid"], "failed": str(e)}
    if "error" in result:
        return {"job": "reanalyze", "uid": data["uid"], "failed": result["error"]}
    result.pop("audio", None)
    result.pop("egg_samples", None)
    result.update({k: data[k] for k in KEPT_KEYS if k in data}, mode=mode)
    exercise = data.get("exercise")
    if exercise and exercise.get("targets"):    # score it again with the notes it was sung against
        latency = exercise.get("latency") or 0.0
        heard = [(start + latency, length, midi) for start, length, midi in exercise["targets"]]
        tracks = result["tracks"]
        result["exercise"] = score_exercise(tracks["times"], tracks["f0"], heard) | {
            "name": exercise["name"], "targets": exercise["targets"], "latency": latency}
    elif exercise:
        result["exercise"] = exercise
    return {"job": "reanalyze", "uid": data["uid"], "take": result}


def rebuild_voice_map(takes, store):
    """A Voice Map built from the saved takes, each in its own mode. Imported voices are left out."""
    items = []
    for data in takes:
        if data.get("file") and (not data.get("source") or "added to folder" in data["source"]):
            try:
                items.append({"tracks": data["tracks"], "audio": _take_audio(data, store), "mode": data.get("mode")})
            except Exception:  # a missing or unreadable file
                continue
    return {"job": "voicemap", "data": voice_map.rebuild(items), "count": len(items)}


def rescore_register(takes, store, state, version):
    """Score takes with the register model; a take whose audio can't be read is reported as failed."""
    model, scored, failed = register_model(state), [], []
    for data in takes:
        try:
            scores, fry, summary = score_register(_take_audio(data, store), data["tracks"], model)
        except Exception:  # a missing or unreadable file shouldn't stop the others
            failed.append(data["uid"])
            continue
        summary["model_version"] = version
        scored.append((data["uid"], scores, fry, summary))
    return {"job": "rescore", "scored": scored, "failed": failed}


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
        self.register_worker = None     # re-scores takes or refits the register model, one job at a time
        self.register_again = False     # something changed while it ran
        self.refit_needed = False
        self.register_failed = set()    # uids that couldn't be scored, so they aren't retried
        self.reanalysis_worker = None   # re-analyses takes made by an older analysis, one at a time
        self.reanalysis_failed = set()  # uids that couldn't be, so they aren't retried this session
        self.reanalysis_total = 0       # takes queued in this round, for the status text
        self.reanalysis_done = 0
        self.voicemap_worker = None
        self.ceiling_candidates = []    # usable takes the profile's formant ceiling is chosen from
        self.session_started = None     # clock() when you first recorded; None until then
        self.session_clock = time.monotonic
        self.rest_nudges = 0
        self.label_features = {}        # uid -> frame features of labelled takes
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
        self.live_panel.window_provider = engine.latest_window
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
        self.voice_map.rebuild_requested.connect(self.rebuild_voice_map)
        self.vowel_check.completed.connect(self.use_vowel_ceiling)
        self.live_panel.mode_changed.connect(self.change_mode)
        self.live_panel.set_mode(profile.mode)
        self.change_mode(profile.mode)
        self.apply_targets()

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
        self.mic_choice.addItem("System default mic", None)
        for _, name in input_devices():
            self.mic_choice.addItem(name, name)
        self.mic_choice.setCurrentIndex(max(0, self.mic_choice.findData(profile.input_device)))
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
        if self.store:
            self.load_takes()

    # --- ENGINE, MODE AND TARGETS (calls into spec 05 and 06 are guarded until they merge) ---

    def change_mode(self, mode):
        """The mode picked on the live panel: remembered, and told to the engine and the Voice Map."""
        self.profile.set_mode(mode)
        self.voice_map.mode = mode
        if hasattr(self.engine, "set_mode"):
            self.engine.set_mode(mode)

    def apply_targets(self):
        if hasattr(self.live_panel, "set_targets"):
            self.live_panel.set_targets(self.profile.targets)

    def stream_options(self):
        """Keyword arguments for engine.open_stream: the noise floor from last time, if it takes one."""
        if self.profile.noise_floor is not None and accepts(self.engine.open_stream, "noise_floor"):
            return {"noise_floor": self.profile.noise_floor}
        return {}

    def mic_check(self):
        show = getattr(self.live_panel, "show_mic_check", None)
        if show is None:
            self.set_status("The mic check isn't available in this build.")
            return
        result = show()
        if isinstance(result, dict):
            self.profile.set_mic_check(result)
            self.set_status("Mic check saved")

    def first_run_mic_check(self):
        """Once, the first time VoiceLab opens a mic."""
        if self.profile.mic_check is None and not self.profile.mic_check_asked:
            self.profile.set_mic_check_asked()
            self.mic_check()

    def use_vowel_ceiling(self, result):
        """The vowel check fits the formant ceiling jointly over eight vowels, the best evidence there is."""
        if result.get("formant_ceiling"):
            self.set_ceiling(result["formant_ceiling"])

    def set_ceiling(self, ceiling):
        self.engine.analyzer.formant_ceiling = ceiling
        self.profile.set_formant_ceiling(ceiling)

    def note_ceiling(self, results):
        """Fix the profile's formant ceiling once, from the first few usable takes (analysis.
        choose_profile_ceiling, spec 05), rather than following every take's own: that jumped by
        150 Hz between takes 13 s apart (audit). Until that exists, the first usable take's."""
        if self.profile.formant_ceiling or not results.get("formant_ceiling") or results.get("source"):
            return
        if coach.take_quality(results, results.get("noise"))["tier"] == "poor":
            return
        self.ceiling_candidates.append(results)
        choose = getattr(analysis, "choose_profile_ceiling", None)
        if choose is None:
            self.set_ceiling(results["formant_ceiling"])
        elif len(self.ceiling_candidates) >= CEILING_TAKES:
            self.set_ceiling(choose(self.ceiling_candidates))

    # --- SESSION ---

    def start_session(self):
        if self.session_started is None:
            self.session_started = self.session_clock()
            self.session_label.show()
            self.session_timer.start()
            self.update_session()

    def session_minutes(self):
        return 0.0 if self.session_started is None else (self.session_clock() - self.session_started) / 60

    def update_session(self):
        minutes = self.session_minutes()
        self.session_label.setText(f"Session {minutes:.0f} min")
        if minutes >= REST_NUDGE_MINUTES * (self.rest_nudges + 1):
            self.rest_nudges += 1
            self.rest_label.setText(f"You have been at this for {minutes:.0f} minutes. A short rest and a sip "
                                    "of water can help; ease off if your voice feels tired or tight.")
            self.rest_label.setToolTip("Vocal-hygiene guidance generally suggests resting the voice regularly. "
                                       "The 25-minute interval is a rough guide (unverified).")
            self.rest_label.show()

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
            self.start_session()
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
            audio = self.engine.stop_take()
            self.analysis_worker = AnalysisWorker(analyze_recording, audio, self.engine.last_take_egg,
                                                  self.profile.register_model, self.profile.register_version,
                                                  self.profile.mode, self.profile.formant_ceiling)
            self.analysis_worker.finished_analysis.connect(self.process_results)
            self.analysis_worker.start()

    def update_record_enabled(self, *_):
        """The record button is unavailable while a drill or exercise records."""
        busy = self.vowel_check.running or self.exercise_view.running or self.analyzing
        if self.vowel_check.running or self.exercise_view.running:
            self.start_session()
        self.record_btn.setEnabled(not busy)
        self.import_btn.setEnabled(not busy and not self.is_recording)

    def import_audio(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import audio", "", AUDIO_FILE_FILTER)
        if not path:
            return
        proceed, egg_channel = self.choose_egg_channel(path)
        if not proceed:
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
        self.analysis_worker = AnalysisWorker(import_and_analyze, path, isolate, egg_channel,
                                              self.profile.register_model, self.profile.register_version,
                                              self.profile.mode, self.profile.formant_ceiling)
        self.analysis_worker.finished_analysis.connect(self.process_results)
        self.analysis_worker.start()

    def choose_egg_channel(self, path):
        """(proceed, channel): for a stereo file, asks whether one channel is an EGG.
        channel is 0, 1 or None (no EGG: the file is mixed to mono)."""
        import soundfile as sf
        try:
            channels = sf.info(path).channels
        except Exception:  # unreadable here; the analysis reports the problem
            return True, None
        if channels != 2:
            return True, None
        box = QMessageBox(QMessageBox.Icon.Question, "EGG channel?",
                          "This file has two channels. Is one of them an electroglottograph (EGG) "
                          "signal, with the voice on the other?", parent=self)
        buttons = [(box.addButton(text, QMessageBox.ButtonRole.AcceptRole), channel) for text, channel in
                   (("Channel 1 is EGG", 0), ("Channel 2 is EGG", 1), ("No EGG: mix to mono", None))]
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        for button, channel in buttons:
            if box.clickedButton() is button:
                return True, channel
        return False, None

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
        # An imported file may be someone else's voice, so it never sets the formant ceiling.
        self.note_ceiling(results)
        results.update(uid=new_uid(), time=datetime.now().isoformat(timespec="seconds"))
        results.setdefault("mode", self.profile.mode)
        results.setdefault("analysis_version", ANALYSIS_VERSION)
        results.setdefault("task", self.current_task(results))
        message, color = "Done", "#888"
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
        egg_info = results.get("egg")
        if egg_info and not egg_info["signal_ok"]:      # say why there's no contact quotient
            message, color = f"{message}. EGG: {egg_info['note']}", "#e67e22"
        self.set_status(message, color)
        self.refresh_progress()
        self.update_register()          # exercise takes arrive without a register result

    def current_task(self, results):
        """What this take is for, so progress compares like with like: the passage being read,
        else the exercise (profile_store.task_of), else free speech or singing."""
        prompt = getattr(self.live_panel, "reading_prompt", None)
        mode = results.get("mode") or self.profile.mode
        if mode == "speak" and not results.get("exercise") and prompt is not None and prompt.isVisible():
            return f"passage: {prompt.title.text().split('·')[-1].strip()}"
        return profile_store.task_of(results | {"mode": mode})

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
                        audio=lambda: self.take_audio(data), on_label=self.label_register,
                        compare_options=self.compare_options, compare_audio=self.take_audio)
        self.cards.append(card)
        self.scroll_layout.insertWidget(self.scroll_layout.indexOf(self.more_btn) if at_bottom else 0, card)
        return card

    def compare_options(self, card):
        """[(label, take)] a card can be compared with: your baseline take first, then recent takes."""
        others = [d for d in self.all_takes() if d is not card.data and d.get("tracks")]
        others.sort(key=lambda d: d["time"], reverse=True)
        options, base_uid = [], (self.profile.baseline or {}).get("uid")
        for data in others:
            if data.get("uid") and data["uid"] == base_uid:
                options.append(("Baseline take", data))
        for data in others[:COMPARE_CHOICES]:
            options.append((data.get("title") or take_date(data) or "Take", data))
        return options

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
        self.update_register()

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
        self.update_register()
        self.update_reanalysis()

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

    # --- REGISTER ---

    def label_register(self, card, label):
        """Label a take chest or head (None clears it); labelled takes calibrate the model."""
        data = card.data
        if label:
            data["register_label"] = label
        else:
            data.pop("register_label", None)
        if data.get("file") and self.store:
            try:
                self.store.write_sidecar(data)
            except OSError as e:
                self.set_status(f"Couldn't save the label: {e}", "#e74c3c")
        card.refresh_register()
        self.set_status(f"Labelled as {label}" if label else "Label cleared")
        self.refit_needed = True
        self.update_register()

    def update_register(self):
        """Run the next background register job: refit after a label change, then score the
        shown cards that lack a result from the current model. One job at a time."""
        if self.register_worker is not None and self.register_worker.isRunning():
            self.register_again = True
            return
        if self.reanalysis_active():     # its results carry fresh register scores; update_reanalysis calls back
            return
        self.register_again = False
        job = None
        if self.refit_needed:
            self.refit_needed = False
            labelled = [d for d in self.all_takes() if d.get("register_label") and d.get("uid")]
            if labelled:
                job = AnalysisWorker(refit_register, labelled, self.store, self.label_features)
        if job is None:
            version = self.profile.register_version
            stale = [c.data for c in self.cards if c.data.get("uid") not in self.register_failed
                     and is_stale(c.data, version)]
            if stale:
                job = AnalysisWorker(rescore_register, stale, self.store, self.profile.register_model, version)
        if job is None:
            return
        self.register_worker = job
        job.finished_analysis.connect(self._register_job_done)
        job.start()

    def _register_job_done(self, result):
        if "error" in result:
            self.set_status(result["error"], "#e74c3c")
            return
        if result["job"] == "refit":
            self._apply_refit(result)
        else:
            self._apply_scores(result)
        self.update_register()

    def _apply_refit(self, result):
        counts = ", ".join(f"{n} {name}" for name, n in result["counts"].items())
        if result["model"] is None:
            need = register.MIN_CALIBRATION_FRAMES
            self.set_status(f"Register labels so far: {counts} frames. Calibration needs {need} of each "
                            "class (label a few more takes, or longer ones).")
            return
        first = not register_model(self.profile.register_model).is_calibrated()
        self.profile.set_register_model(result["model"])
        self.live_panel.refresh_profile()
        self.set_status("Register calibrated to your voice (" + counts + " labelled frames)" if first else
                        "Register model updated from your labels (" + counts + " frames)", "#2ecc71")

    def _apply_scores(self, result):
        by_uid = {c.data.get("uid"): c for c in self.cards}
        self.register_failed.update(result["failed"])
        for uid, scores, fry, summary in result["scored"]:
            card = by_uid.get(uid)
            if card is None:
                continue
            data = card.data
            data["tracks"]["register"], data["tracks"]["fry"] = scores, fry.astype(float)
            data["register"] = summary
            if data.get("file") and self.store:
                try:
                    self.store.write_sidecar(data)
                except OSError:
                    pass
            card.refresh_register()
        self.refresh_progress()

    # --- RE-ANALYSIS ---

    def outdated_takes(self):
        """Saved takes made by an older analysis, newest first. (Unsaved ones were just made.)"""
        stale = [d for d in self.all_takes() if d.get("file") and d.get("uid") not in self.reanalysis_failed
                 and is_outdated(d)]
        return sorted(stale, key=lambda d: d["time"], reverse=True)

    def reanalysis_active(self):
        return (self.reanalysis_worker is not None and self.reanalysis_worker.isRunning()) \
            or bool(self.outdated_takes() and self.store)

    def update_reanalysis(self):
        """Re-analyse the next take made by an older analysis, in the background, one at a time. The WAV is
        never touched: only the sidecar is rewritten. The register jobs wait until this is done."""
        if not self.store or (self.reanalysis_worker is not None and self.reanalysis_worker.isRunning()):
            return
        stale = self.outdated_takes()
        if not stale:
            if self.reanalysis_total:
                self.set_status(f"Re-analysed {self.reanalysis_done} of your takes with the improved analysis. "
                                "Use “Rebuild from takes” on the Voice Map tab to clear old noise readings from it.",
                                "#2ecc71")
                self.reanalysis_total = self.reanalysis_done = 0
            self.update_register()
            return
        self.reanalysis_total = max(self.reanalysis_total, self.reanalysis_done + len(stale))
        self.set_status(f"Re-analysing your takes with the improved analysis: {self.reanalysis_done + 1} "
                        f"of {self.reanalysis_total}…", "#f1c40f")
        job = AnalysisWorker(reanalyze_take, stale[0], self.store, self.profile.register_model,
                             self.profile.register_version, self.profile.formant_ceiling)
        self.reanalysis_worker = job
        job.finished_analysis.connect(self._reanalysis_done)
        job.start()

    def _reanalysis_done(self, result):
        uid = result.get("uid")
        if "error" in result or result.get("failed"):
            self.reanalysis_failed.add(uid)
            self.set_status(result.get("failed") or result["error"], "#e67e22")
        else:
            self._apply_reanalysis(result)
            self.reanalysis_done += 1
        self.update_reanalysis()

    def _apply_reanalysis(self, result):
        """Swap a take's analysis for the new one in place, save the sidecar, and redraw its card."""
        data = next((d for d in self.all_takes() if d.get("uid") == result["uid"]), None)
        if data is None:               # deleted while it ran
            return
        audio = data.get("audio")
        data.clear()
        data.update(result["take"])
        if audio is not None:
            data["audio"] = audio
        try:
            self.store.write_sidecar(data)
        except OSError as e:
            self.set_status(f"Couldn't save the new analysis: {e}", "#e74c3c")
        card = next((c for c in self.cards if c.data is data), None)
        if card is not None:
            self.live_panel.plane.remove_take(card.number)
            if data["delta_f"] is not None:
                self.live_panel.plane.add_take(card.number, data["pitch"], data["delta_f"])
            card.reload()
        self.refresh_progress()

    def rebuild_voice_map(self):
        """Replace the Voice Map with one built from the saved takes, which holds none of the noise
        the old live frames did."""
        if not self.store:
            self.set_status("Choose a takes folder first: the Voice Map is rebuilt from saved takes.", "#e67e22")
            return
        if self.reanalysis_active() or (self.voicemap_worker and self.voicemap_worker.isRunning()):
            self.set_status("Wait until your takes are re-analysed, then rebuild the Voice Map.", "#e67e22")
            return
        answer = QMessageBox.question(
            self, "Rebuild Voice Map",
            "Replace the Voice Map with one built from your saved takes? What it has gathered live is "
            "replaced, and imported voices are left out.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.set_status("Rebuilding the Voice Map…", "#f1c40f")
        self.voicemap_worker = AnalysisWorker(rebuild_voice_map, [d for d in self.all_takes()], self.store)
        self.voicemap_worker.finished_analysis.connect(self._voice_map_rebuilt)
        self.voicemap_worker.start()

    def _voice_map_rebuilt(self, result):
        if "error" in result:
            self.set_status(result["error"], "#e74c3c")
            return
        self.voice_map.use(result["data"])
        self.set_status(f"Voice Map rebuilt from {result['count']} take{'s' if result['count'] != 1 else ''}",
                        "#2ecc71")

    # --- MIC ---

    def update_egg_choice(self):
        """Offer the EGG option only if the chosen mic has two or more inputs."""
        name = self.mic_choice.currentData()
        two_inputs = input_channels(device_index(name)) >= 2
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
        name = self.mic_choice.currentData()
        if self.sender() is self.mic_choice:
            self.update_egg_choice()
        channel = None if self.egg_choice.isHidden() else self.egg_choice.currentData()
        try:
            if channel is None:
                self.engine.open_stream(device_index(name), **self.stream_options())
            else:
                self.engine.open_stream(device_index(name), channel, **self.stream_options())
        except (sd.PortAudioError, ValueError) as e:
            if channel is None:
                self.set_status(f"Couldn't open {name or 'the default mic'}: {e}", "#e74c3c")
                return
            self.revert_egg(name, f"Couldn't open the EGG on channel {channel + 1} of "
                                  f"{name or 'the default mic'}: {e}")
            return
        self.profile.set_input_device(name)
        self.profile.set_egg_channel(channel)
        self.live_panel.set_egg(channel is not None)
        self.set_status(f"Listening on {name or 'the system default mic'}" +
                        ("" if channel is None else f", EGG on channel {channel + 1}"))
        self.update_record_enabled()

    def revert_egg(self, name, message):
        """The two-channel stream wouldn't open: show why and listen to the mic alone again."""
        self._set_egg_choice(None)
        self.profile.set_egg_channel(None)
        self.live_panel.set_egg(False)
        try:
            self.engine.open_stream(device_index(name), **self.stream_options())
        except sd.PortAudioError as e:
            message += f" (and the mic alone won't open either: {e})"
        else:
            self.profile.set_input_device(name)
            message += ". Listening without the EGG."
        self.set_status(message, "#e74c3c")

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
        """Targets around this take, as wide as its own frames spread. A reading the recording can't
        support (coach.take_quality) never becomes a target: that target keeps its old value."""
        quality = coach.take_quality(results, results.get("noise"))
        trusted = {key for key in TARGET_KEYS if quality["trust"].get(key, True)}
        if not trusted:
            self.set_status(f"This take is too noisy to set targets from ({quality['reasons'][0]}). "
                            "Record a cleaner one.", "#e67e22")
            return
        self.profile.set_targets_from_take(results, trusted)
        left = [METRIC_DEFS[k][0] for k in TARGET_KEYS if k not in trusted and results.get(k) is not None]
        message = "Targets set from take. Fine-tune them under Targets."
        if left:
            message += f" Not set from this take (it can't support them): {', '.join(left)}."
        self.refresh_profile(message)

    def edit_targets(self):
        TargetsDialog(self.profile, self).exec()
        # Refresh even on Cancel: clearing the baseline applies immediately.
        self.refresh_profile()

    def refresh_profile(self, message=None):
        self.apply_targets()
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
        floor = getattr(self.engine, "noise_floor", None)
        if callable(floor):     # spec 02: the floor learned this session starts the next one
            self.profile.set_noise_floor(floor())
        for worker in (self.analysis_worker, self.load_worker, self.register_worker, self.reanalysis_worker,
                       self.voicemap_worker):
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
        start_options = {"noise_floor": profile.noise_floor} if \
            profile.noise_floor is not None and accepts(engine.start, "noise_floor") else {}
        engine.start(device_index(profile.input_device), **start_options)
    except sd.PortAudioError as e:
        # Still usable without a mic: imported files can be analysed and shifted.
        window.record_btn.setEnabled(False)
        window.status_label.setText("No microphone. Import audio still works.")
        QMessageBox.warning(window, "No microphone",
                            f"Couldn't open the microphone ({e}).\n"
                            "Connect one and pick it under Mic, or restart VoiceLab.")
    else:
        window.restore_egg()
        QTimer.singleShot(0, window.first_run_mic_check)
    QTimer.singleShot(0, window.ask_for_takes_folder)
    sys.exit(app.exec())
