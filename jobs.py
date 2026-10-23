"""Background work with visible progress. No UI: results come back through Events' dispatch."""
import inspect
import threading
import time

PUBLIC_KEYS = ("id", "kind", "label", "done", "total", "text", "started")


def accepts(fn, name):
    """True if the callable takes a parameter called `name`."""
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


class JobRunner:
    """Runs fn(*args) on worker threads. Emits "busy"(jobs) whenever the job list changes; a job's
    on_done(result) runs through the events' dispatch. A job that raises gives {"error": "Error: ..."}."""

    def __init__(self, events, clock=time.monotonic):
        self.events = events
        self.clock = clock
        self._lock = threading.Lock()
        self._jobs = []             # queued and running jobs, in submission order
        self._threads = []
        self._next_id = 1

    def submit(self, kind, label, fn, *args, on_done=None, single=False):
        """Start fn(*args) in the background; returns the job id. With single=True only one job of this
        kind runs at a time: a newer submit waits, replacing a queued (not running) one. fn may take a
        `progress` keyword: progress(done, total, text=None) updates the job."""
        with self._lock:
            job = {"id": self._next_id, "kind": kind, "label": label, "done": 0, "total": None, "text": None,
                   "started": None, "fn": fn, "args": args, "on_done": on_done, "single": single}
            self._next_id += 1
            wait = False
            if single:
                same = [j for j in self._jobs if j["kind"] == kind and j["single"]]
                wait = any(j["started"] is not None for j in same)
                if wait:
                    self._jobs = [j for j in self._jobs if not (j in same and j["started"] is None)]
            self._jobs.append(job)
            if not wait:
                job["started"] = self.clock()
        if not wait:
            self._start(job)
        self._busy()
        return job["id"]

    def jobs(self):
        """[{"id", "kind", "label", "done", "total", "text", "started"}] of queued and running jobs;
        started is None while queued."""
        with self._lock:
            return [{key: job[key] for key in PUBLIC_KEYS} for job in self._jobs]

    def active(self, job_id):
        """True while the job is queued or running."""
        with self._lock:
            return any(job["id"] == job_id for job in self._jobs)

    def running(self, kind):
        """True while a job of this kind is queued or running."""
        with self._lock:
            return any(job["kind"] == kind for job in self._jobs)

    def wait_all(self, timeout=None):
        """Wait until no job is queued or running (including jobs their on_done started, when on_done
        runs on the worker thread). True if that happened within `timeout` seconds."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                self._threads = [t for t in self._threads if t.is_alive()]
                threads, pending = list(self._threads), bool(self._jobs)
            if not threads and not pending:
                return True
            left = None if end is None else end - time.monotonic()
            if left is not None and left <= 0:
                return False
            if threads:
                threads[0].join(left)
            else:                           # a queued job between threads
                time.sleep(0.005)

    def _start(self, job):
        thread = threading.Thread(target=self._run, args=(job,), daemon=True, name=f"job-{job['kind']}")
        with self._lock:
            self._threads.append(thread)
        thread.start()

    def _progress(self, job):
        def progress(done, total, text=None):
            with self._lock:
                job.update(done=done, total=total, text=text)
            self._busy()
        return progress

    def _run(self, job):
        fn, kwargs = job["fn"], {}
        if accepts(fn, "progress"):
            kwargs["progress"] = self._progress(job)
        try:
            result = fn(*job["args"], **kwargs)
        except Exception as e:  # reported to on_done, like any other result
            result = {"error": f"Error: {e}"}
        following = None
        with self._lock:
            self._jobs.remove(job)
            if job["single"]:
                following = next((j for j in self._jobs if j["kind"] == job["kind"] and j["single"]), None)
                if following is not None:
                    following["started"] = self.clock()
        if following is not None:
            self._start(following)
        self._busy()
        if job["on_done"] is not None:
            self.events.call(job["on_done"], result)

    def _busy(self):
        self.events.emit("busy", self.jobs())
