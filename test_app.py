"""Tests for retrying, scheduling and the course list in app.py. Run: .venv/bin/python test_app.py

Never touches the network or the token file: the relevant functions are replaced with fakes.
"""
import os
import tempfile
import types
import urllib.error

import app
import learnhub as lh

NO_NET = urllib.error.URLError(OSError(8, "nodename nor servname provided, or not known"))


def test_check_user_offline_keeps_token():
    def offline(*args, **kwargs):
        raise NO_NET
    old = lh.get_token, lh.call
    lh.get_token, lh.call = (lambda: "t"), offline
    try:
        app.status.update(checked=False, user=None, has_token=False, error=None)
        assert app.check_user() is False  # auto_sync retries
        # The token is kept: the UI shows "offline" instead of the login box.
        assert app.status["has_token"] and not app.status["user"] and app.status["checked"]
        assert "Can't reach" in app.status["error"]
    finally:
        lh.get_token, lh.call = old


def test_auto_sync_retries_offline_and_counts_sleep():
    start = 1_000_000
    clock = [start]
    online = [False]
    events = []

    class Stop(Exception):
        pass

    def check_user():
        events.append(("check", clock[0] - start))
        app.status["user"] = "U" if online[0] else None
        return online[0]

    def sleep(seconds):
        clock[0] += seconds
        if clock[0] - start == 120:
            online[0] = True  # internet came back 2 minutes after start
        if clock[0] - start == 600:
            clock[0] += 4 * 3600  # lid closed: 4 hours asleep, which time.sleep does not count
        if clock[0] - start > 4 * 3600 + 900:
            raise Stop

    old = app.check_user, app.run_sync, app.time
    app.check_user = check_user
    app.run_sync = lambda: events.append(("sync", clock[0] - start))
    app.time = types.SimpleNamespace(time=lambda: clock[0], sleep=sleep)
    try:
        app.auto_sync()
    except Stop:
        pass
    finally:
        app.check_user, app.run_sync, app.time = old
    wake = 4 * 3600 + 600
    assert events == [("check", 0), ("check", 60), ("check", 120), ("sync", 120),
                      ("check", wake), ("sync", wake)], events


def test_dropped_tracked_course_still_listed():
    old = lh.call, lh.load_config
    lh.call = lambda fn, **kw: {"courses": [{"id": 1, "fullname": "A &amp; B", "shortname": "AB"}]}
    lh.load_config = lambda: {"courses": {"1": "AB", "7": "ETHC113"}}  # 7 was dropped
    try:
        got = app.active_courses()
    finally:
        lh.call, lh.load_config = old
    assert got == [{"id": 1, "name": "A & B", "short": "AB", "tracked": True},
                   {"id": 7, "name": "ETHC113", "short": "ETHC113", "tracked": True, "dropped": True}], got


def test_reveal_stays_inside_download_dir():
    opened = []
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "Course"))
        open(os.path.join(tmp, "Course", "a.pdf"), "w").close()
        old = lh.load_config, app.subprocess.run
        lh.load_config, app.subprocess.run = (lambda: {"download_dir": tmp, "courses": {}}), opened.append
        try:
            assert app.reveal("Course/a.pdf") and opened[-1][-1].endswith("Course/a.pdf")
            assert not app.reveal("../../etc/passwd") and not app.reveal("/etc/passwd")  # outside the folder
            assert not app.reveal("Course/missing.pdf") and len(opened) == 1
        finally:
            lh.load_config, app.subprocess.run = old


def test_sync_requested_during_sync_runs_once_more():
    runs = []

    def sync_once():
        runs.append(1)
        if len(runs) == 1:
            app.run_sync()  # a new request came from the UI while the first sync was running
        return True

    old = lh.sync_once
    lh.sync_once = sync_once
    try:
        app.run_sync()
    finally:
        lh.sync_once = old
    assert len(runs) == 2, runs
    assert not app.status["syncing"] and not app.sync_lock.locked()


def test_pick_folder_adds_courses():
    old = app.subprocess.run
    try:
        for out, code, want in [("/Users/a/Desktop/\n", 0, "/Users/a/Desktop/Courses"),
                                ("/Users/a/Courses/\n", 0, "/Users/a/Courses"),  # no Courses/Courses
                                ("", 1, None)]:  # Cancel
            app.subprocess.run = lambda *a, **kw: types.SimpleNamespace(stdout=out, returncode=code)
            assert app.pick_folder() == want, (out, app.pick_folder())
    finally:
        app.subprocess.run = old


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
