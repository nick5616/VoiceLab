"""Hearing a take with the voice shifted: slider steps, rendering in the background, playback and what
the shifted version measures. A UI draws the sliders and texts from ShiftSession. No UI toolkit."""
import numpy as np

from analysis import hz_to_note
from events import Events
from jobs import JobRunner
from playback import Player
from shift import NEUTRAL, SHIFT_PARAMS, is_neutral, render_and_analyze

STEPS = {"semitones": 0.5, "resonance": 1, "intonation": 5, "tempo": 5,
         "breathiness": 5, "brightness": 0.5}      # slider step of each parameter, in its unit
HINTS = {
    "semitones": "Moves pitch, keeps resonance.",
    "resonance": "Scales formants: + brighter / smaller tract, − darker / larger.",
    "intonation": "Widens or flattens pitch movement around your median.",
    "tempo": "Speeds up or slows down without changing pitch.",
    "breathiness": "+ airier, − more pressed. Weight-like.",
    "brightness": "Spectral tilt: + edgier/heavier, − softer/lighter.",
}
SIGNED = ("semitones", "resonance", "breathiness", "brightness")    # shown with a + when positive
MOVE_HINT = "Move a slider: the shifted version renders and plays when you let go."


def slider_range(key):
    """(lowest, highest, neutral) slider positions of a parameter, in steps."""
    _, lo, hi, neutral, _ = SHIFT_PARAMS[key]
    step = STEPS[key]
    return int(lo / step), int(hi / step), int(neutral / step)


def params_text(params):
    """The changed parameters, e.g. "semitones +4 st, resonance +5 %"."""
    changed = [f"{SHIFT_PARAMS[k][0].lower()} {v:+g}{SHIFT_PARAMS[k][4] and ' ' + SHIFT_PARAMS[k][4]}"
               if k not in ("intonation", "tempo") else f"{SHIFT_PARAMS[k][0].lower()} {v:g}%"
               for k, v in params.items() if v != NEUTRAL[k]]
    return ", ".join(changed)


def compare_text(original, shifted):
    """Shifted values with the change from the original take."""
    parts = []
    note, _ = hz_to_note(shifted["pitch"])
    parts.append(f"pitch {note} {shifted['pitch']:.0f} Hz "
                 f"({12 * np.log2(shifted['pitch'] / original['pitch']):+.1f} st)")
    if shifted["delta_f"] and original["delta_f"]:
        parts.append(f"ΔF {shifted['delta_f']:.0f} Hz ({shifted['delta_f'] - original['delta_f']:+.0f})")
    for key, name in (("h1h2", "fold contact"), ("cpp", "clarity"), ("ring", "ring")):
        if not np.isnan(shifted[key]) and not np.isnan(original[key]):
            parts.append(f"{name} {shifted[key]:.1f} dB ({shifted[key] - original[key]:+.1f})")
    if not np.isnan(shifted["pitch_range"]):
        parts.append(f"intonation {shifted['pitch_range']:.1f} st")
    return " · ".join(parts)


class ShiftSession:
    """One take's shift settings. set_steps() takes slider positions; render() runs in the background
    (one render at a time, redone if the sliders moved meanwhile); `message` is the text to show.
    `audio()` returns the take's samples; `on_change()` is called when the state changes."""

    def __init__(self, take, audio=None, runner=None, player=None, on_change=None, events=None):
        self.take = take
        self.audio = audio or (lambda: take["audio"])
        self.runner = runner or JobRunner(events or Events())
        self.player = player or Player()
        self.on_change = on_change or (lambda: None)
        self.steps = {key: slider_range(key)[2] for key in SHIFT_PARAMS}
        self.job = None
        self.rendered = None
        self.pending_play = False
        self.message = MOVE_HINT

    def params(self):
        return {key: position * STEPS[key] for key, position in self.steps.items()}

    def set_steps(self, steps):
        """Slider positions {key: steps}. Returns True when the shifted version needs rendering (the UI
        renders after a pause, see render())."""
        self.steps.update(steps)
        if self.neutral():
            self.message = MOVE_HINT
        return self.needs_render()

    def reset_steps(self):
        """Slider positions of the unshifted voice."""
        return {key: int(NEUTRAL[key] / STEPS[key]) for key in SHIFT_PARAMS}

    def value_labels(self):
        """{key: text} beside each slider."""
        labels = {}
        for key, value in self.params().items():
            unit = SHIFT_PARAMS[key][4]
            sign = "+" if key in SIGNED and value > 0 else ""
            labels[key] = f"{sign}{value:g} {unit}".strip()
        return labels

    def neutral(self):
        return is_neutral(self.params())

    def is_current(self):
        """True when the last render matches the sliders."""
        return self.rendered is not None and self.rendered["params"] == self.params()

    def needs_render(self):
        return not self.neutral() and not self.is_current() and self.job is None

    def can_target(self):
        """A shift that couldn't be measured has no values to target."""
        return not self.neutral() and self.is_current() and "error" not in self.rendered["analysis"]

    def play_original(self):
        self.player.sound(self.audio())
        self.message = "Playing original. " + self.summary_text()
        self.on_change()

    def play_shifted(self):
        """Play the shifted version, rendering it first if the sliders moved."""
        if self.neutral():
            self.play_original()
        elif self.is_current():
            self.player.sound(self.rendered["audio"])
            self.message = "Playing shifted. " + self.summary_text()
            self.on_change()
        else:
            self.render(play=True)

    def render(self, play=False):
        params = self.params()
        if is_neutral(params):
            return
        if self.job is not None:          # one render at a time; redo after
            self.pending_play = self.pending_play or play
            return
        self.pending_play = play
        self.message = f"Rendering {params_text(params)}…"
        self.job = self.runner.submit("shift", "Rendering the shifted voice", render_and_analyze, self.audio(), params,
                                      on_done=self._rendered)
        self.on_change()

    def _rendered(self, result):
        self.job = None
        if "error" in result:
            self.message = result["error"]
            self.on_change()
            return
        self.rendered = result
        if not self.is_current():           # sliders moved while rendering
            self.render(play=self.pending_play)
            return
        if self.pending_play:
            self.player.sound(result["audio"])
        self.message = ("Playing shifted. " if self.pending_play else "") + self.summary_text()
        self.on_change()

    def summary_text(self):
        if not self.is_current():
            return ""
        analysis = self.rendered["analysis"]
        if "error" in analysis:
            return f"Shifted audio couldn't be measured: {analysis['error']}"
        return f"Shifted ({params_text(self.rendered['params'])}) measures: {compare_text(self.take, analysis)}"

    def target_analysis(self):
        """The shifted take's analysis to use as a target, or None when there is none to use."""
        if self.rendered and "error" not in self.rendered["analysis"]:
            return self.rendered["analysis"]
        return None
