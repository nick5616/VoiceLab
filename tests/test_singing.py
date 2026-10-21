import os
import sys

import mido
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import exercise  # noqa: E402
import analysis  # noqa: E402
import mic_noise  # noqa: E402
import voice_map  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, melody_contour, synth_vowel, vibrato_contour  # noqa: E402
from voice_map import MIDI_LO, MIN_FRAMES, VoiceMapData, rebuild  # noqa: E402


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
    for _ in range(MIN_FRAMES):
        assert data.add(frame)
    assert not data.add(frame | {"f0": np.nan})
    assert not data.add(frame | {"level": -80.0})
    assert data.range_midi() == (57, 57)                        # A3
    assert np.nanmax(data.mean("cpp")) == 20.0
    assert np.isnan(data.mean("h1h2")).all()

    path = tmp_path / "voicemap.npz"
    data.save(path)
    loaded = VoiceMapData.load(path)
    assert loaded.counts[57 - MIDI_LO].sum() == MIN_FRAMES


# --- phase 9: what a teacher hears ---

SCALE = [60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65, 64, 62, 60]
FRAME_RATE = 100


def on_mic(voice, level=-42, seed=0):
    """The voice at a realistic level over the user's mic noise (rumble + kHz tones)."""
    return mic_noise.at_level(voice, level) + mic_noise.mic_noise(len(voice), seed)


def sing(contour, lead=0.0, mic=True):
    voice = synth_vowel(contour, MALE_FORMANTS)
    audio = np.concatenate([np.zeros(int(lead * SR)), voice])
    return analysis.analyze_take(on_mic(audio) if mic else audio)


def score(result, targets):
    return analysis.score_exercise(result["tracks"]["times"], result["tracks"]["f0"], targets)


def test_vibrato_on_target_is_in_tune_not_penalised():
    targets = [(0.0, 2.0, 57)]
    for mic in (False, True):
        result = sing(vibrato_contour(220, 2.0, rate=6.0, extent_cents=50), mic=mic)
        scored = score(result, targets)
        assert scored["time_in_tune"] >= 0.9 and scored["time_close"] >= 0.99, mic
        assert abs(scored["notes"][0]["cents"]) < 15


def test_perceived_cents_follows_vibrato_mean():
    t = np.arange(20) / FRAME_RATE
    swing = 50 * np.sin(2 * np.pi * 6 * t)
    assert abs(analysis.perceived_cents(swing + 30) - 30) < 20
    assert np.isnan(analysis.perceived_cents([np.nan, 5.0, np.nan]))
    track = analysis.perceived_track(np.r_[np.full(30, 100.0), np.full(30, -100.0)], FRAME_RATE)
    assert track[10] == 100 and track[50] == -100


def test_late_or_early_singer_scores_fully_and_lag_is_reported():
    targets = [(1.0 + i * 0.5, 0.5, m) for i, m in enumerate(SCALE)]
    for late in (-0.2, 0.0, 0.2, 0.4, 0.5):
        result = sing(melody_contour(np.array(SCALE), 0.5), lead=1.0 + late)
        scored = score(result, targets)
        assert scored["notes_in_tune"] == 15, late
        assert abs(scored["lag"] - late) < 0.05, (late, scored["lag"])
    assert analysis.timing_text(0.4) == "timing: 400 ms late"
    assert analysis.timing_text(-0.12) == "timing: 120 ms early"
    assert analysis.timing_text(0.01) == "timing: on time"


def test_wrong_notes_are_still_wrong_after_the_lag_shift():
    targets = [(0.0, 0.5, 60), (0.5, 0.5, 64), (1.0, 0.5, 67)]
    result = sing(melody_contour(np.array([60, 66, 67]), 0.5))          # middle note a tritone off
    scored = score(result, targets)
    assert scored["notes_in_tune"] == 2 and scored["notes"][1]["cents"] > 150
    assert scored["notes_close"] == 2


def vibrato_cents(rate, extent, noise, seed, seconds=3.0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * FRAME_RATE)) / FRAME_RATE
    return (6000 + extent * np.sin(2 * np.pi * rate * t + rng.uniform(0, 6.28))
            + noise * rng.standard_normal(len(t)))


def test_vibrato_grid_recovers_rate_and_extent():
    for rate in (4.5, 5.5, 6.5, 7.5):
        for extent in (20, 50, 100):
            for noise in (0, 4, 8, 12):
                stats = analysis.vibrato_stats(vibrato_cents(rate, extent, noise, 1), FRAME_RATE)
                assert stats["rate"] is not None, (rate, extent, noise)
                assert abs(stats["rate"] - rate) < 0.3, (rate, extent, noise, stats)
                assert abs(stats["extent"] - extent) / extent < 0.10, (rate, extent, noise, stats)
                assert stats["tremolo"] is None


def test_vibrato_survives_dropped_frames_but_needs_four_cycles_and_15_cents():
    y = vibrato_cents(5.5, 50, 0, 2)
    y[[40, 41, 120, 200]] = np.nan                  # isolated tracker dropouts
    stats = analysis.vibrato_stats(y, FRAME_RATE)
    assert stats["rate"] is not None and abs(stats["rate"] - 5.5) < 0.3
    assert analysis.vibrato_stats(vibrato_cents(5.5, 50, 0, 2, seconds=0.6), FRAME_RATE)["rate"] is None
    assert analysis.vibrato_stats(vibrato_cents(5.5, 8, 0, 2), FRAME_RATE)["rate"] is None
    live = analysis.vibrato_stats(vibrato_cents(5.5, 50, 0, 2, seconds=1.2), FRAME_RATE)
    assert live["rate"] is not None            # the live meter's 1.2 s window


def test_wander_is_not_vibrato_and_tremolo_is_flagged():
    from scipy.signal import butter, sosfiltfilt
    rng = np.random.default_rng(0)
    sos = butter(2, 8, fs=FRAME_RATE, output="sos")
    flagged = 0
    for _ in range(30):
        wander = sosfiltfilt(sos, rng.standard_normal(300))
        flagged += analysis.vibrato_stats(6000 + 10 * wander / wander.std(), FRAME_RATE)["rate"] is not None
    assert flagged < 3
    tremolo = analysis.vibrato_stats(vibrato_cents(10.0, 40, 3, 4), FRAME_RATE)
    assert tremolo["tremolo"] is not None and abs(tremolo["tremolo"] - 10) < 0.5 and tremolo["rate"] is None


def test_legato_scale_at_quarter_second_notes_gives_nine_segments():
    contour = melody_contour(np.array([60, 62, 64, 65, 67, 65, 64, 62, 60]), 0.25)
    for mic in (False, True):
        result = sing(contour, mic=mic)
        times = result["tracks"]["times"]
        notes = analysis.segment_notes(result["tracks"]["f0"], 1 / (times[1] - times[0]))
        assert len(notes) == 9, mic


def test_speech_like_contour_gets_no_singing_stats():
    rng = np.random.default_rng(3)
    times = np.arange(0, 6, 0.01)
    f0 = np.full(len(times), np.nan)
    t = 0.2
    while t < 5.6:                                   # syllables of 0.12-0.3 s, each gliding up to 4 semitones
        length = rng.uniform(0.12, 0.3)
        a, b = int(t * 100), int((t + length) * 100)
        f0[a:b] = rng.uniform(100, 160) * 2 ** (rng.uniform(-4, 4) / 12 * np.linspace(0, 1, b - a))
        t += length + rng.uniform(0.02, 0.15)
    assert analysis.singing_stats(times, f0) is None
    rumble = np.full(len(times), np.nan)
    rumble[100:400] = 71 * 2 ** (rng.standard_normal(300) * 8 / 1200)     # a "held note" of desk rumble
    assert analysis.singing_stats(times, rumble) is None


def test_tuning_is_judged_from_the_singers_own_tonic():
    midis = np.array([60, 62, 64, 65, 67, 65, 64, 62, 60]) + 0.4        # every note 40 cents sharp
    stats = sing(melody_contour(midis, 0.7))["singing"]
    assert stats is not None and stats["notes"] == 9
    assert abs(stats["tonic_offset"] - 40) < 10 or abs(stats["tonic_offset"] + 60) < 10
    assert stats["tuning_spread"] < 10 and stats["interval_error"] < 10


def test_singing_stats_report_vibrato():
    times = np.arange(0, 3, 0.01)
    stats = analysis.singing_stats(times, analysis.midi_to_hz(vibrato_cents(5.5, 50, 0, 5) / 100))
    assert stats["vibrato_notes"] == 1 and stats["tremolo_notes"] == 0
    assert abs(stats["vibrato_rate"] - 5.5) < 0.3


def test_timing_compensates_guide_output_latency():
    targets = exercise.pattern_targets([0, 2, 4, 5, 7], 0.5, 60)
    voice = melody_contour(np.array([60, 62, 64, 65, 67]), 0.5)
    audio = np.concatenate([np.zeros(int((exercise.LEAD_IN + 0.25) * SR)), synth_vowel(voice, MALE_FORMANTS)])
    result = exercise.analyze_exercise_take(on_mic(audio), targets, "test", latency=0.25)
    assert result["exercise"]["notes_in_tune"] == 5
    assert abs(result["exercise"]["lag"]) < 0.05 and result["exercise"]["targets"] == targets


def test_live_tuning_colour_has_three_levels_and_unknown():
    assert exercise.tuning_color(-20) == exercise.IN_TUNE_COLOR
    assert exercise.tuning_color(40) == exercise.CLOSE_COLOR
    assert exercise.tuning_color(-80) == exercise.OFF_COLOR
    assert exercise.tuning_color(np.nan) is None


def frames_from(audio):
    analyzer = analysis.FrameAnalyzer()
    step = int(0.025 * SR)
    for end in range(SR // 4, len(audio), step):
        yield analyzer.analyze(audio[max(0, end - int(0.12 * SR)):end])


def test_voice_map_ignores_mic_noise_alone_and_ghost_frequencies():
    data = VoiceMapData()
    for frame in frames_from(mic_noise.mic_noise(SR * 5, seed=3)):
        data.add(frame, "sing")
    assert data.counts.sum() == 0 and data.range_midi() is None
    frame = {"f0": 1000.0, "level": -40.0, "cpp": 20.0, "ring": -10.0, "h1h2": np.nan}
    assert not data.add(frame | {"f0": 62.0}, "sing")           # rumble ghost
    assert not data.add(frame | {"f0": 1250.0}, "sing")
    assert not data.add(frame | {"f0": 700.0}, "speak") and data.add(frame | {"f0": 300.0}, "speak")


def test_voice_map_needs_a_third_of_a_second_per_cell_and_uses_voice_band_level():
    data = VoiceMapData()
    frame = {"f0": 220.0, "level": -20.0, "band_level": -35.0, "cpp": 20.0, "ring": np.nan, "h1h2": np.nan}
    for _ in range(MIN_FRAMES - 1):
        data.add(frame)
    assert data.range_midi() is None and np.isnan(data.mean("cpp")).all()
    data.add(frame)
    assert data.range_midi() == (57, 57)
    row = int((-35 - voice_map.LEVEL_LO) // voice_map.LEVEL_STEP)
    assert data.counts[57 - MIDI_LO, row] == MIN_FRAMES                 # binned by band level, not full-band
    data.clear()
    assert data.counts.sum() == 0 and data.range_midi() is None


def test_voice_map_rebuild_keeps_sung_notes():
    take = analysis.analyze_take(on_mic(synth_vowel(220, MALE_FORMANTS, seconds=2.0)))
    data = rebuild([take, {"tracks": None}], "sing")
    lo, hi = data.range_midi()
    assert lo == hi == 57
    assert data.counts.sum() > 100 and data.has_data("cpp")
    assert rebuild([], "sing").range_midi() is None


def test_score_exposes_signed_error_and_lag_ms():
    targets = [(0.0, 0.6, 57), (0.6, 0.6, 59), (1.2, 0.6, 61)]
    result = sing(melody_contour(np.array([57, 59, 61]) - 0.3, 0.6))      # 30 cents flat
    scored = score(result, targets)
    assert -40 < scored["mean_signed_cents"] < -20
    assert scored["lag_ms"] == scored["lag"] * 1000
