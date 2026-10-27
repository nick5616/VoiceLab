"""The UI lab's pure tools (no Qt):

    python -m tools.uilab eval RUN                   re-run the evals on a run folder (index.html, findings, scorecard)
    python -m tools.uilab diff RUN_A RUN_B --out DIR  screenshots and findings of B against A
    python -m tools.uilab trace DIR                   the evals over a real session recorded with VOICELAB_TRACE
    python -m tools.uilab check RUN [--group G]       findings of a run that are not in tests/uilab_baseline
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def evaluate(run_dir, html=True):
    from tools.uilab import report, score
    findings, metrics = score.evaluate(run_dir)
    card = score.write_results(run_dir, findings, metrics)
    index = report.build(run_dir, findings, card) if html else None
    return findings, card, index


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m tools.uilab", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    e = sub.add_parser("eval")
    e.add_argument("run")
    d = sub.add_parser("diff")
    d.add_argument("a")
    d.add_argument("b")
    d.add_argument("--out", required=True)
    t = sub.add_parser("trace")
    t.add_argument("dir")
    c = sub.add_parser("check")
    c.add_argument("run")
    c.add_argument("--group", default=None)
    args = p.parse_args(argv)
    if args.command in ("eval", "trace"):
        folder = Path(args.run if args.command == "eval" else args.dir)
        findings, card, index = evaluate(folder)
        u = card["unique"]
        print(f"index: {index}")
        print(f"unique findings: {u['error']} errors, {u['warn']} warnings, {u['info']} info")
        lat = card["latency"].get("frame_ui_ms") or {}
        if lat.get("n"):
            print(f"frame UI work: p50 {lat['p50']} ms, p95 {lat['p95']} ms over {lat['n']} frames")
        return 0
    if args.command == "diff":
        from tools.uilab.diff import diff_runs
        s = diff_runs(args.a, args.b, args.out)
        f = s["findings"]
        print(f"{s['changed']} of {s['compared']} screenshots changed; findings {len(f['new'])} new, "
              f"{len(f['fixed'])} fixed, {f['unchanged']} unchanged -> {Path(args.out) / 'index.html'}")
        return 0
    from tools.uilab import score
    from tools.uilab.runio import read_json
    findings = read_json(Path(args.run) / "findings.json", [])
    fresh = score.new_findings(findings, score.load_baselines())
    if args.group:
        fresh = [f for f in fresh if f["group"] == args.group]
    keys = sorted({f["key"] for f in fresh})
    for k in keys:
        print("NEW", k)
    print(f"{len(keys)} finding(s) not in the baseline")
    return 1 if keys else 0


if __name__ == "__main__":
    sys.exit(main())
