"""Regression coverage with fake courses and temporary state; no LearnHub requests."""
import http.client
import json
import tempfile
import threading
from pathlib import Path
from unittest import mock

import app
import learnhub as lh
import study_tasks


def run():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = {'download_dir': tmp, 'courses': {'1': 'TEST101'}}
        state = {'files': {
            'lab': {'path': 'TEST101/Lab01.zip', 'timemodified': 1},
            'solution': {'path': 'TEST101/PS1_Solutions.pdf', 'timemodified': 1}},
            'assignments': {}, 'upcoming': [
                {'course': 'TEST101', 'name': 'Overdue task', 'due': 1, 'submitted': False, 'url': lh.SITE + '/mod/assign/view.php?id=1'},
                {'course': 'TEST101', 'name': 'Undated task', 'due': 0, 'submitted': True, 'url': lh.SITE + '/mod/assign/view.php?id=2'}],
            'activities': [{'course': 'TEST101', 'id': 3, 'name': 'Turnitin paper', 'activity_type': 'turnitintooltwo', 'due': 0, 'url': None}],
            'assessments': [{'course': 'TEST101', 'id': 4, 'name': 'Final exam', 'due': 0, 'url': None}]}
        with mock.patch.object(lh, 'load_config', return_value=cfg), mock.patch.object(lh, 'load_state', return_value=state), mock.patch.object(lh, 'DATA_DIR', tmp):
            data = study_tasks.build()
            assert len(data['tasks']) == 5
            assert data['tasks'][0]['name'] == 'Overdue task'
            lab = next(t for t in data['tasks'] if t['kind'] == 'Problem sets & labs')
            assert lab['due'] == 0 and not lab['official_submitted']
            study_tasks.update({'id': lab['id'], 'done': True})
            assert next(t for t in study_tasks.build()['tasks'] if t['id'] == lab['id'])['personal_done']
            assert state['upcoming'][0]['submitted'] is False  # personal ticks never change official status
            for invalid in ({'id': 'unknown', 'done': True}, {'id': lab['id'], 'done': 'true'}):
                try: study_tasks.update(invalid)
                except ValueError: pass
                else: raise AssertionError('Invalid completion accepted')
            server = app.LocalServer(('127.0.0.1', 0), app.Handler)
            with mock.patch.object(app, 'PORT', server.server_port), mock.patch.object(app, 'KEY', 'test-only'):
                threading.Thread(target=server.serve_forever, daemon=True).start()
                def request(path, method='GET', body=None, headers=None):
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    conn.request(method, path, body, headers or {})
                    result = conn.getresponse()
                    output = result.status, dict(result.getheaders()), result.read()
                    conn.close()
                    return output
                try:
                    assert request('/')[0] == 403
                    code, headers, body = request('/?k=test-only')
                    assert code == 200 and b'Course tasks' in body and b'LEARNHUB_CONFIG' in body
                    assert int(headers['Content-Length']) == len(body)
                    cookie = headers['Set-Cookie'].split(';')[0]
                    assert all(flag in headers['Set-Cookie'] for flag in ('HttpOnly', 'SameSite=Lax', 'Max-Age='))
                    assert request('/', headers={'Cookie': cookie})[0] == 200
                    assert request('/api/tasks')[0] == 403
                    assert request('/api/tasks', headers={'Cookie': cookie})[0] == 200
                    assert request('/api/status', headers={'X-Key': 'test-only', 'Host': 'evil.example'})[0] == 403
                    assert request('/api/tasks', 'POST', '{}', {'Cookie': cookie, 'Origin': 'https://evil.example'})[0] == 403
                    assert request('/api/tasks', 'POST', '[]', {'Cookie': cookie})[0] == 400
                    assert request('/api/tasks', 'POST', '{}', {'Cookie': cookie})[0] == 400
                    assert request('/api/tasks', 'POST', '{}', {'Cookie': cookie, 'Content-Length': '100001'})[0] == 400
                    payload = json.dumps({'id': lab['id'], 'done': False})
                    assert request('/api/tasks', 'POST', payload, {'Cookie': cookie})[0] == 200
                    for path in ('/.local/token', '/app.py', '/%2e%2e/README.md', '/ui.html'):
                        assert request(path, headers={'Cookie': cookie})[0] == 404
                finally:
                    server.shutdown()
                    server.server_close()
    print('PASS: standalone UI, task persistence, official/personal separation, auth, CSRF, body limits and private paths')


if __name__ == '__main__':
    run()
