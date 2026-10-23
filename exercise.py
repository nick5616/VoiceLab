"""Singing exercises: built-in patterns or a MIDI melody, a guide tone, per-note scoring of the take, and
ExerciseSession, the running exercise as data (a UI draws the piano roll from it). No UI toolkit."""
import time
from collections import defaultdict

import numpy as np
import sounddevice as sd

from analysis import (CLOSE_CENTS, IN_TUNE_CENTS, PERCEIVED_SECONDS, SAMPLE_RATE,
                      analyze_take, hz_to_midi, midi_to_hz, perceived_cents, score_exercise)
from display import midi_name
from jobs import JobRunner

# Semitone patterns from the start note: (name, intervals, seconds per note).
PATTERNS = [
    ("Five-note scale", [0, 2, 4, 5, 7, 5, 4, 2, 0], 0.6),
    ("Major arpeggio", [0, 4, 7, 12, 7, 4, 0], 0.7),
    ("Minor arpeggio", [0, 3, 7, 12, 7, 3, 0], 0.7),
    ("Octave scale", [0, 2, 4, 5, 7, 9, 11, 12, 11, 9, 7, 5, 4, 2, 0], 0.5),
    ("Long tone (vibrato & sustain)", [0], 5.0),
]
COUNT_IN_BEATS, BEAT_SECONDS = 4, 0.5
LEAD_IN = COUNT_IN_BEATS * BEAT_SECONDS
TAIL_SECONDS = 0.6                # keep recording briefly after the last note
ROLL_BEFORE, ROLL_AFTER = 3, 5    # piano roll window around "now" (s)
NOTE_RANGE = range(36, 85)        # C2-C6 start notes
IN_TUNE_COLOR, CLOSE_COLOR, OFF_COLOR = "#2ecc71", "#f1c40f", "#e67e22"   # within 25 / 50 cents / beyond
DEFAULT_START_MIDI = 48          # C3
MIDI_CHOICE = len(PATTERNS)       # the choice after the patterns: a MIDI melody
MIDI_CHOICE_TEXT = "Load MIDI melody…"
IDLE_STATUS = "Sing along with the bars. Singing an octave down is fine."


def pattern_targets(intervals, note_seconds, start_midi):
    """[(start_s, duration_s, midi)] after the count-in."""
    return [(LEAD_IN + i * note_seconds, note_seconds, start_midi + step)
            for i, step in enumerate(intervals)]


def load_midi_melody(path, transpose=0):
    """Monophonic melody from a MIDI file: the busiest non-drum channel, keeping
    the highest note when notes start together and cutting each note off where
    the next begins. Times are shifted to start after the count-in."""
    import mido
    active, by_channel, now = {}, defaultdict(list), 0.0
    for msg in mido.MidiFile(path):          # iterating gives times in seconds
        now += msg.time
        if msg.type not in ("note_on", "note_off"):
            continue
        key = (msg.channel, msg.note)
        if msg.type == "note_on" and msg.velocity > 0:
            active[key] = now
        elif key in active:
            start = active.pop(key)
            by_channel[msg.channel].append((start, now - start, msg.note))
    channels = [c for c in by_channel if c != 9]  # channel 10 is drums
    if not channels:
        raise ValueError("No melodic notes found in this MIDI file.")
    notes = sorted(by_channel[max(channels, key=lambda c: len(by_channel[c]))],
                   key=lambda n: (round(n[0], 2), -n[2]))
    melody = []
    for start, duration, note in notes:
        if melody and round(start, 2) == round(melody[-1][0], 2):
            continue                              # chord: keep the top note
        if melody and start < melody[-1][0] + melody[-1][1]:
            prev = melody[-1]
            melody[-1] = (prev[0], start - prev[0], prev[2])
        melody.append((start, duration, note))
    offset = LEAD_IN - melody[0][0]
    return [(s + offset, d, n + transpose) for s, d, n in melody if d > 0.05]


def guide_audio(targets, sample_rate=SAMPLE_RATE):
    """Count-in clicks plus a soft tone for each target note."""
    end = max(s + d for s, d, _ in targets) + TAIL_SECONDS
    out = np.zeros(int(end * sample_rate), dtype=np.float32)
    click_len = int(0.02 * sample_rate)
    click = np.sin(2 * np.pi * 1500 * np.arange(click_len) / sample_rate) * np.hanning(click_len)
    for beat in range(COUNT_IN_BEATS):
        i = int(beat * BEAT_SECONDS * sample_rate)
        out[i:i + click_len] += 0.3 * click
    for start, duration, midi in targets:
        n = int(duration * sample_rate)
        t = np.arange(n) / sample_rate
        f = float(midi_to_hz(midi))
        tone = sum(a * np.sin(2 * np.pi * k * f * t) for k, a in ((1, 1.0), (2, 0.4), (3, 0.15)))
        env = np.minimum(1, np.minimum(t / 0.03, (duration - t) / 0.06)).clip(0)
        i = int(start * sample_rate)
        out[i:i + n] += 0.15 * (tone * env)[:len(out) - i]
    return out


def tuning_color(cents):
    """Colour for a perceived error in cents: green within 25, yellow within 50, else orange; None if unknown."""
    if cents is None or np.isnan(cents):
        return None
    if abs(cents) <= IN_TUNE_CENTS:
        return IN_TUNE_COLOR
    return CLOSE_COLOR if abs(cents) <= CLOSE_CENTS else OFF_COLOR


def output_latency():
    """Seconds between sd.play and the guide being heard, from the output stream (0 if unknown)."""
    try:
        latency = sd.get_stream().latency
        return float(latency[1] if np.ndim(latency) else latency)
    except Exception:
        return 0.0


def analyze_exercise_take(audio, targets, name, latency=0.0):
    """analyze_take plus the exercise score. `latency` is the guide's output latency: the guide
    sounded that much after the recording started, so the singer is scored against the targets
    shifted by it (the reported timing is then reaction time, not device lag)."""
    result = analyze_take(audio, mode="sing")
    if "error" not in result:
        times, f0 = result["tracks"]["times"], result["tracks"]["f0"]
        heard = [(s + latency, d, m) for s, d, m in targets]
        result["exercise"] = score_exercise(times, f0, heard) | {"name": name, "targets": targets,
                                                                 "latency": latency}
    return result


def current_target(targets, now):
    for start, duration, midi in targets:
        if start <= now < start + duration:
            return midi
    return None


def fold_to_target(f0, target):
    """f0 as a (fractional) MIDI note, folded onto the target's octave so an octave-down singer stays
    on the bars; NaN when unvoiced."""
    if np.isnan(f0):
        return np.nan
    midi = float(hz_to_midi(f0))
    if target is not None:
        midi -= 12 * np.round((midi - target) / 12)
    return midi


class ExerciseSession:
    """Pick an exercise, sing it against the guide, get a scored take. Emits "exercise_changed"(state)
    when its text changes outside a frame, "practice"(running) when it starts or stops, and
    "exercise_scored"(result) when a scored take is ready. on_frame(frame) returns the frame's state."""

    def __init__(self, engine, events, runner=None, clock=time.perf_counter):
        self.engine, self.events = engine, events
        self.runner = runner or JobRunner(events)
        self.clock = clock
        self.running = False
        self.choice = 0                  # index into PATTERNS, or MIDI_CHOICE
        self.start_midi = DEFAULT_START_MIDI
        self.transpose = 0               # semitones, for a MIDI melody
        self.guide = True                # play the guide tone
        self.targets, self.midi_targets, self.midi_file = [], None, None
        self.t0 = None
        self.latency = 0.0               # guide output latency, s
        self.recent = []                 # (time, cents from target) over the last PERCEIVED_SECONDS
        self.status = IDLE_STATUS
        self.cents_text, self.cents_color = "—", None    # the big readout; colour from tuning_color
        self.result = None
        self.job = None

    # --- CHOOSING ---

    @staticmethod
    def choice_names():
        return [name for name, _, _ in PATTERNS] + [MIDI_CHOICE_TEXT]

    @staticmethod
    def start_notes():
        """[(MIDI, name)] of the start notes offered."""
        return [(m, midi_name(m)) for m in NOTE_RANGE]

    def is_midi(self):
        return self.choice == MIDI_CHOICE

    def choose(self, choice, midi_path=None):
        """Pick a pattern, or (MIDI_CHOICE) a melody loaded from `midi_path`. Without a melody loaded the
        first pattern is picked instead. Returns the choice in effect."""
        self.choice = choice
        if self.is_midi():
            if midi_path:
                try:
                    self.midi_targets = load_midi_melody(midi_path)
                    self.midi_file = midi_path.replace("\\", "/").split("/")[-1]
                except Exception as e:
                    self.status = f"Couldn't load MIDI: {e}"
                    self.midi_targets = None
                self._changed()
            if not self.midi_targets:
                self.choice = 0
        return self.choice

    def build_targets(self):
        if self.is_midi() and self.midi_targets:
            return [(s, d, n + self.transpose) for s, d, n in self.midi_targets]
        name, intervals, seconds = PATTERNS[min(self.choice, len(PATTERNS) - 1)]
        return pattern_targets(intervals, seconds, self.start_midi)

    def exercise_name(self):
        if self.is_midi() and self.midi_file:
            return self.midi_file
        return f"{self.choice_names()[self.choice]} from {midi_name(self.start_midi)}"

    # --- RUNNING ---

    def state(self):
        """{"running", "status", "cents_text", "cents_color", "targets"}."""
        return {"running": self.running, "status": self.status, "cents_text": self.cents_text,
                "cents_color": self.cents_color, "targets": self.targets if self.running else self.build_targets()}

    def _changed(self):
        self.events.emit("exercise_changed", self.state())

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.engine.is_capturing():
            self.status = "Stop the current recording first."
            self._changed()
            return False
        self.targets = self.build_targets()
        self.engine.start_take()
        self.latency = 0.0
        self.recent = []
        if self.guide:
            sd.play(guide_audio(self.targets), SAMPLE_RATE)
            self.latency = output_latency()
        self.t0 = self.clock()
        self.status = "Count-in…"
        self._set_running(True)
        return True

    def stop(self, finished=False):
        sd.stop()
        audio = self.engine.stop_take() if self.engine.is_capturing() else np.zeros(0)
        self.status = "Scoring…" if finished else "Stopped."
        self._set_running(False)
        if finished:
            self.job = self.runner.submit("exercise", "Scoring the exercise", analyze_exercise_take, audio,
                                          self.targets, self.exercise_name(), self.latency, on_done=self._scored)

    def _set_running(self, running):
        self.running = running
        self._changed()
        self.events.emit("practice", running)

    def now(self):
        return self.clock() - self.t0 if self.running else None

    def current_target(self):
        """MIDI note the singer should be on right now, or None."""
        return current_target(self.targets, self.now()) if self.running else None

    def perceived_error(self, now, f0, target):
        """Perceived cents from `target` (octave-folded) over the last PERCEIVED_SECONDS, or NaN."""
        cents = np.nan
        if target is not None and not np.isnan(f0):
            cents = 100 * (float(hz_to_midi(f0)) - target)
            cents -= 1200 * np.round(cents / 1200)
        self.recent = [(t, c) for t, c in self.recent if now - t <= PERCEIVED_SECONDS] + [(now, cents)]
        return perceived_cents([c for _, c in self.recent])

    def on_frame(self, frame):
        """Follow one live frame. Returns None when not running, else {"now", "midi" (the voice folded onto
        the target's octave, NaN unvoiced), "color" (tuning_color of the perceived error), "current"
        ((start, duration, midi) of the bar under the playhead, or None), "finished", and state()'s keys}.
        Finishing the exercise stops it and scores the take."""
        if not self.running:
            return None
        now = self.clock() - self.t0
        f0 = frame["f0"]
        # The singer follows what they hear, which the output device delays.
        target = current_target(self.targets, now - self.latency)
        cents = self.perceived_error(now, f0, target)
        color = tuning_color(cents)
        if target is not None and color is not None:
            self.cents_text, self.cents_color = f"{midi_name(target)} {cents:+.0f}¢", color
        elif target is not None:
            self.cents_text, self.cents_color = midi_name(target), None
        self.status = f"Count-in… {COUNT_IN_BEATS - int(now / BEAT_SECONDS)}" if now < LEAD_IN else "Sing!"
        current = next(((s, d, m) for s, d, m in self.targets if s <= now < s + d), None)
        view = {"now": now, "midi": fold_to_target(f0, current_target(self.targets, now)), "color": color,
                "current": current, "finished": False}
        if now > max(s + d for s, d, _ in self.targets) + TAIL_SECONDS:
            view["finished"] = True
            self.stop(finished=True)
        return self.state() | view

    def _scored(self, result):
        self.job = None
        if "error" in result:
            self.status = result["error"]
            self._changed()
            return
        score = result["exercise"]
        if score["notes_sung"]:
            self.status = (f"{score['notes_in_tune']}/{score['notes_total']} notes in tune "
                           f"(±{IN_TUNE_CENTS}¢), {score['notes_close']} close (±{CLOSE_CENTS}¢), "
                           f"average error {score['mean_abs_cents']:.0f}¢ · {score['timing']}.")
        else:
            self.status = "No sung notes detected."
        self.cents_text, self.cents_color = "—", None
        self.result = result
        self._changed()
        self.events.emit("exercise_scored", result)
