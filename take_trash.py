"""A deleted take goes to a `.trash` folder inside the takes folder, so a slip can be undone. Plain files:
the audio and its .json move, nothing is converted, and the user can look in the folder or empty it
themselves. Nothing here imports a UI toolkit."""
import os
import shutil
import time
from pathlib import Path

TRASH_DIR = ".trash"            # inside the takes folder; skipped when the folder is loaded (not an audio file)
KEEP_DAYS = 30                  # days a trashed take stays before it is emptied; own choice
STAMP = "%Y%m%d-%H%M%S"         # prefix that keeps two takes with the same name apart in the trash


def trash_folder(folder):
    return Path(folder) / TRASH_DIR


def move_to_trash(folder, paths, now=None):
    """Move the existing files in `paths` into the trash. Returns [(trash path, original path)], the
    receipt that restore() needs. A file that is already gone is skipped."""
    target = trash_folder(folder)
    target.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime(STAMP, time.localtime(now or time.time()))
    moved = []
    for path in map(Path, paths):
        if not path.exists():
            continue
        dest = target / f"{stamp} {path.name}"
        n = 2
        while dest.exists():
            dest = target / f"{stamp} ({n}) {path.name}"
            n += 1
        shutil.move(str(path), str(dest))
        os.utime(dest, None)        # the trash clock starts now, not at the file's age
        moved.append((dest, path))
    return moved


def restore(receipt):
    """Put the files of a receipt back where they were. A name taken meanwhile gets " (restored)" added.
    Returns the restored paths."""
    back = []
    for trashed, original in receipt:
        trashed, original = Path(trashed), Path(original)
        if not trashed.exists():
            continue
        if original.exists():
            original = original.with_name(f"{original.stem} (restored){original.suffix}")
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(trashed), str(original))
        back.append(original)
    return back


def purge(folder, days=KEEP_DAYS, now=None):
    """Delete trashed files older than `days`. Returns how many were removed."""
    target = trash_folder(folder)
    if not target.is_dir():
        return 0
    limit = (now or time.time()) - days * 86400
    removed = 0
    for path in target.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < limit:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
