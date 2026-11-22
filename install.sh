#!/usr/bin/env bash
# ClipForge v1 installer (Ubuntu): creates a local venv, installs the
# Python requirements, and checks that ffmpeg (with libass) is present.
set -e
cd "$(dirname "$0")"

echo "== checking ffmpeg =="
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not found. Install it first:"
  echo "  sudo apt update && sudo apt install -y ffmpeg"
  exit 1
fi
if ! ffmpeg -hide_banner -filters 2>/dev/null | grep -q " ass "; then
  echo "warning: this ffmpeg build seems to lack the libass 'ass' filter;"
  echo "captions cannot be burned in without it."
  exit 1
fi
echo "ffmpeg OK: $(ffmpeg -version 2>/dev/null | head -1)"

echo "== creating virtual environment (.venv) =="
python3 -m venv .venv

echo "== installing requirements =="
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo ""
echo "Done. First run will download the whisper 'small' model once"
echo "(about 500 MB) and cache it locally; after that it works offline."
echo ""
echo "Usage:"
echo "  ./.venv/bin/python clipper.py --file /path/to/video.mp4 --top 3"
