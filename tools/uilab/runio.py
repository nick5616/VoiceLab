"""Reading and writing a lab run's files: JSON that tolerates numpy values and NaN, and JSON lines."""
import json
import math
from pathlib import Path

import numpy as np


def jsonable(value):
    """value with numpy types, tuples, sets and NaN/inf made JSON-safe (NaN and inf become None)."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return [jsonable(v) for v in value.tolist()]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    return repr(value)


def write_json(path, value, indent=1):
    Path(path).write_text(json.dumps(jsonable(value), indent=indent, ensure_ascii=False), encoding="utf-8")


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


class JsonLines:
    """Append JSON objects to a file, one per line."""

    def __init__(self, path):
        self.path = Path(path)
        self.file = open(self.path, "a", encoding="utf-8")

    def write(self, obj):
        self.file.write(json.dumps(jsonable(obj), ensure_ascii=False) + "\n")

    def flush(self):
        self.file.flush()

    def close(self):
        if not self.file.closed:
            self.file.close()


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:    # a line cut short by a crash
                continue
    return out


def summarize(args):
    """Event arguments shortened for the log: takes as their uid and a few fields, job lists as labels."""
    out = []
    for a in args:
        if isinstance(a, dict) and "uid" in a:
            out.append({k: a.get(k) for k in ("uid", "title", "pitch", "mode", "analysis_version") if k in a})
        elif isinstance(a, list) and (not a or isinstance(a[0], dict) and "label" in a[0]):
            out.append([{k: j.get(k) for k in ("id", "kind", "label", "done", "total")} for j in a])
        elif isinstance(a, dict):
            out.append({k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v)[:80])
                        for k, v in list(a.items())[:12]})
        elif isinstance(a, str):
            out.append(a[:300])
        elif isinstance(a, (int, float, bool, type(None))):
            out.append(a)
        else:
            out.append(str(a)[:120])
    return out
