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
    for f0 in (185, 210, 225):              # resonance is withheld above coach.FORMANT_MAX_F0 (230)
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


def test_overlap_resonance_keeps_the_overall_in_the_overlap_in_one_sentence():
    rep = report.build_report(make_take(f0=215, df=1030))
    assert rep["overall"]["zone"] == "overlap"
    text = rep["overall"]["text"]
    assert "overall impression stays in the overlap" in text and text.count(".") == 1
    assert "androgynous" not in text                       # a goal word, never a zone


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
    assert res["headline"].startswith(res["reason"][0].upper() + res["reason"][1:] + ". Try")   # the reason, then the fix
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
    assert "ends of sentences" in floor["why"] and "120 Hz" in floor["why"]
    assert floor["text"].startswith("Try keeping the last word up")
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
    assert "starting your sentences near the height you reached" in pf["text"] and "reached" in pf["why"]
    assert "settled" in pm["why"] and "without pushing down" in pm["text"]
    mid = lambda t: np.mean(t["moments"][0])                         # noqa: E731
    times, f0 = take["tracks"]["times"], take["tracks"]["f0"]
    at = lambda s: np.nanmedian(f0[(times >= s - 0.3) & (times <= s + 0.3)])   # noqa: E731
    assert at(mid(pf)) > 160 > at(mid(pm))
    rf, rm = tip(fem, "resonance_stretches"), tip(masc, "resonance_stretches")
    assert "carrying the feeling of your brightest stretch" in rf["text"].replace("Try c", "c")
    assert "relaxed, open space" in rm["text"]
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
                                "caveat", "goal", "last_tip"}
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


# --- PHASE 11 / 04: LAST TIME'S TIP, WORDS FOR CHANGE, ZONE WORDS ---

def test_the_zone_is_never_called_androgynous():
    assert report.ZONE_LABELS == {"masculine": "typically masculine", "overlap": "overlap",
                                  "feminine": "typically feminine"}
    assert report.ZONE_SHORT == {"masculine": "Masculine", "overlap": "Overlap", "feminine": "Feminine"}
    for goal in (None, "feminine", "androgynous", "masculine"):
        for take in (make_take(f0=215, df=1030), make_take(f0=160, df=1030, wobble=3), make_take(f0=115, df=950)):
            text = report.render_text(report.build_report(take, goal=goal))
            assert "overlap / androgynous" not in text
            assert "toward androgynous" not in text


def test_tip_check_says_whether_this_take_did_what_the_last_tip_asked():
    prev = make_take(f0=150, df=1030, wobble=3)
    assert report.build_report(prev, goal="feminine")["last_tip"] is None          # no earlier take
    verdicts = {}
    for f0 in (150, 156, 170):
        rep = report.build_report(make_take(f0=f0, df=1030, wobble=3), previous=prev, goal="feminine")
        verdicts[f0] = rep["last_tip"]
    assert verdicts[150]["verdict"] == "not yet" and verdicts[156]["verdict"] == "partly"
    yes = verdicts[170]
    assert yes["verdict"] == "yes" and yes["text"].startswith("Last time: start your sentences higher.")
    assert "This take: you did." in yes["text"] and "Hz higher" in yes["detail"]
    assert json.dumps(yes)


def test_tip_check_follows_the_goal_direction_and_stays_symmetrical():
    prev = make_take(f0=170, df=1030, wobble=3)
    lower = make_take(f0=150, df=1030, wobble=3)
    masc = report.build_report(lower, previous=prev, goal="masculine")["last_tip"]
    fem = report.build_report(lower, previous=prev, goal="feminine")
    assert masc["verdict"] == "yes" and "lower" in masc["detail"] and "rest lower" in masc["tip"]
    assert fem["last_tip"] is None or fem["last_tip"]["verdict"] == "not yet"        # fem already met: no pitch tip to check
    prev_low = make_take(f0=115, df=1030, wobble=3)
    ando = report.build_report(make_take(f0=145, df=1030, wobble=3), previous=prev_low, goal="androgynous")["last_tip"]
    assert ando is None or ando["verdict"] in report.TIP_VERDICTS


def test_tip_check_reads_resonance_tips_and_ignores_best_moments():
    prev = make_take(f0=215, df=960, wobble=3)
    tip_prev = tip(report.build_report(prev, goal="feminine"), "resonance_stretches")
    assert tip_prev["aim"]["key"] == "delta_f" and tip_prev["aim"]["direction"] == 1
    brighter = make_take(f0=215, df=1010, wobble=3)
    check = report.tip_check({"tips": [tip_prev]}, brighter, prev)
    assert check["verdict"] == "yes" and "brighter" in check["detail"] and "Hz" in check["detail"]
    best = [t for t in report.build_report(prev, goal="feminine")["tips"] if t["key"] == "best_moment"]
    assert best and report.tip_check({"tips": best}, brighter, prev) is None
    assert report.tip_check({"tips": []}, brighter, prev) is None and report.tip_check(None, brighter, prev) is None


def test_change_text_has_a_word_for_the_direction_and_no_signed_number():
    prev = make_take(f0=150, df=1000, h1h2=8.0, cpp=22.0)
    now = make_take(f0=160, df=1080, h1h2=3.8, cpp=20.0)
    dims = report.build_report(now, previous=prev)["dimensions"]
    texts = [d["change"]["text"] for d in dims if d["change"]]
    assert texts and all("+" not in t and "-" not in t.replace(" - ", " ") and "(" not in t for t in texts)
    weight = next(d for d in dims if d["key"] == "h1h2")["change"]
    assert weight["text"] == "Heavier than your last take, by 4.2 dB" and weight["word"] == "heavier"
    pitch = next(d for d in dims if d["key"] == "pitch")["change"]
    assert pitch["text"].startswith("Higher than your last take, by ") and "semitones" in pitch["text"]
    assert "(" not in report.build_report(now, previous=prev)["headline"].split(",", 1)[-1]


def test_compare_change_reads_from_the_first_takes_side():
    assert report.compare_change("pitch", 165, 150)["text"].startswith("Higher by 1.") 
    assert report.compare_change("delta_f", 1000, 1045) == {"text": "Darker by 45 Hz", "same": False}
    assert report.compare_change("cpp", 20.0, 20.5) == {"text": "About the same", "same": True}


def test_every_cant_judge_headline_says_why_and_what_to_try():
    for kwargs in ({"snr": 15.0}, {"f0": 300}, {"snr": 5.0}):
        rep = report.build_report(make_take(**kwargs))
        for d in rep["dimensions"]:
            if d["confidence"] == "none":
                assert "Try" in d["headline"], (kwargs, d["key"], d["headline"])


def test_lowest_notes_explains_its_own_scale():
    rep = report.build_report(make_take())
    floor = dim(rep, "pitch_floor")
    assert floor["name"] == "Lowest notes" and "its own scale" in floor["explanation"]
    assert "140 Hz and up" in floor["explanation"] and report.ZONES["pitch_floor"]["name"] == "Lowest notes"
    assert dim(rep, "h1h2")["name"] == "Weight" and report.ZONES["h1h2"]["name"] == "Weight"
    assert dim(rep, "pitch_range")["display"].endswith(" st")


def test_tips_are_short_and_do_not_repeat_the_cards_numbers():
    take = make_take(f0=160, df=1030, dip_hz=110, wobble=3)
    for goal in (None, "feminine", "androgynous", "masculine"):
        rep = report.build_report(take, goal=goal)
        median = f"{rep['dimensions'][0]['value']:.0f} Hz"
        for t in rep["tips"]:
            assert len(t["text"].split()) <= 32, t["text"]
            assert median not in t["text"] and "semitones" not in t["text"], t["text"]
