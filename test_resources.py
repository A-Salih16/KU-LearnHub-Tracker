"""Resource classification and authenticated file delivery; no school calls."""
import http.client
import tempfile
import threading
from pathlib import Path
from unittest import mock
import app
import learnhub as lh
import resource_library as library

def run():
    cases={'ECON201/Week 2/Problem Set 1.pdf':'Problem sets & practice',
           'ECON201/Week 2/PS_2_Solutions.pdf':'Solutions',
           'COMP306/01-Introduction.pptx':'Slides & lectures',
           'ECON201/Week 1/Lecture2.pdf':'Slides & lectures',
           'PSYC100/Syllabus.pdf':'Syllabi',
           'SOSC130/Readings/Paper 1.pdf':'Readings',
           'ENGR421/Week1.pdf':'Other materials'}
    for path,category in cases.items(): assert library.category(path)==category,(path,library.category(path))
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);(root/'Course').mkdir();(root/'Course'/'Lecture.pdf').write_bytes(b'%PDF-test');(root/'secret.txt').write_text('private')
        cfg={'download_dir':str(root),'courses':{'1':'Course'}}
        state={'files':{'one':{'path':'Course/Lecture.pdf','at':100,'timemodified':90},'outside':{'path':'../outside.txt','timemodified':0}},'assignments':{}}
        server=app.LocalServer(('127.0.0.1',0),app.Handler)
        with mock.patch.object(lh,'load_config',return_value=cfg),mock.patch.object(lh,'load_state',return_value=state),mock.patch.object(app,'PORT',server.server_port),mock.patch.object(app,'KEY','test-only'):
            threading.Thread(target=server.serve_forever,daemon=True).start()
            def get(path,key='test-only'):
                c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('GET',path,headers={'X-Key':key});r=c.getresponse();result=r.status,r.getheader('Content-Type'),r.read();c.close();return result
            try:
                assert library.resources()[0]['course_url']==lh.SITE+'/course/view.php?id=1'
                assert library.resource_path('secret.txt') is None
                assert library.resource_path('../outside.txt') is None
                assert get('/api/resource?path=Course%2FLecture.pdf')==(200,'application/pdf',b'%PDF-test')
                assert get('/api/resource?path=secret.txt')[0]==404
                assert get('/api/resource?path=..%2Foutside.txt')[0]==404
                assert get('/api/resources',key='wrong')[0]==403
                assert get('/api/resources')[0]==200
                try:
                    duplicate=app.LocalServer(server.server_address,app.Handler)
                except OSError: pass
                else:
                    duplicate.server_close();raise AssertionError('Two servers bound the same address')
            finally: server.shutdown();server.server_close()
    print('PASS: resource groups, all-file listing, source links, authenticated PDF opening, indexed-path limits, single instance')

if __name__=='__main__':run()
