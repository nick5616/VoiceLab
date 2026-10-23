"""VoiceLabCore without a UI: recording, the takes folder, re-analysis, targets, the mic and the session.
A fake engine stands in for the microphone; no audio device is opened."""
import hashlib
import json
import os
import sys

import numpy as np
import pytest
import sounddevice as sd

sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
import app_core  # noqa: E402
from analysis import FrameAnalyzer, analyze_take  # noqa: E402
from app_core import VoiceLabCore  # noqa: E402
from engine import UPDATE_INTERVAL  # noqa: E402
from events import Events  # noqa: E402
from profile_store import Profile  # noqa: E402
from take_register import finish_take  # noqa: E402
from take_store import ANALYSIS_VERSION, TakeStore, is_outdated  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402

VOICE = synth_vowel(140, MALE_FORMANTS, seconds=1.5, noise=0.05)


class FakeEngine:
    """What the core needs of the LiveEngine: take capture, the analyzer's settings, opening a stream."""
    sample_rate = SR

    def __init__(self, take=VOICE):
        self.analyzer = FrameAnalyzer(SR, UPDATE_INTERVAL)
        self.take = take
        self.capturing = False
        self.last_take_egg = None
        self.capture_info = {"path": "wasapi"}
        self.opened = []
        self.stopped = False

    def set_mode(self, mode):
        self.analyzer.mode = mode

    def is_capturing(self):
        return self.capturing

    def start_take(self):
        self.capturing = True

    def stop_take(self):
        self.capturing = False
        return self.take

    def open_stream(self, device=None, egg_channel=None):
        self.opened.append((device, egg_channel))

    def stop(self):
        self.stopped = True

    def latest_window(self):
        return np.zeros(2048)

    def recent_audio(self, seconds):
        return np.zeros(0)


class Recorder:
    """Every event the core emits, by name."""

    def __init__(self, events, names):
        self.heard = {name: [] for name in names}
        for name in names:
            events.subscribe(name, lambda *args, n=name: self.heard[n].append(args))

    def __getitem__(self, name):
        return self.heard[name]


EVENTS = ("take_added", "take_updated", "take_removed", "takes_loaded", "status", "busy", "profile_changed",
          "mode_changed", "mic_changed", "recording", "controls_changed", "progress_changed", "session_started")


@pytest.fixture
def make_core(tmp_path):
    cores = []

    def make(takes_dir=None, engine=None):
        profile = Profile(tmp_path / f"home{len(cores)}")
        if takes_dir:
            profile.set_takes_dir(takes_dir)
        core = VoiceLabCore(profile, engine=engine or FakeEngine(), events=Events())
        core.heard = Recorder(core.events, EVENTS)
        cores.append(core)
        if takes_dir:
            core.reload()
            assert core.runner.wait_all(120)
        return core
    yield make
    for core in cores:
        core.runner.wait_all(60)


def record(core):
    core.start_recording()
    core.stop_recording()
    assert core.runner.wait_all(120)
    return core.takes()[0]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_record_stop_adds_a_take(make_core):
    core = make_core()
    core.start_recording()
    assert core.is_recording and core.heard["recording"] == [(True,)] and core.heard["session_started"]
    assert core.controls()["record"] and not core.controls()["import"]
    assert core.status == ("Recording...", "recording")
    core.stop_recording()
    assert not core.controls()["record"]                       # analysing
    assert core.runner.wait_all(120)
    assert len(core.heard["take_added"]) == 1
    take = core.heard["take_added"][0][0]
    assert abs(take["pitch"] - 140) < 3 and take["mode"] == "speak" and take["uid"]
    assert take["analysis_version"] == ANALYSIS_VERSION and take["task"] == "free speech"
    assert core.takes() == [take] and core.number(take) == 1 and core.shown == 1
    assert core.status == ("Done", "info") and core.controls()["record"]
    assert any(jobs and jobs[0]["kind"] == "analysis" for (jobs,) in core.heard["busy"])
    assert core.heard["busy"][-1] == ([],)
    assert len(core.profile.read_history()) == 1                # no folder: the history log keeps it
    assert core.profile.formant_ceiling == take["formant_ceiling"]


def test_formant_ceiling_is_fixed_once(make_core):
    core = make_core()
    take = record(core)
    ceiling = core.profile.formant_ceiling
    core.note_ceiling(dict(take, formant_ceiling=ceiling + 150))
    assert core.profile.formant_ceiling == ceiling == core.engine.analyzer.formant_ceiling


def test_an_exercise_result_arriving_mid_analysis_keeps_record_locked(make_core):
    core = make_core()
    core.start_recording()
    core.stop_recording()
    core.events.emit("exercise_scored", {"error": "exercise result arrives first"})
    assert core.analyzing and not core.controls()["record"]
    core.runner.wait_all(120)
    assert core.takes() and core.controls()["record"]


def test_takes_folder_save_rename_reload_delete(make_core, tmp_path):
    folder = tmp_path / "takes"
    core = make_core(folder)
    take = record(core)
    wav = folder / take["file"]
    assert wav.exists() and wav.with_suffix(".json").exists() and core.status[0].startswith("Saved")
    assert not core.profile.read_history()                     # the folder is the record

    assert core.rename(take["uid"], "bright ee")
    assert take["file"].endswith(" bright ee.wav") and (folder / take["file"]).exists() and not wav.exists()
    assert core.heard["take_updated"][-1] == (take,)

    again = make_core(folder)                                  # a second session finds it
    loaded = again.takes()[0]
    assert loaded["title"] == "bright ee" and abs(loaded["pitch"] - 140) < 3
    assert len(again.take_audio(loaded["uid"])) == len(take["audio"])
    assert again.heard["takes_loaded"] == [(1, [])] and len(again.progress_entries()) == 1
    assert "Delete “bright ee”?" in again.delete_question(loaded["uid"])
    assert not again.delete(loaded["uid"], confirm=lambda text: False)
    assert again.delete(loaded["uid"], confirm=lambda text: True)
    assert again.heard["take_removed"] == [(loaded["uid"],)] and again.takes() == []
    assert list(folder.iterdir()) == [] and again.progress_entries() == []


def test_session_takes_are_saved_when_a_folder_is_chosen(make_core, tmp_path):
    core = make_core()
    record(core)
    assert len(core.profile.read_history()) == 1
    folder = tmp_path / "chosen"
    core.set_takes_folder(folder)
    core.runner.wait_all(120)
    assert len(core.takes()) == 1 and core.takes()[0]["file"]
    assert (folder / core.takes()[0]["file"]).exists() and core.controls()["folder"] == "chosen"
    assert core.profile.read_history() == []                   # moved, not duplicated
    assert len(core.progress_entries()) == 1


def test_older_takes_page_in(make_core, tmp_path):
    folder = tmp_path / "takes"
    store, base = TakeStore(folder), analyze_take(VOICE)
    for day in range(1, 26):
        store.save(dict(base, time=f"2026-09-{day:02d}T10:00:00"))
    core = make_core(folder)
    loading = [jobs[0] for (jobs,) in core.heard["busy"] if jobs and jobs[0]["kind"] == "load"]
    assert any(job["total"] == 25 for job in loading)          # the load reports its progress
    assert core.shown == 20 and core.older_count() == 5
    assert [core.number(t) for t in core.shown_takes()][:2] == [25, 24]
    page = core.show_older()
    assert [core.number(t) for t in page] == [5, 4, 3, 2, 1] and core.older_count() == 0
    assert len(core.progress_entries()) == 25


def old_take(folder, audio=VOICE, day=1):
    """A take in the folder as an earlier version left it: no analysis version, a ghost-wide range."""
    result = finish_take(analyze_take(audio))
    result.pop("analysis_version", None)
    result.update(time=f"2026-09-{day:02d}T10:00:00", pitch_range=47.0, mode="speak")
    store = TakeStore(folder)
    store.save(result)
    sidecar = json.loads(store.sidecar(result).read_text(encoding="utf-8"))
    sidecar.pop("analysis_version", None)
    store.sidecar(result).write_text(json.dumps(sidecar), encoding="utf-8")
    return result


def test_reanalysis_of_an_outdated_take_leaves_the_wav_unchanged(make_core, tmp_path):
    folder = tmp_path / "takes"
    take = old_take(folder)
    wav = folder / take["file"]
    before, mtime = sha(wav), wav.stat().st_mtime_ns
    core = make_core(folder)
    assert core.runner.wait_all(120) and not core.reanalysis_active()
    data = core.takes()[0]
    assert data["analysis_version"] == ANALYSIS_VERSION and not is_outdated(data)
    assert data["pitch_range"] < 20                              # the stored 47 st was ghosts
    sidecar = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["analysis_version"] == ANALYSIS_VERSION and sidecar["mode"] == "speak"
    assert sha(wav) == before and wav.stat().st_mtime_ns == mtime
    assert data["uid"] == take["uid"] and data["file"] == take["file"]
    assert "Re-analysed 1" in core.status[0] and core.status[1] == "success"
    assert any(args[0] is data for args in core.heard["take_updated"])


def test_current_takes_are_left_alone(make_core, tmp_path):
    folder = tmp_path / "takes"
    take = finish_take(analyze_take(VOICE))
    take["analysis_version"] = ANALYSIS_VERSION
    TakeStore(folder).save(take | {"time": "2026-09-01T10:00:00"})
    core = make_core(folder)
    assert not core.outdated_takes() and core.jobs["reanalysis"] is None


def test_set_target_rules(make_core):
    core = make_core()
    take = record(core)
    message = core.set_target(take["uid"])
    assert message.startswith("Targets set from take") and core.status[1] == "success"
    assert core.profile.target("pitch") and core.heard["profile_changed"]
    tracks = take["tracks"]
    assert core.profile.time_in_target("pitch", tracks["f0"]) >= 0.6
    core.profile.set_target("delta_f", [1000, 1100])
    message = core.set_target(dict(take, pitch=420.0))           # resonance can't be measured at 420 Hz
    assert core.profile.target("delta_f") == [1000, 1100] and "Not set from this take" in message
    pitch = core.profile.target("pitch")
    message = core.set_target(dict(take, snr=6.0, level=-52.0))  # too quiet and noisy
    assert "too noisy" in message and core.status[1] == "warning" and core.profile.target("pitch") == pitch


def test_baseline_and_compare_options(make_core):
    core = make_core()
    first, second = record(core), record(core)
    core.set_baseline(first["uid"])
    assert core.status == ("Baseline saved", "success")
    options = core.compare_options(second["uid"])
    assert options[0] == ("Baseline take", first) and [t for _, t in options[1:]] == [first]
    assert [t for _, t in core.compare_options(first["uid"])] == [second]


def test_the_task_of_a_take(make_core):
    core = make_core()
    core.set_reading(1)
    assert core.current_task({"mode": "speak"}) == "passage: Questions and answers"
    assert core.current_task({"mode": "sing"}) == "free singing"
    core.set_reading(None)
    assert core.current_task({"mode": "speak"}) == "free speech"


def test_session_length_and_rest_nudge(make_core):
    clock = [1000.0]
    core = make_core()
    core.clock = lambda: clock[0]
    assert core.session_minutes() == 0.0 and core.rest_nudge() is None
    core.start_recording()
    clock[0] += 10 * 60
    assert core.session_text() == "Session 10 min" and core.rest_nudge() is None
    clock[0] += 16 * 60
    nudge = core.rest_nudge()
    assert "26 minutes" in nudge and "unverified" in app_core.REST_TOOLTIP
    assert core.rest_nudge() is None                             # once per interval


def test_mode_reaches_the_engine_live_session_and_voice_map(make_core):
    core = make_core()
    core.set_mode("sing")
    assert core.profile.mode == "sing" == core.engine.analyzer.mode == core.live.mode == core.voice_map.mode
    assert core.heard["mode_changed"] == [("sing",)]
    take = record(core)
    assert take["mode"] == "sing" and take["task"] == "free singing"


def test_mic_opens_and_a_failing_egg_falls_back(make_core):
    core = make_core()
    message = core.open_mic(None)
    assert message == "Listening on the system default mic" and core.engine.opened[-1] == (None, None)
    assert core.heard["mic_changed"][-1][0]["egg_channel"] is None

    def open_stream(device=None, egg_channel=None):
        core.engine.opened.append((device, egg_channel))
        if egg_channel is not None:
            raise sd.PortAudioError("Invalid number of channels")
    core.engine.open_stream = open_stream
    message = core.open_mic(None, 1)
    assert "Invalid number of channels" in message and "Listening without the EGG" in message
    assert core.profile.egg_channel is None and not core.live.egg_on and core.status[1] == "error"
    assert core.engine.opened[-2:] == [(None, 1), (None, None)]

    def fail(device=None, egg_channel=None):
        raise sd.PortAudioError("device unavailable")
    core.engine.open_stream = fail
    assert "Couldn't open Broken mic" in core.open_mic("Broken mic")
    assert core.profile.input_device is None                     # not remembered


def test_first_mic_check_is_offered_once_and_saved(make_core):
    core = make_core()
    assert core.should_run_first_mic_check() and not core.should_run_first_mic_check()
    core.save_mic_check({"level_db": -30.0})
    assert core.profile.mic_check == {"level_db": -30.0} and core.status == ("Mic check saved", "info")


def test_voice_map_rebuilds_from_saved_takes(make_core, tmp_path):
    core = make_core()
    assert not core.rebuild_voice_map() and core.status[1] == "warning"      # no folder yet
    core = make_core(tmp_path / "takes")
    record(core)
    core.clear_voice_map()
    assert not core.rebuild_voice_map(confirm=lambda text: False)
    assert core.rebuild_voice_map(confirm=lambda text: "Replace the Voice Map" in text)
    core.runner.wait_all(120)
    assert "rebuilt" in core.status[0] and core.voice_map.data.counts.sum() > 30


def test_labelling_needs_both_classes(make_core, tmp_path):
    core = make_core(tmp_path / "takes")
    take = record(core)
    core.label_register(take["uid"], "chest")
    core.runner.wait_all(120)
    assert take["register_label"] == "chest" and core.profile.register_model is None
    assert "needs" in core.status[0].lower()
    core.label_register(take["uid"], None)
    assert "register_label" not in take


def test_report_is_guarded(make_core):
    core = make_core()
    take = record(core)
    report = core.report(take["uid"])
    assert report is None or isinstance(report, dict)


def test_close_saves_the_voice_map_and_noise_floor(make_core):
    core = make_core()
    core.engine.noise_floor = lambda: -61.5
    core.close()
    assert core.engine.stopped and core.profile.noise_floor == -61.5 and core.profile.voice_map_path.exists()


def test_mic_check_session_runs_its_phases_without_a_ui():
    import mic_check
    clock = [10.0]
    floor = 10 ** (-70 / 20) * np.random.default_rng(1).standard_normal(3 * SR)
    import mic_noise
    ah = mic_noise.at_level(synth_vowel(140, MALE_FORMANTS, seconds=3.0), -25) + floor

    class Engine(FakeEngine):
        def stop_take(self):
            self.capturing = False
            return self.takes.pop(0)
    engine = Engine()
    engine.takes = [floor, ah]
    check = mic_check.MicCheckSession(engine, seconds=3.0, lead_seconds=1.0, clock=lambda: clock[0])
    assert check.start() == 1.0 and check.phase == "lead" and "Get ready" in check.status_text()
    clock[0] += 0.4
    assert check.status_text().endswith("(1 s)")
    assert check.next() == 3.0 and check.phase == "silence" and engine.capturing
    assert check.next() == 3.0 and check.phase == "voice"
    assert check.next() is None and check.phase == "done" and check.status_text() == "Done"
    report = check.report()
    assert report["ok"] and report["advice"] == [mic_check.GOOD_ADVICE] and report["lines"]
    engine.capturing = True
    assert check.start() is None and "recording" in check.status_text()
