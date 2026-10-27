"""The UI lab's command line.

    python -m ui.lab run [--scenarios a,b|all] [--sizes 1920x1080,1280x720] [--themes dark,light]
                         [--group live|takes|shell|practice] [--out DIR] [--takes DIR] [--motion] [--smoke]
    python -m ui.lab baseline --group G [--from RUN]
    python -m ui.lab list

`run` drives the real app through the scenarios, evaluates every capture and writes index.html, findings.json
and scorecard.json into the run folder; with --group it runs that owner's scenarios and reports the group's
findings against tests/uilab_baseline/<group>.json. `baseline` rewrites one group's baseline, from a fresh run
of its scenarios or from an existing run (--from)."""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_RUNS = Path(r"C:\tmp\phase11-work\lab-runs")
DEFAULT_CACHE = Path(r"C:\tmp\phase11-work\lab-cache")
SIZES = "1920x1080,1280x720"
THEMES = "dark,light"


def _sizes(text):
    return [tuple(int(v) for v in s.lower().split("x")) for s in text.split(",") if s]


def run_lab(names, sizes, themes, out, takes=None, motion=False, log=print):
    """Run scenarios into `out`, evaluate them and build the report. Returns (findings, scorecard, index)."""
    from tools.uilab import report, score
    from ui.lab import driver, scenarios
    if takes:
        driver.REAL_TAKES = Path(takes)
    lab = driver.Lab(out, sizes=sizes, themes=themes, motion=motion, cache_dir=DEFAULT_CACHE, log=log)
    started = time.perf_counter()
    done = lab.run(scenarios.build(names))
    ran = time.perf_counter() - started
    findings, metrics = score.evaluate(out)
    metrics["run"] = done
    card = score.write_results(out, findings, metrics)
    card["run_seconds"] = round(ran, 1)
    index = report.build(out, findings, card)
    from tools.uilab.runio import write_json
    write_json(Path(out) / "run.json", {"scenarios": names, "sizes": sizes, "themes": themes, "seconds": round(ran, 1),
                                        "done": done, "takes": str(driver.REAL_TAKES),
                                        "at": datetime.now().isoformat(timespec="seconds")})
    return findings, card, index


def summary(findings, card, group=None, log=print):
    from tools.uilab import score
    u = card["unique"]
    log(f"unique findings: {u['error']} errors, {u['warn']} warnings, {u['info']} info")
    for g, v in card["by_group"].items():
        log(f"  {g:9s} errors {v.get('error', 0):4d}  warnings {v.get('warn', 0):4d}")
    truth = card["truth"]
    if truth["beats"]:
        log(f"truth: worst steady median error {truth['steady_median_error_pct']} %, worst settle "
            f"{truth['steady_settle_s']} s")
    if card["live_vs_take"]["takes"]:
        log(f"live vs take: worst {card['live_vs_take']['max_abs_st']} semitones")
    baselines = score.load_baselines()
    fresh = score.new_findings(findings, baselines)
    if group:
        fresh = [f for f in fresh if f["group"] == group]
    keys = sorted({f["key"] for f in fresh})
    log(f"not in the baseline{f' ({group})' if group else ''}: {len(keys)}")
    for k in keys[:40]:
        log(f"  NEW {k}")
    if group:
        fixed = score.fixed_entries(findings, baselines, [group])[group]
        log(f"baseline entries not seen in this run ({group}): {len(fixed)} (remove them if they are fixed)")
    return keys


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m ui.lab", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run scenarios, evaluate, report")
    r.add_argument("--scenarios", default=None, help="comma-separated ids, or all")
    r.add_argument("--group", choices=["live", "takes", "shell", "practice"])
    r.add_argument("--sizes", default=SIZES)
    r.add_argument("--themes", default=THEMES)
    r.add_argument("--out", default=None)
    r.add_argument("--takes", default=None, help="folder of real takes to copy (default the user's)")
    r.add_argument("--motion", action="store_true", help="motion on, and a frame strip per beat")
    r.add_argument("--smoke", action="store_true", help="the fast smoke set at 1280x720, dark")
    b = sub.add_parser("baseline", help="rewrite tests/uilab_baseline/<group>.json")
    b.add_argument("--group", required=True, choices=["live", "takes", "shell", "practice", "all"])
    b.add_argument("--from", dest="source", default=None, help="an existing run folder to take findings from")
    b.add_argument("--out", default=None)
    sub.add_parser("list", help="list scenarios and groups")
    args = p.parse_args(argv)

    from ui.lab import scenarios
    if args.command == "list":
        for name, fn in scenarios.SCENARIOS.items():
            sc = fn()
            print(f"{name:20s} {len(sc['beats']):3d} beats  {sc.get('title', '')}")
        for g in scenarios.GROUPS:
            print(f"group {g}: {', '.join(scenarios.group_scenarios(g))}")
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.command == "run":
        sizes, themes = _sizes(args.sizes), args.themes.split(",")
        if args.smoke:
            names, sizes, themes = list(scenarios.SMOKE), [scenarios.SMOKE_SIZE], [scenarios.SMOKE_THEME]
        elif args.scenarios:
            names = args.scenarios.split(",") if args.scenarios != "all" else list(scenarios.SCENARIOS)
        elif args.group:
            names = scenarios.GROUPS[args.group]
        else:
            names = list(scenarios.SCENARIOS)
        out = Path(args.out or DEFAULT_RUNS / f"{stamp}{'-' + args.group if args.group else ''}")
        findings, card, index = run_lab(names, sizes, themes, out, args.takes, args.motion)
        print(f"index: {index}")
        summary(findings, card, args.group)
        return 0
    from tools.uilab import score
    groups = list(score.GROUPS) if args.group == "all" else [args.group]
    if args.source:
        from tools.uilab.runio import read_json
        findings = read_json(Path(args.source) / "findings.json", [])
        source = str(args.source)
    else:
        names = []
        for g in groups:
            names += [n for n in scenarios.group_scenarios(g) if n not in names]
        out = Path(args.out or DEFAULT_RUNS / f"{stamp}-baseline-{args.group}")
        findings, card, index = run_lab(names, _sizes(SIZES), THEMES.split(","), out)
        source = str(out)
        print(f"index: {index}")
    for g in groups:
        entries = score.write_baseline(findings, g, scenarios=[source])
        print(f"baseline {g}: {len(entries)} entries -> {score.BASELINE_DIR / (g + '.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
