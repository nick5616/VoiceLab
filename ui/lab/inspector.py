"""The widget inspector: a JSON-ready dump of every visible widget of the main window (and of its visible
dialogs and popups), with a stable path, geometry in composite-screenshot coordinates, the visible part
after clipping, text and whether it fits, font size, colours (declared, and sampled from the screenshot),
clickability, and the key state of each VoiceLab component and plot. It only reads widgets: it never
computes a reading (the evals in tools/uilab judge what it finds)."""
import re

import pyqtgraph as pg
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QFontMetrics, QPalette
from PyQt6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QApplication, QComboBox, QDialog,
                             QGraphicsRectItem, QLabel, QLineEdit, QMenu, QPlainTextEdit, QScrollArea,
                             QStackedWidget, QTextEdit, QWidget)

from tools.uilab import colors as colormath
from ui import theme
from ui.components import (ActivityCenter, Badge, Banner, Button, Chip, ConfidenceChip, EmptyState, HelpTip,
                           MetricCard, MomentChip, NavRail, RecordButton, SegmentedControl, Select, Skeleton,
                           Spinner, StatusDot, Tabs, Toast, ToastStack, Toggle, ZoneBar)
from ui.components.base import TONES, tone_color
from ui.components.controls import TAB_HEIGHT

TOLERANCE = 1                   # px: rect overhangs this small are rounding, not clipping
CLICK_CURSORS = {Qt.CursorShape.PointingHandCursor, Qt.CursorShape.WhatsThisCursor}
TEXT_CLASSES = (QLabel, QAbstractButton, QLineEdit, QComboBox)
SKIP_CLASSES = ("QScrollBar", "QFocusFrame", "QRubberBand", "QSizeGrip", "QToolButton_viewport")
# Containers whose class says what time a reading belongs to (the evals read it as the "context").
CONTEXTS = {"LiveView": "now", "LiveStrip": "now", "LatestTakePanel": "last_take", "TakeCard": "take", "RecentTakes": "recent",
            "ProgressScreen": "progress", "ProgressMini": "progress", "ExerciseView": "now",
            "VowelCheck": "now", "VoiceMapView": "map", "_RecordingPanel": "now", "ReadAlouds": "now",
            "Phrases": "now"}
# (owner class, attribute) -> role: what quantity a widget shows, for the truth evals.
ROLES = {("LiveStrip", "note_label"): "readout.pitch", ("LiveStrip", "note_name"): "readout.note",
         ("LiveView", "note_label"): "readout.pitch", ("LiveView", "note_name"): "readout.note",
         ("LiveView", "coach_label"): "coach", ("LiveView", "graph"): "graph.pitch",
         ("LiveView", "plane"): "plane", ("LiveView", "spectrum"): "spectrum",
         ("ExerciseView", "cents_label"): "readout.tuning", ("TopBar", "status"): "status_line",
         ("TopBar", "mic"): "mic_chip", ("TopBar", "activity"): "activity", ("TopBar", "session"): "session",
         ("AppWindow", "banner"): "banner", ("ActionBar", "record_btn"): "record", ("AppWindow", "toasts"): "toasts",
         ("AppWindow", "nav"): "nav"}
TAG = re.compile(r"<[^>]+>")


def _cls(w):
    return type(w).__name__


def _rect(r):
    return [r.x(), r.y(), r.width(), r.height()]


def _inter(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return [x0, y0, max(0, x1 - x0), max(0, y1 - y0)]


def _contains(outer, inner, tol=TOLERANCE):
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and
            inner[0] + inner[2] <= outer[0] + outer[2] + tol and inner[1] + inner[3] <= outer[1] + outer[3] + tol)


def plain(text):
    """Visible text without rich-text tags."""
    return TAG.sub("", text or "").replace("&nbsp;", " ").strip()


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v or v in (float("inf"), float("-inf")) else round(v, 4)


def _nums(values, limit=None):
    out = [_num(v) for v in list(values)]
    return out if limit is None else out[-limit:]


def top_windows(window):
    """[(widget, (dx, dy))]: the main window, then each visible dialog and popup that belongs to it, with
    its offset in the composite screenshot (dialogs as placed by the lab, popups where Qt put them)."""
    out = [(window, (0, 0))]
    origin = window.mapToGlobal(QPoint(0, 0))
    for w in QApplication.topLevelWidgets():
        if w is window or not w.isVisible() or not w.isWindow():
            continue
        if _cls(w) in ("QToolTip", "QTipLabel"):
            continue
        anc, belongs = w.parentWidget(), False
        while anc is not None:
            if anc is window:
                belongs = True
                break
            anc = anc.parentWidget()
        if not belongs:
            continue
        g = w.mapToGlobal(QPoint(0, 0)) - origin
        out.append((w, (g.x(), g.y())))
    return out


class Inspector:
    """One dump of the window: Inspector(window).tree(pixels) -> dict. `pixels` is the composite screenshot
    as an (h, w, 4) RGBA array (for sampled colours), or None."""

    def __init__(self, window):
        self.window = window
        self.roots = top_windows(window)
        self.widgets = []
        for root, offset in self.roots:
            self._collect(root, offset)
        self.owners = {}
        self._owners()
        self.paths = {}
        self._used = {}

    # --- collecting ---

    def _collect(self, w, offset):
        if not w.isVisible() or _cls(w) in SKIP_CLASSES:
            return
        self.widgets.append((w, offset))
        for child in w.children():
            if isinstance(child, QWidget) and not (child.isWindow() and child is not w):
                self._collect(child, offset)

    def _owners(self):
        """owners[id(widget)] = [(holder, name)]: which widgets keep it as an attribute (or in a dict or list)."""
        holders = [w for w, _ in self.widgets]
        known = {id(w) for w in holders}
        for holder in holders:
            try:
                attrs = vars(holder)
            except TypeError:
                continue
            for name, value in sorted(attrs.items()):
                if name.startswith("__"):
                    continue
                items = [(name, value)]
                if isinstance(value, dict):
                    items = [(f"{name}.{k}", v) for k, v in value.items() if isinstance(k, (str, int))]
                elif isinstance(value, (list, tuple)) and len(value) < 64:
                    items = [(f"{name}.{i}", v) for i, v in enumerate(value)]
                for label, v in items:
                    if isinstance(v, QWidget) and id(v) in known and v is not holder:
                        self.owners.setdefault(id(v), []).append((holder, label))

    # --- names and paths ---

    def path(self, w):
        """A stable path: named by the nearest ancestor that keeps the widget as an attribute, else
        Class#k among its parent's children of that class."""
        key = id(w)
        if key in self.paths:
            return self.paths[key]
        if w is self.window:
            self.paths[key] = ""
            return ""
        holders = {}
        for h, name in sorted(self.owners.get(key, []), key=lambda hn: (hn[1].startswith("_"), "." in hn[1],
                                                                        len(hn[1]))):
            holders.setdefault(id(h), name)
        anc, parent = w.parentWidget(), w.parentWidget()
        named = None
        while anc is not None:
            if id(anc) in holders:
                named = (anc, holders[id(anc)])
                break
            anc = anc.parentWidget()
        if named is None and id(self.window) in holders:
            named = (self.window, holders[id(self.window)])
        if named is not None:
            base, seg = self.path(named[0]), named[1]
        elif parent is None:
            base, seg = "", _cls(w)
        else:
            same = [c for c in parent.children() if isinstance(c, QWidget) and _cls(c) == _cls(w)]
            seg = f"{_cls(w)}#{same.index(w)}" if len(same) > 1 else _cls(w)
            base = self.path(parent)
            if _cls(parent) == "QWidget" and parent.objectName() == "qt_scrollarea_viewport":
                base = self.path(parent.parentWidget()) if parent.parentWidget() is not None else base
        path = f"{base}/{seg}" if base else seg
        n = self._used.get(path, 0)
        self._used[path] = n + 1
        if n:
            path = f"{path}~{n + 1}"
        self.paths[key] = path
        return path

    def role(self, w):
        for holder, name in sorted(self.owners.get(id(w), []), key=lambda hn: ("." in hn[1], len(hn[1]))):
            role = ROLES.get((_cls(holder), name))
            if role:
                return role
        return None

    def context(self, w):
        anc = w
        while anc is not None:
            for cls in type(anc).__mro__:
                if cls.__name__ in CONTEXTS:
                    return CONTEXTS[cls.__name__]
            anc = anc.parentWidget()
        return None

    # --- geometry ---

    def geometry(self, w, offset):
        top = w.window()
        p = w.mapTo(top, QPoint(0, 0)) if w is not top else QPoint(0, 0)
        return [p.x() + offset[0], p.y() + offset[1], w.width(), w.height()]

    def clipping(self, w, offset, rect):
        """(visible rect, clip): the part of the widget not cut off by an ancestor or the window, and the
        nearest ancestor that cuts it ({"by", "scroll", "dx", "dy"}) or None."""
        visible, clip = list(rect), None
        anc = w.parentWidget()
        while anc is not None and not (w.isWindow()):
            ar = self.geometry(anc, offset)
            if clip is None and not _contains(ar, rect):
                viewport = isinstance(anc.parentWidget(), QAbstractScrollArea) and \
                    anc is anc.parentWidget().viewport()
                dx = max(0, ar[0] - rect[0]) + max(0, rect[0] + rect[2] - ar[0] - ar[2])
                dy = max(0, ar[1] - rect[1]) + max(0, rect[1] + rect[3] - ar[1] - ar[3])
                clip = {"by": self.path(anc), "scroll": viewport, "dx": dx, "dy": dy}
            visible = _inter(visible, ar)
            if anc.isWindow():
                break
            anc = anc.parentWidget()
        frame = [0, 0, self.window.width(), self.window.height()]
        if not _contains(frame, rect) and clip is None and w.window() is self.window:
            clip = {"by": "window", "scroll": False,
                    "dx": max(0, -rect[0]) + max(0, rect[0] + rect[2] - frame[2]),
                    "dy": max(0, -rect[1]) + max(0, rect[1] + rect[3] - frame[3])}
        return _inter(visible, frame) if w.window() is self.window else visible, clip

    def in_scroll(self, w):
        anc = w.parentWidget()
        while anc is not None:
            if isinstance(anc, QScrollArea):
                return self.path(anc)
            anc = anc.parentWidget()
        return None

    # --- text ---

    def text_of(self, w):
        """(text, fit): the visible text and how it fits ({"elided", "need_w", "need_h", "avail_w", "avail_h"})."""
        fm = QFontMetrics(w.font())
        if isinstance(w, QLabel):
            text = plain(w.text())
            if not text:
                return "", None
            cr = w.contentsRect()
            fit = {"elided": False, "avail_w": cr.width(), "avail_h": cr.height()}
            if hasattr(w, "elided") and callable(w.elided):
                fit["elided"] = w.elided() != w.text()
                fit["need_w"] = fm.horizontalAdvance(text)
            elif w.wordWrap():
                fit["need_h"] = w.heightForWidth(w.width()) if w.hasHeightForWidth() else fm.boundingRect(
                    QRect(0, 0, max(1, cr.width()), 100000), int(Qt.TextFlag.TextWordWrap), text).height()
                words = [fm.horizontalAdvance(word) for word in text.split()]
                fit["need_w"] = max(words) if words else 0
            else:
                fit["need_w"] = max(fm.horizontalAdvance(line) for line in text.split("\n"))
                fit["need_h"] = fm.height() * (text.count("\n") + 1)
            return text, fit
        if isinstance(w, QAbstractButton):
            text = w.text()
            hint = w.sizeHint()
            return text, {"elided": False, "need_w": hint.width(), "avail_w": w.width(),
                          "need_h": hint.height(), "avail_h": w.height()} if text else None
        if isinstance(w, QComboBox):
            text = w.currentText()
            return text, {"elided": False, "need_w": fm.horizontalAdvance(text) + 40, "avail_w": w.width()}
        if isinstance(w, QLineEdit):
            return w.text() or w.placeholderText(), None
        if isinstance(w, (QPlainTextEdit, QTextEdit)):
            return w.toPlainText()[:400], None
        if isinstance(w, RecordButton):
            text = w.caption()
            font = theme.font("numeric", size=15, weight=600) if w.state == "recording" else theme.font("label")
            return text, {"elided": False, "need_w": QFontMetrics(font).horizontalAdvance(text), "avail_w": w.width()}
        if isinstance(w, (Chip, Badge, MomentChip)):
            return w.text, None
        if isinstance(w, StatusDot):
            return w.label or "", None
        if isinstance(w, ConfidenceChip):
            return w.label, None
        if isinstance(w, Toggle):
            return w.text, None
        if isinstance(w, HelpTip):
            return "?", None
        return "", None

    # --- components ---

    def component(self, w):
        """(kind, state) of a VoiceLab component, or (None, None)."""
        if isinstance(w, ZoneBar):
            return "zone_bar", {"value": _num(w.value), "scale": list(w.scale), "log": w.log, "reverse": w.reverse,
                                "zones": [{k: z.get(k) for k in ("id", "label", "lo", "hi")} for z in w.zones],
                                "target": list(w.target) if w.target else None, "drawn_labels": list(w.drawn_labels),
                                "compact": w.compact, "end_labels": list(w.end_labels) if w.end_labels else None,
                                "unit": w.unit}
        if isinstance(w, MetricCard):
            return "metric_card", {"name": w.name_label.text(), "value": w.value_label.text(),
                                   "unit": w.unit_label.text(), "headline": w.headline.text(),
                                   "unavailable": w.unavailable, "confidence": w.confidence.level,
                                   "reason": w.confidence.reason}
        if _cls(w) == "LiveCard":
            return "live_card", {"key": w.key, "value": w.value.text(), "name": w.name.text(), "note": w.note.text(),
                                 "unavailable": w.unavailable, "verdict": w.verdict,
                                 "ends": [w.low.text(), w.high.text()]}
        if isinstance(w, RecordButton):
            return "record_button", {"state": w.state, "caption": w.caption(), "elapsed": round(w.elapsed, 2)}
        if isinstance(w, Banner):
            return "banner", {"kind": w.kind, "text": plain(w.label.text()),
                              "action": w.action.text() if w.action else None}
        if isinstance(w, Toast):
            return "toast", {"kind": w.kind, "text": w.text, "action": w.action.text() if w.action else None,
                             "closing": w.closing}
        if isinstance(w, ActivityCenter):
            return "activity", {"title": w.title.text(), "count": w.count(), "expanded": w.expanded,
                                "rows": [r.label.text() for r in w.rows.values()], "tooltip": w.toolTip()}
        if isinstance(w, NavRail):
            return "nav", {"current": w.current(), "badges": {k: v for k, v in w.badges.items() if v},
                           "items": [{"key": k, "label": label, "rect": _rect(w.item_rect(k).toRect())}
                                     for k, _, label in w.items + w.footer]}
        if isinstance(w, Tabs):
            return "tabs", {"current": w.current(), "items": [
                {"key": k, "label": label, "rect": _rect(r.toRect())} for r, (k, label, _) in zip(w._rects(), w.options)]}
        if isinstance(w, SegmentedControl):
            return "segmented", {"current": w.current(), "items": [
                {"key": k, "label": label, "rect": [3 + i * w.segment, 3, w.segment, w.height() - 6]}
                for i, (k, label, _) in enumerate(w.options)]}
        if isinstance(w, StatusDot):
            return "status_dot", {"state": w.state, "label": w.label}
        if isinstance(w, ConfidenceChip):
            return "confidence", {"level": w.level, "label": w.label, "reason": w.reason}
        if isinstance(w, Chip):
            return "chip", {"text": w.text, "kind": w.kind}
        if isinstance(w, EmptyState):
            return "empty_state", {"text": w.label.text(), "detail": w.detail.text() if w.detail.isVisible() else "",
                                   "action": w.action.text() if w.action else None}
        if isinstance(w, Skeleton):
            return "skeleton", {"shape": w.shape}
        if isinstance(w, Spinner):
            return "spinner", {}
        if isinstance(w, ToastStack):
            return "toast_stack", {"count": len(w.toasts)}
        if isinstance(w, HelpTip):
            return "help", {"text": w.text}
        if isinstance(w, Toggle):
            return "toggle", {"checked": w.checked, "text": w.text}
        if isinstance(w, Select):
            return "select", {"current": w.currentText(), "options": [w.itemText(i) for i in range(w.count())]}
        if isinstance(w, QMenu):
            return "menu", {"actions": [a.text() for a in w.actions() if a.text()]}
        if isinstance(w, pg.PlotWidget) or isinstance(w, pg.GraphicsLayoutWidget):
            return "plot", self.plot(w)
        if _cls(w) == "Collapsible":
            return "collapsible", {"title": w.header.label.text(), "expanded": w.expanded}
        if isinstance(w, QDialog):
            return "dialog", {"title": w.windowTitle(), "modal": w.isModal()}
        return None, None

    def plot(self, w):
        """A pyqtgraph plot's view range, axes and items (raw data coordinates: the evals know which axes
        are log2 Hz)."""
        out = {"class": _cls(w), "items": []}
        plot_item = getattr(w, "plotItem", None)
        if plot_item is None:
            return out
        vb = plot_item.getViewBox()
        (x0, x1), (y0, y1) = vb.viewRange()
        out["view"] = {"x": [_num(x0), _num(x1)], "y": [_num(y0), _num(y1)]}
        axes = {}
        for name in ("left", "bottom"):
            axis = plot_item.getAxis(name)
            levels = getattr(axis, "_tickLevels", None)
            ticks = [[_num(v), str(t)] for level in levels for v, t in level] if levels else None
            axes[name] = {"label": plain(axis.labelText) if axis.label.isVisible() else "",
                          "ticks": ticks, "shows_values": bool(axis.style.get("showValues", True)),
                          "visible": axis.isVisible()}
        out["axes"] = axes
        for attr in ("shown_hz", "readout_hz", "off_scale", "mode", "frozen"):
            if hasattr(w, attr):
                v = getattr(w, attr)
                out[attr] = list(v) if isinstance(v, tuple) else (v if not isinstance(v, float) else _num(v))
        named = {}
        for name, value in vars(w).items():
            if isinstance(value, pg.GraphicsObject) or isinstance(value, QGraphicsRectItem):
                named[id(value)] = name
        view_scene = vb.sceneBoundingRect()
        for item in plot_item.items:
            entry = {"name": named.get(id(item), _cls(item)), "class": _cls(item), "visible": item.isVisible()}
            if isinstance(item, pg.PlotDataItem):
                x, y = item.getData()
                if x is not None and len(x):
                    finite = [(a, b) for a, b in zip(x, y) if b == b]
                    entry.update(n=len(x), last=[_num(x[-1]), _num(y[-1])],
                                 last_finite=[_num(finite[-1][0]), _num(finite[-1][1])] if finite else None)
                else:
                    entry.update(n=0)
            elif isinstance(item, pg.ScatterPlotItem):
                pts = item.data
                entry.update(points=[[_num(p["x"]), _num(p["y"])] for p in pts[:50]], n=len(pts))
            elif isinstance(item, pg.TextItem):
                pos = item.pos()
                br = item.sceneBoundingRect()
                origin = w.mapFromScene(br.topLeft())
                entry.update(text=item.toPlainText(), pos=[_num(pos.x()), _num(pos.y())],
                             rect=[origin.x(), origin.y(), int(br.width()), int(br.height())],
                             inside=bool(view_scene.contains(br)))
            elif isinstance(item, pg.LinearRegionItem):
                entry.update(region=_nums(item.getRegion()))
            elif isinstance(item, pg.InfiniteLine):
                entry.update(value=_num(item.value()))
            elif isinstance(item, QGraphicsRectItem):
                r = item.rect()
                zone = getattr(item, "zone", None)
                entry.update(rect=[_num(r.x()), _num(r.y()), _num(r.width()), _num(r.height())],
                             zone=zone.get("id") if isinstance(zone, dict) else None,
                             brush=item.brush().color().name())
            out["items"].append(entry)
        return out

    # --- colours ---

    def declared_color(self, w):
        if isinstance(w, QLineEdit):
            role = QPalette.ColorRole.PlaceholderText if not w.text() else QPalette.ColorRole.Text
            return w.palette().color(role).name()
        if isinstance(w, QLabel) and getattr(w, "tone", None) is not None:
            tone = w.tone
            if tone in TONES or tone in theme.current().colors:
                return tone_color(tone)
        if isinstance(w, Button):
            return w.colors()["text"].name() if w.isEnabled() else theme.current()["text_muted"]
        return None

    def clickable(self, w):
        if isinstance(w, (QAbstractButton, QComboBox, RecordButton, Toggle, HelpTip, MomentChip)):
            return True
        if isinstance(w, (SegmentedControl, Tabs, NavRail)):
            return True
        # a hand cursor set on the widget itself (children inherit it, but the parent is the target)
        own_cursor = w.testAttribute(Qt.WidgetAttribute.WA_SetCursor)
        return own_cursor and w.cursor().shape() in CLICK_CURSORS and not isinstance(w, QLabel)

    def targets(self, w, kind, state, rect):
        """Click targets inside multi-target painted controls (segments, tabs, nav items) as rects."""
        if kind in ("segmented", "tabs", "nav"):
            out = []
            for item in state["items"]:
                x, y, iw, ih = item["rect"]
                out.append({"key": item["key"], "rect": [rect[0] + x, rect[1] + y, iw, ih if kind != "tabs" else TAB_HEIGHT]})
            return out
        return None

    # --- the dump ---

    def tree(self, pixels=None):
        out = []
        for w, offset in self.widgets:
            rect = self.geometry(w, offset)
            visible, clip = self.clipping(w, offset, rect)
            text, fit = self.text_of(w)
            kind, state = self.component(w)
            rec = {"path": self.path(w), "cls": _cls(w), "kind": kind, "rect": rect, "visible_rect": visible,
                   "enabled": w.isEnabled(), "window": "main" if w.window() is self.window else self.path(w.window())}
            parent = w.parentWidget()
            if parent is not None:
                rec["parent"] = self.path(parent)
            if text:
                rec["text"] = text
            if fit:
                rec["fit"] = fit
            tip = w.toolTip()
            if tip:
                rec["tooltip"] = plain(tip)[:300]
            if w.accessibleName():
                rec["accessible"] = w.accessibleName()
            if text or isinstance(w, TEXT_CLASSES):
                f = w.font()
                rec["font_px"] = f.pixelSize() if f.pixelSize() > 0 else round(f.pointSizeF() * 96 / 72, 1)
            if clip:
                rec["clip"] = clip
            scroll = self.in_scroll(w)
            if scroll:
                rec["scroll"] = scroll
            if state is not None:
                rec["state"] = state
                tg = self.targets(w, kind, state, rect)
                if tg:
                    rec["targets"] = tg
            role = self.role(w)
            if role:
                rec["role"] = role
            ctx = self.context(w)
            if ctx:
                rec["context"] = ctx
            if self.clickable(w):
                rec["clickable"] = True
            if isinstance(w, (QStackedWidget,)):
                rec["stack_current"] = self.path(w.currentWidget()) if w.currentWidget() is not None else None
            if isinstance(w, QScrollArea) and w.widget() is not None:
                content = w.widget()
                rec["scroll_area"] = {"viewport": [w.viewport().width(), w.viewport().height()],
                                      "content": [content.width(), content.height()],
                                      "overflow_y": max(0, content.height() - w.viewport().height()),
                                      "overflow_x": max(0, content.width() - w.viewport().width()),
                                      "value": w.verticalScrollBar().value()}
            covered = self.covered_by(w, visible)
            if covered:
                rec["covered_by"] = covered
            px_size = rec.get("font_px") or 12
            whole = visible[2] * visible[3] >= 0.9 * rect[2] * rect[3] and visible[3] >= 0.6 * px_size
            if pixels is not None and text and not covered and whole:
                x, y, vw, vh = visible
                sampled = colormath.sample(pixels[y:y + vh, x:x + vw], self.declared_color(w))
                if sampled:
                    sampled["declared"] = self.declared_color(w)
                    rec["colors"] = sampled
            if kind == "zone_bar" and pixels is not None and not covered:
                rec["band_colors"] = self.band_colors(w, rect, visible, pixels)
            out.append(rec)
        return out

    def overlays(self):
        """[(widget, rect)] of what floats over the window: the toast stack and other top-level windows."""
        if not hasattr(self, "_overlays"):
            self._overlays = []
            for w, offset in self.widgets:
                if isinstance(w, ToastStack) and w.height() > 0:
                    self._overlays.append((w, self.geometry(w, offset)))
                elif w.isWindow() and w is not self.window:
                    self._overlays.append((w, self.geometry(w, offset)))
        return self._overlays

    def covered_by(self, w, visible):
        """The path of an overlay (a toast, a dialog, a popup) drawn over a main-window widget, or None."""
        if visible[2] <= 0 or visible[3] <= 0 or w.window() is not self.window:
            return None
        for over, rect in self.overlays():
            if over is w or over.isAncestorOf(w) or w.isAncestorOf(over):
                continue
            inter = _inter(visible, rect)
            if inter[2] * inter[3] > 0.25 * visible[2] * visible[3]:
                return self.path(over)
        return None

    def band_colors(self, bar, rect, visible, pixels):
        """Each zone band's fill sampled at its middle and the colour just above the band (what the bar sits
        on), for the non-text contrast check."""
        from ui.components.data import END_PAD, HEADROOM
        top = 7 if bar.compact else HEADROOM
        x0, span = END_PAD, max(1.0, bar.width() - 2 * END_PAD)
        out = []
        H, W = pixels.shape[:2]

        def inside(x, y):
            return visible[0] <= x < visible[0] + visible[2] and visible[1] <= y < visible[1] + visible[3]

        def px(x, y):
            x, y = int(min(W - 1, max(0, x))), int(min(H - 1, max(0, y)))
            return colormath.to_hex(pixels[y, x])
        bx, by = rect[0] + 2, rect[1] + top - 3
        if not inside(bx, by):
            return out
        behind = px(bx, by)
        for z in bar.zones:
            fa, fb = bar._zone_span(z)
            if fb <= fa:
                continue
            cx, cy = rect[0] + x0 + (fa + fb) / 2 * span, rect[1] + top + 2
            if inside(cx, cy):
                out.append({"id": z.get("id"), "fill": px(cx, cy), "behind": behind})
        return out


def find(window, path):
    """The visible widget at a path (as Inspector.path names it), or None."""
    insp = Inspector(window)
    for w, _ in insp.widgets:
        if insp.path(w) == path:
            return w
    return None
