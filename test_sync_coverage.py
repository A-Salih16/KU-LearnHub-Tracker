"""Freshness must reflect failed checks, missing files and updated revisions."""
import tempfile
from pathlib import Path
from unittest import mock
import learnhub as lh
import sync_coverage


def run():
    with tempfile.TemporaryDirectory() as tmp:
        cfg={'courses':{'1':'TEST'},'download_dir':tmp}
        f={'type':'file','filename':'a.pdf','fileurl':'https://learn.hub.ku.edu.tr/a.pdf','timemodified':2}
        sections=[{'name':'Week 1','modules':[{'id':2,'name':'Slides','modname':'resource','contents':[f]},
                  {'id':3,'name':'Hidden','modname':'resource','uservisible':False,'contents':[f]},
                  {'id':4,'name':'Announcements','modname':'forum'}]}]
        state={'files':{},'assignments':{}}
        sync_coverage.capture('TEST',sections,state,tmp,lh.file_key)
        row=sync_coverage.summary(cfg,state)['courses'][0]
        assert row['expected']==1 and row['available']==0 and len(row['modules'])==2
        Path(tmp,'a.pdf').write_bytes(b'pdf')
        state['files'][lh.file_key(f['fileurl'])]={'path':'a.pdf','timemodified':1}
        assert sync_coverage.summary(cfg,state)['courses'][0]['available']==0
        state['files'][lh.file_key(f['fileurl'])]['timemodified']=2
        assert sync_coverage.summary(cfg,state)['courses'][0]['available']==1
        Path(tmp,'a.pdf').unlink()
        assert sync_coverage.summary(cfg,state)['courses'][0]['available']==0
        state['last_successful_sync']=100
        with mock.patch.object(lh,'load_config',return_value=cfg),mock.patch.object(lh,'load_state',return_value=state),mock.patch.object(lh,'save_state'),mock.patch.object(lh,'sync_files',side_effect=OSError('offline')):
            try:lh.sync_once()
            except OSError:pass
            else:raise AssertionError('failure swallowed')
        assert state['last_successful_sync']==100 and state['sync_errors'] and state['last_sync_attempt']>100
        with mock.patch.object(lh,'call',side_effect=RuntimeError('hidden')),mock.patch.object(lh,'log'):
            lh.sync_files(cfg,state)
        assert state['coverage']['TEST']['error'] and state['sync_errors']
    print('PASS: missing/revised files, hidden modules, failed checks preserve last success, inaccessible courses reported.')

if __name__=='__main__':run()
