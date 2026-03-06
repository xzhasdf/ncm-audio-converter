#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate

python3 -m pip install --upgrade pip
python3 -m pip install pyinstaller flask pywebview

npm --prefix frontend install
npm --prefix frontend run build

pyinstaller --noconfirm --clean --windowed --name NCMAudioConverterApp \
  --paths src \
  --add-data 'frontend/dist:frontend_dist' \
  --hidden-import flask \
  --hidden-import werkzeug \
  --hidden-import webview \
  src/ncm_audio_converter/desktop.py

echo "Build finished: $ROOT_DIR/dist/NCMAudioConverterApp.app"
