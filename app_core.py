"""VoiceLab without a UI: takes, recording and import, re-analysis and the register model, targets,
the mic, the session, progress and the practice sessions. A UI calls these methods and draws what the
events say. No UI toolkit.

Events (Events.emit): "take_added"(take), "take_updated"(take), "take_removed"(uid),
"takes_loaded"(count, problems), "status"(text, level: "info"|"success"|"warning"|"error"|"busy"|"recording"),
"busy"(jobs), "profile_changed"(), "mode_changed"(mode), "mic_changed"(info), "recording"(bool),
"controls_changed"(controls), "progress_changed"(), "session_started"(), "folder_changed"(),
"voice_map_changed"(), plus the practice sessions' "exercise_changed", "vowels_changed" and "practice".
"""
import time
from datetime import datetime

import sounddevice as sd
import soundfile as sf

import analysis
import coach
import isolation
import profile_store
import register
import report
import take_view
import voice_map
from analysis import SAMPLE_RATE, score_exercise
from display import METRIC_DEFS, take_date
from egg import load_stereo_take
from engine import device_index, input_channels, input_devices
from events import Events
from exercise import ExerciseSession
from jobs import JobRunner
from live_session import LiveSession
from playback import Player
from profile_store import TARGET_KEYS
from progress import collect_entries
from speech import READING_PASSAGES, VowelCheckSession
from take_register import (analyze_recording, analyze_with_options, finish_take, fit_labelled, is_stale, label_rows,
                           register_model, score_register)
from take_store import (ANALYSIS_VERSION, IMPORT_MAX_SECONDS, TakeStore, default_mode, is_outdated, load_audio,
                        new_uid)

TAKES_PAGE = 20                 # takes shown at first; older ones on request
COMPARE_CHOICES = 15            # recent takes offered under "Compare with…"
CEILING_TAKES = 3               # usable takes the profile's formant ceiling is chosen from
REST_NUDGE_MINUTES = 25         # first rest reminder, then every so often. Own judgement: vocal-hygiene
#                                 advice commonly says to rest the voice regularly, but I could not confirm
#                                 a specific interval (unverified)
# What a take keeps through re-analysis: who it is, not what was measured.
KEPT_KEYS = ("uid", "time", "title", "dated", "file", "source", "register_label", "egg_channel", "task")
READY = "Ready to Record"
REST_TOOLTIP = ("Vocal-hygiene guidance generally suggests resting the voice regularly. "
                "The 25-minute interval is a rough guide (unverified).")
ISOLATE_QUESTION = ("Is there music behind the voice? Demucs can isolate the vocals first.\n"
                    "This is slower, and the first use downloads its model (~80 MB).")
EGG_QUESTION = ("This file has two channels. Is one of them an electroglottograph (EGG) "
                "signal, with the voice on the other?")
TAKES_FOLDER_QUESTION = ("Choose a folder to save takes in? Each take is saved there as a WAV file with its "
                         "analysis beside it, and loads again next time.\n\n"
                         "You can choose or change the folder any time with the folder button at the bottom.")
REBUILD_QUESTION = ("Replace the Voice Map with one built from your saved takes? What it has gathered live is "
                    "replaced, and imported voices are left out.")
DEFAULT_MIC = "System default mic"


# --- BACKGROUND JOBS (plain functions, run by the JobRunner) ---

def import_and_analyze(path, isolate, egg_channel=None, state=None, version=0, mode="speak", ceiling=None):
    """Analyse a file in `mode`. With egg_channel (0 or 1) that channel of a stereo file is the EGG
    and the other the mic; `state` and `version` are the profile's register model."""
    if egg_channel is None:
        audio, trimmed = load_audio(path)
        egg_samples = None
    else:
        audio, egg_samples = load_stereo_take(path, egg_channel)
        limit = IMPORT_MAX_SECONDS * SAMPLE_RATE
        trimmed = len(audio) > limit
        audio, egg_samples = audio[:limit], egg_samples[:limit]
    if isolate:
        audio = isolation.isolate_vocals(audio, SAMPLE_RATE)
    result = finish_take(analyze_with_options(audio, mode, ceiling), egg_samples, state, version)
    if "error" not in result:
        name = path.replace("\\", "/").split("/")[-1]
        notes = [name] + (["vocals isolated with Demucs"] if isolate else []) + \
                ([f"first {IMPORT_MAX_SECONDS} s"] if trimmed else [])
        result["source"] = " · ".join(notes)
    return result


def load_folder(store, progress=None):
    takes, problems = store.load_all(progress=progress)
    return {"takes": takes, "problems": problems}


def _take_audio(data, store):
    return store.audio(data) if data.get("file") and store else data["audio"]


def refit_register(takes, store, cache, progress=None):
    """Refit the register model to the labelled takes. `cache` holds each take's frame
    features (uid -> rows), so a label change doesn't re-measure every take."""
    labelled = []
    for done, data in enumerate(takes):
        if progress:
            progress(done, len(takes))
        if data["uid"] not in cache:
            cache[data["uid"]] = label_rows(_take_audio(data, store), data["tracks"])
        labelled.append((data["register_label"], cache[data["uid"]]))
    model, counts = fit_labelled(labelled)
    return {"job": "refit", "model": model, "counts": counts}


def reanalyze_take(data, store, state, version, ceiling=None):
    """Analyse a stored take again with the current analysis, in its own mode. Only the sidecar changes,
    never the audio file. Returns {"job": "reanalyze", "uid", "take"} or {..., "failed": message}."""
    try:
        audio = _take_audio(data, store)
        egg_samples = store.egg(data) if store and data.get("egg_channel") is not None else None
        mode = default_mode(data)
        result = finish_take(analyze_with_options(audio, mode, ceiling), egg_samples, state, version)
    except Exception as e:  # an unreadable file shouldn't stop the others
        return {"job": "reanalyze", "uid": data["uid"], "failed": str(e)}
    if "error" in result:
        return {"job": "reanalyze", "uid": data["uid"], "failed": result["error"]}
    result.pop("audio", None)
    result.pop("egg_samples", None)
    result.update({k: data[k] for k in KEPT_KEYS if k in data}, mode=mode)
    exercise = data.get("exercise")
    if exercise and exercise.get("targets"):    # score it again with the notes it was sung against
        latency = exercise.get("latency") or 0.0
        heard = [(start + latency, length, midi) for start, length, midi in exercise["targets"]]
        tracks = result["tracks"]
        result["exercise"] = score_exercise(tracks["times"], tracks["f0"], heard) | {
            "name": exercise["name"], "targets": exercise["targets"], "latency": latency}
    elif exercise:
        result["exercise"] = exercise
    return {"job": "reanalyze", "uid": data["uid"], "take": result}


def rebuild_voice_map(takes, store, progress=None):
    """A Voice Map built from the saved takes, each in its own mode. Imported voices are left out."""
    items = []
    for done, data in enumerate(takes):
        if progress:
            progress(done, len(takes))
        if data.get("file") and (not data.get("source") or "added to folder" in data["source"]):
            try:
                items.append({"tracks": data["tracks"], "audio": _take_audio(data, store), "mode": data.get("mode")})
            except Exception:  # a missing or unreadable file
                continue
    return {"job": "voicemap", "data": voice_map.rebuild(items), "count": len(items)}


def rescore_register(takes, store, state, version, progress=None):
    """Score takes with the register model; a take whose audio can't be read is reported as failed."""
    model, scored, failed = register_model(state), [], []
    for done, data in enumerate(takes):
        if progress:
            progress(done, len(takes))
        try:
            scores, fry, summary = score_register(_take_audio(data, store), data["tracks"], model)
        except Exception:  # a missing or unreadable file shouldn't stop the others
            failed.append(data["uid"])
            continue
        summary["model_version"] = version
        scored.append((data["uid"], scores, fry, summary))
    return {"job": "rescore", "scored": scored, "failed": failed}


class VoiceLabCore:
    """Everything the main window decides. Without `store`, takes are saved to the profile's takes folder
    (if it has one). Call reload() once the UI listens, to load the folder."""

    def __init__(self, profile, engine=None, store=None, events=None, runner=None, clock=time.monotonic):
        self.profile = profile
        self.engine = engine
        self.events = events or Events()
        self.runner = runner or JobRunner(self.events)
        self.clock = clock              # the session clock
        self.store = store if store is not None else (TakeStore(profile.takes_dir) if profile.takes_dir else None)
        self._takes = []                # every take, oldest first
        self._numbers = {}              # id(take) -> its number on screen ("Take #3")
        self.take_count = 0
        self.shown = 0                  # the newest takes shown; older ones on request (show_older)
        self.is_recording = False
        self.analyzing = False
        self.loading = False
        self.status = (READY, "info")
        self.reading = None             # index of the passage being read aloud, or None
        self.ceiling_candidates = []    # usable takes the profile's formant ceiling is chosen from
        self.session_started = None     # clock() when you first recorded; None until then
        self.rest_nudges = 0
        self.label_features = {}        # uid -> frame features of labelled takes
        self.refit_needed = False
        self.register_again = False     # something changed while a register job ran
        self.register_failed = set()    # uids that couldn't be scored, so they aren't retried
        self.reanalysis_failed = set()  # uids that couldn't be re-analysed, so they aren't retried this session
        self.reanalysis_total = 0       # takes queued in this round, for the status text
        self.reanalysis_done = 0
        self.jobs = {"analysis": None, "load": None, "register": None, "reanalysis": None, "voicemap": None}
        if profile.formant_ceiling and engine is not None:
            engine.analyzer.formant_ceiling = profile.formant_ceiling
        self.player = Player()
        self.live = LiveSession(profile, profile.mode, engine=engine)
        self.exercise = ExerciseSession(engine, self.events, runner=self.runner)
        self.exercise.ceiling = lambda: self.profile.formant_ceiling
        self.vowels = VowelCheckSession(engine, profile, self.events, runner=self.runner)
        self.voice_map = voice_map.MapSession(profile.voice_map_path)
        self.live.target_provider = self.exercise.current_target
        self.events.subscribe("practice", self._practice_changed)
        self.events.subscribe("exercise_scored", self.process_results)
        self.events.subscribe("vowel_check_done", self._vowel_check_done)
        self.set_mode(profile.mode)
        self.live.set_targets(profile.targets)

    @property
    def mode(self):
        return self.profile.mode

    def _status(self, text, level="info"):
        self.status = (text, level)
        self.events.emit("status", text, level)

    def _job_running(self, name):
        return self.jobs[name] is not None and self.runner.active(self.jobs[name])

    # --- TAKES ---

    def takes(self):
        """Every take, newest first."""
        return list(reversed(self._takes))

    def shown_takes(self):
        """The takes shown, newest first; older ones come with show_older()."""
        return self.takes()[:self.shown]

    def older_count(self):
        return len(self._takes) - self.shown

    def older_text(self):
        """The label of the button that shows older takes."""
        return f"Show older takes ({self.older_count()} more)"

    def show_older(self):
        """Show the next page of older takes; returns them, newest first."""
        page = self.takes()[self.shown:self.shown + TAKES_PAGE]
        self.shown += len(page)
        self.update_register()
        return page

    def number(self, take):
        """The take's number on screen ("Take #3")."""
        return self._numbers.get(id(take))

    def take(self, uid):
        """The take with this uid (or, given a take, that take); None if there is none."""
        if isinstance(uid, dict):
            return uid
        return next((t for t in self._takes if t.get("uid") == uid), None)

    def take_audio(self, uid):
        data = self.take(uid)
        return self.store.audio(data) if data.get("file") and self.store else data["audio"]

    def compare_options(self, uid):
        """[(label, take)] a take can be compared with: your baseline take first, then recent takes."""
        this = self.take(uid)
        others = [d for d in self._takes if d is not this and d.get("tracks")]
        others.sort(key=lambda d: d["time"], reverse=True)
        options, base_uid = [], (self.profile.baseline or {}).get("uid")
        for data in others:
            if data.get("uid") and data["uid"] == base_uid:
                options.append(("Baseline take", data))
        for data in others[:COMPARE_CHOICES]:
            options.append((data.get("title") or take_date(data) or "Take", data))
        return options

    def _add(self, data, number=None):
        if number is None:
            self.take_count += 1
            number = self.take_count
        self._numbers[id(data)] = number
        self._takes.append(data)
        self._takes.sort(key=lambda d: d["time"])
        self.shown += 1
        self.events.emit("take_added", data)

    def report(self, uid):
        """report.build_report for a take, compared with previous_take(); None for an unknown take."""
        data = self.take(uid)
        if data is None:
            return None
        return report.build_report(data, self.profile, self.previous_take(data))

    def previous_take(self, uid):
        """The latest earlier take made for the same task (profile_store.task_of), else in the same mode,
        else None: what a report compares with."""
        data = self.take(uid)
        earlier = [d for d in self._takes if d is not data and d["time"] < data["time"]]
        task = profile_store.task_of(data)
        same = [d for d in earlier if profile_store.task_of(d) == task] or \
            [d for d in earlier if default_mode(d) == default_mode(data)]
        return same[-1] if same else None

    # --- RECORDING AND IMPORT ---

    def controls(self):
        """{"record", "import", "recording", "reload", "folder"}: which controls work now, and the folder's
        name (None without a takes folder)."""
        busy = self.vowels.running or self.exercise.running or self.analyzing
        return {"record": not busy, "import": not busy and not self.is_recording, "recording": self.is_recording,
                "reload": not self.loading, "folder": self.store.folder.name if self.store else None}

    def _controls_changed(self):
        self.events.emit("controls_changed", self.controls())

    def _practice_changed(self, running):
        """A drill or exercise started or stopped: the record button is unavailable while one records."""
        if self.vowels.running or self.exercise.running:
            self.start_session()
        self._controls_changed()

    def start_recording(self):
        if self.is_recording:
            return
        self.is_recording = True
        self.start_session()
        self._status("Recording...", "recording")
        self.engine.start_take()
        self.events.emit("recording", True)
        self._controls_changed()

    def stop_recording(self):
        """Stop the take; it is analysed as a job and arrives as "take_added"."""
        if not self.is_recording:
            return
        self.is_recording = False
        self._status("Processing...", "busy")
        self.analyzing = True
        audio = self.engine.stop_take()
        self.events.emit("recording", False)
        self._controls_changed()
        self.jobs["analysis"] = self.runner.submit(
            "analysis", "Analysing your take", analyze_recording, audio, self.engine.last_take_egg,
            self.profile.register_model, self.profile.register_version, self.profile.mode,
            self.profile.formant_ceiling, on_done=self._analysis_done)

    def toggle_recording(self):
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def can_isolate(self):
        """True when Demucs is installed, so an import can have its vocals isolated."""
        return isolation.available()

    def file_channels(self, path):
        """Channels of an audio file (to ask whether one is an EGG), or None if it can't be read here."""
        try:
            return sf.info(path).channels
        except Exception:  # unreadable here; the analysis reports the problem
            return None

    def import_file(self, path, isolate=False, egg_channel=None):
        """Analyse an audio file as a take (a job); it arrives as "take_added"."""
        self.analyzing = True
        self._controls_changed()
        self._status("Isolating vocals and analyzing…" if isolate else "Analyzing file…", "busy")
        self.jobs["analysis"] = self.runner.submit(
            "analysis", "Analysing " + path.replace("\\", "/").split("/")[-1], import_and_analyze, path, isolate,
            egg_channel, self.profile.register_model, self.profile.register_version, self.profile.mode,
            self.profile.formant_ceiling, on_done=self._analysis_done)

    def _analysis_done(self, results):
        self.analyzing = False
        self._controls_changed()
        self.process_results(results)

    def process_results(self, results):
        """A new take (recorded, imported or an exercise): saved, logged and added."""
        if "error" in results:
            self._status(results["error"], "error")
            return
        # An imported file may be someone else's voice, so it never sets the formant ceiling.
        self.note_ceiling(results)
        results.update(uid=new_uid(), time=datetime.now().isoformat(timespec="seconds"))
        results.setdefault("mode", self.profile.mode)
        results.setdefault("analysis_version", ANALYSIS_VERSION)
        results.setdefault("task", self.current_task(results))
        message, level = "Done", "info"
        # With a takes folder, the folder is the record of every take; without
        # one, a summary goes to the history log so progress still builds up.
        if self.store:
            try:
                self.store.save(results)
                message = f"Saved {results['file']}"
            except OSError as e:
                message = f"Couldn't save the take: {e}"
                self.profile.log_take(results)
        else:
            self.profile.log_take(results)
        self._add(results)
        egg_info = results.get("egg")
        if egg_info and not egg_info["signal_ok"]:      # say why there's no contact quotient
            message, level = f"{message}. EGG: {egg_info['note']}", "warning"
        self._status(message, level)
        self.events.emit("progress_changed")
        self.update_register()          # exercise takes arrive without a register result

    def set_reading(self, index):
        """The passage being read aloud (an index into READING_PASSAGES), or None: takes made meanwhile
        are for that passage."""
        self.reading = index

    def current_task(self, results):
        """What this take is for, so progress compares like with like: the passage being read,
        else the exercise (profile_store.task_of), else free speech or singing."""
        mode = results.get("mode") or self.profile.mode
        if mode == "speak" and not results.get("exercise") and self.reading is not None:
            return f"passage: {READING_PASSAGES[self.reading % len(READING_PASSAGES)][0]}"
        return profile_store.task_of(results | {"mode": mode})

    # --- MODE, CEILING AND TARGETS ---

    def set_mode(self, mode):
        """Speak or sing: remembered, and told to the engine, the live session and the Voice Map."""
        self.profile.set_mode(mode)
        self.voice_map.mode = mode
        self.live.set_mode(mode)        # tells the engine too
        self.events.emit("mode_changed", mode)

    def note_ceiling(self, results):
        """Fix the profile's formant ceiling once, from the first few usable recordings of your own voice
        (analysis.choose_profile_ceiling), rather than following every take's own: that jumped by 150 Hz
        between takes 13 s apart (audit). Once set it is stored in the profile and passed to every
        analysis and to the live meters; only the vowel check, the best evidence, changes it."""
        if self.profile.formant_ceiling or results.get("source") or "tracks" not in results:
            return
        if coach.take_quality(results, results.get("noise"))["tier"] == "poor":
            return
        self.ceiling_candidates.append(results)
        if len(self.ceiling_candidates) >= CEILING_TAKES:
            self.set_ceiling(analysis.choose_profile_ceiling(self.ceiling_candidates))
            self.ceiling_candidates = []

    def set_ceiling(self, ceiling):
        if self.engine is not None:
            self.engine.analyzer.formant_ceiling = ceiling
        self.profile.set_formant_ceiling(ceiling)

    def _vowel_check_done(self, result):
        self.events.emit("progress_changed")
        self.use_vowel_ceiling(result)

    def use_vowel_ceiling(self, result):
        """The vowel check fits the formant ceiling jointly over eight vowels, the best evidence there is:
        it always replaces the profile's ceiling."""
        if result.get("formant_ceiling"):
            self.set_ceiling(result["formant_ceiling"])

    def set_baseline(self, uid):
        self.profile.set_baseline(self.take(uid))
        self.refresh_profile("Baseline saved")

    def set_target(self, uid):
        """Targets around this take, as wide as its own frames spread. A reading the recording can't
        support (coach.take_quality) never becomes a target: that target keeps its old value. Returns the message."""
        results = self.take(uid)
        quality = coach.take_quality(results, results.get("noise"))
        trusted = {key for key in TARGET_KEYS if quality["trust"].get(key, True)}
        if not trusted:
            message = (f"This take is too noisy to set targets from ({quality['reasons'][0]}). "
                       "Record a cleaner one.")
            self._status(message, "warning")
            return message
        self.profile.set_targets_from_take(results, trusted)
        left = [METRIC_DEFS[k][0] for k in TARGET_KEYS if k not in trusted and results.get(k) is not None]
        message = "Targets set from take. Fine-tune them under Targets."
        if left:
            message += f" Not set from this take (it can't support them): {', '.join(left)}."
        self.refresh_profile(message)
        return message

    def set_targets(self, targets):
        """Use these targets ({key: [low, high]}) live; the profile is not saved."""
        self.live.set_targets(targets)
        self.events.emit("profile_changed")

    def refresh_profile(self, message=None):
        """The profile changed (targets, baseline, model): everything that shows it re-reads it."""
        self.live.set_targets(self.profile.targets)
        self.vowels.refresh_profile()
        self.events.emit("profile_changed")
        self.events.emit("progress_changed")
        if message:
            self._status(message, "success")

    # --- SESSION ---

    def start_session(self):
        if self.session_started is None:
            self.session_started = self.clock()
            self.events.emit("session_started")

    def session_minutes(self):
        return 0.0 if self.session_started is None else (self.clock() - self.session_started) / 60

    def session_text(self):
        return f"Session {self.session_minutes():.0f} min"

    def rest_nudge(self):
        """A gentle rest reminder once every REST_NUDGE_MINUTES of the session, or None when none is due."""
        minutes = self.session_minutes()
        if minutes >= REST_NUDGE_MINUTES * (self.rest_nudges + 1):
            self.rest_nudges += 1
            return (f"You have been at this for {minutes:.0f} minutes. A short rest and a sip "
                    "of water can help; ease off if your voice feels tired or tight.")
        return None

    # --- TAKES FOLDER ---

    def folder_label(self):
        """(text, tooltip) of the takes folder button."""
        if self.store:
            return f" {self.store.folder.name}", f"Takes are saved in {self.store.folder}. Click to change."
        return (" Choose takes folder…", "Pick a folder to save takes in and load them from, "
                "so they're kept between sessions.")

    def should_ask_takes_folder(self):
        """True on the first launch without a takes folder (offer one, once)."""
        return not self.store and not self.profile.takes_dir_asked

    def decline_takes_folder(self):
        self.profile.set_takes_dir(None)

    def set_takes_folder(self, folder):
        """Use `folder` for takes. This session's unsaved takes are saved into it."""
        self.profile.set_takes_dir(folder)
        self.store = TakeStore(folder)
        unsaved = [d for d in self.shown_takes() if not d.get("file")]
        for data in sorted(unsaved, key=lambda d: d["time"]):
            try:
                self.store.save(data)
            except OSError as e:
                self._status(f"Couldn't save a take: {e}", "error")
        # They're in the folder now, so the history log shouldn't count them again.
        self.profile.forget_takes(d["uid"] for d in unsaved if d.get("file"))
        self.events.emit("folder_changed")
        self._controls_changed()
        self.reload()

    def reload(self):
        """(Re)load every take in the folder, in the background."""
        if not self.store or self._job_running("load"):
            return
        self._status(f"Loading takes from {self.store.folder}…", "busy")
        self.loading = True
        self._controls_changed()
        self.jobs["load"] = self.runner.submit("load", "Loading your takes", load_folder, self.store,
                                               on_done=self._takes_loaded)

    def _takes_loaded(self, result):
        self.loading = False
        self._controls_changed()
        if "error" in result:
            self._status(result["error"], "error")
            return
        # Keep takes that aren't in the folder, and any saved while it loaded;
        # takes whose files were deleted outside VoiceLab go.
        loaded = {d["uid"] for d in result["takes"]}
        kept = [d for d in self.shown_takes() if d.get("uid") not in loaded and
                (not d.get("file") or self.store.path(d).exists())]
        takes = sorted(result["takes"] + kept, key=lambda d: d["time"])
        self._takes = takes
        self._numbers = {id(d): n for n, d in enumerate(takes, start=1)}
        self.take_count = len(takes)
        self.shown = min(TAKES_PAGE, len(takes))
        count, problems = len(result["takes"]), result["problems"]
        self.events.emit("takes_loaded", count, problems)
        message = f"Loaded {count} take{'s' if count != 1 else ''} from {self.store.folder.name}"
        if problems:
            message += f" · {len(problems)} file(s) couldn't be read"
        self._status(message, "warning" if problems else "info")
        self.events.emit("progress_changed")
        self.update_register()
        self.update_reanalysis()

    def rename(self, uid, title):
        """Retitle a take (renaming its files). Returns True when it worked; "take_updated" either way."""
        data = self.take(uid)
        ok = True
        if data.get("file") and self.store:
            try:
                self.store.rename(data, title)
            except OSError as e:
                self._status(f"Couldn't rename: {e}", "error")
                ok = False
            else:
                self._status(f"Renamed to {data['file']}")
        else:
            data["title"] = title
        self.events.emit("take_updated", data)
        return ok

    def delete_question(self, uid):
        """The question to ask before deleting a take."""
        data = self.take(uid)
        return take_view.delete_question(data, self.number(data))[0]

    def delete(self, uid, confirm=None):
        """Delete a take and its files. `confirm(question)` is asked first when given. True if deleted."""
        data = self.take(uid)
        text, name = take_view.delete_question(data, self.number(data))
        if confirm is not None and not confirm(text):
            return False
        if data.get("file") and self.store:
            try:
                self.store.delete(data)
            except OSError as e:
                self._status(f"Couldn't delete: {e}", "error")
                return False
        else:
            self.profile.forget_takes([data.get("uid")])
        if any(d is data for d in self.shown_takes()):
            self.shown -= 1
        self._takes = [d for d in self._takes if d is not data]
        self.events.emit("take_removed", data.get("uid"))
        self._status(f"Deleted {name}")
        self.events.emit("progress_changed")
        return True

    # --- REGISTER ---

    def label_register(self, uid, label):
        """Label a take chest or head (None clears it); labelled takes calibrate the model."""
        data = self.take(uid)
        if label:
            data["register_label"] = label
        else:
            data.pop("register_label", None)
        if data.get("file") and self.store:
            try:
                self.store.write_sidecar(data)
            except OSError as e:
                self._status(f"Couldn't save the label: {e}", "error")
        self.events.emit("take_updated", data)
        self._status(f"Labelled as {label}" if label else "Label cleared")
        self.refit_needed = True
        self.update_register()

    def update_register(self):
        """Run the next background register job: refit after a label change, then score the
        shown takes that lack a result from the current model. One job at a time."""
        if self._job_running("register"):
            self.register_again = True
            return
        if self.reanalysis_active():     # its results carry fresh register scores; update_reanalysis calls back
            return
        self.register_again = False
        if self.refit_needed:
            self.refit_needed = False
            labelled = [d for d in self._takes if d.get("register_label") and d.get("uid")]
            if labelled:
                self.jobs["register"] = self.runner.submit(
                    "register", "Calibrating the register to your labels", refit_register, labelled, self.store,
                    self.label_features, on_done=self._register_job_done)
                return
        version = self.profile.register_version
        stale = [d for d in self.shown_takes()[::-1] if d.get("uid") not in self.register_failed
                 and is_stale(d, version)]
        if stale:
            self.jobs["register"] = self.runner.submit(
                "register", f"Estimating the register of {len(stale)} take{'s' if len(stale) != 1 else ''}",
                rescore_register, stale, self.store, self.profile.register_model, version,
                on_done=self._register_job_done)

    def _register_job_done(self, result):
        if "error" in result:
            self._status(result["error"], "error")
            return
        if result["job"] == "refit":
            self._apply_refit(result)
        else:
            self._apply_scores(result)
        self.update_register()

    def _apply_refit(self, result):
        counts = ", ".join(f"{n} {name}" for name, n in result["counts"].items())
        if result["model"] is None:
            need = register.MIN_CALIBRATION_FRAMES
            self._status(f"Register labels so far: {counts} frames. Calibration needs {need} of each "
                         "class (label a few more takes, or longer ones).")
            return
        first = not register_model(self.profile.register_model).is_calibrated()
        self.profile.set_register_model(result["model"])
        self.live.refresh_profile()
        self.events.emit("profile_changed")
        self._status("Register calibrated to your voice (" + counts + " labelled frames)" if first else
                     "Register model updated from your labels (" + counts + " frames)", "success")

    def _apply_scores(self, result):
        by_uid = {d.get("uid"): d for d in self.shown_takes()}
        self.register_failed.update(result["failed"])
        for uid, scores, fry, summary in result["scored"]:
            data = by_uid.get(uid)
            if data is None:
                continue
            data["tracks"]["register"], data["tracks"]["fry"] = scores, fry.astype(float)
            data["register"] = summary
            if data.get("file") and self.store:
                try:
                    self.store.write_sidecar(data)
                except OSError:
                    pass
            self.events.emit("take_updated", data)
        self.events.emit("progress_changed")

    # --- RE-ANALYSIS ---

    def outdated_takes(self):
        """Saved takes made by an older analysis, newest first. (Unsaved ones were just made.)"""
        stale = [d for d in self._takes if d.get("file") and d.get("uid") not in self.reanalysis_failed
                 and is_outdated(d)]
        return sorted(stale, key=lambda d: d["time"], reverse=True)

    def reanalysis_active(self):
        return self._job_running("reanalysis") or bool(self.outdated_takes() and self.store)

    def update_reanalysis(self):
        """Re-analyse the next take made by an older analysis, in the background, one at a time. The WAV is
        never touched: only the sidecar is rewritten. The register jobs wait until this is done."""
        if not self.store or self._job_running("reanalysis"):
            return
        stale = self.outdated_takes()
        if not stale:
            if self.reanalysis_total:
                self._status(f"Re-analysed {self.reanalysis_done} of your takes with the improved analysis. "
                             "Use “Rebuild from takes” on the Voice Map tab to clear old noise readings from it.",
                             "success")
                self.reanalysis_total = self.reanalysis_done = 0
            self.update_register()
            return
        self.reanalysis_total = max(self.reanalysis_total, self.reanalysis_done + len(stale))
        text = (f"Re-analysing your takes with the improved analysis: {self.reanalysis_done + 1} "
                f"of {self.reanalysis_total}…")
        self._status(text, "busy")
        self.jobs["reanalysis"] = self.runner.submit(
            "reanalysis", text, reanalyze_take, stale[0], self.store, self.profile.register_model,
            self.profile.register_version, self.profile.formant_ceiling, on_done=self._reanalysis_done)

    def _reanalysis_done(self, result):
        uid = result.get("uid")
        if "error" in result or result.get("failed"):
            self.reanalysis_failed.add(uid)
            self._status(result.get("failed") or result["error"], "warning")
        else:
            self._apply_reanalysis(result)
            self.reanalysis_done += 1
        self.update_reanalysis()

    def _apply_reanalysis(self, result):
        """Swap a take's analysis for the new one in place and save the sidecar ("take_updated")."""
        data = next((d for d in self._takes if d.get("uid") == result["uid"]), None)
        if data is None:               # deleted while it ran
            return
        audio = data.get("audio")
        data.clear()
        data.update(result["take"])
        if audio is not None:
            data["audio"] = audio
        try:
            self.store.write_sidecar(data)
        except OSError as e:
            self._status(f"Couldn't save the new analysis: {e}", "error")
        self.events.emit("take_updated", data)
        self.events.emit("progress_changed")

    # --- VOICE MAP ---

    def rebuild_voice_map(self, confirm=None):
        """Replace the Voice Map with one built from the saved takes, which holds none of the noise the old
        live frames did. `confirm(question)` is asked first when given. True if the rebuild started."""
        if not self.store:
            self._status("Choose a takes folder first: the Voice Map is rebuilt from saved takes.", "warning")
            return False
        if self.reanalysis_active() or self._job_running("voicemap"):
            self._status("Wait until your takes are re-analysed, then rebuild the Voice Map.", "warning")
            return False
        if confirm is not None and not confirm(REBUILD_QUESTION):
            return False
        self._status("Rebuilding the Voice Map…", "busy")
        self.jobs["voicemap"] = self.runner.submit("voicemap", "Rebuilding the Voice Map", rebuild_voice_map,
                                                   list(self._takes), self.store, on_done=self._voice_map_rebuilt)
        return True

    def _voice_map_rebuilt(self, result):
        if "error" in result:
            self._status(result["error"], "error")
            return
        self.voice_map.use(result["data"])
        self.events.emit("voice_map_changed")
        self._status(f"Voice Map rebuilt from {result['count']} take{'s' if result['count'] != 1 else ''}",
                     "success")

    def clear_voice_map(self):
        self.voice_map.clear()
        self.events.emit("voice_map_changed")

    # --- MIC ---

    def input_devices(self):
        """[(label, name)] of the mics to choose from; name None is the system default."""
        return [(DEFAULT_MIC, None)] + [(name, name) for _, name in input_devices()]

    def egg_possible(self, name):
        """True if the mic called `name` (None: the default) has two or more inputs, so one can be an EGG."""
        return input_channels(device_index(name)) >= 2

    def stream_options(self):
        """Keyword arguments for engine.open_stream and engine.start: the noise floor from last time."""
        return {"noise_floor": self.profile.noise_floor}

    def start_options(self):
        """Keyword arguments for engine.start (as stream_options)."""
        return self.stream_options()

    def open_mic(self, name, egg_channel=None):
        """Listen on the mic called `name` (None: the system default), with an EGG on input channel
        `egg_channel` (0 or 1) if given. A two-channel stream that won't open falls back to the mic alone.
        Returns the status message; "mic_changed"(info) says what is open."""
        try:
            if egg_channel is None:
                self.engine.open_stream(device_index(name), **self.stream_options())
            else:
                self.engine.open_stream(device_index(name), egg_channel, **self.stream_options())
        except (sd.PortAudioError, ValueError) as e:
            if egg_channel is None:
                message = f"Couldn't open {name or 'the default mic'}: {e}"
                self._status(message, "error")
                return message
            return self._revert_egg(name, f"Couldn't open the EGG on channel {egg_channel + 1} of "
                                          f"{name or 'the default mic'}: {e}")
        self.profile.set_input_device(name)
        self.profile.set_egg_channel(egg_channel)
        self.live.set_egg(egg_channel is not None)
        message = f"Listening on {name or 'the system default mic'}" + \
                  ("" if egg_channel is None else f", EGG on channel {egg_channel + 1}")
        self._status(message)
        self.events.emit("mic_changed", self._mic_info(name, egg_channel))
        self._controls_changed()
        return message

    def _revert_egg(self, name, message):
        """The two-channel stream wouldn't open: say why and listen to the mic alone again."""
        self.profile.set_egg_channel(None)
        self.live.set_egg(False)
        try:
            self.engine.open_stream(device_index(name), **self.stream_options())
        except sd.PortAudioError as e:
            message += f" (and the mic alone won't open either: {e})"
        else:
            self.profile.set_input_device(name)
            message += ". Listening without the EGG."
        self._status(message, "error")
        self.events.emit("mic_changed", self._mic_info(name, None))
        return message

    def _mic_info(self, name, egg_channel):
        return {"device": name, "egg_channel": egg_channel,
                "capture": dict(self.engine.capture_info or {})}

    def should_run_first_mic_check(self):
        """True once, the first time VoiceLab opens a mic."""
        if self.profile.mic_check is None and not self.profile.mic_check_asked:
            self.profile.set_mic_check_asked()
            return True
        return False

    def save_mic_check(self, result):
        """The mic check's result is kept in the profile, so the next launch needn't ask."""
        self.profile.set_mic_check(result)
        self._status("Mic check saved")

    # --- PROGRESS AND FRAMES ---

    def progress_entries(self):
        folder_takes = [d for d in self._takes if d.get("file")]
        return collect_entries(self.profile.read_history(), folder_takes)

    def on_frame(self, frame, window=None):
        """One live frame for everything that follows the voice: {"live": the live snapshot, "exercise": the
        exercise's frame state or None, "vowels": the vowel check's state or None, "map": True when the
        Voice Map gained a frame}. Call it on the thread the UI runs on."""
        live = self.live.update(frame, window)
        vowels = self.vowels.on_frame(frame)
        exercise = self.exercise.on_frame(frame)
        return {"live": live, "vowels": vowels, "exercise": exercise, "map": self.voice_map.on_frame(frame)}

    def close(self):
        """Stop listening and keep what should last: the Voice Map and the noise floor. Waits for the jobs."""
        self.voice_map.save()
        if self.engine is not None:
            self.engine.stop()
            floor = self.engine.noise_floor()
            if floor is not None:       # the floor learned now starts the next session
                self.profile.set_noise_floor(floor)
        self.runner.wait_all()
