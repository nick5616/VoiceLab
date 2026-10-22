"""The live panel: mode toggle, pitch and spectrum views, meters and noise badges."""
import time
from collections import deque

import numpy as np
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame, QTabWidget,
                             QButtonGroup)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
import qtawesome as qta

import coach
import denoise
import register
from analysis import (IN_TUNE_CENTS,
    NOTE_NAMES,
    VOICE_BAND_HZ,
    SAMPLE_RATE,
    NoiseMonitor,
    hz_to_midi,
    pitch_range_st,
    PERCEIVED_SECONDS,
    perceived_cents,
    vibrato_stats)
from engine import UPDATE_INTERVAL
from mic_check import MicCheckDialog
from take_register import register_model
from exercise import midi_name
from live_views import PitchView, SpectrumView, PitchResonancePlane, pitch_label
from speech import ReadingPrompt
from widgets import ElidedLabel, ResponsiveGrid
from ui_common import (BOTH_MODES,
    BUTTON_STYLE,
    METER_MIN_WIDTH,
    REGISTER_COLORS,
    MetricBar,
    MIX_BAND_COLOR,
    MIX_EDGE_COLOR,
    OFF_TARGET_COLOR,
    ON_TARGET_COLOR,
    VerdictTracker,
    fix_text_height)

# Live meters in display order, with the modes they appear in.
# Ring is a singing measure (it rises with the singer's formant); it means nothing for speech.
LIVE_METERS = [
    ("pitch", {"speak"}), ("tuning", {"sing"}), ("vibrato", {"sing"}), ("delta_f", {"speak"}),
    ("h1h2", BOTH_MODES), ("cpp", BOTH_MODES), ("ring", {"sing"}), ("register", BOTH_MODES),
    ("cq", BOTH_MODES), ("pitch_range", {"speak"}), ("level", BOTH_MODES),
]
VERDICT_KEYS = ["pitch", "delta_f", "h1h2", "cpp", "ring"]   # judged over 1.5 s of voice, not per frame
EGG_METERS = {"cq"}             # shown only while an EGG is configured
SMOOTHED_KEYS = ["delta_f", "h1h2", "cpp", "ring"]   # per-frame metrics, smoothed for display

INTONATION_WINDOW_SECONDS = 6
VIBRATO_WINDOW_SECONDS = 1.2
NOISE_WINDOW_SECONDS = 10

NOISE_BADGE_EVERY = 10          # live frames between SNR badge updates
NOISE_REPORT_EVERY = 120        # live frames between noise reports (3 s); each reads the last few seconds
NOISE_REPORT_SECONDS = 5        # audio a noise report reads: needs pauses (>= 0.9 s) and some voice
NOISE_REPORT_MIN_SECONDS = 2.0
COACH_EVERY = 80                # live frames between coach line updates (2 s, spec 06)
COACH_WINDOW_SECONDS = 6        # the coach line reads this much recent voice
COACH_MIN_VOICED_FRAMES = 40    # 1 s of voice before advising
TUNING_LEAVE_CENTS = IN_TUNE_CENTS + 5   # hysteresis: tuning stays "in tune" a little past the edge
FRESH_VERDICT_FRAMES = 20       # a verdict counts for the note readout only while voice is this recent
CLIP_REPORT_FRACTION = 1e-4     # share of clipped samples in a noise report that counts as clipping
H1H2_ROUGH_HZ = coach.FORMANT_MAX_F0     # H1*-H2* is rough above this f0 (F1 meets H1; audit)
LIVE_TEXT_SECONDS = 0.2         # live numbers change at most this often, so they can be read
NOTE_WINDOW_SECONDS = 0.25      # the note readout shows the median pitch over this
NOTE_HOLD_SECONDS = 0.5         # ...and keeps the last note this long through gaps
CLIP_BADGE_SECONDS = 1.0        # the clipping badge stays up this long after a clip
EGG_SIGNAL_SECONDS = 1.5        # the EGG badge stays green this long after the last contact quotient
FRY_CHECK_FRAMES = 12           # recent pitch frames judged for vocal fry

NOISE_BADGE_WIDTH = 150
NOISE_BADGE_STYLE = ("QPushButton {{ color: {color}; font-size: 11px; font-weight: bold; border: none; "
                     "text-align: right; padding: 0px; }}")
COACH_IDLE = "Speak or sing for a moment and I'll suggest what to try."

LIVE_VIEW_HEIGHT = 220

# Exponential smoothing for live meters (~200 ms time constant).
METER_SMOOTHING = 1 - np.exp(-UPDATE_INTERVAL / 0.2)


class LivePanel(QFrame):
    """Tabbed live views and meters fed by the LiveEngine, in Speak or Sing mode."""

    mode_changed = pyqtSignal(str)
    mic_check_done = pyqtSignal(dict)       # the mic check's result dict (mic_check.analyze_check)

    def __init__(self, spectrum_freqs, profile, on_edit_targets):
        super().__init__()
        self.profile = profile
        self.mode = None
        # Returns the exercise's current target note (MIDI) or None.
        self.target_provider = lambda: None
        self.setStyleSheet("QFrame { background-color: #2b2b2b; border-radius: 10px; } "
                           "QLabel { color: #ffffff; }")
        layout = QVBoxLayout(self)

        toggle_style = (f"QPushButton {{ {BUTTON_STYLE} }} "
                        "QPushButton:checked { background-color: #3498db; }")
        header = QHBoxLayout()
        self.mode_buttons = {}
        mode_group = QButtonGroup(self)
        for mode, label, icon in (("speak", "Speak", "fa5s.comment"), ("sing", "Sing", "fa5s.music")):
            btn = QPushButton(qta.icon(icon, color="white"), f" {label}")
            btn.setCheckable(True)
            btn.setStyleSheet(toggle_style)
            btn.clicked.connect(lambda _, m=mode: self.set_mode(m, user=True))
            mode_group.addButton(btn)
            self.mode_buttons[mode] = btn
            header.addWidget(btn)
        targets_btn = QPushButton(qta.icon("fa5s.bullseye", color="white"), " Targets")
        targets_btn.setStyleSheet(BUTTON_STYLE)
        targets_btn.clicked.connect(on_edit_targets)
        self.read_btn = QPushButton(qta.icon("fa5s.book-open", color="white"), " Read aloud")
        self.read_btn.setCheckable(True)
        self.read_btn.setStyleSheet(toggle_style)
        # Header readouts have fixed widths: text changing size would resize the
        # panel, and with it everything beside it.
        self.note_label = QLabel("—")
        self.note_label.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        self.note_label.setFixedWidth(QFontMetrics(self.note_label.font()).horizontalAdvance(
            "C#5 -50¢ · 1200 Hz") + 6)
        self.note_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        fix_text_height(self.note_label, "C#5 -50¢ · 1200 Hz —")
        self.note_shown_at = 0.0
        self.last_voiced_at = 0.0
        # The noise badge: the main input problem found in the last few seconds, or the SNR.
        # Clicking it opens the mic check.
        self.noise_badge = QPushButton("SNR —")
        self.noise_badge.setFlat(True)
        self.noise_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.noise_badge.setFixedWidth(NOISE_BADGE_WIDTH)
        self.noise_badge.setStyleSheet(NOISE_BADGE_STYLE.format(color="#aaa"))   # styled from the start: no resize
        self.noise_badge.clicked.connect(self.show_mic_check)
        self.path_badge = QLabel("slow audio path")
        self.path_badge.setStyleSheet(f"color: {OFF_TARGET_COLOR}; font-size: 11px; font-weight: bold;")
        self.path_badge.setToolTip("The mic is read through an older Windows audio path (MME or similar) "
                                   "with extra delay. Choose the mic again, or close other audio apps.")
        self.path_badge.hide()
        self.noise = NoiseMonitor(int(NOISE_WINDOW_SECONDS / UPDATE_INTERVAL))
        self.noise_info = {}            # the last denoise.noise_report, {} before the first
        self.quality = coach.take_quality({})
        self.live_level = None
        self.snr = None
        self._engine = None
        self.mic_dialog = None
        self.last_mic_check = None
        self.frames_seen = 0
        self.clip_badge = QLabel("CLIPPING")
        self.clip_badge.setStyleSheet("color: white; background-color: #e74c3c; font-size: 11px; "
                                      "font-weight: bold; padding: 1px 5px; border-radius: 3px;")
        self.clip_badge.setToolTip("The input is overloading. Lower the mic gain or move back a little: "
                                   "clipping adds harshness that inflates Ring and lowers Clarity.")
        policy = self.clip_badge.sizePolicy()
        policy.setRetainSizeWhenHidden(True)    # appearing doesn't shift the header
        self.clip_badge.setSizePolicy(policy)
        self.clip_badge.hide()
        self.clip_frames_left = 0
        self.egg_badge = QLabel("EGG ●")
        self.egg_badge.setFixedWidth(52)
        self.egg_on = False
        self.egg_frame = -10 ** 9        # frame count when a contact quotient last came in
        self.egg_badge.hide()
        header.addSpacing(8)
        header.addWidget(targets_btn)
        header.addWidget(self.read_btn)
        header.addStretch()
        header.addWidget(self.clip_badge)
        header.addWidget(self.egg_badge)
        header.addWidget(self.note_label)
        layout.addLayout(header)

        # One live coaching line (coach.advice on the last few seconds), and the input badges.
        status = QHBoxLayout()
        self.coach_label = ElidedLabel(COACH_IDLE)
        self.coach_label.setStyleSheet("color: #ddd; font-size: 12px;")
        fix_text_height(self.coach_label, "Hz ≈ ✓ ¢ ± — ·")
        status.addWidget(self.coach_label, 1)
        status.addWidget(self.path_badge)
        status.addWidget(self.noise_badge)
        self.noise_badge.setFixedHeight(self.coach_label.height())
        layout.addLayout(status)

        self.reading_prompt = ReadingPrompt()
        self.reading_prompt.hide()
        self.read_btn.toggled.connect(self.reading_prompt.setVisible)
        layout.addWidget(self.reading_prompt)

        self.pitch_view = PitchView()
        self.spectrum_view = SpectrumView(spectrum_freqs)
        self.plane = PitchResonancePlane()
        self.tabs = QTabWidget()
        # Grows when the window gives the live panel more room (side-by-side layout).
        self.tabs.setMinimumHeight(LIVE_VIEW_HEIGHT + 30)
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: none; }
            QTabBar::tab { background: #333; color: #aaa; padding: 5px 14px; border-radius: 4px; margin-right: 4px; }
            QTabBar::tab:selected { background: #3498db; color: white; }
        """)
        self.tab_modes = []
        self.default_tab = {}
        self.add_tab(self.pitch_view, "Pitch", BOTH_MODES, default_for="speak")
        self.add_tab(self.spectrum_view, "Spectrum", BOTH_MODES)
        self.add_tab(self.plane, "Pitch × Resonance", {"speak"})
        layout.addWidget(self.tabs, 1)

        self.meters = {}
        self.meter_grid = ResponsiveGrid(METER_MIN_WIDTH)
        for key, modes in LIVE_METERS:
            meter = MetricBar(key, centered=(key == "tuning"), live=True, tracked=key not in ("register", "tuning"))
            meter.modes = modes
            self.meters[key] = meter
            self.meter_grid.addWidget(meter)
        layout.addWidget(self.meter_grid)
        self.meters["pitch_range"].label = f"Intonation Range (last {INTONATION_WINDOW_SECONDS} s)"
        self.meters["level"].label = "Input Level"
        # In-tune zone: ±IN_TUNE_CENTS on a ±50 cent bar.
        self.meters["tuning"].bar.band = (0.5 - IN_TUNE_CENTS / 100, 0.5 + IN_TUNE_CENTS / 100)
        reg = self.meters["register"]
        reg.parens = False
        reg.bar.band = register.MIX_BAND         # the mix zone, shown on the bar
        reg.bar.band_color = (MIX_BAND_COLOR, MIX_EDGE_COLOR)
        reg.color_fn = lambda position: (REGISTER_COLORS["chest"] if position < register.MIX_BAND[0]
                                         else REGISTER_COLORS["head"] if position > register.MIX_BAND[1]
                                         else REGISTER_COLORS["mix"])
        # Where the live meter gets the audio window for features the frame doesn't carry.
        self.window_provider = None
        self.register_model = register_model(profile.register_model)
        self.smoothed = {key: np.nan for key in SMOOTHED_KEYS + ["register", "cq"]}
        self.smoothed_log_f0 = np.nan
        self.recent_f0 = deque(maxlen=int(INTONATION_WINDOW_SECONDS / UPDATE_INTERVAL))
        # Absolute pitch in cents (100 x MIDI) of the last PERCEIVED_SECONDS, for tuning.
        self.recent_cents = deque(maxlen=int(round(PERCEIVED_SECONDS / UPDATE_INTERVAL)))
        # Per-frame values of the last few seconds for the coach line: (f0, delta_f, h1h2, cpp, ring, level).
        self.recent = deque(maxlen=int(COACH_WINDOW_SECONDS / UPDATE_INTERVAL))
        self.verdicts = {key: VerdictTracker() for key in VERDICT_KEYS}
        self.tuning_ok = None
        self.coach_frame = -10 ** 9
        self.refresh_profile()

    def add_tab(self, widget, name, modes, default_for=None):
        self.tabs.addTab(widget, name)
        self.tab_modes.append((widget, modes))
        if default_for:
            self.default_tab[default_for] = widget
        if self.mode:
            self.tabs.setTabVisible(self.tabs.indexOf(widget), self.mode in modes)

    def set_mode(self, mode, user=False):
        self.mode = mode
        self.mode_buttons[mode].setChecked(True)
        for widget, modes in self.tab_modes:
            self.tabs.setTabVisible(self.tabs.indexOf(widget), mode in modes)
        for key, meter in self.meters.items():
            meter.setVisible(mode in meter.modes and (self.egg_on or key not in EGG_METERS))
            meter.set_mode(mode)
        self.pitch_view.set_mode(mode)
        engine = self.engine
        if engine is not None:
            engine.set_mode(mode)
        self.recent.clear()                  # the last few seconds were the other mode's
        self.recent_cents.clear()
        self.coach_frame = -10 ** 9
        self.refresh_profile()
        self.meter_grid.relayout()
        self.read_btn.setVisible(mode == "speak")
        if mode != "speak":
            self.read_btn.setChecked(False)
        if mode in self.default_tab:
            self.tabs.setCurrentWidget(self.default_tab[mode])
        if user:
            self.mode_changed.emit(mode)

    def set_egg(self, on):
        """Show the contact quotient meter and EGG badge while an EGG is configured."""
        self.egg_on = bool(on)
        self.egg_frame = -10 ** 9
        self.egg_badge.setVisible(self.egg_on)
        self._update_egg_badge()
        self.smoothed["cq"] = np.nan
        if self.mode:
            self.set_mode(self.mode)

    @property
    def engine(self):
        """The LiveEngine feeding this panel (for the mic check and the noise report), or None.
        main.py can set it; failing that it is found through window_provider."""
        if self._engine is not None:
            return self._engine
        owner = getattr(self.window_provider, "__self__", None)
        return owner if hasattr(owner, "start_take") else None

    @engine.setter
    def engine(self, engine):
        self._engine = engine

    def set_targets(self, targets):
        """Use these targets ({key: [low, high]}) for the live verdicts. The profile is not saved."""
        self.profile.targets = {key: list(value) for key, value in (targets or {}).items()}
        self.refresh_profile()

    def reset_verdicts(self):
        for tracker in self.verdicts.values():
            tracker.reset()
        self.tuning_ok = None

    def refresh_profile(self):
        self.reset_verdicts()
        p = self.profile
        self.register_model = register_model(p.register_model)
        self.meters["register"].label = "Register" if self.register_model.is_calibrated() \
            else "Register (estimate)"
        for key, meter in self.meters.items():
            if key not in ("tuning", "vibrato"):   # fixed scales, no profile targets
                meter.set_reference(p)
        self.pitch_view.set_reference(p.baseline_value("pitch"), p.target("pitch"))
        baseline = (p.baseline_value("pitch"), p.baseline_value("delta_f")) if p.baseline else None
        self.plane.set_reference(baseline, p.target("pitch"), p.target("delta_f"))

    @staticmethod
    def _smooth(prev, value):
        return value if np.isnan(prev) else prev + METER_SMOOTHING * (value - prev)

    def update_frame(self, frame):
        f0 = frame["f0"]
        self.pitch_view.update_frame(frame)
        self.spectrum_view.update_frame(frame)
        self.meters["level"].set_value(frame["level"], self.profile)
        self.noise.add(frame)
        self._update_clip_badge(frame.get("clipped", False))
        self.frames_seen += 1
        if self.frames_seen % NOISE_BADGE_EVERY == 0:
            self._update_quality()
        if self.frames_seen % NOISE_REPORT_EVERY == 0:
            self._update_noise_report()
        if self.egg_on:
            self._update_egg(frame)
        self.recent_f0.append(f0)
        self.recent_cents.append(np.nan if np.isnan(f0) else 100 * float(hz_to_midi(f0)))
        self.recent.append((f0, frame["delta_f"] if frame["resonance_reliable"] else np.nan,
                            frame["h1h2"], frame["cpp"], frame["ring"], frame["level"]))
        self.meters["pitch_range"].set_value(pitch_range_st(self.recent_f0), self.profile)
        self._update_vibrato()
        self._update_note_label(f0)
        self._update_coach()

        if np.isnan(f0):
            for key, tracker in self.verdicts.items():
                tracker.update(None, self.profile.target(key))
            self.meters["tuning"].set_value(None, self.profile)
            self.plane.update_point(np.nan, np.nan)
            return
        self._update_tuning()
        self.smoothed_log_f0 = self._smooth(self.smoothed_log_f0, np.log2(f0))
        verdict = self.verdicts["pitch"].update(f0, self.profile.target("pitch"))
        self.meters["pitch"].set_value(2 ** self.smoothed_log_f0, self.profile, on_target=verdict,
                                       use_profile=False)
        for key in SMOOTHED_KEYS:
            self._update_smoothed_meter(key, frame, f0)

        resonance = self.smoothed["delta_f"] if self._reading_note("delta_f", frame, f0) is None else np.nan
        self.plane.update_point(2 ** self.smoothed_log_f0, resonance)
        self._update_register(frame)

    def _reading_note(self, key, frame, f0):
        """Why this live reading can't be shown (a short reason), or None when it can: the pitch is
        too high for formants, or noise or too little level per coach.take_quality."""
        if key == "delta_f" and (not frame["resonance_reliable"] or f0 > coach.FORMANT_MAX_F0):
            return "pitch too high to measure"
        if not self.quality["trust"].get(key, True):
            return "too quiet or noisy" if self.quality["tier"] == "poor" else "too noisy"
        return None

    def _update_smoothed_meter(self, key, frame, f0):
        """One of SMOOTHED_KEYS: smoothed bar, 1.5 s verdict, or a hatched bar with the reason."""
        meter, value = self.meters[key], frame[key]
        note = self._reading_note(key, frame, f0)
        usable = note is None and not np.isnan(value)
        verdict = self.verdicts[key].update(value if usable else None, self.profile.target(key))
        if note:
            meter.set_value(None, self.profile, note=note)
            return
        if usable:
            self.smoothed[key] = self._smooth(self.smoothed[key], value)
        display = None
        if key == "h1h2" and f0 > H1H2_ROUGH_HZ and not np.isnan(self.smoothed[key]):
            display = f"{self.smoothed[key]:+.1f} dB, rough above {H1H2_ROUGH_HZ} Hz"
        meter.set_value(self.smoothed[key], self.profile, display=display, on_target=verdict,
                        use_profile=False)

    def _update_egg(self, frame):
        """Contact quotient meter and badge; the badge goes grey when no EGG cycles come in."""
        cq = frame.get("cq", np.nan)
        if not np.isnan(cq):
            self.egg_frame = self.frames_seen
            self.smoothed["cq"] = self._smooth(self.smoothed["cq"], cq)
        meter, value = self.meters["cq"], self.smoothed["cq"]
        meter.set_value(None if np.isnan(value) else value, self.profile,
                        note="no EGG signal" if np.isnan(value) else None)
        self._update_egg_badge()

    def _update_egg_badge(self):
        ok = self.frames_seen - self.egg_frame <= EGG_SIGNAL_SECONDS / UPDATE_INTERVAL
        self.egg_badge.setStyleSheet(f"color: {ON_TARGET_COLOR if ok else '#777'}; "
                                     "font-size: 11px; font-weight: bold;")
        self.egg_badge.setToolTip("EGG connected: contact quotient is being measured." if ok else
                                  "No EGG signal. Check the electrodes, the cable, the channel choice "
                                  "and the EGG's gain (and that voice is being made).")

    def register_features(self, frame):
        """Feature dict for the register score from a live frame (and the audio window, for H1-A3)."""
        f0, cq = frame["f0"], frame.get("cq", np.nan)
        window = self.window_provider() if self.window_provider else None
        if window is not None and len(window) >= register.FRAME:
            features = register.frame_features(window[-register.FRAME:], SAMPLE_RATE, f0, frame["formants"],
                                               register.NOMINAL_BANDWIDTHS, cq)
        else:
            features = {"h1h2": np.nan, "h1a3": np.nan, "cpp": np.nan, "f0": f0,
                        "cq": None if np.isnan(cq) else cq}
        for key in ("h1h2", "cpp"):             # the live analyzer already measured these
            if not np.isnan(frame[key]):
                features[key] = frame[key]
        return features

    def _update_register(self, frame):
        """Chest <-> head meter: the register model's M2 score, smoothed; "fry" when the pitch says so."""
        meter = self.meters["register"]
        score = self.register_model.score(self.register_features(frame))
        recent = np.full(FRY_CHECK_FRAMES, np.nan)     # padded: is_fry needs at least 5 frames
        tail = list(self.recent_f0)[-FRY_CHECK_FRAMES:]
        recent[len(recent) - len(tail):] = tail
        fry = bool(register.is_fry(recent, 1 / UPDATE_INTERVAL)[-1])
        if not np.isnan(score) and not fry:
            self.smoothed["register"] = self._smooth(self.smoothed["register"], score)
        shown = self.smoothed["register"]
        if fry:
            meter.set_value(None if np.isnan(shown) else 100 * shown, self.profile,
                            display="fry", note=None if not np.isnan(shown) else "fry")
        elif not np.isnan(score):
            meter.set_value(100 * shown, self.profile, display=self.register_model.label(shown))

    def pitch_and_resonance_on_target(self):
        """True when pitch and resonance are both on target (as Profile.in_both, for the targets that
        are set), judged on the steady verdicts. None when neither target is set."""
        needed = [key for key in ("pitch", "delta_f") if self.profile.target(key) is not None]
        if not needed:
            return None
        for key in needed:
            tracker = self.verdicts[key]
            if tracker.state is not True or tracker.unvoiced > FRESH_VERDICT_FRAMES:
                return False
        return True

    def _update_note_label(self, f0):
        """The note readout: median pitch of the last ~quarter second, changing
        at most every LIVE_TEXT_SECONDS and held briefly through gaps, so it
        can be read rather than flickering. Green only when pitch and resonance
        are both on target (see pitch_and_resonance_on_target)."""
        now = time.monotonic()
        if not np.isnan(f0):
            self.last_voiced_at = now
        if now - self.note_shown_at < LIVE_TEXT_SECONDS:
            return
        recent = np.array(list(self.recent_f0)[-int(NOTE_WINDOW_SECONDS / UPDATE_INTERVAL):])
        recent = recent[~np.isnan(recent)]
        if len(recent):
            pitch = float(np.median(recent))
            on_target = self.pitch_and_resonance_on_target()
            self.note_label.setText(pitch_label(pitch))
            self.note_label.setStyleSheet(f"color: {ON_TARGET_COLOR if on_target else 'white'};")
        elif now - self.last_voiced_at >= NOTE_HOLD_SECONDS:
            self.note_label.setText("—")
            self.note_label.setStyleSheet("color: white;")
        else:
            return
        self.note_shown_at = now

    def _update_clip_badge(self, clipped):
        if clipped:
            self.clip_frames_left = int(CLIP_BADGE_SECONDS / UPDATE_INTERVAL)
        elif self.clip_frames_left:
            self.clip_frames_left -= 1
        self.clip_badge.setVisible(self.clip_frames_left > 0)

    def _update_quality(self):
        """Rolling input quality from the last seconds of frames: the voice-band SNR and the voiced
        level, and from them which readings can be trusted (coach.take_quality)."""
        self.snr, _ = self.noise.snr()
        voiced = np.array([row[5] for row in self.recent if not np.isnan(row[0])])
        self.live_level = float(np.median(voiced)) if len(voiced) >= COACH_MIN_VOICED_FRAMES // 4 else None
        self.quality = coach.take_quality({"snr": self.snr, "level": self.live_level}, self.noise_info)
        self._update_noise_badge()

    def _update_noise_report(self):
        """Run denoise.noise_report on the last few seconds of audio (needs the engine)."""
        engine = self.engine
        if engine is None or not hasattr(engine, "recent_audio"):
            return
        audio = engine.recent_audio(NOISE_REPORT_SECONDS)
        if len(audio) < NOISE_REPORT_MIN_SECONDS * engine.sample_rate:
            return
        self.noise_info = denoise.noise_report(audio, engine.sample_rate)
        self._update_noise_badge()

    def noise_issues(self):
        """Short names of the input problems found, most important first: "whine", "rumble",
        "quiet: move closer", "clipping"."""
        info, issues = self.noise_info, []
        if any(level > coach.LINE_REPORT_DBFS for _, level in info.get("lines") or []):
            issues.append("whine")
        rumble = info.get("rumble_share")
        if rumble is not None and not np.isnan(rumble) and rumble > coach.RUMBLE_SHARE:
            issues.append("rumble")
        if self.live_level is not None and self.live_level < coach.QUIET_DBFS:
            issues.append("quiet: move closer")
        if info.get("clip_fraction", 0) > CLIP_REPORT_FRACTION:
            issues.append("clipping")
        return issues

    def _update_noise_badge(self):
        """The badge shows the first problem, else the SNR; the tooltip lists everything."""
        issues, snr = self.noise_issues(), self.snr
        tip = ([f"Voice-to-noise ratio in the voice band (0–{VOICE_BAND_HZ // 1000} kHz), where "
                "VoiceLab measures, over the last 10 s."] if snr is not None else
               ["Pause briefly so the room noise can be measured."])
        if issues:
            text, color = issues[0], OFF_TARGET_COLOR
            tip.append("Found: " + ", ".join(issues) + ".")
        elif snr is None:
            text, color = "SNR —", "#aaa"
        else:
            text = f"SNR {snr:.0f} dB"
            color = ON_TARGET_COLOR if snr >= coach.GOOD_SNR else OFF_TARGET_COLOR
        if snr is not None and snr < coach.GOOD_SNR:
            tip.append(f"Below {coach.GOOD_SNR} dB, Clarity and Heavy ↔ Light become unreliable and are "
                       "shown as — until the input is better.")
        tip.append("Click to run the mic check.")
        self.noise_badge.setText(text)
        self.noise_badge.setStyleSheet(NOISE_BADGE_STYLE.format(color=color))
        self.noise_badge.setToolTip(" ".join(tip))
        info = getattr(self.engine, "capture_info", None) or {}
        slow = bool(info) and info.get("path") != "wasapi"
        self.path_badge.setVisible(slow)
        if slow:
            latency = f", {1000 * info['latency']:.0f} ms latency" if info.get("latency") else ""
            self.path_badge.setToolTip(f"Capture path: {info.get('host_api', 'unknown')}{latency}. WASAPI "
                                       "has less delay and keeps the mic's own sample rate. Choose the "
                                       "mic again, or close other audio apps.")

    def show_mic_check(self):
        """Open the mic check dialog (non-modal); its result is stored in last_mic_check and sent
        through mic_check_done. Does nothing without an engine."""
        engine = self.engine
        if engine is None:
            return None
        self.mic_dialog = MicCheckDialog(engine, self)
        self.mic_dialog.finished_check.connect(self._mic_checked)
        self.mic_dialog.show()
        return self.mic_dialog

    def _mic_checked(self, result):
        self.last_mic_check = result
        self.mic_check_done.emit(result)

    def _update_coach(self):
        """The one-line coach: coach.advice on the last few seconds, at most every COACH_EVERY frames."""
        if self.frames_seen - self.coach_frame < COACH_EVERY:
            return
        self.coach_frame = self.frames_seen
        text, tip = self.coach_text()
        self.coach_label.setText(text)
        self.coach_label.setToolTip(tip)

    def rolling_summary(self):
        """take-style summary dict of the last few seconds of voiced frames, or None with too little voice."""
        rows = np.array(list(self.recent), dtype=float).reshape(-1, 6)
        voiced = rows[~np.isnan(rows[:, 0])]
        if len(voiced) < COACH_MIN_VOICED_FRAMES:
            return None

        def median(column):
            values = voiced[:, column][~np.isnan(voiced[:, column])]
            return float(np.median(values)) if len(values) else None
        f0 = voiced[:, 0]
        return {"pitch": float(np.median(f0)), "pitch_floor": float(np.percentile(f0, 10)),
                "pitch_range": pitch_range_st(f0), "delta_f": median(1), "h1h2": median(2),
                "cpp": median(3), "ring": median(4), "level": float(np.median(voiced[:, 5])),
                "snr": self.snr, "clip_events": 1 if self.clip_frames_left else 0}

    def coach_text(self):
        """(one line, full text) for the coach line from the rolling summary."""
        summary = self.rolling_summary()
        if summary is None:
            return COACH_IDLE, ""
        quality = coach.take_quality(summary, self.noise_info)
        profile = self.profile
        messages = coach.advice(summary, profile.targets, profile.baseline, self.mode or "speak", quality)
        if quality["tier"] == "poor":
            reason = quality["reasons"][0] if quality["reasons"] else "the input is too noisy"
            line = reason[0].upper() + reason[1:] + "."
            return line, line
        if not messages:
            line = "Nothing to adjust right now." if profile.targets else "Set targets to get live tips."
            return line, line
        texts = [m["text"].replace("in this take", "in the last few seconds") for m in messages]
        return texts[0].split(". ")[0].rstrip(".") + ".", " ".join(texts)

    def _update_tuning(self):
        """Cents from the exercise's target note, or from the nearest note. The bar and verdict follow
        the perceived pitch (median of the last 0.2 s, spec 03), so vibrato around the note
        doesn't flip the colour; the raw pitch is drawn as a thin line beside it."""
        meter = self.meters["tuning"]
        cents = perceived_cents(list(self.recent_cents))
        if np.isnan(cents):
            meter.set_value(None, self.profile)
            return
        raw = self.recent_cents[-1]
        target = self.target_provider()
        if target is not None:
            def offset(c):
                return (c - 100 * target) - 1200 * round((c - 100 * target) / 1200)     # any octave counts
            shown, raw_shown = offset(cents), offset(raw)
            text = f"{shown:+.0f}¢ vs {midi_name(target)}"
        else:
            nearest = round(cents / 100)
            shown, raw_shown = cents - 100 * nearest, raw - 100 * nearest
            text = f"{shown:+.0f}¢ from {NOTE_NAMES[nearest % 12]}"
        limit = TUNING_LEAVE_CENTS if self.tuning_ok else IN_TUNE_CENTS
        self.tuning_ok = abs(shown) <= limit
        meter.set_value(shown, self.profile, display=text, on_target=self.tuning_ok, use_profile=False)
        meter.bar.marker = meter._fraction(raw_shown)
        meter.bar.update()

    def _update_vibrato(self):
        """Vibrato over the last ~1.2 s, only while a note is held."""
        n = int(VIBRATO_WINDOW_SECONDS / UPDATE_INTERVAL)
        recent = np.array(list(self.recent_f0)[-n:])
        meter = self.meters["vibrato"]
        if len(recent) < n or np.isnan(recent).any():
            meter.set_value(None, self.profile, note="hold a note")
            return
        cents = 100 * hz_to_midi(recent)
        if np.ptp(cents) > 300:                       # moving between notes
            meter.set_value(None, self.profile, note="hold a note")
            return
        vib = vibrato_stats(cents, 1 / UPDATE_INTERVAL)
        if vib["rate"] is None:
            meter.set_value(0, self.profile, display="straight tone")
        else:
            meter.set_value(vib["extent"], self.profile,
                            display=f"{vib['rate']:.1f} Hz · ±{vib['extent']:.0f}¢")
