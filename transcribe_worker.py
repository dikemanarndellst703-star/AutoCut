"""Isolated ASR process: cancellation releases model memory immediately."""
import argparse
import json
import os
from pathlib import Path


def emit(**data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audio', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--models', required=True)
    args = parser.parse_args()
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
    os.environ.setdefault('HF_HOME', str(Path(args.models) / 'hub'))
    from faster_whisper import WhisperModel
    emit(stage='正在准备模型', progress=None)
    from model_downloads import cached_model
    local = cached_model(Path(args.models), args.model)
    if not local:
        from download_progress import download_with_progress
        emit(stage='正在下载模型', progress=None)
        local = download_with_progress(args.models, args.model)
    emit(stage='正在加载模型', progress=None)
    model = WhisperModel(str(local), device='cpu', compute_type='int8', cpu_threads=max(1, min(8, (os.cpu_count() or 4) // 4)), download_root=args.models)
    emit(stage='正在转写语音', progress=0)
    segments, _ = model.transcribe(args.audio, language='zh', beam_size=5, vad_filter=True,
                                   vad_parameters={'min_silence_duration_ms': 700},
                                   word_timestamps=True, condition_on_previous_text=False)
    for s in segments:
        emit(segment={'start': s.start, 'end': s.end, 'text': s.text,
                      'words': [{'start': w.start, 'end': w.end, 'text': w.word} for w in (s.words or [])]})
    emit(done=True)


if __name__ == '__main__':
    main()
