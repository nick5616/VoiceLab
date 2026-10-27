"""Visual regression between two lab runs: per-beat perceptual difference of the screenshots, and the findings
delta (new, fixed and unchanged findings by check)."""
import html
from collections import Counter
from pathlib import Path

import numpy as np

from tools.uilab import png
from tools.uilab.runio import read_json, write_json

PIXEL_TOLERANCE = 24            # 0-255: a pixel counts as changed past this (anti-aliasing and hinting stay under)
CHANGED_SHARE = 0.002           # a beat counts as visually changed when more than 0.2 % of its pixels change
SCALE = 2                       # compare at half size: small rendering jitter averages out


def captures(run_dir):
    """{(scenario, stem): png path} of a run."""
    out = {}
    for beats in Path(run_dir).glob("*/beats.json"):
        data = read_json(beats, {})
        for beat in data.get("beats", []):
            for c in beat.get("captures", []):
                out[(beats.parent.name, c["stem"])] = beats.parent / f"{c['stem']}.png"
    return out


def mean_abs_diff(a, b):
    """Mean absolute difference of two same-shaped images (0-255)."""
    return round(float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))), 3)


def gray(pixels):
    p = png.shrink(png.rgb(pixels), SCALE).astype(np.float32)
    return 0.299 * p[:, :, 0] + 0.587 * p[:, :, 1] + 0.114 * p[:, :, 2]


def compare(a_path, b_path):
    """{"changed_pct", "mean_diff", "same_size", "mask"} of two screenshots."""
    a, b = png.read_png(a_path), png.read_png(b_path)
    if a.shape != b.shape:
        return {"same_size": False, "changed_pct": 100.0, "mean_diff": None, "mask": None,
                "sizes": [list(a.shape[:2]), list(b.shape[:2])]}
    d = np.abs(gray(a) - gray(b))
    mask = d > PIXEL_TOLERANCE
    return {"same_size": True, "changed_pct": round(100 * float(mask.mean()), 3), "mean_diff": round(float(d.mean()), 3),
            "mask": mask}


def side_by_side(a_path, b_path, mask, out_path):
    """A | B with the changed pixels tinted in B, saved as one PNG."""
    a, b = png.rgb(png.read_png(a_path)).copy(), png.rgb(png.read_png(b_path)).copy()
    if mask is not None:
        big = np.repeat(np.repeat(mask, SCALE, axis=0), SCALE, axis=1)
        h, w = big.shape
        region = b[:h, :w]
        region[big] = (0.4 * region[big] + 0.6 * np.array([255, 0, 200])).astype(np.uint8)
    gap = np.full((a.shape[0], 8, 3), 128, dtype=np.uint8)
    png.write_png(out_path, np.concatenate([a, gap, b], axis=1))


def findings_delta(a_dir, b_dir):
    fa = read_json(Path(a_dir) / "findings.json", [])
    fb = read_json(Path(b_dir) / "findings.json", [])
    ka = {f["key"]: f for f in fa if f["severity"] != "info"}
    kb = {f["key"]: f for f in fb if f["severity"] != "info"}
    new = sorted(set(kb) - set(ka))
    fixed = sorted(set(ka) - set(kb))
    same = sorted(set(ka) & set(kb))

    def by_check(keys, src):
        return dict(Counter(src[k]["check"] for k in keys))
    return {"new": new, "fixed": fixed, "unchanged": len(same), "new_by_check": by_check(new, kb),
            "fixed_by_check": by_check(fixed, ka), "unchanged_by_check": by_check(same, ka),
            "examples": {k: kb[k]["message"] for k in new[:200]}}


def diff_runs(a_dir, b_dir, out_dir, images=True):
    """Compare run B against run A; writes diff.json and index.html into out_dir. Returns the summary."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ca, cb = captures(a_dir), captures(b_dir)
    beats = []
    for key in sorted(set(ca) & set(cb)):
        r = compare(ca[key], cb[key])
        entry = {"scenario": key[0], "stem": key[1], "changed_pct": r["changed_pct"], "mean_diff": r["mean_diff"],
                 "same_size": r["same_size"], "changed": r["changed_pct"] > 100 * CHANGED_SHARE}
        if entry["changed"] and images:
            path = out / f"{key[0]}__{key[1]}.diff.png"
            side_by_side(ca[key], cb[key], r["mask"], path)
            entry["image"] = path.name
        beats.append(entry)
    summary = {"a": str(a_dir), "b": str(b_dir), "beats": beats,
               "only_in_a": sorted("/".join(k) for k in set(ca) - set(cb)),
               "only_in_b": sorted("/".join(k) for k in set(cb) - set(ca)),
               "changed": sum(1 for b in beats if b["changed"]), "compared": len(beats),
               "findings": findings_delta(a_dir, b_dir)}
    write_json(out / "diff.json", summary)
    rows = "".join(f"<tr><td>{html.escape(b['scenario'])}</td><td>{html.escape(b['stem'])}</td>"
                   f"<td>{b['changed_pct']}</td><td>{'<a href=' + repr(b['image']) + '>A | B</a>' if b.get('image') else ''}"
                   f"</td></tr>" for b in beats if b["changed"])
    f = summary["findings"]
    news = "".join(f"<li><code>{html.escape(k)}</code> {html.escape(f['examples'].get(k, ''))}</li>" for k in f["new"])
    page = (f"<!doctype html><meta charset=utf-8><title>UI lab diff</title><style>body{{font:14px 'Segoe UI',sans-serif;"
            f"margin:24px}} td,th{{border:1px solid #ccc;padding:3px 8px}} table{{border-collapse:collapse}}</style>"
            f"<h1>UI lab diff</h1><p>A: {html.escape(str(a_dir))}<br>B: {html.escape(str(b_dir))}</p>"
            f"<p>{summary['changed']} of {summary['compared']} screenshots changed · findings: {len(f['new'])} new, "
            f"{len(f['fixed'])} fixed, {f['unchanged']} unchanged</p><h2>Changed screenshots</h2><table><tr><th>scenario"
            f"</th><th>beat</th><th>% pixels</th><th></th></tr>{rows}</table><h2>New findings</h2><ul>{news}</ul>"
            f"<h2>Fixed findings</h2><ul>{''.join(f'<li><code>{html.escape(k)}</code></li>' for k in f['fixed'])}</ul>")
    (out / "index.html").write_text(page, encoding="utf-8")
    return summary
