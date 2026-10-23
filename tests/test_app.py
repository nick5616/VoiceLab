"""End-to-end checks of the app window, driven without a mic or speakers.

Audio is fed straight into the LiveEngine and each live frame is analysed and
delivered in-line (engine.publish), so the tests don't depend on real time; background
analysis still runs on the core's job threads. The window is built as main.py builds it:
a VoiceLabCore with its events on the Qt main thread.
"""
import os
import sys
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
import sounddevice as sd  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from analysis import midi_to_hz  # noqa: E402
from conftest import wait_for  # noqa: E402
from engine import UPDATE_INTERVAL  # noqa: E402
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
    count = len(window.cards)
    window.toggle_recording()
    play(window, audio)
    window.toggle_recording()
    assert not window.record_btn.isEnabled()            # analysing
    assert wait_for(lambda: len(window.cards) > count), window.status_label.text()
    return window.cards[-1]


def test_live_meters(make_window):
    window = make_window()
    play(window, np.concatenate([VOICE, silence(1.2), VOICE, silence(1.2), VOICE]))
    live = window.live_panel
    assert "Hz" in live.meters["delta_f"].title.text()
    assert "dB" in live.meters["cpp"].title.text()
    assert live.noise_badge.text() != "SNR —"                  # the badge has read the pauses
    assert live.clip_badge.isHidden()
    play(window, np.clip(VOICE * 4, -1, 1)[:int(0.3 * SR)])
    assert not live.clip_badge.isHidden()


def test_takes_folder_crud(make_window, tmp_path):
    folder = tmp_path / "takes"
    window = make_window(folder)
    card = record(window, np.concatenate([silence(0.3), VOICE, silence(0.5)]))
    assert window.record_btn.isEnabled()
    wav = folder / card.data["file"]
    assert wav.exists() and wav.with_suffix(".json").exists()
    assert not window.profile.read_history()            # the folder is the record

    card.title.setText("bright ee")
    card._title_edited()
    assert card.data["file"].endswith(" bright ee.wav") and (folder / card.data["file"]).exists()
    assert not wav.exists()

    # A second session finds it, audio included.
    again = make_window(folder)
    assert len(again.cards) == 1
    loaded = again.cards[0]
    assert loaded.data["title"] == "bright ee"
    assert abs(loaded.data["pitch"] - 140) < 3
    assert len(loaded.audio()) == len(card.data["audio"])
    assert len(again.core.progress_entries()) == 1

    again.delete_take(loaded)
    assert again.cards == [] and list(folder.iterdir()) == []
    assert again.core.progress_entries() == []


def test_session_takes_are_saved_when_a_folder_is_chosen(make_window, tmp_path):
    window = make_window()
    card = record(window, VOICE)
    assert "not saved" in card.date.text()
    assert len(window.profile.read_history()) == 1

    folder = tmp_path / "chosen"
    window.set_takes_folder(folder)
    wait_for(lambda: not window.core.loading)
    assert len(window.cards) == 1 and window.cards[0].data["file"]
    assert (folder / window.cards[0].data["file"]).exists()
    assert window.profile.read_history() == []          # moved, not duplicated
    assert len(window.core.progress_entries()) == 1


def test_folder_reload_picks_up_outside_changes(make_window, tmp_path):
    import soundfile as sf
    folder = tmp_path / "takes"
    folder.mkdir()
    sf.write(folder / "teacher.wav", VOICE, SR)
    window = make_window(folder)
    assert len(window.cards) == 1
    assert "added to folder" in window.cards[0].data["source"]
    # Imported files stay out of progress unless asked for.
    window.show_progress()
    assert window.progress_dialog.charts["pitch"].isHidden()
    window.progress_dialog.imported.setChecked(True)
    assert not window.progress_dialog.charts["pitch"].isHidden()

    (folder / "teacher.wav").unlink()
    (folder / "teacher.json").unlink()
    window.load_takes()
    wait_for(lambda: not window.core.loading)
    assert window.cards == []


def test_older_takes_page_in(make_window, tmp_path):
    import analysis
    from take_store import TakeStore
    folder = tmp_path / "takes"
    store, base = TakeStore(folder), analysis.analyze_take(VOICE)
    for day in range(1, 26):
        store.save(dict(base, time=f"2026-09-{day:02d}T10:00:00"))
    window = make_window(folder)
    assert len(window.cards) == 20 and window.more_btn.isVisibleTo(window)
    assert "5 more" in window.more_btn.text()
    window.show_older_takes()
    assert len(window.cards) == 25 and not window.more_btn.isVisibleTo(window)
    assert len(window.core.progress_entries()) == 25


def test_exercise_is_scored_logged_and_charted(make_window):
    window = make_window()
    window.core.set_mode("sing")
    ex = window.exercise_view
    targets = ex.build_targets()
    lead = targets[0][0]
    melody = np.concatenate([np.full(int(d * SR), float(midi_to_hz(m))) for _, d, m in targets])
    audio = np.concatenate([silence(lead), synth_vowel(melody, MALE_FORMANTS), silence(1.0)])
    ex.start()
    assert not window.record_btn.isEnabled()

    def clock(t):                                        # the exercise runs on audio time
        window.core.exercise.t0 = time.perf_counter() - t
    play(window, audio, on_frame=clock)
    assert wait_for(lambda: window.cards), ex.status.text()
    score = window.cards[-1].data["exercise"]
    assert score["notes_in_tune"] >= score["notes_total"] - 1

    entry = window.profile.read_history()[-1]
    assert entry["mode"] == "sing" and entry["exercise"]["notes_total"] == score["notes_total"]
    window.show_progress()
    assert not window.progress_dialog.charts["in_tune"].isHidden()
    assert window.progress_dialog.charts["vowel_delta_f"].isHidden()


def test_exercise_finishing_mid_analysis_keeps_record_locked(make_window):
    window = make_window()
    window.toggle_recording()
    play(window, VOICE)
    window.toggle_recording()                            # take analysis running
    window.core.events.emit("exercise_scored", {"error": "exercise result arrives first"})
    assert window.core.analyzing and not window.record_btn.isEnabled()
    assert wait_for(lambda: window.cards)
    assert window.record_btn.isEnabled()


def test_vowel_check(make_window):
    window = make_window()
    vc = window.vowel_check
    vowels = {"heed": (270, 2290), "hid": (390, 1990), "head": (530, 1840), "had": (660, 1720),
              "hod": (730, 1090), "hud": (640, 1190), "hood": (440, 1020), "who'd": (300, 870)}
    window.core.vowels.start()
    for f1, f2 in vowels.values():
        forms = [(f1, 80), (f2, 100), (2500, 120), (3500, 150), (4500, 200)]
        play(window, np.concatenate([synth_vowel(130, forms, seconds=0.5), silence(0.6)]))
    assert wait_for(lambda: vc.result is not None), vc.status.text()
    assert len(vc.result["vowels"]) >= 6
    assert window.profile.read_history()[-1]["kind"] == "vowels"


def test_shift_and_target(make_window):
    window = make_window()
    card = record(window, VOICE)
    panel = card.shift_panel
    panel.show()
    panel.sliders["semitones"].setValue(8)               # +4 st
    assert wait_for(lambda: panel._is_current())
    assert "+4.0 st" in panel.result_label.text() or "+3.9 st" in panel.result_label.text()
    assert panel.target_btn.isEnabled()
    panel._use_as_target()
    low, high = window.profile.target("pitch")
    assert low < 140 * 2 ** (4 / 12) < high

    # A shift that can't be measured must not wipe the targets.
    panel.rendered = dict(panel.rendered, analysis={"error": "Not enough voiced audio"})
    panel._update_labels()
    assert not panel.target_btn.isEnabled()
    panel._use_as_target()
    assert window.profile.target("pitch") == [low, high]


def wheel_over(widget, steps=-1):
    """Scroll the wheel over `widget` the way Qt delivers real wheel input: to
    the widget under the pointer, then up its parents until one accepts it.
    (Hand-made events aren't propagated by Qt itself.)"""
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    target = widget.viewport() if hasattr(widget, "viewport") else widget
    while target is not None:
        pos = QPointF(target.width() / 2, target.height() / 2)
        event = QWheelEvent(pos, QPointF(target.mapToGlobal(pos.toPoint())), QPoint(0, 0),
                            QPoint(0, 120 * steps), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        if QApplication.sendEvent(target, event) and event.isAccepted():
            return target
        target = target.parentWidget()


def test_wheel_scrolls_takes_from_anywhere(make_window, tmp_path):
    import analysis
    from ui.take_card import AnalysisGraph
    from take_store import TakeStore
    folder = tmp_path / "takes"
    store, base = TakeStore(folder), analysis.analyze_take(VOICE)
    for day in range(1, 4):
        store.save(dict(base, time=f"2026-09-{day:02d}T10:00:00"))
    window = make_window(folder)
    window.resize(800, 900)
    window.show()
    QApplication.processEvents()
    bar = window.splitter.widget(1).verticalScrollBar()
    assert bar.maximum() > 0
    card = window.cards[0]
    for widget in (card.findChild(AnalysisGraph), card.shift_panel.sliders["semitones"],
                   window.live_panel.pitch_view, window.status_label):
        card.shift_panel.show()
        QApplication.processEvents()
        before = bar.value()
        wheel_over(widget)
        assert bar.value() > before, type(widget).__name__
    assert card.shift_panel.sliders["semitones"].value() == 0     # unfocused slider didn't move


def wide_width(window):
    """A width that gets the side-by-side layout (depends on the fonts)."""
    from ui import main_window
    return max(main_window.WIDE_LAYOUT_WIDTH,
               window.live_panel.minimumSizeHint().width() + main_window.TAKES_COLUMN_WIDTH) + 50


def test_layout_follows_window_width(make_window):
    from PyQt6.QtCore import Qt
    window = make_window()
    window.show()
    window.resize(wide_width(window), 900)
    QApplication.processEvents()
    assert window.splitter.orientation() == Qt.Orientation.Horizontal
    window.resize(800, 900)
    QApplication.processEvents()
    assert window.splitter.orientation() == Qt.Orientation.Vertical
    assert window.live_panel.meter_grid.columns == 2          # meters two-up at 800 px
    window.core.set_mode("sing")
    shown = [m for m in window.live_panel.meters.values() if not m.isHidden()]
    assert len(shown) == 7 and window.live_panel.meter_grid.grid.count() == 7


def test_layout_holds_still_while_the_readouts_change(make_window, tmp_path):
    """Changing live text (note, SNR, clipping, meter values) mustn't resize
    the live panel, or the take cards beside or below it jump around."""
    import analysis
    from take_store import TakeStore
    folder = tmp_path / "takes"
    store, base = TakeStore(folder), analysis.analyze_take(VOICE)
    for day in (1, 2):
        store.save(dict(base, time=f"2026-09-{day:02d}T10:00:00"))
    window = make_window(folder)
    window.set_baseline(base)                    # longest meter text: "... vs baseline ✓ on target"
    window.set_target(base)
    glide = synth_vowel(np.geomspace(90, 700, int(3 * SR)), MALE_FORMANTS)
    audio = np.concatenate([silence(1.0), glide, silence(1.0), np.clip(VOICE * 4, -1, 1), silence(0.6)])
    for size in ((wide_width(window), 900), (820, 1000)):
        window.resize(*size)
        window.show()
        QApplication.processEvents()
        geometry = lambda: ([c.size() for c in window.cards], window.live_panel.size(),  # noqa: E731
                            window.live_panel.tabs.size(), window.splitter.sizes())
        before = geometry()
        for start in range(0, len(audio), SR // 2):         # check while it plays, not only after
            play(window, audio[start:start + SR // 2])
            assert geometry() == before, size


def test_note_readout_is_readable(make_window):
    window = make_window()
    label, texts = window.live_panel.note_label, []
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
    assert "Couldn't open Broken mic" in window.status_label.text()
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
    meter = window.live_panel.meters["register"]
    assert "(estimate)" in meter.label
    play(window, np.concatenate([silence(1.0), CHEST_VOICE[:int(1.5 * SR)]]))
    assert meter.bar.position < 0.35 and "chest" in meter.title.text() and "estimate" in meter.title.text()
    play(window, np.concatenate([silence(1.0), HEAD_VOICE[:int(1.5 * SR)]]))
    assert meter.bar.position > 0.65 and "head" in meter.title.text()
    assert meter.bar.band == (0.35, 0.65)                 # the mix zone
    window.core.set_mode("sing")
    assert not meter.isHidden()


def test_recording_has_a_register_row_and_old_takes_still_load(make_window, tmp_path):
    import analysis
    from take_store import TakeStore
    folder = tmp_path / "takes"
    old = analysis.analyze_take(VOICE)                    # saved before the register existed
    TakeStore(folder).save(old | {"time": "2026-09-01T10:00:00"})
    window = make_window(folder)
    assert len(window.cards) == 1
    assert wait_for(lambda: window.cards[0].data.get("register")), "old take is scored lazily"
    assert "register" in window.cards[0].data["tracks"]
    assert '"register"' in (folder / window.cards[0].data["file"]).with_suffix(".json").read_text()

    # Sung: uncalibrated speech is scored with the speech defaults (spec 10-01), which call this
    # synthetic light voice at 140 Hz chest; the sung register still reads it as head.
    window.core.set_mode("sing")
    card = record(window, np.concatenate([silence(0.3), HEAD_VOICE[:int(2 * SR)], silence(0.5)]))
    reg = card.data["register"]
    assert reg["head_share"] > 0.7 and not reg["calibrated"]
    assert len(card.data["tracks"]["register"]) == len(card.data["tracks"]["times"])
    assert card.register_box.isVisibleTo(card) and "estimate" in card.register_title.text()
    assert "head" in card.register_text.text()
    assert window.profile.read_history() == []


def test_labelled_takes_calibrate_the_register_and_persist(make_window, tmp_path):
    folder = tmp_path / "takes"
    save_scored(folder, CHEST_VOICE, 1)
    save_scored(folder, HEAD_VOICE, 2)
    window = make_window(folder)
    cards = {c.data["time"][:10]: c for c in window.cards}
    window.label_register(cards["2026-09-01"], "chest")
    assert wait_for(lambda: "needs" in window.status_label.text().lower())
    assert window.profile.register_model is None            # one side is not enough
    window.label_register(cards["2026-09-02"], "head")
    assert wait_for(lambda: window.profile.register_model is not None)
    assert "calibrated to your voice" in window.status_label.text()
    assert window.core.live.register_model.is_calibrated()
    assert window.live_panel.meters["register"].label == "Register"
    version = window.profile.register_version
    assert wait_for(lambda: all((c.data.get("register") or {}).get("model_version") == version
                                for c in window.cards)), "shown cards are re-scored"
    assert all(c.data["register"]["calibrated"] for c in window.cards)
    assert "calibrated to your voice" in cards["2026-09-02"].register_title.text()
    assert "labelled it head" in cards["2026-09-02"].register_title.text()
    head_json = (folder / cards["2026-09-02"].data["file"]).with_suffix(".json")
    assert '"register_label": "head"' in head_json.read_text()

    again = make_window(folder)                             # a new window starts calibrated
    assert again.core.live.register_model.is_calibrated()
    assert again.profile.register_model == window.profile.register_model
    assert {c.data["register_label"] for c in again.cards} == {"chest", "head"}
    window.label_register(cards["2026-09-02"], None)
    assert "register_label" not in cards["2026-09-02"].data


def test_without_an_egg_there_is_no_cq(make_window, monkeypatch):
    import app_core
    monkeypatch.setattr(app_core, "input_channels", lambda index: 1)
    window = make_window()
    window.show()
    window.update_egg_choice()
    assert window.egg_choice.isHidden() and window.egg_label.isHidden()
    play(window, VOICE)
    live = window.live_panel
    assert live.meters["cq"].isHidden() and live.egg_badge.isHidden()
    keys = [window.voice_map.metric.itemData(i) for i in range(window.voice_map.metric.count())]
    assert "cq" not in keys and "h1h2" in keys
    card = record(window, VOICE)
    assert "cq" not in card.bars and "egg" not in card.data


def test_egg_session_records_stereo_and_shows_cq(two_input_window, tmp_path):
    import soundfile as sf
    from egg import synth_egg
    folder = tmp_path / "takes"
    window = two_input_window(folder)
    assert not window.egg_choice.isHidden()
    window.egg_choice.setCurrentIndex(window.egg_choice.findData(1))     # EGG on channel 2
    assert two_input_window.opened[-1] == 1 and window.profile.egg_channel == 1
    live = window.live_panel
    assert window.core.live.egg_on and not live.egg_badge.isHidden() and not live.meters["cq"].isHidden()

    voice = synth_vowel(140, MALE_FORMANTS, seconds=1.5, noise=0.05)
    egg_samples = synth_egg(140, 1.5, 0.5)
    window.toggle_recording()
    play_with_egg(window, voice, egg_samples)
    assert "Contact quotient" in live.meters["cq"].title.text()
    assert abs(window.core.live.smoothed["cq"] - 0.5) < 0.05
    keys = [window.voice_map.metric.itemData(i) for i in range(window.voice_map.metric.count())]
    assert "cq" in keys
    window.toggle_recording()
    assert wait_for(lambda: window.cards), window.status_label.text()
    card = window.cards[0]
    assert abs(card.data["cq"] - 0.5) < 0.05 and "cq" in card.bars
    assert "cq" in card.data["tracks"] and card.data["register"]["head_share"] is not None
    wav = folder / card.data["file"]
    assert sf.info(str(wav)).channels == 2
    assert '"egg_channel": 1' in wav.with_suffix(".json").read_text()

    again = two_input_window(folder)                        # reloads with the EGG intact
    loaded = again.cards[0]
    assert abs(loaded.data["cq"] - 0.5) < 0.05 and "cq" in loaded.bars
    assert len(again.core.store.egg(loaded.data)) == len(loaded.audio())
    assert np.max(np.abs(loaded.audio() - voice[:len(loaded.audio())])) < 1e-3     # mic, not a mix
    assert window.profile.egg_channel == 1 and again.profile.egg_channel == 1


def test_egg_that_fails_to_open_is_dropped_with_the_error(two_input_window):
    window = two_input_window()
    calls = []

    def open_stream(device=None, egg_channel=None, noise_floor=None):
        calls.append(egg_channel)
        if egg_channel is not None:
            raise sd.PortAudioError("Invalid number of channels")
    window.engine.open_stream = open_stream
    window.egg_choice.setCurrentIndex(window.egg_choice.findData(0))
    assert "Invalid number of channels" in window.status_label.text()
    assert window.egg_choice.currentData() is None and window.profile.egg_channel is None
    assert not window.core.live.egg_on and calls == [0, None]       # fell back to the mic alone


def test_a_dead_egg_channel_is_explained(two_input_window):
    window = two_input_window()
    window.egg_choice.setCurrentIndex(window.egg_choice.findData(1))
    window.toggle_recording()
    play_with_egg(window, VOICE, np.zeros(len(VOICE)))
    window.toggle_recording()
    assert wait_for(lambda: window.cards), window.status_label.text()
    assert "EGG" in window.status_label.text() and "flat" in window.status_label.text()
    assert "cq" not in window.cards[0].bars and "cq" not in window.cards[0].data


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
    assert wait_for(lambda: window.cards), window.status_label.text()
    card = window.cards[0]
    assert abs(card.data["cq"] - 0.5) < 0.05 and "session.wav" in card.data["source"]
    assert abs(card.data["pitch"] - 140) < 3

    window.choose_egg_channel = lambda p: (True, None)       # declined: mixed to mono, no EGG
    window.import_audio()
    assert wait_for(lambda: len(window.cards) == 2)
    assert "cq" not in window.cards[-1].data

    mono = str(tmp_path / "mono.wav")
    sf.write(mono, voice, SR)
    from ui.main_window import MainWindow
    assert MainWindow.choose_egg_channel(window, mono) == (True, None)    # no question for mono


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
    assert "fry" in window.live_panel.meters["register"].title.text()


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
