"""Loopback-only HTTP service for the local video workbench."""
import argparse
import json
import mimetypes
import re
import secrets
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from workbench import Conflict, EXTENSIONS, Workbench, probe

WEB = Path(__file__).parent / 'web'


class Handler(BaseHTTPRequestHandler):
    def valid_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.valid_host():
            self.send_error(403)
            return
        route = urlparse(self.path).path
        app = self.server.app
        try:
            if route == '/api/library':
                self.json_response(app.library())
            elif route == '/api/status':
                self.json_response(app.status())
            elif route.startswith('/api/media/'):
                self.json_response(app.project(route.rsplit('/', 1)[-1]))
            elif route.startswith('/media/'):
                self.send_file(app.path(route.rsplit('/', 1)[-1]))
            elif route.startswith('/project/'):
                project = app.project(route.rsplit('/', 1)[-1])
                self.json_response(project, filename=project['name'] + '.project.json')
            elif route.startswith('/transcript/'):
                from core import subtitles
                project = app.project(route.rsplit('/', 1)[-1])
                self.bytes_response(subtitles(project['segments']).encode('utf-8'), 'application/x-subrip; charset=utf-8', project['name'] + '.srt')
            elif route.startswith('/downloads/'):
                match = re.fullmatch(r'/downloads/([a-f0-9]{32})/([a-z0-9.-]+)', route)
                if not match:
                    self.send_error(404)
                    return
                job_id, filename = match.groups()
                with app.lock:
                    job = app.jobs.get(job_id, {})
                    valid = job.get('status') == 'completed' and any(x['name'] == filename for x in job.get('downloads', []))
                if not valid:
                    self.send_error(404)
                    return
                self.send_file(app.data / 'exports' / job_id / filename, download=filename)
            elif route in {'/', '/app.js', '/style.css', '/focus.css'}:
                self.send_file(WEB / ('index.html' if route == '/' else route[1:]))
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Conflict as error:
            self.json_response({'error': str(error)}, 409)
        except (ValueError, KeyError) as error:
            self.json_response({'error': str(error)}, 400)
        except Exception as error:
            self.json_response({'error': str(error)}, 500)

    def authorized_write(self):
        if not self.valid_host() or not secrets.compare_digest(self.headers.get('X-Workbench-Token', ''), self.server.app.token):
            self.json_response({'error': '页面会话已失效，请刷新后重试'}, 403)
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}:
            self.json_response({'error': '仅允许本地工作台操作'}, 403)
            return False
        return True

    def do_POST(self):
        if not self.authorized_write():
            return
        route = urlparse(self.path).path
        app = self.server.app
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length <= 0 or length > 32 * 1024 * 1024:
                raise ValueError('请求数据大小无效')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('请求数据必须为对象')
            if route == '/api/text-export':
                data, mime, name = app.text_export(body.get('ids'), body.get('format', 'original-srt'))
                self.bytes_response(data, mime, name)
            elif route == '/api/settings':
                self.json_response(app.set_concurrency(body.get('analysis_concurrency'), body.get('cloud_concurrency'),
                                                       body.get('export_concurrency')))
            elif route == '/api/models/download':
                self.json_response(app.models.start(body.get('model')), 202)
            elif route == '/api/models/cancel':
                app.models.cancel(body.get('model'))
                self.json_response({'ok': True})
            elif route == '/api/analyze':
                self.json_response({'jobs': app.enqueue(body.get('ids'), 'analyze', model=body.get('model', 'large-v3'), engine=body.get('engine', 'local'))}, 202)
            elif route == '/api/export':
                self.json_response({'jobs': app.enqueue(body.get('ids'), 'export', mode=body.get('mode', 'kept'))}, 202)
            elif route.startswith('/api/save/'):
                self.json_response(app.save(route.rsplit('/', 1)[-1], body))
            elif route.startswith('/api/cancel/'):
                app.cancel(route.rsplit('/', 1)[-1])
                self.json_response({'ok': True})
            elif route == '/api/rules':
                self.json_response(app.update_rules(body))
            elif route == '/api/scan':
                self.json_response(app.scan())
            else:
                self.send_error(404)
        except Conflict as error:
            self.json_response({'error': str(error)}, 409)
        except (ValueError, TypeError, KeyError) as error:
            self.json_response({'error': str(error)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.json_response({'error': str(error)}, 500)

    def do_PUT(self):
        if not self.authorized_write():
            return
        parsed = urlparse(self.path)
        if parsed.path != '/api/upload':
            self.send_error(404)
            return
        destination = None
        try:
            filename = parse_qs(parsed.query).get('name', ['video.mp4'])[0]
            filename = re.sub(r'[\x00-\x1f/\\:*?"<>|]', '_', filename).strip(' .')[:180]
            if Path(filename).suffix.lower() not in EXTENSIONS:
                raise ValueError('请选择支持的视频或音频文件')
            length = int(self.headers.get('Content-Length', 0))
            if length <= 0 or length > 50 * 1024 ** 3:
                raise ValueError('单个媒体文件须小于 50 GB')
            destination = self.server.app.data / 'uploads' / f'{uuid.uuid4().hex}__{filename}'
            self.connection.settimeout(180)
            with destination.open('xb') as stream:
                remaining = length
                while remaining:
                    block = self.rfile.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError('导入中断，请重试')
                    stream.write(block)
                    remaining -= len(block)
            probe(destination)
            library = self.server.app.scan()
            ident = next(k for k, p in self.server.app.media.items() if p == destination)
            self.json_response({'id': ident, 'library': library}, 201)
        except Exception as error:
            if destination:
                destination.unlink(missing_ok=True)
            self.json_response({'error': str(error)}, 400)

    def bytes_response(self, data, mime, filename=None, status=200):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if filename:
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(filename))
        self.end_headers()
        self.wfile.write(data)

    def json_response(self, value, status=200, filename=None):
        self.bytes_response(json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8', filename, status)

    def send_file(self, path, download=None):
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        header = self.headers.get('Range')
        if header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
            if match and any(match.groups()):
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), size - 1) if right else size - 1
                else:
                    start = max(0, size - int(right))
                status = 206
            else:
                start = size
            if start > end or start >= size:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
        self.send_response(status)
        self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(max(0, end - start + 1)))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if path.parent == WEB:
            self.send_header('Cache-Control', 'no-cache')
        if download:
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(download))
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        with path.open('rb') as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                block = stream.read(min(256 * 1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--media-dir', type=Path, default=Path(__file__).parent / 'data' / 'uploads')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).parent / 'data')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.app = Workbench(args.media_dir, args.data_dir)
    print(f'AutoCut · http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        with server.app.lock:
            for job_id in server.app.jobs:
                server.app.cancel(job_id)
    finally:
        server.app.models.close()
        server.server_close()


if __name__ == '__main__':
    main()
