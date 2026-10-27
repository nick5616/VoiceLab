"""Reachability: drive each feature's path (tools/uilab/features.py) from the dashboard with real clicks and
shortcuts, and say whether its target became visible, how many clicks it took, and where it dead-ended."""
import numpy as np
import soundfile as sf
from PyQt6.QtWidgets import QApplication, QDialog, QMenu

import synth
from tools.uilab.features import BY_ID, UNCOUNTED
from ui.lab import env
from ui.lab.capture import settle
from ui.lab.inspector import Inspector

WAIT_STEP = 0.5                 # s of audio per timer tick while a "wait" step lets time pass


def _files(session):
    """A mono and a stereo WAV in the scenario's work folder, for the import paths."""
    out = {}
    voice = synth.steady(170, 1.5, synth.NEUTRAL_FORMANTS)
    for kind, data in (("mono", voice), ("stereo", np.stack([voice, 0.5 * voice], axis=1))):
        path = session.work / f"import-{kind}.wav"
        if not path.exists():
            sf.write(str(path), data, synth.SR)
        out[kind] = str(path)
    return out


def reset(session):
    """Back to a plain dashboard: no dialogs, menus or popups, no practice open, jobs done, toasts gone."""
    w = session.window
    for top in QApplication.topLevelWidgets():
        if top.isVisible() and top is not w:
            top.close() if isinstance(top, QDialog) else top.hide()
    if w.practice.current:
        w.practice.close_practice()
    session.wait_jobs()
    for toast in list(w.toasts.toasts):
        toast.dismiss()
    w.go("dashboard")
    env.reset_calls()
    env.answers().update(open_file="", folder="")
    settle()


def _find(window, path=None, prefix=None, text=None, clickable=False):
    insp = Inspector(window)
    for w, _ in insp.widgets:
        p = insp.path(w)
        if path is not None and p == path:
            return w
        if prefix is not None and (p == prefix or p.startswith(prefix + "/") or prefix == ""):
            t = w.text() if hasattr(w, "text") and callable(w.text) else getattr(w, "text", "")
            if text is not None and isinstance(t, str) and t.strip() == text and \
                    (not clickable or insp.clickable(w)):
                return w
    return None


def _has_text(window, prefix, text):
    insp = Inspector(window)
    for rec in insp.tree():
        if (not prefix or rec["path"] == prefix or rec["path"].startswith(prefix + "/")) and \
                text.lower() in (rec.get("text") or "").lower():
            return True
    return False


def step(session, s):
    from ui.lab.driver import click, press
    w, kind = session.window, s[0]
    if kind == "nav":
        click(w.nav, s[1])
    elif kind == "click":
        target = _find(w, path=s[1])
        if target is None:
            raise LookupError(f"nothing visible at {s[1]}")
        click(target)
    elif kind == "item":
        target = _find(w, path=s[1])
        if target is None:
            raise LookupError(f"nothing visible at {s[1]}")
        click(target, s[2])
    elif kind == "text":
        target = _find(w, prefix=s[1], text=s[2], clickable=True)
        if target is None:
            raise LookupError(f"no visible “{s[2]}” under {s[1] or 'the window'}")
        click(target)
    elif kind == "expand":
        target = _find(w, path=s[1] + "/header")
        if target is None:
            raise LookupError(f"no section at {s[1]}")
        click(target)
    elif kind == "key":
        press(w, s[1])
    elif kind == "answer":
        env.answers()[s[1]] = _files(session)[s[2]] if s[2] in ("mono", "stereo") else s[2]
    elif kind == "wait":
        session.advance(s[1], step=WAIT_STEP)
    settle()


def reached(session, target):
    """(reached, why) for a target."""
    w = session.window
    if "visible" in target:
        found = _find(w, path=target["visible"])
        return found is not None, None if found is not None else f"{target['visible']} is not visible"
    if "text" in target:
        ok = _has_text(w, target.get("under", ""), target["text"])
        return ok, None if ok else f"no “{target['text']}” under {target.get('under') or 'the window'}"
    if "called" in target:
        ok = any(kind == target["called"] and target.get("contains", "") in detail for kind, detail in env.calls())
        return ok, None if ok else f"the app never asked for {target['called']} {target.get('contains', '')}".strip()
    if "menu" in target:
        menus = [m for m in QApplication.topLevelWidgets() if isinstance(m, QMenu) and m.isVisible()]
        ok = any(a.text() == target["menu"] for m in menus for a in m.actions())
        return ok, None if ok else f"no open menu with “{target['menu']}”"
    if "toast" in target:
        ok = any(target["toast"] in t.text for t in w.toasts.toasts)
        return ok, None if ok else f"no toast with “{target['toast']}”"
    if "state" in target:
        path, key, value = target["state"]
        rec = next((r for r in Inspector(w).tree() if r["path"] == path), None)
        ok = rec is not None and (rec.get("state") or {}).get(key) == value
        return ok, None if ok else f"{path} {key} is not {value}"
    if "points" in target:
        plot = _find(w, path=target["points"][0])
        item = getattr(plot, target["points"][1], None) if plot is not None else None
        ok = item is not None and len(item.data) > 0
        return ok, None if ok else f"{target['points'][0]} shows no {target['points'][1]}"
    return False, "unknown target"


def drive(session, feature_id):
    """Run one feature's path from a reset dashboard; the result goes into beats.json."""
    reset(session)
    return run_feature(session, BY_ID[feature_id])


def run_feature(session, feature):
    """Follow a feature's steps and check its target: {"feature", "name", "reached", "clicks", "failed_step",
    "why", "screen", "conditional"}."""
    feature_id = feature["id"]
    clicks, failed, why = 0, None, None
    for s in feature["steps"]:
        try:
            step(session, s)
        except LookupError as e:
            failed, why = " ".join(str(x) for x in s), str(e)
            break
        if s[0] not in UNCOUNTED:
            clicks += 1
    ok, missing = (False, why) if failed else reached(session, feature["target"])
    result = {"feature": feature_id, "name": feature["name"], "reached": ok, "clicks": clicks,
              "failed_step": failed, "why": why or missing,
              "screen": session.window.current() if hasattr(session.window, "current") else None,
              "conditional": feature.get("conditional") if not ok and feature.get("conditional") else None}
    for s in feature.get("after", []):
        try:
            step(session, s)
        except LookupError:
            pass
    return result
