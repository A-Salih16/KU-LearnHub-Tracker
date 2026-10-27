# LearnHub Tracker

Downloads course files and tracks coursework from Koç University LearnHub on macOS and Windows

![LearnHub Tracker](docs/screenshot.png)

- **Tasks by course:** assignments, quizzes and surveys, practice files/labs, and other assessments stay separate. Overdue and undated work stays visible; dated work is sorted by deadline within each group.
- **Personal ticks:** mark work done across authorized browser profiles on this computer. These ticks do not submit work to LearnHub or change official submission status.
- **Materials:** search downloaded slides, readings, syllabi, problem sets and solutions by course and filename. PDFs open in the browser; other formats download. Filename categories are suggestions, not instructor requirements.
- **Sync evidence:** Settings shows the last successful check, available/expected file counts and partial failures. Original files are never overwritten.
- **Read-only:** the same five permitted Moodle API functions are used. KU login happens in Chrome; the password never reaches this app.

## Requirements

- macOS or Windows
- Python 3.9 or newer
- Google Chrome for KU NetID login

## Getting started

Download and unpack the repository using **Code → Download ZIP**

### macOS

Double-click **Start.command**. If macOS blocks it, use **System Settings → Privacy & Security → Open Anyway**. Accept a developer-tools installation if prompted, then run the launcher again.

### Windows

Install Python from [python.org](https://www.python.org/downloads/windows/) with **Add Python to PATH** enabled, then double-click **Start-Windows.cmd**. The launcher works from folders containing spaces and installs the required login tools on first run or after requirements change.

### Connect

1. Use the browser page opened by the launcher
2. Click **Log in with KU NetID** and finish login in the separate Chrome window
3. In **Settings**, choose courses and save the selection
4. Optionally change the download folder

Keep the launcher's terminal open for automatic checks every three hours. **Sync now** checks immediately. Reopening the launcher opens the running app. **Quit app** or closing the terminal stops checks.

## Daily use

- Open a course to see its tasks and materials. Filter by course, To do / Completed / All tasks, or search filenames and folders.
- Official Moodle assignment submission status is shown when available. Quiz attempts, surveys, Turnitin submissions and grade-only assessments have **unknown official completion**; personal ticks are explicitly separate.
- Practice files and labs are recognized from filenames. Solutions remain in Materials rather than becoming tasks. No deadline or grading requirement is inferred from a filename or syllabus.
- Files removed locally are downloaded again on the next sync. Updated course files receive a new filename, preserving the earlier copy. Materials absent from the latest source list remain available as older saved files and are not promoted as current practice tasks.
- The tracker does not cover forum posts, external tools, quiz questions/attempts or full Turnitin details. Check [official LearnHub](https://learn.hub.ku.edu.tr/) for the authoritative activity list.

## Browser access and private data

The server listens only on `127.0.0.1:47653`. Open the authorized URL from the launcher once in each browser profile. A local HttpOnly, SameSite cookie keeps that profile connected across restarts. The URL key is removed from the address bar after loading. This is local access, not an internet or campus-network service.

App data locations:

- macOS: `~/Library/Application Support/Course_Tracking_System/` (existing installations keep their data)
- Windows: `%LOCALAPPDATA%\LearnHubTracker\`

Configuration, sync history, personal ticks, the local UI access key and the Moodle login token live there. Windows protects newly saved Moodle tokens with current-user DPAPI; macOS retains the existing user-readable-only token file. These files and course downloads must not be committed. The Moodle token never enters the browser API response.

Files default to `~/Documents/Courses`. Choose another folder using Settings. Personal ticks live in `progress.json` and are shared by authorized browsers using this installation; they are not Moodle submissions or cloud backups.

## Terminal use

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

On Windows, use `python` and `.venv\Scripts\python.exe` instead. The launchers handle setup automatically.

The existing command-line client remains available:

```bash
.venv/bin/python learnhub.py login
.venv/bin/python learnhub.py courses
.venv/bin/python learnhub.py sync
```

CLI `sync` checks once. Only `app.py` runs the three-hour scheduler.

## Development and checks

No frontend build step or framework is required. The upstream Mac launcher and its pip bootstrap fix remain intact.

- `learnhub.py`: read-only Moodle API, downloads, activity metadata and assignment status
- `app.py` + `ui.html`: standalone local server and browser UI
- `resource_library.py`: local file categorization and indexed file access
- `study_tasks.py`: coursework list and separate local completion records
- `sync_coverage.py`: per-course checks, file revision coverage and partial failures
- `windows_support.py`: current-user DPAPI token encryption

Run all offline checks:

```bash
python run_tests.py
```

Tests use fake course data and temporary files, never a real KU session. GitHub Actions runs them on macOS and Windows with Python 3.9 and 3.13. Chrome login and real course sync need manual verification on each OS.

Originally created by [Kerem İkizoğlu](https://github.com/kikizoglu23). These additions adapt a personal Study Hub integration into a standalone tracker without student-specific schedules, course documents or local folder dependencies.
