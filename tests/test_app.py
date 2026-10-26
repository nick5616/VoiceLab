"""End-to-end checks of the app window (the spec 05 shell), driven without a mic or speakers.

Audio is fed straight into the LiveEngine and each live frame is analysed and
delivered in-line (engine.publish), so the tests don't depend on real time; background
analysis still runs on the core's job threads. The window is built as main.py builds it:
a VoiceLabCore with its events on the Qt main thread.

Ported from the old main window: what a take card showed is read from the take and from take_view
(the card itself is spec 06's), and the live meters are the live cards.
"""
import os
import sys
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
import sounddevice as sd  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import progress  # noqa: E402
import take_view  # noqa: E402
from analysis import midi_to_hz  # noqa: E402
from conftest import wait_for  # noqa: E402
from engine import UPDATE_INTERVAL  # noqa: E402
from shift_session import ShiftSession  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402

VOICE = synth_vowel(140, MALE_FORMANTS, seconds=1.5, noise=0.05)
HOP = int(UPDATE_INTERVAL * SR)


def silence(seconds):
    return 1e-4 * np.random.default_rng(0).standard_normal(int(seconds * SR))


def play(window, audio, on_frame=None):
    """Feed audio as the mic would, delivering a live frame every hop.
    on_frame(t) runs before each frame with the time into the audio."""
    engine = window.engine
    for i in range(0, len(audio) - HOP + 1, HOP):
        engine.feed(audio[i:i + HOP])
        if on_frame:
            on_frame(i / SR)
        engine.publish(engine.analyzer.analyze(engine.latest_window()))
    QApplication.processEvents()


def record(window, audio):
    """Record `audio` as a take; returns the take."""
    count = len(window.core.takes())
    window.toggle_recording()
    play(window, audio)
    window.toggle_recording()
    assert window.record_btn.state == "analysing"
    assert wait_for(lambda: len(window.core.takes()) > count), window.status_text()
    QApplication.processEvents()
    return window.core.takes()[0]


def record_take(window, audio):
    """Record `audio`; returns the take (a dict)."""
    count = len(window.core.takes())
    record(window, audio)
    assert len(window.core.takes()) == count + 1
    return window.core.takes()[0]


def cards(window):
    return window.dashboard.live.cards.cards


def test_live_cards_and_the_banner(make_window):
    window = make_window()
    window.core.live.text_interval = 0
    play(window, np.concatenate([VOICE, silence(1.2), VOICE, silence(1.2), VOICE]))
    assert "Hz" in cards(window)["delta_f"].text
    assert "dB" in cards(window)["cpp"].text
    assert window.core.live.input["snr"] is not None                # the pauses were heard
    assert window.banner.isHidden()
    play(window, np.clip(VOICE * 4, -1, 1)[:int(0.3 * SR)])
    assert not window.banner.isHidden() and "clipping" in window.banner.label.text()


def test_takes_folder_crud(make_window, tmp_path):
    folder = tmp_path / "takes"
    window = make_window(folder)
    take = record_take(window, np.concatenate([silence(0.3), VOICE, silence(0.5)]))
    assert window.record_btn.state == "idle"
    wav = folder / take["file"]
    assert wav.exists() and wav.with_suffix(".json").exists()
    assert not window.profile.read_history()            # the folder is the record

    assert window.core.rename(take, "bright ee")
    assert take["file"].endswith(" bright ee.wav") and (folder / take["file"]).exists()
    assert not wav.exists()

    # A second session finds it, audio included.
    again = make_window(folder)
    assert len(again.core.takes()) == 1
    loaded = again.core.takes()[0]
    assert loaded["title"] == "bright ee"
    assert abs(loaded["pitch"] - 140) < 3
    assert len(again.core.take_audio(loaded)) == len(take["audio"])
    assert len(again.core.progress_entries()) == 1

    assert again.core.delete(loaded["uid"])
    assert again.core.takes() == [] and list(folder.iterdir()) == []
    assert again.core.progress_entries() == []


def test_session_takes_are_saved_when_a_folder_is_chosen(make_window, tmp_path):
    window = make_window()
    take = record_take(window, VOICE)
    assert "not saved" in take_view.saved_state(take)[0]
    assert len(window.profile.read_history()) == 1

    folder = tmp_path / "chosen"
    window.set_takes_folder(folder)
    wait_for(lambda: not window.core.loading)
    takes = window.core.takes()
    assert len(takes) == 1 and takes[0]["file"]
    assert (folder / takes[0]["file"]).exists()
    assert window.profile.read_history() == []          # moved, not duplicated
    assert len(window.core.progress_entries()) == 1
    assert str(folder) in window.settings.folder_label.text()


def test_folder_reload_picks_up_outside_changes(make_window, tmp_path):
    import soundfile as sf
    folder = tmp_path / "takes"
    folder.mkdir()
    sf.write(folder / "teacher.wav", VOICE, SR)
    window = make_window(folder)
    assert len(window.core.takes()) == 1
    assert "added to folder" in window.core.takes()[0]["source"]
    # Imported files stay out of progress unless asked for.
    entries = window.core.progress_entries()
    assert progress.view(entries, window.profile)["charts"]["pitch"] is None
    assert progress.view(entries, window.profile, include_imported=True)["charts"]["pitch"] is not None

    (folder / "teacher.wav").unlink()
    (folder / "teacher.json").unlink()
    window.load_takes()
    wait_for(lambda: not window.core.loading)
    assert window.core.takes() == []


def test_older_takes_page_in(make_window, tmp_path):
    import analysis
    from take_store import TakeStore
    folder = tmp_path / "takes"
    store, base = TakeStore(folder), analysis.analyze_take(VOICE)
    for day in range(1, 26):
        store.save(dict(base, time=f"2026-09-{day:02d}T10:00:00"))
    window = make_window(folder)
    assert len(window.core.shown_takes()) == 20 and "5 more" in window.core.older_text()
    window.core.show_older()
    assert len(window.core.shown_takes()) == 25 and not window.core.older_count()
    assert len(window.core.progress_entries()) == 25


def test_exercise_is_scored_logged_and_charted(make_window):
    window = make_window()
    window.practice.open("exercise")
    assert window.core.mode == "sing"
    ex = window.exercise_view
    targets = ex.build_targets()
    lead = targets[0][0]
    melody = np.concatenate([np.full(int(d * SR), float(midi_to_hz(m))) for _, d, m in targets])
    audio = np.concatenate([silence(lead), synth_vowel(melody, MALE_FORMANTS), silence(1.0)])
    ex.start()
    assert window.record_btn.state == "disabled"

    def clock(t):                                        # the exercise runs on audio time
        window.core.exercise.t0 = time.perf_counter() - t
    play(window, audio, on_frame=clock)
    assert wait_for(lambda: window.core.takes()), ex.status.text()
    score = window.core.takes()[0]["exercise"]
    assert score["notes_in_tune"] >= score["notes_total"] - 1

    entry = window.profile.read_history()[-1]
    assert entry["mode"] == "sing" and entry["exercise"]["notes_total"] == score["notes_total"]
    charts = progress.view(window.core.progress_entries(), window.profile)["charts"]
    assert charts["in_tune"] is not None and charts["vowel_delta_f"] is None


def test_exercise_finishing_mid_analysis_keeps_record_locked(make_window):
    window = make_window()
    window.toggle_recording()
    play(window, VOICE)
    window.toggle_recording()                            # take analysis running
    window.core.events.emit("exercise_scored", {"error": "exercise result arrives first"})
    assert window.core.analyzing and window.record_btn.state == "analysing"
    assert wait_for(lambda: window.core.takes())
    assert wait_for(lambda: window.record_btn.state == "idle")


def test_vowel_check(make_window):
    window = make_window()
    window.practice.open("vowels")
    vc = window.vowel_check
    vowels = {"heed": (270, 2290), "hid": (390, 1990), "head": (530, 1840), "had": (660, 1720),
              "hod": (730, 1090), "hud": (640, 1190), "hood": (440, 1020), "who'd": (300, 870)}
    window.core.vowels.start()
    assert window.record_btn.state == "disabled"
    for f1, f2 in vowels.values():
        forms = [(f1, 80), (f2, 100), (2500, 120), (3500, 150), (4500, 200)]
        play(window, np.concatenate([synth_vowel(130, forms, seconds=0.5), silence(0.6)]))
    assert wait_for(lambda: vc.result is not None), vc.status.text()
    assert len(vc.result["vowels"]) >= 6
    assert window.profile.read_history()[-1]["kind"] == "vowels"
    assert vc.baseline_btn.isEnabled()


def test_shift_and_target(make_window):
    window = make_window()
    take = record_take(window, VOICE)
    core = window.core
    session = ShiftSession(take, audio=lambda: core.take_audio(take), runner=core.runner, player=core.player)
    session.set_steps({"semitones": 8})                  # +4 st
    session.render()
    assert wait_for(lambda: session.is_current())
    assert "+4.0 st" in session.summary_text() or "+3.9 st" in session.summary_text()
    assert session.can_target()
    core.set_target(session.target_analysis())
    low, high = window.profile.target("pitch")
    assert low < 140 * 2 ** (4 / 12) < high

    # A shift that can't be measured must not wipe the targets.
    session.rendered = dict(session.rendered, analysis={"error": "Not enough voiced audio"})
    assert not session.can_target() and session.target_analysis() is None
    assert window.profile.target("pitch") == [low, high]


def test_layout_fits_and_reflows(make_window):
    window = make_window()
    window.show()
    for size in ((1920, 1080), (1280, 720)):
        window.resize(*size)
        QApplication.processEvents()
        assert (window.width(), window.height()) == size
    live = window.dashboard.live
    assert live.cards.visible_keys() == ["pitch", "delta_f", "h1h2", "cpp", "pitch_range", "register"]
    window.core.set_mode("sing")
    play(window, VOICE[:HOP * 4])
    assert live.cards.visible_keys() == ["tuning", "vibrato", "register", "ring", "cpp", "h1h2"]
    assert live.cards.grid.count() == 6


def test_layout_holds_still_while_the_readouts_change(make_window, tmp_path):
    """Changing live text (note, coach line, card values, the input banner) mustn't resize anything on
    the dashboard, or the latest take and recent takes beside it jump around."""
    import analysis
    from take_store import TakeStore
    folder = tmp_path / "takes"
    store, base = TakeStore(folder), analysis.analyze_take(VOICE)
    for day in (1, 2):
        store.save(dict(base, time=f"2026-09-{day:02d}T10:00:00"))
    window = make_window(folder)
    window.core.set_baseline(window.core.takes()[0])
    window.core.set_target(window.core.takes()[0])
    glide = synth_vowel(np.geomspace(90, 700, int(3 * SR)), MALE_FORMANTS)
    audio = np.concatenate([silence(1.0), glide, silence(1.0), np.clip(VOICE * 4, -1, 1), silence(0.6)])
    for size in ((1920, 1080), (1280, 720)):
        window.resize(*size)
        window.show()
        play(window, VOICE[:HOP * 4])
        QTest.qWait(100)                                    # let the first layout settle
        d = window.dashboard

        def geometry():
            return (d.live_card.size(), d.live.stack.size(), d.right.size(), window.stack.size(),
                    [c.size() for c in d.live.cards.cards.values() if not c.isHidden()])
        before = geometry()
        for start in range(0, len(audio), SR // 2):         # check while it plays, not only after
            play(window, audio[start:start + SR // 2])
            assert geometry() == before, size


def test_note_readout_is_readable(make_window):
    window = make_window()
    label, texts = window.dashboard.live.note_label, []
    label_set = label.setText
    label.setText = lambda text: (texts.append((time.monotonic(), text)), label_set(text))
    glide = synth_vowel(np.geomspace(100, 400, int(2 * SR)), MALE_FORMANTS)
    start = time.monotonic()
    play(window, np.concatenate([glide, silence(0.2), glide]))
    elapsed = time.monotonic() - start
    assert len(texts) <= elapsed / 0.2 + 2                   # at most ~5 changes a second
    assert "—" not in [t for _, t in texts[1:]]              # a 0.2 s gap holds the note


def test_mic_that_fails_to_open(make_window, monkeypatch):
    window = make_window()
    window.mic_choice.addItem("Broken mic", "Broken mic")

    def fail(device=None, noise_floor=None):
        raise sd.PortAudioError("device unavailable")
    monkeypatch.setattr(window.engine, "open_stream", fail)
    window.mic_choice.setCurrentIndex(window.mic_choice.count() - 1)
    assert "Couldn't open Broken mic" in window.status_text()
    assert window.toasts.toasts[-1].kind == "error"
    assert window.profile.input_device is None           # not remembered


# --- REGISTER AND EGG ---

HEAD_VOICE = synth_vowel(140, MALE_FORMANTS, seconds=3.5, open_quotient=0.85, noise=0.05)
CHEST_VOICE = synth_vowel(140, MALE_FORMANTS, seconds=3.5, open_quotient=0.4, noise=0.05)


def save_scored(folder, audio, day):
    """A take in the folder with its register scored, as a recording would have it."""
    import analysis
    from take_register import finish_take
    from take_store import TakeStore
    result = finish_take(analysis.analyze_take(audio))
    TakeStore(folder).save(result | {"time": f"2026-09-{day:02d}T10:00:00"})


def play_with_egg(window, audio, egg_samples):
    """Like play(), but a two-channel input: each frame also carries the contact quotient."""
    engine = window.engine
    for i in range(0, len(audio) - HOP + 1, HOP):
        engine.feed(audio[i:i + HOP], egg_samples[i:i + HOP])
        engine.publish(engine._worker.frame())
    QApplication.processEvents()


@pytest.fixture
def two_input_window(make_window, monkeypatch):
    """A window whose mic has two inputs; opening the stream just records the EGG channel."""
    import app_core
    monkeypatch.setattr(app_core, "input_channels", lambda index: 2)
    opened = []

    def make(takes_dir=None):
        window = make_window(takes_dir)

        def open_stream(device=None, egg_channel=None, noise_floor=None):
            opened.append(egg_channel)
            window.engine.egg_channel = egg_channel
        monkeypatch.setattr(window.engine, "open_stream", open_stream)
        return window
    make.opened = opened
    return make


def test_live_register_follows_open_quotient(make_window):
    window = make_window()
    window.core.live.text_interval = 0
    card = cards(window)["register"]
    assert "(estimate)" in window.core.live.label("register")
    # Sung register: uncalibrated speech uses the speech defaults (spec 10-01), which call this
    # synthetic light voice at 140 Hz chest.
    window.core.set_mode("sing")
    play(window, VOICE[:HOP * 2])
    assert "register" in window.dashboard.live.cards.visible_keys()
    play(window, np.concatenate([silence(1.0), CHEST_VOICE[:int(1.5 * SR)]]))
    assert card.bar.value < 35 and "chest" in card.text
    play(window, np.concatenate([silence(1.0), HEAD_VOICE[:int(1.5 * SR)]]))
    assert card.bar.value > 65 and "head" in card.text
    assert [(z["id"], z["lo"], z["hi"]) for z in card.bar.zones][1] == ("mix", 35.0, 65.0)   # the mix zone


def test_recording_has_a_register_row_and_old_takes_still_load(make_window, tmp_path):
    import analysis
    from take_store import TakeStore
    folder = tmp_path / "takes"
    old = analysis.analyze_take(VOICE)                    # saved before the register existed
    TakeStore(folder).save(old | {"time": "2026-09-01T10:00:00"})
    window = make_window(folder)
    assert len(window.core.takes()) == 1
    first = window.core.takes()[0]
    assert wait_for(lambda: first.get("register")), "old take is scored lazily"
    assert "register" in first["tracks"]
    assert '"register"' in (folder / first["file"]).with_suffix(".json").read_text()

    # Sung: uncalibrated speech is scored with the speech defaults (spec 10-01), which call this
    # synthetic light voice at 140 Hz chest; the sung register still reads it as head.
    window.core.set_mode("sing")
    take = record_take(window, np.concatenate([silence(0.3), HEAD_VOICE[:int(2 * SR)], silence(0.5)]))
    reg = take["register"]
    assert reg["head_share"] > 0.7 and not reg["calibrated"]
    assert len(take["tracks"]["register"]) == len(take["tracks"]["times"])
    row = take_view.register_row(take, window.profile)
    assert "estimate" in row["title"] and any("head" in text for _, text in row["parts"])
    assert window.profile.read_history() == []


def test_labelled_takes_calibrate_the_register_and_persist(make_window, tmp_path):
    folder = tmp_path / "takes"
    save_scored(folder, CHEST_VOICE, 1)
    save_scored(folder, HEAD_VOICE, 2)
    window = make_window(folder)
    takes = {t["time"][:10]: t for t in window.core.takes()}
    window.core.label_register(takes["2026-09-01"], "chest")
    assert wait_for(lambda: "needs" in window.status_text().lower())
    assert window.profile.register_model is None            # one side is not enough
    window.core.label_register(takes["2026-09-02"], "head")
    assert wait_for(lambda: window.profile.register_model is not None)
    assert "calibrated to your voice" in window.status_text()
    assert window.core.live.register_model.is_calibrated()
    assert window.core.live.label("register") == "Register"
    version = window.profile.register_version
    assert wait_for(lambda: all((t.get("register") or {}).get("model_version") == version
                                for t in window.core.takes())), "shown takes are re-scored"
    assert all(t["register"]["calibrated"] for t in window.core.takes())
    title = take_view.register_row(takes["2026-09-02"], window.profile)["title"]
    assert "calibrated to your voice" in title and "labelled it head" in title
    head_json = (folder / takes["2026-09-02"]["file"]).with_suffix(".json")
    assert '"register_label": "head"' in head_json.read_text()

    again = make_window(folder)                             # a new window starts calibrated
    assert again.core.live.register_model.is_calibrated()
    assert again.profile.register_model == window.profile.register_model
    assert {t["register_label"] for t in again.core.takes()} == {"chest", "head"}
    window.core.label_register(takes["2026-09-02"], None)
    assert "register_label" not in takes["2026-09-02"]


def test_without_an_egg_there_is_no_cq(make_window, monkeypatch):
    import app_core
    monkeypatch.setattr(app_core, "input_channels", lambda index: 1)
    window = make_window()
    window.show()
    window.update_egg_choice()
    assert window.egg_choice.isHidden() and window.egg_label.isHidden()
    play(window, VOICE)
    live = window.dashboard.live
    assert "cq" not in live.cards.visible_keys() and live.egg_chip.isHidden()
    keys = [window.voice_map.metric.itemData(i) for i in range(window.voice_map.metric.count())]
    assert "cq" not in keys and "h1h2" in keys
    take = record_take(window, VOICE)
    assert "cq" not in take_view.card(take)["bar_keys"] and "egg" not in take


def test_egg_session_records_stereo_and_shows_cq(two_input_window, tmp_path):
    import soundfile as sf
    from egg import synth_egg
    folder = tmp_path / "takes"
    window = two_input_window(folder)
    assert not window.egg_choice.isHidden()
    window.egg_choice.setCurrentIndex(window.egg_choice.findData("1"))     # EGG on channel 2
    assert two_input_window.opened[-1] == 1 and window.profile.egg_channel == 1
    live = window.dashboard.live
    assert window.core.live.egg_on and not live.egg_chip.isHidden()
    assert "EGG on channel 2" in window.top.mic.label

    voice = synth_vowel(140, MALE_FORMANTS, seconds=1.5, noise=0.05)
    egg_samples = synth_egg(140, 1.5, 0.5)
    window.core.live.text_interval = 0
    window.toggle_recording()
    play_with_egg(window, voice, egg_samples)
    assert "cq" in live.cards.visible_keys() and live.cards.cards["cq"].text.startswith("0.5")
    assert abs(window.core.live.smoothed["cq"] - 0.5) < 0.05
    keys = [window.voice_map.metric.itemData(i) for i in range(window.voice_map.metric.count())]
    assert "cq" in keys
    window.toggle_recording()
    assert wait_for(lambda: window.core.takes()), window.status_text()
    take = window.core.takes()[0]
    assert abs(take["cq"] - 0.5) < 0.05 and "cq" in take_view.card(take)["bar_keys"]
    assert "cq" in take["tracks"] and take["register"]["head_share"] is not None
    wav = folder / take["file"]
    assert sf.info(str(wav)).channels == 2
    assert '"egg_channel": 1' in wav.with_suffix(".json").read_text()

    again = two_input_window(folder)                        # reloads with the EGG intact
    loaded = again.core.takes()[0]
    assert abs(loaded["cq"] - 0.5) < 0.05 and "cq" in take_view.card(loaded)["bar_keys"]
    audio = again.core.take_audio(loaded)
    assert len(again.core.store.egg(loaded)) == len(audio)
    assert np.max(np.abs(audio - voice[:len(audio)])) < 1e-3     # mic, not a mix
    assert window.profile.egg_channel == 1 and again.profile.egg_channel == 1


def test_egg_that_fails_to_open_is_dropped_with_the_error(two_input_window):
    window = two_input_window()
    calls = []

    def open_stream(device=None, egg_channel=None, noise_floor=None):
        calls.append(egg_channel)
        if egg_channel is not None:
            raise sd.PortAudioError("Invalid number of channels")
    window.engine.open_stream = open_stream
    window.egg_choice.setCurrentIndex(window.egg_choice.findData("0"))
    assert "Invalid number of channels" in window.status_text()
    assert window.egg_choice.currentData() == "none" and window.profile.egg_channel is None
    assert not window.core.live.egg_on and calls == [0, None]       # fell back to the mic alone


def test_a_dead_egg_channel_is_explained(two_input_window):
    window = two_input_window()
    window.egg_choice.setCurrentIndex(window.egg_choice.findData("1"))
    window.toggle_recording()
    play_with_egg(window, VOICE, np.zeros(len(VOICE)))
    window.toggle_recording()
    assert wait_for(lambda: window.core.takes()), window.status_text()
    assert "EGG" in window.status_text() and "flat" in window.status_text()
    assert window.toasts.toasts[-1].kind == "warning"
    take = window.core.takes()[0]
    assert "cq" not in take_view.card(take)["bar_keys"] and "cq" not in take


def test_import_of_a_stereo_file_with_an_egg_channel(make_window, tmp_path, monkeypatch):
    import isolation
    import soundfile as sf
    from egg import synth_egg
    from PyQt6.QtWidgets import QFileDialog
    voice = synth_vowel(140, MALE_FORMANTS, seconds=1.5, noise=0.05)
    path = str(tmp_path / "session.wav")
    sf.write(path, np.column_stack([voice, synth_egg(140, 1.5, 0.5)]), SR, subtype="FLOAT")
    monkeypatch.setattr(isolation, "available", lambda: False)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (path, ""))
    window = make_window()
    window.choose_egg_channel = lambda p: (True, 1)          # "channel 2 is EGG"
    window.import_audio()
    assert window.record_btn.state == "analysing"
    assert wait_for(lambda: window.core.takes()), window.status_text()
    take = window.core.takes()[0]
    assert abs(take["cq"] - 0.5) < 0.05 and "session.wav" in take["source"]
    assert abs(take["pitch"] - 140) < 3

    assert wait_for(lambda: window.core.controls()["import"])
    window.choose_egg_channel = lambda p: (True, None)       # declined: mixed to mono, no EGG
    window.import_audio()
    assert wait_for(lambda: len(window.core.takes()) == 2)
    assert "cq" not in window.core.takes()[0]

    mono = str(tmp_path / "mono.wav")
    sf.write(mono, voice, SR)
    from ui.app_window import AppWindow
    assert AppWindow.choose_egg_channel(window, mono) == (True, None)    # no question for mono


def test_live_register_reads_fry_when_the_pitch_says_so(make_window):
    window = make_window()
    play(window, np.concatenate([silence(1.0), HEAD_VOICE[:int(1.0 * SR)]]))
    engine = window.engine
    for _ in range(8):                                    # frames at 50 Hz, below the fry limit
        engine.feed(VOICE[:HOP])
        frame = engine.analyzer.analyze(engine.latest_window())
        engine.publish(dict(frame, f0=50.0))
    time.sleep(0.25)                                      # live text changes at most every 0.2 s
    engine.publish(dict(frame, f0=50.0))
    assert "fry" in cards(window)["register"].text


def test_old_voice_map_loads_and_gains_cq(tmp_path):
    import voice_map
    data = voice_map.VoiceMapData()
    data.counts[5, 5] = voice_map.MIN_FRAMES
    data.sums["cpp"][5, 5], data.metric_counts["cpp"][5, 5] = 20.0 * voice_map.MIN_FRAMES, voice_map.MIN_FRAMES
    path = tmp_path / "voicemap.npz"
    np.savez(path, counts=data.counts, **{f"sum_{k}": data.sums[k] for k in ("cpp", "ring", "h1h2")},
             **{f"n_{k}": data.metric_counts[k] for k in ("cpp", "ring", "h1h2")})       # saved before CQ
    loaded = voice_map.VoiceMapData.load(path)
    assert loaded.counts[5, 5] == voice_map.MIN_FRAMES and loaded.mean("cpp")[5, 5] == 20.0
    assert not loaded.has_data("cq")
    loaded.add({"f0": 220.0, "level": -30.0, "cq": 0.45, "cpp": 20.0, "ring": -10.0, "h1h2": 2.0})
    assert loaded.has_data("cq")
