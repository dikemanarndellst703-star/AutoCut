"""Read-only smoke test against a running local preview service."""
import json
import urllib.request
from urllib.error import HTTPError

BASE = 'http://127.0.0.1:8765'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def get(path, headers=None):
    return opener.open(urllib.request.Request(BASE + path, headers=headers or {}), timeout=30)

files = json.load(get('/api/library'))
assert files, 'Start with a media directory containing at least one video'
ident = files[0]['id']
meta = json.load(get('/api/media/' + ident))
assert meta['duration'] > 0
for value, length in [('bytes=0-1023', 1024), ('bytes=1024-2047', 1024), ('bytes=-128', 128)]:
    response = get('/media/' + ident, {'Range': value})
    assert response.status == 206 and len(response.read()) == length
for path, code, headers in [
    ('/media/missing', 400, {}),
    ('/../server.py', 404, {}),
    ('/api/library', 403, {'Host': 'untrusted.example'}),
    ('/media/' + ident, 416, {'Range': 'bytes=999999999999-'}),
]:
    try:
        get(path, headers)
        raise AssertionError(path)
    except HTTPError as error:
        assert error.code == code, (error.code, code)
assert b'AutoCut' in get('/').read()
models = json.load(get('/api/status'))['models']
assert all(row['progress'] is None or 0 <= row['progress'] <= 100 for row in models)
assert all(row['progress'] == 100 for row in models if row['ready'])
print(f'PASS: {len(files)} media files, metadata, video ranges, invalid ranges, path and host checks, homepage.')
