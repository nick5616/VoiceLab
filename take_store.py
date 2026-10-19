"""Takes kept in a folder the user picks, so they persist and can be managed
like any other files.

Each take is an audio file plus a JSON sidecar with the same name holding its
analysis, so the folder loads without re-analysing. Takes recorded in VoiceLab
are named "<YYYY-MM-DD HH-MM-SS>[ <title>].wav". Audio files added to the folder
by hand are analysed when the folder loads and get a sidecar then. Delete or
rename a take in the folder (both files) and Reload picks it up.

A take recorded with an EGG is a stereo WAV, mic in channel 0 and EGG in channel 1,
and its sidecar says "egg_channel": 1. audio() gives the mic alone, egg() the EGG;
only files without that note are mixed down to mono.
"""
import json
import re
import secrets
from datetime import datetime
from math import gcd
from pathlib import Path

import numpy as np

from analysis import SAMPLE_RATE, analyze_take
from egg import load_stereo_take
from profile_store import _clean

FORMAT_VERSION = 1
AUDIO_EXTENSIONS = {".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif"}
IMPORT_MAX_SECONDS = 90         # longer files are trimmed to keep analysis quick
# analyze_take reports these as NaN when unmeasured; JSON stores them as null.
NAN_KEYS = ("h1h2", "cpp", "ring", "level", "contour", "pitch_range", "cq")
# In-memory only: never written to the sidecar.
RUNTIME_KEYS = ("audio", "file", "egg_samples")
STAMP_FORMAT = "%Y-%m-%d %H-%M-%S"
MAX_TITLE_CHARS = 60
EGG_CHANNEL = 1                 # channel of a saved take's EGG; the mic is the other one


def load_audio(path, max_seconds=IMPORT_MAX_SECONDS):
    """Mono audio at SAMPLE_RATE, trimmed to max_seconds; returns (audio, trimmed)."""
    import soundfile as sf
    from scipy.signal import resample_poly
    audio, sr = sf.read(str(path), dtype="float64", always_2d=True)
    audio = audio.mean(axis=1)
    trimmed = len(audio) > max_seconds * sr
    audio = audio[:int(max_seconds * sr)]
    if sr != SAMPLE_RATE:
        g = gcd(SAMPLE_RATE, sr)
        audio = resample_poly(audio, SAMPLE_RATE // g, sr // g)
    return audio, trimmed


def new_uid():
    return datetime.now().strftime("%Y%m%dT%H%M%S-") + secrets.token_hex(3)


def safe_title(title):
    """A title usable in a file name: no path or reserved characters."""
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title or "")
    return re.sub(r"\s+", " ", title).strip(" .")[:MAX_TITLE_CHARS]


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return _clean(value)


def _from_json(data):
    """A sidecar back into the shape analyze_take returns."""
    data["tracks"] = {k: np.array(v, dtype=float) for k, v in data["tracks"].items()}
    for key in NAN_KEYS:
        if data.get(key) is None:
            data[key] = np.nan
    return data


class TakeStore:
    def __init__(self, folder):
        self.folder = Path(folder)

    def path(self, data):
        return self.folder / data["file"]

    def sidecar(self, data):
        return self.path(data).with_suffix(".json")

    # --- CREATE ---

    def save(self, result, title=None):
        """Write a take's audio and analysis; sets result["file"]."""
        import soundfile as sf
        self.folder.mkdir(parents=True, exist_ok=True)
        result.setdefault("uid", new_uid())
        result.setdefault("time", datetime.now().isoformat(timespec="seconds"))
        result["dated"] = True
        result["title"] = safe_title(title or result.get("title"))
        stem = self._free_stem(self._stem(result))
        result["file"] = stem + ".wav"
        samples = np.clip(result["audio"], -1, 1)
        egg_samples = result.pop("egg_samples", None)
        if egg_samples is not None:
            n = min(len(samples), len(egg_samples))
            samples = np.column_stack([samples[:n], np.clip(egg_samples[:n], -1, 1)])
            result["egg_channel"] = EGG_CHANNEL
        sf.write(str(self.path(result)), samples, SAMPLE_RATE, subtype="PCM_16")
        self.write_sidecar(result)
        return result["file"]

    def write_sidecar(self, data):
        sidecar = _jsonable({k: v for k, v in data.items() if k not in RUNTIME_KEYS})
        sidecar["version"] = FORMAT_VERSION
        path = self.sidecar(data)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sidecar), encoding="utf-8")
        tmp.replace(path)

    # --- READ ---

    def load_all(self):
        """(takes oldest first, [problem messages]). Audio files without a
        readable sidecar are analysed now and get one."""
        if not self.folder.is_dir():
            return [], [f"Takes folder not found: {self.folder}"]
        takes, problems = [], []
        for path in sorted(self.folder.iterdir()):
            if path.suffix.lower() not in AUDIO_EXTENSIONS or not path.is_file():
                continue
            try:
                data = self._read_sidecar(path)
                if data is None:
                    data = self._analyze_new_file(path)
            except Exception as e:  # one bad file shouldn't stop the folder loading
                problems.append(f"{path.name}: {e}")
                continue
            if "error" in data:
                problems.append(f"{path.name}: {data['error']}")
                continue
            takes.append(data)
        takes.sort(key=lambda d: d["time"])
        return takes, problems

    def _read_sidecar(self, audio_path):
        json_path = audio_path.with_suffix(".json")
        if not json_path.exists():
            return None
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("version") != FORMAT_VERSION or "tracks" not in data:
            return None
        data["file"] = audio_path.name
        return _from_json(data)

    def _analyze_new_file(self, path):
        audio, trimmed = load_audio(path)
        result = analyze_take(audio)
        if "error" in result:
            return result
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        result.update(uid=new_uid(), time=modified.isoformat(timespec="seconds"),
                      title=path.stem, dated=False, file=path.name,
                      source=f"{path.name} (added to folder)" +
                             (f" · first {IMPORT_MAX_SECONDS} s" if trimmed else ""))
        self.write_sidecar(result)
        result.pop("audio")           # loaded again on demand, like saved takes
        return result

    def audio(self, data):
        """The take's audio (the mic, for a take with an EGG), loaded from its file the first time."""
        if data.get("audio") is None:
            if data.get("egg_channel") is not None:
                data["audio"] = self._stereo(data)[0]
            else:
                data["audio"] = load_audio(self.path(data))[0]
        return data["audio"]

    def egg(self, data):
        """The take's EGG samples, or None if it was recorded without one."""
        if data.get("egg_channel") is None:
            return None
        return self._stereo(data)[1]

    def _stereo(self, data):
        mic, egg_samples = load_stereo_take(str(self.path(data)), data["egg_channel"])
        limit = IMPORT_MAX_SECONDS * SAMPLE_RATE
        return mic[:limit], egg_samples[:limit]

    # --- UPDATE ---

    def rename(self, data, title):
        """Retitle a take; its files are renamed to match."""
        data["title"] = safe_title(title)
        old_audio, old_json = self.path(data), self.sidecar(data)
        stem = self._stem(data) or old_audio.stem
        if stem != old_audio.stem:
            stem = self._free_stem(stem, own=old_audio.stem)
            new_audio = old_audio.with_name(stem + old_audio.suffix)
            old_audio.rename(new_audio)
            if old_json.exists():
                old_json.rename(new_audio.with_suffix(".json"))
            data["file"] = new_audio.name
        self.write_sidecar(data)

    # --- DELETE ---

    def delete(self, data):
        for path in (self.path(data), self.sidecar(data)):
            path.unlink(missing_ok=True)

    # --- NAMING ---

    def _stem(self, data):
        """Recorded takes keep their date in the name; files added by hand are
        named by title alone."""
        title = data.get("title") or ""
        if not data.get("dated"):
            return title
        stamp = datetime.fromisoformat(data["time"]).strftime(STAMP_FORMAT)
        return f"{stamp} {title}".strip()

    def _free_stem(self, stem, own=None):
        """stem, or "stem (2)", "stem (3)"... if another take already uses it."""
        taken = {p.stem.lower() for p in self.folder.iterdir()} if self.folder.is_dir() else set()
        taken.discard((own or "").lower())
        candidate, n = stem, 2
        while candidate.lower() in taken:
            candidate, n = f"{stem} ({n})", n + 1
        return candidate
