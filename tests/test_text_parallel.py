import io
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from core import piece
from workbench import Workbench


class NewFeaturesTests(unittest.TestCase):
    def make_app(self, root):
        media=Path(root)/'media';media.mkdir()
        for n in range(4):(media/f'{n}.mp4').write_bytes(b'fixture')
        with patch('workbench.probe',return_value={'duration':6,'has_audio':False}):
            app=Workbench(media,Path(root)/'data')
            for item in app.library():app.project(item['id'])
            return app

    def wait(self, predicate):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.02)
        self.fail('Task timed out')

    def test_srt_modes_txt_zip_and_validation(self):
        with tempfile.TemporaryDirectory() as root:
            app=self.make_app(root);ids=[p['id'] for p in app.library()]
            for ident in ids[:2]:
                p=app.project(ident);p['analyzed']=True
                p['segments']=[piece(0,1,'开头','other'),piece(1,3,'讲解','know'),piece(3,4,'','do'),piece(4,6,'结束','do')]
                p['segments'][0]['keep']=False
                app.projects[ident]=p
            data,_,name=app.text_export([ids[0]])
            self.assertIn('开头',data.decode());self.assertIn('00:00:04,000 --> 00:00:06,000',data.decode())
            self.assertTrue(name.endswith('_完整逐字稿.srt'))
            data,_,_=app.text_export([ids[0]],'kept-srt')
            self.assertNotIn('开头',data.decode());self.assertIn('00:00:03,000 --> 00:00:05,000',data.decode())
            self.assertEqual(data.decode().count('-->'),2)
            data,_,_=app.text_export([ids[0]],'txt');self.assertEqual(data.decode(),'开头\n讲解\n结束')
            data,_,_=app.text_export(ids[:2]);self.assertEqual(len(zipfile.ZipFile(io.BytesIO(data)).namelist()),2)
            with self.assertRaises(ValueError):app.text_export(ids[:3])
            with self.assertRaises(ValueError):app.text_export([ids[0]],'bad')
            with self.assertRaises(ValueError):app.set_concurrency(True)
            self.assertEqual(app.set_concurrency(3)['analysis_concurrency'],3)

    def test_parallel_lowering_and_independent_export(self):
        with tempfile.TemporaryDirectory() as root:
            app=self.make_app(root);ids=[p['id'] for p in app.library()]
            release=threading.Event();started=[];lock=threading.Lock()
            def analyze(job,project,rules):
                with lock:started.append(job)
                release.wait(5)
                if project['id']==ids[0]:raise RuntimeError('isolated failure')
            app.analyze=analyze
            with patch('workbench.importlib.util.find_spec',return_value=True):jobs=app.enqueue(ids,'analyze')
            try:
                self.wait(lambda:len(started)==2)
                app.set_concurrency(1)
                time.sleep(.2);self.assertEqual(len(started),2)
                # The independent export worker should finish while ASR is occupied.
                app.render=lambda *a:None
                fake='f'*32
                app.jobs[fake]={'id':fake,'kind':'export','status':'queued','cancel':False}
                app.pending['export'].put((fake,app.project(ids[3]),{}))
                self.wait(lambda:app.jobs[fake]['status']=='completed')
                app.cancel(jobs[2]['id'])
            finally:release.set()
            self.wait(lambda:all(app.jobs[j['id']]['status'] not in ('queued','running') for j in jobs))
            self.assertEqual(app.jobs[jobs[0]['id']]['status'],'failed')
            self.assertEqual(app.jobs[jobs[2]['id']]['status'],'cancelled')
            self.assertEqual(app.jobs[jobs[3]['id']]['status'],'completed')
