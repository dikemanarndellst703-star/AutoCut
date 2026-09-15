import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from model_downloads import ModelDownloads, cached_model


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.manager = ModelDownloads(self.root)

    def tearDown(self):
        self.manager.close()
        self.tmp.cleanup()

    def test_partial_cache_is_not_downloaded(self):
        repo = self.root / 'models--Systran--faster-whisper-base'
        blobs = repo / 'blobs'
        blobs.mkdir(parents=True)
        (blobs / 'weights.incomplete').write_bytes(b'partial')
        status = next(x for x in self.manager.status() if x['model'] == 'base')
        self.assertEqual(status['status'], 'missing')
        self.assertFalse(status['ready'])
        self.assertEqual(status['bytes'], 7)

    def test_complete_snapshot_and_no_duplicate_download(self):
        folder = self.root / 'models--Systran--faster-whisper-base' / 'snapshots' / 'test'
        folder.mkdir(parents=True)
        for name in ('config.json', 'tokenizer.json'):
            (folder / name).write_text(json.dumps({'ok': True}))
        (folder / 'model.bin').write_bytes(b'test model')
        (folder / 'vocabulary.txt').write_text('word')
        self.assertEqual(cached_model(self.root, 'base'), folder)
        with patch('model_downloads.threading.Thread') as thread:
            self.assertEqual(self.manager.start('base')['status'], 'downloaded')
            thread.assert_not_called()

    def test_queue_cancel_retry_and_invalid_model(self):
        with patch('model_downloads.threading.Thread') as thread:
            self.manager.start('tiny')
            self.manager.start('tiny')
            self.assertEqual(thread.call_count, 1)
            self.manager.cancel('tiny')
            self.assertEqual(self.manager.tasks['tiny']['status'], 'cancelled')
            self.manager.start('tiny')
            self.assertEqual(thread.call_count, 2)
        with self.assertRaises(ValueError):
            self.manager.start('../arbitrary')

    def test_failed_download_reports_error(self):
        task = {'model': 'small', 'status': 'queued', 'cancel': False}
        self.manager.tasks['small'] = task
        with patch('model_downloads.subprocess.Popen', side_effect=OSError('network unavailable')):
            self.manager._download('small', task)
        state = next(x for x in self.manager.status() if x['model'] == 'small')
        self.assertEqual(state['status'], 'failed')
        self.assertIn('network unavailable', state['error'])


if __name__ == '__main__':
    unittest.main()
