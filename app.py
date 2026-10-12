"""LearnHub UI: local server (127.0.0.1 only) + a page in the browser + automatic sync while it is open.

Run: .venv/bin/python app.py   (to stop: "Quit" on the page, or Ctrl+C in the terminal)
"""
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

import learnhub as lh

PORT = 47653
SYNC_EVERY = 3 * 3600
UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")
KEY = secrets.token_urlsafe(24)  # the UI's secret key; new on every start

# has_token: if there is a token but LearnHub can't be reached, the UI shows "offline" instead of the login box.
status = {"checked": False, "user": None, "has_token": False, "syncing": False, "logging_in": False,
          "last_sync": None, "next_sync": None, "error": None}
sync_lock = threading.Lock()
sync_wanted = threading.Event()  # a request that arrives during a sync is not lost
login_lock = threading.Lock()


def ui_url():
    return f"http://127.0.0.1:{PORT}/?k={KEY}"


def check_user():
    """Tries the token on LearnHub. Returns False if LearnHub can't be reached: the token is kept and retried later."""
    try:
        lh.get_token()
        status["has_token"] = True
        status["user"] = lh.call("core_webservice_get_site_info")["fullname"]
        status["error"] = None
    except lh.TokenError:
        status["user"] = status["has_token"] = status["error"] = None
    except Exception as e:  # no internet, LearnHub under maintenance, etc.
        status["error"] = f"Can't reach LearnHub ({type(e).__name__})."
        return False
    finally:
        status["checked"] = True
    return True


def run_sync():
    sync_wanted.set()
    # If the lock is taken the request is not lost: the running sync sees sync_wanted and does one more round.
    while sync_wanted.is_set() and sync_lock.acquire(blocking=False):
        sync_wanted.clear()
        status["syncing"] = True
        try:
            if lh.sync_once():
                status["last_sync"] = time.time()
            status["error"] = None
        except lh.TokenError:
            status["user"] = status["has_token"] = None
            status["error"] = "Your session has expired; log in again."
            lh.notify("LearnHub", "Your session has expired. Log in again from the UI.")
        except Exception as e:  # network drop, etc.: retried on the next round
            status["error"] = f"{type(e).__name__}: {e}"
            lh.log(f"[error] {status['error']}")
        finally:
            status["syncing"] = False
            sync_lock.release()


def start(fn):
    threading.Thread(target=fn, daemon=True).start()


def auto_sync():
    last = 0
    while True:
        # time.time() is wall-clock time: it counts the time the Mac was asleep, which time.sleep's wait does not.
        # If LearnHub can't be reached, last stays the same and it is retried a minute later.
        if time.time() - last >= SYNC_EVERY and check_user():
            last = time.time()
            status["next_sync"] = last + SYNC_EVERY
            if status["user"]:
                run_sync()
        time.sleep(60)


def run_login():
    if not login_lock.acquire(blocking=False):
        return  # the login window is already open
    status["logging_in"] = True
    try:
        status["user"] = lh.login()
        status["has_token"] = True
        status["error"] = None
        start(run_sync)
    except Exception as e:
        status["error"] = f"Login failed: {e}"
    finally:
        status["logging_in"] = False
        login_lock.release()


def pick_folder():
    """Files go into a "Courses" folder inside the chosen one (not created again if Courses itself is chosen)."""
    r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt '
                        '"Where should LearnHub save files? A Courses folder will be created inside it.")'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    chosen = r.stdout.strip().rstrip("/") or "/"
    return chosen if os.path.basename(chosen) == "Courses" else os.path.join(chosen, "Courses")


def active_courses():
    tracked = lh.load_config()["courses"]
    courses = lh.call("core_course_get_enrolled_courses_by_timeline_classification",
                      classification="inprogress")["courses"]
    ids = {str(c["id"]) for c in courses}
    # A tracked course LearnHub no longer returns (dropped): keep it in the list so it can be unchecked.
    return [{"id": c["id"], "name": lh.html.unescape(c["fullname"]),
             "short": lh.html.unescape(c["shortname"]), "tracked": str(c["id"]) in tracked}
            for c in courses] + [{"id": int(cid), "name": short, "short": short, "tracked": True, "dropped": True}
                                 for cid, short in tracked.items() if cid not in ids]


def api_status():
    cfg, state = lh.load_config(), lh.load_state()
    # Recently downloaded files; old entries have no "at" (download time), so the upload time is used instead.
    files = sorted(state["files"].values(), key=lambda v: v.get("at", v["timemodified"]), reverse=True)[:8]
    return {**status, "download_dir": os.path.expanduser(cfg["download_dir"]), "tracked": cfg["courses"],
            "upcoming": state.get("upcoming", []), "recent": list(lh.RECENT)[:30],
            "files": [{"path": v["path"], "at": v.get("at", v["timemodified"])} for v in files]}


def reveal(rel):
    """Shows a file from the download folder in Finder. A path that leaves the folder is rejected."""
    root = os.path.realpath(os.path.expanduser(lh.load_config()["download_dir"]))
    path = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([root, path]) != root or not os.path.exists(path):
        return False
    subprocess.run(["open", "-R", path])
    return True


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def allowed(self, key):
        # Host check: against DNS rebinding (another site's name pointed at 127.0.0.1).
        # Key check: open websites must not be able to send requests to the UI.
        host_ok = self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
        return host_ok and secrets.compare_digest(key or "", KEY)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            if not self.allowed(urllib.parse.parse_qs(query).get("k", [""])[0]):
                return self.send(403, b"Open this page from the address printed in the terminal.", "text/plain")
            with open(UI, "rb") as f:
                return self.send(200, f.read(), "text/html")
        if not self.allowed(self.headers.get("X-Key")):
            return self.send(403, {"error": "unauthorized"})
        try:
            if path == "/api/status":
                return self.send(200, api_status())
            if path == "/api/courses":
                return self.send(200, active_courses())
        except lh.TokenError:
            status["user"] = status["has_token"] = None
            return self.send(401, {"error": "Not logged in; log in first."})
        except Exception as e:
            return self.send(500, {"error": f"{type(e).__name__}: {e}"})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.allowed(self.headers.get("X-Key")):
            return self.send(403, {"error": "unauthorized"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self.send(400, {"error": "invalid request"})
        path = self.path
        if path == "/api/login":
            start(run_login)
        elif path == "/api/courses":
            first = not os.path.exists(lh.CONFIG)
            cfg = lh.load_config()
            if first:  # first save, nothing downloaded yet: ask where files go (Cancel keeps the default)
                cfg["download_dir"] = pick_folder() or cfg["download_dir"]
            cfg["courses"] = {str(int(c["id"])): str(c["short"]) for c in body.get("courses", [])}
            lh.save_json(lh.CONFIG, cfg)
            start(run_sync)
        elif path == "/api/sync":
            start(run_sync)
        elif path == "/api/folder":
            chosen = pick_folder()
            if chosen:
                cfg = lh.load_config()
                cfg["download_dir"] = chosen
                lh.save_json(lh.CONFIG, cfg)
        elif path == "/api/reveal":
            if not reveal(str(body.get("path", ""))):
                return self.send(404, {"error": "File not found (it may have been moved or deleted)."})
        elif path == "/api/open":
            folder = os.path.expanduser(lh.load_config()["download_dir"])
            os.makedirs(folder, exist_ok=True)
            subprocess.run(["open", folder])
        elif path == "/api/quit":
            self.send(200, {"ok": True})
            threading.Timer(0.3, lambda: os._exit(0)).start()
            return
        else:
            return self.send(404, {"error": "not found"})
        self.send(200, {"ok": True})


def main():
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        sys.exit(f"LearnHub is already running (port {PORT}); use the address in the terminal where it runs.")
    start(auto_sync)
    print(f"LearnHub UI: {ui_url()}\nTo stop: click Quit on the page, or press Ctrl+C.", flush=True)
    webbrowser.open(ui_url())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
