$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Test-Path '.venv')) {
  python -m venv .venv
}

. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install pyinstaller flask pywebview

npm --prefix frontend install
npm --prefix frontend run build

pyinstaller --noconfirm --clean --windowed --name NCMAudioConverterApp `
  --paths src `
  --add-data "frontend/dist;frontend_dist" `
  --hidden-import flask `
  --hidden-import werkzeug `
  --hidden-import webview `
  src/ncm_audio_converter/desktop.py
Write-Host "Build finished: $Root\dist\NCMAudioConverterApp.exe"
