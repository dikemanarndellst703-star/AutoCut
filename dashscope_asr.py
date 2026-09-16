"""DashScope paraformer-v2 long-form transcription through temporary OSS objects.

Credentials are read from environment variables only. This module never returns,
persists, or logs credentials and signed URLs.
"""
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = 'https://dashscope.aliyuncs.com/api/v1'
MODEL = 'paraformer-v2'
SIGNED_URL_TTL = 72 * 60 * 60
REQUIRED_ENV = (
    'DASHSCOPE_API_KEY',
    'OSS_ACCESS_KEY_ID',
    'OSS_ACCESS_KEY_SECRET',
    'OSS_ENDPOINT',
    'OSS_BUCKET',
)


class DashScopeError(RuntimeError):
    pass


def cloud_status(environ=None):
    env = os.environ if environ is None else environ
    missing = [name for name in REQUIRED_ENV if not str(env.get(name, '')).strip()]
    configured_model = str(env.get('DASHSCOPE_ASR_MODEL', MODEL)).strip()
    error = None
    if configured_model != MODEL:
        error = f'DASHSCOPE_ASR_MODEL 必须为 {MODEL}'
    return {
        'configured': not missing and error is None,
        'model': MODEL,
        'missing': missing,
        'error': error,
    }


def _positive_number(env, name, default, minimum):
    raw = str(env.get(name, default)).strip()
    try:
        value = float(raw)
    except ValueError as error:
        raise DashScopeError(f'{name} 必须是数字') from error
    if value < minimum:
        raise DashScopeError(f'{name} 必须不小于 {minimum}')
    return value


def _redact(value, secrets=()):
    text = str(value or '')
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[已隐藏]')
    text = re.sub(r'https?://[^\s"\']+[?&][^\s"\']+', '[已隐藏的签名URL]', text)
    return text[:1200]


class DashScopeASR:
    def __init__(self, environ=None, opener=urlopen):
        env = os.environ if environ is None else environ
        status = cloud_status(env)
        if status['missing']:
            raise DashScopeError('缺少云端转录环境变量：' + '、'.join(status['missing']))
        if status['error']:
            raise DashScopeError(status['error'])
        self.api_key = str(env['DASHSCOPE_API_KEY']).strip()
        self.base_url = str(env.get('DASHSCOPE_BASE_URL', DEFAULT_BASE_URL)).strip().rstrip('/')
        if not self.base_url.startswith('https://'):
            raise DashScopeError('DASHSCOPE_BASE_URL 必须使用 HTTPS')
        self.oss_id = str(env['OSS_ACCESS_KEY_ID']).strip()
        self.oss_secret = str(env['OSS_ACCESS_KEY_SECRET']).strip()
        self.oss_endpoint = str(env['OSS_ENDPOINT']).strip()
        self.oss_bucket = str(env['OSS_BUCKET']).strip()
        self.poll_interval = _positive_number(env, 'DASHSCOPE_ASR_POLL_INTERVAL', 3, .2)
        self.timeout = _positive_number(env, 'DASHSCOPE_ASR_TIMEOUT', 600, 10)
        self.opener = opener

    @property
    def _secrets(self):
        return self.api_key, self.oss_id, self.oss_secret

    def _json_request(self, method, url, payload=None, authorization=True, timeout=60, extra_headers=None):
        headers = {'Accept': 'application/json'}
        if authorization:
            headers['Authorization'] = f'Bearer {self.api_key}'
        data = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        if extra_headers:
            headers.update(extra_headers)
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=timeout) as response:
                body = response.read()
        except HTTPError as error:
            detail = error.read(4096).decode('utf-8', 'replace')
            try:
                decoded = json.loads(detail)
                detail = decoded.get('message') or decoded.get('code') or detail
            except (ValueError, TypeError):
                pass
            raise DashScopeError(f'DashScope HTTP {error.code}：{_redact(detail, self._secrets)}') from error
        except (URLError, TimeoutError, OSError) as error:
            raise DashScopeError('无法连接 DashScope：' + _redact(error, self._secrets)) from error
        try:
            return json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as error:
            raise DashScopeError('DashScope 返回了无法解析的 JSON') from error

    @staticmethod
    def _sleep(seconds, cancelled):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            cancelled()
            time.sleep(min(.25, deadline - time.monotonic()))

    @staticmethod
    def _task_error(output, response):
        code = output.get('code') or response.get('code') or 'UNKNOWN'
        message = output.get('message') or response.get('message') or '任务失败'
        return f'DashScope 转写失败（{code}）：{message}'

    @staticmethod
    def parse_transcription(data, video, duration):
        transcripts = data.get('transcripts')
        if transcripts is None and isinstance(data.get('output'), dict):
            transcripts = data['output'].get('transcripts')
        if not isinstance(transcripts, list):
            raise DashScopeError('DashScope 结果缺少 transcripts 列表')
        segments = []
        for transcript in transcripts:
            sentences = transcript.get('sentences', []) if isinstance(transcript, dict) else []
            if not isinstance(sentences, list):
                continue
            for sentence in sentences:
                if not isinstance(sentence, dict):
                    continue
                begin, end = sentence.get('begin_time'), sentence.get('end_time')
                text = str(sentence.get('text', '')).strip()
                if isinstance(begin, (int, float)) and not isinstance(begin, bool) and isinstance(end, (int, float)) and not isinstance(end, bool) and end > begin and text:
                    segments.append({'start': begin / 1000.0, 'end': end / 1000.0, 'text': text})
        segments.sort(key=lambda row: (row['start'], row['end']))
        if not segments:
            raise DashScopeError('DashScope 转写成功，但返回了空字幕')
        return {
            'video': video,
            'duration': float(duration),
            'language': 'zh',
            'model': MODEL,
            'segments': segments,
        }

    def transcribe(self, path, video, duration, progress, cancelled):
        try:
            import oss2
        except ImportError as error:
            raise DashScopeError('OSS 组件未安装，请重新运行启动脚本安装 requirements.txt') from error
        path = Path(path)
        suffix = path.suffix.lower()[:12]
        date = datetime.now(timezone.utc).strftime('%Y/%m/%d')
        object_key = f'autocut-asr/{date}/{uuid.uuid4().hex}{suffix}'
        auth = oss2.Auth(self.oss_id, self.oss_secret)
        bucket = oss2.Bucket(auth, self.oss_endpoint, self.oss_bucket)
        uploaded = False
        try:
            progress('正在上传媒体到 OSS', 0)
            total_size = max(1, path.stat().st_size)

            def upload_progress(consumed, total):
                cancelled()
                size = total or total_size
                progress('正在上传媒体到 OSS', min(20, int(consumed * 20 / max(1, size))))

            try:
                bucket.put_object_from_file(object_key, str(path), progress_callback=upload_progress)
                uploaded = True
                signed_url = bucket.sign_url('GET', object_key, SIGNED_URL_TTL, slash_safe=True)
            except Exception as error:
                raise DashScopeError('OSS 上传或签名失败：' + _redact(error, self._secrets)) from error

            cancelled()
            progress('正在提交 DashScope 转写任务', 22)
            response = self._json_request(
                'POST',
                self.base_url + '/services/audio/asr/transcription',
                {
                    'model': MODEL,
                    'input': {'file_urls': [signed_url]},
                    'parameters': {
                        'language_hints': ['zh'],
                        'enable_itn': True,
                        'enable_punctuation': True,
                        'enable_speaker_info': False,
                    },
                },
                extra_headers={'X-DashScope-Async': 'enable'},
            )
            output = response.get('output') if isinstance(response.get('output'), dict) else {}
            task_id = output.get('task_id') or response.get('task_id')
            if not task_id:
                raise DashScopeError('DashScope 提交成功，但响应中没有 task_id')

            started = time.monotonic()
            while True:
                cancelled()
                elapsed = time.monotonic() - started
                if elapsed > self.timeout:
                    raise DashScopeError(f'DashScope 转写超时（超过 {self.timeout:g} 秒）')
                response = self._json_request('GET', self.base_url + '/tasks/' + str(task_id), timeout=min(60, self.poll_interval + 30))
                output = response.get('output') if isinstance(response.get('output'), dict) else {}
                status = str(output.get('task_status') or response.get('task_status') or '').upper()
                if status == 'SUCCEEDED':
                    break
                if status == 'FAILED':
                    raise DashScopeError(self._task_error(output, response))
                if status not in ('PENDING', 'RUNNING'):
                    raise DashScopeError('DashScope 返回未知任务状态：' + (status or '缺失'))
                estimated = min(90, 25 + int(elapsed / self.timeout * 65))
                progress('DashScope 云端转写中', estimated)
                self._sleep(self.poll_interval, cancelled)

            results = output.get('results')
            if not isinstance(results, list) or not results:
                raise DashScopeError('DashScope 完成任务，但响应中没有 results')
            successful = next((row for row in results if isinstance(row, dict) and str(row.get('subtask_status', '')).upper() == 'SUCCEEDED' and row.get('transcription_url')), None)
            if successful is None:
                failed = next((row for row in results if isinstance(row, dict)), {})
                raise DashScopeError(self._task_error(failed, response))
            progress('正在下载云端字幕', 92)
            transcription = self._json_request('GET', successful['transcription_url'], authorization=False)
            unified = self.parse_transcription(transcription, video, duration)
            progress('云端字幕已下载', 96)
            return unified
        finally:
            if uploaded:
                try:
                    bucket.delete_object(object_key)
                except Exception:
                    pass
