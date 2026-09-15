"""Local model inventory and explicitly requested background downloads."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from download_progress import download_with_progress, progress_bytes

MODEL_NAMES = ('tiny', 'base', 'small', 'medium', 'large-v3')


def cached_model(root, model):
    """Inspect actual snapshot files, never mistake an incomplete blob for a model."""
    repo = Path(root) / f'models--Systran--faster-whisper-{model}'
    snapshots = repo / 'snapshots'
    if not snapshots.exists():
        return None
    main = repo / 'refs' / 'main'
    candidates = list(snapshots.iterdir())
    if main.exists():
        ref = main.read_text().strip()
        candidates.sort(key=lambda p: p.name != ref)
    for folder in candidates:
        required = [folder / name for name in ('model.bin', 'config.json', 'tokenizer.json')]
        vocab = list(folder.glob('vocabulary.*'))
        if not vocab or not all(p.is_file() and p.stat().st_size > 0 for p in required + vocab):
            continue
        try:
            for name in ('config.json', 'tokenizer.json'):
                if not isinstance(json.loads((folder / name).read_text('utf-8')), dict):
                    raise ValueError('Invalid metadata')
            return folder
        except (OSError, ValueError):
            continue
    return None


def cached_bytes(root, model):
    folder = Path(root) / f'models--Systran--faster-whisper-{model}' / 'blobs'
    if not folder.exists():
        return 0
    return sum(p.stat().st_size for p in folder.iterdir() if p.is_file())


class ModelDownloads:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.serial = threading.Semaphore(1)
        self.tasks = {}
        self.processes = {}
        self.closed = False

    def status(self):
        with self.lock:
            rows = []
            for model in MODEL_NAMES:
                task = self.tasks.get(model, {})
                ready = cached_model(self.root, model) is not None
                active = task.get('status') in ('queued', 'downloading', 'cancelling')
                status = task['status'] if active else 'downloaded' if ready else task.get('status', 'missing')
                measurement = progress_bytes(self.root, model)
                if ready:
                    folder = cached_model(self.root, model)
                    size = sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
                    measurement = {'bytes': size, 'total_bytes': size, 'progress': 100}
                rows.append({'model': model, 'status': status, 'ready': ready,
                             **(measurement or {'bytes': cached_bytes(self.root, model), 'total_bytes': None, 'progress': None}),
                             'error': task.get('error') if status == 'failed' else None})
            return rows

    def start(self, model):
        if model not in MODEL_NAMES:
            raise ValueError('请选择有效模型')
        with self.lock:
            if self.closed:
                raise ValueError('服务正在退出')
            if cached_model(self.root, model):
                return {'status': 'downloaded', 'model': model}
            if self.tasks.get(model, {}).get('status') in ('queued', 'downloading', 'cancelling'):
                return copy.deepcopy(self.tasks[model])
            task = {'model': model, 'status': 'queued', 'cancel': False, 'error': None}
            self.tasks[model] = task
            threading.Thread(target=self._download, args=(model, task), daemon=True).start()
            return copy.deepcopy(task)

    def cancel(self, model):
        with self.lock:
            task = self.tasks.get(model)
            if not task or task['status'] not in ('queued', 'downloading', 'cancelling'):
                return
            task['cancel'] = True
            task['status'] = 'cancelling' if model in self.processes else 'cancelled'
            process = self.processes.get(model)
            if process and process.poll() is None:
                process.terminate()

    def close(self):
        with self.lock:
            self.closed = True
            for model in list(self.tasks):
                self.cancel(model)

    def _download(self, model, task):
        with self.serial:
            with self.lock:
                if task['cancel'] or self.closed:
                    task['status'] = 'cancelled'
                    return
                task['status'] = 'downloading'
            try:
                with tempfile.TemporaryFile() as log:
                    env = os.environ.copy()
                    env.update(HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_XET='1', HF_HOME=str(self.root / 'hub'))
                    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--download', model, str(self.root)], stdout=log, stderr=log, env=env)
                    with self.lock:
                        self.processes[model] = process
                    try:
                        while process.poll() is None:
                            if task['cancel']:
                                process.terminate()
                                try:
                                    process.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                break
                            time.sleep(.2)
                        process.wait()
                        if task['cancel']:
                            task['status'] = 'cancelled'
                        elif process.returncode or cached_model(self.root, model) is None:
                            log.seek(0)
                            raise RuntimeError(log.read().decode('utf-8', 'replace')[-1600:] or '下载文件不完整，请重试')
                        else:
                            task['status'] = 'downloaded'
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.wait()
                        with self.lock:
                            self.processes.pop(model, None)
            except Exception as error:
                with self.lock:
                    task.update(status='cancelled' if task['cancel'] else 'failed', error=str(error))


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--download' or sys.argv[2] not in MODEL_NAMES:
        raise SystemExit('Invalid download arguments')
    download_with_progress(sys.argv[3], sys.argv[2])
