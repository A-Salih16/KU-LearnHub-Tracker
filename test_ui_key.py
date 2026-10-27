"""Local authorization survives restarts; no Moodle calls or real keys used."""
from pathlib import Path
import tempfile
from unittest import mock
import app
import learnhub as lh

def run():
    with tempfile.TemporaryDirectory() as tmp:
        url=Path(tmp)/'ui_url'
        with mock.patch.object(lh,'DATA_DIR',tmp),mock.patch.object(app,'URL_FILE',str(url)):
            key=app.load_ui_key()
            assert len(key)==32 and key==app.load_ui_key()
            assert (Path(tmp)/'ui_key').read_text()==key
            # A new launch URL cannot silently rotate an existing installation key.
            url.write_text('http://127.0.0.1:'+str(app.PORT)+'/?k='+'z'*32)
            assert app.load_ui_key()==key
    with tempfile.TemporaryDirectory() as tmp:
        url=Path(tmp)/'ui_url';legacy='a'*32
        url.write_text('http://127.0.0.1:'+str(app.PORT)+'/?k='+legacy)
        with mock.patch.object(lh,'DATA_DIR',tmp),mock.patch.object(app,'URL_FILE',str(url)):
            assert app.load_ui_key()==legacy
            assert app.load_ui_key()==legacy
    print('PASS: stable local key, repeated startups, migration preserving current browser sessions')

if __name__=='__main__':run()
