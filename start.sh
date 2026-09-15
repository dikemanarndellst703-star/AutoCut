#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
for tool in ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "缺少 $tool，请先安装 FFmpeg 并加入 PATH。" >&2
    exit 1
  fi
done
if [ ! -x .venv/bin/python ]; then
  task_python="${ZHIXING_PYTHON:-python3}"
  "$task_python" -m venv .venv
fi
if ! .venv/bin/python -c 'import faster_whisper' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
echo "打开 http://127.0.0.1:8765 （自定义端口请以服务输出为准）"
exec .venv/bin/python server.py "$@"
