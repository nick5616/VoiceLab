"""Everything the live view decides, as data: meters and their verdicts, the note readout, the coach line,
the input badges and the pitch history. A UI calls update(frame) once per live frame and draws the
snapshot it returns. No UI toolkit."""
import time
from collections import deque

import numpy as np

import coach
import denoise
import register
from analysis import (IN_TUNE_CENTS, NOTE_NAMES, PERCEIVED_SECONDS, SAMPLE_RATE, VOICE_BAND_HZ, NoiseMonitor,
                      hz_to_midi, perceived_cents, pitch_range_st, vibrato_stats)
from display import (BOTH_MODES, LIVE_TEXT_SECONDS, METRIC_DEFS, fraction, metric_reading, midi_name, pitch_label,
                     reference)
from engine import UPDATE_INTERVAL
from take_register import register_model

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
UNTRACKED_METERS = {"register", "tuning"}            # no profile baseline, target or change text

INTONATION_WINDOW_SECONDS = 6
VIBRATO_WINDOW_SECONDS = 1.2
NOISE_WINDOW_SECONDS = 10
HISTORY_SECONDS = 8             # pitch history kept for the live graph
HISTORY_LEN = int(HISTORY_SECONDS / UPDATE_INTERVAL)

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
NOTE_WINDOW_SECONDS = 0.25      # the note readout shows the median pitch over this
NOTE_HOLD_SECONDS = 0.5         # ...and keeps the last note this long through gaps
CLIP_BADGE_SECONDS = 1.0        # the clipping badge stays up this long after a clip
EGG_SIGNAL_SECONDS = 1.5        # the EGG badge stays green this long after the last contact quotient
FRY_CHECK_FRAMES = 12           # recent pitch frames judged for vocal fry

COACH_IDLE = "Speak or sing for a moment and I'll suggest what to try."
CLIP_TOOLTIP = ("The input is overloading. Lower the mic gain or move back a little: "
                "clipping adds harshness that inflates Ring and lowers Clarity.")
SLOW_PATH_TOOLTIP = ("The mic is read through an older Windows audio path (MME or similar) "
                     "with extra delay. Choose the mic again, or close other audio apps.")
EGG_OK_TOOLTIP = "EGG connected: contact quotient is being measured."
EGG_NO_SIGNAL_TOOLTIP = ("No EGG signal. Check the electrodes, the cable, the channel choice "
                         "and the EGG's gain (and that voice is being made).")
LABELS = {"pitch_range": f"Intonation Range (last {INTONATION_WINDOW_SECONDS} s)", "level": "Input Level"}

# Exponential smoothing for live meters (~200 ms time constant).
METER_SMOOTHING = 1 - np.exp(-UPDATE_INTERVAL / 0.2)


# --- STABLE VERDICTS ---

VERDICT_FRAMES = 80         # voiced frames judged (2 s at 25 ms): longer than a vowel (spec 06)
VERDICT_MIN_FRAMES = 12     # no verdict before this much voice
VERDICT_DWELL_FRAMES = 40   # a verdict holds at least this long (1 s); own judgement
VERDICT_MARGIN = 0.15       # of the target's width: hysteresis at its edges; own judgement
VERDICT_RESET_FRAMES = 80   # a pause this long (2 s) forgets the window


def robust_center(values):
    """The mean of the middle half of the values (interquartile mean). Like a median it ignores
    outliers, but it moves smoothly when a window holds two vowels' worth of values, where the
    median jumps between the two clusters as their shares cross 50%."""
    values = np.sort(np.asarray(values, dtype=float))
    n = len(values)
    return float(values[n // 4:n - n // 4].mean())


class VerdictTracker:
    """On/off-target verdict for a live value that doesn't flicker: judged on the robust centre
    of the last VERDICT_FRAMES voiced values, with hysteresis at the target's edges and a
    minimum dwell time. update() returns True, False, or None (no target or not enough voice)."""

    def __init__(self, frames=VERDICT_FRAMES):
        self.frames = frames
        self.reset()

    def reset(self):
        self.values, self.state, self.since_change, self.unvoiced = [], None, 0, 0

    def center(self):
        """The robust centre of the window, or None with too little voice."""
        return robust_center(self.values) if len(self.values) >= VERDICT_MIN_FRAMES else None

    def update(self, value, target):
        """Feed one frame's value (None or NaN when it has none); `target` is [low, high] or None."""
        if value is None or np.isnan(value):
            self.unvoiced += 1
            if self.unvoiced >= VERDICT_RESET_FRAMES:
                self.reset()
            return self.state if target is not None else None
        self.unvoiced = 0
        self.values.append(float(value))
        del self.values[:-self.frames]
        center = self.center()
        if target is None or center is None:
            self.state = None
            return None
        margin = VERDICT_MARGIN * (target[1] - target[0])
        self.since_change += 1
        if self.state is None:
            self.state, self.since_change = target[0] <= center <= target[1], 0
        elif self.since_change >= VERDICT_DWELL_FRAMES:
            if self.state:
                now = target[0] - margin <= center <= target[1] + margin
            else:
                now = target[0] + margin <= center <= target[1] - margin
            if now != self.state:
                self.state, self.since_change = now, 0
        return self.state


def register_zone(position):
    """chest, mix or head for a 0-1 register score (the mix band in the middle)."""
    return "chest" if position < register.MIX_BAND[0] else "head" if position > register.MIX_BAND[1] else "mix"


def _smooth(prev, value):
    return value if np.isnan(prev) else prev + METER_SMOOTHING * (value - prev)


class LiveSession:
    """The live meters, verdicts, note, coach line and input badges for one profile. `engine` (optional)
    supplies the audio window for register features, recent audio for the noise report and the capture
    path; `target_provider()` gives the exercise's current target note (MIDI) or None."""

    def __init__(self, profile, mode="speak", clock=time.monotonic, engine=None):
        self.profile = profile
        self.clock = clock
        self.engine = engine
        self.mode = None
        self.target_provider = lambda: None
        self.text_interval = LIVE_TEXT_SECONDS     # tests set 0: text is throttled by the clock
        self.noise = NoiseMonitor(int(NOISE_WINDOW_SECONDS / UPDATE_INTERVAL))
        self.noise_info = {}            # the last denoise.noise_report, {} before the first
        self.quality = coach.take_quality({})
        self.live_level = None
        self.snr = None
        self.frames_seen = 0
        self.clip_frames_left = 0
        self.egg_on = False
        self.egg_frame = -10 ** 9       # frame count when a contact quotient last came in
        self.register_model = register_model(profile.register_model)
        self.smoothed = {key: np.nan for key in SMOOTHED_KEYS + ["register", "cq"]}
        self.smoothed_log_f0 = np.nan
        self.recent_f0 = deque(maxlen=int(INTONATION_WINDOW_SECONDS / UPDATE_INTERVAL))
        # Absolute pitch in cents (100 x MIDI) of the last PERCEIVED_SECONDS, for tuning.
        self.recent_cents = deque(maxlen=int(round(PERCEIVED_SECONDS / UPDATE_INTERVAL)))
        # Per-frame values of the last few seconds for the coach line: (f0, delta_f, h1h2, cpp, ring, level).
        self.recent = deque(maxlen=int(COACH_WINDOW_SECONDS / UPDATE_INTERVAL))
        self.pitch_history = deque([np.nan] * HISTORY_LEN, maxlen=HISTORY_LEN)
        self.verdicts = {key: VerdictTracker() for key in VERDICT_KEYS}
        self.tuning_ok = None
        self.coach_frame = -10 ** 9
        self.coach = {"line": COACH_IDLE, "full": ""}
        self.note = {"text": "—", "on_target": None}
        self.note_shown_at = 0.0
        self.last_voiced_at = 0.0
        self.plane = {"f0": None, "delta_f": None}
        self.input = {"badge": "SNR —", "severity": "unknown", "tooltip": "", "issues": [], "slow_path": False,
                      "slow_path_tooltip": SLOW_PATH_TOOLTIP, "clipping": False, "clip_tooltip": CLIP_TOOLTIP,
                      "snr": None}
        self.metrics = {}
        self._text_at = {}
        self.changed_text = False
        for key, _ in LIVE_METERS:
            self.metrics[key] = self._reading(key, None)
            self.metrics[key]["title"] = self.label(key)
        self.set_mode(mode)

    # --- SETTINGS ---

    def label(self, key):
        if key == "register":
            return "Register" if self.register_model.is_calibrated() else "Register (estimate)"
        return LABELS.get(key) or METRIC_DEFS[key][0]

    def meter_modes(self, key):
        return dict(LIVE_METERS)[key]

    def visible(self, key):
        """Whether a meter is shown in the current mode (the EGG meter only with an EGG)."""
        return self.mode in self.meter_modes(key) and (self.egg_on or key not in EGG_METERS)

    def set_mode(self, mode):
        """"speak" or "sing": which meters show, the pitch scale, and the pitch tracker's mode."""
        self.mode = mode
        if self.engine is not None and hasattr(self.engine, "set_mode"):
            self.engine.set_mode(mode)
        self.recent.clear()                  # the last few seconds were the other mode's
        self.recent_cents.clear()
        self.coach_frame = -10 ** 9
        self.refresh_profile()

    def set_egg(self, on):
        """Show the contact quotient meter and EGG badge while an EGG is configured."""
        self.egg_on = bool(on)
        self.egg_frame = -10 ** 9
        self.smoothed["cq"] = np.nan
        if self.mode:
            self.set_mode(self.mode)

    def set_targets(self, targets):
        """Use these targets ({key: [low, high]}) for the live verdicts. The profile is not saved."""
        self.profile.targets = {key: list(value) for key, value in (targets or {}).items()}
        self.refresh_profile()

    def reset_verdicts(self):
        for tracker in self.verdicts.values():
            tracker.reset()
        self.tuning_ok = None

    def refresh_profile(self):
        """Re-read the profile: register model, targets and baseline on the meters; verdicts start over."""
        self.reset_verdicts()
        self.register_model = register_model(self.profile.register_model)
        for key, reading in self.metrics.items():
            reading["label"] = self.label(key)
            reading["visible"] = self.visible(key)
            if key in ("tuning", "vibrato") or key in UNTRACKED_METERS:
                continue
            band, baseline = reference(key, self.profile, self.mode)
            reading.update(band=band, band_kind="target" if band else None, baseline=baseline)

    # --- FRAMES ---

    def update(self, frame, window=None):
        """Take one live frame (and, optionally, the audio window it came from) and return the snapshot."""
        self.changed_text = False
        f0 = frame["f0"]
        self.pitch_history.append(f0)
        self._set("level", frame["level"])
        self.noise.add(frame)
        self._update_clip(frame.get("clipped", False))
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
        self._set("pitch_range", pitch_range_st(self.recent_f0))
        self._update_vibrato()
        self._update_note(f0)
        self._update_coach()

        if np.isnan(f0):
            for key, tracker in self.verdicts.items():
                tracker.update(None, self.profile.target(key))
            self._set("tuning", None)
            self.plane = {"f0": None, "delta_f": None}
            return self.snapshot()
        self._update_tuning()
        self.smoothed_log_f0 = _smooth(self.smoothed_log_f0, np.log2(f0))
        verdict = self.verdicts["pitch"].update(f0, self.profile.target("pitch"))
        self._set("pitch", 2 ** self.smoothed_log_f0, on_target=verdict, use_profile=False)
        for key in SMOOTHED_KEYS:
            self._update_smoothed(key, frame, f0)
        resonance = self.smoothed["delta_f"] if self.reading_note("delta_f", frame, f0) is None else np.nan
        smoothed_f0 = 2 ** self.smoothed_log_f0
        self.plane = {"f0": None if np.isnan(smoothed_f0) else float(smoothed_f0),
                      "delta_f": None if np.isnan(resonance) else float(resonance)}
        self._update_register(frame, window)
        return self.snapshot()

    def snapshot(self):
        """The state to draw: {"mode", "voiced", "f0", "note": {"text", "on_target"}, "metrics": {key: reading},
        "coach": {"line", "full"}, "input": {...}, "egg": {"on", "signal", "tooltip"}, "pitch_history",
        "plane": {"f0", "delta_f"}, "changed_text"}. A reading is display.metric_reading's dict."""
        return {
            "mode": self.mode,
            "voiced": bool(len(self.recent_f0)) and not np.isnan(self.recent_f0[-1]),
            "f0": None if not len(self.recent_f0) or np.isnan(self.recent_f0[-1]) else float(self.recent_f0[-1]),
            "note": dict(self.note),
            "metrics": {key: dict(reading, visible=self.visible(key)) for key, reading in self.metrics.items()},
            "coach": dict(self.coach),
            "input": dict(self.input, issues=list(self.input["issues"])),
            "egg": self.egg_state(),
            "pitch_history": list(self.pitch_history),
            "plane": dict(self.plane),
            "changed_text": self.changed_text,
        }

    def _reading(self, key, value, **kwargs):
        reading = metric_reading(key, value, self.profile, mode=self.mode, tracked=key not in UNTRACKED_METERS,
                                 label=self.label(key), parens=key != "register",
                                 zone_fn=register_zone if key == "register" else None, **kwargs)
        if key == "tuning":            # the in-tune zone: ±IN_TUNE_CENTS on a ±50 cent bar
            reading.update(band=(0.5 - IN_TUNE_CENTS / 100, 0.5 + IN_TUNE_CENTS / 100), band_kind="in_tune")
        elif key == "register":        # the mix zone
            reading.update(band=tuple(register.MIX_BAND), band_kind="mix")
        elif key == "vibrato":         # fixed scale, no profile target
            reading.update(band=None, band_kind=None, baseline=None)
        return reading

    def _set(self, key, value, marker=None, **kwargs):
        """New reading for a meter. Its text changes at most every text_interval, so numbers stay readable;
        the bar itself moves every frame."""
        reading = self._reading(key, value, **kwargs)
        reading["marker"] = marker
        now = self.clock()
        old = self.metrics.get(key)
        if old is not None and self.text_interval and now - self._text_at.get(key, 0.0) < self.text_interval:
            reading["title"] = old["title"]
        else:
            self._text_at[key] = now
            self.changed_text = self.changed_text or old is None or old["title"] != reading["title"]
        self.metrics[key] = reading

    def reading_note(self, key, frame, f0):
        """Why this live reading can't be shown (a short reason), or None when it can: the pitch is
        too high for formants, or noise or too little level per coach.take_quality."""
        if key == "delta_f" and (not frame["resonance_reliable"] or f0 > coach.FORMANT_MAX_F0):
            return "pitch too high to measure"
        if not self.quality["trust"].get(key, True):
            return "too quiet or noisy" if self.quality["tier"] == "poor" else "too noisy"
        return None

    def _update_smoothed(self, key, frame, f0):
        """One of SMOOTHED_KEYS: smoothed bar, 1.5 s verdict, or an unavailable reading with the reason."""
        value = frame[key]
        note = self.reading_note(key, frame, f0)
        usable = note is None and not np.isnan(value)
        verdict = self.verdicts[key].update(value if usable else None, self.profile.target(key))
        if note:
            self._set(key, None, note=note)
            return
        if usable:
            self.smoothed[key] = _smooth(self.smoothed[key], value)
        display = None
        if key == "h1h2" and f0 > H1H2_ROUGH_HZ and not np.isnan(self.smoothed[key]):
            display = f"{self.smoothed[key]:+.1f} dB, rough above {H1H2_ROUGH_HZ} Hz"
        self._set(key, self.smoothed[key], display=display, on_target=verdict, use_profile=False)

    def _update_egg(self, frame):
        """Contact quotient meter; the badge goes grey when no EGG cycles come in."""
        cq = frame.get("cq", np.nan)
        if not np.isnan(cq):
            self.egg_frame = self.frames_seen
            self.smoothed["cq"] = _smooth(self.smoothed["cq"], cq)
        value = self.smoothed["cq"]
        self._set("cq", None if np.isnan(value) else value, note="no EGG signal" if np.isnan(value) else None)

    def egg_state(self):
        """{"on", "signal", "tooltip"}: the EGG badge."""
        ok = self.frames_seen - self.egg_frame <= EGG_SIGNAL_SECONDS / UPDATE_INTERVAL
        return {"on": self.egg_on, "signal": ok, "tooltip": EGG_OK_TOOLTIP if ok else EGG_NO_SIGNAL_TOOLTIP}

    def register_features(self, frame, window=None):
        """Feature dict for the register score from a live frame (and the audio window, for H1-A3)."""
        f0, cq = frame["f0"], frame.get("cq", np.nan)
        if window is None and self.engine is not None and hasattr(self.engine, "latest_window"):
            window = self.engine.latest_window()
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

    def _update_register(self, frame, window):
        """Chest <-> head meter: the register model's M2 score, smoothed; "fry" when the pitch says so."""
        score = self.register_model.score(self.register_features(frame, window))
        recent = np.full(FRY_CHECK_FRAMES, np.nan)     # padded: is_fry needs at least 5 frames
        tail = list(self.recent_f0)[-FRY_CHECK_FRAMES:]
        recent[len(recent) - len(tail):] = tail
        fry = bool(register.is_fry(recent, 1 / UPDATE_INTERVAL)[-1])
        if not np.isnan(score) and not fry:
            self.smoothed["register"] = _smooth(self.smoothed["register"], score)
        shown = self.smoothed["register"]
        if fry:
            self._set("register", None if np.isnan(shown) else 100 * shown,
                      display="fry", note=None if not np.isnan(shown) else "fry")
        elif not np.isnan(score):
            self._set("register", 100 * shown, display=self.register_model.label(shown))

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

    def _update_note(self, f0):
        """The note readout: median pitch of the last ~quarter second, changing at most every
        LIVE_TEXT_SECONDS and held briefly through gaps, so it can be read rather than flickering.
        On target only when pitch and resonance are both on target (pitch_and_resonance_on_target)."""
        now = self.clock()
        if not np.isnan(f0):
            self.last_voiced_at = now
        if now - self.note_shown_at < LIVE_TEXT_SECONDS:
            return
        recent = np.array(list(self.recent_f0)[-int(NOTE_WINDOW_SECONDS / UPDATE_INTERVAL):])
        recent = recent[~np.isnan(recent)]
        if len(recent):
            pitch = float(np.median(recent))
            note = {"text": pitch_label(pitch), "on_target": self.pitch_and_resonance_on_target()}
        elif now - self.last_voiced_at >= NOTE_HOLD_SECONDS:
            note = {"text": "—", "on_target": None}
        else:
            return
        self.changed_text = self.changed_text or note != self.note
        self.note = note
        self.note_shown_at = now

    def _update_clip(self, clipped):
        if clipped:
            self.clip_frames_left = int(CLIP_BADGE_SECONDS / UPDATE_INTERVAL)
        elif self.clip_frames_left:
            self.clip_frames_left -= 1
        self.input["clipping"] = self.clip_frames_left > 0

    def _update_quality(self):
        """Rolling input quality from the last seconds of frames: the voice-band SNR and the voiced
        level, and from them which readings can be trusted (coach.take_quality)."""
        self.snr, _ = self.noise.snr()
        voiced = np.array([row[5] for row in self.recent if not np.isnan(row[0])])
        self.live_level = float(np.median(voiced)) if len(voiced) >= COACH_MIN_VOICED_FRAMES // 4 else None
        self.quality = coach.take_quality({"snr": self.snr, "level": self.live_level}, self.noise_info)
        self.refresh_input()

    def noise_report(self, audio, sample_rate):
        """Run denoise.noise_report on recent audio (pauses and some voice); it feeds the input badge."""
        self.noise_info = denoise.noise_report(audio, sample_rate)
        self.refresh_input()
        return self.noise_info

    def _update_noise_report(self):
        """The noise report on the engine's last few seconds of audio (needs an engine)."""
        engine = self.engine
        if engine is None or not hasattr(engine, "recent_audio"):
            return
        audio = engine.recent_audio(NOISE_REPORT_SECONDS)
        if len(audio) < NOISE_REPORT_MIN_SECONDS * engine.sample_rate:
            return
        self.noise_report(audio, engine.sample_rate)

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

    def refresh_input(self):
        """The input badge: the first problem, else the SNR; the tooltip lists everything. Also whether the
        capture path is the slow one."""
        issues, snr = self.noise_issues(), self.snr
        tip = ([f"Voice-to-noise ratio in the voice band (0–{VOICE_BAND_HZ // 1000} kHz), where "
                "VoiceLab measures, over the last 10 s."] if snr is not None else
               ["Pause briefly so the room noise can be measured."])
        if issues:
            text, severity = issues[0], "warn"
            tip.append("Found: " + ", ".join(issues) + ".")
        elif snr is None:
            text, severity = "SNR —", "unknown"
        else:
            text = f"SNR {snr:.0f} dB"
            severity = "ok" if snr >= coach.GOOD_SNR else "warn"
        if snr is not None and snr < coach.GOOD_SNR:
            tip.append(f"Below {coach.GOOD_SNR} dB, Clarity and Heavy ↔ Light become unreliable and are "
                       "shown as — until the input is better.")
        tip.append("Click to run the mic check.")
        info = getattr(self.engine, "capture_info", None) or {}
        slow = bool(info) and info.get("path") != "wasapi"
        path_tip = self.input["slow_path_tooltip"]
        if slow:
            latency = f", {1000 * info['latency']:.0f} ms latency" if info.get("latency") else ""
            path_tip = (f"Capture path: {info.get('host_api', 'unknown')}{latency}. WASAPI "
                        "has less delay and keeps the mic's own sample rate. Choose the "
                        "mic again, or close other audio apps.")
        self.input.update(badge=text, severity=severity, tooltip=" ".join(tip), issues=issues, snr=snr,
                          slow_path=slow, slow_path_tooltip=path_tip)
        self.changed_text = True

    def _update_coach(self):
        """The one-line coach: coach.advice on the last few seconds, at most every COACH_EVERY frames."""
        if self.frames_seen - self.coach_frame < COACH_EVERY:
            return
        self.coach_frame = self.frames_seen
        line, full = self.coach_text()
        self.coach = {"line": line, "full": full}
        self.changed_text = True

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
        doesn't flip the colour; the raw pitch is the marker beside it."""
        cents = perceived_cents(list(self.recent_cents))
        if np.isnan(cents):
            self._set("tuning", None)
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
        self._set("tuning", shown, display=text, on_target=self.tuning_ok, use_profile=False,
                  marker=fraction("tuning", raw_shown, self.mode))

    def _update_vibrato(self):
        """Vibrato over the last ~1.2 s, only while a note is held."""
        n = int(VIBRATO_WINDOW_SECONDS / UPDATE_INTERVAL)
        recent = np.array(list(self.recent_f0)[-n:])
        if len(recent) < n or np.isnan(recent).any():
            self._set("vibrato", None, note="hold a note")
            return
        cents = 100 * hz_to_midi(recent)
        if np.ptp(cents) > 300:                       # moving between notes
            self._set("vibrato", None, note="hold a note")
            return
        vib = vibrato_stats(cents, 1 / UPDATE_INTERVAL)
        if vib["rate"] is None:
            self._set("vibrato", 0, display="straight tone")
        else:
            self._set("vibrato", vib["extent"], display=f"{vib['rate']:.1f} Hz · ±{vib['extent']:.0f}¢")
