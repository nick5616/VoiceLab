"""Events and the JobRunner, without a UI."""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from events import Events  # noqa: E402
from jobs import JobRunner  # noqa: E402


def test_events_subscribe_emit_and_unsubscribe():
    events, heard = Events(), []
    unsubscribe = events.subscribe("status", lambda text, level: heard.append((text, level)))
    events.emit("status", "Saved", "info")
    events.emit("other", 1)
    unsubscribe()
    events.emit("status", "again", "info")
    assert heard == [("Saved", "info")]


def test_dispatch_decides_where_handlers_run():
    queued = []
    events = Events(dispatch=lambda fn, *args: queued.append((fn, args)))
    heard = []
    events.subscribe("x", heard.append)
    events.emit("x", 5)
    assert heard == [] and len(queued) == 1
    fn, args = queued.pop()
    fn(*args)
    assert heard == [5]


def test_busy_fires_with_a_jobs_progress():
    events = Events()
    seen, release = [], threading.Event()
    events.subscribe("busy", lambda jobs: seen.append([dict(j) for j in jobs]))

    def work(n, progress):
        for i in range(n):
            progress(i + 1, n, f"step {i + 1}")
        release.wait(5)
        return {"n": n}
    results = []
    runner = JobRunner(events)
    job = runner.submit("demo", "Counting", work, 3, on_done=results.append)
    assert runner.active(job) and runner.running("demo")
    release.set()
    assert runner.wait_all(10)
    assert results == [{"n": 3}] and not runner.active(job) and runner.jobs() == []
    progress = [j[0] for j in seen if j and j[0]["done"]]
    dones = [p["done"] for p in progress]
    last = max(progress, key=lambda p: p["done"])
    assert sorted(set(dones)) == [1, 2, 3] and last["total"] == 3
    assert last["text"] == "step 3" and last["label"] == "Counting"
    assert set(last) == {"id", "kind", "label", "done", "total", "text", "started"}
    assert seen[-1] == []                                   # the list empties when the job ends


def test_a_failing_job_reports_an_error():
    results = []
    runner = JobRunner(Events())
    runner.submit("bad", "Failing", lambda: 1 / 0, on_done=results.append)
    runner.wait_all(10)
    assert "error" in results[0] and "division" in results[0]["error"]


def test_single_jobs_queue_and_a_newer_one_replaces_the_queued():
    gate, order = threading.Event(), []

    def work(name):
        if name == "first":
            gate.wait(5)
        order.append(name)
        return name
    runner = JobRunner(Events())
    runner.submit("render", "first", work, "first", single=True)
    runner.submit("render", "second", work, "second", single=True)
    runner.submit("render", "third", work, "third", single=True)
    jobs = runner.jobs()
    assert [j["label"] for j in jobs] == ["first", "third"] and jobs[1]["started"] is None
    gate.set()
    runner.wait_all(10)
    assert order == ["first", "third"]


def test_on_done_can_chain_jobs_and_wait_all_waits_for_them():
    results = []
    runner = JobRunner(Events())

    def chain(result):
        results.append(result)
        if result < 3:
            runner.submit("step", "Next", lambda: result + 1, on_done=chain)
    runner.submit("step", "First", lambda: 1, on_done=chain)
    assert runner.wait_all(10)
    assert results == [1, 2, 3]
