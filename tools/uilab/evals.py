"""The evals: pure checks over what the lab captured (the widget tree, the core's state and the scenario's truth,
the frame and event logs, the screenshots). Each check returns findings:
    {check, severity (error|warn|info), screen, scenario, beat, size, theme, widget_path, message, evidence}
plus "rect" (where to draw it; never part of a finding's identity). No Qt."""
import math
import re

import numpy as np

import report
from tools.uilab import png, vocab
from tools.uilab.colors import contrast

# --- SEVERITIES: one per check, each with its reason ---
SEVERITY = {
    "truncation": "error",       # text the user cannot read in full is wrong information
    "overlap": "error",          # one widget hides another
    "offscreen": "error",        # shown but out of reach
    "window_size": "error",      # the window cannot be made the size the spec asks for
    "fold": "error",             # spec 05: at 1920x1080 a desktop user should never need to scroll the dashboard
    "small_target": "warn",      # hard to hit, but it still works
    "small_text": "warn",        # hard to read, but readable
    "contrast": "error",         # WCAG 2.1 AA 1.4.3 failure: some people cannot read it
    "indicator_contrast": "warn",  # WCAG 1.4.11; zone bands also carry text labels and gaps (theme.py)
    "consistency": "error",      # one quantity, two values, no label saying why: the user can't trust either
    "unlabelled_pair": "warn",   # two moments shown side by side without saying which is which
    "truth": "error",            # the readout is wrong against a known voice
    "truth_moving": "warn",      # off by more than the tolerance while the pitch moves (throttled text)
    "settle": "warn",            # right, but slow to get there
    "zone": "error",             # the zone shown is not the zone of the known value
    "live_vs_take": "warn",      # live and the take's report disagree by more than a semitone
    "readout_range": "error",    # phase 10 rule: the readout always sits on the graph or an arrow says where
    "vocabulary": "warn",        # inconsistent words for one thing make the user wonder if they differ
    "source_text": "warn",       # text cut short before it reached the UI (a device name, a status)
    "jargon": "warn",            # plain words first; jargon belongs in tooltips and Details
    "axis": "warn",              # a scale without its unit makes the user guess
    "raw_unit": "warn",          # a number with a unit and no scale words means nothing to most people
    "state": "error",            # the core is in a state the screen does not show (or contradicts)
    "listening": "warn",         # nothing says whether the app hears you: the numbers alone have to tell
    "distinguish": "error",      # two different states look the same where the user looks
    "latency": "warn",           # slow, not wrong
    "next_action": "warn",       # a dead end in words: the user is told a problem but not what to do
    "reach": "error",            # a feature can no longer be reached from the dashboard
    "capture": "error",          # a beat failed to run (the lab could not drive the app)
    "exception": "error",        # the app raised in a slot or handler: something did not happen, or may crash
}

# --- THRESHOLDS ---
TRUNCATE_SLACK = 2              # px a text may overhang its box before it counts as cut (rounding, hinting)
PAINTED_SLACK = 8               # px a painted control may be clipped: its own padding (the record button's ring)
TEXT_WIDGETS = ("Label", "Caption", "Headline", "ElidedLabel", "QLabel", "Button", "IconButton", "QPushButton",
                "NumberLabel")
SAMPLED_SLACK = 0.85            # text whose colour is only sampled (no declared ink) must fall below 85 % of the
#                                 threshold: anti-aliased small text rarely reaches its true colour
OVERLAP_MIN_AREA = 9            # px²: smaller intersections are borders touching
TARGET_MIN = 28                 # px: spec 01 A5 (WCAG 2.5.8 asks 24; the spec sets 28 for this app)
TEXT_MIN_PX = 11                # px: spec 01 A5
CONTRAST_TEXT = 4.5             # WCAG 2.1 AA 1.4.3, normal text
CONTRAST_LARGE = 3.0            # WCAG 2.1 AA 1.4.3, large text (>= 24 px, or >= 18.66 px bold)
CONTRAST_INDICATOR = 3.0        # WCAG 2.1 AA 1.4.11, graphical objects
LARGE_TEXT_PX = 24
PITCH_TOLERANCE_HZ = 1.0        # spec 01 B1: one quantity agrees within 1 Hz (or the same rounding)
TRUTH_TOLERANCE = 0.02          # spec 01 B2: within 2 % of the known f0 after settling
MOVING_TOLERANCE = 0.03         # moving voices: the readout's text holds 0.2 s (display.LIVE_TEXT_SECONDS)
#                                 while a 13-semitone glide moves ~0.7 st, so 3 % (own judgement)
NOTE_LAG_S = 0.125              # the readout is the median of the last 0.25 s (live_session.NOTE_WINDOW_SECONDS)
OCTAVE_LOG2 = 0.8               # |log2(shown / known)| past this is an octave error, not a lag
OCTAVE_SHARE_PCT = 5.0          # % of voiced frames an octave off that makes it an error (own judgement)
SETTLE_BUDGET_S = 0.5           # a steady voice should read right within half a second (own judgement)
SETTLE_FRAMES = 4               # frames that must read right in a row to count as settled
LIVE_TAKE_WARN_ST = 1.0         # semitones between the live median and the take's report (one step is audible)
FRAME_BUDGET_MS = 25.0          # one live frame's UI work must fit in one hop (engine.UPDATE_INTERVAL)
READOUT_BUDGET_MS = 150.0       # spec 01 C3: frame publish to visible readout change
ONSET_BUDGET_MS = 450.0         # voice onset to a first readout: the 0.25 s median window + 0.2 s text hold
TAKE_SLACK_MS = 500.0           # spec 01 C3: take visible within the analysis time + 500 ms
TOAST_BUDGET_MS = 100.0         # status event to toast
DIFF_SAME = 1.5                 # mean abs pixel difference (0-255) under which two crops look the same
GOOD_RECORDING = re.compile(r"good recording", re.I)
ZONE_IDS = ("masculine", "overlap", "feminine")
LIVE_SCREENS = ("dashboard", "live")
VOICED_FOR_S = 0.5              # a voice this long must show on the readout (its median window and text hold
#                                 take up to 0.45 s: live_session.NOTE_WINDOW_SECONDS + display.LIVE_TEXT_SECONDS)
PAINT_BUDGET_MS = 25.0          # one repaint of the window should fit in a hop (engine.UPDATE_INTERVAL)
ZONE_LABEL_WORDS = 3            # a text this short is a label (zone wording rules); longer is prose
SHELL_PREFIXES = ("top", "nav", "action_bar", "banner", "toasts", "onboarding", "mic_dialog", "QWidget/Bar",
                  "status_label", "activity")
PATH_GROUPS = [("dashboard/latest", "takes"), ("dashboard/recent", "takes"), ("dashboard/progress", "takes"),
               ("practice", "practice"), ("takes", "takes"), ("progress", "takes"), ("settings", "shell")]
SCREEN_GROUPS = {"dashboard": "live", "live": "live", "takes": "takes", "progress": "takes", "practice": "practice",
                 "settings": "shell", "onboarding": "shell", "mic_check": "shell"}
DIALOG_SCREENS = {"Welcome": "onboarding", "Mic check": "mic_check"}
CODE_GROUPS = {"take_card.py": "takes", "take_graph.py": "takes", "takes_screen.py": "takes", "latest_take.py": "takes",
               "recent_list.py": "takes", "progress_screen.py": "takes", "player_strip.py": "takes",
               "trust_line.py": "takes", "compare_view.py": "takes", "live.py": "live", "dashboard.py": "live",
               "live_graph.py": "live", "live_cards.py": "live", "live_views.py": "live", "practice.py": "practice",
               "exercise_view.py": "practice", "vowel_check.py": "practice", "voice_map_view.py": "practice"}


def group_of(finding):
    """The wave B owner of a finding: by where the widget lives, else by the screen."""
    path = finding.get("widget_path") or ""
    root = path.split("/")[0]
    if root in SHELL_PREFIXES or path.startswith(SHELL_PREFIXES):
        return "shell"
    if path.startswith("code:"):
        return CODE_GROUPS.get(path[5:].split(":")[0], "shell")
    for prefix, group in PATH_GROUPS:
        if path == prefix or path.startswith(prefix + "/"):
            return group
    return SCREEN_GROUPS.get(finding.get("screen"), "shell")


UID_SEGMENT = re.compile(r"\d{8}T\d{6}-[0-9a-f]+")     # a take's uid inside a path (takes/cards.<uid>)
INDEX_SEGMENT = re.compile(r"(\.|#)\d+(?=/|$|#)")       # list positions (tip_rows.0, QWidget#2)


def normal_path(path):
    """A widget path with take uids and list positions folded, so one defect in every take card or list row
    is one ratchet entry."""
    path = re.sub(r"~\d+(?=/|$|#)", "", path or "")
    return INDEX_SEGMENT.sub(lambda m: m.group(1) + "*", UID_SEGMENT.sub("*", path))


def finding_key(f):
    """A finding's identity for the ratchet: check, screen and normalised widget path (no pixels, sizes or
    themes)."""
    return f"{f['check']}|{f.get('screen') or ''}|{normal_path(f.get('widget_path'))}"


# --- the capture each check reads ---

class Capture:
    """One beat at one size and theme: tree widgets, state and meta, with lookups the checks share."""

    def __init__(self, scenario, beat, theme, size, stem, tree, state, png_path=None):
        self.scenario, self.beat, self.theme, self.size, self.stem = scenario, beat, theme, tuple(size), stem
        self.meta = tree.get("meta", {})
        widgets = tree.get("widgets", [])
        if self.meta.get("dialog"):     # a dialog is open: judge it, not the window behind (judged elsewhere)
            widgets = [w for w in widgets if w.get("window") != "main"] or widgets
        self.widgets = widgets
        self.state = state or {}
        self.png_path = png_path
        self.by_path = {w["path"]: w for w in self.widgets}
        dialog = self.meta.get("dialog")
        self.screen = DIALOG_SCREENS.get(dialog, dialog.lower() if dialog else None) or self.state.get("screen") \
            or self.meta.get("screen")

    def finding(self, check, path, message, evidence=None, rect=None, severity=None):
        w = self.by_path.get(path) if path else None
        return {"check": check, "severity": severity or SEVERITY[check], "screen": self.screen,
                "scenario": self.scenario, "beat": self.beat, "size": f"{self.size[0]}x{self.size[1]}",
                "theme": self.theme, "widget_path": path or "", "message": message, "evidence": evidence or {},
                "rect": rect or (w.get("visible_rect") if w else None)}

    def kind(self, kind):
        return [w for w in self.widgets if w.get("kind") == kind]

    def texts(self):
        """Visible texts: (widget, text), including painted labels (zone bars, plots)."""
        for w in self.widgets:
            if w.get("text"):
                yield w, w["text"]
            state = w.get("state") or {}
            if w.get("kind") == "zone_bar":
                for _, label in state.get("drawn_labels", []):
                    yield w, label
            if w.get("kind") == "plot":
                for item in state.get("items", []):
                    if item.get("class") == "TextItem" and item.get("visible") and item.get("text"):
                        yield w, item["text"]
                for axis in (state.get("axes") or {}).values():
                    if axis.get("label"):
                        yield w, axis["label"]
            if w.get("kind") in ("nav", "tabs", "segmented"):
                for item in state.get("items", []):
                    yield w, item["label"]

    def visible_text(self):
        return " \n ".join(t for _, t in self.texts())

    def ancestors(self, w):
        out, parent = [], w.get("parent")
        while parent is not None and parent in self.by_path:
            out.append(self.by_path[parent])
            parent = self.by_path[parent].get("parent")
        return out


def _area(r):
    return max(0, r[2]) * max(0, r[3]) if r else 0


def _inter(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return [x0, y0, max(0, x1 - x0), max(0, y1 - y0)]


# --- A. LAYOUT ---

def check_truncation(cap):
    out = []
    for w in cap.widgets:
        text, fit = w.get("text"), w.get("fit")
        if text and fit:
            if fit.get("elided"):
                out.append(cap.finding("truncation", w["path"], f"Text is cut short with an ellipsis: “{text[:80]}”",
                                       {"text": text, "fit": fit}))
                continue
            need_w, avail_w = fit.get("need_w"), fit.get("avail_w")
            need_h, avail_h = fit.get("need_h"), fit.get("avail_h")
            if w["cls"] in ("QComboBox", "Select"):
                if need_w and avail_w and need_w > avail_w + TRUNCATE_SLACK:
                    out.append(cap.finding("truncation", w["path"], f"Choice “{text}” is wider than its box",
                                           {"need_w": need_w, "avail_w": avail_w}, severity="warn"))
                continue
            if need_w and avail_w is not None and need_w > avail_w + TRUNCATE_SLACK:
                out.append(cap.finding("truncation", w["path"],
                                       f"Text needs {need_w} px but has {avail_w} px: “{text[:80]}”",
                                       {"text": text, "need_w": need_w, "avail_w": avail_w}))
                continue
            if need_h and avail_h is not None and need_h > avail_h + TRUNCATE_SLACK:
                out.append(cap.finding("truncation", w["path"],
                                       f"Wrapped text needs {need_h} px of height but has {avail_h} px: “{text[:80]}”",
                                       {"text": text, "need_h": need_h, "avail_h": avail_h}))
                continue
        clip = w.get("clip")
        if clip and (text or w.get("clickable")) and _area(w.get("visible_rect")) > 0:
            slack = TRUNCATE_SLACK if w["cls"] in TEXT_WIDGETS else PAINTED_SLACK
            horizontal = clip["dx"] > slack
            vertical = clip["dy"] > slack and not clip["scroll"]
            if (horizontal or vertical) and clip["by"] != "window":
                what = f"“{text[:60]}”" if text else w["cls"]
                out.append(cap.finding("truncation", w["path"], f"{what} is cut off by {clip['by'] or 'its parent'} "
                                       f"({clip['dx']} px across, {clip['dy']} px down)", {"clip": clip}))
        if w.get("kind") == "plot":
            for item in (w.get("state") or {}).get("items", []):
                if item.get("class") == "TextItem" and item.get("visible") and item.get("text") \
                        and item.get("inside") is False:
                    r = item.get("rect")
                    rect = [w["rect"][0] + r[0], w["rect"][1] + r[1], r[2], r[3]] if r else None
                    out.append(cap.finding("truncation", w["path"] + "/" + item["name"],
                                           f"Graph label “{item['text']}” runs outside the plot", {"item": item},
                                           rect=rect, severity="warn"))
    return out


def check_overlap(cap):
    out = []
    children = {}
    for w in cap.widgets:
        if w.get("parent") is not None and w.get("window") == "main" and _area(w.get("visible_rect")) > 0:
            children.setdefault(w["parent"], []).append(w)
    skip_kinds = {"toast_stack", "toast"}
    for parent, kids in children.items():
        pw = cap.by_path.get(parent)
        if pw and pw.get("kind") == "plot":
            continue
        kids = [k for k in kids if k.get("kind") not in skip_kinds]
        for i, a in enumerate(kids):
            for b in kids[i + 1:]:
                inter = _inter(a["visible_rect"], b["visible_rect"])
                if _area(inter) >= OVERLAP_MIN_AREA:
                    out.append(cap.finding("overlap", b["path"], f"{b['cls']} overlaps {a['cls']} ({a['path']}) by "
                                           f"{inter[2]}×{inter[3]} px", {"other": a["path"], "intersection": inter},
                                           rect=inter))
    return out


def check_covered(cap):
    """A floating overlay (toast, popup) over a control the user may need: it can't be clicked meanwhile."""
    out = []
    dialogs = {w["path"] for w in cap.kind("dialog")}
    for w in cap.widgets:
        if w.get("covered_by") in dialogs:      # a dialog is meant to sit on top
            continue
        if w.get("covered_by") and w.get("clickable") and w.get("enabled"):
            out.append(cap.finding("overlap", w["path"], f"{w['cls']} “{(w.get('text') or '')[:40]}” is covered by "
                                   f"{w['covered_by']}", {"covered_by": w["covered_by"]}, severity="warn"))
    return out


def check_offscreen(cap):
    out = []
    W, H = cap.meta.get("size") or cap.size
    if tuple(cap.meta.get("size") or cap.size) != cap.size:
        out.append(cap.finding("window_size", "", f"The window is {W}×{H}, not the {cap.size[0]}×{cap.size[1]} asked "
                               "for: its minimum size is larger", {"actual": [W, H]}))
    for w in cap.widgets:
        clip = w.get("clip")
        if not clip or w.get("window") != "main":
            continue
        if clip["by"] == "window":
            out.append(cap.finding("offscreen", w["path"], f"{w['cls']} sticks out of the window "
                                   f"({clip['dx']} px across, {clip['dy']} px down)", {"clip": clip}))
        elif not clip["scroll"] and not w.get("scroll") and _area(w.get("visible_rect")) == 0 and                 (w.get("text") or w.get("clickable")):
            out.append(cap.finding("offscreen", w["path"], f"{w['cls']} is shown but entirely cut off by "
                                   f"{clip['by']}", {"clip": clip}, rect=w["rect"]))
    return out


def check_fold(cap):
    """Spec 05: the dashboard at 1920x1080 fits without scrolling. Elsewhere the scroll amount is info."""
    out = []
    for w in cap.widgets:
        sa = w.get("scroll_area")
        if not sa or sa["overflow_y"] <= 0:
            continue
        message = f"{cap.screen} scrolls {sa['overflow_y']} px at {cap.size[0]}×{cap.size[1]}"
        severity = "info"
        if cap.screen == "dashboard" and cap.size == (1920, 1080):
            severity = "error"
            message = "The dashboard needs scrolling at 1920×1080: " + message
        elif cap.screen == "live" and cap.size == (1920, 1080):
            severity = "warn"
        out.append(cap.finding("fold", w["path"], message, {"overflow_y": sa["overflow_y"],
                                                            "viewport": sa["viewport"]}, severity=severity))
        cut = [c for c in cap.widgets if c.get("scroll") == w["path"] and c.get("text") and c.get("visible_rect")
               and 0 < c["visible_rect"][3] < c["rect"][3] - TRUNCATE_SLACK]
        if cut and cap.screen in LIVE_SCREENS:
            out.append(cap.finding("fold", w["path"] + "/edge", f"The bottom of the {cap.screen} cuts through "
                                   f"{len(cut)} text(s) at {cap.size[0]}×{cap.size[1]}: " +
                                   ", ".join(f"“{c['text'][:24]}”" for c in cut[:4]),
                                   {"cut": [c["path"] for c in cut]}, rect=cut[0]["visible_rect"], severity="warn"))
    return out


def check_axes(cap):
    """A graph's time axis says what its numbers are (seconds), and a live graph has one."""
    out = []
    for w in cap.kind("plot"):
        if w.get("role") != "graph.pitch" or _area(w.get("visible_rect")) == 0:
            continue
        bottom = (w["state"].get("axes") or {}).get("bottom") or {}
        if not bottom.get("shows_values"):
            out.append(cap.finding("axis", w["path"], "The live graph has no time axis: nothing says how far back "
                                   "the line goes", {"axis": bottom}))
        elif not bottom.get("label"):
            out.append(cap.finding("axis", w["path"], "The time axis has numbers but no unit (seconds? ago?)",
                                   {"axis": bottom}))
    return out


def check_targets(cap):
    out = []
    for w in cap.widgets:
        if w.get("clickable") and w.get("enabled") and _area(w.get("visible_rect")) > 0:
            for t in w.get("targets") or [{"key": None, "rect": w["rect"]}]:
                r = t["rect"]
                if r[2] < TARGET_MIN or r[3] < TARGET_MIN:
                    path = w["path"] + (f"#{t['key']}" if t["key"] else "")
                    out.append(cap.finding("small_target", path, f"Click target {r[2]}×{r[3]} px is under "
                                           f"{TARGET_MIN}×{TARGET_MIN}", {"size": r[2:]}, rect=r))
        px = w.get("font_px")
        if w.get("text") and px and px < TEXT_MIN_PX:
            out.append(cap.finding("small_text", w["path"], f"Text at {px} px is under {TEXT_MIN_PX} px: "
                                   f"“{w['text'][:60]}”", {"font_px": px}))
    return out


def check_contrast(cap):
    out = []
    for w in cap.widgets:
        c = w.get("colors")
        if not c or not w.get("enabled") or not w.get("text") or not c.get("solid"):
            continue
        px = w.get("font_px") or 14
        need = CONTRAST_LARGE if px >= LARGE_TEXT_PX else CONTRAST_TEXT
        sampled_only = not c.get("declared_drawn")
        if c["contrast"] < need * (SAMPLED_SLACK if sampled_only else 1.0):
            how = "sampled" if sampled_only else "drawn"
            out.append(cap.finding("contrast", w["path"], f"Text contrast {c['contrast']:.2f}:1 ({how}) is under "
                                   f"{need}:1 ({c['fg']} on {c['bg']}): “{w['text'][:60]}”", c))
    for w in cap.kind("zone_bar"):
        low = [dict(band, contrast=round(contrast(band["fill"], band["behind"]), 2)) for band in
               w.get("band_colors") or []]
        low = [b for b in low if b["contrast"] < CONTRAST_INDICATOR]
        if low:
            out.append(cap.finding("indicator_contrast", w["path"], "Zone band(s) " + ", ".join(
                f"{b['id']} {b['fill']} at {b['contrast']:.2f}:1" for b in low) + f" against what they sit on "
                f"({low[0]['behind']}); WCAG 1.4.11 asks {CONTRAST_INDICATOR}:1", {"bands": low},
                rect=w["visible_rect"]))
    return out


# --- B. TRUTH AND CONSISTENCY ---

def _view_root(path):
    for root in ("dashboard/live", "live/view"):
        if path == root or path.startswith(root + "/"):
            return root
    return None


def pitch_displays(cap):
    """[(view root, what, Hz, widget)] of every live display of the pitch: the big readout, the Pitch card,
    the graph's dot (or its off-scale arrow) and the plane's dot."""
    out = []
    for w in cap.widgets:
        root = _view_root(w["path"])
        if root is None:
            continue
        if w.get("role") == "readout.pitch":
            hz = vocab.hz_values(w.get("text"))
            if hz:
                out.append((root, "readout", hz[0], w))
        elif w.get("kind") == "live_card" and w["state"].get("key") == "pitch":
            hz = vocab.hz_values(w["state"].get("value"))
            if hz:
                out.append((root, "pitch card", hz[0], w))
        elif w.get("kind") == "plot" and w.get("role") in ("graph.pitch", "plane"):
            for item in w["state"].get("items", []):
                if not item.get("visible"):
                    continue
                if w["role"] == "graph.pitch" and item["name"] == "dot" and item.get("points"):
                    y = item["points"][-1][1]
                    if y is not None:
                        out.append((root, "graph dot", round(2 ** y, 2), w))
                if w["role"] == "graph.pitch" and item["name"] == "marker" and item.get("text"):
                    hz = vocab.hz_values(item["text"])
                    if hz:
                        out.append((root, "graph arrow", hz[0], w))
                if w["role"] == "plane" and item["name"] == "current" and item.get("last_finite"):
                    y = item["last_finite"][1]
                    if y is not None:
                        out.append((root, "plane dot", round(2 ** y, 2), w))
    return out


def _labelled(cap, w, pattern):
    """True when the widget, its card or its view carries visible words matching `pattern`."""
    for node in [w] + cap.ancestors(w)[:4]:
        texts = [node.get("text") or ""]
        texts += [c.get("text") or "" for c in cap.widgets if c.get("parent") == node["path"]]
        if any(pattern.search(t) for t in texts):
            return True
    return False


def _rounded_same(a, b):
    return round(a) == round(b)


def check_consistency(cap):
    """One quantity, one value: the live pitch displays agree (or say why not); live and last take are
    labelled as such when both show a pitch."""
    out = []
    displays = pitch_displays(cap)
    by_root = {}
    for d in displays:
        by_root.setdefault(d[0], []).append(d)
    for root, items in by_root.items():
        for i, (_, what_a, a, wa) in enumerate(items):
            for _, what_b, b, wb in items[i + 1:]:
                if abs(a - b) <= PITCH_TOLERANCE_HZ or _rounded_same(a, b):
                    continue
                # displays labelled differently ("now" vs "last 3 s") may differ on purpose: info only
                deliberate = _labelled(cap, wa, vocab.NOW_WORDS) != _labelled(cap, wb, vocab.NOW_WORDS)
                out.append(cap.finding("consistency", wb["path"],
                                       f"Pitch reads {a:.0f} Hz on the {what_a} and {b:.0f} Hz on the {what_b} at "
                                       "the same moment" + ("" if deliberate else ", and nothing says why they differ"),
                                       {"a": {"what": what_a, "hz": a, "path": wa["path"]},
                                        "b": {"what": what_b, "hz": b, "path": wb["path"]}},
                                       severity="info" if deliberate else None))
    live = next((d for d in displays if d[1] == "readout"), None)
    if live is not None:
        for w in cap.widgets:
            if w.get("context") != "last_take" or not w.get("text"):
                continue
            hz = vocab.hz_values(w["text"])
            if not hz or "pitch" not in (w["text"] + " " + _sibling_text(cap, w)).lower():
                continue
            if abs(hz[0] - live[2]) <= PITCH_TOLERANCE_HZ:
                continue
            now_ok = _labelled(cap, live[3], vocab.NOW_WORDS)
            take_ok = _labelled(cap, w, vocab.TAKE_WORDS) or any(
                vocab.TAKE_WORDS.search(a.get("text") or "") for a in cap.ancestors(w) + _titles(cap, w))
            if not (now_ok and take_ok):
                missing = " and ".join(x for x, ok in (("the live readout doesn't say “now”", now_ok),
                                                         ("the take's pitch doesn't say “last take”", take_ok)) if not ok)
                out.append(cap.finding("unlabelled_pair", w["path"],
                                       f"Live pitch {live[2]:.0f} Hz sits beside the last take's {hz[0]:.0f} Hz: "
                                       f"{missing}", {"live": live[2], "take": hz[0], "live_path": live[3]["path"]}))
    return out


def _sibling_text(cap, w):
    return " ".join(c.get("text") or "" for c in cap.widgets if c.get("parent") == w.get("parent") and c is not w)


def _titles(cap, w):
    """The title labels of the cards around a widget."""
    out = []
    for a in cap.ancestors(w):
        t = cap.by_path.get(a["path"] + "/title_label")
        if t:
            out.append(t)
    return out


def check_readout_range(cap):
    out = []
    for w in cap.widgets:
        if w.get("role") != "graph.pitch":
            continue
        s = w["state"]
        hz, shown = s.get("readout_hz"), s.get("shown_hz")
        if hz is None or not shown:
            continue
        marker = next((i for i in s.get("items", []) if i["name"] == "marker"), {})
        if not (shown[0] <= hz <= shown[1]) and not marker.get("visible"):
            out.append(cap.finding("readout_range", w["path"], f"The readout's {hz:.0f} Hz is off the graph "
                                   f"({shown[0]:.0f}–{shown[1]:.0f} Hz) and no arrow says so", {"hz": hz, "axis": shown}))
    return out


def check_vocabulary(cap):
    out = []
    zone_words = {}
    for w, text in cap.texts():
        state = w.get("state") or {}
        # zone labels, not prose: bar and graph labels, zone chips, and short label texts
        label_like = len(text.split()) <= ZONE_LABEL_WORDS
        zoned = w.get("kind") in ("zone_bar", "plot") or (w.get("kind") == "chip" and state.get("kind") in ZONE_IDS) \
            or w["cls"] in ("Label", "Caption", "QLabel")
        if zoned and label_like and w.get("kind") not in ("select", "segmented"):
            for zone, word in vocab.zone_wordings(text):
                zone_words.setdefault(zone, {}).setdefault(word if word in vocab.ZONE_ALLOWED else word, w["path"])
                if word not in vocab.ZONE_ALLOWED and not any(word in a for a in vocab.ZONE_ALLOWED):
                    out.append(cap.finding("vocabulary", w["path"], f"Zone word “{word}” is not one of the app's "
                                           f"zone labels ({', '.join(sorted(vocab.ZONE_ALLOWED))})", {"text": text}))
    for zone, words in zone_words.items():
        forms = {re.sub(r"\s+", " ", w.lower()) for w in words}
        if len(forms) > 1:
            first = cap.by_path.get(sorted(words.values())[0])
            out.append(cap.finding("vocabulary", f"words:zone.{zone}", f"The {zone} zone is called "
                                   f"{' and '.join(sorted(words))} on one screen", {"wordings": words},
                                   rect=first.get("visible_rect") if first else None))
    names = {}
    for w in cap.widgets:
        text = (w.get("text") or "").strip().rstrip(":")
        key = vocab.NAME_TO_QUANTITY.get(text.lower())
        if key and w["cls"] in ("Label", "Caption", "QLabel"):
            names.setdefault(key, {}).setdefault(text, w["path"])
    for w in cap.kind("plot"):
        for axis in (w["state"].get("axes") or {}).values():
            label = axis.get("label") or ""
            for name, key in vocab.NAME_TO_QUANTITY.items():
                if label.lower().startswith(name) and len(name) > 4:
                    names.setdefault(key, {}).setdefault(label.split(" (")[0].split(":")[0], w["path"])
    for key, seen in names.items():
        distinct = {n.lower() for n in seen}
        if len(distinct) > 1:
            first = cap.by_path.get(sorted(seen.values())[0])
            out.append(cap.finding("vocabulary", f"words:name.{key}", f"One quantity under {len(seen)} names on "
                                   f"one screen: {', '.join(sorted(seen))}", {"quantity": key, "names": seen},
                                   rect=first.get("visible_rect") if first else None))
    units = {}
    for w, text in cap.texts():
        for m in re.finditer(r"\d\s*(st|semitones?)\b", text):
            units.setdefault("semitones" if m.group(1).startswith("semi") else "st", w["path"])
    if len(units) > 1:
        first = cap.by_path.get(sorted(units.values())[0])
        out.append(cap.finding("vocabulary", "words:unit.semitones", "Semitones are written both “st” and "
                               "“semitones” on one screen", {"where": units},
                               rect=first.get("visible_rect") if first else None))
    for w in cap.widgets:
        text = w.get("text") or ""
        if text and vocab.unbalanced(text) and len(text) > 6:
            out.append(cap.finding("source_text", w["path"], f"Text opens a bracket it never closes (cut short "
                                   f"before it reached the screen?): “{text[:80]}”", {"text": text}))
    for w in cap.kind("status_dot"):
        label = w["state"].get("label") or ""
        if vocab.unbalanced(label):
            out.append(cap.finding("source_text", w["path"], f"Text opens a bracket it never closes (cut short "
                                   f"before it reached the screen?): “{label}”", {"text": label}))
    return out


def _in_details(cap, w):
    if any(seg in w["path"] for seg in vocab.DETAIL_SEGMENTS):
        return True
    return any(a.get("kind") == "collapsible" and (a.get("state") or {}).get("title", "").lower() in
               ("details", "all numbers") for a in cap.ancestors(w))


def check_jargon(cap):
    out = []
    seen = set()
    for w, text in cap.texts():
        if _in_details(cap, w) or w.get("kind") == "help":
            continue
        for term in vocab.jargon(text):
            key = (w["path"], term)
            if key in seen:
                continue
            seen.add(key)
            out.append(cap.finding("jargon", w["path"], f"Jargon “{term}” in visible text: “{text[:80]}”",
                                   {"term": term, "text": text}))
    for w in cap.kind("live_card"):
        value = w["state"].get("value") or ""
        bar = cap.by_path.get(w["path"] + "/bar")
        if vocab.LONE_DB.match(value.strip()) and bar and not (bar.get("state") or {}).get("zones"):
            ends = [e for e in w["state"].get("ends") or [] if e]
            out.append(cap.finding("raw_unit", w["path"] + "/value", f"“{value}” on {w['state'].get('name')} has "
                                   "no zones to say what it means" + (f" (only the ends: {' / '.join(ends)})" if ends
                                                                     else ""), {"value": value, "ends": ends}))
    return out


# --- C. STATE LEGIBILITY ---

def _visible_kind(cap, kind):
    return [w for w in cap.kind(kind) if _area(w.get("visible_rect")) > 0]


def check_state(cap):
    """The state oracle: each core state needs its visible signature, and nothing visible may contradict it."""
    out, s = [], cap.state
    if not s or cap.meta.get("dialog"):
        return out
    text = cap.visible_text()
    records = _visible_kind(cap, "record_button")
    rec = records[0] if records else None
    if s.get("recording"):
        ok = rec is not None and rec["state"]["state"] == "recording" and re.match(r"\d+:\d\d", rec["state"]["caption"])
        if not ok and not re.search(r"\brecording\b", text, re.I):
            out.append(cap.finding("state", rec["path"] if rec else "", "Recording, but nothing on this screen says so "
                                   "(no record button showing the time, no “recording” anywhere)", {"screen": cap.screen}))
    if s.get("analyzing"):
        spinning = (rec is not None and rec["state"]["state"] == "analysing") or bool(_visible_kind(cap, "spinner"))
        if not spinning:
            out.append(cap.finding("state", rec["path"] if rec else "top/activity",
                                   "A take is being analysed, but no spinner shows it on this screen", {}))
    jobs = s.get("jobs") or []
    activity = next(iter(_visible_kind(cap, "activity")), None)
    for job in jobs:
        label = job.get("label") or job.get("kind") or ""
        words = label.split(":")[0].strip("… .")[:40].lower()
        if job.get("kind") == "analysis" and rec is not None and rec["state"]["state"] == "analysing":
            continue                    # the record button's spinner and "Analysing…" name the new take's job
        if words and words not in text.lower():
            out.append(cap.finding("state", activity["path"] if activity else "top/activity",
                                   f"“{label}” is running, but nothing visible names it" +
                                   (f" (the top bar says “{activity['state']['title']}”)" if activity else ""),
                                   {"job": job}))
    if jobs and activity and activity["state"]["title"] == "Nothing running":
        out.append(cap.finding("state", activity["path"], f"“Nothing running” while {len(jobs)} job(s) run",
                               {"jobs": jobs}))
    if not jobs and activity and activity["state"]["count"]:
        out.append(cap.finding("state", activity["path"], f"The top bar says “{activity['state']['title']}” but no job "
                               "is running", {}))
    if rec is not None:
        want = s.get("record_state")
        if want and rec["state"]["state"] != want:
            out.append(cap.finding("state", rec["path"], f"The record button shows {rec['state']['state']} while the "
                                   f"core is {want}", {"shown": rec["state"]["state"], "core": want}))
    if s.get("mic_available") is False:
        mic = next((w for w in _visible_kind(cap, "status_dot") if w.get("role") == "mic_chip"), None)
        if mic is None or mic["state"]["state"] != "error":
            out.append(cap.finding("state", mic["path"] if mic else "top/mic", "The mic is unavailable but the top bar's "
                                   "mic chip does not show an error", {"chip": mic and mic["state"]}))
        if rec is not None and (rec["state"]["state"] != "disabled" or "mic" not in rec["state"]["caption"].lower()):
            out.append(cap.finding("state", rec["path"], "The mic is unavailable but the record button doesn't say so "
                                   f"(it shows “{rec['state']['caption']}”)", {"record": rec["state"]}))
    banner_info = s.get("banner_info") or (s.get("snapshot") or {}).get("banner")
    banners = [w for w in _visible_kind(cap, "banner") if w.get("window") == "main"
               and w.get("context") not in ("last_take", "take", "recent")]
    if banner_info and cap.screen in LIVE_SCREENS + ("practice",) and len(banners) != 1:
        out.append(cap.finding("state", banners[0]["path"] if banners else "banner",
                               f"The input can't be trusted (“{banner_info.get('text')}”) but {len(banners)} banners "
                               "show (one is right)", {"banners": [b["path"] for b in banners]}))
    if s.get("loading"):
        if _visible_kind(cap, "empty_state") and not (_visible_kind(cap, "skeleton") or _visible_kind(cap, "spinner")):
            out.append(cap.finding("state", _visible_kind(cap, "empty_state")[0]["path"],
                                   "Takes are loading, but an empty state shows instead of a skeleton", {}))
    elif s.get("take_count") == 0 and cap.screen in ("dashboard", "takes"):
        empties = [w for w in _visible_kind(cap, "empty_state") if w["state"].get("action")]
        if not empties:
            out.append(cap.finding("state", cap.screen, "No takes yet, and no empty state offers a next step", {}))
    nav = next(iter(cap.kind("nav")), None)
    if nav is not None and "takes_in_progress" in s:
        badge = nav["state"]["badges"].get("takes")
        want = str(s["takes_in_progress"]) if s["takes_in_progress"] else None
        if badge != want:
            out.append(cap.finding("state", nav["path"] + "#takes", f"The Takes badge says {badge or 'nothing'} while "
                                   f"{s['takes_in_progress']} take(s) are being analysed", {"badge": badge, "want": want}))
    if GOOD_RECORDING.search(text):
        for chip in _visible_kind(cap, "confidence"):
            if chip["state"]["level"] == "none" and not chip["state"].get("reason"):
                out.append(cap.finding("state", chip["path"], "“Good recording” next to a “Can't judge” with no reason",
                                       {}))
    return out


def check_listening(cap):
    """On the live screens, the user can tell whether the app hears a voice right now."""
    out, s = [], cap.state
    if cap.screen not in LIVE_SCREENS or not s.get("snapshot") or cap.meta.get("dialog"):
        return out
    snap = s["snapshot"]
    readout = next((w for w in cap.widgets if w.get("role") == "readout.pitch" and _view_root(w["path"])), None)
    if readout is None:
        return out
    shows_number = bool(vocab.hz_values(readout.get("text")))
    text = cap.visible_text()
    if snap.get("voiced") and s.get("voiced_seconds", 1.0) >= VOICED_FOR_S and not shows_number:
        out.append(cap.finding("state", readout["path"], "A voice is heard right now but the readout shows no pitch",
                               {"readout": readout.get("text")}))
    if snap.get("voiced") and not vocab.HEARD_WORDS.search(text):
        out.append(cap.finding("listening", readout["path"], "Nothing says the app hears you: only the numbers move "
                               "(no “listening” or “hearing you” state)", {}))
    if not snap.get("voiced") and not shows_number and not vocab.WAITING_WORDS.search(text):
        out.append(cap.finding("listening", readout["path"], "Silent, and nothing says the app is waiting for your "
                               "voice (the readout is just “—”)", {}))
    return out


def check_next_action(cap):
    out = []
    for w in _visible_kind(cap, "empty_state"):
        if not w["state"].get("action"):
            out.append(cap.finding("next_action", w["path"], f"Empty state “{w['state']['text']}” offers no next "
                                   "step", w["state"]))
    for w in _visible_kind(cap, "toast"):
        st = w["state"]
        if st["kind"] in ("error", "warning") and not st.get("action") and not vocab.NEXT_STEP.search(st["text"]):
            out.append(cap.finding("next_action", w["path"], f"{st['kind'].capitalize()} toast says what went wrong "
                                   f"but not what to do: “{st['text'][:80]}”", st))
    for w in _visible_kind(cap, "banner"):
        if not w["state"].get("action"):
            out.append(cap.finding("next_action", w["path"], f"Banner “{w['state']['text'][:60]}” has no action",
                                   w["state"]))
    for w in _visible_kind(cap, "metric_card"):
        st = w["state"]
        if st.get("unavailable") or st.get("confidence") == "none":
            card_texts = " ".join(c.get("text") or "" for c in cap.widgets if c["path"].startswith(w["path"] + "/"))
            buttons = [c for c in cap.widgets if c["path"].startswith(w["path"] + "/") and c.get("clickable")
                       and c["cls"] in ("Button", "QPushButton")]
            if not buttons and not vocab.NEXT_STEP.search(card_texts):
                out.append(cap.finding("next_action", w["path"], f"“Can't judge” on {st.get('name')} says why "
                                       f"(“{st.get('headline', '')[:60]}”) but not what to do", st))
    return out


CAPTURE_CHECKS = [check_truncation, check_overlap, check_covered, check_offscreen, check_fold, check_axes, check_targets, check_contrast,
                  check_consistency, check_readout_range, check_vocabulary, check_jargon, check_state,
                  check_listening, check_next_action]


def check_capture(cap):
    """Every per-capture check on one capture."""
    out = []
    for check in CAPTURE_CHECKS:
        out += check(cap)
    return out


# --- B2, B3, C2, C3: across a scenario's beats and logs ---

def _truth_at(truth, t_rel):
    """The known f0 at t_rel seconds after the feed started (None when silent)."""
    track, step = truth.get("track"), truth.get("track_step")
    if not track or not step:
        return None
    i = int(round(t_rel / step))
    return track[i] if 0 <= i < len(track) else None


def readout_series(frames, start, end):
    """[(t, readout Hz or None, frame)] of the frames logged between two audio times."""
    out = []
    for f in frames:
        if start < f["t"] <= end + 1e-9 and "readout" in f:
            hz = vocab.hz_values(f.get("readout"))
            out.append((f["t"], hz[0] if hz else None, f))
    return out


def truth_metrics(scenario, beat, frames):
    """Displayed vs known pitch for one beat with a synthetic voice: {"kind", "median_error_pct",
    "within_pct" (share of voiced frames within tolerance), "settle_s", "onset_ms", "frames"} or None."""
    truth = beat.get("truth") or {}
    if truth.get("kind") not in ("steady", "glide", "scale", "vibrato", "speech") or not truth.get("track"):
        return None
    start, end = truth["beat_start"], truth["beat_end"]
    series = readout_series(frames, start, end)
    if not series:                  # the screen had no pitch readout (a practice page): nothing to judge
        return None
    moving = truth["kind"] != "steady"
    errors, ok_flags, times, octaves = [], [], [], 0
    for t, hz, _ in series:
        known = _truth_at(truth, t - start - (NOTE_LAG_S if moving else 0.0))
        if known is None or hz is None:
            continue
        err = (hz - known) / known
        octaves += abs(math.log2(hz / known)) > OCTAVE_LOG2
        errors.append(err)
        ok_flags.append(abs(err) <= (MOVING_TOLERANCE if moving else TRUTH_TOLERANCE))
        times.append(t)
    if not errors:
        return {"kind": truth["kind"], "frames": 0, "median_error_pct": None, "within_pct": 0.0, "settle_s": None,
                "onset_ms": None}
    voiced = truth.get("voiced") or []
    onset = start + voiced[0][0] if voiced else start
    first = next((t for t, hz, _ in series if hz is not None and t >= onset), None)
    settle = None
    run = 0
    for t, ok in zip(times, ok_flags):
        run = run + 1 if ok else 0
        if run >= SETTLE_FRAMES:
            settle = t - onset - (SETTLE_FRAMES - 1) * 0.025
            break
    settled_errors = [e for t, e in zip(times, errors) if settle is not None and t >= onset + settle]
    pool = settled_errors if (not moving and settled_errors) else errors
    return {"kind": truth["kind"], "frames": len(errors), "octave_pct": round(100 * octaves / len(errors), 1),
            "median_error_pct": round(100 * float(np.median(np.abs(pool))), 3),
            "median_signed_pct": round(100 * float(np.median(pool)), 3),
            "within_pct": round(100 * float(np.mean(ok_flags)), 1),
            "settle_s": None if settle is None else round(max(0.0, settle), 3),
            "onset_ms": None if first is None else round(1000 * (first - onset), 1),
            "f0": truth.get("f0")}


def check_truth(scenario, beat, frames, last_capture=None):
    """B2: for synthetic voices, the readout is within tolerance of the known f0, settles fast, and (steady
    voices) the zone shown is the zone of the known value."""
    out = []
    m = truth_metrics(scenario, beat, frames)
    if m is None:
        return out, None
    base = {"check": "truth", "screen": (last_capture.screen if last_capture else None), "scenario": scenario,
            "beat": beat["name"], "size": "", "theme": "", "widget_path": "dashboard/live/note_label"
            if not last_capture or last_capture.screen != "live" else "live/view/note_label", "rect": None}
    tol = MOVING_TOLERANCE if m["kind"] != "steady" else TRUTH_TOLERANCE
    if m["frames"] and m.get("octave_pct", 0) >= OCTAVE_SHARE_PCT:
        out.append(dict(base, severity="error", message=f"The readout is an octave off for {m['octave_pct']:.0f} % "
                        f"of a known {m['kind']} voice (the tracker jumps octaves)", evidence=m))
    if m["frames"] == 0:
        out.append(dict(base, severity="error", message="The readout never showed a pitch for a known voice",
                        evidence=m))
    elif m["median_error_pct"] > 100 * tol:
        sev = "error" if m["kind"] == "steady" else SEVERITY["truth_moving"]
        out.append(dict(base, check="truth" if m["kind"] == "steady" else "truth_moving", severity=sev,
                        message=f"The readout is off by {m['median_error_pct']:.1f} % (median) against a known "
                        f"{m['kind']} voice; tolerance {100 * tol:.0f} %", evidence=m))
    if m["kind"] == "steady" and m["frames"] and (m["settle_s"] is None or m["settle_s"] > SETTLE_BUDGET_S):
        out.append(dict(base, check="settle", severity=SEVERITY["settle"],
                        message=f"The readout took {m['settle_s']} s to settle within {100 * tol:.0f} % "
                        f"(budget {SETTLE_BUDGET_S} s)" if m["settle_s"] is not None else
                        "The readout never settled within tolerance", evidence=m))
    if m["kind"] == "steady" and last_capture is not None and m.get("f0"):
        want = report.zone_of("pitch", m["f0"])
        for w in last_capture.widgets:
            if w.get("kind") == "live_card" and w["state"].get("key") == "pitch":
                bar = last_capture.by_path.get(w["path"] + "/bar")
                value = (bar or {}).get("state", {}).get("value")
                if value is None:
                    continue
                shown = next((z["id"] for z in bar["state"]["zones"]
                              if (z["lo"] is None or value >= z["lo"]) and (z["hi"] is None or value < z["hi"])), None)
                if shown != want:
                    out.append(dict(base, check="zone", severity=SEVERITY["zone"], widget_path=bar["path"],
                                    message=f"A {m['f0']} Hz voice is {want} under report.ZONES but the Pitch card "
                                    f"shows {shown} ({value:.0f} Hz)", evidence={"want": want, "shown": shown,
                                                                                    "value": value}))
    return out, m


def live_vs_take(beat, frames, sidecar):
    """B3: the median live readout over a replayed real take vs the take report's pitch (and vs the take's
    own pitch track over the same seconds). Hz and semitones, or None."""
    truth = beat.get("truth") or {}
    series = [hz for _, hz, _ in readout_series(frames, truth.get("beat_start", 0), truth.get("beat_end", 0))
              if hz is not None]
    if not series or not sidecar:
        return None
    live = float(np.median(series))
    out = {"take": truth.get("take"), "live_median_hz": round(live, 2), "frames": len(series)}
    rep = sidecar.get("pitch")
    if rep:
        out.update(report_hz=round(rep, 2), diff_hz=round(live - rep, 2), diff_st=round(12 * math.log2(live / rep), 3))
    tracks = sidecar.get("tracks") or {}
    times, f0 = np.asarray(tracks.get("times") or [], dtype=float), np.asarray(tracks.get("f0") or [], dtype=float)
    fed = beat["result"].get("fed_seconds") if isinstance(beat.get("result"), dict) else None
    if len(times) and fed:
        span = f0[(times <= fed) & np.isfinite(f0)]
        if len(span):
            same = float(np.median(span))
            out.update(track_hz=round(same, 2), track_diff_st=round(12 * math.log2(live / same), 3))
    return out


def check_live_vs_take(scenario, beat, frames, sidecar):
    m = live_vs_take(beat, frames, sidecar)
    if m is None:
        return [], None
    out = []
    diff = m.get("track_diff_st", m.get("diff_st"))
    if diff is not None and abs(diff) > LIVE_TAKE_WARN_ST:
        out.append({"check": "live_vs_take", "severity": "error" if abs(diff) > 2 * LIVE_TAKE_WARN_ST else "warn",
                    "screen": "dashboard", "scenario": scenario, "beat": beat["name"], "size": "", "theme": "",
                    "widget_path": "dashboard/live/note_label", "rect": None,
                    "message": f"Live pitch while replaying take {m['take']} reads {m['live_median_hz']:.0f} Hz "
                    f"(median); the take itself says {m.get('track_hz', m.get('report_hz')):.0f} Hz over the same "
                    f"seconds ({diff:+.2f} semitones)", "evidence": m})
    return out, m


def crop_diff(a, b, rect):
    """Mean absolute difference (0-255) of two RGB arrays inside rect."""
    ca, cb = png.crop(a, rect), png.crop(b, rect)
    if ca.size == 0 or ca.shape != cb.shape:
        return None
    return float(np.mean(np.abs(ca[:, :, :3].astype(np.int16) - cb[:, :, :3].astype(np.int16))))


def state_signature(state):
    return {"record": state.get("record_state"), "mic": state.get("mic_available"),
            "recording": state.get("recording"), "analysing": state.get("analyzing")}


def check_distinguish(pairs):
    """C2: consecutive captures (same size and theme) whose core states differ must look different where the
    user looks: the record button for recording state, the top bar for the mic. `pairs` is
    [(capture A, capture B, pixels A, pixels B)]."""
    out = []
    for a, b, pa, pb in pairs:
        sa, sb = state_signature(a.state), state_signature(b.state)
        for what, keys, path in (("record", ("record", "recording", "analysing"), "action_bar/record_btn"),
                                 ("mic", ("mic",), "top")):
            if all(sa[k] == sb[k] for k in keys):
                continue
            wa, wb = a.by_path.get(path), b.by_path.get(path)
            if not wa or not wb:
                continue
            d = crop_diff(pa, pb, wa["visible_rect"])
            if d is not None and d < DIFF_SAME:
                out.append(b.finding("distinguish", path, f"The {what} state changed ({ {k: sa[k] for k in keys} } → "
                                     f"{ {k: sb[k] for k in keys} }) but {path} looks the same (diff {d:.2f})",
                                     {"diff": round(d, 3), "before": a.beat}))
    return out


def percentile(values, q):
    return round(float(np.percentile(values, q)), 2) if values else None


def _job_kinds(event):
    """The job kinds a logged "busy" event lists."""
    args = event.get("args") or []
    jobs = args[0] if args and isinstance(args[0], list) else []
    return {j.get("kind") for j in jobs if isinstance(j, dict)}


def latency_metrics(events, beats):
    """C3: {"frame_ui_ms": [...], "onset_ms": [...], "take_visible": [...], "toast_ms": [...]} from a scenario's
    event log (wall-clock times in ms, except onset which is audio time)."""
    frames = [e for e in events if e.get("type") == "frame"]
    out = {"frame_ui_ms": [f["ui_ms"] for f in frames if "ui_ms" in f], "take_visible": [], "toast_ms": [],
           "paint_ms": [e["ms"] for e in events if e.get("type") == "paint"]}
    statuses = [e for e in events if e.get("type") == "event" and e.get("name") == "status"]
    toasts = [e for e in events if e.get("type") == "toast"]
    for s in statuses:
        text = (s.get("args") or [""])[0]
        match = next((t for t in toasts if t["text"] == text and t["wall"] >= s["wall"]), None)
        if match:
            out["toast_ms"].append(round(1000 * (match["wall"] - s["wall"]), 2))
    stops = [e for e in events if e.get("type") == "beat" and e.get("phase") == "start" and "record-stop" in e["name"]]
    for stop in stops:
        added = next((e for e in events if e.get("type") == "event" and e.get("name") == "take_added"
                      and e["wall"] >= stop["wall"]), None)
        busy = [e for e in events if e.get("type") == "event" and e.get("name") == "busy" and e["wall"] >= stop["wall"]]
        started = next((e["wall"] for e in busy if "analysis" in _job_kinds(e)), None)
        ended = next((e["wall"] for e in busy if started and e["wall"] > started and
                      "analysis" not in _job_kinds(e)), None)
        if added:
            out["take_visible"].append({"ms": round(1000 * (added["wall"] - stop["wall"]), 1),
                                        "analysis_ms": round(1000 * (ended - started), 1) if started and ended else None})
    return out


def check_latency(scenario, metrics):
    out = []
    base = {"scenario": scenario, "beat": "", "size": "", "theme": "", "rect": None}
    p95 = percentile(metrics["frame_ui_ms"], 95)
    if p95 is not None and p95 > FRAME_BUDGET_MS:
        out.append(dict(base, check="latency", severity="warn", screen="dashboard", widget_path="dashboard/live",
                        message=f"A live frame's UI work takes {p95:.1f} ms at p95: more than one {FRAME_BUDGET_MS:.0f} "
                        "ms hop, so the display falls behind the voice", evidence={"p95_ms": p95}))
    if p95 is not None and p95 > READOUT_BUDGET_MS:
        out.append(dict(base, check="latency", severity="error", screen="dashboard", widget_path="dashboard/live/note_label",
                        message=f"Frame to readout takes {p95:.0f} ms at p95 (budget {READOUT_BUDGET_MS:.0f} ms)",
                        evidence={"p95_ms": p95}))
    for t in metrics["take_visible"]:
        budget = (t["analysis_ms"] or 0) + TAKE_SLACK_MS
        if t["analysis_ms"] is not None and t["ms"] > budget:
            out.append(dict(base, check="latency", severity="warn", screen="dashboard", widget_path="dashboard/latest",
                            message=f"The take appeared {t['ms']:.0f} ms after Stop; its analysis took "
                            f"{t['analysis_ms']:.0f} ms (budget +{TAKE_SLACK_MS:.0f} ms)", evidence=t))
    p95p = percentile(metrics.get("paint_ms") or [], 95)
    if p95p is not None and p95p > PAINT_BUDGET_MS:
        out.append(dict(base, check="latency", severity="warn", screen="", widget_path="",
                        message=f"A repaint of the window takes {p95p:.0f} ms at p95 (budget {PAINT_BUDGET_MS:.0f} ms, "
                        "one hop): the live view can't redraw every frame", evidence={"p95_ms": p95p}))
    p95t = percentile(metrics["toast_ms"], 95)
    if p95t is not None and p95t > TOAST_BUDGET_MS:
        out.append(dict(base, check="latency", severity="warn", screen="", widget_path="toasts",
                        message=f"Status to toast takes {p95t:.0f} ms at p95 (budget {TOAST_BUDGET_MS:.0f} ms)",
                        evidence={"p95_ms": p95t}))
    return out


# --- D. REACHABILITY ---

def check_reach(scenario, beats):
    """Findings for features the lab could not reach, and each feature's clicks from the dashboard."""
    out, table = [], []
    for beat in beats:
        res = beat.get("result")
        if beat.get("do") != "reach" or not isinstance(res, dict):
            continue
        table.append({k: res.get(k) for k in ("feature", "name", "reached", "clicks", "failed_step", "conditional")})
        if not res.get("reached") and not res.get("conditional"):
            out.append({"check": "reach", "severity": SEVERITY["reach"], "screen": res.get("screen") or "",
                        "scenario": scenario, "beat": beat["name"], "size": "", "theme": "",
                        "widget_path": f"feature:{res.get('feature')}", "rect": None,
                        "message": f"“{res.get('name')}” can't be reached: {res.get('why') or 'dead end'}",
                        "evidence": res})
    return out, table


def check_exceptions(scenario, events):
    """An exception raised in the app's code during a scenario (a Qt slot, a handler): one finding per place."""
    out, seen = [], {}
    for e in events:
        if e.get("type") != "exception":
            continue
        seen.setdefault(e.get("where") or e.get("error"), []).append(e)
    for where, items in seen.items():
        e = items[0]
        out.append({"check": "exception", "severity": SEVERITY["exception"], "screen": "", "scenario": scenario,
                    "beat": "", "size": "", "theme": "", "widget_path": f"code:{where}", "rect": None,
                    "message": f"{e['error']} at {where} ({len(items)}×): {(e.get('code') or '')[:80]}",
                    "evidence": {"count": len(items), "first_t": e.get("t")}})
    return out


def check_beat_errors(scenario, beats):
    out = []
    for beat in beats:
        if beat.get("error"):
            out.append({"check": "capture", "severity": SEVERITY["capture"], "screen": "", "scenario": scenario,
                        "beat": beat["name"], "size": "", "theme": "", "widget_path": f"beat:{beat['do']}",
                        "rect": None, "message": f"The beat failed: {beat['error']}", "evidence": {}})
    return out
