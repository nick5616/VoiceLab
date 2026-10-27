"""The scenario driver: builds the real window on a prepared profile and takes folder, runs a scenario's beats
(one action each), and captures every beat at each requested size and theme. Time is audio time: an
AudioClock advances with every fed hop (and by BEAT_SECONDS per other beat), and the window's clocks and
timers that would follow the wall clock (record button, session, banner hold, toasts, exercise) follow it
instead, so a run is the same every time."""
import random
import shutil
import sys
import time
import traceback
import zlib
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QMenu

import main
from engine import UPDATE_INTERVAL, LiveEngine
from profile_store import Profile
from tools.uilab.runio import JsonLines, read_json, summarize, write_json
from ui import theme
from ui.components import Collapsible, Toast
from ui.lab import env
from ui.lab.capture import capture, settle
from ui.lab.inspector import Inspector

HOME_SIZE = (1920, 1080)        # px: beats run at this size; other sizes are visited for each capture
SAMPLE_RATE = 44100             # Hz; the engine's rate (LiveEngine default)
HOP = int(UPDATE_INTERVAL * SAMPLE_RATE)
PROCESS_EVERY = 4               # fed frames between Qt event passes (100 ms of audio): paints stay cheap
MOTION_FRAMES = 8               # --motion: frames in a beat's strip
MOTION_MS = 60                  # ms between them (about 0.5 s in all: longer than anim.ENTER_MS and MOVE_MS)
MOTION_SHRINK = 4               # strip frames are a quarter size
MOTION_MOVED = 0.05             # mean abs pixel change between frames that counts as motion
MOTION_SETTLED = 0.02           # ... and under which the last two frames count as at rest
BEAT_SECONDS = 1.0              # audio time a non-audio beat takes (a click, a look): timers move on with it
JOB_TIMEOUT = 600               # s: the longest a beat waits for background jobs
WORK_ROOT = Path(r"C:\tmp\vl-lab")                     # scenario profiles and takes copies (see Lab.work_root)
REAL_TAKES = Path(r"C:\Users\nickb\VoiceLab")          # the user's takes: read only, always copied
USER_HOME = Path.home() / ".voicelab"                   # the user's profile: read only, always copied
TARGETS = {"pitch": [165.0, 230.0], "delta_f": [1060.0, 1250.0]}   # the targets the phase 10 shots used


class AudioClock:
    """Seconds of audio fed so far (plus BEAT_SECONDS per non-audio beat)."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class AudioTimer:
    """A stand-in for a QTimer that fires on audio time (driven by Session.tick)."""

    def __init__(self, clock, interval_ms, callback, single=True):
        self.clock, self.interval, self.callback, self.single = clock, interval_ms / 1000.0, callback, single
        self.due = None

    def start(self, ms=None):
        if ms is not None:
            self.interval = ms / 1000.0
        self.due = self.clock() + self.interval

    def stop(self):
        self.due = None

    def isActive(self):
        return self.due is not None

    def setInterval(self, ms):
        self.interval = ms / 1000.0

    def fire_if_due(self):
        if self.due is not None and self.clock() >= self.due - 1e-9:
            self.due = None if self.single else self.clock() + self.interval
            try:
                self.callback()
            except RuntimeError:        # its widget was deleted meanwhile (a toast already gone)
                self.due = None


def isolate_theme_hub():
    """A fresh theme-change signal for each scenario's window. Some widgets connect theme changes to lambdas
    that outlive them (ui/app_window.Bar, ui/takes/take_card._Icon, take_graph), so a theme switch after a
    window is gone raises in every one; a new hub keeps one scenario's leftovers out of the next."""
    theme._hub = theme._Hub()
    theme.changed = theme._hub.changed


def copy_takes(source, dest, names=None):
    """Copy WAVs and sidecars (all, or the stems in `names`) from source to dest; never writes to source."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(Path(source).glob("*")):
        if path.suffix.lower() in (".wav", ".json") and (names is None or path.stem in names):
            shutil.copy2(path, dest / path.name)
    return dest


def make_profile(home, options, takes_dir):
    """A profile in `home` from options: base ("user": a copy of the user's profile; else fresh), onboarded,
    goal, targets, baseline (False drops it), theme, mode."""
    home = Path(home)
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    if options.get("base") == "user" and USER_HOME.exists():
        for name in ("profile.json", "voicemap.npz", "history.jsonl"):
            if (USER_HOME / name).exists():
                shutil.copy2(USER_HOME / name, home / name)
    profile = Profile(home)
    profile.takes_dir = str(takes_dir) if takes_dir else None
    profile.takes_dir_asked = takes_dir is not None or options.get("asked", True)
    profile.mode = options.get("mode", "speak")
    profile.ui["onboarded"] = options.get("onboarded", True)
    profile.ui["theme"] = options.get("theme", "dark")
    profile.ui["reduce_motion"] = False
    if "goal" in options:
        profile.voice_goal = options["goal"]
    if "targets" in options:
        profile.targets = {k: list(v) for k, v in (options["targets"] or {}).items()}
    if options.get("baseline") is False:
        profile.baseline = None
    profile.mic_check_asked = True
    profile.save()
    return Profile(home)


def prepare_cache(cache_dir, log=print):
    """A copy of the user's takes re-analysed with the current analysis, made once and reused: scenarios
    copy from it, so they don't wait minutes for the re-analysis on every load."""
    import app_core
    from take_store import ANALYSIS_VERSION, TakeStore
    tag = zlib.crc32(str(REAL_TAKES).lower().encode()) & 0xFFFF
    cache = Path(cache_dir) / f"takes-v{ANALYSIS_VERSION}-{tag:04x}"
    marker = cache / "_ready.json"
    names = sorted(p.name for p in REAL_TAKES.glob("*.wav"))
    if read_json(marker, {}).get("names") == names:
        return cache
    log(f"preparing the real-takes cache in {cache} (one-off: re-analysing {len(names)} takes)…")
    if cache.exists():
        shutil.rmtree(cache)
    copy_takes(REAL_TAKES, cache)
    home = Path(cache_dir) / "cache-home"
    profile = make_profile(home, {"base": "user"}, cache)
    core = app_core.VoiceLabCore(profile, engine=None, store=TakeStore(cache))
    core.reload()
    end = time.time() + 1800
    while time.time() < end:
        core.runner.wait_all(5)
        if not core.runner.jobs() and not core.outdated_takes():
            break
    write_json(marker, {"names": names, "analysis_version": ANALYSIS_VERSION})
    return cache


class Session:
    """One scenario's window, clock, recorders and beats."""

    def __init__(self, lab, scenario):
        self.lab, self.scenario = lab, scenario
        self.id = scenario["id"]
        self.dir = Path(lab.run_dir) / self.id
        self.work = lab.work_root / self.id
        for d in (self.dir, self.work):
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)
        self.clock = AudioClock()
        self.timers = []
        self.events = JsonLines(self.dir / "events.jsonl")
        self.beats = []
        self.frames = 0
        self.truth = dict(scenario.get("truth") or {})
        self.home_theme = scenario.get("profile", {}).get("theme", "dark")
        self.window = None

    # --- building ---

    def takes_dir(self):
        kind = self.scenario.get("takes", "none")
        if kind == "none":
            return None
        dest = self.work / "takes"
        if kind == "empty":
            dest.mkdir(parents=True, exist_ok=True)
        elif kind == "real":
            copy_takes(self.lab.cache(), dest)
        elif kind == "real_original":
            copy_takes(REAL_TAKES, dest)
        return dest

    def build(self):
        isolate_theme_hub()
        self.catch_exceptions()
        Collapsible.memory.clear()
        import ui.takes
        ui.takes._reports.clear()
        env.reset_calls()
        takes = self.takes_dir()
        profile = make_profile(self.work / "home", self.scenario.get("profile", {}), takes)
        theme.set_theme(self.home_theme)
        engine = LiveEngine()
        window = main.build(engine, profile)
        self.window, self.core, self.engine = window, window.core, engine
        self._instrument()
        window.resize(*HOME_SIZE)
        window.show()
        settle()
        if takes is not None:
            self.wait(lambda: not self.core.loading)
            self.wait_jobs()
        self.truth.setdefault("expected_takes", len(self.core.takes()))

    def _instrument(self):
        """Audio-time clocks and timers, and the event, frame and toast recorders."""
        w, core, clock = self.window, self.window.core, self.clock
        core.clock = clock
        core.live.clock = clock
        core.live.text_interval = 0
        core.exercise.clock = clock
        w.record_btn.clock = clock
        hold = w.banner_timer.interval()
        w.banner_timer.stop()
        w.banner_timer = AudioTimer(clock, hold, lambda: w.show_banner(w.banner_info, force=True))
        w.session_timer.stop()
        w.session_timer = AudioTimer(clock, w.session_timer.interval(), w.update_session, single=False)
        self.timers += [w.banner_timer, w.session_timer]
        session = self

        def start_toast(toast):
            from ui import anim
            anim.fade_in(toast)
            if toast.timeout_ms:
                timer = AudioTimer(clock, toast.timeout_ms, toast.dismiss)
                timer.start()
                session.timers.append(timer)
        self.toast_start = Toast.start
        Toast.start = start_toast
        emit = core.events.emit

        def logged_emit(name, *args):
            self.events.write({"type": "event", "name": name, "args": summarize(args), "t": round(clock.t, 4),
                               "wall": round(time.perf_counter(), 4)})
            emit(name, *args)
        core.events.emit = logged_emit
        show_toast = w.toasts.show_toast

        def logged_toast(text, kind="info", *a, **k):
            self.events.write({"type": "toast", "text": text, "kind": kind, "t": round(clock.t, 4),
                               "wall": round(time.perf_counter(), 4)})
            return show_toast(text, kind, *a, **k)
        w.toasts.show_toast = logged_toast

    # --- time ---

    def tick(self):
        for timer in list(self.timers):
            timer.fire_if_due()
        self.timers = [t for t in self.timers if t.isActive() or not t.single]
        self.window.record_btn._tick()

    def advance(self, seconds, step=UPDATE_INTERVAL):
        """Let audio time pass without sound (timers fire as it does)."""
        end = self.clock.t + seconds
        while self.clock.t < end - 1e-9:
            self.clock.t = min(end, self.clock.t + step)
            self.tick()
        settle(1)

    def wait(self, condition, timeout=JOB_TIMEOUT):
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            QApplication.processEvents()
            if condition():
                settle(1)
                return True
            time.sleep(0.01)
        return False

    def wait_jobs(self, timeout=JOB_TIMEOUT):
        ok = self.wait(lambda: not self.core.runner.jobs(), timeout)
        settle()
        return ok

    # --- audio ---

    def feed(self, audio, realtime=False):
        """Feed audio as the mic would: one live frame per hop (the engine's own frame function), with the
        UI's readouts logged after each frame."""
        audio = np.asarray(audio, dtype=float)
        engine = self.engine
        started = time.perf_counter()
        for i in range(0, len(audio) - HOP + 1, HOP):
            engine.feed(audio[i:i + HOP])
            self.clock.t += HOP / SAMPLE_RATE
            frame = engine._worker.frame()
            t0 = time.perf_counter()
            engine.publish(frame)
            cost = time.perf_counter() - t0
            self.frames += 1
            self.tick()
            self._log_frame(frame, cost)
            if realtime:
                QApplication.processEvents()
                lag = started + self.clock.t - time.perf_counter()
                if lag > 0:
                    time.sleep(lag)
            elif self.frames % PROCESS_EVERY == 0:
                t1 = time.perf_counter()
                QApplication.processEvents()
                self.events.write({"type": "paint", "ms": round(1000 * (time.perf_counter() - t1), 2),
                                   "t": round(self.clock.t, 4)})
        settle(1)

    def _log_frame(self, frame, cost):
        w = self.window
        screen = w.stack.currentWidget()
        view = w.dashboard.live if screen is w.dashboard else (w.live.view if screen is w.live else None)
        rec = {"type": "frame", "i": self.frames, "t": round(self.clock.t, 4), "ui_ms": round(cost * 1000, 2),
               "f0": frame.get("f0"), "screen": w.current()}
        if view is not None:
            rec["readout"] = view.note_label.text()
            card = view.cards.cards.get("pitch")
            rec["card_pitch"] = card.value.text() if card is not None and card.isVisible() else None
            x, y = view.graph.dot.getData()
            rec["dot"] = None if y is None or not len(y) else float(y[0])
            rec["marker"] = view.graph.marker.toPlainText() if view.graph.marker.isVisible() else None
        rec["banner"] = w.banner.label.text() if not w.banner.isHidden() else None
        self.events.write(rec)

    # --- beats ---

    def run(self):
        self.build()
        for index, beat in enumerate(self.scenario["beats"], start=1):
            self.run_beat(index, beat)
        self.finish()

    def run_beat(self, index, beat):
        name = f"{index:02d}-{beat['name']}"
        self.events.write({"type": "beat", "name": name, "phase": "start", "t": round(self.clock.t, 4),
                           "wall": round(time.perf_counter(), 4)})
        truth = dict(beat.get("truth") or {})
        truth["beat_start"] = round(self.clock.t, 4)
        error = None
        try:
            result = getattr(self, "do_" + beat["do"])(**beat.get("args", {}))
        except Exception as e:      # a broken beat is a finding, not a crashed run
            import traceback
            error, result = f"{type(e).__name__}: {e}", None
            traceback.print_exc()
        motion = self.motion_strip(name) if self.lab.motion and beat.get("capture", True) else None
        if beat["do"] != "feed" and not beat.get("instant"):
            self.advance(beat.get("seconds", BEAT_SECONDS))
        truth["beat_end"] = round(self.clock.t, 4)
        self.events.write({"type": "beat", "name": name, "phase": "end", "t": round(self.clock.t, 4),
                           "wall": round(time.perf_counter(), 4), "error": error})
        record = {"name": name, "do": beat["do"], "error": error, "truth": truth, "captures": [],
                  "result": result if isinstance(result, (dict, list, str, bool, int, float)) else None}
        if beat.get("capture", True):
            record["captures"] = self.capture_all(name, truth, beat.get("variants"))
        if motion:
            record["motion"] = motion
        self.beats.append(record)
        self.lab.log(f"  {self.id} {name}" + (f"  ERROR {error}" if error else ""))

    def capture_all(self, name, truth, variants=None):
        """Capture the beat at each (theme, size) of the run, then go back to the home theme and size."""
        out = []
        themes = self.lab.themes if variants != "home" else [self.home_theme]
        sizes = self.lab.sizes if variants != "home" else [HOME_SIZE]
        full_truth = dict(self.truth, **truth)
        for th in themes:
            theme.set_theme(th)
            for size in sizes:
                self.window.resize(*size)
                settle()
                stem = str(self.dir / f"{name}-{th}-{size[0]}x{size[1]}")
                meta = capture(self.window, stem, full_truth, {"theme": th, "size_requested": list(size),
                                                               "audio_t": round(self.clock.t, 4)})
                out.append({"theme": th, "size": list(size), "stem": Path(stem).name, "actual": meta["size"],
                            "screen": meta["screen"], "dialog": meta["dialog"], "full": meta["full"],
                            "capture_ms": meta["capture_ms"]})
        theme.set_theme(self.home_theme)
        self.window.resize(*HOME_SIZE)
        settle()
        return out

    def motion_strip(self, name):
        """With --motion: MOTION_FRAMES grabs MOTION_MS apart right after the action, as one strip PNG, and
        whether anything moved and whether it came to rest (the last two frames match)."""
        from tools.uilab import png
        from tools.uilab.diff import mean_abs_diff
        from ui.lab.capture import to_array
        frames = []
        for _ in range(MOTION_FRAMES):
            QApplication.processEvents()
            time.sleep(MOTION_MS / 1000)
            frames.append(png.shrink(to_array(self.window.grab())[:, :, :3], MOTION_SHRINK))
        diffs = [mean_abs_diff(a, b) for a, b in zip(frames, frames[1:])]
        gap = np.full((frames[0].shape[0], 4, 3), 128, dtype=np.uint8)
        strip = np.concatenate([x for f in frames for x in (f, gap)][:-1], axis=1)
        path = self.dir / f"{name}.motion.png"
        png.write_png(path, strip)
        return {"file": path.name, "diffs": [round(d, 3) for d in diffs], "moved": max(diffs, default=0) > MOTION_MOVED,
                "settled": bool(diffs) and diffs[-1] < MOTION_SETTLED}

    def catch_exceptions(self):
        """Exceptions raised in Qt slots and handlers during the scenario go to the event log (an eval turns them
        into findings) as well as to stderr."""
        previous = sys.excepthook

        def hook(kind, value, tb):
            frames = traceback.extract_tb(tb)
            app_frames = [f for f in frames if Path(f.filename).parent.name != "lab"] or frames
            where = app_frames[-1] if app_frames else None
            self.events.write({"type": "exception", "error": f"{kind.__name__}: {value}"[:300],
                               "where": f"{Path(where.filename).name}:{where.lineno}" if where else "",
                               "code": where.line if where else "", "t": round(self.clock.t, 4)})
            previous(kind, value, tb)
        self.previous_hook = previous
        sys.excepthook = hook

    def finish(self):
        write_json(self.dir / "beats.json", {"scenario": self.scenario_meta(), "beats": self.beats})
        self.events.close()
        w = self.window
        for dialog in w.findChildren(QDialog):
            dialog.hide()
        w.close()                   # waits for the jobs (core.close)
        for wav in (self.work / "takes").glob("*.wav"):     # keep the sidecars (the evals read them), not the audio
            try:
                wav.unlink()
            except OSError:
                pass
        if getattr(self, "toast_start", None) is not None:
            Toast.start = self.toast_start
        if getattr(self, "previous_hook", None) is not None:
            sys.excepthook = self.previous_hook
        w.deleteLater()
        settle()
        theme.set_theme("dark")

    def scenario_meta(self):
        sc = self.scenario
        return {"id": sc["id"], "groups": sc.get("groups", []), "title": sc.get("title", ""),
                "takes": sc.get("takes", "none"), "profile": sc.get("profile", {}), "truth": self.truth}

    # --- the actions a beat can take ---

    def do_go(self, screen):
        self.window.go(screen)

    def do_set_mode(self, mode):
        self.core.set_mode(mode)

    def do_open_practice(self, key):
        self.window.go("practice")
        self.window.practice.open(key)

    def do_close_practice(self):
        self.window.practice.close_practice()

    def do_feed(self, audio, realtime=False, **_):
        self.feed(audio() if callable(audio) else audio, realtime)

    def do_wait(self, seconds):
        self.advance(seconds)

    def do_record_start(self):
        self.window.toggle_recording()

    def do_record_stop(self):
        self.window.toggle_recording()

    def do_wait_jobs(self, timeout=JOB_TIMEOUT):
        return {"finished": self.wait_jobs(timeout)}

    def do_wait_event(self, event, timeout=JOB_TIMEOUT):
        """Wait (wall time) for one core event, e.g. a take re-analysed."""
        seen = []
        off = self.core.events.subscribe(event, lambda *a: seen.append(1))
        ok = self.wait(lambda: bool(seen), timeout)
        off()
        return {"seen": ok}

    def do_import(self, path):
        env.answers()["open_file"] = str(path)
        self.window.import_audio()
        env.answers()["open_file"] = ""

    def do_start_up(self):
        self.window.start_up()

    def do_click(self, path, item=None):
        """Click the visible widget at `path` (an item key for nav, tabs and segmented controls)."""
        w = find_widget(self.window, path)
        if w is None:
            raise LookupError(f"no visible widget at {path}")
        click(w, item)

    def do_key(self, sequence):
        press(self.window, sequence)

    def do_call(self, target, *args):
        """Call a method on the window by attribute path ("practice.exercise.start")."""
        obj = self.window
        for part in target.split("."):
            obj = getattr(obj, part)
        return obj(*args) if callable(obj) else obj

    def do_resize(self, w, h):
        self.window.resize(w, h)
        settle()

    def do_theme(self, theme):
        self.core.set_ui_pref(theme=theme)
        self.home_theme = theme

    def do_fake_mic_error(self, problem="Error opening InputStream: Device unavailable [PaErrorCode -9985]. "
                                        "Connect one and pick it in Settings"):
        self.core.set_mic_available(False, problem)

    def do_inject_status(self, text, level="info"):
        self.core._status(text, level)

    def do_start_reanalysis(self, count=2):
        """Mark the newest `count` saved takes as made by an older analysis and start the re-analysis."""
        marked = []
        for data in self.core.takes()[:count]:
            data["analysis_version"] = 0
            marked.append(data.get("uid"))
        self.core.update_reanalysis()
        settle()
        return {"marked": marked}

    def do_show_take(self, index):
        takes = self.core.takes()
        if index < len(takes):
            self.window.go("takes")
            self.window.takes.show_take(takes[index]["uid"])
            settle()
            return {"uid": takes[index]["uid"]}
        return None

    def do_expand_all(self, root="takes/full"):
        """Open every collapsed section under a widget (the take report's Details and its sections)."""
        base = find_widget(self.window, root)
        if base is None:
            return {"expanded": 0}
        count = 0
        for _ in range(3):              # sections built when opened may hold more sections
            settle()
            for col in base.findChildren(Collapsible):
                if col.isVisibleTo(base) and not col.expanded:
                    col.set_expanded(True, animate=False)
                    count += 1
        settle()
        return {"expanded": count}

    def do_scroll(self, path, to="bottom"):
        w = find_widget(self.window, path)
        bar = w.verticalScrollBar()
        bar.setValue(bar.maximum() if to == "bottom" else (0 if to == "top" else int(to)))

    def do_dismiss(self):
        """Close dialogs, menus and popups (as Escape would)."""
        for w in QApplication.topLevelWidgets():
            if w.isVisible() and (isinstance(w, (QDialog, QMenu)) or w.windowType() == Qt.WindowType.Popup):
                w.close() if isinstance(w, QDialog) else w.hide()
        settle()

    def do_replay_take(self, stem, seconds_max):
        """Feed the start of a real take (the run's copy) live, as if it were being spoken now."""
        from ui.lab.scenarios import replay_audio
        folder = self.work / "takes"
        audio = replay_audio(stem, seconds_max, folder)
        self.feed(audio)
        return {"take": stem, "fed_seconds": round(len(audio) / SAMPLE_RATE, 3), "sidecar": str(folder / f"{stem}.json")}

    def do_reach(self, feature):
        from ui.lab.reach import drive
        return drive(self, feature)

    def do_exercise_sing(self, formants=None, seconds_after=1.5):
        """Sing the running exercise's notes in time (synth), then stop feeding when it has finished."""
        import synth
        session = self.core.exercise
        targets = list(session.targets or [])
        if not targets:
            return {"notes": 0}
        end = max(s + d for s, d, _ in targets) + seconds_after
        start = session.now() or 0.0
        t = np.arange(int((end - start) * SAMPLE_RATE)) / SAMPLE_RATE + start
        f0 = np.full(len(t), np.nan)
        for s, d, midi in targets:
            f0[(t >= s + 0.05) & (t < s + d - 0.05)] = 440.0 * 2 ** ((midi - 69) / 12)
        audio = synth.synth_voice(f0, formants or synth.NEUTRAL_FORMANTS)
        self.feed(audio + synth.room_noise(len(audio) / SAMPLE_RATE, seed=3))
        return {"notes": len(targets)}


def find_widget(window, path):
    insp = Inspector(window)
    for w, _ in insp.widgets:
        if insp.path(w) == path:
            return w
    return None


def item_rect(w, key):
    """The rect of one item of a nav rail, tab bar or segmented control, in the widget's coordinates."""
    from ui.components import NavRail, SegmentedControl, Tabs
    if isinstance(w, NavRail):
        return w.item_rect(key).toRect()
    if isinstance(w, Tabs):
        return w._rects()[w.keys.index(key)].toRect()
    if isinstance(w, SegmentedControl):
        i = w.keys.index(key)
        from PyQt6.QtCore import QRect
        return QRect(3 + i * w.segment, 3, w.segment, w.height() - 6)
    raise ValueError(f"{type(w).__name__} has no items")


def click(w, item=None):
    """A real left click at the widget's centre (or at an item's centre)."""
    pos = item_rect(w, item).center() if item is not None else w.rect().center()
    QTest.mouseClick(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(pos.x(), pos.y()))
    settle()


def press(window, sequence):
    """A keyboard shortcut: the window's QShortcut for it is activated (the offscreen platform does not
    focus windows, so key events would not reach it), else the key goes to the focused widget."""
    seq = QKeySequence(sequence)
    for shortcut in window.findChildren(QShortcut):
        if shortcut.key() == seq and shortcut.isEnabled():
            shortcut.activated.emit()
            settle()
            return True
    QTest.keySequence(QApplication.focusWidget() or window, seq)
    settle()
    return False


class Lab:
    """Runs scenarios into run_dir at each size and theme."""

    def __init__(self, run_dir, sizes=((1920, 1080), (1280, 720)), themes=("dark", "light"), motion=False,
                 cache_dir=None, log=print):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.sizes = [tuple(s) for s in sizes]
        self.themes = list(themes)
        self.motion = motion
        self.cache_dir = Path(cache_dir) if cache_dir else self.run_dir.parent / "_lab-cache"
        # Profiles and takes copies live under a short path of fixed length, whatever the run folder: the
        # takes folder's path shows in Settings, and a longer one would wrap or cut differently.
        self.work_root = WORK_ROOT / f"{random.SystemRandom().randrange(10 ** 5, 10 ** 6)}"
        write_json(self.run_dir / "work.json", {"work": str(self.work_root)})
        self._cache = None
        self.log = log
        self.app = env.setup(motion)

    def cache(self):
        if self._cache is None:
            self._cache = prepare_cache(self.cache_dir, self.log)
        return self._cache

    def run(self, scenarios):
        done = []
        for sc in scenarios:
            started = time.perf_counter()
            self.log(f"scenario {sc['id']}")
            session = Session(self, sc)
            try:
                session.run()
            except Exception as e:  # report and go on with the next scenario
                import traceback
                traceback.print_exc()
                self.log(f"  scenario {sc['id']} failed: {e}")
                try:
                    session.finish()
                except Exception:
                    pass
            done.append({"id": sc["id"], "seconds": round(time.perf_counter() - started, 1)})
        return done
