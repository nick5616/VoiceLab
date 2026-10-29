"""Phase 11 measurement truth from the lab: synthetic voices with a known pitch (synth.py) through the
live analyzer, as the UI lab feeds them. Each test is the synthetic twin of a lab truth table row.

Run with: python -m pytest tests/test_truth_lab.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import analysis  # noqa: E402
import denoise  # noqa: E402
import synth  # noqa: E402

SR = analysis.SAMPLE_RATE
LEAD = 0.6                  # s of room noise before the voice, as the lab's voice()
HOP = int(0.025 * SR)       # the live tick
WINDOW = int(analysis.FrameAnalyzer.WINDOW_SECONDS * SR)
TWO_PERCENT = 1200 * np.log2(1.02)   # cents: the lab's 2 % median-error target
THREE_PERCENT = 1200 * np.log2(1.03)
LAG = 0.04                  # s: a live frame describes the voice a little before its end (window of 0.12 s)


def room(voice, seed=0, dbfs=synth.ROOM_DBFS):
    return synth.mix(voice, synth.room_noise(len(voice) / SR, dbfs, seed))


def live(audio, mode="speak"):
    """[(time of the frame's end, f0, frame)] as the engine produces them."""
    analyzer = analysis.FrameAnalyzer(SR, 0.025, mode=mode)
    padded = np.concatenate([np.zeros(WINDOW), audio])
    out = []
    for end in range(WINDOW, len(padded), HOP):
        frame = analyzer.analyze(padded[end - WINDOW:end])
        out.append(((end - WINDOW) / SR, frame["f0"], frame))
    return out


def errors_vs_truth(frames, truth, lag=LAG):
    """(cents error of each reading made while the voice sounds, number of voiced instants)."""
    errs, voiced = [], 0
    for t, f0, _ in frames:
        i = int((t - lag) * SR)
        if 0 <= i < len(truth) and np.isfinite(truth[i]):
            voiced += 1
            if not np.isnan(f0):
                errs.append(1200 * np.log2(f0 / truth[i]))
    return np.array(errs), voiced


def octave_share(errs):
    return float(np.mean(np.abs(np.abs(errs) - 1200) < 150)) if len(errs) else 0.0


def test_sung_scale_has_no_octave_errors_and_a_tight_median():
    track = synth.scale_track(57)           # A3 to E4: C#4 and D4 used to read an octave high
    truth = synth.with_silence(track, before=LEAD)
    audio = room(synth.synth_voice(truth, synth.NEUTRAL_FORMANTS, level_dbfs=-30))
    errs, voiced = errors_vs_truth(live(audio, "sing"), truth)
    assert len(errs) > 0.9 * voiced
    assert octave_share(errs) == 0.0
    assert np.median(np.abs(errs)) < TWO_PERCENT


def test_every_note_of_the_scale_reads_its_own_pitch():
    for midi in range(45, 70):               # A2 to A4 in semitones, held 0.5 s each
        f0 = 440.0 * 2 ** ((midi - 69) / 12)
        truth = synth.with_silence(synth.steady_track(f0, 0.8), before=LEAD)
        audio = room(synth.synth_voice(truth, synth.NEUTRAL_FORMANTS, level_dbfs=-30))
        errs, _ = errors_vs_truth(live(audio, "sing")[-12:], truth, lag=0.0)
        late = errs[-8:]
        assert len(late) > 4, midi
        assert np.all(np.abs(late) < 50), (midi, late)


def test_breathy_sung_note_is_not_an_octave_high():
    truth = synth.with_silence(synth.steady_track(262, 1.5), before=LEAD)       # C4, aspiration 0.8
    audio = room(synth.synth_voice(truth, synth.NEUTRAL_FORMANTS, breath=0.8, level_dbfs=-30))
    errs, _ = errors_vs_truth(live(audio, "sing"), truth)
    assert octave_share(errs) < 0.15          # 100 % at the old octave cost


def test_held_vibrato_still_tracks_in_sing_mode():
    track = synth.vibrato_track(220.0, 3.0, 5.5, 50)
    truth = synth.with_silence(track, before=LEAD)
    audio = room(synth.synth_voice(truth, synth.NEUTRAL_FORMANTS, level_dbfs=-30, seed=2))
    errs, voiced = errors_vs_truth(live(audio, "sing"), truth)
    assert len(errs) > 0.9 * voiced
    assert octave_share(errs) == 0.0
    assert np.median(np.abs(errs)) < 60


def speech_with(noise_parts, center=170, seed=7, level=-30):
    track = synth.speech_track(center, 6.0, seed=seed)
    truth = synth.with_silence(track, LEAD, 0.6)
    voice = synth.synth_voice(truth, synth.NEUTRAL_FORMANTS, seed=seed, level_dbfs=level)
    return synth.mix(room(voice), *[part(len(voice) / SR) for part in noise_parts]), truth


def test_desk_rumble_does_not_drag_speech_down():
    audio, truth = speech_with([lambda s: synth.rumble(s, -40.0)])
    errs, voiced = errors_vs_truth(live(audio), truth)
    assert len(errs) > 0.8 * voiced
    assert octave_share(errs) < 0.02
    assert np.median(np.abs(errs)) < THREE_PERCENT


def test_rumble_reads_the_same_as_the_clean_voice():
    clean, truth = speech_with([])
    noisy_audio, _ = speech_with([lambda s: synth.rumble(s, -40.0)])
    a, b = live(clean), live(noisy_audio)
    both = [(x[1], y[1]) for x, y in zip(a, b) if not np.isnan(x[1]) and not np.isnan(y[1])]
    assert len(both) > 100
    assert np.median([abs(1200 * np.log2(x / y)) for x, y in both]) < 10


def test_low_male_voices_survive_the_rumble_high_pass():
    for f0 in (85, 100, 120):
        truth = synth.with_silence(synth.steady_track(f0, 1.5), before=LEAD)
        audio = room(synth.synth_voice(truth, synth.MALE_FORMANTS, level_dbfs=-35))
        errs, voiced = errors_vs_truth(live(audio), truth, lag=0.0)
        assert len(errs) > 0.9 * voiced, f0
        assert np.max(np.abs(errs)) < 30, f0


def test_formants_and_levels_do_not_see_the_pitch_high_pass():
    audio = room(synth.steady(150, 1.5, synth.MALE_FORMANTS, level_dbfs=-35))
    frames = [f for _, _, f in live(audio)]
    assert any(np.isfinite(f["formants"][0]) and 350 < f["formants"][0] < 650 for f in frames)


def test_banner_ranking_names_the_problem_that_stands_out():
    def report(extra):
        audio = synth.mix(room(synth.speech_like(170, 6.0, synth.NEUTRAL_FORMANTS, seed=7)), *extra)
        return denoise.noise_report(audio, SR)
    both = ["whine", "rumble"]                # the mic's own 4 kHz line is always there
    assert denoise.rank_issues(report([synth.rumble(6.0, -40.0)]), both)[0] == "rumble"
    assert denoise.rank_issues(report([synth.whine(6.0, dbfs=-50.0)]), both)[0] == "whine"
    assert denoise.rank_issues({}, both) == both            # no measurement: the order is kept


def test_quiet_voice_over_a_quiet_floor_is_read_and_flagged():
    track = synth.speech_track(170, 5.0, seed=3)
    truth = synth.with_silence(track, 1.0, 0.5)
    voice = synth.synth_voice(truth, synth.NEUTRAL_FORMANTS, seed=3, level_dbfs=-52)
    frames = live(synth.mix(voice, synth.room_noise(len(voice) / SR, -75.0)))
    errs, voiced = errors_vs_truth(frames, truth)
    assert len(errs) > 0.85 * voiced                     # it used to read 18 %
    assert octave_share(errs) == 0.0
    assert np.median(np.abs(errs)) < 40
    flagged = [f["too_quiet"] for t, f0, f in frames if not np.isnan(f0)]
    assert np.mean(flagged) > 0.8


def test_normal_voice_is_not_flagged_too_quiet():
    audio, _ = speech_with([])
    flags = [f["too_quiet"] for _, f0, f in live(audio) if not np.isnan(f0)]
    assert np.mean(flags) < 0.05


def test_quiet_room_alone_reads_nothing():
    for floor in (-58.0, -75.0):
        frames = live(synth.room_noise(8.0, floor))
        assert np.mean([not np.isnan(f0) for _, f0, _ in frames]) < 0.01


def test_live_reads_nothing_while_the_whole_window_is_silent():
    track = synth.speech_track(170, 6.0, seed=11)
    truth = synth.with_silence(track, LEAD, 0.6)
    audio = room(synth.synth_voice(truth, synth.NEUTRAL_FORMANTS, seed=11, level_dbfs=-30))
    frames = live(audio)
    silent = [f0 for t, f0, _ in frames
              if t * SR >= WINDOW and not np.isfinite(truth[int(t * SR) - WINDOW:int(t * SR)]).any()]
    assert len(silent) > 20
    assert all(np.isnan(f0) for f0 in silent)


def test_live_and_take_agree_on_the_same_speech():
    track = synth.speech_track(165, 8.0, seed=5)
    truth = synth.with_silence(track, LEAD, 0.6)
    audio = room(synth.synth_voice(truth, synth.NEUTRAL_FORMANTS, seed=5, level_dbfs=-38))
    live_f0 = np.array([f0 for _, f0, _ in live(audio)])
    take = analysis.analyze_take(audio, SR)
    assert abs(12 * np.log2(np.nanmedian(live_f0) / take["pitch"])) < 0.5


def test_voice_map_range_ignores_ghost_columns_of_old_maps():
    import voice_map
    data = voice_map.VoiceMapData()
    for midi in range(50, 57):                       # D3 to G#3: a speech voice at 146-210 Hz
        data.counts[midi - voice_map.MIDI_LO, 20] = 3000
    for midi in list(range(36, 42)) + list(range(74, 87)):     # C2-F2 rumble ghosts, D5-D6 ghosts
        data.counts[midi - voice_map.MIDI_LO, 20] = 60           # each one a shown cell on its own
    assert data.range_midi() == (50, 56)


def test_voice_map_range_keeps_a_real_wide_range():
    import voice_map
    data = voice_map.VoiceMapData()
    for midi in range(48, 73):                       # a singer: C3 to C5, evenly used
        data.counts[midi - voice_map.MIDI_LO, 20] = 400
    assert data.range_midi() == (49, 71)             # the outer 5 % of the frames is the two end notes


def test_a_two_frame_subharmonic_is_not_reported_but_a_real_octave_leap_is():
    analyzer = analysis.FrameAnalyzer(SR, 0.025)
    analyzer._voiced, analyzer._last_f0 = True, 198.0
    analyzer._candidates.extend([198.0, 198.0, 198.0])
    assert not analyzer._confirmed(99.0)            # first frame of the halving
    assert not analyzer._confirmed(99.0)
    assert not analyzer._confirmed(99.0)            # three of five: enough for a fifth, not for an octave
    assert analyzer._confirmed(99.0)                # a fourth frame: the voice really went down
    analyzer._candidates.clear()
    analyzer._last_f0 = 198.0
    analyzer._candidates.extend([198.0, 198.0, 198.0])
    assert analyzer._confirmed(230.0)               # an ordinary step under 600 cents needs no backing


def test_a_phrase_that_starts_an_octave_down_needs_backing_but_a_new_pitch_does_not():
    analyzer = analysis.FrameAnalyzer(SR, 0.025)
    for _ in range(30):
        analyzer._end_frame(-40.0, 198.0)           # a phrase was read at 198 Hz ...
    for _ in range(6):
        analyzer._end_frame(-60.0, np.nan)          # ... then a short pause
    assert not analyzer._confirmed(99.0)            # the next phrase opens on the subharmonic
    assert not analyzer._confirmed(99.0)
    assert not analyzer._confirmed(99.0)
    assert analyzer._confirmed(99.0)                # four frames in a row: it is real
    other = analysis.FrameAnalyzer(SR, 0.025)
    for _ in range(30):
        other._end_frame(-40.0, 198.0)
    other._end_frame(-60.0, np.nan)
    assert other._confirmed(150.0)                  # a different pitch after a pause is just a new phrase
    late = analysis.FrameAnalyzer(SR, 0.025)
    for _ in range(30):
        late._end_frame(-40.0, 198.0)
    for _ in range(analysis.LIVE_REFERENCE_FRAMES + 1):
        late._end_frame(-60.0, np.nan)
    assert late._confirmed(99.0)                    # after ten seconds of silence nothing is remembered
