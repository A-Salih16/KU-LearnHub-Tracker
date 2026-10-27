"""Evidence of what the read-only sync checked, including partial failures."""
import collections
import html
import os
import re
import time


def capture(short, sections, state, root, file_key):
    modules = []
    expected = {}
    for section in sections:
        for mod in section.get('modules', []):
            if mod.get('uservisible') is False:
                continue
            files = [f for f in mod.get('contents', []) if f.get('type') == 'file']
            for f in files:
                expected[file_key(f['fileurl'])] = f.get('timemodified', 0)
            modules.append(dict(id=mod.get('id'), name=html.unescape(mod.get('name', '')),
                                kind=mod['modname'], section=html.unescape(section.get('name', '')),
                                url=mod.get('url'), dates=mod.get('dates', []),
                                description=html.unescape(re.sub('<[^>]+>', ' ', mod.get('description', ''))).strip(),
                                files=[f['filename'] for f in files]))
    row = dict(course=short, checked_at=int(time.time()), modules=modules,
               counts=dict(collections.Counter(m['kind'] for m in modules)), expected=expected,
               error=None)
    state.setdefault('coverage', {})[short] = row
    return row


def summary(cfg, state):
    rows = []
    root = os.path.realpath(os.path.expanduser(cfg['download_dir']))
    for cid, short in cfg['courses'].items():
        saved = state.get('coverage', {}).get(short, {})
        available = 0
        for key, revision in saved.get('expected', {}).items():
            f = state['files'].get(key)
            if not f or f.get('timemodified', 0) < revision:
                continue
            path = os.path.realpath(os.path.join(root, f['path']))
            try:
                inside = os.path.commonpath([root, path]) == root
            except ValueError:  # different Windows drives
                inside = False
            if inside and os.path.isfile(path):
                available += 1
        rows.append(dict(course=short, course_id=cid, checked_at=saved.get('checked_at'),
                         error=saved.get('error'), expected=len(saved.get('expected', {})),
                         available=available, counts=saved.get('counts', {}), modules=saved.get('modules', [])))
    return dict(courses=rows, last_attempt=state.get('last_sync_attempt'),
                last_success=state.get('last_successful_sync'), errors=state.get('sync_errors', []),
                assignments_checked_at=state.get('assignments_checked_at'))
