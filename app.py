"""LearnHub UI: local server (127.0.0.1 only) + a page in the browser + automatic sync while it is open.

Run: .venv/bin/python app.py   (to stop: "Quit" on the page, or Ctrl+C in the terminal)
"""
import http.server
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
import resource_library
import sync_coverage
import study_tasks
from pathlib import Path
from http.cookies import SimpleCookie

import learnhub as lh

PORT = 47653
SYNC_EVERY = 3 * 3600
UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")
KEY = secrets.token_urlsafe(24)  # tests/imports; main loads this installation's saved local key
URL_FILE = os.path.join(lh.DATA_DIR, "ui_url")  # lets a second start reopen the running instance's page

# has_token: if there is a token but LearnHub can't be reached, the UI shows "offline" instead of the login box.
status = {"checked": False, "user": None, "has_token": False, "syncing": False, "logging_in": False,
          "last_sync": None, "next_sync": None, "error": None}
sync_lock = threading.Lock()
sync_wanted = threading.Event()  # a request that arrives during a sync is not lost
login_lock = threading.Lock()


def ui_url():
    return f"http://127.0.0.1:{PORT}/?k={KEY}"


def load_ui_key():
    """Keep authorized browser profiles working across app restarts."""
    os.makedirs(lh.DATA_DIR, exist_ok=True)
    keyfile=Path(lh.DATA_DIR)/'ui_key'
    if keyfile.exists():
        value=keyfile.read_text(encoding='ascii').strip()
        if len(value)!=32 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
            raise ValueError('Saved app access key is damaged. Remove ui_key in the app data folder and reopen the launcher.')
        return value
    value=secrets.token_urlsafe(24)
    # Reuse the current launch key during migration, preserving current sessions.
    try:
        previous=urllib.parse.urlparse(Path(URL_FILE).read_text().strip())
        candidate=urllib.parse.parse_qs(previous.query).get('k',[''])[0]
        if previous.hostname=='127.0.0.1' and previous.port==PORT and len(candidate)==32 and all(c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in candidate):
            value=candidate
    except (OSError,ValueError):
        pass
    with os.fdopen(os.open(keyfile,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
        f.write(value)
    return value


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
            else:
                status["error"] = ("Sync partly completed. Check sync coverage in Settings."
                                   if lh.load_config()["courses"] else None)
        except lh.TokenError:
            status["user"] = status["has_token"] = None
            status["error"] = "Your session has expired; log in again."
            lh.notify("LearnHub", "Your session has expired. Log in again from the UI.")
        except Exception as e:  # network drop, etc.: retried on the next round
            status["error"] = f"Sync failed ({type(e).__name__}). Check your connection and retry."
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
        status["error"] = f"Login failed ({type(e).__name__}). Finish KU login in Chrome and try again."
    finally:
        status["logging_in"] = False
        login_lock.release()


def pick_folder():
    """Files go into a "Courses" folder inside the chosen one (not created again if Courses itself is chosen)."""
    if sys.platform == "win32":
        # Run the native chooser in a separate process; Tk must own its UI thread.
        script = 'import tkinter as t; from tkinter import filedialog; r=t.Tk(); r.withdraw(); print(filedialog.askdirectory(title="Choose a download folder")); r.destroy()'
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        chosen = r.stdout.strip()
        return (chosen if os.path.basename(chosen) == "Courses" else os.path.join(chosen, "Courses")) if chosen else None
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
    return {**status, "last_sync":state.get('last_successful_sync',status['last_sync']),
            "coverage":sync_coverage.summary(cfg,state),
            "download_dir": os.path.expanduser(cfg["download_dir"]), "tracked": cfg["courses"],
            "upcoming": state.get("upcoming", []), "activities":state.get('activities',[]), "recent": list(lh.RECENT)[:30],
            "files": [{"path": v["path"], "at": v.get("at", v["timemodified"])} for v in files]}


def reveal(rel):
    """Shows a file from the download folder in Finder. A path that leaves the folder is rejected."""
    root = os.path.realpath(os.path.expanduser(lh.load_config()["download_dir"]))
    path = os.path.realpath(os.path.join(root, rel))
    try:
        inside = os.path.commonpath([root, path]) == root
    except ValueError:
        inside = False
    if not inside or not os.path.exists(path):
        return False
    subprocess.run(["explorer.exe", "/select,", path] if sys.platform == "win32" else ["open", "-R", path])
    return True


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if ctype == "text/html":
            self.send_header("Set-Cookie", f"study_session={KEY}; Path=/; HttpOnly; SameSite=Lax; Max-Age=7776000")
        self.end_headers()
        self.wfile.write(data)

    def allowed(self, key):
        # Host check: against DNS rebinding (another site's name pointed at 127.0.0.1).
        # Key check: open websites must not be able to send requests to the UI.
        host_ok = self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
        cookie = SimpleCookie()
        try: cookie.load(self.headers.get("Cookie", ""))
        except Exception: pass
        session = cookie.get("study_session")
        return host_ok and (secrets.compare_digest(key or "", KEY) or
                            bool(session and secrets.compare_digest(session.value, KEY)))

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            if not self.allowed(urllib.parse.parse_qs(query).get("k", [""])[0]):
                return self.send(403, b"Open LearnHub Tracker using Start.command (Mac) or Start-Windows.cmd (Windows). Each browser profile needs the launcher link once.", "text/plain")
            text = Path(UI).read_text(encoding="utf-8")
            config = '<script>window.LEARNHUB_CONFIG=' + json.dumps({"key": KEY}) + ';</script>'
            return self.send(200, text.replace('<head>', '<head>' + config, 1).encode(), "text/html")
        if not self.allowed(self.headers.get("X-Key")):
            return self.send(403, {"error": "unauthorized"})
        try:
            if path == '/api/tasks':
                return self.send(200, study_tasks.build())
            if path == '/api/resources':
                return self.send(200, {'resources':resource_library.resources()})
            if path == '/api/resource':
                target=resource_library.resource_path(urllib.parse.parse_qs(query).get('path',[''])[0])
                if target is None: return self.send(404, {'error':'File not found. Sync again if the file was moved.'})
                pdf=target.suffix.casefold()=='.pdf'
                self.send_response(200)
                self.send_header('Content-Type','application/pdf' if pdf else 'application/octet-stream')
                self.send_header('Content-Length',str(target.stat().st_size))
                self.send_header('Content-Disposition',('inline' if pdf else 'attachment')+"; filename*=UTF-8''"+urllib.parse.quote(target.name))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Referrer-Policy','no-referrer')
                self.end_headers()
                with target.open('rb') as source:
                    while chunk:=source.read(65536): self.wfile.write(chunk)
                return
            if path == "/api/status":
                return self.send(200, api_status())
            if path == "/api/courses":
                return self.send(200, active_courses())
        except lh.TokenError:
            status["user"] = status["has_token"] = None
            return self.send(401, {"error": "Not logged in; log in first."})
        except Exception as e:
            return self.send(500, {"error": f"LearnHub request failed ({type(e).__name__}). Try again."})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'):
            return self.send(403, {"error":"unauthorized origin"})
        if not self.allowed(self.headers.get("X-Key")):
            return self.send(403, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 <= length <= 100000: raise ValueError('body too large')
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict): raise ValueError('expected object')
            if self.path == '/api/courses':
                items = body.get('courses', [])
                if not isinstance(items, list) or len(items) > 100: raise ValueError('invalid courses')
                for c in items:
                    if not isinstance(c, dict) or int(c['id']) <= 0 or not isinstance(c['short'], str): raise ValueError('invalid course')
        except (ValueError, KeyError, TypeError):
            return self.send(400, {"error": "invalid request"})
        path = self.path
        if path == '/api/tasks':
            try:
                return self.send(200, study_tasks.update(body))
            except ValueError:
                return self.send(400, {'error': 'Invalid task or completion value.'})
        elif path == "/api/login":
            start(run_login)
        elif path == "/api/courses":
            cfg = lh.load_config()
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
            subprocess.run(["explorer.exe", folder] if sys.platform == "win32" else ["open", folder])
        elif path == "/api/quit":
            self.send(200, {"ok": True})
            threading.Timer(0.3, lambda: os._exit(0)).start()
            return
        else:
            return self.send(404, {"error": "not found"})
        self.send(200, {"ok": True})


class LocalServer(http.server.ThreadingHTTPServer):
    # Windows SO_REUSEADDR can let two app instances bind the same port.
    # They then disagree about the session key and valid pages return 403.
    allow_reuse_address = False

    def server_bind(self):
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    global KEY
    try:
        server = LocalServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # Already running (Start.command clicked again): reopen its page instead of failing.
        try:
            with open(URL_FILE) as f:
                url = f.read()
        except OSError:
            sys.exit(f"Port {PORT} is in use; use the address in the terminal where LearnHub runs.")
        print(f"LearnHub is already running; opening it: {url}")
        webbrowser.open(url)
        return
    os.makedirs(lh.DATA_DIR, exist_ok=True)
    KEY=load_ui_key()
    # 0600 like the token: the URL holds the UI key, only this user may read it.
    with os.fdopen(os.open(URL_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(ui_url())
    start(auto_sync)
    print(f"LearnHub UI: {ui_url()}\nTo stop: click Quit on the page, or press Ctrl+C.", flush=True)
    webbrowser.open(ui_url())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
