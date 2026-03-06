# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os

project_root = Path(os.getcwd()).resolve()
frontend_dist = project_root / 'frontend' / 'dist'

block_cipher = None

datas = []
if frontend_dist.exists():
    datas.append((str(frontend_dist), 'frontend_dist'))

a = Analysis(
    [str(project_root / 'src' / 'ncm_audio_converter' / 'desktop.py')],
    pathex=[str(project_root / 'src')],
    binaries=[],
    datas=datas,
    hiddenimports=['flask', 'werkzeug', 'webview'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='NCMAudioConverter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
