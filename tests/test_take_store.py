"""Takes folder: save, load, rename, delete, and files added by hand."""
import json
import os
import sys

import numpy as np
import pytest
import soundfile as sf

sys.path[:0] = [os.path.join(os.path.dirname(__file__), ".."), os.path.dirname(__file__)]
import analysis  # noqa: E402
from take_store import TakeStore, safe_title  # noqa: E402
from test_analysis import MALE_FORMANTS, SR, synth_vowel  # noqa: E402

VOICE = synth_vowel(150, MALE_FORMANTS, seconds=1.5)


@pytest.fixture(scope="module")
def take():
    result = analysis.analyze_take(VOICE)
    result["exercise"] = analysis.score_exercise(result["tracks"]["times"], result["tracks"]["f0"],
                                                 [(0.2, 1.0, 50)]) | {"name": "Long tone", "targets": [(0.2, 1.0, 50)]}
    return result


def fresh(take, **extra):
    data = {k: v for k, v in take.items() if k not in ("file", "uid", "time", "title", "dated")}
    return data | extra


def test_round_trip(tmp_path, take):
    store = TakeStore(tmp_path)
    data = fresh(take, time="2026-10-18T10:32:05", h1h2=np.nan)
    store.save(data, title="first try")
    assert data["file"] == "2026-10-18 10-32-05 first try.wav"
    assert (tmp_path / "2026-10-18 10-32-05 first try.json").exists()

    loaded, problems = store.load_all()
    assert problems == [] and len(loaded) == 1
    got = loaded[0]
    assert got["title"] == "first try" and got["uid"] == data["uid"]
    assert got["pitch"] == pytest.approx(take["pitch"])
    assert np.isnan(got["h1h2"])                       # NaN survives as null
    assert np.allclose(got["tracks"]["f0"], take["tracks"]["f0"], equal_nan=True)
    assert got["exercise"]["notes"][0]["midi"] == 50
    assert "audio" not in got                          # loaded on demand
    audio = store.audio(got)
    assert len(audio) == len(VOICE) and np.max(np.abs(audio - VOICE)) < 1e-3


def test_noise_report_round_trips_as_plain_json(tmp_path):
    import mic_noise
    noisy = mic_noise.at_level(VOICE, -42) + mic_noise.mic_noise(len(VOICE))
    result = analysis.analyze_take(np.concatenate([mic_noise.mic_noise(SR), noisy]))
    noise = result["noise"]
    store = TakeStore(tmp_path)
    store.save(fresh(result, time="2026-10-18T10:40:00"), title="noisy")
    sidecar = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert all(isinstance(v, (int, float, list, type(None))) for v in sidecar["noise"].values())
    got = store.load_all()[0][0]["noise"]
    assert got["lines"] and all(len(line) == 2 for line in got["lines"])
    for key, value in noise.items():
        if key != "lines" and value is not None and np.isfinite(value):
            assert got[key] == pytest.approx(float(value)), key


def test_names_dont_collide(tmp_path, take):
    store = TakeStore(tmp_path)
    a, b = fresh(take, time="2026-10-18T10:00:00"), fresh(take, time="2026-10-18T10:00:00")
    store.save(a)
    store.save(b)
    assert a["file"] == "2026-10-18 10-00-00.wav"
    assert b["file"] == "2026-10-18 10-00-00 (2).wav"


def test_rename_and_delete(tmp_path, take):
    store = TakeStore(tmp_path)
    data = fresh(take, time="2026-10-18T10:00:00")
    store.save(data)
    store.rename(data, 'bright / forward: "ee"')
    assert data["file"] == "2026-10-18 10-00-00 bright forward ee.wav"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "2026-10-18 10-00-00 bright forward ee.json", "2026-10-18 10-00-00 bright forward ee.wav"]
    store.rename(data, "Bright forward ee")            # case-only change keeps the name free
    assert data["file"] == "2026-10-18 10-00-00 Bright forward ee.wav"
    loaded, _ = store.load_all()
    assert loaded[0]["title"] == "Bright forward ee"

    store.delete(loaded[0])
    assert list(tmp_path.iterdir()) == []


def test_file_added_by_hand_is_analyzed(tmp_path):
    sf.write(tmp_path / "reference.wav", VOICE, 48000)  # any sample rate
    (tmp_path / "notes.txt").write_text("not audio")
    store = TakeStore(tmp_path)
    loaded, problems = store.load_all()
    assert problems == [] and len(loaded) == 1
    got = loaded[0]
    assert got["title"] == "reference" and "added to folder" in got["source"]
    assert (tmp_path / "reference.json").exists()
    # Renaming a hand-added file keeps it undated.
    store.rename(got, "soprano ref")
    assert (tmp_path / "soprano ref.wav").exists() and (tmp_path / "soprano ref.json").exists()
    # The sidecar is reused on the next load: same uid, no re-analysis.
    again, _ = store.load_all()
    assert again[0]["uid"] == got["uid"]


def test_bad_files_are_reported_not_fatal(tmp_path, take):
    store = TakeStore(tmp_path)
    store.save(fresh(take, time="2026-10-18T10:00:00"))
    (tmp_path / "broken.wav").write_bytes(b"not a wav")
    sf.write(tmp_path / "silence.wav", np.zeros(SR), SR)
    loaded, problems = store.load_all()
    assert len(loaded) == 1
    assert sorted(p.split(":")[0] for p in problems) == ["broken.wav", "silence.wav"]


def test_corrupt_or_old_sidecar_is_redone(tmp_path, take):
    store = TakeStore(tmp_path)
    data = fresh(take, time="2026-10-18T10:00:00")
    store.save(data)
    sidecar = tmp_path / data["file"].replace(".wav", ".json")
    sidecar.write_text("{oops")
    loaded, problems = store.load_all()
    assert problems == [] and loaded[0]["pitch"] == pytest.approx(take["pitch"], abs=1)
    assert json.loads(sidecar.read_text())["version"] == 1


def test_takes_load_oldest_first(tmp_path, take):
    store = TakeStore(tmp_path)
    for t in ("2026-10-20T09:00:00", "2026-10-01T09:00:00", "2026-10-10T09:00:00"):
        store.save(fresh(take, time=t))
    loaded, _ = store.load_all()
    assert [d["time"][:10] for d in loaded] == ["2026-10-01", "2026-10-10", "2026-10-20"]


def test_missing_folder(tmp_path):
    loaded, problems = TakeStore(tmp_path / "gone").load_all()
    assert loaded == [] and "not found" in problems[0]


def test_safe_title():
    assert safe_title(' a/b\\c:*?"<>|d  e. ') == "abcd e"
    assert len(safe_title("x" * 200)) == 60


def test_take_with_egg_is_stereo_and_never_mixed_down(tmp_path, take):
    from egg import synth_egg
    store = TakeStore(tmp_path)
    egg_samples = synth_egg(150, 1.5, 0.5)
    data = fresh(take, time="2026-10-18T10:32:05", egg_samples=egg_samples, cq=0.5,
                 egg={"signal_ok": True, "note": None, "cycles": 200, "polarity": 1})
    store.save(data)
    assert sf.info(str(tmp_path / data["file"])).channels == 2
    assert data["egg_channel"] == 1 and "egg_samples" not in data
    assert json.loads((tmp_path / data["file"]).with_suffix(".json").read_text())["egg_channel"] == 1

    got = store.load_all()[0][0]
    mic, egg_back = store.audio(got), store.egg(got)
    # The mic comes back alone: averaging the two channels would bury the voice in the EGG.
    assert len(mic) == len(VOICE) and np.max(np.abs(mic - VOICE)) < 1e-3
    assert np.max(np.abs(egg_back - egg_samples)) < 1e-3
    assert abs(got["cq"] - 0.5) < 1e-9 and got["egg"]["signal_ok"] is True


def test_mono_take_has_no_egg(tmp_path, take):
    store = TakeStore(tmp_path)
    data = fresh(take, time="2026-10-18T10:32:05")
    store.save(data)
    got = store.load_all()[0][0]
    assert store.egg(got) is None and "egg_channel" not in got
    assert np.isnan(got.get("cq", np.nan))
