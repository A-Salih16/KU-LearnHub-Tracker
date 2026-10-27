"""Tests for the safety rules and the sync logic. Run: .venv/bin/python test_learnhub.py"""
import base64
import errno
import hashlib
import io
import os
import tempfile
import time
from unittest import mock

import learnhub

PF = "https://x/webservice/pluginfile.php/7/"


def contents(modname, names, revision=1):
    """Fake core_course_get_contents response: one section, one module, the given files."""
    return [{"name": "Week 1", "modules": [{"modname": modname, "name": "Readings", "contents": [
        {"type": "file", "filename": n, "filepath": "/", "timemodified": 5, "filesize": 3,
         "fileurl": f"{PF}mod_{modname}/content/{revision}/{n}?forcedownload=1"} for n in names]}]}]


def test_only_read_functions_allowed():
    for bad in ["mod_assign_save_submission", "mod_assign_submit_for_grading",
                "mod_resource_view_resource", "core_files_upload"]:
        try:
            learnhub.call(bad, token="x")
        except PermissionError:
            continue
        raise AssertionError(f"{bad} was not rejected")


def test_decode_launch():
    passport = "123"
    sig = hashlib.md5((learnhub.SITE + passport).encode()).hexdigest()
    loc = "moodlemobile://token=" + base64.b64encode(f"{sig}:::abc:::priv".encode()).decode()
    assert learnhub.decode_launch(loc, passport) == "abc"
    try:
        learnhub.decode_launch(loc, "999")
    except ValueError:
        pass
    else:
        raise AssertionError("a wrong signature was accepted")


def test_safe_name():
    assert learnhub.safe_name("../../etc") == "..-..-etc"
    assert learnhub.safe_name("..") == "_"
    assert learnhub.safe_name("A&amp;B / Notes") == "A&B - Notes"


def test_never_overwrites_and_stays_inside_root():
    with tempfile.TemporaryDirectory() as root:
        p = os.path.join(root, "Course", "notes.pdf")
        assert learnhub.write_new(p, [b"old"], root) == p
        p2 = learnhub.write_new(p, [b"new"], root)
        assert p2 == os.path.join(root, "Course", "notes (2).pdf")
        assert open(p, "rb").read() == b"old"  # the old file is unchanged

        def broken():
            yield b"half"
            raise IOError("connection dropped")
        try:
            learnhub.write_new(p, broken(), root)
        except IOError:
            pass
        assert sorted(os.listdir(os.path.join(root, "Course"))) == ["notes (2).pdf", "notes.pdf"]

        try:
            learnhub.write_new(os.path.join(root, "..", "outside.txt"), [b"x"], root)
        except ValueError:
            pass
        else:
            raise AssertionError("wrote outside the download folder")


def test_assignment_notifications():
    now = time.time()
    assigns = [
        {"id": 1, "name": "Old", "duedate": now - 86400},            # past due: silent
        {"id": 2, "name": "New", "duedate": now + 7 * 86400},        # "new" notification
        {"id": 3, "name": "Urgent", "duedate": now + 3600},          # new + due soon
        {"id": 4, "name": "Done", "duedate": now + 3600},            # new, already submitted
        {"id": 5, "name": "Exam", "duedate": 0, "nosubmissions": 1},  # grade item: skip
    ]
    fake = {"mod_assign_get_assignments": {"courses": [{"id": 9, "shortname": "X", "assignments": assigns}]}}
    sent = []
    old_call, old_notify = learnhub.call, learnhub.notify
    learnhub.call = lambda fn, **kw: fake.get(fn) or {
        "lastattempt": {"submission": {"status": "submitted" if kw["assignid"] == 4 else "new"}}}
    learnhub.notify = lambda title, msg: sent.append(title.split(":")[0] + ":" + msg.split(" ")[0])
    try:
        state = {"assignments": {}}
        learnhub.sync_assignments({"courses": {"9": "X"}}, state)
        assert sent == ["New assignment:New", "New assignment:Urgent", "Due soon:Urgent", "New assignment:Done"], sent
        assert {a['name'] for a in state['upcoming']} == {'Old', 'New', 'Urgent', 'Done'}
        assert state['assessments'][0]['name'] == 'Exam'
        # Undated submissions remain visible without a fabricated deadline.
        assigns.append({'id': 6, 'name': 'Undated', 'duedate': 0})
        sent.clear()
        learnhub.sync_assignments({"courses": {"9": "X"}}, state)
        assert sent == ['New assignment:Undated'], sent
        assert next(a for a in state['upcoming'] if a['name'] == 'Undated')['due'] == 0
        sent.clear()
        learnhub.sync_assignments({'courses': {'9': 'X'}}, state)
        assert sent == [], 'the same notification was sent again'
    finally:
        learnhub.call, learnhub.notify = old_call, old_notify


def test_ui_rejects_wrong_host_or_key():
    import app
    h = object.__new__(app.Handler)
    good_host = f"127.0.0.1:{app.PORT}"
    h.headers = {"Host": good_host}
    assert h.allowed(app.KEY)
    assert not h.allowed("wrong-key")
    assert not h.allowed(None)
    h.headers = {"Host": f"evil-site.com:{app.PORT}"}  # DNS rebinding
    assert not h.allowed(app.KEY)


def test_flatten():
    assert learnhub.flatten({"courseids": [5, 7], "a": 1}) == {"courseids[0]": 5, "courseids[1]": 7, "a": 1}


def test_truncated_download_is_rejected():
    class Response(io.BytesIO):  # fake urlopen response
        def __init__(self, body, headers):
            super().__init__(body)
            self.headers = headers

    def get(headers, filesize):
        with mock.patch.object(learnhub.urllib.request, "urlopen", lambda url, timeout: Response(b"12345", headers)):
            return learnhub.write_new(os.path.join(root, "a.pdf"), learnhub.download("https://x/a.pdf", filesize), root)

    with tempfile.TemporaryDirectory() as root, mock.patch.object(learnhub, "get_token", lambda: "t"):
        for headers, filesize in [({"Content-Length": "10"}, 0), ({}, 10)]:  # connection dropped at byte 5
            try:
                get(headers, filesize)
            except OSError:
                pass
            else:
                raise AssertionError("an incomplete download was counted as complete")
            assert os.listdir(root) == []  # the partial file was removed
        # Content-Length wins over filesize; with no known size (page index.html: filesize 0) there is no check.
        assert open(get({"Content-Length": "5"}, 99), "rb").read() == b"12345"
        assert open(get({}, 0), "rb").read() == b"12345"


def test_one_bad_file_does_not_stop_sync():
    def call(fn, **kw):
        if fn == "core_course_get_contents":
            return contents("resource", ["broken.pdf", "good.pdf"] if kw["courseid"] == 1 else ["other.pdf"])
        return {"courses": [{"id": 1, "shortname": "A", "assignments": [{"id": 5, "name": "Homework", "duedate": 0}]}]}

    # A 404 is only logged; no permission / disk full is raised after the round so the UI can show it.
    for error, surfaced in [(OSError("HTTP Error 404: Not Found"), False),
                            (PermissionError(errno.EPERM, "Operation not permitted"), True)]:
        def download(url, filesize=0):
            if "broken" in url:
                raise error
            yield b"abc"

        logs, sent = [], []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.multiple(
                learnhub, call=call, download=download, log=logs.append, notify=lambda t, m: sent.append(t),
                load_config=lambda: {"download_dir": tmp, "courses": {"1": "A", "2": "B"}},
                STATE=os.path.join(tmp, "state.json"), DATA_DIR=tmp):
            try:
                learnhub.sync_once()
                assert not surfaced, "the permission error did not reach the user"
            except PermissionError:
                assert surfaced
            assert os.listdir(os.path.join(tmp, "A", "Week 1")) == ["good.pdf"]
            assert os.listdir(os.path.join(tmp, "B", "Week 1")) == ["other.pdf"]  # the next course ran too
            assert len(learnhub.load_state()["files"]) == 2  # the broken one is not in the state: retried later
        assert any("broken.pdf" in m for m in logs), logs
        assert "New assignment: A" in sent, sent  # the assignment check ran too


def test_revision_change_does_not_redownload():
    k = learnhub.file_key
    assert k(PF + "mod_resource/content/3/a.pdf?forcedownload=1") == k(PF + "mod_resource/content/4/a.pdf")
    assert k(PF + "mod_folder/content/1/2024/b.pdf") == k(PF + "mod_folder/content/2/2024/b.pdf") \
        != k(PF + "mod_folder/content/2/2025/b.pdf")
    assert k(PF + "mod_page/content/8/c.png") == k(PF + "mod_page/content/9/c.png")
    for url in [PF + "mod_page/content/index.html", PF + "mod_book/chapter/11/index.html"]:
        assert k(url) == url  # no revision / a chapter number: left alone
    assert k(k(PF + "mod_folder/content/5/d.pdf")) == k(PF + "mod_folder/content/5/d.pdf")  # safe to apply twice

    # An old state (keys with a revision) is migrated; when a file is added to a folder (revision 1→2),
    # only the new one is downloaded.
    downloaded = []

    def download(url, filesize=0):
        downloaded.append(url.split("?")[0].rsplit("/", 1)[1])
        yield b"abc"

    old = {"files": {PF + "mod_folder/content/1/old.pdf": {"timemodified": 5, "path": "X/old.pdf"}}, "assignments": {}}
    with tempfile.TemporaryDirectory() as tmp, mock.patch.multiple(
            learnhub, call=lambda fn, **kw: contents("folder", ["old.pdf", "new.pdf"], revision=2),
            download=download, notify=lambda t, m: None, STATE=os.path.join(tmp, "state.json"), DATA_DIR=tmp):
        learnhub.save_state(old)
        os.makedirs(os.path.join(tmp, "X"))
        open(os.path.join(tmp, "X", "old.pdf"), "w").close()  # the old file is still on disk
        learnhub.sync_files({"download_dir": tmp, "courses": {"1": "X"}}, learnhub.load_state())
    assert downloaded == ["new.pdf"], downloaded


def test_deleted_file_is_downloaded_again():
    downloaded = []

    def download(url, filesize=0):
        downloaded.append(url.split("?")[0].rsplit("/", 1)[1])
        yield b"abc"

    with tempfile.TemporaryDirectory() as tmp, mock.patch.multiple(
            learnhub, call=lambda fn, **kw: contents("resource", ["a.pdf", "b.pdf"]),
            download=download, notify=lambda t, m: None, STATE=os.path.join(tmp, "state.json"), DATA_DIR=tmp):
        cfg = {"download_dir": tmp, "courses": {"1": "X"}}
        learnhub.sync_files(cfg, state := learnhub.load_state())
        folder = os.path.join(tmp, "X", "Week 1")
        os.remove(os.path.join(folder, "a.pdf"))  # the user deleted it
        downloaded.clear()
        learnhub.sync_files(cfg, state)
        assert downloaded == ["a.pdf"], downloaded  # only the missing one, under the same name
        assert sorted(os.listdir(folder)) == ["a.pdf", "b.pdf"]


def test_assignment_state_saved_even_if_check_fails():
    now = time.time()
    assigns = [{"id": 1, "name": "First", "duedate": now + 3600}, {"id": 2, "name": "Second", "duedate": now + 3600}]
    network = {"up": False}

    def call(fn, **kw):
        if fn == "core_course_get_contents":
            return []
        if fn == "mod_assign_get_assignments":
            return {"courses": [{"id": 9, "shortname": "X", "assignments": assigns}]}
        if not network["up"]:
            raise OSError("network dropped")
        return {"lastattempt": {"submission": {"status": "new"}}}

    sent = []
    with tempfile.TemporaryDirectory() as tmp, mock.patch.multiple(
            learnhub, call=call, notify=lambda t, m: sent.append(t.split(":")[0] + ":" + m.split(" ")[0]),
            load_config=lambda: {"download_dir": tmp, "courses": {"9": "X"}},
            STATE=os.path.join(tmp, "state.json"), DATA_DIR=tmp):
        try:
            learnhub.sync_once()  # "New assignment: First" was sent, then the network dropped while checking submissions
        except OSError:
            pass
        assert sent == ["New assignment:First"], sent
        network["up"] = True
        learnhub.sync_once()
    assert sent[1:] == ["Due soon:First", "New assignment:Second", "Due soon:Second"], sent


def test_download_goes_to_part_file_first():
    with tempfile.TemporaryDirectory() as root:
        d = os.path.join(root, "Course")
        p = os.path.join(d, "notes.pdf")

        def chunks():
            yield b"first"
            assert os.listdir(d) == ["notes.pdf.part"], os.listdir(d)  # the real name stays free while downloading
            yield b"last"
        assert learnhub.write_new(p, chunks(), root) == p
        assert open(p, "rb").read() == b"firstlast" and os.listdir(d) == ["notes.pdf"]

        # A half .part left by a crash does not take the real name, and it is not touched either.
        q = os.path.join(d, "slides.pdf")
        with open(q + ".part", "wb") as f:
            f.write(b"half")
        assert learnhub.write_new(q, [b"full"], root) == q
        assert open(q, "rb").read() == b"full" and open(q + ".part", "rb").read() == b"half"


def test_team_submission_counts():
    def submitted(sub, team):
        attempt = {"lastattempt": {"submission": sub, "teamsubmission": team}}
        with mock.patch.object(learnhub, "call", lambda fn, **kw: attempt):
            return learnhub.is_submitted(1)
    assert submitted({"status": "new"}, {"status": "submitted"})  # the group submitted
    assert submitted({"status": "submitted"}, None)
    assert not submitted({"status": "new"}, {"status": "draft"})
    assert not submitted(None, None)


def test_token_file_is_private():
    with tempfile.TemporaryDirectory() as tmp, mock.patch.multiple(
            learnhub, DATA_DIR=tmp, TOKEN=os.path.join(tmp, "token")):
        try:
            learnhub.get_token()
        except learnhub.TokenError:
            pass
        else:
            raise AssertionError("expected TokenError without a token")
        learnhub.save_token("abc")
        learnhub.save_token("def")  # logging in again replaces the old one
        assert learnhub.get_token() == "def"
        if os.name == 'nt':
            assert open(learnhub.TOKEN, encoding='utf-8').read().startswith('dpapi:')
            assert 'def' not in open(learnhub.TOKEN, encoding='utf-8').read()
        else:
            assert os.stat(learnhub.TOKEN).st_mode & 0o777 == 0o600
        assert os.listdir(tmp) == ["token"]  # no .tmp left behind


def test_safe_name_colon_and_length():
    s = learnhub.safe_name
    assert s("Week 1: Intro") == "Week 1- Intro" and s("a\tb") == "a-b"
    assert s("é" * 200 + ".pptx") == "é" * ((learnhub.MAX_NAME - 5) // 2) + ".pptx"  # never splits a character
    assert len(s("1. Week " + "x" * 300).encode()) <= learnhub.MAX_NAME


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
