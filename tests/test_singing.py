import os
import sys

import mido
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import exercise  # noqa: E402
from voice_map import VoiceMapData, MIDI_LO  # noqa: E402


def write_midi(path, channels):
    """channels: {channel: [(start_beats, length_beats, note), ...]} at 120 bpm."""
    mid = mido.MidiFile(ticks_per_beat=480)
    for channel, notes in channels.items():
        track = mido.MidiTrack()
        events = []
        for start, length, note in notes:
            events.append((int(start * 480), "note_on", note))
            events.append((int((start + length) * 480), "note_off", note))
        events.sort(key=lambda e: (e[0], e[1] == "note_on"))
        last = 0
        for tick, kind, note in events:
            track.append(mido.Message(kind, note=note, velocity=80, channel=channel, time=tick - last))
            last = tick
        mid.tracks.append(track)
    mid.save(path)


def test_pattern_targets_follow_count_in():
    targets = exercise.pattern_targets([0, 4, 7], 0.5, 60)
    assert targets[0] == (exercise.LEAD_IN, 0.5, 60)
    assert targets[2] == (exercise.LEAD_IN + 1.0, 0.5, 67)


def test_midi_melody_picks_busiest_channel_and_top_note(tmp_path):
    path = tmp_path / "song.mid"
    write_midi(path, {
        0: [(0, 1, 60), (0, 1, 64), (1, 1, 62), (2, 2, 65)],   # chord, then melody
        1: [(0, 4, 48)],                                       # bass, fewer notes
        9: [(i * 0.5, 0.25, 36) for i in range(8)],            # drums, ignored
    })
    melody = exercise.load_midi_melody(str(path), transpose=-12)
    assert [n for _, _, n in melody] == [52, 50, 53]           # top of chord, transposed
    assert abs(melody[0][0] - exercise.LEAD_IN) < 1e-6
    assert abs(melody[1][0] - melody[0][0] - 0.5) < 1e-6       # one beat at 120 bpm
    assert abs(melody[2][1] - 1.0) < 1e-6


def test_guide_audio_length_and_level():
    targets = exercise.pattern_targets([0, 2], 0.5, 60)
    audio = exercise.guide_audio(targets)
    assert len(audio) / exercise.SAMPLE_RATE > exercise.LEAD_IN + 1.0
    assert 0.05 < np.abs(audio).max() <= 1.0


def test_current_target():
    targets = [(2.0, 0.5, 60), (2.5, 0.5, 62)]
    assert exercise.current_target(targets, 1.0) is None
    assert exercise.current_target(targets, 2.6) == 62


def test_voice_map_accumulates_and_round_trips(tmp_path):
    data = VoiceMapData()
    frame = {"f0": 220.0, "level": -20.0, "cpp": 20.0, "ring": -10.0, "h1h2": np.nan}
    for _ in range(3):
        assert data.add(frame)
    assert not data.add(frame | {"f0": np.nan})
    assert not data.add(frame | {"level": -80.0})
    assert data.range_midi() == (57, 57)                        # A3
    assert np.nanmax(data.mean("cpp")) == 20.0
    assert np.isnan(data.mean("h1h2")).all()

    path = tmp_path / "voicemap.npz"
    data.save(path)
    loaded = VoiceMapData.load(path)
    assert loaded.counts[57 - MIDI_LO].sum() == 3
