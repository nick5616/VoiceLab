"""Evaluate a lab run: every check over every capture and log, then findings.json, scorecard.json and the
ratchet (tests/uilab_baseline/<group>.json). Pure: reads the run's files only."""
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from tools.uilab import evals, png
from tools.uilab.runio import read_json, read_jsonl, write_json

GROUPS = ("live", "takes", "shell", "practice")
BASELINE_DIR = Path(__file__).resolve().parents[2] / "tests" / "uilab_baseline"
RATCHET_SEVERITIES = ("error", "warn")      # info findings are measurements (scroll amounts), not defects
UNRATCHETED = ("latency",)                  # wall-clock timings vary with the machine's load: scorecard only


def scenario_dirs(run_dir):
    return sorted(p.parent for p in Path(run_dir).glob("*/beats.json") if not p.parent.name.startswith("_"))


def load_capture(sdir, scenario, beat, c):
    stem = sdir / c["stem"]
    tree = read_json(str(stem) + ".tree.json", {})
    state = read_json(str(stem) + ".state.json", {})
    return evals.Capture(scenario, beat["name"], c["theme"], c["size"], c["stem"], tree, state,
                         png_path=str(stem) + ".png")


def evaluate(run_dir, log=None):
    """(findings, metrics) for a run directory."""
    run_dir = Path(run_dir)
    findings = []
    metrics = {"truth": [], "live_vs_take": [], "latency": {}, "reach": [], "fold": [], "scenarios": []}
    for sdir in scenario_dirs(run_dir):
        data = read_json(sdir / "beats.json", {})
        sid = data.get("scenario", {}).get("id", sdir.name)
        beats = data.get("beats", [])
        events = read_jsonl(sdir / "events.jsonl")
        frames = [e for e in events if e.get("type") == "frame"]
        started = time.perf_counter()
        previous = {}
        count = 0
        for beat in beats:
            last = None
            for c in beat.get("captures", []):
                cap = load_capture(sdir, sid, beat, c)
                count += 1
                found = evals.check_capture(cap)
                findings += found
                for f in found:
                    if f["check"] == "fold" and "overflow_y" in f["evidence"]:
                        metrics["fold"].append({"scenario": sid, "beat": beat["name"], "screen": f["screen"],
                                                "size": f["size"], "theme": f["theme"],
                                                "overflow_y": f["evidence"]["overflow_y"], "path": f["widget_path"]})
                key = (c["theme"], tuple(c["size"]))
                prev = previous.get(key)
                if prev is not None and evals.state_signature(prev.state) != evals.state_signature(cap.state):
                    try:
                        pa, pb = png.rgb(png.read_png(prev.png_path)), png.rgb(png.read_png(cap.png_path))
                        findings += evals.check_distinguish([(prev, cap, pa, pb)])
                    except (OSError, ValueError):
                        pass
                previous[key] = _light(cap)
                last = last or cap
            found, m = evals.check_truth(sid, beat, frames, last)
            findings += found
            if m:
                metrics["truth"].append(dict(m, scenario=sid, beat=beat["name"]))
            result = beat.get("result") if isinstance(beat.get("result"), dict) else {}
            if result.get("sidecar"):
                sidecar = read_json(beat["result"]["sidecar"], None)
                found, m = evals.check_live_vs_take(sid, beat, frames, sidecar)
                findings += found
                if m:
                    metrics["live_vs_take"].append(dict(m, scenario=sid, beat=beat["name"]))
        lat = evals.latency_metrics(events, beats)
        findings += evals.check_latency(sid, lat)
        for key, values in lat.items():
            metrics["latency"].setdefault(key, []).extend(values)
        found, table = evals.check_reach(sid, beats)
        findings += found
        metrics["reach"] += table
        findings += evals.check_beat_errors(sid, beats)
        findings += evals.check_exceptions(sid, events)
        metrics["scenarios"].append({"id": sid, "beats": len(beats), "captures": count,
                                     "eval_seconds": round(time.perf_counter() - started, 2)})
        if log:
            log(f"evaluated {sid}: {count} captures")
    for f in findings:
        f["group"] = evals.group_of(f)
        f["key"] = evals.finding_key(f)
    return findings, metrics


def _light(cap):
    """A capture with only what the distinguishability check needs (trees are large)."""
    keep = {p: cap.by_path[p] for p in ("action_bar/record_btn", "top") if p in cap.by_path}
    light = evals.Capture(cap.scenario, cap.beat, cap.theme, cap.size, cap.stem, {"widgets": list(keep.values()),
                                                                                  "meta": cap.meta}, cap.state,
                          cap.png_path)
    return light


def latency_summary(lat):
    out = {}
    for key, values in lat.items():
        if key == "take_visible":
            ms = [v["ms"] for v in values]
            out[key] = {"p50": evals.percentile(ms, 50), "p95": evals.percentile(ms, 95), "n": len(ms),
                        "analysis_ms": [v["analysis_ms"] for v in values]}
        else:
            out[key] = {"p50": evals.percentile(values, 50), "p95": evals.percentile(values, 95), "n": len(values)}
    budgets = {"frame_ui_ms": evals.FRAME_BUDGET_MS, "toast_ms": evals.TOAST_BUDGET_MS, "paint_ms": evals.PAINT_BUDGET_MS,
               "take_visible": f"analysis + {evals.TAKE_SLACK_MS:.0f}"}
    for key, budget in budgets.items():
        if key in out:
            out[key]["budget_ms"] = budget
    return out


def unique(findings):
    """One entry per ratchet key: {key: {check, severity (worst), screen, widget_path, group, count, example,
    scenarios, sizes, themes}}."""
    rank = {"error": 0, "warn": 1, "info": 2}
    out = {}
    for f in findings:
        e = out.get(f["key"])
        if e is None:
            e = out[f["key"]] = {"check": f["check"], "severity": f["severity"], "screen": f["screen"],
                                 "widget_path": f["widget_path"], "group": f["group"], "count": 0,
                                 "example": f["message"], "scenarios": set(), "sizes": set(), "themes": set()}
        e["count"] += 1
        if rank[f["severity"]] < rank[e["severity"]]:
            e["severity"], e["example"] = f["severity"], f["message"]
        e["scenarios"].add(f["scenario"])
        if f.get("size"):
            e["sizes"].add(f["size"])
        if f.get("theme"):
            e["themes"].add(f["theme"])
    for e in out.values():
        for k in ("scenarios", "sizes", "themes"):
            e[k] = sorted(e[k])
    return out


def scorecard(findings, metrics):
    by = defaultdict(Counter)
    for f in findings:
        by[f["check"]][f["severity"]] += 1
    uniq = unique(findings)
    keys = defaultdict(Counter)
    screens = defaultdict(lambda: defaultdict(Counter))
    groups = defaultdict(Counter)
    for e in uniq.values():
        keys[e["check"]][e["severity"]] += 1
        screens[e["screen"] or "-"][e["check"]][e["severity"]] += 1
        groups[e["group"]][e["severity"]] += 1
    truth = metrics["truth"]
    steady = [m for m in truth if m["kind"] == "steady" and m["median_error_pct"] is not None]
    lvt = metrics["live_vs_take"]
    return {
        "totals": {sev: sum(1 for f in findings if f["severity"] == sev) for sev in ("error", "warn", "info")},
        "unique": {sev: sum(1 for e in uniq.values() if e["severity"] == sev) for sev in ("error", "warn", "info")},
        "by_check": {c: dict(v) for c, v in sorted(by.items())},
        "unique_by_check": {c: dict(v) for c, v in sorted(keys.items())},
        "by_screen": {s: {c: dict(v) for c, v in d.items()} for s, d in sorted(screens.items())},
        "by_group": {g: dict(v) for g, v in sorted(groups.items())},
        "truth": {"beats": truth,
                  "steady_median_error_pct": max((m["median_error_pct"] for m in steady), default=None),
                  "steady_settle_s": max((m["settle_s"] or 99 for m in steady), default=None)},
        "live_vs_take": {"takes": lvt, "max_abs_st": max((abs(m.get("track_diff_st", m.get("diff_st", 0)) or 0)
                                                          for m in lvt), default=None)},
        "latency": latency_summary(metrics["latency"]),
        "reach": {"features": metrics["reach"], "reached": sum(1 for r in metrics["reach"] if r.get("reached")),
                  "total": len(metrics["reach"])},
        "fold": metrics["fold"],
        "scenarios": metrics["scenarios"],
    }


def write_results(run_dir, findings, metrics):
    run_dir = Path(run_dir)
    for i, f in enumerate(findings, start=1):
        f["id"] = i
    write_json(run_dir / "findings.json", findings)
    card = scorecard(findings, metrics)
    write_json(run_dir / "scorecard.json", card)
    return card


# --- the ratchet ---

def baseline_entries(findings, group):
    """The accepted findings of a group: unique keys (error and warn only), without pixels."""
    out = {}
    for key, e in sorted(unique([f for f in findings if f["group"] == group and ratcheted(f)]).items()):
        out[key] = {k: e[k] for k in ("check", "severity", "screen", "widget_path", "count", "example", "scenarios")}
    return out


def write_baseline(findings, group, folder=BASELINE_DIR, scenarios=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    entries = baseline_entries(findings, group)
    data = {"group": group, "note": "Accepted findings (check|screen|widget path). Wave B may only remove entries; "
            "regenerate with python -m ui.lab baseline --group " + group, "scenarios": scenarios or [],
            "count": len(entries), "entries": entries}
    (folder / f"{group}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return entries


def load_baselines(folder=BASELINE_DIR):
    out = {}
    for group in GROUPS:
        data = read_json(Path(folder) / f"{group}.json", {"entries": {}})
        out[group] = data.get("entries", {})
    return out


def ratcheted(f):
    return f["severity"] in RATCHET_SEVERITIES and f["check"] not in UNRATCHETED


def new_findings(findings, baselines):
    """Findings (error and warn) whose key is not in their group's baseline."""
    return [f for f in findings if ratcheted(f) and f["key"] not in baselines.get(f["group"], {})]


def fixed_entries(findings, baselines, groups=GROUPS):
    """Baseline keys of the groups that no longer appear in the findings."""
    seen = {f["key"] for f in findings}
    return {g: sorted(k for k in baselines.get(g, {}) if k not in seen) for g in groups}
