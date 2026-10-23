"""The shell's screens. This module also fetches spec 06's take, report and progress widgets, falling back
to small placeholders (ui/screens/placeholders.py) while spec 06 hasn't merged, so the shell runs either
way. TAKES_UI says which: "spec06" or "placeholder" (per widget in TAKES_SOURCES)."""
import importlib

from ui.screens import placeholders

# name: (spec 06 module, class): the contract in specs/phase10/05-shell-live.md
CONTRACT = {
    "TakeCard": ("ui.takes.take_card", "TakeCard"),
    "TakesScreen": ("ui.takes.takes_screen", "TakesScreen"),
    "LatestTakePanel": ("ui.takes.latest_take", "LatestTakePanel"),
    "RecentTakes": ("ui.takes.recent_list", "RecentTakes"),
    "ProgressScreen": ("ui.progress.progress_screen", "ProgressScreen"),
    "ProgressMini": ("ui.progress.progress_screen", "ProgressMini"),
    "TargetsEditor": ("ui.takes.targets_editor", "TargetsEditor"),
}
TAKES_SOURCES = {}


def _load(name):
    module, cls = CONTRACT[name]
    try:
        found = getattr(importlib.import_module(module), cls)
        TAKES_SOURCES[name] = "spec06"
        return found
    except ModuleNotFoundError as e:
        if not (e.name or "").startswith(("ui.takes", "ui.progress")):
            raise
    except AttributeError:           # the module is there but not (yet) the class
        pass
    TAKES_SOURCES[name] = "placeholder"
    return getattr(placeholders, cls)


TakeCard = _load("TakeCard")
TakesScreen = _load("TakesScreen")
LatestTakePanel = _load("LatestTakePanel")
RecentTakes = _load("RecentTakes")
ProgressScreen = _load("ProgressScreen")
ProgressMini = _load("ProgressMini")
TargetsEditor = _load("TargetsEditor")
TAKES_UI = "spec06" if all(v == "spec06" for v in TAKES_SOURCES.values()) else "placeholder"
