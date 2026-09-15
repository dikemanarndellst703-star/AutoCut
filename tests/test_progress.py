import json
import os
import tempfile
import unittest
from pathlib import Path
from download_progress import progress_bytes, manifest_path


class ByteProgressTests(unittest.TestCase):
    def test_exact_percent_and_no_double_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = manifest_path(root, 'tiny')
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'revision':'rev','files':[{'name':'model.bin','size':100,'etag':'abc'}]}))
            blobs = root / 'models--Systran--faster-whisper-tiny' / 'blobs'
            blobs.mkdir(parents=True)
            partial = blobs / 'abc.worker.incomplete'
            partial.write_bytes(b'x' * 50)
            self.assertEqual(progress_bytes(root, 'tiny'), {'bytes':50,'total_bytes':100,'progress':50})
            partial.write_bytes(b'x' * 51)
            self.assertEqual(progress_bytes(root, 'tiny')['progress'], 51)
            stale = blobs / 'abc.old.incomplete'
            stale.write_bytes(b'x' * 90)
            os.utime(stale, (1, 1))
            self.assertEqual(progress_bytes(root, 'tiny')['progress'], 51)
            (blobs / 'abc').write_bytes(b'x' * 100)
            (blobs / 'unrelated').write_bytes(b'x' * 1000)
            self.assertEqual(progress_bytes(root, 'tiny')['bytes'], 100)
            self.assertEqual(progress_bytes(root, 'tiny')['progress'], 100)

    def test_missing_size_is_unknown_not_fake_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(progress_bytes(Path(tmp), 'base'))


if __name__ == '__main__':
    unittest.main()
