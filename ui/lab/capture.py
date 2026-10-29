"""Capture: the window as pixels (with its open dialogs and popups pasted where they sit), full-height renders
of scroll areas, the widget tree and the core's own state, written next to each other for one beat."""
import time

import numpy as np
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QDialog, QScrollArea

from engine import UPDATE_INTERVAL
from tools.uilab import png
from tools.uilab.runio import write_json
from ui.lab.inspector import Inspector, top_windows


FRAME = 2                       # px of window frame drawn around a pasted dialog
FRAME_INK = 110                 # its grey (0-255)


def settle(rounds=3):
    """Run pending events, layout requests and deferred deletes, so what is drawn is current."""
    for _ in range(rounds):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest.value)
        QApplication.processEvents()


def to_array(image):
    """An (h, w, 4) RGBA uint8 copy of a QImage or QPixmap."""
    if not isinstance(image, QImage):
        image = image.toImage()
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h, bpl = image.width(), image.height(), image.bytesPerLine()
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    return np.frombuffer(bits, dtype=np.uint8).reshape(h, bpl)[:, :w * 4].reshape(h, w, 4).copy()


def place_dialogs(window):
    """Centre each visible dialog over the window (the offscreen platform leaves them at 0, 0)."""
    centre = window.mapToGlobal(window.rect().center())
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QDialog) and w.isVisible() and w is not window:
            w.move(centre.x() - w.width() // 2, centre.y() - w.height() // 2)


def composite(window):
    """(pixels, roots): the window's screenshot with its visible dialogs and popups pasted at their offsets."""
    base = to_array(window.grab())
    H, W = base.shape[:2]
    roots = top_windows(window)
    for w, (dx, dy) in roots[1:]:
        over = to_array(w.grab())
        h, ww = over.shape[:2]
        if isinstance(w, QDialog):          # the frame the OS would draw around a dialog
            base[max(0, dy - FRAME):min(H, dy + h + FRAME), max(0, dx - FRAME):min(W, dx + ww + FRAME), :3] = FRAME_INK
        x0, y0, x1, y1 = max(0, dx), max(0, dy), min(W, dx + ww), min(H, dy + h)
        if x1 > x0 and y1 > y0:
            base[y0:y1, x0:x1] = over[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    base[:, :, 3] = 255
    return base, roots


def scroll_renders(window, stem):
    """Save a full-height render of every visible scroll area on the current screen whose content is taller
    than its viewport: [(scroll area path, file name, content height)]."""
    out = []
    insp = Inspector(window)
    for w, _ in insp.widgets:
        if isinstance(w, QScrollArea) and w.widget() is not None:
            content = w.widget()
            if content.height() <= w.viewport().height() + 1:
                continue
            name = insp.path(w).replace("/", ".").replace("#", "") or "window"
            path = f"{stem}.{name}.full.png"
            png.write_png(path, to_array(content.grab())[:, :, :3])
            out.append({"scroll": insp.path(w), "file": path.replace("\\", "/").split("/")[-1],
                        "content_height": content.height(), "viewport_height": w.viewport().height()})
    return out


def core_state(window, truth=None, extra=None):
    """The core's own truth now: record state, mode, loading, jobs, mic, takes, the live snapshot."""
    core = window.core
    state, caption = core.record_state()
    jobs = core.runner.jobs()
    snap = core.live.snapshot()
    if hasattr(core.live, "current_state"):     # as the screen shows it now: the heartbeat counts between frames,
        from live_session import state_strip    # and a lost mic is "no signal" whatever the last frame said
        snap["state"] = state_strip(core.live.current_state(), core.mic_status().get("state", "ok"))["state"]
    history = snap.pop("pitch_history", None) or []
    run = 0
    for hz in reversed(history):        # how long the voice has sounded without a break, up to now
        if hz is None or hz != hz:
            break
        run += 1
    out = {"record_state": state, "record_caption": caption, "mode": core.mode, "loading": core.loading,
           "analyzing": core.analyzing, "recording": core.is_recording, "jobs": jobs,
           "mic_available": core.mic_available, "mic_problem": core.mic_problem,
           "takes_in_progress": core.takes_in_progress(), "take_count": len(core.takes()),
           "voiced_seconds": round(run * UPDATE_INTERVAL, 3),
           "status": list(core.status), "screen": window.current(), "snapshot": snap,
           "banner_info": window.banner_info, "onboarding_open": bool(window.onboarding and window.onboarding.isVisible()),
           "practice": window.practice.current, "exercise_running": core.exercise.running,
           "vowels_running": core.vowels.running, "store": bool(core.store),
           "toasts": [{"kind": t.kind, "text": t.text} for t in window.toasts.toasts if not t.closing]}
    if truth is not None:
        out["truth"] = truth
    if extra:
        out.update(extra)
    return out


def capture(window, stem, truth=None, extra=None, full=True):
    """Write <stem>.png, <stem>.tree.json and <stem>.state.json (and any .full.png); returns a summary."""
    started = time.perf_counter()
    place_dialogs(window)
    settle()
    pixels, _ = composite(window)
    png.write_png(stem + ".png", pixels[:, :, :3])
    tree = Inspector(window).tree(pixels)
    fulls = scroll_renders(window, stem) if full else []
    meta = {"size": [window.width(), window.height()], "screen": window.current(),
            "dialog": next((w.windowTitle() for w, _ in top_windows(window)[1:] if isinstance(w, QDialog)), None),
            "full": fulls, "capture_ms": None}
    write_json(stem + ".tree.json", {"meta": meta, "widgets": tree})
    write_json(stem + ".state.json", core_state(window, truth, extra))
    meta["capture_ms"] = round(1000 * (time.perf_counter() - started), 1)
    return meta
