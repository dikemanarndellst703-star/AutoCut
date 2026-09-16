"""Minimal live verification for DashScope ASR. Never prints credentials or signed URLs."""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

from dashscope_asr import DashScopeASR, MODEL


def duration_of(path):
    process = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(path)],
        capture_output=True, text=True, timeout=45,
    )
    if process.returncode:
        raise RuntimeError('ffprobe 无法读取媒体：' + process.stderr[-500:])
    return float(json.loads(process.stdout)['format']['duration'])


def validate_output(data):
    if set(data) != {'video', 'duration', 'language', 'model', 'segments'}:
        raise ValueError('输出 JSON 字段不符合约定')
    if data['language'] != 'zh' or data['model'] != MODEL or not isinstance(data['segments'], list) or not data['segments']:
        raise ValueError('输出 JSON 的语言、模型或字幕列表无效')
    for row in data['segments']:
        if set(row) != {'start', 'end', 'text'} or not isinstance(row['text'], str) or not row['text']:
            raise ValueError('字幕片段字段无效')
        if not all(isinstance(row[key], (int, float)) and not isinstance(row[key], bool) and math.isfinite(row[key]) for key in ('start', 'end')) or row['end'] <= row['start']:
            raise ValueError('字幕时间戳无效')
        if any(abs(row[key] * 1000 - round(row[key] * 1000)) > 1e-6 for key in ('start', 'end')):
            raise ValueError('字幕时间戳没有保持毫秒精度')


def main():
    parser = argparse.ArgumentParser(description='验证阿里云百炼 paraformer-v2 长音频转写')
    parser.add_argument('media', type=Path)
    parser.add_argument('-o', '--output', type=Path, default=Path('transcription.json'))
    args = parser.parse_args()
    if not args.media.is_file():
        parser.error('媒体文件不存在')
    client = DashScopeASR()

    def progress(stage, percent):
        print(f'[{percent:02d}%] {stage}', file=sys.stderr, flush=True)

    data = client.transcribe(args.media.resolve(), args.media.name, duration_of(args.media), progress, lambda: None)
    validate_output(data)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    first = data['segments'][0]
    print(f'验证通过：{len(data["segments"])} 段，首段 {first["start"]:.3f}–{first["end"]:.3f} 秒；已写入 {args.output}')


if __name__ == '__main__':
    main()
