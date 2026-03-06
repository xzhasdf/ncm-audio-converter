# 网易云歌曲批量转码工具

一个基于 `ffmpeg` 的批量转码工具，提供 CLI 和 `Vue 3 + TypeScript` 前端，可将本地音乐文件转换为常见音频格式（`mp3`/`wav`/`flac`/`m4a`/`ogg`）。

> 注意：本工具只处理系统可以正常解码的音频文件，不提供 DRM 绕过能力。

## 功能

- 递归扫描输入目录
- 按扩展名过滤可转换文件
- `.ncm` 文件自动先解码，再按目标格式转码
- 并发转码（可配置线程数）
- 支持覆盖或跳过已有文件
- 输出完成/失败统计

## 环境要求

- Python 3.10+
- 已安装 `ffmpeg` 并可在命令行执行 `ffmpeg -version`

## 快速开始

```bash
cd ncm-audio-converter
python3 -m venv .venv
source .venv/bin/activate
pip install -e . --no-build-isolation
# 如果要使用 Web 前端：
pip install -e .[web] --no-build-isolation
```

## 用法（Vue3 + TS 前端）

先启动 Python 后端 API（终端 1）：

```bash
cd ncm-audio-converter
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[web] --no-build-isolation
ncm-web
```

再启动 Vue 前端（终端 2）：

```bash
cd ncm-audio-converter/frontend
npm install
npm run dev
```

然后在浏览器打开前端页面：

```text
http://127.0.0.1:5173
```

前端会通过 `/api/convert` 代理到后端 `http://127.0.0.1:8765`。

页面中操作：

- 用文件选择框选择多个文件，或直接选择整个文件夹
- 选择文件夹时，仅处理该文件夹根目录文件（忽略子文件夹）
- 选择目标格式、常用比特率、并发数
- 点击“开始转码”后会上传并处理
- 输出方式可选：`直接下载(zip)` 或 `选择输出文件夹自动输出`

## 用法（CLI）

```bash
ncm-convert \
  --input "/path/to/music" \
  --output "/path/to/output" \
  --format mp3 \
  --bitrate 320k \
  --workers 4
```

### 常用参数

- `--input`：输入目录（递归扫描）
- `--output`：输出目录
- `--format`：目标格式，支持 `mp3|wav|flac|m4a|ogg`
- `--bitrate`：有损编码比特率（如 `192k`、`320k`）
- `--workers`：并发数
- `--overwrite`：覆盖已有文件
- `--extensions`：自定义待扫描扩展名，逗号分隔

示例（转为 flac）：

```bash
ncm-convert --input ./music --output ./converted --format flac --workers 6
```

## 桌面运行（不开发前端）

```bash
cd ncm-audio-converter
source .venv/bin/activate
npm --prefix frontend run build
ncm-desktop
```

启动后会自动打开浏览器并加载内置前端页面。

## 打包（Windows / macOS）

说明：不能在 macOS 上直接产出 Windows 可执行文件，也不能在 Windows 上直接产出 macOS app。需要在各自系统本机执行打包。

- macOS 打包：

```bash
cd ncm-audio-converter
./scripts/build_macos.sh
```

- Windows 打包（PowerShell）：

```powershell
cd ncm-audio-converter
.\scripts\build_windows.ps1
```

打包配置文件：

- `packaging/desktop.spec`

## 注意事项

- 如果某些文件转码失败，请先手动确认该文件可被 `ffmpeg` 正常识别。
- 对于文件名冲突，工具会保持目录结构并按同名输出；若目标已存在且未开启 `--overwrite`，将自动跳过。
