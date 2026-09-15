import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from workbench import Workbench, Conflict, probe
from core import piece


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Requires FFmpeg')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.media = self.root / 'media'
        self.media.mkdir()
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=25:duration=6', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(self.media / 'test.mp4')], check=True)
        self.app = Workbench(self.media, self.root / 'data')
        self.ident = self.app.library()[0]['id']

    def tearDown(self):
        self.tmp.cleanup()

    def wait_for_job(self, ident):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            job = self.app.jobs[ident]
            if job['status'] not in ('queued', 'running'):
                return job
            time.sleep(.1)
        self.fail('Task timeout')

    def test_persist_revision_and_export(self):
        p = self.app.project(self.ident)
        rows = [piece(0, 1, 'first', 'know'), piece(1, 3, '', 'other'), piece(3, 6, 'last', 'do')]
        rows[1]['keep'] = False
        saved = self.app.save(self.ident, {'revision': 0, 'segments': rows})
        self.assertEqual(saved['revision'], 1)
        with self.assertRaises(Conflict):
            self.app.save(self.ident, {'revision': 0, 'segments': rows})
        fresh = Workbench(self.media, self.root / 'data')
        self.assertEqual(fresh.project(self.ident)['segments'], saved['segments'])
        job = self.app.enqueue([self.ident], 'export')[0]
        finished = self.wait_for_job(job['id'])
        self.assertEqual(finished['status'], 'completed', finished.get('error'))
        folder = self.root / 'data' / 'exports' / job['id']
        actual = probe(folder / 'video.mp4')['duration']
        self.assertLess(abs(actual - 4), .16)
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(folder / 'video.mp4'), '-f', 'null', '-'], check=True, capture_output=True)
        self.assertIn('00:00:01,000 --> 00:00:04,000', (folder / 'subtitles.srt').read_text())
        self.assertEqual(json.loads((folder / 'cuts.json').read_text())['ranges'], [[0, 1.0], [3.0, 6.0]])

    def test_many_cuts_do_not_accumulate_encoder_padding(self):
        rows = [piece(i * .4, (i + 1) * .4, category='do') for i in range(15)]
        for i, row in enumerate(rows):
            row['keep'] = i % 2 == 0
        self.app.save(self.ident, {'revision': 0, 'segments': rows})
        job = self.app.enqueue([self.ident], 'export')[0]
        done = self.wait_for_job(job['id'])
        self.assertEqual(done['status'], 'completed', done.get('error'))
        actual = probe(self.root / 'data' / 'exports' / job['id'] / 'video.mp4')['duration']
        self.assertLess(abs(actual - 3.2), .2)

    def test_invalid_batch_does_not_partially_enqueue(self):
        with self.assertRaises(ValueError):
            self.app.enqueue([self.ident, 'missing'], 'export')
        self.assertFalse(self.app.jobs)

    def test_no_audio_is_covered(self):
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=red:size=160x90:duration=2', '-c:v', 'libx264', str(self.media / 'silent.mp4')], check=True)
        item = next(f for f in self.app.scan() if f['name'] == 'silent.mp4')
        job = self.app.enqueue([item['id']], 'analyze')[0]
        self.assertEqual(job['model'], 'large-v3')
        self.assertEqual(self.wait_for_job(job['id'])['status'], 'completed')
        project = self.app.project(item['id'])
        self.assertTrue(project['analyzed'])
        self.assertTrue(all(not s['text'] and s['keep'] for s in project['segments']))
        retry = self.app.enqueue([item['id']], 'analyze', model='base')[0]
        self.assertEqual(retry['model'], 'base')
        self.assertEqual(self.wait_for_job(retry['id'])['status'], 'completed')
        self.assertEqual(self.app.project(item['id'])['model'], 'base')
        with self.assertRaises(ValueError):
            self.app.enqueue([item['id']], 'analyze', model='unknown')


if __name__ == '__main__':
    unittest.main()
