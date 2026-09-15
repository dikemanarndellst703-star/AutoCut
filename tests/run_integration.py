"""Opt-in real ASR + HTTP + export check on a short local sample.
Run with an existing <=60s MP4: .venv/bin/python tests/run_integration.py sample.mp4
Uses an isolated temporary project, the existing cached high-accuracy model, and port 8770.
"""
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workbench import probe
from core import validate_segments


def main():
    source = Path(sys.argv[1]).resolve()
    assert source.is_file() and probe(source)['duration'] <= 60, 'Use a short sample'
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        media = work / 'media'
        media.mkdir()
        (media / source.name).symlink_to(source)
        data = work / 'data'
        data.mkdir()
        (data / 'models').symlink_to(ROOT / 'data' / 'models', target_is_directory=True)
        process = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--media-dir', str(media), '--data-dir', str(data), '--port', '8770'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        token = ''

        def request(path, body=None, use_token=True):
            headers = {'Content-Type': 'application/json'}
            if use_token:
                headers['X-Workbench-Token'] = token
            raw = json.dumps(body).encode() if body is not None else None
            response = client.open(urllib.request.Request('http://127.0.0.1:8770' + path, data=raw, headers=headers), timeout=30)
            return json.load(response)

        def wait(job_id):
            end = time.monotonic() + 240
            while time.monotonic() < end:
                job = next(j for j in request('/api/status')['jobs'] if j['id'] == job_id)
                if job['status'] not in ('queued', 'running'):
                    assert job['status'] == 'completed', job
                    return job
                time.sleep(.5)
            raise AssertionError('Timed out')

        try:
            for _ in range(60):
                try:
                    status = request('/api/status')
                    break
                except URLError:
                    time.sleep(.1)
            token = status['token']
            media_id = request('/api/library')[0]['id']
            original = request('/api/media/' + media_id)
            try:
                request('/api/analyze', {'ids': [media_id]}, False)
                raise AssertionError('Unauthenticated mutation was accepted')
            except HTTPError as error:
                assert error.code == 403
            job_id = request('/api/analyze', {'ids': [media_id]})['jobs'][0]['id']
            assert request('/api/analyze', {'ids': [media_id]})['jobs'] == []
            try:
                request('/api/save/' + media_id, {'revision': original['revision'], 'segments': original['segments']})
                raise AssertionError('Editing a running analysis was accepted')
            except HTTPError as error:
                assert error.code == 409
            wait(job_id)
            project = request('/api/media/' + media_id)
            validate_segments(project['segments'], project['duration'])
            spoken = [s for s in project['segments'] if s['text']]
            assert spoken and project['analyzed'], 'No actual speech was recognized'
            assert any(not s['text'] for s in project['segments']), 'Sample should contain a gap'
            export_id = request('/api/export', {'ids': [media_id], 'mode': 'kept'})['jobs'][0]['id']
            exported = wait(export_id)
            assert len(exported['downloads']) == 6
            link = next(x for x in exported['downloads'] if x['name'] == 'video.mp4')['url']
            video = work / 'result.mp4'
            with client.open('http://127.0.0.1:8770' + link) as response:
                video.write_bytes(response.read())
            assert abs(probe(video)['duration'] - project['duration']) < .2
            subprocess.run(['ffmpeg', '-v', 'error', '-i', str(video), '-f', 'null', '-'], check=True, capture_output=True)
            print(f'PASS: real ASR ({len(spoken)} text segments), full timeline, authorization, duplicate prevention, edit locking, HTTP export, 6 downloads and MP4 decode.', flush=True)
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
