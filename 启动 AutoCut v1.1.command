#!/bin/zsh
set -e
cd "/Users/lingze/Desktop/呼吸视频21节/AutoCut-v1.1"
(sleep 2; open "http://127.0.0.1:8766/") &
exec "/Users/lingze/Desktop/文件夹合集/呼吸视频21节/zhixing-workbench/.venv/bin/python" server.py \
  --media-dir "/Volumes/NO NAME/十个一生/十个一生第一季/" \
  --data-dir "/Users/lingze/Desktop/呼吸视频21节/AutoCut-v1.1/data" \
  --port 8766
