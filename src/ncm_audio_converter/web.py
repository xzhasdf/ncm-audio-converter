from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import webbrowser
import zipfile
import os
from pathlib import Path, PureWindowsPath

try:
    from flask import Flask, jsonify, request, send_from_directory
except ModuleNotFoundError:
    Flask = None  # type: ignore[assignment]
    jsonify = None  # type: ignore[assignment]
    request = None  # type: ignore[assignment]
    send_from_directory = None  # type: ignore[assignment]

from ncm_audio_converter.converter import (
    DEFAULT_EXTENSIONS,
    SUPPORTED_FORMATS,
    build_tasks,
    convert_batch,
    ensure_ffmpeg,
    find_audio_files,
    parse_extensions,
)

app = Flask(__name__) if Flask is not None else None

ALLOWED_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}
DOWNLOAD_CACHE: dict[str, Path] = {}


if app is not None:
    def _frontend_dist_dir() -> Path | None:
        configured_raw = os.environ.get("NCM_FRONTEND_DIST", "").strip()
        if configured_raw:
            p = Path(configured_raw).expanduser().resolve()
            if (p / "index.html").exists():
                return p

        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", Path.cwd()))
            for candidate in [base / "frontend_dist", base / "frontend" / "dist"]:
                if (candidate / "index.html").exists():
                    return candidate

        project_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
        if (project_dist / "index.html").exists():
            return project_dist
        return None


    @app.after_request
    def apply_cors(response):
        origin = request.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        return response


    @app.get("/")
    def index():
        dist = _frontend_dist_dir()
        if dist is not None and send_from_directory is not None:
            return send_from_directory(dist, "index.html")
        return jsonify({"name": "ncm-audio-converter-api", "message": "Vue frontend 请访问 http://localhost:5173"})


    @app.get("/assets/<path:filename>")
    def frontend_assets(filename: str):
        dist = _frontend_dist_dir()
        if dist is None or send_from_directory is None:
            return jsonify({"error": "frontend dist 不存在"}), 404
        return send_from_directory(dist / "assets", filename)


    def _sanitize_relative_path(raw: str) -> Path:
        cleaned = raw.replace("\\", "/").strip()
        candidate = Path(cleaned)
        if candidate.is_absolute():
            raise ValueError("不允许绝对路径")
        if any(part in {"..", ""} for part in candidate.parts):
            raise ValueError("非法相对路径")
        return candidate


    def _collect_source_files(input_dir: Path, extensions: set[str], non_recursive: bool) -> list[Path]:
        if non_recursive:
            return [p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in extensions]
        return find_audio_files(input_dir, extensions)


    @app.route("/api/pick-directory", methods=["POST", "OPTIONS"])
    def api_pick_directory():
        if request.method == "OPTIONS":
            return ("", 204)

        data = request.get_json(silent=True) or {}
        title = str(data.get("title", "请选择输出目录")).strip() or "请选择输出目录"
        errors: list[str] = []

        # On macOS, avoid tkinter in Flask worker threads; use osascript only.
        if sys.platform == "darwin":
            try:
                script = (
                    'set chosenFolder to choose folder with prompt "'
                    + title.replace('"', '\\"')
                    + '"\n'
                    'POSIX path of chosenFolder'
                )
                proc = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                selected = proc.stdout.strip()
                if not selected:
                    return jsonify({"cancelled": True}), 200
                return jsonify({"path": str(Path(selected).resolve())})
            except subprocess.CalledProcessError as err:
                stderr = (err.stderr or "").strip()
                errors.append(f"osascript: {stderr or err}")
            except Exception as err:
                errors.append(f"osascript: {err}")
            return jsonify({"error": "目录选择窗口不可用；" + " | ".join(errors)}), 500

        # Windows native folder picker via PowerShell.
        if sys.platform.startswith("win"):
            try:
                title_ps = title.replace("'", "''")
                ps_script = (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$dlg = New-Object System.Windows.Forms.FolderBrowserDialog; "
                    f"$dlg.Description = '{title_ps}'; "
                    "if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { "
                    "$dlg.SelectedPath }"
                )
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_script],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                selected = proc.stdout.strip()
                if not selected:
                    return jsonify({"cancelled": True}), 200
                return jsonify({"path": str(Path(selected).resolve())})
            except subprocess.CalledProcessError as err:
                stderr = (err.stderr or "").strip()
                errors.append(f"powershell: {stderr or err}")
            except Exception as err:
                errors.append(f"powershell: {err}")
            return jsonify({"error": "目录选择窗口不可用；" + " | ".join(errors)}), 500

        # Non-macOS fallback.
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            selected = filedialog.askdirectory(title=title)
            root.destroy()
            if not selected:
                return jsonify({"cancelled": True}), 200
            return jsonify({"path": str(Path(selected).resolve())})
        except Exception as err:
            return jsonify({"error": f"目录选择窗口不可用；tkinter: {err}"}), 500


    @app.route("/api/convert-folder", methods=["POST", "OPTIONS"])
    def api_convert_folder():
        if request.method == "OPTIONS":
            return ("", 204)

        data = request.get_json(silent=True) or {}
        input_dir = Path(str(data.get("input_dir", "")).strip()).expanduser().resolve()
        target_format = str(data.get("target_format", "")).strip().lower()
        bitrate = str(data.get("bitrate", "320k")).strip() or "320k"
        try:
            workers = int(data.get("workers", 4))
        except (TypeError, ValueError):
            return jsonify({"error": "workers 必须是整数"}), 400
        overwrite = bool(data.get("overwrite", False))
        flac_fallback = bool(data.get("flac_fallback", False))
        extensions_raw = str(data.get("extensions", ",".join(sorted(DEFAULT_EXTENSIONS))))
        output_mode = str(data.get("output_mode", "folder")).strip().lower() or "folder"
        output_dir_raw = str(data.get("output_dir", "")).strip()
        non_recursive = bool(data.get("non_recursive", True))

        if not input_dir.exists() or not input_dir.is_dir():
            return jsonify({"error": f"输入目录不存在或不可用: {input_dir}"}), 400
        if target_format not in SUPPORTED_FORMATS:
            return jsonify({"error": f"不支持的目标格式: {target_format}"}), 400
        if workers < 1:
            return jsonify({"error": "workers 必须 >= 1"}), 400
        if output_mode not in {"download", "folder"}:
            return jsonify({"error": "output_mode 仅支持 download/folder"}), 400

        try:
            ensure_ffmpeg()
        except RuntimeError as err:
            return jsonify({"error": str(err)}), 400

        extensions = parse_extensions(extensions_raw)
        src_files = _collect_source_files(input_dir, extensions, non_recursive=non_recursive)
        if not src_files:
            return jsonify({"error": "未找到符合扩展名的文件"}), 400

        workspace = Path(tempfile.mkdtemp(prefix="ncm_folder_convert_"))
        temp_output_dir = workspace / "output"
        temp_output_dir.mkdir(parents=True, exist_ok=True)

        try:
            tasks = build_tasks(src_files, input_dir, temp_output_dir, target_format)
            results = convert_batch(
                tasks=tasks,
                target_format=target_format,
                bitrate=bitrate,
                overwrite=overwrite,
                workers=workers,
                flac_fallback=flac_fallback,
            )

            payload_rows = [
                {
                    "src": str(r.task.src.relative_to(input_dir)),
                    "dst": str(r.task.dst.relative_to(temp_output_dir)),
                    "success": r.success,
                    "message": r.message,
                }
                for r in sorted(results, key=lambda item: str(item.task.src))
            ]

            success = sum(1 for r in results if r.success)
            failed = len(results) - success

            download_url: str | None = None
            output_dir_applied: str | None = None
            if output_mode == "folder":
                target_output = Path(output_dir_raw).expanduser().resolve() if output_dir_raw else input_dir
                target_output.mkdir(parents=True, exist_ok=True)
                for out_file in temp_output_dir.rglob("*"):
                    if not out_file.is_file():
                        continue
                    rel = out_file.relative_to(temp_output_dir)
                    dst = target_output / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(out_file, dst)
                shutil.rmtree(workspace, ignore_errors=True)
                output_dir_applied = str(target_output)
            else:
                token = uuid.uuid4().hex
                archive_path = workspace / f"converted_{token}.zip"
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for out_file in temp_output_dir.rglob("*"):
                        if out_file.is_file():
                            zf.write(out_file, out_file.relative_to(temp_output_dir))
                DOWNLOAD_CACHE[token] = archive_path
                download_url = f"/api/download/{token}"

            return jsonify(
                {
                    "total": len(results),
                    "success": success,
                    "failed": failed,
                    "results": payload_rows,
                    "download_url": download_url,
                    "output_dir": output_dir_applied,
                }
            )
        except Exception as err:
            shutil.rmtree(workspace, ignore_errors=True)
            return jsonify({"error": f"目录转码失败: {err}"}), 500


    @app.route("/api/upload-convert", methods=["POST", "OPTIONS"])
    def api_upload_convert():
        if request.method == "OPTIONS":
            return ("", 204)

        target_format = str(request.form.get("target_format", "")).strip().lower()
        bitrate = str(request.form.get("bitrate", "320k")).strip() or "320k"
        try:
            workers = int(request.form.get("workers", 4))
        except (TypeError, ValueError):
            return jsonify({"error": "workers 必须是整数"}), 400
        overwrite = str(request.form.get("overwrite", "false")).strip().lower() == "true"
        flac_fallback = str(request.form.get("flac_fallback", "false")).strip().lower() == "true"
        output_mode = str(request.form.get("output_mode", "download")).strip().lower() or "download"
        output_dir_raw = str(request.form.get("output_dir", "")).strip()
        extensions_raw = str(request.form.get("extensions", ",".join(sorted(DEFAULT_EXTENSIONS))))
        relpaths = request.form.getlist("relpaths")
        source_paths = request.form.getlist("source_paths")
        files = request.files.getlist("files")

        if not files:
            return jsonify({"error": "请先选择要上传的音频文件"}), 400
        if target_format not in SUPPORTED_FORMATS:
            return jsonify({"error": f"不支持的目标格式: {target_format}"}), 400
        if workers < 1:
            return jsonify({"error": "workers 必须 >= 1"}), 400
        if output_mode not in {"download", "folder"}:
            return jsonify({"error": "output_mode 仅支持 download/folder"}), 400
        output_dir_target: Path | None = None
        auto_source_output = False
        if output_mode == "folder":
            if output_dir_raw:
                output_dir_target = Path(output_dir_raw).expanduser().resolve()
                output_dir_target.mkdir(parents=True, exist_ok=True)
                if not output_dir_target.is_dir():
                    return jsonify({"error": f"输出目录不可用: {output_dir_target}"}), 400
            else:
                auto_source_output = True

        try:
            ensure_ffmpeg()
        except RuntimeError as err:
            return jsonify({"error": str(err)}), 400

        workspace = Path(tempfile.mkdtemp(prefix="ncm_upload_"))
        input_dir = workspace / "input"
        output_dir = workspace / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            source_parent_by_rel: dict[str, Path] = {}
            for idx, item in enumerate(files):
                rel = relpaths[idx] if idx < len(relpaths) else (item.filename or f"upload_{idx}")
                try:
                    rel_path = _sanitize_relative_path(rel)
                except ValueError:
                    rel_path = Path(item.filename or f"upload_{idx}")
                save_path = input_dir / rel_path
                save_path.parent.mkdir(parents=True, exist_ok=True)
                item.save(save_path)
                if idx < len(source_paths):
                    raw_src = source_paths[idx].strip()
                    if raw_src:
                        try:
                            src_parent: Path
                            if sys.platform.startswith("win"):
                                src_parent = Path(PureWindowsPath(raw_src)).parent
                            else:
                                src_parent = Path(raw_src).expanduser().resolve().parent
                            source_parent_by_rel[str(rel_path)] = src_parent
                        except Exception:
                            pass

            extensions = parse_extensions(extensions_raw)
            src_files = find_audio_files(input_dir, extensions)
            tasks = build_tasks(src_files, input_dir, output_dir, target_format)

            results = convert_batch(
                tasks=tasks,
                target_format=target_format,
                bitrate=bitrate,
                overwrite=overwrite,
                workers=workers,
                flac_fallback=flac_fallback,
            )

            payload_rows = [
                {
                    "src": str(r.task.src.relative_to(input_dir)),
                    "dst": str(r.task.dst.relative_to(output_dir)),
                    "success": r.success,
                    "message": r.message,
                }
                for r in sorted(results, key=lambda item: str(item.task.src))
            ]

            success = sum(1 for r in results if r.success)
            failed = len(results) - success

            download_url: str | None = None
            output_dir_applied: str | None = None
            if output_mode == "folder":
                if output_dir_target is not None:
                    for out_file in output_dir.rglob("*"):
                        if not out_file.is_file():
                            continue
                        rel = out_file.relative_to(output_dir)
                        dst = output_dir_target / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(out_file, dst)
                    shutil.rmtree(workspace, ignore_errors=True)
                    output_dir_applied = str(output_dir_target)
                elif auto_source_output:
                    copied = 0
                    for r in results:
                        if not r.success:
                            continue
                        rel_src = str(r.task.src.relative_to(input_dir))
                        src_parent = source_parent_by_rel.get(rel_src)
                        if src_parent is None:
                            continue
                        src_output = r.task.dst
                        if not src_output.exists():
                            continue
                        dst = src_parent / src_output.name
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_output, dst)
                        copied += 1
                    shutil.rmtree(workspace, ignore_errors=True)
                    if copied == 0:
                        return jsonify({"error": "未能获取原文件目录，请手动选择输出目录"}), 400
                    output_dir_applied = "按原文件目录输出"
            else:
                token = uuid.uuid4().hex
                archive_path = workspace / f"converted_{token}.zip"
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for out_file in output_dir.rglob("*"):
                        if out_file.is_file():
                            zf.write(out_file, out_file.relative_to(output_dir))
                DOWNLOAD_CACHE[token] = archive_path
                download_url = f"/api/download/{token}"

            return jsonify(
                {
                    "total": len(results),
                    "success": success,
                    "failed": failed,
                    "results": payload_rows,
                    "download_url": download_url,
                    "output_dir": output_dir_applied,
                }
            )
        except Exception as err:
            shutil.rmtree(workspace, ignore_errors=True)
            return jsonify({"error": f"上传转码失败: {err}"}), 500


    @app.get("/api/download/<token>")
    def api_download(token: str):
        from flask import after_this_request, send_file

        def _safe_zip_name(raw: str) -> str:
            name = raw.strip()
            if not name:
                return ""
            invalid = '<>:"/\\|?*'
            for ch in invalid:
                name = name.replace(ch, "_")
            name = name.strip(" .")
            if not name:
                return ""
            if not name.lower().endswith(".zip"):
                name += ".zip"
            return name

        archive_path = DOWNLOAD_CACHE.get(token)
        if archive_path is None or not archive_path.exists():
            return jsonify({"error": "下载文件不存在或已过期"}), 404
        filename = _safe_zip_name(str(request.args.get("filename", "")))
        if not filename:
            filename = f"converted_{token[:8]}.zip"

        @after_this_request
        def _cleanup(response):
            DOWNLOAD_CACHE.pop(token, None)
            shutil.rmtree(archive_path.parent, ignore_errors=True)
            return response

        return send_file(
            archive_path,
            as_attachment=True,
            download_name=filename,
            mimetype="application/zip",
        )


    @app.route("/api/save-download", methods=["POST", "OPTIONS"])
    def api_save_download():
        if request.method == "OPTIONS":
            return ("", 204)

        data = request.get_json(silent=True) or {}
        token = str(data.get("token", "")).strip()
        output_dir_raw = str(data.get("output_dir", "")).strip()
        filename_raw = str(data.get("filename", "")).strip()
        if not token:
            return jsonify({"error": "缺少下载 token"}), 400
        if not output_dir_raw:
            return jsonify({"error": "缺少输出目录"}), 400

        archive_path = DOWNLOAD_CACHE.get(token)
        if archive_path is None or not archive_path.exists():
            return jsonify({"error": "下载文件不存在或已过期"}), 404

        output_dir = Path(output_dir_raw).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = filename_raw
        invalid = '<>:"/\\|?*'
        for ch in invalid:
            filename = filename.replace(ch, "_")
        filename = filename.strip(" .")
        if not filename:
            filename = archive_path.name
        if not filename.lower().endswith(".zip"):
            filename += ".zip"
        target = output_dir / filename
        shutil.copy2(archive_path, target)
        DOWNLOAD_CACHE.pop(token, None)
        shutil.rmtree(archive_path.parent, ignore_errors=True)
        return jsonify({"saved_path": str(target)})


    @app.route("/api/convert", methods=["POST", "OPTIONS"])
    def api_convert():
        if request.method == "OPTIONS":
            return ("", 204)

        data = request.get_json(silent=True) or {}

        input_dir = Path(str(data.get("input_dir", "")).strip()).expanduser().resolve()
        output_dir = Path(str(data.get("output_dir", "")).strip()).expanduser().resolve()
        target_format = str(data.get("target_format", "")).strip().lower()
        bitrate = str(data.get("bitrate", "320k")).strip() or "320k"
        try:
            workers = int(data.get("workers", 4))
        except (TypeError, ValueError):
            return jsonify({"error": "workers 必须是整数"}), 400

        overwrite = bool(data.get("overwrite", False))
        flac_fallback = bool(data.get("flac_fallback", False))
        extensions_raw = str(data.get("extensions", ",".join(sorted(DEFAULT_EXTENSIONS))))

        if not input_dir.exists() or not input_dir.is_dir():
            return jsonify({"error": f"输入目录不存在或不可用: {input_dir}"}), 400
        if target_format not in SUPPORTED_FORMATS:
            return jsonify({"error": f"不支持的目标格式: {target_format}"}), 400
        if workers < 1:
            return jsonify({"error": "workers 必须 >= 1"}), 400

        try:
            ensure_ffmpeg()
        except RuntimeError as err:
            return jsonify({"error": str(err)}), 400

        extensions = parse_extensions(extensions_raw)
        files = find_audio_files(input_dir, extensions)
        tasks = build_tasks(files, input_dir, output_dir, target_format)

        results = convert_batch(
            tasks=tasks,
            target_format=target_format,
            bitrate=bitrate,
            overwrite=overwrite,
            workers=workers,
            flac_fallback=flac_fallback,
        )

        payload_rows = [
            {
                "src": str(r.task.src),
                "dst": str(r.task.dst),
                "success": r.success,
                "message": r.message,
            }
            for r in sorted(results, key=lambda item: str(item.task.src))
        ]

        success = sum(1 for r in results if r.success)
        failed = len(results) - success

        return jsonify(
            {
                "total": len(results),
                "success": success,
                "failed": failed,
                "results": payload_rows,
            }
        )


def main(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> int:
    if app is None:
        print("未安装 Flask。请运行: pip install -e .[web] --no-build-isolation")
        return 2

    if open_browser:
        def _open() -> None:
            webbrowser.open(f"http://{host}:{port}")

        timer = threading.Timer(0.6, _open)
        timer.daemon = True
        timer.start()

    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
