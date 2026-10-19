"""End-to-end checks of the app window, driven without a mic or speakers.

Audio is fed straight into the LiveEngine and each live frame is analysed and
delivered in-line, so the tests don't depend on real time; background analysis
still runs on its worker threads.
"""
import os
import sys
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
import sounddevice as sd  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from analysis import midi_to_hz  # noqa: E402
from engine import UPDATE_INTERVAL, LiveEngine  # noqa: E402
from profile_store import Profile  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402

VOICE = synth_vowel(140, MALE_FORMANTS, seconds=1.5, noise=0.05)
HOP = int(UPDATE_INTERVAL * SR)


def silence(seconds):
    return 1e-4 * np.random.default_rng(0).standard_normal(int(seconds * SR))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(app, tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(sd, "play", lambda *a, **k: None)
    monkeypatch.setattr(sd, "stop", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    windows = []

    def make(takes_dir=None):
        profile = Profile(tmp_path / "home")
        if takes_dir:
            profile.set_takes_dir(takes_dir)
        window = main.MainWindow(LiveEngine(), profile)
        windows.append(window)
        if takes_dir:
            wait_for(lambda: window.load_worker is None or not window.load_worker.isRunning())
        return window

    yield make
    for window in windows:
        window.close()


def wait_for(condition, timeout=60):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        QApplication.processEvents()
        if condition():
            QApplication.processEvents()
            return True
        time.sleep(0.01)
    return False


def play(window, audio, on_frame=None):
    """Feed audio as the mic would, delivering a live frame every hop.
    on_frame(t) runs before each frame with the time into the audio."""
    engine = window.engine
    for i in range(0, len(audio) - HOP + 1, HOP):
        engine.feed(audio[i:i + HOP])
        if on_frame:
            on_frame(i / SR)
        engine.frame_ready.emit(engine.analyzer.analyze(engine.latest_window()))
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
    assert live.snr_badge.text().startswith("SNR ") and live.snr_badge.text() != "SNR —"
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
    assert len(again.progress_entries()) == 1

    again.delete_take(loaded)
    assert again.cards == [] and list(folder.iterdir()) == []
    assert again.progress_entries() == []


def test_session_takes_are_saved_when_a_folder_is_chosen(make_window, tmp_path):
    window = make_window()
    card = record(window, VOICE)
    assert "not saved" in card.date.text()
    assert len(window.profile.read_history()) == 1

    folder = tmp_path / "chosen"
    window.set_takes_folder(folder)
    wait_for(lambda: not window.load_worker.isRunning())
    assert len(window.cards) == 1 and window.cards[0].data["file"]
    assert (folder / window.cards[0].data["file"]).exists()
    assert window.profile.read_history() == []          # moved, not duplicated
    assert len(window.progress_entries()) == 1


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
    wait_for(lambda: not window.load_worker.isRunning())
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
    assert len(window.progress_entries()) == 25


def test_exercise_is_scored_logged_and_charted(make_window):
    window = make_window()
    window.live_panel.set_mode("sing", user=True)
    ex = window.exercise_view
    targets = ex.build_targets()
    lead = targets[0][0]
    melody = np.concatenate([np.full(int(d * SR), float(midi_to_hz(m))) for _, d, m in targets])
    audio = np.concatenate([silence(lead), synth_vowel(melody, MALE_FORMANTS), silence(1.0)])
    ex.start()
    assert not window.record_btn.isEnabled()

    def clock(t):                                        # the exercise runs on audio time
        ex.t0 = time.perf_counter() - t
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
    window.exercise_view.take_ready.emit({"error": "exercise result arrives first"})
    assert window.analyzing and not window.record_btn.isEnabled()
    assert wait_for(lambda: window.cards)
    assert window.record_btn.isEnabled()


def test_vowel_check(make_window):
    window = make_window()
    vc = window.vowel_check
    vowels = {"heed": (270, 2290), "hid": (390, 1990), "head": (530, 1840), "had": (660, 1720),
              "hod": (730, 1090), "hud": (640, 1190), "hood": (440, 1020), "who'd": (300, 870)}
    vc.start()
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
    from take_card import AnalysisGraph
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
    import main
    return max(main.WIDE_LAYOUT_WIDTH,
               window.live_panel.minimumSizeHint().width() + main.TAKES_COLUMN_WIDTH) + 50


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
    window.live_panel.set_mode("sing", user=True)
    shown = [m for m in window.live_panel.meters.values() if not m.isHidden()]
    assert len(shown) == 6 and window.live_panel.meter_grid.grid.count() == 6


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

    def fail(device=None):
        raise sd.PortAudioError("device unavailable")
    monkeypatch.setattr(window.engine, "open_stream", fail)
    window.mic_choice.setCurrentIndex(window.mic_choice.count() - 1)
    assert "Couldn't open Broken mic" in window.status_label.text()
    assert window.profile.input_device is None           # not remembered
