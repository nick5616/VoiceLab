"""The UI lab (phase 11 spec 01): the pure pieces (PNG, colours, vocabulary, the truth and latency evals, the
ratchet), the eval of the evals (each check catches its seeded defect in a tiny Qt fixture and passes the clean
twin), the reachability driver, and a fast smoke run of the real app that fails on any finding not in
tests/uilab_baseline/."""
import math
import os
import sys
import types

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]

import synth  # noqa: E402
from tools.uilab import colors, evals, png, score, vocab  # noqa: E402

SMOKE_SECONDS = 240             # generous: the spec asks ~60 s, and a cold cache of the real takes takes longer


# --- pure pieces ---

def test_synth_is_deterministic_and_matches_the_app_rate():
    import analysis
    assert synth.SR == analysis.SAMPLE_RATE
    a, b = synth.speech_like(150, 2.0, seed=3), synth.speech_like(150, 2.0, seed=3)
    assert np.array_equal(a, b) and len(a) == 2 * synth.SR
    rms = np.sqrt(np.mean(a[np.abs(a) > 1e-9] ** 2))
    assert abs(20 * np.log10(rms) - synth.VOICE_DBFS) < 0.5
    spans = synth.voiced_spans(synth.with_silence(synth.steady_track(150, 1.0), before=0.5))
    assert len(spans) == 1 and abs(spans[0][0] - 0.5) < 1e-3 and abs(spans[0][1] - 1.5) < 1e-3


def test_synth_pitch_is_what_the_analysis_hears():
    from engine import UPDATE_INTERVAL, LiveEngine
    engine = LiveEngine()
    audio = synth.steady(185, 1.5, synth.NEUTRAL_FORMANTS, level_dbfs=-30)
    hop = int(UPDATE_INTERVAL * synth.SR)
    f0 = []
    for i in range(0, len(audio) - hop + 1, hop):
        engine.feed(audio[i:i + hop])
        f0.append(engine._worker.frame()["f0"])
    voiced = [v for v in f0[20:] if not math.isnan(v)]
    assert voiced and abs(np.median(voiced) - 185) < 185 * 0.01


def test_png_round_trip_and_filters():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (17, 23, 3), dtype=np.uint8)
    assert np.array_equal(png.decode_png(png.encode_png(img)), img)
    rgba = rng.integers(0, 255, (5, 7, 4), dtype=np.uint8)
    assert np.array_equal(png.decode_png(png.encode_png(rgba)), rgba)
    # a PNG with Sub, Up, Average and Paeth rows (as other writers make) decodes the same
    import struct
    import zlib
    h, w, c = img.shape
    rows, prev = [], np.zeros(w * c, dtype=np.int32)
    for y in range(h):
        line = img[y].reshape(-1).astype(np.int32)
        kind = y % 5
        left = np.concatenate([np.zeros(c, dtype=np.int32), line[:-c]])
        upleft = np.concatenate([np.zeros(c, dtype=np.int32), prev[:-c]])
        if kind == 1:
            pred = left
        elif kind == 2:
            pred = prev
        elif kind == 3:
            pred = (left + prev) // 2
        elif kind == 4:
            pred = np.array([png._paeth(a, b, cc) for a, b, cc in zip(left, prev, upleft)])
        else:
            pred = np.zeros_like(line)
        rows.append(bytes([kind]) + ((line - pred) % 256).astype(np.uint8).tobytes())
        prev = line
    header = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    blob = png.SIGNATURE + png._chunk(b"IHDR", header) + png._chunk(b"IDAT", zlib.compress(b"".join(rows))) + \
        png._chunk(b"IEND", b"")
    assert np.array_equal(png.decode_png(blob), img)


def test_contrast_and_sampling():
    assert abs(colors.contrast("#000000", "#ffffff") - 21) < 0.01
    assert abs(colors.contrast("#777777", "#777777") - 1) < 1e-9
    region = np.zeros((20, 40, 3), dtype=np.uint8) + 40
    region[8:12, 5:30] = 230                        # a stroke of light ink on a dark background
    s = colors.sample(region)
    assert s["bg"] == "#282828" and s["contrast"] > 10 and s["solid"]
    s = colors.sample(region, declared="#e6e6e6")
    assert s["declared_drawn"]


def test_vocabulary_parsers():
    assert vocab.jargon("Resonance ΔF 1020 Hz") == ["ΔF"]
    assert vocab.jargon("+12¢ from A") == ["¢ without words"]
    assert vocab.jargon("+12¢ (cents) from A") == []
    assert vocab.jargon("4.3 st") == ["st without 'semitones'"] and vocab.jargon("4.3 semitones") == []
    assert vocab.hz_values("A3 · 220 Hz, then −5 Hz") == [220.0, -5.0]
    assert vocab.unbalanced("Microphone (USB PnP Audio Devic") and not vocab.unbalanced("Mic (USB)")
    assert ("overlap", "overlap / androgynous") in vocab.zone_wordings("sits in the overlap / androgynous zone")


def test_ratchet_keys_fold_take_uids_and_list_positions():
    f = {"check": "truncation", "screen": "takes", "widget_path": "takes/cards.20261018T110405-e88102/tips.3/label~2"}
    assert evals.finding_key(f) == "truncation|takes|takes/cards.*/tips.*/label"
    assert evals.group_of({"widget_path": "top/mic", "screen": "takes"}) == "shell"
    assert evals.group_of({"widget_path": "dashboard/latest/summary", "screen": "dashboard"}) == "takes"
    assert evals.group_of({"widget_path": "dashboard/live/note_label", "screen": "dashboard"}) == "live"
    assert evals.group_of({"widget_path": "practice/read/start_btn", "screen": "practice"}) == "practice"
    findings = [dict(f, severity="warn", group="takes", key=evals.finding_key(f), message="m", scenario="s",
                     size="1x1", theme="dark")]
    base = {"takes": score.baseline_entries(findings, "takes")}
    assert score.new_findings(findings, base) == []
    other = dict(findings[0], key="overlap|takes|x", check="overlap")
    assert score.new_findings(findings + [other], base) == [other]


def _frames(times, hz):
    return [{"type": "frame", "t": t, "readout": f"{h:.0f} Hz" if h else "—"} for t, h in zip(times, hz)]


def test_truth_eval_catches_a_wrong_readout_and_passes_a_right_one():
    step = 0.01
    track = [None] * 50 + [150.0] * 200
    beat = {"name": "01-steady", "truth": {"kind": "steady", "f0": 150, "track": track, "track_step": step,
                                           "voiced": [(0.5, 2.5)], "beat_start": 0.0, "beat_end": 2.5}}
    times = [i * 0.025 for i in range(1, 101)]
    good = _frames(times, [None if t < 0.6 else 150.0 for t in times])
    found, m = evals.check_truth("s", beat, good)
    assert found == [] and m["median_error_pct"] == 0.0 and m["settle_s"] < evals.SETTLE_BUDGET_S
    bad = _frames(times, [None if t < 0.6 else 161.0 for t in times])     # 7 % high
    found, m = evals.check_truth("s", beat, bad)
    assert [f["check"] for f in found] == ["truth", "settle"]
    octave = _frames(times, [None if t < 0.6 else (300.0 if int(t * 10) % 3 == 0 else 150.0) for t in times])
    found, m = evals.check_truth("s", beat, octave)
    assert any("octave" in f["message"] for f in found)


def test_live_vs_take_eval():
    beat = {"name": "01-replay", "truth": {"take": "t1", "beat_start": 0.0, "beat_end": 2.0},
            "result": {"fed_seconds": 2.0}}
    frames = _frames([0.5, 1.0, 1.5], [150, 152, 151])
    sidecar = {"pitch": 151.0, "tracks": {"times": [0.5, 1.0, 1.5, 9.0], "f0": [150, 151, 152, 300]}}
    found, m = evals.check_live_vs_take("s", beat, frames, sidecar)
    assert found == [] and abs(m["track_diff_st"]) < 0.1
    sidecar["tracks"]["f0"] = [200, 201, 202, 300]                       # live reads 5 semitones under
    found, m = evals.check_live_vs_take("s", beat, frames, sidecar)
    assert found and found[0]["severity"] == "error"


def test_latency_eval():
    events = [{"type": "frame", "ui_ms": 2.0}] * 20 + [{"type": "frame", "ui_ms": 40.0}] * 5
    m = evals.latency_metrics(events, [])
    assert [f["check"] for f in evals.check_latency("s", m)] == ["latency"]
    m = evals.latency_metrics([{"type": "frame", "ui_ms": 2.0}] * 20, [])
    assert evals.check_latency("s", m) == []


def test_exceptions_in_slots_become_findings():
    events = [{"type": "exception", "error": "RuntimeError: wrapped C/C++ object of type _Icon has been deleted",
               "where": "take_card.py:208", "code": "theme.changed.connect(lambda *_: self.update())", "t": 1.0}] * 3
    found = evals.check_exceptions("s", events)
    assert len(found) == 1 and found[0]["evidence"]["count"] == 3
    assert evals.group_of(found[0]) == "takes"
    assert evals.check_exceptions("s", [{"type": "frame"}]) == []


def test_distinguish_eval():
    rec = {"path": "action_bar/record_btn", "kind": "record_button", "visible_rect": [0, 0, 10, 10],
           "rect": [0, 0, 10, 10]}
    a = evals.Capture("s", "1", "dark", (20, 20), "a", {"widgets": [rec]}, {"record_state": "idle"})
    b = evals.Capture("s", "2", "dark", (20, 20), "b", {"widgets": [rec]}, {"record_state": "recording"})
    same = np.zeros((20, 20, 3), dtype=np.uint8)
    assert [f["check"] for f in evals.check_distinguish([(a, b, same, same)])] == ["distinguish"]
    differ = same.copy()
    differ[:10, :10] = 200
    assert evals.check_distinguish([(a, b, same, differ)]) == []


# --- the eval of the evals: every seeded defect is caught, every clean twin passes ---

SEEDED_CHECKS = {"truncation": "truncation", "overlap": "overlap", "contrast": "contrast",
                 "consistency": "consistency", "unlabelled_pair": "unlabelled_pair", "state:spinner": "state",
                 "state:nothing_running": "state", "jargon": "jargon", "fold": "fold", "small_target": "small_target"}


@pytest.mark.parametrize("name", sorted(SEEDED_CHECKS))
def test_each_check_catches_its_seeded_defect(lab_env, name):
    from ui.lab import fixtures
    check = SEEDED_CHECKS[name]
    caught = [f for f in evals.check_capture(fixtures.run(name)) if f["check"] == check and f["severity"] != "info"]
    clean = [f for f in evals.check_capture(fixtures.run(name, clean=True)) if f["check"] == check
             and f["severity"] != "info"]
    assert caught, f"{check} missed its seeded defect ({name})"
    assert clean == [], f"{check} fired on the clean fixture: {[f['message'] for f in clean]}"


def test_a_dead_end_feature_is_a_finding(lab_env):
    from ui.lab import fixtures, reach
    for clean in (False, True):
        window, _, _ = fixtures.dead_end(clean)
        window.show()
        session = types.SimpleNamespace(window=window, advance=lambda *a, **k: None)
        result = reach.run_feature(session, fixtures.DEAD_END_FEATURE)
        window.close()
        found, table = evals.check_reach("fixture", [{"name": "01", "do": "reach", "result": result}])
        assert table[0]["clicks"] == 1
        assert bool(found) is (not clean), result


# --- in-app observability ---

def test_trace_records_a_session_the_evals_can_read(make_window, tmp_path):
    import main
    from engine import UPDATE_INTERVAL
    from tools.uilab.__main__ import evaluate
    from tools.uilab.runio import read_json, read_jsonl
    window = make_window()
    window.show()
    main.observe(window, ["main.py", "--trace", str(tmp_path / "trace")])
    tracer = window.tracer
    audio = synth.steady(185, 1.0, synth.NEUTRAL_FORMANTS, level_dbfs=-30)
    hop = int(UPDATE_INTERVAL * synth.SR)
    for i in range(0, len(audio) - hop + 1, hop):
        window.engine.feed(audio[i:i + hop])
        window.engine.publish(window.engine._worker.frame())
    window.core._status("Couldn't save the take: disk full", "error")
    tracer.shoot("test")
    tracer.close()
    session = tmp_path / "trace" / "session"
    frames = read_jsonl(session / "frames.jsonl")
    events = read_jsonl(session / "events.jsonl")
    assert len(frames) == len(audio) // hop and "f0" in frames[0]
    assert any(e.get("type") == "frame" and e.get("readout") for e in events)
    assert any(e.get("name") == "status" for e in events)
    assert read_json(session / "beats.json")["beats"][0]["captures"]
    findings, card, index = evaluate(tmp_path / "trace")
    assert index.exists() and card["latency"]["frame_ui_ms"]["n"] == len(frames)


def test_inspector_panel_opens_with_the_shortcut(make_window):
    import main
    window = make_window()
    window.show()
    main.observe(window, ["main.py"])
    window.shortcuts["lab_inspector"].activated.emit()
    panel = window.lab_inspector
    assert panel.isVisible()
    window.core._status("Loaded", "info")
    panel.refresh()
    assert "snapshot" in panel.pages["Live"].toPlainText()
    assert "status" in panel.pages["Events"].toPlainText()
    window.shortcuts["lab_inspector"].activated.emit()
    assert not panel.isVisible()


# --- the smoke run of the real app ---

@pytest.mark.skipif(not os.path.isdir(r"C:\Users\nickb\VoiceLab"), reason="needs the user's takes to copy")
def test_smoke_run_has_no_findings_outside_the_baseline(lab_env, tmp_path):
    import time

    from ui.lab import scenarios
    from ui.lab.__main__ import run_lab
    started = time.perf_counter()
    findings, card, index = run_lab(scenarios.SMOKE, [scenarios.SMOKE_SIZE], [scenarios.SMOKE_THEME], tmp_path / "run",
                                    log=lambda *a: None)
    elapsed = time.perf_counter() - started
    assert index.exists() and (tmp_path / "run" / "scorecard.json").exists()
    assert not [f for f in findings if f["check"] == "capture"], "a beat failed to run"
    steady = [m for m in card["truth"]["beats"] if m["kind"] == "steady"]
    assert steady and all(m["median_error_pct"] <= 100 * evals.TRUTH_TOLERANCE for m in steady)
    fresh = score.new_findings(findings, score.load_baselines())
    keys = sorted({f["key"] for f in fresh})
    assert keys == [], "findings not in tests/uilab_baseline (fix them, or justify and run python -m ui.lab " \
                       "baseline --group <g>):\n" + "\n".join(keys)
    assert elapsed < SMOKE_SECONDS
