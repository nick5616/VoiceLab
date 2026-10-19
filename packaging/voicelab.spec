# PyInstaller spec: builds the VoiceLab app (onedir, windowed) and a console smoke-test target.
# Run from the repo root: pyinstaller packaging/voicelab.spec
import glob
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # SPECPATH is the packaging/ folder

# Third-party packages with native libs, data files or lazy imports that PyInstaller misses.
datas, binaries, hiddenimports = [], [], []
for pkg in ("parselmouth", "sounddevice", "_sounddevice_data", "qtawesome", "pyworld", "soundfile"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("pyqtgraph") + collect_submodules("mido")
datas += collect_data_files("pyqtgraph")

# Every app module in the repo root (covers modules added later), plus scipy bits pulled in lazily.
app_modules = [os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(ROOT, "*.py"))]
hiddenimports += app_modules + ["scipy.signal", "scipy.special._cdflib"]

# QtOpenGL, QtSvg and QtWidgets stay in: pyqtgraph imports them at load time.
EXCLUDES = [
    "torch", "torchaudio", "demucs", "tkinter", "matplotlib", "IPython", "pytest",
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineQuick", "PyQt6.QtWebChannel",
    "PyQt6.Qt3DCore", "PyQt6.Qt3DRender", "PyQt6.Qt3DInput", "PyQt6.Qt3DLogic", "PyQt6.Qt3DAnimation",
    "PyQt6.Qt3DExtras", "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets", "PyQt6.QtQml", "PyQt6.QtQuick",
    "PyQt6.QtQuickWidgets", "PyQt6.QtQuick3D", "PyQt6.QtDesigner", "PyQt6.QtBluetooth", "PyQt6.QtNfc",
    "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtPositioning",
    "PyQt6.QtRemoteObjects", "PyQt6.QtSql", "PyQt6.QtTest", "PyQt6.QtHelp", "PyQt6.QtNetwork",
    "PyQt6.QtDBus",
]


def target(script, name, console):
    a = Analysis([os.path.join(ROOT, script) if script == "main.py" else os.path.join(SPECPATH, script)],
                 pathex=[ROOT], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
                 excludes=EXCLUDES, noarchive=False)
    pyz = PYZ(a.pure)
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console, upx=False)
    return a, exe


app_a, app_exe = target("main.py", "VoiceLab", False)
smoke_a, smoke_exe = target("smoke_entry.py", "VoiceLabSmoke", True)

# One shared folder: dist/VoiceLab/VoiceLab.exe (and VoiceLabSmoke.exe beside it).
coll = COLLECT(app_exe, app_a.binaries, app_a.datas,
               smoke_exe, smoke_a.binaries, smoke_a.datas,
               upx=False, name="VoiceLab")
