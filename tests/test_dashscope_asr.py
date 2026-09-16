import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from dashscope_asr import DashScopeASR, DashScopeError, MODEL, SIGNED_URL_TTL, cloud_status


ENV = {
    'DASHSCOPE_API_KEY': 'test-dashscope-secret',
    'DASHSCOPE_BASE_URL': 'https://dashscope.example/api/v1',
    'DASHSCOPE_ASR_MODEL': 'paraformer-v2',
    'OSS_ACCESS_KEY_ID': 'test-oss-id',
    'OSS_ACCESS_KEY_SECRET': 'test-oss-secret',
    'OSS_ENDPOINT': 'https://oss.example',
    'OSS_BUCKET': 'test-bucket',
    'DASHSCOPE_ASR_POLL_INTERVAL': '0.2',
    'DASHSCOPE_ASR_TIMEOUT': '10',
}


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class DashScopeTests(unittest.TestCase):
    def test_status_reports_names_only_and_fixed_model(self):
        status = cloud_status({})
        self.assertFalse(status['configured'])
        self.assertIn('DASHSCOPE_API_KEY', status['missing'])
        self.assertNotIn('test', json.dumps(status))
        invalid = dict(ENV, DASHSCOPE_ASR_MODEL='other')
        self.assertFalse(cloud_status(invalid)['configured'])

    def test_parse_preserves_milliseconds_and_exact_schema(self):
        result = DashScopeASR.parse_transcription({
            'transcripts': [{'sentences': [
                {'begin_time': 1234, 'end_time': 2501, 'text': '第一句。'},
                {'begin_time': 2501, 'end_time': 4009, 'text': '第二句。'},
            ]}],
        }, 'lesson.mp4', 4.009)
        self.assertEqual(list(result), ['video', 'duration', 'language', 'model', 'segments'])
        self.assertEqual(result['model'], MODEL)
        self.assertEqual(result['segments'][0], {'start': 1.234, 'end': 2.501, 'text': '第一句。'})
        self.assertEqual(result['segments'][1]['end'], 4.009)

    def test_empty_subtitles_have_clear_error(self):
        with self.assertRaisesRegex(DashScopeError, '空字幕'):
            DashScopeASR.parse_transcription({'transcripts': []}, 'empty.wav', 1)

    def test_full_flow_uses_required_payload_and_cleans_oss(self):
        calls = []
        responses = iter([
            {'output': {'task_id': 'task-1'}},
            {'output': {'task_status': 'RUNNING'}},
            {'output': {'task_status': 'SUCCEEDED', 'results': [
                {'subtask_status': 'SUCCEEDED', 'transcription_url': 'https://result.example/result.json'}
            ]}},
            {'transcripts': [{'sentences': [
                {'begin_time': 7, 'end_time': 1019, 'text': '测试。'}
            ]}]},
        ])

        def opener(request, timeout=0):
            body = json.loads(request.data) if request.data else None
            calls.append((request.get_method(), request.full_url, dict(request.header_items()), body))
            return Response(next(responses))

        bucket_state = {}

        class Bucket:
            def __init__(self, auth, endpoint, bucket):
                bucket_state.update(endpoint=endpoint, bucket=bucket)

            def put_object_from_file(self, key, path, progress_callback=None):
                bucket_state['key'] = key
                progress_callback(Path(path).stat().st_size, Path(path).stat().st_size)

            def sign_url(self, method, key, expires, slash_safe=False):
                bucket_state.update(method=method, expires=expires, slash_safe=slash_safe)
                return 'https://oss.example/signed?token=secret'

            def delete_object(self, key):
                bucket_state['deleted'] = key

        fake_oss = types.SimpleNamespace(Auth=lambda key, secret: object(), Bucket=Bucket)
        progress = []
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'sample.mp3'
            media.write_bytes(b'audio')
            with patch.dict(sys.modules, {'oss2': fake_oss}):
                result = DashScopeASR(ENV, opener=opener).transcribe(
                    media, media.name, 1.019, lambda stage, percent: progress.append((stage, percent)), lambda: None
                )

        self.assertEqual(bucket_state['expires'], SIGNED_URL_TTL)
        self.assertEqual(bucket_state['deleted'], bucket_state['key'])
        method, url, headers, body = calls[0]
        self.assertEqual((method, url), ('POST', 'https://dashscope.example/api/v1/services/audio/asr/transcription'))
        self.assertEqual(headers['Authorization'], 'Bearer test-dashscope-secret')
        self.assertEqual(headers['X-dashscope-async'], 'enable')
        self.assertEqual(body, {
            'model': 'paraformer-v2',
            'input': {'file_urls': ['https://oss.example/signed?token=secret']},
            'parameters': {
                'language_hints': ['zh'],
                'enable_itn': True,
                'enable_punctuation': True,
                'enable_speaker_info': False,
            },
        })
        self.assertEqual(result['segments'], [{'start': .007, 'end': 1.019, 'text': '测试。'}])
        self.assertTrue(progress)


if __name__ == '__main__':
    unittest.main()
