"""The feature map (phase 10 spec 05: "keep every existing feature reachable"): for each feature, the clicks and
keys that reach it from the dashboard and what must then be visible. The lab drives each path (ui/lab/reach.py)
and the eval turns dead ends into findings.

Steps:   ("nav", key)                 click a nav rail item
         ("click", path)              click the visible widget at a path
         ("item", path, key)          click one item of tabs or a segmented control
         ("text", prefix, text)       click the visible clickable widget under `prefix` whose text is `text`
         ("expand", path)             click a collapsible section's header
         ("key", sequence)            a keyboard shortcut
         ("answer", kind, value)      what the next faked file picker returns ("open_file": "stereo" or "mono")
         ("wait", seconds)            let audio time pass (not a click)
Targets: {"visible": path} | {"text": substring, "under": prefix} | {"called": dialog kind, "contains": text}
         | {"menu": action text} | {"toast": substring} | {"points": (plot path, item name)}
         | {"state": (path, key, value)}   a component's state (tools/uilab tree "state") has key == value
"conditional": the feature exists only on some machines (Demucs installed, a two-input mic); a dead end
there is reported as conditional, not as an error."""
from app_core import REST_NUDGE_MINUTES

FEATURES = [
    {"id": "speak_sing", "name": "Speak / Sing", "steps": [("item", "top/mode", "sing")],
     "target": {"state": ("top/mode", "current", "sing")}, "after": [("item", "top/mode", "speak")]},
    {"id": "voice_goal", "name": "Voice goal", "steps": [], "target": {"visible": "top/goal"}},
    {"id": "live_screen", "name": "Live screen", "steps": [("nav", "live")], "target": {"visible": "live/view/graph"}},
    {"id": "spectrum", "name": "Spectrum", "steps": [("nav", "live"), ("item", "live/view/tabs", "spectrum")],
     "target": {"visible": "live/view/spectrum"}, "after": [("item", "live/view/tabs", "pitch")]},
    {"id": "plane", "name": "Pitch × Resonance plane with take dots",       # phase 11 / 03: the full view is on Live
     "steps": [("nav", "live"), ("item", "live/view/tabs", "plane")], "target": {"points": ("live/view/plane", "takes")},
     "after": [("item", "live/view/tabs", "pitch")]},
    {"id": "targets", "name": "Targets", "steps": [("nav", "settings")], "target": {"visible": "settings/targets"}},
    {"id": "baseline", "name": "Set a take as baseline", "steps": [("nav", "takes"), ("click", "takes/full/more_btn")],
     "target": {"menu": "Set as baseline"}},
    {"id": "use_as_target", "name": "Use a take as target", "steps": [("nav", "takes"), ("click", "takes/full/more_btn")],
     "target": {"menu": "Use as target"}},
    {"id": "read_aloud", "name": "Read aloud", "steps": [("nav", "practice"), ("text", "practice/cards.read", "Open")],
     "target": {"visible": "practice/read/start_btn"}},
    {"id": "phrases", "name": "Phrases", "steps": [("nav", "practice"), ("text", "practice/cards.phrases", "Open")],
     "target": {"visible": "practice/phrases/start_btn"}},
    {"id": "vowel_check", "name": "Vowel check", "steps": [("nav", "practice"), ("text", "practice/cards.vowels", "Open")],
     "target": {"visible": "practice/panels.vowels/view/start_btn"}},
    {"id": "exercises", "name": "Exercises", "steps": [("nav", "practice"), ("text", "practice/cards.exercise", "Open")],
     "target": {"visible": "practice/panels.exercise/view/start_btn"}},
    {"id": "voice_map", "name": "Voice Map", "steps": [("nav", "practice"), ("text", "practice/cards.voicemap", "Open")],
     "target": {"visible": "practice/panels.voicemap/view/plot"}},
    {"id": "voice_map_rebuild", "name": "Voice Map: rebuild from takes",
     "steps": [("nav", "practice"), ("text", "practice/cards.voicemap", "Open"),
               ("text", "practice/panels.voicemap/view", "Rebuild from takes")],
     "target": {"called": "question", "contains": "Rebuild"}},
    {"id": "voice_map_clear", "name": "Voice Map: clear",
     "steps": [("nav", "practice"), ("text", "practice/cards.voicemap", "Open"),
               ("expand", "practice/panels.voicemap/view/more")],      # phase 11 / 06: Clear map is under "More options"
     "target": {"text": "Clear map", "under": "practice/panels.voicemap/view"}},
    {"id": "import", "name": "Import audio", "steps": [("click", "action_bar/import_btn")],
     "target": {"called": "open_file"}},
    {"id": "import_egg", "name": "Import: the EGG channel choice",
     "steps": [("answer", "open_file", "stereo"), ("click", "action_bar/import_btn")],
     "target": {"called": "dialog", "contains": "second sensor"}},      # phase 11 / 05: the import sheet
    {"id": "import_isolate", "name": "Import: isolate vocals with Demucs",
     "steps": [("answer", "open_file", "mono"), ("click", "action_bar/import_btn")],
     "target": {"called": "dialog", "contains": "music behind the voice"}, "conditional": "Demucs installed"},
    {"id": "takes_folder", "name": "Takes folder", "steps": [("click", "action_bar/folder_btn")],
     "target": {"called": "folder"}},
    {"id": "reload", "name": "Reload the takes folder", "steps": [], "target": {"visible": "action_bar/reload_btn"}},
    {"id": "mic_picker", "name": "Mic picker", "steps": [("nav", "settings")], "target": {"visible": "settings/mic_choice"}},
    {"id": "egg_picker", "name": "EGG picker", "steps": [("nav", "settings")],
     "target": {"visible": "settings/egg_choice"}, "conditional": "a mic with two inputs"},
    {"id": "mic_check", "name": "Mic check", "steps": [("click", "action_bar/mic_check_btn")],
     "target": {"text": "Mic check", "under": "mic_dialog"}},
    {"id": "progress", "name": "Progress", "steps": [("nav", "progress")], "target": {"visible": "progress"}},
    {"id": "session_time", "name": "Session time", "steps": [("key", "Space"), ("key", "Space")],
     "target": {"text": "Session", "under": "top/session"}},
    {"id": "rest_nudge", "name": "Rest nudge", "steps": [("key", "Space"), ("key", "Space"), ("wait", REST_NUDGE_MINUTES * 60 + 5)],
     "target": {"toast": "rest"}},
    {"id": "register_label", "name": "Register labelling",
     "steps": [("nav", "takes"), ("expand", "takes/full/sections.register")],
     "target": {"text": "Label register", "under": "takes/full"}},
    {"id": "shift", "name": "Hear it shifted", "steps": [("nav", "takes"), ("expand", "takes/full/sections.shift")],
     "target": {"visible": "takes/full/shift_view"}},
    {"id": "replay", "name": "Replay last 10 s", "steps": [], "target": {"visible": "action_bar/replay_btn"}},
    {"id": "welcome_again", "name": "Show the welcome again",
     "steps": [("nav", "settings"), ("text", "settings", "Show the welcome again")],
     "target": {"visible": "onboarding/next_btn"}},
    {"id": "theme", "name": "Theme and reduce motion", "steps": [("nav", "settings")],
     "target": {"visible": "settings/theme_choice"}},
    {"id": "screen_shortcuts", "name": "Ctrl+4 opens Takes", "steps": [("key", "Ctrl+4")],
     "target": {"visible": "takes/search"}},
]
BY_ID = {f["id"]: f for f in FEATURES}
UNCOUNTED = ("wait", "answer")      # steps that are not clicks


def clicks(feature):
    """Clicks (and key presses) from the dashboard."""
    return sum(1 for s in feature["steps"] if s[0] not in UNCOUNTED)
