"""The run's contact sheet: index.html (scorecard on top, then each scenario's beats as thumbnails with their
findings) and a .marked.png beside every screenshot that has findings: each finding's rectangle in its
severity's colour with its number, so an agent reading images sees what the JSON says. Local files only."""
import html
from pathlib import Path

from tools.uilab import png
from tools.uilab.runio import read_json

COLORS = {"error": (235, 64, 52), "warn": (240, 170, 30), "info": (60, 150, 230)}
DIGITS = {  # 3x5 bitmap digits for finding numbers on the marked images
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
    "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
    "8": "111101111101111", "9": "111101111001111"}
SCALE = 2                       # px per bitmap dot of a finding number
THUMB_WIDTH = 360               # px in the contact sheet


def draw_number(pixels, x, y, number, color):
    """Draw a number in a filled tag at (x, y)."""
    text = str(number)
    w = len(text) * 4 * SCALE + SCALE
    h = 7 * SCALE
    png.fill(pixels, [x, y, w, h], color, alpha=1.0)
    for i, ch in enumerate(text):
        bits = DIGITS[ch]
        for r in range(5):
            for c in range(3):
                if bits[r * 3 + c] == "1":
                    x0, y0 = x + SCALE + i * 4 * SCALE + c * SCALE, y + SCALE + r * SCALE
                    pixels[max(0, y0):y0 + SCALE, max(0, x0):x0 + SCALE, :3] = 255


def mark(png_path, findings, out_path):
    """Write a copy of the screenshot with each finding's rect outlined and numbered."""
    pixels = png.rgb(png.read_png(png_path)).copy()
    H, W = pixels.shape[:2]
    for f in sorted((f for f in findings if f["severity"] != "info"), key=lambda f: {"warn": 0, "error": 1}[f["severity"]]):
        r = f.get("rect")
        color = COLORS[f["severity"]]
        if r and r[2] > 0 and r[3] > 0:
            png.outline(pixels, r, color, width=2)
            x, y = int(max(0, min(W - 30, r[0]))), int(max(0, min(H - 22, r[1] - 22 if r[1] >= 22 else r[1])))
        else:
            x, y = 4, 4
        draw_number(pixels, x, y, f["id"], color)
    png.write_png(out_path, pixels)


def _table(rows, head):
    out = ["<table><tr>" + "".join(f"<th>{html.escape(str(h))}</th>" for h in head) + "</tr>"]
    for row in rows:
        out.append("<tr>" + "".join(f"<td>{html.escape(str(c)) if c is not None else ''}</td>" for c in row) + "</tr>")
    return "\n".join(out + ["</table>"])


def scorecard_html(card):
    parts = ["<h2>Scorecard</h2>"]
    t, u = card["totals"], card["unique"]
    parts.append(f"<p class=big><b class=error>{u['error']}</b> errors · <b class=warn>{u['warn']}</b> warnings · "
                 f"<b class=info>{u['info']}</b> info <span class=muted>(unique by check, screen and widget; "
                 f"{t['error']} / {t['warn']} / {t['info']} across every size, theme and beat)</span></p>")
    checks = sorted(card["unique_by_check"])
    parts.append(_table([(c, card["unique_by_check"][c].get("error", ""), card["unique_by_check"][c].get("warn", ""),
                          card["unique_by_check"][c].get("info", ""), sum(card["by_check"].get(c, {}).values()))
                         for c in checks], ["check", "errors", "warnings", "info", "instances"]))
    parts.append("<h3>By owner (wave B group)</h3>")
    parts.append(_table([(g, v.get("error", ""), v.get("warn", ""), v.get("info", ""))
                         for g, v in card["by_group"].items()], ["group", "errors", "warnings", "info"]))
    tr = card["truth"]["beats"]
    if tr:
        parts.append("<h3>Truth: displayed pitch vs the known voice</h3>")
        parts.append(_table([(m["scenario"], m["beat"], m["kind"], m.get("f0"), m["median_error_pct"],
                              m.get("median_signed_pct"), m["within_pct"], m["settle_s"], m["onset_ms"], m["frames"])
                             for m in tr], ["scenario", "beat", "voice", "f0", "median |err| %", "median err %",
                                            "% frames in tol", "settle s", "onset→readout ms", "frames"]))
    lvt = card["live_vs_take"]["takes"]
    if lvt:
        parts.append("<h3>Live vs take (real takes replayed)</h3>")
        parts.append(_table([(m["take"], m["live_median_hz"], m.get("track_hz"), m.get("track_diff_st"),
                              m.get("report_hz"), m.get("diff_st"), m["frames"]) for m in lvt],
                            ["take", "live median Hz", "take track Hz (same s)", "diff st", "report Hz",
                             "diff vs report st", "frames"]))
    lat = card["latency"]
    if lat:
        parts.append("<h3>Responsiveness</h3>")
        parts.append(_table([(k, v.get("p50"), v.get("p95"), v.get("n"), v.get("budget_ms", ""))
                             for k, v in lat.items()], ["measure", "p50 ms", "p95 ms", "n", "budget"]))
    reach = card["reach"]
    if reach["features"]:
        parts.append(f"<h3>Reachability: {reach['reached']} of {reach['total']} features</h3>")
        parts.append(_table([(r["feature"], r["name"], "yes" if r["reached"] else ("conditional" if r.get("conditional")
                                                                                    else "NO"), r["clicks"],
                              r.get("failed_step") or "") for r in reach["features"]],
                            ["feature", "name", "reached", "clicks from dashboard", "failed at"]))
    folds = {}
    for f in card["fold"]:
        folds[(f["screen"], f["size"])] = max(folds.get((f["screen"], f["size"]), 0), f["overflow_y"])
    if folds:
        parts.append("<h3>Scrolling needed (px, worst)</h3>")
        parts.append(_table([(s, z, v) for (s, z), v in sorted(folds.items())], ["screen", "size", "scrolls px"]))
    return "\n".join(parts)


CSS = """
:root { --bg:#f6f7f9; --fg:#1b1f27; --muted:#5d6676; --card:#fff; --border:#d8dde5; }
@media (prefers-color-scheme: dark) { :root { --bg:#15181d; --fg:#eef1f6; --muted:#929cab; --card:#1d2129; --border:#333a46; } }
body { background:var(--bg); color:var(--fg); font:14px/1.45 "Segoe UI", system-ui, sans-serif; margin:24px; }
table { border-collapse:collapse; margin:8px 0 16px; font-size:13px; }
td, th { border:1px solid var(--border); padding:3px 8px; text-align:left; vertical-align:top; }
.error { color:#e5483f; } .warn { color:#c98a0b; } .info { color:#2f8fd8; } .muted { color:var(--muted); }
.big { font-size:18px; } .beat { background:var(--card); border:1px solid var(--border); border-radius:10px;
  padding:12px; margin:12px 0; } .thumbs { display:flex; flex-wrap:wrap; gap:10px; }
.thumbs figure { margin:0; } .thumbs img { width:%dpx; border:1px solid var(--border); border-radius:6px; }
figcaption { font-size:12px; color:var(--muted); } ul.f { margin:6px 0 0 0; padding-left:18px; }
""" % THUMB_WIDTH


def build(run_dir, findings, card, mark_images=True):
    """Write index.html (and the .marked.png files) for a run; returns the index path."""
    run_dir = Path(run_dir)
    by_stem = {}
    for f in findings:
        if f.get("size") and f.get("theme"):
            stem = f"{f['beat']}-{f['theme']}-{f['size']}"
            by_stem.setdefault((f["scenario"], stem), []).append(f)
    body = [f"<h1>VoiceLab UI lab · {html.escape(run_dir.name)}</h1>", scorecard_html(card)]
    for sdir in sorted(p.parent for p in run_dir.glob("*/beats.json") if not p.parent.name.startswith("_")):
        data = read_json(sdir / "beats.json", {})
        sc = data.get("scenario", {})
        body.append(f"<h2 id='{html.escape(sc.get('id', sdir.name))}'>{html.escape(sc.get('id', sdir.name))}"
                    f" <span class=muted>{html.escape(sc.get('title', ''))}</span></h2>")
        loose = [f for f in findings if f["scenario"] == sc.get("id") and not f.get("size")]
        if loose:
            body.append("<ul class=f>" + "".join(_li(f) for f in loose) + "</ul>")
        for beat in data.get("beats", []):
            figs, items = [], {}
            for c in beat.get("captures", []):
                stem = c["stem"]
                found = by_stem.get((sc.get("id"), stem), [])
                img = f"{sdir.name}/{stem}.png"
                if any(f["severity"] != "info" for f in found) and mark_images:
                    mark(sdir / f"{stem}.png", found, sdir / f"{stem}.marked.png")
                    img = f"{sdir.name}/{stem}.marked.png"
                fulls = "".join(f" · <a href='{sdir.name}/{x['file']}'>full</a>" for x in c.get("full", []))
                figs.append(f"<figure><a href='{img}'><img loading=lazy src='{img}'></a><figcaption>{c['theme']} "
                            f"{c['size'][0]}×{c['size'][1]} · {c['screen']} · {len(found)} findings · "
                            f"<a href='{sdir.name}/{stem}.png'>clean</a> · <a href='{sdir.name}/{stem}.tree.json'>tree"
                            f"</a> · <a href='{sdir.name}/{stem}.state.json'>state</a>{fulls}</figcaption></figure>")
                for f in found:
                    items.setdefault((f["check"], f["widget_path"], f["message"]), []).append(f)
            err = f" <b class=error>{html.escape(beat['error'])}</b>" if beat.get("error") else ""
            lis = "".join(_li(fs[0], fs[1:]) for fs in sorted(items.values(), key=lambda fs: (
                {"error": 0, "warn": 1, "info": 2}[fs[0]["severity"]], fs[0]["id"])))
            body.append(f"<div class=beat><b>{html.escape(beat['name'])}</b>{err}<div class=thumbs>{''.join(figs)}"
                        f"</div><ul class=f>{lis}</ul></div>")
    page = (f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>UI lab run</title><style>{CSS}</style></head><body>{''.join(body)}</body></html>")
    index = run_dir / "index.html"
    index.write_text(page, encoding="utf-8")
    return index


def _li(f, others=()):
    ids = ", ".join(f"#{g['id']}" for g in [f, *others])
    return (f"<li><b class={f['severity']}>{ids} {f['severity']}</b> <code>{html.escape(f['check'])}</code> "
            f"<code>{html.escape(f['widget_path'])}</code> {html.escape(f['message'])}</li>")
