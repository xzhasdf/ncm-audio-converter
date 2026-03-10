# PyInstaller spec — Windows x64 desktop build (compatible with PyInstaller 6.x)
# Run from project root after:
#   1. npm run build  (inside frontend/)
#   2. copy ffmpeg.exe to project root
#   3. pip install -e ".[desktop]" cryptography pyinstaller
#   4. pyinstaller build.spec

from PyInstaller.utils.hooks import collect_all

# ── collect pywebview & flask sub-modules ──────────────────────────────────
datas = []
binaries = []
hiddenimports = []

for pkg in ("webview", "flask", "werkzeug", "jinja2", "click"):
    _d, _b, _h = collect_all(pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# Vue frontend dist
datas += [("frontend/dist", "frontend/dist")]

# Bundled ffmpeg (copied to project root before running pyinstaller)
binaries += [("ffmpeg.exe", ".")]

hiddenimports += [
    "ncm_audio_converter",
    "ncm_audio_converter.web",
    "ncm_audio_converter.converter",
    "ncm_audio_converter.ncm_decoder",
    "ncm_audio_converter.desktop",
    "cryptography",
    "cryptography.hazmat.primitives.ciphers",
    "cryptography.hazmat.primitives.ciphers.algorithms",
    "cryptography.hazmat.primitives.ciphers.modes",
    # pywebview Windows backends
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "webview.platforms.mshtml",
    "clr_loader",
]

# ── Analysis ───────────────────────────────────────────────────────────────
a = Analysis(
    ["src/ncm_audio_converter/desktop.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "email", "xmlrpc", "http.server"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NCM-Converter",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # no black terminal window
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="NCM-Converter",
)
