#!/bin/bash
# Double-click in Finder: installs on the first run (and after requirements.txt changes), then opens LearnHub.
cd "$(dirname "$0")" || exit 1

fail() { echo; echo "$1"; echo "Press Enter to close."; read -r; exit 1; }

if [ ! -x .venv/bin/python ]; then
  echo "First run: installing (takes about a minute)..."
  python3 -m venv .venv || fail "Python 3 is needed: if macOS offers to install developer tools, accept, then run this again."
fi
if [ requirements.txt -nt .venv/.installed ]; then
  { .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt && touch .venv/.installed; } \
    || fail "Install failed: check the internet connection, then run this again."
fi

.venv/bin/python app.py || fail "LearnHub stopped with an error (see above)."
