"""Voice report (phase 10 spec 02): zones, honest overall placement, collapsed issues, tips."""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import report  # noqa: E402
from profile_store import Profile  # noqa: E402

STEP = 0.01             # s per frame, as analysis tracks
PHRASE_S, GAP_S = 2.5, 0.5
INSTRUCTION_WORDS = ("next take", "try ", "raise", "lower", "lift", "keep ", "settle", "push")


def make_take(f0=210.0, df=950.0, phrases=4, dip_hz=None, snr=25.0, level=-30.0, wobble=1.0,
              f2=1600.0, mode="speak", **summary):
    """A take dict shaped like analyze_take's (summary keys + tracks). Each phrase is PHRASE_S of
    voice with a gentle +/- `wobble` st pitch movement; with `dip_hz` its last 0.3 s falls there."""
    voiced = int(PHRASE_S / STEP)
    gap = int(GAP_S / STEP)
    f0_track, df_track = [], []
    for _ in range(phrases):
        t = np.arange(voiced) * STEP
        phrase = f0 * 2 ** (wobble * np.sin(2 * np.pi * t / PHRASE_S) / 12)
        if dip_hz is not None:
            phrase[-int(0.3 / STEP):] = dip_hz
        f0_track += list(phrase) + [np.nan] * gap
        df_track += list(df + 20 * np.sin(2 * np.pi * t / 1.3)) + [np.nan] * gap
    f0_track, df_track = np.array(f0_track), np.array(df_track)
    times = np.arange(len(f0_track)) * STEP + 0.02
    voiced_f0 = f0_track[~np.isnan(f0_track)]
    lo, hi = np.percentile(voiced_f0, [10, 90])
    take = {"mode": mode, "task": "free speech", "snr": snr, "snr_full": snr, "level": level,
            "clip_events": 0, "voiced_fraction": 0.8, "ghost_fraction": 0.02,
            "pitch": float(np.median(voiced_f0)), "pitch_floor": float(lo),
            "pitch_range": float(12 * np.log2(hi / lo)), "delta_f": df, "h1h2": 4.0, "cpp": 20.0,
            "ring": -20.0, "formant_ceiling": 5000, "note": "G#3", "singing": None,
            "tracks": {"times": times, "f0": f0_track, "delta_f": df_track,
                       "f2": np.where(np.isnan(f0_track), np.nan, f2),
                       "h1h2": np.where(np.isnan(f0_track), np.nan, 4.0),
                       "cpp": np.where(np.isnan(f0_track), np.nan, 20.0)}}
    take.update(summary)
    return take


def dim(rep, key):
    return next(d for d in rep["dimensions"] if d["key"] == key)


def tip(rep, key):
    return next((t for t in rep["tips"] if t["key"] == key), None)


# --- ZONES ---

def test_every_zone_is_sourced_contiguous_and_ordered():
    for key in report.DIMENSIONS_SPEAK:
        spec = report.zones(key)
        assert spec["source"].strip() and spec["note"].strip()
        zones = spec["zones"]
        assert sorted(z["id"] for z in zones) == ["feminine", "masculine", "overlap"]
        assert zones[0]["lo"] == spec["scale"][0] and zones[-1]["hi"] == spec["scale"][1]
        for a, b in zip(zones, zones[1:]):
            assert a["hi"] == b["lo"]
        for z in zones:
            assert z["lo"] < z["hi"] and z["label"] == report.ZONE_LABELS[z["id"]]
        assert zones[1]["id"] == "overlap"
    assert report.zones("nonsense") is None
    assert abs(sum(report.WEIGHTS.values()) - 1) < 1e-9


def test_position_is_log_for_pitch_and_clipped():
    assert report.position("pitch", 80) == 0 and report.position("pitch", 400) == 1
    assert abs(report.position("pitch", np.sqrt(80 * 400)) - 0.5) < 1e-9
    assert report.position("pitch", 30) == 0 and report.position("delta_f", 5000) == 1
    assert abs(report.position("delta_f", 1100) - 0.5) < 1e-9          # linear
    assert report.position("pitch", None) is None and report.position("pitch", np.nan) is None
    assert report.zone_of("pitch", 150) == "overlap"
    assert report.zone_of("pitch", 120) == "masculine" and report.zone_of("pitch", 220) == "feminine"
    assert report.zone_of("cpp", 30) == "masculine"                   # clarity runs the other way
    assert report.zone_of("pitch", None) is None


def test_live_zones():
    live = report.live_zones("speak")
    assert live["key"] == "pitch" and live["log"] and len(live["zones"]) == 3
    assert report.live_zones("sing") is None


# --- OVERALL ---

def test_high_pitch_with_dark_resonance_is_not_feminine():
    rep = report.build_report(make_take(f0=210, df=950))
    assert dim(rep, "pitch")["zone"] == "feminine"
    assert dim(rep, "delta_f")["zone"] == "masculine"
    assert rep["overall"]["zone"] != "feminine"
    pulls = {p["key"]: p["direction"] for p in rep["overall"]["pulls"]}
    assert pulls["delta_f"] == "masculine" and pulls["pitch"] == "feminine"
    assert "resonance" in rep["overall"]["text"].lower()


def test_pitch_alone_cannot_push_past_overlap_when_resonance_is_masculine():
    for f0 in (185, 210, 245):
        for df in (900, 960, 995):
            take = make_take(f0=f0, df=df, wobble=4)                    # lively intonation too
            rep = report.build_report(take, goal="feminine")
            assert rep["overall"]["zone"] in ("overlap", "masculine"), (f0, df)
            assert rep["overall"]["position"] <= report.OVERALL_EDGES[1]
            lo, hi = rep["overall"]["range"]
            assert lo <= rep["overall"]["position"] <= hi


def test_matching_pitch_and_resonance_place_feminine_and_masculine():
    fem = report.build_report(make_take(f0=215, df=1130))
    masc = report.build_report(make_take(f0=115, df=950))
    assert fem["overall"]["zone"] == "feminine" and masc["overall"]["zone"] == "masculine"
    assert fem["overall"]["position"] > 0.75 > 0.25 > masc["overall"]["position"]


def test_overlap_resonance_named_as_pulling_toward_androgynous():
    rep = report.build_report(make_take(f0=215, df=1030))
    assert rep["overall"]["zone"] == "overlap"
    assert "pulls the overall impression toward androgynous" in rep["overall"]["text"]


# --- UNTRUSTED ---

def test_poor_take_has_no_placement_and_one_issue():
    rep = report.build_report(make_take(snr=5.0))
    assert rep["overall"]["position"] is None and rep["overall"]["reason"]
    assert len(rep["issues"]) == 1
    assert set(rep["issues"][0]["affects"]) == set(report.DIMENSIONS_SPEAK)
    assert all(d["position"] is None and d["zone"] is None for d in rep["dimensions"])
    assert all(d["confidence"] == "none" for d in rep["dimensions"])
    assert rep["tips"] == []
    assert "hard to judge" in rep["headline"]


def test_high_pitch_makes_resonance_unjudgeable_and_overall_none():
    rep = report.build_report(make_take(f0=300, df=1150))
    res = dim(rep, "delta_f")
    assert res["confidence"] == "none" and res["reason"] and res["position"] is None
    assert res["headline"] == res["reason"][0].upper() + res["reason"][1:]
    assert rep["overall"]["position"] is None
    assert "brightness" in rep["overall"]["reason"]


def test_same_cause_collapses_into_one_issue():
    rep = report.build_report(make_take(snr=15.0))          # below the CPP SNR: weight and clarity
    noisy = [i for i in rep["issues"] if "h1h2" in i["affects"]]
    assert len(noisy) == 1 and set(noisy[0]["affects"]) == {"h1h2", "cpp"}
    assert rep["overall"]["position"] is not None           # pitch and resonance still trusted


def test_spec01_confidence_is_used_when_present():
    take = make_take(confidence={"delta_f": "none", "pitch": {"level": "low", "reason": "short"},
                                 "reasons": {"delta_f": "formants were unstable"}})
    rep = report.build_report(take)
    assert dim(rep, "delta_f")["confidence"] == "none"
    assert dim(rep, "delta_f")["reason"] == "formants were unstable"
    assert dim(rep, "pitch")["confidence"] == "low" and dim(rep, "pitch")["reason"] == "short"
    assert rep["overall"]["position"] is None


def test_weak_proxies_are_capped_low():
    rep = report.build_report(make_take(snr=30.0))
    assert dim(rep, "h1h2")["confidence"] == "low" and dim(rep, "cpp")["confidence"] == "low"
    assert dim(rep, "pitch")["confidence"] == "high"
    assert "rough" in dim(rep, "h1h2")["headline"]


# --- TIPS ---

def test_sentence_final_dips_give_a_floor_tip_covering_them():
    take = make_take(f0=200, df=1120, phrases=3, dip_hz=120)
    rep = report.build_report(take, goal="feminine")
    floor = tip(rep, "pitch_floor_dips")
    assert floor is not None and floor["dimension"] == "pitch_floor"
    assert "ends of sentences" in floor["text"] and "120 Hz" in floor["text"]
    period = PHRASE_S + GAP_S
    for k in range(3):
        dip = (0.02 + k * period + PHRASE_S - 0.3, 0.02 + k * period + PHRASE_S)
        assert any(s <= dip[0] + 0.02 and e >= dip[1] - 0.02 for s, e in floor["moments"]), dip
    best = tip(rep, "best_moment")
    end = float(take["tracks"]["times"][-1]) + STEP
    assert best["kind"] == "best" and 0 <= best["moments"][0][0] < best["moments"][0][1] <= end
    assert len(rep["tips"]) <= report.MAX_TIPS and rep["tips"][-1]["kind"] == "best"


def test_goal_flips_tip_direction():
    take = make_take(f0=160, df=1030, wobble=3)
    fem = report.build_report(take, goal="feminine")
    masc = report.build_report(take, goal="masculine")
    pf, pm = tip(fem, "pitch_toward_goal"), tip(masc, "pitch_toward_goal")
    assert "reached" in pf["text"] and "starting your sentences near that height" in pf["text"]
    assert "settled" in pm["text"] and "without pushing down" in pm["text"]
    mid = lambda t: np.mean(t["moments"][0])                         # noqa: E731
    times, f0 = take["tracks"]["times"], take["tracks"]["f0"]
    at = lambda s: np.nanmedian(f0[(times >= s - 0.3) & (times <= s + 0.3)])   # noqa: E731
    assert at(mid(pf)) > 160 > at(mid(pm))
    rf, rm = tip(fem, "resonance_stretches"), tip(masc, "resonance_stretches")
    assert rf["text"].startswith("Your brightest stretch") and rm["text"].startswith("Your darkest")
    assert "masculine goal" in tip(masc, "best_moment")["text"]


def test_no_goal_describes_without_instructions():
    take = make_take(f0=160, df=1030, dip_hz=110, wobble=3)
    rep = report.build_report(take)
    assert rep["goal"] is None and rep["tips"]
    for t in rep["tips"]:
        assert not any(w in t["text"].lower() for w in INSTRUCTION_WORDS), t["text"]
        assert t["kind"] in ("info", "best")


def test_goal_defaults_to_profile(tmp_path):
    profile = Profile(tmp_path)
    profile.set_voice_goal("masculine")
    assert report.build_report(make_take(f0=160, df=1030), profile)["goal"] == "masculine"
    assert report.build_report(make_take(), profile, goal="feminine")["goal"] == "feminine"


def test_change_against_previous_take_and_baseline(tmp_path):
    previous = make_take(df=950)
    rep = report.build_report(make_take(df=1030), previous=previous)
    change = dim(rep, "delta_f")["change"]
    assert change["vs"] == "previous" and change["text"].startswith("Brighter than your last take")
    assert "brighter than your last take" in dim(rep, "delta_f")["headline"]
    other_task = make_take(df=950, task="passage: Rainbow")
    profile = Profile(tmp_path)
    profile.set_baseline(make_take(df=1028))
    rep = report.build_report(make_take(df=1030), profile, previous=other_task)
    change = dim(rep, "delta_f")["change"]
    assert change["vs"] == "baseline" and change["text"].startswith("About the same")
    assert dim(rep, "delta_f")["baseline"] == 1028


def test_profile_target_and_ceiling_mismatch(tmp_path):
    profile = Profile(tmp_path)
    profile.set_target("pitch", (180, 230))
    profile.set_formant_ceiling(6000)
    rep = report.build_report(make_take(formant_ceiling=4500), profile)
    assert dim(rep, "pitch")["target"] == (180.0, 230.0)
    assert dim(rep, "delta_f")["confidence"] == "low" and "ceiling" in dim(rep, "delta_f")["reason"]


# --- SING ---

def test_sing_report_has_no_gender_placement():
    take = make_take(f0=330, df=1200, mode="sing", wobble=0.1,
                     singing={"notes": 6, "held_notes": 3, "vibrato_notes": 1, "tremolo_notes": 0,
                              "vibrato_rate": 5.5, "vibrato_extent": 40, "vibrato_regularity": 0.1,
                              "drift": 12.0, "tonic_offset": -5.0, "tuning_spread": 15.0,
                              "interval_error": 20.0},
                     register={"head_share": 0.7, "chest_share": 0.3, "calibrated": False})
    rep = report.build_report(take, goal="feminine")
    assert rep["mode"] == "sing" and rep["overall"]["position"] is None
    assert rep["overall"]["zone"] is None and rep["overall"]["reason"]
    assert [d["key"] for d in rep["dimensions"]] == report.DIMENSIONS_SING
    assert all(d["zone"] is None for d in rep["dimensions"])
    assert dim(rep, "steadiness")["value"] == 12.0 and dim(rep, "tuning")["value"] == 15.0
    assert dim(rep, "register")["confidence"] == "low"                 # uncalibrated model
    best = tip(rep, "best_moment")
    assert best and 0 <= best["moments"][0][0] < best["moments"][0][1]
    json.dumps(rep, allow_nan=False)


# --- PLAIN DATA ---

def test_report_is_json_serialisable(tmp_path):
    profile = Profile(tmp_path)
    profile.set_baseline(make_take())
    profile.set_targets_from_preset("speak")
    for take in (make_take(dip_hz=120), make_take(snr=5.0), make_take(f0=300),
                 make_take(h1h2=np.nan, cpp=np.float64(18.0))):
        for goal in (None, "feminine", "androgynous", "masculine"):
            rep = report.build_report(take, profile, previous=make_take(df=1000), goal=goal)
            text = json.dumps(rep, allow_nan=False)
            assert json.loads(text)["mode"] == "speak"
            assert set(rep) == {"mode", "headline", "overall", "dimensions", "tips", "issues",
                                "caveat", "goal"}
            assert report.render_text(rep)


def test_dimensions_carry_the_middle_half_of_their_frames():
    rep = report.build_report(make_take(f0=200.0, df=1000.0))
    pitch, resonance = dim(rep, "pitch"), dim(rep, "delta_f")
    lo, hi = pitch["range"]
    assert lo < pitch["value"] < hi and 12 * np.log2(hi / lo) < 2      # a +/-1 st wobble
    lo, hi = resonance["range"]
    assert lo < 1000 < hi and hi - lo < 40                              # +/-20 Hz movement
    assert dim(rep, "pitch_floor")["range"] is None and dim(rep, "pitch_range")["range"] is None
    poor = report.build_report(make_take(snr=6.0, level=-52.0))
    assert all(d["range"] is None for d in poor["dimensions"])
    json.dumps(rep, default=float)
