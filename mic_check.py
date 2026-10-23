"""Mic check: record a few seconds of silence and a sustained "ah", and say what to fix.

analyze_check() and mic_advice() are plain functions; MicCheckSession drives the recording through
an engine (anything with sample_rate, capture_info, is_capturing, start_take and stop_take), so
tests can use a fake and no audio hardware is ever opened by this module. No UI toolkit.
"""
import time
from datetime import datetime

import numpy as np

import coach
import denoise

PHASE_SECONDS = 3.0         # silence, then "ah" (spec 06)
LEAD_SECONDS = 1.0          # time to settle after pressing Start, so the click isn't recorded
TRIM_SECONDS = 0.3          # dropped from the start of each recording: key click, onset of the "ah"
MIN_SNR_DB = coach.GOOD_SNR     # voice-band SNR for "good"; below this, advise
GOOD_PATHS = ("wasapi",)
WHINE_MIN_DBFS = coach.LINE_REPORT_DBFS      # a steady line this loud is a whine (user's: -73.5)
RUMBLE_SHARE = coach.RUMBLE_SHARE
QUIET_DBFS = coach.QUIET_DBFS               # voice quieter than this: move closer (user's takes: -38..-47)
CLIP_FRACTION = 1e-4        # of samples at full scale; own judgement

DISTANCE_ADVICE = ("Move to about 10-15 cm from the mic, slightly off to the side of your mouth "
                   "rather than straight in front, which also softens breath pops. Then raise the "
                   "Windows input level a little (Settings > Sound > your mic > Properties) so "
                   "your voice sits higher above the noise.")
WHINE_ADVICE = ("There is a steady whine from the mic's electronics. Try a USB port on the back of "
                "the computer, away from other USB devices, and a different cable.")
RUMBLE_ADVICE = ("The mic is picking up low rumble. Put it on something soft or on a stand rather "
                 "than straight on the desk.")
ENHANCEMENTS_ADVICE = "Turn off Windows audio enhancements for this mic if they are on."
CLIP_ADVICE = "Your voice is clipping: move back a little or lower the input level."
PATH_ADVICE = ("The mic is being read through an older Windows audio path, which adds delay and "
               "limits quality. Choosing the mic again in VoiceLab, or closing other audio "
               "apps, may let it use the faster path.")
INTERFACE_ADVICE = ("For a lasting improvement, a USB audio interface with an XLR microphone gives "
                    "a much cleaner signal than a small USB mic.")
NO_VOICE_ADVICE = ("I could not hear your \"ah\". Check that the right mic is chosen and its "
                   "input level is not muted, then try again.")
GOOD_ADVICE = "Your input looks good. Keep about this distance when you record."
INTRO = ("This takes about 8 seconds. Sit where you normally record, with the "
         "mic where you normally keep it. First you stay quiet, then you "
         "say a steady \"ah\".")
BUSY = "A recording is in progress. Finish it first, then run the mic check."
PHASE_TEXT = {"lead": "Get ready... stay quiet", "silence": "Stay quiet", "voice": "Say a steady \"ahhh\""}


def analyze_check(silence, voice, sample_rate, capture_info=None):
    """Result dict for a silence recording and a sustained-voice recording (mono arrays).
    Keys: noise_db, voice_db, snr_db (full band), snr_voice_db, rumble_share, rumble, whine,
    lines [(Hz, dBFS)], peak_db, clipped, path, host_api, native_rate, latency_ms, advice, ok, time.
    NaN where something could not be measured."""
    trim = int(TRIM_SECONDS * sample_rate)
    silence = np.asarray(silence, dtype=float)[trim:]
    voice = np.asarray(voice, dtype=float)[trim:]
    audio = np.concatenate([silence, voice])
    mask = np.concatenate([np.zeros(len(silence), bool), np.ones(len(voice), bool)])
    report = denoise.noise_report(audio, sample_rate, voiced=mask)
    info = capture_info or {}
    latency = info.get("latency")
    result = {
        "noise_db": report["noise_db"], "voice_db": report["voiced_db"], "snr_db": report["snr_db"],
        "snr_voice_db": report["snr_voice_db"], "rumble_share": report["rumble_share"],
        "rumble": bool(report["rumble_share"] > RUMBLE_SHARE),
        "lines": [(float(hz), float(db)) for hz, db in report["lines"]],
        "whine": any(db > WHINE_MIN_DBFS for _, db in report["lines"]),
        "peak_db": report["peak_db"],
        "clipped": bool(report["clip_fraction"] > CLIP_FRACTION),
        "path": info.get("path"), "host_api": info.get("host_api"), "native_rate": info.get("native_rate"),
        "latency_ms": None if latency is None else float(latency) * 1000,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    result["advice"] = mic_advice(result)
    result["ok"] = result["advice"] == [GOOD_ADVICE]
    return result


def mic_advice(result):
    """Plain advice (list of strings, in the order to try them) for an analyze_check result."""
    voice_db, snr = result["voice_db"], result["snr_voice_db"]
    if np.isnan(voice_db) or voice_db < -70:
        return [NO_VOICE_ADVICE]
    advice = []
    quiet = voice_db < QUIET_DBFS
    noisy = not np.isnan(snr) and snr < MIN_SNR_DB
    if quiet or noisy:
        advice.append(DISTANCE_ADVICE)
    if result["whine"]:
        advice.append(WHINE_ADVICE)
    if result["rumble"]:
        advice.append(RUMBLE_ADVICE)
    if result["clipped"]:
        advice.append(CLIP_ADVICE)
    if result.get("path") not in (None, *GOOD_PATHS):
        advice.append(PATH_ADVICE)
    if quiet or noisy or result["whine"]:
        advice.append(ENHANCEMENTS_ADVICE)
    if (noisy and (result["whine"] or result["rumble"] or snr < coach.POOR_SNR)):
        advice.append(INTERFACE_ADVICE)
    return advice or [GOOD_ADVICE]


def _num(value, unit, digits=0):
    return "n/a" if value is None or np.isnan(value) else f"{value:.{digits}f} {unit}"


def summary_lines(result):
    """The measured numbers as short text lines."""
    lines = [f"Noise floor: {_num(result['noise_db'], 'dBFS')}",
             f"Your voice: {_num(result['voice_db'], 'dBFS')}",
             f"Signal to noise: {_num(result['snr_voice_db'], 'dB')} in the voice band"]
    found = []
    if result["rumble"]:
        found.append(f"rumble ({100 * result['rumble_share']:.0f}% of the noise below {denoise.RUMBLE_HZ} Hz)")
    if result["whine"]:
        loudest = max(result["lines"], key=lambda line: line[1])
        found.append(f"whine at {loudest[0] / 1000:.1f} kHz ({loudest[1]:.0f} dBFS)")
    lines.append("Found: " + (", ".join(found) if found else "no rumble or whine"))
    path = result.get("path")
    if path:
        rate = result.get("native_rate")
        latency = result.get("latency_ms")
        lines.append(f"Capture: {result.get('host_api') or path}"
                     + (f", {rate / 1000:.1f} kHz" if rate else "")
                     + (f", {latency:.0f} ms latency" if latency is not None else ""))
    return lines


class MicCheckSession:
    """A 3 s silence + 3 s "ah" recording through `engine`, and what it found. start() and next() return how
    long the phase lasts (the UI calls next() then); `result` holds analyze_check's dict at the end.
    Nothing here opens an audio device."""

    def __init__(self, engine, seconds=PHASE_SECONDS, lead_seconds=LEAD_SECONDS, clock=time.monotonic):
        self.engine = engine
        self.seconds, self.lead_seconds = seconds, lead_seconds
        self.clock = clock
        self.phase = "idle"             # idle, lead, silence, voice, done
        self.result = None
        self.silence = None
        self.message = ""               # why start() refused
        self.ends_at = 0.0

    def start(self):
        """Begin the check: seconds until next() is due, or None (refused while a take is being recorded)."""
        if self.engine.is_capturing():
            self.message = BUSY
            return None
        self.result, self.silence, self.message = None, None, ""
        return self._enter("lead", self.lead_seconds)

    def _enter(self, phase, seconds):
        self.phase = phase
        self.ends_at = self.clock() + seconds
        return seconds

    def next(self):
        """End the current phase and start the next: its length in seconds, or None when the check is done."""
        if self.phase == "lead":
            self.engine.start_take()
            return self._enter("silence", self.seconds)
        if self.phase == "silence":
            self.silence = self.engine.stop_take()
            self.engine.start_take()
            return self._enter("voice", self.seconds)
        if self.phase == "voice":
            self._finish(self.engine.stop_take())
        return None

    def _finish(self, voice):
        self.phase = "done"
        self.result = analyze_check(self.silence, voice, self.engine.sample_rate,
                                    getattr(self.engine, "capture_info", None))

    def status_text(self):
        """What to do now, with the seconds left in the phase; "Done" at the end; why start() refused."""
        if self.message:
            return self.message
        if self.phase == "done":
            return "Done"
        if self.phase == "idle":
            return ""
        left = max(0.0, self.ends_at - self.clock())
        text = PHASE_TEXT.get(self.phase, "")
        return f"{text}  ({left:.0f} s)" if left >= 0.5 else text

    def report(self):
        """{"lines": the measured numbers, "advice": [str], "ok": bool}, or None before the end."""
        if self.result is None:
            return None
        return {"lines": summary_lines(self.result), "advice": self.result["advice"], "ok": self.result["ok"]}

    def cancel(self):
        """Stop mid-check: the recording is thrown away."""
        if self.phase in ("silence", "voice"):
            self.engine.stop_take()
        self.phase = "idle"
