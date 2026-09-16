"""Persistent local projects, bounded concurrent job queues, transcription and rendering."""
import copy
import hashlib
import io
import zipfile
import importlib.util
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from model_downloads import ModelDownloads
from core import DEFAULT_RULES, blank_timeline, build_timeline, export_ranges, subtitles, validate_segments
from dashscope_asr import DashScopeASR, MODEL as DASHSCOPE_MODEL, cloud_status

ROOT = Path(__file__).resolve().parent
EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi', '.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus'}
ANALYSIS_MODEL = 'large-v3'
ANALYSIS_MODELS = {'tiny', 'base', 'small', 'medium', 'large-v3'}


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def probe(path):
    process = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', str(path)], capture_output=True, text=True, timeout=45)
    if process.returncode:
        raise ValueError('无法读取视频：' + process.stderr[-800:])
    data = json.loads(process.stdout)
    duration = float(data['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('视频时长无效')
    has_video = any(s['codec_type'] == 'video' for s in data.get('streams', []))
    has_audio = any(s['codec_type'] == 'audio' for s in data.get('streams', []))
    if not has_video and not has_audio:
        raise ValueError('文件没有可处理的音频或视频轨道')
    return {'duration': duration, 'has_audio': has_audio, 'has_video': has_video}


class Conflict(ValueError):
    pass


class Cancelled(Exception):
    pass


class Workbench:
    def __init__(self, media_dir, data_dir):
        self.media_dir, self.data = Path(media_dir).resolve(), Path(data_dir).resolve()
        for name in ('projects', 'uploads', 'exports', 'models', 'backups', 'tmp'):
            (self.data / name).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.media = {}
        self.metadata = {}
        self.projects = {}
        self.jobs = {}
        self.processes = {}
        self.pending = {'analyze': queue.Queue(), 'cloud': queue.Queue(), 'export': queue.Queue()}
        self.concurrency = 2
        self.cloud_concurrency = 30
        settings = self.data / 'settings.json'
        if settings.exists():
            saved_settings = json.loads(settings.read_text('utf-8'))
            value = saved_settings.get('analysis_concurrency', 2)
            if type(value) is int and 1 <= value <= 3:
                self.concurrency = value
            cloud_value = saved_settings.get('cloud_concurrency', 30)
            if type(cloud_value) is int and 1 <= cloud_value <= 50:
                self.cloud_concurrency = cloud_value
        self.token = uuid.uuid4().hex
        self.models = ModelDownloads(self.data / "models")
        self.rules = copy.deepcopy(DEFAULT_RULES)
        rules_path = self.data / 'rules.json'
        if rules_path.exists():
            self.rules = self.validate_rules(json.loads(rules_path.read_text('utf-8')))
        jobs_path = self.data / 'jobs.json'
        if jobs_path.exists():
            self.jobs = json.loads(jobs_path.read_text('utf-8'))
            for job in self.jobs.values():
                if job['status'] in ('queued', 'running'):
                    job.update(status='failed', stage='服务重启导致任务中断，请重试', error='服务重启导致任务中断')
            self._save_jobs()
        self.scan()
        self.threads = []
        for pool in ('analyze', 'analyze', 'analyze', 'export'):
            thread = threading.Thread(target=self._worker, args=(pool,), daemon=True)
            thread.start()
            self.threads.append(thread)
        cloud_thread = threading.Thread(target=self._cloud_dispatcher, daemon=True)
        cloud_thread.start()
        self.threads.append(cloud_thread)
        self.thread = self.threads[0]

    def scan(self):
        with self.lock:
            for folder in (self.media_dir, self.data / 'uploads'):
                if folder.is_dir():
                    for p in sorted(folder.iterdir(), key=lambda p: [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)', p.name)]):
                        if p.is_file() and p.suffix.lower() in EXTENSIONS:
                            ident = hashlib.sha256(str(p.resolve()).encode()).hexdigest()[:16]
                            self.media[ident] = p.resolve()
            self.media = {k: p for k, p in self.media.items() if p.is_file()}
            return self.library()

    def library(self):
        with self.lock:
            return [{'id': key, 'name': self.display_name(path), 'size': path.stat().st_size,
                     'trimmed': '_trimmed' in path.stem,
                     'analyzed': self._project_file(key).exists() and self._read_project(key).get('analyzed', False)}
                    for key, path in self.media.items()]

    def display_name(self, path):
        return path.name.split('__', 1)[-1] if path.parent == self.data / 'uploads' else path.name

    def path(self, ident):
        with self.lock:
            if ident not in self.media:
                raise ValueError('视频不存在，请刷新素材库')
            return self.media[ident]

    def _project_file(self, ident):
        if not re.fullmatch(r'[0-9a-f]{16}', ident):
            raise ValueError('视频编号无效')
        return self.data / 'projects' / f'{ident}.json'

    def _read_project(self, ident):
        if ident not in self.projects:
            self.projects[ident] = json.loads(self._project_file(ident).read_text('utf-8'))
        saved = self.projects[ident]
        if saved.get('analyzed') and not saved.get('gap_category_policy'):
            atomic_json(self.data / 'backups' / f'{ident}-before-gap-category.json', saved)
            previous_category = 'know'
            for segment in saved['segments']:
                if not segment['text'].strip() and segment['category'] == 'unclassified' and not segment.get('reviewed'):
                    segment['category'] = previous_category
                    segment['reason'] = '无文字片段沿用前段分类；可在独立审查列表复核'
                previous_category = segment['category']
            saved['gap_category_policy'] = 1
            saved['revision'] += 1
            atomic_json(self._project_file(ident), saved)
        if saved.get('analyzed') and not saved.get('auto_keep_policy'):
            atomic_json(self.data / 'backups' / f'{ident}-before-auto-keep.json', saved)
            for segment in saved['segments']:
                if segment['category'] == 'other' and segment['text'].strip() and not segment.get('reviewed'):
                    segment['keep'] = False
            saved['auto_keep_policy'] = 1
            saved['revision'] += 1
            atomic_json(self._project_file(ident), saved)
        return self.projects[ident]

    def project(self, ident):
        with self.lock:
            path = self.path(ident)
            stat = path.stat()
            fingerprint = f'{stat.st_size}:{stat.st_mtime_ns}'
            if self._project_file(ident).exists():
                saved = self._read_project(ident)
                if saved.get('fingerprint') != fingerprint:
                    raise ValueError('源文件已变化。请以新文件名导入，避免将旧时间轴套用到新视频')
                return copy.deepcopy(saved)
            metadata = probe(path)
            self.metadata[ident] = metadata
            project = {'schema': 1, 'id': ident, 'name': self.display_name(path), 'fingerprint': fingerprint,
                       **metadata, 'analyzed': False, 'revision': 0, 'updated': time.time(),
                       'segments': blank_timeline(metadata['duration'])}
            self.projects[ident] = project
            atomic_json(self._project_file(ident), project)
            return copy.deepcopy(project)

    def active(self, ident, kind='analyze'):
        return any(j['media_id'] == ident and j['kind'] == kind and j['status'] in ('queued', 'running') for j in self.jobs.values())

    def save(self, ident, body):
        with self.lock:
            if self.active(ident):
                raise Conflict('此视频正在分析，请等待完成或取消后编辑')
            project = self.project(ident)
            if body.get('revision') != project['revision']:
                raise Conflict('工程已更新，请重新载入后编辑，避免覆盖其他窗口的修改')
            project['segments'] = validate_segments(body['segments'], project['duration'])
            project['revision'] += 1
            project['updated'] = time.time()
            self.projects[ident] = project
            atomic_json(self._project_file(ident), project)
            return copy.deepcopy(project)

    @staticmethod
    def validate_rules(rules):
        if not isinstance(rules, dict) or set(rules) != {'know', 'do', 'other'}:
            raise ValueError('规则必须包含知、行、无关三个类别')
        clean = {}
        for key, words in rules.items():
            if not isinstance(words, list) or len(words) > 200 or any(not isinstance(w, str) or not w.strip() or len(w) > 80 for w in words):
                raise ValueError('每类最多 200 个关键词，每个关键词 1–80 字')
            clean[key] = list(dict.fromkeys(w.strip() for w in words))
        return clean

    def update_rules(self, rules):
        with self.lock:
            self.rules = self.validate_rules(rules)
            atomic_json(self.data / 'rules.json', self.rules)
            return self.rules

    def _save_jobs(self):
        # Keep job history and download paths across restarts.
        atomic_json(self.data / 'jobs.json', self.jobs)

    def update_job(self, job_id, **values):
        with self.lock:
            if values.get('status') in ('completed', 'failed', 'cancelled'):
                values['finished'] = time.time()
            self.jobs[job_id].update(values, updated=time.time())
            self._save_jobs()

    def enqueue(self, ids, kind, mode='kept', model=ANALYSIS_MODEL, engine='local'):
        if kind not in ('analyze', 'export') or mode not in ('kept', 'know', 'do') or engine not in ('local', 'dashscope'):
            raise ValueError('任务参数无效')
        if kind == 'export' and engine != 'local':
            raise ValueError('导出任务参数无效')
        if kind == 'analyze' and engine == 'local' and model not in ANALYSIS_MODELS:
            raise ValueError('本地转录模型无效')
        if kind == 'analyze' and engine == 'dashscope':
            status = cloud_status()
            if status['missing']:
                raise ValueError('缺少云端转录环境变量：' + '、'.join(status['missing']))
            if status['error']:
                raise ValueError(status['error'])
            model = DASHSCOPE_MODEL
        if not isinstance(ids, list) or not ids or len(ids) > 200:
            raise ValueError('请选择 1–200 个视频')
        added = []
        with self.lock:
            prepared = []
            for ident in dict.fromkeys(ids):
                self.path(ident)
                if self.active(ident, kind):
                    continue
                project = self.project(ident)
                if kind == 'export':
                    if self.active(ident, 'analyze'):
                        raise Conflict('请等待分析完成后导出')
                    if not export_ranges(project['segments'], mode)[1]:
                        raise ValueError(f"{project['name']} 没有符合条件的保留片段")
                prepared.append((ident, project))
            if (kind == 'analyze' and engine == 'local'
                    and any(project.get('has_audio') for _, project in prepared)
                    and importlib.util.find_spec('faster_whisper') is None):
                raise ValueError('转写组件未安装。请使用启动脚本安装 requirements.txt 后重启服务')
            # Validate the entire batch before scheduling any side effects.
            for ident, project in prepared:
                job_id = uuid.uuid4().hex
                job = {'id': job_id, 'media_id': ident, 'name': project['name'], 'kind': kind,
                       'engine': engine, 'model': model, 'mode': mode, 'status': 'queued', 'progress': 0,
                       'stage': '等待处理', 'created': time.time(), 'updated': time.time(),
                       'cancel': False, 'error': None, 'downloads': []}
                self.jobs[job_id] = job
                # Export is based on a frozen saved project; later edits do not change it.
                snapshot = copy.deepcopy(project)
                pool = 'cloud' if kind == 'analyze' and engine == 'dashscope' else kind
                self.pending[pool].put((job_id, snapshot, copy.deepcopy(self.rules)))
                added.append(copy.deepcopy(job))
            self._save_jobs()
        return added

    def cancel(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError('任务不存在')
            job = self.jobs[job_id]
            if job['status'] not in ('queued', 'running'):
                return
            job['cancel'] = True
            if job['status'] == 'queued':
                job.update(status='cancelled', stage='已取消')
            else:
                job['stage'] = '正在取消…'
            process = self.processes.get(job_id)
            if process and process.poll() is None:
                process.terminate()
            self._save_jobs()

    def check_cancel(self, job_id):
        if self.jobs[job_id]['cancel']:
            raise Cancelled()

    def run_process(self, job_id, command, duration=None, offset=0, total=None):
        self.check_cancel(job_id)
        with tempfile.TemporaryFile() as log, tempfile.TemporaryDirectory(dir=self.data / 'tmp') as progress_dir:
            progress_file = Path(progress_dir) / 'ffmpeg-progress.txt'
            if duration:
                command = [command[0], '-progress', str(progress_file), '-stats_period', '0.5', *command[1:]]
            last_percent = -1
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=log)
            with self.lock:
                self.processes[job_id] = process
            try:
                while process.poll() is None:
                    if self.jobs[job_id]['cancel']:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        raise Cancelled()
                    if duration and progress_file.exists():
                        values = re.findall(r'out_time_us=(\d+)', progress_file.read_text('utf-8', errors='ignore'))
                        if values:
                            at = min(duration, int(values[-1]) / 1000000)
                            percent = min(99, int(100 * (offset + at) / (total or duration)))
                            if percent != last_percent:
                                self.update_job(job_id, progress=percent)
                                last_percent = percent
                    time.sleep(.15)
                self.check_cancel(job_id)
                if process.returncode:
                    log.seek(0)
                    raise RuntimeError(log.read().decode('utf-8', 'replace')[-1600:])
                if duration:
                    self.update_job(job_id, progress=min(100, int(100 * (offset + duration) / (total or duration))))
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
                with self.lock:
                    self.processes.pop(job_id, None)

    def set_concurrency(self, value, cloud_value=None):
        if type(value) is not int or not 1 <= value <= 3:
            raise ValueError('同时分析数量必须为 1、2 或 3')
        if cloud_value is None:
            cloud_value = self.cloud_concurrency
        if type(cloud_value) is not int or not 1 <= cloud_value <= 50:
            raise ValueError('云端同时分析数量必须为 1–50')
        with self.lock:
            atomic_json(self.data / 'settings.json', {'analysis_concurrency': value, 'cloud_concurrency': cloud_value})
            self.concurrency = value
            self.cloud_concurrency = cloud_value
        return {'analysis_concurrency': value, 'cloud_concurrency': cloud_value}

    def text_export(self, ids, format='original-srt'):
        if format not in ('original-srt', 'kept-srt', 'txt'):
            raise ValueError('文字导出类型无效')
        if not isinstance(ids, list) or not ids or len(ids) > 200:
            raise ValueError('请选择 1–200 个视频')
        with self.lock:
            projects = [self.project(i) for i in dict.fromkeys(ids)]
            invalid = [p['name'] for p in projects if not p.get('analyzed') or self.active(p['id'])]
            if invalid:
                raise ValueError('以下素材尚未分析完成，请取消选择后重试：' + '、'.join(invalid))
        files, used = [], set()
        suffix = {'original-srt': '_完整逐字稿.srt', 'kept-srt': '_成片字幕.srt', 'txt': '_文字稿.txt'}[format]
        for p in projects:
            rows = p['segments']
            if format == 'kept-srt':
                rows = export_ranges(rows)[0]
                if not rows:
                    raise ValueError(p['name'] + ' 没有保留片段')
            if format == 'txt':
                content = '\n'.join(s['text'] for s in rows if s['text'].strip())
            else:
                content = subtitles(rows, remap=format == 'kept-srt')
            name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', Path(p['name']).stem).strip('. ') or '视频'
            base, number = name, 1
            while (name + suffix).casefold() in used:
                number += 1
                name = f'{base}_{number}'
            used.add((name + suffix).casefold())
            files.append((name + suffix, content.encode('utf-8')))
        if len(files) == 1:
            return files[0][1], ('text/plain; charset=utf-8' if format == 'txt' else 'application/x-subrip; charset=utf-8'), files[0][0]
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in files:
                archive.writestr(name, data)
        return output.getvalue(), 'application/zip', 'AutoCut_文字导出.zip'

    def _worker(self, pool):
        pending = self.pending[pool]
        while True:
            item = None
            with self.lock:
                if pool == 'analyze':
                    running = sum(j['kind'] == 'analyze' and j.get('engine', 'local') == 'local' and j['status'] == 'running' for j in self.jobs.values())
                    limit = self.concurrency
                else:
                    running = sum(j['kind'] == 'export' and j['status'] == 'running' for j in self.jobs.values())
                    limit = 1
                if running < limit:
                    try:
                        item = pending.get_nowait()
                    except queue.Empty:
                        pass
                    if item and not self.jobs[item[0]]['cancel']:
                        self.update_job(item[0], status='running', started=time.time(), stage='正在准备')
            if item is None:
                time.sleep(.1)
                continue
            self._run_claimed(pool, item)

    def _cloud_dispatcher(self):
        pending = self.pending['cloud']
        while True:
            item = None
            with self.lock:
                running = sum(j['kind'] == 'analyze' and j.get('engine') == 'dashscope' and j['status'] == 'running' for j in self.jobs.values())
                if running < self.cloud_concurrency:
                    try:
                        item = pending.get_nowait()
                    except queue.Empty:
                        pass
                    if item and not self.jobs[item[0]]['cancel']:
                        self.update_job(item[0], status='running', started=time.time(), stage='正在准备云端任务')
            if item is None:
                time.sleep(.05)
                continue
            if self.jobs[item[0]]['cancel']:
                pending.task_done()
                continue
            threading.Thread(target=self._run_claimed, args=('cloud', item), daemon=True).start()

    def _run_claimed(self, pool, item):
        pending = self.pending[pool]
        job_id, project, rules = item
        try:
            self.check_cancel(job_id)
            self.project(project['id'])  # Validate source fingerprint again when a queued task starts.
            self.update_job(job_id, status='running', stage='正在准备')
            if self.jobs[job_id]['kind'] == 'analyze':
                self.analyze(job_id, project, rules)
            else:
                self.render(job_id, project)
            self.check_cancel(job_id)
            self.update_job(job_id, status='completed', progress=100, stage='处理完成')
        except Cancelled:
            self.update_job(job_id, status='cancelled', stage='已取消')
        except Exception as error:
            self.update_job(job_id, status='failed', stage='处理失败，可重试', error=str(error)[-2000:])
        finally:
            pending.task_done()

    def analyze(self, job_id, project, rules):
        raw = []
        unified = None
        if project['has_audio']:
            if self.jobs[job_id].get('engine') == 'dashscope':
                client = DashScopeASR()
                unified = client.transcribe(
                    self.path(project['id']), project['name'], project['duration'],
                    lambda stage, progress: self.update_job(job_id, stage=stage, progress=progress),
                    lambda: self.check_cancel(job_id),
                )
                raw = unified['segments']
            else:
                with tempfile.TemporaryDirectory(dir=self.data / 'tmp') as temporary:
                    audio = Path(temporary) / 'audio.wav'
                    self.update_job(job_id, stage='正在提取音轨', progress=0)
                    self.run_process(job_id, ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(self.path(project['id'])), '-vn', '-ac', '1', '-ar', '16000', str(audio)], duration=project['duration'])
                    command = [sys.executable, '-u', str(ROOT / 'transcribe_worker.py'), '--audio', str(audio), '--model', self.jobs[job_id]['model'], '--models', str(self.data / 'models')]
                    with tempfile.TemporaryFile() as log:
                        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf-8')
                        with self.lock:
                            self.processes[job_id] = process
                        done = False
                        last_update = 0
                        try:
                            for line in process.stdout:
                                self.check_cancel(job_id)
                                event = json.loads(line)
                                if 'stage' in event:
                                    self.update_job(job_id, stage=event['stage'], progress=event.get('progress'))
                                if 'segment' in event:
                                    raw.append(event['segment'])
                                    if time.monotonic() - last_update > 1:
                                        at = min(event['segment']['end'], project['duration'])
                                        self.update_job(job_id, progress=min(99, int(100 * at / project['duration'])), stage=f"正在转写 · 已到 {int(at)//60:02}:{int(at)%60:02}")
                                        last_update = time.monotonic()
                                done = done or event.get('done', False)
                            process.wait()
                            self.check_cancel(job_id)
                            if process.returncode or not done:
                                log.seek(0)
                                raise RuntimeError('转写失败：' + log.read().decode('utf-8', 'replace')[-1800:])
                        finally:
                            if process.poll() is None:
                                process.kill()
                            process.wait()
                            process.stdout.close()
                            with self.lock:
                                self.processes.pop(job_id, None)
        self.check_cancel(job_id)
        self.update_job(job_id, stage='正在分类并补齐无文字区间', progress=None)
        project['segments'] = build_timeline(raw, project['duration'], rules)
        project['auto_keep_policy'] = 1
        project['gap_category_policy'] = 1
        project.update(analyzed=True, model=self.jobs[job_id]['model'], engine=self.jobs[job_id].get('engine', 'local'), revision=project['revision'] + 1, updated=time.time())
        with self.lock:
            self.check_cancel(job_id)
            previous = self.project(project['id'])
            atomic_json(self.data / 'backups' / f"{project['id']}-{job_id}.json", previous)
            atomic_json(self._project_file(project['id']), project)
            self.projects[project['id']] = project
            if unified is not None:
                folder = self.data / 'exports' / job_id
                folder.mkdir(exist_ok=True)
                atomic_json(folder / 'transcription.json', unified)
                self.update_job(job_id, downloads=[{'name': 'transcription.json', 'url': f'/downloads/{job_id}/transcription.json'}])

    def render(self, job_id, project):
        job = self.jobs[job_id]
        if not project.get('has_video', True):
            raise ValueError('纯音频素材不能导出视频，可导出字幕或统一转写 JSON')
        selected, ranges = export_ranges(project['segments'], job['mode'])
        if not ranges:
            raise ValueError('没有可导出的片段')
        folder = self.data / 'exports' / job_id
        folder.mkdir()
        final = folder / 'video.mp4'
        try:
            with tempfile.TemporaryDirectory(dir=self.data / 'tmp') as temporary:
                temporary = Path(temporary)
                total = sum(b - a for a, b in ranges)
                completed = 0
                clip_paths = []
                for i, (start, end) in enumerate(ranges):
                    self.update_job(job_id, stage=f'正在剪辑第 {i+1}/{len(ranges)} 段', progress=int(completed / total * 100))
                    clip = temporary / f'clip-{i:05}.mp4'
                    # Decode/re-encode after an accurate seek; do not use keyframe-only stream copy for cuts.
                    command = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{start:.6f}', '-i', str(self.path(project['id'])), '-t', f'{end-start:.6f}', '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', str(clip)]
                    self.run_process(job_id, command, duration=end-start, offset=completed, total=total)
                    clip_paths.append(clip)
                    completed += end - start
                self.update_job(job_id, stage='正在合并输出文件', progress=0)
                listing = temporary / 'concat.txt'
                listing.write_text('\n'.join(f"file '{p.name}'" for p in clip_paths), encoding='utf-8')
                self.run_process(job_id, ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'concat', '-safe', '1', '-i', str(listing), '-c', 'copy', '-movflags', '+faststart', str(final)], duration=total)
            (folder / 'subtitles.srt').write_text(subtitles(selected, remap=True), encoding='utf-8')
            (folder / 'transcript-original.srt').write_text(subtitles(project['segments']), encoding='utf-8')
            (folder / 'transcript.txt').write_text('\n'.join(f"[{s['start']:.3f}–{s['end']:.3f}] {s['text'] or '[未识别到文字]'}" for s in project['segments']), encoding='utf-8')
            atomic_json(folder / 'project.json', project)
            atomic_json(folder / 'cuts.json', {'source': project['name'], 'mode': job['mode'], 'ranges': ranges, 'expected_duration': total, 'actual_duration': probe(final)['duration']})
            self.update_job(job_id, downloads=[{'name': name, 'url': f'/downloads/{job_id}/{name}'} for name in ['video.mp4', 'subtitles.srt', 'transcript-original.srt', 'transcript.txt', 'project.json', 'cuts.json']])
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def status(self):
        with self.lock:
            models = self.models.status()
            jobs = copy.deepcopy(list(self.jobs.values())[-200:])
            for job in jobs:
                if job['status'] == 'running' and job['stage'] == '正在下载模型':
                    info = next((m for m in models if m['model'] == job['model']), None)
                    if info:
                        job['progress'] = info['progress']
                        job['download_bytes'] = info['bytes']
                        job['download_total'] = info['total_bytes']
            return {'token': self.token, 'asr_installed': importlib.util.find_spec('faster_whisper') is not None,
                    'ffmpeg': bool(shutil.which('ffmpeg')), 'ffprobe': bool(shutil.which('ffprobe')),
                    'jobs': jobs, 'rules': copy.deepcopy(self.rules), 'models': models,
                    'analysis_concurrency': self.concurrency, 'cloud_concurrency': self.cloud_concurrency,
                    'dashscope': cloud_status()}
