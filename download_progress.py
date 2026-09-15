"""Byte-based progress for the exact model revision being downloaded."""
import json
import os
import uuid
from pathlib import Path


def manifest_path(root, model):
    return Path(root) / '.progress' / f'{model}.json'


def prepare_manifest(root, model):
    from huggingface_hub import HfApi
    repo = f'Systran/faster-whisper-{model}'
    info = HfApi().model_info(repo, files_metadata=True)
    files = []
    for item in info.siblings:
        if item.rfilename not in ('model.bin', 'config.json', 'tokenizer.json', 'preprocessor_config.json') and not item.rfilename.startswith('vocabulary.'):
            continue
        if not isinstance(item.size, int) or item.size <= 0:
            raise ValueError('无法获取模型文件大小，请重试')
        lfs = item.lfs
        etag = (lfs.get('sha256') if isinstance(lfs, dict) else getattr(lfs, 'sha256', None)) if lfs else item.blob_id
        if not etag:
            raise ValueError('无法获取模型文件标识')
        files.append({'name': item.rfilename, 'size': item.size, 'etag': etag})
    if not any(f['name'] == 'model.bin' for f in files):
        raise ValueError('模型清单缺少权重文件')
    manifest = {'repo': repo, 'revision': info.sha, 'files': files}
    path = manifest_path(root, model)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(manifest), encoding='utf-8')
    os.replace(temporary, path)
    return manifest


def progress_bytes(root, model):
    path = manifest_path(root, model)
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text('utf-8'))
        repo = Path(root) / f'models--Systran--faster-whisper-{model}'
        done = 0
        total = sum(f['size'] for f in manifest['files'])
        for f in manifest['files']:
            blob = repo / 'blobs' / f['etag']
            snapshot = repo / 'snapshots' / manifest['revision'] / f['name']
            completed = [p for p in (blob, snapshot) if p.is_file()]
            partials = [p for p in (repo / 'blobs').glob(f['etag'] + '*.incomplete') if p.is_file()]
            # A previous cancelled attempt may be larger than the active download.
            candidates = completed or sorted(partials, key=lambda p: p.stat().st_mtime_ns, reverse=True)[:1]
            size = max((p.stat().st_size for p in candidates), default=0)
            done += min(size, f['size'])
        return {'bytes': done, 'total_bytes': total, 'progress': min(100, int(done * 100 / total)) if total else 0}
    except (OSError, ValueError, KeyError):
        return None


def download_with_progress(root, model):
    from huggingface_hub import snapshot_download
    manifest = prepare_manifest(root, model)
    return snapshot_download(manifest['repo'], revision=manifest['revision'], cache_dir=str(root),
                             allow_patterns=[f['name'] for f in manifest['files']])
