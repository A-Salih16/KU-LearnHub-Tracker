"""Local study ticks, separate from official Moodle submission status."""
import hashlib
import os
import threading
import urllib.parse

import learnhub as lh
import resource_library

LOCK = threading.Lock()


def build():
    cfg, state = lh.load_config(), lh.load_state()
    selected = set(cfg['courses'].values())
    progress = lh.load_json(os.path.join(lh.DATA_DIR, 'progress.json'), {})
    tasks = []
    for a in state.get('upcoming', []):
        if a['course'] not in selected:
            continue
        identity = a.get('url') or a['course'] + ':' + a['name']
        tasks.append(dict(id='assignment:' + identity, course=a['course'], name=a['name'],
                          kind='Assignments', due=a.get('due', 0), url=a.get('url'),
                          official_submitted=bool(a.get('submitted')), status_known=True))
    for a in state.get('activities', []):
        if a['course'] not in selected:
            continue
        tasks.append(dict(id='activity:' + a['course'] + ':' + str(a['id']), course=a['course'],
                          name=a['name'], kind='Assignments' if a['activity_type'] == 'turnitintooltwo'
                          else 'Quizzes & surveys', due=a.get('due', 0), url=a.get('url'),
                          official_submitted=False, status_known=False))
    for a in state.get('assessments', []):
        if a['course'] in selected:
            tasks.append(dict(id='assessment:' + a['course'] + ':' + str(a['id']), course=a['course'],
                              name=a['name'], kind='Other assessments', due=a.get('due', 0),
                              url=a.get('url'), official_submitted=False, status_known=False))
    for r in resource_library.resources():
        if r['course'] not in selected or r.get('current') is False or r['category'] != 'Problem sets & practice':
            continue
        identity = hashlib.sha256(r['path'].encode()).hexdigest()
        tasks.append(dict(id='practice:' + identity, course=r['course'], name=r['name'],
                          kind='Problem sets & labs', due=0,
                          url='/api/resource?' + urllib.parse.urlencode({'path': r['path']}),
                          official_submitted=False, status_known=False))
    for task in tasks:
        task['personal_done'] = progress.get(task['id'], False) is True
    tasks.sort(key=lambda task: (task['due'] or float('inf'), task['name'].casefold()))
    return {'tasks': tasks, 'courses': sorted(selected)}


def update(body):
    identity, done = body.get('id'), body.get('done')
    if not isinstance(identity, str) or not isinstance(done, bool):
        raise ValueError('Expected a task id and boolean done value')
    with LOCK:
        if identity not in {t['id'] for t in build()['tasks']}:
            raise ValueError('Unknown task')
        path = os.path.join(lh.DATA_DIR, 'progress.json')
        progress = lh.load_json(path, {})
        progress[identity] = done
        lh.save_json(path, progress)
    return {'ok': True}
