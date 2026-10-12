"""KU LearnHub tracker. READ-ONLY: never changes anything on LearnHub."""
import argparse
import base64
import collections
import errno
import hashlib
import html
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request

SITE = "https://learn.hub.ku.edu.tr"
DATA_DIR = os.path.expanduser("~/Library/Application Support/Course_Tracking_System")
CONFIG = os.path.join(DATA_DIR, "config.json")
STATE = os.path.join(DATA_DIR, "state.json")
TOKEN = os.path.join(DATA_DIR, "token")  # not the Keychain: it kept showing permission prompts
RECENT = collections.deque(maxlen=100)  # recent events shown in the UI
DUE_SOON = 48 * 3600  # warn this long before a deadline (if not submitted)
MAX_NAME = 120  # bytes; the limit is 255, and " (2026-10-07)", " (2)", ".part" may be appended later

# The ONLY Moodle functions this program may call. All of them read.
# Never add a write / submit / "view" function here.
READ_ONLY = {
    "core_webservice_get_site_info",
    "core_course_get_enrolled_courses_by_timeline_classification",
    "core_course_get_contents",
    "mod_assign_get_assignments",
    "mod_assign_get_submission_status",
}


class TokenError(Exception):
    pass


def flatten(args, prefix=""):
    """{"courseids": [1, 2]} -> {"courseids[0]": 1, "courseids[1]": 2} (Moodle REST format)."""
    out = {}
    items = args.items() if isinstance(args, dict) else enumerate(args)
    for k, v in items:
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, (dict, list)):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out


def call(function, token=None, **args):
    if function not in READ_ONLY:
        raise PermissionError(f"Moodle function not allowed: {function}")
    data = {"wstoken": token or get_token(), "wsfunction": function,
            "moodlewsrestformat": "json", **flatten(args)}
    req = urllib.request.Request(f"{SITE}/webservice/rest/server.php",
                                 data=urllib.parse.urlencode(data).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        result = json.load(r)
    if isinstance(result, dict) and "exception" in result:
        if result.get("errorcode") in ("invalidtoken", "accessexception"):
            raise TokenError("Token is invalid or expired: run `learnhub.py login`.")
        raise RuntimeError(f"{result.get('errorcode')}: {result.get('message')}")
    return result


def get_token():
    try:
        with open(TOKEN, encoding="utf-8") as f:
            token = f.read().strip()
    except FileNotFoundError:
        token = ""
    if not token:
        raise TokenError("No saved token: run `learnhub.py login`.")
    return token


def save_token(token):
    os.makedirs(DATA_DIR, exist_ok=True)
    # 0600: only this user can read the file; it is created with this mode, so it is never briefly open to others.
    with os.fdopen(os.open(TOKEN + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(token)
    os.replace(TOKEN + ".tmp", TOKEN)


def decode_launch(location, passport):
    """moodlemobile://token=<base64> -> token. The signature is checked against md5(site + passport)."""
    payload = location.split("token=", 1)[1]
    sig, token, *_ = base64.b64decode(payload).decode().split(":::")
    if sig != hashlib.md5((SITE + passport).encode()).hexdigest():
        raise ValueError("Token signature does not match; it may have come from another site.")
    return token


def login():
    """Log in with KU NetID (Playwright + Chrome). Saves the token and returns the user's name."""
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth

    passport = str(secrets.randbelow(10**9))
    url = f"{SITE}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport={passport}&urlscheme=moodlemobile"
    location = ""

    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url)
        print("Log in with KU NetID in the Chrome window that opened...", flush=True)
        # The moodlemobile:// redirect after login sits in the middle of a redirect chain, so the
        # browser can't catch it (the page looks "stuck"). Instead we poll launch.php ourselves with
        # the browser's cookies: once login is done, the token arrives in the Location header.
        deadline = time.time() + 300
        while time.time() < deadline and not page.is_closed():
            page.wait_for_timeout(3000)
            resp = context.request.get(url, max_redirects=0)
            location = resp.headers.get("location", "")
            if location.startswith("moodlemobile://token="):
                break
        browser.close()

    if not location.startswith("moodlemobile://token="):
        raise TokenError("Could not get a token (login not finished within 5 minutes, or the window was closed).")
    token = decode_launch(location, passport)
    name = call("core_webservice_get_site_info", token=token)["fullname"]
    save_token(token)
    notify("LearnHub", f"Logged in: {name}")
    return name


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)  # our own file; never left half-written


def load_config():
    return load_json(CONFIG, {"download_dir": "~/Documents/Courses", "courses": {}})


def courses():
    cfg = load_config()
    active = call("core_course_get_enrolled_courses_by_timeline_classification",
                  classification="inprogress")["courses"]
    tracked = {}
    print("Choose the courses to track (Enter = keep the current choice):")
    for c in active:
        cid = str(c["id"])
        was = cid in cfg["courses"]
        name = html.unescape(c["fullname"])
        answer = input(f"  {name}  [{'Y/n' if was else 'y/N'}] ").strip().lower()
        if answer == "y" or (answer == "" and was):
            tracked[cid] = html.unescape(c["shortname"])
    cfg["courses"] = tracked
    save_json(CONFIG, cfg)
    print(f"\nTracking {len(tracked)} courses: {', '.join(tracked.values()) or '-'}")


def safe_name(name):
    """Turns a name from the server into one safe file/folder name: no ../ or / (":" shows up as "/" in
    Finder, so it is replaced too); long names are cut to MAX_NAME bytes, keeping the extension."""
    name = re.sub(r"[\x00-\x1f/:]", "-", html.unescape(name).strip())
    if len(name.encode()) > MAX_NAME:
        stem, ext = os.path.splitext(name)
        if len(ext) > 10:  # in names like "1. Week - Intro" the part after the dot is not an extension
            stem, ext = name, ""
        name = stem.encode()[:MAX_NAME - len(ext.encode())].decode(errors="ignore") + ext
    return name if name not in ("", ".", "..") else "_"


def create_new(path):
    """Opens a new, empty file; if the name is taken, tries "name (2).pdf", "name (3).pdf"... Returns (file, path)."""
    base, ext = os.path.splitext(path)
    n = 1
    while True:
        candidate = path if n == 1 else f"{base} ({n}){ext}"
        try:
            return open(candidate, "xb"), candidate  # "x": the OS refuses if the file exists
        except FileExistsError:
            n += 1


def write_new(path, chunks, root):
    """Writes the file ONLY as a new file and never overwrites an existing one. It first writes to
    "name.pdf.part"; when done, it reserves a free name ("name (2).pdf" if "name.pdf" is taken) and moves
    the .part onto its own empty file: an interrupted download never takes the real name. Returns the path."""
    root = os.path.realpath(root)
    if os.path.commonpath([root, os.path.realpath(path)]) != root:
        raise ValueError(f"Blocked a write outside the download folder: {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f, part = create_new(path + ".part")
    mine = [part]  # files created by this call; on error only these are removed
    try:
        with f:
            for chunk in chunks:
                f.write(chunk)
        reserved, final = create_new(path)
        mine.append(final)
        reserved.close()
        os.replace(part, final)
    except BaseException:
        for p in mine:
            os.remove(p)
        raise
    return final


def download(fileurl, filesize=0):
    sep = "&" if "?" in fileurl else "?"
    with urllib.request.urlopen(f"{fileurl}{sep}token={get_token()}", timeout=120) as r:
        # If the connection drops early, http.client raises nothing and the body just ends short: check the size.
        expected = int(r.headers.get("Content-Length") or filesize)
        got = 0
        while chunk := r.read(1 << 16):
            got += len(chunk)
            yield chunk
    if expected and got != expected:
        raise OSError(f"Incomplete download: {got}/{expected} bytes")


def log(msg):
    print(msg, flush=True)
    RECENT.appendleft(f"{time.strftime('%d.%m %H:%M')}  {msg}")


def notify(title, message):
    log(f"[{title}] {message}")
    # The text is passed as argv, not embedded in the script: no quoting/escaping problems.
    subprocess.run(["osascript", "-e", "on run argv", "-e",
                    "display notification (item 2 of argv) with title (item 1 of argv)",
                    "-e", "end run", title, message], capture_output=True)


def file_key(fileurl):
    """State key. The .../content/<revision>/... number in resource/folder/page URLs goes up whenever a file
    is added to a folder or a resource is edited; if it stayed in the key, the same files would look new.
    It is pinned to 0 (safe to apply twice). mod_book has a chapter number there, so it is left alone."""
    return re.sub(r"(pluginfile\.php/\d+/mod_(?:resource|folder|page)/content/)\d+/", r"\g<1>0/",
                  fileurl.split("?")[0], count=1)


def load_state():
    state = load_json(STATE, {"files": {}, "assignments": {}})
    # Keys from the old version contain the revision: migrate them the same way. On a clash the newest entry wins.
    files = sorted(state["files"].items(), key=lambda kv: kv[1]["timemodified"])
    state["files"] = {file_key(k): v for k, v in files}
    return state


def save_state(state):
    save_json(STATE, state)


def sync_files(cfg, state):
    """Downloads new files. Returns the first error the user has to fix (no permission, disk full)."""
    root = os.path.expanduser(cfg["download_dir"])
    blocking = None
    for cid, short in cfg["courses"].items():
        new = []
        try:
            sections = call("core_course_get_contents", courseid=int(cid))
        except RuntimeError as e:  # course hidden / dropped: keep going with the other courses
            log(f"[skipped] {short}: not accessible ({e})")
            continue
        for section in sections:
            for mod in section["modules"]:
                for f in mod.get("contents", []):
                    if f["type"] != "file":
                        continue
                    key = file_key(f["fileurl"])
                    seen = state["files"].get(key)
                    updated = seen and f["timemodified"] > seen["timemodified"]
                    # Downloaded and still on disk: skip. If it was deleted or the download folder changed,
                    # download it again under the same name.
                    if seen and not updated and os.path.exists(os.path.join(root, seen["path"])):
                        continue
                    parts = [root, safe_name(short), safe_name(section["name"])]
                    if mod["modname"] == "folder":
                        parts.append(safe_name(mod["name"]))
                        parts += [safe_name(p) for p in f["filepath"].split("/") if p]
                    name = safe_name(f["filename"])
                    if updated:  # the instructor updated it: the old one stays, the new one gets a dated name
                        stem, ext = os.path.splitext(name)
                        name = f"{stem} ({time.strftime('%Y-%m-%d', time.localtime(f['timemodified']))}){ext}"
                    try:
                        path = write_new(os.path.join(*parts, name),
                                         download(f["fileurl"], f.get("filesize", 0)), root)
                    except TokenError:
                        raise
                    except Exception as e:  # 404, permission, disk full, incomplete...: other files continue
                        log(f"[error] {short}: {f['filename']} not downloaded ({type(e).__name__}: {e})")
                        if getattr(e, "errno", None) in (errno.EPERM, errno.EACCES, errno.ENOSPC):
                            blocking = blocking or e
                        continue
                    state["files"][key] = {"timemodified": f["timemodified"],
                                           "path": os.path.relpath(path, root), "at": int(time.time())}
                    save_state(state)
                    new.append(os.path.basename(path))
        if new:
            notify(f"LearnHub: {short}", f"{len(new)} new file{'s' if len(new) > 1 else ''}: {', '.join(new[:3])}"
                   + (" ..." if len(new) > 3 else ""))
    return blocking


def is_submitted(assign_id):
    s = call("mod_assign_get_submission_status", assignid=assign_id).get("lastattempt", {})
    # In a group assignment the individual record can stay "new" while the team's submission is "submitted".
    return any((s.get(k) or {}).get("status") == "submitted" for k in ("submission", "teamsubmission"))


def sync_assignments(cfg, state):
    now = time.time()
    upcoming = []
    courses = call("mod_assign_get_assignments", courseids=[int(c) for c in cfg["courses"]])["courses"]
    for course in courses:
        short = cfg["courses"].get(str(course["id"]), course["shortname"])
        for a in course["assignments"]:
            if a.get("nosubmissions"):  # a grade item without submissions (exam, participation)
                continue
            seen = state["assignments"].setdefault(str(a["id"]), {"notified_new": False, "notified_due": False})
            due = a["duedate"]
            due_text = time.strftime("%d.%m %H:%M", time.localtime(due)) if due else "no due date"
            name = html.unescape(a["name"])
            if not seen["notified_new"]:
                seen["notified_new"] = True
                if not due or due > now:  # no notification for old assignments that are already past due
                    notify(f"New assignment: {short}", f"{name} (due: {due_text})")
            if due <= now:
                continue
            submitted = is_submitted(a["id"])
            upcoming.append({"course": short, "name": name, "due": due, "submitted": submitted,
                             "url": f"{SITE}/mod/assign/view.php?id={a['cmid']}" if a.get("cmid") else None})
            if due - now < DUE_SOON and not seen["notified_due"] and not submitted:
                seen["notified_due"] = True
                notify(f"Due soon: {short}", f"{name} is not submitted! Due: {due_text}")
    state["upcoming"] = sorted(upcoming, key=lambda u: u["due"])


def sync_once():
    cfg = load_config()
    if not cfg["courses"]:  # with an empty list Moodle returns the assignments of ALL courses
        return False
    state = load_state()
    try:
        blocking = sync_files(cfg, state)
        sync_assignments(cfg, state)
    finally:  # even if the assignment pass fails halfway, save the sent notifications so they don't repeat
        save_state(state)
    if blocking:  # no permission / disk full: the rest of the work is done, but the error still reaches the user
        raise blocking
    return True


def sync():
    if not sync_once():
        sys.exit("No tracked courses: run `learnhub.py courses` first.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="log in with KU NetID and save the token")
    sub.add_parser("courses", help="choose the active courses to track")
    sub.add_parser("sync", help="download new files and check assignments")
    cmd = parser.parse_args().cmd
    try:
        {"login": login, "courses": courses, "sync": sync}[cmd]()
    except TokenError as e:
        notify("LearnHub", str(e))
        sys.exit(str(e))


if __name__ == "__main__":
    main()
