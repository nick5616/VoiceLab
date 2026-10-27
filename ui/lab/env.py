"""The lab's process: an offscreen Qt application, motion off, and fakes for everything that would reach the
real machine (speakers, the mic list, file pickers and blocking message boxes). Nothing here opens a mic.
setup() installs the fakes; restore() puts the originals back (tests share their process with other tests)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAKE_DEVICES = [(0, "Microphone (USB PnP Audio Devic"), (1, "Headset Microphone (Lab)")]  # the user's MME name, as Windows cuts it
FAKE_CHANNELS = 1               # inputs per fake device: no EGG option (as the user's USB mic)
_state = {"saved": None, "calls": [], "answers": {"open_file": "", "folder": ""}}


def calls():
    """[(kind, detail)] of the dialogs the app asked for since the last reset (file pickers, message boxes)."""
    return _state["calls"]


def reset_calls():
    _state["calls"].clear()


def answers():
    """What the faked file pickers return ({"open_file": path, "folder": path}); a beat may change them."""
    return _state["answers"]


def setup(motion=False):
    """The QApplication with VoiceLab's theme, motion as asked, and the fakes installed."""
    os.environ["VOICELAB_REDUCE_MOTION"] = "0" if motion else "1"
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import pyqtgraph as pg

    from ui import theme
    if "app_saved" not in _state:
        _state["app_saved"] = (app.styleSheet(), app.font(), app.palette(), theme._state.get("app"))
    theme.set_reduce_motion(not motion)
    theme.apply(app)
    pg.setConfigOptions(antialias=True)
    if _state["saved"] is None:
        _state["saved"] = _install()
    return app


def restore():
    """Undo setup()'s fakes (the application stays)."""
    saved, _state["saved"] = _state["saved"], None
    for obj, name, value in reversed(saved or []):
        setattr(obj, name, value)
    os.environ.pop("VOICELAB_REDUCE_MOTION", None)
    app_saved = _state.pop("app_saved", None)
    if app_saved is not None:
        from PyQt6.QtWidgets import QApplication

        from ui import theme
        app = QApplication.instance()
        sheet, font, palette, themed = app_saved
        app.setStyleSheet(sheet)
        app.setFont(font)
        app.setPalette(palette)
        theme._state["app"] = themed


def _install():
    import sounddevice as sd
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    import app_core
    saved = []

    def put(obj, name, value):
        saved.append((obj, name, obj.__dict__[name] if name in obj.__dict__ else getattr(obj, name)))
        setattr(obj, name, value)

    def no_stream(*a, **k):
        raise sd.PortAudioError("the UI lab never opens a mic")

    def open_file(*a, **k):
        _state["calls"].append(("open_file", a[1] if len(a) > 1 else ""))
        return _state["answers"]["open_file"], ""

    def folder(*a, **k):
        _state["calls"].append(("folder", a[1] if len(a) > 1 else ""))
        return _state["answers"]["folder"]

    def question(parent, title, text, *a, **k):
        _state["calls"].append(("question", f"{title}: {text}"))
        return QMessageBox.StandardButton.Yes

    def exec_box(box):
        _state["calls"].append(("message_box", f"{box.windowTitle()}: {box.text()}"))
        return 0

    put(sd, "play", lambda *a, **k: None)
    put(sd, "stop", lambda *a, **k: None)
    put(sd, "InputStream", no_stream)
    put(app_core, "input_devices", lambda: list(FAKE_DEVICES))
    put(app_core, "input_channels", lambda index: FAKE_CHANNELS)
    put(app_core, "device_index", lambda name: next((i for i, n in FAKE_DEVICES if n == name), None))
    put(QFileDialog, "getOpenFileName", staticmethod(open_file))
    put(QFileDialog, "getExistingDirectory", staticmethod(folder))
    put(QMessageBox, "question", staticmethod(question))
    put(QMessageBox, "exec", exec_box)
    return saved
